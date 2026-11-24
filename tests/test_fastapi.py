"""The FastAPI dependency: every encoding DataTables can send, and a 422 that never quotes the request."""

from collections.abc import MutableMapping
import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from datatables_backend import Column
from datatables_backend import DTResponse
from datatables_backend.fastapi import DataTablesParams
from datatables_backend.opensearch import SearchTable
from datatables_backend.sqlalchemy import SQLTable

from .conftest import criterion
from .conftest import people

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


@app.api_route('/echo', methods=['GET', 'POST', 'PUT'])
def echo(params: DataTablesParams) -> dict[str, Any]:
    return {'draw': params.draw, 'search': params.search.value}


#: The request with `draw` 2 and the search `body`, and the one the query string must never contribute
SENT = {'draw': 2, 'search': {'value': 'body', 'regex': False}}
AS_JSON = json.dumps(SENT)
AS_FORM = 'draw=2&search%5Bvalue%5D=body&search%5Bregex%5D=false'
IN_THE_QUERY = 'draw=1&search%5Bvalue%5D=query&search%5Bregex%5D=false'
#: What `submitAs: 'json'` alone sends: DataTables' default Content-Type is the form's, and it sets JSON only if none is
#: set (dataTables.mjs:621, :643-645), so the JSON body travels under this one
FORM_TYPE = 'application/x-www-form-urlencoded; charset=UTF-8'
BODY_READ = {'draw': 2, 'search': 'body'}
NO_PARAMETER = 'The table request holds no DataTables parameter: send them as a JSON object or as a form.'
MALFORMED = 'The table request is malformed: send it as a JSON object or as a form.'


@pytest.mark.parametrize(
    'content_type',
    ['application/json', 'application/json; charset=utf-8', FORM_TYPE, 'text/plain', ''],
    ids=['json', 'json-charset', 'form-type', 'plain', 'none'],
)
def test_a_json_object_is_read_whatever_its_content_type(content_type: str):
    """SPEC-A9.2, SPEC-A9.4: DataTables' `submitAs: 'json'` keeps the form Content-Type unless the page sets one."""
    response = client.post('/echo', content=AS_JSON, headers={'content-type': content_type})
    assert (response.status_code, response.json()) == (200, BODY_READ)


@pytest.mark.parametrize(
    'content_type',
    ['application/x-www-form-urlencoded', FORM_TYPE, 'text/plain', ''],
    ids=['form', 'form-charset', 'plain', 'none'],
)
def test_a_form_body_is_read_whatever_its_content_type(content_type: str):
    """SPEC-A9.5: any body that is no JSON object is read as a form."""
    response = client.post('/echo', content=AS_FORM, headers={'content-type': content_type})
    assert (response.status_code, response.json()) == (200, BODY_READ)


def test_a_multipart_form_is_read():
    """SPEC-A9.5: a body under `multipart/form-data` goes through Starlette's parser, a file field beside it ignored."""
    response = client.post(
        '/echo', data={'draw': '2', 'search[value]': 'body'}, files={'upload': ('a.txt', b'x', 'text/plain')}
    )
    assert (response.status_code, response.json()) == (200, BODY_READ)


@pytest.mark.parametrize('body', [AS_JSON, AS_FORM], ids=['json', 'form'])
def test_the_body_is_read_and_the_query_string_is_never_merged_into_it(body: str):
    """SPEC-A9.1: DataTables appends its cache-buster `_` to the query of a POST; a merge would also apply the query's
    search, `query`, and empty the page. The `_` of DataTables' own request is in the query here."""
    response = client.post(f'/echo?_=1700&{IN_THE_QUERY}', content=body, headers={'content-type': FORM_TYPE})
    assert (response.status_code, response.json()) == (200, BODY_READ)


@pytest.mark.parametrize('method', ['GET', 'POST', 'PUT'])
@pytest.mark.parametrize('body', [b'', b' \n '], ids=['empty', 'white-space'])
def test_the_query_string_is_read_when_there_is_no_body(method: str, body: bytes):
    """SPEC-A9.3: for every method, and a body of white space is none."""
    response = client.request(method, f'/echo?{IN_THE_QUERY}', content=body)
    assert (response.status_code, response.json()) == (200, {'draw': 1, 'search': 'query'})


@pytest.mark.parametrize(
    'content_type', ['application/json', FORM_TYPE, 'text/plain', ''], ids=['json', 'form', 'plain', 'none']
)
@pytest.mark.parametrize(
    'body', ['{not json', '[1]', '"x"', 'null', '{"x": 1}', '{"draw":', '[["draw", "1"], ["length", "5"]]'], ids=repr
)
def test_a_body_that_is_no_json_object_and_no_form_answers_422_that_says_what_to_send(content_type: str, body: str):
    """SPEC-A9.6: it falls to the form rule, which finds no DataTables parameter."""
    response = client.post('/echo', content=body, headers={'content-type': content_type})
    assert (response.status_code, response.json()) == (422, {'detail': {'message': NO_PARAMETER}})


@pytest.mark.parametrize('content_type', ['application/json', FORM_TYPE, 'text/plain'], ids=['json', 'form', 'plain'])
@pytest.mark.parametrize(
    ('body', 'message'),
    [
        ('[' * 700_000, NO_PARAMETER),
        ('{"a":' * 150_000, NO_PARAMETER),
        # This one decodes on some Pythons and not on others, so the message is not compared
        ('{"draw": 1, "x": ' + '[' * 300_000 + ']' * 300_000 + '}', None),
    ],
    ids=['open-list', 'open-object', 'closed'],
)
def test_a_body_nested_deeper_than_it_can_be_decoded_answers_422_never_500(
    content_type: str, body: str, message: str | None
):
    """SPEC-A9.7: the decoder's `RecursionError` is a refusal, not a server error a client can cause; a body that does
    not decode falls to the form rule. Each body is under the 1 MB Starlette allows a form field."""
    response = TestClient(app, raise_server_exceptions=False).post(
        '/echo', content=body, headers={'content-type': content_type}
    )
    assert response.status_code == 422
    if message:
        assert response.json() == {'detail': {'message': message}}


def test_a_body_of_several_megabytes_nested_deeper_than_it_can_be_decoded_answers_422_never_500():
    """SPEC-A9.7 for a body Starlette's form parser would refuse (HTTP 400): under a JSON Content-Type there is none."""
    response = TestClient(app, raise_server_exceptions=False).post(
        '/echo', content='{"a":' * 1_000_000, headers={'content-type': 'application/json'}
    )
    assert response.status_code == 422


def test_a_form_of_more_fields_than_the_limit_is_refused_under_a_plain_content_type():
    """SPEC-A9.8: the 1,000 fields Starlette allows a form, for the form read without its parser too."""
    body = 'draw=2&' + '&'.join(f'columns[{i}][data]=c{i}' for i in range(1001))
    response = client.post('/echo', content=body, headers={'content-type': 'text/plain'})
    assert (response.status_code, response.json()) == (422, {'detail': {'message': MALFORMED}})


def test_a_json_body_over_a_megabyte_is_read_under_a_form_content_type():
    """SPEC-A9.4: Starlette's form parser refuses a field over 1 MB (HTTP 400); a JSON body never reaches it."""
    body = json.dumps({'draw': 2, 'columns': [{'data': f'column-{i}'} for i in range(60_000)]})
    assert len(body) > 1_048_576
    response = client.post('/echo', content=body, headers={'content-type': FORM_TYPE})
    assert (response.status_code, response.json()) == (200, {'draw': 2, 'search': ''})


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


def test_a_user_error_over_sql_answers_http_200_with_the_whole_body(session: Session):
    """SPEC-A11.1: `=` `abc` on `age` is `invalid_number`, and the body is the 200 answer a client reads."""
    sql_app = FastAPI()
    people_table = SQLTable(people, [Column('id'), Column('age')])

    @sql_app.post('/people')
    def answer_people(params: DataTablesParams) -> DTResponse:
        return people_table.fetch_sync(params, session)

    columns = [{'data': name, 'searchable': True, 'orderable': True} for name in ('id', 'age')]
    response = TestClient(sql_app).post('/people', json={'draw': 3, 'columns': columns, **criterion('age', '=', 'abc')})
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body['error'], str) and body['error']
    assert body == {
        'draw': 3,
        'recordsTotal': 0,
        'recordsFiltered': 0,
        'data': [],
        'error': body['error'],
        'error_code': 'invalid_number',
    }


async def _answer_to_a_client_that_goes_away(messages: list[dict[str, Any]]) -> list[MutableMapping[str, Any]]:
    """The ASGI messages `app` sends for a POST whose client sends ``messages`` and then disconnects. Nothing is
    caught here: an exception that leaves the app fails the caller."""
    scope = {
        'type': 'http',
        'asgi': {'version': '3.0'},
        'http_version': '1.1',
        'method': 'POST',
        'scheme': 'http',
        'path': '/table',
        'raw_path': b'/table',
        'query_string': b'',
        'headers': [(b'content-type', b'application/json')],
        'client': ('testclient', 50000),
        'server': ('testserver', 80),
    }
    pending = iter(messages)
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        return next(pending, {'type': 'http.disconnect'})

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


@pytest.mark.parametrize(
    'messages',
    [
        [],
        [{'type': 'http.request', 'body': b'{"length": 5, "columns": [{"da', 'more_body': True}],
    ],
    ids=['before the body', 'in the middle of the body'],
)
async def test_a_client_that_disconnects_while_the_body_is_read_is_answered_400_and_raises_nothing(
    messages: list[dict[str, Any]],
):
    """SPEC-A9.9: a disconnect is neither the integrator's nor the user's mistake, so it is no 422 (PRD-E6), and no
    exception leaves the dependency for the server to log as an unhandled error. The answer is HTTP 400 with a text
    that names no value and says what to do; its `detail` is that text alone, with no `draw`, since the body was never
    read (unlike the 422, PRD-A10)."""
    sent = await _answer_to_a_client_that_goes_away(messages)
    assert sent[0]['type'] == 'http.response.start'
    assert sent[0]['status'] == 400
    body = b''.join(message.get('body', b'') for message in sent[1:])
    assert json.loads(body) == {
        'detail': 'The request body could not be read: the client disconnected; send the request again.'
    }
