"""The DataTables server-side protocol: the request a table sends, the answer it expects, and how to read the request.

See https://datatables.net/manual/server-side and, for the SearchBuilder tree,
https://datatables.net/extensions/searchbuilder/server-side.
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Mapping
import json
import logging
import re
from typing import Annotated
from typing import Any
from typing import Literal
from typing import cast

from pydantic import AfterValidator
from pydantic import BaseModel
from pydantic import BeforeValidator
from pydantic import ConfigDict
from pydantic import Discriminator
from pydantic import Field
from pydantic import Tag
from pydantic import TypeAdapter
from pydantic import ValidationError
from pydantic import model_validator

log = logging.getLogger('datatables_backend')

#: The default largest page a request may ask for. DataTables' "All" sends -1 and is refused.
MAX_PAGE_LENGTH = 1000
#: The largest ``draw`` and ``length``: DataTables' own counters are 32-bit. (An order index is bounded by the
#: request's own columns.)
MAX_PAGING_NUMBER = 2**31 - 1
#: The largest ``start``: the largest whole number JavaScript holds exactly, which a client may page up to.
MAX_START = 2**53 - 1

#: The codes of a mistake the user can fix; stable, since a frontend maps them to its own messages
type ErrorCode = Literal[
    'invalid_filter',
    'invalid_character',
    'value_too_long',
    'too_many_words',
    'request_too_large',
    'invalid_date',
    'invalid_number',
    'page_out_of_range',
]

type Condition = Literal[
    '=',
    '!=',
    '<',
    '<=',
    '>',
    '>=',
    'between',
    '!between',
    'contains',
    '!contains',
    'starts',
    '!starts',
    'ends',
    '!ends',
    'null',
    '!null',
]


_DIGITS = re.compile(r'-?[0-9]+')


def _whole_number(value: Any) -> Any:
    """A JSON integer, or the digits of one (ASCII, with an optional minus) as a form carries it: what DataTables
    writes for a number (``String(n)``, dataTables.mjs:884-890). Pydantic would also read ``true`` as 1, ``1.0`` and
    ``" 1"`` as 1, which would page or sort as nobody asked (PRD-P8)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _DIGITS.fullmatch(value):
        return int(value)
    raise ValueError('not a whole number')


#: A paging number or an order index: an integer, and never a JSON boolean, a fraction or a loosely written number
_Integer = Annotated[int, BeforeValidator(_whole_number)]


def _true_or_false(value: Any) -> Any:
    """A JSON boolean, or exactly the text a form carries for one: DataTables writes ``String(bool)``. Pydantic would
    also read ``yes``, ``on``, ``1`` and the like (PRD-P14)."""
    if isinstance(value, bool):
        return value
    if value in ('true', 'false'):
        return value == 'true'
    raise ValueError('not true or false')


_Boolean = Annotated[bool, BeforeValidator(_true_or_false)]


def _data(value: Any) -> Any:
    """A column's ``data``: text; an integer, which DataTables sends for a column that sets none, its index, as its
    decimal text (PRD-P20); an object (orthogonal data) or ``null``, which name no column, as empty text (PRD-P18,
    PRD-P21). A boolean, a fraction or a list is no ``data``."""
    if isinstance(value, bool):
        raise ValueError('not a data source')
    if isinstance(value, int):
        return str(value)
    return '' if value is None or isinstance(value, Mapping) else value


class _Model(BaseModel):
    model_config = ConfigDict(extra='ignore')


class Search(_Model):
    """A search value: the table's global search or one column's."""

    value: str = ''
    regex: _Boolean = False
    #: Not part of the DataTables protocol: a column's multi-select may send its values as a list here; the global
    #: search refuses one
    multi_values: list[str] | None = None
    #: DataTables' fixed and grouped searches, which the library does not answer: refused unless empty, so a client
    #: that uses them is told so instead of getting fewer filters than it asked for
    fixed: list[Any] = Field(default=[], max_length=0)
    groups: list[Any] = Field(default=[], max_length=0)
    groupsFixed: list[Any] = Field(default=[], max_length=0)


def _empty(value: Any) -> Any:
    """An empty list or object, the idle form of a filter the library does not answer; anything else is refused."""
    if not isinstance(value, (list, dict)) or value:
        raise ValueError('a filter the library does not answer')
    return cast(Any, value)


class ColumnControlSearch(_Model):
    """ColumnControl's column search, which the library does not answer: refused when active, as ColumnControl
    itself counts it (a value, or the ``empty``/``notEmpty`` logic that needs none). It sends an idle one on every
    request."""

    value: str = ''
    logic: str = ''

    @model_validator(mode='after')
    def _idle(self) -> ColumnControlSearch:
        if self.value or self.logic in ('empty', 'notEmpty'):
            raise ValueError('a ColumnControl search is not supported')
        return self


class ColumnControl(_Model):
    """ColumnControl's per-column filters: refused unless idle, so a client is told instead of getting fewer filters
    than it asked for."""

    search: ColumnControlSearch = ColumnControlSearch()
    list: Annotated[Any, AfterValidator(_empty)] = []


class DTColumn(_Model):
    """One column as the request describes it. ``name`` is what the server matches on; it falls back to ``data``."""

    data: Annotated[str, BeforeValidator(_data)] = ''
    name: str = ''
    searchable: _Boolean = True
    orderable: _Boolean = True
    search: Search = Search()
    columnControl: ColumnControl = ColumnControl()

    @property
    def key(self) -> str:
        """The name the server matches against its declared columns."""
        return self.name or self.data or ''


class DTOrder(_Model):
    """One sort instruction: an index into the request's own ``columns`` and a direction."""

    column: _Integer
    dir: Literal['asc', 'desc'] = 'asc'


def _no_condition(value: Any) -> Any:
    """The condition of a criterion the user has not finished, which the client sends as empty text, or as the word
    ``undefined`` in a form (DataTables 3 writes JavaScript's ``undefined`` so): no condition at all (PRD-F30)."""
    return None if value in ('', 'undefined') else value


class SearchCriterion(_Model):
    """A SearchBuilder condition. ``origData`` names the column; ``type`` is the client's guess and is not trusted.
    A new criterion has no condition and no column yet, and the client sends it so; it is unfinished (PRD-F30), and
    so is one with no value for a condition that takes one."""

    condition: Annotated[Condition | None, BeforeValidator(_no_condition)] = None
    origData: str = ''
    data: str = ''
    type: str = ''
    value: list[str] = []


class SearchGroup(_Model):
    """A SearchBuilder group: criteria and nested groups joined by AND or OR."""

    logic: Literal['AND', 'OR'] = 'AND'
    criteria: list[SearchNode] = []


def _node_kind(value: Any) -> str | None:
    if isinstance(value, Mapping):
        fields: Mapping[str, Any] = cast(Mapping[str, Any], value)
    elif isinstance(value, (SearchGroup, SearchCriterion)):
        fields = vars(value)
    else:  # neither: pydantic reports it, and the request is malformed
        return None
    return 'group' if 'criteria' in fields or 'logic' in fields else 'criterion'


#: A group or a criterion, told apart by the group's own keys (both would validate a criterion's dict otherwise)
type SearchNode = Annotated[
    Annotated[SearchGroup, Tag('group')] | Annotated[SearchCriterion, Tag('criterion')], Discriminator(_node_kind)
]


def _no_selection(panes: dict[str, Any]) -> dict[str, Any]:
    for pane in panes.values():  # checked here, not by a typed dict, so a refusal never names a client's column
        _empty(pane)
    return panes


type Draw = Annotated[_Integer, Field(ge=0, le=MAX_PAGING_NUMBER)]


class DTParams(_Model):
    """A parsed DataTables request."""

    draw: Draw = 0
    start: _Integer = Field(default=0, ge=0, le=MAX_START)
    length: _Integer = Field(default=10, ge=1, le=MAX_PAGING_NUMBER)
    columns: list[DTColumn] = []
    order: list[DTOrder] = []
    search: Search = Search()
    searchBuilder: SearchGroup | None = None
    #: SearchPanes' selections, which the library does not answer: a pane holding a selection is refused. SearchPanes
    #: sends an empty object per pane on every request, ``searchPanes_options`` on every request and
    #: ``searchPanesLast`` with a selection; those are accepted or ignored.
    searchPanes: Annotated[dict[str, Any], AfterValidator(_no_selection)] = {}
    searchPanes_null: Annotated[dict[str, Any], AfterValidator(_no_selection)] = {}


class DTResponse(_Model):
    """The answer DataTables expects. ``error_code`` says which kind of failure ``error`` reports.

    The library answers a mistake the user can fix with one of eight codes: ``invalid_character`` (a control
    character, which the user cannot see, in a search or filter value), ``value_too_long`` (a value of more than
    255 code points), ``too_many_words`` (search text of more than 16 words), ``request_too_large`` (a request of
    more than 960 conditions: words times columns searched, values and ranges), ``invalid_date`` (a date it cannot
    read, or the last day there is), ``invalid_number`` (a number it cannot read, or one outside the column's
    range), ``page_out_of_range``, and ``invalid_filter`` for any other. ``error`` says what is wrong and what to do
    about it. ``query_failed`` is only ever set by an app, through ``DTResponse.query_failed``.

    The list is closed: a release answers only the codes its documentation lists, and a code is added only in a
    release that says so. A client that reads a code it does not know treats it as a refusal of the request and
    shows ``error``.
    """

    draw: int
    recordsTotal: int = 0
    recordsFiltered: int = 0
    data: list[dict[str, Any]] = []
    error: str | None = None
    error_code: ErrorCode | Literal['query_failed'] | None = None

    @classmethod
    def query_failed(cls, draw: int, message: str = 'The table query failed.') -> DTResponse:
        """The conventional answer for a failure that is not the user's, for an app that catches one."""
        return cls(draw=draw, error=message, error_code='query_failed')

    def to_dict(self) -> dict[str, Any]:
        """The JSON object DataTables reads; unset ``error`` and ``error_code`` are left out."""
        return self.model_dump(exclude_none=True)


class MalformedRequest(ValueError):
    """The request is not a DataTables request the library can read. The message names a request parameter, never
    the client's key or value.

    ``draw`` is the request's own when that part of it could be read, so the answer can still be matched to the
    request; None otherwise. ``parse_request`` logs each one once, under ``event``: ``datatables_request_malformed``,
    or ``datatables_length_refused`` for a page longer than the caller allows. ``parameter`` is the request parameter
    the message names, if it names one.
    """

    draw: int | None = None
    event: str = 'datatables_request_malformed'
    parameter: str | None = None


class InvalidFilter(ValueError):
    """The user's filter cannot be answered. The message is written for the user and never quotes what they typed.

    ``code`` is the answer's ``error_code``, ``step`` its place in the order that decides which of a request's
    mistakes is answered (lowest first), ``event`` the fixed name it is logged under, and ``column`` the declared
    name of the column it is about, if the log may name one. The library raises it for the mistakes it knows, each
    with its event; it is not for an app to raise, so none of the three has a default.
    """

    def __init__(self, reason: str, *, code: ErrorCode, step: int, event: str, column: str | None = None) -> None:
        super().__init__(reason)
        self.code: ErrorCode = code
        self.step = step
        self.event = event
        self.column = column


class PartialResultError(RuntimeError):
    """A search answer does not cover every shard, or its shard information cannot be read."""


class SchemaError(RuntimeError):
    """The table's declaration cannot be served (a column the source lacks, analysed text declared orderable): the
    server's mistake, not the user's, raised when the table is built."""


_HEAD = re.compile(r'[^.\[\]]*')
_KEY = re.compile(r'[^.\[\]]+(?:\[[^.\[\]]*\]|\.[^.\[\]]+)*')
_PART = re.compile(r'\[([^.\[\]]*)\]|\.([^.\[\]]+)')


def parse_request(
    data: Mapping[str, Any] | Iterable[tuple[str, Any]], *, max_length: int = MAX_PAGE_LENGTH
) -> DTParams:
    """Read a DataTables request.

    ``data`` is either the decoded JSON object a table posts (``ajax.submitAs: 'json'``), or flat
    parameters, as a mapping, a multi-dict (anything with ``getlist``, whose every value is read) or key-value pairs
    (a query string or form with repeated ``key[]`` entries). Flat keys
    may be bracketed, as DataTables' default encoding sends them (``columns[0][search][value]``), dotted
    (``columns[0].search.value``, as spring-data-jpa-datatables reads them, or ``columns.0.search.value``), or mixed.
    A flat key is the request's own when its name before the first ``.``, ``[`` or ``]`` is a request parameter
    (``draw``, ``start``, ``length``, ``columns``, ``order``, ``search``, ``searchBuilder``, ``searchPanes``,
    ``searchPanes_null``). Any other key, such as DataTables' cache-buster ``_``, SearchPanes' ``searchPanes_options``
    or an app's own ``ajax.data`` keys, is ignored, so an app key must not begin with one of those names followed by
    ``.`` or ``[``. A request key must be ``[part]`` and ``.part`` segments, a bracket segment holding no ``.``; an
    index is ``0`` or at most 9 ASCII digits without a leading zero, and ``[]`` appends a value, never a field.

    Filters the library does not answer are refused unless empty or idle, so a client is told instead of getting fewer
    filters than it asked for: DataTables' fixed and grouped searches (``search[fixed]``, ``search[groups]``,
    ``search[groupsFixed]``, ``columns[i][search][fixed]``), SearchPanes' selections (a pane in ``searchPanes`` or
    ``searchPanes_null`` that is not empty; SearchPanes sends an empty one per pane) and ColumnControl's
    (``columns[i][columnControl][list]``, and a ``columns[i][columnControl][search]`` with a value or the
    ``empty``/``notEmpty`` logic).

    Raises:
        MalformedRequest: the request holds no DataTables parameter (an empty one, or DataTables 1.9's form), or
            the structure, a request key's shape, a paging number, or the page length is wrong, or a
            filter the library does not answer is sent, or an ``order`` entry points outside the request's columns.
            The message names the request parameter where it can, and never quotes the request's keys or values.
            It carries the request's ``draw`` when that could be read, and is logged once, as a WARNING.
    """
    if not isinstance(data, Mapping) and not hasattr(data, 'getlist') and isinstance(data, Iterable):
        data = list(data)  # pairs may be an iterator, and a refusal reads them again for the draw
    try:
        try:
            return _parse(data, max_length)
        except RecursionError:  # flat keys or a JSON body nested deeper than Python walks: no request has that
            raise _malformed() from None
    except MalformedRequest as exc:
        exc.draw = _draw(data)
        log.warning(exc.event, extra={'parameter': exc.parameter})
        raise


def _malformed(parameter: str | None = None) -> MalformedRequest:
    """The refusal that names ``parameter``, a request parameter and so never the client's text, or none."""
    refusal = MalformedRequest(f'Invalid {parameter} parameter.' if parameter else 'The table request is malformed.')
    refusal.parameter = parameter
    return refusal


def _parse(data: Mapping[str, Any] | Iterable[tuple[str, Any]], max_length: int) -> DTParams:
    body = _unflatten(data) if _is_flat(data) else dict(cast(Mapping[str, Any], data))
    if not body.keys() & DTParams.model_fields.keys():  # empty, or another protocol (e.g. DataTables 1.9's sEcho)
        raise MalformedRequest('The table request holds no DataTables parameter.')
    try:
        json.dumps(body, ensure_ascii=False).encode()
    except (UnicodeEncodeError, TypeError, ValueError):  # a lone surrogate (no driver can encode it), or no JSON
        raise _malformed() from None
    try:
        params = DTParams.model_validate(body)
    except ValidationError as exc:
        # Only the request parameter: the path below it is the client's own key when it sends dotted keys
        where = exc.errors()[0]['loc']
        raise _malformed(str(where[0]) if where else None) from None
    if any(not 0 <= item.column < len(params.columns) for item in params.order):
        raise _malformed('order')  # only an integration bug orders by a column the request does not hold
    if params.length > max_length:
        refusal = MalformedRequest(f'Invalid length parameter: at most {max_length} rows per request.')
        refusal.parameter, refusal.event = 'length', 'datatables_length_refused'
        raise refusal
    return params


_DRAW: TypeAdapter[int] = TypeAdapter(Draw)


def _draw(data: object) -> int | None:
    """The request's ``draw`` when it alone reads as a valid one, whatever else is wrong with the request."""
    values: list[Any]
    if hasattr(data, 'getlist'):
        values = cast(Any, data).getlist('draw')
    elif isinstance(data, Mapping):
        fields = cast(Mapping[Any, Any], data)
        raw = fields.get('draw')
        # Among flat keys a list is the values of one key (as urllib's parse_qs gives them)
        values = cast(list[Any], raw) if isinstance(raw, list) and _is_flat(fields) else [raw]
    elif isinstance(data, list):
        values = [pair[1] for pair in cast(list[Any], data) if _is_pair(pair) and pair[0] == 'draw']
    else:
        return None
    if len(values) != 1 or values[0] is None:
        return None
    try:
        return _DRAW.validate_python(values[0])
    except ValidationError:
        return None


def _is_pair(item: object) -> bool:
    return isinstance(item, (tuple, list)) and len(cast(Any, item)) == 2


def _is_flat(data: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> bool:
    if not isinstance(data, Mapping) or hasattr(data, 'getlist'):  # pairs, or a multi-dict of form fields
        return True
    return any(
        isinstance(key, str) and _head(key) in DTParams.model_fields and _head(key) != key
        for key in cast(Mapping[str, Any], data)
    )


def _head(key: str) -> str:
    return cast(re.Match[str], _HEAD.match(key)).group()


def _path(key: str) -> list[str] | None:
    """``columns[0][search][value]``, ``columns[0].search.value`` or ``columns.0.search.value`` as its parts.

    None for a key that is not the request's own; a request key of any other shape is refused.
    """
    head = _head(key)
    if head not in DTParams.model_fields:
        return None
    if not _KEY.fullmatch(key):
        raise _malformed(head)
    return [head, *(dotted or bracketed for bracketed, dotted in _PART.findall(key, len(head)))]


def _unflatten(data: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> dict[str, Any]:
    """``{'columns[0][data]': 'a'}`` or ``{'columns.0.data': 'a'}`` as ``{'columns': [{'data': 'a'}]}``.

    A ``key[]`` repeated is a list. Keys that are not the request's own are left out.
    """
    items: Iterable[tuple[str, Any]]
    if hasattr(data, 'getlist'):  # a multi-dict (Starlette, Werkzeug, Django): every value of a repeated key
        multi = cast(Any, data)
        items = [(key, value) for key in multi for value in multi.getlist(key)]
    elif isinstance(data, Mapping):
        items = cast(Mapping[str, Any], data).items()
    else:  # key-value pairs; a decoded JSON list of anything else, or a value that is no list at all, is refused
        items = cast(list[Any], data) if isinstance(data, list) else []
        if not isinstance(data, list) or not all(_is_pair(item) for item in items):
            raise _malformed()
    root: dict[str, Any] = {}
    for key, raw in items:
        if not isinstance(key, str):
            raise MalformedRequest('The table request is malformed.')
        path = _path(key)
        if path is None:
            continue
        if isinstance(raw, list) and not raw and not key.endswith('[]'):  # no value for a single-value key
            raise _malformed(path[0])
        values = cast(list[Any], raw if isinstance(raw, list) else [raw])
        if any(isinstance(value, dict) for value in values):  # an object among flat keys: JSON and form mixed
            raise _malformed(path[0])
        for value in values:
            _assign(root, path, value)
    return _lists(root)


def _assign(node: dict[str, Any], path: list[str], value: Any) -> None:
    for part in path[:-1]:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise MalformedRequest('The table request is malformed.')
        node = cast(dict[str, Any], child)
    last = path[-1]
    if last == '':  # key[]: append
        appended = node.setdefault('', [])
        if not isinstance(appended, list):  # a field was put under this append first (key[][part])
            raise MalformedRequest('The table request is malformed.')
        cast(list[Any], appended).append(value)
    elif last in node:
        raise MalformedRequest('The table request is malformed.')
    else:
        node[last] = value


def _index(key: str) -> bool:
    """Whether ``key`` is a list index: ``0``, or at most 9 ASCII digits without a leading zero."""
    return key == '0' or (key.isascii() and key.isdigit() and len(key) <= 9 and not key.startswith('0'))


def _lists(node: Any) -> Any:
    """Dicts whose keys are 0..n-1 become lists; a ``key[]`` list replaces its holder."""
    if not isinstance(node, dict):
        return node
    mapping = cast(dict[str, Any], node)
    if '' in mapping and len(mapping) > 1:  # key[] beside key[part]: an append and a field under one parent
        raise MalformedRequest('The table request is malformed.')
    if set(mapping) == {''}:
        if not isinstance(mapping[''], list):  # key[][part]: a field under an append, which nothing sends
            raise MalformedRequest('The table request is malformed.')
        return [_lists(value) for value in cast(list[Any], mapping[''])]
    converted = {key: _lists(value) for key, value in mapping.items()}
    if converted and all(_index(key) for key in converted):
        indexes = sorted(int(key) for key in converted)
        if indexes != list(range(len(indexes))):
            raise MalformedRequest('The table request is malformed.')
        return [converted[str(index)] for index in indexes]
    return converted
