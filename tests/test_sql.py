"""The SQL backend through the public API only: a request in, a response out.

It runs on SQLite, or on the database DTB_SQL_URL names (the container jobs: PostgreSQL, MySQL, SQL Server).
"""

import asyncio
from collections.abc import Callable
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal
import logging
import os
from pathlib import Path
import re
import time
from typing import Any
from typing import get_args
import uuid
import warnings

import pytest
from sqlalchemy import CHAR
from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import Column as SQLColumn
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import Enum
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import Uuid
from sqlalchemy import and_
from sqlalchemy import case
from sqlalchemy import cast
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy import func
from sqlalchemy import literal_column
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.dialects import mssql
from sqlalchemy.dialects import mysql
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import sqlite
from sqlalchemy.engine import Dialect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import Pool
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.expression import ColumnElement
from sqlalchemy.types import TypeDecorator
from sqlalchemy.types import TypeEngine

from datatables_backend import Column
from datatables_backend import DTParams
from datatables_backend import DTResponse
from datatables_backend import RowHooks
from datatables_backend import SchemaError
from datatables_backend import TotalCache
from datatables_backend import parse_request
from datatables_backend.opensearch import IndexColumn
from datatables_backend.protocol import ErrorCode
from datatables_backend.sqlalchemy import SQLTable

from .conftest import ERROR_CODES
from .conftest import LARGEST_WORDS
from .conftest import ROWS
from .conftest import AsyncStore
from .conftest import AwaitingStore
from .conftest import EmptyStore
from .conftest import Store
from .conftest import async_engine
from .conftest import checked_database
from .conftest import columncontrol
from .conftest import columncontrol_list
from .conftest import criterion
from .conftest import exact_values
from .conftest import flat
from .conftest import labelled
from .conftest import nested
from .conftest import people
from .conftest import request

ALL = ('id', 'name', 'email', 'age', 'born', 'seen', 'team')
COLUMNS = [
    Column('id'),
    Column('name'),
    Column('email', field='email_address'),
    Column('age'),
    Column('born'),
    Column('seen'),
    Column('team', orderable=False),
]
table = SQLTable(people, COLUMNS)


def ids(response: DTResponse) -> list[Any]:
    assert response.error is None, response.error
    return [row['id'] for row in response.data]


def refused(response: DTResponse, code: str = 'invalid_filter') -> str:
    """The text of an answered user error. SPEC-E2.1: every refusal of these tests is checked against the eight codes,
    so a case that expects another code, `query_failed` among them, fails here."""
    assert code in ERROR_CODES
    assert response.error_code == code
    assert response.data == []
    assert response.error is not None
    return response.error


# -- paging, counts, rows ---------------------------------------------------------------------------------------------


def test_the_suite_runs_on_the_database_it_was_given(session: Session):
    """A container job names its dialect, so a run that silently fell back to SQLite fails."""
    assert session.get_bind().dialect.name == os.environ.get('DTB_SQL_DIALECT', 'sqlite')


async def test_the_async_suite_runs_on_the_database_it_was_given(async_session: AsyncSession):
    assert async_session.get_bind().dialect.name == os.environ.get('DTB_SQL_DIALECT', 'sqlite')


@pytest.mark.parametrize('name', ['people', 'staging', 'dtbx', 'dtb_testx', None])
def test_the_suite_refuses_a_database_it_does_not_own(name: str | None):
    """The fixtures drop and create tables, so a real database must be named for the tests; checked before any DDL."""
    with pytest.raises(RuntimeError, match='dtb_test'):
        checked_database('postgresql+psycopg://user@host/' + (name or ''))


@pytest.mark.parametrize('name', ['dtb', 'dtb_test', 'dtb_test_local'])
def test_the_suite_runs_on_a_database_named_for_it(name: str):
    assert checked_database(f'mssql+pyodbc://sa:pw@localhost:1433/{name}?driver=x') == name


def test_a_page_comes_with_both_counts(session: Session):
    """SPEC-C1.1: with no filter the total is every row."""
    response = table.fetch_sync(request(*ALL, start=1, length=2, draw=7), session)
    assert response.draw == 7
    assert (response.recordsTotal, response.recordsFiltered) == (5, 5)
    assert ids(response) == [2, 3]  # no order asked: the primary key orders the rows


def test_rows_carry_only_the_declared_columns_under_their_client_names(session: Session):
    """SPEC-R1.1: `email` reads the stored `email_address`, which never reaches the client under its own name."""
    row = table.fetch_sync(request(*ALL, length=1), session).data[0]
    assert set(row) == set(ALL)
    assert row['email'] == 'ann@example.org'


async def test_the_async_fetch_answers_like_the_sync_one(session: Session, async_session: AsyncSession):
    params = request(*ALL, search={'value': 'example'}, order=[{'column': 3, 'dir': 'desc'}])
    assert (await table.fetch(params, async_session)) == table.fetch_sync(params, session)


async def test_an_async_connection_works_too(async_session: AsyncSession):
    connection = await async_session.connection()
    assert ids(await table.fetch(request(*ALL), connection)) == [1, 2, 3, 4, 5]


def test_a_sync_connection_works_too(session: Session):
    assert ids(table.fetch_sync(request(*ALL), session.connection())) == [1, 2, 3, 4, 5]


def test_the_base_filter_limits_the_total_too(session: Session):
    """SPEC-C1.1: a base filter limits the total. SPEC-C1.2: a search narrows the filtered count, not the total.
    SPEC-F31.1: with no search the rows are the base filter's, and both counts are its."""
    reds = SQLTable(people, COLUMNS, base_filter=people.c.team == 'red')
    response = reds.fetch_sync(request(*ALL), session)
    assert (response.recordsTotal, response.recordsFiltered, ids(response)) == (2, 2, [1, 3])
    response = reds.fetch_sync(request(*ALL, search={'value': 'ann'}), session)
    assert (response.recordsTotal, response.recordsFiltered) == (2, 2)
    response = reds.fetch_sync(request(*ALL, search={'value': 'anna'}), session)
    assert (response.recordsTotal, response.recordsFiltered, ids(response)) == (2, 1, [3])


def test_a_scope_limits_the_total_the_count_and_the_page_together_with_the_base_filter(session: Session):
    """SPEC-F31.2: the base filter `team = 'red'` alone gives rows 1 and 3, and the scope `id > 1` alone rows 2 to 5; the
    two apply together, to the total and to the filtered set (its count and its page come from one statement). A scope
    that replaced the base filter shows rows 2 to 5, one that missed the total a total of 2, and one that missed the
    filtered set rows 1 and 3. SPEC-F31.3: a scope of `None` answers as no scope does."""
    reds, scope = SQLTable(people, COLUMNS, base_filter=people.c.team == 'red'), people.c.id > 1
    response = reds.fetch_sync(request(*ALL), session, scope=scope)
    assert (response.recordsTotal, response.recordsFiltered, ids(response)) == (1, 1, [3])
    response = reds.fetch_sync(request(*ALL, search={'value': 'ann'}), session, scope=scope)
    assert (response.recordsTotal, response.recordsFiltered, ids(response)) == (1, 1, [3])  # row 1 holds `ann` too
    params = request(*ALL, search={'value': 'ann'})
    assert reds.fetch_sync(params, session, scope=None) == reds.fetch_sync(params, session)


def test_row_hooks_shape_rows_and_add_row_id_and_data(session: Session):
    hooked = SQLTable(
        people,
        COLUMNS,
        rows=RowHooks(
            shape=lambda row: {'id': row['id'], 'label': row['name'].upper()},
            row_id=lambda row: f'person-{row["id"]}',
            row_data=lambda row: {'pk': row['id']} if row['id'] == 1 else None,
        ),
    )
    first, second = hooked.fetch_sync(request(*ALL, length=2), session).data
    assert first == {'id': 1, 'label': 'ANN', 'DT_RowId': 'person-1', 'DT_RowData': {'pk': 1}}
    assert 'DT_RowData' not in second


def test_a_row_shaped_through_a_model_carries_the_field_the_model_adds(session: Session):
    """SPEC-R2.1: `shape` maps each row through a Pydantic model that adds `label`; every row carries it."""
    shaped = SQLTable(people, COLUMNS, rows=labelled('name'))
    rows = shaped.fetch_sync(request(*ALL), session).data
    assert [(row['id'], row['label']) for row in rows] == [
        (1, 'label Ann'), (2, 'label Bob'), (3, 'label anna_100%'), (4, 'label Cy'), (5, 'label Dee'),
    ]  # fmt: skip
    assert all(set(row) == {*ALL, 'label'} for row in rows)


def test_the_row_id_hook_adds_dt_rowid_and_without_it_no_row_has_one(session: Session):
    """SPEC-R3.1: a hook returning `row_<id>` gives each row its `DT_RowId`; the plain table adds none."""
    hooked = SQLTable(people, COLUMNS, rows=RowHooks(row_id=lambda row: f'row_{row["id"]}'))
    rows = hooked.fetch_sync(request(*ALL), session).data
    assert [row['DT_RowId'] for row in rows] == [f'row_{number}' for number in range(1, 6)]
    assert all('DT_RowId' not in row for row in table.fetch_sync(request(*ALL), session).data)


def test_the_row_data_hook_adds_dt_rowdata_and_without_it_no_row_has_any(session: Session):
    """SPEC-R4.1: a hook returning `{"pk": id}` gives each row its `DT_RowData`; the plain table adds none."""
    hooked = SQLTable(people, COLUMNS, rows=RowHooks(row_data=lambda row: {'pk': row['id']}))
    rows = hooked.fetch_sync(request(*ALL), session).data
    assert [row['DT_RowData'] for row in rows] == [{'pk': number} for number in range(1, 6)]
    assert all('DT_RowData' not in row for row in table.fetch_sync(request(*ALL), session).data)


def test_a_declared_column_the_source_lacks_fails_at_construction():
    """SPEC-D2.2: a column whose field the source has not is the integrator's mistake, raised when the table is built."""
    with pytest.raises(SchemaError, match='nope'):
        SQLTable(people, [Column('id'), Column('nope')])


def test_the_total_is_cached_until_the_ttl(session: Session):
    """SPEC-C16.1: a write is not seen until `clear()`; the filtered count is always live."""
    cache = TotalCache(ttl=60)
    cached = SQLTable(people, COLUMNS, total_cache=cache)
    assert cached.fetch_sync(request(*ALL), session).recordsTotal == 5
    session.execute(people.delete().where(people.c.id == 5))
    response = cached.fetch_sync(request(*ALL), session)
    assert (response.recordsTotal, response.recordsFiltered) == (5, 4)  # the cached total, the live filtered count
    cache.clear()
    assert cached.fetch_sync(request(*ALL), session).recordsTotal == 4


def _scoped_by_base_filter(team: str, cache: TotalCache) -> SQLTable:
    return SQLTable(people, [Column('id'), Column('name')], base_filter=people.c.team == team, total_cache=cache)


def _scoped_in_a_subquery(team: str, cache: TotalCache) -> SQLTable:
    scoped = select(people).where(people.c.team == team).subquery('scoped')
    return SQLTable(scoped, [Column('id'), Column('name')], total_cache=cache)


def _scoped_in_a_join(team: str, cache: TotalCache) -> SQLTable:
    other = people.alias('other')
    joined = people.join(other, and_(other.c.id == people.c.id, other.c.team == team))
    columns = [Column('id', field='dtb_test_people_id'), Column('name', field='dtb_test_people_name')]
    return SQLTable(joined, columns, total_cache=cache)


@pytest.mark.parametrize('scoped', [_scoped_by_base_filter, _scoped_in_a_subquery, _scoped_in_a_join])
def test_two_scopes_that_differ_only_in_a_bound_value_never_share_a_total(
    scoped: Callable[[str, TotalCache], SQLTable], session: Session
):
    """SPEC-C15.1, SPEC-C15.5, SPEC-C15.6: one cache, and the team `red` (rows 1 and 3) then `green` (row 4). A key
    that leaves out a bound value, of the base filter or of the source, shows `green` the total of `red`."""
    cache, params = TotalCache(ttl=60), request('id', 'name', search={'value': 'e'})
    totals = [scoped(team, cache).fetch_sync(params, session).recordsTotal for team in ('red', 'green')]
    assert totals == [2, 1]


async def test_the_async_fetch_keeps_two_scopes_apart_too(async_session: AsyncSession):
    """SPEC-C15.5 through `fetch`, which reads and fills the cache in code of its own."""
    cache, params = TotalCache(ttl=60), request('id', 'name', search={'value': 'e'})
    totals = [
        (await _scoped_in_a_subquery(team, cache).fetch(params, async_session)).recordsTotal
        for team in ('red', 'green')
    ]
    assert totals == [2, 1]


def test_two_scopes_passed_to_fetch_never_share_a_total(session: Session):
    """SPEC-C15.8: one table without a base filter and one cache, and the scopes `team = 'red'` (rows 1 and 3) then
    `green` (row 4) on the same request. A key built from the table alone shows `green` the total of `red`."""
    cache, params = TotalCache(ttl=60), request('id', 'name', search={'value': 'e'})
    cached = SQLTable(people, [Column('id'), Column('name')], total_cache=cache)
    totals = [cached.fetch_sync(params, session, scope=people.c.team == team).recordsTotal for team in ('red', 'green')]
    assert totals == [2, 1]


async def test_the_async_fetch_keeps_two_scopes_apart_too_when_they_are_passed_to_fetch(async_session: AsyncSession):
    """SPEC-C15.8 through `fetch`, which reads and fills the cache in code of its own."""
    cache, params = TotalCache(ttl=60), request('id', 'name', search={'value': 'e'})
    cached = SQLTable(people, [Column('id'), Column('name')], total_cache=cache)
    totals = [
        (await cached.fetch(params, async_session, scope=people.c.team == team)).recordsTotal
        for team in ('red', 'green')
    ]
    assert totals == [2, 1]


class _every_row(ColumnElement[bool]):  # noqa: N801
    """A condition with a handler for each database the tests run on and none for SQLAlchemy's default dialect, as
    an application's own construct or MySQL's ``MATCH`` has."""

    inherit_cache = True


@compiles(_every_row, 'sqlite')
@compiles(_every_row, 'postgresql')
@compiles(_every_row, 'mysql')
@compiles(_every_row, 'mariadb')
@compiles(_every_row, 'mssql')
def _every_row_sql(element: _every_row, compiler: SQLCompiler, **kw: Any) -> str:
    return '1 = 1'


def test_a_base_filter_only_its_database_can_compile_works_with_a_cache(session: Session):
    """SPEC-C15.7: a cache key compiled for another dialect than the session's raises on every request."""
    cached = SQLTable(people, COLUMNS, base_filter=_every_row(), total_cache=TotalCache(ttl=60))
    assert cached.fetch_sync(request(*ALL), session).recordsTotal == 5


SCOPE_VALUE = 'tenant-zq-4711'


def test_a_store_that_is_no_total_cache_serves_the_second_draw_and_sees_only_digests(session: Session):
    """SPEC-C18.1: a plain class with `get` and `put`. A row deleted between two draws leaves the total at 5, so the
    second is served from it. Every key it was given is a SHA-256 digest: the scope's bound value is in the count that
    makes the key, and never reaches the store."""
    store = Store()
    cached = SQLTable(people, COLUMNS, total_cache=store)
    scope = people.c.team != SCOPE_VALUE
    assert cached.fetch_sync(request(*ALL), session, scope=scope).recordsTotal == 5
    session.execute(people.delete().where(people.c.id == 5))
    assert cached.fetch_sync(request(*ALL), session, scope=scope).recordsTotal == 5
    assert store.keys
    assert all(re.fullmatch('[0-9a-f]{64}', key) for key in store.keys)
    assert not any(SCOPE_VALUE in key for key in store.keys)


async def test_an_async_store_serves_the_second_async_draw_and_sees_only_digests(async_session: AsyncSession):
    """SPEC-C18.2: the same through `fetch` with a store whose methods are coroutines."""
    store = AsyncStore()
    cached = SQLTable(people, COLUMNS, total_cache=store)
    scope = people.c.team != SCOPE_VALUE
    assert (await cached.fetch(request(*ALL), async_session, scope=scope)).recordsTotal == 5
    await async_session.execute(people.delete().where(people.c.id == 5))
    assert (await cached.fetch(request(*ALL), async_session, scope=scope)).recordsTotal == 5
    assert store.keys
    assert all(re.fullmatch('[0-9a-f]{64}', key) for key in store.keys)
    assert not any(SCOPE_VALUE in key for key in store.keys)


async def test_a_store_whose_methods_return_awaitables_is_awaited_too(async_session: AsyncSession):
    """SPEC-C18.2: `fetch` awaits what `get` and `put` return, whichever way the store is written."""
    store = AwaitingStore()
    cached = SQLTable(people, COLUMNS, total_cache=store)
    scope = people.c.team != SCOPE_VALUE
    assert (await cached.fetch(request(*ALL), async_session, scope=scope)).recordsTotal == 5
    await async_session.execute(people.delete().where(people.c.id == 5))
    assert (await cached.fetch(request(*ALL), async_session, scope=scope)).recordsTotal == 5
    assert store.keys
    assert all(re.fullmatch('[0-9a-f]{64}', key) for key in store.keys)
    assert not any(SCOPE_VALUE in key for key in store.keys)


def test_a_store_that_says_it_is_empty_is_used_all_the_same(session: Session):
    """SPEC-C18.3: a store is in use because it was given, not because it is truthy."""
    cached = SQLTable(people, COLUMNS, total_cache=EmptyStore())
    assert cached.fetch_sync(request(*ALL), session).recordsTotal == 5
    session.execute(people.delete().where(people.c.id == 5))
    assert cached.fetch_sync(request(*ALL), session).recordsTotal == 5


async def test_the_async_fetch_uses_a_store_that_says_it_is_empty_too(async_session: AsyncSession):
    """SPEC-C18.3 through `fetch`, which reads and fills the store in code of its own."""
    cached = SQLTable(people, COLUMNS, total_cache=EmptyStore())
    assert (await cached.fetch(request(*ALL), async_session)).recordsTotal == 5
    await async_session.execute(people.delete().where(people.c.id == 5))
    assert (await cached.fetch(request(*ALL), async_session)).recordsTotal == 5


def test_fetch_sync_with_an_async_store_raises_before_any_statement_runs(session: Session):
    """SPEC-C18.4: the message names the condition and the fix, and the database saw nothing."""
    statements: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: statements.append(args[2]))
    cached = SQLTable(people, COLUMNS, total_cache=AsyncStore())
    with pytest.raises(TypeError, match='call fetch'):
        cached.fetch_sync(request(*ALL), session)
    assert not [s for s in statements if 'dtb_test_people' in s]


def test_fetch_sync_with_a_store_returning_awaitables_raises(session: Session):
    """SPEC-C18.4: `get` returns an awaitable the sync path cannot use; it is an error, not a total."""
    cached = SQLTable(people, COLUMNS, total_cache=AwaitingStore())
    with pytest.raises(TypeError, match='call fetch'):
        cached.fetch_sync(request(*ALL), session)


# -- ordering ---


def test_order_follows_the_requests_columns_then_the_primary_key(session: Session):
    params = request(*ALL, order=[{'column': 3, 'dir': 'asc'}])  # age; Ann and Cy tie at 31; row 3's age is NULL
    assert [row for row in ids(table.fetch_sync(params, session)) if row != 3] == [1, 4, 2, 5]


def test_null_comes_first_in_an_ascending_order(session: Session):
    """SPEC-O2.1, SPEC-O2.4: PostgreSQL's proof, which puts NULL last ascending when left to itself; the other
    databases order NULL lowest themselves."""
    params = request(*ALL, order=[{'column': 3, 'dir': 'asc'}])
    assert ids(table.fetch_sync(params, session))[0] == 3


def test_a_descending_order_is_the_reverse_of_the_ascending_one(session: Session):
    """SPEC-O3.1, and SPEC-O2.1 descending: Ann and Cy tie at 31, and the key breaks the tie in the order's own
    direction, so Cy (4) comes before Ann (1). Row 3's NULL is last: PostgreSQL would put it first."""
    params = request(*ALL, order=[{'column': 3, 'dir': 'desc'}])
    assert ids(table.fetch_sync(params, session)) == [5, 2, 4, 1, 3]


def test_the_key_follows_the_first_of_several_order_entries(session: Session):
    """SPEC-O3.1 with two applied entries: Ann (1) and Cy (4) tie on `age` and on `active`, and the key breaks the
    tie as `age` runs, descending, not as the last entry does."""
    several = SQLTable(people, [Column('id'), Column('age'), Column('active')])
    params = request('id', 'age', 'active', order=[{'column': 1, 'dir': 'desc'}, {'column': 2, 'dir': 'asc'}])
    assert ids(several.fetch_sync(params, session)) == [5, 2, 4, 1, 3]


def test_null_placement_is_written_only_where_the_database_needs_it(session: Session):
    """PRD-O2: `id` cannot hold NULL and is ordered plainly, so an index on it still serves the order; `age` can,
    and gets its placement on a database that would put NULL elsewhere. MySQL and SQL Server do not take the clause."""
    statements: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: statements.append(args[2]))
    table.fetch_sync(request(*ALL, order=[{'column': 0, 'dir': 'desc'}, {'column': 3, 'dir': 'asc'}]), session)
    order_by = statements[-1][statements[-1].rindex('ORDER BY') :]
    written = ['NULLS FIRST'] if session.get_bind().dialect.name == 'postgresql' else []
    assert re.findall(r'NULLS \w+', order_by) == written
    # The same `id` through a join: an outer join gives NULL for a column that holds none, so no column of a join
    # is taken to be free of it
    other = people.alias('other')
    joined = SQLTable(people.join(other, people.c.age == other.c.age), [Column('id', field='dtb_test_people_id')])
    joined.fetch_sync(request('id', order=[{'column': 0, 'dir': 'asc'}]), session)
    order_by = statements[-1][statements[-1].rindex('ORDER BY') :]
    assert ('NULLS FIRST' in order_by) == (session.get_bind().dialect.name == 'postgresql')


def test_an_order_the_server_does_not_allow_is_refused(session: Session):
    """`team` is declared not orderable, whatever the request claims."""
    refused(table.fetch_sync(request(*ALL, order=[{'column': 6, 'dir': 'asc'}]), session))


def test_an_order_the_request_marks_unorderable_is_answered(session: Session):
    """SPEC-O1.3: the server decides. DataTables sends its default order whatever the column's `orderable` says."""
    params = request(*ALL, flags={'id': {'orderable': False}}, order=[{'column': 0, 'dir': 'desc'}])
    assert ids(table.fetch_sync(params, session)) == [5, 4, 3, 2, 1]


@pytest.mark.parametrize(
    ('names', 'flags', 'order', 'expected'),
    [
        # SPEC-O1.4, SPEC-O8.3, SPEC-O3.3: unorderable on both sides. `age` is then the primary order, and the key
        # follows its direction: following the skipped entry's would put Cy (4) before Ann (1)
        (
            ALL,
            {'team': {'orderable': False}},
            [{'column': 6, 'dir': 'desc'}, {'column': 3, 'dir': 'asc'}],
            [3, 1, 4, 2, 5],
        ),
        # SPEC-O1.5, SPEC-O1.7, SPEC-O4.2: a name the server did not declare, marked unorderable. With every entry
        # skipped the rows come in the key's ascending order, not in the skipped entry's direction
        (('extra', *ALL), {'extra': {'orderable': False}}, [{'column': 0, 'dir': 'desc'}], [1, 2, 3, 4, 5]),
        # SPEC-O1.6: a column that only renders, whatever its `orderable`
        (('', *ALL), {'': {'data': None}}, [{'column': 0, 'dir': 'desc'}], [1, 2, 3, 4, 5]),
        # SPEC-P21.2: how DataTables sends a column whose `data` is a function
        (('function', *ALL), {}, [{'column': 0, 'dir': 'desc'}], [1, 2, 3, 4, 5]),
    ],
    ids=['unorderable-on-both-sides', 'undeclared-and-unorderable', 'nameless', 'function'],
)
def test_an_order_entry_that_cannot_apply_is_skipped(
    session: Session,
    names: tuple[str, ...],
    flags: dict[str, dict[str, Any]],
    order: list[dict[str, Any]],
    expected: list[int],
):
    assert ids(table.fetch_sync(request(*names, flags=flags, order=order), session)) == expected


def test_an_order_on_an_undeclared_name_is_refused_also_when_its_data_is_empty(session: Session):
    """SPEC-O1.5, PRD-P21: a column with a name is not one that only renders, whatever its `data` holds."""
    params = request('', *ALL, flags={'': {'name': 'extra'}}, order=[{'column': 0, 'dir': 'asc'}])
    refused(table.fetch_sync(params, session))


def test_a_column_the_server_names_function_is_ordered_by(session: Session):
    """SPEC-P21.2: `function` is a column that only renders unless the server declares that name."""
    named = SQLTable(people, [Column('function', field='id'), Column('name')])
    response = named.fetch_sync(request('function', 'name', order=[{'column': 0, 'dir': 'desc'}]), session)
    assert [row['function'] for row in response.data] == [5, 4, 3, 2, 1]


def test_a_column_ordered_twice_is_ordered_by_its_first_entry(session: Session):
    """SPEC-O8.4: 2,000 entries on one column are one. SQLite refuses an ORDER BY of that many terms."""
    order = [{'column': 3, 'dir': 'desc'}, *[{'column': 3, 'dir': 'asc'}] * 1999]
    assert ids(table.fetch_sync(request(*ALL, order=order), session)) == [5, 2, 4, 1, 3]


def test_a_skipped_order_entry_or_column_logs_nothing(session: Session, caplog: pytest.LogCaptureFixture):
    """SPEC-E13.2, SPEC-P21.1: a column that only renders and DataTables' default order on it are every request's
    traffic. Each skip there is, is in this request: an order on a column that only renders, on an undeclared one
    and on `team`, which both sides mark unorderable, a column ordered twice, and the two columns the global search
    leaves out."""
    params = request(
        '',
        'extra',
        *ALL,
        flags={'': {'data': None}, 'extra': {'orderable': False}, 'team': {'orderable': False}},
        order=[{'column': index, 'dir': 'asc'} for index in (0, 1, 8, 2, 2)],  # nameless, extra, team, id, id
        search={'value': 'ann'},
    )
    with caplog.at_level(logging.DEBUG, logger='datatables_backend'):
        assert ids(table.fetch_sync(params, session)) == [1, 3]
    assert library_log(caplog) == ''


def test_a_start_at_the_bound_is_an_empty_page_with_both_counts(session: Session):
    """SPEC-P10.1, SPEC-C3.2: the largest `start` reaches each database as an offset it takes."""
    response = table.fetch_sync(request(*ALL, start=2**53 - 1), session)
    assert (response.recordsTotal, response.recordsFiltered, ids(response)) == (5, 5, [])


# -- searches ---


def test_the_global_search_is_a_case_insensitive_substring_over_searchable_columns(session: Session):
    """SPEC-F9.1: `ANN` finds `Ann` and `anna_100%`, a substring in another case."""
    assert ids(table.fetch_sync(request(*ALL, search={'value': 'ANN'}), session)) == [1, 3]


def test_like_wildcards_in_a_search_are_literal(session: Session):
    assert ids(table.fetch_sync(request(*ALL, search={'value': '0%'}), session)) == [3]
    assert ids(table.fetch_sync(request(*ALL, search={'value': 'a_1'}), session)) == [3]


def test_the_global_search_matches_a_date_by_its_day_text(session: Session):
    """SPEC-F23.3: the text of a date column, and of a datetime column, is searched."""
    assert ids(table.fetch_sync(request(*ALL, search={'value': '2026-07-1'}), session)) == [1, 2, 5]
    assert ids(table.fetch_sync(request(*ALL, search={'value': '1980-07'}), session)) == [2]  # a Date column


def test_the_global_search_matches_a_number_by_its_text(session: Session):
    """SPEC-F23.1."""
    assert ids(table.fetch_sync(request(*ALL, search={'value': '45'}), session)) == [2]


@pytest.mark.parametrize(
    ('column', 'text', 'expected'),
    [
        ('age', '1', [1, 4]),  # SPEC-F23.1: a number column searched by its text, a substring: `31`
        ('age', '45', [2]),
        ('born', '1980-07', [2]),  # SPEC-F23.2: a date column's text is `YYYY-MM-DD`
        ('born', '07-15', [2]),  # an infix of it
        ('born', '03-01', [1]),
    ],
)
def test_a_column_search_on_a_number_or_a_date_reads_its_text(
    session: Session, column: str, text: str, expected: list[int]
):
    """SPEC-F23.1, SPEC-F23.2 (on SQL, a number and a date are text to a search)."""
    assert ids(table.fetch_sync(request(*ALL, column_search={column: {'value': text}}), session)) == expected


@pytest.mark.parametrize('column', ['double', 'decimal'])
@pytest.mark.parametrize(
    ('text', 'expected'),
    [('7', [1, 3]), ('12.5', [2]), ('1234567', [3]), ('-3.25', [4])],
    ids=['a-digit-of-two', 'a-fraction', 'seven-digits', 'a-negative-fraction'],
)
def test_a_number_as_the_table_shows_it_finds_its_row(session: Session, column: str, text: str, expected: list[int]):
    """SPEC-F23.4, PRD-F23: a double-precision column and a decimal one hold 17, 12.5, 1234567 and -3.25. A database
    that cast a double to text with six digits (SQL Server's `CAST`: `1.23457e+006`) would lose the row of 1234567 to
    the search `1234567`. Single precision is left out: PostgreSQL's `real` still misses it, and the test must not
    freeze that."""
    numbers = Table(
        'dtb_test_tags',
        MetaData(),
        SQLColumn('id', Integer, primary_key=True, autoincrement=False),
        SQLColumn('double', Float(53)),
        SQLColumn('decimal', Numeric(10, 2)),
    )
    numbers.create(session.connection())
    session.execute(
        numbers.insert(),
        [
            {'id': id_, 'double': value, 'decimal': Decimal(str(value))}
            for id_, value in enumerate((17, 12.5, 1234567, -3.25), start=1)
        ],
    )
    shown = SQLTable(numbers, [Column('id'), Column('double'), Column('decimal')])
    params = request('id', column, column_search={column: {'value': text}})
    assert ids(shown.fetch_sync(params, session)) == expected


def test_a_column_search_narrows_by_that_column(session: Session):
    params = request(*ALL)
    params.columns[6].search.value = 'RED'
    params.columns[1].search.value = 'ann'
    assert ids(table.fetch_sync(params, session)) == [1, 3]


def test_a_multi_select_matches_its_values_exactly(session: Session):
    params = request(*ALL)
    params.columns[6].search.value = '^(blue|green)$'
    params.columns[6].search.regex = True
    assert ids(table.fetch_sync(params, session)) == [2, 4, 5]


def test_a_multi_select_on_a_number_column_compares_numbers(session: Session):
    params = request(*ALL)
    params.columns[3].search.multi_values = ['31', '60']
    assert ids(table.fetch_sync(params, session)) == [1, 4, 5]


def test_an_undeclared_column_cannot_be_searched_or_sorted(session: Session):
    """SPEC-D6.1 (a value), SPEC-D6.2 (a criterion), SPEC-O1.5 (an order entry): an undeclared name is refused."""
    params = request(*ALL, 'secret')
    params.columns[7].search.value = 's1'
    reason = refused(table.fetch_sync(params, session))
    assert 'secret' not in reason
    refused(table.fetch_sync(request(*ALL, 'secret', order=[{'column': 7, 'dir': 'asc'}]), session))
    refused(table.fetch_sync(request(*ALL, **criterion('secret', '=', 's1')), session))


@pytest.mark.parametrize('name', ['Name', 'name ', 'email_address'])
def test_a_name_must_be_a_declared_name_exactly(session: Session, name: str):
    """SPEC-P15.2: another case, a space after it, and the source field of the renamed `email` name no column."""
    refused(table.fetch_sync(request(*ALL, name, column_search={name: {'value': 'a'}}), session))


def test_a_columns_name_decides_before_its_data(session: Session):
    """SPEC-P15.2: `data` says where the page reads the cell; the server matches `name` when there is one. Ordered
    by `id`, which the column's `data` names, the rows would come 1 to 5."""
    params = request(*ALL, flags={'id': {'name': 'age'}}, order=[{'column': 0, 'dir': 'asc'}])
    assert ids(table.fetch_sync(params, session)) == [3, 1, 4, 2, 5]


def test_a_column_with_data_and_no_name_is_the_declared_column_of_that_name(session: Session):
    """SPEC-P15.1: `data` `name` with an empty `name` is `name`: a column search on it finds Bob only."""
    assert ids(table.fetch_sync(request(*ALL, column_search={'name': {'value': 'bob'}}), session)) == [2]


def test_an_order_entry_is_decided_by_its_column_index_not_its_name(session: Session):
    """SPEC-P16.1: column 3 is `age`; the entry's `name` `id` is not read. By `id` the rows would come 1 to 5."""
    order = [{'column': 3, 'dir': 'asc', 'name': 'id'}]
    assert ids(table.fetch_sync(request(*ALL, order=order), session)) == [3, 1, 4, 2, 5]


@pytest.mark.parametrize(('name', 'found'), [('name', [2]), ('', None)], ids=['named', 'nameless'])
def test_a_column_whose_data_is_an_object_is_known_by_its_name(session: Session, name: str, found: list[int] | None):
    """SPEC-P18.1: `data` `{"_": "name", "sort": "name"}` with `name` `name` is the declared column; with no `name` it
    is nameless, and a column search on it is refused (PRD-P21)."""
    flags = {'name': {'data': {'_': 'name', 'sort': 'name'}, 'name': name}}
    response = table.fetch_sync(request(*ALL, flags=flags, column_search={'name': {'value': 'bob'}}), session)
    if found is None:
        refused(response)
    else:
        assert ids(response) == found


def test_a_data_of_the_integer_zero_is_a_column_named_zero(session: Session):
    """SPEC-P20.2: DataTables' default `data` is the index, which a JSON body carries as the number 0; a column
    declared `'0'` is that column."""
    zero = SQLTable(people, [Column('0', field='id', type='number'), Column('name')])
    params = request('0', 'name', flags={'0': {'data': 0}}, column_search={'0': {'value': '3'}})
    response = zero.fetch_sync(params, session)
    assert response.error is None, response.error
    assert [row['0'] for row in response.data] == [3]


def test_a_column_search_on_a_nameless_column_is_refused(session: Session):
    """SPEC-P21.3: a column that only renders has no field to search."""
    params = request('', *ALL, flags={'': {'data': None}}, column_search={'': {'value': 'x'}})
    refused(table.fetch_sync(params, session))


def test_the_global_search_leaves_out_a_column_the_server_did_not_declare(session: Session):
    """SPEC-F41.1, SPEC-D6.3: a column the page only renders must not fail every search."""
    assert ids(table.fetch_sync(request('extra', *ALL, search={'value': 'ANN'}), session)) == [1, 3]


def test_a_global_search_over_no_searchable_column_is_refused(session: Session):
    params = request(*ALL)
    for column in params.columns:
        column.searchable = False
    refused(
        table.fetch_sync(params.model_copy(update={'search': params.search.model_copy(update={'value': 'a'})}), session)
    )


def test_a_search_value_hook_transforms_what_the_user_typed(session: Session):
    hooked = SQLTable(people, [Column('id'), Column('secret', search_value=lambda text: f's{text}')])
    params = request('id', 'secret')
    params.columns[1].search.multi_values = ['4']
    assert ids(hooked.fetch_sync(params, session)) == [4]


# -- exact matches in any case (PRD-F9) ---
#
# The spec statements cited speak of their own four-row fixture; the rows here are the same cases on this table.

CASED = ('id', 'kind', 'wrapped', 'ref', 'secret', 'size', 'plain')
#: A collation that tells `red` from `RED`, where the database's default does not. PostgreSQL's and SQLite's do.
CASE_SENSITIVE = {'mysql': 'utf8mb4_bin', 'mariadb': 'utf8mb4_bin', 'mssql': 'Latin1_General_CS_AS'}


class Wrapped(TypeDecorator[str]):
    """Text behind a type of the app's own, as an app declares one to convert values."""

    impl = String
    cache_ok = True


class Nested(TypeDecorator[str]):
    """A type of the app's own over another one: what is stored is two steps away."""

    impl = Wrapped
    cache_ok = True


class Guid(TypeDecorator[str]):
    """A UUID after SQLAlchemy's documented recipe ("Backend-agnostic GUID Type"): PostgreSQL stores it in its own
    `UUID` type, every other database as its 32 hex digits in a `CHAR(32)`. The type converts a value on its way
    into a statement, so `1f960000-0000-...` is compared as `1f9600000000...` there, and back on its way out."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(postgresql.UUID())
        return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return str(uuid.UUID(value)) if dialect.name == 'postgresql' else uuid.UUID(value).hex

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else str(uuid.UUID(str(value)))


def ref(row: int) -> str:
    """The UUID of a row of the `cased` table, as a client writes it."""
    return f'{row}f960000-0000-4000-8000-00000000000{row}'


@pytest.mark.parametrize(
    ('sql_type', 'declared', 'fits', 'named'),
    [
        (String(20), 'number', 'string', 'VARCHAR'),
        (String(20), 'date', 'string', 'VARCHAR'),
        (String(20), 'boolean', 'string', 'VARCHAR'),
        (Date(), 'number', 'date', 'DATE'),
        (DateTime(), 'number', 'date', 'DATETIME'),
        (Wrapped(20), 'number', 'string', 'VARCHAR'),
    ],
)
def test_a_declared_type_that_can_never_match_the_sql_type_fails_at_construction(
    sql_type: TypeEngine[Any], declared: Any, fits: str, named: str
):
    """SPEC-D22.1: the column finds no row for any value, or the server refuses the comparison, so the declaration is
    the integrator's mistake, found when the table is built. The message names the column and both types, and says
    what to declare instead. A `TypeDecorator` is judged by the type it stores."""
    source = Table(
        'dtb_test_tags', MetaData(), SQLColumn('id', Integer, primary_key=True), SQLColumn('amount', sql_type)
    )
    with pytest.raises(SchemaError) as raised:
        SQLTable(source, [Column('id'), Column('amount', type=declared)])
    message = str(raised.value)
    assert f'"amount" is declared {declared}' in message
    assert f'its SQL type is {named}' in message
    assert f'declare it {fits}' in message


def test_a_declared_type_that_can_match_or_is_not_judged_constructs_and_answers(session: Session):
    """SPEC-D22.2: an integer or a boolean column declared `string`, a boolean declared `number`, an expression
    without a type declared `number` and a `Uuid` declared `string` are the declarations a mismatch rule must leave
    alone: some answer today, and the database decides the others."""
    tags = Table(
        'dtb_test_tags',
        MetaData(),
        SQLColumn('id', Integer, primary_key=True, autoincrement=False),
        SQLColumn('n', Integer, nullable=True),
        SQLColumn('flag', Boolean, nullable=True),
        SQLColumn('uid', Uuid, nullable=True),
    )
    tags.create(session.connection())
    session.execute(
        tags.insert(),
        [{'id': 1, 'n': 5, 'flag': True, 'uid': uuid.UUID(ref(1))}, {'id': 2, 'n': None, 'flag': None, 'uid': None}],
    )
    table = SQLTable(
        tags,
        [
            Column('id'),
            Column('n', type='string'),
            Column('flag', type='string'),
            Column('flag_number', field='flag', type='number'),
            Column('uid', type='string'),
        ],
    )
    for name in ('n', 'flag', 'flag_number', 'uid'):
        found = table.fetch_sync(request('id', name, **criterion(name, 'null')), session)
        assert ids(found) == [2], name
    untyped = SQLTable(
        select(tags.c.id, literal_column('n').label('untyped')).subquery(),
        [Column('id'), Column('untyped', type='number')],
    )
    assert ids(untyped.fetch_sync(request('id', 'untyped', **criterion('untyped', '=', '5')), session)) == [1]


@pytest.fixture
def cased(session: Session) -> SQLTable:
    """A table whose text columns are case-sensitive on every database: `kind` holds `red` in two cases, `wrapped`
    the same values behind a `TypeDecorator`, `ref` a UUID the type `Guid` converts, `secret` is stored transformed
    (a search-value hook prefixes `enc:`), `size` is an enum (a native type on PostgreSQL and MySQL), and `uid` is
    the row's UUID in SQLAlchemy's own `Uuid` type, for which the table declares no column."""
    collation = CASE_SENSITIVE.get(session.get_bind().dialect.name)
    tags = Table(
        'dtb_test_tags',
        MetaData(),
        SQLColumn('id', Integer, primary_key=True, autoincrement=False),
        SQLColumn('kind', String(20, collation=collation)),
        SQLColumn('wrapped', Wrapped(20, collation=collation)),
        SQLColumn('nested', Nested(20), nullable=True),
        SQLColumn('ref', Guid(32)),
        SQLColumn('secret', String(20, collation=collation)),
        SQLColumn('size', Enum('Small', 'LARGE', name='dtb_test_size'), nullable=True),
        SQLColumn('uid', Uuid, nullable=True),
    )
    tags.create(session.connection())
    rows = [
        {'id': 1, 'kind': 'red', 'secret': 'enc:Alpha', 'size': 'Small'},
        {'id': 2, 'kind': 'reddish', 'secret': 'enc:Beta', 'size': 'LARGE'},
        {'id': 3, 'kind': 'blue', 'secret': 'enc:Gamma', 'size': None},
        {'id': 4, 'kind': 'RED', 'secret': 'enc:Delta', 'size': 'Small'},
        {'id': 5, 'kind': 'ÄPFEL', 'secret': 'enc:Eta', 'size': 'LARGE'},
    ]
    session.execute(
        tags.insert(),
        [
            {
                **row,
                'wrapped': row['kind'],
                'nested': row['kind'],
                'ref': ref(row['id']),
                'uid': uuid.UUID(ref(row['id'])),
            }
            for row in rows
        ],
    )
    same_case = session.execute(select(func.count()).select_from(tags).where(tags.c.kind == 'RED')).scalar_one()
    assert same_case == 1, (
        'The column `kind` is not case-sensitive in this database, so the tests of this fixture would pass on it '
        'without the library folding anything.'
    )
    return SQLTable(
        tags,
        [
            Column('id'),
            Column('kind'),
            Column('wrapped'),
            Column('ref'),
            Column('secret', search_value=lambda text: f'enc:{text}'),
            Column('size'),
            Column('plain', field='kind', fold_case=False),  # the same stored values, compared as sent (PRD-D21)
        ],
    )


@pytest.mark.parametrize(
    ('body', 'expected'),
    [
        (criterion('kind', '=', 'Red'), [1, 4]),  # SPEC-F9.2
        (criterion('kind', '!=', 'Red'), [2, 3, 5]),
        ({'column_search': {'kind': {'multi_values': ['rEd', 'BLUE']}}}, [1, 3, 4]),  # SPEC-F9.3, SPEC-F20.1
        # The database folds both sides: SQLite's lower() leaves `Ä` alone, where Python's would make it `ä`
        (criterion('kind', '=', 'ÄPFEL'), [5]),
        ({'column_search': {'kind': {'multi_values': ['ÄPFEL']}}}, [5]),
    ],
)
def test_an_exact_match_on_a_string_column_ignores_case(
    session: Session, cased: SQLTable, body: dict[str, Any], expected: list[int]
):
    """PRD-F9, also under a case-sensitive collation (SPEC-F9.5). A categorical filter is the same list as
    `multi_values` once the request is read."""
    assert ids(cased.fetch_sync(request(*CASED, **body), session)) == expected


@pytest.mark.parametrize(
    ('typed', 'value', 'expected'), [(String, 'rEd', [1, 4]), (None, 'rEd', []), (None, 'RED', [1, 4])]
)
def test_an_exact_match_on_an_expression_ignores_case_once_it_has_a_string_type(
    session: Session, cased: SQLTable, typed: type[String] | None, value: str, expected: list[int]
):
    """PRD-F9 with PRD-D10 (SPEC-F9.12): `upper(kind)` is `RED` for rows 1 and 4. Without a type the library cannot
    know the expression is text, and compares it as sent."""
    shouted = select(cased.source.c.id, literal_column('upper(kind)', typed).label('up')).subquery()
    table = SQLTable(shouted, [Column('id'), Column('up')])
    assert ids(table.fetch_sync(request('id', 'up', **criterion('up', '=', value)), session)) == expected


@pytest.mark.parametrize(
    ('body', 'expected'),
    [
        # An exact match is the plain comparison, which sends the value through the column's own type
        (criterion('ref', '=', ref(1).upper()), [1]),
        (criterion('ref', '!=', ref(1)), [2, 3, 4, 5]),
        ({'column_search': {'ref': {'multi_values': [ref(1), ref(4)]}}}, [1, 4]),
        # A search looks at the stored text, and its pattern is no value for the type to convert
        (criterion('ref', 'contains', '2F96'), [2]),
        ({'search': {'value': 'reddish'}}, [2]),  # every searchable column takes part, `ref` and `size` too
        ({'search': {'value': 'SMA'}}, [1, 4]),  # the enum column, searched as text
    ],
)
def test_a_column_with_a_type_of_its_own_is_compared_as_its_type_compares(
    session: Session, cased: SQLTable, body: dict[str, Any], expected: list[int]
):
    """PRD-F9: the library folds only a column whose SQL type is a string type. A `TypeDecorator` that converts
    values keeps its own comparison, and a search on it or on an `Enum` reads the stored text."""
    assert ids(cased.fetch_sync(request(*CASED, **body), session)) == expected


@pytest.mark.parametrize(
    'body', [criterion('secret', '=', 'alpha'), {'column_search': {'secret': {'multi_values': ['alpha']}}}]
)
def test_an_exact_match_on_a_hooked_column_adds_no_folding(session: Session, cased: SQLTable, body: dict[str, Any]):
    """PRD-F9, PRD-D15, PRD-D18: the stored `enc:Alpha` is not what `alpha` transforms to, and the column's own
    collation, case-sensitive here, is all that compares them."""
    assert ids(cased.fetch_sync(request(*CASED, **body), session)) == []


@pytest.mark.parametrize(
    ('body', 'expected'),
    [
        (criterion('plain', '=', 'Red'), []),  # with the fold: rows 1 and 4
        (criterion('plain', '=', 'red'), [1]),
        ({'column_search': {'plain': {'multi_values': ['RED']}}}, [4]),  # with the fold: rows 1 and 4
    ],
)
def test_an_exact_match_on_a_column_declared_without_the_fold_is_compared_as_sent(
    session: Session, cased: SQLTable, body: dict[str, Any], expected: list[int]
):
    """SPEC-D21.1: a case-sensitive collation tells `Red`, `red` and `RED` apart."""
    assert ids(cased.fetch_sync(request(*CASED, **body), session)) == expected


@pytest.mark.parametrize(('text', 'expected'), [('alpha', []), ('Alpha', [1])])
def test_a_column_search_on_a_hooked_column_compares_exactly(
    session: Session, cased: SQLTable, text: str, expected: list[int]
):
    """SPEC-F9.8: the library adds no folding on a hooked column, so the collation, case-sensitive here, compares
    `enc:alpha` with the stored `enc:Alpha`."""
    params = request(*CASED, column_search={'secret': {'value': text}})
    assert ids(cased.fetch_sync(params, session)) == expected


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('Alpha', [1]),
        ('  Alpha  ', [1]),  # trimmed before the hook
        ('alpha', []),  # the collation is case-sensitive here, and a hooked column adds no folding
        ('Alph', []),  # the whole value, not a part of it
    ],
)
def test_the_global_search_on_a_hooked_column_compares_exactly(
    session: Session, cased: SQLTable, text: str, expected: list[int]
):
    """SPEC-D12.1, SPEC-D12.3, PRD-F9, PRD-D21: the stored `enc:Alpha` is found by the whole text only, on every
    database; the other searchable columns hold none of these words."""
    assert ids(cased.fetch_sync(request(*CASED, search={'value': text}), session)) == expected


def test_an_exact_match_on_an_enum_column_ignores_case(session: Session, cased: SQLTable):
    """PostgreSQL stores an `Enum` as a type of its own, which has no `lower()`, and MySQL as an `ENUM`; the column
    is cast to text first on every database."""
    params = request(*CASED, column_search={'size': {'multi_values': ['small']}})
    assert ids(cased.fetch_sync(params, session)) == [1, 4]


def with_blank_rows(session: Session, cased: SQLTable) -> Table:
    """The `cased` table with two more rows: row 6 holds `''` in `kind` and `wrapped`, row 7 NULL; `ref`, `size`
    and `uid` are NULL in both."""
    tags = cased.source
    assert isinstance(tags, Table)
    blank, absent = dict.fromkeys(('kind', 'wrapped', 'nested'), ''), dict.fromkeys(('kind', 'wrapped', 'nested'))
    session.execute(tags.insert(), [{'id': 6, **blank}, {'id': 7, **absent}])
    return tags


@pytest.mark.parametrize(
    ('column', 'expected'),
    [
        ('wrapped', [6, 7]),  # a `TypeDecorator` over a string type stores text
        ('nested', [6, 7]),  # and so does one over that
        ('ref', [6, 7]),  # `''` is compared with what is stored: the type `Guid` itself refuses to convert it
        # An enum without `''` among its values: PostgreSQL refuses to compare a native enum with it. Only the
        # `postgresql` leg proves this row: the other databases answer the comparison, with the same rows
        ('size', [3, 6, 7]),
        ('uid', [6, 7]),  # a type that stores no text is never compared with `''`
    ],
)
def test_empty_is_the_empty_string_too_where_the_column_stores_text(
    session: Session, cased: SQLTable, column: str, expected: list[int]
):
    """SPEC-F26.3: what the column stores decides, seen through a `TypeDecorator`."""
    tags = with_blank_rows(session, cased)
    stored = SQLTable(
        tags,
        [
            Column('id'),
            Column('wrapped'),
            Column('nested'),
            Column('ref'),
            Column('size'),
            Column('uid'),
        ],
    )
    assert ids(stored.fetch_sync(request('id', column, **criterion(column, 'null')), session)) == expected


@pytest.mark.parametrize('column', ['ref', 'uid'])
def test_an_empty_entry_on_a_column_with_a_type_of_its_own_is_never_sent_as_a_value(
    session: Session, cased: SQLTable, column: str
):
    """SPEC-F26.4: both types refuse to convert `''`, so as a value of the list it failed the query."""
    tags = with_blank_rows(session, cased)
    typed = SQLTable(tags, [Column('id'), Column('ref'), Column('uid')])
    params = request('id', column, column_search={column: {'multi_values': ['']}})
    assert ids(typed.fetch_sync(params, session)) == [6, 7]


@pytest.mark.parametrize(('column', 'expected'), [('text', [6, 7]), ('day', [7])])
def test_empty_on_an_expression_without_a_type_follows_its_declaration(
    session: Session, cased: SQLTable, column: str, expected: list[int]
):
    """SPEC-F26.3: the library cannot know what such an expression stores. Declared a string (the default) it is
    compared with `''`; declared a date it is not, as a date expression would refuse the comparison."""
    tags = with_blank_rows(session, cased)
    untyped = select(tags.c.id, literal_column('wrapped').label('text')).subquery()
    declared = SQLTable(untyped, [Column('id'), Column('text'), Column('day', field='text', type='date')])
    assert ids(declared.fetch_sync(request('id', column, **criterion(column, 'null')), session)) == expected


@pytest.mark.parametrize(
    ('expression', 'empty', 'present'),
    [
        pytest.param(lambda email: func.nullif(email, 'NULL'), [2, 3, 6], [1, 4, 5, 7], id='nullif'),
        pytest.param(
            lambda email: case((email.in_(['NULL', '-']), None), else_=email), [2, 3, 6, 7], [1, 4, 5], id='case'
        ),
    ],
)
def test_a_text_that_stands_for_a_missing_value_is_empty_when_the_source_turns_it_into_null(
    session: Session, expression: Callable[[Any], Any], empty: list[int], present: list[int]
):
    """SPEC-F26.5: the recipe of docs/rules.md. A load wrote `NULL` (row 6) and `-` (row 7) for what is missing; the
    column is declared over `NULLIF` or a `CASE` that turns the sentinel into NULL, so the cell, `null` and `!null`, an
    empty list entry and the order all agree. A plain column keeps the sentinel a value: `null` does not find it. Breaks
    if the library stopped reading an expression column's NULLs as empty, or treated a stored sentinel as empty by
    default."""
    session.execute(
        people.insert(),
        [
            {'id': 6, 'name': 'Eve', 'team': 'red', 'secret': 's6', 'email_address': 'NULL'},
            {'id': 7, 'name': 'Fay', 'team': 'red', 'secret': 's7', 'email_address': '-'},
        ],
    )
    source = select(people.c.id, expression(people.c.email_address).label('email')).subquery()
    cleaned = SQLTable(source, [Column('id'), Column('email')])
    columns = ('id', 'email')

    def found(**body: Any) -> list[Any]:
        return sorted(ids(cleaned.fetch_sync(request(*columns, **body), session)))

    assert found(**criterion('email', 'null')) == empty
    assert found(**criterion('email', '!null')) == present
    assert found(column_search={'email': {'multi_values': ['']}}) == empty
    shown = {row['id']: row['email'] for row in cleaned.fetch_sync(request(*columns), session).data}
    assert shown[6] is None  # the cell shows what the filter finds
    assert (shown[7] is None) == (7 in empty)
    ordered = ids(cleaned.fetch_sync(request(*columns, order=[{'column': 1, 'dir': 'asc'}]), session))
    assert sorted(ordered[: len(empty)]) == empty  # the rows that are empty come first, as NULLs do
    plain = SQLTable(people, [Column('id'), Column('email', field='email_address')])
    kept = ids(plain.fetch_sync(request(*columns, **criterion('email', 'null')), session))
    assert sorted(kept) == [2, 3]  # the sentinels are values on the plain column


def test_empty_on_an_enum_is_the_empty_string_only_when_that_is_one_of_its_values(session: Session):
    """SPEC-F26.3: an enum that names `''` as a value can hold it, so there it is empty."""
    levels = Table(
        'dtb_test_tags',
        MetaData(),
        SQLColumn('id', Integer, primary_key=True, autoincrement=False),
        SQLColumn('level', Enum('', 'high', name='dtb_test_level'), nullable=True),
    )
    levels.create(session.connection())
    session.execute(levels.insert(), [{'id': 1, 'level': 'high'}, {'id': 2, 'level': ''}, {'id': 3, 'level': None}])
    levelled = SQLTable(levels, [Column('id'), Column('level')])
    assert ids(levelled.fetch_sync(request('id', 'level', **criterion('level', 'null')), session)) == [2, 3]


# -- SearchBuilder ---


@pytest.mark.parametrize(
    ('column', 'condition', 'values', 'expected'),
    [
        ('name', '=', ['Bob'], [2]),
        ('name', '!=', ['Bob'], [1, 3, 4, 5]),
        ('name', 'contains', ['NN'], [1, 3]),
        ('name', '!contains', ['nn'], [2, 4, 5]),
        ('name', 'starts', ['an'], [1, 3]),
        ('name', 'ends', ['%'], [3]),
        ('email', 'null', [], [2, 3]),  # NULL and the empty string
        ('email', '!null', [], [1, 4, 5]),
        ('age', '=', ['31'], [1, 4]),
        ('age', '>', ['31'], [2, 5]),
        ('age', '<', ['45'], [1, 4]),
        ('age', '<=', ['31.0'], [1, 4]),
        ('age', 'between', ['40', '60'], [2, 5]),
        ('age', '!between', ['40', '60'], [1, 3, 4]),  # row 3 has no age (PRD-F37)
        ('age', 'null', [], [3]),
        ('born', '=', ['1980-07-15'], [2]),
        ('born', '<', ['1980-07-15'], [5]),
        ('born', '<=', ['1980-07-15'], [2, 5]),
        ('born', '>', ['1980-07-15'], [1, 4]),
        ('born', '>=', ['1980-07-15'], [1, 2, 4]),
        ('born', 'between', ['1980-01-01', '1994-03-01'], [1, 2]),
        ('born', 'contains', ['1980'], [2]),
        ('born', 'contains', ['2001-12'], [4]),
        ('born', 'contains', ['1980-07-15'], [2]),  # a full day is a span of one day
        ('born', 'contains', ['1980-07-14'], []),  # SPEC-F22.1: and of no more, so the day before Bob's holds nobody
        # SPEC-F25.3: the spellings of a number beside plain digits: a sign, a point at the end, a point first and an
        # exponent
        ('age', '=', ['+31'], [1, 4]),
        ('age', '=', ['31.'], [1, 4]),
        ('age', '=', ['.31E+2'], [1, 4]),
        ('age', '=', [' 31 '], [1, 4]),  # white space around a value is ignored, here and for the dates below
        ('born', 'contains', [' 1980 '], [2]),
        # SPEC-F21.9: a time of day after the day is ignored: after a space, with seconds and a fraction, as
        # SearchBuilder sends it for a formatted column; after a `T`, without seconds
        ('born', '=', ['1980-07-15 10:00:00.250'], [2]),
        ('born', '=', ['1980-07-15T23:59'], [2]),
        ('born', 'contains', ['1980-07-15 10:00:00'], [2]),  # SPEC-F22.5: `contains` reads a day as `=` does
        ('seen', '=', ['2026-07-15'], [1]),  # a datetime column compares by day
        ('seen', '>', ['2026-07-15'], [2]),
        ('seen', '>=', ['2026-07-15'], [1, 2]),
        ('seen', 'between', ['2026-07-14', '2026-07-15'], [1, 5]),
        # PRD-F37 (SPEC-F37.1): NULL passes every negative condition, where SQL's NOT drops it. `email` is NULL in
        # row 2 and `''` in row 3. "Not empty" above is the one negation it does not pass
        ('email', '!=', ['ann@example.org'], [2, 3, 4, 5]),
        ('email', '!ends', ['org'], [2, 3]),
        ('email', '!contains', ['example'], [2, 3]),
        ('age', '!=', ['31'], [2, 3, 5]),  # SPEC-F37.2: row 3 has no age
    ],
)
def test_searchbuilder_conditions(
    session: Session, column: str, condition: str, values: list[str], expected: list[int]
):
    """SPEC-F21.1 (the six conditions on the date column `born`), SPEC-F21.4 (`seen` is a naive datetime, compared by
    day), SPEC-F26.1 (`null` on `email` finds NULL and the empty text, `!null` the rest), SPEC-F27.6 (a strict `<`,
    `between` and `!between` on a number, where row 3 has no age)."""
    assert ids(table.fetch_sync(request(*ALL, **criterion(column, condition, *values)), session)) == expected


@pytest.mark.parametrize(
    ('column', 'condition', 'values', 'expected'),
    [
        ('age', 'between', ['45', '9'], [1, 2, 4]),  # as text `45` comes first, as a number last
        ('age', '!between', ['45', '9'], [3, 5]),  # row 3 has no age, which passes (PRD-F37)
        ('born', 'between', ['1994-03-01', '1980-07-15'], [1, 2]),  # both ends are whole days, the later one too
    ],
)
def test_the_two_values_of_between_are_a_range_in_either_order(
    session: Session, column: str, condition: str, values: list[str], expected: list[int]
):
    """SPEC-F27.9: catches reversed bounds being read as an empty range, which gave `between` no row and `!between`
    every row, and bounds ordered by their text rather than their number or day."""
    assert ids(table.fetch_sync(request(*ALL, **criterion(column, condition, *values)), session)) == expected


#: `seen_tz` holds 23:59 UTC on the 15th (row 1), 00:00 UTC on the 16th (row 2), nothing (row 3), noon on 2025-01-01
#: (row 4) and 20:00 on the 14th (row 5): the instants of rows 1 and 2 are a minute apart, their UTC days are not
AWARE_CASES = [
    ('=', ['2026-07-15'], [1]),
    ('>', ['2026-07-15'], [2]),
    ('<', ['2026-07-15'], [4, 5]),
    ('>=', ['2026-07-16'], [2]),
    ('>=', ['2026-07-15'], [1, 2]),
    ('<=', ['2026-07-15'], [1, 4, 5]),
    ('between', ['2026-07-15', '2026-07-15'], [1]),  # both ends are whole days
    ('between', ['2026-07-14', '2026-07-15'], [1, 5]),
    ('!between', ['2026-07-15', '2026-07-15'], [2, 3, 4, 5]),  # row 3 has no value, which passes (PRD-F37)
    ('contains', ['2026-07-16'], [2]),  # a span of a UTC day, and of a UTC month
    ('contains', ['2026-07'], [1, 2, 5]),
]


@pytest.mark.parametrize(('condition', 'values', 'expected'), AWARE_CASES)
def test_a_timezone_aware_column_compares_by_utc_day(
    session: Session, condition: str, values: list[str], expected: list[int]
):
    """SPEC-F21.2, SPEC-F21.3, SPEC-F21.11, SPEC-F22.3: every condition compares the UTC day. The day bounds are UTC
    midnights. Every container job runs Python in a zone far from UTC (TZ, America/Adak, UTC-10/-9), which catches a
    bound built from local time; the guard fails a job that forgot it. The PostgreSQL jobs also catch naive bounds
    through their session zone (PGTZ, UTC+14), which reads a naive bound as local to it. SQL Server and MySQL cannot
    tell a naive bound from a UTC one, so there the test shows the column works and that no bound is local. A
    developer machine whose local zone is not UTC catches local-time bounds on SQLite too."""
    if os.environ.get('DTB_SQL_URL'):  # a container job; SQLite and local runs are not held to a zone
        assert datetime.now().astimezone().utcoffset(), (
            'Set TZ to a zone far from UTC, or local-time bounds go unchecked.'
        )
    if session.get_bind().dialect.name == 'postgresql':
        zone = session.execute(select(func.current_setting('TimeZone'))).scalar()
        assert zone not in ('UTC', 'Etc/UTC'), 'Set PGTZ to a zone far from UTC, or this test checks nothing.'
    aware = SQLTable(people, [Column('id'), Column('seen_tz')])
    assert ids(aware.fetch_sync(request('id', 'seen_tz', **criterion('seen_tz', condition, *values)), session)) == (
        expected
    )


@pytest.mark.parametrize(('condition', 'values', 'expected'), AWARE_CASES)
async def test_a_timezone_aware_column_compares_by_utc_day_through_the_async_fetch(
    async_session: AsyncSession, condition: str, values: list[str], expected: list[int]
):
    """SPEC-F21.3, SPEC-F21.11 through the async driver. asyncpg ignores PGTZ, so its session was UTC beside the sync one in
    UTC+14, and the one case that tells a naive bound from a UTC one went unchecked on that leg: the zone is asserted
    here as it is there, and the conftest asks asyncpg for it."""
    if async_session.get_bind().dialect.name == 'postgresql':
        zone = (await async_session.execute(select(func.current_setting('TimeZone')))).scalar()
        assert zone not in ('UTC', 'Etc/UTC'), 'The async session is in UTC, so this test checks nothing.'
    aware = SQLTable(people, [Column('id'), Column('seen_tz')])
    response = await aware.fetch(request('id', 'seen_tz', **criterion('seen_tz', condition, *values)), async_session)
    assert ids(response) == expected


def test_a_mysql_timestamp_is_read_through_the_session_time_zone(session: Session):
    """SPEC-F21.12 (PRD-F21): a TIMESTAMP is stored as UTC and read back in the session's `time_zone`, and the library
    adds no conversion, so `=` finds it by the day of that zone. 23:30 UTC on the 15th, written in a UTC session, is
    01:30 on the 16th in a session at +02:00, which the 16th finds and the 15th does not; back in UTC the 15th finds
    it. The zone is set on this session's own connection and restored."""
    dialect = session.get_bind().dialect.name
    if dialect not in ('mysql', 'mariadb'):
        pytest.skip('SPEC-F21.12 is about a MySQL or MariaDB TIMESTAMP, which no other engine has')
    stamps = Table(
        'dtb_test_stamps', MetaData(), SQLColumn('id', Integer, primary_key=True), SQLColumn('at', mysql.TIMESTAMP)
    )
    stamps.create(session.connection())
    original = session.execute(text('SELECT @@session.time_zone')).scalar_one()
    try:
        session.execute(text("SET time_zone = '+00:00'"))
        session.execute(stamps.insert(), [{'id': 1, 'at': datetime(2026, 7, 15, 23, 30)}])
        table = SQLTable(stamps, [Column('id'), Column('at')])

        def found(day: str) -> list[Any]:
            return ids(table.fetch_sync(request('id', 'at', **criterion('at', '=', day)), session))

        session.execute(text("SET time_zone = '+02:00'"))
        assert (found('2026-07-16'), found('2026-07-15')) == ([1], [])
        session.execute(text("SET time_zone = '+00:00'"))
        assert (found('2026-07-15'), found('2026-07-16')) == ([1], [])
    finally:
        session.execute(text('SET time_zone = :zone'), {'zone': original})


def test_an_aware_value_written_with_an_offset_is_read_without_it_on_sqlite(session: Session):
    """SPEC-F21.8: SQLite stores the text it is given, so 01:00 at +02:00 on the 16th, which is 23:00 UTC on the 15th,
    reads as 01:00 UTC on the 16th (PRD-F21: the app must write UTC there). Row 2 is 00:00 UTC on the 16th."""
    if session.get_bind().dialect.name != 'sqlite':
        pytest.skip('SPEC-F21.8 is about SQLite, which stores the offset as text and reads it back as written')
    session.execute(
        text(
            'INSERT INTO dtb_test_people (id, name, team, secret, seen_tz) '
            "VALUES (6, 'Eve', 'red', 's6', '2026-07-16 01:00:00+02:00')"
        )
    )
    aware = SQLTable(people, [Column('id'), Column('seen_tz')])
    on_the_16th = request('id', 'seen_tz', **criterion('seen_tz', '=', '2026-07-16'))
    on_the_15th = request('id', 'seen_tz', **criterion('seen_tz', '=', '2026-07-15'))
    assert ids(aware.fetch_sync(on_the_16th, session)) == [2, 6]
    assert ids(aware.fetch_sync(on_the_15th, session)) == [1]


def test_the_global_search_finds_a_timezone_aware_column_by_its_utc_day(session: Session):
    """SPEC-F42.1, PostgreSQL's proof: in the job's session zone (UTC+14) the day text of row 1, 23:59 UTC on the
    15th, is the 16th, and row 5's is the 15th. A column search runs the same expression; the test below goes
    through one."""
    aware = SQLTable(people, [Column('id'), Column('seen_tz')])
    assert ids(aware.fetch_sync(request('id', 'seen_tz', search={'value': '2026-07-15'}), session)) == [1]


def test_a_column_search_finds_a_value_stored_at_another_offset_by_its_utc_day(session: Session):
    """SPEC-F42.2, SQL Server's proof, on the table as the database describes it: 2026-08-01 23:00 at -05:00 is
    2026-08-02 04:00 UTC, and the day text of a `DATETIMEOFFSET` is the day in the offset it was stored with. A
    reflected `DATETIMEOFFSET` is SQLAlchemy's SQL Server type, which does not say `timezone=True`, so this is also
    the proof that such a column is known to carry a zone.

    The row is added in this test's transaction only. SQLite and MySQL store no offset (PRD-F21), so there it is
    written as its UTC time, as such a column holds it, and the test only shows the search works. On PostgreSQL it
    passes with the session-zone text as well, since UTC+14 puts 04:00 UTC on the same day: PostgreSQL's proof is
    the test above."""
    stored = datetime(2026, 8, 1, 23, 0, tzinfo=timezone(timedelta(hours=-5)))
    if session.get_bind().dialect.name not in ('postgresql', 'mssql'):
        stored = stored.astimezone(UTC)
    session.execute(people.insert(), {'id': 6, 'name': 'Eve', 'team': 'red', 'secret': 's6', 'seen_tz': stored})
    reflected = Table(people.name, MetaData(), autoload_with=session.connection())
    aware = SQLTable(reflected, [Column('id'), Column('seen_tz')])
    params = request('id', 'seen_tz', column_search={'seen_tz': {'value': '2026-08-02'}})
    assert ids(aware.fetch_sync(params, session)) == [6]


def test_the_day_text_of_a_column_without_a_zone_is_the_day_it_stores(session: Session):
    """PRD-F42 changes only a timezone-aware column. PostgreSQL's proof: were a `timestamp` taken to UTC like a
    `timestamptz`, the job's session zone (UTC+14) would render row 1, stored as 23:59 on the 15th, on the 16th,
    and row 5, stored as 20:00 on the 14th, on the 15th."""
    naive = SQLTable(people, [Column('id'), Column('seen')])
    assert ids(naive.fetch_sync(request('id', 'seen', search={'value': '2026-07-15'}), session)) == [1]


@pytest.mark.parametrize(
    ('column', 'search', 'expected'),
    [
        # A column with a time, through the categorical regex: each entry is its day, not the instant of midnight,
        # and on a timezone-aware column the day is bounded by UTC midnights
        ('seen_tz', {'value': '^(2026-07-15|2025-01-01)$', 'regex': True}, [1, 4]),
        # SPEC-F21.7 beside a day on a date column: an entry of only white space is empty, the row without a date,
        # and joins the day, around which white space is ignored as around any value
        ('born', {'multi_values': [' ', ' 1980-07-15 ']}, [2, 3]),
    ],
)
def test_a_list_on_a_date_column_matches_each_entry_as_a_day(
    session: Session, column: str, search: dict[str, Any], expected: list[int]
):
    """SPEC-F21.5, SPEC-F21.7, and SPEC-F21.9's white space: a categorical value or `multi_values` entry is a day
    and matches as `=` does."""
    dated = SQLTable(people, [Column('id'), Column('born'), Column('seen_tz')])
    params = request('id', 'born', 'seen_tz', column_search={column: search})
    assert ids(dated.fetch_sync(params, session)) == expected


def test_a_list_on_a_column_only_declared_a_date_matches_by_day_too(session: Session):
    """SPEC-F21.5 on an expression without a SQL type, declared a date, whose values hold a time: an entry is the
    day `=` matches, not a value the column must equal."""
    source = select(people.c.id, literal_column('seen').label('stamp')).subquery()
    stamped = SQLTable(source, [Column('id'), Column('stamp', type='date')])
    params = request('id', 'stamp', column_search={'stamp': {'multi_values': ['2026-07-15']}})
    assert ids(stamped.fetch_sync(params, session)) == [1]


# -- a boolean column ---
#
# `active` is a SQLAlchemy `Boolean` declared without a type (BOOLEAN, also on SQLite, which stores 1 and 0; BIT on
# SQL Server; TINYINT(1) on MySQL): true for ids 1 and 4, false for 2 and 5, NULL for 3. The spec statements cited
# speak of their own four-row fixture; the rows here are the same cases on this one.

FLAGGED = ('id', 'name', 'active')
flagged = SQLTable(people, [Column('id'), Column('name'), Column('active')])


def library_log(caplog: pytest.LogCaptureFixture) -> str:
    """Everything the library's records hold: message, arguments and extra fields."""
    return ' '.join(repr(vars(record)) for record in caplog.records if record.name.startswith('datatables_backend'))


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('TRUE', [1, 4]),
        ('1', [1, 4]),
        ('false', [2, 5]),
        ('0', [2, 5]),
        (' true ', [1, 4]),  # SPEC-F43.4: surrounding whitespace is ignored, as for numbers and dates
    ],
)
def test_searchbuilder_equality_on_a_boolean_column(session: Session, value: str, expected: list[int]):
    """SPEC-F43.1, SPEC-F43.4, and SPEC-D8.1 for `active`: a `Boolean` column is boolean without being declared so."""
    assert ids(flagged.fetch_sync(request(*FLAGGED, **criterion('active', '=', value)), session)) == expected


@pytest.mark.parametrize(
    ('condition', 'values', 'expected'),
    [
        ('!=', ['true'], [2, 3, 5]),  # NULL is not true (PRD-F37)
        ('null', [], [3]),
        ('!null', [], [1, 2, 4, 5]),
    ],
)
def test_the_other_conditions_a_boolean_column_takes(
    session: Session, condition: str, values: list[str], expected: list[int]
):
    """SPEC-F43.2."""
    assert ids(flagged.fetch_sync(request(*FLAGGED, **criterion('active', condition, *values)), session)) == expected


@pytest.mark.parametrize(
    ('condition', 'values'),
    [
        ('=', ['yes']),  # each of the three is a value some reading of "boolean" would take
        ('=', ['2']),
        ('=', ['t']),
        ('<', ['true']),  # a number's condition
        ('contains', ['true']),  # a string's condition
    ],
)
def test_a_value_or_condition_a_boolean_column_does_not_take_is_refused(
    session: Session, caplog: pytest.LogCaptureFixture, condition: str, values: list[str]
):
    """SPEC-F43.3, and SPEC-D8.1's `<`: answered as `invalid_filter`, never a database error. For the value `yes`,
    neither the reason nor the log record of the refusal holds it."""
    with caplog.at_level(logging.DEBUG, logger='datatables_backend'):
        reason = refused(flagged.fetch_sync(request(*FLAGGED, **criterion('active', condition, *values)), session))
    if values == ['yes']:
        assert 'yes' not in reason
        assert 'yes' not in library_log(caplog)


def test_the_global_search_skips_a_boolean_column(session: Session):
    """SPEC-F44.1: a table whose only searchable column is boolean has no usable column for a global search
    (PRD-F13). Were the column searched, the request would be answered."""
    only = SQLTable(people, [Column('id', searchable=False), Column('active')])
    refused(only.fetch_sync(request('id', 'active', search={'value': 'x'}), session))


def test_a_skipped_boolean_column_logs_nothing(session: Session, caplog: pytest.LogCaptureFixture):
    """SPEC-E13.2 for what is idle or left out by type: the boolean column the global search skips, an idle SearchPanes
    pane and an idle ColumnControl search, which a client sends on every request."""
    idle = {'columnControl': {'search': {'value': '', 'logic': 'contains'}}}
    body = {
        'draw': 1,
        'search': {'value': 'ann'},
        'searchPanes': {'name': []},
        'columns': [{'data': name, **idle} for name in FLAGGED],
    }
    with caplog.at_level(logging.DEBUG, logger='datatables_backend'):
        assert ids(flagged.fetch_sync(parse_request(body), session)) == [1, 3]
    assert library_log(caplog) == ''


@pytest.mark.parametrize(
    ('search', 'expected'),
    [
        ({'value': 'false'}, [2, 5]),  # SPEC-F44.2: the value itself, not text the cell contains
        ({'value': '^(true|0)$', 'regex': True}, [1, 2, 4, 5]),  # SPEC-F44.3
        ({'multi_values': ['TRUE']}, [1, 4]),  # SPEC-F44.3
    ],
)
def test_a_column_search_on_a_boolean_column_matches_its_values_exactly(
    session: Session, search: dict[str, Any], expected: list[int]
):
    """A plain value, a categorical filter and `multi_values` each take the values of SPEC-F43.1."""
    params = request(*FLAGGED)
    params.columns[2].search = params.columns[2].search.model_copy(update=search)
    assert ids(flagged.fetch_sync(params, session)) == expected


EVERY_TYPE = ('id', 'email', 'age', 'born', 'team', 'active')
every_type = SQLTable(
    people,
    [
        Column('id'),
        Column('email', field='email_address'),
        Column('age'),
        Column('born'),
        Column('team'),
        Column('active'),
    ],
)
#: The forms SPEC-F18.1 names: a metacharacter that is no structure of a categorical filter, or an escape PRD-F19's
#: set does not hold
REGEXES = ['^a|b$', 'a.b', 'a*', '(a)', '[ab]', 'a{2}', r'\d', r'\w', r'\n', 'a+', 'a?', '^(a|b)']
#: Every character PRD-F6 counts as a space: U+0020, LF, CR, U+2028 and U+2029
SPACES = ' \n\r' + chr(0x2028) + chr(0x2029)


@pytest.mark.parametrize(
    ('column', 'search', 'expected'),
    [
        # `email` is NULL in row 2 and `''` in row 3. An empty alternative beside a value, a group that holds
        # nothing else, anchors around nothing, and a bar between nothing (PRD-F17)
        ('email', {'value': r'^(|ann@example\.org)$', 'regex': True}, [1, 2, 3]),
        ('email', {'value': '^()$', 'regex': True}, [2, 3]),
        ('email', {'value': '^$', 'regex': True}, [2, 3]),
        ('email', {'value': '|', 'regex': True}, [2, 3]),
        ('email', {'value': r'ann@example\.org|', 'regex': True}, [1, 2, 3]),  # at the end, unanchored
        ('email', {'value': r'^(ann@example\.org||cy@example\.org)$', 'regex': True}, [1, 2, 3, 4]),
        ('email', {'multi_values': ['', 'ann@example.org']}, [1, 2, 3]),  # SPEC-F20.2
        ('team', {'value': '^(|red)$', 'regex': True}, [1, 3]),  # SPEC-F17.2: no row holds an empty `team`
        ('age', {'multi_values': [' ', '31']}, [1, 3, 4]),  # white space holds nothing either (PRD-F33)
        ('active', {'multi_values': [' ', '1']}, [1, 3, 4]),  # SPEC-F44.3, SPEC-F44.4
    ],
)
def test_an_empty_alternative_or_entry_matches_the_rows_without_a_value(
    session: Session, column: str, search: dict[str, Any], expected: list[int]
):
    """SPEC-F17.1 (the first case, a NULL, an empty text and a value), SPEC-F17.2, SPEC-F17.3 (`^()$`), SPEC-F17.4,
    SPEC-F20.2, SPEC-F33.5, SPEC-F44.4: on a column of any type. The date column's row is in
    test_a_list_on_a_date_column_matches_each_entry_as_a_day."""
    params = request(*EVERY_TYPE, column_search={column: search})
    assert ids(every_type.fetch_sync(params, session)) == expected


def test_white_space_is_a_value_on_a_string_column(session: Session):
    """SPEC-F20.4: an exact value is never rewritten, so an entry of one space is that value, not an empty
    entry, which would find row 2's NULL too. No row holds a space. SQL Server compares strings without their
    trailing spaces (MEASURED on the `sqlserver` leg, run 45044380547), so there the space equals the `''` of
    row 3. So does MariaDB's default collation `utf8mb4_uca1400_ai_ci`, which is PAD SPACE (MEASURED 2026-11-24 on
    12.3.3: `information_schema.COLLATIONS` says `uca1400_ai_ci` is `PAD SPACE`, `uca1400_nopad_ai_ci` `NO PAD`),
    unlike MySQL's `utf8mb4_0900_ai_ci`, which is NO PAD."""
    params = request(*EVERY_TYPE, column_search={'email': {'multi_values': [' ']}})
    found = [3] if session.get_bind().dialect.name in ('mssql', 'mariadb') else []
    assert ids(every_type.fetch_sync(params, session)) == found


@pytest.mark.parametrize('pattern', [*REGEXES, '^red|blue$', r'r\ed'])
def test_a_regex_that_is_no_categorical_filter_is_refused(session: Session, pattern: str):
    """SPEC-F18.1: each form the statement names, and two more: anchors without the group anchor each value at one end
    only, and a backslash escapes only the characters of PRD-F19: `\\e` is no `e`. Read as values, these two would
    find rows."""
    params = request(*EVERY_TYPE, column_search={'team': {'value': pattern, 'regex': True}})
    refused(every_type.fetch_sync(params, session))


def test_a_long_regex_that_is_no_categorical_filter_is_refused_as_a_regex(session: Session):
    """SPEC-F14.6: PRD-F14 measures a categorical alternative after unescaping, not the raw pattern; a long
    non-categorical regex measured as raw text answers `value_too_long` instead. PRD-F15 has no such clause: a
    control character in a regex column search stops being found if the regex is refused first as a form."""
    params = request(*EVERY_TYPE, column_search={'team': {'value': 'a.' + 'x' * 256, 'regex': True}})
    refused(every_type.fetch_sync(params, session))
    params = request(*EVERY_TYPE, column_search={'team': {'value': 'a.\x01', 'regex': True}})
    refused(every_type.fetch_sync(params, session), 'invalid_character')


@pytest.mark.parametrize('pattern', [r'^C:\\$', r'^(C:\\)$', r'C:\\'])
def test_a_value_that_ends_in_an_escaped_backslash_is_read_in_every_form(session: Session, pattern: str):
    r"""SPEC-F19.2: row 6 holds `C:\`. In the form `^v$` the `$` after `C:\\` is the anchor, not an escaped `$`;
    taken for one, the filter was refused as a regex."""
    session.execute(people.insert(), [{'id': 6, 'name': 'Eve', 'team': 'C:\\', 'secret': 's6'}])
    params = request(*EVERY_TYPE, column_search={'team': {'value': pattern, 'regex': True}})
    assert ids(every_type.fetch_sync(params, session)) == [6]


@pytest.mark.parametrize('value', ['red', '^(red)$'])
def test_a_regex_global_search_with_a_word_is_refused(session: Session, value: str):
    """SPEC-F18.2: the categorical forms are column searches only, so any regex in the global search is "any other"."""
    refused(every_type.fetch_sync(request(*EVERY_TYPE, search={'value': value, 'regex': True}), session))


@pytest.mark.parametrize('value', ['', '   ', '"'])
def test_a_regex_global_search_with_no_word_is_idle(session: Session, value: str):
    """SPEC-F18.3: PRD-F6 reduces these to nothing, so the flag has nothing to refuse and every row is answered."""
    params = request(*EVERY_TYPE, search={'value': value, 'regex': True})
    assert ids(every_type.fetch_sync(params, session)) == [1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('red', [1, 4]),  # SPEC-F16.1: an exact match, in any case (PRD-F9); not row 2, `reddish`
        ('red|blue', [1, 3, 4]),  # SPEC-F16.2
        ('^red$', [1, 4]),
        ('^(red|blue)$', [1, 3, 4]),
    ],
)
def test_a_categorical_filter_matches_whole_values(session: Session, cased: SQLTable, value: str, expected: list[int]):
    """SPEC-F16.1, SPEC-F16.2: the four forms of PRD-F16 are one IN list, never a substring."""
    params = request(*CASED, column_search={'kind': {'value': value, 'regex': True}})
    assert ids(cased.fetch_sync(params, session)) == expected


@pytest.mark.parametrize('pattern', [r'^(a-b/c)$', r'^(a\-b\/c)$'])
def test_a_hyphen_and_a_slash_are_read_escaped_or_not(session: Session, pattern: str):
    """SPEC-F16.3: PRD-F16 accepts `-` and `/` either way."""
    session.execute(people.insert(), [{'id': 6, 'name': 'Eve', 'team': 'a-b/c', 'secret': 's6'}])
    params = request(*EVERY_TYPE, column_search={'team': {'value': pattern, 'regex': True}})
    assert ids(every_type.fetch_sync(params, session)) == [6]


@pytest.mark.parametrize('search', [{'value': '', 'regex': True}, {'value': '', 'multi_values': []}])
def test_an_empty_categorical_filter_or_list_is_idle(session: Session, search: dict[str, Any]):
    """SPEC-F16.4, SPEC-F20.3: no value, so no filter, with or without the flag and the empty list. An idle search is
    not counted either: 65 columns that hold one are answered (PRD-F38 counts the searches that hold a value)."""
    params = request(*EVERY_TYPE, column_search={'team': search})
    assert ids(every_type.fetch_sync(params, session)) == [1, 2, 3, 4, 5]
    many = request(*(('team',) * 65), column_search={'team': search})
    assert ids(every_type.fetch_sync(many, session)) == [1, 2, 3, 4, 5]


def test_every_character_of_the_escape_set_matches_itself_escaped(session: Session):
    """SPEC-F19.1: the whole set of PRD-F19 in one value, which no other row holds."""
    whole_set = '/.*+?|()[]{}\\$^-'
    session.execute(people.insert(), [{'id': 6, 'name': 'Eve', 'team': whole_set, 'secret': 's6'}])
    escaped = ''.join('\\' + character for character in whole_set)
    params = request(*EVERY_TYPE, column_search={'team': {'value': f'^({escaped})$', 'regex': True}})
    assert ids(every_type.fetch_sync(params, session)) == [6]


@pytest.mark.parametrize(
    ('condition', 'value', 'expected'),
    [
        ('contains', 'NN', [1, 3]),
        ('!contains', 'NN', [2, 4, 5]),
        ('starts', 'AN', [1, 3]),
        ('!starts', 'AN', [2, 4, 5]),
        ('ends', 'EE', [5]),
        ('!ends', 'EE', [1, 2, 3, 4]),
        ('ends', 'NN', [1]),
    ],
)
def test_a_string_condition_ignores_case_and_its_negation_is_the_complement(
    session: Session, condition: str, value: str, expected: list[int]
):
    """SPEC-F9.4: names are `Ann`, `Bob`, `anna_100%`, `Cy` and `Dee`; the value is in another case than stored."""
    assert ids(table.fetch_sync(request(*ALL, **criterion('name', condition, value)), session)) == expected


@pytest.mark.parametrize(
    ('condition', 'value', 'expected'),
    [
        ('starts', 'anna_', [3]),
        ('ends', '00%', [3]),
        ('!starts', 'anna_', [1, 2, 4, 5, 6, 7, 8]),
        ('!ends', '00%', [1, 2, 4, 5, 6, 7, 8]),
        ('starts', 'a[b', [7]),
    ],
)
def test_a_wildcard_in_a_starts_or_ends_value_is_text(
    session: Session, condition: str, value: str, expected: list[int]
):
    """SPEC-F11.8: row 6 matches `anna_` and `00%` only if `_` and `%` are wildcards, rows 7 and 8 `a[b` only if `[`
    opens a set. (The `[` row is proved on SQL Server by CI.)"""
    session.execute(
        people.insert(),
        [
            {'id': 6, 'name': 'annaX100Y', 'team': 'x', 'secret': 's6'},
            {'id': 7, 'name': 'a[bc', 'team': 'x', 'secret': 's7'},
            {'id': 8, 'name': 'abc', 'team': 'x', 'secret': 's8'},
        ],
    )
    assert ids(table.fetch_sync(request(*ALL, **criterion('name', condition, value)), session)) == expected


@pytest.mark.parametrize('text', ['nan', 'NaN', 'inf', '-inf', 'Infinity'])
def test_a_non_finite_number_is_refused_as_no_number(session: Session, text: str):
    """SPEC-F25.9: Python's `float` reads each as a number, and no database column holds one; the integer column
    here and the float column of `wide` both refuse them."""
    refused(table.fetch_sync(request(*ALL, **criterion('age', '=', text)), session), 'invalid_number')
    params = request(*WIDE, **criterion('ratio', '=', text))
    refused(wide(session).fetch_sync(params, session), 'invalid_number')


@pytest.mark.parametrize(
    ('body', 'expected'),
    [
        ({'column_search': {'team': {'value': 'red'}}, **criterion('age', '=', '31')}, [1]),
        (  # the group is a part of the request: row 3 is `red` too, and `anna_100%` has no age
            {
                'column_search': {'team': {'value': 'blue'}},
                'searchBuilder': {
                    'logic': 'OR',
                    'criteria': [
                        {'condition': '=', 'origData': 'age', 'value': ['45']},
                        {'condition': '=', 'origData': 'name', 'value': ['Cy']},
                    ],
                },
            },
            [2],
        ),
    ],
)
def test_a_column_search_and_the_searchbuilder_tree_combine_with_and(
    session: Session, body: dict[str, Any], expected: list[int]
):
    """SPEC-F12.3: each half alone answers more rows (the column search 1, 3 and 2, 5; the tree 1, 4 and 2, 4), so
    the answer is their intersection."""
    assert ids(table.fetch_sync(request(*ALL, **body), session)) == expected


def test_two_column_searches_combine_with_and(session: Session):
    """SPEC-F12.1: `red` on `team` alone finds rows 1 and 3, `example` on `email` rows 1, 4 and 5; both → row 1."""
    team = {'team': {'value': 'red'}}
    email = {'email': {'value': 'example'}}
    answers = [ids(table.fetch_sync(request(*ALL, column_search=search), session)) for search in (team, email)]
    assert answers == [[1, 3], [1, 4, 5]]
    assert ids(table.fetch_sync(request(*ALL, column_search={**team, **email}), session)) == [1]


@pytest.mark.parametrize(('search', 'expected'), [('example', [5]), ('cy', [])])
def test_a_column_search_and_the_global_search_combine_with_and(session: Session, search: str, expected: list[int]):
    """SPEC-F12.2: `blue` on `team` alone finds rows 2 and 5. The global search finds rows 1, 4 and 5 for `example`
    and row 4 for `cy`, so the answer is the intersection, and not the union."""
    body = {'column_search': {'team': {'value': 'blue'}}, 'search': {'value': search}}
    assert ids(table.fetch_sync(request(*ALL, **body), session)) == expected


def test_a_column_the_request_marks_unsearchable_is_left_out_of_the_global_search_only(session: Session):
    """SPEC-F41.3: the library declares `name` searchable, so only the global search listens to the request's flag;
    row 3 is the only one holding `anna` in any column."""
    flags = {'name': {'searchable': False}}
    assert ids(table.fetch_sync(request(*ALL, search={'value': 'anna'}, flags=flags), session)) == []
    params = request(*ALL, column_search={'name': {'value': 'anna'}}, flags=flags)
    assert ids(table.fetch_sync(params, session)) == [3]


@pytest.mark.parametrize('search', [{'value': 'x'}, {'multi_values': ['x']}])
def test_a_filter_with_a_value_on_an_undeclared_column_is_refused_and_an_empty_one_is_not(
    session: Session, search: dict[str, Any]
):
    """SPEC-F41.4: a value, or a list that holds one, on `extra`, which the table does not declare; an empty list is
    no filter (PRD-D6)."""
    names = (*ALL, 'extra')
    refused(table.fetch_sync(request(*names, column_search={'extra': search}), session))
    params = request(*names, column_search={'extra': {'multi_values': []}})
    assert ids(table.fetch_sync(params, session)) == [1, 2, 3, 4, 5]


#: What each database does with accents, for the claims of SPEC-F9.6, SPEC-F9.7, SPEC-F10.1 and SPEC-F10.2. MySQL's and
#: SQL Server's column is declared with the collation the statement names; SQLite and PostgreSQL keep their default.
ACCENT_COLLATION = {
    'mysql': 'utf8mb4_0900_ai_ci',
    'mariadb': 'utf8mb4_uca1400_ai_ci',
    'mssql': 'Latin1_General_CI_AS',
}


@pytest.fixture
def accented(session: Session) -> SQLTable:
    """Row 1 holds `Apple`, row 4 `Äpfel`."""
    collation = ACCENT_COLLATION.get(session.get_bind().dialect.name)
    fruits = Table(
        'dtb_test_tags',
        MetaData(),
        SQLColumn('id', Integer, primary_key=True, autoincrement=False),
        SQLColumn('name', String(20, collation=collation)),
    )
    fruits.create(session.connection())
    session.execute(fruits.insert(), [{'id': 1, 'name': 'Apple'}, {'id': 4, 'name': 'Äpfel'}])
    return SQLTable(fruits, [Column('id'), Column('name')])


def folds_letters_beyond_ascii(session: Session) -> bool:
    """Whether the database folds `Ä` to `ä` on its own: SQLite folds ASCII only, PostgreSQL does in a UTF-8 locale
    (the official image's `en_US.utf8`) and not in the `C` locale, MySQL's, MariaDB's and SQL Server's collations do."""
    dialect = session.get_bind().dialect.name
    if dialect == 'sqlite':
        return False
    if dialect == 'postgresql':
        collate = session.execute(text('SELECT datcollate FROM pg_database WHERE datname = current_database()'))
        return collate.scalar_one() not in ('C', 'POSIX')
    return True


def test_the_global_search_finds_a_capital_beyond_ascii_by_its_own_case_and_a_small_one_where_the_database_folds(
    session: Session, accented: SQLTable
):
    """SPEC-F9.6, SPEC-F9.7: `ÄPFEL` is the stored `Äpfel` in another case that no database misses; `äpfel` needs the
    database to fold `Ä` to `ä`, which SQLite does not (it folds ASCII only) and the others do, PostgreSQL in a UTF-8
    locale (SPEC-F9.7 reads its setting too). SQL Server's and PostgreSQL's cases are proved by CI."""
    capital = accented.fetch_sync(request('id', 'name', search={'value': 'ÄPFEL'}), session)
    small = accented.fetch_sync(request('id', 'name', search={'value': 'äpfel'}), session)
    assert (ids(capital), ids(small)) == ([4], [4] if folds_letters_beyond_ascii(session) else [])


def test_the_library_folds_no_accent_the_collation_does_not(session: Session, accented: SQLTable):
    """SPEC-F10.1, SPEC-F10.2: `apfel` finds `Äpfel` only where the collation ignores accents: MySQL's
    `utf8mb4_0900_ai_ci` and MariaDB's default `utf8mb4_uca1400_ai_ci` (SQL Server's `Latin1_General_CI_AS` does not,
    SQLite and PostgreSQL's default do not)."""
    found = [4] if session.get_bind().dialect.name in ('mysql', 'mariadb') else []
    assert ids(accented.fetch_sync(request('id', 'name', search={'value': 'apfel'}), session)) == found


def test_an_empty_entry_is_not_given_to_the_search_value_hook(session: Session):
    """SPEC-D16.1: "empty" is about what is stored, not text to transform. The hook sees the other entry only."""
    seen: list[str] = []

    def stored(text: str) -> str:
        seen.append(text)
        return text

    hooked = SQLTable(people, [Column('id'), Column('email', field='email_address', search_value=stored)])
    params = request('id', 'email', column_search={'email': {'multi_values': ['', 'ann@example.org']}})
    assert ids(hooked.fetch_sync(params, session)) == [1, 2, 3]
    assert set(seen) == {'ann@example.org'}  # however often the query is built


@pytest.mark.parametrize(
    ('body', 'expected'),
    [
        ({'column_search': {'active': {'value': SPACES}}}, [1, 2, 3, 4, 5]),  # also no value a boolean must read
        ({'search': {'value': SPACES}}, [1, 2, 3, 4, 5]),
        # With `regex: true` the spaces are a categorical value, which no row holds
        ({'column_search': {'team': {'value': SPACES, 'regex': True}}}, []),
        ({'column_search': {'age': {'value': SPACES, 'regex': True}}}, [3]),  # SPEC-F6.4: on a number, empty
    ],
)
def test_search_text_of_spaces_only_adds_nothing(session: Session, body: dict[str, Any], expected: list[int]):
    """SPEC-F6.3, for every character that counts as a space (PRD-F7): a user who types a space gets every row."""
    assert ids(every_type.fetch_sync(request(*EVERY_TYPE, **body), session)) == expected


def test_a_reflected_boolean_column_is_typed_by_what_the_database_reports(session: Session):
    """SPEC-D8.1 and SPEC-D8.2 on the table as the database describes it, `active` declared without a type.

    SQLite, PostgreSQL and SQL Server (whose `BIT` is a SQLAlchemy `Boolean`) run the first branch: the column is
    boolean. MySQL runs the second: its `TINYINT(1)` is a number until it is declared boolean, and both halves are
    asserted there. Any other dialect fails."""
    reflected = Table(people.name, MetaData(), autoload_with=session.connection())
    undeclared = SQLTable(reflected, [Column('id'), Column('active')])
    dialect = session.get_bind().dialect.name
    if dialect in ('sqlite', 'postgresql', 'mssql'):
        assert ids(undeclared.fetch_sync(request('id', 'active', **criterion('active', '=', 'true')), session)) == [
            1,
            4,
        ]
    elif dialect in ('mysql', 'mariadb'):
        assert ids(undeclared.fetch_sync(request('id', 'active', **criterion('active', '<', '1')), session)) == [2, 5]
        declared = SQLTable(reflected, [Column('id'), Column('active', type='boolean')])
        assert ids(declared.fetch_sync(request('id', 'active', **criterion('active', '=', 'true')), session)) == [1, 4]
    else:
        pytest.fail(f'no expectation for the dialect {dialect}')


def test_an_integer_column_declared_boolean_is_compared_with_one_and_zero(session: Session):
    """A column that stores 1 and 0 as integers, declared boolean (SPEC-D8.2's case on every database): the value
    reaches the database as a number. PostgreSQL has no `integer = boolean`, so a `true` sent as a boolean fails this
    test there; the other databases read one as the other, and so cannot tell."""
    source = select(people.c.id, cast(people.c.active, Integer).label('flag')).subquery()
    declared = SQLTable(source, [Column('id'), Column('flag', type='boolean')])
    assert ids(declared.fetch_sync(request('id', 'flag', **criterion('flag', '=', 'true')), session)) == [1, 4]
    assert ids(declared.fetch_sync(request('id', 'flag', **criterion('flag', '!=', 'true')), session)) == [2, 3, 5]
    params = request('id', 'flag')
    params.columns[1].search.multi_values = ['1']
    assert ids(declared.fetch_sync(params, session)) == [1, 4]


@pytest.mark.parametrize(('direction', 'expected'), [('asc', [2, 5, 1, 4]), ('desc', [1, 4, 2, 5])])
def test_false_sorts_before_true(session: Session, direction: str, expected: list[int]):
    """The boolean part of SPEC-O2.3, SPEC-O8.1 and SPEC-O8.2, measured on each database: `active`, then `id`
    ascending, over the rows that have a value. Where NULL goes and which way the key runs are PRD-O2's and PRD-O3's."""
    params = request(*FLAGGED, order=[{'column': 2, 'dir': direction}, {'column': 0, 'dir': 'asc'}])
    assert [row for row in ids(flagged.fetch_sync(params, session)) if row != 3] == expected


@pytest.mark.parametrize(
    ('declared', 'kind'),
    [
        (Column('age', search_value=str.upper), 'number'),
        (Column('born', search_value=str.upper), 'date'),
        (Column('seen', search_value=str.upper), 'date'),
        (Column('active', search_value=str.upper), 'boolean'),
        (Column('team', type='number', search_value=str.upper), 'number'),  # the declared type decides
    ],
)
def test_a_search_value_hook_on_a_column_that_is_no_string_is_a_schema_error(declared: Column, kind: str):
    """SPEC-D14.1: the hook transforms text the user types. The message names the declared column and the fix."""
    with pytest.raises(SchemaError) as raised:
        SQLTable(people, [Column('id'), declared])
    assert str(raised.value) == (
        f'The column "{declared.name}" has a search_value hook but is a {kind} column. Only a string column takes '
        'one: remove the hook.'
    )


def test_two_columns_with_one_name_are_a_schema_error():
    """SPEC-D1.2: the name keys the request and each row, so the second column would silently replace the first."""
    with pytest.raises(SchemaError) as raised:
        SQLTable(people, [Column('id'), Column('team'), Column('team', field='name')])
    assert str(raised.value) == (
        'Two columns are declared with the name "team". Give each its own name: Column(name, field=...) reads one '
        'field under another name.'
    )


def test_a_table_without_columns_is_a_schema_error():
    """SPEC-D1.2: a table that has none answers every request with empty rows."""
    with pytest.raises(SchemaError) as raised:
        SQLTable(people, [])
    assert str(raised.value) == 'No column is declared. Declare at least one Column.'


def test_an_index_column_on_a_sql_table_is_a_schema_error():
    """An IndexColumn declares an OpenSearch field; a SQL table would ignore what it adds and look as if it used it."""
    with pytest.raises(SchemaError) as raised:
        SQLTable(people, [Column('id'), IndexColumn('team', text='text')])
    assert str(raised.value) == (
        'The column "team" is an IndexColumn, which declares an OpenSearch field, and a SQL table ignores what it '
        'adds. Declare it as Column("team", ...) instead.'
    )


def test_a_type_outside_the_four_is_a_schema_error_on_a_sql_table_too():
    """SPEC-D23.10: the check is the core's, so both backends refuse the misspelled type when the table is built, where
    it used to build and fail on every SearchBuilder condition with a `KeyError`."""
    misspelled: Any = 'numbr'
    with pytest.raises(SchemaError, match=r'"id".*\'numbr\'.*boolean'):
        SQLTable(people, [Column('id', type=misspelled)])


def test_a_search_value_that_cannot_be_called_is_a_schema_error_on_a_sql_table_too():
    """SPEC-D23.14: the check is the core's, shared with OpenSearch."""
    hook: Any = 'x'
    with pytest.raises(SchemaError, match=r'"secret".*search_value.*callable'):
        SQLTable(people, [Column('id'), Column('secret', search_value=hook)])


@pytest.mark.parametrize(('name', 'written'), [('', "name=''"), (None, 'name=None')])
def test_an_empty_name_is_a_schema_error_that_says_what_to_give_on_a_sql_table_too(name: Any, written: str):
    """SPEC-D23.15: checked before the missing-column check, which says only that the table has no column of that
    name, and before SQLAlchemy's `ArgumentError` for a name of `None`."""
    with pytest.raises(SchemaError) as raised:
        SQLTable(people, [Column('id'), Column(name)])
    assert written in str(raised.value)
    assert 'Give it a non-empty name' in str(raised.value)


def test_a_search_value_on_an_unsearchable_column_is_a_schema_error_on_a_sql_table_too():
    """SPEC-D23.16: the check is the core's, shared with OpenSearch: no search reaches the column, so the hook never
    runs."""
    with pytest.raises(SchemaError, match=r'"secret".*search_value.*searchable=False.*never runs') as raised:
        SQLTable(people, [Column('id'), Column('secret', searchable=False, search_value=str.upper)])
    assert 'Remove the hook, or declare the column searchable' in str(raised.value)


HOOKED = SQLTable(people, [Column('id'), Column('secret', search_value=lambda text: f's{text}')])


@pytest.mark.parametrize(
    ('condition', 'values', 'expected'),
    [
        ('=', ['4'], [4]),  # the hook turns `4` into the stored `s4`
        ('!=', ['4'], [1, 2, 3, 5, 6]),  # PRD-F37: row 6, with no value, is not equal to `s4`
        ('null', [], [6]),  # row 6 holds no secret; the rows that hold one are not empty
        ('!null', [], [1, 2, 3, 4, 5]),
    ],
)
def test_searchbuilder_on_a_hooked_column_answers_equality_and_empty(
    session: Session, condition: str, values: list[str], expected: list[int]
):
    """SPEC-D18.1 (PRD-D18): `=` and `!=` give the value to the hook; `null` and `!null` take none."""
    session.execute(people.insert(), [{'id': 6, 'secret': None}])
    assert (
        ids(HOOKED.fetch_sync(request('id', 'secret', **criterion('secret', condition, *values)), session)) == expected
    )


@pytest.mark.parametrize('condition', ['contains', '!contains', 'starts', '!starts', 'ends', '!ends'])
def test_searchbuilder_on_a_hooked_column_refuses_a_part_of_the_value(session: Session, condition: str):
    """SPEC-D18.2: a part of a transformed value means nothing. The message names the condition (a protocol literal)
    and the declared column, never the value."""
    response = HOOKED.fetch_sync(request('id', 'secret', **criterion('secret', condition, 'zzz')), session)
    assert refused(response) == (
        f'The condition "{condition}" cannot be used on "secret", whose search value is transformed before it is '
        'compared. Choose =, !=, null or !null.'
    )


@pytest.mark.parametrize(
    ('column', 'value', 'code'),
    [('age', 'abc', 'invalid_number'), ('born', '9999-12-31', 'invalid_date')],
)
def test_a_number_or_date_mistake_is_reported_before_a_boolean_one(
    session: Session, column: str, value: str, code: str
):
    """SPEC-E3.3 (a format, step 2) and SPEC-E3.7 (a range, step 3) come before a boolean value outside the set
    (step 4), whichever the request names first."""
    typed = SQLTable(people, [Column('id'), Column('age'), Column('born'), Column('active')])
    params = request('id', 'age', 'born', 'active', **criterion(column, '=', value))
    params.columns[3].search.value = 'yes'
    assert typed.fetch_sync(params, session).to_dict()['error_code'] == code


@pytest.mark.parametrize(
    ('condition', 'values', 'expected'),
    [
        ('!=', ['1980-07-15'], [1, 3, 4, 5]),
        ('!between', ['1980-01-01', '1994-03-01'], [3, 4, 5]),
        ('!contains', ['1980'], [1, 3, 4, 5]),
    ],
)
def test_negated_date_conditions(session: Session, condition: str, values: list[str], expected: list[int]):
    """SPEC-F37.3: each is the days outside a span. Row 3 has no date, and passes them all (PRD-F37)."""
    assert ids(table.fetch_sync(request(*ALL, **criterion('born', condition, *values)), session)) == expected


def test_searchbuilder_groups_nest_and_empty_groups_add_nothing(session: Session):
    tree = {
        'logic': 'OR',
        'criteria': [
            {'logic': 'AND', 'criteria': [
                {'condition': '=', 'origData': 'team', 'value': ['red']},
                {'condition': '>', 'origData': 'age', 'value': ['30']},
            ]},
            {'condition': '=', 'origData': 'name', 'value': ['Dee']},
            {'logic': 'AND', 'criteria': []},
        ],
    }  # fmt: skip
    assert ids(table.fetch_sync(request(*ALL, searchBuilder=tree), session)) == [1, 5]


BLUE = {'condition': '=', 'origData': 'team', 'data': 'team', 'value': ['blue']}  # rows 2 and 5


@pytest.mark.parametrize(
    'unfinished',
    [
        pytest.param({'origData': 'name', 'value': ['Ann']}, id='no condition'),
        pytest.param({'condition': '', 'origData': 'name', 'value': ['Ann']}, id='empty condition'),
        pytest.param({'condition': '=', 'value': ['Ann']}, id='no column'),
        pytest.param({'condition': '=', 'origData': '', 'data': '', 'value': ['Ann']}, id='empty column'),
        pytest.param({'condition': '=', 'origData': 'name'}, id='equals without a value key'),
        pytest.param({'condition': '=', 'origData': 'name', 'value': []}, id='equals with no value'),
        pytest.param({'condition': 'between', 'origData': 'age', 'value': ['5']}, id='between with one value'),
        pytest.param({'condition': 'between', 'origData': 'age'}, id='between without a value key'),
        # SearchBuilder's client does not apply a criterion with an empty input (SPEC-F30.5)
        pytest.param({'condition': '=', 'origData': 'name', 'value': ['']}, id='equals with an empty value'),
        pytest.param({'condition': '=', 'origData': 'age', 'value': ['']}, id='number equals with an empty value'),
        pytest.param({'condition': '=', 'origData': 'born', 'value': ['']}, id='date equals with an empty value'),
        pytest.param({'condition': 'between', 'origData': 'age', 'value': ['', '5']}, id='between with an empty first'),
        pytest.param({'condition': 'between', 'origData': 'age', 'value': ['5', '']}, id='between with an empty last'),
    ],
)
def test_an_unfinished_criterion_adds_nothing(session: Session, unfinished: dict[str, Any]):
    """SPEC-F30.1, SPEC-F30.2 (`no column` and `empty column`), SPEC-F30.3 and SPEC-F30.5 (PRD-F30), as an empty group
    does (SPEC-F28.3). Alone it leaves every
    row; beside `team = blue` it leaves that criterion's rows in an AND group and in an OR group: read as true it would
    give every row in the OR group, and read as false none in the AND group."""
    alone = {'logic': 'AND', 'criteria': [unfinished]}
    assert ids(table.fetch_sync(request(*ALL, searchBuilder=alone), session)) == [1, 2, 3, 4, 5]
    for logic in ('AND', 'OR'):
        tree = {'logic': logic, 'criteria': [BLUE, unfinished]}
        assert ids(table.fetch_sync(request(*ALL, searchBuilder=tree), session)) == [2, 5], logic


RANGES = ('<', '<=', '>', '>=')


@pytest.mark.parametrize(
    ('column', 'conditions', 'values'),
    [
        ('name', (*RANGES, 'between', '!between'), ['b', 'm']),  # SPEC-F27.1: no order on text
        ('age', ('contains', '!contains', 'starts', '!starts', 'ends', '!ends'), ['3']),  # SPEC-F27.2
        ('born', ('starts', 'ends', '!starts', '!ends'), ['19']),  # SPEC-F27.3, SPEC-F27.8
    ],
)
def test_a_condition_the_type_does_not_take_is_refused(
    session: Session, column: str, conditions: tuple[str, ...], values: list[str]
):
    """SPEC-F27.1, SPEC-F27.2, SPEC-F27.3, SPEC-F27.8: every condition outside the type's set is `invalid_filter`, and
    the text is asserted once, in test_a_user_error_says_what_is_wrong_and_what_to_do. `between` reads two values."""
    for condition in conditions:
        body = criterion(column, condition, *(values if 'between' in condition else values[:1]))
        refused(table.fetch_sync(request(*ALL, **body), session))
    assert ids(table.fetch_sync(request(*ALL, **criterion('born', 'contains', '1980')), session)) == [2]  # SPEC-F27.3


@pytest.mark.parametrize(
    ('tree', 'expected'),
    [
        (  # SPEC-F28.1: an OR group is a union
            {
                'logic': 'OR',
                'criteria': [
                    {'condition': '=', 'origData': 'team', 'value': ['blue']},
                    {'condition': '=', 'origData': 'age', 'value': ['31']},
                ],
            },
            [1, 2, 4, 5],
        ),
        (  # SPEC-F28.2: blue is rows 2 and 5; the nested group keeps 45 (row 2) and no age (row 3), so only row 2
            {
                'logic': 'AND',
                'criteria': [
                    {'condition': '=', 'origData': 'team', 'value': ['blue']},
                    {
                        'logic': 'OR',
                        'criteria': [
                            {'condition': '=', 'origData': 'age', 'value': ['45']},
                            {'condition': 'null', 'origData': 'age', 'value': []},
                        ],
                    },
                ],
            },
            [2],  # dropping the nested group gives 2 and 5; OR throughout gives 2, 3 and 5
        ),
    ],
)
def test_searchbuilder_groups_join_their_criteria_by_their_logic(
    session: Session, tree: dict[str, Any], expected: list[int]
):
    """SPEC-F28.1, SPEC-F28.2."""
    assert ids(table.fetch_sync(request(*ALL, searchBuilder=tree), session)) == expected


@pytest.mark.parametrize(
    ('condition', 'values', 'expected'),
    [
        ('>', ['30.5'], [1, 2, 4, 5]),  # the two 31s are above 30.5, as is everyone older
        ('<', ['30.5'], []),
        ('>', ['31.5'], [2, 5]),
        ('<', ['31.5'], [1, 4]),
        ('=', ['31.0'], [1, 4]),
        ('=', ['31.5'], []),
        ('between', ['30.5', '31.5'], [1, 4]),
    ],
)
def test_a_number_with_a_fraction_is_compared_as_that_number_on_an_integer_column(
    session: Session, condition: str, values: list[str], expected: list[int]
):
    """SPEC-F25.10: `age` is a `SmallInteger`; a fraction is no whole number, so `=` finds nothing and a range sits
    between two whole numbers. Row 3 has no age."""
    assert ids(table.fetch_sync(request(*ALL, **criterion('age', condition, *values)), session)) == expected


@pytest.mark.parametrize(
    'search',
    [{'value': '^(31|60)$', 'regex': True}, {'multi_values': ['31', '60']}],
    ids=['categorical', 'multi_values'],
)
def test_a_categorical_filter_on_a_number_column_matches_its_numbers(session: Session, search: dict[str, Any]):
    """SPEC-F33.3: the number column's values equal the numbers listed, as `=` does, not their text."""
    assert ids(table.fetch_sync(request(*ALL, column_search={'age': search}), session)) == [1, 4, 5]


def test_a_list_leaves_out_a_number_with_a_fraction_on_an_integer_column(session: Session):
    """SPEC-F25.10: 31.4 is no whole number, and PostgreSQL's cast to an integer would round it to 31, so it is left
    out of the list as `=` leaves it to match nothing, and only 60 is found. It catches a fraction that reaches the
    list; SQLite compares it as it is, so only the servers can fail it."""
    search = {'multi_values': ['31.4', '60.0']}
    assert ids(table.fetch_sync(request(*ALL, column_search={'age': search}), session)) == [5]


def test_a_value_that_is_there_but_unreadable_is_refused_not_left_out(session: Session):
    """SPEC-F30.4: only a missing value is unfinished; an unreadable one is the user's mistake."""
    refused(table.fetch_sync(request(*ALL, **criterion('age', 'between', 'abc')), session), 'invalid_number')


def test_white_space_is_a_value_not_a_blank_one(session: Session):
    """SPEC-F30.5: only the empty text is blank, as in SearchBuilder's client; `=` with one space still asks for the
    rows whose name is one space, of which there are none."""
    assert ids(table.fetch_sync(request(*ALL, **criterion('name', '=', ' ')), session)) == []


def test_a_surplus_value_is_refused_also_when_it_is_blank(session: Session):
    """SPEC-F30.5, SPEC-F36.1: the surplus is checked before a blank value counts as missing."""
    refused(table.fetch_sync(request(*ALL, **criterion('name', '=', '', '')), session))
    refused(table.fetch_sync(request(*ALL, **criterion('age', 'between', '', '5', '6')), session))


def flat_request(criteria: list[dict[str, str]], logic: str) -> DTParams:
    """The request DataTables 3 sends as a form: `undefined` is written as that word, an empty `value` list as no key
    at all, and value1 and value2 beside it (SPEC-F30.6)."""
    pairs: list[tuple[str, str]] = [('draw', '1'), ('start', '0'), ('length', '10')]
    for index, name in enumerate(ALL):
        pairs += [
            (f'columns[{index}][data]', name),
            (f'columns[{index}][name]', ''),
            (f'columns[{index}][searchable]', 'true'),
            (f'columns[{index}][orderable]', 'true'),
            (f'columns[{index}][search][value]', ''),
            (f'columns[{index}][search][regex]', 'false'),
        ]
    pairs.append(('searchBuilder[logic]', logic))
    for index, criterion_ in enumerate(criteria):
        for key, value in criterion_.items():
            suffix = '[value][]' if key == 'value[]' else f'[{key}]'  # a list sends one key per entry
            pairs.append((f'searchBuilder[criteria][{index}]{suffix}', value))
    return parse_request(pairs)


FLAT_BLUE = {
    'condition': '=',
    'data': 'team',
    'origData': 'team',
    'type': 'string',
    'value[]': 'blue',
    'value1': 'blue',
    'value2': 'undefined',
}


@pytest.mark.parametrize(
    'fresh',
    [
        pytest.param(  # "Add condition" was clicked: nothing is chosen yet
            {
                'condition': 'undefined',
                'data': 'undefined',
                'origData': 'undefined',
                'type': '',
                'value1': 'undefined',
                'value2': 'undefined',
            },
            id='a new criterion',
        ),
        pytest.param(  # a column is chosen, no condition yet
            {
                'condition': 'undefined',
                'data': 'name',
                'origData': 'name',
                'type': 'string',
                'value1': 'undefined',
                'value2': 'undefined',
            },
            id='a column chosen',
        ),
        pytest.param(  # a condition is chosen, no value typed: the empty `value` list sends no key
            {
                'condition': '=',
                'data': 'name',
                'origData': 'name',
                'type': 'string',
                'value1': 'undefined',
                'value2': 'undefined',
            },
            id='a condition chosen',
        ),
    ],
)
def test_a_criterion_a_form_sends_before_it_is_finished_adds_nothing(session: Session, fresh: dict[str, str]):
    """SPEC-F30.6: every draw after the user clicks "Add condition" carries such a criterion; it must neither be
    malformed nor change the rows, alone or beside another criterion in an AND or an OR group."""
    assert ids(table.fetch_sync(flat_request([fresh], 'AND'), session)) == [1, 2, 3, 4, 5]
    for logic in ('AND', 'OR'):
        assert ids(table.fetch_sync(flat_request([FLAT_BLUE, fresh], logic), session)) == [2, 5], logic


def test_the_extra_values_a_client_sends_beside_value_are_ignored(session: Session):
    """SPEC-F27.7: SearchBuilder sends value1 and value2 beside value; a criterion that carries them is still answered."""
    tree = {'criteria': [{'condition': '=', 'origData': 'name', 'value': ['Bob'], 'value1': 'x', 'value2': 'y'}]}
    assert ids(table.fetch_sync(request(*ALL, searchBuilder=tree), session)) == [2]


def test_the_declared_type_decides_not_the_clients(session: Session):
    """SPEC-D7.1, SPEC-F27.4: a client that calls a number column a string still gets a numeric comparison, and cannot
    run 'contains'."""
    tree = {'criteria': [{'condition': '>', 'origData': 'age', 'type': 'string', 'value': ['9']}]}
    assert ids(table.fetch_sync(request(*ALL, searchBuilder=tree), session)) == [1, 2, 4, 5]
    tree = {'criteria': [{'condition': 'contains', 'origData': 'age', 'type': 'string', 'value': ['3']}]}
    refused(table.fetch_sync(request(*ALL, searchBuilder=tree), session))


FULL_WIDTH = {ord('0') + digit: 0xFF10 + digit for digit in range(10)}  # the digits U+FF10 to U+FF19


@pytest.mark.parametrize(
    ('column', 'condition', 'values', 'code'),
    [
        ('age', '>', ['1e999'], 'invalid_number'),  # SPEC-F25.1: written as a number, and no column holds infinity
        ('age', 'between', ['old'], 'invalid_number'),  # the value there is, not the one missing
        # SPEC-F25.1: an exponent no decimal holds, which a float read as infinity or as zero
        ('age', '>', ['1e9999999999999999999999'], 'invalid_number'),
        ('age', '<', ['1e-9999999999999999999999'], 'invalid_number'),
        # SPEC-F25.3: spellings Python's own parsers read as 31, and PRD-F25 does not
        ('age', '=', ['3_1'], 'invalid_number'),
        ('age', '=', ['--3.5'], 'invalid_number'),  # a second sign, which `float` refuses with an exception
        ('age', '=', ['31'.translate(FULL_WIDTH)], 'invalid_number'),
        # SPEC-F21.10: text after the day, another ISO form, an offset (its instant may be another UTC day), and a
        # time of day that does not exist
        ('born', '=', ['1980-07-15xyz'], 'invalid_date'),
        ('born', '=', ['1980-07-15T10:00Z'], 'invalid_date'),
        ('born', '=', ['  '], 'invalid_date'),  # only in a list is a blank value an empty entry
        ('born', '=', ['19800715'], 'invalid_date'),
        ('born', '=', ['1980-07-15T10:00+02:00'], 'invalid_date'),
        ('born', '=', ['1980-07-15 24:00'], 'invalid_date'),
        ('born', '=', ['1980-07-15T10'], 'invalid_date'),  # an hour is no time of day
        ('born', '=', ['1980-07-15T' + '10:00'.translate(FULL_WIDTH)], 'invalid_date'),  # the time in other digits
        ('born', 'contains', ['1980'.translate(FULL_WIDTH)], 'invalid_date'),  # SPEC-F22.5: a year in other digits
        ('born', 'contains', ['1980-07-15xyz'], 'invalid_date'),  # and a day as `=` reads it, to its end
        ('born', 'contains', ['9999-12'], 'invalid_date'),  # SPEC-F24.1: a month that holds the last day
        ('born', 'contains', ['9999-12-31'], 'invalid_date'),  # and the day, whose next day would overflow
        ('born', 'contains', ['2024-00'], 'invalid_date'),  # no month 00: it is not read as January
        ('born', 'contains', ['2024-03-00'], 'invalid_date'),  # nor a day 00 as the first
        ('born', 'contains', ['0000'], 'invalid_date'),  # nor the year 0000 as the year 1
        # SPEC-F22.2: a month, a year and a day that are not written in full
        ('born', 'contains', ['1980-7'], 'invalid_date'),
        ('born', 'contains', ['80'], 'invalid_date'),
        ('born', 'contains', ['1980-07-1'], 'invalid_date'),
        # SPEC-F21.10: a full day in full-width digits: `contains` reads a day as `=` does, in ASCII digits only
        ('born', 'contains', ['2024-03-05'.translate(FULL_WIDTH)], 'invalid_date'),
        ('age', 'contains', ['abc'], 'invalid_filter'),  # SPEC-E3.8: the value is never read
    ],
)
def test_a_value_the_column_cannot_take_is_refused_with_its_code_without_echoing_it(
    session: Session, column: str, condition: str, values: list[str], *, code: str
):
    """SPEC-E4.1 and the cases the test of the texts below does not hold: each has its code (PRD-E3, steps 2 to 4),
    and the text never holds the value."""
    message = refused(table.fetch_sync(request(*ALL, **criterion(column, condition, *values)), session), code)
    assert all(value not in message for value in values)


def test_a_whole_number_is_compared_as_the_integer_it_writes(session: Session):
    """SPEC-F25.2: a whole number written without a point, signed or not, reaches the query as an integer. Read as
    a float, which holds 53 bits, -9223372036854775807 is -9223372036854775808: in the column's range, and no row."""
    source = select(people.c.id, (cast(people.c.id, BigInteger) - 9223372036854775807 - 1).label('big')).subquery()
    wide = SQLTable(source, [Column('id'), Column('big')])
    params = request('id', 'big', **criterion('big', '=', '-9223372036854775807'))
    assert ids(wide.fetch_sync(params, session)) == [1]


def test_the_last_month_before_the_last_day_is_answered(session: Session):
    """The other side of SPEC-F24.1's spans: only one that holds 9999-12-31 is refused."""
    assert ids(table.fetch_sync(request(*ALL, **criterion('born', 'contains', '9999-11')), session)) == []


def test_the_user_error_codes_are_the_eight_of_the_prd_and_no_other():
    """SPEC-E2.2: the codes a frontend maps are stable; a ninth, or one renamed or dropped, is a change that is made
    on purpose, together with the PRD and its documentation."""
    assert set(get_args(ErrorCode.__value__)) == ERROR_CODES


def test_a_user_error_is_an_answer_with_the_draw_and_nothing_else(session: Session):
    """SPEC-E1.1, SPEC-R5.2, SPEC-R6.1: what a caller sends on for a mistake the user can fix."""
    response = table.fetch_sync(request(*ALL, draw=7, **criterion('age', '=', 'abc')), session)
    assert response.error  # its wording is asserted once, in the test of the texts below
    assert response.to_dict() == {
        'draw': 7,
        'recordsTotal': 0,
        'recordsFiltered': 0,
        'data': [],
        'error': response.error,
        'error_code': 'invalid_number',
    }


WORDED = ('id', 'name', 'age', 'born', 'active', 'secret', 'big', 'team')
worded = SQLTable(
    select(
        people.c.id,  # an INTEGER
        people.c.name,
        people.c.age,  # a SMALLINT
        people.c.born,
        people.c.active,
        people.c.secret,
        cast(people.c.id, BigInteger).label('big'),
        people.c.team,
    ).subquery(),
    [
        Column('id'),
        Column('name'),
        Column('age'),
        Column('born'),
        Column('active'),
        Column('secret', searchable=False),
        Column('big'),
        Column('team', orderable=False),
    ],
)
UNDECLARED = (*WORDED, 'extra')
CONTROL = 'holds an invisible control character, such as a tab. Delete its'
CONDITION = 'Choose another condition.'
SPAN = (
    'The filter on "born" needs a year, a month or a day that exists, written as YYYY, YYYY-MM or YYYY-MM-DD, like '
    '2024-03.'
)
LAST_DAY = 'The filter on "born" reaches the last day of the year 9999, which cannot be searched. Use an earlier date.'


@pytest.mark.parametrize(
    ('names', 'body', 'code', 'text'),
    [
        # SPEC-E4.3, SPEC-E4.4, SPEC-E4.5: the owner's case, a tab nobody sees, and where it is
        (
            WORDED,
            {'search': {'value': 'a\tb'}},
            'invalid_character',
            f'The search box {CONTROL} text and type it again.',
        ),
        (
            WORDED,
            {'column_search': {'name': {'value': 'a\tb'}}},
            'invalid_character',
            f'The filter on "name" {CONTROL} value and type it again.',
        ),
        (
            WORDED,
            criterion('name', '=', 'a\tb'),  # the criterion finds its column by another route than a column search
            'invalid_character',
            f'The filter on "name" {CONTROL} value and type it again.',
        ),
        (
            UNDECLARED,
            {'column_search': {'extra': {'value': 'a\tb'}}},
            'invalid_character',
            f'A column filter {CONTROL} value and type it again.',
        ),
        (
            UNDECLARED,
            criterion('extra', '=', 'a\tb'),  # the control character is answered before the name that is not declared
            'invalid_character',
            f'A column filter {CONTROL} value and type it again.',
        ),
        # SPEC-E4.6, SPEC-E4.7, SPEC-E4.8, and SPEC-E4.25 for a list of values, which must not be told to hold one number
        (
            WORDED,
            criterion('age', '=', 'abc'),
            'invalid_number',
            'The filter on "age" needs one number, written like 42 or -3.5.',
        ),
        (
            WORDED,
            {'column_search': {'age': {'multi_values': ['31', 'abc']}}},
            'invalid_number',
            'Each value in the filter on "age" must be a number, written like 42 or -3.5.',
        ),
        (
            WORDED,
            criterion('age', 'between', '1', 'abc'),  # a `between` reads two values, so it is not told to hold one
            'invalid_number',
            'Each value in the filter on "age" must be a number, written like 42 or -3.5.',
        ),
        (
            WORDED,
            criterion('born', '=', '15/07/1980'),
            'invalid_date',
            'The filter on "born" needs a date that exists, written as YYYY-MM-DD, like 2024-03-05.',
        ),
        # SPEC-E4.27, and SPEC-F21.6's code: a list of days, and the two of a `between`, are not told to be one date
        (
            WORDED,
            {'column_search': {'born': {'multi_values': ['1980-07-15', '2024-13-01']}}},  # a day that does not exist
            'invalid_date',
            'Each value in the filter on "born" must be a date that exists, written as YYYY-MM-DD, like 2024-03-05.',
        ),
        (
            WORDED,
            # PRD-E3 within one filter: the second value's format is answered, not the first value's range
            criterion('born', 'between', '9999-12-31', 'abc'),
            'invalid_date',
            'Each value in the filter on "born" must be a date that exists, written as YYYY-MM-DD, like 2024-03-05.',
        ),
        (WORDED, criterion('born', 'contains', '1980-13'), 'invalid_date', SPAN),
        # A day that does not exist: `contains` also takes a year or a month, so its text must say so
        (WORDED, criterion('born', 'contains', '1980-02-30'), 'invalid_date', SPAN),
        # SPEC-E4.9: the bounds are those of the column's own width
        (
            WORDED,
            criterion('age', '=', '40000'),
            'invalid_number',
            'The filter on "age" needs a number between -32768 and 32767.',
        ),
        (
            WORDED,
            criterion('id', '=', '2147483648'),
            'invalid_number',
            'The filter on "id" needs a number between -2147483648 and 2147483647.',
        ),
        (
            WORDED,
            criterion('big', '=', '9223372036854775808'),
            'invalid_number',
            'The filter on "big" needs a number between -9223372036854775808 and 9223372036854775807.',
        ),
        # SPEC-E4.26: the list form of the range text, for a list of values and for the two of a `between`
        (
            WORDED,
            {'column_search': {'age': {'multi_values': ['7', '70000']}}},
            'invalid_number',
            'Each value in the filter on "age" must be a number between -32768 and 32767.',
        ),
        (
            WORDED,
            criterion('age', 'between', '1', '70000'),
            'invalid_number',
            'Each value in the filter on "age" must be a number between -32768 and 32767.',
        ),
        # SPEC-E4.10, SPEC-F24.1: the last day itself, and a year that holds it
        (WORDED, criterion('born', '<', '9999-12-31'), 'invalid_date', LAST_DAY),
        (WORDED, criterion('born', 'contains', '9999'), 'invalid_date', LAST_DAY),
        # SPEC-E4.11, SPEC-E4.12, SPEC-E4.2: a name the server did not declare is never given
        (
            UNDECLARED,
            {'order': [{'column': 8, 'dir': 'asc'}]},
            'invalid_filter',
            'The table was asked to sort by a column the server does not offer. Sort by another column.',
        ),
        (
            UNDECLARED,
            {'column_search': {'extra': {'value': 'x'}}},
            'invalid_filter',
            'A filter is on a column the server does not offer. Remove that filter.',
        ),
        # SPEC-E4.13, SPEC-E4.14, SPEC-E4.15
        (
            WORDED,
            {'order': [{'column': 7, 'dir': 'asc'}]},
            'invalid_filter',
            'The table cannot be sorted by "team". Sort by another column.',
        ),
        (
            WORDED,
            {'column_search': {'secret': {'value': 'x'}}},
            'invalid_filter',
            'The column "secret" cannot be filtered. Remove the filter on it.',
        ),
        (
            WORDED,
            criterion('secret', '=', 'x'),  # SPEC-F41.2: a criterion finds its column by another route
            'invalid_filter',
            'The column "secret" cannot be filtered. Remove the filter on it.',
        ),
        (
            ('active',),  # the only column asked for is a boolean, which the search box skips
            {'search': {'value': 'x'}},
            'invalid_filter',
            'No column of this table can be searched from the search box. Clear it and use a column filter instead.',
        ),
        # SPEC-E4.16, SPEC-E4.17
        (
            WORDED,
            {'search': {'value': 'a.*', 'regex': True}},
            'invalid_filter',
            'The search box does not take regular expressions. Search for plain text.',
        ),
        (
            WORDED,
            {'column_search': {'name': {'value': 'A.+', 'regex': True}}},
            'invalid_filter',
            'The filter on "name" does not take regular expressions. Remove these characters and search for plain '
            'text: . * + ? [ ] { } ( ) ^ $ | \\',
        ),
        # SPEC-E4.18, SPEC-E4.19, SPEC-F40.1, SPEC-F39.1: only a client that builds its own request makes these. The
        # second is SPEC-E3.8 on a number column too: the form is refused, and `abc`, which is no number, never read
        (
            WORDED,
            {'search': {'value': '', 'multi_values': ['Bob']}},
            'invalid_filter',
            'The search box takes text, not a list of values. Send the text as the search value.',
        ),
        (
            WORDED,
            {'column_search': {'age': {'value': 'abc', 'multi_values': ['x']}}},
            'invalid_filter',
            'The filter on "age" holds both text and a list of values. Send only one of them.',
        ),
        # SPEC-E4.20: what the column holds, by its declared type
        (
            WORDED,
            criterion('name', '<', 'm'),
            'invalid_filter',
            f'The condition "<" cannot be used on "name", which holds text. {CONDITION}',
        ),
        (
            WORDED,
            criterion('age', 'contains', '3'),
            'invalid_filter',
            f'The condition "contains" cannot be used on "age", which holds numbers. {CONDITION}',
        ),
        (
            WORDED,
            criterion('born', 'starts', '19'),
            'invalid_filter',
            f'The condition "starts" cannot be used on "born", which holds dates. {CONDITION}',
        ),
        (
            WORDED,
            criterion('active', '<', '1'),
            'invalid_filter',
            f'The condition "<" cannot be used on "active", which holds true or false. {CONDITION}',
        ),
        # SPEC-E4.22
        (
            WORDED,
            criterion('active', '=', 'yes'),
            'invalid_filter',
            'The filter on "active" needs true or false (1 or 0 also work).',
        ),
        # PRD-F36 (SPEC-F36.1): one value more than the condition takes
        (
            WORDED,
            criterion('name', '=', 'Bob', 'Ann'),
            'invalid_filter',
            'The filter on "name" holds more values than its condition takes. Remove the filter and set it again.',
        ),
        # PRD-F38 (SPEC-E4.31, SPEC-E4.32, SPEC-E4.33, SPEC-E4.34, SPEC-E4.35): each bound names itself, and a list on a name that is not declared is not
        # named
        (
            WORDED,
            {'searchBuilder': nested(9, 'name')},
            'invalid_filter',
            'The search builder nests more than 8 groups. Nest at most 8 groups.',
        ),
        (
            WORDED,
            {'searchBuilder': flat(65, 'name')},
            'invalid_filter',
            'The search builder holds more than 64 criteria. Use at most 64 criteria.',
        ),
        (
            WORDED,
            {'column_search': {'name': {'multi_values': exact_values(257, 'Ann')}}},
            'invalid_filter',
            'The filter on "name" holds more than 256 values. Select at most 256 values.',
        ),
        (
            UNDECLARED,
            {'column_search': {'extra': {'multi_values': exact_values(257, 'Ann')}}},
            'invalid_filter',
            'A column filter holds more than 256 values. Select at most 256 values.',
        ),
        (
            WORDED,
            {
                'column_search': {
                    'name': {'multi_values': exact_values(256, 'Ann')},
                    'team': {'multi_values': exact_values(256, 'red')},
                    'age': {'multi_values': ['31']},
                }
            },
            'invalid_filter',
            'The filters hold more than 512 values in all. Remove some values.',
        ),
        (
            ('name',) * 65,
            {'column_search': {'name': {'value': 'Ann'}}},
            'invalid_filter',
            'The request searches more than 64 columns. Clear some column searches.',
        ),
    ],
)
def test_a_user_error_says_what_is_wrong_and_what_to_do(
    session: Session, names: tuple[str, ...], body: dict[str, Any], *, code: str, text: str
):
    """SPEC-E4.3, SPEC-E4.4, SPEC-E4.5, SPEC-E4.6, SPEC-E4.7, SPEC-E4.8, SPEC-E4.9, SPEC-E4.10, SPEC-E4.11,
    SPEC-E4.12, SPEC-E4.13, SPEC-E4.14, SPEC-E4.15, SPEC-E4.16, SPEC-E4.17, SPEC-E4.18, SPEC-E4.19, SPEC-E4.20,
    SPEC-E4.22, SPEC-E4.25, SPEC-E4.26, SPEC-E4.27, SPEC-E4.28, SPEC-E4.31, SPEC-E4.32, SPEC-E4.33, SPEC-E4.34 and
    SPEC-E4.35: each mistake has a text of its own, which names the condition and the fix, and a column only by the
    name the server declared (SPEC-E4.2: a text on the undeclared `extra` is equal to the fixed one, so it does not
    hold the name). A text is asserted here and nowhere else. The rows are also the proof of their codes: SPEC-E1.2,
    SPEC-F21.6, SPEC-F24.1, SPEC-F25.1, SPEC-F25.2, SPEC-F39.1, SPEC-F40.1."""
    assert refused(worded.fetch_sync(request(*names, **body), session), code) == text


@pytest.mark.parametrize('entry', ['a\x01', 'x' * 256], ids=['control-character', 'too-long'])
def test_a_global_search_list_is_refused_without_its_entries_being_read(session: Session, entry: str):
    """SPEC-F40.2: the list on the global search is a form PRD-F40 refuses, so it answers `invalid_filter` though its
    entry holds a control character or is too long. Catches the entries of a refused global list being read first,
    so that `invalid_character` or `value_too_long` of step 1 answers instead."""
    refused(table.fetch_sync(request(*ALL, search={'value': '', 'multi_values': [entry]}), session))


def test_a_global_search_list_is_not_counted_against_the_bounds_on_lists(session: Session):
    """SPEC-F40.3: a global list of 600 entries is over PRD-F38's 256 per list and 512 per request, but its entries are
    neither read nor counted (PRD-F40), so the number mistake of step 2 beside it answers, and alone it answers its own
    refusal. Catches the global list counted against PRD-F38's bounds, which answers `invalid_filter` of step 1
    instead."""
    search = {'value': '', 'multi_values': ['red'] * 600}
    refused(table.fetch_sync(request(*ALL, search=search, **criterion('age', '=', 'abc')), session), 'invalid_number')
    refused(table.fetch_sync(request(*ALL, search=search), session))


@pytest.mark.parametrize(
    ('column', 'condition', 'values', 'code'),
    [
        ('age', 'between', ['30', '40', '50'], 'invalid_filter'),  # SPEC-F36.1, beside the two a `between` takes
        ('name', '=', ['Bob', 'a\x00'], 'invalid_filter'),  # the surplus value is not read: no invalid_character
        ('age', '=', ['abc', '1'], 'invalid_number'),  # the value the condition reads is checked first (PRD-E3)
    ],
)
def test_a_criterion_with_more_values_than_its_condition_takes_is_refused(
    session: Session, column: str, condition: str, values: list[str], code: str
):
    """SPEC-F36.1, SPEC-F36.4: using the first value alone would answer another question than the one asked. The text is in
    test_a_user_error_says_what_is_wrong_and_what_to_do."""
    refused(table.fetch_sync(request(*ALL, **criterion(column, condition, *values)), session), code)


WIDE = ('id', 'ratio', 'exact', 'count', 'untyped')
TWO_64 = 2**64


def wide(session: Session) -> SQLTable:
    """A table of number columns that are no plain integers. Row 1 holds 2^64 in `ratio` (a float), in `exact` (a
    `Numeric(32, 2)`). Its `count` holds the largest
    value the column takes: the column is declared MySQL's unsigned BIGINT, which only MySQL and MariaDB create
    unsigned, so 2^64 - 1 there and 2^63 - 1 elsewhere. `untyped` is `ratio` as an expression without a SQL type,
    declared a number. Row 2 holds NULL."""
    dialect = session.get_bind().dialect.name
    numbers = Table(
        'dtb_test_tags',
        MetaData(),
        SQLColumn('id', Integer, primary_key=True, autoincrement=False),
        SQLColumn('ratio', Float),
        SQLColumn('exact', Numeric(32, 2)),
        SQLColumn('count', mysql.BIGINT(unsigned=True)),
    )
    numbers.create(session.connection())
    largest = TWO_64 - 1 if dialect in ('mysql', 'mariadb') else 2**63 - 1
    session.execute(
        numbers.insert(),
        [
            {'id': 1, 'ratio': 2.0**64, 'exact': Decimal(TWO_64), 'count': largest},
            {'id': 2, 'ratio': None, 'exact': None, 'count': None},
        ],
    )
    source = select(*numbers.c, literal_column('ratio').label('untyped')).subquery()
    return SQLTable(
        source,
        [
            Column('id'),
            Column('ratio'),
            Column('exact'),
            Column('count'),
            Column('untyped', type='number'),
        ],
    )


@pytest.mark.parametrize(
    'body',
    [
        criterion('ratio', '=', str(TWO_64)),
        {'column_search': {'ratio': {'multi_values': [str(TWO_64)]}}},  # the list reads its values in its own place
        criterion('untyped', '=', str(TWO_64)),  # a column without a number type of its own compares as a float too
        criterion('exact', '=', str(TWO_64)),
    ],
)
def test_a_whole_number_outside_64_bits_is_answered_on_a_column_that_is_no_integer(
    session: Session, body: dict[str, Any]
):
    """SPEC-F25.4, SPEC-F25.5: a driver sends a whole number as a 64-bit integer, so this one failed the request on
    SQLite, PostgreSQL and SQL Server. It goes as the column compares: the nearest float, a decimal to a `Numeric`
    column."""
    assert ids(wide(session).fetch_sync(request(*WIDE, **body), session)) == [1]


def test_the_first_whole_number_outside_64_bits_is_answered_too(session: Session):
    """SPEC-F25.4: 2^63 is one more than a signed 64-bit integer holds. No row holds it."""
    params = request(*WIDE, **criterion('ratio', '=', str(2**63)))
    assert ids(wide(session).fetch_sync(params, session)) == []


def test_a_whole_number_outside_64_bits_is_compared_exactly_with_a_numeric_column(session: Session):
    """SPEC-F25.5: 2^64 + 1 is not the 2^64 the row holds, though as floats they are the same number. SQLite is the
    stated exception (PRD section 5): SQLAlchemy binds a decimal as a float there, so it finds the row."""
    params = request(*WIDE, **criterion('exact', '=', str(TWO_64 + 1)))
    found = [1] if session.get_bind().dialect.name == 'sqlite' else []
    assert ids(wide(session).fetch_sync(params, session)) == found


def test_a_numeric_column_is_not_bounded_by_its_declared_precision(session: Session):
    """SPEC-F25.5: `exact` holds 30 digits before the point, and "below a number of 31 digits" is still a fair
    question, which every database answers: the row."""
    params = request(*WIDE, **criterion('exact', '<', '9' * 31))
    assert ids(wide(session).fetch_sync(params, session)) == [1]


async def test_a_numeric_column_is_not_bounded_by_its_declared_precision_through_the_async_fetch_too(
    session: Session, async_session: AsyncSession
):
    """SPEC-F25.5: the same question, asked through `fetch`. asyncpg casts a `Decimal` compared with a column to the
    column's own `NUMERIC(32, 2)`, so a number of 31 digits overflowed it and the request raised
    `numeric field overflow` there, while the sync drivers answered."""
    numbers = wide(session)
    session.commit()
    params = request(*WIDE, **criterion('exact', '<', '9' * 31))
    assert ids(await numbers.fetch(params, async_session)) == [1]


@pytest.mark.parametrize(
    ('value', 'text'),
    [
        (
            '9' * 39,
            'The filter on "exact" needs a number with at most 38 digits in all, before and after the decimal point; shorten the number.',
        ),
        (
            '1e39',
            'The filter on "exact" needs a number with at most 38 digits in all, before and after the decimal point; shorten the number.',
        ),
    ],
)
def test_a_numeric_column_takes_38_digits_on_sql_server(session: Session, value: str, text: str):
    """SPEC-F25.5 and the text of SPEC-E4.29, asserted here only: SQL Server's decimal holds 38 digits in all, and its
    driver refuses to send a longer number (MEASURED: HY104), so there such a number is the user's
    mistake, written as a whole number or with an exponent. The other databases take it, and no row holds it."""
    response = wide(session).fetch_sync(request(*WIDE, **criterion('exact', '=', value)), session)
    if session.get_bind().dialect.name == 'mssql':
        assert refused(response, 'invalid_number') == text
    else:
        assert ids(response) == []


EXACT = ('12345678901234567.89', '12345678901234567.90', '12345678901234568.00', '0.30', '0.31', '0.00')


def exactly(session: Session) -> SQLTable:
    """A table of one `Numeric(20, 2)` column, `n`, whose rows 1 to 3 are three numbers of 17 digits that a float
    cannot tell apart (they are one float) and whose rows 4 to 6 are 0.30, 0.31 and 0.00."""
    numbers = Table(
        'dtb_test_tags',
        MetaData(),
        SQLColumn('id', Integer, primary_key=True, autoincrement=False),
        SQLColumn('n', Numeric(20, 2)),
    )
    numbers.create(session.connection())
    session.execute(numbers.insert(), [{'id': row, 'n': Decimal(value)} for row, value in enumerate(EXACT, 1)])
    return SQLTable(numbers, [Column('id'), Column('n')])


@pytest.mark.parametrize(
    ('where', 'found', 'sqlite'),
    [
        (('=', '12345678901234567.89'), [1], [1, 2, 3]),
        (('=', '12345678901234567.90'), [2], [1, 2, 3]),
        (('between', '12345678901234567.89', '12345678901234567.89'), [1], [1, 2, 3]),
        (('<', '12345678901234567.90'), [1, 4, 5, 6], [4, 5, 6]),
        (('>', '12345678901234567.89'), [2, 3], []),
        (('=', '12345678901234567.891'), [], [1, 2, 3]),
    ],
)
async def test_a_number_with_a_point_is_compared_exactly_with_a_numeric_column(
    session: Session,
    async_session: AsyncSession,
    where: tuple[str, ...],
    found: list[int],
    sqlite: list[int],
):
    """SPEC-F25.11, PRD-F25: a number written with a point is sent to a `Numeric` column as a decimal, so numbers of
    17 digits are told apart, as the sync and the async driver answer alike. It catches a number sent as a float,
    which is one number for all three rows. SQLite is the stated exception (PRD section 5): it stores the column as
    a float or an integer, so the three rows are one number there."""
    numbers = exactly(session)
    session.commit()
    expected = sqlite if session.get_bind().dialect.name == 'sqlite' else found
    params = request('id', 'n', **criterion('n', *where))
    assert ids(numbers.fetch_sync(params, session)) == expected
    assert ids(await numbers.fetch(params, async_session)) == expected


async def test_a_fraction_longer_than_the_scale_matches_no_row_of_a_numeric_column(
    session: Session, async_session: AsyncSession
):
    """SPEC-F25.11: 0.305 is no number of a `Numeric(20, 2)` column, so `=` finds nothing and `>` stops short of 0.30.
    asyncpg casts a decimal compared with a column to the column's own scale and would round it to 0.31."""
    numbers = exactly(session)
    session.commit()
    for condition, expected in (('=', []), ('>', [1, 2, 3, 5])):
        params = request('id', 'n', **criterion('n', condition, '0.305'))
        assert ids(numbers.fetch_sync(params, session)) == expected
        assert ids(await numbers.fetch(params, async_session)) == expected


@pytest.mark.parametrize(
    ('value', 'found'),
    [
        ('0.' + '1' * 38, [6]),
        ('1' * 20 + '.' + '1' * 18, [1, 2, 3, 4, 5, 6]),
        ('0.31' + '0' * 44, [4, 6]),
        ('1' * 38, [1, 2, 3, 4, 5, 6]),
        ('0.30e0', [6]),
        ('3.1e-1', [4, 6]),
    ],
)
async def test_a_numeric_column_takes_a_number_of_38_digits_on_every_database(
    session: Session, async_session: AsyncSession, value: str, found: list[int]
):
    """SPEC-F25.12: 38 digits in all, the most SQL Server's decimal holds, are answered on both of its drivers
    (pyodbc and aioodbc), a fraction that ends in zeros counting only up to its last digit that is not one."""
    numbers = exactly(session)
    session.commit()
    params = request('id', 'n', **criterion('n', '<', value))
    assert ids(numbers.fetch_sync(params, session)) == found
    assert ids(await numbers.fetch(params, async_session)) == found


@pytest.mark.parametrize('value', ['0.' + '1' * 39, '1' * 20 + '.' + '1' * 19, '-0.' + '1' * 39])
@pytest.mark.parametrize('condition', ['=', '<', 'between'])
def test_a_number_of_more_than_38_digits_is_refused_on_sql_server(session: Session, condition: str, value: str):
    """SPEC-F25.12, SPEC-E4.29: on SQL Server a number of 39 digits in all is the user's mistake, as `invalid_number`
    for every condition, and never the driver's HY104. The other databases take it. It catches a long number
    reaching the driver."""
    numbers = exactly(session)
    values = [value, value] if condition == 'between' else [value]
    response = numbers.fetch_sync(request('id', 'n', **criterion('n', condition, *values)), session)
    if session.get_bind().dialect.name == 'mssql':
        assert 'at most 38 digits' in refused(response, 'invalid_number')
    else:
        assert response.error is None


class Compiling:
    """A source that runs nothing: it keeps the `Decimal` parameters of each statement as `dialect` would send them."""

    def __init__(self, dialect: Dialect) -> None:
        self.dialect = dialect
        self.decimals: list[Decimal] = []
        self.types: list[Any] = []
        self.statements: list[str] = []

    def execute(self, statement: Any) -> Any:
        compiled = statement.compile(dialect=self.dialect, compile_kwargs={'render_postcompile': True})
        binds = [bind for bind in compiled.binds.values() if isinstance(bind.value, Decimal)]
        self.decimals += [bind.value for bind in binds]
        self.statements.append(str(compiled))
        self.types += [bind.type for bind in binds]
        return self

    def scalar_one(self) -> int:
        return 0

    def mappings(self) -> Any:
        return self

    def all(self) -> list[Any]:
        return []


@pytest.mark.parametrize(
    ('value', 'sent'),
    [
        ('0.31' + '0' * 44, Decimal('0.31')),  # a fraction's trailing zeros are left out
        ('100.00', Decimal(100)),
        ('1e37', Decimal('1E+37')),  # an exponent stays as written, 38 digits
        ('3.1e-1', Decimal('3.1E-1')),
        ('0.' + '1' * 38, Decimal('0.' + '1' * 38)),
        ('1' * 20 + '.' + '1' * 18, Decimal('1' * 20 + '.' + '1' * 18)),
        ('0.' + '0' * 40, Decimal(0)),  # zero has no digits, however long its fraction is written
        ('0e-50', Decimal(0)),
        ('0e40', Decimal(0)),
        ('0.' + '1' * 39, None),
        ('1e38', None),  # 39 digits
        ('1' * 39, None),
        ('0.' + '1' * 38 + '0' * 5 + '1', None),
    ],
)
def test_the_38_digits_of_sql_server_are_counted_as_it_counts_them(value: str, sent: Decimal | None):
    """SPEC-F25.12, SPEC-E4.29: compiled for SQL Server, a number of 38 digits in all goes as a `Decimal` with its
    fraction's trailing zeros left out and an exponent as written, and one of 39 is `invalid_number`, named by its limit
    and its fix; the zero before the point of `0.` and 38 digits is no digit. Another database takes the longer number.
    It catches the count and the zeros, which the SQL Server leg shows only with the driver."""
    numbers = Table(
        'dtb_test_tags', MetaData(), SQLColumn('id', Integer, primary_key=True), SQLColumn('n', Numeric(20, 2))
    )
    table = SQLTable(numbers, [Column('id'), Column('n')])
    params = request('id', 'n', **criterion('n', '<', value))
    source: Any = Compiling(mssql.dialect())
    response = table.fetch_sync(params, source)
    if sent is None:
        text = refused(response, 'invalid_number')
        assert 'at most 38 digits in all' in text
        assert source.decimals == []
        other: Any = Compiling(postgresql.dialect())
        assert table.fetch_sync(params, other).error is None
        assert other.decimals
    else:
        assert response.error is None
        assert {str(sent_value) for sent_value in source.decimals} == {str(sent)}  # the same digits and exponent
        # no precision and no scale: asyncpg casts a decimal to the type it is bound as, and the column's own
        # `NUMERIC(20, 2)` would round a longer fraction and overflow a longer whole number
        assert all(isinstance(kind, Numeric) and (kind.precision, kind.scale) == (None, None) for kind in source.types)


def integer_numbers() -> SQLTable:
    numbers = Table('dtb_test_tags', MetaData(), SQLColumn('id', Integer, primary_key=True), SQLColumn('i', Integer))
    return SQLTable(numbers, [Column('id'), Column('i')])


@pytest.mark.parametrize('dialect', [postgresql.dialect, mssql.dialect])
def test_a_fraction_on_an_integer_column_is_compared_as_the_number_written(dialect: Callable[[], Dialect]):
    """SPEC-F25.10: 8.0000000000000000001 is no whole number and a float of it is 8.0, which an integer column
    holds, so it goes as a `Decimal` typed `Numeric()`, as on a `Numeric` column, and a list leaves it out as `=`
    leaves it to match nothing. SQLite compares it as a float whatever the bind (PRD section 5), so only a compiled
    statement shows it there."""
    fraction = '8.0000000000000000001'
    source: Any = Compiling(dialect())
    integer_numbers().fetch_sync(request('id', 'i', **criterion('i', '<', fraction)), source)
    assert {str(value) for value in source.decimals} == {fraction}
    assert all(isinstance(kind, Numeric) and (kind.precision, kind.scale) == (None, None) for kind in source.types)
    listed: Any = Compiling(dialect())
    search = {'multi_values': [fraction, '8']}
    integer_numbers().fetch_sync(request('id', 'i', column_search={'i': search}), listed)
    assert listed.decimals == []


def test_a_fraction_of_more_than_38_digits_on_an_integer_column_is_refused_on_sql_server():
    """SPEC-F25.12, SPEC-F25.14: the count of SQL Server's digits holds on an integer column too, for a fraction it
    compares, `1e-16383` included (16383 digits after the point). That value catches a count of significant digits only,
    which skips the zeros at the start of a fraction."""
    for value in ('8.' + '1' * 38, '1e-16383'):
        source: Any = Compiling(mssql.dialect())
        response = integer_numbers().fetch_sync(request('id', 'i', **criterion('i', '<', value)), source)
        assert 'at most 38 digits in all' in refused(response, 'invalid_number')


class Hundredths(TypeDecorator[Decimal]):
    """A number held in hundredths: the column stores 150 for 1.5."""

    impl = Numeric(20, 0)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        return None if value is None else value * 100

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        return None if value is None else Decimal(value) / 100


def test_a_converting_numeric_type_sees_the_number_a_filter_asks_for(session: Session):
    """SPEC-F25.13: a `TypeDecorator` over `Numeric` converts what it is given, so the number goes through the
    column's own type, as any other comparison does: with a column that holds hundredths, `= 1.5` finds 150 and `= 2`
    and `= 2.0` find 200. It catches a bind that bypasses the decorator, which compared 1.5 with 150. (On asyncpg
    the decimal is cast as the decorator's impl says; only a server shows it.)"""
    numbers = Table('dtb_test_tags', MetaData(), SQLColumn('id', Integer, primary_key=True), SQLColumn('n', Hundredths))
    numbers.create(session.connection())
    session.execute(numbers.insert(), [{'id': 1, 'n': Decimal('1.5')}, {'id': 2, 'n': Decimal(2)}])
    table = SQLTable(numbers, [Column('id'), Column('n')])
    for value, found in (('1.5', [1]), ('2', [2]), ('2.0', [2]), ('3.5', [])):
        assert ids(table.fetch_sync(request('id', 'n', **criterion('n', '=', value)), session)) == found


class Counted(TypeDecorator[int]):
    """A whole number that converts nothing."""

    impl = Integer
    cache_ok = True


def test_a_fraction_on_a_converting_integer_type_is_compared_as_a_float(session: Session):
    """SPEC-F25.10: a `TypeDecorator` over an integer sees no `Decimal` of a fraction: it gets the nearest float, as
    it always did, so `> 7.5` finds 8 and `= 7.5` finds nothing."""
    numbers = Table('dtb_test_tags', MetaData(), SQLColumn('id', Integer, primary_key=True), SQLColumn('i', Counted))
    numbers.create(session.connection())
    session.execute(numbers.insert(), [{'id': 1, 'i': 8}])
    table = SQLTable(numbers, [Column('id'), Column('i')])
    assert ids(table.fetch_sync(request('id', 'i', **criterion('i', '>', '7.5')), session)) == [1]
    assert ids(table.fetch_sync(request('id', 'i', **criterion('i', '=', '7.5')), session)) == []


def test_a_fraction_on_an_integer_column_is_compared_as_exactly_as_the_database_binds_it(session: Session):
    """PRD section 5 (PRD-F25): `8.0000000000000000001` is no whole number, so `=` finds no row on any engine that binds
    the `Decimal` as it is (on PostgreSQL, MySQL, MariaDB and SQL Server). SQLite is the stated exception: SQLAlchemy
    binds the `Decimal` of a fraction as a float there, so it finds the row holding 8. `1e-16383`, which no float holds,
    is bound as zero on SQLite and finds the row holding 0; PostgreSQL finds no row, MySQL and MariaDB find it too (the
    reason is INFERRED: a fraction of more than 30 digits after the point is compared as a double there,
    docs/databases.md), and SQL Server refuses it by its 38 digits. These are today's results, pinned so the stated
    limits are not forgotten if they change."""
    numbers = Table('dtb_test_tags', MetaData(), SQLColumn('id', Integer, primary_key=True), SQLColumn('i', Integer))
    numbers.create(session.connection())
    session.execute(numbers.insert(), [{'id': 1, 'i': 0}, {'id': 2, 'i': 8}])
    table = SQLTable(numbers, [Column('id'), Column('i')])
    engine = session.get_bind().dialect.name
    fraction = table.fetch_sync(request('id', 'i', **criterion('i', '=', '8.0000000000000000001')), session)
    assert ids(fraction) == ([2] if engine == 'sqlite' else [])
    tiny = table.fetch_sync(request('id', 'i', **criterion('i', '=', '1e-16383')), session)
    if engine == 'mssql':
        refused(tiny, 'invalid_number')
    else:
        assert ids(tiny) == ([1] if engine in ('sqlite', 'mysql', 'mariadb') else [])


@pytest.mark.parametrize('dialect', [postgresql.psycopg.dialect, postgresql.asyncpg.dialect])
def test_a_fraction_on_a_converting_integer_type_is_not_cast_to_an_integer(dialect: Callable[[], Dialect]):
    """SPEC-F25.10: psycopg 3 and asyncpg cast a value bound as a `TypeDecorator`'s type to its impl, and PostgreSQL's
    `7.5::INTEGER` is 8, so `> 7.5` would miss 8 (psycopg 2 renders no cast, which is why the live test passes there).
    The fraction is bound as a float instead, so no `INTEGER` is rendered. MEASURED as a compile; the rounding of the
    cast is INFERRED."""
    numbers = Table('dtb_test_tags', MetaData(), SQLColumn('id', Integer, primary_key=True), SQLColumn('i', Counted))
    source: Any = Compiling(dialect())
    SQLTable(numbers, [Column('id'), Column('i')]).fetch_sync(request('id', 'i', **criterion('i', '>', '7.5')), source)
    conditions = [text.split('WHERE')[1].split('ORDER BY')[0] for text in source.statements if 'WHERE' in text]
    assert conditions  # the page's own LIMIT and OFFSET are cast, and are no part of the condition
    assert not any('INTEGER' in condition for condition in conditions)


@pytest.mark.parametrize('kind', [Counted, Float])
@pytest.mark.parametrize('value', ['8.' + '1' * 38, '1e-16383'])
def test_sql_servers_38_digits_do_not_bound_a_column_that_compares_a_float(kind: Any, value: str):
    """SPEC-F25.12, SPEC-F25.14: only a number on a `Numeric` column and a fraction on an integer column are bounded
    there, so a `TypeDecorator` over an integer and a `Float` column take a number of more than 38 digits as the nearest
    float. It catches the check moved before the float is chosen, which refuses valid filters on these columns."""
    numbers = Table('dtb_test_tags', MetaData(), SQLColumn('id', Integer, primary_key=True), SQLColumn('n', kind))
    source: Any = Compiling(mssql.dialect())
    response = SQLTable(numbers, [Column('id'), Column('n')]).fetch_sync(
        request('id', 'n', **criterion('n', '<', value)), source
    )
    assert response.error is None, response.error


def test_a_number_of_more_than_16383_digits_after_the_point_is_refused():
    """SPEC-F25.14, SPEC-F25.16: 1e-16383 is the smallest positive number PostgreSQL's `numeric` takes (its manual,
    Table 8.2, fetched 2026-11-24), so one with more digits after the point is `invalid_number`, never the driver's
    error; it was a float that read as zero. That `1e-16383` itself is answered is shown on a `Numeric` column by the
    live test of SPEC-F25.16 (except on SQL Server, with its 38 digits). `1.5e-16383` has a digit past the limit though
    its first digit is not: it catches the limit read from the size of the number instead of from its last digit. A list
    entry and the bound of a `between` are refused too, with the wording for a value among several (`listed`). The
    `mssql` leg adds that this refusal comes before SQL Server's 38 digits."""
    single = 'The filter on "i" needs a number with at most 16383 digits after the decimal point.'
    among = 'Each value in the filter on "i" must be a number with at most 16383 digits after the decimal point.'
    for dialect in (postgresql.dialect, mssql.dialect):
        for value in ('1e-16384', '1.0e-16384', '-1.0e-16384', '1.5e-16383'):
            source: Any = Compiling(dialect())
            found = integer_numbers().fetch_sync(request('id', 'i', **criterion('i', '=', value)), source)
            assert refused(found, 'invalid_number') == single
        listed = request('id', 'i', column_search={'i': {'multi_values': ['7', '1e-16384']}})
        ranged = request('id', 'i', **criterion('i', 'between', '7', '1e-16384'))
        for params in (listed, ranged):
            compiling: Any = Compiling(dialect())
            found = integer_numbers().fetch_sync(params, compiling)
            assert refused(found, 'invalid_number') == among


@pytest.mark.parametrize(
    ('value', 'past_the_limit'),
    [
        ('1e-9999999999999999999', True),
        ('1E-9999999999999999999', True),  # SPEC-F25.14: also with `E`
        ('-1.5e-9999999999999999999', True),
        ('0.5e-9999999999999999999', True),  # nonzero though it starts with a zero
        ('1e9999999999999999999', False),  # no Decimal holds it; told the reason of `1e400`
        ('0e-9999999999999999999', False),  # zero has no fraction to count, whatever its spelling with such an exponent
        ('-0.0e-9999999999999999999', False),
        ('+0e-9999999999999999999', False),
    ],
)
def test_an_exponent_no_decimal_holds_is_refused_with_the_reason_that_is_true(value: str, past_the_limit: bool):
    """SPEC-F25.14: a nonzero number whose negative exponent is beyond what a `Decimal` holds is one of more than 16383
    digits after the point, so the refusal says that, as it does for `1e-16384`; one with a positive exponent is too
    large, as `1e400`, and zero has no fraction, so both get 'needs one number', the reason for a number no filter can
    use. A bound of a `between` gets the wording for a value among several. It catches the first kind told 'needs one
    number', which says nothing of the limit it is past, a zero told it is past the limit, a spelling with `E` missed,
    and the wording of one value for a `between` bound."""
    reasons = {
        True: (
            'needs a number with at most 16383 digits after the decimal point.',
            'must be a number with at most 16383 digits after the decimal point.',
        ),
        False: ('needs one number, written like 42 or -3.5.', 'must be a number, written like 42 or -3.5.'),
    }
    single, among = reasons[past_the_limit]
    source: Any = Compiling(postgresql.dialect())
    found = integer_numbers().fetch_sync(request('id', 'i', **criterion('i', '=', value)), source)
    assert refused(found, 'invalid_number') == f'The filter on "i" {single}'
    ranged: Any = Compiling(postgresql.dialect())
    found = integer_numbers().fetch_sync(request('id', 'i', **criterion('i', 'between', '7', value)), ranged)
    assert refused(found, 'invalid_number') == f'Each value in the filter on "i" {among}'


@pytest.mark.parametrize(
    ('value', 'sql_server_answers'),
    [('1e-16383', False), ('1.0e-16383', False), ('100e-16385', False), ('0e-16384', True)],
)
def test_the_16383_digit_limit_is_read_by_the_value_not_the_spelling(
    session: Session, value: str, sql_server_answers: bool
):
    """SPEC-F25.16: `1e-16383`, `1.0e-16383` and `100e-16385` are one number and zero is another with no fraction, all
    within the limit, so each is answered on every database, except the first three on SQL Server, whose 38 digits
    refuse them (SPEC-F25.12). `1e-16383`, the plain spelling, is the control; on PostgreSQL its live send also shows
    that the database takes a fraction of 16383 digits (SQLite gets a float, PRD section 5). It catches the limit read
    from the exponent as written, which refuses the others."""
    response = wide(session).fetch_sync(request(*WIDE, **criterion('exact', '=', value)), session)
    if session.get_bind().dialect.name == 'mssql' and not sql_server_answers:
        refused(response, 'invalid_number')
    else:
        assert response.error is None


@pytest.mark.parametrize(
    ('value', 'sent'),
    [
        ('-1.0e-16383', '-1E-16383'),
        # 34 digits written, three trailing zeros among them: `Decimal.normalize` would round the 31 left to 28
        ('1234567890123456789012345678901000e-16386', '1.234567890123456789012345678901E-16353'),
    ],
)
def test_a_number_at_the_16383_digit_limit_reaches_postgresql_with_its_sign_and_digits(value: str, sent: str):
    """SPEC-F25.16: PostgreSQL gets a negative number with its sign and one of 31 significant digits with all of them,
    so the driver never sees a fraction of more than 16383 digits. It catches a sign lost while the zeros are dropped,
    which makes `< -1.0e-16383` include the rows holding 0, and digits rounded away by `Decimal.normalize`."""
    exact = SQLTable(
        Table('dtb_test_tags', MetaData(), SQLColumn('id', Integer, primary_key=True), SQLColumn('n', Numeric(20, 2))),
        [Column('id'), Column('n')],
    )
    source: Any = Compiling(postgresql.dialect())
    assert exact.fetch_sync(request('id', 'n', **criterion('n', '=', value)), source).error is None
    assert {str(bound) for bound in source.decimals} == {sent}  # each statement of the page binds it


@pytest.mark.parametrize('column', ['ratio', 'exact', 'untyped'])
def test_a_number_no_float_holds_is_refused_on_every_number_column(session: Session, column: str):
    """SPEC-F25.1: `1e400` is written as a number, and no column holds infinity, which a float of it would be. The
    decimal that keeps its digits must not let it through to a column compared as a float."""
    response = wide(session).fetch_sync(request(*WIDE, **criterion(column, '<', '1e400')), session)
    refused(response, 'invalid_number')


def test_the_longest_whole_number_a_request_may_hold_is_compared_as_a_float(session: Session):
    """SPEC-F25.4: a float holds every whole number of up to 308 digits and a value is at most 255 characters
    (PRD-F14), so the longest is answered, with no row. A longer one is `value_too_long`, which another test asserts."""
    numbers = wide(session)
    assert ids(numbers.fetch_sync(request(*WIDE, **criterion('ratio', '=', '9' * 255)), session)) == []


def test_a_list_holds_a_whole_number_outside_64_bits(session: Session):
    """SPEC-F25.7: a list entry is sent to a decimal column as a single value is. SQLite binds no `Decimal` and
    raised `ProgrammingError` for the entry; it compares a `Numeric` as a float, so it gets the nearest float."""
    params = request(*WIDE, column_search={'exact': {'multi_values': ['1', str(TWO_64)]}})
    assert ids(wide(session).fetch_sync(params, session)) == [1]


def test_an_unsigned_integer_column_is_bounded_from_zero_up(session: Session):
    """SPEC-F25.6: MySQL's unsigned BIGINT takes 0 to 2^64 - 1, which the type says of itself. A negative number is
    outside it on every database; the largest value is found on MySQL and MariaDB, the only databases that store it
    (MEASURED 2026-11-24 on MariaDB 12.3.3: `bigint(20) unsigned` holds 18446744073709551615)."""
    numbers = wide(session)
    refused(numbers.fetch_sync(request(*WIDE, **criterion('count', '=', '-1')), session), 'invalid_number')
    if session.get_bind().dialect.name in ('mysql', 'mariadb'):
        assert ids(numbers.fetch_sync(request(*WIDE, **criterion('count', '=', str(TWO_64 - 1))), session)) == [1]


@pytest.mark.parametrize(
    ('first', 'second'),
    [
        # SPEC-E3.4: step 3 before step 4, though the condition is refused by the plan and the number's range only
        # later, against the SQL column (`age` is a SMALLINT)
        (('name', '<', 'a'), ('age', '=', '40000')),
        # step 2 before step 3: a number of the wrong format before the last day there is
        (('born', '=', '9999-12-31'), ('age', '=', 'abc')),
    ],
    ids=['range-before-condition', 'format-before-range'],
)
def test_the_step_of_a_mistake_decides_not_its_place_in_the_request(
    session: Session, first: tuple[str, str, str], second: tuple[str, str, str]
):
    """PRD-E3: in each row the second criterion's mistake is of the lower step, and both rows answer for it."""
    tree = {
        'criteria': [
            {'condition': condition, 'origData': column, 'value': [value]}
            for column, condition, value in (first, second)
        ]
    }
    refused(table.fetch_sync(request(*ALL, searchBuilder=tree), session), 'invalid_number')


@pytest.mark.parametrize('found_in_the_build', ['a list value that is no number', 'a number outside the range'])
def test_a_mistake_found_while_the_query_is_built_waits_for_an_earlier_step_of_the_plan(
    session: Session, found_in_the_build: str
):
    """PRD-E3 across the two places a mistake is found: one from the build must not be raised on the spot, since the
    plan may hold one of a lower step. Each row's build mistake alone would answer `invalid_number`."""
    if found_in_the_build == 'a list value that is no number':  # step 2, beside a control character (step 1)
        params = request(*ALL, column_search={'age': {'multi_values': ['abc']}}, **criterion('name', '=', 'a\x00b'))
        expected = 'invalid_character'
    else:  # step 3, beside a date of the wrong format (step 2)
        tree = {
            'criteria': [
                {'condition': '=', 'origData': 'age', 'value': ['40000']},
                {'condition': '=', 'origData': 'born', 'value': ['xx']},
            ]
        }
        params, expected = request(*ALL, searchBuilder=tree), 'invalid_date'
    refused(table.fetch_sync(params, session), expected)


def test_a_group_left_with_no_criterion_by_range_mistakes_is_left_out_whole(session: Session):
    """A criterion that holds a mistake is left out of what is built. A group whose criteria all are must go with
    them: an AND or OR of nothing is no condition SQLAlchemy takes without complaint."""
    tree = {
        'criteria': [
            {'condition': '=', 'origData': 'name', 'value': ['Bob']},
            {
                'logic': 'OR',
                'criteria': [
                    {'condition': '=', 'origData': 'age', 'value': ['40000']},
                    {'condition': '>', 'origData': 'age', 'value': ['50000']},
                ],
            },
        ]
    }
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        refused(table.fetch_sync(request(*ALL, searchBuilder=tree), session), 'invalid_number')


def logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The event of each record, after checking that each is a WARNING of the library's logger (SPEC-E12.1)."""
    assert all((record.name, record.levelname) == ('datatables_backend', 'WARNING') for record in caplog.records), (
        caplog.records
    )
    return [record.msg for record in caplog.records]


@pytest.mark.parametrize(
    'body',
    [
        {'search': {'value': 'a\x00b'}},
        {'search': {'value': 'a\tb'}},  # a control character that is also white space
        {'search': {'value': 'a\x7fb'}},  # and one above the first 32
        {'column_search': {'name': {'value': 'a\x00b'}}},
        {'column_search': {'name': {'value': '^(x|a\x00b)$', 'regex': True}}},
        # On a number column, where the entry would otherwise be refused as no number, with another code
        {'column_search': {'age': {'multi_values': ['31', 'a\x00b']}}},
        criterion('name', '=', 'a\x00b'),
    ],
    ids=['global-nul', 'global-tab', 'global-delete', 'column', 'categorical', 'multi_values', 'criterion'],
)
def test_a_control_character_is_refused_wherever_a_value_is_read(session: Session, body: dict[str, Any]):
    """SPEC-F15.1, SPEC-F15.2: a NUL once cut a LIKE pattern short on SQLite and matched the wrong rows."""
    refused(table.fetch_sync(request(*ALL, **body), session), 'invalid_character')


def test_a_line_break_in_a_search_is_not_a_control_character_to_refuse(session: Session):
    """SPEC-F15.3: LF and CR are what a pasted search holds."""
    assert ids(table.fetch_sync(request(*ALL, search={'value': 'zz\nzz\r'}), session)) == []


def test_a_value_a_condition_does_not_read_is_not_checked(session: Session):
    """SPEC-F36.2's part of PRD-F15: `null` reads no value, so one holding a NUL cannot be the user's mistake."""
    assert ids(table.fetch_sync(request(*ALL, **criterion('age', 'null', 'a\x00b')), session)) == [3]


def test_a_control_character_comes_before_a_number_mistake_and_is_the_one_logged(
    session: Session, caplog: pytest.LogCaptureFixture
):
    """SPEC-E3.1, SPEC-E13.3: step 1 before step 2, and a request holding two mistakes is logged once, for the one
    answered. The number mistake is the one found first: its criterion comes before the one holding the NUL."""
    tree = {
        'criteria': [
            {'condition': '=', 'origData': 'age', 'value': ['abc']},
            {'condition': '=', 'origData': 'name', 'value': ['a\x00b']},
        ]
    }
    with caplog.at_level(logging.DEBUG):
        refused(table.fetch_sync(request(*ALL, searchBuilder=tree), session), 'invalid_character')
    assert logged(caplog) == ['datatables_control_character']


@pytest.mark.parametrize('bound', ['words', 'length', 'depth'])
def test_text_or_a_tree_over_the_bounds_comes_before_a_date_or_number_mistake(session: Session, bound: str):
    """SPEC-E3.2: step 1 before step 2, each bound with its own code."""
    code = 'invalid_filter'
    if bound == 'words':
        code = 'too_many_words'
        params = request(*ALL, search={'value': ' '.join(['w'] * 17)}, **criterion('born', '=', '2024-13-01'))
    elif bound == 'length':
        code = 'value_too_long'
        params = request(*ALL, search={'value': 'w' * 256}, **criterion('born', '=', '2024-13-01'))
    else:
        tree: dict[str, Any] = {'criteria': [{'condition': '=', 'origData': 'age', 'value': ['abc']}]}
        for _ in range(9):
            tree = {'criteria': [tree]}
        params = request(*ALL, searchBuilder=tree)
    refused(table.fetch_sync(params, session), code)


def test_within_one_step_the_mistake_read_first_is_answered(session: Session):
    """SPEC-E3.10: two mistakes of step 2 are answered in step 1's read order, the request's column order and then the
    tree. Catches an answer that prefers one code (a number before a date, or the reverse), that follows the declared
    column order instead of the request's, or that reads the tree before the column searches."""
    both = {'age': {'multi_values': ['abc']}, 'born': {'multi_values': ['2024-13-01']}}
    refused(table.fetch_sync(request(*ALL, column_search=both), session), 'invalid_number')
    born_first = ('id', 'name', 'email', 'born', 'age', 'seen', 'team')
    refused(table.fetch_sync(request(*born_first, column_search=both), session), 'invalid_date')
    params = request(*ALL, column_search={'born': both['born']}, **criterion('age', '=', 'abc'))
    refused(table.fetch_sync(params, session), 'invalid_date')


def test_a_refused_order_entry_comes_after_a_number_mistake(session: Session):
    """SPEC-E3.11: `team` is declared unorderable, so the order on it is refused, at step 4; a number mistake of step 2
    in the tree is answered before it, though the order entry is read first. Catches an order refusal answered as soon
    as it is found."""
    order = [{'column': ALL.index('team'), 'dir': 'asc'}]
    refused(table.fetch_sync(request(*ALL, order=order), session))
    refused(table.fetch_sync(request(*ALL, order=order, **criterion('age', '=', 'abc')), session), 'invalid_number')


def test_a_filter_on_an_unsearchable_column_is_refused_without_its_value_being_read(session: Session):
    """SPEC-E3.12: the server declared `age` unsearchable, so a filter on it is `invalid_filter`, whatever its value.
    Catches a value read as a number before the column's refusal, which would answer `invalid_number`."""
    closed = SQLTable(people, [Column('id'), Column('age', searchable=False)])
    refused(closed.fetch_sync(request('id', 'age', **criterion('age', '=', 'abc')), session))
    params = request('id', 'age', column_search={'age': {'multi_values': ['abc']}})
    refused(closed.fetch_sync(params, session))


def test_within_step_3_the_last_day_comes_before_a_number_outside_its_integer_column(session: Session):
    """SPEC-E3.13: `age` is a SMALLINT, so 40000 is outside its own type, and `< 9999-12-31` uses the maximum date
    every date column has as a bound; both are of step 3, and the maximum date is answered in either order. Catches
    step 3 answered in read order alone, which answers `invalid_number` when the number is read first."""
    number = {'condition': '=', 'origData': 'age', 'value': ['40000']}
    day = {'condition': '<', 'origData': 'born', 'value': ['9999-12-31']}
    refused(table.fetch_sync(request(*ALL, searchBuilder={'criteria': [number]}), session), 'invalid_number')
    for criteria in ([number, day], [day, number]):
        refused(table.fetch_sync(request(*ALL, searchBuilder={'criteria': criteria}), session), 'invalid_date')


def test_a_criterion_without_its_value_is_still_refused_for_its_column_or_its_condition(session: Session):
    """SPEC-E3.14: with no value the criterion is unfinished (PRD-F30), but its column and its condition can still be
    checked, and a refusal of either is reported without a value (`SPEC-E3.8`); with no condition nothing can be
    checked, so it adds nothing, on a refused column and on an undeclared one alike. Catches a criterion without its
    value dropped before its column or condition is checked, which answers every row, and one without a condition
    refused for its column."""
    closed = SQLTable(people, [Column('id'), Column('name'), Column('age', searchable=False)])
    names = ('id', 'name', 'age')
    refused(closed.fetch_sync(request(*names, **criterion('age', '=')), session))
    refused(closed.fetch_sync(request(*names, **criterion('name', '<')), session))
    for condition in ({}, {'condition': ''}, {'condition': 'undefined'}):
        for column in ('age', 'extra'):
            tree = {'criteria': [{**condition, 'origData': column, 'value': ['x']}]}
            assert ids(closed.fetch_sync(request(*names, searchBuilder=tree), session)) == [1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    ('tree', 'answered'),
    [
        (nested(8, 'name'), True),
        (nested(9, 'name'), False),
        (flat(64, 'name'), True),
        (flat(65, 'name'), False),
        # The 65 are counted over the whole tree, and 9 groups side by side are not 9 deep
        ({'logic': 'AND', 'criteria': [flat(32, 'name'), flat(33, 'name')]}, False),
        ({'logic': 'AND', 'criteria': [flat(1, 'name') for _ in range(9)]}, True),
        # The depth is that of the deepest branch, whichever of the groups side by side it is
        ({'logic': 'AND', 'criteria': [flat(1, 'name'), nested(8, 'name')]}, False),
        ({'logic': 'AND', 'criteria': [nested(8, 'name'), flat(1, 'name')]}, False),
    ],
    ids=[
        'depth-8',
        'depth-9',
        'criteria-64',
        'criteria-65',
        'criteria-65-in-two-groups',
        'nine-groups-side-by-side',
        'depth-9-in-the-second-group',
        'depth-9-in-the-first-group',
    ],
)
def test_a_search_builder_tree_is_answered_up_to_8_groups_deep_and_64_criteria(
    session: Session, tree: dict[str, Any], *, answered: bool
):
    """SPEC-F38.1, SPEC-F38.2: the bound is where the statements say, in each direction; a tree past it is
    refused as `invalid_filter` and never reaches the database."""
    response = table.fetch_sync(request(*ALL, searchBuilder=tree), session)
    if answered:
        assert ids(response) == [1]
    else:
        refused(response)


@pytest.mark.parametrize('form', ['multi_values', 'regex'])
@pytest.mark.parametrize(('count', 'answered'), [(256, True), (257, False)])
def test_a_list_of_exact_values_is_answered_up_to_256_values(
    session: Session, form: str, count: int, *, answered: bool
):
    """SPEC-F38.3: a `multi_values` list and a categorical regex are each bounded at 256 values."""
    values = exact_values(count, 'Ann')
    search = {'multi_values': values} if form == 'multi_values' else {'value': f'^({"|".join(values)})$', 'regex': True}
    response = table.fetch_sync(request(*ALL, column_search={'name': search}), session)
    if answered:
        assert ids(response) == [1]
    else:
        refused(response)


@pytest.mark.parametrize(('last', 'answered'), [(0, True), (1, False)], ids=['512', '513'])
def test_the_lists_of_one_request_together_hold_at_most_512_values(session: Session, last: int, *, answered: bool):
    """SPEC-F38.5: two lists of 256 (each within SPEC-F38.3) are answered; one more value in a third list is not.
    A bound on each list alone lets a request send 64 lists of 256."""
    lists = {
        'name': exact_values(256, 'Ann'),
        'email': exact_values(256, 'ann@example.org'),
        'team': ['red'] if last else [],
    }
    searches = {name: {'multi_values': values} for name, values in lists.items() if values}
    response = table.fetch_sync(request(*ALL, column_search=searches), session)
    if answered:
        assert ids(response) == [1]
    else:
        refused(response)


@pytest.mark.parametrize(
    ('searched', 'unsearched', 'answered'),
    [(64, 0, True), (65, 0, False), (64, 50, True)],
    ids=['64', '65', '64-among-columns-with-no-search'],
)
def test_a_request_searches_at_most_64_columns(session: Session, searched: int, unsearched: int, *, answered: bool):
    """SPEC-F38.6: the count is of column searches that hold a value, so the column `name` named 65 times is 65, and
    a column without a search, or with one of spaces only (PRD-F6), is none."""
    names = ('name',) * searched + ('id', 'team') * unsearched
    searches = {'name': {'value': 'Ann'}, 'team': {'value': '  '}}
    response = table.fetch_sync(request(*names, column_search=searches), session)
    if answered:
        assert ids(response) == [1, 3]
    else:
        refused(response)


def test_the_global_search_covers_a_column_once_however_often_the_request_names_it(session: Session):
    """SPEC-F38.7: the column `name` named 1,000 times asks the database the same as when it is named once."""
    statements: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: statements.append(args[2]))
    table.fetch_sync(request('name', search={'value': 'ann'}), session)
    once, statements[:] = statements[:], []
    table.fetch_sync(request(*('name',) * 1000, search={'value': 'ann'}), session)
    same = statements == once  # not asserted inline: a failure would print a thousand terms
    assert same


def test_the_global_search_covers_a_column_named_twice_if_either_entry_is_searchable(session: Session):
    """SPEC-F38.8: `Bob` is only in `name`, and `id` is marked not searchable, so the row is found only if `name` is
    searched; with both of its entries marked not searchable no column is left (PRD-F13). Catches the first or the last
    entry of the name deciding alone."""

    def named_twice(*searchable: bool) -> DTParams:
        columns = [
            {'data': name, 'name': '', 'searchable': flag, 'orderable': True, 'search': {'value': '', 'regex': False}}
            for name, flag in (('id', False), *(('name', flag) for flag in searchable))
        ]
        return parse_request({'draw': 1, 'start': 0, 'length': 10, 'columns': columns, 'search': {'value': 'Bob'}})

    assert ids(table.fetch_sync(named_twice(True, False), session)) == [2]
    assert ids(table.fetch_sync(named_twice(False, True), session)) == [2]
    refused(table.fetch_sync(named_twice(False, False), session))


@pytest.mark.parametrize(
    ('names', 'body', 'event'),
    [
        # A column search with a value, on a name the server did not declare
        (
            (*ALL, 'MARKER_NAME'),
            {'column_search': {'MARKER_NAME': {'value': 'MARKER_VALUE'}}},
            'datatables_name_not_declared',
        ),
        (ALL, criterion('age', '=', 'MARKER_VALUE'), 'datatables_number_unreadable'),
    ],
    ids=['undeclared-column', 'number'],
)
def test_a_user_error_is_logged_exactly_once_without_what_the_client_sent(
    session: Session, caplog: pytest.LogCaptureFixture, names: tuple[str, ...], body: dict[str, Any], event: str
):
    """SPEC-E13.1, SPEC-E10.1, SPEC-E11.1: one WARNING per refusal, under an event of its own that the same case
    logs again, and neither the record nor the reason holds the value or the undeclared name."""
    params = request(*names, **body)
    with caplog.at_level(logging.DEBUG):
        first, second = table.fetch_sync(params, session), table.fetch_sync(params, session)
    assert first.error_code is not None
    assert first == second
    assert logged(caplog) == [event, event]
    assert 'MARKER' not in str(first.error)
    assert 'MARKER' not in repr([vars(record) for record in caplog.records])


async def test_the_async_fetch_answers_and_logs_a_user_error_too(
    async_session: AsyncSession, caplog: pytest.LogCaptureFixture
):
    """SPEC-E1.1 says `fetch` and `fetch_sync`: each has its own place where the mistake becomes an answer."""
    with caplog.at_level(logging.DEBUG, logger='datatables_backend'):
        response = await table.fetch(request(*ALL, **criterion('age', '=', 'abc')), async_session)
    assert refused(response, 'invalid_number')
    assert logged(caplog) == ['datatables_number_unreadable']


def test_a_user_error_runs_no_statement(session: Session):
    """SPEC-E7.1, SPEC-A6.1: also when the mistake is found only while the query is built (the number's range)."""
    statements: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: statements.append(args[2]))
    refused(table.fetch_sync(request(*ALL, **criterion('age', '=', '40000')), session), 'invalid_number')
    assert statements == []


def test_a_failure_that_is_not_the_users_raises(session: Session):
    """SPEC-E5.2, SPEC-E8.1: a table the database does not have is the server's failure; it is never answered, as
    `query_failed` or as an empty page."""
    missing = Table('dtb_test_tags', MetaData(), SQLColumn('code', String(5), primary_key=True))
    with pytest.raises(DBAPIError):
        SQLTable(missing, [Column('code')]).fetch_sync(request('code'), session)


def refuse_every_statement(*_: Any) -> None:
    raise OperationalError('SELECT 1', {}, Exception('connection lost'))


def test_a_connection_that_fails_raises_through_the_sync_fetch(session: Session):
    """SPEC-E8.2: the database's own error reaches the caller; it is not answered as a user error or an empty page."""
    event.listen(session.get_bind(), 'before_cursor_execute', refuse_every_statement)
    with pytest.raises(OperationalError):
        table.fetch_sync(request(*ALL), session)


async def test_a_connection_that_fails_raises_through_the_async_fetch(async_session: AsyncSession):
    """SPEC-E8.2 through `fetch`, which has its own place where the statements run."""
    event.listen(async_session.sync_session.get_bind(), 'before_cursor_execute', refuse_every_statement)
    with pytest.raises(OperationalError):
        await table.fetch(request(*ALL), async_session)


def test_an_exception_of_a_search_value_hook_propagates_unchanged(session: Session):
    """SPEC-E8.4: a hook is the caller's code, so its failure is the caller's to see; answered as `invalid_filter`
    it would send the user to fix a filter that was right."""
    failure = ValueError('the hook failed')

    def fail(_: str) -> str:
        raise failure

    hooked = SQLTable(people, [Column('id'), Column('name', search_value=fail)])
    with pytest.raises(ValueError, match='the hook failed') as raised:
        hooked.fetch_sync(request('id', 'name', column_search={'name': {'value': 'Ann'}}), session)
    assert raised.value is failure


@pytest.mark.parametrize('hook', ['shape', 'row_id', 'row_data'])
def test_an_exception_of_a_row_hook_propagates_unchanged(session: Session, hook: str):
    """SPEC-E8.4: each of the three row hooks, over a row that was found."""
    failure = ValueError('the hook failed')

    def fail(_: Any) -> Any:
        raise failure

    hooked = SQLTable(people, [Column('id'), Column('name')], rows=RowHooks(**{hook: fail}))
    with pytest.raises(ValueError, match='the hook failed') as raised:
        hooked.fetch_sync(request('id', 'name'), session)
    assert raised.value is failure


def test_row_values_come_back_as_the_database_gives_them(session: Session):
    """SPEC-R7.1: no HTML escaping and no conversion to text. A date stays a date, a number a number, NULL None."""
    session.execute(people.update().where(people.c.id == 3).values(name='<b>x</b> & "y"', born=date(2024, 3, 5)))
    response = table.fetch_sync(request(*ALL, start=2, length=1), session)
    assert response.data == [
        {
            'id': 3,
            'name': '<b>x</b> & "y"',
            'email': '',
            'age': None,
            'born': date(2024, 3, 5),
            'seen': None,
            'team': 'red',
        }
    ]


# -- the budget of conditions (PRD-C17, issue #89) ---------------------------------------------------------------------

#: PRD-C17's budget: the conditions one request may hold. The number is measured (docs/decisions/005): a request of
#: this many must be answered by every supported database, and one more is refused before any query.
BUDGET = 960


def wide_request(columns: int, *, search: str, **body: Any) -> tuple[SQLTable, DTParams]:
    """A table of ``columns`` columns that all read `email_address`, and a request whose global search of ``search``
    covers every one of them."""
    wide = SQLTable(people, [Column(f'e{number}', field='email_address') for number in range(columns)])
    return wide, request(*(f'e{number}' for number in range(columns)), search={'value': search}, **body)


def worst_binds_request(*, one_more: bool = False) -> tuple[SQLTable, DTParams]:
    """BUDGET conditions that bind nearly the most parameters: 511 days in two lists on columns that may hold a time
    (two each), 64 ranges (two each), and 385 words of column searches (one each), which is 1,535 parameters. One
    day short of the most the bounds allow (1,536), so that ``one_more`` can add an empty entry to a full list, which
    is 512 entries in all, the bound of PRD-F38, and so BUDGET + 1 conditions."""
    emails = [f'e{number}' for number in range(25)]
    wide = SQLTable(
        people,
        [Column('id'), *(Column(name, field='email_address') for name in emails), Column('seen'), Column('seen_tz')],
    )
    days = [(date(2000, 1, 1) + timedelta(days=offset)).isoformat() for offset in range(255)] + ['2026-07-15']
    between = {'condition': 'between', 'origData': 'id', 'value': ['0', '99']}
    names = ['id', *emails, 'seen', 'seen_tz']
    searched = {name: {'value': LARGEST_WORDS if number < 24 else 'ann'} for number, name in enumerate(emails)}
    listed = {'seen': {'multi_values': days}, 'seen_tz': {'multi_values': [*days[1:], ''] if one_more else days[1:]}}
    criteria = {'logic': 'AND', 'criteria': [between] * 64}
    return wide, request(*names, column_search={**searched, **listed}, searchBuilder=criteria)


def test_a_request_of_the_most_binds_the_budget_allows_is_answered(session: Session):
    """SPEC-C17.1, PRD-C17: BUDGET conditions of the kind that bind two parameters each (days of a column that may
    hold a time, ranges) up to the bounds, which is 1,535 and, with the page's two, within SQL Server's measured
    2,097. It catches a budget that counts a condition as less than the parameters it binds."""
    wide, params = worst_binds_request()
    assert ids(wide.fetch_sync(params, session)) == [1]


def test_a_request_of_the_deepest_expression_the_budget_allows_is_answered(session: Session):
    """SPEC-C17.1, PRD-C17: one negated word of the global search over BUDGET columns, the deepest expression a
    request of BUDGET conditions makes (a chain of BUDGET terms, each `NOT ... OR ... IS NULL`), which SQLite refuses
    from "maximum depth 1000". It catches a budget set so high that the expression outgrows SQLite's depth."""
    wide, params = wide_request(BUDGET, search='!ann')
    response = wide.fetch_sync(params, session)
    assert response.error is None
    assert response.recordsFiltered == 4  # a row without an email passes a negation


@pytest.mark.parametrize(
    'shape',
    [
        pytest.param(lambda: wide_request(BUDGET + 1, search='ann'), id='one word over a column too many'),
        pytest.param(lambda: wide_request(BUDGET + 1, search='!ann'), id='a negated word over a column too many'),
        pytest.param(
            lambda: worst_binds_request(one_more=True), id='the most binds the budget allows, and an empty entry'
        ),
    ],
)
def test_a_request_one_condition_over_the_budget_is_refused_before_any_query(
    session: Session, shape: Callable[[], tuple[SQLTable, DTParams]]
):
    """SPEC-C17.2, PRD-C17, PRD-E2: a request of BUDGET + 1 conditions is answered `request_too_large` with nothing
    run, on every database. The first shape counts the words of the global search; the second a negated word, which
    SQLite's depth would refuse at 992 columns, a client-causable failure; the third adds days of lists and ranges
    and an empty entry of a list, so it catches a budget that leaves those kinds out of the count, which SQL Server's
    limit would show only later. It also catches a budget that is counted after a statement has gone out."""
    sent: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: sent.append(args[2]))
    wide, params = shape()
    response = wide.fetch_sync(params, session)
    assert response.error_code == 'request_too_large'
    assert response.data == []
    assert sent == []


def hooked_wide_request(plain: int, *, search: str, **body: Any) -> tuple[SQLTable, DTParams]:
    """A table of ``plain`` columns that all read `email_address` and a hooked `secret`, and a request whose global
    search of ``search`` covers every one of them."""
    columns = [Column(f'e{number}', field='email_address') for number in range(plain)]
    wide = SQLTable(people, [*columns, Column('secret', search_value=lambda text: f's{text}')])
    return wide, request(*(f'e{number}' for number in range(plain)), 'secret', search={'value': search}, **body)


def test_a_hooked_column_in_the_global_search_is_one_condition_however_many_words(session: Session):
    """SPEC-C17.1, PRD-C17, PRD-D12: two words over 479 columns are 958, a word of a column search is 959, and the
    hooked column's equality is the 960th, where one condition per word would make it 961 and be refused."""
    wide, params = hooked_wide_request(479, search='ann bob', column_search={'e0': {'value': 'ann'}})
    assert wide.fetch_sync(params, session).error is None


def test_a_hooked_column_in_the_global_search_is_counted_in_the_budget(session: Session):
    """SPEC-C17.2, PRD-C17, PRD-D12: two words over 480 columns are exactly BUDGET, and the hooked column's equality
    is the one condition over it, answered `request_too_large`."""
    wide, params = hooked_wide_request(480, search='ann bob')
    assert wide.fetch_sync(params, session).error_code == 'request_too_large'


def test_the_budget_message_defines_a_condition_and_says_how_to_fix_it(session: Session):
    """SPEC-E4.36, PRD-E4: the reason says what is too large, what counts as a condition and what to change, in the
    words of a person at the table, and quotes neither the search nor a column's name. It catches a reason a user
    cannot act on, or one that echoes the request."""
    wide, params = wide_request(BUDGET + 1, search='ann')
    error = wide.fetch_sync(params, session).error
    assert error == (
        'The request is too large: it holds more than 960 conditions (each search word counts once per column '
        'searched, each selected value and each range once). Search for fewer words, search fewer columns, or select '
        'fewer values.'
    )
    assert 'ann' not in error
    assert 'e0' not in error


@pytest.mark.parametrize(
    ('criteria', 'column_search', 'code'),
    [
        pytest.param(['1', 'abc'], {}, 'request_too_large', id='the budget before a number mistake of step 2'),
        pytest.param(['abc'], {}, 'invalid_number', id='a part with a mistake is not counted'),
        pytest.param(['1'], {'e0': {'value': 'a' * 256}}, 'value_too_long', id='the budget after a mistake of step 1'),
    ],
)
def test_the_budget_is_the_last_mistake_of_step_1(
    session: Session, criteria: list[str], column_search: dict[str, Any], code: str
):
    """SPEC-E3.9, PRD-E3: BUDGET conditions of the global search, `id` out of it, and criteria on `id`. A request over
    the budget is answered with it unless a mistake of step 1 was found first, and before a mistake of step 2; a
    criterion with a mistake is left out of the plan and so is not counted. It catches a budget checked at another
    place in the order, or one that counts a part the plan dropped."""
    wide = SQLTable(people, [Column('id'), *(Column(f'e{number}', field='email_address') for number in range(BUDGET))])
    names = ['id', *(f'e{number}' for number in range(BUDGET))]
    tree = {
        'logic': 'AND',
        'criteria': [{'condition': '=', 'origData': 'id', 'value': [value]} for value in criteria],
    }
    params = request(
        *names,
        flags={'id': {'searchable': False}},
        search={'value': 'ann'},
        column_search=column_search,
        searchBuilder=tree,
    )
    assert wide.fetch_sync(params, session).error_code == code


@pytest.mark.parametrize('dialect', [sqlite.dialect, postgresql.dialect, mysql.dialect, mssql.dialect])
@pytest.mark.parametrize(
    ('body', 'conditions'),
    [
        pytest.param({'search': {'value': 'ann'}}, 7, id='a word of the global search, once per column'),
        pytest.param({'search': {'value': '!ann'}}, 7, id='a negated word of the global search'),
        pytest.param({'column_search': {'email': {'value': 'ann'}}}, 1, id='a word of a column search'),
        pytest.param({'column_search': {'name': {'multi_values': list('abcde')}}}, 5, id='five strings'),
        pytest.param({'column_search': {'name': {'multi_values': ['a', '']}}}, 2, id='a string and an empty entry'),
        pytest.param({'column_search': {'age': {'multi_values': list('12345')}}}, 5, id='five numbers'),
        pytest.param(
            {'column_search': {'born': {'multi_values': [f'2024-01-0{day}' for day in range(1, 6)]}}},
            5,
            id='five days of a Date column',
        ),
        pytest.param(
            {'column_search': {'seen': {'multi_values': [f'2024-01-0{day}' for day in range(1, 6)]}}},
            5,
            id='five days of a column that may hold a time',
        ),
        *(
            pytest.param(criterion(column, condition, *values), 1, id=f'{column} {condition}')
            for column, condition, values in [
                ('name', 'contains', ('a',)),
                ('name', '!contains', ('a',)),
                ('name', 'starts', ('a',)),
                ('name', 'ends', ('a',)),
                ('name', '=', ('a',)),
                ('name', '!=', ('a',)),
                ('name', 'null', ()),
                ('name', '!null', ()),
                ('age', '=', ('1',)),
                ('age', '<=', ('1',)),
                ('age', '>', ('1',)),
                ('age', 'between', ('1', '2')),
                ('age', '!between', ('1', '2')),
                ('born', '=', ('2024-01-01',)),
                ('born', '<', ('2024-01-01',)),
                ('born', 'contains', ('2024',)),
                ('seen', 'between', ('2024-01-01', '2024-02-01')),
                ('seen', '!between', ('2024-01-01', '2024-02-01')),
            ]
        ),
    ],
)
def test_a_condition_binds_at_most_two_parameters(
    dialect: Callable[[], Dialect], body: dict[str, Any], conditions: int
):
    """SPEC-C17.4, PRD-C17: the budget counts a condition as one, which holds only while a condition binds at most two
    parameters (a day of a column that may hold a time, a range): the budget times two, and the page's two, must stay
    within SQL Server's 2,100. It catches a new kind of condition, or a change to one, that binds three, which the
    SQL Server leg shows only if the largest request holds that kind."""
    sent: list[int] = []

    class Recorder:
        """A source that runs nothing: it counts the parameters of each statement as `dialect` would send them."""

        def __init__(self) -> None:
            self.dialect = dialect()

        def execute(self, statement: Any) -> Any:
            compiled = statement.compile(dialect=self.dialect, compile_kwargs={'render_postcompile': True})
            sent.append(len(compiled.params))
            return self

        def scalar_one(self) -> int:
            return 0

        def mappings(self) -> Any:
            return self

        def all(self) -> list[Any]:
            return []

    source: Any = Recorder()
    table.fetch_sync(request(*ALL, **body), source)
    total, filtered, page = sent
    assert total == 0
    assert 0 < filtered <= 2 * conditions
    assert page == filtered + 2


def test_a_value_the_number_column_cannot_hold_matches_nothing(session: Session):
    """44.6 on a SMALLINT column matches nothing, as it does on every database: a conversion that failed would fail
    the whole query, and one that rounded would find row 2, which holds 45 (a cast to INTEGER on PostgreSQL does)."""
    params = request(*ALL, column_search={'age': {'multi_values': ['31', '44.6']}})
    assert ids(table.fetch_sync(params, session)) == [1, 4]
    alone = request(*ALL, column_search={'age': {'multi_values': ['44.6']}})  # nothing left to compare with
    assert ids(table.fetch_sync(alone, session)) == []


@pytest.mark.skipif(
    os.environ.get('DTB_SQL_DIALECT', 'sqlite') != 'sqlite',
    reason='a column declared a number that the database holds as a Boolean is refused by PostgreSQL (boolean = '
    'integer) and SQL Server (bit and int) themselves, for a list as for `=` (the leg runs showed it): a wrong '
    'declaration, not a request. SQLite compares across types',
)
def test_a_number_list_on_a_boolean_column_declared_a_number_matches_nothing(session: Session):
    """SPEC-F25.8: a column declared a number that SQLite holds as a Boolean compares a list entry as `=` does, so `2`
    finds no row. Binding it as the column's type made the type refuse it: `StatementError`. (A `Date` declared a
    number is refused at construction, SPEC-D22.1.)"""
    odd = SQLTable(people, [Column('id'), Column('active', type='number')])
    params = request('id', 'active', column_search={'active': {'multi_values': ['2']}})
    assert ids(odd.fetch_sync(params, session)) == []


def test_a_value_longer_than_the_string_column_is_not_cut_to_match(session: Session):
    """`name` is VARCHAR(50). Cut to 50 characters this value is 'Bob' and spaces, which SQL Server compares equal to
    'Bob'."""
    params = request(*ALL, column_search={'name': {'multi_values': ['Bob' + ' ' * 47 + 'x']}})
    assert ids(table.fetch_sync(params, session)) == []


def test_an_order_by_the_key_of_a_join_names_the_key_once(session: Session):
    """PRD-O3 on a join, whose columns are named `<table>_<column>`: the tie-breaker must know the key the user
    already orders by. SQL Server refuses a column named twice in an ORDER BY."""
    other = people.alias('other')
    joined = SQLTable(people.join(other, people.c.id == other.c.id), [Column('id', field='dtb_test_people_id')])
    statements: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: statements.append(args[2]))
    assert ids(joined.fetch_sync(request('id', order=[{'column': 0, 'dir': 'desc'}]), session)) == [5, 4, 3, 2, 1]
    assert statements[-1][statements[-1].rindex('ORDER BY') :].count('dtb_test_people.id') == 1


def test_two_columns_on_one_field_are_ordered_by_once(session: Session):
    """SPEC-O8.4 for two declared columns that read the same field: the second entry is the first one's repeat."""
    twice = SQLTable(people, [Column('id'), Column('name'), Column('label', field='name')])
    statements: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: statements.append(args[2]))
    params = request('id', 'name', 'label', order=[{'column': 1, 'dir': 'desc'}, {'column': 2, 'dir': 'asc'}])
    assert len(ids(twice.fetch_sync(params, session))) == 5  # answered: SQL Server refuses a column named twice
    assert statements[-1][statements[-1].rindex('ORDER BY') :].count('dtb_test_people.name') == 1


def test_a_join_works_as_a_source(session: Session):
    other = people.alias('other')
    joined = people.join(other, people.c.age == other.c.age)
    paired = SQLTable(joined, [Column('id', field='dtb_test_people_id'), Column('name', field='other_name')])
    response = paired.fetch_sync(request('id', 'name'), session)
    assert response.recordsTotal == len(session.execute(select(people.c.id).select_from(joined)).all())


def test_ties_are_broken_by_the_primary_key_not_by_storage_order(session: Session):
    tags = Table(
        'dtb_test_tags', MetaData(), SQLColumn('code', String(5), primary_key=True), SQLColumn('kind', String(5))
    )
    tags.create(session.connection())
    session.execute(tags.insert(), [{'code': c, 'kind': 'k'} for c in ('c', 'a', 'b')])  # stored in this order
    tagged = SQLTable(tags, [Column('code'), Column('kind')])
    response = tagged.fetch_sync(request('code', 'kind', order=[{'column': 1, 'dir': 'asc'}]), session)
    assert [row['code'] for row in response.data] == ['a', 'b', 'c']


# -- the declared unique key (PRD-D11, PRD-O3) ---


def keyed(source: Any, columns: list[Column], key: str | tuple[str, ...]) -> SQLTable:
    return SQLTable(source, columns, unique_key=key)


def tags_without_a_key() -> Table:
    """The table of SPEC-O3.5: no primary key, as a reflected view has none."""
    return Table(
        'dtb_test_tags',
        MetaData(),
        SQLColumn('id', Integer),
        SQLColumn('person_id', Integer),
        SQLColumn('tag', String(5)),
    )


def tagged_people() -> tuple[Table, Any, list[Column]]:
    tags = tags_without_a_key()
    joined = people.join(tags, people.c.id == tags.c.person_id)
    columns = [
        Column('id', field='dtb_test_people_id'),
        Column('age', field='dtb_test_people_age'),
        Column('tag_id', field='dtb_test_tags_id'),
    ]
    return tags, joined, columns


def test_a_declared_key_breaks_ties_on_a_join_without_a_primary_key(session: Session):
    """SPEC-O3.5: Ann (1) has two tags and ties with Cy (4) on `age`. The join's SQLAlchemy primary key is
    `people.id` alone, which repeats, so the tie-breaker must be the declared `tag_id`, whatever the storage order."""
    tags, joined, columns = tagged_people()
    tags.create(session.connection())
    rows = [(4, 5), (3, 4), (2, 1), (1, 1)]  # stored in this order: (tag id, person id)
    session.execute(tags.insert(), [{'id': t, 'person_id': p, 'tag': 'x'} for t, p in rows])
    paired = keyed(joined, columns, 'tag_id')

    def tag_ids(direction: str) -> list[Any]:
        response = paired.fetch_sync(request('id', 'age', 'tag_id', order=[{'column': 1, 'dir': direction}]), session)
        return [row['tag_id'] for row in response.data]

    assert tag_ids('asc') == [1, 2, 3, 4]
    assert tag_ids('desc') == [4, 3, 2, 1]


def test_a_key_of_several_columns_follows_the_first_entrys_direction(session: Session):
    """SPEC-D11.4: ties on `active` are broken by `team`, then `id`, in the direction `active` runs. The declared key
    wins over the primary key (spec/README.md, "Declared key"): Ann (red, 1) and Cy (green, 4) tie on `active`, and
    `team` puts Cy first ascending, which the primary key `id` would not. Bob (2) and Dee (5) tie on `team` as well,
    so only the second name of the key tells them apart."""
    several = keyed(people, [Column('id'), Column('active'), Column('team')], ('team', 'id'))
    for direction, expected in (('asc', [3, 2, 5, 4, 1]), ('desc', [1, 4, 5, 2, 3])):
        params = request('id', 'active', 'team', order=[{'column': 1, 'dir': direction}])
        assert ids(several.fetch_sync(params, session)) == expected


def composite() -> Table:
    return Table(
        'dtb_test_tags',
        MetaData(),
        SQLColumn('a', Integer, primary_key=True),
        SQLColumn('b', Integer, primary_key=True),
        SQLColumn('c', String(5)),
    )


def part_of_a_composite_key() -> Any:
    source = composite()
    return select(source.c.a, source.c.c).subquery()


def join_of_part_of_a_composite_key() -> Any:
    """A subquery that selects only `a` of the key (a, b), inside a join: the join's reduced key holds `a` and the
    other table's key, which two rows of the subquery can share."""
    source = composite()
    part = select(source.c.a, source.c.c).subquery('part')
    other = Table('dtb_test_people', MetaData(), SQLColumn('id', Integer, primary_key=True))
    return part.join(other, part.c.a == other.c.id)


def subquery_over_a_join_with_a_table_without_a_key() -> Any:
    """Only the keyed table's column is selected, so SQLAlchemy reports its key: but each person repeats once for
    every tag the other table holds for them."""
    _, joined, _ = tagged_people()
    return select(people.c.id).select_from(joined).subquery()


def subquery_of_no_table() -> Any:
    return select(literal_column('1').label('one')).subquery()


@pytest.mark.parametrize(
    'make_source',
    [
        tags_without_a_key,  # a table, or a reflected view
        lambda: tagged_people()[1],  # a join of a table without a key
        lambda: select(tags_without_a_key().c.id).subquery(),
        part_of_a_composite_key,  # SPEC-D11.3
        join_of_part_of_a_composite_key,  # SPEC-D11.3, under a join too
        subquery_over_a_join_with_a_table_without_a_key,  # SPEC-D11.2: a key is reported, and repeats
        subquery_of_no_table,  # SPEC-D11.2: no table, so no key at all
    ],
    ids=[
        'table',
        'join',
        'subquery',
        'part-of-a-composite-key',
        'join-of-part-of-a-composite-key',
        'subquery-over-a-join-with-a-table-without-a-key',
        'subquery-of-no-table',
    ],
)
def test_a_source_that_has_no_unique_key_needs_one_declared(make_source: Callable[[], Any]):
    """SPEC-D11.2, SPEC-D11.3: SQLAlchemy reports no key, or a part of one, and the tie-breaker would not be unique.
    The message names the fix."""
    source = make_source()
    first = next(iter(source.columns.keys()))  # a join keys its columns `<table>_<column>`
    with pytest.raises(SchemaError, match=r'unique_key='):
        SQLTable(source, [Column(first)])
    keyed(source, [Column(first)], first)  # declared, it is accepted


def test_a_source_with_its_whole_primary_key_needs_no_declared_key():
    """SPEC-D11.2: the check is not a blanket ban on subqueries or joins. A join on the key of both tables keeps one of
    the two columns (SQLAlchemy reduces them as equal), and is unique all the same."""
    source = composite()
    SQLTable(source, [Column('a')])
    SQLTable(select(source.c.a, source.c.b).subquery(), [Column('a')])
    other = Table('dtb_test_tags', MetaData(), SQLColumn('id', Integer, primary_key=True))
    SQLTable(people.join(other, people.c.id == other.c.id), [Column('id', field='dtb_test_people_id')])


@pytest.mark.parametrize('key', ['nope', ('a', 'nope'), ()])
def test_a_unique_key_the_source_cannot_use_is_a_schema_error(key: str | tuple[str, ...]):
    """SPEC-D11.1: the key must name a declared column."""
    with pytest.raises(SchemaError, match='unique_key'):
        keyed(composite(), [Column('a'), Column('c')], key)


def test_a_form_encoded_multi_values_list_is_read(session: Session):
    """The only request that sends ``multi_values`` as a form does (``...[multi_values][]``): were the key dropped or
    read as no list, this global search would be idle and answered."""
    params = parse_request([('columns[0][data]', 'name'), ('search[multi_values][]', 'Bob')])
    refused(table.fetch_sync(params, session))


# -- the bounds of every value (PRD-F14), answered with their own codes (PRD-E2, PRD-E3) ---

SEVENTEEN = ' '.join(f'q{number}' for number in range(17))  # distinct, so an echo of any word shows
OVER_THE_BOUNDS: list[tuple[dict[str, Any], str]] = [
    ({'column_search': {'age': {'value': '1' * 256}}}, 'value_too_long'),  # SQL searches a number's text
    ({'column_search': {'name': {'multi_values': ['a', 'b' * 256]}}}, 'value_too_long'),
    ({'column_search': {'born': {'multi_values': ['2024-01-01', '1' * 256]}}}, 'value_too_long'),
    ({'column_search': {'name': {'value': f'^(a|{"b" * 256})$', 'regex': True}}}, 'value_too_long'),
    (criterion('name', '=', 'a' * 256), 'value_too_long'),
    (criterion('name', 'contains', 'a' * 256), 'value_too_long'),
    (criterion('name', 'starts', 'a' * 256), 'value_too_long'),
    (criterion('age', '=', '1' * 256), 'value_too_long'),
    # SPEC-F14.4: the length comes before the number is read, so no `invalid_number` and no conversion: Python refuses
    # an int of more than 4,300 digits, and a long run of zeros costs a parse
    (criterion('age', '=', '1' * 4301), 'value_too_long'),
    (criterion('age', '=', '0' * 5000), 'value_too_long'),
    (criterion('age', 'between', '1', '2' * 256), 'value_too_long'),
    (criterion('born', '=', '2024-01-01' + ' ' * 250), 'value_too_long'),
    (criterion('born', 'contains', '2024' + ' ' * 252), 'value_too_long'),
    # the length comes before the name: `active` is no column of this table
    (criterion('active', '=', 'true' + ' ' * 252), 'value_too_long'),
    # PRD-E3: step 1 comes before every step it hides, in the same part of the request as in another
    (criterion('age', '=', 'x' * 300), 'value_too_long'),  # no number, and too long
    (criterion('born', '=', '2024-13-01' + ' ' * 250), 'value_too_long'),
    ({'search': {'value': SEVENTEEN, 'regex': True}}, 'too_many_words'),
    ({'column_search': {'extra': {'value': SEVENTEEN}}}, 'too_many_words'),  # no column to lift the cap
    ({'column_search': {'name': {'value': ' '.join(['abcdefghij'] * 30)}}}, 'value_too_long'),  # 30 words, length first
    # PRD-E3, within one filter: a control character in any value, then the length of any value
    ({'column_search': {'name': {'multi_values': ['a' * 256, 'b\x01']}}}, 'invalid_character'),
    (criterion('age', 'between', '1' * 256, '2\x01'), 'invalid_character'),
    ({'search': {'value': 'a' * 256, 'multi_values': ['x']}}, 'value_too_long'),
    ({'column_search': {'extra': {'value': 'a' * 256}}}, 'value_too_long'),  # not a name the server declares
    ({'column_search': {'name': {'value': 'a' * 256, 'multi_values': ['x']}}}, 'value_too_long'),
    ({'search': {'value': 'a' * 256 + '\x00'}}, 'invalid_character'),
    ({'search': {'value': ' '.join(['abcdefghij'] * 30)}}, 'value_too_long'),  # 30 words too, and the length first
    (
        {'search': {'value': SEVENTEEN}, 'column_search': {'name': {'value': 'a' * 256}}},
        'too_many_words',
    ),  # reading order
    ({'search': {'value': 'a' * 256}, **criterion('age', '=', 'abc')}, 'value_too_long'),
]


@pytest.mark.parametrize(('body', 'code'), OVER_THE_BOUNDS)
def test_a_value_over_the_bounds_is_refused_with_its_code_and_echoes_nothing(
    session: Session, body: dict[str, Any], code: str
):
    """PRD-F14, PRD-E3 (SPEC-F14.1, SPEC-F14.2, SPEC-F14.3, SPEC-F14.4, SPEC-F14.5, SPEC-E3.2): the same refusal for
    every kind of value and part of the request, with a text that holds none of what was sent."""
    names = (*ALL, 'extra') if 'extra' in str(body) else ALL
    message = refused(table.fetch_sync(request(*names, **body), session), code)
    assert not any(sent in message for sent in ('aaa', 'bbb', 'xxx', 'abc', '111', *SEVENTEEN.split()))


@pytest.mark.parametrize(
    'body',
    [
        {'column_search': {'name': {'value': 'a' * 255}}},
        {'column_search': {'name': {'multi_values': ['a', 'b' * 255]}}},
        {'column_search': {'name': {'value': f'^(a|{"b" * 255})$', 'regex': True}}},
        criterion('name', '=', 'a' * 255),
        criterion('name', 'contains', 'a' * 255),
        criterion('name', 'starts', 'a' * 255),
        criterion('age', '=', '0' * 255),  # 255 digits, a number the column holds
        criterion('age', 'between', '1', '0' * 252 + '100'),  # the second value is 255 characters, and 100
        criterion('born', '=', '2024-01-01' + ' ' * 245),
        criterion('age', 'null', '1' * 300),  # a value the condition does not read is not measured
    ],
)
def test_a_value_within_the_bounds_is_answered(session: Session, body: dict[str, Any]):
    """SPEC-F14.4: the bound is at 255 code points, not below."""
    assert table.fetch_sync(request(*ALL, **body), session).error is None


@pytest.mark.parametrize(('padding', 'code'), [(251, None), (252, 'value_too_long')])
def test_a_boolean_value_is_measured_at_255_code_points(session: Session, padding: int, code: str | None):
    """SPEC-F14.4: `true` and its padding are 255 characters and are read as `true`; one more is too long. The boolean
    column is declared here, which the bounds table above does not do."""
    response = flagged.fetch_sync(request(*FLAGGED, **criterion('active', '=', 'true' + ' ' * padding)), session)
    if code is None:
        assert ids(response) == [1, 4]
    else:
        refused(response, code)


def test_a_surplus_value_is_not_measured_and_stays_the_mistake_it_is(session: Session):
    """PRD-F14, PRD-F36: only the values a condition reads are measured."""
    refused(table.fetch_sync(request(*ALL, **criterion('name', '=', 'Bob', 'a' * 300)), session))


@pytest.mark.parametrize(
    ('body', 'code', 'text'),
    [
        (
            {'search': {'value': 'a' * 256}},
            'value_too_long',
            'The search text holds more than 255 characters. Shorten it to at most 255.',
        ),
        (
            {'column_search': {'name': {'value': 'a' * 256}}},
            'value_too_long',
            'The search on "name" holds more than 255 characters. Shorten it to at most 255.',
        ),
        (
            {'column_search': {'name': {'multi_values': ['a' * 256]}}},
            'value_too_long',
            'A value in the filter on "name" holds more than 255 characters. Shorten each value to at most 255.',
        ),
        (
            criterion('name', '=', 'a' * 256),
            'value_too_long',
            'A value in the filter on "name" holds more than 255 characters. Shorten each value to at most 255.',
        ),
        (
            {'search': {'value': SEVENTEEN}},
            'too_many_words',
            'The search text holds more than 16 words. Search for at most 16 words.',
        ),
        (
            {'column_search': {'name': {'value': SEVENTEEN}}},
            'too_many_words',
            'The search on "name" holds more than 16 words. Search for at most 16 words.',
        ),
    ],
)
def test_the_text_of_each_bound_names_the_condition_and_the_fix(
    session: Session, body: dict[str, Any], code: str, text: str
):
    """SPEC-E4.30: each text is fixed, names only a declared column, and says what to shorten."""
    assert refused(table.fetch_sync(request(*ALL, **body), session), code) == text


def test_words_are_not_counted_on_a_boolean_column(session: Session):
    """PRD-F14: a boolean's value is one value to read, not words, so 17 of them are no `too_many_words` but the
    value that is no boolean (PRD-E3 (4))."""
    flags = SQLTable(people, [Column('id'), Column('active')])
    response = flags.fetch_sync(request('id', 'active', column_search={'active': {'value': SEVENTEEN}}), session)
    assert refused(response) == 'The filter on "active" needs true or false (1 or 0 also work).'


# -- column declaration (PRD-D1 to PRD-D11, PRD-D13, PRD-D21) ---


@pytest.mark.parametrize('names', [('team', 'id'), ('id',), tuple(reversed(ALL))], ids=['subset', 'one', 'reversed'])
def test_a_request_naming_some_columns_gets_rows_with_every_declared_column(session: Session, names: tuple[str, ...]):
    """SPEC-D1.1: the request may name the declared columns in any order or only some; each row holds them all."""
    rows = table.fetch_sync(request(*names), session).data
    assert [row['id'] for row in rows] == [1, 2, 3, 4, 5]
    assert all(set(row) == set(ALL) for row in rows)


def test_a_request_with_no_columns_search_or_order_answers_every_row(session: Session):
    """SPEC-D1.3: nothing is asked of the columns, so every row comes, and filtered equals total."""
    response = table.fetch_sync(parse_request({'draw': 1, 'start': 0, 'length': 10}), session)
    assert (response.recordsTotal, response.recordsFiltered) == (5, 5)
    assert ids(response) == [1, 2, 3, 4, 5]
    assert all(set(row) == set(ALL) for row in response.data)


def test_a_renamed_column_is_searched_by_its_stored_field_and_reaches_the_client_by_its_name(session: Session):
    """SPEC-D2.1: `email` reads `email_address`: a search on it finds Cy, and the row has `email` and never the field."""
    response = table.fetch_sync(request(*ALL, column_search={'email': {'value': 'cy@'}}), session)
    assert ids(response) == [4]
    assert response.data[0]['email'] == 'cy@example.org'
    assert 'email_address' not in response.data[0]


def test_the_declared_type_decides_the_comparison(session: Session):
    """SPEC-D3.1: `<` on `age` (a number) compares numbers, and on `name` (a string) is refused."""
    assert ids(table.fetch_sync(request(*ALL, **criterion('age', '<', '40')), session)) == [1, 4]
    refused(table.fetch_sync(request(*ALL, **criterion('name', '<', 'm')), session))


def test_a_column_declared_unsearchable_is_left_out_of_every_search(session: Session):
    """SPEC-D4.1: with `name` unsearchable, the global search does not find Ann through it and a column search on it is
    refused."""
    unsearchable = SQLTable(people, [Column('id'), Column('name', searchable=False), Column('team')])
    assert ids(unsearchable.fetch_sync(request('id', 'name', 'team', search={'value': 'ann'}), session)) == []
    refused(unsearchable.fetch_sync(request('id', 'name', column_search={'name': {'value': 'ann'}}), session))


def test_a_column_search_on_an_undeclared_name_is_refused_with_a_value_and_answered_without_one(session: Session):
    """SPEC-D6.1."""
    assert ids(table.fetch_sync(request(*ALL, 'extra'), session)) == [1, 2, 3, 4, 5]
    refused(table.fetch_sync(request(*ALL, 'extra', column_search={'extra': {'value': 'x'}}), session))


def test_a_criterion_on_an_undeclared_name_is_refused_at_any_depth_beside_a_matching_one(session: Session):
    """SPEC-D6.2: in a nested group, in an OR group, next to a criterion that matches rows."""
    tree = {
        'logic': 'OR',
        'criteria': [
            {'condition': '=', 'origData': 'name', 'value': ['Ann']},
            {'logic': 'AND', 'criteria': [{'condition': '=', 'origData': 'secret', 'value': ['s1']}]},
        ],
    }
    refused(table.fetch_sync(request(*ALL, searchBuilder=tree), session))


def test_a_column_search_calls_the_hook_once_with_what_was_typed(session: Session, cased: SQLTable):
    """SPEC-D9.1: `Alpha` reaches the hook once, and matches the stored `enc:Alpha`."""
    seen: list[str] = []

    def hook(text: str) -> str:
        seen.append(text)
        return f'enc:{text}'

    hooked = SQLTable(cased.source, [Column('id'), Column('secret', search_value=hook)])
    assert ids(hooked.fetch_sync(request('id', 'secret', column_search={'secret': {'value': 'Alpha'}}), session)) == [1]
    assert seen == ['Alpha']


def test_a_column_over_an_expression_is_ordered_and_searched_by_it(session: Session, cased: SQLTable):
    """SPEC-D10.1: a column over `-id` is ordered as the database orders that expression, which is not the order of the
    key on any database (a collation would decide for `lower(kind)`, and PostgreSQL's and SQL Server's order it as
    `kind`); a search on one over `lower(kind)` finds `red` in every case."""
    tags = cased.source
    over = SQLTable(
        select(tags.c.id, (-tags.c.id).label('neg'), func.lower(tags.c.kind).label('low')).subquery(),
        [Column('id'), Column('neg'), Column('low')],
    )
    ordered = ids(over.fetch_sync(request('id', 'neg', 'low', order=[{'column': 1, 'dir': 'asc'}]), session))
    by_database = [row.id for row in session.execute(select(tags.c.id).order_by(-tags.c.id))]
    assert ordered == by_database
    assert ordered != sorted(ordered)  # the key's own order, which an order that was ignored would give
    found = over.fetch_sync(request('id', 'neg', 'low', column_search={'low': {'value': 'red'}}), session)
    assert ids(found) == [1, 2, 4]


def test_a_column_declared_without_the_fold_still_searches_ignoring_case(session: Session, cased: SQLTable):
    """SPEC-D21.3: `contains` `RED` on `plain` (`fold_case=False`) finds `red`, `reddish` and `RED`, as on `kind`."""
    for column in ('plain', 'kind'):
        assert ids(cased.fetch_sync(request(*CASED, **criterion(column, 'contains', 'RED')), session)) == [1, 2, 4]


# -- ordering and the API (PRD-O, PRD-A) ---


@pytest.mark.parametrize(
    'order',
    [
        [{'column': 6, 'dir': 'asc'}, {'column': 3, 'dir': 'asc'}],
        [{'column': 3, 'dir': 'asc'}, {'column': 6, 'dir': 'asc'}],
    ],
    ids=['first', 'second'],
)
def test_an_order_list_is_refused_whole_wherever_the_refused_entry_stands(
    session: Session, order: list[dict[str, Any]]
):
    """SPEC-O1.8: `team` is unorderable for the server and the request calls it orderable; `age` is fine."""
    refused(table.fetch_sync(request(*ALL, order=order), session))


def test_the_page_walk_of_an_order_with_ties_holds_every_row_once_and_descending_is_its_reverse(session: Session):
    """SPEC-O3.2: Ann and Cy tie at 31 and row 3 has no age; for every length from 1 to 6 the pages joined give each
    row once, in the order of one big page, and descending gives exactly the reverse. Ages are numbers: no collation
    decides this order."""

    def walk(direction: str, length: int) -> list[int]:
        order = [{'column': 3, 'dir': direction}]
        pages = [
            ids(table.fetch_sync(request(*ALL, start=s, length=length, order=order), session))
            for s in range(0, 5, length)
        ]
        return [row for page in pages for row in page]

    whole = {
        direction: ids(table.fetch_sync(request(*ALL, order=[{'column': 3, 'dir': direction}]), session))
        for direction in ('asc', 'desc')
    }
    assert sorted(whole['asc']) == [1, 2, 3, 4, 5]
    assert whole['desc'] == whole['asc'][::-1]
    for length in range(1, 7):
        assert walk('asc', length) == whole['asc'], length
        assert walk('desc', length) == whole['desc'], length


def test_a_string_column_is_ordered_as_the_database_orders_it_with_no_collation_of_the_librarys(session: Session):
    """SPEC-O6.1: the names come in the database's own order for `ORDER BY name`, whichever collation it uses. On SQLite
    that is the code-point order, with `anna_100%` last, where an order that folded case would put it second."""
    ordered = ids(table.fetch_sync(request(*ALL, order=[{'column': 1, 'dir': 'asc'}]), session))
    assert ordered == [row.id for row in session.execute(select(people.c.id).order_by(people.c.name, people.c.id))]
    if session.get_bind().dialect.name == 'sqlite':
        assert ordered == [1, 2, 4, 5, 3]  # a case-folding order gives [1, 3, 2, 4, 5]


def test_the_order_by_of_a_string_column_holds_no_collate_clause(session: Session):
    """SPEC-O6.2: the library adds no collation of its own to a string column's ORDER BY."""
    statements: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: statements.append(args[2]))
    table.fetch_sync(request(*ALL, order=[{'column': 1, 'dir': 'asc'}]), session)
    order_by = [statement for statement in statements if 'ORDER BY' in statement]
    assert order_by
    assert all('COLLATE' not in statement.upper() for statement in order_by)


def test_one_table_answers_the_same_request_the_same_after_another(session: Session):
    """SPEC-A1.1: request X, then Y (a column search and an order), then X again: the two answers to X are equal."""
    x = request(*ALL, search={'value': 'red'})
    y = request(*ALL, column_search={'team': {'value': 'blue'}}, order=[{'column': 3, 'dir': 'desc'}])
    first = table.fetch_sync(x, session)
    answer_y = table.fetch_sync(y, session)
    assert table.fetch_sync(x, session) == first
    assert answer_y != first
    assert ids(answer_y) == [5, 2]


def _varied_requests(count: int) -> list[DTParams]:
    words = ['red', 'ann', 'example', 'zzz', '']
    return [
        request(
            *ALL, search={'value': words[number % 5]}, order=[{'column': 3, 'dir': 'desc' if number % 2 else 'asc'}]
        )
        for number in range(count)
    ]


async def test_one_table_answers_100_requests_and_20_concurrent_ones_as_a_fresh_table_does(
    session: Session, database: tuple[str, str]
):
    """SPEC-A2.1: each answer equals the one of a table built for that request; the concurrent ones each run on their
    own `AsyncConnection`."""
    for params in _varied_requests(100):
        assert table.fetch_sync(params, session) == SQLTable(people, COLUMNS).fetch_sync(params, session)
    engine = async_engine(database[1])

    async def answer(params: DTParams) -> DTResponse:
        async with engine.connect() as connection:
            return await table.fetch(params, connection)

    try:
        concurrent = _varied_requests(20)
        answers = await asyncio.gather(*(answer(params) for params in concurrent))
    finally:
        await engine.dispose()
    assert answers == [SQLTable(people, COLUMNS).fetch_sync(params, session) for params in concurrent]


def test_building_a_table_touches_no_database_and_the_first_statement_runs_inside_fetch(session: Session):
    """SPEC-A3.1: the constructor takes no connection, so there is none to touch."""
    statements: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: statements.append(args[2]))
    built = SQLTable(people, COLUMNS)
    assert statements == []
    built.fetch_sync(request(*ALL), session)
    assert statements


async def test_an_async_session_and_an_async_connection_answer_the_base_request(async_session: AsyncSession):
    """SPEC-A4.1."""
    assert ids(await table.fetch(request(*ALL), async_session)) == [1, 2, 3, 4, 5]
    assert ids(await table.fetch(request(*ALL), await async_session.connection())) == [1, 2, 3, 4, 5]


def test_a_session_and_a_connection_answer_the_base_request(session: Session):
    """SPEC-A5.1."""
    assert ids(table.fetch_sync(request(*ALL), session)) == [1, 2, 3, 4, 5]
    assert ids(table.fetch_sync(request(*ALL), session.connection())) == [1, 2, 3, 4, 5]


def test_a_join_and_a_subquery_are_tables_too(session: Session, cased: SQLTable):
    """SPEC-A7.1: a join of `people` to the rows of `dtb_test_tags` whose size is LARGE, its columns named
    `<table>_<column>`, answers the two people it joins; a table over the subquery of the red people answers them."""
    tags = cased.source
    joined = people.join(tags, and_(people.c.id == tags.c.id, tags.c.size == 'LARGE'))
    over_join = SQLTable(
        joined, [Column('id', field='dtb_test_people_id'), Column('name', field='dtb_test_people_name')]
    )
    assert ids(over_join.fetch_sync(request('id', 'name'), session)) == [2, 5]
    reds = select(people).where(people.c.team == 'red').subquery()
    assert ids(SQLTable(reds, [Column('id'), Column('team')]).fetch_sync(request('id', 'team'), session)) == [1, 3]


# -- counts and paging (PRD-C) ---


def test_a_page_shorter_than_the_filtered_set_still_counts_the_whole_set(session: Session):
    """SPEC-C2.1: `red` finds Ann and Anna; with a length of 1 the page holds one row and `recordsFiltered` stays 2."""
    whole = table.fetch_sync(request(*ALL, search={'value': 'red'}), session)
    assert (whole.recordsTotal, whole.recordsFiltered, ids(whole)) == (5, 2, [1, 3])
    page = table.fetch_sync(request(*ALL, search={'value': 'red'}, length=1), session)
    assert (page.recordsTotal, page.recordsFiltered, ids(page)) == (5, 2, [1])


def test_a_search_that_finds_nothing_has_filtered_zero_and_the_total_intact(session: Session):
    """SPEC-C2.2."""
    response = table.fetch_sync(request(*ALL, search={'value': 'zzz'}), session)
    assert (response.recordsTotal, response.recordsFiltered, response.data, response.error) == (5, 0, [], None)


def test_a_start_equal_to_the_row_count_is_an_empty_page_with_both_counts(session: Session):
    """SPEC-C3.1: the page after the last row is empty, not an error."""
    response = table.fetch_sync(request(*ALL, start=5, length=10), session)
    assert (response.recordsTotal, response.recordsFiltered, response.data, response.error) == (5, 5, [], None)


def test_a_negated_column_search_counts_with_count_statements_and_keeps_the_null_row(session: Session):
    """SPEC-C4.1: `!ann` on the email column leaves out Ann only: Bob's NULL email passes. Both counts are
    `count(*)` statements, and `recordsFiltered` is the number of rows the page returns."""
    statements: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: statements.append(args[2]))
    response = table.fetch_sync(request(*ALL, column_search={'email': {'value': '!ann'}}), session)
    assert ids(response) == [2, 3, 4, 5]
    assert (response.recordsTotal, response.recordsFiltered) == (5, 4)
    counts = [s for s in statements if 'dtb_test_people' in s and 'count(' in s.lower()]
    assert len(counts) == 2  # not the statements a first connection runs to learn the dialect


def test_the_page_statement_limits_the_rows_in_the_database(session: Session):
    """SPEC-C5.1: start 1, length 2 gives rows 2 and 3, and the page statement limits them itself: `LIMIT` on SQLite,
    PostgreSQL and MySQL (`LIMIT x, y` there), `OFFSET ... FETCH` on SQL Server, whichever the leg is."""
    statements: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: statements.append(args[2]))
    assert ids(table.fetch_sync(request(*ALL, start=1, length=2), session)) == [2, 3]
    page = [s for s in statements if 'dtb_test_people' in s and 'count(' not in s.lower()]
    assert page
    assert re.search(r'\b(LIMIT|FETCH|TOP)\b', page[-1].upper())


def test_the_page_statement_selects_no_undeclared_column(session: Session):
    """SPEC-C6.1: `secret`, `active` and `seen_tz` are not declared: the page statement names none and no row has one."""
    statements: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: statements.append(args[2]))
    response = table.fetch_sync(request(*ALL), session)
    page = [s for s in statements if 'dtb_test_people' in s and 'count(' not in s.lower()][-1].lower()
    assert not any(name in page for name in ('secret', 'active', 'seen_tz'))
    assert all(not {'secret', 'active', 'seen_tz'} & set(row) for row in response.data)


def test_a_cached_total_is_counted_once_between_two_requests_within_the_ttl(session: Session):
    """SPEC-C14.1: with a search the total is a count of its own; the second request runs the filtered count and the
    page only, and both answer the same total."""
    statements: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: statements.append(args[2]))
    cached = SQLTable(people, COLUMNS, total_cache=TotalCache(ttl=60))
    first = cached.fetch_sync(request(*ALL, search={'value': 'red'}), session)
    ran_first = len([s for s in statements if 'dtb_test_people' in s])
    second = cached.fetch_sync(request(*ALL, search={'value': 'red'}), session)
    ran_second = len([s for s in statements if 'dtb_test_people' in s]) - ran_first
    assert (first.recordsTotal, second.recordsTotal) == (5, 5)
    assert (ran_first, ran_second) == (3, 2)  # total, filtered count, page; then the last two


async def test_the_async_fetch_counts_a_cached_total_once_between_two_requests_within_the_ttl(
    async_session: AsyncSession,
):
    """SPEC-C14.1 through `fetch`: the async path reads and fills the cache as `fetch_sync` does."""
    statements: list[str] = []
    event.listen(async_session.get_bind(), 'before_cursor_execute', lambda *args: statements.append(args[2]))
    cached = SQLTable(people, COLUMNS, total_cache=TotalCache(ttl=60))
    first = await cached.fetch(request(*ALL, search={'value': 'red'}), async_session)
    ran_first = len([s for s in statements if 'dtb_test_people' in s])
    second = await cached.fetch(request(*ALL, search={'value': 'red'}), async_session)
    ran_second = len([s for s in statements if 'dtb_test_people' in s]) - ran_first
    assert (first.recordsTotal, second.recordsTotal) == (5, 5)
    assert (ran_first, ran_second) == (3, 2)  # total, filtered count, page; then the last two


def test_the_cached_total_is_counted_again_after_the_ttl(session: Session):
    """SPEC-C14.2: a row inserted at once is not seen; a request after the TTL (0.3 s, waited out) counts again."""
    cached = SQLTable(people, COLUMNS, total_cache=TotalCache(ttl=0.3))
    params = request(*ALL, search={'value': 'red'})
    assert cached.fetch_sync(params, session).recordsTotal == 5
    session.execute(people.insert(), {'id': 6, 'name': 'Eve', 'team': 'red', 'secret': 's6'})
    assert cached.fetch_sync(params, session).recordsTotal == 5
    time.sleep(0.35)
    assert cached.fetch_sync(params, session).recordsTotal == 6


def _bounded_asker(session: Session, cache: TotalCache) -> Callable[[int], tuple[int, int]]:
    """Asks the table whose base filter is `id <= bound` (a total of `bound`) over the shared cache, and answers its
    `recordsTotal` and the statements the request ran: 3 on a miss (total, filtered count, page), 2 on a hit."""
    statements: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: statements.append(args[2]))

    def ask(bound: int) -> tuple[int, int]:
        before = len([s for s in statements if 'dtb_test_people' in s])
        table_for_bound = SQLTable(people, COLUMNS, base_filter=people.c.id <= bound, total_cache=cache)
        response = table_for_bound.fetch_sync(request(*ALL, search={'value': 'red'}), session)
        return response.recordsTotal, len([s for s in statements if 'dtb_test_people' in s]) - before

    return ask


def test_a_full_cache_drops_the_oldest_total_and_a_read_does_not_make_one_newer(session: Session):
    """SPEC-C14.4: with room for two, the third total drops the first; reading the second changes nothing; the fifth
    drops the second and the sixth still finds the third. Breaks if the cache has no bound, a wrong bound, drops the
    newest, or reorders on a read (the last request would count)."""
    ask = _bounded_asker(session, TotalCache(ttl=60, max_entries=2))
    answers = [ask(bound) for bound in (1, 2, 3, 2, 1, 3)]
    assert answers == [(1, 3), (2, 3), (3, 3), (2, 2), (1, 3), (3, 2)]


def test_a_total_counted_again_after_its_expiry_is_the_newest(session: Session):
    """SPEC-C14.5: both totals expire; the first is counted again and so is newer than the second, which the third
    then drops. Breaks if a total stored again keeps its old place (the third would drop the one just counted)."""
    ask = _bounded_asker(session, TotalCache(ttl=0.3, max_entries=2))
    answers = [ask(1), ask(2)]
    time.sleep(0.35)
    answers += [ask(bound) for bound in (1, 3, 1)]
    assert answers == [(1, 3), (2, 3), (1, 3), (3, 3), (1, 2)]


def test_a_cache_with_no_room_is_refused_when_built_and_the_default_is_10000():
    """SPEC-C14.6: a bound below 1 would silently cache nothing; the message names the parameter. Without a bound
    the cache holds 10,000 totals (PRD-C14 states the number)."""
    for bound in (0, -1):
        with pytest.raises(ValueError, match='max_entries'):
            TotalCache(max_entries=bound)
    assert TotalCache().max_entries == 10_000


def test_one_table_and_one_cache_never_share_a_total_between_two_databases(tmp_path: Path):
    """SPEC-C15.2: two SQLite files holding 4 and 2 rows. The key holds the database URL on every dialect, so
    this proves it for all."""
    cache = TotalCache(ttl=60)
    cached = SQLTable(people, COLUMNS, total_cache=cache)
    totals: list[int] = []
    for name, rows in (('four.db', ROWS[:4]), ('two.db', ROWS[:2])):
        engine = create_engine(f'sqlite:///{tmp_path / name}')
        people.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(people.insert(), rows)
        with Session(engine) as database_session:
            totals.append(cached.fetch_sync(request(*ALL, search={'value': 'red'}), database_session).recordsTotal)
        engine.dispose()
    assert totals == [4, 2]


def _listeners(*targets: Any) -> dict[tuple[str, str], int]:
    """How many listeners each event of each target has: an engine, a pool, or the classes themselves."""
    snapshot: dict[tuple[str, str], int] = {}
    for target in targets:
        label = f'class {target.__name__}' if isinstance(target, type) else f'{type(target).__name__} {id(target)}'
        for name in target.dispatch._event_names:
            collection = getattr(target.dispatch, name)
            levels = getattr(collection, '_clslevel', None)
            snapshot[label, name] = (
                sum(len(each) for each in levels.values()) if levels is not None else len(collection)
            )
    return snapshot


def test_building_and_using_a_table_and_a_cache_registers_no_listener(session: Session):
    """SPEC-C16.2: engine and pool, and the classes every engine and pool shares, hold the same listeners before and
    after a table and a cache are built and a cached request is answered (PRD-C16: a total is as old as its TTL)."""
    engine = session.get_bind().engine
    targets = (engine, engine.pool, Engine, Pool)
    before = _listeners(*targets)

    def spy(*args: Any) -> None:
        pass

    event.listen(engine, 'before_cursor_execute', spy)  # the snapshot sees a listener at all
    assert _listeners(*targets) != before
    event.remove(engine, 'before_cursor_execute', spy)
    assert _listeners(*targets) == before
    built = SQLTable(people, COLUMNS, total_cache=TotalCache(ttl=60))
    assert _listeners(*targets) == before
    built.fetch_sync(request(*ALL, search={'value': 'red'}), session)
    assert _listeners(*targets) == before


# -- ColumnControl's per-column search and list (PRD-F45) --------------------------------------------------------------


def _found(*, flags: dict[str, dict[str, Any]], session: Session, using: SQLTable = table, **body: Any) -> list[Any]:
    return sorted(ids(using.fetch_sync(request(*ALL, flags=flags, **body), session)))


def _refusal(*, flags: dict[str, dict[str, Any]], session: Session, using: SQLTable = table, **body: Any) -> str:
    response = using.fetch_sync(request(*ALL, flags=flags, **body), session)
    assert response.error_code is not None
    return response.error_code


def test_columncontrol_contains_gives_the_rows_of_a_column_search(session: Session):
    """SPEC-F45.2: the control's own `contains` is the column search it sits beside (PRD-F2): the same words, the same
    rows, on a column of text, of numbers and of days."""
    for column, words in (('name', 'an'), ('team', 'RED'), ('age', '31'), ('born', '2001')):
        plain = sorted(ids(table.fetch_sync(request(*ALL, column_search={column: {'value': words}}), session)))
        assert plain
        assert _found(flags=columncontrol(column, 'contains', words), session=session) == plain


@pytest.mark.parametrize(
    ('column', 'logic', 'value', 'expected'),
    [
        ('team', 'equal', 'RED', [1, 3]),
        ('team', 'notEqual', 'red', [2, 4, 5]),
        ('team', 'starts', 'bl', [2, 5]),
        ('team', 'ends', 'en', [4]),
        ('team', 'notContains', 'lu', [1, 3, 4]),
        ('email', 'empty', '', [2, 3]),
        ('email', 'notEmpty', '', [1, 4, 5]),
    ],
)
def test_columncontrol_logics_answer_as_the_searchbuilder_condition_they_name(
    session: Session, column: str, logic: str, value: str, expected: list[int]
):
    """SPEC-F45.3, SPEC-F45.4: `equal`, `notEqual`, `starts`, `ends`, `notContains` and `empty` give the rows of `=`,
    `!=`, `starts`, `ends`, `!contains` and `null`, and `notEmpty` those of `!null`: a NULL and the empty text are both
    empty on SQL (PRD-F26)."""
    conditions = {'equal': '=', 'notEqual': '!=', 'notContains': '!contains', 'empty': 'null', 'notEmpty': '!null'}
    flags = columncontrol(column, logic, value)
    assert _found(flags=flags, session=session) == expected
    as_criterion = criterion(column, conditions.get(logic, logic), *([value] if value else []))
    assert _found(flags={}, session=session, **as_criterion) == expected


def test_a_columncontrol_list_answers_as_a_list_of_exact_values(session: Session):
    """SPEC-F45.5: a list of values is the exact values of `multi_values` (PRD-F20), the empty entry meaning empty."""
    assert _found(flags=columncontrol_list('team', ['RED', 'green']), session=session) == [1, 3, 4]
    assert _found(flags=columncontrol_list('email', ['', 'ann@example.org']), session=session) == [1, 2, 3]


def test_a_plain_search_a_columncontrol_search_and_a_list_on_one_column_all_apply(session: Session):
    """SPEC-F45.6: three filters of one column are ANDed; PRD-F39 refuses a value beside a list in one search, not
    across them."""
    flags = {'name': {'search': {'value': 'an', 'regex': False}, **columncontrol('name', 'starts', 'anna')['name']}}
    assert _found(flags=flags, session=session) == [3]
    search = columncontrol('team', 'notEqual', 'blue')['team']['columnControl']['search']
    both = {'team': {'columnControl': {'search': search, 'list': {'0': 'red', '1': 'blue'}}}}
    assert _found(flags=both, session=session) == [1, 3]


@pytest.mark.parametrize('text', ['x', 'x' * 300])
def test_the_text_left_in_the_input_of_an_empty_search_is_neither_read_nor_measured(session: Session, text: str):
    """SPEC-F45.7: `empty` and `notEmpty` take no value, so the text ColumnControl leaves in its input is no
    `value_too_long` and no search."""
    assert _found(flags=columncontrol('email', 'empty', text), session=session) == [2, 3]
    assert _found(flags=columncontrol('email', 'notEmpty', text), session=session) == [1, 4, 5]


@pytest.mark.parametrize(
    ('column', 'logic', 'value'),
    [('age', 'starts', '3'), ('age', 'ends', '1'), ('age', 'notContains', '3'), ('born', 'starts', '2001')],
)
def test_a_columncontrol_logic_the_columns_type_does_not_take_is_refused(
    session: Session, column: str, logic: str, value: str
):
    """SPEC-F45.8: the condition a logic names is refused on a number or a date as SearchBuilder refuses it (PRD-F27)."""
    assert _refusal(flags=columncontrol(column, logic, value), session=session) == 'invalid_filter'


def test_a_columncontrol_number_that_is_no_number_is_invalid_number(session: Session):
    """SPEC-F45.8: the number rules of a condition come with it (PRD-F25): `equal` `abc` on a number column."""
    assert _refusal(flags=columncontrol('age', 'equal', 'abc'), session=session) == 'invalid_number'
    assert _refusal(flags=columncontrol_list('age', ['abc']), session=session) == 'invalid_number'


def test_columncontrol_on_a_hooked_column_is_equality_or_refused(session: Session):
    """SPEC-F45.9: `contains` is the whole text through the hook (PRD-D17), `equal` is the hooked value, `starts` is
    refused (PRD-D18), and `empty` is answered."""
    hooked = SQLTable(people, [Column('id'), Column('secret', search_value=lambda text: f's{text}')])
    columns = ('id', 'secret')

    def found(flags: dict[str, dict[str, Any]]) -> list[Any]:
        return sorted(ids(hooked.fetch_sync(request(*columns, flags=flags), session)))

    assert found(columncontrol('secret', 'contains', '1')) == [1]
    assert found(columncontrol('secret', 'equal', '2')) == [2]
    assert found(columncontrol_list('secret', ['3', '4'])) == [3, 4]
    assert found(columncontrol('secret', 'empty')) == []
    assert found(columncontrol('secret', 'notEmpty')) == [1, 2, 3, 4, 5]
    refused = hooked.fetch_sync(request(*columns, flags=columncontrol('secret', 'starts', '1')), session)
    assert refused.error_code == 'invalid_filter'


def test_a_columncontrol_list_holds_at_most_256_values_and_512_in_all(session: Session):
    """SPEC-F45.10: a list of 256 is answered and one of 257 refused; three lists of 256 are over the request's 512."""
    names = [f'v{number}' for number in range(255)]
    assert _found(flags=columncontrol_list('team', ['red', *names]), session=session) == [1, 3]
    assert _refusal(flags=columncontrol_list('team', ['red', *names, 'v255']), session=session) == 'invalid_filter'
    three = {column: columncontrol_list(column, ['red', *names])[column] for column in ('team', 'name', 'email')}
    assert _refusal(flags=three, session=session) == 'invalid_filter'


def _wide_control_request(words_on: int, *, extra_word: bool) -> tuple[SQLTable, DTParams]:
    """``words_on`` columns that read `email_address`, each with a ColumnControl `contains` of 16 words (16
    conditions), and one more with a word, which is the 961st condition when ``words_on`` is 60."""
    names = [f'e{number}' for number in range(words_on + extra_word)]
    wide = SQLTable(people, [Column(name, field='email_address') for name in names])
    flags: dict[str, dict[str, Any]] = {}
    for number, name in enumerate(names):
        flags |= columncontrol(name, 'contains', LARGEST_WORDS if number < words_on else 'ann')
    return wide, request(*names, flags=flags)


def test_columncontrol_searches_count_toward_the_budget_of_conditions(session: Session):
    """SPEC-F45.10: 60 searches of 16 words are exactly the budget and answered; one more word is `request_too_large`,
    with nothing run."""
    wide, params = _wide_control_request(60, extra_word=False)
    assert wide.fetch_sync(params, session).error is None
    sent: list[str] = []
    event.listen(session.get_bind(), 'before_cursor_execute', lambda *args: sent.append(args[2]))
    wide, params = _wide_control_request(60, extra_word=True)
    assert wide.fetch_sync(params, session).error_code == 'request_too_large'
    assert sent == []


def test_columncontrol_filters_count_toward_the_64_column_searches(session: Session):
    """SPEC-F45.10: a search and a list of one column are two of the 64 (PRD-F38): 32 columns of both are answered, and
    a 33rd column's search makes 65."""
    names = [f'e{number}' for number in range(33)]
    wide = SQLTable(people, [Column(name, field='email_address') for name in names])
    flags: dict[str, dict[str, Any]] = {}
    for name in names[:32]:
        flags[name] = {
            'columnControl': {**columncontrol(name, 'equal', 'x')[name]['columnControl'], 'list': {'0': 'y'}}
        }
    assert wide.fetch_sync(request(*names, flags=flags), session).error is None
    flags |= columncontrol(names[32], 'equal', 'x')
    assert wide.fetch_sync(request(*names, flags=flags), session).error_code == 'invalid_filter'


def test_the_idle_columncontrol_parts_of_a_wide_table_count_for_nothing(session: Session):
    """SPEC-F45.10: ColumnControl sends an idle search and an empty list for every column on every request, so 70 of
    them are no 70 column searches (PRD-F38) and the request is answered."""
    names = [f'e{number}' for number in range(70)]
    wide = SQLTable(people, [Column(name, field='email_address') for name in names])
    flags = {
        name: {'columnControl': {**columncontrol(name, 'contains')[name]['columnControl'], 'list': {}}}
        for name in names
    }
    assert wide.fetch_sync(request(*names, flags=flags), session).error is None


def test_a_columncontrol_value_is_checked_for_a_control_character_and_for_its_length_first(session: Session):
    """SPEC-F45.11: a control character is `invalid_character` and a value over 255 code points `value_too_long`, each
    before the `invalid_filter` of another column's refused logic (PRD-E3 step 1 against step 4)."""
    refused_logic = columncontrol('age', 'starts', '3')
    for flags, code in (
        (columncontrol('name', 'contains', 'a\x07'), 'invalid_character'),
        (columncontrol('name', 'equal', 'a\x07'), 'invalid_character'),
        (columncontrol_list('team', ['red\x07']), 'invalid_character'),
        (columncontrol('name', 'equal', 'a' * 256), 'value_too_long'),
        (columncontrol('name', 'contains', 'a' * 256), 'value_too_long'),
    ):
        assert _refusal(flags={**refused_logic, **flags}, session=session) == code


@pytest.mark.parametrize('logic', ['equal', 'empty'])
def test_a_columncontrol_search_on_a_column_the_server_does_not_offer_is_refused(session: Session, logic: str):
    """SPEC-F45.12: a column the server declares unsearchable, one it does not declare and one with no name are refused
    as a column search on them is (PRD-F41), and never dropped."""
    closed = SQLTable(people, [Column('id'), Column('team', searchable=False)])
    names = ('id', 'team', 'nope', '')
    part = columncontrol('any', logic, 'x')['any']  # the column's ColumnControl part, whichever column holds it
    for name in ('team', 'nope', ''):
        flags: dict[str, dict[str, Any]] = {'': {'data': None}}  # the column with no name renders only
        flags[name] = {**flags.get(name, {}), **part}
        response = closed.fetch_sync(request(*names, flags=flags), session)
        assert response.error_code == 'invalid_filter', name
    listed = closed.fetch_sync(request(*names, flags=columncontrol_list('team', ['red'])), session)
    assert listed.error_code == 'invalid_filter'


@pytest.mark.parametrize(
    ('value', 'code'), [('a\x07', 'invalid_character'), ('a' * 300, 'value_too_long')], ids=['control', 'long']
)
def test_a_columncontrol_value_on_a_nameless_column_is_checked_before_the_refusals_of_later_steps(
    session: Session, value: str, code: str
):
    """SPEC-F45.12, SPEC-F45.11: the control character and the length are step 1 of PRD-E3, whichever column holds the
    value; the refusal of a column with no name is step 4, so another column's number mistake (step 2) does not hide
    them either."""
    names = ('id', 'age', '')
    nameless = {'': {'data': None, **columncontrol('', 'equal', value)['']}}
    for flags in (nameless, {**nameless, **columncontrol('age', 'greater', 'abc')}):
        response = table.fetch_sync(request(*names, flags=flags), session)
        assert response.error_code == code


def test_what_a_column_refuses_is_answered_in_the_order_plain_search_then_columncontrol_search(session: Session):
    """SPEC-F45.6, PRD-E3: a plain search and a ColumnControl search of one column that are both refused at step 4 are
    answered with the plain search's message."""
    plain = {'value': '.*', 'regex': True}
    flags = {'age': {'search': plain, **columncontrol('age', 'starts', '3')['age']}}
    error = table.fetch_sync(request(*ALL, flags=flags), session).error
    assert error is not None
    assert 'regular expressions' in error
    assert _refusal(flags=columncontrol('age', 'starts', '3'), session=session) == 'invalid_filter'


def test_a_columncontrol_value_of_white_space_is_a_value_and_not_dropped(session: Session):
    """SPEC-F45.3, PRD-F30: `equal` two spaces matches the rows that hold exactly that, here none, and the request is
    not answered as if the filter were absent."""
    assert _found(flags=columncontrol('team', 'equal', '  '), session=session) == []
    assert _found(flags=columncontrol('team', 'notEqual', '  '), session=session) == [1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    ('logic', 'value', 'expected'),
    [
        ('greater', '31', [2, 5]),
        ('greaterOrEqual', '45', [2, 5]),
        ('less', '45', [1, 4]),
        ('lessOrEqual', '45', [1, 2, 4]),
    ],
)
def test_columncontrol_number_logics_answer_as_a_comparison_and_leave_null_out(
    session: Session, logic: str, value: str, expected: list[int]
):
    """SPEC-F45.13: `greater`, `greaterOrEqual`, `less` and `lessOrEqual` compare, as `>`, `>=`, `<` and `<=`; the row
    with no age is in none of them (the stated difference, section 5)."""
    assert _found(flags=columncontrol('age', logic, value), session=session) == expected


def test_columncontrol_number_logics_follow_the_number_rules_and_the_type(session: Session):
    """SPEC-F45.14: a text that is no number is `invalid_number`; the four are refused on text as their conditions are
    (PRD-F27)."""
    assert _refusal(flags=columncontrol('age', 'greater', 'abc'), session=session) == 'invalid_number'
    assert _refusal(flags=columncontrol('team', 'greater', 'a'), session=session) == 'invalid_filter'


def test_columncontrol_not_equal_lets_a_null_through(session: Session):
    """SPEC-F45.15: `notEqual` is `!=`, which keeps the row with no value (PRD-F37), on a number as on text."""
    assert _found(flags=columncontrol('age', 'notEqual', '31'), session=session) == [2, 3, 5]


@pytest.mark.parametrize(
    ('logic', 'expected'),
    [('greater', [4]), ('greaterOrEqual', [1, 4]), ('less', [2, 5]), ('lessOrEqual', [1, 2, 5])],
)
def test_columncontrol_comparisons_on_a_date_compare_days(session: Session, logic: str, expected: list[int]):
    """SPEC-F45.16: sent with `type` `text`, the four answer as `>`, `>=`, `<` and `<=` on the date column, compared
    as days; row 1 holds the day itself, and row 3, with no date, is in none. On text they are refused (PRD-F27).
    Catches the four refused, dropped or read as numbers on a date column, and the bound day put on the wrong side."""
    assert _found(flags=columncontrol('born', logic, '1994-03-01'), session=session) == expected
    assert _refusal(flags=columncontrol('team', logic, 'red'), session=session) == 'invalid_filter'
