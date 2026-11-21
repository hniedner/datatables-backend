"""The FastAPI dependency: every encoding DataTables can send, and a 422 that never quotes the request."""

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from datatables_backend.fastapi import DataTablesParams

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
    assert response.json() == {'detail': {'message': 'Invalid length parameter.', 'draw': 5}}


def test_a_422_leaves_out_a_draw_that_could_not_be_read():
    """SPEC-A10.1: no `draw` key at all, rather than a null or a guess."""
    response = client.post('/table', data={'draw': 'x', 'length': '10'})
    assert (response.status_code, response.json()) == (422, {'detail': {'message': 'Invalid draw parameter.'}})


def test_a_body_that_is_not_json_answers_422():
    response = client.post('/table', content=b'{not json', headers={'content-type': 'application/json'})
    assert (response.status_code, response.json()) == (422, {'detail': {'message': 'The table request is malformed.'}})


def test_a_json_body_that_is_not_an_object_answers_422():
    """Also a list of pairs, which `parse_request` would read: over HTTP, JSON carries the request as an object."""
    response = client.post('/table', json=[['draw', '1'], ['length', '5']])
    assert (response.status_code, response.json()) == (422, {'detail': {'message': 'The table request is malformed.'}})
