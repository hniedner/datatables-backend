"""Reading a DataTables request: both encodings DataTables sends, and every way a request is refused."""

import logging
from typing import Any

import pytest

from datatables_backend import DTColumn
from datatables_backend import DTOrder
from datatables_backend import DTParams
from datatables_backend import DTResponse
from datatables_backend import MalformedRequest
from datatables_backend import Search
from datatables_backend import SearchCriterion
from datatables_backend import SearchGroup
from datatables_backend import parse_request

from .conftest import BRACKETED
from .conftest import reshaped


def test_a_json_request_is_read():
    params = parse_request(
        {
            'draw': 3,
            'start': 20,
            'length': 10,
            'search': {'value': 'ann', 'regex': False},
            'columns': [{'data': 'name', 'name': '', 'searchable': True, 'orderable': True, 'search': {'value': ''}}],
            'order': [{'column': 0, 'dir': 'desc'}],
        }
    )
    assert (params.draw, params.start, params.length, params.search.value) == (3, 20, 10, 'ann')
    assert params.columns[0].key == 'name'  # name falls back to data
    assert params.order[0].dir == 'desc'
    # SPEC-P17.1: the types a caller reads
    assert isinstance(params, DTParams)
    assert isinstance(params.columns[0], DTColumn)
    assert isinstance(params.order[0], DTOrder)
    assert isinstance(params.search, Search)


def test_the_default_form_encoding_is_read():
    """DataTables' default ``ajax`` sends flat bracket keys, every value a string."""
    params = parse_request(
        [
            ('draw', '2'),
            ('start', '0'),
            ('length', '25'),
            ('columns[0][data]', 'name'),
            ('columns[0][searchable]', 'false'),
            ('columns[0][orderable]', 'true'),
            ('columns[0][search][value]', 'x'),
            ('columns[0][search][regex]', 'false'),
            ('columns[1][data]', 'age'),
            ('order[0][column]', '1'),
            ('order[0][dir]', 'asc'),
            ('searchBuilder[logic]', 'OR'),
            ('searchBuilder[criteria][0][condition]', 'between'),
            ('searchBuilder[criteria][0][origData]', 'age'),
            ('searchBuilder[criteria][0][value][]', '1'),
            ('searchBuilder[criteria][0][value][]', '9'),
        ]
    )
    assert params.length == 25
    assert params.columns[0].searchable is False
    assert params.columns[0].search.value == 'x'
    assert params.columns[1].key == 'age'
    assert params.searchBuilder is not None
    criterion = params.searchBuilder.criteria[0]
    assert isinstance(criterion, SearchCriterion)
    assert criterion.value == ['1', '9']


def test_a_searchbuilder_tree_keeps_groups_and_criteria_apart():
    params = parse_request(
        {
            'searchBuilder': {
                'logic': 'AND',
                'criteria': [
                    {'condition': '=', 'origData': 'name', 'type': 'string', 'value': ['a']},
                    {'logic': 'OR', 'criteria': [{'condition': 'null', 'origData': 'email'}]},
                ],
            }
        }
    )
    assert params.searchBuilder is not None
    first, second = params.searchBuilder.criteria
    assert isinstance(first, SearchCriterion)
    assert isinstance(second, SearchGroup)
    assert isinstance(second.criteria[0], SearchCriterion)  # SPEC-P17.1: a tree of groups and criteria


@pytest.mark.parametrize(
    ('body', 'message'),
    [
        ({'length': -1}, 'length'),  # DataTables' "All"
        ({'length': 1001}, 'at most 1000'),
        ({'start': -5}, 'start'),
        ({'start': -1}, 'start'),  # SPEC-P9.1
        ({'start': 2**53}, 'start'),  # SPEC-P10.1: one above the largest start
        ({'start': 'ten'}, 'start'),
        ({'draw': -1}, 'draw'),  # SPEC-P9.2
        # SPEC-P8.2: a JSON boolean is no integer, in each of the four places one is read
        ({'draw': True}, 'draw'),
        ({'start': True}, 'start'),
        ({'start': False}, 'start'),  # which would read as 0, the first page
        ({'length': True}, 'length'),
        ({'columns': [{'data': 'a'}, {'data': 'b'}], 'order': [{'column': True, 'dir': 'asc'}]}, 'order'),
        ({'columns': [{'data': 'a'}], 'order': [{'column': 0, 'dir': 'sideways'}]}, 'order'),
        # SPEC-P6.5: exactly `asc` or `desc`
        *[
            ({'columns': [{'data': 'a'}], 'order': [{'column': 0, 'dir': wrong}]}, 'order')
            for wrong in ('up', 'ASC', '')
        ],
        ({'columns': 'name'}, 'columns'),
        # SPEC-P6.4: a condition that is none of SearchBuilder's
        *[
            ({'searchBuilder': {'criteria': [{'condition': wrong, 'origData': 'a'}]}}, 'searchBuilder')
            for wrong in ('like', 'without', 'regex')
        ],
    ],
)
def test_a_malformed_request_names_the_parameter(body: dict[str, Any], message: str):
    with pytest.raises(MalformedRequest, match=message):
        parse_request(body)


ONE_COLUMN = [{'data': 'a'}, {'data': 'b'}]
#: Where each integer is read: its request body, and the parameter a refusal names
INTEGER_PLACES = {
    'draw': (lambda value: {'draw': value}, 'draw'),
    'start': (lambda value: {'start': value}, 'start'),
    'length': (lambda value: {'length': value}, 'length'),
    'order': (lambda value: {'columns': ONE_COLUMN, 'order': [{'column': value, 'dir': 'asc'}]}, 'order'),
}


def as_pairs(body: dict[str, Any]) -> list[tuple[str, str]]:
    """The same body as the form DataTables posts: bracket keys, every value its text (dataTables.mjs:884-890)."""

    def walk(value: Any, key: str) -> list[tuple[str, str]]:
        if isinstance(value, dict):
            return [pair for name, item in value.items() for pair in walk(item, f'{key}[{name}]' if key else name)]
        if isinstance(value, list):
            return [pair for index, item in enumerate(value) for pair in walk(item, f'{key}[{index}]')]
        return [(key, '' if value is None else str(value).lower() if isinstance(value, bool) else str(value))]

    return walk(body, '')


@pytest.mark.parametrize('place', INTEGER_PLACES)
@pytest.mark.parametrize('value', ['1.0', ' 1', '+1', '1e0', '\u0661', 1.0, 1.5], ids=repr)
def test_an_integer_that_is_not_a_whole_number_in_digits_is_malformed(place: str, value: Any):
    """SPEC-P8.1: a fraction, even a zero one, a space, a plus sign, an exponent and non-ASCII digits are refused, as
    JSON and as form text; DataTables writes `String(n)`, so none of them is something it sends."""
    build, parameter = INTEGER_PLACES[place]
    body = build(value)
    for encoded in [body] + ([as_pairs(body)] if isinstance(value, str) else []):
        with pytest.raises(MalformedRequest) as refused:
            parse_request(encoded)
        assert refused.value.parameter == parameter


@pytest.mark.parametrize('place', INTEGER_PLACES)
@pytest.mark.parametrize('value', [1, '1', '01'], ids=repr)
def test_an_integer_as_a_number_or_as_digits_is_read(place: str, value: Any):
    """SPEC-P8.3: what DataTables writes (a JSON integer, or digit text in a form), and leading zeros, which buy nothing
    to refuse."""
    build, _ = INTEGER_PLACES[place]
    body = build(value)
    assert parse_request(body) == parse_request(as_pairs(body))


#: Where each boolean is read, and the parameter a refusal names
BOOLEAN_PLACES = {
    'searchable': (lambda value: {'columns': [{'data': 'a', 'searchable': value}]}, 'columns'),
    'orderable': (lambda value: {'columns': [{'data': 'a', 'orderable': value}]}, 'columns'),
    'column regex': (lambda value: {'columns': [{'data': 'a', 'search': {'value': '', 'regex': value}}]}, 'columns'),
    'global regex': (lambda value: {'search': {'value': '', 'regex': value}}, 'search'),
}


@pytest.mark.parametrize('place', BOOLEAN_PLACES)
@pytest.mark.parametrize(
    'value', ['True', 'TRUE', 'yes', 'no', 'on', 'off', '1', '0', 't', 'y', '', 1, 0, 2, None], ids=repr
)
def test_a_boolean_that_is_not_true_or_false_is_malformed(place: str, value: Any):
    """SPEC-P14.1: a JSON boolean, or exactly the text `true` or `false` that DataTables writes for one; the other
    spellings a lax reader takes are not what it sends."""
    build, parameter = BOOLEAN_PLACES[place]
    body = build(value)
    for encoded in [body] + ([as_pairs(body)] if isinstance(value, str) else []):
        with pytest.raises(MalformedRequest) as refused:
            parse_request(encoded)
        assert refused.value.parameter == parameter


@pytest.mark.parametrize('place', BOOLEAN_PLACES)
@pytest.mark.parametrize('value', [True, False, 'true', 'false'], ids=repr)
def test_a_boolean_as_json_or_as_its_text_is_read(place: str, value: Any):
    build, _ = BOOLEAN_PLACES[place]
    body = build(value)
    assert parse_request(body) == parse_request(as_pairs(body))


def test_an_integer_data_is_the_text_of_the_integer():
    """SPEC-P20.1: DataTables' default `data` is the column's index (dataTables.mjs:7923), which a JSON body carries as
    a number."""
    params = parse_request({'draw': 1, 'columns': [{'data': 0, 'name': ''}, {'data': 1, 'name': 'age'}]})
    assert [column.data for column in params.columns] == ['0', '1']
    assert [column.key for column in params.columns] == ['0', 'age']


@pytest.mark.parametrize('data', [True, 1.5, ['a']], ids=repr)
def test_a_data_that_is_no_text_integer_object_or_null_is_malformed(data: Any):
    with pytest.raises(MalformedRequest) as refused:
        parse_request({'draw': 1, 'columns': [{'data': data}]})
    assert refused.value.parameter == 'columns'


@pytest.mark.parametrize('name, key', [('', ''), ('age', 'age')])
def test_an_object_data_is_read_in_json_and_in_a_form(name: str, key: str):
    """SPEC-P18.2: a column with orthogonal data sends an object (dataTables.mjs:5628); its identity is its name, and
    without one it is nameless."""
    body = {'draw': 1, 'columns': [{'data': {'_': 'x', 'display': 'y'}, 'name': name}]}
    for encoded in (body, as_pairs(body)):
        params = parse_request(encoded)
        assert (params.columns[0].data, params.columns[0].key) == ('', key)


def test_the_request_datatables_builds_is_read_as_json_and_as_a_form():
    """SPEC-P1.3. What `ajaxParameters` (dataTables.mjs:5618-5681) builds for three columns: the default integer `data`, a name, an
    object and null; booleans; `fixed`, `groups` and `groupsFixed` as empty arrays; an order entry with its `name`. A
    strict reader must refuse none of it, and the two encodings must read as one request."""
    sent = {
        'draw': 4,
        'columns': [
            {'data': 0, 'name': '', 'searchable': True, 'orderable': True, 'search': {'value': '', 'regex': False, 'fixed': []}},
            {'data': 'age', 'name': 'age', 'searchable': False, 'orderable': True,
             'search': {'value': 'x', 'regex': False, 'fixed': []}},
            {'data': {'_': 'a', 'display': 'b'}, 'name': '', 'searchable': True, 'orderable': False,
             'search': {'value': '', 'regex': True, 'fixed': []}},
            {'data': None, 'name': 'tag', 'searchable': True, 'orderable': True,
             'search': {'value': '', 'regex': False, 'fixed': []}},
        ],
        'order': [{'column': 1, 'dir': 'desc', 'name': 'age'}],
        'start': 20,
        'length': 10,
        'search': {'value': '', 'regex': False, 'fixed': [], 'groups': [], 'groupsFixed': []},
    }  # fmt: skip
    from_json, from_form = parse_request(sent), parse_request(as_pairs(sent))
    assert from_json == from_form
    assert [column.key for column in from_json.columns] == ['0', 'age', '', 'tag']
    assert (from_json.draw, from_json.start, from_json.length, from_json.order[0].column) == (4, 20, 10, 1)


def test_a_searchbuilder_criterion_with_value1_and_value2_is_read_as_without_them():
    """SPEC-P4.2: SearchBuilder sends `value1` and `value2` beside `value`; they are unknown fields (PRD-P4)."""
    criterion = {'condition': 'between', 'origData': 'age', 'type': 'num', 'value': ['1', '9']}
    plain = parse_request({'searchBuilder': {'logic': 'AND', 'criteria': [criterion]}})
    extra = {**criterion, 'value1': '1', 'value2': '9'}
    assert parse_request({'searchBuilder': {'logic': 'AND', 'criteria': [extra]}}) == plain


@pytest.mark.parametrize(
    ('body', 'message'),
    [
        ({'draw': 'x'}, 'Invalid draw parameter: send a whole number, as DataTables does.'),
        ({'draw': -1}, 'Invalid draw parameter: send a whole number from 0 to 2147483647.'),
        ({'start': 1.5}, 'Invalid start parameter: send a whole number, as DataTables does.'),
        ({'start': -5}, 'Invalid start parameter: send a whole number from 0 to 9007199254740991.'),
        ({'length': '10.0'}, 'Invalid length parameter: send a whole number, as DataTables does.'),
        ({'length': -1}, 'Invalid length parameter: send a whole number from 1 to 2147483647.'),
        (
            {'columns': [{'data': 'a'}, {'data': 'b'}], 'order': [{'column': 'x', 'dir': 'asc'}]},
            'Invalid order parameter: column must be a whole number.',
        ),
        (
            {'columns': [{'data': 'a'}], 'order': [{'column': 0, 'dir': 'up'}]},
            'Invalid order parameter: dir must be asc or desc.',
        ),
        (
            {'columns': [{'data': 'a', 'searchable': 'yes'}]},
            'Invalid columns parameter: searchable must be true or false.',
        ),
        (
            {'columns': [{'data': 'a', 'orderable': 1}]},
            'Invalid columns parameter: orderable must be true or false.',
        ),
        (
            {'columns': [{'data': 'a', 'search': {'regex': 'on'}}]},
            'Invalid columns parameter: regex must be true or false.',
        ),
        ({'search': {'regex': 'on'}}, 'Invalid search parameter: regex must be true or false.'),
        (
            {'columns': [{'data': True}]},
            'Invalid columns parameter: data must be text, a whole number, an object or null.',
        ),
        ({'columns': 'name'}, 'Invalid columns parameter.'),
    ],
)
def test_a_malformed_request_says_what_to_send_and_never_shows_the_value(body: dict[str, Any], message: str):
    """SPEC-P12.2, the rule that an error says how to fix it: the parameter, what it must be, and the field inside
    it where that is one of the protocol's own (never a key or a value of the client)."""
    with pytest.raises(MalformedRequest) as refused:
        parse_request(body)
    assert str(refused.value) == message


@pytest.mark.parametrize('index', [1, -1])
def test_an_order_on_a_column_the_request_does_not_hold_is_malformed(index: int):
    """SPEC-O1.1, SPEC-P12.1: only an integration bug sends one, so it is not answered as the user's mistake. The
    request holds one column, index 0."""
    with pytest.raises(MalformedRequest) as refused:
        parse_request({'draw': 1, 'columns': [{'data': 'a'}], 'order': [{'column': index, 'dir': 'asc'}]})
    assert str(refused.value) == ("Invalid order parameter: column must be the index of one of the request's columns.")


@pytest.mark.parametrize('request_data', [[{'draw': 1}], [1], 7])
def test_a_top_level_value_that_is_neither_an_object_nor_pairs_is_malformed(request_data: Any):
    """SPEC-P6.1: a decoded JSON list of objects or of numbers, or a bare number, is refused as a request, not left to
    fail with whatever error reading it as pairs gives."""
    with pytest.raises(MalformedRequest):
        parse_request(request_data)


def deeply_nested_json() -> dict[str, Any]:
    tree: dict[str, Any] = {'criteria': []}
    for _ in range(10_000):
        tree = {'criteria': [tree]}
    return {'draw': 9, 'searchBuilder': tree}


@pytest.mark.parametrize(
    'request_data',
    [[('draw', '9'), ('columns' + '[a]' * 3000, 'x')], deeply_nested_json()],
    ids=['flat-key', 'json'],
)
def test_a_request_nested_deeper_than_it_can_be_walked_is_malformed(request_data: Any):
    """PRD-P6, SPEC-F38.4: it is refused like any other structure that cannot be read, with its draw, and never
    surfaces as a `RecursionError`."""
    with pytest.raises(MalformedRequest) as refused:
        parse_request(request_data)
    assert refused.value.draw == 9


@pytest.mark.parametrize(
    ('request_data', 'draw'),
    [
        ({'draw': '3', 'length': 'abc'}, 3),  # SPEC-E6.1: refused while the model is validated
        ([('draw', '4'), ('columns[0]x', 'a')], 4),  # refused before that, while the flat keys are read
        (iter([('draw', '5'), ('length', 'abc')]), 5),  # pairs that can be read only once
        ({'draw': ['6'], 'columns[0][data]': ['a'], 'length': ['abc']}, 6),  # urllib's parse_qs: every value a list
        ({'draw': 'x'}, None),  # SPEC-E6.2: the draw itself is the malformed part
        ({'draw': 2147483648}, None),  # SPEC-E6.2, SPEC-P10.2: one above the largest draw
        ({'length': 'abc'}, None),  # no draw sent
        ([('draw', '1'), ('draw', '2')], None),  # two draws: neither is the request's
    ],
)
def test_a_malformed_request_carries_the_draw_it_could_read(request_data: Any, draw: int | None):
    with pytest.raises(MalformedRequest) as refused:
        parse_request(request_data)
    assert refused.value.draw == draw


@pytest.mark.parametrize(
    ('parameter', 'value'), [('draw', 2147483647), ('draw', 0), ('start', 0), ('start', 2**53 - 1)]
)
def test_a_paging_number_at_its_bound_is_read(parameter: str, value: int):
    """SPEC-P10.2's and SPEC-P10.1's other half, and SPEC-P9.1's and SPEC-P9.2's: a bound refuses nothing a client
    sends."""
    assert getattr(parse_request({parameter: value}), parameter) == value


def warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The event of each record, after checking that each is a WARNING of the library's logger (SPEC-E12.1)."""
    assert all((record.name, record.levelname) == ('datatables_backend', 'WARNING') for record in caplog.records), (
        caplog.records
    )
    return [record.msg for record in caplog.records]


@pytest.mark.parametrize(
    ('request_data', 'event'),
    [
        ({'draw': 2, 'start': 'ten'}, 'datatables_request_malformed'),
        ({'draw': 2, 'length': 1001}, 'datatables_length_refused'),
    ],
    ids=['wrong-type', 'length-limit'],
)
def test_a_malformed_request_is_logged_exactly_once(caplog: pytest.LogCaptureFixture, request_data: Any, event: str):
    """SPEC-E13.1: one WARNING per refusal, under the event of its case. Every refusal is logged in one place, so
    these two stand for all of them."""
    with caplog.at_level(logging.DEBUG), pytest.raises(MalformedRequest):
        parse_request(request_data)
    assert warnings(caplog) == [event]


@pytest.mark.parametrize('key', ['columns[0][searchable]', 'columns.0.searchable'])
def test_a_refused_value_names_the_parameter_and_never_the_key_or_the_value(caplog: pytest.LogCaptureFixture, key: str):
    """SPEC-P12.1, SPEC-E10.1: the path below the parameter is the client's own key when it sends dotted keys."""
    with caplog.at_level(logging.DEBUG), pytest.raises(MalformedRequest) as refused:
        parse_request([('draw', '1'), (key, 'MARKER_V')])
    # The message names the protocol's own field, from a table of ours; the log record names none
    assert str(refused.value) == 'Invalid columns parameter: searchable must be true or false.'
    assert caplog.records
    logged = repr([vars(record) for record in caplog.records])
    assert 'MARKER_V' not in logged
    assert 'searchable' not in logged


def test_a_refusal_never_quotes_the_value():
    with pytest.raises(MalformedRequest) as refused:
        parse_request({'start': 'secret-value'})
    assert 'secret-value' not in str(refused.value)


@pytest.mark.parametrize(
    'body',
    [{'search': {'value': '\ud800'}}, {'columns': [{'data': '\ud800'}]}],
    ids=['search', 'column data'],
)
def test_text_no_driver_can_encode_is_refused(body: dict[str, Any]):
    """SPEC-P11.1: a lone surrogate, in the global search and in a column's `data`."""
    with pytest.raises(MalformedRequest):
        parse_request(body)


def test_the_page_length_limit_is_the_callers():
    assert parse_request({'length': 5000}, max_length=5000).length == 5000


@pytest.mark.parametrize(
    'request_data',
    [
        [('draw', '1'), ('draw', '2')],
        [('columns[0][data]', 'a'), ('columns.0.data', 'b')],
        {'columns[0][data]': 'a', 'columns.0.data': 'b'},
    ],
    ids=['same key', 'bracket and dot, pairs', 'bracket and dot, mapping'],
)
def test_a_flat_key_given_twice_is_refused(request_data: Any):
    """SPEC-P6.2: the dotted and the bracketed spelling are one key, so two values for it are refused."""
    with pytest.raises(MalformedRequest):
        parse_request(request_data)


def test_flat_indexes_with_a_gap_are_refused():
    """SPEC-P6.2: `columns[0]` and `columns[2]` with no `columns[1]`."""
    with pytest.raises(MalformedRequest):
        parse_request({'columns[0][data]': 'a', 'columns[2][data]': 'b'})


@pytest.mark.parametrize('form', ['spring', 'dots', 'mixed'])
def test_the_dotted_form_reads_as_the_bracketed_one(form: str):
    dotted = [(reshaped(key, form), value) for key, value in BRACKETED]
    assert parse_request(dotted) == parse_request(BRACKETED)
    assert parse_request(dict(dotted)) == parse_request(dict(BRACKETED))


def test_keys_a_real_client_adds_are_ignored():
    """SPEC-P3.1, SPEC-P3.2: DataTables' GET cache-buster ``_``, an app's own ``ajax.data`` keys in either notation,
    and the keys of an extension, though some have the shape of a request parameter's."""
    extras = [
        ('_', '1763550000000'),
        ('_[x]', '1'),
        ('myApp.x', '1'),
        ('myAppKey', 'x'),
        ('searchPanes_options', 'x'),
        ('searchPanesLast', 'x'),
        ('status', 'open'),
        ('filter[owner]', 'me'),
        ('app.key', 'x'),
        ('filters[user.id]', '1'),
        ('app..key', 'x'),
    ]
    assert parse_request(BRACKETED + extras) == parse_request(BRACKETED)


def test_an_app_key_beside_a_json_body_is_ignored():
    """SPEC-P3.1: in a JSON body too."""
    body = {'draw': 1, 'columns': [{'data': 'name'}], 'search': {'value': 'ann'}}
    extras = {'filter[owner]': 'me', 'app.key': 'x', '_': '1', 'myAppKey': 'x', 'searchPanesLast': 'x'}
    assert parse_request({**body, **extras}) == parse_request(body)


def test_unknown_fields_of_a_protocol_object_are_ignored():
    """SPEC-P4.1: `columns[0][foo]` and `search[smart]`, which a newer DataTables or a plug-in may send."""
    unknown = [('columns[0][foo]', 'x'), ('search[smart]', 'true')]
    assert parse_request(BRACKETED + unknown) == parse_request(BRACKETED)


@pytest.mark.parametrize(
    'extra',
    [
        ('search[fixed][0][name]', 'scope'),
        ('search[groups][0][columns][]', '1'),
        ('search[groupsFixed][0][name]', 'scope'),
        ('columns[1][search][fixed][0][term]', 'x'),
    ],
)
def test_a_fixed_or_grouped_search_is_refused(extra: tuple[str, str]):
    """SPEC-P5.1: DataTables sends these only when the page sets them; dropping them would answer fewer filters than
    asked."""
    with pytest.raises(MalformedRequest, match=r'^Invalid (search|columns) parameter\.$'):
        parse_request([*BRACKETED, extra])


def test_a_fixed_search_in_a_json_body_is_refused_and_an_empty_one_is_not():
    """SPEC-P5.1: an empty `fixed` (JSON `[]`) parses, a filled one is refused."""
    body: dict[str, Any] = {'columns': [{'data': 'name', 'search': {'value': '', 'fixed': []}}]}
    assert parse_request(body) == parse_request({'columns': [{'data': 'name'}]})
    with pytest.raises(MalformedRequest, match='search'):
        parse_request({'search': {'value': '', 'fixed': [{'name': 'scope', 'term': 'x'}]}})


@pytest.mark.parametrize(
    'key',
    [
        'columns[0][data',
        'columns[0]].data',
        'columns[0]data',
        'columns[0][name]x',
        'search..value',
        'search.value]',
        'search[value]x',
        'order.[0].dir',
        'order[0]]',
        'columns[0][search.value]',
        'search[a.b]',
        'search[value]]',
        'columns[0.x][data]',  # SPEC-P7.1
        'columns[0][data][]x',
        'columns[1234567890][data]',
    ],
)
def test_a_protocol_key_of_unknown_shape_is_refused(key: str):
    """SPEC-P7.1: each key alone over keys it cannot collide with, so only the shape rule can refuse it."""
    with pytest.raises(MalformedRequest, match=r'^Invalid (columns|search|order) parameter\.$'):
        parse_request([('draw', '1'), (key, 'x')])


def test_a_refused_key_shape_names_the_parameter_and_never_the_key(caplog: pytest.LogCaptureFixture):
    with pytest.raises(MalformedRequest) as refused:
        parse_request([*BRACKETED, ('columns[0][name]secret', 'x')])
    assert str(refused.value) == 'Invalid columns parameter.'
    assert 'secret' not in str(refused.value)
    assert all('secret' not in str(vars(record)) for record in caplog.records)


@pytest.mark.parametrize(('key', 'digit'), [('columns[\u00b2][data]', '\u00b2'), ('columns.\u0660.data', '\u0660')])
def test_a_non_ascii_digit_index_is_refused_without_quoting_it(key: str, digit: str):
    """SPEC-P7.1: superscript two and ARABIC-INDIC DIGIT ZERO are Unicode digits, not indexes."""
    with pytest.raises(MalformedRequest) as refused:
        parse_request([(key, 'a')])
    assert digit not in str(refused.value)


@pytest.mark.parametrize(
    'pairs',
    [
        [('columns[00][data]', 'a')],
        [('columns.00.data', 'a')],
        [('columns[0][data]', 'a'), ('columns[01][data]', 'b')],
    ],
)
def test_an_index_with_a_leading_zero_is_refused(pairs: list[tuple[str, str]]):
    """SPEC-P7.1: `columns[01]` where a lenient parser would read column 1."""
    with pytest.raises(MalformedRequest, match='columns'):
        parse_request(pairs)


@pytest.mark.parametrize(
    'extra',
    [
        ('searchPanes[team][0]', 'red'),
        ('searchPanes_null[team][0]', 'true'),
        ('columns[2][columnControl][search][value]', 'red'),
        ('columns[2][columnControl][search][logic]', 'empty'),
        ('columns[2][columnControl][search][logic]', 'notEmpty'),
        ('columns[2][columnControl][list][0]', 'red'),
    ],
)
def test_a_searchpanes_or_columncontrol_filter_is_refused(extra: tuple[str, str]):
    """SPEC-P5.2, SPEC-P5.3: SearchPanes and ColumnControl filters are not answered; dropping them would answer fewer
    filters than asked."""
    with pytest.raises(MalformedRequest, match=r'^Invalid (searchPanes|searchPanes_null|columns) parameter\.$'):
        parse_request([*BRACKETED, extra])


def test_idle_searchpanes_and_columncontrol_parameters_are_accepted():
    """SPEC-P5.3: SearchPanes sends its options, and ColumnControl an empty search, on every request."""
    idle = [
        ('searchPanes_options[cascade]', 'false'),
        ('searchPanes_options[viewTotal]', 'false'),
        ('columns[2][columnControl][search][value]', ''),
        ('columns[2][columnControl][search][logic]', 'contains'),
        ('columns[2][columnControl][search][type]', 'text'),
    ]
    without_column_control = {'columns': {'__all__': {'columnControl'}}}
    assert parse_request(BRACKETED + idle).model_dump(exclude=without_column_control) == parse_request(
        BRACKETED
    ).model_dump(exclude=without_column_control)
    body: dict[str, Any] = {'columns': [{'data': 'a', 'columnControl': {'list': {}}}], 'searchPanes': {}}
    assert parse_request(body).model_dump(exclude=without_column_control) == parse_request(
        {'columns': [{'data': 'a'}]}
    ).model_dump(exclude=without_column_control)


def test_an_index_too_long_for_a_number_is_refused():
    """SPEC-P7.1: a `MalformedRequest`, not a `ValueError` from converting the index."""
    with pytest.raises(MalformedRequest, match='columns'):
        parse_request([(f'columns[{"1" * 5000}][data]', 'a')])


def test_an_idle_searchpanes_body_is_accepted_and_a_selection_is_not():
    """SPEC-P5.2: SearchPanes sends an empty pane (an object, or JSON `[]`) on every request; only a selection is a
    filter."""
    idle = {'searchPanes': {'team': {}, 'colour': []}, 'searchPanes_null': {'team': {}}}
    assert (
        parse_request({'columns': [{'data': 'a'}], **idle}).columns
        == parse_request({'columns': [{'data': 'a'}]}).columns
    )
    with pytest.raises(MalformedRequest) as refused:
        parse_request({'searchPanes': {'team': {'0': 'red'}}})
    assert str(refused.value) == 'Invalid searchPanes parameter.'


def test_a_multi_dict_keeps_every_value_of_a_repeated_key():
    """Starlette's, Werkzeug's and Django's multi-dicts give one value per key through ``.items()``."""
    from starlette.datastructures import ImmutableMultiDict

    repeated: ImmutableMultiDict[str, str] = ImmutableMultiDict(
        [
            ('searchBuilder[criteria][0][condition]', 'between'),
            ('searchBuilder[criteria][0][origData]', 'age'),
            ('searchBuilder[criteria][0][value][]', '1'),
            ('searchBuilder[criteria][0][value][]', '9'),
        ]
    )
    builder = parse_request(repeated).searchBuilder
    assert builder is not None
    criterion = builder.criteria[0]
    assert isinstance(criterion, SearchCriterion)
    assert criterion.value == ['1', '9']
    with pytest.raises(MalformedRequest):
        parse_request(ImmutableMultiDict([('search[value]', 'a'), ('search[value]', 'b')]))


@pytest.mark.parametrize(
    'request_data',
    [
        [
            ('searchBuilder[criteria][0][condition]', '='),
            ('searchBuilder[criteria][0][origData]', 'name'),
            ('searchBuilder[criteria][0][value][][x]', 'red'),
        ],
        [('searchBuilder[criteria][][condition]', '=')],
        {'searchBuilder': {'criteria': ['x']}},
    ],
)
def test_a_searchbuilder_part_of_the_wrong_kind_is_refused(request_data: Any):
    with pytest.raises(MalformedRequest):
        parse_request(request_data)


@pytest.mark.parametrize('key', ['search[value]', 'columns[0][data]', 'searchBuilder[criteria][0][value]'])
@pytest.mark.parametrize('field_first', [True, False])
def test_a_field_and_a_value_under_one_append_are_refused_in_either_order(key: str, field_first: bool):
    pairs = [(f'{key}[][x]', '1'), (f'{key}[]', '2')]
    with pytest.raises(MalformedRequest):
        parse_request(pairs if field_first else pairs[::-1])


@pytest.mark.parametrize('key', ['columns[2][columnControl][list][0]', 'columns[2][columnControl][list]'])
def test_a_columncontrol_list_refusal_names_only_the_parameter(key: str):
    """SPEC-P5.3: a ColumnControl list is a filter, which is refused."""
    with pytest.raises(MalformedRequest) as refused:
        parse_request([*BRACKETED, (key, 'red')])
    assert str(refused.value) == 'Invalid columns parameter.'


def test_an_answer_holds_only_what_datatables_reads():
    """SPEC-R5.2, SPEC-E5.1: the keys a caller sends on, for an ordinary answer (whatever else it was built with) and
    for the failure an app reports itself."""
    answer = DTResponse.model_validate({'draw': 5, 'recordsTotal': 9, 'recordsFiltered': 2, 'metadata': {'took': 3}})
    assert answer.to_dict() == {'draw': 5, 'recordsTotal': 9, 'recordsFiltered': 2, 'data': []}
    assert DTResponse.query_failed(5).to_dict() == {
        'draw': 5,
        'recordsTotal': 0,
        'recordsFiltered': 0,
        'data': [],
        'error': 'The table query failed.',
        'error_code': 'query_failed',
    }


def test_an_empty_list_for_a_single_value_key_is_refused():
    """A form decoder may give a key no value as an empty list; dropping the key would drop the filter."""
    with pytest.raises(MalformedRequest):
        parse_request({**dict(BRACKETED), 'search[value]': []})


def test_a_searchpanes_pane_that_is_not_an_object_or_list_is_refused():
    with pytest.raises(MalformedRequest) as refused:
        parse_request({'searchPanes': {'team': 0}})
    assert str(refused.value) == 'Invalid searchPanes parameter.'


def test_an_append_beside_a_field_is_refused():
    with pytest.raises(MalformedRequest):
        parse_request([('columns[0][data]', 'a'), ('columns[0][]', 'x')])


def test_a_key_repeated_in_a_multi_dict_is_refused():
    """A duplicated key in a multi-dict is refused, as in any other flat form."""
    from starlette.datastructures import ImmutableMultiDict

    duplicated: ImmutableMultiDict[str, str] = ImmutableMultiDict([('draw', '1'), ('start', '0'), ('start', '5')])
    with pytest.raises(MalformedRequest):
        parse_request(duplicated)


def test_an_empty_list_for_an_append_key_is_accepted():
    params = parse_request(
        {
            'searchBuilder[criteria][0][condition]': 'null',
            'searchBuilder[criteria][0][origData]': 'name',
            'searchBuilder[criteria][0][value][]': [],
        }
    )
    assert params.searchBuilder is not None
    criterion = params.searchBuilder.criteria[0]
    assert isinstance(criterion, SearchCriterion)
    assert criterion.value == []


@pytest.mark.parametrize(
    'request_data',
    [
        {'search': {'value': 'x'}, 'search[regex]': 'true'},  # a nested object among flat keys
        [(b'search[value]', 'x')],  # a key that is not text
        {1: 'x'},  # an int key in the mapping
        {'sEcho': '1', 'iDisplayStart': '0', 'iDisplayLength': '10', 'sSearch': 'x'},  # the legacy 1.9 form
        {},
        [],
    ],
    ids=['nested-in-flat', 'bytes-key', 'int-key', 'legacy', 'empty-mapping', 'empty-pairs'],
)
def test_a_request_that_is_not_a_datatables_request_is_refused(request_data: Any):
    """SPEC-P6.3: a flat value that is a dict, or a key that is not text; SPEC-P19.1: an empty request and DataTables
    1.9's."""
    with pytest.raises(MalformedRequest):
        parse_request(request_data)


def test_the_base_request_reads_alike_in_every_form():
    """SPEC-P1.1: as nested JSON, as a mapping of flat keys, as a multi-dict and as a list of pairs."""
    from starlette.datastructures import ImmutableMultiDict

    body: dict[str, Any] = {
        'draw': 1,
        'start': 0,
        'length': 10,
        'columns': [{'data': 'name', 'name': '', 'searchable': True, 'orderable': True, 'search': {'value': '', 'regex': False}}],
        'search': {'value': '', 'regex': False},
    }  # fmt: skip
    pairs = as_pairs(body)
    assert parse_request(dict(pairs)) == parse_request(ImmutableMultiDict(pairs)) == parse_request(pairs)
    assert parse_request(body) == parse_request(pairs)


def test_a_repeated_multi_values_key_adds_its_values():
    """SPEC-P1.2, SPEC-P7.2: a multi-dict and a pair list repeating `columns[0][search][multi_values][]` read both
    values, in order; `[]` in place of an index is refused."""
    from starlette.datastructures import ImmutableMultiDict

    pairs = [
        ('columns[0][data]', 'colour'),
        ('columns[0][search][multi_values][]', 'red'),
        ('columns[0][search][multi_values][]', 'blue'),
    ]
    assert parse_request(pairs).columns[0].search.multi_values == ['red', 'blue']
    assert parse_request(ImmutableMultiDict(pairs)).columns[0].search.multi_values == ['red', 'blue']
    with pytest.raises(MalformedRequest):
        parse_request([('columns[0][data]', 'a'), ('columns[][data]', 'x')])


def test_the_four_spellings_of_a_nested_key_are_one_request():
    """SPEC-P2.1."""
    spellings = [
        'columns[0][search][value]',
        'columns[0].search.value',
        'columns.0.search.value',
        'columns[0].search[value]',
    ]
    parsed = [parse_request([('columns[0][data]', 'a'), (key, 'x')]) for key in spellings]
    assert parsed[0].columns[0].search.value == 'x'
    assert all(other == parsed[0] for other in parsed)


@pytest.mark.parametrize('length', [1, 1000])
def test_a_length_from_one_to_the_limit_is_read(length: int):
    """SPEC-P13.1."""
    assert parse_request({'length': length}).length == length


@pytest.mark.parametrize(('length', 'limit'), [(0, 1000), (-1, 1000), (1001, 1000), (51, 50)])
def test_a_length_outside_one_to_the_limit_is_refused(length: int, limit: int):
    """SPEC-P13.1: the limit is the caller's `max_length`, 1000 by default."""
    with pytest.raises(MalformedRequest) as refused:
        parse_request({'length': length}, max_length=limit)
    assert refused.value.parameter == 'length'


def test_a_response_holds_exactly_the_fields_datatables_and_the_error_use():
    """SPEC-R5.1: a field added to the model would reach every client; a field dropped would break one."""
    assert list(DTResponse.model_fields) == ['draw', 'recordsTotal', 'recordsFiltered', 'data', 'error', 'error_code']
