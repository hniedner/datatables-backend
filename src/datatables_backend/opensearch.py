"""DataTables from an OpenSearch index, with the sync or the async client.

    reports = SearchTable('reports', [IndexColumn('title', text='text'), IndexColumn('id', text='keyword'),
                                      Column('created', type='date')])
    response = await reports.fetch(params, AsyncOpenSearch(...))

Requests are plain bodies (``client.search(index=..., body=...)``): no DSL library is needed, and any client with
that call shape works.
"""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
import json
import logging
from typing import Any
from typing import Literal
from typing import Protocol
from typing import cast

from ._plan import AnyOf
from ._plan import Contains
from ._plan import Criterion
from ._plan import DaySpan
from ._plan import Group
from ._plan import Plan
from ._plan import day_span
from ._plan import next_day
from ._plan import parse_number
from ._plan import plan
from ._text_query import QueryParser
from .cache import TotalCache
from .columns import Column
from .columns import ColumnType
from .protocol import NO_USABLE_COLUMN
from .protocol import DTParams
from .protocol import DTResponse
from .protocol import InvalidFilter
from .protocol import PartialResultError
from .rows import RowHooks

log = logging.getLogger('datatables_backend')

#: How a string field is mapped: ``keyword``; ``text`` (analysed only); or ``text`` with a ``.keyword`` subfield,
#: which is what dynamic mapping creates for a string
type TextKind = Literal['keyword', 'text', 'text+keyword']

#: OpenSearch's default ``index.max_result_window``: ``from + size`` beyond it fails the whole search
MAX_RESULT_WINDOW = 10_000


@dataclass(frozen=True, kw_only=True)
class IndexColumn(Column):
    """A ``Column`` that says how its string field is mapped. A plain ``Column`` is taken as ``text+keyword``."""

    text: TextKind = 'text+keyword'


class SyncClient(Protocol):
    def search(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def count(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]: ...


class AsyncClient(Protocol):
    def search(self, *, index: str, body: Mapping[str, Any]) -> Awaitable[Mapping[str, Any]]: ...
    def count(self, *, index: str, body: Mapping[str, Any]) -> Awaitable[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class _Request:
    search: dict[str, Any]
    count: dict[str, Any] | None  # None: the search's own count is the total


class SearchTable:
    """A DataTables view of an OpenSearch index. Build it once; ``fetch`` answers each request.

    Args:
        index: The index (or alias, or pattern) to search.
        columns: The columns the client may see, search and sort on; only their fields are fetched.
        base_filter: A query that limits the documents the table shows at all; it applies to ``recordsTotal`` too.
        rows: Hooks that shape each row and add ``DT_RowId`` / ``DT_RowData``.
        total_cache: Caches ``recordsTotal`` for requests that filter. One cache per cluster.
        max_result_window: The index's ``max_result_window``; a page beyond it is refused as the user's request.
    """

    def __init__(
        self,
        index: str,
        columns: Sequence[Column],
        *,
        base_filter: Mapping[str, Any] | None = None,
        rows: RowHooks | None = None,
        total_cache: TotalCache | None = None,
        max_result_window: int = MAX_RESULT_WINDOW,
    ) -> None:
        self.index = index
        self.columns = tuple(columns)
        self.base_filter = dict(base_filter) if base_filter else None
        self.rows = rows or RowHooks()
        self.total_cache = total_cache
        self.max_result_window = max_result_window
        self._total_key = f'{index} {json.dumps(self.base_filter, sort_keys=True, default=str)}'

    async def fetch(self, params: DTParams, client: AsyncClient) -> DTResponse:
        """Answer one request with an async client (``AsyncOpenSearch``).

        A filter the user can change answers with ``error_code='invalid_filter'``; any other failure raises, a partial
        answer as ``PartialResultError``.
        """
        try:
            request = self._request(params)
        except InvalidFilter as exc:
            return DTResponse.invalid_filter(params.draw, str(exc))
        answer = await client.search(index=self.index, body=request.search)
        filtered = self._filtered(answer)
        total = self._cached_total(request, filtered)
        if total is None:
            total = self._count(await client.count(index=self.index, body=cast(dict[str, Any], request.count)))
            self._remember(total)
        return self._response(params, answer, total, filtered)

    def fetch_sync(self, params: DTParams, client: SyncClient) -> DTResponse:
        """``fetch`` with a sync client (``OpenSearch``)."""
        try:
            request = self._request(params)
        except InvalidFilter as exc:
            return DTResponse.invalid_filter(params.draw, str(exc))
        answer = client.search(index=self.index, body=request.search)
        filtered = self._filtered(answer)
        total = self._cached_total(request, filtered)
        if total is None:
            total = self._count(client.count(index=self.index, body=cast(dict[str, Any], request.count)))
            self._remember(total)
        return self._response(params, answer, total, filtered)

    # -- building ---------------------------------------------------------------------------------------------------

    def _request(self, params: DTParams) -> _Request:
        """Both request bodies, built before either is sent."""
        if params.start + params.length > self.max_result_window:
            raise InvalidFilter(f'This table cannot page beyond row {self.max_result_window:,}.')
        request = plan(params, self.columns, _type_of)
        must: list[dict[str, Any]] = [self.base_filter] if self.base_filter else []
        if request.global_search:
            should = [_global_clause(search) for search in request.global_search if search.type == 'string']
            if not should:  # numbers and dates take no free text; an empty should would match every document
                raise InvalidFilter(NO_USABLE_COLUMN)
            must.append(_any(should))
        must.extend(_column_clause(search) for search in request.column_searches)
        if request.search_builder is not None:
            must.append(_node(request.search_builder))
        search = {
            'query': {'bool': {'must': must}} if must else {'match_all': {}},
            'from': params.start,
            'size': params.length,
            'track_total_hits': True,
            '_source': [column.source for column in self.columns],
        }
        if sort := _sort(request):
            search['sort'] = sort
        count = {'query': {'bool': {'must': [self.base_filter]}} if self.base_filter else {'match_all': {}}}
        return _Request(search, count if request.filtered else None)

    # -- running ----------------------------------------------------------------------------------------------------

    def _filtered(self, answer: Mapping[str, Any]) -> int:
        if partial_answer(answer, self.index):
            raise PartialResultError(f'The search of {self.index} did not cover every shard.')
        total = answer['hits']['total']
        # track_total_hits makes it exact; an older engine may still answer a bare number
        return int(total['value'] if isinstance(total, Mapping) else total)

    def _cached_total(self, request: _Request, filtered: int) -> int | None:
        """The total without asking again: the search's own count when the request filters nothing, else the cache."""
        if request.count is None:
            self._remember(filtered)
            return filtered
        return self.total_cache.get(self._total_key) if self.total_cache else None

    def _remember(self, total: int) -> None:
        if self.total_cache:
            self.total_cache.put(self._total_key, total)

    def _count(self, answer: Mapping[str, Any]) -> int:
        if partial_answer(answer, self.index):
            raise PartialResultError(f'The count of {self.index} did not cover every shard.')
        return int(answer['count'])

    def _response(self, params: DTParams, answer: Mapping[str, Any], total: int, filtered: int) -> DTResponse:
        hits = cast(list[Mapping[str, Any]], answer['hits']['hits'])
        data = [self.rows.apply(self._row(hit.get('_source') or {})) for hit in hits]
        return DTResponse(draw=params.draw, recordsTotal=total, recordsFiltered=filtered, data=data)

    def _row(self, source: Mapping[str, Any]) -> dict[str, Any]:
        return {column.name: _get(source, column.source) for column in self.columns}


# -- query building ---


def _type_of(column: Column) -> ColumnType:
    return column.type or 'string'


def _kind(column: Column) -> TextKind:
    return column.text if isinstance(column, IndexColumn) else 'text+keyword'


def _keyword(column: Column) -> str | None:
    """The field that holds the whole string (for exact matches, wildcards and sorting); None for analysed text."""
    match _kind(column):
        case 'keyword':
            return column.source
        case 'text+keyword':
            return f'{column.source}.keyword'
        case 'text':
            return None


def _any(clauses: list[dict[str, Any]]) -> dict[str, Any]:
    return {'bool': {'should': clauses, 'minimum_should_match': 1}}


def _not(clause: dict[str, Any]) -> dict[str, Any]:
    return {'bool': {'must_not': [clause]}}


def _wildcard(field: str, pattern: str) -> dict[str, Any]:
    return {'wildcard': {field: {'value': pattern, 'case_insensitive': True}}}


def escape_wildcard(value: str) -> str:
    """``value`` with the wildcard query's special characters escaped: ``\\``, ``*``, ``?``."""
    return value.replace('\\', '\\\\').replace('*', '\\*').replace('?', '\\?')


def _string_contains(column: Column, value: str, *, parse: bool) -> dict[str, Any]:
    """A string column contains ``value``: a wildcard on the whole string, a match on analysed text, or either."""
    keyword = _keyword(column)
    text_match = (
        QueryParser().to_opensearch_query(value, column.source)
        if parse
        else {'match': {column.source: {'query': value}}}
    )
    if keyword is None:
        return text_match
    wildcard = _wildcard(keyword, f'*{escape_wildcard(value)}*')
    if _kind(column) == 'keyword':
        return wildcard
    return _any([text_match, wildcard])


def _global_clause(search: Contains) -> dict[str, Any]:
    return _string_contains(search.column, str(search.value), parse=False)


def _column_clause(search: Contains | AnyOf) -> dict[str, Any]:
    column = search.column
    if isinstance(search, AnyOf):
        if search.type == 'number':
            return {'terms': {column.source: [parse_number(str(value)) for value in search.values]}}
        field = column.source if search.type == 'date' else _keyword(column)
        if field is None:
            raise InvalidFilter(NO_USABLE_COLUMN)
        return {'terms': {field: list(search.values)}}
    match search.type:
        case 'number':
            return {'term': {column.source: parse_number(str(search.value))}}
        case 'date':
            span = day_span(str(search.value))
            return _days(column.source, span.start.isoformat(), span.end.isoformat())
        case 'string':
            return _string_contains(column, str(search.value), parse=True)


def _days(field: str, start: str | None, end: str | None) -> dict[str, Any]:
    """Days from ``start`` up to, not including, ``end`` (either may be open)."""
    bounds = {key: value for key, value in (('gte', start), ('lt', end)) if value is not None}
    return {'range': {field: {**bounds, 'format': 'strict_date'}}}


def _node(node: Criterion | Group) -> dict[str, Any]:
    if isinstance(node, Group):
        clauses = [_node(item) for item in node.items]
        return {'bool': {'must': clauses}} if node.logic == 'AND' else _any(clauses)
    if node.condition in ('null', '!null'):
        exists = {'exists': {'field': node.column.source}}
        return _not(exists) if node.condition == 'null' else exists
    match node.type:
        case 'number':
            return _number_criterion(node)
        case 'date':
            return _date_criterion(node)
        case 'string':
            return _string_criterion(node)


def _number_criterion(node: Criterion) -> dict[str, Any]:
    field, values = node.column.source, node.values
    match node.condition:
        case '=' | '!=':
            term = {'term': {field: values[0]}}
            return term if node.condition == '=' else _not(term)
        case 'between' | '!between':
            inside = {'range': {field: {'gte': values[0], 'lte': values[1]}}}
            return inside if node.condition == 'between' else _not(inside)
        case _:
            operator = {'<': 'lt', '<=': 'lte', '>': 'gt', '>=': 'gte'}[node.condition]
            return {'range': {field: {operator: values[0]}}}


def _date_criterion(node: Criterion) -> dict[str, Any]:
    """Dates compare by day, as on SQL. A negated condition is a ``must_not``, which keeps documents without the
    field; SQL leaves out a row whose value is NULL."""
    field, values = node.column.source, node.values

    def day(index: int, *, after: bool = False) -> str:
        return (next_day(values[index]) if after else values[index]).isoformat()

    match node.condition:
        case 'contains' | '!contains':
            span = cast(DaySpan, values[0])
            inside = _days(field, span.start.isoformat(), span.end.isoformat())
            return inside if node.condition == 'contains' else _not(inside)
        case '=' | '!=':
            inside = _days(field, day(0), day(0, after=True))
            return inside if node.condition == '=' else _not(inside)
        case 'between' | '!between':
            inside = _days(field, day(0), day(1, after=True))
            return inside if node.condition == 'between' else _not(inside)
        case '<':
            return _days(field, None, day(0))
        case '<=':
            return _days(field, None, day(0, after=True))
        case '>':
            return _days(field, day(0, after=True), None)
        case _:  # '>='
            return _days(field, day(0), None)


def _string_criterion(node: Criterion) -> dict[str, Any]:
    column, value = node.column, str(node.values[0])
    keyword = _keyword(column)
    negated = node.condition.startswith('!')
    match node.condition.lstrip('!'):
        case '=':
            clause = {'term': {keyword: value}} if keyword else {'match_phrase': {column.source: value}}
        case 'contains':
            clause = _string_contains(column, value, parse=True)
        case 'starts' | 'ends' if keyword is not None:
            escaped = escape_wildcard(value)
            pattern = f'{escaped}*' if node.condition.lstrip('!') == 'starts' else f'*{escaped}'
            clause = _wildcard(keyword, pattern)
        case _:  # starts or ends on analysed text: it has no whole string to anchor on, so it reads as contains
            clause = _string_contains(column, value, parse=True)
    return _not(clause) if negated else clause


def _sort(request: Plan) -> list[dict[str, Any]]:
    sort: list[dict[str, Any]] = []
    for item in request.order:
        field = _keyword(item.column) if item.type == 'string' else item.column.source
        if field is None:
            # OpenSearch refuses the whole search on a sort by analysed text: the order is dropped instead
            log.warning('datatables_sort_dropped', extra={'column': item.column.name})
            continue
        sort.append({field: {'order': 'desc' if item.descending else 'asc'}})
    return sort


def _get(source: Mapping[str, Any], path: str) -> Any:
    """``source[path]``, or the value at a dotted path through nested objects."""
    if path in source:
        return source[path]
    value: Any = source
    for part in path.split('.'):
        if not isinstance(value, Mapping):
            return None
        value = cast(Mapping[str, Any], value).get(part)
    return value


# -- answers ---


def _whole_number(value: object, minimum: int) -> int | None:
    return value if type(value) is int and value >= minimum else None


def partial_answer(answer: object, index: str) -> bool:
    """Whether a search or count answer is not known to cover every shard, and so must not be shown as complete.

    Partial: a shard failed or the search timed out (OpenSearch answers 200 with the shards that did answer).
    Unverifiable: ``_shards`` or its counts, or ``timed_out``, are missing or of the wrong type. Both are logged at
    ERROR with counts and names, never values.
    """
    if not isinstance(answer, Mapping):
        log.error('opensearch_answer_unverifiable', extra={'index': index, 'unreadable': ['answer']})
        return True
    fields = cast(Mapping[str, Any], answer)
    shards = fields.get('_shards')
    shard_fields = cast(Mapping[str, Any], shards) if isinstance(shards, Mapping) else {}
    total = _whole_number(shard_fields.get('total'), 1)
    failed = _whole_number(shard_fields.get('failed'), 0)
    timed_out = fields.get('timed_out')
    if failed or timed_out is True:
        log.error(
            'opensearch_answer_partial',
            extra={'index': index, 'timed_out': timed_out is True, 'shards_total': total, 'shards_failed': failed},
        )
        return True
    unreadable = (
        ['_shards']
        if not isinstance(shards, Mapping)
        else [name for name, value in (('_shards.total', total), ('_shards.failed', failed)) if value is None]
    )
    if 'timed_out' in fields and not isinstance(timed_out, bool):
        unreadable.append('timed_out')
    if unreadable:
        log.error('opensearch_answer_unverifiable', extra={'index': index, 'unreadable': unreadable})
        return True
    return False
