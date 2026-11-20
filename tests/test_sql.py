"""The SQL backend through the public API only: a request in, a response out.

It runs on SQLite, or on the database DTB_SQL_URL names (the container jobs: PostgreSQL, MySQL, SQL Server).
"""

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import os
from typing import Any

import pytest
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.exc import DataError
from sqlalchemy.exc import ProgrammingError
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


def refused(response: DTResponse) -> str:
    assert response.error_code == 'invalid_filter'
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


@pytest.mark.parametrize(
    'body',
    [
        {'order': [{'column': 9, 'dir': 'asc'}]},  # no such request column
        {'order': [{'column': 6, 'dir': 'asc'}]},  # declared not orderable, whatever the request claims
    ],
)
def test_an_order_the_server_does_not_allow_is_refused(session: Session, body: dict[str, Any]):
    refused(table.fetch_sync(request(*ALL, **body), session))


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
    """The day bounds are UTC midnights. Only PostgreSQL in a non-UTC session (CI sets PGTZ to UTC+14) checks that:
    there a naive bound would be read in the session zone. Elsewhere (SQLite, MySQL, SQL Server) the test only shows
    the column works."""
    if session.get_bind().dialect.name == 'postgresql':
        zone = session.execute(select(func.current_setting('TimeZone'))).scalar()
        assert zone not in ('UTC', 'Etc/UTC'), 'Set PGTZ to a zone far from UTC, or this test checks nothing.'
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


#: What SearchBuilder `=` on a Boolean column (True for ids 1 and 4, False for 2 and 5) gives today, per backend: the
#: ids answered, or the exception raised. Draft 1 has no boolean type, so the column is a string column and the value
#: goes to the database as text. PRD v2 (PRD-F43) makes true/false (any case) or 1/0 match and refuses other values
#: as invalid_filter.
BOOLEAN_TODAY: dict[str, dict[str, list[int] | type[Exception]]] = {
    'sqlite': {'1': [1, 4], 'true': [], 'abc': []},
    'mysql': {'1': [1, 4], 'true': [2, 5], 'abc': [2, 5]},  # text that is not a number reads as 0
    'mssql': {'1': [1, 4], 'true': [1, 4], 'abc': DataError},
    'postgresql': {'1': ProgrammingError, 'true': ProgrammingError, 'abc': ProgrammingError},
}


@pytest.mark.parametrize(
    ('value', 'today'),
    [
        pytest.param(
            value,
            today,
            marks=pytest.mark.xfail(
                reason='draft 1: the database refuses text compared with a Boolean column, so the query fails; '
                'PRD v2 (PRD-F43) makes true/false (any case) or 1/0 match and refuses other values as invalid_filter',
                strict=True,
                raises=today,
            ),
        )
        if isinstance(today, type)
        else pytest.param(value, today)
        for value, today in BOOLEAN_TODAY[os.environ.get('DTB_SQL_DIALECT', 'sqlite')].items()
    ],
)
def test_searchbuilder_equality_on_a_boolean_column_as_it_is_today(
    session: Session, value: str, today: list[int] | type[Exception]
):
    flagged = SQLTable(people, [Column('id'), Column('active')])
    assert ids(flagged.fetch_sync(request('id', 'active', **criterion('active', '=', value)), session)) == today


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
    ('column', 'condition', 'values', 'reason'),
    [
        ('age', '>', ['old'], 'must be a number'),
        ('age', '>', ['inf'], 'must be a number'),
        ('age', '>', ['40000'], 'outside the range'),  # a SMALLINT
        ('born', '=', ['15/07/1980'], 'YYYY-MM-DD'),
        ('born', '<=', ['9999-12-31'], 'outside the range'),
        ('born', 'contains', ['1980-13'], 'YYYY, YYYY-MM or YYYY-MM-DD'),
        ('age', 'between', ['1'], 'needs two values'),
        ('name', '=', [], 'needs a value'),
        ('name', '<', ['m'], 'not supported'),
    ],
)
def test_a_value_the_column_cannot_take_is_refused_without_echoing_it(
    session: Session, column: str, condition: str, values: list[str], reason: str
):
    message = refused(table.fetch_sync(request(*ALL, **criterion(column, condition, *values)), session))
    assert reason in message
    assert all(value not in message for value in values if len(value) > 2)


def test_many_multi_select_values_still_run(session: Session):
    """Thousands of values bind normally on SQLite; SQL Server would get one JSON parameter instead."""
    params = request(*ALL)
    params.columns[1].search.multi_values = [f'n{index}' for index in range(3000)] + ['Bob']
    assert ids(table.fetch_sync(params, session)) == [2]


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
    from sqlalchemy import Column as SQLColumn
    from sqlalchemy import MetaData
    from sqlalchemy import String
    from sqlalchemy import Table

    tags = Table(
        'dtb_test_tags', MetaData(), SQLColumn('code', String(5), primary_key=True), SQLColumn('kind', String(5))
    )
    tags.create(session.connection())
    session.execute(tags.insert(), [{'code': c, 'kind': 'k'} for c in ('c', 'a', 'b')])  # stored in this order
    tagged = SQLTable(tags, [Column('code'), Column('kind')])
    response = tagged.fetch_sync(request('code', 'kind', order=[{'column': 1, 'dir': 'asc'}]), session)
    assert [row['code'] for row in response.data] == ['a', 'b', 'c']


def test_a_column_search_with_both_a_value_and_multi_values_is_refused(session: Session):
    params = parse_request(
        [
            ('columns[0][data]', 'name'),
            ('columns[0][search][value]', 'Ann'),
            ('columns[0][search][multi_values][]', 'Bob'),
        ]
    )
    assert refused(table.fetch_sync(params, session))


def test_a_global_search_with_multi_values_is_refused(session: Session):
    """``multi_values`` belongs to a column search; on the global search it would be dropped without a word."""
    params = parse_request([('columns[0][data]', 'name'), ('search[multi_values][]', 'Bob')])
    assert refused(table.fetch_sync(params, session))
