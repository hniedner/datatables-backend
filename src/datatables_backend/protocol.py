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

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Discriminator
from pydantic import Field
from pydantic import Tag
from pydantic import ValidationError

log = logging.getLogger('datatables_backend')

#: The default largest page a request may ask for. DataTables' "All" sends -1 and is refused.
MAX_PAGE_LENGTH = 1000
#: DataTables' own counters are 32-bit; a larger paging number is the client's mistake, not an offset to bind.
MAX_PAGING_NUMBER = 2**31 - 1

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


class _Model(BaseModel):
    model_config = ConfigDict(extra='ignore')


class Search(_Model):
    """A search value: the table's global search or one column's."""

    value: str = ''
    regex: bool = False
    #: Not part of the DataTables protocol: a multi-select may send its values as a list here
    multi_values: list[str] | None = None
    #: DataTables' fixed and grouped searches, which the library does not answer: refused unless empty, so a client
    #: that uses them is told so instead of getting fewer filters than it asked for
    fixed: list[Any] = Field(default=[], max_length=0)
    groups: list[Any] = Field(default=[], max_length=0)
    groupsFixed: list[Any] = Field(default=[], max_length=0)


class DTColumn(_Model):
    """One column as the request describes it. ``name`` is what the server matches on; it falls back to ``data``."""

    data: str | None = ''
    name: str = ''
    searchable: bool = True
    orderable: bool = True
    search: Search = Search()

    @property
    def key(self) -> str:
        """The name the server matches against its declared columns."""
        return self.name or self.data or ''


class DTOrder(_Model):
    """One sort instruction: an index into the request's own ``columns`` and a direction."""

    column: int
    dir: Literal['asc', 'desc'] = 'asc'


class SearchCriterion(_Model):
    """A SearchBuilder condition. ``origData`` names the column; ``type`` is the client's guess and is not trusted."""

    condition: Condition
    origData: str
    data: str = ''
    type: str = ''
    value: list[str] = []


class SearchGroup(_Model):
    """A SearchBuilder group: criteria and nested groups joined by AND or OR."""

    logic: Literal['AND', 'OR'] = 'AND'
    criteria: list[SearchNode] = []


def _node_kind(value: Any) -> str:
    fields = cast(Mapping[str, Any], value) if isinstance(value, Mapping) else vars(value)
    return 'group' if 'criteria' in fields or 'logic' in fields else 'criterion'


#: A group or a criterion, told apart by the group's own keys (both would validate a criterion's dict otherwise)
type SearchNode = Annotated[
    Annotated[SearchGroup, Tag('group')] | Annotated[SearchCriterion, Tag('criterion')], Discriminator(_node_kind)
]


class DTParams(_Model):
    """A parsed DataTables request."""

    draw: int = 0
    start: int = Field(default=0, ge=0, le=MAX_PAGING_NUMBER)
    length: int = Field(default=10, ge=1, le=MAX_PAGING_NUMBER)
    columns: list[DTColumn] = []
    order: list[DTOrder] = []
    search: Search = Search()
    searchBuilder: SearchGroup | None = None


class DTResponse(_Model):
    """The answer DataTables expects. ``error_code`` says which kind of failure ``error`` reports."""

    draw: int
    recordsTotal: int = 0
    recordsFiltered: int = 0
    data: list[dict[str, Any]] = []
    error: str | None = None
    error_code: Literal['invalid_filter', 'query_failed'] | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def invalid_filter(cls, draw: int, reason: str) -> DTResponse:
        """The answer to a filter the user can change; ``reason`` is shown to them."""
        return cls(draw=draw, error=reason, error_code='invalid_filter')

    @classmethod
    def query_failed(cls, draw: int, message: str = 'The table query failed.') -> DTResponse:
        """The conventional answer for a failure that is not the user's, for an app that catches one."""
        return cls(draw=draw, error=message, error_code='query_failed')

    def to_dict(self) -> dict[str, Any]:
        """The JSON object DataTables reads; unset ``error``, ``error_code`` and ``metadata`` are left out."""
        return self.model_dump(exclude_none=True)


class MalformedRequest(ValueError):
    """The request is not a DataTables request the library can read. The message names a parameter, never a value."""


class InvalidFilter(ValueError):
    """The user's filter cannot be answered. The message is written for the user and never quotes what they typed."""


class PartialResultError(RuntimeError):
    """A search answer does not cover every shard, or its shard information cannot be read."""


class SchemaError(RuntimeError):
    """A declared column does not exist in the source: the server's mistake, not the user's."""


# A search, a filter or an order on a name the server did not declare, or an order it does not allow
NO_USABLE_COLUMN = 'This table cannot be searched or sorted that way.'

_HEAD = re.compile(r'[^.\[\]]*')
_KEY = re.compile(r'[^.\[\]]+(?:\[[^.\[\]]*\]|\.[^.\[\]]+)*')
_PART = re.compile(r'\[([^.\[\]]*)\]|\.([^.\[\]]+)')


def parse_request(
    data: Mapping[str, Any] | Iterable[tuple[str, Any]], *, max_length: int = MAX_PAGE_LENGTH
) -> DTParams:
    """Read a DataTables request.

    ``data`` is either the decoded JSON object a table posts (``ajax.data`` returning ``JSON.stringify(d)``), or flat
    parameters, as a mapping or as key-value pairs (a query string or form with repeated ``key[]`` entries). Flat keys
    may be bracketed, as DataTables' default encoding sends them (``columns[0][search][value]``), dotted
    (``columns[0].search.value``, as spring-data-jpa-datatables reads them, or ``columns.0.search.value``), or mixed.
    A flat key is the request's own when its name before the first ``.``, ``[`` or ``]`` is a request parameter
    (``draw``, ``start``, ``length``, ``columns``, ``order``, ``search``, ``searchBuilder``). Any other key, such as
    DataTables' cache-buster ``_`` or an app's own ``ajax.data`` keys, is ignored, so an app key must not begin with one
    of those names followed by ``.`` or ``[``. A request key must be ``[part]`` and ``.part`` segments, a bracket
    segment holding no ``.``; an index is ASCII digits. DataTables' fixed and grouped searches (``search[fixed]``,
    ``search[groups]``, ``search[groupsFixed]``, ``columns[i][search][fixed]``) are refused unless empty.

    Raises:
        MalformedRequest: the structure, a request key's shape, a paging number, or the page length is wrong, or a
            fixed or grouped search is sent. The message names the parameter, never the request's keys or values.
    """
    body = _unflatten(data) if _is_flat(data) else dict(cast(Mapping[str, Any], data))
    try:
        json.dumps(body, ensure_ascii=False).encode()
    except UnicodeEncodeError:  # a lone surrogate: no driver can encode it
        raise MalformedRequest('The table request is malformed.') from None
    except (TypeError, ValueError):
        raise MalformedRequest('The table request is malformed.') from None
    try:
        params = DTParams.model_validate(body)
    except ValidationError as exc:
        where = '.'.join(str(part) for part in exc.errors()[0]['loc'])
        log.warning('datatables_request_malformed', extra={'parameter': where})
        raise MalformedRequest(f'Invalid {where} parameter.') from None
    if params.length > max_length:
        log.warning('datatables_length_refused', extra={'max_length': max_length})
        raise MalformedRequest(f'Invalid length parameter: at most {max_length} rows per request.')
    return params


def _is_flat(data: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> bool:
    if not isinstance(data, Mapping):
        return True
    return any(_head(key) in DTParams.model_fields and _head(key) != key for key in cast(Mapping[str, Any], data))


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
        log.warning('datatables_request_malformed', extra={'parameter': head})
        raise MalformedRequest(f'Invalid {head} parameter.')
    return [head, *(dotted or bracketed for bracketed, dotted in _PART.findall(key, len(head)))]


def _unflatten(data: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> dict[str, Any]:
    """``{'columns[0][data]': 'a'}`` or ``{'columns.0.data': 'a'}`` as ``{'columns': [{'data': 'a'}]}``.

    A ``key[]`` repeated is a list. Keys that are not the request's own are left out.
    """
    items: Iterable[tuple[str, Any]] = cast(Mapping[str, Any], data).items() if isinstance(data, Mapping) else data
    root: dict[str, Any] = {}
    for key, raw in items:
        path = _path(key)
        if path is None:
            continue
        values = raw if isinstance(raw, list) else [raw]
        for value in cast(list[Any], values):
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
        cast(list[Any], appended).append(value)
    elif last in node:
        raise MalformedRequest('The table request is malformed.')
    else:
        node[last] = value


def _lists(node: Any) -> Any:
    """Dicts whose keys are 0..n-1 become lists; a ``key[]`` list replaces its holder."""
    if not isinstance(node, dict):
        return node
    mapping = cast(dict[str, Any], node)
    if set(mapping) == {''}:
        return [_lists(value) for value in mapping['']]
    converted = {key: _lists(value) for key, value in mapping.items()}
    if converted and all(key.isascii() and key.isdigit() for key in converted):
        indexes = sorted(int(key) for key in converted)
        if indexes != list(range(len(indexes))):
            raise MalformedRequest('The table request is malformed.')
        return [converted[str(index)] for index in indexes]
    return converted
