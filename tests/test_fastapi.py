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


def test_a_malformed_request_answers_422_naming_only_the_parameter():
    response = client.post('/table', json={'start': 'my-secret'})
    assert response.status_code == 422
    assert response.json() == {'detail': 'Invalid start parameter.'}


def test_a_body_that_is_not_json_answers_422():
    response = client.post('/table', content=b'{not json', headers={'content-type': 'application/json'})
    assert (response.status_code, response.json()) == (422, {'detail': 'The table request is malformed.'})
