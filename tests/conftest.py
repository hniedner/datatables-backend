from collections.abc import AsyncIterator
from collections.abc import Iterator
from datetime import UTC
from datetime import date
from datetime import datetime
import os
from pathlib import Path
from typing import Any

import pytest
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
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from datatables_backend import DTParams
from datatables_backend import parse_request


def checked_database(url: str) -> str:
    """The database a URL names, if the tests may drop and create tables in it: `dtb` (CI's) or `dtb_test...`."""
    name = make_url(url).database or ''
    if name != 'dtb' and not name.startswith('dtb_test'):
        raise RuntimeError(
            'The SQL tests drop and create tables: DTB_SQL_URL and DTB_SQL_ASYNC_URL must name a database called dtb '
            'or dtb_test...'
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


def pytest_report_header() -> list[str]:
    """The real servers the tests run on, with their versions, so a run cannot pass on SQLite unnoticed."""
    lines: list[str] = []
    if SQL_URLS[0]:
        engine = create_engine(SQL_URLS[0])
        with engine.connect() as connection:
            version = '.'.join(map(str, connection.dialect.server_version_info or ()))
            if engine.dialect.name == 'postgresql':  # a non-UTC session is what makes the timezone test bite
                version += f', session TimeZone {connection.exec_driver_sql("SHOW TimeZone").scalar()}'
        engine.dispose()
        lines.append(f'SQL tests on {engine.dialect.name} {version}')
    if url := os.environ.get('DTB_OPENSEARCH_URL'):
        from opensearchpy import OpenSearch  # only for a live run

        client = OpenSearch(hosts=[url])
        lines.append(f'OpenSearch tests on {client.info()["version"]["number"]}')
        client.close()
    return lines


metadata = MetaData()
TEST_TABLES = frozenset({'dtb_test_people', 'dtb_test_tags'})  # the second is created by a test in test_sql.py

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
)

ROWS: list[dict[str, Any]] = [
    {
        'id': 1, 'name': 'Ann', 'email_address': 'ann@example.org', 'age': 31, 'born': date(1994, 3, 1),
        'seen': datetime(2026, 7, 15, 23, 59), 'seen_tz': datetime(2026, 7, 15, 23, 59, tzinfo=UTC),
        'team': 'red', 'secret': 's1',
    },
    {
        'id': 2, 'name': 'Bob', 'email_address': None, 'age': 45, 'born': date(1980, 7, 15),
        'seen': datetime(2026, 7, 16, 0, 0), 'seen_tz': datetime(2026, 7, 16, 0, 0, tzinfo=UTC),
        'team': 'blue', 'secret': 's2',
    },
    {
        'id': 3, 'name': 'anna_100%', 'email_address': '', 'age': None, 'born': None,
        'seen': None, 'seen_tz': None, 'team': 'red', 'secret': 's3',
    },
    {
        'id': 4, 'name': 'Cy', 'email_address': 'cy@example.org', 'age': 31, 'born': date(2001, 12, 31),
        'seen': datetime(2025, 1, 1, 12, 0), 'seen_tz': datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
        'team': 'green', 'secret': 's4',
    },
    {
        'id': 5, 'name': 'Dee', 'email_address': 'dee@example.org', 'age': 60, 'born': date(1966, 7, 1),
        'seen': datetime(2026, 7, 14, 20, 0), 'seen_tz': datetime(2026, 7, 14, 20, 0, tzinfo=UTC),
        'team': 'blue', 'secret': 's5',
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
    engine = create_async_engine(database[1])
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


def request(*names: str, **body: Any) -> DTParams:
    """A request for the columns ``names``, as DataTables sends it, with ``body`` merged in."""
    columns = [
        {'data': name, 'name': '', 'searchable': True, 'orderable': True, 'search': {'value': '', 'regex': False}}
        for name in names
    ]
    return parse_request({'draw': 1, 'start': 0, 'length': 10, 'columns': columns, **body})


def criterion(column: str, condition: str, *values: str) -> dict[str, Any]:
    return {
        'searchBuilder': {
            'logic': 'AND',
            'criteria': [{'condition': condition, 'origData': column, 'value': list(values)}],
        }
    }


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
