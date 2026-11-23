"""The OpenSearch backend through real opensearch-py clients, sync and async.

A stub connection stands in for the network: the client serializes the request and sends it through the stub, which
records the method, path and body it receives and answers with canned JSON. This pins the call the backend makes on
the client (``search``/``count`` with ``index=`` and ``body=``) at whatever opensearch-py version is installed, which
the minimum-versions job sets to the declared floor.
"""

from __future__ import annotations

from collections.abc import Collection
from collections.abc import Iterator
from collections.abc import Mapping
import json
from typing import Any

from opensearchpy import AsyncConnection
from opensearchpy import AsyncOpenSearch
from opensearchpy import Connection
from opensearchpy import OpenSearch
import pytest

from datatables_backend.opensearch import IndexColumn
from datatables_backend.opensearch import SearchTable

from .conftest import request

SHARDS = {'total': 1, 'successful': 1, 'failed': 0}
ANSWERS: dict[str, dict[str, Any]] = {
    '/docs/_search': {
        'took': 1,
        'timed_out': False,
        '_shards': SHARDS,
        'hits': {'total': {'value': 1, 'relation': 'eq'}, 'max_score': None, 'hits': [{'_source': {'id': 'a'}}]},
    },
    '/docs/_count': {'count': 5, '_shards': SHARDS},
}
SENT: list[tuple[str, str, Any]] = []
table = SearchTable('docs', [IndexColumn('id', text='keyword'), IndexColumn('title')], unique_key='id')


def answer(method: str, url: str, body: bytes | str | None) -> tuple[int, dict[str, str], str]:
    SENT.append((method, url, json.loads(body) if body else None))
    return 200, {'content-type': 'application/json'}, json.dumps(ANSWERS[url])


class StubConnection(Connection):
    def perform_request(  # noqa: PLR0917
        self,
        method: str,
        url: str,
        params: Mapping[str, Any] | None = None,
        body: bytes | None = None,
        timeout: float | None = None,
        ignore: Collection[int] = (),
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        return answer(method, url, body)


class StubAsyncConnection(AsyncConnection):
    async def perform_request(  # noqa: PLR0917
        self,
        method: str,
        url: str,
        params: Mapping[str, Any] | None = None,
        body: bytes | None = None,
        timeout: float | None = None,
        ignore: Collection[int] = (),
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        return answer(method, url, body)

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def sent() -> Iterator[list[tuple[str, str, Any]]]:
    SENT.clear()
    yield SENT
    SENT.clear()


def check(sent: list[tuple[str, str, Any]], rows: list[dict[str, Any]], counts: tuple[int, int]) -> None:
    (search_method, search_path, search), (count_method, count_path, count) = sent
    assert (search_method, search_path) == ('POST', '/docs/_search')
    assert (search['from'], search['size'], search['track_total_hits']) == (0, 10, True)
    assert search['_source'] == ['id', 'title']
    assert (count_method, count_path, count) == ('POST', '/docs/_count', {'query': {'match_all': {}}})
    assert rows == [{'id': 'a', 'title': None}]
    assert counts == (5, 1)


def test_a_real_sync_client_sends_the_search_and_count_and_maps_the_answer(sent: list[tuple[str, str, Any]]):
    client = OpenSearch(hosts=[{'host': 'stub', 'port': 9200}], connection_class=StubConnection)
    response = table.fetch_sync(request('id', 'title', search={'value': 'a'}), client)
    check(sent, response.data, (response.recordsTotal, response.recordsFiltered))


async def test_a_real_async_client_sends_the_search_and_count_and_maps_the_answer(sent: list[tuple[str, str, Any]]):
    client = AsyncOpenSearch(hosts=[{'host': 'stub', 'port': 9200}], connection_class=StubAsyncConnection)
    try:
        response = await table.fetch(request('id', 'title', search={'value': 'a'}), client)
    finally:
        await client.close()
    check(sent, response.data, (response.recordsTotal, response.recordsFiltered))
