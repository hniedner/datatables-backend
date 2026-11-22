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

from ._plan import Bound
from ._plan import Capabilities
from ._plan import Contains
from ._plan import Empty
from ._plan import Equals
from ._plan import Filter
from ._plan import Group
from ._plan import Mistakes
from ._plan import Not
from ._plan import OneOf
from ._plan import Part
from ._plan import Plan
from ._plan import Range
from ._plan import mistake
from ._plan import next_day
from ._plan import plan
from ._plan import refused
from ._text_query import QueryParser
from .cache import TotalCache
from .columns import Column
from .columns import ColumnType
from .protocol import DTParams
from .protocol import DTResponse
from .protocol import InvalidFilter
from .protocol import PartialResultError
from .protocol import SchemaError
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
        unsortable = [column.name for column in self.columns if column.orderable and not _sortable(column)]
        if unsortable:
            raise SchemaError(
                f'{", ".join(unsortable)}: analysed text has no whole string to sort by, so it cannot be orderable'
            )

    async def fetch(self, params: DTParams, client: AsyncClient) -> DTResponse:
        """Answer one request with an async client (``AsyncOpenSearch``).

        A mistake the user can fix is answered with its ``error_code`` (see ``DTResponse``) and logged once, before
        anything is sent; any other failure raises, a partial answer as ``PartialResultError``.
        """
        try:
            request = self._request(params)
        except InvalidFilter as found:
            return refused(params, found)
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
        except InvalidFilter as found:
            return refused(params, found)
        answer = client.search(index=self.index, body=request.search)
        filtered = self._filtered(answer)
        total = self._cached_total(request, filtered)
        if total is None:
            total = self._count(client.count(index=self.index, body=cast(dict[str, Any], request.count)))
            self._remember(total)
        return self._response(params, answer, total, filtered)

    # -- building ---------------------------------------------------------------------------------------------------

    def _request(self, params: DTParams) -> _Request:
        """Both request bodies, built before either is sent.

        Raises:
            InvalidFilter: the one of the user's mistakes that decides the answer, from the plan or from the clauses
                built here; a page beyond the result window only when the request holds no other. It is raised in
                one place, once every clause is built.
        """
        mistakes = Mistakes()
        request = plan(params, self.columns, _describe, mistakes)
        must: list[dict[str, Any]] = [self.base_filter] if self.base_filter else []
        if request.global_search:
            must.append(_any([_global_clause(search) for search in request.global_search]))
        for column_search in request.column_searches:
            with mistakes.part():
                must.append(_compile(column_search))
        if request.search_builder is not None:
            must.append(_compile(request.search_builder))
        with mistakes.part():
            if params.start + params.length > self.max_result_window:
                raise mistake(
                    'datatables_page_out_of_range',
                    f'This table can be paged through its first {self.max_result_window:,} rows only. Go back to an '
                    'earlier page, or narrow the search so fewer rows match.',
                )
        mistakes.raise_first()
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


def _sortable(column: Column) -> bool:
    """Whether the index holds something to sort the column by: not for a string that is analysed text only, on
    which OpenSearch refuses the whole search."""
    return _type_of(column) != 'string' or _keyword(column) is not None


def _describe(column: Column) -> Capabilities:
    """A column can be sorted by unless it is declared not to, or left undeclared and not sortable. Only a string
    takes part in the global search, and a column search on a number or a date is one value: OpenSearch cannot match
    a number or a date as text without a script (PRD-F32, PRD-F33, PRD-F34)."""
    kind = _type_of(column)
    orderable = _sortable(column) if column.orderable is None else column.orderable
    return Capabilities(kind, orderable, searched=kind == 'string', value_search=True)


def _any(clauses: list[dict[str, Any]]) -> dict[str, Any]:
    return {'bool': {'should': clauses, 'minimum_should_match': 1}}


def _not(clause: dict[str, Any]) -> dict[str, Any]:
    return {'bool': {'must_not': [clause]}}


def _folds(column: Column) -> bool:
    """Whether an exact match on a string column ignores case: not when it is declared ``fold_case=False`` or a hook
    transforms what is searched for. The query is then sent plain, and the field's mapping decides (PRD-D21,
    PRD-F9)."""
    return column.fold_case and column.search_value is None


# Code points: PRD-F14's longest search text, and the longest value measured as a regexp on every server.
MAX_REGEXP_VALUE = 255


def _cased(char: str) -> bool:
    """Whether ``char`` has another case, as this interpreter's Unicode tables have it."""
    return char.lower() != char or char.upper() != char


def _cased_beyond_ascii(value: str) -> bool:
    """Whether ``value`` holds a character outside ASCII that has another case. On a ``term`` and a ``wildcard``
    query OpenSearch's ``case_insensitive`` folds ASCII letters only; on a ``regexp`` query it folds such a
    character too, from OpenSearch 3.1 on (PRD-F9). Every other value keeps the query it had, by decision: a
    ``regexp`` would fold a few characters more for it (the Kelvin sign to ``k``)."""
    return any(not char.isascii() and _cased(char) for char in value)


def _regexp(field: str, before: str, value: str, after: str) -> dict[str, Any] | None:
    """The whole string is ``value`` in any case, between the patterns ``before`` and ``after``; None for a value
    that keeps its ``term`` or ``wildcard`` query: one without a cased character outside ASCII, and one longer than
    255 code points, beyond which no value was measured as a ``regexp`` on every server (a pattern is then at most
    768 UTF-16 units, under the 1,000 of the default ``index.max_regex_length``).

    A character of ``value`` gets a backslash, also when it is outside ASCII: the server runs the pattern through
    the field's normalizer, which can make an ASCII bracket of a fullwidth one. The backslash makes it that
    character and nothing else, except where a normalizer turns one character into several (PRD section 5 states
    that limit). Three kinds of character get none:

    - an alphanumeric one, which is no operator of the syntax;
    - a cased one that is not alphanumeric (a circled letter): a normalizer can make it an ASCII letter, and a
      backslash before a letter is a class of the syntax or an error;
    - whitespace, which is no operator either: a normalizer that trims takes a space at the end of the pattern and
      would leave its backslash there, which is an error."""
    if len(value) > MAX_REGEXP_VALUE or not _cased_beyond_ascii(value):
        return None
    literal = ''.join(char if char.isalnum() or _cased(char) or char.isspace() else f'\\{char}' for char in value)
    return {'regexp': {field: {'value': f'{before}{literal}{after}', 'case_insensitive': True}}}


def _term(column: Column, field: str, value: str) -> dict[str, Any]:
    """The whole string equals ``value``, in any case where the column folds."""
    if not _folds(column):
        return {'term': {field: value}}
    return _regexp(field, '', value, '') or {'term': {field: {'value': value, 'case_insensitive': True}}}


def _part(column: Column, field: str, value: str, *, start: bool = False, end: bool = False) -> dict[str, Any]:
    """The whole string holds ``value`` in any case: anywhere, or at its ``start`` or its ``end``. A hooked column's
    value is what its hook made of the text, and is sent as before."""
    pattern = f'{"" if start else "*"}{escape_wildcard(value)}{"" if end else "*"}'
    wildcard = {'wildcard': {field: {'value': pattern, 'case_insensitive': True}}}
    if column.search_value is not None:
        return wildcard
    return _regexp(field, '' if start else '.*', value, '' if end else '.*') or wildcard


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
    wildcard = _part(column, keyword, value)
    if _kind(column) == 'keyword':
        return wildcard
    return _any([text_match, wildcard])


def _global_clause(search: Contains) -> dict[str, Any]:
    return _string_contains(search.column, str(search.value), parse=False)


def _compile(node: Filter | Group) -> dict[str, Any]:
    """The clause of one node of the plan."""
    if isinstance(node, Group):
        clauses = [_compile(item) for item in node.items]
        return {'bool': {'must': clauses}} if node.logic == 'AND' else _any(clauses)
    column, field = node.column, node.column.source
    match node:
        case Not(node=Empty()):  # "not empty" is asked for directly, not as the negation of a negation
            return {'exists': {'field': field}}
        case Not():  # a must_not keeps a document without the field, so a missing value passes (PRD-F37)
            return _not(_compile(node.node))
        case Empty():
            return _not({'exists': {'field': field}})
        case Contains():
            return _string_contains(column, str(node.value), parse=True)
        case Range():
            bounds: dict[str, Any] = {}
            if node.lower is not None:
                bounds['gte' if node.lower.included else 'gt'] = node.lower.value
            if node.upper is not None:
                bounds['lte' if node.upper.included else 'lt'] = node.upper.value
            if node.type == 'date':  # days, in the one format every date field reads
                bounds = {**{key: str(day) for key, day in bounds.items()}, 'format': 'strict_date'}  # ISO days
            return {'range': {field: bounds}}
        case Equals() if node.type != 'string':
            return {'term': {field: node.value}}
        case Equals():
            keyword = _keyword(column)
            return _term(column, keyword, str(node.value)) if keyword else {'match_phrase': {field: str(node.value)}}
        case Part():
            keyword = _keyword(column)
            if node.where == 'any' or keyword is None:
                # Analysed text has no whole string to anchor on, so starts and ends read as contains there
                return _string_contains(column, str(node.value), parse=True)
            return _part(column, keyword, str(node.value), start=node.where == 'start', end=node.where == 'end')
        case OneOf():
            return _one_of(node)


def _one_of(node: OneOf) -> dict[str, Any]:
    column = node.column
    if node.type == 'date':  # each day as the range `=` gives it, and a missing field for an empty entry
        days = [_compile(Range(column, 'date', Bound(day, True), Bound(next_day(day), False))) for day in node.values]
        return _any([*days, *([_compile(Empty(column, 'date'))] if node.empty else [])])
    if node.type != 'string':
        return {'terms': {column.source: list(node.values)}}
    field = _keyword(column)
    if field is None:  # analysed text holds no whole string to match exactly
        raise mistake(
            'datatables_search_refused',
            f'The column "{column.name}" can be searched only for text it contains, not for a list of exact '
            'values. Type the text to search for instead.',
            column,
        )
    if not _folds(column):
        return {'terms': {field: list(node.values)}}
    # A terms query takes no case_insensitive, so a list of strings is one term query for each value
    return _any([_term(column, field, str(value)) for value in node.values])


def _sort(request: Plan) -> list[dict[str, Any]]:
    """The user's order, a document without the field as the lowest value: left to itself OpenSearch puts it last
    in either direction. A string is sorted by its keyword field; the plan lets no analysed-only text through."""
    sort: list[dict[str, Any]] = []
    for item in request.order:
        field = (_keyword(item.column) if item.type == 'string' else None) or item.column.source
        placed = {'order': 'desc', 'missing': '_last'} if item.descending else {'order': 'asc', 'missing': '_first'}
        sort.append({field: placed})
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
    successful = _whole_number(shard_fields.get('successful'), 0)
    failed = _whole_number(shard_fields.get('failed'), 0)
    timed_out = fields.get('timed_out')
    # A shard may be missing from the answer without being counted as failed
    missing = total is not None and successful is not None and successful < total
    if failed or missing or timed_out is True:
        log.error(
            'opensearch_answer_partial',
            extra={
                'index': index,
                'timed_out': timed_out is True,
                'shards_total': total,
                'shards_successful': successful,
                'shards_failed': failed,
            },
        )
        return True
    counts = (('_shards.total', total), ('_shards.successful', successful), ('_shards.failed', failed))
    unreadable = ['_shards'] if not isinstance(shards, Mapping) else [name for name, value in counts if value is None]
    if 'timed_out' in fields and not isinstance(timed_out, bool):
        unreadable.append('timed_out')
    if unreadable:
        log.error('opensearch_answer_unverifiable', extra={'index': index, 'unreadable': unreadable})
        return True
    return False
