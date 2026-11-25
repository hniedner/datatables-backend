"""The library against a reference evaluator (issue #205): generated rows and requests, answered by `fetch_sync` on an
in-memory SQLite database and by `tests/reference_evaluator.py`, which is written from `docs/PRD.md` and `spec/` and
never from `src`. Both give the same rows in the same order with the same counts, or the same error code. A
difference is a finding (a gap in the specification, a defect in the library or an engine fact), reported and never
silenced by narrowing the generator.

The table: `id` (Integer, the key), `s` and `t` (String), `n` (BigInteger), `d` (Date) and `b` (Boolean), 0 to 8 rows
inserted in a random key order. The request: the global search, column searches (smart, categorical, `multi_values`),
ColumnControl's search and list, a SearchBuilder tree of at most 5 criteria, 0 to 3 order entries (often a skipped one
first, then one applied in the other direction, now and then over rows that tie on the applied column with a short
page that cuts the tie, half of those with no filter to thin the tie out), paging past the end included; on any column, now and then column search text that PRD-F6
reduces to no word, alone or beside a list; columns named twice, nameless columns
and an undeclared one; a server declaration with at least one unorderable column, and mistakes of every step of
PRD-E3. The rates are set so that about 60% of the requests are answered and each outcome and path below is reached
about 30 times in 500 examples, averaged over seeds; one seed, such as the `ci` profile's, varies around that
(`hypothesis --hypothesis-show-statistics` prints its events). A choice is drawn by `chance` and `rarely` of
`tests/conftest.py`: Hypothesis draws the first of a list of choices far more often than the others.

Covered (PRD): the smart search's grammar (F3, F4, F6, F7 for LF and CR, F8, F35); the global and column searches (F1,
F2, F11, F12, F13, F41, F44, and F23: on SQL a number or date column is searched as its text); ASCII case folding
(F9); categorical filters and lists (F16 to F20, F33, F39, F40); SearchBuilder (F21 for days, F22, F24, F25, F26,
F27, F28, F30, F36, F37, F43); ColumnControl (F45); ordering (O1 to O4, O8, and O6 as SQLite's `BINARY` collation,
which is SQLite's own and not the other databases'); counts and paging (C1 to C3); the error codes and which one a
request with several mistakes gets (E2, E3, F14, F15).

Left out: case folding beyond ASCII and every character outside ASCII (curly quotes, U+2028 and U+2029 included);
datetimes and time zones, and a time of day after a day; numbers with a fraction or an exponent; hooked columns,
`fold_case=False` and `field=`; a base filter and a scope; the size bounds (PRD-F38's, and `request_too_large`); what is
OpenSearch's only (`page_out_of_range`, PRD-C10 to PRD-C13, PRD-O5, PRD-O7); the total cache; the `error` texts and
the log records; the types a database adds of its own.

Generator restrictions, each with its reason:
- A number beyond 32 bits is sent only to `n`, the BigInteger: `id` is an Integer, whose width the evaluator does not
  model. The evaluator reads every number column with a BigInteger's range, the mistake of the column's own type that
  PRD-E3 answers in step 3 after a day past the last one.
- No value with a fraction or an exponent, no curly quote and no character outside ASCII: out of scope above.
- A request column's `name` is always empty, so its name is its `data` (PRD-P15), and an order entry sends no `name`:
  those readings are `parse_request`'s, which the parse properties and the hand-written tests cover.
- ColumnControl's search is always sent with `type` `text`: the date control is refused while parsing (PRD-F45), which
  `tests/test_parse_properties.py` covers.
- A step-1 value (a control character, 256 code points, 17 words) goes only into a filter that reads it: no unfinished
  criterion carries one (the evaluator would raise gap G12), no categorical regex is longer than 255 code points (gap
  G13) and no list on the global search holds one. A ColumnControl search never holds 17 words (gap G13).
- No SearchBuilder tree near PRD-F38's bounds, no list near 256 values and no request near PRD-C17's 960 conditions:
  out of scope above.

The spec gaps the evaluator raises (`SpecGap`) are counted with `hypothesis.event` and not compared. Every gap the
generator met is decided now. Two are not reached, since the generator never sends them (#205): G12, a control
character or a long value in an unfinished criterion; G13, a regex over 255 code points that is no categorical
filter, or a ColumnControl search of more than 16 words.
"""

from collections import Counter
from datetime import date
from typing import Any

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
from .reference_evaluator import SpecGap
from .reference_evaluator import _words  # pyright: ignore[reportPrivateUsage]
from .reference_evaluator import evaluate

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


BIG = '9223372036854775808'  # 2^63, one past a BigInteger (PRD-F25, step 3)
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


def step_one[T](common: st.SearchStrategy[str], one_in: int, words: bool = True) -> st.SearchStrategy[str]:
    """``common``, and about once in ``one_in`` draws each a mistake of step 1 (PRD-E3): a control character, 256 code
    points and, for search text, 17 words. One draw per kind: a single choice among them would favour its first."""
    control = st.sampled_from(['a\x00', 'a\tb', 'b\x7f'])
    common = rarely(rarely(common, control, one_in * 2 // 3), st.just('x' * 256), one_in)
    return rarely(common, st.just(' '.join(['a'] * 17)), one_in) if words else common


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
        return {'value': draw(rarely(shapes, st.sampled_from(['a.b', '(a)', '^a|b$', '\\d', 'a$']), 12)), 'regex': True}
    return {'value': draw(step_one(SEARCHED[kind], 20, words=kind != 'boolean')), 'regex': False}


@st.composite
def columncontrol(draw: st.DrawFn, name: str, refused: bool, active: bool = False) -> dict[str, Any]:
    """ColumnControl's part: mostly none; a search of a logic that fits the column, now and then one that does not, or
    a list."""
    kind = KINDS.get(name, 'string')
    if not active and not draw(chance(48 if refused else 6)):
        return {}
    if draw(chance(3)):
        return {'list': draw(values_of(name))}
    logic = draw(rarely(st.sampled_from(LOGICS[kind]), st.sampled_from(ALL_LOGICS), 16))
    if logic in ('empty', 'notEmpty'):
        value = ''
    elif logic == 'contains':
        value = draw(step_one(SEARCHED[kind], 32, words=False))
    elif logic == 'notContains' and kind == 'date':
        value = draw(SPANS)
    else:
        value = draw(step_one(VALUES[name], 32, words=False))
    return {'search': {'value': value, 'logic': logic, 'type': 'text'}}


@st.composite
def criterion(draw: st.DrawFn) -> dict[str, Any]:
    """A SearchBuilder criterion: mostly a condition that fits its column with the values it takes; now and then one
    that does not fit, a value too many or too few, an empty value, a column not declared, or an unfinished one."""
    name = draw(rarely(st.sampled_from(NAMES), st.just('extra'), 48))
    kind = KINDS.get(name, 'string')
    if draw(chance(24)):  # unfinished: no condition, or no column
        unfinished = draw(st.sampled_from([{'condition': ''}, {'origData': ''}, {}]))
        return {'origData': name, 'data': name, 'type': 'string', 'value': [draw(VALUES[name])], **unfinished}
    condition = draw(rarely(st.sampled_from(CONDITIONS[kind]), st.sampled_from(ALL_CONDITIONS), 16))
    takes = 2 if condition in ('between', '!between') else 0 if condition in ('null', '!null') else 1
    count = draw(rarely(st.just(takes), st.sampled_from([0, takes + 1]), 24))
    value = SPANS if kind == 'date' and condition in ('contains', '!contains') else VALUES[name]
    values = draw(st.lists(step_one(value, 48, words=False), min_size=count, max_size=count))
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
        body['search']['multi_values'] = draw(st.lists(VALUES['s'], max_size=2))
    if draw(chance(3)):
        body['searchBuilder'] = draw(tree)
    if added := draw(added_criteria()):
        body.setdefault('searchBuilder', {'logic': 'AND', 'criteria': []})['criteria'].extend(added)
    if unfiltered:
        body['search'] = {'value': '', 'regex': False}
        body.pop('searchBuilder', None)
        for column in columns:
            column['search'], column['columnControl'] = {'value': '', 'regex': False}, {}
    return rows, decl, body


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
    return list(dict.fromkeys([*reached, *no_word_paths(decl, body)]))


def no_word_paths(decl: tuple[Declared, ...], body: dict[str, Any]) -> list[str]:
    """The paths of column search text that PRD-F6 reduces to no word."""
    reached: list[str] = []
    searchable = {name: flag for name, _, flag, _ in decl}
    for column in body['columns']:
        search = column['search']
        if not search['regex'] and search['value'] and not any(_words(search['value'])):
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


@given(cases())
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
    no hand-written test lists. The example: the entry on `id` is skipped (unorderable on both sides), so `s` desc is
    the first applied entry, and its tie (every `s` NULL) is broken by the key descending: row 3 comes first (PRD-O3,
    PRD-O8)."""
    rows, decl, body = case
    params = parse_request(body)
    try:
        expected = evaluate(decl, rows, params)
    except SpecGap as gap:
        event(f'spec gap {gap.name}')
        return
    event(outcome(rows, expected))
    if isinstance(expected, Answer):
        for path in paths(rows, decl, body):
            event(path)
    assert library(decl, rows, params) == expected, 'the library and the reference evaluator answer differently'
