"""The DataTables server-side protocol: the request a table sends, the answer it expects, and how to read the request.

See https://datatables.net/manual/core/server-side and, for the SearchBuilder tree,
https://datatables.net/manual/extensions/searchbuilder/serverside.
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
from typing import LiteralString
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
from pydantic_core import PydanticCustomError

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


#: The logics ColumnControl's controls send (decision 007), and ``''`` for none. ``greater`` to ``lessOrEqual`` are the
#: number control's.
_COLUMNCONTROL_LOGICS = frozenset([
    '', 'contains', 'notContains', 'equal', 'notEqual', 'starts', 'ends', 'empty', 'notEmpty',
    'greater', 'greaterOrEqual', 'less', 'lessOrEqual',
])  # fmt: skip

#: What a refusal of ColumnControl's part says, by the error that raised it. Keyed by the kind of error, not by the
#: field it is under: ``logic`` is also SearchBuilder's group field, and ``search`` holds two of these (PRD-F45,
#: PRD-E4).
_COLUMNCONTROL_REFUSALS = {
    'columncontrol_logic': (
        'a ColumnControl search sent a logic the server does not answer. Offer only contains, notContains, equal, '
        'notEqual, starts, ends, empty and notEmpty (a number control also greater, greaterOrEqual, less and '
        'lessOrEqual), or remove the control from the table.'
    ),
    'columncontrol_no_logic': (
        "a ColumnControl search sent a value without a logic. Send the control's logic with its value."
    ),
    'columncontrol_date': (
        "ColumnControl's date search is not answered. Remove the date control from this column, or give the column a "
        'text search control.'
    ),
    'columncontrol_entry': (
        'a ColumnControl list holds a value that is neither text nor a whole number. Give each option a text or '
        'whole-number value.'
    ),
    'columncontrol_list': (
        'a ColumnControl list is neither an array nor an object keyed by whole-number indexes. Send an array, or an '
        'object keyed 0, 1, 2 and so on.'
    ),
}


def _refusal(kind: LiteralString) -> PydanticCustomError:
    return PydanticCustomError(kind, 'refused')


def _closed_logic(logic: str) -> str:
    if logic not in _COLUMNCONTROL_LOGICS:
        raise _refusal('columncontrol_logic')
    return logic


class ColumnControlSearch(_Model):
    """ColumnControl's column search: a value and a logic from a closed set. It sends an idle one on every request, an
    empty value with a logic other than ``empty`` and ``notEmpty``, which adds nothing, as ColumnControl itself counts
    it. An active one with no logic, or with the date control's ``type``, is refused. ``type`` is read only to refuse
    the date control; ``mask`` and any other field the client sends is ignored."""

    value: str = ''
    logic: Annotated[str, AfterValidator(_closed_logic)] = ''
    type: str = ''

    @model_validator(mode='after')
    def _answerable(self) -> ColumnControlSearch:
        active = bool(self.value) or self.logic in ('empty', 'notEmpty')
        if self.value and not self.logic:
            raise _refusal('columncontrol_no_logic')
        if active and self.type == 'date':
            raise _refusal('columncontrol_date')
        return self


def _entries(value: Any) -> Any:
    """A ColumnControl list as the client sends it, an object keyed by index (``{"0": "a"}``), or as an array: the
    entries as text, in index order. The object is sorted by its keys and never built by index, so a key of nine digits
    costs no more than one digit; a gap is closed up. An entry is text or a whole number, which reads as its text, since
    the client does not coerce an option's value."""
    if isinstance(value, Mapping):
        fields = cast(Mapping[Any, Any], value)
        if not all(isinstance(key, str) and _index(key) for key in fields):
            raise _refusal('columncontrol_list')
        entries = [fields[key] for key in sorted(fields, key=int)]
    elif isinstance(value, list):
        entries = cast(list[Any], value)
    else:
        raise _refusal('columncontrol_list')
    if any(isinstance(entry, bool) or not isinstance(entry, (str, int)) for entry in entries):
        raise _refusal('columncontrol_entry')
    return [str(entry) for entry in entries]


_Entries = Annotated[list[str], BeforeValidator(_entries)]


class ColumnControl(_Model):
    """ColumnControl's per-column filters (PRD-F45): its search, and its list of ticked options, ``[]`` when none is."""

    search: ColumnControlSearch = ColumnControlSearch()
    list: _Entries = []


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
    ``searchPanes_null`` that is not empty; SearchPanes sends an empty one per pane).

    ColumnControl's per-column part is read (PRD-F45): ``columns[i][columnControl][search]`` as a value and a logic, and
    ``columns[i][columnControl][list]`` as text entries, as JSON or as a form; its idle search and empty list add
    nothing. A logic outside the closed set, a value with no logic, an active search of the date control, and a list
    entry that is neither text nor a whole number are refused, each with a message that says what to change.

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


def _malformed(parameter: str | None = None, fix: str | None = None) -> MalformedRequest:
    """The refusal that names ``parameter``, a request parameter and so never the client's text, or none, and says
    what to send (``fix``) where that is known."""
    if not parameter:
        text = 'The table request is malformed.'
    else:
        text = f'Invalid {parameter} parameter: {fix}' if fix else f'Invalid {parameter} parameter.'
    refusal = MalformedRequest(text)
    refusal.parameter = parameter
    return refusal


#: The number a request parameter may hold, from and to (PRD-P9, PRD-P10, PRD-P13)
_RANGES = {'draw': (0, MAX_PAGING_NUMBER), 'start': (0, MAX_START), 'length': (1, MAX_PAGING_NUMBER)}
#: What a field of the protocol must be, named by the protocol's own field names and never by a client's key
_FIELDS = {
    'column': 'column must be a whole number.',
    'dir': 'dir must be asc or desc.',
    'searchable': 'searchable must be true or false.',
    'orderable': 'orderable must be true or false.',
    'regex': 'regex must be true or false.',
    'data': 'data must be text, a whole number, an object or null.',
}


def _what_to_send(error: Mapping[str, Any]) -> str | None:
    """What a request parameter or one of its fields must be, for the first error of a validation, or None where
    nothing is said beyond the parameter. The field is named only if it is one of the protocol's (``_FIELDS``)."""
    if error['type'] in _COLUMNCONTROL_REFUSALS:
        return _COLUMNCONTROL_REFUSALS[error['type']]
    where = tuple(error['loc'])
    if len(where) == 1 and where[0] in _RANGES:
        if error['type'] in ('greater_than_equal', 'less_than_equal'):
            low, high = _RANGES[where[0]]
            return f'send a whole number from {low} to {high}.'
        return 'send a whole number, as DataTables does.'
    return _FIELDS.get(where[-1]) if where and isinstance(where[-1], str) else None


def _parse(data: Mapping[str, Any] | Iterable[tuple[str, Any]], max_length: int) -> DTParams:
    body = _unflatten(data) if _is_flat(data) else dict(cast(Mapping[str, Any], data))
    if not body.keys() & DTParams.model_fields.keys():  # empty, or another protocol (e.g. DataTables 1.9's sEcho)
        raise MalformedRequest(
            'The table request holds no DataTables parameter: send them as a JSON object or as a form.'
        )
    try:
        json.dumps(body, ensure_ascii=False).encode()
    except (UnicodeEncodeError, TypeError, ValueError):  # a lone surrogate (no driver can encode it), or no JSON
        raise _malformed() from None
    try:
        params = DTParams.model_validate(body)
    except ValidationError as exc:
        # The request parameter, and a field of the protocol's own under it: the path is otherwise the client's key
        first = exc.errors()[0]
        where = first['loc']
        raise _malformed(str(where[0]) if where else None, _what_to_send(first)) from None
    if any(not 0 <= item.column < len(params.columns) for item in params.order):
        # only an integration bug orders by a column the request does not hold
        raise _malformed('order', "column must be the index of one of the request's columns.")
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
