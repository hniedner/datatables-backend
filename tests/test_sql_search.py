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


def found(session: Session, rows: Sequence[str | None], body: dict[str, Any]) -> list[int]:
    """The positions (from 1) of the rows of ``rows`` that ``body`` finds in ``teams``."""
    session.execute(people.insert(), [{'id': 10 + n, 'team': text, 'secret': 's'} for n, text in enumerate(rows, 1)])
    return [row_id - 10 for row_id in ids(teams.fetch_sync(request('id', 'team', **body), session))]


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
    """SPEC-F4.1 to SPEC-F4.8. The last row: a phrase keeps the spaces inside its quotes."""
    assert found(session, rows, body) == expected


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
    """SPEC-F5.1 to SPEC-F5.3, SPEC-F6.1, SPEC-F35.1, SPEC-F35.2: a NULL column passes, and `!x` is no condition."""
    assert found(session, rows, body) == expected


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


@pytest.mark.parametrize('space', SPACES)
def test_each_of_the_five_space_characters_splits_words(session: Session, space: str):
    """SPEC-F7.3: `aa bb` finds the row holding `aa` and `bb` apart and the row holding `aabb`, never `bb` alone."""
    assert found(session, ['aa bb', 'aabb', 'bb'], glob(f'aa{space}bb')) == [1, 2]


# -- hooked columns keep today's whole-text match until PRD-D12 (28) ---


def test_a_hooked_column_is_matched_for_the_whole_text_beside_the_words_of_the_others(session: Session):
    assert ids(mixed.fetch_sync(request('id', 'name', 'secret', **glob('3')), session)) == [3]


def test_text_with_no_word_adds_nothing_to_a_hooked_column_either(session: Session):
    """SPEC-F6.2 with PRD-D12: the hook would turn `!x` into `s!x`, which no row holds."""
    assert ids(mixed.fetch_sync(request('id', 'name', 'secret', **glob('!x')), session)) == [1, 2, 3, 4, 5]


# -- the bounds of search text (PRD-F14), each with its own code (PRD-E2) ---

EMOJI = chr(0x1F600)
TABLES = {'all': (table, ALL), 'mixed': (mixed, ('id', 'name', 'secret')), 'hooked': (only_hooked, ('id', 'secret'))}


@pytest.mark.parametrize(
    ('which', 'body', 'bound'),
    [
        ('all', glob(words(16)), None),
        ('all', glob(words(17)), '16'),
        ('all', glob(f'{words(16)} !x "" !"" "'), None),  # tokens that reduce to no word are not counted
        ('all', glob(f'{words(15)} "a b"'), None),  # a phrase is one word
        ('all', glob(f'{words(16)} "a b"'), '16'),
        ('all', column(words(16), 'name'), None),
        ('all', column(words(17), 'name'), '16'),
        ('all', glob('a' * 255), None),
        ('all', glob('a' * 256), '255'),
        ('all', glob(' ' * 256), '255'),  # the raw text is measured, blank or not
        ('all', column(' ' * 256, 'name'), '255'),
        ('all', glob(EMOJI * 255), None),  # code points, not UTF-16 units or bytes
        ('all', glob(EMOJI * 256), '255'),
        ('all', column('a' * 256, 'name'), '255'),
        ('mixed', glob(words(17)), '16'),  # PRD-D12: otherwise the whole request is refused
        ('hooked', glob(words(17)), None),  # ... and the cap is lifted when every searchable column is hooked
        ('hooked', glob('a' * 256), '255'),
        ('hooked', column(words(17), 'secret'), None),  # PRD-D17: no word cap on a hooked column's own search
        ('hooked', column('a' * 256, 'secret'), '255'),
    ],
)
def test_search_text_is_bound_in_code_points_and_words(
    session: Session, which: str, body: dict[str, Any], bound: str | None
):
    """SPEC-F14.1 to SPEC-F14.3, SPEC-D12 and SPEC-D17. A refusal names its bound and echoes nothing sent."""
    source, names = TABLES[which]
    response = source.fetch_sync(request(*names, **body), session)
    if bound is None:
        assert response.error is None
    else:
        message = refused(response, 'value_too_long' if bound == '255' else 'too_many_words')
        assert bound in message
        assert 'q0' not in message
        assert 'aaa' not in message
