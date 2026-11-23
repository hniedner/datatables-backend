"""The FastAPI dependency: every encoding DataTables can send, and a 422 that never quotes the request."""

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from datatables_backend import Column
from datatables_backend import DTResponse
from datatables_backend.fastapi import DataTablesParams
from datatables_backend.opensearch import SearchTable

from .conftest import criterion

app = FastAPI()


@app.api_route('/table', methods=['GET', 'POST'])
def table(params: DataTablesParams) -> dict[str, Any]:
    return {'length': params.length, 'columns': [column.key for column in params.columns]}


client = TestClient(app)


def test_a_json_body_is_read():
    response = client.post('/table', json={'length': 5, 'columns': [{'data': 'name'}]})
    assert response.json() == {'length': 5, 'columns': ['name']}


def test_a_get_query_string_is_read():
    response = client.get('/table', params={'length': '15', 'columns[0][data]': 'age'})
    assert response.json() == {'length': 15, 'columns': ['age']}


def test_a_form_post_is_read():
    response = client.post('/table', data={'length': '20', 'columns[0][data]': 'team'})
    assert response.json() == {'length': 20, 'columns': ['team']}


def test_a_malformed_request_answers_422_with_its_draw_naming_only_the_parameter():
    """SPEC-A10.1: FastAPI's own shape, the draw inside `detail`, so a client can still match the answer."""
    response = client.post('/table', data={'draw': '5', 'length': 'my-secret'})
    assert response.status_code == 422
    assert response.json() == {
        'detail': {'message': 'Invalid length parameter: send a whole number, as DataTables does.', 'draw': 5}
    }


def test_a_422_leaves_out_a_draw_that_could_not_be_read():
    """SPEC-A10.1: no `draw` key at all, rather than a null or a guess."""
    response = client.post('/table', data={'draw': 'x', 'length': '10'})
    assert (response.status_code, response.json()) == (
        422,
        {'detail': {'message': 'Invalid draw parameter: send a whole number, as DataTables does.'}},
    )


def test_a_body_that_is_not_json_answers_422():
    response = client.post('/table', content=b'{not json', headers={'content-type': 'application/json'})
    assert (response.status_code, response.json()) == (422, {'detail': {'message': 'The table request is malformed.'}})


def test_a_json_body_that_is_not_an_object_answers_422():
    """Also a list of pairs, which `parse_request` would read: over HTTP, JSON carries the request as an object."""
    response = client.post('/table', json=[['draw', '1'], ['length', '5']])
    assert (response.status_code, response.json()) == (422, {'detail': {'message': 'The table request is malformed.'}})


docs = SearchTable(
    'docs',
    [Column('name'), Column('made', type='date'), Column('price', type='number')],
    unique_key='name',
    max_result_window=3,
)
no_server: Any = object()  # the cases of `wide` below are refused before a server would be asked


class CountsOnly:
    """A server that answers a count and nothing else: a page search on this path fails loudly (no `search`)."""

    def count(self, *, index: str, body: Any) -> dict[str, Any]:
        return {'count': 10, '_shards': {'total': 1, 'successful': 1, 'failed': 0}}


@app.post('/docs')
def answer(params: DataTablesParams) -> DTResponse:
    # Every case below is a mistake found before a server is asked, but a page out of range, which asks for counts
    return docs.fetch_sync(params, CountsOnly())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ('extra', 'code'),
    [
        (criterion('name', '<', 'a'), 'invalid_filter'),
        ({'search': {'value': '\t'}}, 'invalid_character'),
        ({'search': {'value': 'a' * 256}}, 'value_too_long'),
        ({'search': {'value': ' '.join(['w'] * 17)}}, 'too_many_words'),
        (criterion('made', '=', '2024-13-01'), 'invalid_date'),
        (criterion('price', '=', 'abc'), 'invalid_number'),
        ({'start': 4, 'length': 3}, 'page_out_of_range'),
    ],
)
def test_a_user_error_of_each_code_answers_http_200_with_its_code(extra: dict[str, Any], code: str):
    """SPEC-A11.2: one case per code; the dependency takes no option that changes the status."""
    columns = [{'data': name, 'searchable': True, 'orderable': True} for name in ('name', 'made', 'price')]
    response = client.post('/docs', json={'draw': 3, 'columns': columns, **extra})
    assert response.status_code == 200
    assert (response.json()['draw'], response.json()['error_code']) == (3, code)


wide = SearchTable('docs', [Column(f't{number}', field='title') for number in range(61)], unique_key='t0')


@app.post('/wide')
def answer_wide(params: DataTablesParams) -> DTResponse:
    return wide.fetch_sync(params, no_server)


def test_a_request_over_the_budget_answers_http_200_with_its_code():
    """SPEC-A11.3, PRD-A11, PRD-C17: 16 words over 61 columns are 976 conditions. It catches an adapter that answers
    a code it does not know with another status, which no other case here shows: the code is new."""
    columns = [{'data': f't{number}', 'searchable': True, 'orderable': True} for number in range(61)]
    body = {'draw': 4, 'columns': columns, 'search': {'value': ' '.join(f'w{number}' for number in range(16))}}
    response = client.post('/wide', json=body)
    assert response.status_code == 200
    assert (response.json()['draw'], response.json()['error_code']) == (4, 'request_too_large')
