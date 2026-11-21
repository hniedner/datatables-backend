"""The SQL backend through the public API only: a request in, a response out.

It runs on SQLite, or on the database DTB_SQL_URL names (the container jobs: PostgreSQL, MySQL, SQL Server).
"""

from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import logging
import os
from typing import Any
import warnings

import pytest
from sqlalchemy import Column as SQLColumn
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import cast
from sqlalchemy import event
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

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


# -- ordering ---


def test_order_follows_the_requests_columns_then_the_primary_key(session: Session):
    params = request(*ALL, order=[{'column': 3, 'dir': 'asc'}])  # age; Ann and Cy tie at 31; row 3's age is NULL
    assert [row for row in ids(table.fetch_sync(params, session)) if row != 3] == [1, 4, 2, 5]


@pytest.mark.xfail(
    os.environ.get('DTB_SQL_DIALECT') == 'postgresql',
    reason="draft 1: NULL placement is the database's default (PostgreSQL: last ascending); PRD v2 (PRD-O2) makes it "
    'lowest everywhere',
    strict=True,
    raises=AssertionError,
)
def test_null_comes_first_in_an_ascending_order(session: Session):
    params = request(*ALL, order=[{'column': 3, 'dir': 'asc'}])
    assert ids(table.fetch_sync(params, session))[0] == 3


def test_an_order_the_server_does_not_allow_is_refused(session: Session):
    """`team` is declared not orderable, whatever the request claims."""
    refused(table.fetch_sync(request(*ALL, order=[{'column': 6, 'dir': 'asc'}]), session))


def test_an_order_the_request_marks_unorderable_is_refused(session: Session):
    params = request(*ALL, order=[{'column': 0, 'dir': 'asc'}])
    params.columns[0].orderable = False
    refused(table.fetch_sync(params, session))


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


def test_a_regex_search_is_refused(session: Session):
    assert 'Regular-expression' in refused(
        table.fetch_sync(request(*ALL, search={'value': 'a.*', 'regex': True}), session)
    )
    params = request(*ALL)
    params.columns[1].search.value = 'A.+'
    params.columns[1].search.regex = True
    refused(table.fetch_sync(params, session))


def test_an_undeclared_column_cannot_be_searched_or_sorted(session: Session):
    params = request(*ALL, 'secret')
    params.columns[7].search.value = 's1'
    reason = refused(table.fetch_sync(params, session))
    assert 'secret' not in reason
    refused(table.fetch_sync(request(*ALL, 'secret', order=[{'column': 7, 'dir': 'asc'}]), session))
    refused(table.fetch_sync(request(*ALL, **criterion('secret', '=', 's1')), session))


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
        ('age', '!between', ['40', '60'], [1, 4]),
        ('age', 'null', [], [3]),
        ('born', '=', ['1980-07-15'], [2]),
        ('born', '<', ['1980-07-15'], [5]),
        ('born', '<=', ['1980-07-15'], [2, 5]),
        ('born', 'between', ['1980-01-01', '1994-03-01'], [1, 2]),
        ('born', 'contains', ['1980'], [2]),
        ('born', 'contains', ['2001-12'], [4]),
        ('born', 'contains', ['1980-07-15'], [2]),  # a full day is a span of one day
        ('seen', '=', ['2026-07-15'], [1]),  # a datetime column compares by day
        ('seen', '>', ['2026-07-15'], [2]),
        ('seen', '>=', ['2026-07-15'], [1, 2]),
        ('seen', 'between', ['2026-07-14', '2026-07-15'], [1, 5]),
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


@pytest.mark.xfail(
    os.environ.get('DTB_SQL_DIALECT') == 'postgresql',
    reason="draft 1: PostgreSQL's day text of a timestamptz is its day in the session zone; PRD v2 (PRD-F42) makes "
    'it the UTC day',
    strict=True,
    raises=AssertionError,
)
def test_the_global_search_finds_a_timezone_aware_column_by_its_utc_day(session: Session):
    aware = SQLTable(people, [Column('id'), Column('seen_tz')])
    assert ids(aware.fetch_sync(request('id', 'seen_tz', search={'value': '2026-07-15'}), session)) == [1]


@pytest.mark.xfail(
    os.environ.get('DTB_SQL_DIALECT') == 'mssql',
    reason='draft 1: DATETIMEOFFSET day text in its stored offset; PRD v2 (PRD-F42) makes it the UTC day',
    strict=True,
    raises=AssertionError,
)
def test_the_global_search_finds_a_value_stored_at_another_offset_by_its_utc_day(session: Session):
    """2026-08-01 23:00 at -05:00 is 2026-08-02 04:00 UTC. The row is added in this test's transaction only. SQLite
    and MySQL store no offset (PRD v2, PRD-F21), so there it is written as its UTC time, as such a column holds it.
    On PostgreSQL this test passes because the session's +14 puts 04:00 UTC on the same day; PostgreSQL's own check
    is the strict xfail of `test_the_global_search_finds_a_timezone_aware_column_by_its_utc_day`."""
    stored = datetime(2026, 8, 1, 23, 0, tzinfo=timezone(timedelta(hours=-5)))
    if session.get_bind().dialect.name not in ('postgresql', 'mssql'):
        stored = stored.astimezone(UTC)
    session.execute(people.insert(), {'id': 6, 'name': 'Eve', 'team': 'red', 'secret': 's6', 'seen_tz': stored})
    aware = SQLTable(people, [Column('id'), Column('seen_tz')])
    assert ids(aware.fetch_sync(request('id', 'seen_tz', search={'value': '2026-08-02'}), session)) == [6]


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
    """SPEC-E13.2, the skips that exist today: the boolean column the global search leaves out, an idle SearchPanes
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


@pytest.mark.xfail(
    reason='PRD-F20, PRD-F17: an empty multi_values entry does not mean empty yet, on any column type (SPEC-F44.3)',
    strict=True,
    raises=AssertionError,
)
def test_an_empty_multi_values_entry_on_a_boolean_column_means_null(session: Session):
    params = request(*FLAGGED)
    params.columns[2].search.multi_values = ['', '1']
    assert ids(flagged.fetch_sync(params, session)) == [1, 3, 4]


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
        ('!=', ['1980-07-15'], [1, 4, 5]),
        ('!between', ['1980-01-01', '1994-03-01'], [4, 5]),
        ('!contains', ['1980'], [1, 4, 5]),
    ],
)
def test_negated_date_conditions(session: Session, condition: str, values: list[str], expected: list[int]):
    """Over rows that all have a date: what a negation does with NULL is PRD v2's to settle (PRD-F37)."""
    dated = SQLTable(people, COLUMNS, base_filter=people.c.born.is_not(None))
    assert ids(dated.fetch_sync(request(*ALL, **criterion('born', condition, *values)), session)) == expected


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
    assert '"contains"' in refused(table.fetch_sync(request(*ALL, searchBuilder=tree), session))


@pytest.mark.parametrize(
    ('column', 'condition', 'values', 'code', 'reason'),
    [
        ('age', '>', ['old'], 'invalid_number', 'must be a number'),
        ('age', '>', ['inf'], 'invalid_number', 'must be a number'),
        ('age', '>', ['40000'], 'invalid_number', 'outside the range'),  # a SMALLINT
        ('age', 'between', ['old'], 'invalid_number', 'must be a number'),  # the value there is, not the one missing
        ('born', '=', ['15/07/1980'], 'invalid_date', 'YYYY-MM-DD'),
        ('born', '<=', ['9999-12-31'], 'invalid_date', 'outside the range'),
        ('born', '<', ['9999-12-31'], 'invalid_date', 'outside the range'),  # SPEC-F24.1: in every condition
        ('born', 'contains', ['9999'], 'invalid_date', 'outside the range'),  # a year that holds the last day
        ('born', 'contains', ['9999-12'], 'invalid_date', 'outside the range'),  # and so does this month
        ('born', 'contains', ['1980-13'], 'invalid_date', 'YYYY, YYYY-MM or YYYY-MM-DD'),
        ('age', 'between', ['1'], 'invalid_filter', 'needs two values'),
        ('name', '=', [], 'invalid_filter', 'needs a value'),
        ('name', '<', ['m'], 'invalid_filter', 'not supported'),
        ('age', 'contains', ['abc'], 'invalid_filter', 'not supported'),  # SPEC-E3.8: the value is never read
    ],
)
def test_a_value_the_column_cannot_take_is_refused_with_its_code_without_echoing_it(
    session: Session, column: str, condition: str, values: list[str], *, code: str, reason: str
):
    """SPEC-F24.1, SPEC-F25.1, SPEC-F25.2, SPEC-E1.2, SPEC-E3.8: a date mistake, a number mistake and any other each
    have their code (PRD-E3, steps 2 to 4)."""
    message = refused(table.fetch_sync(request(*ALL, **criterion(column, condition, *values)), session), code)
    assert reason in message
    assert all(value not in message for value in values if len(value) > 2)


def test_the_last_month_before_the_last_day_is_answered(session: Session):
    """The other side of SPEC-F24.1's spans: only one that holds 9999-12-31 is refused."""
    assert ids(table.fetch_sync(request(*ALL, **criterion('born', 'contains', '9999-11')), session)) == []


def test_a_user_error_is_an_answer_with_the_draw_and_nothing_else(session: Session):
    """SPEC-E1.1, SPEC-R5.2, SPEC-R6.1: what a caller sends on for a mistake the user can fix."""
    response = table.fetch_sync(request(*ALL, draw=7, **criterion('age', '=', 'abc')), session)
    assert response.to_dict() == {
        'draw': 7,
        'recordsTotal': 0,
        'recordsFiltered': 0,
        'data': [],
        'error': 'The filter value must be a number.',
        'error_code': 'invalid_number',
    }


def test_a_range_mistake_found_while_the_query_is_built_comes_before_a_condition_mistake(session: Session):
    """SPEC-E3.4: step 3 before step 4, though the condition is refused by the plan and the number's range only
    later, against the SQL column (`age` is a SMALLINT)."""
    tree = {
        'criteria': [
            {'condition': '<', 'origData': 'name', 'value': ['a']},
            {'condition': '=', 'origData': 'age', 'value': ['40000']},
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
        params = request(*ALL, **criterion('name', '=', 'a\x00b'))
        params.columns[3].search.multi_values = ['abc']
        expected = 'invalid_filter'
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
    ('place', 'value'),
    [
        ('global', 'a\x00b'),
        ('global', 'a\tb'),  # a control character that is also white space
        ('global', 'a\x7fb'),  # and one above the first 32
        ('column', 'a\x00b'),
        ('categorical', 'a\x00b'),
        ('multi_values', 'a\x00b'),
        ('criterion', 'a\x00b'),
    ],
)
def test_a_control_character_is_refused_wherever_a_value_is_read(session: Session, place: str, value: str):
    """SPEC-F15.1, SPEC-F15.2: a NUL once cut a LIKE pattern short on SQLite and matched the wrong rows."""
    params = request(*ALL)
    if place == 'global':
        params = request(*ALL, search={'value': value})
    elif place == 'column':
        params.columns[1].search.value = value
    elif place == 'categorical':
        params.columns[1].search = params.columns[1].search.model_copy(
            update={'value': f'^(x|{value})$', 'regex': True}
        )
    elif place == 'multi_values':
        params.columns[1].search.multi_values = ['x', value]
    else:
        params = request(*ALL, **criterion('name', '=', value))
    assert 'control character' in refused(table.fetch_sync(params, session))


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
        assert 'control character' in refused(table.fetch_sync(request(*ALL, searchBuilder=tree), session))
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


def user_error_cases() -> list[Any]:
    """Three refusals of different kinds, each with what the client sent that must not be logged."""
    undeclared = request(*ALL, 'MARKER_NAME')
    undeclared.columns[7].search.value = 'MARKER_VALUE'
    return [
        pytest.param(undeclared, 'datatables_name_not_declared', id='undeclared-column'),
        pytest.param(request(*ALL, order=[{'column': 6, 'dir': 'asc'}]), 'datatables_order_refused', id='order'),
        pytest.param(
            request(*ALL, **criterion('age', '=', 'MARKER_VALUE')), 'datatables_number_unreadable', id='number'
        ),
    ]


@pytest.mark.parametrize(('params', 'event'), user_error_cases())
def test_a_user_error_is_logged_exactly_once_without_what_the_client_sent(
    session: Session, caplog: pytest.LogCaptureFixture, params: Any, event: str
):
    """SPEC-E13.1, SPEC-E10.1, SPEC-E11.1: one WARNING per refusal, under an event of its own that the same case
    logs again, and neither the record nor the reason holds the value or the undeclared name."""
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


def test_a_column_search_with_both_a_value_and_multi_values_is_refused(session: Session):
    """SPEC-F39.1, and SPEC-E3.8 on a number column: the form is refused, and the value in the list, which is no
    number, is never read."""
    params = parse_request(
        [
            ('columns[0][data]', 'age'),
            ('columns[0][search][value]', 'abc'),
            ('columns[0][search][multi_values][]', 'x'),
        ]
    )
    assert 'not both' in refused(table.fetch_sync(params, session))


def test_a_global_search_with_multi_values_is_refused(session: Session):
    """``multi_values`` belongs to a column search; on the global search it would be dropped without a word."""
    params = parse_request([('columns[0][data]', 'name'), ('search[multi_values][]', 'Bob')])
    assert refused(table.fetch_sync(params, session))
