"""Property tests: whatever a client sends, ``parse_request`` returns or raises ``MalformedRequest`` (PRD-P12), the
message of a refusal quotes nothing the client sent, and a request that parses is answered or refused with a stable
code, never with another exception.

Generated input is biased to DataTables' own keys, so it reaches the paths under ``columns``, ``order``, ``search`` and
``searchBuilder`` and not only "no DataTables parameter". Text that a client could see echoed carries ``MARK``: a
message or a log record that holds it quotes the client. That proves no marked key or value is echoed, nothing about
every text. A refusal is logged once, as a WARNING, and a request that is answered logs nothing; and one request is read
the same in every form a client can send it.
"""

from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
import logging
from typing import Any

from hypothesis import example
from hypothesis import given
from hypothesis import settings
from hypothesis import strategies as st
from sqlalchemy import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from datatables_backend import Column
from datatables_backend import DTParams
from datatables_backend import DTResponse
from datatables_backend import MalformedRequest
from datatables_backend import parse_request
from datatables_backend.sqlalchemy import SQLTable

from .conftest import ROWS
from .conftest import metadata
from .conftest import people
from .conftest import rarely

MARK = 'QXQ'
STABLE_CODES = {
    'invalid_filter',
    'invalid_character',
    'value_too_long',
    'too_many_words',
    'request_too_large',
    'invalid_date',
    'invalid_number',
    'page_out_of_range',
}
KEYS = [
    'draw', 'start', 'length', 'columns', 'order', 'search', 'searchBuilder', 'searchPanes', 'searchPanes_null',
    'data', 'name', 'searchable', 'orderable', 'regex', 'value', 'fixed', 'logic', 'criteria', 'origData', 'condition',
    'type', 'value1', 'column', 'dir', 'columnControl', 'list', 'groups', 'groupsFixed', 'empty',
]  # fmt: skip
NAMES = ['id', 'name', 'email', 'age', 'born', 'seen', 'team']
CONDITIONS = ['=', '!=', 'starts', '!starts', 'ends', '!ends', 'contains', '!contains', '<', '<=', '>', '>=',
              'between', '!between', 'null', '!null', 'empty', '!empty', 'bogus']  # fmt: skip
SEGMENTS = ['[0]', '[1]', '[]', '[', ']', '[]]', '[[0]]', '.0', '.', ' ', '[00]', '[0123456789]', '[0].x', '[a.b]', '']

marked = st.text(alphabet='abcdefghijklmnopqrstuvwxyz', min_size=4, max_size=10).map(f'{MARK}{{}}'.format)
#: A marked value over the 255 code points a value may hold, so the refusal of a value too long is reached with a
#: mark in the value it must not repeat
long_marked = marked.map(lambda text: text + 'x' * 256)


def seldom_long(base: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    """``base`` mostly, a long marked value one time in sixteen: often enough to reach the refusal of a value too long,
    seldom enough that most requests still reach the query. A column search is pinned by an example instead."""
    return rarely(base, long_marked, 16)


words = st.sampled_from(
    ['', ' ', 'a', 'Ann', '31', '-1', '0', '2026-07-15', '2026-13-45', '2026-07', '1e999', '٣', '%', '_', '\x00', 'x' * 300]
)  # fmt: skip
scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-(2**70), 2**70),
    st.floats(),
    st.text(max_size=12),
    words,
    marked,
    st.sampled_from(['true', 'false', 'asc', 'desc', 'AND', 'OR', 'string', 'num', 'date', 'html']),
)
keys = st.one_of(st.sampled_from(KEYS), marked, st.text(max_size=5))
anything = st.recursive(
    scalars,
    lambda inner: st.one_of(st.lists(inner, max_size=4), st.dictionaries(keys, inner, max_size=4)),
    max_leaves=10,
)


def maybe[T](typed: st.SearchStrategy[T]) -> st.SearchStrategy[Any]:
    """Mostly what DataTables sends, sometimes anything: so a typed path is reached and a wrong type is too."""
    return st.one_of(typed, typed, typed, anything)


flag = st.booleans()
criterion = st.recursive(
    st.fixed_dictionaries(
        {},
        optional={
            'origData': maybe(st.sampled_from(NAMES)),
            'condition': maybe(st.sampled_from(CONDITIONS)),
            'type': maybe(st.sampled_from(['string', 'num', 'num-fmt', 'date', 'moment', 'html', 'html-num'])),
            'value': maybe(st.lists(words | marked, max_size=4)),
            'value1': maybe(words | marked),
            'value2': maybe(words | marked),
        },
    ),
    lambda child: st.fixed_dictionaries(
        {}, optional={'logic': maybe(st.sampled_from(['AND', 'OR'])), 'criteria': maybe(st.lists(child, max_size=3))}
    ),
    max_leaves=8,
)
search = st.fixed_dictionaries({}, optional={'value': maybe(words | marked), 'regex': maybe(flag), 'fixed': anything})
CC_LOGICS = ['contains', 'notContains', 'equal', 'notEqual', 'starts', 'ends', 'empty', 'notEmpty', 'greater',
             'greaterOrEqual', 'less', 'lessOrEqual', '', 'bogus']  # fmt: skip
#: ColumnControl's part of a column: a search of a value, a logic and a type from the valid and from arbitrary text, and
#: a list as an object whose keys are indexes, huge ones and arbitrary text included, or as an array of anything
columncontrol_search = st.fixed_dictionaries(
    {},
    optional={
        'value': maybe(words | marked),
        'logic': maybe(st.sampled_from(CC_LOGICS)),
        'type': maybe(st.sampled_from(['text', 'num', 'date', 'html'])),
        'mask': anything,
    },
)
columncontrol_list = st.one_of(
    st.dictionaries(
        st.one_of(st.integers(0, 5).map(str), st.just('999999999'), keys), maybe(words | marked), max_size=4
    ),
    st.lists(anything, max_size=4),
)
columncontrol = st.one_of(
    st.fixed_dictionaries({}, optional={'search': maybe(columncontrol_search), 'list': maybe(columncontrol_list)}),
    anything,
)
column = st.fixed_dictionaries(
    {},
    optional={
        'data': st.one_of(st.sampled_from(NAMES), anything),
        'name': maybe(words | marked),
        'searchable': maybe(flag),
        'orderable': maybe(flag),
        'search': maybe(search),
        'columnControl': columncontrol,
    },
)
order_entry = st.fixed_dictionaries(
    {}, optional={'column': maybe(st.integers(0, 8)), 'dir': maybe(st.sampled_from(['asc', 'desc'])), 'name': anything}
)
shaped = st.fixed_dictionaries(
    {},
    optional={
        'draw': maybe(st.integers(0, 10**6)),
        'start': maybe(st.integers(0, 50)),
        'length': maybe(st.integers(-1, 200)),
        'columns': maybe(st.lists(column, max_size=5)),
        'order': maybe(st.lists(order_entry, max_size=4)),
        'search': maybe(search),
        'searchBuilder': maybe(criterion),
        'searchPanes': anything,
        'searchPanes_null': anything,
    },
)


@st.composite
def plausible(draw: st.DrawFn) -> dict[str, Any]:
    """A request the server's own columns could get: columns by their declared names, SearchBuilder conditions on
    them, orders by index. It is what reaches the planner and the query, which ``shaped`` seldom does with three
    names to match at once; a part of it is wrong now and then. It also names columns the server never declared, and
    now and then sends a value over the 255 code points a value may hold, both marked."""
    names = draw(st.lists(st.sampled_from(NAMES), min_size=1, max_size=7, unique=True))
    # Names the server never declared, marked: one request in sixteen. More would end most requests in a refusal of
    # the name
    undeclared = draw(rarely(st.just([]), st.lists(marked, min_size=1, max_size=2, unique=True), 16))
    columns = [
        {
            'data': name,
            'name': '',
            'searchable': draw(flag),
            'orderable': draw(flag),
            'search': {'value': draw(st.one_of(st.just(''), words, marked)), 'regex': False},
        }
        for name in [*names, *undeclared]
    ]
    # A long marked value goes into `value`, which the library reads; `value1` and `value2` are ignored keys
    leaf = st.fixed_dictionaries(
        {
            'origData': rarely(st.sampled_from([*NAMES, 'born', 'seen']), marked, 16),
            'condition': st.sampled_from([*CONDITIONS, '=', '<', '>', 'between']),
            'type': st.sampled_from(['string', 'num', 'date', 'html']),
            'value': st.lists(rarely(words | marked, long_marked, 8), max_size=3),
        },
        optional={'value1': words | marked, 'value2': words | marked},
    )

    def groups_of(child: st.SearchStrategy[Any]) -> st.SearchStrategy[dict[str, Any]]:
        return st.fixed_dictionaries(
            {'logic': st.sampled_from(['AND', 'OR']), 'criteria': st.lists(child, min_size=1, max_size=3)}
        )

    group = groups_of(st.recursive(leaf, groups_of, max_leaves=6))  # the top is always a group, as DataTables sends it
    return {
        'draw': draw(st.integers(0, 10**6)),
        'start': draw(st.integers(0, 10)),
        'length': draw(st.integers(1, 50)),
        'columns': columns,
        'order': draw(
            st.lists(
                st.fixed_dictionaries(
                    {
                        'column': st.one_of(st.integers(0, len(names) - 1), st.integers(0, len(names) + 3)),
                        'dir': st.sampled_from(['asc', 'desc']),
                    },
                    optional={'name': marked},
                ),
                max_size=3,
            )
        ),
        'search': {'value': draw(seldom_long(st.one_of(st.just(''), words, marked))), 'regex': False},
        **draw(st.one_of(st.just({}), *[st.builds(lambda g: {'searchBuilder': g}, maybe(group))] * 3)),
    }


free = st.dictionaries(keys, anything, max_size=6)
bodies = st.one_of(shaped, plausible(), plausible(), free, st.lists(anything, max_size=4))

root = st.sampled_from(
    ['columns', 'order', 'search', 'searchBuilder', 'searchPanes', 'searchPanes_null', 'draw', 'start']
)
segment = st.one_of(
    st.sampled_from(SEGMENTS),
    st.sampled_from(KEYS).map('[{}]'.format),
    st.sampled_from(KEYS).map('.{}'.format),
    st.integers(0, 12).map('[{}]'.format),
    marked.map('[{}]'.format),
    marked.map('.{}'.format),
)
flat_key = st.builds(lambda start, rest: start + ''.join(rest), root, st.lists(segment, max_size=8))
flat_value = st.one_of(words, marked, st.text(max_size=8), st.sampled_from(['true', 'false', 'asc', 'desc']))
pairs = st.lists(st.tuples(flat_key, flat_value), max_size=14)


class MultiDict(Mapping[str, str]):
    """What a framework's form gives: one value per key by indexing, every value by ``getlist``."""

    def __init__(self, items: list[tuple[str, str]]) -> None:
        self._items = items

    def __getitem__(self, key: str) -> str:
        return [value for name, value in self._items if name == key][-1]

    def __iter__(self) -> Iterator[str]:
        return iter(dict.fromkeys(name for name, _ in self._items))

    def __len__(self) -> int:
        return len({name for name, _ in self._items})

    def getlist(self, key: str) -> list[str]:
        return [value for name, value in self._items if name == key]


containers = st.one_of(
    pairs,
    pairs.map(iter),
    pairs.map(dict),
    pairs.map(MultiDict),
)
requests = st.one_of(bodies, containers)


@contextmanager
def captured() -> Iterator[list[logging.LogRecord]]:
    """The records the library logs while the block runs, whatever the logging configuration says: a handler on the
    library's logger, at its lowest level, removed again whatever happens."""
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger('datatables_backend')
    handler, level = Collect(logging.DEBUG), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)


def holds_nothing_sent(records: list[logging.LogRecord]) -> None:
    """Every record is a WARNING (SPEC-E13.1) and none holds a marked value or name, in its message, arguments or extra
    fields, in either case: search text is lower-cased for a LIKE, so a record may echo the lower-cased mark."""
    for record in records:
        assert record.levelno == logging.WARNING, 'a refusal is logged as a WARNING'
        text = repr(vars(record)) + record.getMessage()
        assert MARK.lower() not in text.lower(), 'a log record quotes what the client sent'


def parsed_or_refused(data: Any) -> DTParams | None:
    """The request's parameters, or None for a refusal that quotes nothing marked."""
    try:
        return parse_request(data)
    except MalformedRequest as refused:
        assert MARK not in str(refused)
        assert MARK not in getattr(refused, 'event', '')
        assert MARK.lower() not in str(refused).lower(), 'a refusal quotes what the client sent, lower-cased'
        return None


@given(requests)
@example([])
@example([{'columns': 1}])
@example({'columns': {'0': {'data': None}}, 'order': 'x'})
def test_a_request_is_read_or_refused_as_malformed_and_the_refusal_quotes_nothing(data: Any):
    """Any other exception is a client-caused crash, which the library promises not to have (a 500 in the adapter).
    A refusal is logged once and a request that is read logs nothing (SPEC-E13.1, SPEC-E13.2), and no record quotes
    what the client sent (SPEC-E10.1)."""
    with captured() as records:
        parsed = parsed_or_refused(data)
    assert len(records) == (parsed is None), 'a refusal is logged once, a request that is read is not logged'
    holds_nothing_sent(records)


@given(st.integers(50, 5000), st.sampled_from(['columns', 'order', 'search', 'searchBuilder', 'draw']))
def test_a_request_nested_beyond_what_python_walks_is_refused_as_malformed(depth: int, name: str):
    """A flat key or a body nested thousands deep ended in ``RecursionError`` once."""
    flat = {f'{name}{"[0]" * depth}': 'x'}
    nested_dict: Any = 'x'
    nested_list: Any = 'x'
    for _ in range(depth):
        nested_dict, nested_list = {'criteria': nested_dict}, [nested_list]
    for data in (flat, {name: nested_dict}, {name: nested_list}, nested_list):
        parsed_or_refused(data)


# One in-memory database for every example: a function-scoped fixture would be rebuilt (or refused) per example.
_engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
metadata.create_all(_engine)
with _engine.begin() as _connection:
    _connection.execute(people.insert(), ROWS)
_table = SQLTable(
    people,
    [
        Column('id'),
        Column('name'),
        Column('email', field='email_address'),
        Column('age'),
        Column('born'),
        Column('seen'),
        Column('team', orderable=False),
    ],
)


def one_column_request(**parts: Any) -> dict[str, Any]:
    """The plainest request the table above answers, with ``parts`` added: pins what the generators must reach."""
    column = {'data': 'id', 'name': '', 'searchable': True, 'orderable': True, 'search': {'value': '', 'regex': False}}
    return {'draw': 1, 'start': 0, 'length': 5, 'columns': [column], 'order': [], 'search': {'value': ''}, **parts}


@settings(max_examples=max(1, settings().max_examples // 2))
@given(st.one_of(plausible(), plausible(), shaped))
@example(
    one_column_request(
        searchBuilder={
            'logic': 'AND',
            'criteria': [{'origData': f'{MARK}name', 'condition': '=', 'type': 'string', 'value': ['a']}],
        }
    )
)  # an undeclared name in a criterion: a refusal that echoed it would be found here
@example(
    one_column_request(
        columns=[
            {
                'data': 'id',
                'name': '',
                'searchable': True,
                'orderable': True,
                'search': {'value': f'{MARK}{"x" * 256}', 'regex': False},
            }
        ]
    )
)  # a marked value over 255 code points: a record that carried it would be found here
@example(
    one_column_request(
        searchBuilder={
            'logic': 'AND',
            'criteria': [{'origData': 'id', 'condition': '=', 'type': 'num', 'value': [f'{MARK}{"x" * 256}']}],
        }
    )
)  # the same in a criterion's value, which the generators seldom carry to the check in the `ci` profile
def test_a_request_that_is_read_is_answered_or_refused_with_a_stable_code(data: dict[str, Any]):
    """A client-caused exception in ``fetch_sync`` would be a 500 for a request any browser could send."""
    with captured() as records:
        params = parsed_or_refused(data)
        if params is None:
            assert len(records) == 1, 'a refusal is logged once'
            holds_nothing_sent(records)
            return
        assert not records, 'a request that is read is not logged'
        with Session(_engine) as session:
            response = _table.fetch_sync(params, session)
    assert isinstance(response, DTResponse)
    assert response.error_code is None or response.error_code in STABLE_CODES
    assert (response.error is None) == (response.error_code is None)
    assert MARK not in (response.error or '')
    assert MARK.lower() not in (response.error or '').lower(), 'an answer quotes what the client sent, lower-cased'
    assert len(records) == (response.error_code is not None), 'a user error is logged once, an answer is not'
    holds_nothing_sent(records)


def text_of(value: Any) -> str:
    """A scalar as DataTables writes it into a form (dataTables.mjs:884-890): digits, `true`, `false` or the text."""
    return str(value).lower() if isinstance(value, bool) else str(value)


def flatten(node: Any, key: str, *, dotted: bool, append: bool) -> list[tuple[str, str]]:
    """``node`` as flat pairs, written from the generated value alone (nothing here reads a key back). A segment is
    ``[name]``, or ``.name`` when ``dotted``; a list of text is repeated ``key[]`` entries when ``append``, else
    indexed like a list of objects."""

    def under(name: Any) -> str:
        return f'{key}.{name}' if dotted and key else f'{key}[{name}]' if key else str(name)

    if isinstance(node, dict):
        return [
            pair for name, item in node.items() for pair in flatten(item, under(name), dotted=dotted, append=append)
        ]
    if isinstance(node, list):
        if append and not dotted and not any(isinstance(item, dict) for item in node):
            return [(f'{key}[]', text_of(item)) for item in node]
        return [
            pair
            for index, item in enumerate(node)
            for pair in flatten(item, under(index), dotted=dotted, append=append)
        ]
    return [(key, text_of(node))]


def forms_of(body: dict[str, Any]) -> list[Any]:
    """One request in every form a client can send it: the JSON body (the reference), DataTables' own bracket keys as
    pairs and as a multi-dict, with a list of text as repeated ``key[]`` entries, and every list indexed, in brackets
    and in dots, as a plain mapping (which cannot repeat a key)."""
    bracket = flatten(body, '', dotted=False, append=True)
    return [
        body,
        bracket,
        iter(bracket),
        MultiDict(bracket),
        dict(flatten(body, '', dotted=False, append=False)),
        dict(flatten(body, '', dotted=True, append=False)),
    ]


def outcome(data: Any) -> tuple[Any, ...]:
    try:
        return ('read', parse_request(data))
    except MalformedRequest as refused:
        return ('refused', str(refused), refused.parameter, refused.draw)


criterion_value_lists = st.lists(words | marked, min_size=1, max_size=12)
encodable_criterion = st.fixed_dictionaries(
    {
        'origData': st.sampled_from(NAMES),
        'condition': st.sampled_from(CONDITIONS),
        'type': st.sampled_from(['string', 'num', 'date', 'html']),
        'value': criterion_value_lists,
    },
    optional={'value1': words | marked, 'value2': words | marked},
)


def encodable_groups_of(child: st.SearchStrategy[Any]) -> st.SearchStrategy[dict[str, Any]]:
    return st.fixed_dictionaries(
        {'logic': st.sampled_from(['AND', 'OR']), 'criteria': st.lists(child, min_size=1, max_size=3)}
    )


encodable_group = encodable_groups_of(st.recursive(encodable_criterion, encodable_groups_of, max_leaves=6))
encodable_column = st.fixed_dictionaries(
    {
        'data': st.sampled_from(NAMES),
        'name': st.one_of(st.just(''), st.sampled_from(NAMES)),
        'searchable': flag,
        'orderable': flag,
        'search': st.fixed_dictionaries({'value': words | marked, 'regex': flag}),
    }
)
encodable = st.fixed_dictionaries(
    {
        'draw': st.integers(0, 10**6),
        'start': st.integers(0, 50),
        'length': st.integers(1, 200),
        'columns': st.lists(encodable_column, min_size=1, max_size=12),
        'search': st.fixed_dictionaries({'value': words | marked, 'regex': flag}),
    },
    optional={
        'order': st.lists(
            st.fixed_dictionaries({'column': st.integers(0, 13), 'dir': st.sampled_from(['asc', 'desc'])}),
            min_size=1,
            max_size=12,
        ),
        'searchBuilder': encodable_group,
    },
)


@given(encodable)
@example(
    {
        'draw': 1,
        'start': 0,
        'length': 10,
        'columns': [
            {'data': 'id', 'name': '', 'searchable': True, 'orderable': True, 'search': {'value': '', 'regex': False}}
        ]
        * 11,
        'search': {'value': '', 'regex': False},
    }
)  # eleven columns: an index of two digits, which sorts before 2 as text
@example(
    {
        'draw': 1,
        'start': 0,
        'length': 10,
        'columns': [
            {'data': 'id', 'name': '', 'searchable': True, 'orderable': True, 'search': {'value': '', 'regex': False}}
        ],
        'search': {'value': '', 'regex': False},
        'searchBuilder': {
            'logic': 'AND',
            'criteria': [{'origData': 'id', 'condition': 'between', 'type': 'num', 'value': ['1', '', '2']}],
        },
    }
)  # an empty entry in the middle of a list of text, sent as `value[]=`
def test_one_request_is_read_the_same_in_every_form(body: dict[str, Any]):
    """SPEC-P1.1, SPEC-P2.1: the JSON body, the bracket form (as pairs and as a multi-dict) and the indexed bracket and
    dotted forms of one request are read to equal parameters, or refused with the same message, parameter and draw.
    Only requests every form can say are generated: no empty list and no empty object (a flat form sends no key for
    one), and no null, float or key with a `.` or a bracket (a flat form reads them as text or as a path). The
    ColumnControl and SearchPanes parameters and an order entry's `name` are left out as out of scope: examples pin them
    (SPEC-P5.3, SPEC-P5.4). Each encoder writes keys from the generated value and never calls the parser."""
    reference = outcome(body)
    for form in forms_of(body):
        assert outcome(form) == reference, 'a form of the request is read differently from its JSON body'
