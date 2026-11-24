"""The smart search on the SQL backend through the public API (PRD-F1 to F7, F14, F35): words, phrases, `!`, and the
bounds of search text.

The spec statements cited speak of their own four-row fixture; the rows here are the same cases on this table. A
case that needs a row of its own inserts it into a table of one searchable column, `team`, which a base filter
limits to the rows the test inserted (ids from 11, named by their position in the list given).
"""

from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy.orm import Session

from datatables_backend import Column
from datatables_backend import DTResponse
from datatables_backend.sqlalchemy import SQLTable

from .conftest import criterion
from .conftest import people
from .conftest import request

ALL = ('id', 'name', 'email', 'age', 'born', 'seen', 'team')
table = SQLTable(
    people,
    [
        Column('id'),
        Column('name'),
        Column('email', field='email_address'),
        Column('age'),
        Column('born'),
        Column('seen'),
        Column('team'),
    ],
)
#: One searchable column, over the rows a test inserts
teams = SQLTable(people, [Column('id', searchable=False), Column('team')], base_filter=people.c.id > 10)


def hook(text: str) -> str:
    return f's{text}'


#: The search value hook of `secret` is no smart search (PRD-D12): one searchable column hooked, one not
mixed = SQLTable(people, [Column('id', searchable=False), Column('name'), Column('secret', search_value=hook)])
only_hooked = SQLTable(people, [Column('id', searchable=False), Column('secret', search_value=hook)])
unsearchable = SQLTable(people, [Column('id', searchable=False), Column('team', searchable=False)])
SPACES = [' ', '\n', '\r', chr(0x2028), chr(0x2029)]


def ids(response: DTResponse) -> list[int]:
    assert response.error is None, response.error
    return [row['id'] for row in response.data]


def refused(response: DTResponse, code: str = 'invalid_filter') -> str:
    assert response.error_code == code
    assert response.error is not None
    return response.error


def glob(text: str) -> dict[str, Any]:
    return {'search': {'value': text}}


def column(text: str, name: str = 'team') -> dict[str, Any]:
    return {'column_search': {name: {'value': text}}}


def found_by(session: Session, rows: Sequence[str | None], *bodies: dict[str, Any]) -> list[list[int]]:
    """For each of ``bodies``, the positions (from 1) of the rows of ``rows`` that it finds in ``teams``; the rows are
    inserted once."""
    session.execute(people.insert(), [{'id': 10 + n, 'team': text, 'secret': 's'} for n, text in enumerate(rows, 1)])
    return [
        [row_id - 10 for row_id in ids(teams.fetch_sync(request('id', 'team', **body), session))] for body in bodies
    ]


def found(session: Session, rows: Sequence[str | None], body: dict[str, Any]) -> list[int]:
    """The positions (from 1) of the rows of ``rows`` that ``body`` finds in ``teams``."""
    return found_by(session, rows, body)[0]


def words(count: int) -> str:
    return ' '.join(f'q{n}' for n in range(count))


# -- words and the row as the unit (PRD-F1, PRD-F2, PRD-F3) ---


@pytest.mark.parametrize(('text', 'expected'), [('red 31', [1]), ('31 red', [1]), ('bob blue', [2])])
def test_every_word_must_be_found_in_some_column_and_not_always_the_same_one(
    session: Session, text: str, expected: list[int]
):
    """SPEC-F1.2, SPEC-F3.1: `red` is in the team and `31` in the age of row 1 only. Matching the whole text, or
    all words in one column, finds nothing."""
    assert ids(table.fetch_sync(request(*ALL, **glob(text)), session)) == expected


@pytest.mark.parametrize(
    ('name', 'text', 'expected'),
    [
        ('name', 'anna 100', [3]),
        ('email', 'example org', [1, 4, 5]),
        ('name', 'ann example', []),  # `example` is in the email of row 1: a column search stays in its column
    ],
)
def test_a_column_search_needs_every_word_in_that_column(session: Session, name: str, text: str, expected: list[int]):
    """SPEC-F2.1, SPEC-F2.2."""
    assert ids(table.fetch_sync(request(*ALL, **column(text, name)), session)) == expected


@pytest.mark.parametrize(
    ('text', 'expected'),
    [('"a" "b"x', [1, 5, 6]), ('x"b"y', [3]), ('x"a b"', [5]), ('"a""b"', [1, 2, 5, 6])],
)
def test_a_phrase_is_read_only_where_a_word_begins(session: Session, text: str, expected: list[int]):
    """SPEC-F3.2: each input is split in its own way, so a change to the rule moves one of the four answers."""
    rows = ['a b x', 'a b', 'xby', 'x b y', 'xa b', 'x a b']
    assert found(session, rows, glob(text)) == expected


# -- phrases and quotes (PRD-F4) and `!` (PRD-F5, PRD-F6, PRD-F35) ---


@pytest.mark.parametrize(
    ('rows', 'body', 'expected'),
    [
        (['red apple', 'x'], glob('"red apple"'), [1]),
        (['red apple'], glob('"apple red"'), []),  # one word, not two: split, `red` and `apple` are both in the row
        (['red apple', 'x'], glob('“red apple”'), [1]),
        (['a b”', 'a b', '“a b'], glob('"a b”'), [1]),
        (['a b”', 'a b', '“a b'], glob('“a b"'), [3]),
        (['50 plum', '5"'], column('50"'), [1]),
        (['plum', 'x'], column('"plum'), [1]),
        (['5" pipe', '50', 'x'], glob('!5"'), [2, 3]),
        (['x “abc', 'abc'], glob('“abc'), [1]),
        (['"a b”', 'a b”'], glob('!"a b”'), [2]),
        ([' a ', 'xax', 'a'], glob('" a "'), [1]),
    ],
)
def test_a_phrase_is_one_word_with_its_quotes_read_as_datatables_reads_them(
    session: Session, rows: list[str | None], body: dict[str, Any], expected: list[int]
):
    """SPEC-F4.1, SPEC-F4.2, SPEC-F4.3, SPEC-F4.4, SPEC-F4.5, SPEC-F4.6, SPEC-F4.7, SPEC-F4.8. The last row: a phrase
    keeps the spaces inside its quotes."""
    assert found(session, rows, body) == expected


def test_an_unclosed_quote_opens_no_phrase_and_each_word_after_it_matches_alone(session: Session):
    """SPEC-F4.9: `"apple red` is the words `apple` and `red`, which row 1 holds in the other order; read as a phrase
    to the end of the text it finds no row (as `"apple red"` does, SPEC-F4.1)."""
    rows = ['red apple', 'apple', 'red']
    assert found_by(session, rows, glob('"apple red'), glob('apple red'), glob('"apple red"')) == [[1], [1], []]


@pytest.mark.parametrize(
    ('rows', 'body', 'expected'),
    [
        (['red apple', 'reddish pear', 'blue', 'pipe', None], glob('!reddish'), [1, 3, 4, 5]),
        (['red apple', 'reddish pear', 'blue', 'RED pipe'], glob('red !apple'), [2, 4]),
        (['red apple', 'red', 'x apple red'], glob('!"red apple"'), [2, 3]),
        (['pipe', 'x', None], column('!pipe'), [2, 3]),  # NULL contains no word, so it passes
        (['a-b', 'a.b', 'abc'], glob('!-'), [2, 3]),
        (['a-b', 'a.b', 'abc'], glob('!.'), [1, 3]),
        (['x', 'y', None], glob('!x'), [1, 2, 3]),  # a word of one character adds nothing
    ],
)
def test_a_word_with_a_bang_excludes_the_rows_that_hold_it(
    session: Session, rows: list[str | None], body: dict[str, Any], expected: list[int]
):
    """SPEC-F5.1, SPEC-F5.2, SPEC-F5.3, SPEC-F6.1, SPEC-F35.1, SPEC-F35.2: a NULL column passes, and `!x` is no
    condition."""
    assert found(session, rows, body) == expected


def test_an_empty_quote_next_to_other_words_adds_nothing(session: Session):
    """SPEC-F6.5: the empty quote is dropped, so the row whose `team` is NULL still passes `!pipe` (kept as an empty
    word it would be a condition the NULL cannot meet)."""
    rows = ['pipe', 'x', None]
    assert found_by(session, rows, column('!pipe ""'), column('!pipe')) == [[2, 3], [2, 3]]


def test_a_negated_word_is_looked_for_in_the_text_of_a_date_too(session: Session):
    """SPEC-F35.1: every row with a date holds `-` in its day text, so only row 3, which has none, is left."""
    assert ids(table.fetch_sync(request(*ALL, **glob('!-')), session)) == [3]


@pytest.mark.parametrize('kind', [glob, column])
@pytest.mark.parametrize('text', ['"', '"""', '!""', '""', '!', '!x'])
def test_text_that_reduces_to_no_word_finds_every_row(session: Session, kind: Any, text: str):
    """SPEC-F6.2: kept as a condition, an empty word would drop the row whose `team` is NULL (and `"` finds only the
    row that holds one)."""
    assert found(session, ['x', None, '5"'], kind(text)) == [1, 2, 3]


@pytest.mark.parametrize('name', ['active', 'age', 'born'])
@pytest.mark.parametrize('text', ['"', '!x'])
def test_text_with_no_word_adds_nothing_on_a_column_of_any_type(session: Session, name: str, text: str):
    """SPEC-F6.2 for PRD-F6's "column of any type": a boolean column must not ask for `true` or `false` of it."""
    typed = SQLTable(people, [Column('id'), Column(name)])
    assert ids(typed.fetch_sync(request('id', name, **column(text, name)), session)) == [1, 2, 3, 4, 5]


def test_a_search_with_no_word_asks_for_no_searchable_column(session: Session):
    """SPEC-F6.2 with PRD-F13: a refusal for lack of a column is for a search that has a word."""
    assert ids(unsearchable.fetch_sync(request('id', 'team', **glob('!x')), session)) == [1, 2, 3, 4, 5]


def test_a_search_with_a_word_on_a_table_with_no_searchable_column_is_refused(session: Session):
    """SPEC-F13.1: nothing can answer `red`, so it is the user's mistake and not an empty page."""
    refused(unsearchable.fetch_sync(request('id', 'team', **glob('red')), session))


@pytest.mark.parametrize('text', ['', '   '])
def test_an_empty_search_on_a_table_with_no_searchable_column_asks_for_nothing(session: Session, text: str):
    """SPEC-F13.3: no word, so no column is needed and every row is answered (`!x` is the other such text, in the test
    above)."""
    assert ids(unsearchable.fetch_sync(request('id', 'team', **glob(text)), session)) == [1, 2, 3, 4, 5]


@pytest.mark.parametrize('space', SPACES)
def test_each_of_the_five_space_characters_splits_words(session: Session, space: str):
    """SPEC-F7.3: `aa bb` finds the row holding `aa` and `bb` apart and the row holding `aabb`, never `bb` alone."""
    assert found(session, ['aa bb', 'aabb', 'bb'], glob(f'aa{space}bb')) == [1, 2]


@pytest.mark.parametrize('space', SPACES)
def test_a_line_break_in_search_text_is_a_space(session: Session, space: str):
    """SPEC-F7.1: `red` and `apple` split by any of the five are the words of `red apple`."""
    rows = ['red apple', 'red', 'apple']
    assert found_by(session, rows, glob(f'red{space}apple'), glob('red apple')) == [[1], [1]]


def test_an_exact_value_with_a_line_break_is_never_rewritten(session: Session):
    """SPEC-F7.2: `a` + LF + `b` is that value and not `a b`, in `multi_values` and in SearchBuilder `=`."""
    rows = ['a\nb', 'a b']
    listed = {'column_search': {'team': {'multi_values': ['a\nb']}}}
    assert found_by(session, rows, listed, criterion('team', '=', 'a\nb')) == [[1], [1]]


def test_a_leading_minus_is_part_of_the_word(session: Session):
    """SPEC-F8.2: `-red` is searched as text, so `red` alone does not match."""
    assert found(session, ['-red', 'red', 'blue'], glob('-red')) == [1]


@pytest.mark.parametrize(('text', 'expected'), [('red*', [1]), ('re?', [4])])
def test_wildcard_characters_in_search_text_are_text(session: Session, text: str, expected: list[int]):
    """SPEC-F8.3: `*` and `?` match themselves and no other character."""
    assert found(session, ['red*', 'red', 'redx', 're?', 'rex'], glob(text)) == expected


def test_a_star_in_contains_is_text(session: Session):
    """SPEC-F8.4: `a*b` is found inside `xa*by` and not in `xaby`."""
    assert found(session, ['xa*by', 'xaby'], criterion('team', 'contains', 'a*b')) == [1]


@pytest.mark.parametrize(('value', 'rows'), [('x_y', ['x_y', 'xay']), ('50%', ['50%', '50 plum'])])
def test_the_like_wildcards_in_contains_are_text(session: Session, value: str, rows: list[str]):
    """SPEC-F8.5: `_` and `%` match themselves; SQL's `LIKE` would match `xay` and `50 plum` too."""
    assert found(session, rows, criterion('team', 'contains', value)) == [1]


@pytest.mark.parametrize('text', ['red NOT apple', 'red not apple'])
def test_not_is_a_word_and_negates_nothing(session: Session, text: str):
    """SPEC-F8.6: only the row holding `not` is found; as a negation `red NOT apple` would find row 2."""
    assert found(session, ['red apple', 'red', 'apple', 'red not apple'], glob(text)) == [4]


# -- a hooked column in the global search (PRD-D12) ---

TWENTY = ' '.join(
    'abcdefghijklmnopqrst'
)  # twenty one-letter words: 39 characters, within `secret`'s 50 with the hook's `s`
#: Stored as the hook `s{text}` would make them: a superset of `1`, a padded `1`, the bare marks of the grammar, a line
#: feed beside the same letters with a space, a negated word, and twenty words
HOOKED_ROWS = {
    11: 's1x',
    12: 's 1 ',
    13: 's"1"',
    14: 's!x',
    15: 's"',
    16: 'sa\nb',
    17: 'sa b',
    18: 's!ab',
    19: f's{TWENTY}',
}
PADDED = f'{chr(0xA0)} {chr(0x3000)}'  # White_Space that PRD-F7 does not count as a space
NAMES = {'mixed': ('id', 'name', 'secret'), 'hooked': ('id', 'secret')}


def recording() -> tuple[list[str], dict[str, SQLTable]]:
    """The texts a hook is given, and `mixed` and `only_hooked` over a hook that records them."""
    seen: list[str] = []

    def recorded(text: str) -> str:
        seen.append(text)
        return hook(text)

    return seen, {
        'mixed': SQLTable(
            people, [Column('id', searchable=False), Column('name'), Column('secret', search_value=recorded)]
        ),
        'hooked': SQLTable(people, [Column('id', searchable=False), Column('secret', search_value=recorded)]),
    }


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('1', [1]),  # not row 11, whose `s1x` holds `s1`
        (f'{PADDED}1{PADDED}', [1]),  # SPEC-D12.3: Unicode White_Space, not only the five of PRD-F7; not row 12
        (' 1 ', [1]),
        ('"1"', [13]),  # SPEC-D12.1: no grammar, so the quotes are part of the value
        ('!ab', [18]),  # a negated word excludes nothing by the hooked column: the whole text is compared
        ('a\nb', [16]),  # SPEC-D12.7: PRD-F7's rewriting of a line break does not apply
        (TWENTY, [19]),  # SPEC-D12.5: no word cap where every searchable column is hooked
    ],
)
def test_a_hooked_column_is_equality_on_the_trimmed_whole_text_in_the_global_search(
    session: Session, text: str, expected: list[int]
):
    """SPEC-D12.1, SPEC-D12.3, SPEC-D12.5, SPEC-D12.7, SPEC-D12.9 on a table whose every searchable column is hooked,
    so the equality alone decides. A match on a part of the value finds more rows (`1` also row 11), and one that did not
    trim hands the hook the padding."""
    session.execute(people.insert(), [{'id': row, 'secret': stored} for row, stored in HOOKED_ROWS.items()])
    assert ids(only_hooked.fetch_sync(request('id', 'secret', **glob(text)), session)) == expected


def test_a_hooked_column_beside_other_columns_is_equality_on_the_whole_text(session: Session):
    """SPEC-D12.1 on a mixed table: `1` finds row 1 by equality and row 3, whose name `anna_100%` holds it, but not
    row 11 (`s1x`); `1x` finds row 11, which only the hooked column holds whole."""
    session.execute(people.insert(), [{'id': 11, 'secret': 's1x'}])
    assert ids(mixed.fetch_sync(request(*NAMES['mixed'], **glob('1')), session)) == [1, 3]
    assert ids(mixed.fetch_sync(request(*NAMES['mixed'], **glob('1x')), session)) == [11]


def test_the_words_of_the_text_are_not_spread_over_a_hooked_column(session: Session):
    """SPEC-D12.2: `anna` is in the name of row 3 and `3` is the hooked `s3`, but the whole text `anna 3` is neither
    found in the name nor equal to a hooked value. A hooked column matched word by word finds row 3."""
    assert ids(mixed.fetch_sync(request(*NAMES['mixed'], **glob('anna 3')), session)) == []


def test_a_negated_word_excludes_no_row_by_a_hooked_column(session: Session):
    """SPEC-D12.4: `!s3` leaves out the rows whose name holds `s3`, and never row 21, whose hooked value is `ss3`,
    which is what the hook makes of `s3`."""
    session.execute(people.insert(), [{'id': 21, 'name': 'zed', 'secret': 'ss3'}])
    assert ids(mixed.fetch_sync(request(*NAMES['mixed'], **glob('!s3')), session)) == [1, 2, 3, 4, 5, 21]


@pytest.mark.parametrize('shape', ['mixed', 'hooked'])
@pytest.mark.parametrize('text', ['  Alpha  ', f'{PADDED}Alpha{PADDED}', 'Alpha beta', '!Alpha'])
def test_the_hook_is_given_the_trimmed_whole_text_once(session: Session, shape: str, text: str):
    """SPEC-D12.3, SPEC-D12.4: one call, with the text trimmed and not split into words. `!Alpha` is compared whole,
    so its hook sees the `!`."""
    seen, tables = recording()
    tables[shape].fetch_sync(request(*NAMES[shape], **glob(text)), session)
    assert seen == [text.strip()]


@pytest.mark.parametrize('shape', ['mixed', 'hooked'])
@pytest.mark.parametrize('text', ['""', '!x', '!"', '"', '!', '   '])
def test_text_with_no_word_adds_nothing_to_a_hooked_column_in_the_global_search(
    session: Session, shape: str, text: str
):
    """SPEC-F6.2, SPEC-D12.9: text whose every token adds nothing adds nothing here either, on a table of hooked
    columns only as on a mixed one, and the hook is not called (it would turn `!x` into `s!x`, which no row holds)."""
    seen, tables = recording()
    assert ids(tables[shape].fetch_sync(request(*NAMES[shape], **glob(text)), session)) == [1, 2, 3, 4, 5]
    assert seen == []


def test_text_of_white_space_beyond_the_five_adds_nothing_to_the_hooked_side(session: Session):
    """SPEC-F6.3 with PRD-D12's trimming: trimmed to nothing, the text is blank for the hooked column, so with no
    other column every row is found and the hook is not called. The other columns read it as PRD-F6 does, where
    U+00A0 is a word that no name holds."""
    seen, tables = recording()
    assert ids(tables['hooked'].fetch_sync(request(*NAMES['hooked'], **glob(chr(0xA0))), session)) == [1, 2, 3, 4, 5]
    assert ids(tables['mixed'].fetch_sync(request(*NAMES['mixed'], **glob(chr(0xA0))), session)) == []
    assert seen == []


# -- a column search on a hooked column (PRD-D17) ---


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('1', [1]),  # not row 11, whose `s1x` holds `s1`
        (' 1 ', [1]),  # not row 12: the hook is given `1`, not ` 1 `
        (f'{chr(0xA0)}1{chr(0x3000)}', [1]),  # SPEC-D17.1: Unicode White_Space, not only the five of PRD-F7
        (f'{chr(0x2003)}1{chr(0x2003)}', [1]),
        ('1x', [11]),
        ('"1"', [13]),  # SPEC-D17.1: no grammar, so the quotes are part of the value
        ('!x', [14]),  # ... and `!` too: it is no negation, and the text is no "no word"
        ('"', [15]),
        ('a\nb', [16]),  # SPEC-D17.3: PRD-F7's rewriting of a line break does not apply
        ('a b', [17]),
    ],
)
def test_a_column_search_on_a_hooked_column_is_equality_on_the_trimmed_whole_text(
    session: Session, text: str, expected: list[int]
):
    """SPEC-D17.1, SPEC-D17.3. A search that took the text apart into words or matched a part of it finds more rows
    here (`1` also row 11, `!x` every row), and one that did not trim hands the hook the padding."""
    session.execute(people.insert(), [{'id': row, 'secret': stored} for row, stored in HOOKED_ROWS.items()])
    assert ids(mixed.fetch_sync(request('id', 'name', 'secret', **column(text, 'secret')), session)) == expected


@pytest.mark.parametrize('blank', [chr(0xA0), chr(0x3000), chr(0x2003)])
def test_a_column_search_on_a_hooked_column_of_blank_text_adds_nothing(session: Session, blank: str):
    """SPEC-F6.3 with PRD-D12's trimming: text of only White_Space beyond PRD-F7's five is blank too, so every row
    is found; handed to the hook it would be the value `s` plus the space, which no row holds."""
    assert ids(mixed.fetch_sync(request('id', 'name', 'secret', **column(blank, 'secret')), session)) == [
        1,
        2,
        3,
        4,
        5,
    ]


@pytest.mark.parametrize(
    'search',
    [
        {'multi_values': ['1', '2']},
        {'value': '^(1|2)$', 'regex': True},
    ],
)
def test_a_list_of_values_on_a_hooked_column_gives_the_hook_each_value_once(session: Session, search: dict[str, Any]):
    """SPEC-D15.1: `multi_values` and a categorical filter find the rows whose stored value is the hook's answer for
    either value, and the hook is called for `1` and for `2` once each. A list given to the hook whole, or a value
    transformed twice, shows in what it saw."""
    seen, tables = recording()
    response = tables['hooked'].fetch_sync(request('id', 'secret', column_search={'secret': search}), session)
    assert ids(response) == [1, 2]
    assert seen == ['1', '2']


# -- the bounds of search text (PRD-F14), each with its own code (PRD-E2) ---

EMOJI = chr(0x1F600)
TABLES = {'all': (table, ALL), 'mixed': (mixed, ('id', 'name', 'secret')), 'hooked': (only_hooked, ('id', 'secret'))}


@pytest.mark.parametrize(
    ('which', 'body', 'bound'),
    [
        ('all', glob(words(16)), None),
        ('all', glob(f'{words(16)} !x "" !"" "'), None),  # tokens that reduce to no word are not counted
        ('all', glob(f'{words(15)} "a b"'), None),  # a phrase is one word
        ('all', glob(f'{words(16)} "a b"'), '16'),
        ('all', column(words(16), 'name'), None),
        (
            'all',
            column(f'{words(16)} !x "" !"" "', 'name'),
            None,
        ),  # blank tokens and `!` are no words in a column either
        ('all', column(f'{words(15)} "a b"', 'name'), None),  # a phrase is one word there too
        ('all', column(f'{words(16)} "a b"', 'name'), '16'),
        ('all', glob('a' * 255), None),
        ('all', glob(' ' * 256), '255'),  # the raw text is measured, blank or not
        ('all', column(' ' * 256, 'name'), '255'),
        ('all', glob(EMOJI * 255), None),  # code points, not UTF-16 units or bytes
        ('all', glob(EMOJI * 256), '255'),
        ('mixed', glob(words(17)), '16'),  # PRD-D12: otherwise the whole request is refused
        ('hooked', glob(words(17)), None),  # ... and the cap is lifted when every searchable column is hooked
        ('hooked', glob('a' * 256), '255'),
        ('hooked', column(words(17), 'secret'), None),  # PRD-D17: no word cap on a hooked column's own search
        ('hooked', column(words(20), 'secret'), None),  # SPEC-D17.2: twenty words
        ('hooked', column('a' * 256, 'secret'), '255'),
        # SPEC-D12.8: `Alpha` and 251 spaces around it, so 5 characters after trimming: the raw text is measured
        ('hooked', glob(' ' * 125 + 'Alpha' + ' ' * 126), '255'),
    ],
)
def test_search_text_is_bound_in_code_points_and_words(
    session: Session, which: str, body: dict[str, Any], bound: str | None
):
    """SPEC-F14.1, SPEC-F14.2, SPEC-F14.3, SPEC-D12.6, SPEC-D12.8, SPEC-D17.2. A refusal names its bound and echoes
    nothing sent."""
    source, names = TABLES[which]
    response = source.fetch_sync(request(*names, **body), session)
    if bound is None:
        assert response.error is None
    else:
        message = refused(response, 'value_too_long' if bound == '255' else 'too_many_words')
        assert bound in message
        assert 'q0' not in message
        assert 'aaa' not in message


def test_search_text_over_the_word_bound_is_counted_with_no_column_to_search(session: Session):
    """PRD-F14, PRD-F13: with no searchable column the cap is not lifted (nothing is hooked), so the words are the
    mistake and not the missing column."""
    response = unsearchable.fetch_sync(request('id', 'team', **glob(words(17))), session)
    assert refused(response, 'too_many_words')
