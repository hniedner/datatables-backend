"""SvelteDT's connection helper's request examples read as the spec states them (#48, partial).

Source: SvelteDT spec/connection.md, CONN-02, CONN-04, CONN-05, CONN-06, CONN-07, CONN-09, CONN-10 and CONN-12, at
commit 980e65e (text unchanged since a6917bb; 1c61e47 is identical); re-pin deliberately. CONN-04's server side is the
bracketed pairs of the first test (`true`, `false` and decimal text read as the JSON form's booleans and numbers).
The rows are copied here as literals, since CI has no SvelteDT checkout. This is not release criterion 4:
those are recordings of real backends (SvelteDT's PRD-W11), which do not exist yet. It pins what its spec already says
the helper sends, so a disagreement shows before the recordings do.

The spec's map M is three columns, `{ data: 'name', name: 'n' }`, `{ data: 'code', name: 'c' }` and
`{ data: 'status', name: 's' }`: the server matches a column by its `name` when it has one, so a table for this helper
declares `n`, `c` and `s`. The other statements of that spec (CONN-03, 08, 13 to 16) are read by tests that
already exist; the PR text lists them.
"""

import json
import re
from typing import Any

import pytest
from sqlalchemy import Column as SQLColumn
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from datatables_backend import Column
from datatables_backend import DTOrder
from datatables_backend import Search
from datatables_backend import parse_request
from datatables_backend.sqlalchemy import SQLTable

# CONN-02, first row: the request of M and the initial query Q0 (sort name ascending, no filter, no search, offset 0,
# limit 10), in the bracketed form, 26 pairs
Q0_BRACKETED = [
    ('draw', '1'),
    ('start', '0'),
    ('length', '10'),
    ('search[value]', ''),
    ('search[regex]', 'false'),
    *[
        pair
        for index, (data, name) in enumerate([('name', 'n'), ('code', 'c'), ('status', 's')])
        for pair in [
            (f'columns[{index}][data]', data),
            (f'columns[{index}][name]', name),
            (f'columns[{index}][searchable]', 'true'),
            (f'columns[{index}][orderable]', 'true'),
            (f'columns[{index}][search][value]', ''),
            (f'columns[{index}][search][regex]', 'false'),
        ]
    ],
    ('order[0][column]', '0'),
    ('order[0][dir]', 'asc'),
    ('order[0][name]', 'n'),
]

# CONN-06: the plain object a DataTables 2.x client builds for the same request
Q0_JSON: dict[str, Any] = {
    'draw': 1, 'start': 0, 'length': 10, 'search': {'value': '', 'regex': False},
    'columns': [
        {'data': 'name', 'name': 'n', 'searchable': True, 'orderable': True, 'search': {'value': '', 'regex': False}},
        {'data': 'code', 'name': 'c', 'searchable': True, 'orderable': True, 'search': {'value': '', 'regex': False}},
        {'data': 'status', 'name': 's', 'searchable': True, 'orderable': True, 'search': {'value': '', 'regex': False}},
    ],
    'order': [{'column': 0, 'dir': 'asc', 'name': 'n'}],
}  # fmt: skip


def dotted(key: str) -> str:
    """CONN-05: list indexes stay in brackets and object keys are joined with a dot."""
    return re.sub(r'\[([a-z_]+)\]', r'.\1', key)


def test_the_helpers_base_request_reads_alike_in_the_bracketed_dotted_and_json_forms():
    """CONN-02, CONN-05, CONN-06: the 26 pairs, the same pairs under the dotted keys of CONN-05's table, and the JSON
    object are one request, and it names its columns by `name`, not by `data`."""
    assert len(Q0_BRACKETED) == 26
    assert [dotted(key) for key in ('columns[0][data]', 'columns[0][search][value]', 'order[0][column]')] == [
        'columns[0].data',
        'columns[0].search.value',
        'order[0].column',
    ]
    assert [dotted(key) for key in ('search[value]', 'draw')] == ['search.value', 'draw']  # CONN-05's table
    from_brackets = parse_request(Q0_BRACKETED)
    from_dots = parse_request([(dotted(key), value) for key, value in Q0_BRACKETED])
    assert from_brackets == from_dots == parse_request(Q0_JSON) == parse_request(json.loads(json.dumps(Q0_JSON)))
    assert [column.key for column in from_brackets.columns] == ['n', 'c', 's']
    assert [column.data for column in from_brackets.columns] == ['name', 'code', 'status']
    assert from_brackets.order == [DTOrder(column=0, dir='asc')]
    assert (from_brackets.draw, from_brackets.start, from_brackets.length) == (1, 0, 10)
    assert from_brackets.search == Search(value='', regex=False)


metadata = MetaData()
items = Table(
    'items',
    metadata,
    SQLColumn('id', Integer, primary_key=True),
    SQLColumn('name', String(20)),
    SQLColumn('code', String(20)),
    SQLColumn('status', String(20)),
)
ROWS = [
    (1, 'Beta', 'k1', 'active'),
    (2, 'Alpha', 'k2', 'inactive'),
    (3, 'axb', 'k3', 'a|b'),  # a decoy for the text `a.b`, and the one value `a|b`
    (4, 'a.b', 'k4', 'a'),
    (5, 'Gamma', 'k5', 'b'),
]
table = SQLTable(items, [Column('n', field='name'), Column('c', field='code'), Column('s', field='status')])


@pytest.fixture
def session():
    engine = create_engine('sqlite://', poolclass=StaticPool)
    metadata.create_all(engine)
    with Session(engine) as session:
        session.execute(items.insert(), [dict(zip(('id', 'name', 'code', 'status'), row, strict=True)) for row in ROWS])
        yield session
    engine.dispose()


def answer(
    session: Session, *, column: int | None = None, value: str = '', regex: bool = False, glob: str = ''
) -> list[str]:
    """The codes answered for Q0's request with `value` as one column's filter and `glob` as the global search."""
    body = json.loads(json.dumps(Q0_JSON))
    body['search']['value'] = glob
    if column is not None:
        body['columns'][column]['search'] = {'value': value, 'regex': regex}
    return sorted(row['c'] for row in table.fetch_sync(parse_request(body), session).data)


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('Beta ', ['k1']),  # CONN-09: the space is kept in what is sent, and it is no part of the word
        ('a.b', ['k4']),  # CONN-09: a dot is text, so not `axb`
    ],
)
def test_a_text_filter_and_a_global_search_are_sent_as_given_and_read_as_text(
    session: Session, text: str, expected: list[str]
):
    """CONN-09 and CONN-12: `columns[0][search][value]` and `search[value]` with `regex` false, for the two texts of
    the spec's tables that no other test holds."""
    assert answer(session, column=0, value=text) == expected
    assert answer(session, glob=text) == expected


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        (r'^(a\|b)$', ['k3']),  # CONN-10: a pipe inside a value is escaped, so it is one value `a|b`, not `a` or `b`
        ('^(a|b)$', ['k4', 'k5']),  # the same text without the backslash is two values
    ],
)
def test_a_pipe_in_a_selected_value_is_one_value_only_when_it_is_escaped(
    session: Session, value: str, expected: list[str]
):
    """CONN-10's `['a|b']` row: the helper sends `^(a\\|b)$` (backslash, then pipe) with `regex` true."""
    assert answer(session, column=2, value=value, regex=True) == expected


@pytest.mark.parametrize(
    ('start', 'length', 'expected'),
    [
        ('0', '10', ['k2', 'k1', 'k5', 'k4', 'k3']),  # CONN-07's first row
        ('20', '25', []),  # its second row: past the 5 rows, an empty page
        ('30', '10', []),  # its third row, after `pager.onpage(30)`
        ('1', '2', ['k1', 'k5']),  # the offset skips one row and the limit keeps two
        ('3', '10', ['k4', 'k3']),  # the offset skips three and the limit is not reached
    ],
)
def test_start_is_the_offset_and_length_the_limit(session: Session, start: str, length: str, expected: list[str]):
    """CONN-07: `start` and `length` as the decimal text of the form are the offset and the limit of the page of Q0 (name
    ascending: Alpha, Beta, Gamma, a.b, axb), and read as the JSON form's numbers do; `recordsFiltered` stays 5."""
    pairs = [(key, start if key == 'start' else length if key == 'length' else value) for key, value in Q0_BRACKETED]
    params = parse_request(pairs)
    assert params == parse_request({**Q0_JSON, 'start': int(start), 'length': int(length)})
    response = table.fetch_sync(params, session)
    assert [row['c'] for row in response.data] == expected
    assert response.recordsFiltered == 5
