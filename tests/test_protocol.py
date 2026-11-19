"""Reading a DataTables request: both encodings DataTables sends, and every way a request is refused."""

from typing import Any

import pytest

from datatables_backend import MalformedRequest
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


@pytest.mark.parametrize('form', ['spring', 'dots'])
def test_the_dotted_form_reads_as_the_bracketed_one(form: str):
    dotted = [(reshaped(key, form), value) for key, value in BRACKETED]
    assert parse_request(dotted) == parse_request(BRACKETED)
    assert parse_request(dict(dotted)) == parse_request(dict(BRACKETED))


def test_keys_a_real_client_adds_are_ignored():
    """DataTables' GET cache-buster ``_`` and an app's own ``ajax.data`` keys, in either notation."""
    extras = [
        ('_', '1763550000000'),
        ('status', 'open'),
        ('filter[owner]', 'me'),
        ('app.key', 'x'),
        ('filters[user.id]', '1'),
        ('app..key', 'x'),
    ]
    assert parse_request(BRACKETED + extras) == parse_request(BRACKETED)


def test_an_app_key_beside_a_json_body_is_ignored():
    body = {'draw': 1, 'columns': [{'data': 'name'}], 'search': {'value': 'ann'}}
    assert parse_request({**body, 'filter[owner]': 'me', 'app.key': 'x'}) == parse_request(body)


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
    """DataTables sends these only when the page sets them; dropping them would answer fewer filters than asked."""
    with pytest.raises(MalformedRequest, match='search'):
        parse_request([*BRACKETED, extra])


def test_a_fixed_search_in_a_json_body_is_refused_and_an_empty_one_is_not():
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
        'search]',
    ],
)
def test_a_protocol_key_of_unknown_shape_is_refused(key: str):
    """Each key alone over keys it cannot collide with, so only the shape rule can refuse it."""
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
    """Superscript two and ARABIC-INDIC DIGIT ZERO are Unicode digits, not indexes."""
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
    """SearchPanes and ColumnControl filters are not answered; dropping them would answer fewer filters than asked."""
    with pytest.raises(MalformedRequest, match=r'searchPanes|columnControl'):
        parse_request([*BRACKETED, extra])


def test_idle_searchpanes_and_columncontrol_parameters_are_accepted():
    """SearchPanes sends its options, and ColumnControl an empty search, on every request."""
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
    with pytest.raises(MalformedRequest, match='columns'):
        parse_request([(f'columns[{"1" * 5000}][data]', 'a')])


def test_an_idle_searchpanes_body_is_accepted_and_a_selection_is_not():
    """SearchPanes sends an empty object per pane on every request; only a selection is a filter."""
    idle = {'searchPanes': {'team': {}}, 'searchPanes_null': {'team': {}}}
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
    with pytest.raises(MalformedRequest) as refused:
        parse_request([*BRACKETED, (key, 'red')])
    assert str(refused.value) == 'Invalid columns.2.columnControl.list parameter.'


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
