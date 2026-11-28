"""A FastAPI application on SQLite that answers a DataTables page, with invented data.

Run it from the repository root (the uvicorn here is an overlay: it adds nothing to pyproject.toml or the lockfile):

    uv sync
    uv run --with uvicorn uvicorn example_app:app --app-dir examples

then open http://127.0.0.1:8000/. The page loads DataTables and ColumnControl from a CDN, so it needs the network.
Nothing in `examples/` ships in the wheel or the sdist.
"""

from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from datetime import timedelta
from pathlib import Path
from typing import Annotated
from typing import Any

from fastapi import Depends
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import Column as SQLColumn
from sqlalchemy import Date
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from datatables_backend import Column
from datatables_backend.fastapi import DataTablesParams
from datatables_backend.sqlalchemy import SQLTable

ADJECTIVES = ('amber', 'brisk', 'calm', 'dusty', 'eager', 'fuzzy', 'gentle', 'hollow', 'ivory', 'jolly')
NOUNS = ('kite', 'lamp', 'mug', 'net', 'oar', 'pail', 'quilt', 'rope', 'sled', 'tent', 'urn', 'vase')
COLOURS = ('red', 'green', 'blue', 'yellow')  # none is part of another, so a column search for one finds only it

# 120 rows, each label unique: an adjective and a noun. The size repeats, so sorting by it has ties.
SEED: list[dict[str, Any]] = [
    {
        'id': number + 1,
        'label': f'{ADJECTIVES[number % 10]} {NOUNS[number // 10]}',
        'colour': COLOURS[number % 4],
        'size': (number * 37) % 100 + 1,
        'added': date(2026, 1, 1) + timedelta(days=(number * 11) % 300),
        'status': 'closed' if number % 3 == 0 else 'open',
    }
    for number in range(120)
]

metadata = MetaData()
items_table = Table(
    'items',
    metadata,
    SQLColumn('id', Integer, primary_key=True),
    SQLColumn('label', String),
    SQLColumn('colour', String),
    SQLColumn('size', Integer),
    SQLColumn('added', Date),
    SQLColumn('status', String),
)
# An in-memory database: one connection, shared, so every request sees the rows the lifespan wrote
engine = create_async_engine('sqlite+aiosqlite://', poolclass=StaticPool)

# The types come from the SQL columns; a name is the table's column and the page's `data`
items = SQLTable(items_table, [Column(name) for name in ('id', 'label', 'colour', 'size', 'added', 'status')])


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(items_table.insert(), SEED)
    yield
    await engine.dispose()


async def session() -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine) as opened:
        yield opened


SessionDep = Annotated[AsyncSession, Depends(session)]
app = FastAPI(lifespan=lifespan)


@app.get('/', response_class=HTMLResponse)
async def page() -> str:
    return Path(__file__).with_name('index.html').read_text(encoding='utf-8')


@app.post('/items/table')
async def items_table_route(params: DataTablesParams, opened: SessionDep) -> dict[str, Any]:
    # `to_dict()`, not the `DTResponse` model: a route annotated `-> DTResponse` also answers `error: null`
    return (await items.fetch(params, opened)).to_dict()
