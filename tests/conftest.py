import asyncio
from collections.abc import AsyncIterator
from collections.abc import Callable
from collections.abc import Iterator
from datetime import UTC
from datetime import date
from datetime import datetime
import os
from pathlib import Path
from types import CoroutineType
from typing import Any

from hypothesis import HealthCheck
from hypothesis import settings
from hypothesis import strategies as st
from pydantic import BaseModel
import pytest
from sqlalchemy import Boolean
from sqlalchemy import Column as SQLColumn
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import SmallInteger
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from datatables_backend import DTParams
from datatables_backend import RowHooks
from datatables_backend import parse_request

# The property tests (tests/test_parse_properties.py and tests/test_reference_properties.py). `ci` is the default and
# runs the same examples on every run, so a red run is the code's and never luck's; `explore` is for a developer who
# wants more, and different, examples: HYPOTHESIS_PROFILE=explore uv run pytest tests/test_reference_properties.py
_SLOW = [HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much]
settings.register_profile(
    'ci', max_examples=500, derandomize=True, database=None, deadline=None, suppress_health_check=_SLOW
)
settings.register_profile('explore', max_examples=5000, deadline=None, suppress_health_check=_SLOW)
settings.load_profile(os.environ.get('HYPOTHESIS_PROFILE', 'ci'))


def server_examples[T](count: int, *, on_server: bool = True) -> Callable[[T], T]:
    """A property test's number of examples on a leg that runs against a server: ``count`` under the `ci` profile, so
    the leg's run time stays bounded; under any other profile (`explore`), or off a server, the profile's own."""
    if not on_server or settings.get_current_profile_name() != 'ci':
        return lambda test: test
    return settings(max_examples=count)


def chance(one_in: int) -> st.SearchStrategy[bool]:
    """True about once in ``one_in`` draws. True stands in the middle: Hypothesis draws the first choice, its simplest,
    far more often than the others (measured: the first of 16 integers in a quarter of the draws)."""
    return st.sampled_from([False] * (one_in // 2) + [True] + [False] * (one_in - 1 - one_in // 2))


def rarely[T](common: st.SearchStrategy[T], rare: st.SearchStrategy[T], one_in: int) -> st.SearchStrategy[T]:
    """``common``, and ``rare`` about once in ``one_in`` draws. Not ``one_of`` with ``common`` repeated: Hypothesis
    flattens a ``one_of`` in a ``one_of``, so when ``common`` is one the rare branch's share grows (measured: one in 24
    came one in four)."""
    return chance(one_in).flatmap(lambda now: rare if now else common)


def checked_database(url: str) -> str:
    """The database a URL names, if the tests may drop and create tables in it: `dtb` (CI's), `dtb_test` or
    `dtb_test_...`."""
    name = make_url(url).database or ''
    if not (name in ('dtb', 'dtb_test') or name.startswith('dtb_test_')):
        raise RuntimeError(
            'The SQL tests drop and create tables: DTB_SQL_URL and DTB_SQL_ASYNC_URL must name a database called dtb, '
            'dtb_test or dtb_test_...'
        )
    return name


#: A real database instead of SQLite: a sync URL and an async URL for the same database, both or neither (the
#: container jobs in CI set them). Both must name a database the tests own (`checked_database`), checked here,
#: before anything connects; before each test, the tables the tests create (TEST_TABLES) are dropped there.
SQL_URLS = (os.environ.get('DTB_SQL_URL', ''), os.environ.get('DTB_SQL_ASYNC_URL', ''))
if any(SQL_URLS) and not all(SQL_URLS):
    raise RuntimeError('Set both DTB_SQL_URL and DTB_SQL_ASYNC_URL, or neither.')
for _url in filter(None, SQL_URLS):
    checked_database(_url)


#: A live OpenSearch cluster, for the modules that run against one (the CI job starts one)
OPENSEARCH_URL = os.environ.get('DTB_OPENSEARCH_URL', '')


def opensearch_live() -> pytest.MarkDecorator:
    """The `pytestmark` of a module that runs against a live cluster: without DTB_OPENSEARCH_URL its tests are
    skipped, and the reason shows in the summary, unless DTB_REQUIRE_OPENSEARCH=1 (the CI job), which makes a missing
    URL an error while the module is collected: a run that skips every test is a failure there."""
    if os.environ.get('DTB_REQUIRE_OPENSEARCH') == '1' and not OPENSEARCH_URL:
        raise RuntimeError('DTB_REQUIRE_OPENSEARCH is set, but DTB_OPENSEARCH_URL is not')
    return pytest.mark.skipif(not OPENSEARCH_URL, reason='DTB_OPENSEARCH_URL is not set')


def async_engine(url: str) -> AsyncEngine:
    """The async engine of the tests. asyncpg does not read ``PGTZ`` (libpq's variable, which psycopg and psycopg2 read):
    the first run on CI showed its session in UTC beside the sync one in the zone ``PGTZ`` names. So where ``PGTZ`` is
    set, it is asked for that zone as asyncpg takes a setting, and the async leg runs in the same far-from-UTC zone."""
    connect_args: dict[str, Any] = {}
    if make_url(url).get_driver_name() == 'asyncpg' and (zone := os.environ.get('PGTZ')):
        connect_args['server_settings'] = {'timezone': zone}
    return create_async_engine(url, connect_args=connect_args)


async def async_time_zone() -> str:
    """The session time zone of a connection of the async driver: libpq reads PGTZ, but a driver that does not use
    libpq may not, so the async tests could run in UTC while the sync ones do not."""
    engine = async_engine(SQL_URLS[1])
    try:
        async with engine.connect() as connection:
            return str((await connection.exec_driver_sql('SHOW TimeZone')).scalar())
    finally:
        await engine.dispose()


def pytest_report_header() -> list[str]:
    """The real servers the tests run on, with their versions, so a run cannot pass on SQLite unnoticed."""
    lines: list[str] = []
    if SQL_URLS[0]:
        engine = create_engine(SQL_URLS[0])
        with engine.connect() as connection:
            version = '.'.join(map(str, connection.dialect.server_version_info or ()))
            if engine.dialect.name == 'postgresql':  # a non-UTC session is what makes the timezone test bite
                zone = connection.exec_driver_sql('SHOW TimeZone').scalar()
                version += f', session TimeZone {zone} (sync), {asyncio.run(async_time_zone())} (async)'
        engine.dispose()
        lines.append(f'SQL tests on {engine.dialect.name} {version}')
    if OPENSEARCH_URL:
        from opensearchpy import OpenSearch  # only for a live run

        client = OpenSearch(hosts=[OPENSEARCH_URL])
        lines.append(f'OpenSearch tests on {client.info()["version"]["number"]}')
        client.close()
    return lines


metadata = MetaData()
#: the last two are created by tests in test_sql.py, and `dtb_test_tags` also by the reference property
#: (tests/test_reference_properties.py)
TEST_TABLES = frozenset({'dtb_test_people', 'dtb_test_tags', 'dtb_test_stamps'})

people = Table(
    'dtb_test_people',
    metadata,
    SQLColumn('id', Integer, primary_key=True),
    SQLColumn('name', String(50)),
    SQLColumn('email_address', String(100), nullable=True),
    SQLColumn('age', SmallInteger, nullable=True),
    SQLColumn('born', Date, nullable=True),
    SQLColumn('seen', DateTime, nullable=True),
    SQLColumn(
        'seen_tz', DateTime(timezone=True), nullable=True
    ),  # TIMESTAMPTZ, DATETIMEOFFSET (DATETIME on MySQL and SQLite)
    SQLColumn('team', String(20)),
    SQLColumn('secret', String(50)),
    SQLColumn('active', Boolean, nullable=True),  # BOOLEAN (SQLite stores 1 and 0), BIT, TINYINT(1)
)

ROWS: list[dict[str, Any]] = [
    {
        'id': 1, 'name': 'Ann', 'email_address': 'ann@example.org', 'age': 31, 'born': date(1994, 3, 1),
        'seen': datetime(2026, 7, 15, 23, 59), 'seen_tz': datetime(2026, 7, 15, 23, 59, tzinfo=UTC),
        'team': 'red', 'secret': 's1', 'active': True,
    },
    {
        'id': 2, 'name': 'Bob', 'email_address': None, 'age': 45, 'born': date(1980, 7, 15),
        'seen': datetime(2026, 7, 16, 0, 0), 'seen_tz': datetime(2026, 7, 16, 0, 0, tzinfo=UTC),
        'team': 'blue', 'secret': 's2', 'active': False,
    },
    {
        'id': 3, 'name': 'anna_100%', 'email_address': '', 'age': None, 'born': None,
        'seen': None, 'seen_tz': None, 'team': 'red', 'secret': 's3', 'active': None,
    },
    {
        'id': 4, 'name': 'Cy', 'email_address': 'cy@example.org', 'age': 31, 'born': date(2001, 12, 31),
        'seen': datetime(2025, 1, 1, 12, 0), 'seen_tz': datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
        'team': 'green', 'secret': 's4', 'active': True,
    },
    {
        'id': 5, 'name': 'Dee', 'email_address': 'dee@example.org', 'age': 60, 'born': date(1966, 7, 1),
        'seen': datetime(2026, 7, 14, 20, 0), 'seen_tz': datetime(2026, 7, 14, 20, 0, tzinfo=UTC),
        'team': 'blue', 'secret': 's5', 'active': False,
    },
]  # fmt: skip


@pytest.fixture
def database(tmp_path: Path) -> tuple[str, str]:
    """The sync and async URL of a database where the tests' tables (TEST_TABLES) were dropped and
    ``dtb_test_people`` recreated with ROWS; no other table is touched."""
    path = tmp_path / 'people.db'
    urls = SQL_URLS if SQL_URLS[0] else (f'sqlite:///{path}', f'sqlite+aiosqlite:///{path}')
    engine = create_engine(urls[0])
    with engine.begin() as connection:
        existing = MetaData()
        existing.reflect(connection, only=lambda name, _: name in TEST_TABLES)  # what an earlier test left
        existing.drop_all(connection)
        metadata.create_all(connection)
        connection.execute(people.insert(), ROWS)
    engine.dispose()
    return urls


@pytest.fixture
def session(database: tuple[str, str]) -> Iterator[Session]:
    engine = create_engine(database[0])
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
async def async_session(database: tuple[str, str]) -> AsyncIterator[AsyncSession]:
    engine = async_engine(database[1])
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


#: What a log record holds about the moment and the place of the call: its own digits, which a test that looks for a
#: number of its own in a log line must not trip over
VOLATILE_RECORD_ATTRIBUTES = frozenset(
    {
        'created',
        'msecs',
        'relativeCreated',
        'thread',
        'threadName',
        'process',
        'processName',
        'taskName',
        'lineno',
    }
)


def logged_text(caplog: pytest.LogCaptureFixture) -> str:
    """Every log record of the test as text, without the attributes that hold the record's own time, ids and line
    number. A digit needle (``'4242'``) can match those by chance; the message, the arguments and any ``extra`` stay."""
    return repr(
        [
            {key: value for key, value in vars(record).items() if key not in VOLATILE_RECORD_ATTRIBUTES}
            for record in caplog.records
        ]
    )


def request(
    *names: str,
    column_search: dict[str, dict[str, Any]] | None = None,
    flags: dict[str, dict[str, Any]] | None = None,
    **body: Any,
) -> DTParams:
    """A request for the columns ``names``, as DataTables sends it, with ``body`` merged in. ``column_search`` gives
    a column's search by its name (``{'age': {'value': '31'}}``) and ``flags`` its other fields
    (``{'team': {'orderable': False}}``, or ``{'': {'data': None}}`` for a column that only renders), so the request
    is parsed with them."""
    searches, fields = column_search or {}, flags or {}
    columns = [
        {
            'data': name,
            'name': '',
            'searchable': True,
            'orderable': True,
            'search': {'value': '', 'regex': False, **searches.get(name, {})},
            **fields.get(name, {}),
        }
        for name in names
    ]
    return parse_request({'draw': 1, 'start': 0, 'length': 10, 'columns': columns, **body})


#: PRD-E2's list, closed: the only codes a user error is answered with
ERROR_CODES = frozenset(
    {
        'invalid_filter',
        'invalid_character',
        'value_too_long',
        'too_many_words',
        'request_too_large',
        'invalid_date',
        'invalid_number',
        'page_out_of_range',
    }
)


#: Sixteen words (PRD-F14's cap), all substrings of `ann@example.org`
LARGEST_WORDS = 'ann an nn a n ex xa am mp pl le or rg org ple example'


def criterion(column: str, condition: str, *values: str) -> dict[str, Any]:
    return {
        'searchBuilder': {
            'logic': 'AND',
            'criteria': [{'condition': condition, 'origData': column, 'value': list(values)}],
        }
    }


def columncontrol(column: str, logic: str, value: str = '', **extra: Any) -> dict[str, dict[str, Any]]:
    """The ``flags`` of `request` that give ``column`` ColumnControl's search, as the client sends it (decision 007)."""
    search = {'value': value, 'logic': logic, 'type': 'text', **extra}
    return {column: {'columnControl': {'search': search}}}


def columncontrol_list(column: str, values: list[str]) -> dict[str, dict[str, Any]]:
    """The ``flags`` that give ``column`` ColumnControl's list: an object keyed by index, as the client sends it."""
    return {column: {'columnControl': {'list': {str(index): value for index, value in enumerate(values)}}}}


def leaf(column: str) -> dict[str, Any]:
    return {'condition': '=', 'origData': column, 'value': ['Ann']}


def nested(depth: int, column: str) -> dict[str, Any]:
    """A SearchBuilder tree ``depth`` groups deep, the root counted as 1, AND and OR alternating. Each group holds a
    criterion beside the next group, so the plan cannot collapse it: this is the shape that raised `RecursionError`
    at 200 groups before PRD-F38's bounds."""
    tree: dict[str, Any] = {'logic': 'AND', 'criteria': [leaf(column)]}
    for level in range(depth - 1):
        tree = {'logic': 'OR' if level % 2 == 0 else 'AND', 'criteria': [leaf(column), tree]}
    return tree


def flat(count: int, column: str) -> dict[str, Any]:
    """A SearchBuilder tree of one group holding ``count`` criteria."""
    return {'logic': 'AND', 'criteria': [leaf(column) for _ in range(count)]}


def exact_values(count: int, first: str) -> list[str]:
    """``count`` values, ``first`` among them and the rest matching nothing."""
    return [first, *(f'v{number}' for number in range(count - 1))]


class Labelled(BaseModel, extra='allow'):
    """A row model that adds one field, `label`, to what the table reads."""

    label: str


def labelled(key: str) -> RowHooks:
    """Hooks that shape each row through `Labelled`, naming it by the row's `key` column (SPEC-R2.1)."""
    return RowHooks(shape=lambda row: Labelled(label=f'label {row[key]}', **row).model_dump())


BRACKETED = [
    ('draw', '4'),
    ('start', '0'),
    ('length', '10'),
    ('columns[0][data]', 'id'),
    ('columns[1][data]', 'name'),
    ('columns[1][search][value]', 'ann'),
    ('columns[1][search][regex]', 'false'),
    ('columns[2][data]', 'team'),
    ('columns[2][orderable]', 'false'),
    ('order[0][column]', '0'),
    ('order[0][dir]', 'desc'),
    ('order[0][name]', ''),
    ('search[value]', 'r'),
    ('search[regex]', 'false'),
]


def reshaped(key: str, form: str) -> str:
    """``columns[1][search][value]`` as ``columns[1].search.value`` (spring), ``columns.1.search.value`` (dots) or
    ``columns.1[search].value`` (mixed)."""
    head, *parts = key.replace(']', '').split('[')
    if form == 'spring':  # spring-data-jpa-datatables' flatten(): indexes stay bracketed, names are dotted
        return head + ''.join(f'[{part}]' if part.isdigit() else f'.{part}' for part in parts)
    if form == 'mixed':  # dots and brackets alternate, each following the other kind
        return head + ''.join(f'[{part}]' if position % 2 else f'.{part}' for position, part in enumerate(parts))
    return '.'.join([head, *parts])


# -- total stores that are no TotalCache (PRD-C18) ---


class Store:
    """A total store that is no `TotalCache`: a dict, with the keys it was given kept for the tests to read."""

    def __init__(self) -> None:
        self.totals: dict[str, int] = {}
        self.keys: list[str] = []

    def get(self, key: str) -> int | None:
        self.keys.append(key)
        return self.totals.get(key)

    def put(self, key: str, total: int) -> None:
        self.keys.append(key)
        self.totals[key] = total


class AsyncStore:
    """`Store` with coroutine methods, for `fetch`."""

    def __init__(self) -> None:
        self.inner = Store()

    async def get(self, key: str) -> int | None:
        return self.inner.get(key)

    async def put(self, key: str, total: int) -> None:
        self.inner.put(key, total)

    @property
    def keys(self) -> list[str]:
        return self.inner.keys


class EmptyStore(Store):
    """Holds totals but says it is empty, as a container whose `__len__` counts something else does."""

    def __len__(self) -> int:
        return 0


class AwaitingStore:
    """An async store whose methods are plain functions returning awaitables: no `iscoroutinefunction` sees it."""

    def __init__(self) -> None:
        self.inner = AsyncStore()

    def get(self, key: str) -> 'CoroutineType[Any, Any, int | None]':
        return self.inner.get(key)

    def put(self, key: str, total: int) -> 'CoroutineType[Any, Any, None]':
        return self.inner.put(key, total)

    @property
    def keys(self) -> list[str]:
        return self.inner.keys


# -- the number kinds of an index column (PRD-D23, decision 011), shared by the recording and the live tests ---

INTEGER_BOUNDS = '-2147483648 and 2147483647'
KIND_BOUNDS = {
    'byte': INTEGER_BOUNDS,  # OpenSearch refuses a byte or a short only beyond an integer's range
    'short': INTEGER_BOUNDS,
    'integer': INTEGER_BOUNDS,
    'long': '-9223372036854775808 and 9223372036854775807',
    'unsigned_long': '0 and 18446744073709551615',
    'float': '-3.4028234663852886e+38 and 3.4028234663852886e+38',
    'half_float': '-65504 and 65504',
}
#: (kind, a value OpenSearch answers, a value it refuses). Mostly the last and the first either side of the edge:
#: `float`'s are the two floats either side of the one that rounds to infinity, and `half_float`'s 65519 and 65520,
#: which the measurement found on both servers. `half_float-through-float` is 65519.99 and 65519.999, a pair that
#: straddles the edge without being its last and first: a `half_float` field reads a number as a 32-bit float first, so
#: the edge is 65519.998046875 (the float tie that rounds to 65520.0) and 65519.999 is the float 65520.0 (the server's
#: 400 for it, on both servers, CI run 46091503895), where rounded straight to half it would be 65504.
NUMBER_EDGES = [
    pytest.param('byte', '2147483647', '2147483648', id='byte-high'),
    pytest.param('byte', '-2147483648', '-2147483649', id='byte-low'),
    pytest.param('short', '2147483647', '2147483648', id='short-high'),
    pytest.param('short', '-2147483648', '-2147483649', id='short-low'),
    pytest.param('integer', '2147483647', '2147483648', id='integer-high'),
    pytest.param('integer', '-2147483648', '-2147483649', id='integer-low'),
    pytest.param('long', str(2**63 - 1), str(2**63), id='long-high'),
    pytest.param('long', str(-(2**63)), str(-(2**63) - 1), id='long-low'),
    pytest.param('unsigned_long', str(2**64 - 1), str(2**64), id='unsigned_long-high'),
    pytest.param('unsigned_long', '0', '-1', id='unsigned_long-low'),
    pytest.param('float', '3.4028235677973362e38', '3.4028235677973366e38', id='float-high'),
    pytest.param('float', '-3.4028235677973362e38', '-3.4028235677973366e38', id='float-low'),
    pytest.param('half_float', '65519', '65520', id='half_float-high'),
    pytest.param('half_float', '-65519', '-65520', id='half_float-low'),
    pytest.param('half_float', '65519.99', '65519.999', id='half_float-through-float'),
]


def number_edge(name: str) -> Any:
    """The `NUMBER_EDGES` pair with this id, for a test that wants a few of them."""
    return next(edge for edge in NUMBER_EDGES if edge.id == name)
