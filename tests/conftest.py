from collections.abc import AsyncIterator
from collections.abc import Iterator
from datetime import date
from datetime import datetime
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
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from datatables_backend import DTParams
from datatables_backend import parse_request

metadata = MetaData()

people = Table(
    'people',
    metadata,
    SQLColumn('id', Integer, primary_key=True),
    SQLColumn('name', String(50)),
    SQLColumn('email_address', String(100), nullable=True),
    SQLColumn('age', SmallInteger, nullable=True),
    SQLColumn('born', Date, nullable=True),
    SQLColumn('seen', DateTime, nullable=True),
    SQLColumn('team', String(20)),
    SQLColumn('secret', String(50)),
)

ROWS: list[dict[str, Any]] = [
    {
        'id': 1, 'name': 'Ann', 'email_address': 'ann@example.org', 'age': 31, 'born': date(1994, 3, 1),
        'seen': datetime(2026, 7, 15, 23, 59), 'team': 'red', 'secret': 's1',
    },
    {
        'id': 2, 'name': 'Bob', 'email_address': None, 'age': 45, 'born': date(1980, 7, 15),
        'seen': datetime(2026, 7, 16, 0, 0), 'team': 'blue', 'secret': 's2',
    },
    {
        'id': 3, 'name': 'anna_100%', 'email_address': '', 'age': None, 'born': None,
        'seen': None, 'team': 'red', 'secret': 's3',
    },
    {
        'id': 4, 'name': 'Cy', 'email_address': 'cy@example.org', 'age': 31, 'born': date(2001, 12, 31),
        'seen': datetime(2025, 1, 1, 12, 0), 'team': 'green', 'secret': 's4',
    },
    {
        'id': 5, 'name': 'Dee', 'email_address': 'dee@example.org', 'age': 60, 'born': date(1966, 7, 1),
        'seen': datetime(2026, 7, 14, 8, 0), 'team': 'blue', 'secret': 's5',
    },
]  # fmt: skip


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / 'people.db'
    engine = create_engine(f'sqlite:///{path}')
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(people.insert(), ROWS)
    engine.dispose()
    return path


@pytest.fixture
def session(database: Path) -> Iterator[Session]:
    engine = create_engine(f'sqlite:///{database}')
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
async def async_session(database: Path) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(f'sqlite+aiosqlite:///{database}')
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
