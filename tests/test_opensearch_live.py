"""The OpenSearch backend against a real cluster: the bodies the recording tests pin down, evaluated for real.

Runs only when DTB_OPENSEARCH_URL names a cluster (the CI job starts one); otherwise every test here is skipped, and
the reason shows in the summary, unless DTB_REQUIRE_OPENSEARCH=1 (the CI job), which makes a missing URL an error. The
index holds five synthetic documents with one field of each mapping kind.

The rows asserted are what the backend answers today. Left untested on purpose: the query syntax's OR (a `should` of
words) in a column search, which PRD v2 drops, and a negated condition on a missing field of a string, number or date
column, which PRD v2 settles (PRD-F37). A boolean column follows PRD v2 already, its `!=` on a missing field included.
"""

from collections.abc import AsyncIterator
from collections.abc import Iterator
import os
from typing import Any

from opensearchpy import AsyncOpenSearch
from opensearchpy import OpenSearch
from opensearchpy.exceptions import RequestError
import pytest

from datatables_backend import Column
from datatables_backend import DTResponse
from datatables_backend.opensearch import IndexColumn
from datatables_backend.opensearch import SearchTable

from .conftest import criterion
from .conftest import request

URL = os.environ.get('DTB_OPENSEARCH_URL', '')
if os.environ.get('DTB_REQUIRE_OPENSEARCH') == '1' and not URL:  # the CI job: a run that skips every test is a failure
    raise RuntimeError('DTB_REQUIRE_OPENSEARCH is set, but DTB_OPENSEARCH_URL is not')
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
        'label': KEYWORD_TEXT,  # declared only by the table `labelled` below
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
     'meta': {'author': 'Dee'}, 'active': False, 'label': 'ÄPFEL'},
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


# -- exact matches in any case (PRD-F9) ---


@pytest.mark.parametrize(
    ('body', 'expected'),
    [
        (criterion('title', '=', 'apple PIE'), ['a1']),  # SPEC-F9.2
        ({'column_search': {'title': {'multi_values': ['APPLE PIE', 'date LOAF']}}}, ['a1', 'e5']),  # SPEC-F9.3
    ],
)
def test_an_exact_match_on_a_string_column_ignores_case(client: OpenSearch, body: dict[str, Any], expected: list[str]):
    assert found(table.fetch_sync(request(*NAMES, **body), client)) == expected


LABEL_NAMES = ('id', 'label')
labelled = SearchTable(INDEX, [IndexColumn('id', text='keyword'), IndexColumn('label')])  # e5 holds `ÄPFEL`


@pytest.mark.parametrize(
    'body',
    [
        {'search': {'value': 'äpfel'}},  # SPEC-F9.9: a whole word, which the analysed text answers
        {'search': {'value': 'äpf'}},  # SPEC-F9.10: a substring, which only the wildcard on the whole string answers
        criterion('label', '=', 'äpfel'),  # SPEC-F9.11: the term query
    ],
)
def test_a_letter_outside_ascii_is_matched_in_any_case(client: OpenSearch, body: dict[str, Any]):
    """What the cluster makes of `case_insensitive` beyond ASCII (PRD-F9)."""
    assert found(labelled.fetch_sync(request(*LABEL_NAMES, **body), client)) == ['e5']


def test_a_long_list_of_exact_values_is_answered(client: OpenSearch):
    """1,100 values are 1,100 term queries in one `bool`, where a `terms` query took 65,536."""
    values = [f'n{index}' for index in range(1099)] + ['APPLE PIE']
    params = request(*NAMES, column_search={'title': {'multi_values': values}})
    assert found(table.fetch_sync(params, client)) == ['a1']


# The two tests below declare string columns on a boolean mapping. An OpenSearch column is string unless declared
# boolean (PRD-D8), so PRD-F43 does not change their outcomes. The cases with the column declared boolean follow them.


@pytest.mark.parametrize('value', ['1', 'true', 'abc'])
def test_searchbuilder_equality_on_a_boolean_field_matches_nothing_today(client: OpenSearch, value: str):
    """A string column on a boolean mapping: the column is a default (text+keyword) string column, and `=` queries its
    keyword subfield `active.keyword`, which a boolean mapping does not have: no document matches, whatever the value.
    PRD-D8/PRD-F43 apply once the column is declared boolean."""
    flagged = SearchTable(INDEX, [IndexColumn('id', text='keyword'), Column('active')])
    assert found(flagged.fetch_sync(request('id', 'active', **criterion('active', '=', value)), client)) == []


REFUSED_BY_OPENSEARCH = pytest.mark.xfail(
    reason='a string column on a boolean mapping: OpenSearch rejects a term other than true/false on a boolean field, '
    'so the search fails; PRD-D8/PRD-F43 apply once the column is declared boolean',
    strict=True,
    raises=RequestError,
)


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        pytest.param('1', ['a1', 'd4'], marks=REFUSED_BY_OPENSEARCH),
        ('true', ['a1', 'd4']),
        pytest.param('abc', [], marks=REFUSED_BY_OPENSEARCH),
    ],
)
def test_searchbuilder_equality_on_a_boolean_field_itself(client: OpenSearch, value: str, expected: list[str]):
    """A string column on a boolean mapping: a keyword-kind column terms on `active` itself, and only `true` and
    `false` reach a boolean field. The expected rows of the strict xfails are never compared, since the search raises.
    PRD-D8/PRD-F43 apply once the column is declared boolean."""
    flagged = SearchTable(INDEX, [IndexColumn('id', text='keyword'), IndexColumn('active', text='keyword')])
    assert found(flagged.fetch_sync(request('id', 'active', **criterion('active', '=', value)), client)) == expected


# The column declared boolean (PRD-D8): `active` is true for a1 and d4, false for b2 and e5, missing in c3. The spec
# statements cited speak of their own four-row fixture; the rows here are the same cases on this index.

FLAG_NAMES = ('id', 'active')
flags = SearchTable(INDEX, [IndexColumn('id', text='keyword'), Column('active', type='boolean')])


@pytest.mark.parametrize(
    ('condition', 'values', 'expected'),
    [
        ('=', ['1'], ['a1', 'd4']),  # the term OpenSearch rejects on the string column above, sent as a JSON true
        ('=', ['false'], ['b2', 'e5']),
        ('!=', ['true'], ['b2', 'c3', 'e5']),  # a missing field is not true (PRD-F37)
    ],
)
def test_searchbuilder_on_a_declared_boolean_column(
    client: OpenSearch, condition: str, values: list[str], expected: list[str]
):
    """SPEC-F43.1, SPEC-F43.2: what the cluster answers to the JSON booleans the backend sends."""
    params = request(*FLAG_NAMES, **criterion('active', condition, *values))
    assert found(flags.fetch_sync(params, client)) == expected


def test_a_categorical_filter_on_a_declared_boolean_column(client: OpenSearch):
    """SPEC-F44.3: a `terms` query of both JSON booleans on the boolean field."""
    params = request(*FLAG_NAMES)
    params.columns[1].search.value = '^(true|0)$'
    params.columns[1].search.regex = True
    assert found(flags.fetch_sync(params, client)) == ['a1', 'b2', 'd4', 'e5']


@pytest.mark.parametrize(
    ('direction', 'expected'), [('asc', ['b2', 'e5', 'a1', 'd4']), ('desc', ['a1', 'd4', 'b2', 'e5'])]
)
def test_false_sorts_before_true(client: OpenSearch, direction: str, expected: list[str]):
    """The boolean part of SPEC-O2.3, SPEC-O8.1 and SPEC-O8.2, measured: `active`, then `id` ascending, over the
    documents that have the field. Where a missing value goes and which way the key runs are PRD-O2's and PRD-O3's."""
    params = request(*FLAG_NAMES, order=[{'column': 1, 'dir': direction}, {'column': 0, 'dir': 'asc'}])
    assert [row for row in ids(flags.fetch_sync(params, client)) if row != 'c3'] == expected


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
