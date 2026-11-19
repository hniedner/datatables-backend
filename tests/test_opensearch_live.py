"""The OpenSearch backend against a real cluster: the bodies the recording tests pin down, evaluated for real.

Runs only when DTB_OPENSEARCH_URL names a cluster (the CI job starts one); otherwise every test here is skipped, and
the reason shows in the summary. The index holds five synthetic documents with one field of each mapping kind.

The rows asserted are what the backend answers today (draft 1). Left untested on purpose: the query syntax's OR (a
`should` of words) in a column search, which PRD v2 drops, and a negated condition on a missing field, which PRD v2
settles (PRD-F37).
"""

from collections.abc import AsyncIterator
from collections.abc import Iterator
import os
from typing import Any

from opensearchpy import AsyncOpenSearch
from opensearchpy import OpenSearch
import pytest

from datatables_backend import Column
from datatables_backend import DTResponse
from datatables_backend.opensearch import IndexColumn
from datatables_backend.opensearch import SearchTable

from .conftest import criterion
from .conftest import request

URL = os.environ.get('DTB_OPENSEARCH_URL', '')
pytestmark = pytest.mark.skipif(not URL, reason='DTB_OPENSEARCH_URL is not set')

INDEX = 'dtb-test-docs'  # owned by this module: deleted and recreated on every run
KEYWORD_TEXT = {'type': 'text', 'fields': {'keyword': {'type': 'keyword'}}}  # what dynamic mapping gives a string
MAPPING = {
    'properties': {
        'id': {'type': 'keyword'},
        'title': KEYWORD_TEXT,
        'body': {'type': 'text'},
        'pages': {'type': 'integer'},
        'created': {'type': 'date', 'format': 'strict_date'},
        'meta': {'properties': {'author': KEYWORD_TEXT}},
        'active': {'type': 'boolean'},
    }
}
DOCUMENTS: list[dict[str, Any]] = [
    {'id': 'a1', 'title': 'Apple pie', 'body': 'granny smith apples', 'pages': 12, 'created': '2026-07-15',
     'meta': {'author': 'Ann'}, 'active': True},
    {'id': 'b2', 'title': 'Banana bread', 'body': 'ripe bananas and green tea', 'pages': 30, 'created': '2026-07-16',
     'meta': {'author': 'Bob'}, 'active': False},
    {'id': 'c3', 'title': 'Cherry tart 100%', 'body': 'sour cherries'},
    {'id': 'd4', 'title': 'apple crumble', 'body': 'green apples', 'pages': 12, 'created': '2025-01-01',
     'meta': {'author': 'Cy'}, 'active': True},
    {'id': 'e5', 'title': 'Date loaf', 'body': 'medjool dates', 'pages': 60, 'created': '2026-02-10',
     'meta': {'author': 'Dee'}, 'active': False},
]  # fmt: skip
COLUMNS = [
    IndexColumn('id', text='keyword'),
    IndexColumn('title'),  # text+keyword
    IndexColumn('body', text='text'),
    Column('pages', type='number'),
    Column('created', type='date'),
    Column('author', field='meta.author'),
]
NAMES = ('id', 'title', 'body', 'pages', 'created', 'author')
table = SearchTable(INDEX, COLUMNS)


@pytest.fixture(scope='module')
def client() -> Iterator[OpenSearch]:
    client = OpenSearch(hosts=[URL])
    if client.indices.exists(index=INDEX):  # left by an interrupted run
        client.indices.delete(index=INDEX)
    client.indices.create(index=INDEX, body={'mappings': MAPPING, 'settings': {'number_of_replicas': 0}})
    for number, document in enumerate(DOCUMENTS, 1):
        client.index(index=INDEX, id=str(number), body=document)
    client.indices.refresh(index=INDEX)
    yield client
    client.indices.delete(index=INDEX)
    client.close()


@pytest.fixture
async def async_client(client: OpenSearch) -> AsyncIterator[AsyncOpenSearch]:  # the index exists once `client` ran
    async_client = AsyncOpenSearch(hosts=[URL])
    yield async_client
    await async_client.close()


def ids(response: DTResponse) -> list[str]:
    """The ids of the rows answered, in the order answered."""
    assert response.error is None, response.error
    return [row['id'] for row in response.data]


def found(response: DTResponse) -> list[str]:
    """The ids of the rows answered, in id order (for requests without an order)."""
    return sorted(ids(response))


def test_the_cluster_is_the_one_asked_for(client: OpenSearch):
    assert client.info()['version']['distribution'] == 'opensearch'


def test_an_unfiltered_draw_counts_every_document_and_reads_dotted_fields(client: OpenSearch):
    response = table.fetch_sync(request(*NAMES, order=[{'column': 0, 'dir': 'asc'}]), client)
    assert (response.recordsTotal, response.recordsFiltered) == (5, 5)
    assert response.data[0] == {
        'id': 'a1', 'title': 'Apple pie', 'body': 'granny smith apples', 'pages': 12, 'created': '2026-07-15',
        'author': 'Ann',
    }  # fmt: skip
    assert response.data[2]['author'] is None


def test_a_filtered_draw_counts_the_total_apart(client: OpenSearch):
    response = table.fetch_sync(request(*NAMES, search={'value': 'apple'}), client)
    assert (response.recordsTotal, response.recordsFiltered) == (5, 2)


def test_the_base_filter_limits_the_total_too(client: OpenSearch):
    long_reads = SearchTable(INDEX, COLUMNS, base_filter={'range': {'pages': {'gte': 30}}})
    response = long_reads.fetch_sync(request(*NAMES, search={'value': 'bread'}), client)
    assert (response.recordsTotal, response.recordsFiltered) == (2, 1)


async def test_the_async_client_answers_like_the_sync_one(client: OpenSearch, async_client: AsyncOpenSearch):
    params = request(*NAMES, search={'value': 'apple'}, order=[{'column': 0, 'dir': 'desc'}])
    assert await table.fetch(params, async_client) == table.fetch_sync(params, client)


def test_a_page_is_a_slice_of_the_order(client: OpenSearch):
    response = table.fetch_sync(request(*NAMES, start=1, length=2, order=[{'column': 0, 'dir': 'asc'}]), client)
    assert ids(response) == ['b2', 'c3']


# -- searches ---


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('apple', ['a1', 'd4']),  # the title, analysed or whole; the body's analysed `apples` is another token
        ('APPL', ['a1', 'd4']),  # a substring of the whole title, in any case
        ('green', ['b2', 'd4']),  # an analysed body word
        ('p?e', []),  # `?` is text: unescaped, the wildcard `*p?e*` would find "Apple pie"
        ('a*b', []),  # `*` is text too
        ('ann', ['a1']),  # the dotted author field
    ],
)
def test_the_global_search(client: OpenSearch, text: str, expected: list[str]):
    assert found(table.fetch_sync(request(*NAMES, search={'value': text}), client)) == expected


@pytest.mark.parametrize(
    ('column', 'value', 'expected'),
    [
        (2, '"granny smith" -green', ['a1']),  # a text column takes the query syntax
        (1, 'CRUMBLE', ['d4']),
        (3, '12', ['a1', 'd4']),  # a number column matches exactly
        (4, '2026-07', ['a1', 'b2']),  # a date column takes a month
        (4, '2026', ['a1', 'b2', 'e5']),
    ],
)
def test_a_column_search(client: OpenSearch, column: int, value: str, expected: list[str]):
    params = request(*NAMES)
    params.columns[column].search.value = value
    assert found(table.fetch_sync(params, client)) == expected


def test_a_multi_select_matches_whole_values(client: OpenSearch):
    params = request(*NAMES)
    params.columns[1].search.value = '^(Apple pie|Date loaf)$'
    params.columns[1].search.regex = True
    assert found(table.fetch_sync(params, client)) == ['a1', 'e5']


@pytest.mark.parametrize(
    ('column', 'values', 'expected'),
    [(0, ['a1', 'e5'], ['a1', 'e5']), (3, ['12', '60'], ['a1', 'd4', 'e5']), (4, ['2026-07-16'], ['b2'])],
)
def test_multi_values_match_exactly(client: OpenSearch, column: int, values: list[str], expected: list[str]):
    params = request(*NAMES)
    params.columns[column].search.multi_values = values
    assert found(table.fetch_sync(params, client)) == expected


# -- SearchBuilder ---


@pytest.mark.parametrize(
    ('column', 'condition', 'values', 'expected'),
    [
        ('id', '=', ['a1'], ['a1']),
        ('title', '=', ['Apple pie'], ['a1']),
        ('title', '!=', ['Apple pie'], ['b2', 'c3', 'd4', 'e5']),
        ('body', '=', ['green apples'], ['d4']),  # a phrase on analysed text
        ('body', '!=', ['green apples'], ['a1', 'b2', 'c3', 'e5']),
        ('body', 'starts', ['smith'], ['a1']),  # analysed text has no whole string: starts reads as contains
        ('body', 'ends', ['granny'], ['a1']),  # and so does ends
        ('title', 'contains', ['TART'], ['c3']),
        ('title', '!contains', ['apple'], ['b2', 'c3', 'e5']),
        ('title', 'starts', ['apple'], ['a1', 'd4']),
        ('id', '!ends', ['1'], ['b2', 'c3', 'd4', 'e5']),
        ('pages', '=', ['12'], ['a1', 'd4']),
        ('pages', '>', ['12'], ['b2', 'e5']),
        ('pages', '<=', ['30'], ['a1', 'b2', 'd4']),
        ('pages', 'between', ['12', '30'], ['a1', 'b2', 'd4']),
        ('pages', 'null', [], ['c3']),
        ('created', '=', ['2026-07-15'], ['a1']),
        ('created', '<', ['2026-07-15'], ['d4', 'e5']),
        ('created', '<=', ['2026-07-15'], ['a1', 'd4', 'e5']),
        ('created', '>', ['2026-07-15'], ['b2']),
        ('created', '>=', ['2026-07-15'], ['a1', 'b2']),
        ('created', 'between', ['2026-02-10', '2026-07-15'], ['a1', 'e5']),
        ('created', 'contains', ['2026-07'], ['a1', 'b2']),
        ('author', 'null', [], ['c3']),
        ('author', '!null', [], ['a1', 'b2', 'd4', 'e5']),
    ],
)
def test_searchbuilder_conditions(
    client: OpenSearch, column: str, condition: str, values: list[str], expected: list[str]
):
    assert found(table.fetch_sync(request(*NAMES, **criterion(column, condition, *values)), client)) == expected


def test_probe_boolean_equality(client: OpenSearch):
    """PROBE, replaced in the next commit: records what SearchBuilder `=` does on a boolean field."""
    flagged = SearchTable(INDEX, [IndexColumn('id', text='keyword'), Column('active')])
    outcomes: dict[str, object] = {}
    for value in ('1', 'true', 'abc'):
        try:
            outcomes[value] = found(
                flagged.fetch_sync(request('id', 'active', **criterion('active', '=', value)), client)
            )
        except Exception as error:  # the probe records whatever happens
            outcomes[value] = f'{type(error).__module__}.{type(error).__qualname__}'
    assert outcomes == {}, outcomes


def test_searchbuilder_groups_nest(client: OpenSearch):
    tree = {'logic': 'OR', 'criteria': [
        {'logic': 'AND', 'criteria': [
            {'condition': '=', 'origData': 'pages', 'value': ['12']},
            {'condition': 'contains', 'origData': 'body', 'value': ['granny']},
        ]},
        {'condition': '=', 'origData': 'id', 'value': ['e5']},
        {'logic': 'AND', 'criteria': []},
    ]}  # fmt: skip
    assert found(table.fetch_sync(request(*NAMES, searchBuilder=tree), client)) == ['a1', 'e5']


# -- order ---


@pytest.mark.parametrize(
    ('column', 'direction', 'expected'),
    [
        (1, 'asc', ['a1', 'b2', 'c3', 'e5', 'd4']),  # the whole title, by code point: capitals first
        (4, 'desc', ['b2', 'a1', 'e5', 'd4', 'c3']),  # a missing date last (OpenSearch's default)
    ],
)
def test_order(client: OpenSearch, column: int, direction: str, expected: list[str]):
    response = table.fetch_sync(request(*NAMES, order=[{'column': column, 'dir': direction}]), client)
    assert ids(response) == expected


def test_an_order_on_analysed_text_is_dropped_and_the_draw_still_answers(client: OpenSearch):
    response = table.fetch_sync(request(*NAMES, order=[{'column': 2, 'dir': 'asc'}]), client)
    assert found(response) == ['a1', 'b2', 'c3', 'd4', 'e5']
