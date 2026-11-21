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
from opensearchpy.exceptions import ConnectionError as NoConnection
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


def server_version() -> tuple[int, int]:
    """The server's major and minor version, asked once while the module is read: what a case-insensitive `regexp`
    folds depends on it (PRD-F9), so some expected rows do."""
    probe = OpenSearch(hosts=[URL])
    try:
        major, minor = probe.info()['version']['number'].split('.')[:2]
    except NoConnection:
        raise RuntimeError('No OpenSearch server answers at the address DTB_OPENSEARCH_URL names') from None
    finally:
        probe.close()
    return int(major), int(minor)


# From OpenSearch 3.1 on, a case-insensitive `regexp` folds letters outside ASCII; 3.0 and 2.19 fold ASCII only.
FOLDS_BEYOND_ASCII = bool(URL) and server_version() >= (3, 1)

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
LONGEST = ('ABCDEFGHIJKLMNOPQRSTUVWXYZ' * 10)[:255]  # as many code points as PRD-F14 lets a search text hold
LONGEST_BEYOND_ASCII = 'Ä' * 255  # the same length in one cased letter outside ASCII, the hardest text for a pattern
# A value whose regular expression is long, 514 UTF-16 units as a server counts them: a letter outside the BMP, which
# counts as two, and a backslash before each of 254 dots.
LONGEST_PATTERN = chr(0x10400) + '.' * 254
# Every ASCII character a value may hold that is not a letter or a digit: line feed and carriage return, the two
# control characters PRD-F15 lets through, then the space and the punctuation.
PUNCTUATION = '\n\r' + ''.join(char for char in map(chr, range(32, 127)) if not char.isalnum())
# Of the characters that are operators of a regular expression without a backslash (measured on 3.10.0 and 2.19.7),
# the three whose missing backslash the row with all of PUNCTUATION does not show. a1 holds each between Ä and Z,
# and b2 a value that `@` (any string) and, on OpenSearch 2.19 only, `~` (not what follows) match as operators.
OPERATORS = '@\\~'
ESCAPED = [*(f'Ä{char}Z' for char in OPERATORS), f'Ä{PUNCTUATION}Z']
DECOY = 'ÄXZ'
DOCUMENTS: list[dict[str, Any]] = [
    {'id': 'a1', 'title': 'Apple pie', 'body': 'granny smith apples', 'pages': 12, 'created': '2026-07-15',
     'meta': {'author': 'Ann'}, 'active': True, 'label': ESCAPED},
    {'id': 'b2', 'title': 'Banana bread', 'body': 'ripe bananas and green tea', 'pages': 30, 'created': '2026-07-16',
     'meta': {'author': 'Bob'}, 'active': False, 'label': DECOY},
    {'id': 'c3', 'title': 'Cherry tart 100%', 'body': 'sour cherries', 'label': LONGEST},
    {'id': 'd4', 'title': 'apple crumble', 'body': 'green apples', 'pages': 12, 'created': '2025-01-01',
     'meta': {'author': 'Cy'}, 'active': True, 'label': [LONGEST_BEYOND_ASCII, LONGEST_PATTERN, 'bär']},
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
    """DTB_OPENSEARCH_VERSION (the CI job sets it for each leg) names the server the run must be on: an image pinned
    by digest runs that digest, whatever version its tag says."""
    version = client.info()['version']
    assert version['distribution'] == 'opensearch'
    assert version['number'] == os.environ.get('DTB_OPENSEARCH_VERSION', version['number'])


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
    ('body', 'expected'),
    [
        ({'search': {'value': 'äpfel'}}, ['e5']),  # SPEC-F9.9: a whole word, which the analysed text folds
        ({'search': {'value': 'äpf'}}, ['e5'] if FOLDS_BEYOND_ASCII else []),  # SPEC-F9.10: a substring
        ({'search': {'value': 'ÄPF'}}, ['e5']),  # and is found in its own case on every server
        (criterion('label', '=', 'äpfel'), ['e5'] if FOLDS_BEYOND_ASCII else []),  # SPEC-F9.11: an exact match
        (criterion('label', '=', 'ÄPFEL'), ['e5']),
        (criterion('label', '=', 'BÄR'), ['d4'] if FOLDS_BEYOND_ASCII else []),  # stored small, searched capital
    ],
)
def test_a_letter_outside_ascii_is_folded_from_opensearch_3_1_on(
    client: OpenSearch, body: dict[str, Any], expected: list[str]
):
    """PRD-F9: a substring and an exact match in another case find `ÄPFEL` from OpenSearch 3.1 on, whose
    case-insensitive `regexp` folds letters outside ASCII (measured on 3.1.0 to 3.10.0); on 3.0 and 2.19 it folds
    ASCII letters only (measured on 3.0.0 and 2.19.7), and they miss it. A row turning red means the server folds
    differently, and PRD-F9 says so no longer."""
    assert found(labelled.fetch_sync(request(*LABEL_NAMES, **body), client)) == expected


whole_label = SearchTable(
    INDEX, [IndexColumn('id', text='keyword'), IndexColumn('label', field='label.keyword', text='keyword')]
)  # the label as one whole string only, so no analysed word can answer for it: c3 holds LONGEST


@pytest.mark.parametrize('condition', ['=', 'contains'])
def test_the_longest_search_text_is_answered_in_any_case(client: OpenSearch, condition: str):
    """PRD-F14, PRD-F9 (SPEC-F9.17): a measurement of the server, not of the `regexp` form: what it does with the
    longest text of ASCII letters a search may hold, as a case-insensitive `term` (`=`) and a case-insensitive
    `wildcard` (`contains`; `starts` and `ends` are the same query). OpenSearch's documentation warns that on 2.x
    and earlier the cost of a case-insensitive query can grow exponentially with the number of characters. A row
    turns red when a server refuses or fails such a query at 255 letters, or stops folding at that length."""
    params = request(*LABEL_NAMES, **criterion('label', condition, LONGEST.lower()))
    assert found(whole_label.fetch_sync(params, client)) == ['c3']


@pytest.mark.parametrize('value', [LONGEST_BEYOND_ASCII, LONGEST_PATTERN])
def test_the_longest_regular_expression_is_answered(client: OpenSearch, value: str):
    """PRD-F14, PRD-F9 (SPEC-F9.17): a value with a cased letter outside ASCII is a `regexp` query, up to 255 code
    points; both rows are that long, as a substring, open at both ends. The first is one folding letter repeated,
    the text a server finds hardest to turn into an automaton (600 such letters it refuses). The second is a
    pattern of 514 UTF-16 units, which is what a server counts (two for the letter, a backslash before each of 254
    dots), against the 1,000 it allows by default (`index.max_regex_length`); it also turns red when a dot loses
    its backslash. Searched in the stored case, so every server finds it. A row turns red when a server refuses or
    fails such a query."""
    params = request(*LABEL_NAMES, **criterion('label', 'contains', value))
    assert found(whole_label.fetch_sync(params, client)) == ['d4']


@pytest.mark.parametrize('operator', OPERATORS)
def test_a_character_of_the_regular_expression_syntax_in_a_value_is_that_character(client: OpenSearch, operator: str):
    """SPEC-F9.16: a1 holds Ä, the character, Z. Without its backslash the server rejects the pattern (`\\`) or
    finds b2 as well (`@`, and `~` on OpenSearch 2.19 only: the 2.19 leg is the one that turns red for it)."""
    params = request(*LABEL_NAMES, **criterion('label', '=', f'Ä{operator}Z'))
    assert found(whole_label.fetch_sync(params, client)) == ['a1']


def test_punctuation_in_a_value_is_matched_as_written(client: OpenSearch):
    """SPEC-F9.16: every ASCII character that is not a letter or a digit, in one value. It turns red when a server
    rejects or misreads a backslash before any of them, or when one that is an operator has lost its backslash."""
    params = request(*LABEL_NAMES, **criterion('label', '=', f'Ä{PUNCTUATION}Z'))
    assert found(whole_label.fetch_sync(params, client)) == ['a1']


@pytest.mark.xfail(
    reason='PRD-F38, issue #27: a list is not capped at 256 values yet, and a string list is one term query for each '
    'value (PRD-F9), so past the cluster\'s 1,024 clauses ("maxClauseCount is set to 1024", what 3.10.0 and 2.19.7 answer) the '
    'search fails where a `terms` query answered',
    strict=True,
    raises=RequestError,
)
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
