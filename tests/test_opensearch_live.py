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
from contextlib import contextmanager
import os
import time
from typing import Any

from opensearchpy import AsyncOpenSearch
from opensearchpy import OpenSearch
from opensearchpy.exceptions import ConnectionError as NoConnection
from opensearchpy.exceptions import RequestError
import pytest

from datatables_backend import Column
from datatables_backend import DTResponse
from datatables_backend import TotalCache
from datatables_backend.opensearch import IndexColumn
from datatables_backend.opensearch import NumberKind
from datatables_backend.opensearch import SearchTable

from .conftest import NUMBER_EDGES
from .conftest import OPENSEARCH_URL as URL
from .conftest import criterion
from .conftest import opensearch_live
from .conftest import request

pytestmark = opensearch_live()


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
        'seen': {'type': 'date'},  # a date-time field, in the default format; searched only by the table `timed`
        'meta': {'properties': {'author': KEYWORD_TEXT}},
        'active': {'type': 'boolean'},
        # One field of each type that refuses a number, for the tests of `IndexColumn(number=...)` below (`pages` is
        # the `integer`); and a `scaled_float`, which refuses none
        'n_byte': {'type': 'byte'},
        'n_short': {'type': 'short'},
        'n_long': {'type': 'long'},
        'n_unsigned': {'type': 'unsigned_long'},
        # `n_float`, `n_half` and `n_double` keep -0.0 apart from 0.0; b2 holds 0.0 in each
        'n_float': {'type': 'float'},
        'n_half': {'type': 'half_float'},
        'n_scaled': {'type': 'scaled_float', 'scaling_factor': 100},
        'n_double': {'type': 'double'},
        'label': KEYWORD_TEXT,  # declared only by the table `labelled` below
        # A whole string the server folds to ASCII and to small letters; declared only by the table `folded` below
        'folded': {'type': 'keyword', 'normalizer': 'to_ascii'},
    }
}
SETTINGS = {
    'number_of_replicas': 0,
    'analysis': {'normalizer': {'to_ascii': {'type': 'custom', 'filter': ['asciifolding', 'lowercase']}}},
}
ASTERISK, QUESTION, BACKSLASH = chr(0xFF0A), chr(0xFF1F), chr(0xFF3C)  # the fullwidth forms of `*`, `?` and `\`
LONGEST = ('ABCDEFGHIJKLMNOPQRSTUVWXYZ' * 10)[:255]  # as many code points as PRD-F14 lets a search text hold
# Three values of 255 code points with a cased letter outside ASCII, each a `regexp` query. One letter repeated is
# the text a server refuses earliest of those measured: as a substring it answers 300 of them and refuses 400, as
# a `wildcard` and as a `regexp`, where it answers 500 letters of the alphabet in turn (measured with `b`, `ä` and
# `θ` on 3.10.0 and 2.19.7).
REPEATED_LETTER = 'Ä' * 255
# A letter outside the BMP, which a server counts as two, and 254 dots, each behind a backslash: 514 UTF-16 units.
DOTTED = chr(0x10400) + '.' * 254
# The longest pattern a value can make, 768 UTF-16 units: that letter and 254 marks outside the BMP (an emoji),
# each two units behind a backslash, open at both ends.
LONGEST_PATTERN = chr(0x10400) + chr(0x1F600) * 254
# Every ASCII character a value may hold that is not a letter or a digit: line feed and carriage return, the two
# control characters PRD-F15 lets through, then the space and the punctuation.
PUNCTUATION = '\n\r' + ''.join(char for char in map(chr, range(32, 127)) if not char.isalnum())
# Without a backslash, sixteen of them are operators of a regular expression on 2.19.7 and fifteen on 3.0.0 to
# 3.10.0, where a bare `~` is that character (measured). The row with all of PUNCTUATION turns red when one of
# twelve loses its backslash, the DOTTED row for `.`, and for these three a row of their own: a1 holds each between
# Ä and Z, and b2 a value that `@` (any string) and, on OpenSearch 2.19 only, `~` (not what follows) match as
# operators; a bare `\` the server rejects.
OPERATORS = '@\\~'
ESCAPED = [*(f'Ä{char}Z' for char in OPERATORS), f'Ä{PUNCTUATION}Z']
DECOY = 'ÄXZ'
DOCUMENTS: list[dict[str, Any]] = [
    {'id': 'a1', 'title': 'Apple pie', 'body': 'granny smith apples', 'pages': 12, 'created': '2026-07-15',
     'meta': {'author': 'Ann'}, 'active': True, 'label': ESCAPED, 'folded': 'a*b'},
    {'id': 'b2', 'title': 'Banana bread', 'body': 'ripe bananas and green tea', 'pages': 30, 'created': '2026-07-16',
     'meta': {'author': 'Bob'}, 'active': False, 'label': DECOY, 'folded': f'a{ASTERISK}b', 'n_double': 0.0,
     'n_float': 0.0, 'n_half': 0.0},
    {'id': 'c3', 'title': 'Cherry tart 100%', 'body': 'sour cherries', 'label': LONGEST, 'folded': 'a?b',
     'n_unsigned': 2**64 - 1, 'n_scaled': (2**63 - 1) / 100},  # the largest of each, for the two silent answers below
    {'id': 'd4', 'title': 'apple crumble', 'body': 'green apples', 'pages': 12, 'created': '2025-01-01',
     'meta': {'author': 'Cy'}, 'active': True, 'label': [REPEATED_LETTER, DOTTED, LONGEST_PATTERN, 'bär'],
     'seen': '2026-08-02T22:00:00-05:00',  # written on the 2nd, and 03:00 UTC on the 3rd
     'folded': f'a{QUESTION}b'},
    {'id': 'e5', 'title': 'Date loaf', 'body': 'medjool dates', 'pages': 60, 'created': '2026-02-10',
     'meta': {'author': 'Dee'}, 'active': False, 'label': 'ÄPFEL',
     'seen': '2026-08-01T23:00:00-05:00',  # written on the 1st, and 04:00 UTC on the 2nd
     'folded': ['a\\b', f'a{BACKSLASH}b', 'axyb']},
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
table = SearchTable(INDEX, COLUMNS, unique_key='id')
timed = SearchTable(INDEX, [IndexColumn('id', text='keyword'), Column('seen', type='date')], unique_key='id')

# A second index, so that no count or row of the tests above changes: five documents whose one text is `a`, `b`, `a-b`,
# `b a` and `a b`, in a field of analysed text and in a field of text+keyword (PRD-F11's phrase of tokens).
PHRASE_INDEX = 'dtb-test-docs-phrases'  # owned by this module too: deleted and recreated on every run
PHRASE_TEXTS = {'p1': 'a', 'p2': 'b', 'p3': 'a-b', 'p4': 'b a', 'p5': 'a b'}
# A field stored as a hook would store it (`enc:` and the text), for the table `hooked` below: the whole value, a
# superset of it, a padded one, one in other letters, and p5 without it
SECRETS = {'p1': 'enc:Alpha', 'p2': 'enc:Alphabet', 'p3': 'enc: Alpha ', 'p4': 'enc:alpha'}
PHRASE_MAPPING = {
    'properties': {
        'id': {'type': 'keyword'},
        'only_text': {'type': 'text'},
        'both': KEYWORD_TEXT,
        'secret': KEYWORD_TEXT,
    }
}
phrases = SearchTable(PHRASE_INDEX, [IndexColumn('id', text='keyword'), IndexColumn('only_text', text='text'),
                                     IndexColumn('both')], unique_key='id')  # fmt: skip
PHRASE_NAMES = ('id', 'only_text', 'both')
hooked = SearchTable(
    PHRASE_INDEX,
    [IndexColumn('id', text='keyword'), Column('secret', search_value=lambda text: f'enc:{text}')],
    unique_key='id',
)

# A third index of one document, so that no count of the two above changes: a hooked value of two lines (`enc:a`, a line
# feed, `b`), the table `hooked_lines` below
LINES_INDEX = 'dtb-test-docs-lines'  # owned by this module too: deleted and recreated on every run
LINES_DOCUMENT = {'id': 'l1', 'secret': 'enc:a\nb'}
hooked_lines = SearchTable(
    LINES_INDEX,
    [IndexColumn('id', text='keyword'), Column('secret', search_value=lambda text: f'enc:{text}')],
    unique_key='id',
)


@pytest.fixture(scope='module')
def client() -> Iterator[OpenSearch]:
    client = OpenSearch(hosts=[URL])
    for index in (INDEX, PHRASE_INDEX, LINES_INDEX):
        if client.indices.exists(index=index):  # left by an interrupted run
            client.indices.delete(index=index)
    client.indices.create(index=INDEX, body={'mappings': MAPPING, 'settings': SETTINGS})
    for number, document in enumerate(DOCUMENTS, 1):
        client.index(index=INDEX, id=str(number), body=document)
    client.indices.create(index=PHRASE_INDEX, body={'mappings': PHRASE_MAPPING, 'settings': SETTINGS})
    for key, text in PHRASE_TEXTS.items():
        client.index(
            index=PHRASE_INDEX,
            id=key,
            body={'id': key, 'only_text': text, 'both': text, **({'secret': SECRETS[key]} if key in SECRETS else {})},
        )
    lines_mapping = {'properties': {'id': {'type': 'keyword'}, 'secret': KEYWORD_TEXT}}
    client.indices.create(index=LINES_INDEX, body={'mappings': lines_mapping, 'settings': SETTINGS})
    client.index(index=LINES_INDEX, id=LINES_DOCUMENT['id'], body=LINES_DOCUMENT)
    client.indices.refresh(index=[INDEX, PHRASE_INDEX, LINES_INDEX])
    yield client
    client.indices.delete(index=[INDEX, PHRASE_INDEX, LINES_INDEX])
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
    """SPEC-Q14.1: DTB_OPENSEARCH_VERSION (the CI job sets it for each leg) names the server the run must be on: an
    image pinned by digest runs that digest, whatever version its tag says."""
    version = client.info()['version']
    assert version['distribution'] == 'opensearch'
    assert version['number'] == os.environ.get('DTB_OPENSEARCH_VERSION', version['number'])


def test_an_unfiltered_draw_counts_every_document_and_reads_dotted_fields(client: OpenSearch):
    """SPEC-C1.1: no filter, the total is every document."""
    response = table.fetch_sync(request(*NAMES, order=[{'column': 0, 'dir': 'asc'}]), client)
    assert (response.recordsTotal, response.recordsFiltered) == (5, 5)
    assert response.data[0] == {
        'id': 'a1', 'title': 'Apple pie', 'body': 'granny smith apples', 'pages': 12, 'created': '2026-07-15',
        'author': 'Ann',
    }  # fmt: skip
    assert response.data[2]['author'] is None


def test_a_filtered_draw_counts_the_total_apart(client: OpenSearch):
    """SPEC-C1.2, SPEC-C8.2: a search narrows the filtered count; the total is a count of its own."""
    response = table.fetch_sync(request(*NAMES, search={'value': 'apple'}), client)
    assert (response.recordsTotal, response.recordsFiltered) == (5, 2)


@pytest.mark.parametrize(
    ('search', 'base_filter', 'window', 'start', 'length', 'code', 'counts'),
    [
        # 5 rows, window 2: start 2 asks for rows 3 to 3, and 5 - 2 = 3 rows from the end is beyond the window
        ('', None, 2, 2, 1, 'page_out_of_range', (5, 5)),
        # 2 rows match, window 1: start 0 asks for 2 rows and 2 - 0 > 1; the filtered set and the total are counts of
        # their own
        ('apple', None, 1, 0, 2, 'page_out_of_range', (5, 2)),
        ('bread', {'range': {'pages': {'gte': 30}}}, 2, 5, 1, None, (2, 1)),  # the empty page, past the end of 1 row
    ],
    ids=['unfiltered', 'filtered', 'empty-page-with-a-base-filter'],
)
def test_the_counts_before_a_refusal_are_the_ones_the_search_reports(
    client: OpenSearch,
    *,
    search: str,
    base_filter: dict[str, Any] | None,
    window: int,
    start: int,
    length: int,
    code: str | None,
    counts: tuple[int, int],
):
    """SPEC-C12.1, SPEC-C12.3, SPEC-C3.3, measured: the count API takes the very query body the library builds for a
    search (the base filter, the clauses) and counts what the search reports. The recording client cannot show that
    the cluster accepts the body, or that its count equals the search's own total."""
    narrow = SearchTable(INDEX, COLUMNS, unique_key='id', base_filter=base_filter, max_result_window=window)
    response = narrow.fetch_sync(request(*NAMES, start=start, length=length, search={'value': search}), client)
    assert (response.error_code, response.data) == (code, [])
    assert (response.recordsTotal, response.recordsFiltered) == counts


def test_a_page_within_a_scope_is_read_from_the_end_or_refused_by_the_counts_of_the_scope(client: OpenSearch):
    """SPEC-C12.5: the scope `exists pages` holds 4 of the 5 documents (a1, b2, d4, e5), the window is 2, ordered by
    id. `start` 3 and `length` 1 is the reversed last page: row e5, counts 4 and 4. `start` 1 and `length` 2 ends 3 rows
    from the last one, beyond the window: `page_out_of_range` with the total 4 and the filtered count 4, not the
    index's 5. A scope left out of the count body, or of the reversed search, answers 5 or another row."""
    scoped = SearchTable(INDEX, COLUMNS, unique_key='id', max_result_window=2)
    order, scope = [{'column': 0, 'dir': 'asc'}], {'exists': {'field': 'pages'}}
    last = scoped.fetch_sync(request(*NAMES, start=3, length=1, order=order), client, scope=scope)
    assert (ids(last), (last.recordsTotal, last.recordsFiltered)) == (['e5'], (4, 4))
    refused = scoped.fetch_sync(request(*NAMES, start=1, length=2, order=order), client, scope=scope)
    assert (refused.error_code, refused.data) == ('page_out_of_range', [])
    assert (refused.recordsTotal, refused.recordsFiltered) == (4, 4)


def test_the_base_filter_limits_the_total_too(client: OpenSearch):
    """SPEC-C1.1: a base filter limits the total. SPEC-F31.1: with no search the rows and both counts are its own."""
    long_reads = SearchTable(INDEX, COLUMNS, unique_key='id', base_filter={'range': {'pages': {'gte': 30}}})
    everything = long_reads.fetch_sync(request(*NAMES), client)
    assert (everything.recordsTotal, everything.recordsFiltered, found(everything)) == (2, 2, ['b2', 'e5'])
    response = long_reads.fetch_sync(request(*NAMES, search={'value': 'bread'}), client)
    assert (response.recordsTotal, response.recordsFiltered) == (2, 1)


@pytest.mark.parametrize(
    ('search', 'expected', 'counts'),
    [('', ['b2'], (1, 1)), ('bread', ['b2'], (1, 1)), ('apple', [], (1, 0))],
    ids=['no-search', 'bread', 'apple'],
)
def test_a_scope_and_the_base_filter_apply_together_in_the_search_and_in_the_count(
    client: OpenSearch, search: str, expected: list[str], counts: tuple[int, int]
):
    """SPEC-F31.2 on the cluster: the base filter `pages >= 30` alone finds b2 and e5, the scope `body` matches
    `green` alone b2 and d4; together only b2. The cluster accepts `must: [base, scope, ...]` in the search and in the
    count, and the total is 1, not 2 (the base filter alone, or the scope alone)."""
    long_reads = SearchTable(INDEX, COLUMNS, unique_key='id', base_filter={'range': {'pages': {'gte': 30}}})
    response = long_reads.fetch_sync(
        request(*NAMES, search={'value': search}), client, scope={'match': {'body': 'green'}}
    )
    assert (found(response), (response.recordsTotal, response.recordsFiltered)) == (expected, counts)


def test_an_empty_scope_is_no_scope_on_the_cluster_too(client: OpenSearch):
    """SPEC-F31.3 on the cluster: an empty mapping adds no clause, so it answers as no scope does; an empty clause
    in `must` is refused by OpenSearch (INFERRED, not run)."""
    params = request(*NAMES, search={'value': 'apple'}, order=[{'column': 0, 'dir': 'asc'}])
    assert table.fetch_sync(params, client, scope={}) == table.fetch_sync(params, client)
    assert table.fetch_sync(params, client, scope=None) == table.fetch_sync(params, client)


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
        ('ple', ['a1', 'd4']),  # SPEC-F11.5: a substring in the middle of the whole title (`Apple`, `apple`)
        ('green', ['b2', 'd4']),  # an analysed body word
        ('p?e', []),  # `?` is text: unescaped, the wildcard `*p?e*` would find "Apple pie"
        ('a*b', []),  # `*` is text too
        ('ann', ['a1']),  # the dotted author field
        ('bread green', ['b2']),  # SPEC-F1.1: the title of b2 and its body; as one text it found d4's `green` too
        ('green !apple', ['b2']),  # SPEC-F5.3: d4's title holds `apple`
        ('!apple', ['b2', 'c3', 'e5']),  # SPEC-F5.1: c3 and e5 have no author, which passes
        ('!x', ['a1', 'b2', 'c3', 'd4', 'e5']),  # SPEC-F35.2 and SPEC-F6.2: a word of one character adds nothing
        ('"', ['a1', 'b2', 'c3', 'd4', 'e5']),  # SPEC-F6.2: no word at all
        ('12', []),  # SPEC-F32.1: a1 and d4 have 12 pages, but the number column takes no part in the search
        ('2026', []),  # SPEC-F32.1: nor does the date column, which a1, b2 and e5 hold
        ('apple 12', []),  # SPEC-F1.3: a1's title holds `apple` and its pages 12, but the number column takes no part
    ],
)
def test_the_global_search(client: OpenSearch, text: str, expected: list[str]):
    assert found(table.fetch_sync(request(*NAMES, search={'value': text}), client)) == expected


@pytest.mark.parametrize(
    ('column', 'value', 'expected'),
    [
        (2, 'granny apples', ['a1']),
        (2, 'apples', ['a1', 'd4']),  # SPEC-F11.6: the body is analysed text only, and `apples` is one of its tokens
        (2, 'apple', []),  # SPEC-F11.6: ... of which `apple` is not a part
        (2, '"green tea"', ['b2']),  # SPEC-F4.1: a phrase; d4's body holds `green` only
        (2, 'green tea', ['b2']),  # SPEC-F2.1: both words; as one text it found d4's `green` too
        (1, 'CRUMBLE', ['d4']),
        (3, '12', ['a1', 'd4']),  # a number column matches exactly
        (4, '2026-07', ['a1', 'b2']),  # a date column takes a month
        (4, '2026', ['a1', 'b2', 'e5']),
        (4, '2026-07-15 10:00:00', ['a1']),  # a day with a time after it is that day, one date term, not two words
    ],
)
def test_a_column_search(client: OpenSearch, column: int, value: str, expected: list[str]):
    """SPEC-F11.6 (the text-only `body`), SPEC-F22.4 (a month, and a year, on a date column), SPEC-F33.1 (a number is
    matched exactly), SPEC-F34.1 (a day with a time after it)."""
    params = request(*NAMES)
    params.columns[column].search.value = value
    assert found(table.fetch_sync(params, client)) == expected


@pytest.mark.parametrize('column', ['only_text', 'both'])
@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        # A word the analyser splits is a phrase of its tokens (PRD-F11): p1 holds `a` alone and p4 holds the tokens in
        # the other order, so neither is found. p5 (`a b`) has the same tokens in the same order, and is found.
        ('a-b', ['p3', 'p5']),
        ('!a-b', ['p1', 'p2', 'p4']),
    ],
)
def test_a_word_the_analyser_splits_is_a_phrase_of_its_tokens(
    client: OpenSearch, column: str, text: str, expected: list[str]
):
    """SPEC-F11.7. As a `match` (any token) `a-b` also found p1, p2 and p4, and `!a-b` left only a row without them."""
    params = request(*PHRASE_NAMES)
    params.columns[PHRASE_NAMES.index(column)].search.value = text
    assert found(phrases.fetch_sync(params, client)) == expected


@pytest.mark.parametrize('column', ['only_text', 'both'])
@pytest.mark.parametrize(
    ('condition', 'expected'),
    [('contains', ['p3', 'p5']), ('!contains', ['p1', 'p2', 'p4'])],
)
def test_searchbuilder_contains_on_analysed_text_is_the_same_phrase(
    client: OpenSearch, column: str, condition: str, expected: list[str]
):
    """SPEC-F11.7: SearchBuilder `contains` and a word of a column search find the same rows."""
    assert found(phrases.fetch_sync(request(*PHRASE_NAMES, **criterion(column, condition, 'a-b')), client)) == expected


@pytest.mark.parametrize('column', ['only_text', 'both'])
def test_searchbuilder_contains_with_padding_finds_the_text_inside_it(client: OpenSearch, column: str):
    """SPEC-F11.7 on a value sent as typed (#68): the analyser drops the spaces of ` a-b `, and on text+keyword the
    wildcard `* a-b *` finds nothing, so the rows are those of `a-b`."""
    response = phrases.fetch_sync(request(*PHRASE_NAMES, **criterion(column, 'contains', ' a-b ')), client)
    assert found(response) == ['p3', 'p5']


@pytest.mark.parametrize(
    ('column', 'expected'),
    # Main answered every row on both (run 45075131757); the target: analysed text holds no space, the wildcard `* *`
    # finds the rows holding one, as SQL's substring does
    [('only_text', []), ('both', ['p4', 'p5'])],
)
def test_searchbuilder_contains_of_one_space(client: OpenSearch, column: str, expected: list[str]):
    """SPEC-F11.7 on a blank value sent as typed (#68): the phrase has no token, the wildcard `* *` is a space."""
    response = phrases.fetch_sync(request(*PHRASE_NAMES, **criterion(column, 'contains', ' ')), client)
    assert found(response) == expected


def everywhere(text: str) -> dict[str, Any]:
    return {'search': {'value': text}}


def in_title(text: str) -> dict[str, Any]:
    return {'column_search': {'title': {'value': text}}}


def in_label(text: str) -> dict[str, Any]:
    return {'column_search': {'label': {'value': text}}}


@pytest.mark.parametrize(
    ('title', 'body', 'expected'),
    [
        ('x_y', in_title('x_y'), True),  # SPEC-F11.2: `_` is text
        ('xay', in_title('x_y'), False),
        ('red apple', everywhere('“red apple”'), True),  # SPEC-F4.2
        ('50 plum', in_title('50"'), True),  # SPEC-F4.4: it searches `50`
        ('plum', in_title('"plum'), True),  # SPEC-F4.4: unclosed, it searches `plum`
        ('5" pipe', everywhere('!5"'), False),  # SPEC-F4.5: it excludes `5"`
        ('50', everywhere('!5"'), True),  # SPEC-F4.5: and not `50`
        ('red apple', everywhere('"apple red'), True),  # SPEC-F4.9: no phrase, so `apple` and `red` in any order
        ('apple pie', everywhere('"apple red'), False),
        ('red apple', everywhere('"apple red"'), False),  # SPEC-F4.1, the phrase the unclosed quote must not become
        ('red*', everywhere('red*'), True),  # SPEC-F8.3
        ('redx', everywhere('red*'), False),
        ('re?', everywhere('re?'), True),
        ('rex', everywhere('re?'), False),
        ('xa*by', criterion('title', 'contains', 'a*b'), True),  # SPEC-F8.4
        ('xaby', criterion('title', 'contains', 'a*b'), False),
        ('red not apple', everywhere('red NOT apple'), True),  # SPEC-F8.6: `NOT` is a word
        ('red apple', everywhere('red NOT apple'), False),
        ('red', everywhere('red NOT apple'), False),  # as a negation it would find this one
        ('red not apple', everywhere('red not apple'), True),
        ('red apple', everywhere('red\napple'), True),  # SPEC-F7.1: a line break is a space
        ('red apple', everywhere('red\rapple'), True),
        ('red apple', everywhere(f'red{chr(0x2028)}apple'), True),
        ('red apple', everywhere(f'red{chr(0x2029)}apple'), True),
        ('red', everywhere('red\napple'), False),
        ('a\nb', {'column_search': {'title': {'multi_values': ['a\nb']}}}, True),  # SPEC-F7.2: an exact value
        ('a b', {'column_search': {'title': {'multi_values': ['a\nb']}}}, False),
        ('a\nb', criterion('title', '=', 'a\nb'), True),
        ('a b', criterion('title', '=', 'a\nb'), False),
    ],
)
def test_the_smart_search_finds_a_document_by_the_rule_its_statement_gives(
    client: OpenSearch, title: str, body: dict[str, Any], expected: bool
):
    """SPEC-F4.1, SPEC-F4.2, SPEC-F4.4, SPEC-F4.5, SPEC-F4.9, SPEC-F7.1, SPEC-F7.2, SPEC-F8.3, SPEC-F8.4, SPEC-F8.6,
    SPEC-F11.2 (named in the comments), on a text+keyword column: the document f6 holds the text as its title, and is
    found or is not. Each case is asked on its own, so that a document that differs from the next in one character
    cannot answer for it."""
    with added_document(client, title=title, body='x'):
        assert ('f6' in found(table.fetch_sync(request(*NAMES, **body), client))) is expected


#: The label as one whole string only, a keyword column: the wildcard alone decides, so what an analyser drops tells
#: rows apart (the cases of SPEC-F11.9 are the same texts on text+keyword)
whole = SearchTable(
    INDEX, [IndexColumn('id', text='keyword'), IndexColumn('label', field='label.keyword', text='keyword')],
    unique_key='id',
)  # fmt: skip


@pytest.mark.parametrize(
    ('text', 'body', 'expected'),
    [
        ('a b”', everywhere('"a b”'), True),  # SPEC-F4.3: one document at a time, each against the same search
        ('a b', everywhere('"a b”'), False),
        ('“a b', everywhere('"a b”'), False),
        ('“a b', everywhere('“a b"'), True),
        ('a b”', everywhere('“a b"'), False),
        ('a b', everywhere('“a b"'), False),
        ('x “abc', everywhere('“abc'), True),  # SPEC-F4.6: the curly quote stays as text
        ('abc', everywhere('“abc'), False),
        ('"a b”', everywhere('!"a b”'), False),  # SPEC-F4.7: a negated mixed pair keeps its straight quote
        ('a b”', everywhere('!"a b”'), True),
        (' a ', everywhere('" a "'), True),  # SPEC-F4.8: a phrase keeps the spaces inside its quotes
        ('xax', everywhere('" a "'), False),
        ('a', everywhere('" a "'), False),
        ('-red', everywhere('-red'), True),  # SPEC-F8.2: `-red` is text
        ('red', everywhere('-red'), False),
        ('50%', everywhere('50%'), True),  # SPEC-F11.1: `%` is text
        ('50 plum', everywhere('50%'), False),
        ('a*b', in_label('a*b'), True),  # SPEC-F11.4: `*` and `?` are text in a column search, each its own character
        ('axb', in_label('a*b'), False),
        ('a?b', in_label('a*b'), False),
        ('a?b', in_label('a?b'), True),
        ('axb', in_label('a?b'), False),
        ('a*b', in_label('a?b'), False),
    ],
)
def test_what_an_analyser_drops_tells_rows_apart_on_a_keyword_column(
    client: OpenSearch, text: str, body: dict[str, Any], expected: bool
):
    """SPEC-F4.3, SPEC-F4.6, SPEC-F4.7, SPEC-F4.8, SPEC-F8.2, SPEC-F11.1, SPEC-F11.4 on OpenSearch: the column is a
    keyword column."""
    with added_document(client, label=text):
        assert ('f6' in found(whole.fetch_sync(request('id', 'label', **body), client))) is expected


@pytest.mark.parametrize(
    ('searched', 'other', 'body'),
    [
        ('-red', 'red', everywhere('-red')),
        (' a ', 'a', everywhere('" a "')),
        ('a b”', 'a b', everywhere('"a b”')),
        ('50%', '50 plum', everywhere('50%')),
        ('a*b', 'a?b', in_title('a*b')),
    ],
)
def test_what_the_analyser_drops_does_not_tell_rows_apart_on_text_and_keyword(
    client: OpenSearch, searched: str, other: str, body: dict[str, Any]
):
    """SPEC-F11.9: both rows are found, as the analysed half of the word matches the tokens (SPEC-F11.7). It pins
    today's result: the text of a row is its title, one row at a time."""
    for title in (searched, other):
        with added_document(client, title=title, body='x'):
            assert 'f6' in found(table.fetch_sync(request(*NAMES, **body), client))


def test_a_negation_leaves_every_other_document_and_a_missing_field_passes(client: OpenSearch):
    """SPEC-F6.5: `!pipe` and `!pipe ""` find the same, all five, c3 (no author) among them (missing on OpenSearch is
    a NULL; an empty word kept as a condition would drop it)."""
    plain = found(table.fetch_sync(request(*NAMES, column_search={'author': {'value': '!pipe'}}), client))
    quoted = found(table.fetch_sync(request(*NAMES, column_search={'author': {'value': '!pipe ""'}}), client))
    assert plain == quoted == ['a1', 'b2', 'c3', 'd4', 'e5']


@pytest.mark.parametrize(
    ('text', 'expected'),
    [('Alpha', ['p1']), (f' {chr(0xA0)}Alpha{chr(0x3000)}', ['p1']), ('Alph', []), ('alpha', ['p4'])],
)
def test_a_column_search_on_a_hooked_column_matches_the_whole_stored_value(
    client: OpenSearch, text: str, expected: list[str]
):
    """SPEC-D17.1: the `term` on the keyword field finds `enc:Alpha` only: not the superset `enc:Alphabet` (p2), not
    the padded `enc: Alpha ` (p3) and, case-sensitive as a hooked column is (PRD-D21), not `enc:alpha` (p4), which
    `alpha` finds (SPEC-F9.8: the library adds no folding, so `alpha` does not find `enc:Alpha`). A recording client
    cannot show that the server matches the whole value and not a substring."""
    params = request('id', 'secret', column_search={'secret': {'value': text}})
    assert found(hooked.fetch_sync(params, client)) == expected


@pytest.mark.parametrize(
    ('text', 'expected'),
    [('Alpha', ['p1']), (f' {chr(0xA0)}Alpha{chr(0x3000)}', ['p1']), ('Alph', []), ('alpha', ['p4'])],
)
def test_the_global_search_on_a_hooked_column_matches_the_whole_stored_value(
    client: OpenSearch, text: str, expected: list[str]
):
    """SPEC-D12.1, SPEC-D12.3: the same `term` as the column search, so the same four documents: not the superset
    `enc:Alphabet` (p2) nor the padded `enc: Alpha ` (p3), where a wildcard finds them, and `alpha` finds `enc:alpha`
    (p4) only. The `id` column, searched as text beside it, holds none of these words."""
    assert found(hooked.fetch_sync(request('id', 'secret', search={'value': text}), client)) == expected


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('apple pie', ['a1']),  # SPEC-F16.1: an exact match in any case; not d4, `apple crumble`
        ('^Apple pie$', ['a1']),  # SPEC-F16.2
        ('Apple pie|Date loaf', ['a1', 'e5']),
        ('^(Apple pie|Date loaf)$', ['a1', 'e5']),
    ],
)
def test_a_multi_select_matches_whole_values(client: OpenSearch, value: str, expected: list[str]):
    """SPEC-F16.1, SPEC-F16.2."""
    params = request(*NAMES)
    params.columns[1].search.value = value
    params.columns[1].search.regex = True
    assert found(table.fetch_sync(params, client)) == expected


@pytest.mark.parametrize(
    ('column', 'values', 'expected'),
    [(0, ['a1', 'e5'], ['a1', 'e5']), (3, ['12', '60'], ['a1', 'd4', 'e5']), (4, ['2026-07-16'], ['b2'])],
)
def test_multi_values_match_exactly(client: OpenSearch, column: int, values: list[str], expected: list[str]):
    """SPEC-F20.1: a list of values is an exact match for each."""
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
        # Analysed text: a phrase of the words. `=`, `starts` and `ends` are refused there (PRD-D19)
        ('body', 'contains', ['green apples'], ['d4']),
        ('body', '!contains', ['green apples'], ['a1', 'b2', 'c3', 'e5']),
        ('title', 'contains', ['TART'], ['c3']),
        ('title', '!contains', ['apple'], ['b2', 'c3', 'e5']),
        ('title', 'starts', ['apple'], ['a1', 'd4']),
        ('title', '!starts', ['APPLE'], ['b2', 'c3', 'e5']),
        ('title', 'ends', ['PIE'], ['a1']),
        ('title', 'starts', ['PIE'], []),  # `Apple pie` ends with it, and does not start with it
        ('title', '!starts', ['PIE'], ['a1', 'b2', 'c3', 'd4', 'e5']),
        ('title', 'ends', ['APPLE'], []),  # `Apple pie` holds it, and does not end with it
        ('title', '!ends', ['APPLE'], ['a1', 'b2', 'c3', 'd4', 'e5']),
        ('id', '!ends', ['1'], ['b2', 'c3', 'd4', 'e5']),
        ('pages', '=', ['12'], ['a1', 'd4']),
        ('pages', '>', ['12'], ['b2', 'e5']),
        ('pages', '<', ['30'], ['a1', 'd4']),
        ('pages', '<=', ['30'], ['a1', 'b2', 'd4']),
        ('pages', '!between', ['12', '30'], ['c3', 'e5']),  # c3 has no pages, which passes (PRD-F37)
        # SPEC-F25.10: a number with a fraction is that number, on an integer field
        ('pages', '>', ['12.5'], ['b2', 'e5']),
        ('pages', '<', ['12.5'], ['a1', 'd4']),
        ('pages', '=', ['12.5'], []),
        ('pages', '=', ['12.0'], ['a1', 'd4']),
        ('pages', 'between', ['11.5', '12.5'], ['a1', 'd4']),
        ('pages', 'between', ['12', '30'], ['a1', 'b2', 'd4']),
        ('pages', 'null', [], ['c3']),
        ('created', '=', ['2026-07-15'], ['a1']),
        ('created', '<', ['2026-07-15'], ['d4', 'e5']),
        ('created', '<=', ['2026-07-15'], ['a1', 'd4', 'e5']),
        ('created', '>', ['2026-07-15'], ['b2']),
        ('created', '>=', ['2026-07-15'], ['a1', 'b2']),
        ('created', 'between', ['2026-02-10', '2026-07-15'], ['a1', 'e5']),
        # SPEC-F37.3: c3 has no date, which passes
        ('created', '!=', ['2026-07-15'], ['b2', 'c3', 'd4', 'e5']),
        ('created', '!between', ['2026-02-10', '2026-07-15'], ['b2', 'c3', 'd4']),
        ('created', 'contains', ['2026-07'], ['a1', 'b2']),
        ('author', 'null', [], ['c3']),
        ('author', '!null', [], ['a1', 'b2', 'd4', 'e5']),
    ],
)
def test_searchbuilder_conditions(
    client: OpenSearch, column: str, condition: str, values: list[str], expected: list[str]
):
    """SPEC-F9.4 for `ends` and `!starts` too: the value is in another case than stored. SPEC-F21.1: the six conditions
    on a date, SPEC-F27.6: `<`, `between` and `!between` on a number, SPEC-F25.10, SPEC-F37.3."""
    assert found(table.fetch_sync(request(*NAMES, **criterion(column, condition, *values)), client)) == expected


def test_a_day_on_a_date_time_field_is_its_utc_day(client: OpenSearch):
    """SPEC-F21.2 on a date-time field, measured: OpenSearch holds an instant, and a day bounds it in UTC
    (PRD-F21). e5 was written on the 1st at -05:00, which is the 2nd in UTC; d4 was written on the 2nd, which is
    the 3rd in UTC, so a day read as written answers d4 instead. An entry of a list sends the same range
    (tests/test_opensearch.py)."""
    params = request('id', 'seen', **criterion('seen', '=', '2026-08-02'))
    assert found(timed.fetch_sync(params, client)) == ['e5']


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
labelled = SearchTable(
    INDEX, [IndexColumn('id', text='keyword'), IndexColumn('label')], unique_key='id'
)  # e5 holds `ÄPFEL`


@pytest.mark.parametrize(
    ('body', 'expected'),
    [
        # SPEC-F9.9: a whole word, which the analysed text folds. Only a server before 3.1 proves that fold (in CI
        # the 2.19 leg): from 3.1 on the `regexp` on the whole string finds the row as well.
        ({'search': {'value': 'äpfel'}}, ['e5']),
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
    INDEX,
    [IndexColumn('id', text='keyword'), IndexColumn('label', field='label.keyword', text='keyword')],
    unique_key='id',
)  # the label as one whole string only, so no analysed word can answer for it: c3 holds LONGEST


@pytest.mark.parametrize('condition', ['=', 'contains'])
def test_the_longest_search_text_is_answered_in_any_case(client: OpenSearch, condition: str):
    """PRD-F14, PRD-F9 (SPEC-F9.17): a measurement of the server, not of the `regexp` form: what it does with the
    longest text of ASCII letters a search may hold, as a case-insensitive `term` (`=`) and a case-insensitive
    `wildcard` (`contains`; `starts` and `ends` are the same kind of query). OpenSearch's documentation warns that
    on 2.x and earlier the cost of a case-insensitive query can grow exponentially with the number of characters. A
    row turns red when a server refuses or fails such a query at 255 letters, or stops folding at that length."""
    params = request(*LABEL_NAMES, **criterion('label', condition, LONGEST.lower()))
    assert found(whole_label.fetch_sync(params, client)) == ['c3']


@pytest.mark.parametrize(
    'value',
    [
        pytest.param(REPEATED_LETTER, id='one-letter-repeated'),
        pytest.param(DOTTED, id='dots'),
        pytest.param(LONGEST_PATTERN, id='longest-pattern'),
    ],
)
def test_a_regular_expression_of_255_code_points_is_answered(client: OpenSearch, value: str):
    """PRD-F14, PRD-F9 (SPEC-F9.17): a value with a cased letter outside ASCII is a `regexp` query, up to 255 code
    points; each row is that long, as a substring, open at both ends. One letter repeated is the text a server
    refuses earliest of those measured (at 400 letters, where it answers 500 of the alphabet in turn). The dots
    make a pattern of 514 UTF-16 units, and the row also turns red when a dot loses its backslash. The longest
    pattern is 768 units, against the 1,000 a server allows by default (`index.max_regex_length`, which counts
    UTF-16 units). Searched in the stored case, so every server finds it. A row turns red when a server refuses or
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
    rejects or misreads a backslash before a punctuation mark, or the line feed, carriage return or space that are
    sent without one, or when one of twelve operators has lost its backslash:
    `"`, `#`, `&`, `(`, `)`, `*`, `+`, `<`, `?`, `[`, `{` and the vertical bar. A bare `.` is caught by the row of
    dots above, and `@`, `\\` and `~` by the rows before this one."""
    params = request(*LABEL_NAMES, **criterion('label', '=', f'Ä{PUNCTUATION}Z'))
    assert found(whole_label.fetch_sync(params, client)) == ['a1']


folded = SearchTable(INDEX, [IndexColumn('id', text='keyword'), IndexColumn('folded', text='keyword')], unique_key='id')


@pytest.mark.parametrize(
    ('mark', 'expected'),
    [
        pytest.param(ASTERISK, ['a1', 'b2'], id='asterisk'),  # as any string, it would find every row
        pytest.param(QUESTION, ['c3', 'd4'], id='question-mark'),  # as any one character, every row as well
        pytest.param(BACKSLASH, ['e5'], id='backslash'),  # as the escape of a wildcard, no row
    ],
)
def test_a_fullwidth_mark_is_searched_as_the_character_its_field_folds_it_to(
    client: OpenSearch, mark: str, expected: list[str]
):
    """SPEC-F9.20: `folded` is a keyword field whose normalizer makes the mark an ASCII `*`, `?` or `\\`, in what
    is stored and in what is searched. a1 and b2 hold `a*b`, with the ASCII and with the fullwidth asterisk; c3 and
    d4 hold `a?b` in the same two forms; e5 holds `a\\b` in both and `axyb`. A search for `a`, the mark, `b` finds
    the rows holding the ASCII or the fullwidth character there, and no other."""
    params = request('id', 'folded', **criterion('folded', 'contains', f'a{mark}b'))
    assert found(folded.fetch_sync(params, client)) == expected


NUMBER_FIELDS = {
    'byte': 'n_byte',
    'short': 'n_short',
    'integer': 'pages',
    'long': 'n_long',
    'unsigned_long': 'n_unsigned',
    'float': 'n_float',
    'half_float': 'n_half',
}


def numbers_in(field: str, kind: NumberKind | None) -> SearchTable:
    """A table whose one number column reads ``field`` and declares ``kind``, or nothing."""
    column = (
        Column('n', field=field, type='number')
        if kind is None
        else IndexColumn('n', field=field, type='number', number=kind)
    )
    return SearchTable(INDEX, [IndexColumn('id', text='keyword'), column], unique_key='id')


@pytest.mark.parametrize(('kind', 'last', 'first'), NUMBER_EDGES)
def test_a_declared_kind_refuses_where_the_server_does_and_answers_where_it_answers(
    client: OpenSearch, kind: NumberKind, last: str, first: str
):
    """SPEC-D23.1 on a real server: a value the server answers is answered through the declared column, a value it
    refuses is `invalid_number` before the query, and the same value through an undeclared column is the server's own
    400. So the edge the kind carries is the server's, including the exact edge of `byte` and `short` (an integer's)
    and the rounding edge of `float`, which the measurement of decision 011 only bracketed; the `half_float` pair
    through a float straddles its edge."""
    field = NUMBER_FIELDS[kind]
    assert numbers_in(field, kind).fetch_sync(request('id', 'n', **criterion('n', '=', last)), client).error is None
    params = request('id', 'n', **criterion('n', '=', first))
    assert numbers_in(field, kind).fetch_sync(params, client).error_code == 'invalid_number'
    with pytest.raises(RequestError):
        numbers_in(field, None).fetch_sync(params, client)


def test_a_number_outside_the_range_of_an_undeclared_column_fails_with_the_clients_error(client: OpenSearch):
    """PRD section 5, today's result: the mapping is never read (PRD-D8), so a column that declares no kind sends 2^64 to
    its `integer` field, which refuses it with a 400 that reaches the application as the client's `RequestError`. The
    declared case is `test_a_declared_kind_refuses_where_the_server_does_and_answers_where_it_answers`."""
    params = request(*NAMES, **criterion('pages', '=', str(2**64)))
    with pytest.raises(RequestError):
        table.fetch_sync(params, client)


def test_a_declared_unsigned_long_refuses_minus_one_in_a_list_where_the_server_answers_the_largest_number(
    client: OpenSearch,
):
    """SPEC-D23.2, PRD section 5: a `terms` query reads -1 on an `unsigned_long` as 2^64-1 and answers the document that
    holds it, without an error, so an undeclared column returns c3 for a list of -1. A declared kind refuses it."""
    params = request('id', 'n', column_search={'n': {'multi_values': ['-1']}})
    assert ids(numbers_in('n_unsigned', None).fetch_sync(params, client)) == ['c3']
    assert numbers_in('n_unsigned', 'unsigned_long').fetch_sync(params, client).error_code == 'invalid_number'


def test_a_scaled_float_answers_a_number_beyond_its_range_as_its_largest(client: OpenSearch):
    """PRD section 5, decision 011: no request fails on a `scaled_float`, so there is no kind for it; 1e300 finds the
    document that holds the largest value, which is a wrong match without an error."""
    response = numbers_in('n_scaled', None).fetch_sync(request('id', 'n', **criterion('n', '=', '1e300')), client)
    assert ids(response) == ['c3']


def test_a_255_digit_number_is_answered_by_a_double_and_refused_by_a_float_field(client: OpenSearch):
    """SPEC-D23.7 on a real server: the floor of every number column, a number of at most 255 characters, is finite as a
    double, which `double` takes; a `float` field refuses it, which is why `float` has a kind."""
    params = request('id', 'n', **criterion('n', '=', '9' * 255))
    assert numbers_in('n_float', 'float').fetch_sync(params, client).error_code == 'invalid_number'
    with pytest.raises(RequestError):
        numbers_in('n_float', None).fetch_sync(params, client)


#: What a number finds of b2, the only document with a value (0.0) in `n_double`, `n_float` and `n_half`, through the
#: library: the counts for `=`, a `multi_values` list, `<`, `<=`, `>` and `>=`. A number whose double is zero (`-1e-400`,
#: which the unit tests show sends the same body as `-0.00` and `1e-400`) goes as `0`, so it has the answers of 0.0; a
#: nonzero one goes as it is, so the field's own rounding answers, as docs/databases.md records from the raw queries.
ZERO_COUNTS = (1, 1, 0, 1, 0, 1)
TINY_NUMBER_COUNTS = [
    *(
        (field, kind, '-1e-400', ZERO_COUNTS)
        for field, kind in [('n_double', None), ('n_float', 'float'), ('n_half', 'half_float')]
    ),
    ('n_float', 'float', '-1e-50', (0, 0, 0, 0, 1, 1)),
    ('n_float', 'float', '1e-50', (1, 1, 0, 1, 0, 1)),
    ('n_half', 'half_float', '-1e-10', (0, 0, 0, 0, 1, 1)),
    ('n_half', 'half_float', '1e-10', (1, 1, 1, 1, 0, 0)),
]


@pytest.mark.parametrize(('field', 'kind', 'text', 'counts'), TINY_NUMBER_COUNTS)
def test_what_a_tiny_number_finds_of_a_zero_through_each_kind_of_field(
    client: OpenSearch, field: str, kind: NumberKind | None, text: str, counts: tuple[int, ...]
):
    """SPEC-F25.15 on a real server: b2 is the only document with a value, 0.0, in each of `n_double`, `n_float` and
    `n_half`, so the counts are its answers (another document with one would change them). A number whose double is
    zero is sent as `0` and has the answers of 0.0, `-1e-400` too, where it would be -0.0 and keep `=`, `<=` and a
    list from finding b2 and let `>` find it. A nonzero number is sent as it is and the field rounds
    it: a `float` field reads `-1e-50`, and a `half_float` field `-1e-10`, as -0.0, which is not 0.0, and `1e-50` and
    `1e-10` as 0.0, in `=` and a list; a `half_float` range rounds its bound in the direction of the range, so `<` and
    `>=` with `1e-10` answer as the exact number does, where a `float` range rounds to the nearest float first. The
    counts of a row are the whole result, so a mismatch shows which cell differs."""
    table = numbers_in(field, kind)
    params = [
        request('id', 'n', **criterion('n', '=', text)),
        request('id', 'n', column_search={'n': {'multi_values': [text, '7']}}),
        *(request('id', 'n', **criterion('n', condition, text)) for condition in ('<', '<=', '>', '>=')),
    ]
    found = tuple(len(ids(table.fetch_sync(one, client))) for one in params)
    assert found == counts


def test_a_date_field_declared_as_a_string_fails_a_global_search_with_the_clients_error(client: OpenSearch):
    """PRD-D8 today's result: a column is a string column unless declared, and the global search puts a string column's
    text on its field. `created` is a `date` field, so the server refuses the text with a 400 that reaches the
    application as the client's `RequestError` (docs/databases.md, "A date or number field takes part in the global
    search unless it is declared"). The declared case is `test_the_global_search` (`table` declares `created` as
    `type='date'`): `apple` answers a1 and d4."""
    undeclared = SearchTable(
        INDEX, [IndexColumn('id', text='keyword'), IndexColumn('title'), Column('created')], unique_key='id'
    )
    with pytest.raises(RequestError):
        undeclared.fetch_sync(request('id', 'title', 'created', search={'value': 'apple'}), client)


# The tests below declare string columns on a boolean mapping. An OpenSearch column is string unless declared boolean
# (PRD-D8), so PRD-F43 does not change their outcomes: the fix is to declare the column boolean, as docs/databases.md
# says. The cases with the column declared boolean follow them.


@pytest.mark.parametrize('value', ['1', 'true', 'abc'])
def test_searchbuilder_equality_on_a_boolean_field_matches_nothing_today(client: OpenSearch, value: str):
    """A string column on a boolean mapping: the column is a default (text+keyword) string column, and `=` queries its
    keyword subfield `active.keyword`, which a boolean mapping does not have: no document matches, whatever the value.
    PRD-D8/PRD-F43 apply once the column is declared boolean."""
    flagged = SearchTable(INDEX, [IndexColumn('id', text='keyword'), Column('active')], unique_key='id')
    assert found(flagged.fetch_sync(request('id', 'active', **criterion('active', '=', value)), client)) == []


def test_searchbuilder_equality_on_a_boolean_field_itself(client: OpenSearch):
    """A keyword-kind string column on a boolean mapping terms on `active` itself, which a boolean field answers for
    `true`. The adopter's fix for the refusals below is to declare the column boolean."""
    flagged = SearchTable(
        INDEX, [IndexColumn('id', text='keyword'), IndexColumn('active', text='keyword')], unique_key='id'
    )
    assert found(flagged.fetch_sync(request('id', 'active', **criterion('active', '=', 'true')), client)) == [
        'a1',
        'd4',
    ]


@pytest.mark.parametrize('value', ['1', 'abc'])
def test_a_keyword_column_on_a_boolean_field_fails_for_a_term_that_is_no_boolean(client: OpenSearch, value: str):
    """Today's result: OpenSearch rejects a term other than true/false on a boolean field, so the search raises. An
    adopter who declares a keyword column on a boolean mapping declares it wrong; declared boolean (PRD-D8, PRD-F43)
    `1` is answered (`test_searchbuilder_on_a_declared_boolean_column`) and `abc` is refused with `invalid_filter`."""
    flagged = SearchTable(
        INDEX, [IndexColumn('id', text='keyword'), IndexColumn('active', text='keyword')], unique_key='id'
    )
    with pytest.raises(RequestError):
        flagged.fetch_sync(request('id', 'active', **criterion('active', '=', value)), client)


# The column declared boolean (PRD-D8): `active` is true for a1 and d4, false for b2 and e5, missing in c3. The spec
# statements cited speak of their own four-row fixture; the rows here are the same cases on this index.

FLAG_NAMES = ('id', 'active')
flags = SearchTable(INDEX, [IndexColumn('id', text='keyword'), Column('active', type='boolean')], unique_key='id')


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


@pytest.mark.parametrize(
    ('direction', 'expected'),
    [('asc', ['c3', 'a1', 'd4', 'b2', 'e5']), ('desc', ['e5', 'b2', 'd4', 'a1', 'c3'])],
)
def test_the_declared_key_breaks_ties_and_the_descending_order_is_the_exact_reverse(
    client: OpenSearch, direction: str, expected: list[str]
):
    """SPEC-O3.4, measured: a1 and d4 tie on `pages` (12), and the key runs in the direction of the order; c3 has no
    `pages` and is the lowest value. The sort body, with `missing` on the key field too, is one the cluster accepts."""
    keyed = SearchTable(INDEX, COLUMNS, unique_key='id')
    response = keyed.fetch_sync(request(*NAMES, order=[{'column': 3, 'dir': direction}]), client)
    assert ids(response) == expected


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
        # SPEC-O2.2: a missing value is the lowest, so c3 is last descending and first ascending, where OpenSearch
        # left to itself would put it last too
        (4, 'desc', ['b2', 'a1', 'e5', 'd4', 'c3']),
        (4, 'asc', ['c3', 'd4', 'e5', 'a1', 'b2']),
    ],
)
def test_order(client: OpenSearch, column: int, direction: str, expected: list[str]):
    response = table.fetch_sync(request(*NAMES, order=[{'column': column, 'dir': direction}]), client)
    assert ids(response) == expected


BUDGET = 960  # PRD-C17: measured in docs/decisions/005


def test_a_request_of_the_budget_is_answered(client: OpenSearch):
    """SPEC-C17.1, PRD-C17, a guard: one word of the global search over BUDGET text+keyword columns, which reach one
    field so that the index's own limit of fields is not what is tried (docs/databases.md). The widest `bool` holds
    BUDGET clauses (tests/test_opensearch.py), within the default `indices.query.bool.max_clause_count` of 1,024. It
    catches a budget set past what the server answers, on 3.10 and on 2.19."""
    columns = [IndexColumn(f't{number}', field='title') for number in range(BUDGET)]
    names = [column.name for column in columns]
    response = SearchTable(INDEX, columns, unique_key='t0').fetch_sync(request(*names, search={'value': 'x'}), client)
    assert response.error is None


def test_a_column_declared_for_the_document_id_shows_the_real_id(client: OpenSearch):
    """SPEC-D11.6, measured (issue #96): the documents were indexed with the ids 1 to 5, which no `_source` holds.
    The table is ordered by its keyword key, never by the id."""
    shown = SearchTable(INDEX, [Column('_id'), IndexColumn('id', text='keyword')], unique_key='id')
    response = shown.fetch_sync(request('_id', 'id'), client)
    assert [(row['_id'], row['id']) for row in response.data] == [
        ('1', 'a1'),
        ('2', 'b2'),
        ('3', 'c3'),
        ('4', 'd4'),
        ('5', 'e5'),
    ]


LABELS = [IndexColumn('id', text='keyword'), IndexColumn('label')]  # `label` holds several values in a1 and d4
FLAGGED = [
    IndexColumn('id', text='keyword'),
    Column('active', type='boolean'),
]  # ties: a1 and d4, b2 and e5; c3 has none


@pytest.mark.parametrize(
    ('names', 'columns', 'column', 'direction'),
    [
        (NAMES, COLUMNS, 3, 'asc'),  # a1 and d4 tie on `pages`, and c3 has none
        (NAMES, COLUMNS, 3, 'desc'),
        (NAMES, COLUMNS, 1, 'asc'),
        (NAMES, COLUMNS, 1, 'desc'),
        (
            ('id', 'label'),
            LABELS,
            1,
            'asc',
        ),  # several values in a field: `asc` sorts by the least, `desc` by the greatest
        (('id', 'label'), LABELS, 1, 'desc'),
        (NAMES, COLUMNS, 4, 'asc'),  # a date (c3 has none): the server must accept `mode` on a date field too
        (NAMES, COLUMNS, 4, 'desc'),
        (('id', 'active'), FLAGGED, 1, 'asc'),  # a boolean, with ties broken by the key
        (('id', 'active'), FLAGGED, 1, 'desc'),
    ],
    ids=[
        'pages-asc',
        'pages-desc',
        'title-asc',
        'title-desc',
        'label-asc',
        'label-desc',
        'created-asc',
        'created-desc',
        'active-asc',
        'active-desc',
    ],
)
def test_a_page_read_by_the_reversed_sort_is_the_page_the_ordinary_way_gives(
    client: OpenSearch, names: tuple[str, ...], columns: list[Column], column: int, direction: str
):
    """SPEC-C10.3, measured: with a table window of 3 over the 5 documents (the cluster's setting is untouched), every
    page the reversed sort serves equals the same request on a table of the default window, and every other page
    beyond the window is refused. A field of several values is the case a flip without its `mode` gets wrong, and
    `label` (several values in a1 and d4) is that case. Compared by their UTF-8 terms, `asc` sorts by the least value
    of each document: c3, d4 (`bär`), a1, e5, b2. `desc` sorts by the greatest: d4, a1, b2, e5, c3. That is not the
    reverse of the first (b2, e5, a1, d4, c3), so a reversed page that flips `asc` to a `desc` of the default `mode`
    shows other rows than the ordinary way does, and this test fails; with the `mode` of the original direction the
    reverse is exact."""
    small = SearchTable(INDEX, columns, unique_key='id', max_result_window=3)
    wide = SearchTable(INDEX, columns, unique_key='id')
    for start in range(6):
        for length in range(1, 4):
            asked = request(*names, start=start, length=length, order=[{'column': column, 'dir': direction}])
            got, expected = small.fetch_sync(asked, client), wide.fetch_sync(asked, client)
            where = f'start {start}, length {length}'
            if start + length <= 3 or 5 - start <= 3:
                assert got.error_code is None, where
                assert ids(got) == ids(expected), where
            else:
                assert got.error_code == 'page_out_of_range', where


def test_a_renamed_column_is_searched_by_its_stored_field_and_reaches_the_client_by_its_name(client: OpenSearch):
    """SPEC-D2.1: `author` reads `meta.author`: a column search on it finds Bob's document, and the row has `author`,
    never `meta` nor the dotted field."""
    response = table.fetch_sync(request(*NAMES, column_search={'author': {'value': 'bob'}}), client)
    assert found(response) == ['b2']
    assert response.data[0]['author'] == 'Bob'
    assert 'meta' not in response.data[0]
    assert 'meta.author' not in response.data[0]


ALL_IDS = ['a1', 'b2', 'c3', 'd4', 'e5']


def test_a_sync_client_answers_the_base_request(client: OpenSearch):
    """SPEC-A5.1: every document, in the key's order."""
    assert ids(table.fetch_sync(request(*NAMES), client)) == ALL_IDS


async def test_an_async_client_answers_the_base_request(async_client: AsyncOpenSearch):
    """SPEC-A4.1: the same rows through `AsyncOpenSearch`."""
    assert ids(await table.fetch(request(*NAMES), async_client)) == ALL_IDS


@pytest.mark.parametrize('length', range(1, 7))
def test_the_page_walk_of_an_order_with_ties_holds_every_row_once_and_descending_is_its_reverse(
    client: OpenSearch, length: int
):
    """SPEC-O3.2: a1 and d4 tie on `pages` (12) and c3 has none; the pages joined give each document once, in the order
    of one big page, and descending is exactly the reverse."""

    def walk(direction: str) -> list[str]:
        order = [{'column': 3, 'dir': direction}]
        pages = [
            ids(table.fetch_sync(request(*NAMES, start=s, length=length, order=order), client))
            for s in range(0, 5, length)
        ]
        return [row for page in pages for row in page]

    whole = {
        d: ids(table.fetch_sync(request(*NAMES, order=[{'column': 3, 'dir': d}]), client)) for d in ('asc', 'desc')
    }
    assert sorted(whole['asc']) == ALL_IDS
    assert whole['desc'] == whole['asc'][::-1]
    assert walk('asc') == whole['asc']
    assert walk('desc') == whole['desc']


def test_a_page_shorter_than_the_filtered_set_still_counts_the_whole_set(client: OpenSearch):
    """SPEC-C2.1: `apple` finds two documents; with a length of 1 the page holds one and `recordsFiltered` stays 2."""
    page = table.fetch_sync(request(*NAMES, search={'value': 'apple'}, length=1), client)
    assert (page.recordsTotal, page.recordsFiltered, ids(page)) == (5, 2, ['a1'])


def test_a_search_that_finds_nothing_has_filtered_zero_and_the_total_intact(client: OpenSearch):
    """SPEC-C2.2."""
    response = table.fetch_sync(request(*NAMES, search={'value': 'zzzzz'}), client)
    assert (response.recordsTotal, response.recordsFiltered, response.data, response.error) == (5, 0, [], None)


def test_a_start_equal_to_the_document_count_is_an_empty_page_with_both_counts(client: OpenSearch):
    """SPEC-C3.1."""
    response = table.fetch_sync(request(*NAMES, start=5, length=10), client)
    assert (response.recordsTotal, response.recordsFiltered, response.data, response.error) == (5, 5, [], None)


@contextmanager
def added_document(client: OpenSearch, **fields: Any) -> Iterator[None]:
    """A sixth document, in the index for the length of the block: `client` is shared by every test of the module, so
    a test that adds one leaves the index as it found it, whatever the test does. It holds a `title` and a `body`,
    unless `fields` replace them."""
    client.index(index=INDEX, id='f6', body={'id': 'f6', 'title': 'Fig tart', 'body': 'figs', **fields})
    client.indices.refresh(index=INDEX)
    try:
        yield
    finally:
        client.delete(index=INDEX, id='f6')
        client.indices.refresh(index=INDEX)


def test_the_cached_total_is_counted_again_after_the_ttl(client: OpenSearch):
    """SPEC-C14.2: a document added at once is not seen; a request after the TTL (0.3 s, waited out) counts again."""
    cached = SearchTable(INDEX, COLUMNS, unique_key='id', total_cache=TotalCache(ttl=0.3))
    params = request(*NAMES, search={'value': 'apple'})
    assert cached.fetch_sync(params, client).recordsTotal == 5
    with added_document(client):
        assert cached.fetch_sync(params, client).recordsTotal == 5
        time.sleep(0.35)
        assert cached.fetch_sync(params, client).recordsTotal == 6


def test_clear_makes_the_next_request_count_the_cluster_again(client: OpenSearch):
    """SPEC-C16.1: a document added after the first request is not in the total until `clear()`."""
    cache = TotalCache(ttl=3600)
    cached = SearchTable(INDEX, COLUMNS, unique_key='id', total_cache=cache)
    params = request(*NAMES, search={'value': 'apple'})
    assert cached.fetch_sync(params, client).recordsTotal == 5
    with added_document(client):
        assert cached.fetch_sync(params, client).recordsTotal == 5
        cache.clear()
        assert cached.fetch_sync(params, client).recordsTotal == 6


@pytest.mark.parametrize(
    ('column', 'search', 'expected'),
    [
        # `meta.author` is missing from c3. A string column, whose missing field is "empty" (PRD-F26)
        ('author', {'value': '^(|Ann)$', 'regex': True}, ['a1', 'c3']),  # SPEC-F17.1: missing, and a value
        ('title', {'value': '^(|Apple pie)$', 'regex': True}, ['a1']),  # SPEC-F17.2: no document is empty
        ('author', {'value': '^()$', 'regex': True}, ['c3']),  # SPEC-F17.3: a group holding only the empty alternative
        ('author', {'value': '^$', 'regex': True}, ['c3']),  # SPEC-F17.4
        ('author', {'value': 'Ann||Bob', 'regex': True}, ['a1', 'b2', 'c3']),  # SPEC-F17.4: an empty one between
        ('author', {'multi_values': ['', 'Bob']}, ['b2', 'c3']),  # SPEC-F20.2
    ],
)
def test_an_empty_alternative_or_entry_matches_the_documents_without_the_field(
    client: OpenSearch, column: str, search: dict[str, Any], expected: list[str]
):
    """SPEC-F17.1, SPEC-F17.2, SPEC-F17.3, SPEC-F17.4, SPEC-F20.2: "empty" on OpenSearch is a field that is missing, so
    the rows are c3's, which the recording tests' `must_not` `exists` clause only promises."""
    params = request(*NAMES, column_search={column: search})
    assert found(table.fetch_sync(params, client)) == expected


@pytest.mark.parametrize('pattern', ['^(a-b/c)$', r'^(a\-b\/c)$', r'^(\/\.\*\+\?\|\(\)\[\]\{\}\\\$\^\-)$'])
def test_a_categorical_value_with_every_mark_of_the_escape_set_finds_its_document(client: OpenSearch, pattern: str):
    """SPEC-F16.3, SPEC-F19.1: the document's title is `a-b/c` or the whole set `/.*+?|()[]{}\\$^-`, which no pattern
    here may read as an operator (the `-` and `/` either way); f6 is the one document the pattern finds."""
    title = 'a-b/c' if pattern.startswith('^(a') else '/.*+?|()[]{}\\$^-'
    with added_document(client, title=title):
        params = request(*NAMES, column_search={'title': {'value': pattern, 'regex': True}})
        assert found(table.fetch_sync(params, client)) == ['f6']


@pytest.mark.parametrize(
    ('body', 'expected'),
    [
        ({'column_search': {'title': {'value': 'apple'}}, **criterion('author', '=', 'Ann')}, ['a1']),
        (  # the group is a part of the request: it keeps d4 (Cy) and drops a1, which `apple` finds as well
            {
                'column_search': {'title': {'value': 'apple'}},
                'searchBuilder': {
                    'logic': 'OR',
                    'criteria': [
                        {'condition': '=', 'origData': 'author', 'value': ['Cy']},
                        {'condition': 'null', 'origData': 'pages', 'value': []},
                    ],
                },
            },
            ['d4'],
        ),
    ],
)
def test_a_column_search_and_the_searchbuilder_tree_combine_with_and(
    client: OpenSearch, body: dict[str, Any], expected: list[str]
):
    """SPEC-F12.3: `apple` finds a1 and d4, the criterion a1, the group d4 and c3; the answer is the intersection."""
    assert found(table.fetch_sync(request(*NAMES, **body), client)) == expected


def test_two_column_searches_keep_the_documents_both_find(client: OpenSearch):
    """SPEC-F12.1: `apple` in the title is a1 and d4, `green` in the body is b2 and d4; both → d4."""
    title, body = {'title': {'value': 'apple'}}, {'body': {'value': 'green'}}
    answers = [found(table.fetch_sync(request(*NAMES, column_search=search), client)) for search in (title, body)]
    assert answers == [['a1', 'd4'], ['b2', 'd4']]
    assert found(table.fetch_sync(request(*NAMES, column_search={**title, **body}), client)) == ['d4']


@pytest.mark.parametrize(('search', 'expected'), [('green', ['d4']), ('bread', [])])
def test_a_column_search_and_the_global_search_keep_the_documents_both_find(
    client: OpenSearch, search: str, expected: list[str]
):
    """SPEC-F12.2: `apple` in the title is a1 and d4. The global search finds b2 and d4 for `green` and b2 for
    `bread`, so the answer is the intersection, never the union."""
    body = {'column_search': {'title': {'value': 'apple'}}, 'search': {'value': search}}
    assert found(table.fetch_sync(request(*NAMES, **body), client)) == expected


@pytest.mark.parametrize(
    'search', [{'multi_values': ['Alpha', 'Alphabet']}, {'value': '^(Alpha|Alphabet)$', 'regex': True}]
)
def test_a_list_of_values_on_a_hooked_column_finds_the_documents_of_each_hooked_value(
    client: OpenSearch, search: dict[str, Any]
):
    """SPEC-D15.1: p1 holds `enc:Alpha` and p2 `enc:Alphabet`, which `terms` finds whole; not p3 (`enc: Alpha `) nor p4
    (`enc:alpha`), which the hooked column's case-sensitive equality leaves out (PRD-D21)."""
    params = request('id', 'secret', column_search={'secret': search})
    assert found(hooked.fetch_sync(params, client)) == ['p1', 'p2']


@pytest.mark.parametrize('where', ['column_search', 'search'])
@pytest.mark.parametrize(('text', 'expected'), [('a\nb', ['l1']), ('a b', [])])
def test_a_line_feed_in_a_hooked_value_is_matched_as_it_is_stored(
    client: OpenSearch, where: str, text: str, expected: list[str]
):
    """SPEC-D17.3 (column search), SPEC-D12.7 (global search): the keyword field holds `enc:a`, a line feed and `b`
    whole, and the `term` for `a`, a line feed and `b` finds it, while `a b` (a space for the line feed) finds nothing.
    A recording client shows the term's text but not that the server keeps a control character through the client's
    JSON and matches it exactly."""
    if where == 'column_search':
        params = request('id', 'secret', column_search={'secret': {'value': text}})
    else:
        params = request('id', 'secret', search={'value': text})
    assert found(hooked_lines.fetch_sync(params, client)) == expected


@pytest.mark.parametrize(('condition', 'expected'), [('=', ['p1']), ('!=', ['p2', 'p3', 'p4', 'p5'])])
def test_a_searchbuilder_equality_on_a_hooked_column_keeps_a_document_without_the_field_in_the_negation(
    client: OpenSearch, condition: str, expected: list[str]
):
    """SPEC-D18.1: `=` `Alpha` finds p1 (`enc:Alpha`) only; `!=` finds the other four, p5 among them, which has no
    `secret` field at all: a `must_not` keeps a document without the field (PRD-F37), which only the server can show."""
    params = request('id', 'secret', **criterion('secret', condition, 'Alpha'))
    assert found(hooked.fetch_sync(params, client)) == expected


# What a recording client cannot show: that the cluster answers a value of 255 code points. The number `1` * 255 is
# one no `integer` field takes (an undeclared column fails with the client's error, the test above), so here the numbers
# are zeros and a one.
LONG_ANSWERED = [
    ('search', everywhere('a' * 255)),
    ('title search', in_title('a' * 255)),
    ('title, 16 words', in_title(' '.join(f'q{number}' for number in range(16)))),
    ('multi_values', {'column_search': {'id': {'multi_values': ['a', 'b' * 255]}}}),
    ('categorical', {'column_search': {'id': {'value': f'^(a|{"b" * 255})$', 'regex': True}}}),
    ('=', criterion('title', '=', 'a' * 255)),
    ('contains', criterion('title', 'contains', 'a' * 255)),
    ('starts', criterion('title', 'starts', 'a' * 255)),
    ('number', criterion('pages', '=', '0' * 254 + '1')),
    ('between', criterion('pages', 'between', '1', '0' * 254 + '1')),
    ('date', criterion('created', '=', '2024-01-01' + ' ' * 245)),
]


@pytest.mark.parametrize('body', [body for _, body in LONG_ANSWERED], ids=[name for name, _ in LONG_ANSWERED])
def test_a_value_of_255_code_points_is_answered_by_the_cluster(client: OpenSearch, body: dict[str, Any]):
    """SPEC-F14.4, SPEC-F14.2 (16 words in a column): the longest value is sent and no document matches it."""
    assert ids(table.fetch_sync(request(*NAMES, **body), client)) == []


def test_a_boolean_of_255_code_points_is_answered_by_the_cluster(client: OpenSearch):
    """SPEC-F14.4: `true` and 251 spaces are read as `true`: a1 and d4."""
    params = request(*FLAG_NAMES, **criterion('active', '=', 'true' + ' ' * 251))
    assert found(flags.fetch_sync(params, client)) == ['a1', 'd4']


@pytest.mark.parametrize(
    'search',
    [{'value': '^(12|60)$', 'regex': True}, {'multi_values': ['12', '60']}],
    ids=['categorical', 'multi_values'],
)
def test_a_categorical_filter_on_a_number_column_matches_its_numbers(client: OpenSearch, search: dict[str, Any]):
    """SPEC-F33.3: the documents whose pages are 12 or 60."""
    assert found(table.fetch_sync(request(*NAMES, column_search={'pages': search}), client)) == ['a1', 'd4', 'e5']


@pytest.mark.parametrize(
    ('tree', 'expected'),
    [
        (  # SPEC-F28.1: an OR group is a union
            {
                'logic': 'OR',
                'criteria': [
                    {'condition': '=', 'origData': 'title', 'value': ['Date loaf']},
                    {'condition': '=', 'origData': 'pages', 'value': ['12']},
                ],
            },
            ['a1', 'd4', 'e5'],
        ),
        (  # SPEC-F28.2: `apple` is a1 and d4; the nested group keeps a1 (Ann) and c3 (no pages), so only a1
            {
                'logic': 'AND',
                'criteria': [
                    {'condition': 'contains', 'origData': 'title', 'value': ['apple']},
                    {
                        'logic': 'OR',
                        'criteria': [
                            {'condition': '=', 'origData': 'author', 'value': ['Ann']},
                            {'condition': 'null', 'origData': 'pages', 'value': []},
                        ],
                    },
                ],
            },
            ['a1'],  # dropping the nested group gives a1 and d4; OR throughout gives a1, c3 and d4
        ),
    ],
)
def test_searchbuilder_groups_join_their_criteria_by_their_logic(
    client: OpenSearch, tree: dict[str, Any], expected: list[str]
):
    """SPEC-F28.1, SPEC-F28.2."""
    assert found(table.fetch_sync(request(*NAMES, searchBuilder=tree), client)) == expected


@pytest.mark.parametrize(
    ('condition', 'values', 'expected'),
    [
        ('>', ['2026-08-02'], ['d4']),  # e5 is 04:00 UTC on the 2nd, d4 is 03:00 UTC on the 3rd
        ('>=', ['2026-08-03'], ['d4']),
        ('<', ['2026-08-03'], ['e5']),
        ('<=', ['2026-08-02'], ['e5']),
        ('between', ['2026-08-02', '2026-08-02'], ['e5']),  # both ends are whole days
        ('!between', ['2026-08-02', '2026-08-02'], ['a1', 'b2', 'c3', 'd4']),  # no `seen` passes (PRD-F37)
    ],
)
def test_a_range_on_a_date_time_field_compares_the_utc_day(
    client: OpenSearch, condition: str, values: list[str], expected: list[str]
):
    """SPEC-F21.11: e5 was written on the 1st at -05:00 and d4 on the 2nd at -05:00, which are the 2nd and the 3rd in
    UTC; read as written, the days would be the other way round."""
    params = request('id', 'seen', **criterion('seen', condition, *values))
    assert found(timed.fetch_sync(params, client)) == expected


@pytest.mark.parametrize(
    ('column', 'fields'),
    [('author', {'meta': {'author': ''}}), ('folded', {'folded': ''})],
    ids=['text-and-keyword', 'keyword'],
)
def test_a_stored_empty_text_is_not_a_missing_field(client: OpenSearch, column: str, fields: dict[str, Any]):
    """SPEC-F26.2: the document f6 stores `''`, which `null` does not find, and `!null` does. c3 has no author and is
    the only missing one; every document has a `folded` value, so none of them is missing."""
    searched = folded if column == 'folded' else table
    names = ('id', column) if column == 'folded' else NAMES
    with added_document(client, **fields):
        missing = found(searched.fetch_sync(request(*names, **criterion(column, 'null')), client))
        present = found(searched.fetch_sync(request(*names, **criterion(column, '!null')), client))
    if column == 'author':
        assert (missing, present) == (['c3'], ['a1', 'b2', 'd4', 'e5', 'f6'])
    else:
        assert (missing, present) == ([], ['a1', 'b2', 'c3', 'd4', 'e5', 'f6'])
