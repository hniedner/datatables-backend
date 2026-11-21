"""The OpenSearch backend: the request bodies it sends, and what it makes of the answers.

A recording client stands in for the cluster. Whether OpenSearch evaluates the bodies as intended belongs to the
container tests of milestone 2; these pin down what is sent.
"""

from collections.abc import Mapping
import logging
from typing import Any

import pytest

from datatables_backend import Column
from datatables_backend import DTResponse
from datatables_backend import PartialResultError
from datatables_backend import RowHooks
from datatables_backend import SchemaError
from datatables_backend import TotalCache
from datatables_backend.opensearch import IndexColumn
from datatables_backend.opensearch import SearchTable

from .conftest import criterion
from .conftest import request

SHARDS = {'total': 2, 'successful': 2, 'failed': 0}
COLUMNS = [
    IndexColumn('id', text='keyword'),
    IndexColumn('title'),  # text+keyword
    IndexColumn('body', text='text'),
    Column('pages', type='number'),
    Column('created', type='date'),
    Column('author', field='meta.author'),
]
NAMES = ('id', 'title', 'body', 'pages', 'created', 'author')
table = SearchTable('docs', COLUMNS, base_filter={'term': {'published': True}})


class Client:
    """Answers every search with ``hits`` and every count with ``count``; keeps what it was sent."""

    def __init__(self, hits: list[dict[str, Any]] | None = None, total: int = 7, count: int = 40, **answer: Any):
        self.searches: list[Mapping[str, Any]] = []
        self.counts: list[Mapping[str, Any]] = []
        self.answer = {
            'timed_out': False,
            '_shards': SHARDS,
            'hits': {'total': {'value': total, 'relation': 'eq'}, 'max_score': None, 'hits': hits or []},
            **answer,
        }
        self.count_answer: dict[str, Any] = {'count': count, '_shards': SHARDS}

    def search(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        assert index == 'docs'
        self.searches.append(body)
        return self.answer

    def count(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        assert index == 'docs'
        self.counts.append(body)
        return self.count_answer


class AsyncClient(Client):
    async def search(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:  # type: ignore[override]
        return Client.search(self, index=index, body=body)

    async def count(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:  # type: ignore[override]
        return Client.count(self, index=index, body=body)


def query(client: Client) -> list[dict[str, Any]]:
    """The clauses the request added to the base filter."""
    must = client.searches[-1]['query']['bool']['must']
    assert must[0] == {'term': {'published': True}}
    return must[1:]


def refused(response: DTResponse, code: str = 'invalid_filter') -> str:
    assert response.error_code == code
    assert response.error is not None
    return response.error


# -- one draw ---


def test_an_unfiltered_draw_is_one_search_that_answers_both_counts():
    client = Client(total=7)
    response = table.fetch_sync(request(*NAMES, start=5, length=5), client)
    assert (response.recordsTotal, response.recordsFiltered) == (7, 7)
    assert client.counts == []
    body = client.searches[0]
    assert (body['from'], body['size'], body['track_total_hits']) == (5, 5, True)
    assert body['_source'] == ['id', 'title', 'body', 'pages', 'created', 'meta.author']
    assert query(client) == []


def test_a_filtered_draw_counts_the_total_once_per_ttl():
    client, cached = Client(total=3, count=40), SearchTable('docs', COLUMNS, total_cache=TotalCache(60))
    params = request(*NAMES, search={'value': 'x'})
    assert (cached.fetch_sync(params, client).recordsTotal, cached.fetch_sync(params, client).recordsFiltered) == (
        40,
        3,
    )
    assert client.counts == [{'query': {'match_all': {}}}]


def test_the_total_count_keeps_the_base_filter():
    client = Client()
    table.fetch_sync(request(*NAMES, search={'value': 'x'}), client)
    assert client.counts == [{'query': {'bool': {'must': [{'term': {'published': True}}]}}}]


async def test_the_async_client_answers_like_the_sync_one():
    params = request(*NAMES, search={'value': 'x'}, order=[{'column': 1, 'dir': 'desc'}])
    sync_client, async_client = Client(), AsyncClient()
    assert await table.fetch(params, async_client) == table.fetch_sync(params, sync_client)
    assert async_client.searches == sync_client.searches


def test_rows_are_read_from_the_source_by_field_including_dotted_paths():
    """Also SPEC-R7.2: a value comes back as `_source` holds it, an HTML text unescaped."""
    html = '<b>x</b> & "y"'
    hit = {'_source': {'id': 'a', 'title': html, 'pages': 3, 'meta': {'author': 'Ann'}}, '_score': 1.0}
    hooked = SearchTable('docs', COLUMNS, rows=RowHooks(row_id=lambda row: row['id']))
    (row,) = hooked.fetch_sync(request(*NAMES), Client(hits=[hit])).data
    assert row == {
        'id': 'a', 'title': html, 'body': None, 'pages': 3, 'created': None, 'author': 'Ann', 'DT_RowId': 'a',
    }  # fmt: skip


@pytest.mark.parametrize(
    'answer',
    [
        {'_shards': {'total': 2, 'successful': 1, 'failed': 1}},
        {'_shards': {'total': 2, 'successful': 1, 'failed': 0}},  # SPEC-E9.1: a shard missing, none counted as failed
        {'timed_out': True},
        {'_shards': {'total': 2, 'successful': 2}},  # no failed count: cannot be checked
        {'_shards': {'total': 2, 'failed': 0}},  # nor without the count of those that answered
        {'_shards': None},
    ],
)
def test_an_answer_not_known_to_cover_every_shard_raises(answer: dict[str, Any]):
    with pytest.raises(PartialResultError):
        table.fetch_sync(request(*NAMES), Client(**answer))


def test_a_partial_count_raises_too():
    client = Client()
    client.count_answer = {'count': 1, '_shards': {'total': 2, 'failed': 1}}
    with pytest.raises(PartialResultError):
        table.fetch_sync(request(*NAMES, search={'value': 'x'}), client)


def test_a_page_beyond_the_result_window_is_refused_before_anything_is_sent():
    """The code of PRD-C12. Its counts and the pages PRD-C10 still serves are pinned below."""
    client = Client()
    response = table.fetch_sync(request(*NAMES, start=9995, length=10), client)
    assert 'beyond row 10,000' in refused(response, 'page_out_of_range')
    assert (client.searches, client.counts) == ([], [])


def test_a_filter_mistake_comes_before_a_page_beyond_the_result_window():
    """SPEC-E3.5: step 4 before step 5, so the user first hears of the filter they can change."""
    params = request(*FLAG_NAMES, start=9995, length=10, **criterion('active', '=', 'yes'))
    assert 'true or false' in refused(flags.fetch_sync(params, Client()))


@pytest.mark.xfail(
    reason='PRD-C11, PRD-C12: the counts are not run before the refusal yet, so it carries none (SPEC-C12.1)',
    strict=True,
    raises=AssertionError,
)
def test_a_page_out_of_range_carries_the_real_counts():
    small = SearchTable('docs', COLUMNS, max_result_window=3)
    response = small.fetch_sync(request(*NAMES, start=4, length=3), Client(total=10, count=10))
    assert (response.error_code, response.recordsTotal, response.recordsFiltered) == ('page_out_of_range', 10, 10)


@pytest.mark.xfail(
    reason='PRD-C3: a start past the end beyond the result window is refused, not answered with an empty page and '
    'both counts (SPEC-C3.3)',
    strict=True,
    raises=AssertionError,
)
def test_a_start_past_the_end_is_an_empty_page_also_beyond_the_result_window():
    small = SearchTable('docs', COLUMNS, max_result_window=2)
    response = small.fetch_sync(request(*NAMES, start=10), Client(total=4, count=4))
    assert (response.error_code, response.recordsTotal, response.recordsFiltered) == (None, 4, 4)


@pytest.mark.parametrize(
    ('column', 'value', 'code'),
    [
        (3, '7 8', 'invalid_number'),  # several words
        (3, '!7', 'invalid_number'),
        (4, '!2024', 'invalid_date'),
        (4, '2024-13', 'invalid_date'),
    ],
)
def test_a_column_search_a_number_or_date_column_cannot_read_has_its_code(column: int, value: str, code: str):
    """SPEC-E3.6, SPEC-F33.2, SPEC-F34.2: these are found only while the request body is built, and nothing is
    sent."""
    client, params = Client(), request(*NAMES)
    params.columns[column].search.value = value
    refused(table.fetch_sync(params, client), code)
    assert (client.searches, client.counts) == ([], [])


@pytest.mark.parametrize(
    ('column', 'event'),
    [(3, 'datatables_number_unreadable'), (2, 'datatables_search_refused')],
    ids=['number', 'exact-match-on-analysed-text'],
)
def test_a_mistake_found_while_the_body_is_built_is_logged_once(
    caplog: pytest.LogCaptureFixture, column: int, event: str
):
    """SPEC-E13.1 for the refusals of this backend's own: a value its number column cannot read, and a list of
    exact values on analysed text, which has no whole string to match. The value is in no record."""
    client, params = Client(), request(*NAMES)
    params.columns[column].search.multi_values = ['MARKER']
    with caplog.at_level(logging.DEBUG):
        refused(table.fetch_sync(params, client), 'invalid_number' if column == 3 else 'invalid_filter')
    assert [(record.name, record.levelname, record.msg) for record in caplog.records] == [
        ('datatables_backend', 'WARNING', event)
    ]
    assert 'MARKER' not in repr([vars(record) for record in caplog.records])


# -- searches ---


def test_the_global_search_fits_each_string_mapping_and_skips_numbers_and_dates():
    client = Client()
    table.fetch_sync(request(*NAMES, search={'value': 'a*b'}), client)
    (clause,) = query(client)
    should = clause['bool']['should']
    assert should[0] == {'wildcard': {'id': {'value': '*a\\*b*', 'case_insensitive': True}}}
    assert should[1]['bool']['should'] == [
        {'match': {'title': {'query': 'a*b'}}},
        {'wildcard': {'title.keyword': {'value': '*a\\*b*', 'case_insensitive': True}}},
    ]
    assert should[2] == {'match': {'body': {'query': 'a*b'}}}
    assert len(should) == 4  # id, title, body, author


def test_a_global_search_over_numbers_and_dates_only_is_refused():
    numbers = SearchTable('docs', [Column('pages', type='number'), Column('created', type='date')])
    refused(numbers.fetch_sync(request('pages', 'created', search={'value': '3'}), Client()))


def test_column_searches_follow_the_columns_type():
    client, params = Client(), request(*NAMES)
    params.columns[2].search.value = '"granny smith" -green'
    params.columns[3].search.value = '12'
    params.columns[4].search.value = '2026-02'
    table.fetch_sync(params, client)
    text, number, date = query(client)
    assert text['bool']['must'] == [{'match_phrase': {'body': 'granny smith'}}]
    assert text['bool']['must_not'] == [{'match': {'body': {'query': 'green'}}}]
    assert number == {'term': {'pages': 12}}
    assert date == {'range': {'created': {'gte': '2026-02-01', 'lt': '2026-03-01', 'format': 'strict_date'}}}


def test_a_multi_select_is_a_terms_query_on_the_whole_string():
    client, params = Client(), request(*NAMES)
    params.columns[1].search.value = 'a|b'
    params.columns[1].search.regex = True
    table.fetch_sync(params, client)
    assert query(client) == [{'terms': {'title.keyword': ['a', 'b']}}]


# -- SearchBuilder ---


@pytest.mark.parametrize(
    ('column', 'condition', 'values', 'clause'),
    [
        ('id', '=', ['A1'], {'term': {'id': 'A1'}}),
        ('title', '!=', ['T'], {'bool': {'must_not': [{'term': {'title.keyword': 'T'}}]}}),
        ('body', '=', ['x y'], {'match_phrase': {'body': 'x y'}}),
        ('title', 'starts', ['a?'], {'wildcard': {'title.keyword': {'value': 'a\\?*', 'case_insensitive': True}}}),
        ('id', '!ends', ['z'], {'bool': {'must_not': [{'wildcard': {'id': {'value': '*z', 'case_insensitive': True}}}]}}),
        ('pages', 'between', ['1', '9'], {'range': {'pages': {'gte': 1, 'lte': 9}}}),
        ('pages', '>', ['2.5'], {'range': {'pages': {'gt': 2.5}}}),
        ('created', '=', ['2026-07-15'], {'range': {'created': {'gte': '2026-07-15', 'lt': '2026-07-16', 'format': 'strict_date'}}}),
        ('created', '<=', ['2026-07-15'], {'range': {'created': {'lt': '2026-07-16', 'format': 'strict_date'}}}),
        ('created', '>', ['2026-07-15'], {'range': {'created': {'gte': '2026-07-16', 'format': 'strict_date'}}}),
        ('author', 'null', [], {'bool': {'must_not': [{'exists': {'field': 'meta.author'}}]}}),
        ('author', '!null', [], {'exists': {'field': 'meta.author'}}),
    ],
)  # fmt: skip
def test_searchbuilder_criteria(column: str, condition: str, values: list[str], clause: dict[str, Any]):
    client = Client()
    table.fetch_sync(request(*NAMES, **criterion(column, condition, *values)), client)
    assert query(client) == [clause]


def test_searchbuilder_or_groups_need_one_match():
    client = Client()
    tree = {'logic': 'OR', 'criteria': [
        {'condition': '=', 'origData': 'id', 'value': ['a']},
        {'condition': '=', 'origData': 'id', 'value': ['b']},
        {'logic': 'AND', 'criteria': []},
    ]}  # fmt: skip
    table.fetch_sync(request(*NAMES, searchBuilder=tree), client)
    assert query(client) == [
        {'bool': {'should': [{'term': {'id': 'a'}}, {'term': {'id': 'b'}}], 'minimum_should_match': 1}}
    ]


def test_an_undeclared_field_is_refused_before_anything_is_sent():
    client = Client()
    refused(table.fetch_sync(request(*NAMES, **criterion('secret', '=', 'x')), client))
    refused(table.fetch_sync(request(*NAMES, **criterion('title.keyword', '=', 'x')), client))
    assert client.searches == []


# -- sorting ---


def test_sorting_uses_the_whole_string_and_drops_analysed_text():
    client = Client()
    order = [{'column': 1, 'dir': 'desc'}, {'column': 2, 'dir': 'asc'}, {'column': 3, 'dir': 'asc'}]
    table.fetch_sync(request(*NAMES, order=order), client)
    assert client.searches[0]['sort'] == [{'title.keyword': {'order': 'desc'}}, {'pages': {'order': 'asc'}}]


# -- a boolean column ---
#
# What OpenSearch makes of these bodies is measured in test_opensearch_live.py.

FLAG_NAMES = ('id', 'title', 'active')
flags = SearchTable('docs', [IndexColumn('id', text='keyword'), IndexColumn('title'), Column('active', type='boolean')])


def clauses(client: Client) -> list[dict[str, Any]]:
    """The clauses of the last search of a table without a base filter."""
    return client.searches[-1]['query']['bool']['must']


@pytest.mark.parametrize(
    ('condition', 'clause'),
    [
        ('=', {'term': {'active': True}}),
        ('!=', {'bool': {'must_not': [{'term': {'active': True}}]}}),  # keeps a document without the field
    ],
)
def test_searchbuilder_on_a_boolean_column_sends_a_json_boolean(condition: str, clause: dict[str, Any]):
    """SPEC-F43.1, SPEC-F43.2: the field itself is queried, with a JSON boolean, never the user's text."""
    client = Client()
    flags.fetch_sync(request(*FLAG_NAMES, **criterion('active', condition, 'TRUE')), client)
    assert clauses(client) == [clause]


def test_a_column_search_on_a_boolean_column_is_a_terms_query_of_booleans():
    """SPEC-F44.3: the field itself, not a keyword subfield, and JSON booleans."""
    client, params = Client(), request(*FLAG_NAMES)
    params.columns[2].search.value = '^(true|0)$'
    params.columns[2].search.regex = True
    flags.fetch_sync(params, client)
    assert clauses(client) == [{'terms': {'active': [True, False]}}]


@pytest.mark.xfail(
    reason='PRD-D14: a search-value hook on a column that is not a string is not refused yet, for any type '
    '(SPEC-D14.1)',
    strict=True,
    raises=pytest.fail.Exception,
)
def test_a_search_value_hook_on_a_boolean_column_is_a_schema_error():
    with pytest.raises(SchemaError):
        SearchTable('docs', [IndexColumn('id', text='keyword'), Column('active', type='boolean', search_value=str)])
