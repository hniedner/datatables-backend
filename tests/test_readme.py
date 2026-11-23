"""The README's code blocks run as written: the PyPI page is the first thing an adopter copies, so a renamed argument or
a removed export fails here. SQL runs on SQLite (aiosqlite), OpenSearch on a recording client; the names a block takes
from the reader's app (a table, a session, a request) are supplied."""

import asyncio
from pathlib import Path
import re
import textwrap
from typing import Annotated
from typing import Any

from fastapi import Depends
from fastapi import FastAPI
from fastapi.testclient import TestClient
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine

from datatables_backend import DTParams
from datatables_backend import DTResponse
from datatables_backend import parse_request

BLOCKS = re.findall(r'```python\n(.*?)```', (Path(__file__).parents[1] / 'README.md').read_text(), re.DOTALL)
REQUEST = {
    'draw': 3,
    'start': 0,
    'length': 10,
    'columns': [{'data': name, 'searchable': True, 'orderable': True} for name in ('id', 'email', 'joined')],
}


def run(marker: str, **names: Any) -> Any:
    """Run the one block that holds ``marker``, wrapped in a function so its ``await`` and ``return`` work."""
    (block,) = (block for block in BLOCKS if marker in block)
    scope = dict(names)
    exec(compile('async def example():\n' + textwrap.indent(block, '    '), 'README.md', 'exec'), scope)  # noqa: S102
    return scope['example']()


def test_the_readme_holds_the_three_examples_this_file_runs():
    assert len(BLOCKS) == 3


async def test_the_sql_example_answers_a_request_on_sqlite(tmp_path: Path):
    meta = sa.MetaData()
    users_table = sa.Table(
        'users',
        meta,
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('email_address', sa.String),
        sa.Column('joined', sa.Date),
        sa.Column('active', sa.Boolean),
    )
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "readme.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(meta.create_all)
        await connection.execute(
            sa.insert(users_table),
            [
                {'id': 1, 'email_address': 'ann@example.org', 'active': True},
                {'id': 2, 'email_address': 'bob@example.org', 'active': False},
            ],
        )
    answer = await run(
        'SQLTable(', users_table=users_table, request_json=REQUEST, AsyncSession=AsyncSession, engine=engine
    )
    await engine.dispose()
    assert answer['draw'] == 3
    assert [row['email'] for row in answer['data']] == ['ann@example.org']


async def test_the_opensearch_example_searches_its_index():
    searches: list[dict[str, Any]] = []

    class RecordingClient:
        def __init__(self, hosts: list[str]):
            assert hosts

        async def search(self, *, index: str, body: dict[str, Any]) -> dict[str, Any]:
            searches.append({'index': index, **body})
            shards = {'total': 1, 'successful': 1, 'failed': 0}
            return {
                'timed_out': False,
                '_shards': shards,
                'hits': {'total': {'value': 0, 'relation': 'eq'}, 'hits': []},
            }

        count = search

    params = parse_request(REQUEST)
    await run('SearchTable(', params=params, AsyncOpenSearch=RecordingClient)
    assert searches[0]['index'] == 'reports'


def test_the_fastapi_example_reads_a_request_into_params():
    class Users:
        async def fetch(self, params: DTParams, session: int) -> DTResponse:
            return DTResponse(draw=params.draw, recordsTotal=0, recordsFiltered=0, data=[])

    session = Annotated[int, Depends(lambda: 1)]
    app = FastAPI()
    asyncio.run(run('DataTablesParams', app=app, users=Users(), Any=Any, SessionDep=session))
    answer = TestClient(app).post('/users/table', json=REQUEST)
    assert answer.json()['draw'] == 3
