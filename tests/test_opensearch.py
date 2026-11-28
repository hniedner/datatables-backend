"""The OpenSearch backend: the request bodies it sends, and what it makes of the answers.

A recording client stands in for the cluster. These pin down what is sent; whether OpenSearch evaluates the bodies as
intended is tested against a real cluster in tests/test_opensearch_live.py.
"""

import asyncio
from collections.abc import Callable
from collections.abc import Mapping
import json
import logging
import re
from typing import Any
from typing import cast

from opensearchpy.exceptions import ConnectionError as NoConnection
import pytest

from datatables_backend import Column
from datatables_backend import DTParams
from datatables_backend import DTResponse
from datatables_backend import PartialResultError
from datatables_backend import RowHooks
from datatables_backend import SchemaError
from datatables_backend import TotalCache
from datatables_backend import parse_request
from datatables_backend.opensearch import IndexColumn
from datatables_backend.opensearch import NumberKind
from datatables_backend.opensearch import SearchTable

from .conftest import ERROR_CODES
from .conftest import KIND_BOUNDS
from .conftest import NUMBER_EDGES
from .conftest import AsyncStore
from .conftest import AwaitingStore
from .conftest import EmptyStore
from .conftest import Store
from .conftest import columncontrol
from .conftest import columncontrol_list
from .conftest import criterion
from .conftest import exact_values
from .conftest import flat
from .conftest import labelled
from .conftest import logged_text
from .conftest import nested
from .conftest import number_edge
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
table = SearchTable('docs', COLUMNS, unique_key='id', base_filter={'term': {'published': True}})


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
    """The text of an answered user error; SPEC-E2.1: a case that expects a code outside the eight fails here."""
    assert code in ERROR_CODES
    assert response.error_code == code
    assert response.error is not None
    return response.error


# -- one draw ---


def test_an_unfiltered_draw_is_one_search_that_answers_both_counts():
    """SPEC-C7.1: the search asks for `track_total_hits` and no other call is made; SPEC-C8.1: one search answers
    both counts."""
    client = Client(total=7)
    response = table.fetch_sync(request(*NAMES, start=5, length=5), client)
    assert (response.recordsTotal, response.recordsFiltered) == (7, 7)
    assert client.counts == []
    body = client.searches[0]
    assert (body['from'], body['size'], body['track_total_hits']) == (5, 5, True)
    assert body['_source'] == ['id', 'title', 'body', 'pages', 'created', 'meta.author']
    assert query(client) == []


def test_an_unfiltered_draw_fills_the_total_cache_for_the_filtered_draws_after_it():
    """SPEC-C14.3, PRD-C14, PRD-C8: an unfiltered search's count is the total, and the cache keeps it. Were it not
    kept, every filtered draw after it would pay a count of its own, with nothing to show for it."""
    client, cached = (
        Client(total=7, count=40),
        SearchTable('docs', COLUMNS, unique_key='id', total_cache=TotalCache(60)),
    )
    cached.fetch_sync(request(*NAMES), client)  # nothing beyond the base filter: the search's own count is the total
    response = cached.fetch_sync(request(*NAMES, search={'value': 'x'}), client)
    assert response.recordsTotal == 7
    assert client.counts == []


async def test_the_async_fetch_fills_the_total_cache_with_an_unfiltered_request_too():
    """SPEC-C14.3 through `fetch`, which has its own copy of the unfiltered branch: a global search right after an
    unfiltered request runs no total count and answers that total."""
    client = AsyncClient(total=7, count=40)
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=TotalCache(60))
    await cached.fetch(request(*NAMES), client)
    response = await cached.fetch(request(*NAMES, search={'value': 'x'}), client)
    assert response.recordsTotal == 7
    assert client.counts == []


async def test_the_async_fetch_answers_a_user_error_too():
    """SPEC-E1.1 says `fetch` and `fetch_sync`: each has its own place where the mistake becomes an answer, and
    nothing is sent from either."""
    client = AsyncClient()
    response = await table.fetch(request(*NAMES, **criterion('pages', '=', 'abc')), client)
    assert refused(response, 'invalid_number')
    assert (client.searches, client.counts) == ([], [])


def test_rows_are_read_from_the_source_by_field_including_dotted_paths():
    """SPEC-R1.1: `author` reads `meta.author` and the row has no `meta`; also SPEC-R7.2: a value comes back as
    `_source` holds it, an HTML text unescaped."""
    html = '<b>x</b> & "y"'
    hit = {'_source': {'id': 'a', 'title': html, 'pages': 3, 'meta': {'author': 'Ann'}}, '_score': 1.0}
    hooked = SearchTable('docs', COLUMNS, unique_key='id', rows=RowHooks(row_id=lambda row: row['id']))
    (row,) = hooked.fetch_sync(request(*NAMES), Client(hits=[hit])).data
    assert row == {
        'id': 'a', 'title': html, 'body': None, 'pages': 3, 'created': None, 'author': 'Ann', 'DT_RowId': 'a',
    }  # fmt: skip


#: Answers that are not known to cover every shard (PRD-E9), as the keys they replace in a complete one
NOT_COVERED = [
    {'_shards': {'total': 2, 'successful': 1, 'failed': 1}},
    {'_shards': {'total': 2, 'successful': 1, 'failed': 0}},  # SPEC-E9.1: a shard missing, none counted as failed
    {'timed_out': True},  # SPEC-E9.4: every shard answered, and the search timed out
    {'_shards': {'total': 2, 'successful': 2}},  # no failed count: cannot be checked
    {'_shards': {'total': 2, 'failed': 0}},  # nor without the count of those that answered
    {'_shards': None},
]


@pytest.mark.parametrize('answer', NOT_COVERED)
def test_an_answer_not_known_to_cover_every_shard_raises(answer: dict[str, Any]):
    """SPEC-E9.1, SPEC-E9.4."""
    with pytest.raises(PartialResultError):
        table.fetch_sync(request(*NAMES), Client(**answer))


@pytest.mark.parametrize('answer', NOT_COVERED)
async def test_the_async_fetch_raises_on_an_answer_not_known_to_cover_every_shard(answer: dict[str, Any]):
    """SPEC-E9.1, SPEC-E9.4 through `fetch`, whose normal page search reads its answer apart from `fetch_sync`'s: the
    request filters nothing, so the search's own count is the total, and a partial one must not become the answer."""
    with pytest.raises(PartialResultError):
        await table.fetch(request(*NAMES), AsyncClient(**answer))


def test_a_partial_count_raises_too():
    """SPEC-E9.3."""
    client = Client()
    client.count_answer = {'count': 1, '_shards': {'total': 2, 'failed': 1}}
    with pytest.raises(PartialResultError):
        table.fetch_sync(request(*NAMES, search={'value': 'x'}), client)


async def test_the_async_fetch_raises_on_a_partial_count_too():
    """SPEC-E9.3 through `fetch`: the total is counted where the filtered search is answered, in its own copy."""
    client = AsyncClient()
    client.count_answer = {'count': 1, '_shards': {'total': 2, 'successful': 1, 'failed': 1}}
    with pytest.raises(PartialResultError):
        await table.fetch(request(*NAMES, search={'value': 'x'}), client)


def test_a_page_beyond_the_result_window_asks_for_the_count_and_never_for_a_page():
    """SPEC-C12.1, SPEC-C11.1, SPEC-C10.4's refusal: the refusal carries the real counts; the one count of the table's
    query (the total is its reuse, PRD-C8) runs before it, and no page search."""
    client = Client(count=20000)
    response = table.fetch_sync(request(*NAMES, start=9995, length=10), client)
    refused(response, 'page_out_of_range')
    assert (response.recordsTotal, response.recordsFiltered) == (20000, 20000)
    assert client.searches == []
    assert client.counts == [{'query': {'bool': {'must': [{'term': {'published': True}}]}}}]


@pytest.mark.parametrize(
    ('searched', 'names', 'body', 'code', 'text'),
    [
        (
            table,
            NAMES,
            {'column_search': {'body': {'multi_values': ['a']}}},  # `body` is analysed text with no keyword field
            'invalid_filter',
            'The column "body" can be searched only for text it contains, not for a list of exact values. Type the '
            'text to search for instead.',
        ),
        (
            table,
            NAMES,
            {'column_search': {'pages': {'multi_values': ['7', 'x']}}},  # the list is read where the body is built
            'invalid_number',
            'Each value in the filter on "pages" must be a number, written like 42 or -3.5.',
        ),
        (
            table,
            NAMES,
            {'column_search': {'pages': {'value': '7 8'}}},  # one value, read where the body is built: not a list
            'invalid_number',
            'The filter on "pages" needs one number, written like 42 or -3.5.',
        ),
        (
            SearchTable('docs', [Column('pages', type='number'), Column('created', type='date')], unique_key='pages'),
            ('pages', 'created'),
            {'search': {'value': 'x'}},
            'invalid_filter',
            'No column of this table can be searched from the search box. Clear it and use a column filter instead.',
        ),
        (
            SearchTable(
                'docs', COLUMNS, unique_key='id', max_result_window=3
            ),  # the number in the text is the table's own
            NAMES,
            {'start': 4, 'length': 3},
            'page_out_of_range',
            'This table can be paged through its first 3 rows only. Go back to an earlier page, or narrow the search '
            'so fewer rows match.',
        ),
    ],
    ids=['exact-values-on-analysed-text', 'list-on-a-number-column', 'one-value', 'no-text-column', 'page'],
)
def test_a_user_error_found_while_the_body_is_built_says_what_is_wrong_and_what_to_do(
    searched: SearchTable, names: tuple[str, ...], body: dict[str, Any], code: str, text: str
):
    """SPEC-E4.23, SPEC-E4.25, SPEC-E4.6, SPEC-E4.15, SPEC-E4.24: the texts of the mistakes only the OpenSearch build
    finds, and one the plan finds with this backend's description of the columns (`no-text-column`: a number and a
    date take no part in the search here). The plan's mistakes on every backend are asserted once, in
    tests/test_sql.py."""
    assert refused(searched.fetch_sync(request(*names, **body), Client()), code) == text


def test_a_filter_mistake_comes_before_a_page_beyond_the_result_window():
    """SPEC-E3.5: step 4 before step 5, so the user first hears of the filter they can change."""
    params = request(*FLAG_NAMES, start=9995, length=10, **criterion('active', '=', 'yes'))
    client = Client()
    assert 'true or false' in refused(flags.fetch_sync(params, client))
    assert (client.searches, client.counts) == ([], [])  # the mistake of a lower step runs no count either


@pytest.mark.parametrize('found_in_the_build', ['a column search that is no number', 'no column to search'])
def test_a_mistake_found_while_the_body_is_built_waits_for_an_earlier_step_of_the_plan(found_in_the_build: str):
    """PRD-E3 across the two places a mistake is found: one from the build must not be raised on the spot, since the
    plan may hold one of a lower step."""
    if found_in_the_build == 'a column search that is no number':  # step 2, beside a control character (step 1)
        searched = table
        params = request(*NAMES, column_search={'pages': {'value': 'abc'}}, **criterion('title', '=', 'a\x00b'))
        expected = 'invalid_character'
    else:  # step 4: only a number and a date column, which take no free text; beside a date of the wrong format
        searched = SearchTable(
            'docs', [Column('pages', type='number'), Column('created', type='date')], unique_key='pages'
        )
        params = request('pages', 'created', search={'value': 'x'}, **criterion('created', '=', 'xx'))
        expected = 'invalid_date'
    refused(searched.fetch_sync(params, Client()), expected)


def test_a_mistake_found_while_the_body_is_built_comes_first_when_it_is_read_first():
    """SPEC-E3.10: the column search `abc` on `pages` is found only while the body is built, and the list on `created`
    by the plan; both are of step 2, and `pages` is read first. Catches the plan's mistake answered before an earlier
    one of the same step that the build finds."""
    searches = {'pages': {'value': 'abc'}, 'created': {'multi_values': ['2024-13-01']}}
    client = Client()
    refused(table.fetch_sync(request(*NAMES, column_search=searches), client), 'invalid_number')
    assert (client.searches, client.counts) == ([], [])


# -- the counts before a refusal, and the empty page past the end (PRD-C3, PRD-C9, PRD-C11, PRD-C12) ---

EVERYTHING = {'query': {'match_all': {}}}


class Counting(Client):
    """Answers a count by what it is asked: the query that matches everything (the total of a table without a base
    filter) is `total`, any other `filtered`. The order of the calls is not part of the contract."""

    def __init__(self, *, total: int, filtered: int, **answer: Any):
        super().__init__(**answer)
        self.numbers = {'total': total, 'filtered': filtered}

    def count(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        self.counts.append(body)
        return {'count': self.numbers['total' if body == EVERYTHING else 'filtered'], '_shards': SHARDS}


class AsyncCounting(Counting):
    async def search(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:  # type: ignore[override]
        return Client.search(self, index=index, body=body)

    async def count(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:  # type: ignore[override]
        return Counting.count(self, index=index, body=body)


def bounded(window: int) -> SearchTable:
    return SearchTable('docs', COLUMNS, unique_key='id', max_result_window=window)


def test_a_refusal_with_counts_logs_one_line_and_an_empty_page_none(caplog: pytest.LogCaptureFixture):
    """SPEC-E4.24 with the counts of C12.1: the refusal is logged once, and the line holds neither a count nor the
    search text; the empty page of PRD-C3 is no refusal and logs nothing."""
    client = Counting(total=4242, filtered=1717)
    with caplog.at_level(logging.DEBUG):
        bounded(3).fetch_sync(request(*NAMES, start=4, length=3, search={'value': 'MARKER'}), client)
    assert [(record.levelname, record.msg) for record in caplog.records] == [
        ('WARNING', 'datatables_page_out_of_range')
    ]
    logged = logged_text(caplog)
    assert not any(text in logged for text in ('4242', '1717', 'MARKER'))
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        bounded(3).fetch_sync(request(*NAMES, start=5000), client)  # past the 4242 rows the unfiltered count gives
    assert caplog.records == []


async def test_the_async_fetch_counts_before_the_refusal_too():
    """SPEC-C12.1, SPEC-C12.3, SPEC-C11.1 through `fetch`, which builds its answer apart from `fetch_sync`: a filtered
    refusal, so that both of its counts are run."""
    client = AsyncCounting(total=40, filtered=7)
    response = await bounded(3).fetch(request(*NAMES, start=2, length=3, search={'value': 'x'}), client)
    assert (response.error_code, response.recordsTotal, response.recordsFiltered) == ('page_out_of_range', 40, 7)
    assert client.searches == []
    assert len(client.counts) == 2
    assert EVERYTHING in client.counts


@pytest.mark.parametrize(
    ('filtering', 'total', 'filtered', 'start'),
    [
        ({}, 4, 4, 10),  # well past the end
        # A start equal to the filtered count is past the last row, and the total is not what it is compared with
        ({'search': {'value': 'x'}}, 40, 7, 7),
    ],
    ids=['past-the-end', 'at-the-end-of-a-filtered-set'],
)
def test_a_start_past_the_end_is_an_empty_page_also_beyond_the_result_window(
    filtering: dict[str, Any], total: int, filtered: int, start: int
):
    """SPEC-C3.3: PRD-C3 wins over PRD-C12, and no page is searched for."""
    client = Counting(total=total, filtered=filtered)
    response = bounded(2).fetch_sync(request(*NAMES, start=start, **filtering), client)
    assert (response.error_code, response.recordsTotal, response.recordsFiltered) == (None, total, filtered)
    assert response.data == []
    assert client.searches == []


@pytest.mark.parametrize(
    ('filtering', 'total', 'filtered', 'start'),
    [({}, 4, 4, 10), ({'search': {'value': 'x'}}, 40, 7, 7)],
    ids=['past-the-end', 'at-the-end-of-a-filtered-set'],
)
async def test_the_async_fetch_answers_a_start_past_the_end_as_an_empty_page_beyond_the_window_too(
    filtering: dict[str, Any], total: int, filtered: int, start: int
):
    """SPEC-C3.3 through `fetch`: PRD-C3 wins over PRD-C12, and no page is searched for."""
    client = AsyncCounting(total=total, filtered=filtered)
    response = await bounded(2).fetch(request(*NAMES, start=start, **filtering), client)
    assert (response.error_code, response.recordsTotal, response.recordsFiltered) == (None, total, filtered)
    assert response.data == []
    assert client.searches == []


def test_the_largest_start_is_an_empty_page_and_no_search_carries_it():
    """SPEC-C3.2 on this backend: a `from` of 2^53-1 is one a cluster refuses, so none is sent."""
    client = Counting(total=4, filtered=4)
    response = SearchTable('docs', COLUMNS, unique_key='id').fetch_sync(request(*NAMES, start=2**53 - 1), client)
    assert (response.error_code, response.recordsTotal, response.recordsFiltered, response.data) == (None, 4, 4, [])
    assert client.searches == []


def test_a_filtered_refusal_counts_the_filtered_set_and_the_total_each_by_its_own_query():
    """SPEC-C12.3: with a search the total is a count of its own (PRD-C8). Which answer is which is told by the call's
    body: the order of the two calls is not part of the contract."""
    sent = Client()
    bounded(3).fetch_sync(request(*NAMES, length=3, search={'value': 'x'}), sent)  # what the same filter asks for
    client = Counting(total=40, filtered=7)
    response = bounded(3).fetch_sync(request(*NAMES, start=2, length=3, search={'value': 'x'}), client)
    assert (response.error_code, response.recordsTotal, response.recordsFiltered) == ('page_out_of_range', 40, 7)
    assert client.searches == []
    assert len(client.counts) == 2
    assert EVERYTHING in client.counts
    assert {'query': sent.searches[0]['query']} in client.counts


def test_the_total_cache_serves_the_refusal_too():
    """SPEC-C12.4: the second refusal within the TTL runs only the count of the filtered set."""
    cached = SearchTable('docs', COLUMNS, unique_key='id', max_result_window=3, total_cache=TotalCache(60))
    client = Counting(total=40, filtered=7)
    params = request(*NAMES, start=2, length=3, search={'value': 'x'})
    cached.fetch_sync(params, client)
    response = cached.fetch_sync(params, client)
    assert (response.error_code, response.recordsTotal, response.recordsFiltered) == ('page_out_of_range', 40, 7)
    assert len(client.counts) == 3
    assert client.counts.count(EVERYTHING) == 1


async def test_the_total_cache_serves_the_async_refusal_too():
    """SPEC-C12.4 through `fetch`, which fills and reads the cache by its own lines."""
    cached = SearchTable('docs', COLUMNS, unique_key='id', max_result_window=3, total_cache=TotalCache(60))
    client = AsyncCounting(total=40, filtered=7)
    params = request(*NAMES, start=2, length=3, search={'value': 'x'})
    await cached.fetch(params, client)
    response = await cached.fetch(params, client)
    assert (response.error_code, response.recordsTotal, response.recordsFiltered) == ('page_out_of_range', 40, 7)
    assert len(client.counts) == 3
    assert client.counts.count(EVERYTHING) == 1


@pytest.mark.parametrize(
    ('filtering', 'total', 'counts'),
    [
        ({}, 23456, 1),  # the filtered set is the whole table: its count is the total (PRD-C8)
        ({'search': {'value': 'x'}}, 40000, 2),  # the total stays a count of its own
    ],
    ids=['unfiltered', 'filtered'],
)
def test_a_lower_bound_is_replaced_by_an_exact_count(filtering: dict[str, Any], total: int, counts: int):
    """SPEC-C9.1, SPEC-C9.2: a search that answers `gte` 10,000 says only that there are at least that many."""
    client = Counting(total=total, filtered=23456)
    client.answer['hits']['total'] = {'value': 10000, 'relation': 'gte'}
    response = SearchTable('docs', COLUMNS, unique_key='id').fetch_sync(request(*NAMES, **filtering), client)
    assert (response.recordsTotal, response.recordsFiltered) == (total, 23456)
    assert len(client.counts) == counts


async def test_the_async_fetch_replaces_a_lower_bound_too():
    """SPEC-C9.2 through `fetch`."""
    client = AsyncCounting(total=40000, filtered=23456)
    client.answer['hits']['total'] = {'value': 10000, 'relation': 'gte'}
    plain = SearchTable('docs', COLUMNS, unique_key='id')
    response = await plain.fetch(request(*NAMES, search={'value': 'x'}), client)
    assert (response.recordsTotal, response.recordsFiltered) == (40000, 23456)


@pytest.mark.parametrize(
    ('column', 'value', 'code'),
    [
        ('pages', '7 8', 'invalid_number'),  # several words
        ('pages', '!78', 'invalid_number'),  # a negated word of two characters is a word
        ('created', '!2024', 'invalid_date'),
        ('created', '2024-13', 'invalid_date'),
    ],
)
def test_a_column_search_a_number_or_date_column_cannot_read_has_its_code(column: str, value: str, code: str):
    """SPEC-E3.6, SPEC-F33.2, SPEC-F34.2: these are found only while the request body is built, and nothing is
    sent."""
    client = Client()
    refused(table.fetch_sync(request(*NAMES, column_search={column: {'value': value}}), client), code)
    assert (client.searches, client.counts) == ([], [])


@pytest.mark.parametrize('condition', ['=', '!=', 'starts', '!starts', 'ends', '!ends'])
def test_a_condition_that_needs_a_whole_string_is_refused_on_analysed_text(condition: str):
    """SPEC-D19.1: analysed text holds no whole string to equal or to anchor on, so the plan refuses these, for the
    user to read, before anything is sent."""
    client = Client()
    text = refused(table.fetch_sync(request(*NAMES, **criterion('body', condition, 'red fox')), client))
    assert text == (
        f'The condition "{condition}" cannot be used on "body", which holds analysed text only. Search the column '
        'for words, or choose contains, !contains, null or !null.'
    )
    assert (client.searches, client.counts) == ([], [])


@pytest.mark.parametrize(
    ('body', 'expected'),
    [
        (criterion('body', 'contains', 'red fox'), {'match_phrase': {'body': 'red fox'}}),
        (
            criterion('body', '!contains', 'red fox'),
            {'bool': {'must_not': [{'match_phrase': {'body': 'red fox'}}]}},
        ),
        (criterion('body', 'null'), {'bool': {'must_not': [{'exists': {'field': 'body'}}]}}),
        (criterion('body', '!null'), {'exists': {'field': 'body'}}),
        (
            {'column_search': {'body': {'value': 'red fox'}}},
            {'bool': {'must': [{'match_phrase': {'body': 'red'}}, {'match_phrase': {'body': 'fox'}}]}},
        ),
        ({'column_search': {'body': {'multi_values': ['']}}}, {'bool': {'must_not': [{'exists': {'field': 'body'}}]}}),
        (
            {'column_search': {'body': {'value': '^()$', 'regex': True}}},
            {'bool': {'must_not': [{'exists': {'field': 'body'}}]}},
        ),
    ],
    ids=['contains', 'not-contains', 'null', 'not-null', 'column-search', 'empty-list', 'empty-categorical'],
)
def test_analysed_text_still_answers_words_contains_null_and_an_empty_list(
    body: dict[str, Any], expected: dict[str, Any]
):
    """SPEC-D19.2, SPEC-D19.3, SPEC-F11.7: the refusals of the test above reach no further than their list."""
    client = Client()
    table.fetch_sync(request(*NAMES, **body), client)
    assert query(client) == [expected]


def test_a_hook_on_analysed_text_is_a_schema_error_that_names_the_column_and_the_fix():
    """SPEC-D20.1: a hook compares by equality, which analysed text cannot answer (PRD-D19)."""
    with pytest.raises(SchemaError) as raised:
        SearchTable(
            'docs',
            [IndexColumn('id', text='keyword'), IndexColumn('body', text='text', search_value=str.upper)],
            unique_key='id',
        )
    assert str(raised.value) == (
        'The column "body" has a search_value hook but holds analysed text only, which a hook compares by equality. '
        'Declare it text="keyword" or "text+keyword", or remove the hook.'
    )


@pytest.mark.parametrize('kind', ['keyword', 'text+keyword'])
def test_a_hook_on_a_column_with_a_keyword_field_is_declared(kind: str):
    SearchTable(
        'docs',
        [IndexColumn('id', text='keyword'), IndexColumn('secret', text=cast(Any, kind), search_value=str.upper)],
        unique_key='id',
    )


@pytest.mark.parametrize(
    ('column', 'code', 'event'),
    [
        ('pages', 'invalid_number', 'datatables_number_unreadable'),
        ('body', 'invalid_filter', 'datatables_search_refused'),
    ],
    ids=['number', 'exact-match-on-analysed-text'],
)
def test_a_mistake_found_while_the_body_is_built_is_logged_once(
    caplog: pytest.LogCaptureFixture, column: str, code: str, event: str
):
    """SPEC-E13.1 for the refusals of this backend's own: a value its number column cannot read, and a list of
    exact values on analysed text, which has no whole string to match. The value is in no record."""
    params = request(*NAMES, column_search={column: {'multi_values': ['MARKER']}})
    with caplog.at_level(logging.DEBUG):
        refused(table.fetch_sync(params, Client()), code)
    assert [(record.name, record.levelname, record.msg) for record in caplog.records] == [
        ('datatables_backend', 'WARNING', event)
    ]
    assert 'MARKER' not in repr([vars(record) for record in caplog.records])


# -- searches ---


def _in_column(word: str) -> list[dict[str, Any]]:
    """What one word asks of each searched column of NAMES, in column order: id (keyword), title (text+keyword), body
    (text), author (text+keyword)."""

    def wildcard(field: str) -> dict[str, Any]:
        return {'wildcard': {field: {'value': f'*{word}*', 'case_insensitive': True}}}

    def both(field: str) -> dict[str, Any]:
        return {'bool': {'should': [{'match_phrase': {field: word}}, wildcard(f'{field}.keyword')],
                         'minimum_should_match': 1}}  # fmt: skip

    return [wildcard('id'), both('title'), {'match_phrase': {'body': word}}, both('meta.author')]


def test_a_negated_word_of_the_global_search_is_excluded_from_every_searched_column():
    """SPEC-F5.1, SPEC-F5.2: a `must_not` keeps a document without the field, so a missing value passes."""
    client = Client()
    table.fetch_sync(request(*NAMES, search={'value': '!pipe'}), client)
    assert query(client) == [{'bool': {'must': [{'bool': {'must_not': [clause]}} for clause in _in_column('pipe')]}}]


def test_a_column_search_holds_its_words_and_negations_as_plain_text_clauses():
    """SPEC-F2.1, SPEC-F8.1: no query syntax is read; `-x` and `OR` are words."""
    client, params = Client(), request(*NAMES)
    params.columns[2].search.value = 'granny !green -x'
    table.fetch_sync(params, client)
    assert query(client) == [
        {
            'bool': {
                'must': [
                    {'match_phrase': {'body': 'granny'}},
                    {'bool': {'must_not': [{'match_phrase': {'body': 'green'}}]}},
                    {'match_phrase': {'body': '-x'}},
                ]
            }
        }
    ]


def test_a_phrase_in_a_column_search_is_a_phrase_match_on_analysed_text():
    """SPEC-F4.1: a phrase is one word inside one column. `match` would find text holding only one of its words."""
    client, params = Client(), request(*NAMES)
    params.columns[1].search.value = '"red apple"'
    params.columns[2].search.value = '"granny smith" !"green tea"'
    table.fetch_sync(params, client)
    title, body = query(client)
    assert title == {
        'bool': {
            'should': [
                {'match_phrase': {'title': 'red apple'}},
                {'wildcard': {'title.keyword': {'value': '*red apple*', 'case_insensitive': True}}},
            ],
            'minimum_should_match': 1,
        }
    }
    assert body == {
        'bool': {
            'must': [
                {'match_phrase': {'body': 'granny smith'}},
                {'bool': {'must_not': [{'match_phrase': {'body': 'green tea'}}]}},
            ]
        }
    }


@pytest.mark.parametrize('column', ['pages', 'created'])
def test_a_column_search_with_no_word_sends_no_clause_on_a_number_or_a_date(column: str):
    """SPEC-F6.2: a number or a date is read as one value, so `"` would be refused as unreadable; it is no word."""
    client, params = Client(), request(*NAMES)
    params.columns[NAMES.index(column)].search.value = '"'
    assert table.fetch_sync(params, client).error is None
    assert query(client) == []


@pytest.mark.parametrize(
    ('column', 'term', 'code'), [('pages', '7', 'invalid_number'), ('created', '2024', 'invalid_date')]
)
def test_a_number_or_date_search_without_a_word_is_idle_and_one_with_a_word_is_read_whole(
    column: str, term: str, code: str
):
    """SPEC-F34.3: PRD-F6 decides the whole text before it is read as one term. Breaks if a text with no word is
    read as a number or a date (`!7` refused), or if a text holding `!` or quotes beside a word is read as that word
    alone (`!x 7` answered as `7`)."""

    def sent(value: str) -> tuple[DTResponse, Client]:
        client = Client()
        return table.fetch_sync(request(*NAMES, column_search={column: {'value': value}}), client), client

    (response, client), (_, without) = sent('!7'), sent('')
    assert response.error is None, response.error
    assert (client.searches, client.counts) == (without.searches, without.counts)
    for text in (f'!x {term}', f'"" {term}'):
        response, client = sent(text)
        refused(response, code)
        assert (client.searches, client.counts) == ([], [])


def test_a_date_column_search_reads_a_day_with_a_time_after_it_as_that_day():
    """SPEC-F34.1: the time comes after a space, and it is one date term all the same, not several (PRD-F34)."""
    client, params = Client(), request(*NAMES)
    params.columns[4].search.value = '2026-07-15 10:00:00'
    table.fetch_sync(params, client)
    assert query(client) == [{'range': {'created': {'gte': '2026-07-15', 'lt': '2026-07-16', 'format': 'strict_date'}}}]


def test_a_list_on_a_date_column_is_a_day_range_for_each_entry():
    """SPEC-F21.5, SPEC-F21.7: an entry is the day `=` matches, not a term (an instant on a date-time field), and an
    empty entry a missing field."""
    client = Client()
    table.fetch_sync(request(*NAMES, column_search={'created': {'multi_values': ['2026-07-16', '']}}), client)
    assert query(client) == [
        {
            'bool': {
                'should': [
                    {'range': {'created': {'gte': '2026-07-16', 'lt': '2026-07-17', 'format': 'strict_date'}}},
                    {'bool': {'must_not': [{'exists': {'field': 'created'}}]}},
                ],
                'minimum_should_match': 1,
            }
        }
    ]


MISSING = {'bool': {'must_not': [{'exists': {'field': 'pages'}}]}}


@pytest.mark.parametrize(
    ('column', 'values', 'expected'),
    [
        # Beside a value: a string folds, so each value is a term query; a number is one terms query
        (
            'title',
            ['', 'a'],
            {
                'bool': {
                    'should': [
                        {'term': {'title.keyword': {'value': 'a', 'case_insensitive': True}}},
                        {'bool': {'must_not': [{'exists': {'field': 'title'}}]}},
                    ],
                    'minimum_should_match': 1,
                }
            },
        ),
        ('pages', [' ', '7'], {'bool': {'should': [{'terms': {'pages': [7]}}, MISSING], 'minimum_should_match': 1}}),
        # Analysed text has no whole string for a list of values, but it can be missing
        ('body', [''], {'bool': {'must_not': [{'exists': {'field': 'body'}}]}}),
    ],
)
def test_an_empty_entry_of_a_list_is_a_missing_field(column: str, values: list[str], expected: dict[str, Any]):
    """SPEC-F20.2, SPEC-F33.4, SPEC-F33.5: the entry is never sent as a value. On a number column white
    space is such an entry too."""
    client = Client()
    table.fetch_sync(request(*NAMES, column_search={column: {'multi_values': values}}), client)
    assert query(client) == [expected]


@pytest.mark.parametrize(
    'unfolded',
    [
        Column('secret', search_value=str.upper),  # PRD-F9, PRD-D15, PRD-D18: the transformed value, as stored
        Column('secret', fold_case=False),  # SPEC-D21.2: the value as sent
    ],
)
def test_an_exact_match_on_a_column_the_library_does_not_fold_is_sent_plain(unfolded: Column):
    """A `term` without `case_insensitive`, and a list as one `terms` query: the field's mapping decides."""
    plain = SearchTable('docs', [IndexColumn('id', text='keyword'), unfolded], unique_key='id')
    client = Client()
    plain.fetch_sync(request('id', 'secret', **criterion('secret', '=', 'A')), client)
    plain.fetch_sync(request('id', 'secret', column_search={'secret': {'multi_values': ['A', 'B']}}), client)
    assert [search['query']['bool']['must'] for search in client.searches] == [
        [{'term': {'secret.keyword': 'A'}}],
        [{'terms': {'secret.keyword': ['A', 'B']}}],
    ]


@pytest.mark.parametrize(
    ('condition', 'value', 'pattern'),
    [
        ('contains', 'ä.b', '.*ä\\.b.*'),
        ('starts', 'b.ä', 'b\\.ä.*'),  # the letter is not the first character
        ('ends', 'B.Ä', '.*B\\.Ä'),  # nor a small one
        ('=', f'ä{chr(0xFF08)}', f'ä\\{chr(0xFF08)}'),  # a fullwidth bracket, which a normalizer can make a `(`
        ('=', chr(0x24D3), chr(0x24D3)),  # a circled d, cased and no letter: a normalizer can make it `d`, and `\d`
        # Whitespace, here a space and an ideographic space: a normalizer that trims would leave a backslash at the
        # end of the pattern
        ('=', f'ä {chr(0x3000)}', f'ä {chr(0x3000)}'),
    ],
)
def test_a_value_with_a_cased_letter_outside_ascii_is_a_regexp_in_any_case(condition: str, value: str, pattern: str):
    """PRD-F9 (SPEC-F9.13): `case_insensitive` folds such a letter on a `regexp` query only. Every character of the
    value that is neither alphanumeric, nor cased, nor whitespace gets a backslash, and the pattern is open where
    the condition is."""
    client = Client()
    table.fetch_sync(request(*NAMES, **criterion('id', condition, value)), client)
    assert query(client) == [{'regexp': {'id': {'value': pattern, 'case_insensitive': True}}}]


def test_a_negated_starts_is_anchored_at_the_start():
    """SPEC-F9.13: `!starts` is the query of `starts` in a `must_not`, open at its end and not at its start."""
    client = Client()
    table.fetch_sync(request(*NAMES, **criterion('id', '!starts', 'ä')), client)
    assert query(client) == [{'bool': {'must_not': [{'regexp': {'id': {'value': 'ä.*', 'case_insensitive': True}}}]}}]


def test_each_value_of_a_list_gets_the_query_its_letters_ask_for():
    """SPEC-F9.13: a list is one query for each value, a `regexp` for the value with a cased letter outside ASCII."""
    client = Client()
    table.fetch_sync(request(*NAMES, column_search={'id': {'multi_values': ['ä', 'b']}}), client)
    (clause,) = query(client)
    assert clause['bool']['should'] == [
        {'regexp': {'id': {'value': 'ä', 'case_insensitive': True}}},
        {'term': {'id': {'value': 'b', 'case_insensitive': True}}},
    ]


def test_the_longest_value_a_request_may_hold_is_a_regexp():
    """SPEC-F9.18: a value of 255 code points, the longest any value may be (PRD-F14) and the longest measured as a
    `regexp` on every server, is one. The letter here is outside the BMP and the rest are dots, each of which gets
    a backslash: the length is the value's, in code points, not the pattern's. A longer value never gets here: it is
    refused as `value_too_long`."""
    deseret, client = chr(0x10400), Client()
    table.fetch_sync(request(*NAMES, **criterion('id', '=', deseret + '.' * 254)), client)
    assert query(client) == [{'regexp': {'id': {'value': deseret + '\\.' * 254, 'case_insensitive': True}}}]


FULLWIDTH_MARKS = [chr(0xFF0A), chr(0xFF1F), chr(0xFF3C)]  # the fullwidth asterisk, question mark and backslash


@pytest.mark.parametrize('mark', FULLWIDTH_MARKS)
def test_a_search_for_a_fullwidth_mark_is_a_regexp_with_the_mark_behind_a_backslash(mark: str):
    """SPEC-F9.19: a normalizer can make the mark an ASCII `*`, `?` or `\\`, which a `wildcard` query then reads as
    its own syntax: the search finds too much, or misses its own row. A row turns red when its mark is sent in a
    `wildcard` again."""
    client = Client()
    table.fetch_sync(request(*NAMES, **criterion('id', 'contains', f'a{mark}b')), client)
    assert query(client) == [{'regexp': {'id': {'value': f'.*a\\{mark}b.*', 'case_insensitive': True}}}]


def test_a_fullwidth_mark_changes_no_exact_match():
    """SPEC-F9.19: an exact match is a `term`, which the server compares as a whole, so it stays one."""
    client, mark = Client(), FULLWIDTH_MARKS[0]
    table.fetch_sync(request(*NAMES, **criterion('id', '=', f'a{mark}b')), client)
    assert query(client) == [{'term': {'id': {'value': f'a{mark}b', 'case_insensitive': True}}}]


def test_a_value_outside_ascii_without_case_keeps_its_query():
    """SPEC-F9.14: a script without case has nothing to fold, so the value is sent as before."""
    client = Client()
    table.fetch_sync(request(*NAMES, **criterion('id', '=', '日本.')), client)
    assert query(client) == [{'term': {'id': {'value': '日本.', 'case_insensitive': True}}}]


@pytest.mark.parametrize(
    ('unfolded', 'condition', 'clause'),
    [
        (Column('secret', fold_case=False), '=', {'term': {'secret.keyword': 'ä'}}),  # PRD-D21: exact matches only
        (
            Column('secret', fold_case=False),  # its searches fold as on any column
            'ends',
            {'regexp': {'secret.keyword': {'value': '.*ä', 'case_insensitive': True}}},
        ),
    ],
)
def test_a_cased_letter_outside_ascii_on_a_column_the_library_does_not_fold(
    unfolded: Column, condition: str, clause: dict[str, Any]
):
    """SPEC-F9.15: the regexp is for the library's own fold. An exact match on a `fold_case=False` column is sent
    as before, and a search on it folds."""
    plain = SearchTable('docs', [IndexColumn('id', text='keyword'), unfolded], unique_key='id')
    client = Client()
    plain.fetch_sync(request('id', 'secret', **criterion('secret', condition, 'ä')), client)
    assert client.searches[-1]['query']['bool']['must'] == [clause]


# -- SearchBuilder ---


@pytest.mark.parametrize(
    ('column', 'condition', 'values', 'clause'),
    [
        ('id', '=', ['A1'], {'term': {'id': {'value': 'A1', 'case_insensitive': True}}}),  # SPEC-F9.2
        ('title', '!=', ['T'], {'bool': {'must_not': [{'term': {'title.keyword': {'value': 'T', 'case_insensitive': True}}}]}}),
    ],
)  # fmt: skip
def test_searchbuilder_criteria(column: str, condition: str, values: list[str], clause: dict[str, Any]):
    """SPEC-D13.1: an exact match on a text+keyword column is on `<field>.keyword`, on a keyword column on its field."""
    client = Client()
    table.fetch_sync(request(*NAMES, **criterion(column, condition, *values)), client)
    assert query(client) == [clause]


def test_an_undeclared_field_is_refused_before_anything_is_sent():
    """SPEC-D6.2: a criterion on an undeclared name is refused, and nothing reaches the cluster."""
    client = Client()
    refused(table.fetch_sync(request(*NAMES, **criterion('secret', '=', 'x')), client))
    refused(table.fetch_sync(request(*NAMES, **criterion('title.keyword', '=', 'x')), client))
    assert client.searches == []


# -- the bounds of PRD-F38 ---


def answered_or_refused(params: DTParams, *, answered: bool) -> None:
    """A request within the bounds is sent; one beyond them is refused as `invalid_filter` before anything is."""
    client = Client()
    response = table.fetch_sync(params, client)
    if answered:
        assert response.error is None
        assert client.searches
    else:
        refused(response)
        assert client.searches == []


@pytest.mark.parametrize(
    ('tree', 'answered'),
    [
        (nested(8, 'title'), True),
        (nested(9, 'title'), False),
        (flat(64, 'title'), True),
        (flat(65, 'title'), False),
        # The 65 are counted over the whole tree, and 9 groups side by side are not 9 deep
        ({'logic': 'AND', 'criteria': [flat(32, 'title'), flat(33, 'title')]}, False),
        ({'logic': 'AND', 'criteria': [flat(1, 'title') for _ in range(9)]}, True),
    ],
    ids=['depth-8', 'depth-9', 'criteria-64', 'criteria-65', 'criteria-65-in-two-groups', 'nine-groups-side-by-side'],
)
def test_a_search_builder_tree_is_answered_up_to_8_groups_deep_and_64_criteria(tree: dict[str, Any], *, answered: bool):
    """SPEC-F38.1, SPEC-F38.2: a tree past a bound is refused, and nothing is sent."""
    answered_or_refused(request(*NAMES, searchBuilder=tree), answered=answered)


@pytest.mark.parametrize('form', ['multi_values', 'regex'])
@pytest.mark.parametrize(('count', 'answered'), [(256, True), (257, False)])
def test_a_list_of_exact_values_is_answered_up_to_256_values(form: str, count: int, *, answered: bool):
    """SPEC-F38.3: a `multi_values` list and a categorical regex are each bounded at 256 values."""
    values = exact_values(count, 'Ann')
    search = {'multi_values': values} if form == 'multi_values' else {'value': f'^({"|".join(values)})$', 'regex': True}
    answered_or_refused(request(*NAMES, column_search={'title': search}), answered=answered)


@pytest.mark.parametrize(('last', 'answered'), [(0, True), (1, False)], ids=['512', '513'])
def test_the_lists_of_one_request_together_hold_at_most_512_values(last: int, *, answered: bool):
    """SPEC-F38.5: two lists of 256 are answered; one more value in a third list is not."""
    lists = {'title': exact_values(256, 'Ann'), 'author': exact_values(256, 'Ann'), 'id': ['a1'] if last else []}
    searches = {name: {'multi_values': values} for name, values in lists.items() if values}
    answered_or_refused(request(*NAMES, column_search=searches), answered=answered)


@pytest.mark.parametrize(
    ('searched', 'unsearched', 'answered'),
    [(64, 0, True), (65, 0, False), (64, 50, True)],
    ids=['64', '65', '64-among-columns-with-no-search'],
)
def test_a_request_searches_at_most_64_columns(searched: int, unsearched: int, *, answered: bool):
    """SPEC-F38.6: the count is of column searches that hold a value, so the column `title` named 65 times is 65,
    and a column without a search, or with one of spaces only (PRD-F6), is none."""
    names = ('title',) * searched + ('id', 'author') * unsearched
    searches = {'title': {'value': 'Ann'}, 'author': {'value': '  '}}
    answered_or_refused(request(*names, column_search=searches), answered=answered)


def test_the_global_search_covers_a_column_once_however_often_the_request_names_it():
    """SPEC-F38.7: the column `title` named 1,000 times asks the cluster the same as when it is named once."""
    once, many = Client(), Client()
    table.fetch_sync(request('title', search={'value': 'ann'}), once)
    table.fetch_sync(request(*('title',) * 1000, search={'value': 'ann'}), many)
    same = many.searches == once.searches  # not asserted inline: a failure would print a thousand clauses
    assert same


def test_the_global_search_covers_a_column_named_twice_if_either_entry_is_searchable():
    """SPEC-F38.8: `id` is marked not searchable, so `title` named twice is searched as when it is named once if
    either of its entries is searchable, in either order; with both marked not searchable no column is left (PRD-F13)
    and nothing is sent. Catches the first or the last entry of the name deciding alone."""

    def named_twice(*searchable: bool) -> DTParams:
        columns = [
            {'data': name, 'name': '', 'searchable': flag, 'orderable': True, 'search': {'value': '', 'regex': False}}
            for name, flag in (('id', False), *(('title', flag) for flag in searchable))
        ]
        return parse_request({'draw': 1, 'start': 0, 'length': 10, 'columns': columns, 'search': {'value': 'ann'}})

    once = Client()
    table.fetch_sync(request('id', 'title', flags={'id': {'searchable': False}}, search={'value': 'ann'}), once)
    assert 'title' in json.dumps(query(once))
    for searchable in [(True, False), (False, True)]:
        twice = Client()
        assert table.fetch_sync(named_twice(*searchable), twice).error is None
        assert twice.searches == once.searches
    neither = Client()
    refused(table.fetch_sync(named_twice(False, False), neither))
    assert neither.searches == neither.counts == []


# -- sorting ---


def test_sorting_uses_the_whole_string_and_puts_a_missing_value_lowest():
    """SPEC-O7.1, SPEC-O2.1, SPEC-D13.1: a text+keyword column sorts on `<field>.keyword` and a keyword one on its
    field; left to itself OpenSearch puts a document without the field last in either direction."""
    client = Client()
    order = [{'column': 1, 'dir': 'desc'}, {'column': 3, 'dir': 'asc'}]
    table.fetch_sync(request(*NAMES, order=order), client)
    assert client.searches[0]['sort'] == [
        {'title.keyword': {'order': 'desc', 'missing': '_last'}},
        {'pages': {'order': 'asc', 'missing': '_first'}},
        {'id': {'order': 'desc', 'missing': '_last'}},  # PRD-O3: the key follows the first entry
    ]


def test_an_order_on_analysed_text_is_refused_or_skipped_as_the_request_marks_it():
    """SPEC-O5.1: `body` has no whole string to sort by, and OpenSearch fails a search that sorts by analysed text."""
    client = Client()
    order = [{'column': 2, 'dir': 'asc'}, {'column': 3, 'dir': 'asc'}]
    refused(table.fetch_sync(request(*NAMES, order=order), client))
    assert client.searches == []
    table.fetch_sync(request(*NAMES, flags={'body': {'orderable': False}}, order=order), client)
    assert client.searches[0]['sort'] == [
        {'pages': {'order': 'asc', 'missing': '_first'}},
        {'id': {'order': 'asc', 'missing': '_first'}},
    ]


def test_analysed_text_declared_orderable_is_a_schema_error():
    """SPEC-O5.2. Declared unorderable, which it is anyway, it is no mistake."""
    with pytest.raises(SchemaError, match='body'):
        SearchTable(
            'docs',
            [IndexColumn('id', text='keyword'), IndexColumn('body', text='text', orderable=True)],
            unique_key='id',
        )
    SearchTable(
        'docs', [IndexColumn('id', text='keyword'), IndexColumn('body', text='text', orderable=False)], unique_key='id'
    )


def test_an_order_on_a_column_declared_unorderable_is_refused():
    """SPEC-O1.2, SPEC-D5.1: the declaration decides on this backend too, for a column the index could sort by."""
    fixed = SearchTable('docs', [IndexColumn('id', text='keyword', orderable=False)], unique_key='id')
    client = Client()
    refused(fixed.fetch_sync(request('id', order=[{'column': 0, 'dir': 'asc'}]), client))
    assert client.searches == []


# -- the declared unique key, the tie-breaker (PRD-D11, PRD-O3, PRD-O4) ---


def keyed(key: str | tuple[str, ...], columns: list[Column] | None = None) -> SearchTable:
    return SearchTable('docs', columns or COLUMNS, unique_key=key)


ID_ASC = {'id': {'order': 'asc', 'missing': '_first'}}
ID_DESC = {'id': {'order': 'desc', 'missing': '_last'}}
TITLE_DESC = {'title.keyword': {'order': 'desc', 'missing': '_last'}}
PAGES_ASC = {'pages': {'order': 'asc', 'missing': '_first'}}


def test_a_request_without_an_order_is_sent_in_the_keys_ascending_order():
    """SPEC-O4.1, SPEC-O3.4: without a sort OpenSearch returns ties in no stable order."""
    client = Client()
    keyed('id').fetch_sync(request(*NAMES), client)
    assert client.searches[0]['sort'] == [ID_ASC]


@pytest.mark.parametrize(
    ('order', 'flags', 'expected'),
    [
        # the key follows the first entry, not the last
        ([{'column': 1, 'dir': 'desc'}, {'column': 3, 'dir': 'asc'}], {}, [TITLE_DESC, PAGES_ASC, ID_DESC]),
        # a skipped entry does not decide the direction: `body` is unorderable, so `title` is the first applied one
        (
            [{'column': 2, 'dir': 'asc'}, {'column': 1, 'dir': 'desc'}],
            {'body': {'orderable': False}},
            [TITLE_DESC, ID_DESC],
        ),
    ],
)
def test_the_key_comes_last_in_the_direction_of_the_first_applied_entry(
    order: list[dict[str, Any]], flags: dict[str, Any], expected: list[dict[str, Any]]
):
    """SPEC-O3.4, SPEC-O3.3: the exact reverse of the ascending order holds only if the key follows."""
    client = Client()
    keyed('id').fetch_sync(request(*NAMES, flags=flags, order=order), client)
    assert client.searches[0]['sort'] == expected


def test_a_key_of_several_columns_follows_the_order_one_after_the_other():
    """SPEC-D11.4: an index over an association has no single unique field. `title` is the user's own order here,
    so it is named once."""
    client = Client()
    keyed(('title', 'id')).fetch_sync(request(*NAMES, order=[{'column': 1, 'dir': 'desc'}]), client)
    assert client.searches[0]['sort'] == [TITLE_DESC, ID_DESC]
    keyed(('author', 'id')).fetch_sync(request(*NAMES, order=[{'column': 3, 'dir': 'desc'}]), client)
    assert client.searches[1]['sort'][1:] == [
        {'meta.author.keyword': {'order': 'desc', 'missing': '_last'}},
        ID_DESC,
    ]


def test_a_key_declared_unorderable_still_breaks_ties_but_cannot_be_ordered_by():
    """SPEC-D11.1: `orderable=False` keeps the key out of the client's controls, not out of the tie-breaker."""
    hidden = keyed('id', [IndexColumn('id', text='keyword', orderable=False), Column('pages', type='number')])
    client = Client()
    hidden.fetch_sync(request('id', 'pages', order=[{'column': 1, 'dir': 'asc'}]), client)
    assert client.searches[0]['sort'] == [PAGES_ASC, ID_ASC]
    assert client.searches[0]['_source'] == ['id', 'pages']  # a declared column, so its value is in every row
    refused(hidden.fetch_sync(request('id', 'pages', order=[{'column': 0, 'dir': 'asc'}]), client))
    assert len(client.searches) == 1


def test_an_index_table_without_a_unique_key_is_a_schema_error():
    """SPEC-D11.1: no index has a primary key, so without one the tie-breaker would be whatever OpenSearch does."""
    with pytest.raises(SchemaError) as raised:
        SearchTable('docs', COLUMNS)
    assert 'unique_key=' in str(raised.value)


@pytest.mark.parametrize(
    ('key', 'text'),
    [
        ('nope', 'nope'),  # not a declared column
        (('id', 'nope'), 'nope'),
        ('body', 'body'),  # analysed text only: there is no whole string to sort by
        ((), 'at least one'),
        ('_id', '_id'),  # SPEC-D11.5: `_id` is no reserved key name, and no other name is read as a field
    ],
)
def test_a_unique_key_that_cannot_break_ties_is_a_schema_error(key: str | tuple[str, ...], text: str):
    """SPEC-D11.1: the key must name a declared column the index can sort by."""
    with pytest.raises(SchemaError, match=text) as raised:
        keyed(key)
    assert 'unique_key' in str(raised.value)


# -- the last pages, by a reversed sort (PRD-C10, PRD-C13, issue #74) ---


def doc(number: int) -> dict[str, Any]:
    return {'_id': str(number), '_source': {'id': str(number)}}


class Docs(Counting):
    """An index of `count` documents numbered 1 to `count`, which answers a search by the direction of the `id` key,
    `from` and `size` as the cluster does: the arithmetic of a page can be tried against what it should show."""

    def __init__(self, count: int):
        super().__init__(total=count, filtered=count)
        self.docs = list(range(1, count + 1))

    def search(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        self.searches.append(body)
        key = next(entry['id'] for entry in body['sort'] if 'id' in entry)  # a table of the default order: the key
        ordered = self.docs if key['order'] == 'asc' else self.docs[::-1]
        page = ordered[body['from'] : body['from'] + body['size']]
        hits = {'total': {'value': len(self.docs), 'relation': 'eq'}, 'hits': [doc(number) for number in page]}
        return {'timed_out': False, '_shards': SHARDS, 'hits': hits}


class AsyncDocs(Docs):
    async def search(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:  # type: ignore[override]
        return Docs.search(self, index=index, body=body)

    async def count(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:  # type: ignore[override]
        return Counting.count(self, index=index, body=body)


def row_ids(response: DTResponse) -> list[str]:
    return [row['id'] for row in response.data]


@pytest.mark.parametrize(
    ('start', 'length', 'from_', 'size'),
    [
        (7, 3, 0, 3),  # the first reversed page: start = rows - window
        (7, 2, 1, 2),  # a shorter page: from is not clamped
        (8, 3, 0, 2),  # a page cut short by the end
        (9, 3, 0, 1),  # the last row
        (9, 1, 0, 1),
    ],
)
def test_a_reversed_page_asks_for_the_rows_from_the_end_and_is_shown_in_ascending_order(
    start: int, length: int, from_: int, size: int
):
    """SPEC-C10.5: `from` is `max(0, F - start - length)` and `size` is `min(length, F - start)`, so that from + size
    is F - start, within the window; the key is the only entry of the default order, flipped, and the counts that
    decided the page are the answer's."""
    client = Docs(10)
    response = bounded(3).fetch_sync(request(*NAMES, start=start, length=length), client)
    assert (response.error_code, response.recordsTotal, response.recordsFiltered) == (None, 10, 10)
    assert len(client.searches) == 1
    assert (client.searches[0]['from'], client.searches[0]['size']) == (from_, size)
    assert client.searches[0]['sort'] == [{'id': {'order': 'desc', 'missing': '_last', 'mode': 'min'}}]
    assert row_ids(response) == [str(number) for number in range(start + 1, start + size + 1)]
    assert client.counts == [EVERYTHING]  # the one count, the total being the filtered count (PRD-C8)


def test_the_last_ordinary_page_is_searched_without_a_count():
    """SPEC-C10.1, SPEC-C11.1: start + length = W is ordinary, and the reverse is used only beyond it."""
    client = Docs(10)
    response = bounded(3).fetch_sync(request(*NAMES, start=1, length=2), client)
    assert row_ids(response) == ['2', '3']
    assert (client.searches[0]['from'], client.searches[0]['size']) == (1, 2)
    assert client.counts == []


def test_a_reversed_page_flips_every_entry_of_the_order_with_the_mode_each_one_has():
    """SPEC-C10.6: title desc, pages asc and the key (which follows the first entry, so desc) become title asc, pages
    desc and the key asc. A missing value stays the lowest, and each entry keeps the `mode` of its original direction
    (the minimum for `asc`, the maximum for `desc`): a field of several values sorts by another one in each
    direction, so a flip without it would not reverse the order it flips."""
    client = Docs(10)
    order = [{'column': 1, 'dir': 'desc'}, {'column': 3, 'dir': 'asc'}]
    bounded(3).fetch_sync(request(*NAMES, start=7, length=3, order=order), client)
    assert len(client.searches) == 1
    assert client.searches[0]['sort'] == [
        {'title.keyword': {'order': 'asc', 'missing': '_first', 'mode': 'max'}},
        {'pages': {'order': 'desc', 'missing': '_last', 'mode': 'min'}},
        {'id': {'order': 'asc', 'missing': '_first', 'mode': 'max'}},
    ]


def test_a_reversed_page_answers_the_counts_that_decided_it_though_the_index_has_changed():
    """SPEC-C13.1: the count says 10, the reversed search finds 11 (an index that grew in between): the rows are what
    it found, in ascending order, with no error, no point in time (the client has no `create_pit`), and the answer
    carries the counts that decided the page."""
    client = Counting(total=10, filtered=10, hits=[doc(11), doc(10)])
    response = bounded(3).fetch_sync(request(*NAMES, start=8, length=3), client)
    assert (response.error_code, row_ids(response)) == (None, ['10', '11'])
    assert (response.recordsTotal, response.recordsFiltered) == (10, 10)


async def test_the_async_fetch_serves_a_reversed_page_too():
    """SPEC-C10.2, SPEC-C13.1 through `fetch`, which builds its page apart from `fetch_sync`: the rows come in
    ascending order, and the answer carries the counts that decided the page (the search itself says 7)."""
    client = AsyncCounting(total=10, filtered=10, hits=[doc(10), doc(9)])
    response = await bounded(3).fetch(request(*NAMES, start=8, length=3), client)
    assert row_ids(response) == ['9', '10']
    assert (response.recordsTotal, response.recordsFiltered) == (10, 10)
    assert (client.searches[0]['from'], client.searches[0]['size']) == (0, 2)


PARTIAL = {'total': 2, 'successful': 1, 'failed': 1}


def test_a_partial_answer_to_a_reversed_page_raises():
    """SPEC-E9.1: a page read from some of the shards only is no answer, the reversed one included."""
    client = Counting(total=10, filtered=10, hits=[doc(10)], _shards=PARTIAL)
    with pytest.raises(PartialResultError):
        bounded(3).fetch_sync(request(*NAMES, start=9, length=1), client)


async def test_a_partial_answer_to_an_async_reversed_page_raises():
    """SPEC-E9.1 through `fetch`."""
    client = AsyncCounting(total=10, filtered=10, hits=[doc(10)], _shards=PARTIAL)
    with pytest.raises(PartialResultError):
        await bounded(3).fetch(request(*NAMES, start=9, length=1), client)


# -- the document id: shown, never searched, sorted or used as the key (PRD-D11, issue #96) ---
#
# OpenSearch deprecates sorting on `_id` (field data): its log says so on 3.10.0 and on 2.19.7, so it is no key. A
# column declared for it reads `hit['_id']`, which needs no field data.

DOC_COLUMNS = [Column('_id'), Column('pages', type='number')]
ID_ADVICE = 'Copy the id into a `keyword` field of the documents and name that column as the key.'


def doc_hit(identifier: str, pages: int) -> dict[str, Any]:
    return {'_id': identifier, '_source': {'pages': pages}}  # a document's `_id` is not in its `_source`


@pytest.mark.parametrize(
    'columns',
    [
        [Column('_id'), Column('pages', type='number')],
        [Column('docid', field='_id'), Column('pages', type='number')],  # a column reads the id by its field
    ],
    ids=['by-name', 'by-field'],
)
def test_a_column_of_the_document_id_cannot_be_the_unique_key(columns: list[Column]):
    """SPEC-D11.5: a sort on it is the deprecated one, so the error says what to do instead."""
    with pytest.raises(SchemaError) as raised:
        keyed(columns[0].name, columns)
    assert str(raised.value) == f'OpenSearch deprecates sorting on `_id` (field data). {ID_ADVICE}'


def test_a_column_declared_for_the_document_id_reads_the_hits_id():
    """SPEC-D11.6: the value comes from `hit['_id']`, not from `_source`, where it would always be missing; `_source`
    asks for the other columns only."""
    client = Client(hits=[doc_hit('x7', 3)])
    response = keyed('pages', DOC_COLUMNS).fetch_sync(request('_id', 'pages'), client)
    assert response.data == [{'_id': 'x7', 'pages': 3}]
    assert client.searches[0]['_source'] == ['pages']
    assert client.searches[0]['sort'] == [PAGES_ASC]  # the key is the one declared; `_id` is not sorted on


def test_an_order_on_a_column_of_the_document_id_is_refused_or_skipped_as_the_request_marks_it():
    """SPEC-D11.6: held unorderable, so the plan refuses it as for any column that cannot be sorted by, and skips it
    where the request itself marks it unorderable (DataTables' default order on the first column)."""
    client = Client()
    order = [{'column': 0, 'dir': 'asc'}]
    refused(keyed('pages', DOC_COLUMNS).fetch_sync(request('_id', 'pages', order=order), client))
    assert client.searches == []
    keyed('pages', DOC_COLUMNS).fetch_sync(
        request('_id', 'pages', flags={'_id': {'orderable': False}}, order=order), client
    )
    assert client.searches[0]['sort'] == [PAGES_ASC]


def test_a_column_of_the_document_id_declared_orderable_is_a_schema_error():
    """SPEC-D11.6: as for analysed text, an explicit `orderable=True` is refused; `None` and `False` are not."""
    with pytest.raises(SchemaError) as raised:
        keyed('pages', [Column('_id', orderable=True), Column('pages', type='number')])
    assert str(raised.value) == (
        'The column "_id" shows the document id and cannot be orderable: OpenSearch deprecates sorting on `_id` '
        '(field data). Order by a `keyword` field instead.'
    )
    keyed('pages', [Column('_id', orderable=False), Column('pages', type='number')])


def test_the_document_id_is_not_searchable_and_the_global_search_skips_it():
    """SPEC-D11.6: a column search or a criterion on it is refused by the rule that refuses any column declared
    unsearchable (no new code), and the global search leaves it out; there is no text to match in an `_id`."""
    columns = [Column('_id'), IndexColumn('title')]
    client = Client()
    text = 'The column "_id" cannot be filtered. Remove the filter on it.'
    column_search = request('_id', 'title', column_search={'_id': {'value': 'x'}})
    assert refused(keyed('title', columns).fetch_sync(column_search, client)) == text
    assert (
        refused(keyed('title', columns).fetch_sync(request('_id', 'title', **criterion('_id', '=', 'x')), client))
        == text
    )
    assert client.searches == []
    keyed('title', columns).fetch_sync(request('_id', 'title', search={'value': 'abc'}), client)
    assert '_id' not in json.dumps(client.searches[0]['query'])


def test_a_column_that_reads_the_document_id_by_its_field_is_held_the_same_way():
    """SPEC-D11.6: it is the field that says the column is the id, not the name; so a column `docid` over `_id`
    shows the hit's id and is neither searched nor ordered by."""
    columns = [Column('docid', field='_id'), IndexColumn('title')]
    client = Client(hits=[{'_id': 'x7', '_source': {'title': 'T'}}])
    response = keyed('title', columns).fetch_sync(request('docid', 'title'), client)
    assert response.data == [{'docid': 'x7', 'title': 'T'}]
    filtered = request('docid', 'title', column_search={'docid': {'value': 'x'}})
    assert refused(keyed('title', columns).fetch_sync(filtered, client)).startswith('The column "docid" cannot be')
    ordered = request('docid', 'title', order=[{'column': 0, 'dir': 'asc'}])
    refused(keyed('title', columns).fetch_sync(ordered, client))
    assert len(client.searches) == 1


# -- a boolean column ---
#
# What OpenSearch makes of these bodies is measured in test_opensearch_live.py.

FLAG_NAMES = ('id', 'title', 'active')
flags = SearchTable(
    'docs',
    [IndexColumn('id', text='keyword'), IndexColumn('title'), Column('active', type='boolean')],
    unique_key='id',
)


def clauses(client: Client) -> list[dict[str, Any]]:
    """The clauses of the last search of a table without a base filter."""
    return client.searches[-1]['query']['bool']['must']


def test_a_column_search_on_a_boolean_column_is_a_terms_query_of_booleans():
    """SPEC-F44.3: the field itself, not a keyword subfield, and JSON booleans."""
    client, params = Client(), request(*FLAG_NAMES)
    params.columns[2].search.value = '^(true|0)$'
    params.columns[2].search.regex = True
    flags.fetch_sync(params, client)
    assert clauses(client) == [{'terms': {'active': [True, False]}}]


@pytest.mark.parametrize('kind', ['number', 'date', 'boolean'])
def test_a_search_value_hook_on_a_column_that_is_no_string_is_a_schema_error(kind: Any):
    """SPEC-D14.1: the hook transforms text the user types. The message names the declared column and the fix."""
    with pytest.raises(SchemaError) as raised:
        SearchTable(
            'docs', [IndexColumn('id', text='keyword'), Column('held', type=kind, search_value=str)], unique_key='id'
        )
    assert str(raised.value) == (
        f'The column "held" has a search_value hook but is a {kind} column. Only a string column takes one: remove '
        'the hook.'
    )


def test_two_columns_with_one_name_are_a_schema_error():
    """SPEC-D1.2: the name keys the request and each row, so the second column would silently replace the first."""
    with pytest.raises(SchemaError) as raised:
        SearchTable(
            'docs',
            [IndexColumn('id', text='keyword'), IndexColumn('title'), Column('title', field='name')],
            unique_key='id',
        )
    assert str(raised.value) == (
        'Two columns are declared with the name "title". Give each its own name: Column(name, field=...) reads one '
        'field under another name.'
    )


def test_a_table_without_columns_is_a_schema_error():
    """SPEC-D1.2: a table that has none answers every request with empty rows."""
    with pytest.raises(SchemaError) as raised:
        SearchTable('docs', [])
    assert str(raised.value) == 'No column is declared. Declare at least one Column.'


# -- a hooked column (PRD-D17, PRD-D18) ---


def stored(text: str) -> str:
    return f'enc:{text}'


def test_a_column_search_on_a_hooked_column_is_one_term_of_the_transformed_trimmed_text():
    """SPEC-D9.1, SPEC-D17.1, SPEC-D17.3: the keyword field of text+keyword (`.keyword`) is matched whole, with no
    grammar in the text: `!x` is no negation and the quotes are part of the value. One request gives the hook one
    text."""
    seen: list[str] = []

    def recorded(text: str) -> str:
        seen.append(text)
        return stored(text)

    hooked = SearchTable(
        'docs', [IndexColumn('id', text='keyword'), Column('secret', search_value=recorded)], unique_key='id'
    )
    client = Client()
    cases = ['Alpha', f' {chr(0xA0)}Alpha{chr(0x3000)}', '"Alpha"', '!x', 'a\nb']
    for text in cases:
        hooked.fetch_sync(request('id', 'secret', column_search={'secret': {'value': text}}), client)
    assert [search['query']['bool']['must'] for search in client.searches] == [
        [{'term': {'secret.keyword': stored(text.strip())}}] for text in cases
    ]
    assert seen == [text.strip() for text in cases]


def test_a_column_search_on_a_hooked_column_of_blank_text_sends_no_clause():
    """SPEC-F6.3 with PRD-D12's trimming: text of only White_Space beyond PRD-F7's five is blank too."""
    hooked = SearchTable(
        'docs', [IndexColumn('id', text='keyword'), Column('secret', search_value=stored)], unique_key='id'
    )
    client = Client()
    hooked.fetch_sync(request('id', 'secret', column_search={'secret': {'value': chr(0xA0)}}), client)
    assert client.searches[-1]['query'] == {'match_all': {}}


def recording_hook() -> tuple[list[str], Any]:
    seen: list[str] = []

    def recorded(text: str) -> str:
        seen.append(text)
        return stored(text)

    return seen, recorded


GLOBAL_CASES = ['Alpha', f' {chr(0xA0)}Alpha{chr(0x3000)}', '"Alpha"', '!Alpha', 'a\nb', 'red Alpha']


def test_the_global_search_on_a_hooked_column_is_one_term_of_the_transformed_trimmed_whole_text():
    """SPEC-D12.1, SPEC-D12.3, SPEC-D12.4, SPEC-D12.7: the whole text, trimmed, transformed once and matched whole on
    the keyword field, with no wildcard (a substring) and no phrase. It is the one clause when every searchable column
    is hooked, and `!Alpha` and `a` + LF + `b` are values like any other."""
    seen, recorded = recording_hook()
    hooked = SearchTable(
        'docs',
        [IndexColumn('id', text='keyword', searchable=False), Column('secret', search_value=recorded)],
        unique_key='id',
    )
    client = Client()
    for text in GLOBAL_CASES:
        hooked.fetch_sync(request('id', 'secret', search={'value': text}), client)
    assert [search['query']['bool']['must'] for search in client.searches] == [
        [{'term': {'secret.keyword': stored(text.strip())}}] for text in GLOBAL_CASES
    ]
    assert seen == [text.strip() for text in GLOBAL_CASES]


def test_the_global_search_adds_the_term_of_a_hooked_column_beside_the_words_of_the_others():
    """SPEC-D12.1, SPEC-D12.2: the words go to the other columns, each found in some column, and the hooked column
    adds the one term of the whole text, however many words it holds."""
    seen, recorded = recording_hook()
    hooked = SearchTable(
        'docs', [IndexColumn('id', text='keyword'), Column('secret', search_value=recorded)], unique_key='id'
    )
    client = Client()
    hooked.fetch_sync(request('id', 'secret', search={'value': 'red Alpha'}), client)
    (clause,) = client.searches[-1]['query']['bool']['must']
    words, term = clause['bool']['should']
    assert term == {'term': {'secret.keyword': stored('red Alpha')}}
    assert 'secret' not in json.dumps(words)  # the hooked column is no part of the words
    assert seen == ['red Alpha']


def test_searchbuilder_on_a_hooked_column_refuses_a_part_of_the_value_before_anything_is_sent():
    """SPEC-D18.2: a `wildcard` on a transformed value means nothing. `starts` stands for the six conditions, which
    one rule refuses in the plan (test_sql.py has each)."""
    hooked = SearchTable(
        'docs', [IndexColumn('id', text='keyword'), Column('secret', search_value=stored)], unique_key='id'
    )
    client = Client()
    response = hooked.fetch_sync(request('id', 'secret', **criterion('secret', 'starts', 'zzz')), client)
    assert refused(response) == (
        'The condition "starts" cannot be used on "secret", whose search value is transformed before it is '
        'compared. Choose =, !=, null or !null.'
    )
    assert client.searches == []


SEVENTEEN = ' '.join(f'q{number}' for number in range(17))  # distinct, so an echo of any word shows
HOOKED_NAMES = ('id', 'secret')
TWENTY_WORDS = ' '.join('abcdefghijklmnopqrst')


def hooked_table(*, other_columns: bool = False, hook: Any = stored) -> SearchTable:
    """`secret` hooked, beside a searchable `id` (a mixed table) or an `id` left out of every search."""
    return SearchTable(
        'docs',
        [IndexColumn('id', text='keyword', searchable=other_columns), Column('secret', search_value=hook)],
        unique_key='id',
    )


@pytest.mark.parametrize(
    ('mixed', 'body', 'code'),
    [
        (True, {'search': {'value': SEVENTEEN}}, 'too_many_words'),  # SPEC-D12.6: the cap holds beside another column
        (False, {'search': {'value': 'a' * 256}}, 'value_too_long'),  # SPEC-D12.5
        (False, {'search': {'value': ' ' * 125 + 'Alpha' + ' ' * 126}}, 'value_too_long'),  # SPEC-D12.8: the raw text
        (False, {'column_search': {'secret': {'value': 'a' * 256}}}, 'value_too_long'),  # SPEC-D17.2
    ],
)
def test_search_text_over_the_bounds_on_a_hooked_column_is_refused_before_anything_is_sent(
    mixed: bool, body: dict[str, Any], code: str
):
    """SPEC-D12.5, SPEC-D12.6, SPEC-D12.8, SPEC-D17.2: the word cap is lifted only where every searchable column is
    hooked, and the length is measured on the text as sent, spaces and all (5 characters once trimmed)."""
    client = Client()
    message = refused(hooked_table(other_columns=mixed).fetch_sync(request(*HOOKED_NAMES, **body), client), code)
    assert client.searches == []
    assert not any(sent in message for sent in ('aaa', 'Alpha', 'q0'))


@pytest.mark.parametrize('where', ['search', 'column_search'])
def test_twenty_words_on_a_hooked_column_are_one_value_and_are_answered(where: str):
    """SPEC-D12.5, SPEC-D17.2: no word cap, the whole text is the one term the hook makes of it."""
    body: dict[str, Any] = (
        {'search': {'value': TWENTY_WORDS}}
        if where == 'search'
        else {'column_search': {'secret': {'value': TWENTY_WORDS}}}
    )
    client = Client()
    assert hooked_table().fetch_sync(request(*HOOKED_NAMES, **body), client).error is None
    assert clauses(client) == [{'term': {'secret.keyword': stored(TWENTY_WORDS)}}]


@pytest.mark.parametrize('text', ['""', '!x', '!"', '"', '!', '   '])
def test_text_with_no_word_adds_nothing_to_a_hooked_column_and_the_hook_is_not_called(text: str):
    """SPEC-D12.9: nothing is asked of the hooked column, and the hook would turn `!x` into a value no row holds."""
    seen, recorded = recording_hook()
    client = Client()
    hooked_table(hook=recorded).fetch_sync(request(*HOOKED_NAMES, search={'value': text}), client)
    assert client.searches[-1]['query'] == {'match_all': {}}
    assert seen == []


@pytest.mark.parametrize('search', [{'multi_values': ['Alpha', 'Beta']}, {'value': '^(Alpha|Beta)$', 'regex': True}])
def test_a_list_of_values_on_a_hooked_column_is_one_terms_of_the_hooked_values(search: dict[str, Any]):
    """SPEC-D15.1: the hook is called once for each value, in order, and the list is sent as the hook made it."""
    seen, recorded = recording_hook()
    client = Client()
    hooked_table(hook=recorded).fetch_sync(request(*HOOKED_NAMES, column_search={'secret': search}), client)
    assert clauses(client) == [{'terms': {'secret.keyword': [stored('Alpha'), stored('Beta')]}}]
    assert seen == ['Alpha', 'Beta']


def test_an_empty_entry_on_a_hooked_column_is_a_missing_field_and_the_hook_is_not_called():
    """SPEC-D16.1: "empty" is about what is stored, so no value goes to the hook. On OpenSearch an empty string is no
    missing field, and the live test of PRD-F26 shows the rows."""
    seen, recorded = recording_hook()
    client = Client()
    hooked_table(hook=recorded).fetch_sync(
        request(*HOOKED_NAMES, column_search={'secret': {'multi_values': ['']}}), client
    )
    assert clauses(client) == [{'bool': {'must_not': [{'exists': {'field': 'secret'}}]}}]
    assert seen == []


@pytest.mark.parametrize(
    ('condition', 'values', 'clause'),
    [
        ('=', ['Alpha'], {'term': {'secret.keyword': stored('Alpha')}}),
        ('!=', ['Alpha'], {'bool': {'must_not': [{'term': {'secret.keyword': stored('Alpha')}}]}}),
        ('null', [], {'bool': {'must_not': [{'exists': {'field': 'secret'}}]}}),
        ('!null', [], {'exists': {'field': 'secret'}}),
    ],
)
def test_searchbuilder_on_a_hooked_column_sends_the_hooked_value_or_the_existence_test(
    condition: str, values: list[str], clause: dict[str, Any]
):
    """SPEC-D18.1: `=` and `!=` give the hook the value (a missing field passes `!=`, as `must_not` keeps it); `null`
    and `!null` test the field, an empty string being a stored value. Rows 5 and 6 of the statement differ only in what
    the cluster does with that field, which the live test of PRD-F26 shows."""
    seen, recorded = recording_hook()
    client = Client()
    hooked_table(hook=recorded).fetch_sync(request(*HOOKED_NAMES, **criterion('secret', condition, *values)), client)
    assert clauses(client) == [clause]
    assert seen == values


def test_a_column_search_and_the_global_search_are_both_in_the_one_must():
    """SPEC-F12.2: the global search is one more clause beside the column search, an AND."""
    column, search = {'title': {'value': 'apple'}}, {'value': 'green'}
    both = clauses_of(column_search=column, search=search)
    assert both == [*clauses_of(search=search), *clauses_of(column_search=column)]
    assert len(both) == 2


UNSEARCHABLE = SearchTable(
    'docs',
    [IndexColumn('id', text='keyword', searchable=False), IndexColumn('title', searchable=False)],
    unique_key='id',
)


def test_a_search_with_a_word_on_a_table_with_no_searchable_column_is_refused_and_nothing_is_sent():
    """SPEC-F13.1: every column is declared unsearchable, so `red` has nothing to match."""
    client = Client()
    refused(UNSEARCHABLE.fetch_sync(request('id', 'title', search={'value': 'red'}), client))
    assert (client.searches, client.counts) == ([], [])


def test_a_search_with_a_word_on_a_table_of_numbers_dates_and_booleans_is_refused_and_nothing_is_sent():
    """SPEC-F13.2: only a string takes part in the global search on OpenSearch, so these three kinds leave `red` with
    nothing to match."""
    typed = SearchTable(
        'docs',
        [Column('pages', type='number'), Column('created', type='date'), Column('active', type='boolean')],
        unique_key='pages',
    )
    client = Client()
    refused(typed.fetch_sync(request('pages', 'created', 'active', search={'value': 'red'}), client))
    assert (client.searches, client.counts) == ([], [])


@pytest.mark.parametrize('text', ['', '"'])
def test_an_empty_search_on_a_table_with_no_searchable_column_is_the_plain_request(text: str):
    """SPEC-F13.3, SPEC-F13.4: nothing is asked, so nothing is refused and the body is the one of a request with no
    search. Catches a lone `"` counted as a word, which is refused for want of a column."""
    client = Client()
    assert UNSEARCHABLE.fetch_sync(request('id', 'title', search={'value': text}), client).error is None
    plain = Client()
    UNSEARCHABLE.fetch_sync(request('id', 'title'), plain)
    assert client.searches == plain.searches


def tally(query: Mapping[str, Any]) -> tuple[int, int, int]:
    """The leaf queries, the clauses of every `bool` (a leaf or another `bool` each) and the most clauses one `bool`
    holds, in a query: the three ways a server may count against `indices.query.bool.max_clause_count`."""
    leaves = clauses = widest = 0
    pending = [query]
    while pending:
        node = pending.pop()
        if 'bool' not in node:
            leaves += 1
            continue
        for occurrences in node['bool'].values():
            if isinstance(occurrences, list):
                children = cast(list[Mapping[str, Any]], occurrences)
                clauses += len(children)
                widest = max(widest, len(children))
                pending.extend(children)
    return leaves, clauses, widest


# -- the budget of conditions (PRD-C17, issue #89) ---

BUDGET = 960  # PRD-C17: measured in docs/decisions/005


def test_the_widest_bool_of_a_request_of_the_budget_holds_at_most_the_default_clause_limit():
    """SPEC-C17.3, PRD-C17: one word of the global search over BUDGET text+keyword columns is one `should` for each column, each a
    `bool` of its own of two clauses (`match_phrase` and `wildcard`), so the widest `bool` holds BUDGET clauses, within
    the default `indices.query.bool.max_clause_count` of 1,024, which a server counts per `bool` (decision 005). It
    catches a budget set above the limit, or a change that puts the clauses of a word in one `bool`."""
    columns = [IndexColumn(f't{number}', field='title') for number in range(BUDGET)]
    client = Client()
    SearchTable('docs', columns, unique_key='t0').fetch_sync(
        request(*(c.name for c in columns), search={'value': 'ann'}), client
    )
    leaves, _, widest = tally(client.searches[0]['query'])
    assert (leaves, widest) == (2 * BUDGET, BUDGET)


def test_a_request_one_condition_over_the_budget_is_refused_before_any_search():
    """SPEC-C17.2, PRD-C17, PRD-E2: one word over BUDGET + 1 columns holds one condition too many. It is answered
    `request_too_large`, and nothing is sent to the cluster. It catches a budget that is not counted on this backend,
    or counted after the search has gone out."""
    columns = [IndexColumn(f't{number}', field='title') for number in range(BUDGET + 1)]
    client = Client()
    response = SearchTable('docs', columns, unique_key='t0').fetch_sync(
        request(*(c.name for c in columns), search={'value': 'ann'}), client
    )
    assert response.error_code == 'request_too_large'
    assert (client.searches, client.counts) == ([], [])


# -- the bounds of every value (PRD-F14), answered with their own codes (PRD-E2, PRD-E3) ---

OVER_THE_BOUNDS: list[tuple[dict[str, Any], str]] = [
    ({'column_search': {'title': {'value': SEVENTEEN}}}, 'too_many_words'),
    ({'column_search': {'pages': {'value': '1' * 256}}}, 'value_too_long'),
    # PRD-E3 (2): on a number or date column that is one value to match, words are no words, so the value is unreadable
    ({'column_search': {'pages': {'value': SEVENTEEN}}}, 'invalid_number'),
    ({'column_search': {'created': {'value': SEVENTEEN}}}, 'invalid_date'),
    ({'column_search': {'id': {'multi_values': ['a', 'b' * 256]}}}, 'value_too_long'),
    ({'column_search': {'id': {'value': f'^(a|{"b" * 256})$', 'regex': True}}}, 'value_too_long'),
    (criterion('pages', '=', '1' * 256), 'value_too_long'),
    # SPEC-F14.4: the length comes before the number is read, so no `invalid_number` and no conversion: Python refuses
    # an int of more than 4,300 digits, and a long run of zeros costs a parse
    (criterion('pages', '=', '1' * 4301), 'value_too_long'),
    (criterion('pages', '=', '0' * 5000), 'value_too_long'),
    (criterion('pages', 'between', '1', '2' * 256), 'value_too_long'),
    (criterion('created', '=', '2024-01-01' + ' ' * 250), 'value_too_long'),
    # SPEC-E3.2: step 1 comes before every step it hides, in the same part of the request as in another
    ({'search': {'value': SEVENTEEN}, 'column_search': {'title': {'value': 'a' * 256}}}, 'too_many_words'),
    ({'search': {'value': 'a' * 256}, **criterion('pages', '=', 'abc')}, 'value_too_long'),
]


@pytest.mark.parametrize(('body', 'code'), OVER_THE_BOUNDS)
def test_a_value_over_the_bounds_is_refused_with_its_code_and_echoes_nothing(body: dict[str, Any], code: str):
    """SPEC-F14.2, SPEC-F14.4, SPEC-E3.2, SPEC-F33.2, SPEC-F34.2: the refusal of a value on the columns and in the
    conditions OpenSearch describes, answered before the server is asked, with a text that holds none of what was
    sent. OpenSearch reads a column search on a number or a date as one value, so 17 words there are answered as an
    unreadable value, not counted against the bound of PRD-F14. The rows the table lacks are asserted by the error
    table below and by tests/test_sql.py and tests/test_sql_search.py."""
    client = Client()
    message = refused(table.fetch_sync(request(*NAMES, **body), client), code)
    assert client.searches == []
    assert not any(sent in message for sent in ('aaa', 'bbb', 'abc', *SEVENTEEN.split()))


@pytest.mark.parametrize(
    'body',
    [
        {'search': {'value': ' '.join(SEVENTEEN.split()[:16])}},
        criterion('pages', 'null', '1' * 300),  # a value the condition does not read is not measured
    ],
)
def test_a_value_within_the_bounds_is_answered(body: dict[str, Any]):
    """SPEC-F14.2, SPEC-F14.4: a global search of 16 words, and `null` with a value of 300 code points, are answered.
    The values of 255 code points are answered by the cluster in tests/test_opensearch_live.py."""
    assert table.fetch_sync(request(*NAMES, **body), Client()).error is None


@pytest.mark.parametrize(('padding', 'code'), [(251, None), (252, 'value_too_long')])
def test_a_boolean_value_is_measured_at_255_code_points(padding: int, code: str | None):
    """SPEC-F14.4: `true` and its padding are 255 characters and are read as `true`; one more is too long. The bounds
    table above has no boolean column."""
    client = Client()
    response = flags.fetch_sync(request(*FLAG_NAMES, **criterion('active', '=', 'true' + ' ' * padding)), client)
    if code is None:
        assert response.error is None
        assert clauses(client) == [{'term': {'active': True}}]
    else:
        refused(response, code)
        assert client.searches == []


def sent(search_table: SearchTable, params: DTParams) -> Mapping[str, Any]:
    """The one search body the request makes."""
    client = Client()
    response = search_table.fetch_sync(params, client)
    assert response.error is None, response.error
    return client.searches[-1]


def test_a_column_with_data_and_no_name_is_the_declared_column_of_that_name():
    """SPEC-P15.1: a column search on `title` (data `title`, empty name) sends what the same column with `name` `title`
    sends, and that is a search, not the plain request."""
    search = {'title': {'value': 'pear'}}
    by_data = sent(table, request(*NAMES, column_search=search))
    assert by_data == sent(table, request(*NAMES, column_search=search, flags={'title': {'name': 'title'}}))
    assert by_data != sent(table, request(*NAMES))


def test_an_order_entry_is_decided_by_its_column_index_not_its_name():
    """SPEC-P16.1: column 3 is `pages`, and the entry's `name` `id` is not read; ordering by `id` sorts differently."""
    by_index = sent(table, request(*NAMES, order=[{'column': 3, 'dir': 'asc', 'name': 'id'}]))
    assert by_index == sent(table, request(*NAMES, order=[{'column': 3, 'dir': 'asc'}]))
    assert by_index != sent(table, request(*NAMES, order=[{'column': 0, 'dir': 'asc'}]))


def test_a_column_whose_data_is_an_object_is_known_by_its_name():
    """SPEC-P18.1: `data` `{"_": "title", "sort": "title"}` with `name` `title` is the declared column; with no `name`
    it is nameless (PRD-P21), and a column search on it is refused with nothing sent."""
    search = {'title': {'value': 'pear'}}
    plain = sent(table, request(*NAMES, column_search=search))
    named = request(
        *NAMES, column_search=search, flags={'title': {'data': {'_': 'title', 'sort': 'title'}, 'name': 'title'}}
    )
    assert sent(table, named) == plain
    nameless = request(*NAMES, column_search=search, flags={'title': {'data': {'_': 'title', 'sort': 'title'}}})
    client = Client()
    assert refused(table.fetch_sync(nameless, client))
    assert client.searches == []


def test_a_data_of_the_integer_zero_is_a_column_named_zero():
    """SPEC-P20.2: the default `data` is the index, which a JSON body carries as the number 0; a column declared `'0'` is
    that column, so the body is the one the text `'0'` makes."""
    zero = SearchTable(
        'docs', [IndexColumn('id', text='keyword'), Column('0', field='pages', type='number')], unique_key='id'
    )
    as_text = request('id', '0', column_search={'0': {'value': '3'}})
    as_number = request('id', '0', column_search={'0': {'value': '3'}}, flags={'0': {'data': 0}})
    assert sent(zero, as_number) == sent(zero, as_text)
    assert sent(zero, as_number) != sent(zero, request('id', '0'))


def test_a_column_search_on_a_nameless_column_is_refused():
    """SPEC-P21.3: a column that only renders has no field to search; nothing is sent."""
    client = Client()
    params = request('', *NAMES, flags={'': {'data': None}}, column_search={'': {'value': 'x'}})
    assert refused(table.fetch_sync(params, client))
    assert client.searches == []


def test_a_nameless_first_column_with_the_default_order_and_a_global_search_is_answered():
    """SPEC-P21.1: DataTables' default order on a column that only renders is skipped and the global search leaves it
    out, so the body is the one the request without that column sends, and there is no error."""
    nameless = request(
        '', *NAMES, flags={'': {'data': None}}, order=[{'column': 0, 'dir': 'asc'}], search={'value': 'red'}
    )
    assert sent(table, nameless) == sent(table, request(*NAMES, search={'value': 'red'}))


HITS = [{'_source': {'id': name, 'title': f'title {name}', 'pages': 1}, '_score': 1.0} for name in ('a', 'b')]


def test_a_row_shaped_through_a_model_carries_the_field_the_model_adds():
    """SPEC-R2.1: `shape` maps each row through a Pydantic model that adds `label`; every row carries it."""
    shaped = SearchTable('docs', COLUMNS, unique_key='id', rows=labelled('id'))
    rows = shaped.fetch_sync(request(*NAMES), Client(hits=HITS)).data
    assert [(row['id'], row['label']) for row in rows] == [('a', 'label a'), ('b', 'label b')]
    assert all(set(row) == {*NAMES, 'label'} for row in rows)


def test_the_row_id_hook_adds_dt_rowid_and_without_it_no_row_has_one():
    """SPEC-R3.1: a hook returning `row_<id>` gives each row its `DT_RowId`; the plain table adds none."""
    hooked = SearchTable('docs', COLUMNS, unique_key='id', rows=RowHooks(row_id=lambda row: f'row_{row["id"]}'))
    rows = hooked.fetch_sync(request(*NAMES), Client(hits=HITS)).data
    assert [row['DT_RowId'] for row in rows] == ['row_a', 'row_b']
    assert all('DT_RowId' not in row for row in table.fetch_sync(request(*NAMES), Client(hits=HITS)).data)


def test_the_row_data_hook_adds_dt_rowdata_and_without_it_no_row_has_any():
    """SPEC-R4.1: a hook returning `{"pk": id}` gives each row its `DT_RowData`; the plain table adds none."""
    hooked = SearchTable('docs', COLUMNS, unique_key='id', rows=RowHooks(row_data=lambda row: {'pk': row['id']}))
    rows = hooked.fetch_sync(request(*NAMES), Client(hits=HITS)).data
    assert [row['DT_RowData'] for row in rows] == [{'pk': 'a'}, {'pk': 'b'}]
    assert all('DT_RowData' not in row for row in table.fetch_sync(request(*NAMES), Client(hits=HITS)).data)


# -- column declaration (PRD-D1 to PRD-D11, PRD-D13, PRD-D21) ---


@pytest.mark.parametrize(
    'names', [('author', 'id'), ('id',), tuple(reversed(NAMES))], ids=['subset', 'one', 'reversed']
)
def test_a_request_naming_some_columns_gets_rows_with_every_declared_column(names: tuple[str, ...]):
    """SPEC-D1.1: the answer is built from the hits: each row holds every declared column, whichever the request names."""
    rows = table.fetch_sync(request(*names), Client(hits=HITS)).data
    assert [row['id'] for row in rows] == ['a', 'b']
    assert all(set(row) == set(NAMES) for row in rows)


def test_a_request_with_no_columns_search_or_order_asks_for_nothing_and_counts_alike():
    """SPEC-D1.3: no clause beyond the base filter, every declared field read, and filtered equals total."""
    client = Client(hits=HITS, total=7)
    response = table.fetch_sync(parse_request({'draw': 1, 'start': 0, 'length': 10}), client)
    assert (response.recordsTotal, response.recordsFiltered) == (7, 7)
    assert query(client) == []
    assert client.searches[0]['_source'] == ['id', 'title', 'body', 'pages', 'created', 'meta.author']
    assert [row['id'] for row in response.data] == ['a', 'b']


def test_the_declared_type_decides_the_comparison():
    """SPEC-D3.1: `<` on `pages` (a number) is a range, and on `title` (a string) is refused with nothing sent."""
    client = Client()
    table.fetch_sync(request(*NAMES, **criterion('pages', '<', '8')), client)
    assert query(client) == [{'range': {'pages': {'lt': 8}}}]
    refused_client = Client()
    refused(table.fetch_sync(request(*NAMES, **criterion('title', '<', 'm')), refused_client))
    assert refused_client.searches == []


def test_a_column_declared_unsearchable_is_left_out_of_every_search():
    """SPEC-D4.1: it takes no part in the global search, whose query is the one of the table without it, and a column
    search on it is refused with nothing sent."""
    columns = [IndexColumn('id', text='keyword'), IndexColumn('title', searchable=False)]
    unsearchable = SearchTable('docs', columns, unique_key='id')
    only_id = SearchTable('docs', columns[:1], unique_key='id')
    params = request('id', 'title', search={'value': 'ann'})
    assert sent(unsearchable, params)['query'] == sent(only_id, params)['query']
    assert sent(unsearchable, params)['query'] != sent(table, params)['query']
    client = Client()
    refused(unsearchable.fetch_sync(request('id', 'title', column_search={'title': {'value': 'ann'}}), client))
    assert client.searches == []


def test_a_column_search_on_an_undeclared_name_is_refused_with_a_value_and_answered_without_one():
    """SPEC-D6.1: refused with nothing sent; with an empty value the request is the one without the column."""
    client = Client()
    refused(table.fetch_sync(request(*NAMES, 'extra', column_search={'extra': {'value': 'x'}}), client))
    assert client.searches == []
    assert sent(table, request(*NAMES, 'extra')) == sent(table, request(*NAMES))


def test_a_criterion_on_an_undeclared_name_is_refused_at_any_depth_beside_a_matching_one():
    """SPEC-D6.2: in a nested group, in an OR group, next to a criterion that would match, with nothing sent."""
    tree = {
        'logic': 'OR',
        'criteria': [
            {'condition': '=', 'origData': 'id', 'value': ['a']},
            {'logic': 'AND', 'criteria': [{'condition': '=', 'origData': 'secret', 'value': ['x']}]},
        ],
    }
    client = Client()
    refused(table.fetch_sync(request(*NAMES, searchBuilder=tree), client))
    assert client.searches == []


def test_the_clients_type_is_ignored_both_ways():
    """SPEC-D7.1, SPEC-F27.4: `<` on `pages` sent as a string is still a range; `contains` on it is refused, with nothing sent."""
    client = Client()
    typed: dict[str, Any] = {
        'searchBuilder': {'criteria': [{'condition': '<', 'origData': 'pages', 'type': 'string', 'value': ['8']}]}
    }
    table.fetch_sync(request(*NAMES, **typed), client)
    assert query(client) == [{'range': {'pages': {'lt': 8}}}]
    contains: dict[str, Any] = {
        'searchBuilder': {
            'criteria': [{'condition': 'contains', 'origData': 'pages', 'type': 'string', 'value': ['7']}]
        }
    }
    refused_client = Client()
    refused(table.fetch_sync(request(*NAMES, **contains), refused_client))
    assert refused_client.searches == []


@pytest.mark.parametrize('condition', ['between', '!between'])
def test_the_two_values_of_between_are_a_range_in_either_order(condition: str):
    """SPEC-F27.9: `30` and `12` on `pages` send the body of `12` and `30`, whose rows the live test pins; catches
    reversed bounds sent as an empty range, which no document is in."""
    reversed_bounds = sent(table, request(*NAMES, **criterion('pages', condition, '30', '12')))
    assert reversed_bounds == sent(table, request(*NAMES, **criterion('pages', condition, '12', '30')))


def test_a_number_field_declared_without_a_type_is_a_string_column():
    """SPEC-D8.3: the library does not read the mapping, so `pages` without `type='number'` refuses `<`."""
    untyped = SearchTable('docs', [IndexColumn('id', text='keyword'), Column('pages')], unique_key='id')
    client = Client()
    refused(untyped.fetch_sync(request('id', 'pages', **criterion('pages', '<', '8')), client))
    assert client.searches == []


def test_a_column_declared_without_the_fold_still_searches_ignoring_case():
    """SPEC-D21.3: `contains` `RED` on a `fold_case=False` column sends what the folding column sends."""
    folding = SearchTable('docs', [IndexColumn('id', text='keyword'), Column('secret')], unique_key='id')
    plain = SearchTable('docs', [IndexColumn('id', text='keyword'), Column('secret', fold_case=False)], unique_key='id')
    params = request('id', 'secret', **criterion('secret', 'contains', 'RED'))
    assert sent(plain, params) == sent(folding, params)
    assert 'case_insensitive' in json.dumps(sent(plain, params))


# -- ordering and the API (PRD-O, PRD-A) ---


@pytest.mark.parametrize(
    'order',
    [
        [{'column': 2, 'dir': 'asc'}, {'column': 3, 'dir': 'asc'}],
        [{'column': 3, 'dir': 'asc'}, {'column': 2, 'dir': 'asc'}],
    ],
    ids=['first', 'second'],
)
def test_an_order_list_is_refused_whole_wherever_the_refused_entry_stands(order: list[dict[str, Any]]):
    """SPEC-O1.8: `body` is analysed text, which the server cannot sort by, and the request calls it orderable;
    `pages` is fine. Nothing is sent."""
    client = Client()
    refused(table.fetch_sync(request(*NAMES, order=order), client))
    assert client.searches == []


def _varied_requests(count: int) -> list[DTParams]:
    words = ['red', 'ann', 'example', 'zzz', '']
    return [
        request(
            *NAMES, search={'value': words[number % 5]}, order=[{'column': 3, 'dir': 'desc' if number % 2 else 'asc'}]
        )
        for number in range(count)
    ]


def _fresh() -> SearchTable:
    return SearchTable('docs', COLUMNS, unique_key='id', base_filter={'term': {'published': True}})


def test_one_table_answers_100_requests_as_a_fresh_table_does():
    """SPEC-A2.1: each answer and each body equals the one of a table built for that request."""
    for params in _varied_requests(100):
        used, fresh = Client(hits=HITS), Client(hits=HITS)
        assert table.fetch_sync(params, used) == _fresh().fetch_sync(params, fresh)
        assert (used.searches, used.counts) == (fresh.searches, fresh.counts)


async def test_one_table_answers_20_concurrent_requests_on_one_async_client_as_a_fresh_table_does():
    """SPEC-A2.1: the 20 share one async client, whose recorded bodies are those of 20 fresh tables."""
    concurrent = _varied_requests(20)
    shared = AsyncClient(hits=HITS)
    answers = await asyncio.gather(*(table.fetch(params, shared) for params in concurrent))
    expected_client = Client(hits=HITS)
    expected = [_fresh().fetch_sync(params, expected_client) for params in concurrent]
    assert answers == expected

    def canonical(bodies: list[Mapping[str, Any]]) -> list[str]:
        return sorted(json.dumps(body, sort_keys=True) for body in bodies)

    assert canonical(shared.searches) == canonical(expected_client.searches)
    assert canonical(shared.counts) == canonical(expected_client.counts)


def _plain(value: Any) -> bool:
    """Whether a body is made of dicts, lists, text, numbers, booleans and None, and of nothing else."""
    if isinstance(value, dict):
        return type(value) is dict and all(isinstance(key, str) and _plain(item) for key, item in value.items())  # type: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        return type(value) is list and all(_plain(item) for item in value)  # type: ignore[reportUnknownVariableType]
    return value is None or type(value) in (str, int, float, bool)


def test_every_body_the_client_receives_is_a_plain_dict_that_survives_a_json_round_trip():
    """SPEC-A8.1: a global search, column searches, SearchBuilder, an order and a page, each with its counts."""
    cases = [
        request(*NAMES, search={'value': 'red apple -pie'}, start=5, length=5),
        request(*NAMES, column_search={'title': {'value': 'pear'}, 'pages': {'value': '3'}}),
        request(*NAMES, **criterion('pages', 'between', '1', '9')),
        request(*NAMES, **criterion('created', '=', '2026-07-15')),
        request(*NAMES, **criterion('pages', 'between', '1.5', '9.5')),  # a number with a point is a float in the body
        request(*NAMES, **criterion('pages', '=', '2.5')),
        request(*NAMES, column_search={'pages': {'multi_values': ['1.5', '2']}}),
        request(*NAMES, order=[{'column': 1, 'dir': 'desc'}, {'column': 3, 'dir': 'asc'}]),
        request(*NAMES, search={'value': 'x'}, column_search={'author': {'multi_values': ['Ann', 'Bob']}}),
    ]
    bodies: list[Mapping[str, Any]] = []
    for params in cases:
        client = Client(hits=HITS)
        table.fetch_sync(params, client)
        bodies += [*client.searches, *client.counts]
    assert len(bodies) > len(cases)  # the filtered requests count too
    for body in bodies:
        assert _plain(body)
        assert json.loads(json.dumps(body)) == body


# -- counts and paging (PRD-C) ---


def test_a_filtered_draw_asks_for_the_total_apart_and_answers_it():
    """SPEC-C8.2: with a global search the total is the answer of a count of the base filter, the filtered count the
    search's own."""
    client = Client(total=3, count=40)
    response = table.fetch_sync(request(*NAMES, search={'value': 'x'}), client)
    assert (response.recordsTotal, response.recordsFiltered) == (40, 3)
    assert client.counts == [{'query': {'bool': {'must': [{'term': {'published': True}}]}}}]


class Spy(Client):
    """A client with `search` and `count` whose every other attribute, an index-settings call or any other admin one,
    is recorded and refused."""

    def __init__(self, **answer: Any):
        self.admin: list[str] = []
        super().__init__(**answer)

    def __getattr__(self, name: str) -> Any:
        self.admin.append(name)
        raise AttributeError(name)


def test_no_call_changes_the_index_settings():
    """SPEC-C12.2: an ordinary page, a reversed one and a page out of range ask the cluster nothing but searches and
    counts."""
    windowed = SearchTable('docs', COLUMNS, unique_key='id', max_result_window=3)
    spy = Spy(hits=HITS, total=10, count=10)
    for start in (0, 8, 4):
        windowed.fetch_sync(request(*NAMES, start=start, length=3), spy)
    assert spy.admin == []
    assert spy.searches and spy.counts


def test_a_cached_total_is_counted_once_between_two_requests_within_the_ttl():
    """SPEC-C14.1: two filtered requests run one total count between them, and both answer it."""
    client, cached = (
        Client(total=3, count=40),
        SearchTable('docs', COLUMNS, unique_key='id', total_cache=TotalCache(60)),
    )
    answers = [cached.fetch_sync(request(*NAMES, search={'value': 'x'}), client) for _ in range(2)]
    assert [answer.recordsTotal for answer in answers] == [40, 40]
    assert len(client.counts) == 1


async def test_the_async_fetch_counts_a_cached_total_once_between_two_requests_within_the_ttl():
    """SPEC-C14.1 through `fetch`: the async path has its own copy of the cache read and fill."""
    client = AsyncClient(total=3, count=40)
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=TotalCache(60))
    answers = [await cached.fetch(request(*NAMES, search={'value': 'x'}), client) for _ in range(2)]
    assert [answer.recordsTotal for answer in answers] == [40, 40]
    assert len(client.counts) == 1


SCOPE_VALUE = 'tenant-zq-4711'
SCOPE = {'term': {'tenant': SCOPE_VALUE}}


def test_a_store_that_is_no_total_cache_serves_the_second_draw_and_sees_only_digests():
    """SPEC-C18.1: a plain class with `get` and `put`; the second filtered draw is served from it, so one count runs.
    Every key it was given is a SHA-256 digest: the request's scope is in the key's source, and never reaches the store."""
    store, client = Store(), Client(total=3, count=40)
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=store)
    answers = [cached.fetch_sync(request(*NAMES, search={'value': 'x'}), client, scope=SCOPE) for _ in range(2)]
    assert [answer.recordsTotal for answer in answers] == [40, 40]
    assert len(client.counts) == 1
    assert store.keys
    assert all(re.fullmatch('[0-9a-f]{64}', key) for key in store.keys)
    assert not any(SCOPE_VALUE in key for key in store.keys)


async def test_an_async_store_serves_the_second_async_draw_and_sees_only_digests():
    """SPEC-C18.2: the same through `fetch` with a store whose methods are coroutines."""
    store, client = AsyncStore(), AsyncClient(total=3, count=40)
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=store)
    answers = [await cached.fetch(request(*NAMES, search={'value': 'x'}), client, scope=SCOPE) for _ in range(2)]
    assert [answer.recordsTotal for answer in answers] == [40, 40]
    assert len(client.counts) == 1
    assert store.keys
    assert all(re.fullmatch('[0-9a-f]{64}', key) for key in store.keys)
    assert not any(SCOPE_VALUE in key for key in store.keys)


async def test_a_store_whose_methods_return_awaitables_is_awaited_too():
    """SPEC-C18.2: `fetch` awaits what `get` and `put` return, whichever way the store is written."""
    store, client = AwaitingStore(), AsyncClient(total=3, count=40)
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=store)
    answers = [await cached.fetch(request(*NAMES, search={'value': 'x'}), client, scope=SCOPE) for _ in range(2)]
    assert [answer.recordsTotal for answer in answers] == [40, 40]
    assert len(client.counts) == 1
    assert store.keys
    assert all(re.fullmatch('[0-9a-f]{64}', key) for key in store.keys)
    assert not any(SCOPE_VALUE in key for key in store.keys)


def test_a_store_that_says_it_is_empty_is_used_all_the_same():
    """SPEC-C18.3: a store is in use because it was given, not because it is truthy."""
    client = Client(total=3, count=40)
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=EmptyStore())
    for _ in range(2):
        cached.fetch_sync(request(*NAMES, search={'value': 'x'}), client)
    assert len(client.counts) == 1


async def test_the_async_fetch_uses_a_store_that_says_it_is_empty_too():
    """SPEC-C18.3 through `fetch`, which reads and fills the store in code of its own."""
    client = AsyncClient(total=3, count=40)
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=EmptyStore())
    for _ in range(2):
        await cached.fetch(request(*NAMES, search={'value': 'x'}), client)
    assert len(client.counts) == 1


def test_fetch_sync_with_an_async_store_raises_before_anything_is_sent():
    """SPEC-C18.4: the message names the condition and the fix, and no search or count has run."""
    client = Client()
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=AsyncStore())
    with pytest.raises(TypeError, match='call fetch'):
        cached.fetch_sync(request(*NAMES, search={'value': 'x'}), client)
    assert (client.searches, client.counts) == ([], [])


@pytest.mark.parametrize('params', [request(*NAMES), request(*NAMES, search={'value': 'x'})])
def test_fetch_sync_with_a_store_returning_awaitables_raises_and_never_drops_one_silently(params: DTParams):
    """SPEC-C18.4: an unfiltered draw only calls `put`, a filtered one `get` first; either way the awaitable the store
    returned is no answer, and a dropped one would leave the total uncached without a word."""
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=AwaitingStore())
    with pytest.raises(TypeError, match='call fetch'):
        cached.fetch_sync(params, Client())


class Indexes:
    """A recording client over several indexes, each with its own number of documents."""

    def __init__(self, sizes: dict[str, int]):
        self.sizes = sizes
        self.counted: list[str] = []

    def search(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        hits = {'total': {'value': 1, 'relation': 'eq'}, 'max_score': None, 'hits': []}
        return {'timed_out': False, '_shards': SHARDS, 'hits': hits}

    def count(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        self.counted.append(index)
        return {'count': self.sizes[index], '_shards': SHARDS}


def test_two_tables_over_two_indexes_never_share_a_total():
    """SPEC-C15.4: `items_a` holds 4 documents and `items_b` 2; one cache and one client serve both."""
    cache, client = TotalCache(ttl=60), Indexes({'items_a': 4, 'items_b': 2})
    tables = [SearchTable(index, COLUMNS, unique_key='id', total_cache=cache) for index in ('items_a', 'items_b')]
    totals = [each.fetch_sync(request(*NAMES, search={'value': 'red'}), client).recordsTotal for each in tables]  # type: ignore[arg-type]
    assert totals == [4, 2]
    assert sorted(client.counted) == ['items_a', 'items_b']


class Teams:
    """A client that counts the documents of the `team` a total count's base filter names."""

    def __init__(self, sizes: dict[str, int]):
        self.sizes = sizes
        self.counted: list[str] = []

    def search(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        hits = {'total': {'value': 1, 'relation': 'eq'}, 'max_score': None, 'hits': []}
        return {'timed_out': False, '_shards': SHARDS, 'hits': hits}

    def count(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        team = body['query']['bool']['must'][0]['term']['team']
        self.counted.append(team)
        return {'count': self.sizes[team], '_shards': SHARDS}


def test_two_tables_over_one_index_with_two_base_filters_never_share_a_total():
    """SPEC-C15.3: `red` holds 4 documents and `green` 2; one cache and one client serve both tables, which differ only
    in the bound value of their base filter. It catches a cache key that leaves the base filter out, which answers the
    second table with the first one's total."""
    cache, client = TotalCache(ttl=60), Teams({'red': 4, 'green': 2})
    tables = [
        SearchTable('docs', COLUMNS, unique_key='id', base_filter={'term': {'team': team}}, total_cache=cache)
        for team in ('red', 'green')
    ]
    totals = [each.fetch_sync(request(*NAMES, search={'value': 'red'}), client).recordsTotal for each in tables]  # type: ignore[arg-type]
    assert totals == [4, 2]
    assert client.counted == ['red', 'green']


RED, GREEN = {'term': {'team': 'red'}}, {'term': {'team': 'green'}}


def _team(body: Mapping[str, Any]) -> str:
    """The team the scope of a body names: its first clause, which is the scope when the table has no base filter."""
    return body['query']['bool']['must'][0]['term']['team']


class Scopes:
    """A recording client over documents of two teams. A search or a count answers the size of the team its scope
    names, so a total taken from the wrong key shows."""

    def __init__(self, sizes: dict[str, int]):
        self.sizes = sizes
        self.counts: list[Mapping[str, Any]] = []

    def search(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        hits = {'total': {'value': self.sizes[_team(body)], 'relation': 'eq'}, 'max_score': None, 'hits': []}
        return {'timed_out': False, '_shards': SHARDS, 'hits': hits}

    def count(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        self.counts.append(body)
        return {'count': self.sizes[_team(body)], '_shards': SHARDS}


class AsyncScopes(Scopes):
    """The same, and each call yields to the event loop ``delays[team]`` times before it answers: a request whose scope
    is slow is half way when a faster one starts and finishes."""

    def __init__(self, sizes: dict[str, int], delays: dict[str, int] | None = None):
        super().__init__(sizes)
        self.delays = delays or {}

    async def _yield(self, body: Mapping[str, Any]) -> None:
        for _ in range(max(1, self.delays.get(_team(body), 1))):
            await asyncio.sleep(0)

    async def search(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:  # type: ignore[override]
        await self._yield(body)
        return Scopes.search(self, index=index, body=body)

    async def count(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:  # type: ignore[override]
        await self._yield(body)
        return Scopes.count(self, index=index, body=body)


def test_a_scope_is_anded_with_the_base_filter_in_the_search_and_in_the_total_count():
    """SPEC-F31.2, recorded: the search's `must` holds the base filter, then the scope, then the request's clauses; the
    total count holds both and nothing else. A scope that replaced the base filter, or one left out of the count body
    (the count would then answer the base filter's documents as the total), shows here."""
    base = {'term': {'published': True}}
    scope = {'range': {'pages': {'gt': 1}}}
    client = Client(total=3, count=40)
    table.fetch_sync(request(*NAMES, search={'value': 'x'}), client, scope=scope)  # type: ignore[arg-type]
    must = client.searches[-1]['query']['bool']['must']
    assert must[:2] == [base, scope]
    assert len(must) > 2
    assert client.counts == [{'query': {'bool': {'must': [base, scope]}}}]
    client = Client(total=3, count=40)
    table.fetch_sync(request(*NAMES), client, scope=scope)  # type: ignore[arg-type]
    assert client.searches[-1]['query'] == {'bool': {'must': [base, scope]}}
    assert client.counts == []


@pytest.mark.parametrize('scope', [None, {}], ids=['none', 'empty-mapping'])
@pytest.mark.parametrize('search', ['', 'x'])
def test_a_scope_of_none_or_an_empty_mapping_sends_what_no_scope_sends(scope: dict[str, Any] | None, search: str):
    """SPEC-F31.3, recorded: the bodies equal those of a request with no scope. An empty mapping appended to `must`
    would be an empty clause, which OpenSearch refuses (INFERRED, not run; the live test sends it)."""
    plain, scoped = Client(total=3, count=40), Client(total=3, count=40)
    params = request(*NAMES, search={'value': search})
    expected = table.fetch_sync(params, plain)
    assert table.fetch_sync(params, scoped, scope=scope) == expected  # type: ignore[arg-type]
    assert (scoped.searches, scoped.counts) == (plain.searches, plain.counts)


def test_a_scope_that_differs_never_shares_the_total_an_unfiltered_request_filled():
    """SPEC-C15.9: an unfiltered request with the scope `red` fills the cache with its own count, 4 (PRD-C8). A global
    search with the scope `green` then counts for itself, and answers 2. A key without the scope answers 4."""
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=TotalCache(ttl=60))
    client = Scopes({'red': 4, 'green': 2})
    assert cached.fetch_sync(request(*NAMES), client, scope=RED).recordsTotal == 4  # type: ignore[arg-type]
    assert client.counts == []
    response = cached.fetch_sync(request(*NAMES, search={'value': 'red'}), client, scope=GREEN)  # type: ignore[arg-type]
    assert response.recordsTotal == 2
    assert client.counts == [{'query': {'bool': {'must': [GREEN]}}}]


def test_a_total_counted_for_a_scope_is_served_to_that_scope_only():
    """SPEC-C15.10: the scopes `red`, `green`, `red` on one table with one cache answer 4, 2, 4 and send two total
    counts, each holding its own scope; the third is served from the cache. A key without the scope sends one count
    and answers 4, 4, 4."""
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=TotalCache(ttl=60))
    client = Scopes({'red': 4, 'green': 2})
    totals = [
        cached.fetch_sync(request(*NAMES, search={'value': 'red'}), client, scope=scope).recordsTotal  # type: ignore[arg-type]
        for scope in (RED, GREEN, RED)
    ]
    assert totals == [4, 2, 4]
    assert client.counts == [{'query': {'bool': {'must': [RED]}}}, {'query': {'bool': {'must': [GREEN]}}}]


async def test_the_async_fetch_serves_a_total_to_the_scope_it_was_counted_for_too():
    """SPEC-C15.10 through `fetch`, which reads and fills the cache in code of its own."""
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=TotalCache(ttl=60))
    client = AsyncScopes({'red': 4, 'green': 2})
    totals = [
        (await cached.fetch(request(*NAMES, search={'value': 'red'}), client, scope=scope)).recordsTotal  # type: ignore[arg-type]
        for scope in (RED, GREEN, RED)
    ]
    assert totals == [4, 2, 4]
    assert client.counts == [{'query': {'bool': {'must': [RED]}}}, {'query': {'bool': {'must': [GREEN]}}}]


async def test_two_requests_half_way_together_each_keep_the_cache_key_of_their_own_scope():
    """SPEC-C15.11: `red` answers slowly (5 yields a call) and `green` fast (1), run together. `green` is counted and
    cached before `red` reads the cache. A key held on the table, set by the later request, makes `red` read the key
    of `green` and answer 2 with no count of its own."""
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=TotalCache(ttl=60))
    client = AsyncScopes({'red': 4, 'green': 2}, delays={'red': 5, 'green': 1})
    params = request(*NAMES, search={'value': 'red'})
    red, green = await asyncio.gather(
        cached.fetch(params, client, scope=RED),  # type: ignore[arg-type]
        cached.fetch(params, client, scope=GREEN),  # type: ignore[arg-type]
    )
    assert (red.recordsTotal, green.recordsTotal) == (4, 2)
    assert sorted(_team(body) for body in client.counts) == ['green', 'red']


def test_clear_makes_the_next_request_count_again():
    """SPEC-C16.1: without `clear()` the cached total stays; after it the next request counts the cluster again."""
    client, cache = Client(total=3, count=40), TotalCache(ttl=3600)
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=cache)
    params = request(*NAMES, search={'value': 'x'})
    assert cached.fetch_sync(params, client).recordsTotal == 40
    client.count_answer['count'] = 41
    assert cached.fetch_sync(params, client).recordsTotal == 40
    cache.clear()
    assert cached.fetch_sync(params, client).recordsTotal == 41


# -- errors (PRD-E, issue #122) ---

ERROR_NAMES = ('id', 'name', 'age', 'born', 'active', 'secret', 'team')
errors = SearchTable(
    'docs',
    [
        IndexColumn('id', text='keyword'),
        IndexColumn('name'),
        Column('age', type='number'),
        Column('born', type='date'),
        Column('active', type='boolean'),
        Column('secret', searchable=False),
        Column('team', orderable=False),
    ],
    unique_key='id',
)

#: The statements of PRD-E4 that hold on every backend and whose answer depends on how OpenSearch describes a column
#: (its type, and a number, date or boolean column searched), each with the request it states and its code. The
#: texts, and the statements no description changes, are asserted once, in tests/test_sql.py (the convention of
#: SPEC-E4); on OpenSearch the bounds of PRD-F38 are refused in the tests of those bounds above.
USER_ERRORS = [
    ('SPEC-E4.6', ERROR_NAMES, criterion('age', '=', 'abc'), 'invalid_number'),
    ('SPEC-E4.7', ERROR_NAMES, criterion('born', '=', '15/07/1980'), 'invalid_date'),
    ('SPEC-E4.8', ERROR_NAMES, criterion('born', 'contains', '1980-13'), 'invalid_date'),
    ('SPEC-E4.8', ERROR_NAMES, criterion('born', 'contains', '1980-02-30'), 'invalid_date'),
    ('SPEC-E4.10', ERROR_NAMES, criterion('born', '<', '9999-12-31'), 'invalid_date'),
    ('SPEC-E4.10', ERROR_NAMES, criterion('born', 'contains', '9999'), 'invalid_date'),
    ('SPEC-E4.15', ('active',), {'search': {'value': 'x'}}, 'invalid_filter'),
    (
        'SPEC-E4.19',
        ERROR_NAMES,
        {'column_search': {'age': {'value': 'abc', 'multi_values': ['x']}}},
        'invalid_filter',
    ),
    ('SPEC-E4.20', ERROR_NAMES, criterion('name', '<', 'm'), 'invalid_filter'),
    ('SPEC-E4.20', ERROR_NAMES, criterion('age', 'contains', '3'), 'invalid_filter'),
    ('SPEC-E4.20', ERROR_NAMES, criterion('born', 'starts', '19'), 'invalid_filter'),
    ('SPEC-E4.20', ERROR_NAMES, criterion('active', '<', '1'), 'invalid_filter'),
    ('SPEC-E4.22', ERROR_NAMES, criterion('active', '=', 'yes'), 'invalid_filter'),
    ('SPEC-E4.25', ERROR_NAMES, {'column_search': {'age': {'multi_values': ['31', 'abc']}}}, 'invalid_number'),
    ('SPEC-E4.25', ERROR_NAMES, criterion('age', 'between', '1', 'abc'), 'invalid_number'),
    (
        'SPEC-E4.27',
        ERROR_NAMES,
        {'column_search': {'born': {'multi_values': ['1980-07-15', '2024-13-01']}}},
        'invalid_date',
    ),
    ('SPEC-E4.27', ERROR_NAMES, criterion('born', 'between', '2024-01-01', 'abc'), 'invalid_date'),
    # The bounds of PRD-F14 refused on OpenSearch: the rest of the bounds table is in tests/test_sql.py
    ('SPEC-E4.30', ERROR_NAMES, {'search': {'value': 'x' * 256}}, 'value_too_long'),
    ('SPEC-E4.30', ERROR_NAMES, {'column_search': {'name': {'value': 'x' * 256}}}, 'value_too_long'),
    ('SPEC-E4.30', ERROR_NAMES, criterion('name', '=', 'x' * 256), 'value_too_long'),
    ('SPEC-E4.30', ERROR_NAMES, {'search': {'value': ' '.join('w' * 17)}}, 'too_many_words'),
    (
        'SPEC-E4.34',  # with a list on the number column `age`, which OpenSearch reads where the body is built
        ERROR_NAMES,
        {
            'column_search': {
                'name': {'multi_values': exact_values(256, 'Ann')},
                'team': {'multi_values': exact_values(256, 'red')},
                'age': {'multi_values': ['31']},
            }
        },
        'invalid_filter',
    ),
]


@pytest.mark.parametrize(
    ('spec', 'names', 'body', 'code'), USER_ERRORS, ids=[f'{case[0]}-{index}' for index, case in enumerate(USER_ERRORS)]
)
def test_every_user_error_of_the_specs_has_its_code_on_opensearch_and_sends_nothing(
    spec: str, names: tuple[str, ...], body: dict[str, Any], code: str
):
    """SPEC-E4.6, SPEC-E4.7, SPEC-E4.8, SPEC-E4.10, SPEC-E4.15, SPEC-E4.19, SPEC-E4.20, SPEC-E4.22, SPEC-E4.25,
    SPEC-E4.27, SPEC-E4.30 and SPEC-E4.34 (the `spec` of each case), SPEC-E2.1: the statements tagged [all] answer on
    OpenSearch the code they answer on SQL, and no mistake reaches the cluster, as a search or as a count
    (SPEC-E7.2). The text of each is asserted in tests/test_sql.py, once."""
    client = Client()
    assert refused(errors.fetch_sync(request(*names, **body), client), code)
    assert (client.searches, client.counts) == ([], [])


@pytest.mark.parametrize('entry', ['a\x01', 'x' * 256], ids=['control-character', 'too-long'])
def test_a_global_search_list_is_refused_without_its_entries_being_read_on_opensearch(entry: str):
    """SPEC-F40.2: the list on the global search is a form PRD-F40 refuses, so it answers `invalid_filter`, and sends
    nothing, though its entry holds a control character or is too long. Catches the entries of a refused global list
    being read first, so that `invalid_character` or `value_too_long` of step 1 answers instead."""
    client = Client()
    refused(errors.fetch_sync(request(*ERROR_NAMES, search={'value': '', 'multi_values': [entry]}), client))
    assert (client.searches, client.counts) == ([], [])


def test_a_global_search_list_is_not_counted_against_the_bounds_on_lists_on_opensearch():
    """SPEC-F40.3: a global list of 600 entries is over PRD-F38's 256 per list and 512 per request, but its entries are
    neither read nor counted (PRD-F40), so the number mistake of step 2 beside it answers, and alone it answers its own
    refusal; nothing is sent. Catches the global list counted against PRD-F38's bounds, which answers `invalid_filter`
    of step 1 instead."""
    search = {'value': '', 'multi_values': ['red'] * 600}
    client = Client()
    body = request(*ERROR_NAMES, search=search, **criterion('age', '=', 'abc'))
    refused(errors.fetch_sync(body, client), 'invalid_number')
    refused(errors.fetch_sync(request(*ERROR_NAMES, search=search), client))
    assert (client.searches, client.counts) == ([], [])


@pytest.mark.parametrize(
    ('searched', 'body', 'code'),
    [
        (errors, criterion('age', '=', 'abc'), 'invalid_number'),  # found by the plan
        (errors, {'column_search': {'age': {'multi_values': ['7', 'x']}}}, 'invalid_number'),  # found by the build
        (bounded(3), {'start': 4, 'length': 3}, 'page_out_of_range'),
    ],
    ids=['plan', 'build', 'page'],
)
def test_a_user_error_never_sends_a_page_search_and_only_a_refused_page_sends_counts(
    searched: SearchTable, body: dict[str, Any], code: str
):
    """SPEC-E7.2: the mistake is found before the page is searched; the one that needs a valid count sends the counts of
    PRD-C11 and nothing else."""
    names = ERROR_NAMES if searched is errors else NAMES
    client = Counting(total=40, filtered=7)
    refused(searched.fetch_sync(request(*names, **body), client), code)
    assert client.searches == []
    assert bool(client.counts) == (code == 'page_out_of_range')


class Unreachable(Client):
    """A cluster the transport cannot reach, for the one call it is asked to fail."""

    def __init__(self, fails: str):
        super().__init__()
        self.fails = fails

    def check(self, call: str) -> None:
        if call == self.fails:
            raise NoConnection('N/A', 'Connection refused', OSError('refused'))

    def search(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        self.check('search')
        return super().search(index=index, body=body)

    def count(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        self.check('count')
        return super().count(index=index, body=body)


class AsyncUnreachable(Unreachable):
    async def search(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:  # type: ignore[override]
        return Unreachable.search(self, index=index, body=body)

    async def count(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]:  # type: ignore[override]
        return Unreachable.count(self, index=index, body=body)


@pytest.mark.parametrize('fails', ['search', 'count'])
def test_a_transport_error_raises_through_the_sync_fetch(fails: str):
    """SPEC-E8.3: the request filters, so both calls run; whichever fails reaches the caller, never an empty page."""
    with pytest.raises(NoConnection):
        table.fetch_sync(request(*NAMES, search={'value': 'x'}), Unreachable(fails))


@pytest.mark.parametrize('fails', ['search', 'count'])
async def test_a_transport_error_raises_through_the_async_fetch(fails: str):
    """SPEC-E8.3 through `fetch`."""
    with pytest.raises(NoConnection):
        await table.fetch(request(*NAMES, search={'value': 'x'}), AsyncUnreachable(fails))


def test_an_exception_of_a_search_value_hook_propagates_unchanged():
    """SPEC-E8.4: a hook is the caller's code, so its failure is the caller's to see; answered as `invalid_filter`
    it would send the user to fix a filter that was right."""
    failure = ValueError('the hook failed')

    def fail(_: str) -> str:
        raise failure

    hooked = SearchTable(
        'docs', [IndexColumn('id', text='keyword'), Column('secret', search_value=fail)], unique_key='id'
    )
    client = Client()
    with pytest.raises(ValueError, match='the hook failed') as raised:
        hooked.fetch_sync(request('id', 'secret', column_search={'secret': {'value': 'x'}}), client)
    assert raised.value is failure
    assert client.searches == []


@pytest.mark.parametrize('hook', ['shape', 'row_id', 'row_data'])
def test_an_exception_of_a_row_hook_propagates_unchanged(hook: str):
    """SPEC-E8.4: each of the three row hooks, over a row that was found."""
    failure = ValueError('the hook failed')

    def fail(_: Any) -> Any:
        raise failure

    hooked = SearchTable('docs', COLUMNS, unique_key='id', rows=RowHooks(**{hook: fail}))
    with pytest.raises(ValueError, match='the hook failed') as raised:
        hooked.fetch_sync(request(*NAMES), Client(hits=[{'_source': {'id': 'a'}}]))
    assert raised.value is failure


def logged(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """What the library logged, after checking that it is all of the logger `datatables_backend` (SPEC-E12.1)."""
    assert all(record.name == 'datatables_backend' for record in caplog.records)
    return caplog.records


def test_a_total_taken_from_a_partial_count_is_never_kept():
    """SPEC-E9.5: the count answered by some of the shards raises; the next complete one counts again, so the cache
    held nothing of the partial one."""
    client = Client(total=3, count=40)
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=TotalCache(3600))
    params = request(*NAMES, search={'value': 'x'})
    client.count_answer = {'count': 12, '_shards': PARTIAL}
    with pytest.raises(PartialResultError):
        cached.fetch_sync(params, client)
    client.count_answer = {'count': 40, '_shards': SHARDS}
    assert cached.fetch_sync(params, client).recordsTotal == 40
    assert len(client.counts) == 2


def test_a_total_taken_from_a_partial_search_is_never_kept():
    """SPEC-E9.5: an unfiltered search's own count is the total, which the cache would keep; from a partial answer it
    must not, so the filtered request after it counts."""
    client = Client(total=3, count=40, _shards=PARTIAL)
    cached = SearchTable('docs', COLUMNS, unique_key='id', total_cache=TotalCache(3600))
    with pytest.raises(PartialResultError):
        cached.fetch_sync(request(*NAMES), client)
    client.answer['_shards'] = SHARDS
    assert cached.fetch_sync(request(*NAMES, search={'value': 'x'}), client).recordsTotal == 40
    assert len(client.counts) == 1


@pytest.mark.parametrize(
    ('answer', 'timed_out', 'shards'),
    [({'_shards': PARTIAL}, False, (2, 1, 1)), ({'timed_out': True}, True, (2, 2, 0))],
    ids=['a-shard-failed', 'timed-out'],
)
@pytest.mark.parametrize('through', ['search', 'count', 'async-search'])
async def test_a_partial_answer_is_logged_once_at_error_naming_the_index_and_the_shard_counts(
    caplog: pytest.LogCaptureFixture,
    answer: dict[str, Any],
    timed_out: bool,
    shards: tuple[int, int, int],
    through: str,
):
    """SPEC-E14.1: one record, with the counts and no value: the search text and the document's text are marked, and
    neither is anywhere in a record. The count answer is checked as the search answer is."""
    hit = {'_id': 'a', '_source': {'id': 'a', 'title': 'MARKER_HIT'}}
    client = AsyncClient(hits=[hit]) if through == 'async-search' else Client(hits=[hit])
    if through == 'count':
        client.count_answer = (
            {'count': 1, **answer} if '_shards' in answer else {'count': 1, '_shards': SHARDS, **answer}
        )
    else:
        client.answer.update(answer)
    params = request(*NAMES, search={'value': 'MARKER_SEARCH'})
    with caplog.at_level(logging.DEBUG), pytest.raises(PartialResultError):
        if through == 'async-search':
            await table.fetch(params, cast(AsyncClient, client))
        else:
            table.fetch_sync(params, client)
    (record,) = logged(caplog)
    assert (record.levelname, record.msg) == ('ERROR', 'opensearch_answer_partial')
    assert (record.__dict__['index'], record.__dict__['timed_out']) == ('docs', timed_out)
    assert (
        record.__dict__['shards_total'],
        record.__dict__['shards_successful'],
        record.__dict__['shards_failed'],
    ) == shards
    assert 'MARKER' not in repr(vars(record))
    assert 'MARKER' not in record.getMessage()


def broken(**answer: Any) -> Client:
    client = Client()
    client.answer.update(answer)
    return client


@pytest.mark.parametrize(
    ('client', 'unreadable'),
    [
        (lambda: broken(_shards=None), ['_shards']),
        (lambda: broken(_shards={'total': 'MARKER_TOTAL', 'successful': 2, 'failed': 0}), ['_shards.total']),
        (lambda: broken(timed_out='MARKER_TIMED'), ['timed_out']),
    ],
    ids=['no-shards', 'total-not-an-integer', 'timed-out-not-a-boolean'],
)
def test_an_unreadable_answer_raises_and_is_logged_once_naming_the_fields_never_their_values(
    caplog: pytest.LogCaptureFixture, client: Callable[[], Client], unreadable: list[str]
):
    """SPEC-E9.2 (the first two cases), SPEC-E14.2: the record names the fields it could not read and holds neither
    their values nor the search text."""
    with caplog.at_level(logging.DEBUG), pytest.raises(PartialResultError):
        table.fetch_sync(request(*NAMES, search={'value': 'MARKER_SEARCH'}), client())
    (record,) = logged(caplog)
    assert (record.levelname, record.msg) == ('ERROR', 'opensearch_answer_unverifiable')
    assert (record.__dict__['index'], record.__dict__['unreadable']) == ('docs', unreadable)
    assert 'MARKER' not in repr(vars(record))


def test_an_answer_with_no_shards_key_at_all_raises_too():
    """SPEC-E9.2: the key absent, not only null."""
    client = Client()
    del client.answer['_shards']
    with pytest.raises(PartialResultError):
        table.fetch_sync(request(*NAMES), client)


# -- filters that are not search: categorical values, regex refusals, the tree beside the searches (issue #62) ---

REGEX_FORMS = ['^a|b$', 'a.b', 'a*', '(a)', '[ab]', 'a{2}', r'\d', r'\w', r'\n', 'a+', 'a?', '^(a|b)']  # SPEC-F18.1


def clauses_of(**body: Any) -> list[dict[str, Any]]:
    client = Client()
    assert table.fetch_sync(request(*NAMES, **body), client).error is None
    return query(client)


@pytest.mark.parametrize(
    'search',
    [{'value': '', 'regex': True}, {'value': '', 'multi_values': []}],
    ids=['categorical', 'empty-list'],
)
def test_an_empty_categorical_filter_or_list_adds_no_clause(search: dict[str, Any]):
    """SPEC-F16.4, SPEC-F20.3: the request without the filter sends the same search, and an idle search is not
    counted: 65 columns that hold one are answered (PRD-F38 counts the searches that hold a value)."""
    assert clauses_of(column_search={'title': search}) == clauses_of() == []
    many = table.fetch_sync(request(*(('title',) * 65), column_search={'title': search}), Client())
    assert many.error is None


@pytest.mark.parametrize('pattern', REGEX_FORMS)
def test_a_regex_that_is_no_categorical_filter_is_refused_and_nothing_is_sent(pattern: str):
    """SPEC-F18.1: each form the statement names."""
    client = Client()
    refused(table.fetch_sync(request(*NAMES, column_search={'title': {'value': pattern, 'regex': True}}), client))
    assert (client.searches, client.counts) == ([], [])


def test_a_long_regex_that_is_no_categorical_filter_is_refused_as_a_regex_and_nothing_is_sent():
    """SPEC-F14.6: PRD-F14 measures a categorical alternative after unescaping, not the raw pattern; a long
    non-categorical regex measured as raw text answers `value_too_long` instead. PRD-F15 has no such clause: a
    control character in a regex column search stops being found if the regex is refused first as a form."""
    client = Client()
    search = {'value': 'a.' + 'x' * 256, 'regex': True}
    refused(table.fetch_sync(request(*NAMES, column_search={'title': search}), client))
    search = {'value': 'a.\x01', 'regex': True}
    refused(table.fetch_sync(request(*NAMES, column_search={'title': search}), client), 'invalid_character')
    assert (client.searches, client.counts) == ([], [])


@pytest.mark.parametrize('value', ['red', '^(red)$'])
def test_a_regex_global_search_with_a_word_is_refused_and_nothing_is_sent(value: str):
    """SPEC-F18.2."""
    client = Client()
    refused(table.fetch_sync(request(*NAMES, search={'value': value, 'regex': True}), client))
    assert (client.searches, client.counts) == ([], [])


@pytest.mark.parametrize('value', ['', '   ', '"'])
def test_a_regex_global_search_with_no_word_adds_no_clause(value: str):
    """SPEC-F18.3: PRD-F6 reduces these to nothing, so the flag has nothing to refuse."""
    assert clauses_of(search={'value': value, 'regex': True}) == clauses_of() == []


@pytest.mark.parametrize('text', ['nan', 'NaN', 'inf', '-inf', 'Infinity'])
def test_a_non_finite_number_is_refused_and_nothing_is_sent(text: str):
    """SPEC-F25.9: Python's `float` reads each as a number, and no field holds one."""
    client = Client()
    refused(table.fetch_sync(request(*NAMES, **criterion('pages', '=', text)), client), 'invalid_number')
    assert (client.searches, client.counts) == ([], [])


def test_the_searchbuilder_tree_is_one_more_clause_beside_the_searches():
    """SPEC-F12.3: the column search and the tree each send their clause alone, and together both, in the one `must`
    that ANDs them, the OR group included."""
    column = {'column_search': {'title': {'value': 'apple'}}}
    group = {
        'searchBuilder': {
            'logic': 'OR',
            'criteria': [
                {'condition': '=', 'origData': 'pages', 'value': ['12']},
                {'condition': 'null', 'origData': 'pages', 'value': []},
            ],
        }
    }
    for tree in (criterion('pages', '=', '12'), group):
        assert clauses_of(**column, **tree) == [*clauses_of(**column), *clauses_of(**tree)]


def test_a_column_the_request_marks_unsearchable_is_left_out_of_the_global_search_only():
    """SPEC-F41.3: the table declares `title` searchable, so the request's flag keeps it out of the global search
    and out of nothing else."""
    flags = {'title': {'searchable': False}}
    assert 'title' not in json.dumps(clauses_of(search={'value': 'apple'}, flags=flags))
    assert 'title' in json.dumps(clauses_of(search={'value': 'apple'}))
    column = {'column_search': {'title': {'value': 'apple'}}}
    assert clauses_of(**column, flags=flags) == clauses_of(**column) != []


@pytest.mark.parametrize('search', [{'value': 'x'}, {'multi_values': ['x']}])
def test_a_filter_with_a_value_on_an_undeclared_column_is_refused_and_an_empty_one_is_not(search: dict[str, Any]):
    """SPEC-F41.4: nothing is sent for the refusal, and an empty list is no filter (PRD-D6)."""
    names = (*NAMES, 'extra')
    client = Client()
    refused(table.fetch_sync(request(*names, column_search={'extra': search}), client))
    assert (client.searches, client.counts) == ([], [])
    assert table.fetch_sync(request(*names, column_search={'extra': {'multi_values': []}}), client).error is None
    assert query(client) == []


#: A column search with `regex` false whose text PRD-F6 reduces to no word
NO_WORD = ['   ', '"', '""', '"""', '!""', '!', '!x']
unsearchable_title = SearchTable(
    'docs',
    [IndexColumn('id', text='keyword'), IndexColumn('title', searchable=False)],
    unique_key='id',
    base_filter={'term': {'published': True}},
)
#: Where a column search with a word is refused: the table, the request's columns, the searched one, its other fields
#: and what the search holds beside the text
REFUSING = {
    'undeclared': (table, ('id', 'extra'), 'extra', {}, {}),
    'nameless': (table, ('id', ''), '', {'': {'data': None}}, {}),
    'unsearchable': (unsearchable_title, ('id', 'title'), 'title', {}, {}),
    'beside multi_values': (table, NAMES, 'title', {}, {'multi_values': ['x']}),
}


@pytest.mark.parametrize('place', REFUSING)
@pytest.mark.parametrize(
    ('search', 'idle'), [*(({'value': text}, True) for text in NO_WORD), ({'value': '   ', 'regex': True}, False)]
)
def test_a_column_search_with_no_word_is_idle_where_a_word_would_be_refused(
    place: str, search: dict[str, Any], idle: bool
):
    """SPEC-F6.6: the text adds nothing, so the request sends what it sends without it (beside a list, the list
    alone); with `regex: true` spaces are a categorical value (SPEC-F6.4), refused before anything is sent."""
    source, names, name, flags, beside = REFUSING[place]

    def sent(text: dict[str, Any]) -> tuple[DTResponse, Client]:
        client = Client()
        params = request(*names, column_search={name: {**beside, **text}}, flags=flags)
        return source.fetch_sync(params, client), client

    (response, client), (_, without) = sent(search), sent({})
    if idle:
        assert response.error is None, response.error
        assert client.searches == without.searches
    else:
        refused(response)
        assert client.searches == []


@pytest.mark.parametrize('name', ['title', 'extra'])
@pytest.mark.parametrize('text', ['!\x01', '!\t'])
def test_a_column_search_with_no_word_but_a_control_character_is_refused(name: str, text: str):
    """SPEC-F6.6 with PRD-F15: the word is dropped, but the control character is refused first, on a declared column
    and on an undeclared one alike, and nothing is sent."""
    client = Client()
    params = request('id', 'title', 'extra', column_search={name: {'value': text}})
    refused(table.fetch_sync(params, client), 'invalid_character')
    assert client.searches == []


# -- typed filters: what a condition the type does not take, an unfinished criterion and a list of numbers send ---


@pytest.mark.parametrize(
    ('column', 'conditions', 'values'),
    [
        ('title', ('<', '<=', '>', '>=', 'between', '!between'), ['b', 'm']),  # SPEC-F27.1
        ('pages', ('contains', '!contains', 'starts', '!starts', 'ends', '!ends'), ['3']),  # SPEC-F27.2
        ('created', ('starts', 'ends', '!starts', '!ends'), ['19']),  # SPEC-F27.3, SPEC-F27.8
    ],
)
def test_a_condition_the_type_does_not_take_is_refused_and_nothing_is_sent(
    column: str, conditions: tuple[str, ...], values: list[str]
):
    """SPEC-F27.1, SPEC-F27.2, SPEC-F27.3, SPEC-F27.8: `between` reads two values, the others one."""
    for condition in conditions:
        client = Client()
        body = criterion(column, condition, *(values if 'between' in condition else values[:1]))
        refused(table.fetch_sync(request(*NAMES, **body), client))
        assert (client.searches, client.counts) == ([], [])


@pytest.mark.parametrize(
    'unfinished',
    [
        pytest.param({'condition': '=', 'value': ['Ann']}, id='no column'),
        pytest.param({'condition': '=', 'origData': '', 'data': '', 'value': ['Ann']}, id='empty column'),
    ],
)
def test_a_criterion_with_no_column_adds_nothing(unfinished: dict[str, Any]):
    """SPEC-F30.2: alone it sends no clause, and beside a finished criterion, in an AND group and in an OR group, the
    search is that criterion's alone."""
    finished = {'condition': '=', 'origData': 'title', 'value': ['Apple pie']}
    assert clauses_of(searchBuilder={'logic': 'AND', 'criteria': [unfinished]}) == []
    expected = clauses_of(searchBuilder={'logic': 'AND', 'criteria': [finished]})
    assert expected != []
    for logic in ('AND', 'OR'):
        assert clauses_of(searchBuilder={'logic': logic, 'criteria': [finished, unfinished]}) == expected, logic


# -- ColumnControl's per-column search and list (PRD-F45) --------------------------------------------------------------


def _clauses(**body: Any) -> list[dict[str, Any]]:
    """What a request adds to the base filter, as the cluster is sent it."""
    client = Client()
    response = table.fetch_sync(request(*NAMES, **body), client)
    assert response.error is None, response.error
    return query(client)


@pytest.mark.parametrize(
    ('column', 'logic', 'value', 'equivalent'),
    [
        ('title', 'equal', 'Apple pie', criterion('title', '=', 'Apple pie')),
        ('title', 'notEqual', 'Apple pie', criterion('title', '!=', 'Apple pie')),
        ('title', 'starts', 'App', criterion('title', 'starts', 'App')),
        ('title', 'ends', 'pie', criterion('title', 'ends', 'pie')),
        ('title', 'notContains', 'pie', criterion('title', '!contains', 'pie')),
        ('title', 'empty', '', criterion('title', 'null')),
        ('title', 'notEmpty', '', criterion('title', '!null')),
        ('pages', 'equal', '12', criterion('pages', '=', '12')),
        ('pages', 'greater', '12', criterion('pages', '>', '12')),
        ('pages', 'greaterOrEqual', '12', criterion('pages', '>=', '12')),
        ('pages', 'less', '12', criterion('pages', '<', '12')),
        ('pages', 'lessOrEqual', '12', criterion('pages', '<=', '12')),
        ('created', 'equal', '2026-07-15', criterion('created', '=', '2026-07-15')),
        ('created', 'greater', '2026-07-15', criterion('created', '>', '2026-07-15')),
        ('created', 'greaterOrEqual', '2026-07-15', criterion('created', '>=', '2026-07-15')),
        ('created', 'less', '2026-07-15', criterion('created', '<', '2026-07-15')),
        ('created', 'lessOrEqual', '2026-07-15', criterion('created', '<=', '2026-07-15')),
        ('title', 'contains', 'red fox', {'column_search': {'title': {'value': 'red fox'}}}),
        ('pages', 'contains', '12', {'column_search': {'pages': {'value': '12'}}}),
        ('created', 'contains', '2026-07', {'column_search': {'created': {'value': '2026-07'}}}),
    ],
)
def test_columncontrol_sends_what_the_equivalent_condition_or_column_search_sends(
    column: str, logic: str, value: str, equivalent: dict[str, Any]
):
    """SPEC-F45.2, SPEC-F45.3, SPEC-F45.4, SPEC-F45.13, SPEC-F45.16: each logic is answered with the query of the
    SearchBuilder condition it names, and `contains` with that of a column search, on a column of text, of numbers and
    of days."""
    expected = _clauses(**equivalent)
    assert expected != []
    assert _clauses(flags=columncontrol(column, logic, value)) == expected


def test_a_columncontrol_list_sends_what_the_same_multi_values_send():
    """SPEC-F45.5: the list is the exact values of `multi_values` (PRD-F20), on text, on numbers and on days, and the
    empty entry means empty."""
    for column, values in (('title', ['Apple pie', '']), ('pages', ['12', '30']), ('created', ['2026-07-15'])):
        expected = _clauses(column_search={column: {'multi_values': values}})
        assert expected != []
        assert _clauses(flags=columncontrol_list(column, values)) == expected


def test_the_filters_of_one_column_are_three_clauses_and_none_replaces_another():
    """SPEC-F45.6: a plain search, a ColumnControl search and a ColumnControl list of one column are three clauses,
    none replacing another (PRD-F39 refuses a value beside a list inside one search, not across them)."""
    plain = _clauses(column_search={'title': {'value': 'apple'}})
    control = _clauses(flags=columncontrol('title', 'ends', 'pie'))
    listed = _clauses(column_search={'title': {'multi_values': ['Apple pie']}})
    flags = {'title': {'columnControl': {**columncontrol('title', 'ends', 'pie')['title']['columnControl']}}}
    flags['title']['columnControl']['list'] = {'0': 'Apple pie'}
    together = _clauses(column_search={'title': {'value': 'apple'}}, flags=flags)
    assert sorted(together, key=repr) == sorted([*plain, *control, *listed], key=repr)


def test_a_columncontrol_search_and_list_both_refused_are_answered_with_the_search_first():
    """SPEC-F45.6, PRD-E3: on analysed text `equal` is refused as a condition and a list as a list of exact values, both
    at step 4; the column's ColumnControl search is read before its ColumnControl list, so its message is the answer."""
    column = columncontrol('body', 'equal', 'red fox')['body']['columnControl']
    flags = {'body': {'columnControl': {**column, 'list': {'0': 'red fox'}}}}
    text = refused(table.fetch_sync(request(*NAMES, flags=flags), Client()))
    assert text.startswith('The condition "=" cannot be used on "body"')


@pytest.mark.parametrize('text', ['x', 'x' * 300])
def test_the_text_left_in_the_input_of_an_empty_search_is_not_sent(text: str):
    """SPEC-F45.7: `empty` and `notEmpty` carry no value: the clause is that of `null` and `!null`, whatever the
    input still holds, and a long text is no `value_too_long`."""
    assert _clauses(flags=columncontrol('title', 'empty', text)) == _clauses(**criterion('title', 'null'))
    assert _clauses(flags=columncontrol('title', 'notEmpty', text)) == _clauses(**criterion('title', '!null'))


@pytest.mark.parametrize('logic', ['equal', 'notEqual', 'starts', 'ends'])
def test_columncontrol_logics_that_need_a_whole_string_are_refused_on_analysed_text(logic: str):
    """SPEC-F45.8, SPEC-D19.1: `body` holds analysed text only; `contains`, `notContains`, `empty` and `notEmpty`
    are answered there and the others refused, before anything is sent; so is a list of values, except an empty one."""
    client = Client()
    refused(table.fetch_sync(request(*NAMES, flags=columncontrol('body', logic, 'red fox')), client))
    refused(table.fetch_sync(request(*NAMES, flags=columncontrol_list('body', ['red fox'])), client))
    assert (client.searches, client.counts) == ([], [])


@pytest.mark.parametrize(
    ('logic', 'equivalent'),
    [
        ('contains', {'column_search': {'body': {'value': 'red fox'}}}),
        ('notContains', criterion('body', '!contains', 'red fox')),
        ('empty', criterion('body', 'null')),
        ('notEmpty', criterion('body', '!null')),
    ],
)
def test_columncontrol_logics_answered_on_analysed_text_send_the_equivalent_body(
    logic: str, equivalent: dict[str, Any]
):
    """SPEC-F45.8, SPEC-D19.2: on `body`, analysed text only, `contains`, `notContains`, `empty` and `notEmpty` send what
    the column search or SearchBuilder condition they name sends, whose rows the live module pins (`!contains` on
    `body` in test_searchbuilder_conditions); a list holding only `""` sends that of the same `multi_values`."""
    expected = _clauses(**equivalent)
    assert expected != []
    assert _clauses(flags=columncontrol('body', logic, 'red fox')) == expected
    assert _clauses(flags=columncontrol_list('body', [''])) == _clauses(column_search={'body': {'multi_values': ['']}})


# -- the number kinds of an index column (PRD-D23, decision 011) ---

#: Each way a number reaches the query, as the keyword arguments of `request`.
PATHS: dict[str, Callable[[str], dict[str, Any]]] = {
    'equals': lambda text: criterion('n', '=', text),
    'not-equals': lambda text: criterion('n', '!=', text),
    'less': lambda text: criterion('n', '<', text),
    'less-or-equal': lambda text: criterion('n', '<=', text),
    'greater': lambda text: criterion('n', '>', text),
    'greater-or-equal': lambda text: criterion('n', '>=', text),
    'between': lambda text: criterion('n', 'between', text, text),
    'not-between': lambda text: criterion('n', '!between', text, text),
    'column-search': lambda text: {'column_search': {'n': {'value': text}}},
    'multi-values': lambda text: {'column_search': {'n': {'multi_values': [text, '7']}}},
    'columncontrol': lambda text: {'flags': columncontrol('n', 'equal', text)},
    'columncontrol-list': lambda text: {'flags': columncontrol_list('n', [text])},
}


def numbered(kind: NumberKind | None) -> SearchTable:
    column = IndexColumn('n', type='number', number=kind) if kind else Column('n', type='number')
    return SearchTable('docs', [IndexColumn('id', text='keyword'), column], unique_key='id')


def asked(kind: NumberKind | None, **body: Any) -> tuple[DTResponse, Client]:
    client = Client()
    return numbered(kind).fetch_sync(request('id', 'n', **body), client), client


@pytest.mark.parametrize(('kind', 'last', 'first'), NUMBER_EDGES)
def test_a_number_is_refused_exactly_where_opensearch_refuses_it_for_the_declared_kind(
    kind: NumberKind, last: str, first: str
):
    """SPEC-D23.1: a value the field answers is sent; one it refuses is answered with `invalid_number` and nothing is
    sent. The edges are the measured ones (decision 011; `float`'s exact edge is the live test's), but for the
    `half_float-through-float` pair, which straddles the edge (CI run 46091503895); `byte` and `short` carry an
    integer's, since OpenSearch answers 128 on a byte."""
    response, client = asked(kind, **criterion('n', '=', last))
    assert response.error is None
    assert len(client.searches) == 1
    response, client = asked(kind, **criterion('n', '=', first))
    text = refused(response, 'invalid_number')
    assert (client.searches, client.counts) == ([], [])
    assert f'{kind} field' in text
    assert KIND_BOUNDS[kind] in text


@pytest.mark.parametrize('path', PATHS)
@pytest.mark.parametrize(
    ('kind', 'last', 'first'),
    [number_edge('integer-high'), number_edge('half_float-high'), number_edge('unsigned_long-low')],
)
def test_every_way_a_number_reaches_the_query_is_checked(path: str, kind: NumberKind, last: str, first: str):
    """SPEC-D23.2: SearchBuilder conditions (a bound of a range, an equality, a negation), a column search, a
    `multi_values` list (OpenSearch's `terms`, which reads -1 on an `unsigned_long` as 2^64-1 and answers without an
    error) and both ColumnControl forms. The value the field answers goes through every one; the first it refuses is
    refused by every one."""
    response, client = asked(kind, **PATHS[path](last))
    assert response.error is None
    assert len(client.searches) == 1
    response, client = asked(kind, **PATHS[path](first))
    refused(response, 'invalid_number')
    assert client.searches == []


@pytest.mark.parametrize('kind', ['byte', 'short'])
@pytest.mark.parametrize('text', ['128', '40000', '-129'])
def test_a_byte_or_a_short_refuses_only_beyond_an_integer(kind: NumberKind, text: str):
    """SPEC-D23.3: OpenSearch answers 128 on a `byte` and 40000 on a `short` (`=` finds nothing, `<=` every row), so a
    refusal there would take away an answer the library gives without a kind."""
    response, client = asked(kind, **criterion('n', '<=', text))
    assert response.error is None
    assert len(client.searches) == 1


@pytest.mark.parametrize('kind', list(KIND_BOUNDS))
def test_the_refusal_never_holds_the_value(kind: NumberKind):
    """SPEC-D23.4: it names the field type and its range and the fix, never what the user sent."""
    value = '9' * 45 + '73'  # beyond every kind, and no part of any bound
    text = refused(asked(kind, **criterion('n', '=', value))[0], 'invalid_number')
    assert value not in text
    assert 'within that range' in text


def test_a_value_past_a_fraction_that_rounds_to_the_edge_is_refused_as_it_is_sent():
    """SPEC-D23.5: a number with a point goes as a float, so 2^63 - 0.5 is the float 2^63, which OpenSearch refuses on a
    `long`, though the exact number is inside the range. The check is on what is sent."""
    refused(asked('long', **criterion('n', '=', '9223372036854775807.5'))[0], 'invalid_number')
    refused(asked('long', **criterion('n', '=', '9223372036854775806.5'))[0], 'invalid_number')
    assert asked('long', **criterion('n', '=', '9223372036854774784.5'))[0].error is None  # a float below 2^63


def test_a_column_without_a_kind_sends_what_the_plan_lets_through():
    """SPEC-D23.6, PRD section 5: 2^64 reaches the server, which refuses it on a `long` or an `integer`; no kind, no
    check beyond the finite double the plan insists on."""
    response, client = asked(None, **criterion('n', '=', str(2**64)))
    assert response.error is None
    assert client.searches[0]['query']['bool']['must'][0] == {'term': {'n': 2**64}}


@pytest.mark.parametrize(
    ('kind', 'path', 'text'),
    [
        # the exact zero, where no `-1e-400` row stands for it: a lower bound without a kind, and an integer kind
        (None, 'greater-or-equal', '-0.00'),
        ('long', 'equals', '-0.00'),
        # `-1e-400` covers an equality, each bound and a list: a `terms` list is built apart from a bound or a `term`
        *((None, path, '-1e-400') for path in ('equals', 'less', 'less-or-equal', 'greater', 'multi-values')),
        (None, 'equals', '1e-400'),  # the one spelling `0`, where the double is 0.0 and not -0.0
        (None, 'equals', '-2e-324'),  # below half of the smallest subnormal double, so its double is -0.0
        # a column that declares a float kind: its numbers could be converted apart from `_json`
        ('float', 'equals', '-1e-400'),
        ('half_float', 'equals', '-1e-400'),
    ],
)
def test_a_number_whose_double_is_zero_sends_the_body_of_zero(kind: NumberKind | None, path: str, text: str):
    """SPEC-F25.15: `-0.00` is the number zero, and `-1e-400` is not zero but its double is -0.0, which a `double`
    field keeps apart from 0.0 in a `term` and a `range`; PRD-F25 sends a number whose double is zero as `0`, so every
    such number is sent as `0`, in an equality, a bound and a list, on a column without a kind (a `double` field), and
    `-1e-400` on a column declared `float` or `half_float`, where its double is the same -0.0 as `-0.00`'s. A `-0.00`
    row stays where no `-1e-400` row stands for it: `>=` without a kind, and `=` on a `long`, which shows the exact
    zero on an integer kind (one row, since every path reaches the kind in one place). The bodies are compared as
    JSON, since Python's -0.0 equals 0. It catches a number sent as the float -0.0, also one converted for a float
    kind apart from `_json` (a conversion that treats the exact zero right and `-1e-400` wrong fails the two `-1e-400`
    rows of the float kinds); the `1e-400` row pins the one spelling `0` (OpenSearch reads 0.0 the same). The
    `less-or-equal` row is not the `less` row again: a rule skipped for an inclusive upper bound alone passes the
    `less` row and fails this one; the `greater` row adds the exclusive lower bound and `-1e-400` as a lower bound,
    where the `>=` row already catches a rule skipped for one."""
    zero, tiny = (asked(kind, **PATHS[path](value))[1].searches for value in ('0', text))
    assert len(zero) == 1
    assert json.dumps(tiny) == json.dumps(zero)


@pytest.mark.parametrize('text', ['-5e-324', '3e-324'])
def test_a_number_whose_double_is_not_zero_is_sent_as_that_double(text: str):
    """SPEC-F25.15: a subnormal double is not zero. `5e-324` is the smallest one and `3e-324` rounds up to it (zero
    would be `2e-324`, below half of it), so each is sent as the double it is, not as `0`. The negative row catches a
    rule for negatives alone, the positive row one for positives alone and one that sends `0` up to the smallest
    subnormal itself, and both a rule that sends `0` for every tiny number, subnormals included: no other test sends
    a subnormal."""
    sent = asked(None, **criterion('n', '=', text))[1].searches[0]['query']['bool']['must'][0]
    assert sent == {'term': {'n': float(text)}}


@pytest.mark.parametrize(('kind', 'text'), [('float', '-1e-50'), ('half_float', '-1e-10')])
def test_a_number_that_is_zero_only_in_the_format_of_a_float_field_is_sent_as_it_is(kind: NumberKind, text: str):
    """SPEC-F25.15: `-1e-50` is a nonzero double that a `float` field rounds to -0.0 in a `term` (`-1e-10` on a
    `half_float`), and the library sends it as it is, as a column without a kind does, not as `0`: the field's own
    rounding gives the answers of docs/databases.md (the matrix of CI runs 46093159275 and 46093367670), the exact one
    for a tiny negative, where `0` would give the false one. It catches a rule that sends such a number as `0`; the
    live test of the same numbers, the positive ones too, pins what the field answers."""
    kept = asked(kind, **criterion('n', '=', text))[1].searches
    assert json.dumps(kept) == json.dumps(asked(None, **criterion('n', '=', text))[1].searches)
    assert json.dumps(kept) != json.dumps(asked(None, **criterion('n', '=', '0'))[1].searches)


def test_the_floor_is_a_finite_double_and_at_most_255_characters_whatever_the_kind():
    """SPEC-D23.7: a number the plan reads holds at most 255 characters, so a double holds it; `1e400` is no finite
    double. Every number column has the floor, with or without a kind."""
    assert asked(None, **criterion('n', '=', '9' * 255))[1].searches
    refused(asked(None, **criterion('n', '=', '9' * 256))[0], 'value_too_long')
    refused(asked(None, **criterion('n', '=', '1e400'))[0], 'invalid_number')


def test_a_number_mistake_waits_for_an_earlier_step_of_the_plan_in_a_search_builder_too():
    """SPEC-D23.8: the SearchBuilder is built in one place with the column searches; a range mistake (step 3) found
    there must not be raised on the spot while the plan holds a date mistake (step 2) beside it."""
    columns = [Column('created', type='date'), IndexColumn('n', type='number', number='integer')]
    searched = SearchTable('docs', columns, unique_key='n')
    tree = {
        'searchBuilder': {
            'logic': 'AND',
            'criteria': [
                {'condition': '=', 'origData': 'created', 'value': ['xx']},
                {'condition': '=', 'origData': 'n', 'value': ['2147483648']},
            ],
        }
    }
    refused(searched.fetch_sync(request('created', 'n', **tree), Client()), 'invalid_date')


def test_within_step_3_the_last_day_comes_before_a_number_the_field_type_refuses():
    """SPEC-E3.13: 2147483648 is past an `integer` field (PRD-D23), and `< 9999-12-31` uses the maximum date every
    date column has as a bound; both are of step 3, and the maximum date is answered in either order, with nothing
    sent. Catches step 3 answered in read order alone, which answers `invalid_number` when the number is read first."""
    searched = SearchTable(
        'docs', [Column('created', type='date'), IndexColumn('n', type='number', number='integer')], unique_key='n'
    )
    number = {'condition': '=', 'origData': 'n', 'value': ['2147483648']}
    day = {'condition': '<', 'origData': 'created', 'value': ['9999-12-31']}
    refused(
        searched.fetch_sync(request('created', 'n', searchBuilder={'criteria': [number]}), Client()), 'invalid_number'
    )
    for criteria in ([number, day], [day, number]):
        client = Client()
        refused(
            searched.fetch_sync(request('created', 'n', searchBuilder={'criteria': criteria}), client), 'invalid_date'
        )
        assert (client.searches, client.counts) == ([], [])


def test_a_kind_on_a_column_that_is_no_number_column_is_a_schema_error_that_names_the_fix():
    """SPEC-D23.9: raised when the table is built; the message names the column and what to declare."""
    with pytest.raises(SchemaError, match=r'"title".*type="number"'):
        SearchTable('docs', [IndexColumn('id', text='keyword'), IndexColumn('title', number='long')], unique_key='id')
    with pytest.raises(SchemaError, match='"created"'):
        SearchTable(
            'docs',
            [IndexColumn('id', text='keyword'), IndexColumn('created', type='date', number='long')],
            unique_key='id',
        )
