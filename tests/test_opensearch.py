"""The OpenSearch backend: the request bodies it sends, and what it makes of the answers.

A recording client stands in for the cluster. Whether OpenSearch evaluates the bodies as intended belongs to the
container tests of milestone 2; these pin down what is sent.
"""

from collections.abc import Mapping
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


def refused(response: DTResponse) -> str:
    assert response.error_code == 'invalid_filter'
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
    hit = {'_source': {'id': 'a', 'title': 'T', 'pages': 3, 'meta': {'author': 'Ann'}}, '_score': 1.0}
    hooked = SearchTable('docs', COLUMNS, rows=RowHooks(row_id=lambda row: row['id']))
    (row,) = hooked.fetch_sync(request(*NAMES), Client(hits=[hit])).data
    assert row == {
        'id': 'a', 'title': 'T', 'body': None, 'pages': 3, 'created': None, 'author': 'Ann', 'DT_RowId': 'a',
    }  # fmt: skip


@pytest.mark.parametrize(
    'answer',
    [
        {'_shards': {'total': 2, 'successful': 1, 'failed': 1}},
        {'timed_out': True},
        {'_shards': {'total': 2}},  # no failed count: cannot be checked
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
    client = Client()
    assert 'beyond row 10,000' in refused(table.fetch_sync(request(*NAMES, start=9995, length=10), client))
    assert client.searches == []


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


def test_a_global_search_over_numbers_dates_and_booleans_only_is_refused():
    """SPEC-F13.2."""
    client = Client()
    typed = SearchTable(
        'docs', [Column('pages', type='number'), Column('created', type='date'), Column('active', type='boolean')]
    )
    refused(typed.fetch_sync(request('pages', 'created', 'active', search={'value': '3'}), client))
    assert client.searches == []


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
    ('condition', 'values', 'clause'),
    [
        ('=', ['true'], {'term': {'active': True}}),
        ('=', ['TRUE'], {'term': {'active': True}}),
        ('=', ['1'], {'term': {'active': True}}),
        ('=', ['False'], {'term': {'active': False}}),
        ('=', ['0'], {'term': {'active': False}}),
        ('=', [' true '], {'term': {'active': True}}),  # SPEC-F43.4
        ('!=', ['true'], {'bool': {'must_not': [{'term': {'active': True}}]}}),  # keeps a document without the field
        ('null', [], {'bool': {'must_not': [{'exists': {'field': 'active'}}]}}),
        ('!null', [], {'exists': {'field': 'active'}}),
    ],
)
def test_searchbuilder_on_a_boolean_column_sends_json_booleans(
    condition: str, values: list[str], clause: dict[str, Any]
):
    """SPEC-F43.1, SPEC-F43.2, SPEC-F43.4: the field itself is queried, with a JSON boolean, never the user's text."""
    client = Client()
    flags.fetch_sync(request(*FLAG_NAMES, **criterion('active', condition, *values)), client)
    assert clauses(client) == [clause]


@pytest.mark.parametrize(
    ('condition', 'values'),
    [('=', ['yes']), ('=', ['2']), ('=', ['']), ('<', ['true']), ('contains', ['true']), ('starts', ['true'])],
)
def test_a_value_or_condition_a_boolean_column_does_not_take_sends_nothing(condition: str, values: list[str]):
    """SPEC-F43.3: refused in planning, so OpenSearch never sees a term it would reject."""
    client = Client()
    reason = refused(flags.fetch_sync(request(*FLAG_NAMES, **criterion('active', condition, *values)), client))
    assert 'yes' not in reason
    assert (client.searches, client.counts) == ([], [])


def test_the_global_search_has_no_clause_for_a_boolean_column():
    """SPEC-F44.1."""
    client = Client()
    flags.fetch_sync(request(*FLAG_NAMES, search={'value': 'true'}), client)
    (clause,) = clauses(client)
    assert len(clause['bool']['should']) == 2  # id and title
    assert 'active' not in str(clause)


@pytest.mark.parametrize(
    ('search', 'values'),
    [
        ({'value': 'true'}, [True]),
        ({'value': '0'}, [False]),
        ({'value': '^(true|0)$', 'regex': True}, [True, False]),
        ({'multi_values': ['TRUE', '1', 'True']}, [True]),
    ],
)
def test_a_column_search_on_a_boolean_column_is_a_terms_query_of_booleans(search: dict[str, Any], values: list[bool]):
    """SPEC-F44.2, SPEC-F44.3: a value, a categorical filter and `multi_values` alike match exactly."""
    client, params = Client(), request(*FLAG_NAMES)
    params.columns[2].search = params.columns[2].search.model_copy(update=search)
    flags.fetch_sync(params, client)
    assert clauses(client) == [{'terms': {'active': values}}]


def test_a_column_search_on_a_boolean_column_refuses_any_other_value():
    """SPEC-F44.2."""
    client, params = Client(), request(*FLAG_NAMES)
    params.columns[2].search.value = 'yes'
    assert 'yes' not in refused(flags.fetch_sync(params, client))
    assert client.searches == []


def test_a_boolean_column_sorts_on_the_field_itself():
    """The body behind SPEC-O2.3."""
    client = Client()
    flags.fetch_sync(request(*FLAG_NAMES, order=[{'column': 2, 'dir': 'desc'}]), client)
    assert client.searches[0]['sort'] == [{'active': {'order': 'desc'}}]


def test_a_boolean_field_is_a_string_column_unless_declared_boolean():
    """PRD-D8 for OpenSearch: the mapping is not read, so `active` without a type is a default string column."""
    client = Client()
    undeclared = SearchTable('docs', [IndexColumn('id', text='keyword'), Column('active')])
    undeclared.fetch_sync(request('id', 'active', **criterion('active', '=', 'true')), client)
    assert clauses(client) == [{'term': {'active.keyword': 'true'}}]


@pytest.mark.xfail(
    reason='PRD-D14: a search-value hook on a column that is not a string is not refused yet, for any type '
    '(SPEC-D14.1)',
    strict=True,
    raises=pytest.fail.Exception,
)
def test_a_search_value_hook_on_a_boolean_column_is_a_schema_error():
    with pytest.raises(SchemaError):
        SearchTable('docs', [IndexColumn('id', text='keyword'), Column('active', type='boolean', search_value=str)])
