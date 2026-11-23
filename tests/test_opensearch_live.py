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
        'seen': {'type': 'date'},  # a date-time field, in the default format; searched only by the table `timed`
        'meta': {'properties': {'author': KEYWORD_TEXT}},
        'active': {'type': 'boolean'},
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
     'meta': {'author': 'Bob'}, 'active': False, 'label': DECOY, 'folded': f'a{ASTERISK}b'},
    {'id': 'c3', 'title': 'Cherry tart 100%', 'body': 'sour cherries', 'label': LONGEST, 'folded': 'a?b'},
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
table = SearchTable(INDEX, COLUMNS)
timed = SearchTable(INDEX, [IndexColumn('id', text='keyword'), Column('seen', type='date')])

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
                                     IndexColumn('both')])  # fmt: skip
PHRASE_NAMES = ('id', 'only_text', 'both')
hooked = SearchTable(
    PHRASE_INDEX, [IndexColumn('id', text='keyword'), Column('secret', search_value=lambda text: f'enc:{text}')]
)


@pytest.fixture(scope='module')
def client() -> Iterator[OpenSearch]:
    client = OpenSearch(hosts=[URL])
    for index in (INDEX, PHRASE_INDEX):
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
    client.indices.refresh(index=[INDEX, PHRASE_INDEX])
    yield client
    client.indices.delete(index=[INDEX, PHRASE_INDEX])
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
        ('bread green', ['b2']),  # SPEC-F1.1: the title of b2 and its body; as one text it found d4's `green` too
        ('green !apple', ['b2']),  # SPEC-F5.3: d4's title holds `apple`
        ('!apple', ['b2', 'c3', 'e5']),  # SPEC-F5.1: c3 and e5 have no author, which passes
        ('!x', ['a1', 'b2', 'c3', 'd4', 'e5']),  # SPEC-F35.2 and SPEC-F6.2: a word of one character adds nothing
        ('"', ['a1', 'b2', 'c3', 'd4', 'e5']),  # SPEC-F6.2: no word at all
    ],
)
def test_the_global_search(client: OpenSearch, text: str, expected: list[str]):
    assert found(table.fetch_sync(request(*NAMES, search={'value': text}), client)) == expected


@pytest.mark.parametrize(
    ('column', 'value', 'expected'),
    [
        (2, 'granny apples', ['a1']),
        (2, '"green tea"', ['b2']),  # SPEC-F4.1: a phrase; d4's body holds `green` only
        (2, 'green tea', ['b2']),  # SPEC-F2.1: both words; as one text it found d4's `green` too
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


@pytest.mark.parametrize(
    ('text', 'expected'),
    [('Alpha', ['p1']), (f' {chr(0xA0)}Alpha{chr(0x3000)}', ['p1']), ('Alph', []), ('alpha', ['p4'])],
)
def test_a_column_search_on_a_hooked_column_matches_the_whole_stored_value(
    client: OpenSearch, text: str, expected: list[str]
):
    """SPEC-D17.1: the `term` on the keyword field finds `enc:Alpha` only: not the superset `enc:Alphabet` (p2), not
    the padded `enc: Alpha ` (p3) and, case-sensitive as a hooked column is (PRD-D21), not `enc:alpha` (p4), which
    `alpha` finds. A recording client cannot show that the server matches the whole value and not a substring."""
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
labelled = SearchTable(INDEX, [IndexColumn('id', text='keyword'), IndexColumn('label')])  # e5 holds `ÄPFEL`


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
    INDEX, [IndexColumn('id', text='keyword'), IndexColumn('label', field='label.keyword', text='keyword')]
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


folded = SearchTable(INDEX, [IndexColumn('id', text='keyword'), IndexColumn('folded', text='keyword')])


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


@pytest.mark.xfail(
    reason="PRD-E7, issue #47: a number outside the range of the field's own mapping (`pages` is an `integer`) is "
    'refused by the server with a 400 ("out of range for an integer"), so the request fails where the user\'s '
    'mistake should be answered with invalid_number. PRD section 5 states the limit until the issue is closed',
    strict=True,
    raises=RequestError,
)
def test_a_number_outside_the_range_of_its_field_is_the_users_mistake(client: OpenSearch):
    """The library does not read the mapping (PRD-D8), so it cannot know the field's range; SQL knows its
    column's."""
    params = request(*NAMES, **criterion('pages', '=', str(2**64)))
    assert table.fetch_sync(params, client).error_code == 'invalid_number'


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
    response = SearchTable(INDEX, columns).fetch_sync(request(*names, search={'value': 'x'}), client)
    assert response.error is None
