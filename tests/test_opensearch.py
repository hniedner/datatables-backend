"""The OpenSearch backend: the request bodies it sends, and what it makes of the answers.

A recording client stands in for the cluster. Whether OpenSearch evaluates the bodies as intended belongs to the
container tests of milestone 2; these pin down what is sent.
"""

from collections.abc import Mapping
import logging
from typing import Any

import pytest

from datatables_backend import Column
from datatables_backend import DTParams
from datatables_backend import DTResponse
from datatables_backend import PartialResultError
from datatables_backend import RowHooks
from datatables_backend import SchemaError
from datatables_backend import TotalCache
from datatables_backend.opensearch import IndexColumn
from datatables_backend.opensearch import SearchTable

from .conftest import criterion
from .conftest import exact_values
from .conftest import flat
from .conftest import nested
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


async def test_the_async_fetch_answers_a_user_error_too():
    """SPEC-E1.1 says `fetch` and `fetch_sync`: each has its own place where the mistake becomes an answer, and
    nothing is sent from either."""
    client = AsyncClient()
    response = await table.fetch(request(*NAMES, **criterion('pages', '=', 'abc')), client)
    assert refused(response, 'invalid_number')
    assert (client.searches, client.counts) == ([], [])


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
    refused(response, 'page_out_of_range')
    assert (client.searches, client.counts) == ([], [])


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
            SearchTable('docs', [Column('pages', type='number'), Column('created', type='date')]),
            ('pages', 'created'),
            {'search': {'value': 'x'}},
            'invalid_filter',
            'No column of this table can be searched from the search box. Clear it and use a column filter instead.',
        ),
        (
            SearchTable('docs', COLUMNS, max_result_window=3),  # the number in the text is the table's own
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
    """SPEC-E4.23, SPEC-E4.25, SPEC-E4.6, SPEC-E4.15, SPEC-E4.24: the texts of the mistakes only the OpenSearch build finds. Those the plan
    finds are asserted once, in tests/test_sql.py."""
    assert refused(searched.fetch_sync(request(*names, **body), Client()), code) == text


def test_a_filter_mistake_comes_before_a_page_beyond_the_result_window():
    """SPEC-E3.5: step 4 before step 5, so the user first hears of the filter they can change."""
    params = request(*FLAG_NAMES, start=9995, length=10, **criterion('active', '=', 'yes'))
    assert 'true or false' in refused(flags.fetch_sync(params, Client()))


@pytest.mark.parametrize('found_in_the_build', ['a column search that is no number', 'no column to search'])
def test_a_mistake_found_while_the_body_is_built_waits_for_an_earlier_step_of_the_plan(found_in_the_build: str):
    """PRD-E3 across the two places a mistake is found: one from the build must not be raised on the spot, since the
    plan may hold one of a lower step."""
    if found_in_the_build == 'a column search that is no number':  # step 2, beside a control character (step 1)
        searched = table
        params = request(*NAMES, column_search={'pages': {'value': 'abc'}}, **criterion('title', '=', 'a\x00b'))
        expected = 'invalid_character'
    else:  # step 4: only a number and a date column, which take no free text; beside a date of the wrong format
        searched = SearchTable('docs', [Column('pages', type='number'), Column('created', type='date')])
        params = request('pages', 'created', search={'value': 'x'}, **criterion('created', '=', 'xx'))
        expected = 'invalid_date'
    refused(searched.fetch_sync(params, Client()), expected)


@pytest.mark.xfail(
    reason='PRD-C11, PRD-C12: the counts are not run before the refusal yet, so it carries none (SPEC-C12.1, issue '
    '#29)',
    strict=True,
    raises=AssertionError,
)
def test_a_page_out_of_range_carries_the_real_counts():
    small = SearchTable('docs', COLUMNS, max_result_window=3)
    response = small.fetch_sync(request(*NAMES, start=4, length=3), Client(total=10, count=10))
    assert (response.error_code, response.recordsTotal, response.recordsFiltered) == ('page_out_of_range', 10, 10)


@pytest.mark.xfail(
    reason='PRD-C3: a start past the end beyond the result window is refused, not answered with an empty page and '
    'both counts (SPEC-C3.3, issue #29)',
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


def test_the_global_search_fits_each_string_mapping_and_skips_numbers_and_dates():
    client = Client()
    table.fetch_sync(request(*NAMES, search={'value': 'a*b'}), client)
    (clause,) = query(client)
    should = clause['bool']['should']
    assert should[0] == {'wildcard': {'id': {'value': '*a\\*b*', 'case_insensitive': True}}}
    assert should[1]['bool']['should'] == [
        {'match_phrase': {'title': 'a*b'}},
        {'wildcard': {'title.keyword': {'value': '*a\\*b*', 'case_insensitive': True}}},
    ]
    assert should[2] == {'match_phrase': {'body': 'a*b'}}
    assert len(should) == 4  # id, title, body, author


def _in_column(word: str) -> list[dict[str, Any]]:
    """What one word asks of each searched column of NAMES, in column order: id (keyword), title (text+keyword), body
    (text), author (text+keyword)."""

    def wildcard(field: str) -> dict[str, Any]:
        return {'wildcard': {field: {'value': f'*{word}*', 'case_insensitive': True}}}

    def both(field: str) -> dict[str, Any]:
        return {'bool': {'should': [{'match_phrase': {field: word}}, wildcard(f'{field}.keyword')],
                         'minimum_should_match': 1}}  # fmt: skip

    return [wildcard('id'), both('title'), {'match_phrase': {'body': word}}, both('meta.author')]


def test_each_word_of_the_global_search_must_be_found_in_some_searched_column():
    """SPEC-F1.1: one clause per word, each an alternative over the columns, so the words may sit in different ones."""
    client = Client()
    table.fetch_sync(request(*NAMES, search={'value': 'red 7'}), client)
    assert query(client) == [
        {
            'bool': {
                'must': [
                    {'bool': {'should': _in_column('red'), 'minimum_should_match': 1}},
                    {'bool': {'should': _in_column('7'), 'minimum_should_match': 1}},
                ]
            }
        }
    ]


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


@pytest.mark.parametrize('condition', ['search', 'contains'])
def test_a_word_the_analyser_splits_is_a_phrase_match_on_analysed_text(condition: str):
    """SPEC-F11.7: a `match` of `a-b` is an OR of its tokens, which also finds text holding `a` alone. The word of a
    column search and SearchBuilder `contains` compile alike; the live test shows the rows."""
    client, params = (
        Client(),
        request(*NAMES, **(criterion('body', 'contains', 'a-b') if condition == 'contains' else {})),
    )
    if condition == 'search':
        params.columns[2].search.value = 'a-b'
    table.fetch_sync(params, client)
    assert query(client) == [{'match_phrase': {'body': 'a-b'}}]


@pytest.mark.parametrize('column', ['pages', 'created'])
def test_a_column_search_with_no_word_sends_no_clause_on_a_number_or_a_date(column: str):
    """SPEC-F6.2: a number or a date is read as one value, so `"` would be refused as unreadable; it is no word."""
    client, params = Client(), request(*NAMES)
    params.columns[NAMES.index(column)].search.value = '"'
    assert table.fetch_sync(params, client).error is None
    assert query(client) == []


def test_search_text_over_the_word_bound_is_refused_before_anything_is_sent():
    """SPEC-F14.2: the bound is the plan's, so OpenSearch refuses as SQL does."""
    client = Client()
    text = ' '.join(f'q{n}' for n in range(17))
    assert '16' in refused(table.fetch_sync(request(*NAMES, search={'value': text}), client))
    assert (client.searches, client.counts) == ([], [])


def test_a_global_search_over_numbers_and_dates_only_is_refused():
    numbers = SearchTable('docs', [Column('pages', type='number'), Column('created', type='date')])
    refused(numbers.fetch_sync(request('pages', 'created', search={'value': '3'}), Client()))


def test_column_searches_follow_the_columns_type():
    client, params = Client(), request(*NAMES)
    params.columns[2].search.value = 'granny smith'
    params.columns[3].search.value = '12'
    params.columns[4].search.value = '2026-02'
    table.fetch_sync(params, client)
    text, number, date = query(client)
    assert text == {'bool': {'must': [{'match_phrase': {'body': 'granny'}}, {'match_phrase': {'body': 'smith'}}]}}
    assert number == {'term': {'pages': 12}}
    assert date == {'range': {'created': {'gte': '2026-02-01', 'lt': '2026-03-01', 'format': 'strict_date'}}}


def test_a_date_column_search_reads_a_day_with_a_time_after_it_as_that_day():
    """SPEC-F34.1: the time comes after a space, and it is one date term all the same, not several (PRD-F34)."""
    client, params = Client(), request(*NAMES)
    params.columns[4].search.value = '2026-07-15 10:00:00'
    table.fetch_sync(params, client)
    assert query(client) == [{'range': {'created': {'gte': '2026-07-15', 'lt': '2026-07-16', 'format': 'strict_date'}}}]


def test_a_multi_select_is_a_term_query_in_any_case_for_each_value_on_the_whole_string():
    """PRD-F9 (SPEC-F9.3): a `terms` query takes no `case_insensitive`."""
    client = Client()
    table.fetch_sync(request(*NAMES, column_search={'title': {'value': 'a|b', 'regex': True}}), client)
    assert query(client) == [
        {
            'bool': {
                'should': [
                    {'term': {'title.keyword': {'value': 'a', 'case_insensitive': True}}},
                    {'term': {'title.keyword': {'value': 'b', 'case_insensitive': True}}},
                ],
                'minimum_should_match': 1,
            }
        }
    ]


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
    plain = SearchTable('docs', [IndexColumn('id', text='keyword'), unfolded])
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


def test_a_value_longer_than_a_search_text_may_be_keeps_its_query():
    """SPEC-F9.18: a value of up to 255 code points, the longest a search text may be (PRD-F14) and the longest
    measured as a `regexp` on every server, is one; a longer value is the `term` it was before. The letter here is
    outside the BMP and the rest are dots, each of which gets a backslash: the length is the value's, in code
    points, not the pattern's."""
    deseret, client = chr(0x10400), Client()
    for dots in (254, 255):
        table.fetch_sync(request(*NAMES, **criterion('id', '=', deseret + '.' * dots)), client)
    fits, too_long = (search['query']['bool']['must'][1] for search in client.searches)
    assert fits == {'regexp': {'id': {'value': deseret + '\\.' * 254, 'case_insensitive': True}}}
    assert too_long == {'term': {'id': {'value': deseret + '.' * 255, 'case_insensitive': True}}}


FULLWIDTH_MARKS = [chr(0xFF0A), chr(0xFF1F), chr(0xFF3C)]  # the fullwidth asterisk, question mark and backslash


@pytest.mark.parametrize('mark', FULLWIDTH_MARKS)
def test_a_search_for_a_fullwidth_mark_is_a_regexp_with_the_mark_behind_a_backslash(mark: str):
    """SPEC-F9.19: a normalizer can make the mark an ASCII `*`, `?` or `\\`, which a `wildcard` query then reads as
    its own syntax: the search finds too much, or misses its own row. A row turns red when its mark is sent in a
    `wildcard` again."""
    client = Client()
    table.fetch_sync(request(*NAMES, **criterion('id', 'contains', f'a{mark}b')), client)
    assert query(client) == [{'regexp': {'id': {'value': f'.*a\\{mark}b.*', 'case_insensitive': True}}}]


def test_a_fullwidth_mark_changes_no_exact_match_and_no_value_too_long_for_a_regexp():
    """SPEC-F9.19: an exact match is a `term`, which the server compares as a whole, so it stays one; and a search
    of more than 255 code points keeps its `wildcard`, as SPEC-F9.18 has it for every value."""
    client, mark = Client(), FULLWIDTH_MARKS[0]
    table.fetch_sync(request(*NAMES, **criterion('id', '=', f'a{mark}b')), client)
    table.fetch_sync(request(*NAMES, **criterion('id', 'contains', mark + 'a' * 255)), client)
    exact, too_long = (search['query']['bool']['must'][1] for search in client.searches)
    assert exact == {'term': {'id': {'value': f'a{mark}b', 'case_insensitive': True}}}
    assert too_long == {'wildcard': {'id': {'value': f'*{mark}{"a" * 255}*', 'case_insensitive': True}}}


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
        (
            # Today's result, until issue #28: PRD-D18 asks for this condition to be refused on a hooked column,
            # which is not met yet. Every search of a hooked column changes with #28, so no other path stands in.
            Column('secret', search_value=str.upper),
            'starts',
            {'wildcard': {'secret.keyword': {'value': 'Ä*', 'case_insensitive': True}}},
        ),
    ],
)
def test_a_cased_letter_outside_ascii_on_a_column_the_library_does_not_fold(
    unfolded: Column, condition: str, clause: dict[str, Any]
):
    """SPEC-F9.15: the regexp is for the library's own fold. An exact match on a `fold_case=False` column is sent
    as before, and a search on it folds. A hooked column's search is sent as it was before the `regexp` form: the
    row pins that, not the search itself, which PRD-D18 does away with."""
    plain = SearchTable('docs', [IndexColumn('id', text='keyword'), unfolded])
    client = Client()
    plain.fetch_sync(request('id', 'secret', **criterion('secret', condition, 'ä')), client)
    assert client.searches[-1]['query']['bool']['must'] == [clause]


# -- SearchBuilder ---


@pytest.mark.parametrize(
    ('column', 'condition', 'values', 'clause'),
    [
        ('id', '=', ['A1'], {'term': {'id': {'value': 'A1', 'case_insensitive': True}}}),  # SPEC-F9.2
        ('title', '!=', ['T'], {'bool': {'must_not': [{'term': {'title.keyword': {'value': 'T', 'case_insensitive': True}}}]}}),
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
    terms = [{'term': {'id': {'value': value, 'case_insensitive': True}}} for value in ('a', 'b')]
    assert query(client) == [{'bool': {'should': terms, 'minimum_should_match': 1}}]


def test_an_undeclared_field_is_refused_before_anything_is_sent():
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


# -- sorting ---


def test_sorting_uses_the_whole_string_and_puts_a_missing_value_lowest():
    """SPEC-O7.1, SPEC-O2.1: left to itself OpenSearch puts a document without the field last in either direction."""
    client = Client()
    order = [{'column': 1, 'dir': 'desc'}, {'column': 3, 'dir': 'asc'}]
    table.fetch_sync(request(*NAMES, order=order), client)
    assert client.searches[0]['sort'] == [
        {'title.keyword': {'order': 'desc', 'missing': '_last'}},
        {'pages': {'order': 'asc', 'missing': '_first'}},
    ]


def test_an_order_on_analysed_text_is_refused_or_skipped_as_the_request_marks_it():
    """SPEC-O5.1: `body` has no whole string to sort by, and OpenSearch fails a search that sorts by analysed text."""
    client = Client()
    order = [{'column': 2, 'dir': 'asc'}, {'column': 3, 'dir': 'asc'}]
    refused(table.fetch_sync(request(*NAMES, order=order), client))
    assert client.searches == []
    table.fetch_sync(request(*NAMES, flags={'body': {'orderable': False}}, order=order), client)
    assert client.searches[0]['sort'] == [{'pages': {'order': 'asc', 'missing': '_first'}}]


def test_analysed_text_declared_orderable_is_a_schema_error():
    """SPEC-O5.2. Declared unorderable, which it is anyway, it is no mistake."""
    with pytest.raises(SchemaError, match='body'):
        SearchTable('docs', [IndexColumn('id', text='keyword'), IndexColumn('body', text='text', orderable=True)])
    SearchTable('docs', [IndexColumn('id', text='keyword'), IndexColumn('body', text='text', orderable=False)])


def test_an_order_on_a_column_declared_unorderable_is_refused():
    """SPEC-O1.2, SPEC-D5.1: the declaration decides on this backend too, for a column the index could sort by."""
    fixed = SearchTable('docs', [IndexColumn('id', text='keyword', orderable=False)])
    client = Client()
    refused(fixed.fetch_sync(request('id', order=[{'column': 0, 'dir': 'asc'}]), client))
    assert client.searches == []


@pytest.mark.xfail(
    reason='PRD-O3, PRD-O4, PRD-D11, issue #29: an OpenSearch table declares no unique key yet, so no tie-breaker '
    'follows the order and a request without an order is sent without a sort',
    strict=True,
    raises=AssertionError,
)
def test_ties_are_broken_by_a_unique_key():
    client = Client()
    table.fetch_sync(request(*NAMES), client)
    assert client.searches[0].get('sort') == [{'id': {'order': 'asc'}}]


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
