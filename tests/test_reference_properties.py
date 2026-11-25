"""The library against a reference evaluator (issue #205): generated rows and requests, answered by `fetch_sync` on an
in-memory SQLite database and by `tests/reference_evaluator.py`, which is written from `docs/PRD.md` and `spec/` and
never from `src`. Both give the same rows in the same order with the same counts, or the same error code. A
difference is a finding (a gap in the specification, a defect in the library or an engine fact), reported and never
silenced by narrowing the generator.

The table: `id` (Integer, the key), `s` and `t` (String), `n` (BigInteger), `d` (Date) and `b` (Boolean), 0 to 8 rows
inserted in a random key order. The request: the global search, column searches (smart, categorical, `multi_values`),
ColumnControl's search and list, a SearchBuilder tree of at most 5 criteria, 0 to 3 order entries (often a skipped one
first, then one applied in the other direction, now and then over rows that tie on the applied column with a short
page that cuts the tie, half of those with no filter to thin the tie out), paging past the end included; on any
column, now and then column search text that PRD-F6 reduces to no word, alone or beside a list; columns named twice,
nameless columns and an undeclared one; a server declaration with at least one unorderable column, and mistakes of
every step of PRD-E3. The rates are set so that about half of the requests are answered (54% averaged over seeds,
63% on the `ci` seed) and most outcomes and paths are reached at least about 30 times in 500 examples; one seed varies
around that (`uv run pytest tests/test_reference_properties.py --hypothesis-show-statistics` prints its events). The
paths that tell known mutants apart (M4 to M10b in the events) are drawn on purpose, each in about 1% or more of the
examples: on the `ci` seed M4 about 4%, M5b 3%, M6 2%, M7 9% (`between`) and 2% (`!between`), M10 4% and M10b 3%.
Some cases are rare on any seed and carried by the examples on the property and by the hand-written tests, not by the
`ci` draw: a regex over 255 code points that is no categorical filter (about 4 in 1,000 random examples) and an entry
of 256 code points on a list on the global search (`explore` found mutants of these two only after 1,200 to 1,500
examples), and the order within step 3 of PRD-E3. A choice is drawn by `chance`
and `rarely` of `tests/conftest.py`: Hypothesis draws the first of a list of choices far more often than the others.

Covered (PRD): the smart search's grammar (F3, F4, F6, F7 for LF and CR, F8, F35); the global and column searches (F1,
F2, F11, F12, F13, F41, F44, and F23: on SQL a number or date column is searched as its text); ASCII case folding
(F9); categorical filters and lists (F16 to F20, F33, F39, F40); SearchBuilder (F21 for days, F22, F24, F25, F26,
F27, F28, F30, F36, F37, F43); ColumnControl (F45); ordering (O1 to O4, O8, and O6 as SQLite's `BINARY` collation,
which is SQLite's own and not the other databases'); counts and paging (C1 to C3); the error codes and which one a
request with several mistakes gets (E2, E3, F14, F15). Of PRD-E3, the order within step 3 (the maximum date of
PRD-F24 as a bound before a number the column's own type does not take) is carried by the examples on the property and by the
hand-written tests, not by the random draw, which seldom holds both mistakes at once.

Left out: case folding beyond ASCII and every character outside ASCII (curly quotes, U+2028 and U+2029 included);
datetimes and time zones, and a time of day after a day; numbers with a fraction or an exponent; hooked columns,
`fold_case=False` and `field=`; a base filter and a scope; the size bounds (PRD-F38's, and `request_too_large`); what is
OpenSearch's only (`page_out_of_range`, PRD-C10 to PRD-C13, PRD-O5, PRD-O7); the total cache; the `error` texts and
the log records; the types a database adds of its own.

Generator restrictions, each with its reason:
- A number beyond 32 bits is sent only to `n`, the BigInteger: `id` is an Integer, whose width the evaluator does not
  model. The evaluator reads every number column with a BigInteger's range, the mistake of the column's own type that
  PRD-E3 answers in step 3 after the maximum date of PRD-F24 as a bound.
- No value with a fraction or an exponent, no curly quote and no character outside ASCII: out of scope above.
- A request column's `name` is always empty, so its name is its `data` (PRD-P15), and an order entry sends no `name`:
  those readings are `parse_request`'s, which the parse properties and the hand-written tests cover.
- ColumnControl's search is always sent with `type` `text`: the date control is refused while parsing (PRD-F45), which
  `tests/test_parse_properties.py` covers.
- No SearchBuilder tree near PRD-F38's bounds, no list near 256 values and no request near PRD-C17's 960 conditions:
  out of scope above.

A step-1 value (a control character, 256 code points, 17 words) is also sent where nothing reads it, and the evaluator
answers as the requirements say (#205): in an unfinished criterion (PRD-F14: "A value no condition reads, a surplus
value or that of `null`, is not measured"; PRD-F15 looks only at "a SearchBuilder value its condition reads
(PRD-F36)"); in a regex that is no categorical filter (PRD-F14 measures alternatives "after unescaping and not the raw
pattern", and PRD-F18 refuses any other regex); and in a list on the global search, whose entries PRD-F40 does not
read, so PRD-F14 and PRD-F15 do not apply to them and PRD-F38's bounds on lists do not count them: now and then such a
list holds more than 256, or more than 512, entries beside a mistake of step 2, which answers. ColumnControl's `contains` is the column search of PRD-F2
(PRD-F45), so PRD-F14's word cap applies to it: more than 16 words are `too_many_words`.

Where the PRD is silent the evaluator must raise a marked gap rather than guess; no rule needs one today, since every
gap met so far is decided (#205), and a gap found later is added back as a marked `SpecGap`.
"""

from collections import Counter
from copy import deepcopy
from datetime import date
from typing import Any
import unicodedata

from hypothesis import event
from hypothesis import example
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import Column as SQLColumn
from sqlalchemy import Date
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import StaticPool
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from datatables_backend import Column
from datatables_backend import DTParams
from datatables_backend import parse_request
from datatables_backend.sqlalchemy import SQLTable

from .conftest import chance
from .conftest import rarely
from .reference_evaluator import Answer
from .reference_evaluator import Declared
from .reference_evaluator import Kind
from .reference_evaluator import Refusal
from .reference_evaluator import evaluate
from .reference_evaluator import words

KINDS: dict[str, Kind] = {'id': 'number', 's': 'string', 't': 'string', 'n': 'number', 'd': 'date', 'b': 'boolean'}
NAMES = list(KINDS)

_metadata = MetaData()
reference_rows = Table(
    'reference_rows',
    _metadata,
    SQLColumn('id', Integer, primary_key=True),
    SQLColumn('s', String(20)),
    SQLColumn('t', String(20)),
    SQLColumn('n', BigInteger),
    SQLColumn('d', Date),
    SQLColumn('b', Boolean),
)
# One in-memory database for every example; its rows are replaced per example.
_engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
_metadata.create_all(_engine)

STRINGS = ['', ' ', 'a', 'A', 'ab', 'Ab c', 'b a', 'red', 'Red', 'RED', 'reddish', 'x_y', '5%', 'a-b', '-', '7a', '[a]',
           'a\\b']  # fmt: skip
DATES = [date(2023, 1, 1), date(2024, 3, 5), date(2024, 3, 6), date(2024, 12, 31)]
CELLS: dict[str, st.SearchStrategy[Any]] = {
    's': st.none() | st.sampled_from(STRINGS),
    't': st.none() | st.sampled_from(STRINGS),
    'n': st.none() | st.sampled_from([-7, 0, 7, 17, 70]),
    'd': st.none() | st.sampled_from(DATES),
    'b': st.none() | st.booleans(),
}


@st.composite
def table_rows(draw: st.DrawFn) -> list[dict[str, Any]]:
    count = draw(st.sampled_from([3, 4, 5, 6, 7, 8, 0, 1, 2, 3, 4, 5, 6, 7, 8, 0]))
    keys = draw(st.permutations(range(1, count + 1)))  # inserted in any order; the key decides (PRD-O4)
    return [{'id': key, **{name: draw(cell) for name, cell in CELLS.items()}} for key in keys]


@st.composite
def declarations(draw: st.DrawFn) -> tuple[Declared, ...]:
    """Each column searchable and orderable but one time in twelve, and one of them always unorderable."""
    unorderable = draw(st.sampled_from(NAMES))
    return tuple(
        (name, KINDS[name], not draw(chance(12)), name != unorderable and not draw(chance(12))) for name in NAMES
    )


#: A cell that holds a value, by column
VALUED: dict[str, st.SearchStrategy[Any]] = {
    's': st.sampled_from(STRINGS),
    't': st.sampled_from(STRINGS),
    'n': st.sampled_from([-7, 0, 7, 17, 70]),
    'd': st.sampled_from(DATES),
    'b': st.booleans(),
}
#: The values of a range, low to high, by column
RANGED: dict[str, list[Any]] = {'n': [-7, 0, 7, 17, 70], 'd': DATES}
#: A word that ColumnControl's `contains` finds in some cells, by type
CONTAINED: dict[str, list[str]] = {'string': ['a', 'red', 'b', 'x_y', '-'], 'number': ['7', '0', '1', '-'],
                                   'date': ['2024', '03', '-', '12-31']}  # fmt: skip
BIG = '9223372036854775808'  # 2^63, one past a BigInteger (PRD-F25, step 3)
LAST = '9999-12-31'  # the last day there is (PRD-F24)
#: The values a filter sends, by column: most of them read, some not (`abc`, `2024-13-01`, `yes`, and seldom a
#: number or a day out of range)
VALUES: dict[str, st.SearchStrategy[str]] = {
    's': st.sampled_from([*STRINGS, 'x', 'zz', 'REDDISH', 'A-B']),
    't': st.sampled_from([*STRINGS, 'x', 'zz', 'REDDISH', 'A-B']),
    'id': rarely(st.sampled_from(['1', '2', '3', '5', '8', ' 2 ', '+3', '-1', '4']), st.just('abc'), 6),
    'n': rarely(st.sampled_from(['-7', '0', '7', '17', '70', '8', ' 7 ', '+7', '-7', '0', '7', '17', '70']),
                st.sampled_from(['abc', '1_000', '7 7', BIG]), 10),
    'd': rarely(st.sampled_from(['2023-01-01', '2024-03-05', '2024-03-06', '2024-12-31', ' 2024-03-05 ', '2024-03-05',
                                 '2024-12-31', '2024-03-06', '2023-01-01']),
                st.sampled_from(['2024-13-01', '2024', '2024-02-30', '9999-12-31']), 10),
    'b': rarely(st.sampled_from(['true', 'false', '1', '0', 'TRUE', ' 0 ', 'true', 'false', '1', '0']),
                st.sampled_from(['yes', '   ', 'on']), 8),
}  # fmt: skip
VALUES['extra'] = VALUES['s']
#: A day, a month or a year for `contains` on a date (PRD-F22), and now and then one that cannot be read
SPANS = rarely(st.sampled_from(['2024', '2023', '2024-03', '2024-12', '2024-03-05', ' 2024 ']),
               st.sampled_from(['2024-3', '24', '2024-13', '9999', '9999-12']), 5)  # fmt: skip

WORDS = ['a', 'A', 'b', 'red', 'Red', 'RED', '7', '17', '2024', '2024-03', '03-05', '-', '_', '%', 'x_y', '5%', '[a]',
         '\\', 'ab', 'a-b', 'e', 'zz']  # fmt: skip
TOKENS = [*WORDS, *WORDS, *(f'!{word}' for word in WORDS), '"a b"', '!"a b"', '"b a"', '"', '""', '!', '!x', '5"',
          '!5"', 'x"a', '"a" "b"x']  # fmt: skip
JOINERS = st.sampled_from([' ', ' ', ' ', ' ', '  ', '\n', '\r'])
#: Search text: 1 or 2 tokens, seldom 3
text = st.builds(lambda tokens, joiner: joiner.join(tokens),
                 st.lists(st.sampled_from(TOKENS), min_size=1, max_size=2) | st.lists(st.sampled_from(TOKENS),
                                                                                      min_size=3, max_size=3),
                 JOINERS)  # fmt: skip


def step_one(common: st.SearchStrategy[str], one_in: int, many_words: bool = True) -> st.SearchStrategy[str]:
    """``common``, and about once in ``one_in`` draws each a mistake of step 1 (PRD-E3): a control character, 256 code
    points and, for search text, 17 words. One draw per kind: a single choice among them would favour its first."""
    control = st.sampled_from(['a\x00', 'a\tb', 'b\x7f'])
    common = rarely(rarely(common, control, max(2, one_in * 2 // 3)), st.just('x' * 256), one_in)
    return rarely(common, st.just(' '.join(['a'] * 17)), one_in) if many_words else common


SEARCHED: dict[str, st.SearchStrategy[str]] = {
    'string': text,
    'number': st.sampled_from(['7', '17', '-', '-7', '0', '1', 'abc', '!7', '"7"', '7 0']) | text,
    'date': st.sampled_from(['2024', '2024-03', '03-05', '-', '2023', '12-31', '!2024', '2024 03']) | text,
    'boolean': rarely(st.sampled_from(['true', 'false', '1', '0', 'TRUE', ' 0 ', 'true', 'false', 'yes', '   ']),
                      st.sampled_from(['true false', '!true', '"true"', '!x']), 8),
}  # fmt: skip
CONDITIONS: dict[Kind, list[str]] = {
    'string': ['=', '!=', 'contains', '!contains', 'starts', '!starts', 'ends', '!ends', 'null', '!null'],
    'number': ['=', '!=', '<', '<=', '>', '>=', 'between', '!between', 'null', '!null'],
    'date': ['=', '!=', '<', '<=', '>', '>=', 'between', '!between', 'contains', '!contains', 'null', '!null'],
    'boolean': ['=', '!=', 'null', '!null'],
}
ALL_CONDITIONS = sorted({condition for conditions in CONDITIONS.values() for condition in conditions})
LOGICS: dict[Kind, list[str]] = {
    'string': ['contains', 'notContains', 'equal', 'notEqual', 'starts', 'ends', 'empty', 'notEmpty'],
    'number': ['contains', 'equal', 'notEqual', 'greater', 'greaterOrEqual', 'less', 'lessOrEqual', 'empty',
               'notEmpty'],
    'date': ['contains', 'notContains', 'equal', 'notEqual', 'greater', 'greaterOrEqual', 'less', 'lessOrEqual',
             'empty', 'notEmpty'],
    'boolean': ['contains', 'equal', 'notEqual', 'empty', 'notEmpty'],
}  # fmt: skip
ALL_LOGICS = sorted({logic for logics in LOGICS.values() for logic in logics})
NEGATIVE = {'!=', '!contains', '!starts', '!ends', '!between', 'notEqual', 'notContains'}


def escaped(value: str) -> str:
    """A value escaped by DataTables' `util.escapeRegex` set (PRD-F19)."""
    return ''.join(f'\\{character}' if character in '/.*+?|()[]{}\\$^-' else character for character in value)


#: Column search text that PRD-F6 reduces to no word (`!a` and `!x` are words PRD-F35 drops), now and then with a
#: control character, which PRD-F15 refuses first
NO_WORD = rarely(st.sampled_from(['   ', '"', '""', '"""', '!""', '!', '!x', '!a', ' \n ']),
                 st.sampled_from(['!\x01', '!\t']), 12)  # fmt: skip


def values_of(name: str) -> st.SearchStrategy[list[str]]:
    """The entries of a list: one to three values, now and then an empty one."""
    return st.lists(rarely(VALUES[name], st.just(''), 6), min_size=1, max_size=3)


@st.composite
def column_search(draw: st.DrawFn, name: str, refused: bool, active: bool = False) -> dict[str, Any]:
    """A column's search: mostly idle, on a column that cannot be searched almost always; smart text, a categorical
    regex, a list, or now and then a value beside a list. On any column, now and then text with no word (PRD-F6),
    alone or beside a list."""
    kind = KINDS.get(name, 'string')
    if draw(chance(3 if refused else 8)):
        no_word = {'value': draw(NO_WORD), 'regex': False}
        beside = not refused and draw(st.booleans())  # on a column that cannot be searched the list is refused
        return {**no_word, 'multi_values': draw(values_of(name))} if beside else no_word
    if not active and not draw(chance(32 if refused else 4)):
        return {'value': '', 'regex': False}
    form = draw(st.sampled_from(['text', 'text', 'text', 'regex', 'multi', 'multi']))
    values = draw(values_of(name))
    if form == 'multi':
        both = draw(chance(16))
        return {'value': draw(st.sampled_from(WORDS)) if both else '', 'regex': False, 'multi_values': values}
    if form == 'regex':
        anchored = '^(' + '|'.join(map(escaped, values)) + ')$'
        shapes = st.sampled_from([anchored, '|'.join(map(escaped, values)), f'^{escaped(values[0])}$', '^()$', '|'])
        other = rarely(st.sampled_from(['a.b', '(a)', '^a|b$', '\\d', 'a$']), st.just('a.' + 'x' * 256), 3)
        return {'value': draw(rarely(shapes, other, 6)), 'regex': True}
    return {'value': draw(step_one(SEARCHED[kind], 20, many_words=kind != 'boolean')), 'regex': False}


@st.composite
def columncontrol(draw: st.DrawFn, name: str, refused: bool, active: bool = False) -> dict[str, Any]:
    """ColumnControl's part: mostly none; a search of a logic that fits the column, now and then one that does not, or
    a list. When it must be active, its `contains` half the time, beside the column's own search (PRD-F45)."""
    kind = KINDS.get(name, 'string')
    if not active and not draw(chance(48 if refused else 6)):
        return {}
    contains = active and draw(st.booleans())
    if not contains and draw(chance(3)):
        return {'list': draw(values_of(name))}
    logic = 'contains' if contains else draw(rarely(st.sampled_from(LOGICS[kind]), st.sampled_from(ALL_LOGICS), 16))
    if logic in ('empty', 'notEmpty'):
        value = ''
    elif logic == 'contains':
        value = draw(step_one(SEARCHED[kind], 12, many_words=kind != 'boolean'))
    elif logic == 'notContains' and kind == 'date':
        value = draw(SPANS)
    else:
        value = draw(step_one(VALUES[name], 32, many_words=False))
    return {'search': {'value': value, 'logic': logic, 'type': 'text'}}


@st.composite
def criterion(draw: st.DrawFn) -> dict[str, Any]:
    """A SearchBuilder criterion: mostly a condition that fits its column with the values it takes; now and then one
    that does not fit, a value too many or too few, an empty value, a column not declared, or an unfinished one."""
    name = draw(rarely(st.sampled_from(NAMES), st.just('extra'), 48))
    kind = KINDS.get(name, 'string')
    if draw(chance(24)):  # unfinished: no condition, or no column
        unfinished = draw(st.sampled_from([{'condition': ''}, {'origData': ''}, {}]))
        value = draw(step_one(VALUES[name], 2, many_words=False))  # read by no condition (PRD-F14, PRD-F15)
        return {'origData': name, 'data': name, 'type': 'string', 'value': [value], **unfinished}
    condition = draw(rarely(st.sampled_from(CONDITIONS[kind]), st.sampled_from(ALL_CONDITIONS), 16))
    takes = 2 if condition in ('between', '!between') else 0 if condition in ('null', '!null') else 1
    count = draw(rarely(st.just(takes), st.sampled_from([0, takes + 1]), 24))
    value = SPANS if kind == 'date' and condition in ('contains', '!contains') else VALUES[name]
    values = draw(st.lists(step_one(value, 48, many_words=False), min_size=count, max_size=count))
    return {'origData': name, 'data': name, 'condition': condition, 'type': 'string', 'value': values}


def groups_of(child: st.SearchStrategy[Any]) -> st.SearchStrategy[dict[str, Any]]:
    return st.fixed_dictionaries({'logic': st.sampled_from(['AND', 'OR']), 'criteria': st.lists(child, max_size=3)})


#: The root holds at least one node, as SearchBuilder sends it once a condition is added
tree = st.fixed_dictionaries({
    'logic': st.sampled_from(['AND', 'OR']),
    'criteria': st.lists(st.recursive(criterion(), groups_of, max_leaves=4), min_size=1, max_size=3),
})  # fmt: skip


def nameless(column: dict[str, Any]) -> bool:
    return not column['name'] and (column['data'] in (None, '', 'function') or isinstance(column['data'], dict))


@st.composite
def request_columns(draw: st.DrawFn, server: dict[str, tuple[bool, bool]]) -> list[dict[str, Any]]:
    """The request's columns, with their searches: declared ones, now and then one named twice, a nameless one and an
    undeclared one."""
    names: list[str | None] = list(draw(st.lists(st.sampled_from(NAMES), min_size=1, max_size=5, unique=True)))
    if draw(chance(3)):
        names.append(draw(st.sampled_from(names)))  # a column named twice
    if draw(chance(4)):
        names.append(None)  # a nameless column
    if draw(chance(8)):
        names.append('extra')  # a column the server does not declare
    columns: list[dict[str, Any]] = []
    for name in draw(st.permutations(names)):
        searchable, orderable = server.get(name or '', (False, False))
        refused = name not in server or not searchable
        # The request mostly agrees with the server on what can be sorted, as a configured table does
        request_orderable = orderable != draw(chance(6)) if name in server else draw(chance(8))
        column = {
            'data': draw(st.sampled_from([None, 'function', None, {'_': 's'}])) if name is None else name,
            'name': '',
            'searchable': not draw(chance(8)),
            'orderable': request_orderable,
            'search': draw(column_search(name or 'extra', refused)),
            'columnControl': draw(columncontrol(name or 'extra', refused)),
        }
        columns.append(column)
    searched = [index for index, column in enumerate(columns)
                if isinstance(column['data'], str) and server.get(column['data'], (False, False))[0]]  # fmt: skip
    if searched and draw(chance(4)):  # two filters on one column: its search and ColumnControl's
        index = draw(st.sampled_from(searched))
        columns[index]['search'] = draw(column_search(columns[index]['data'], refused=False, active=True))
        columns[index]['columnControl'] = draw(columncontrol(columns[index]['data'], refused=False, active=True))
    return columns


@st.composite
def added_criteria(draw: st.DrawFn) -> list[dict[str, Any]]:
    """Criteria added to the tree now and then: a value the column cannot read, so that steps 2 and 3 of PRD-E3 meet
    the others, and a range written high to low, which PRD-F27 reads in either order."""
    added: list[dict[str, Any]] = []
    for name, unreadable, one_in in (('d', ['2024-13-01', '2024', '9999-12-31', '05.03.2024'], 12),
                                     ('n', ['abc', '7.5.1', BIG], 18)):  # fmt: skip
        if draw(chance(one_in)):
            condition = draw(st.sampled_from(['=', '<', '!=', '>=']))
            added.append({'origData': name, 'data': name, 'condition': condition,
                          'value': [draw(st.sampled_from(unreadable))]})  # fmt: skip
    for condition in ('between', '!between'):
        if draw(chance(4)):
            name = draw(st.sampled_from(['n', 'd']))
            pool = ['-7', '0', '7', '17', '70'] if name == 'n' else [day.isoformat() for day in DATES]
            low, high = sorted(draw(st.lists(st.sampled_from(range(len(pool))), min_size=2, max_size=2, unique=True)))
            added.append({'origData': name, 'data': name, 'condition': condition, 'value': [pool[high], pool[low]]})
    return added


@st.composite
def cases(draw: st.DrawFn) -> tuple[list[dict[str, Any]], tuple[Declared, ...], dict[str, Any]]:
    """Rows, a declaration and a request body."""
    rows = draw(table_rows())
    decl = draw(declarations())
    server = {name: (searchable, orderable) for name, _, searchable, orderable in decl}
    columns = draw(request_columns(server))

    def skipped(column: dict[str, Any]) -> bool:
        key = column['data'] if isinstance(column['data'], str) else None
        return nameless(column) or (not column['orderable'] and not server.get(key or '', (False, False))[1])

    entry = st.fixed_dictionaries({'column': st.integers(0, len(columns) - 1), 'dir': st.sampled_from(['asc', 'desc'])})
    order = draw(st.lists(entry, max_size=3))
    skips = [index for index, column in enumerate(columns) if skipped(column)]
    paging: dict[str, int] = {}
    unfiltered = False
    applies = [index for index, column in enumerate(columns)
               if isinstance(column['data'], str) and server.get(column['data'], (False, False))[1]]  # fmt: skip
    if skips and applies and not draw(chance(4)):  # a skipped entry first, then one applied, in the other direction
        direction = draw(st.sampled_from(['asc', 'desc']))
        other = 'desc' if direction == 'asc' else 'asc'
        # the key cannot tie, so another column is applied where there is one
        applied = draw(st.sampled_from([index for index in applies if columns[index]['data'] != 'id'] or applies))
        order = [{'column': draw(st.sampled_from(skips)), 'dir': direction},
                 {'column': applied, 'dir': other}, *order[:1]]  # fmt: skip
        tied = columns[applied]['data']
        if tied != 'id' and len(rows) >= 2:
            # A tie on the applied column that a short page cuts: only the key, in the applied entry's direction,
            # orders the tied rows (PRD-O3, PRD-O8)
            value = draw(CELLS[tied])
            for row in rows:
                row[tied] = value
            order = order[:2]
            paging = {'start': draw(st.integers(0, len(rows) - 2)), 'length': draw(st.integers(1, 2))}
            unfiltered = draw(st.booleans())  # half the time no filter thins the tie out
    body: dict[str, Any] = {
        'draw': 1,
        'start': draw(st.sampled_from([0, 0, 0, 1, 2, 3, 5, 8, 9, 20])),
        'length': draw(st.integers(1, 10)),
        'columns': columns,
        'order': order,
        'search': {'value': draw(step_one(rarely(st.just(''), text, 3), 20)), 'regex': draw(chance(24))},
    }
    body.update(paging)
    if draw(chance(32)):
        # PRD-F40: its entries are not read, so PRD-F14 and PRD-F15 do not apply to them
        body['search']['multi_values'] = draw(st.lists(step_one(VALUES['s'], 3, many_words=False), max_size=2))
    if draw(chance(3)):
        body['searchBuilder'] = draw(tree)
    if added := draw(added_criteria()):
        body.setdefault('searchBuilder', {'logic': 'AND', 'criteria': []})['criteria'].extend(added)
    focused(draw, rows, server=server, body=body, applies=applies, unfiltered=unfiltered)
    outranked(draw, rows, server=server, body=body)
    return rows, decl, body


def outranked(
    draw: st.DrawFn, rows: list[dict[str, Any]], *, server: dict[str, tuple[bool, bool]], body: dict[str, Any]
) -> None:
    """Now and then a request that tells more mutants apart, each only adding to the request's filters (a criterion
    joins the tree under an AND), so a range `focused` wrote stays. M5b, about once in four: a one-word global search
    taken from one cell, over at least two searchable columns: a word may be found in any of them (PRD-F1). M10, about
    once in sixteen: the last day beside a mistake of step 1 or 2, in either order (PRD-E3: the earlier step answers);
    M10b, about once in twelve: the last day beside a global search with no searchable column, a mistake of step 4
    (PRD-E3: step 3 answers). About once in twenty: a list of more than 256, or more than 512, entries on the global
    search beside a number mistake of step 2, which answers: PRD-F40 reads no entry of that list, and PRD-F38's bounds
    on lists do not count them."""
    columns = body['columns']
    searched = sorted({column['data'] for column in columns if isinstance(column['data'], str) and column['searchable']
                       and server.get(column['data'], (False, False))[0] and KINDS[column['data']] != 'boolean'})  # fmt: skip
    if len(searched) >= 2 and rows and draw(chance(4)):
        name, row = draw(st.sampled_from(searched)), draw(st.sampled_from(rows))
        cell = '' if row[name] is None else str(row[name])  # a date's str is its ISO day
        begin = draw(st.integers(0, max(0, len(cell) - 1)))
        word = cell[begin : begin + draw(st.integers(1, 3))]
        if word.strip() == word and word and ' ' not in word:
            body.update(search={'value': word, 'regex': False}, start=0)
            if draw(st.booleans()):  # half the time no column filter hides the row
                for column in columns:
                    column['search'], column['columnControl'] = {'value': '', 'regex': False}, {}
    last_day = {'origData': 'd', 'data': 'd', 'condition': draw(st.sampled_from(['=', '<', '>='])), 'value': [LAST]}
    if draw(chance(16)):
        other = {'origData': 'n', 'data': 'n', 'condition': '=',
                 'value': [draw(st.sampled_from(['abc', '1_000', 'a\x00', 'x' * 256]))]}  # fmt: skip
        joined(body, *([last_day, other] if draw(st.booleans()) else [other, last_day]))
    elif draw(chance(12)):
        joined(body, last_day)
        body['search'] = {'value': draw(st.sampled_from(['a', 'red', '7'])), 'regex': False}
        for column in columns:
            column['searchable'] = False
    if draw(chance(20)):
        entries = draw(st.sampled_from([257, 300, 513, 600]))
        body['search'] = {**body['search'], 'multi_values': [f'v{index}' for index in range(entries)]}
        joined(body, {'origData': 'n', 'data': 'n', 'condition': '=', 'value': ['abc']})


def joined(body: dict[str, Any], *criteria: dict[str, Any]) -> None:
    """``criteria`` added to the request's tree under an AND, the tree it held kept whole: beside the root's own
    criteria when the root is an AND group, else beside the root, one level deeper (far within PRD-F38's 8)."""
    existing = body.get('searchBuilder')
    if existing and existing['logic'] == 'AND':
        existing['criteria'] = [*existing['criteria'], *criteria]
    else:
        body['searchBuilder'] = {'logic': 'AND', 'criteria': [*([existing] if existing else []), *criteria]}


def focused(
    draw: st.DrawFn,
    rows: list[dict[str, Any]],
    *,
    server: dict[str, tuple[bool, bool]],
    body: dict[str, Any],
    applies: list[int],
    unfiltered: bool,
) -> None:
    """Now and then one of three requests that tell a mutant of the library apart (M4 and M6 about once in five, M7
    once in eight), half of them with no other filter (and with ``unfiltered`` none either): M4, an ascending order
    over NULLs and values with a short page (PRD-O2); M7, a range written high to low over a row inside it (PRD-F27);
    M6, a list and ColumnControl's `contains` on one column, ANDed (PRD-F45)."""
    columns = body['columns']
    nulls_first = draw(chance(5)) and [index for index in applies if columns[index]['data'] != 'id']
    anded = draw(chance(5)) and [
        index
        for index, column in enumerate(columns)
        if isinstance(column['data'], str)
        and server.get(column['data'], (False, False))[0]
        and KINDS[column['data']] != 'boolean'
    ]
    reversed_range = draw(chance(8)) and bool(rows)
    if unfiltered or (bool(nulls_first or anded or reversed_range) and draw(st.booleans())):
        body['search'] = {'value': '', 'regex': False}
        body.pop('searchBuilder', None)
        for column in columns:
            column['search'], column['columnControl'] = {'value': '', 'regex': False}, {}
    if nulls_first:
        index = draw(st.sampled_from(nulls_first))
        name = columns[index]['data']
        for row in rows:
            row[name] = None if draw(st.booleans()) else draw(VALUED[name])
        body.update(
            order=[{'column': index, 'dir': 'asc'}], start=draw(st.integers(0, 2)), length=draw(st.integers(1, 3))
        )
    if reversed_range:
        name = draw(st.sampled_from(['n', 'd']))
        pool = RANGED[name]
        low, high = sorted(draw(st.lists(st.sampled_from(range(len(pool))), min_size=2, max_size=2, unique=True)))
        draw(st.sampled_from(rows))[name] = pool[draw(st.integers(low, high))]
        bounds = [str(pool[high]), str(pool[low])]  # a date's str is its ISO day
        condition = draw(st.sampled_from(['between', '!between']))
        joined(body, {'origData': name, 'data': name, 'condition': condition, 'value': bounds})
    if anded:
        index = draw(st.sampled_from(anded))
        name = columns[index]['data']
        columns[index]['search'] = {'value': '', 'regex': False, 'multi_values': draw(values_of(name))}
        # now and then more than 16 words, which PRD-F14 refuses in this column search too (PRD-F45)
        word = draw(rarely(st.sampled_from(CONTAINED[KINDS[name]]), st.just(' '.join(['a'] * 17)), 8))
        columns[index]['columnControl'] = {'search': {'value': word, 'logic': 'contains', 'type': 'text'}}


def library(decl: tuple[Declared, ...], rows: list[dict[str, Any]], params: DTParams) -> Answer | Refusal:
    """`fetch_sync`'s answer over the rows, as the evaluator gives one."""
    table = SQLTable(
        reference_rows,
        [
            Column(name, type=kind, searchable=searchable, orderable=orderable)
            for name, kind, searchable, orderable in decl
        ],
    )
    with _engine.begin() as connection:
        connection.execute(reference_rows.delete())
        if rows:
            connection.execute(reference_rows.insert(), rows)
    with Session(_engine) as session:
        response = table.fetch_sync(params, session)
    if response.error_code:
        return Refusal(response.error_code)
    return Answer(tuple(row['id'] for row in response.data), response.recordsTotal, response.recordsFiltered)


def outcome(rows: list[dict[str, Any]], answer: Answer | Refusal) -> str:
    if isinstance(answer, Refusal):
        return f'error {answer.code}'
    if answer.ids:
        return 'answered with rows'
    if not rows:
        return 'empty: no rows'
    if not answer.records_filtered:
        return 'empty: the filters match no row'
    return 'empty: start past the end, rows matched'


def criteria_of(node: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for child in node.get('criteria', []):
        found.extend(criteria_of(child) if 'criteria' in child or 'logic' in child else [child])
    return found


def reversed_bounds(leaf: dict[str, Any]) -> bool:
    """Whether a criterion's two values are a range written high to low (PRD-F27): numbers as numbers, days as days."""
    if len(leaf['value']) != 2 or KINDS.get(leaf['origData']) not in ('number', 'date'):
        return False
    low, high = (value.strip() for value in leaf['value'])
    try:
        return (
            int(low) > int(high)
            if KINDS[leaf['origData']] == 'number'
            else date.fromisoformat(low) > date.fromisoformat(high)
        )
    except ValueError:
        return False


def paths(rows: list[dict[str, Any]], decl: tuple[Declared, ...], body: dict[str, Any]) -> list[str]:
    """The paths of an answered request that the generator is tuned to reach."""
    filters: Counter[str] = Counter()
    negated: set[str] = set()
    for column in body['columns']:
        name = column['data'] if isinstance(column['data'], str) else ''
        search, control = column['search'], column['columnControl']
        if search['value'].strip() or search.get('multi_values'):
            filters[name] += 1
            if not search['regex'] and any(word.startswith('!') and len(word) > 2 for word in search['value'].split()):
                negated.add(name)
        if control.get('list'):
            filters[name] += 1
        if 'search' in control and (control['search']['value'] or control['search']['logic'] in ('empty', 'notEmpty')):
            filters[name] += 1
            if control['search']['logic'] in NEGATIVE:
                negated.add(name)
    leaves = [leaf for leaf in criteria_of(body.get('searchBuilder', {})) if leaf.get('condition') and leaf['origData']]
    for leaf in leaves:
        filters[leaf['origData']] += 1
        if leaf['condition'] in NEGATIVE:
            negated.add(leaf['origData'])
    if any(word.startswith('!') and len(word) > 2 for word in body['search']['value'].split()):
        negated.update(name for name in KINDS if name != 'b')
    reached = []
    if leaves:
        reached.append('path: a SearchBuilder tree')
    reached.extend(
        f'path: a reversed {leaf["condition"]} over rows'
        for leaf in leaves
        if rows and leaf['condition'] in ('between', '!between') and reversed_bounds(leaf)
    )
    if any(count >= 2 for name, count in filters.items() if name in KINDS):
        reached.append('path: two filters on one column')
    if any(row[name] is None or row[name] == '' for row in rows for name in negated if name in KINDS):
        reached.append("path: a negation beside NULL or ''")
    ordered = [body['columns'][entry['column']]['data'] for entry in body['order']]
    if any(name in filters for name in ordered if isinstance(name, str)):
        reached.append('path: an order on a filtered column')
    server = {name: orderable for name, _, _, orderable in decl}
    if len(body['order']) >= 2:
        first, second = body['order'][:2]
        column = body['columns'][first['column']]
        key = column['data'] if isinstance(column['data'], str) else None
        skipped = nameless(column) or (not column['orderable'] and not server.get(key or '', False))
        then = body['columns'][second['column']]
        applied = isinstance(then['data'], str) and server.get(then['data'], False)
        if skipped and applied and second['dir'] != first['dir']:
            reached.append('path: a skipped first order entry, then one applied in the other direction')
            if tie_cut(rows, decl, body, then['data']):
                reached.append('path: ... and a tie on the applied column that the page cuts')
    return list(dict.fromkeys([*reached, *no_word_paths(decl, body), *survivor_paths(rows, decl, body)]))


def survivor_paths(rows: list[dict[str, Any]], decl: tuple[Declared, ...], body: dict[str, Any]) -> list[str]:
    """The paths that tell three mutants of the library apart, each read from the evaluator's answers to the request
    and to the request with one part changed. M4: under an ascending first order, putting the NULLs last instead of
    first would change the page (PRD-O2). M6: dropping either a column search or list, or ColumnControl's `contains`
    on the same column, would change the answer (PRD-F45: they are ANDed). M7: a range written high to low keeps or
    drops a row that the unswapped reading would not (PRD-F27)."""
    reached: list[str] = []
    answer = evaluate(decl, rows, parse_request(body))
    first = first_applied(decl, body)
    if first is not None and first[1] == 'asc':
        whole = evaluate(decl, rows, parse_request({**body, 'start': 0, 'length': max(1, len(rows))}))
        if isinstance(whole, Answer):
            empty = {row['id'] for row in rows if row[first[0]] is None}
            last = [key for key in whole.ids if key not in empty] + [key for key in whole.ids if key in empty]
            window = slice(body['start'], body['start'] + body['length'])
            if whole.ids[window] != tuple(last[window]):
                reached.append('path M4: under an ascending order, NULLs last would change the page')
    for index, column in enumerate(body['columns']):
        search, control = column['search'], column['columnControl'].get('search', {})
        plain = search.get('multi_values') or (search['value'] and (search['regex'] or any(words(search['value']))))
        if plain and control.get('logic') == 'contains' and control.get('value'):
            without_plain, without_control = deepcopy(body), deepcopy(body)
            without_plain['columns'][index]['search'] = {'value': '', 'regex': False}
            del without_control['columns'][index]['columnControl']['search']
            if any(evaluate(decl, rows, parse_request(other)) != answer for other in (without_plain, without_control)):
                reached.append(
                    'path M6: dropping a column search or list, or a ColumnControl contains beside it, '
                    'would change the answer'
                )
    for position, leaf in enumerate(criteria_of(body.get('searchBuilder', {}))):
        if leaf.get('condition') in ('between', '!between') and reversed_bounds(leaf):
            unswapped = deepcopy(body)
            target = criteria_of(unswapped['searchBuilder'])[position]
            target.clear()  # the unswapped reading: `between` high to low holds nothing, `!between` everything
            target.update(
                {
                    'logic': 'AND' if leaf['condition'] == 'between' else 'OR',
                    'criteria': [
                        {'origData': leaf['origData'], 'condition': condition} for condition in ('null', '!null')
                    ],
                }
            )
            if evaluate(decl, rows, parse_request(unswapped)) != answer:
                reached.append(f'path M7: a reversed {leaf["condition"]} changes the answer')
    if isinstance(answer, Answer) and found_in_one_column(rows, decl, body, answer):
        reached.append('path M5b: a global word found in one searchable column of a row and not the others')
    return reached


def found_in_one_column(
    rows: list[dict[str, Any]], decl: tuple[Declared, ...], body: dict[str, Any], answer: Answer
) -> bool:
    """Whether a one-word global search finds a row of the page through one of two or more searched columns only: a
    word must be found in some column, not in each (PRD-F1). Read from the evaluator: the request is run once per
    searched column, with every other column marked unsearchable, and a page row that exactly one run finds counts."""
    positive, negated = words(body['search']['value'])
    searchable = {name for name, kind, flag, _ in decl if flag and kind != 'boolean'}
    searched = {column['data'] for column in body['columns']
                if isinstance(column['data'], str) and column['searchable'] and column['data'] in searchable}  # fmt: skip
    if len(positive) != 1 or negated or len(searched) < 2:
        return False
    found: Counter[Any] = Counter()
    for name in searched:
        alone = {**body, 'start': 0, 'length': max(1, len(rows)), 'columns': [
            {**column, 'searchable': column['searchable'] and column['data'] == name} for column in body['columns']]}  # fmt: skip
        run = evaluate(decl, rows, parse_request(alone))
        if isinstance(run, Answer):
            found.update(run.ids)
    return any(found[key] == 1 for key in answer.ids)


def ranking_paths(
    rows: list[dict[str, Any]], decl: tuple[Declared, ...], body: dict[str, Any], answer: Answer | Refusal
) -> list[str]:
    """M10: the last day there is (step 3 of PRD-E3) beside a mistake of step 1 or 2, which answers; M10b: beside a
    refusal of step 4, where the last day answers. Each read from the evaluator: the tree's criteria on `d` holding
    the last day, alone, answer `invalid_date`."""
    tree = body.get('searchBuilder') or {}
    last = [leaf for leaf in criteria_of(tree) if leaf.get('origData') == 'd' and LAST in leaf.get('value', [])]
    if not last or not isinstance(answer, Refusal):
        return []
    alone = evaluate(decl, rows, parse_request({**body, 'search': {'value': ''}, 'columns': [
        {**column, 'search': {'value': '', 'regex': False}, 'columnControl': {}} for column in body['columns']],
        'order': [], 'searchBuilder': {'logic': 'AND', 'criteria': last}}))  # fmt: skip
    if alone != Refusal('invalid_date'):
        return []
    if answer.code in ('invalid_character', 'value_too_long', 'too_many_words', 'invalid_number'):
        return ['path M10: a mistake of step 1 or 2 answers before the last day']
    without = evaluate(decl, rows, parse_request({key: value for key, value in body.items() if key != 'searchBuilder'}))
    if answer.code == 'invalid_date' and without == Refusal('invalid_filter'):
        return ['path M10b: the last day answers before a refusal of step 4']
    return []


def events(
    rows: list[dict[str, Any]], decl: tuple[Declared, ...], body: dict[str, Any], answer: Answer | Refusal
) -> list[str]:
    """Every event of one example: its outcome, the shapes it sends, and the paths it reaches."""
    reached = [outcome(rows, answer), *shapes(body), *ranking_paths(rows, decl, body, answer)]
    return reached + (paths(rows, decl, body) if isinstance(answer, Answer) else [])


def first_applied(decl: tuple[Declared, ...], body: dict[str, Any]) -> tuple[str, str] | None:
    """The column and direction of the first order entry applied in an answered request (PRD-O1, PRD-O8)."""
    orderable = {name: flag for name, _, _, flag in decl}
    for entry in body['order']:
        column = body['columns'][entry['column']]
        if not nameless(column) and isinstance(column['data'], str) and orderable.get(column['data'], False):
            return column['data'], entry['dir']
    return None


def shapes(body: dict[str, Any]) -> list[str]:
    """Values of step 1 that no condition reads, and ColumnControl text over the word cap, as sent (#205)."""

    def bad(value: str) -> bool:
        control = (character not in '\n\r' and unicodedata.category(character) == 'Cc' for character in value)
        return len(value) > 255 or any(control)

    reached: list[str] = []
    if len(body['search'].get('multi_values') or []) > 256:
        reached.append('shape: a list of more than 256 entries on the global search')
    if any(bad(value) for value in body['search'].get('multi_values') or []):
        reached.append('shape: a list on the global search holding a step-1 value')
    if any(not (leaf.get('condition') and leaf.get('origData')) and any(map(bad, leaf['value']))
           for leaf in criteria_of(body.get('searchBuilder', {}))):  # fmt: skip
        reached.append('shape: an unfinished criterion holding a step-1 value')
    for column in body['columns']:
        if column['search']['regex'] and len(column['search']['value']) > 255:
            reached.append('shape: a regex over 255 code points that is no categorical filter')
        control = column['columnControl'].get('search', {})
        if control.get('logic') == 'contains' and len(control['value'].split()) > 16:
            reached.append('shape: a ColumnControl contains of more than 16 words')
    return list(dict.fromkeys(reached))


def no_word_paths(decl: tuple[Declared, ...], body: dict[str, Any]) -> list[str]:
    """The paths of column search text that PRD-F6 reduces to no word."""
    reached: list[str] = []
    searchable = {name: flag for name, _, flag, _ in decl}
    for column in body['columns']:
        search = column['search']
        if not search['regex'] and search['value'] and not any(words(search['value'])):
            reached.append('path: a column search with no word')
            if search.get('multi_values'):
                reached.append('path: a column search with no word beside a list')
            key = column['data'] if isinstance(column['data'], str) else None
            if nameless(column) or not searchable.get(key or '', False):
                reached.append('path: a column search with no word on a column that cannot be searched')
    return reached


def tie_cut(rows: list[dict[str, Any]], decl: tuple[Declared, ...], body: dict[str, Any], name: str) -> bool:
    """Whether rows that tie on ``name`` stand both inside and outside the page: only the tie-breaker's direction
    decides which of them the page shows (PRD-O3)."""
    whole = evaluate(decl, rows, parse_request({**body, 'start': 0, 'length': max(1, len(rows))}))
    if not isinstance(whole, Answer):
        return False
    values = {row['id']: row[name] for row in rows}
    window = range(body['start'], body['start'] + body['length'])
    sequence = [values[key] for key in whole.ids]
    return any(
        sequence.count(value) >= 2
        and {index in window for index, other in enumerate(sequence) if other == value} == {True, False}
        for value in set(sequence)
    )


DECLARED: tuple[Declared, ...] = tuple((name, KINDS[name], True, True) for name in NAMES)


def three_rows(**third: Any) -> list[dict[str, Any]]:
    """Rows 1 to 3, every cell NULL but those given for row 3."""
    return [{**dict.fromkeys(NAMES), 'id': key, **(third if key == 3 else {})} for key in (1, 2, 3)]


def request_of(*columns: dict[str, Any], **body: Any) -> dict[str, Any]:
    """A request body over the named columns, each searchable and orderable with an idle search, as the generator
    shapes them."""
    shaped = [
        {
            'data': name,
            'name': '',
            'searchable': True,
            'orderable': True,
            'search': {'value': '', 'regex': False},
            'columnControl': {},
            **fields,
        }
        for name, fields in ((column.pop('data'), column) for column in columns)
    ]
    return {'draw': 1, 'start': 0, 'length': 10, 'search': {'value': ''}, 'order': [], 'columns': shaped, **body}


def tree_of(*criteria: tuple[str, str, list[str]]) -> dict[str, Any]:
    return {'logic': 'AND', 'criteria': [{'origData': name, 'condition': condition, 'value': values}
                                         for name, condition, values in criteria]}  # fmt: skip


@given(cases())
# M4: d ascending, length 1: the NULL rows come before row 3's day (PRD-O2), so row 1 is the page
@example(
    (three_rows(d=date(2023, 1, 1)), DECLARED, request_of({'data': 'd'}, length=1, order=[{'column': 0, 'dir': 'asc'}]))
)
# M6: a list `[""]` on n (rows 1 and 2, NULL) and ColumnControl `contains` 7 on n (row 3) are ANDed (PRD-F45): no
# row; either alone, or the two ORed, would find one
@example(
    (
        three_rows(n=7),
        DECLARED,
        request_of(
            {
                'data': 'n',
                'search': {'value': '', 'regex': False, 'multi_values': ['']},
                'columnControl': {'search': {'value': '7', 'logic': 'contains', 'type': 'text'}},
            }
        ),
    )
)
# M7: `!between` 0 and -7 is the range -7 to 0 (PRD-F27): row 3's -7 is in it, the NULL rows pass (PRD-F37)
@example((three_rows(n=-7), DECLARED, request_of({'data': 'n'}, searchBuilder=tree_of(('n', '!between', ['0', '-7'])))))
# PRD-E3, step 3: the last day comes before a number past a BigInteger, in either order: `invalid_date`
@example(
    (
        three_rows(),
        DECLARED,
        request_of({'data': 'n'}, {'data': 'd'}, searchBuilder=tree_of(('n', '=', [BIG]), ('d', '<', ['9999-12-31']))),
    )
)
@example(
    (
        three_rows(),
        DECLARED,
        request_of({'data': 'n'}, {'data': 'd'}, searchBuilder=tree_of(('d', '<', ['9999-12-31']), ('n', '=', [BIG]))),
    )
)
# M10: a mistake of step 2 comes before the last day (step 3), in either order: `invalid_number` (PRD-E3)
@example(
    (
        three_rows(),
        DECLARED,
        request_of({'data': 'n'}, {'data': 'd'}, searchBuilder=tree_of(('d', '=', [LAST]), ('n', '=', ['abc']))),
    )
)
@example(
    (
        three_rows(),
        DECLARED,
        request_of({'data': 'n'}, {'data': 'd'}, searchBuilder=tree_of(('n', '=', ['abc']), ('d', '=', [LAST]))),
    )
)
# M10b: the last day (step 3) comes before a global search with no searchable column (step 4): `invalid_date`
@example(
    (
        three_rows(),
        DECLARED,
        request_of(
            {'data': 'd', 'searchable': False}, search={'value': 'a'}, searchBuilder=tree_of(('d', '=', [LAST]))
        ),
    )
)
# M5b: `ab` is in row 3's `s` and in no `t`: a word may be found in any searched column (PRD-F1), so row 3
@example((three_rows(s='ab', t='x'), DECLARED, request_of({'data': 's'}, {'data': 't'}, search={'value': 'ab'})))
# A regex over 255 code points that is no categorical filter is refused, not measured (PRD-F14, PRD-F18)
@example((three_rows(), DECLARED, request_of({'data': 's', 'search': {'value': 'a.' + 'x' * 256, 'regex': True}})))
# A list on the global search is refused unread (PRD-F40): a control character in it is no `invalid_character`
@example((three_rows(), DECLARED, request_of({'data': 's'}, search={'value': '', 'multi_values': ['a\x00']})))
# 600 entries on the global list: PRD-F40 reads none and PRD-F38's bounds do not count them, so the list is a step-4
# refusal and the step-2 number mistake answers: `invalid_number` (PRD-E3)
@example(
    (
        three_rows(),
        DECLARED,
        request_of(
            {'data': 'n'},
            search={'value': '', 'multi_values': [f'v{i}' for i in range(600)]},
            searchBuilder=tree_of(('n', '=', ['abc'])),
        ),
    )
)
# ColumnControl's `contains` is a column search (PRD-F45): 17 words are `too_many_words` (PRD-F14)
@example(
    (
        three_rows(),
        DECLARED,
        request_of(
            {
                'data': 's',
                'columnControl': {'search': {'value': ' '.join(['a'] * 17), 'logic': 'contains', 'type': 'text'}},
            }
        ),
    )
)
@example((
    [{'id': key, 's': None, 't': None, 'n': None, 'd': None, 'b': None} for key in (1, 2, 3)],
    (('id', 'number', True, False), ('s', 'string', True, True), ('t', 'string', True, True),
     ('n', 'number', True, True), ('d', 'date', True, True), ('b', 'boolean', True, True)),
    {'draw': 1, 'start': 0, 'length': 1, 'search': {'value': ''},
     'columns': [{'data': name, 'name': '', 'searchable': True, 'orderable': name == 's', 'columnControl': {},
                  'search': {'value': '', 'regex': False}} for name in ('id', 's')],
     'order': [{'column': 0, 'dir': 'asc'}, {'column': 1, 'dir': 'desc'}]},
))  # fmt: skip
def test_the_library_answers_as_the_reference_evaluator(case: tuple[list[dict[str, Any]], Any, dict[str, Any]]):
    """The rows, their order and both counts, or the error code, of `fetch_sync` on SQLite equal the evaluator's.
    Catches any rule of PRD section 4.3 to 4.6 in scope where the library and the requirements part, in a combination
    no hand-written test lists. The last example: the entry on `id` is skipped (unorderable on both sides), so `s` desc is
    the first applied entry, and its tie (every `s` NULL) is broken by the key descending: row 3 comes first (PRD-O3,
    PRD-O8)."""
    rows, decl, body = case
    params = parse_request(body)
    expected = evaluate(decl, rows, params)
    for name in events(rows, decl, body, expected):
        event(name)
    assert library(decl, rows, params) == expected, 'the library and the reference evaluator answer differently'
