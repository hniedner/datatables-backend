"""The SQL backend through the public API only: a request in, a response out.

It runs on SQLite, or on the database DTB_SQL_URL names (the container jobs: PostgreSQL, MySQL, SQL Server).
"""

from collections.abc import Callable
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal
import logging
import os
import re
from typing import Any
import uuid
import warnings

import pytest
from sqlalchemy import CHAR
from sqlalchemy import BigInteger
from sqlalchemy import Column as SQLColumn
from sqlalchemy import Enum
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import Uuid
from sqlalchemy import and_
from sqlalchemy import cast
from sqlalchemy import event
from sqlalchemy import func
from sqlalchemy import literal_column
from sqlalchemy import select
from sqlalchemy.dialects import mysql
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Dialect
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.expression import ColumnElement
from sqlalchemy.types import TypeDecorator
from sqlalchemy.types import TypeEngine

from datatables_backend import Column
from datatables_backend import DTResponse
from datatables_backend import RowHooks
from datatables_backend import SchemaError
from datatables_backend import TotalCache
from datatables_backend import parse_request
from datatables_backend.sqlalchemy import SQLTable

from .conftest import checked_database
from .conftest import criterion
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
    response = table.fetch_sync(request(*ALL, start=1, length=2, draw=7), session)
    assert response.draw == 7
    assert (response.recordsTotal, response.recordsFiltered) == (5, 5)
    assert ids(response) == [2, 3]  # no order asked: the primary key orders the rows


def test_rows_carry_only_the_declared_columns_under_their_client_names(session: Session):
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
    reds = SQLTable(people, COLUMNS, base_filter=people.c.team == 'red')
    response = reds.fetch_sync(request(*ALL, search={'value': 'ann'}), session)
    assert (response.recordsTotal, response.recordsFiltered) == (2, 2)
    response = reds.fetch_sync(request(*ALL, search={'value': 'anna'}), session)
    assert (response.recordsTotal, response.recordsFiltered, ids(response)) == (2, 1, [3])


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


def test_a_declared_column_the_source_lacks_fails_at_construction():
    with pytest.raises(SchemaError, match='nope'):
        SQLTable(people, [Column('id'), Column('nope')])


def test_the_total_is_cached_until_the_ttl(session: Session):
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
    """SPEC-E13.2: a column that only renders and DataTables' default order on it are every request's traffic. Each
    skip there is, is in this request: an order on a column that only renders, on an undeclared one and on `team`,
    which both sides mark unorderable, a column ordered twice, and the two columns the global search leaves out."""
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
    assert ids(table.fetch_sync(request(*ALL, search={'value': 'ANN'}), session)) == [1, 3]


def test_like_wildcards_in_a_search_are_literal(session: Session):
    assert ids(table.fetch_sync(request(*ALL, search={'value': '0%'}), session)) == [3]
    assert ids(table.fetch_sync(request(*ALL, search={'value': 'a_1'}), session)) == [3]


def test_the_global_search_matches_a_date_by_its_day_text(session: Session):
    assert ids(table.fetch_sync(request(*ALL, search={'value': '2026-07-1'}), session)) == [1, 2, 5]
    assert ids(table.fetch_sync(request(*ALL, search={'value': '1980-07'}), session)) == [2]  # a Date column


def test_the_global_search_matches_a_number_by_its_text(session: Session):
    assert ids(table.fetch_sync(request(*ALL, search={'value': '45'}), session)) == [2]


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
CASE_SENSITIVE = {'mysql': 'utf8mb4_bin', 'mssql': 'Latin1_General_CS_AS'}


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
        ({'column_search': {'kind': {'multi_values': ['rEd', 'BLUE']}}}, [1, 3, 4]),  # SPEC-F9.3
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


@pytest.mark.xfail(
    reason='PRD-D17 (SPEC-F9.8), issue #28: a column search on a hooked column is still a substring search in any '
    'case over the transformed text, not an equality',
    strict=True,
    raises=AssertionError,
)
def test_a_column_search_on_a_hooked_column_compares_exactly(session: Session, cased: SQLTable):
    params = request(*CASED, column_search={'secret': {'value': 'alpha'}})
    assert ids(cased.fetch_sync(params, session)) == []


@pytest.mark.skipif(
    os.environ.get('DTB_SQL_DIALECT') != 'mssql',
    reason='only SQL Server sends a long list as JSON (PRD-C17); on the other databases this repeats the list above',
)
def test_a_list_sent_as_json_ignores_case_too(session: Session, cased: SQLTable):
    values = [f'n{index}' for index in range(3000)] + ['rEd']
    params = request(*CASED, column_search={'kind': {'multi_values': values}})
    assert ids(cased.fetch_sync(params, session)) == [1, 4]


@pytest.mark.skipif(
    os.environ.get('DTB_SQL_DIALECT') != 'mssql',
    reason='only SQL Server sends a long list as JSON (PRD-C17); on the other databases this repeats the list above',
)
@pytest.mark.parametrize(
    ('column', 'value', 'expected'),
    [
        ('plain', 'rEd', []),
        ('plain', 'RED', [4]),
        ('wrapped', 'RED', [4]),  # the collation is declared on the type a `TypeDecorator` wraps
        # 32 characters on a CHAR(32): a cast to a bare CHAR would cut the value to 30. The list is sent as the
        # client wrote it, so the stored form is what matches here
        ('ref', uuid.UUID(ref(1)).hex, [1]),
    ],
)
def test_a_list_sent_as_json_on_a_column_without_the_fold_follows_its_collation(
    session: Session, cased: SQLTable, column: str, value: str, expected: list[int]
):
    """The JSON list casts each value to text without a length or a collation; the column's own collation,
    case-sensitive here, still compares."""
    values = [f'n{index}' for index in range(3000)] + [value]
    params = request(*CASED, column_search={column: {'multi_values': values}})
    assert ids(cased.fetch_sync(params, session)) == expected


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
        ('day', [6, 7]),  # so does a string column, whatever it is declared as: a blank cell is empty
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
    """SPEC-F26.3: what the column stores decides, seen through a `TypeDecorator`, not what it is declared as."""
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
            Column('day', field='kind', type='date'),
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
        ('age', '<=', ['31.0'], [1, 4]),
        ('age', 'between', ['40', '60'], [2, 5]),
        ('age', '!between', ['40', '60'], [1, 3, 4]),  # row 3 has no age (PRD-F37)
        ('age', 'null', [], [3]),
        ('born', '=', ['1980-07-15'], [2]),
        ('born', '<', ['1980-07-15'], [5]),
        ('born', '<=', ['1980-07-15'], [2, 5]),
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
    assert ids(table.fetch_sync(request(*ALL, **criterion(column, condition, *values)), session)) == expected


@pytest.mark.parametrize(
    ('condition', 'values', 'expected'),
    [('=', ['2026-07-15'], [1]), ('>', ['2026-07-15'], [2]), ('<', ['2026-07-15'], [4, 5])],
)
def test_a_timezone_aware_column_compares_by_utc_day(
    session: Session, condition: str, values: list[str], expected: list[int]
):
    """The day bounds are UTC midnights. The PostgreSQL job catches naive bounds through its session zone (PGTZ,
    UTC+14), which reads a naive bound as local to it, and bounds built from Python's local time through TZ
    (America/Adak, UTC-10/-9). A developer machine whose local zone is not UTC catches local-time bounds on SQLite
    too. SQL Server and MySQL cannot tell a naive bound from a UTC one, and their CI jobs run Python in UTC, so there
    the test only shows the column works."""
    if session.get_bind().dialect.name == 'postgresql':
        zone = session.execute(select(func.current_setting('TimeZone'))).scalar()
        assert zone not in ('UTC', 'Etc/UTC'), 'Set PGTZ to a zone far from UTC, or this test checks nothing.'
        assert datetime.now().astimezone().utcoffset(), (
            'Set TZ to a zone far from UTC, or local-time bounds go unchecked.'
        )
    aware = SQLTable(people, [Column('id'), Column('seen_tz')])
    assert ids(aware.fetch_sync(request('id', 'seen_tz', **criterion('seen_tz', condition, *values)), session)) == (
        expected
    )


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
        ('=', ['yes']),  # each of the four is a value some reading of "boolean" would take
        ('=', ['2']),
        ('=', ['t']),
        ('=', ['']),
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
        ('age', {'multi_values': [' ', '31']}, [1, 3, 4]),  # white space holds nothing either (PRD-F33)
        ('active', {'multi_values': [' ', '1']}, [1, 3, 4]),  # SPEC-F44.3, SPEC-F44.4
    ],
)
def test_an_empty_alternative_or_entry_matches_the_rows_without_a_value(
    session: Session, column: str, search: dict[str, Any], expected: list[int]
):
    """SPEC-F17.4, SPEC-F20.2, SPEC-F33.5, SPEC-F44.4: on a column of any type. The date column's row is in
    test_a_list_on_a_date_column_matches_each_entry_as_a_day."""
    params = request(*EVERY_TYPE, column_search={column: search})
    assert ids(every_type.fetch_sync(params, session)) == expected


def test_white_space_is_a_value_on_a_string_column(session: Session):
    """SPEC-F20.4: an exact value is never rewritten, so an entry of one space is that value, not an empty
    entry, which would find row 2's NULL too. No row holds a space. SQL Server compares strings without their
    trailing spaces (MEASURED on the `sqlserver` leg, run 45044380547), so there the space equals the `''` of
    row 3."""
    params = request(*EVERY_TYPE, column_search={'email': {'multi_values': [' ']}})
    found = [3] if session.get_bind().dialect.name == 'mssql' else []
    assert ids(every_type.fetch_sync(params, session)) == found


@pytest.mark.parametrize('pattern', ['^red|blue$', r'r\ed'])
def test_a_regex_that_is_no_categorical_filter_is_refused(session: Session, pattern: str):
    """SPEC-F18.1: anchors without the group anchor each value at one end only, and a backslash escapes only the
    characters of PRD-F19: `\\e` is no `e`. Read as values, both would find rows."""
    params = request(*EVERY_TYPE, column_search={'team': {'value': pattern, 'regex': True}})
    refused(every_type.fetch_sync(params, session))


@pytest.mark.parametrize('pattern', [r'^C:\\$', r'^(C:\\)$', r'C:\\'])
def test_a_value_that_ends_in_an_escaped_backslash_is_read_in_every_form(session: Session, pattern: str):
    r"""SPEC-F19.2: row 6 holds `C:\`. In the form `^v$` the `$` after `C:\\` is the anchor, not an escaped `$`;
    taken for one, the filter was refused as a regex."""
    session.execute(people.insert(), [{'id': 6, 'name': 'Eve', 'team': 'C:\\', 'secret': 's6'}])
    params = request(*EVERY_TYPE, column_search={'team': {'value': pattern, 'regex': True}})
    assert ids(every_type.fetch_sync(params, session)) == [6]


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
    elif dialect == 'mysql':
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


@pytest.mark.xfail(
    reason='PRD-D14: a search-value hook on a column that is not a string is not refused yet, for any type '
    '(SPEC-D14.1)',
    strict=True,
    raises=pytest.fail.Exception,
)
def test_a_search_value_hook_on_a_boolean_column_is_a_schema_error():
    with pytest.raises(SchemaError):
        SQLTable(people, [Column('id'), Column('active', search_value=str.upper)])


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
    """Each is the days outside a span. Row 3 has no date, and passes them all (PRD-F37)."""
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


def test_the_declared_type_decides_not_the_clients(session: Session):
    """A client that calls a number column a string still gets a numeric comparison, and cannot run 'contains'."""
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
        ('age', '=', ['9' * 5000], 'invalid_number'),  # more digits than Python converts to an integer
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
        # SPEC-E4.3 to E4.5: the owner's case, a tab nobody sees, and where it is
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
        # SPEC-E4.6 to E4.8, and SPEC-E4.25 for a list of values, which must not be told to hold one number
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
        # SPEC-E4.11, E4.12: a name the server did not declare is never given
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
        # SPEC-E4.13 to E4.15
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
        # SPEC-E4.16, E4.17
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
        # SPEC-E4.18, E4.19, SPEC-F40.1, SPEC-F39.1: only a client that builds its own request makes these. The
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
        # SPEC-E4.21, E4.22
        (WORDED, criterion('name', '='), 'invalid_filter', 'The filter on "name" needs a value.'),
        (WORDED, criterion('age', 'between', '1'), 'invalid_filter', 'The filter on "age" needs two values.'),
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
    ],
)
def test_a_user_error_says_what_is_wrong_and_what_to_do(
    session: Session, names: tuple[str, ...], body: dict[str, Any], *, code: str, text: str
):
    """SPEC-E4.3 to SPEC-E4.22 and SPEC-E4.25 to SPEC-E4.28: each mistake has a text of its own, which names the
    condition and the fix, and a column only by the name the server declared. A text is asserted here and nowhere
    else. The rows are also the proof of their codes: SPEC-E1.2, SPEC-F21.6, SPEC-F24.1, SPEC-F25.1, SPEC-F25.2,
    SPEC-F39.1, SPEC-F40.1."""
    assert refused(worded.fetch_sync(request(*names, **body), session), code) == text


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


WIDE = ('id', 'ratio', 'exact', 'digits', 'count', 'untyped')
TWO_64 = 2**64


def wide(session: Session) -> SQLTable:
    """A table of number columns that are no plain integers. Row 1 holds 2^64 in `ratio` (a float), in `exact` (a
    `Numeric(32, 2)`) and, as text, in `digits` (a string column declared a number). Its `count` holds the largest
    value the column takes: the column is declared MySQL's unsigned BIGINT, which only MySQL creates unsigned, so
    2^64 - 1 there and 2^63 - 1 elsewhere. `untyped` is `ratio` as an expression without a SQL type, declared a
    number. Row 2 holds NULL."""
    dialect = session.get_bind().dialect.name
    numbers = Table(
        'dtb_test_tags',
        MetaData(),
        SQLColumn('id', Integer, primary_key=True, autoincrement=False),
        SQLColumn('ratio', Float),
        SQLColumn('exact', Numeric(32, 2)),
        SQLColumn('digits', String(40)),
        SQLColumn('count', mysql.BIGINT(unsigned=True)),
    )
    numbers.create(session.connection())
    largest = TWO_64 - 1 if dialect == 'mysql' else 2**63 - 1
    session.execute(
        numbers.insert(),
        [
            {'id': 1, 'ratio': 2.0**64, 'exact': Decimal(TWO_64), 'digits': str(TWO_64), 'count': largest},
            {'id': 2, 'ratio': None, 'exact': None, 'digits': None, 'count': None},
        ],
    )
    source = select(*numbers.c, literal_column('ratio').label('untyped')).subquery()
    return SQLTable(
        source,
        [
            Column('id'),
            Column('ratio'),
            Column('exact'),
            Column('digits', type='number'),
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
        criterion('digits', '=', str(TWO_64)),  # text is compared with the digits, not with a float's text
    ],
)
def test_a_whole_number_outside_64_bits_is_answered_on_a_column_that_is_no_integer(
    session: Session, body: dict[str, Any]
):
    """SPEC-F25.4, SPEC-F25.5: a driver sends a whole number as a 64-bit integer, so this one failed the request on
    SQLite, PostgreSQL and SQL Server. It goes as the column compares: the nearest float, a decimal to a `Numeric`
    column, its digits to a column that stores text."""
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


@pytest.mark.parametrize(
    ('value', 'text'),
    [
        ('9' * 39, 'The filter on "exact" needs a number with at most 38 digits before the decimal point.'),
        ('1e39', 'The filter on "exact" needs a number with at most 38 digits before the decimal point.'),
    ],
)
def test_a_numeric_column_takes_38_digits_on_sql_server(session: Session, value: str, text: str):
    """SPEC-F25.5 and the text of SPEC-E4.29, asserted here only: SQL Server's decimal holds 38 digits before the
    point, and its driver refuses to send a longer number (MEASURED: HY104), so there such a number is the user's
    mistake, written as a whole number or with an exponent. The other databases take it, and no row holds it."""
    response = wide(session).fetch_sync(request(*WIDE, **criterion('exact', '=', value)), session)
    if session.get_bind().dialect.name == 'mssql':
        assert refused(response, 'invalid_number') == text
    else:
        assert ids(response) == []


def test_a_whole_number_too_large_for_a_float_is_refused_with_the_digits_it_may_have(session: Session):
    """SPEC-F25.4 and the text of SPEC-E4.30, asserted here only: a float holds every whole number of up to 308
    digits, so one of 308 is answered and one of 309 is the user's mistake."""
    numbers = wide(session)
    assert ids(numbers.fetch_sync(request(*WIDE, **criterion('ratio', '=', '9' * 308)), session)) == []
    response = numbers.fetch_sync(request(*WIDE, **criterion('ratio', '=', '9' * 309)), session)
    assert refused(response, 'invalid_number') == 'The filter on "ratio" needs a number of at most 308 digits.'


def test_an_unsigned_integer_column_is_bounded_from_zero_up(session: Session):
    """SPEC-F25.6: MySQL's unsigned BIGINT takes 0 to 2^64 - 1, which the type says of itself. A negative number is
    outside it on every database; the largest value is found on MySQL, the only database that stores it."""
    numbers = wide(session)
    refused(numbers.fetch_sync(request(*WIDE, **criterion('count', '=', '-1')), session), 'invalid_number')
    if session.get_bind().dialect.name == 'mysql':
        assert ids(numbers.fetch_sync(request(*WIDE, **criterion('count', '=', str(TWO_64 - 1))), session)) == [1]


@pytest.mark.skipif(
    os.environ.get('DTB_SQL_DIALECT') != 'mssql',
    reason='only SQL Server sends a long list as JSON (PRD-C17); on the other databases this repeats the list above',
)
def test_a_list_sent_as_json_holds_a_whole_number_outside_64_bits_as_its_digits(session: Session):
    """SPEC-F25.5 on the JSON path: a decimal is no JSON number, so it travels as its digits, which SQL Server casts
    to the column's type as it casts every value."""
    values = [str(number) for number in range(3000)] + [str(TWO_64)]
    params = request(*WIDE, column_search={'exact': {'multi_values': values}})
    assert ids(wide(session).fetch_sync(params, session)) == [1]


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


@pytest.mark.xfail(
    reason='PRD-F14, PRD-F38: search text of more than 16 words and a SearchBuilder tree more than 8 groups deep are '
    'not refused yet, so the date or number mistake beside them is the one answered (SPEC-E3.2, issue #27)',
    strict=True,
    raises=AssertionError,
)
@pytest.mark.parametrize('bound', ['words', 'depth'])
def test_text_or_a_tree_over_the_bounds_comes_before_a_date_or_number_mistake(session: Session, bound: str):
    """SPEC-E3.2: step 1 before step 2."""
    if bound == 'words':
        params = request(*ALL, search={'value': ' '.join(['w'] * 17)}, **criterion('born', '=', '2024-13-01'))
    else:
        tree: dict[str, Any] = {'criteria': [{'condition': '=', 'origData': 'age', 'value': ['abc']}]}
        for _ in range(9):
            tree = {'criteria': [tree]}
        params = request(*ALL, searchBuilder=tree)
    refused(table.fetch_sync(params, session))


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


def test_many_multi_select_values_still_run(session: Session):
    """Thousands of values bind normally on SQLite; SQL Server would get one JSON parameter instead."""
    params = request(*ALL)
    params.columns[1].search.multi_values = [f'n{index}' for index in range(3000)] + ['Bob']
    assert ids(table.fetch_sync(params, session)) == [2]


def test_a_boolean_filter_beside_many_multi_select_values_still_runs(session: Session):
    """Past SQL Server's parameter limit every list of the statement travels as JSON, the boolean column's too, and
    both of its values are cast to the column's type there (`BIT`). Row 5 is false but not among the names, and row
    3 is among them but NULL."""
    params = request(*FLAGGED)
    params.columns[1].search.multi_values = [f'n{index}' for index in range(3000)] + ['Ann', 'Bob', 'anna_100%', 'Cy']
    params.columns[2].search.value = '^(true|0)$'
    params.columns[2].search.regex = True
    assert ids(flagged.fetch_sync(params, session)) == [1, 2, 4]


def test_many_multi_select_values_on_a_number_column_still_run(session: Session):
    """Past SQL Server's 2,100-parameter limit the values travel as JSON and are cast to the column's number type."""
    params = request(*ALL)
    params.columns[3].search.multi_values = [str(age) for age in range(1000, 3100)] + ['31']
    assert ids(table.fetch_sync(params, session)) == [1, 4]


def test_many_days_on_a_date_column_still_run(session: Session):
    """SPEC-F21.11, PRD-C17 for a list of days. A `Date` column compares them as one list: as a range for each day,
    3,000 of them nest deeper than SQLite's expression tree allows (1,000) and take 6,000 parameters on SQL Server
    (2,100). There the days travel as JSON and are cast to the column's `DATE`."""
    days = [(date(2010, 1, 1) + timedelta(days=offset)).isoformat() for offset in range(3000)]  # nobody's
    params = request(*ALL, column_search={'born': {'multi_values': [*days, '1980-07-15']}})
    assert ids(table.fetch_sync(params, session)) == [2]


def test_a_value_the_number_column_cannot_hold_matches_nothing(session: Session):
    """31.5 on a SMALLINT column: SQL Server's JSON path casts with TRY_CAST, so it matches nothing, as a bound
    parameter does on every other database (a plain conversion would fail the whole query)."""
    params = request(*ALL)
    params.columns[3].search.multi_values = [str(age) for age in range(1000, 3100)] + ['31', '31.5']
    assert ids(table.fetch_sync(params, session)) == [1, 4]


def test_a_value_longer_than_the_string_column_is_not_cut_to_match(session: Session):
    """`name` is VARCHAR(50). Cut to 50 characters this value is 'Bob' and spaces, which SQL Server compares equal to
    'Bob'; SQL Server's JSON path must cast to an unbounded text type."""
    params = request(*ALL)
    params.columns[1].search.multi_values = [f'n{index}' for index in range(3000)] + ['Bob' + ' ' * 47 + 'x']
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


def test_a_form_encoded_multi_values_list_is_read(session: Session):
    """The only request that sends ``multi_values`` as a form does (``...[multi_values][]``): were the key dropped or
    read as no list, this global search would be idle and answered."""
    params = parse_request([('columns[0][data]', 'name'), ('search[multi_values][]', 'Bob')])
    refused(table.fetch_sync(params, session))
