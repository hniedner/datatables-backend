"""The example application answers the requests its page sends, so it cannot rot unnoticed.

The page's requests are DataTables' own: ``page_requests.json`` holds four of them, captured from ``index.html`` with
the pinned DataTables and ColumnControl files in jsdom. ``page_request`` builds the same bodies, and the first test
pins it to the capture; the others use it for the requests of a person using the page. The expected rows are
computed here from ``SEED``, in plain Python, never by the library.

Run it with the rest: ``uv run pytest``. It is not in the wheel or the sdist (``examples/`` ships in neither).
"""

from collections.abc import Iterator
from collections.abc import Mapping
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlencode

from fastapi.testclient import TestClient
import pytest

import example_app

HERE = Path(__file__).parent
CAPTURED = json.loads((HERE / 'page_requests.json').read_text(encoding='utf-8'))['requests']
COLUMNS = ['id', 'label', 'colour', 'size', 'added', 'status']
FORM = 'application/x-www-form-urlencoded; charset=UTF-8'  # what DataTables sends, `submitAs: 'json'` included
KEYS = {'draw', 'recordsTotal', 'recordsFiltered', 'data'}  # an answer holds no `error: null`


def page_request(
    draw: int = 1,
    *,
    search: str = '',
    colour: str = '',
    statuses: tuple[str, ...] = (),
    order: tuple[tuple[int, str], ...] = ((0, 'asc'),),
    start: int = 0,
    length: int = 10,
) -> dict[str, Any]:
    """The request DataTables 3.1.6 builds for the page, as the nested object it serialises."""
    columns: list[dict[str, Any]] = []
    for index, name in enumerate(COLUMNS):
        column: dict[str, Any] = {
            'data': name,
            'name': '',
            'searchable': True,
            'orderable': True,
            'search': {'value': colour if index == 2 else '', 'regex': False},
        }
        if index == 5 and statuses:  # ColumnControl's list: its ticked values, keyed by position
            column['columnControl'] = {'list': {str(position): value for position, value in enumerate(statuses)}}
        columns.append(column)
    return {
        'draw': draw,
        'columns': columns,
        'order': [{'column': column, 'dir': direction, 'name': ''} for column, direction in order],
        'start': start,
        'length': length,
        'search': {'value': search, 'regex': False},
    }


def pairs(value: Any, prefix: str = '') -> Iterator[tuple[str, str]]:
    """DataTables' form serialisation: brackets for nesting, ``true`` and ``false``, nothing for an empty object."""
    if isinstance(value, Mapping):
        for key, inner in value.items():  # type: ignore[reportUnknownVariableType]
            yield from pairs(inner, f'{prefix}[{key}]' if prefix else str(key))  # type: ignore[reportUnknownArgumentType]
    elif isinstance(value, list):
        for position, inner in enumerate(value):  # type: ignore[reportUnknownVariableType]
            yield from pairs(inner, f'{prefix}[{position}]')
    else:
        yield prefix, str(value).lower() if isinstance(value, bool) else str(value)


def as_form(body: dict[str, Any]) -> str:
    return urlencode(list(pairs(body)))


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(example_app.app) as running:  # the lifespan creates and seeds the database
        yield running


def post(client: TestClient, body: dict[str, Any], *, as_json: bool = False) -> dict[str, Any]:
    """What the page does: a POST to its own `ajax` url, with the cache-buster DataTables adds."""
    data = json.dumps(body) if as_json else as_form(body)
    response = client.post('/items/table?_=1764000000000', content=data, headers={'Content-Type': FORM})
    assert response.status_code == 200
    answer: dict[str, Any] = response.json()
    assert answer['draw'] == body['draw']
    return answer


def test_the_builder_makes_the_requests_the_page_sends():
    """The four captured requests, from the page in jsdom, are the builder's: so a change of DataTables' wire form
    that the test would not see is seen when the capture is renewed, and the test's other requests are the page's."""
    assert as_form(page_request(1)) == CAPTURED['first_draw']
    assert as_form(page_request(2, colour='red')) == CAPTURED['colour_red']
    assert as_form(page_request(3, colour='red', statuses=('open',))) == CAPTURED['colour_red_and_status_open']
    assert as_form(page_request(4, order=((2, 'asc'), (3, 'desc')))) == CAPTURED['colour_asc_size_desc']


@pytest.mark.parametrize('as_json', [False, True], ids=['form', 'json'])
def test_the_first_draw_is_the_first_page_of_every_row(client: TestClient, as_json: bool):
    """The page's first request, as a form and as the JSON of `submitAs: 'json'` (which keeps the form's
    Content-Type). The answer holds the four keys DataTables reads and no `error: null`, which a route annotated
    `-> DTResponse` would add."""
    answer = post(client, page_request(1), as_json=as_json)
    assert set(answer) == KEYS
    assert answer['recordsTotal'] == answer['recordsFiltered'] == len(example_app.SEED)
    assert [row['id'] for row in answer['data']] == list(range(1, 11))
    assert all(set(row) == set(COLUMNS) for row in answer['data'])
    assert all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['added']) for row in answer['data'])


def seeded(**held: Any) -> list[dict[str, Any]]:
    return [row for row in example_app.SEED if all(check(row[name]) for name, check in held.items())]


@pytest.mark.parametrize('as_json', [False, True], ids=['form', 'json'])
@pytest.mark.parametrize(
    ('body', 'expected'),
    [
        pytest.param({'search': 'amber'}, lambda: seeded(label=lambda label: 'amber' in label), id='the global search'),
        pytest.param({'colour': 'red'}, lambda: seeded(colour=lambda colour: 'red' in colour), id='the column filter'),
        pytest.param(
            {'statuses': ('open',)}, lambda: seeded(status=lambda status: status == 'open'), id='the list, one value'
        ),
        pytest.param({'statuses': ('open', 'closed')}, lambda: example_app.SEED, id='the list, both values'),
        pytest.param(
            {'search': 'amber', 'colour': 'red', 'statuses': ('closed',), 'order': ((3, 'desc'),)},
            lambda: seeded(
                label=lambda label: 'amber' in label,
                colour=lambda colour: colour == 'red',
                status=lambda s: s == 'closed',
            ),
            id='all three, sorted by size',
        ),
    ],
)
def test_a_person_using_the_page_gets_the_rows_that_match(
    client: TestClient, body: dict[str, Any], expected: Any, as_json: bool
):
    """The global search, the column filter and the list, alone and together: the counts are those of the seed, and
    every row on the page matches. It catches a page and an app that no longer agree on a column's name, a filter
    that no longer reaches the database, and an answer that no longer has DataTables' keys."""
    wanted = expected()
    assert wanted  # a request of this test finds something
    answer = post(client, page_request(2, length=200, **body), as_json=as_json)
    assert set(answer) == KEYS
    assert answer['recordsFiltered'] == len(wanted)
    assert answer['recordsTotal'] == len(example_app.SEED)
    assert sorted(row['id'] for row in answer['data']) == sorted(row['id'] for row in wanted)


def test_the_second_page_in_descending_size_order(client: TestClient):
    """The page's sort click and its next page: the sizes are the seed's, sorted, from the eleventh to the twentieth.
    A tie is no matter: the sizes do not depend on how the database orders it."""
    answer = post(client, page_request(3, order=((3, 'desc'),), start=10))
    sizes = sorted((row['size'] for row in example_app.SEED), reverse=True)
    assert [row['size'] for row in answer['data']] == sizes[10:20]


@pytest.mark.parametrize('as_json', [False, True], ids=['form', 'json'])
def test_a_shift_click_orders_by_colour_then_size(client: TestClient, as_json: bool):
    """The page's click on Colour and two Shift+clicks on Size: every row by colour ascending, then size descending,
    a tie by the unique key in the first entry's direction (docs/rules.md, Columns and ordering): `id` ascending."""
    answer = post(client, page_request(4, order=((2, 'asc'), (3, 'desc')), length=200), as_json=as_json)
    wanted = sorted(example_app.SEED, key=lambda row: (row['colour'], -row['size'], row['id']))
    assert [row['id'] for row in answer['data']] == [row['id'] for row in wanted]


def test_a_user_mistake_is_the_error_the_page_shows(client: TestClient):
    """Seventeen words in the search are the user's mistake: HTTP 200 with the text the page puts in `#items-error`
    and the code, and no rows (docs/rules.md)."""
    answer = post(client, page_request(4, search=' '.join('abcdefghijklmnopq')))
    assert answer['error_code'] == 'too_many_words'
    assert answer['error']
    assert answer['data'] == []


def test_the_page_is_served_and_names_what_the_app_answers(client: TestClient):
    """`GET /` is the page; its `ajax` url is the route the test posts to, its columns are the table's own, and the
    list's static options are values the seed holds (a list shows only the options the page gives it)."""
    response = client.get('/')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/html')
    page = response.text
    assert re.search(r"ajax:\s*\{\s*url:\s*'/items/table',\s*type:\s*'POST'", page)
    assert re.findall(r"data:\s*'(\w+)'", page) == COLUMNS
    options = re.findall(r"value:\s*'(\w+)'", page)
    assert options
    assert set(options) <= {row['status'] for row in example_app.SEED}
