"""Property tests: whatever a client sends, ``parse_request`` returns or raises ``MalformedRequest`` (PRD-P12), the
message of a refusal quotes nothing the client sent, and a request that parses is answered or refused with a stable
code, never with another exception.

Generated input is biased to DataTables' own keys, so it reaches the paths under ``columns``, ``order``, ``search`` and
``searchBuilder`` and not only "no DataTables parameter". Text that a client could see echoed carries ``MARK``: a
message that holds it quotes the client. That proves no marked key or value is echoed, nothing about every text.
"""

from collections.abc import Iterator
from collections.abc import Mapping
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
    names to match at once; a part of it is wrong now and then."""
    names = draw(st.lists(st.sampled_from(NAMES), min_size=1, max_size=7, unique=True))
    columns = [
        {
            'data': name,
            'name': '',
            'searchable': draw(flag),
            'orderable': draw(flag),
            'search': {'value': draw(st.one_of(st.just(''), words, marked)), 'regex': False},
        }
        for name in names
    ]
    leaf = st.fixed_dictionaries(
        {
            'origData': st.sampled_from([*NAMES, 'born', 'seen']),
            'condition': st.sampled_from([*CONDITIONS, '=', '<', '>', 'between']),
            'type': st.sampled_from(['string', 'num', 'date', 'html']),
            'value': st.lists(words | marked, max_size=3),
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
                    }
                ),
                max_size=3,
            )
        ),
        'search': {'value': draw(st.one_of(st.just(''), words, marked)), 'regex': False},
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


def parsed_or_refused(data: Any) -> DTParams | None:
    """The request's parameters, or None for a refusal that quotes nothing marked."""
    try:
        return parse_request(data)
    except MalformedRequest as refused:
        assert MARK not in str(refused)
        assert MARK not in getattr(refused, 'event', '')
        return None


@given(requests)
@example([])
@example([{'columns': 1}])
@example({'columns': {'0': {'data': None}}, 'order': 'x'})
def test_a_request_is_read_or_refused_as_malformed_and_the_refusal_quotes_nothing(data: Any):
    """Any other exception is a client-caused crash, which the library promises not to have (a 500 in the adapter)."""
    parsed_or_refused(data)


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


@settings(max_examples=max(1, settings().max_examples // 2))
@given(st.one_of(plausible(), plausible(), shaped))
def test_a_request_that_is_read_is_answered_or_refused_with_a_stable_code(data: dict[str, Any]):
    """A client-caused exception in ``fetch_sync`` would be a 500 for a request any browser could send."""
    params = parsed_or_refused(data)
    if params is None:
        return
    with Session(_engine) as session:
        response = _table.fetch_sync(params, session)
    assert isinstance(response, DTResponse)
    assert response.error_code is None or response.error_code in STABLE_CODES
    assert (response.error is None) == (response.error_code is None)
    assert MARK not in (response.error or '')
