"""The OpenSearch backend against the reference evaluator (issue #206): the requests and rows of
`tests/test_reference_properties.py`, answered by `fetch_sync` and by `fetch` on a live cluster and by
`tests/reference_evaluator.py` under `engine_for('opensearch', ...)`. Both answers must equal the evaluator's; the leg
is compared with the evaluator only, never with another backend.

Runs only when DTB_OPENSEARCH_URL names a cluster; otherwise the test is skipped, and the reason shows in the summary,
unless DTB_REQUIRE_OPENSEARCH=1 (the CI job), which makes a missing URL an error (`opensearch_live` in
`tests/conftest.py`).

The index `dtb-test-docs-oracle` is this module's own: deleted first and at the end. Its mapping holds the property's
columns as an adopter would map them: `id` and `n` `long`, declared `IndexColumn(type='number', number='long')`, so a
number outside 64 bits is refused before the query as on a SQL `BigInteger` (PRD-D23, step 3 of PRD-E3); `s` and `t`
`keyword`, declared `IndexColumn(text='keyword')`; `d` a `date` of `strict_date`, declared `type='date'`; `b`
`boolean`, declared `type='boolean'`; `m` a `double`, declared `type='number'` with no kind, whose documents hold the
cells of decision 010 written as floating-point numbers, so the evaluator compares them as doubles (`numeric='double'`,
#217; decision 010 did not measure OpenSearch's parsing of a number, so this leg is that measurement); the key is `id`
(PRD-D11). A NULL cell is a field the document does not hold (PRD-F26), so a stored `''` is a value there. Each
example replaces the documents with one `bulk` request that deletes
those of the example before and indexes its own, and refreshes the index.

PRD-O6, the order of text: a `keyword` field orders as OpenSearch itself sorts it. The leg measures it once, with a
plain search sorted on `s` over the documents of `STRINGS`, not through the library, and passes that order to the
evaluator; it is not asserted.

The count: 300 examples under the `ci` profile (`explore` keeps its own), so that the leg grows by well under a minute
(#206). Each example sends one `bulk` with a refresh, then a search and a count for `fetch_sync` and for `fetch`.
Measured on a local Podman VM (CI is expected at about twice that), the test's call time: under `ci` 4.0 to 5 s on
OpenSearch 3.10 and 4.0 s on 2.19.7; under `explore` (5,000 examples) 62 s and 48 s.
"""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from decimal import Decimal
from typing import Any

from hypothesis import event
from hypothesis import given
from opensearchpy import AsyncOpenSearch
from opensearchpy import OpenSearch
import pytest

from datatables_backend import Column
from datatables_backend import parse_request
from datatables_backend.opensearch import IndexColumn
from datatables_backend.opensearch import SearchTable

from .conftest import OPENSEARCH_URL
from .conftest import opensearch_live
from .conftest import server_examples
from .reference_evaluator import Declared
from .reference_evaluator import Engine
from .reference_evaluator import evaluate
from .test_reference_properties import STRINGS
from .test_reference_properties import TYPED_EXAMPLES
from .test_reference_properties import answer_of
from .test_reference_properties import cases
from .test_reference_properties import engine_for
from .test_reference_properties import events
from .test_reference_properties import pinned

pytestmark = opensearch_live()

INDEX = 'dtb-test-docs-oracle'  # owned by this module: deleted and recreated on every run
MAPPING = {
    'properties': {
        'id': {'type': 'long'},
        's': {'type': 'keyword'},
        't': {'type': 'keyword'},
        'n': {'type': 'long'},
        'd': {'type': 'date', 'format': 'strict_date'},
        'b': {'type': 'boolean'},
        'm': {'type': 'double'},
    }
}
#: The examples of the property on this leg under the `ci` profile
SERVER_EXAMPLES = 300


@dataclass
class Leg:
    """The cluster, through the sync and the async client, the evaluator's reading of it, and the keys of the
    documents the index holds."""

    client: OpenSearch
    async_client: AsyncOpenSearch
    #: The one event loop of the module: the async client's connections belong to the loop they were opened in
    runner: asyncio.Runner
    reading: Engine = field(default_factory=Engine)  # set by the fixture once the order of `keyword` is measured
    loaded: set[int] = field(default_factory=set)


def load(leg: Leg, rows: list[dict[str, Any]]) -> None:
    """One `bulk` request: the documents of the rows before that are not among ``rows`` deleted, ``rows`` indexed,
    the index refreshed. A NULL cell is left out of its document (PRD-F26)."""
    keys = {row['id'] for row in rows}
    actions: list[dict[str, Any]] = [{'delete': {'_index': INDEX, '_id': str(key)}} for key in leg.loaded - keys]
    for row in rows:
        document = {
            name: value.isoformat()
            if isinstance(value, date)
            else float(value)
            if isinstance(value, Decimal)
            else value
            for name, value in row.items()
            if value is not None
        }
        actions.extend([{'index': {'_index': INDEX, '_id': str(row['id'])}}, document])
    if actions:
        answer = leg.client.bulk(body=actions, params={'refresh': 'true'})
        assert not answer['errors'], answer
    leg.loaded = keys


def keyword_ranks(leg: Leg) -> dict[str, int]:
    """PRD-O6 as OpenSearch sorts a `keyword` field: the documents of `STRINGS` sorted on `s` by a plain search, and
    each value's rank in that order, values the sort ties sharing one."""
    load(leg, [{'id': key, 's': value} for key, value in enumerate(STRINGS, 1)])
    body = {'size': len(STRINGS), 'sort': [{'s': 'asc'}], '_source': ['s']}
    hits = leg.client.search(index=INDEX, body=body)['hits']['hits']
    ranks: dict[str, int] = {}
    rank, previous = 0, None
    for hit in hits:
        if hit['sort'] != previous:
            rank, previous = rank + 1, hit['sort']
        ranks[hit['_source']['s']] = rank
    assert sorted(ranks) == sorted(STRINGS), 'the sort did not return every value of STRINGS'
    return ranks


@pytest.fixture(scope='module')
def leg() -> Iterator[Leg]:
    client = OpenSearch(hosts=[OPENSEARCH_URL])
    try:
        if client.indices.exists(index=INDEX):  # left by an interrupted run
            client.indices.delete(index=INDEX)
        client.indices.create(index=INDEX, body={'mappings': MAPPING, 'settings': {'number_of_replicas': 0}})
        runner = asyncio.Runner()
        try:

            async def opened() -> AsyncOpenSearch:
                return AsyncOpenSearch(hosts=[OPENSEARCH_URL])

            leg = Leg(client, runner.run(opened()), runner)
            try:
                leg.reading = engine_for('opensearch', keyword_ranks(leg).__getitem__)
                yield leg
            finally:
                runner.run(leg.async_client.close())
        finally:
            runner.close()
            client.indices.delete(index=INDEX)
    finally:
        client.close()


def table_of(decl: tuple[Declared, ...]) -> SearchTable:
    """The declaration as an adopter of the mapping above writes it."""
    columns: list[Column] = []
    for name, kind, searchable, orderable in decl:
        if kind == 'string':
            columns.append(IndexColumn(name, text='keyword', searchable=searchable, orderable=orderable))
        elif kind == 'number':
            columns.append(IndexColumn(name, type='number', number='long', searchable=searchable, orderable=orderable))
        elif kind == 'decimal':  # a `double` field, declared with no kind (PRD-D23)
            columns.append(Column(name, type='number', searchable=searchable, orderable=orderable))
        else:
            columns.append(Column(name, type=kind, searchable=searchable, orderable=orderable))
    return SearchTable(INDEX, columns, unique_key='id')


@server_examples(SERVER_EXAMPLES)
@given(cases())
@pinned(TYPED_EXAMPLES)
def test_the_opensearch_backend_answers_as_the_reference_evaluator(
    leg: Leg, case: tuple[list[dict[str, Any]], Any, dict[str, Any]]
):
    """The rows, their order and both counts, or the error code, of `fetch_sync` and of `fetch` over the same indexed
    documents equal the evaluator's reading of OpenSearch. Catches a rule of PRD section 4.3 to 4.6 in scope where the
    backend and the requirements part on a real cluster, through either client: a query the cluster reads otherwise
    than the recording tests assume. The examples are the property's whose answer here hangs on the backend, its query
    or the 64-bit range of a `long` declaration (`TYPED_EXAMPLES`), among them `n = 2^53 + 1`, which finds the document
    that holds it, not its neighbour 2^53, as a number sent as a double would (PRD-F25)."""
    rows, decl, body = case
    params = parse_request(body)
    expected = evaluate(decl, rows, params, engine=leg.reading)
    for name in events(rows, decl, body, expected, leg.reading):
        event(name)
    table = table_of(decl)
    load(leg, rows)
    synced = answer_of(table.fetch_sync(params, leg.client))
    assert synced == expected, '`fetch_sync` and the reference evaluator answer differently'
    fetched = answer_of(leg.runner.run(table.fetch(params, leg.async_client)))
    assert fetched == expected, '`fetch` and the reference evaluator answer differently'
