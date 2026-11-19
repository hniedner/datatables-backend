"""Reading a DataTables request: both encodings DataTables sends, and every way a request is refused."""

from typing import Any

import pytest

from datatables_backend import MalformedRequest
from datatables_backend import SearchCriterion
from datatables_backend import SearchGroup
from datatables_backend import parse_request


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
    assert isinstance(second.criteria[0], SearchCriterion)


@pytest.mark.parametrize(
    ('body', 'message'),
    [
        ({'length': -1}, 'length'),  # DataTables' "All"
        ({'length': 1001}, 'at most 1000'),
        ({'start': -5}, 'start'),
        ({'start': 2**31}, 'start'),
        ({'start': 'ten'}, 'start'),
        ({'order': [{'column': 0, 'dir': 'sideways'}]}, 'order.0.dir'),
        ({'columns': 'name'}, 'columns'),
        ({'searchBuilder': {'criteria': [{'condition': 'like', 'origData': 'a'}]}}, 'searchBuilder'),
    ],
)
def test_a_malformed_request_names_the_parameter(body: dict[str, Any], message: str):
    with pytest.raises(MalformedRequest, match=message):
        parse_request(body)


def test_a_refusal_never_quotes_the_value():
    with pytest.raises(MalformedRequest) as refused:
        parse_request({'start': 'secret-value'})
    assert 'secret-value' not in str(refused.value)


def test_text_no_driver_can_encode_is_refused():
    with pytest.raises(MalformedRequest):
        parse_request({'search': {'value': '\ud800'}})


def test_the_page_length_limit_is_the_callers():
    assert parse_request({'length': 5000}, max_length=5000).length == 5000


def test_a_flat_key_given_twice_is_refused():
    with pytest.raises(MalformedRequest):
        parse_request([('draw', '1'), ('draw', '2')])


def test_flat_indexes_with_a_gap_are_refused():
    with pytest.raises(MalformedRequest):
        parse_request({'columns[0][data]': 'a', 'columns[2][data]': 'b'})


BRACKETED = [
    ('draw', '4'),
    ('start', '0'),
    ('length', '10'),
    ('columns[0][data]', 'id'),
    ('columns[1][data]', 'name'),
    ('columns[1][search][value]', 'ann'),
    ('columns[1][search][regex]', 'false'),
    ('columns[2][data]', 'team'),
    ('columns[2][orderable]', 'false'),
    ('order[0][column]', '0'),
    ('order[0][dir]', 'desc'),
    ('order[0][name]', ''),
    ('search[value]', 'r'),
    ('search[regex]', 'false'),
]


def reshaped(key: str, form: str) -> str:
    """``columns[1][search][value]`` as ``columns[1].search.value`` (spring) or ``columns.1.search.value`` (dots)."""
    head, *parts = key.replace(']', '').split('[')
    if form == 'spring':  # spring-data-jpa-datatables' flatten(): indexes stay bracketed, names are dotted
        return head + ''.join(f'[{part}]' if part.isdigit() else f'.{part}' for part in parts)
    return '.'.join([head, *parts])


@pytest.mark.parametrize('form', ['spring', 'dots'])
def test_the_dotted_form_reads_as_the_bracketed_one(form: str):
    dotted = [(reshaped(key, form), value) for key, value in BRACKETED]
    assert parse_request(dotted) == parse_request(BRACKETED)
    assert parse_request(dict(dotted)) == parse_request(dict(BRACKETED))


def test_dotted_column_names_are_read():
    params = parse_request({'draw': '1', 'start': '0', 'length': '10', 'columns.0.data': 'a'})
    assert [column.key for column in params.columns] == ['a']


def test_keys_a_real_client_adds_are_ignored():
    """DataTables' GET cache-buster ``_``, its fixed and grouped searches, and an app's own ``ajax.data`` keys."""
    extras = [
        ('_', '1763550000000'),
        ('search[fixed][0][name]', 'scope'),
        ('search[fixed][0][term]', 'x'),
        ('search[groups][0][columns][]', '1'),
        ('columns[1][search][fixed][0][name]', 'scope'),
        ('status', 'open'),
        ('filter[owner]', 'me'),
    ]
    assert parse_request(BRACKETED + extras) == parse_request(BRACKETED)


@pytest.mark.parametrize(
    'key', ['columns[0][data', 'columns[0]].data', 'columns[0]data', 'search..value', 'order.[0].dir', 'search.']
)
def test_a_protocol_key_of_unknown_shape_is_refused(key: str):
    with pytest.raises(MalformedRequest):
        parse_request([*BRACKETED, (key, 'x')])
