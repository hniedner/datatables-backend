"""DataTables from an OpenSearch index, with the sync or the async client.

    reports = SearchTable('reports', [IndexColumn('title', text='text'), IndexColumn('id', text='keyword'),
                                      Column('created', type='date')], unique_key='id')
    response = await reports.fetch(params, AsyncOpenSearch(...))

Requests are plain bodies (``client.search(index=..., body=...)``): no DSL library is needed, and any client with
that call shape works.
"""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from decimal import Decimal
import json
import logging
import struct
from typing import Any
from typing import Literal
from typing import Protocol
from typing import cast
from typing import get_args

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
from ._plan import check_declaration
from ._plan import declared_key
from ._plan import mistake
from ._plan import next_day
from ._plan import plan
from ._plan import refused
from .cache import AsyncTotalStore
from .cache import TotalStore
from .cache import key_digest
from .cache import read_total
from .cache import read_total_async
from .cache import require_sync_store
from .cache import write_total
from .cache import write_total_async
from .columns import Column
from .columns import ColumnType
from .protocol import DTParams
from .protocol import DTResponse
from .protocol import InvalidFilter
from .protocol import PartialResultError
from .protocol import SchemaError
from .rows import RowHooks

__all__ = ['IndexColumn', 'NumberKind', 'SearchTable', 'TextKind', 'partial_answer']

log = logging.getLogger('datatables_backend')

#: How a string field is mapped: ``keyword``; ``text`` (analysed only); or ``text`` with a ``.keyword`` subfield,
#: which is what dynamic mapping creates for a string
type TextKind = Literal['keyword', 'text', 'text+keyword']

#: The mapping type of a number field (decision 011). ``byte`` and ``short`` are refused by OpenSearch only beyond an
#: ``integer``'s range, and carry its bounds. ``double`` and ``scaled_float`` refuse nothing a request can send, so they
#: are checked as a double, which every number of at most 255 characters passes. A ``scaled_float`` still clamps
#: a number whose scaled form passes a ``long``, because its scaling factor lives in the mapping, which is never read.
type NumberKind = Literal[
    'byte', 'short', 'integer', 'long', 'unsigned_long', 'float', 'half_float', 'double', 'scaled_float'
]

#: What OpenSearch accepts for each ``NumberKind`` (decision 011): the lowest and the highest number, as the message
#: names them. ``byte`` and ``short`` carry an ``integer``'s, which is where OpenSearch refuses theirs. The floats'
#: range is where a number rounds to infinity in their format, which ``_accepted`` asks of ``struct``.
_INTEGER_BOUNDS = (-(2**31), 2**31 - 1)
_NUMBER_BOUNDS: dict[str, tuple[int | float, int | float]] = {
    'byte': _INTEGER_BOUNDS,
    'short': _INTEGER_BOUNDS,
    'integer': _INTEGER_BOUNDS,
    'long': (-(2**63), 2**63 - 1),
    'unsigned_long': (0, 2**64 - 1),
    'float': (-3.4028234663852886e38, 3.4028234663852886e38),
    'half_float': (-65504, 65504),
    'double': (-1.7976931348623157e308, 1.7976931348623157e308),
    'scaled_float': (-1.7976931348623157e308, 1.7976931348623157e308),
}
_TEXT_KINDS = get_args(TextKind.__value__)
_NUMBER_KINDS = get_args(NumberKind.__value__)
_FLOAT_FORMATS = {'float': '<f', 'half_float': '<e'}  # struct's IEEE 754 single and half precision
_AN = frozenset({'integer', 'unsigned_long'})  # the kinds whose name takes "an"

#: OpenSearch's default ``index.max_result_window``: ``from + size`` beyond it fails the whole search
_MAX_RESULT_WINDOW = 10_000
#: The document's own id: a metadata field, read from the hit (never from ``_source``). OpenSearch deprecates sorting
#: on it (field data), so a column for it is neither orderable nor a key, and it has no text to search
_DOCUMENT_ID = '_id'


@dataclass(frozen=True, kw_only=True)
class IndexColumn(Column):
    """A ``Column`` that says how its string field is mapped. A plain ``Column`` is taken as ``text+keyword``.

    A column whose field is the document id, ``_id``, reads the hit's id. It is never searchable, whatever its
    ``searchable`` says (an id has no text to match), and never orderable: OpenSearch deprecates sorting on ``_id``, so
    ``orderable=True`` on it raises ``SchemaError``.

    A number column (``type='number'``) may say the mapping type of its field with ``number``. A number that OpenSearch
    refuses for that type is then answered with ``invalid_number`` before the query (PRD-D23), where it would otherwise
    fail the request with the client's ``RequestError``. Without it nothing is checked beyond what every number column
    is: finite as a double. ``number`` on a column that is not a number column raises ``SchemaError``, and so does a
    ``text`` or a ``number`` that is none of the kinds. A ``double`` or ``scaled_float`` kind refuses nothing (it is
    checked as a double, which every number of at most 255 characters passes); a ``scaled_float`` still clamps a number
    whose scaled form passes a ``long``, because its scaling factor lives in the mapping, which is never read.
    """

    text: TextKind = 'text+keyword'
    number: NumberKind | None = None


class _SyncClient(Protocol):
    def search(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def count(self, *, index: str, body: Mapping[str, Any]) -> Mapping[str, Any]: ...


class _AsyncClient(Protocol):
    def search(self, *, index: str, body: Mapping[str, Any]) -> Awaitable[Mapping[str, Any]]: ...
    def count(self, *, index: str, body: Mapping[str, Any]) -> Awaitable[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class _Request:
    search: dict[str, Any]
    count: dict[str, Any] | None  # None: the search's own count is the total
    beyond: InvalidFilter | None = None  # set: the page is past the result window, so the counts run first (PRD-C11)
    flipped: list[dict[str, Any]] = field(default_factory=list)  # the sort of a reversed page (PRD-C10), if beyond
    #: The total cache's key: the index, the base filter and this request's scope. It belongs to the request, since two
    #: async requests of one table run half way together. Empty when the table has no cache.
    key: str = ''

    @property
    def filtered_count(self) -> dict[str, Any]:
        """The count of the search's own query: the filtered set, exact where a search's total may be a lower bound."""
        return {'query': self.search['query']}


class SearchTable:
    """A DataTables view of an OpenSearch index. Build it once; ``fetch`` answers each request.

    Args:
        index: The index (or alias, or pattern) to search.
        columns: The columns the client may see, search and sort on; only their fields are fetched. A column whose
            field is the document id (``_id``) reads the hit's id but is neither searchable (whatever its
            ``searchable`` says) nor orderable (``orderable=True`` raises ``SchemaError``), and cannot be the key:
            OpenSearch deprecates sorting on ``_id``.
        unique_key: The name of a declared column, or of several, whose values are unique for every document: the
            tie-breaker of every order (an index has no primary key). It must be sortable, so not analysed text only.
            An index whose documents hold no unique field copies the id into a ``keyword`` field at ingest. Required:
            a table without one raises ``SchemaError``.
        base_filter: A query that limits the documents the table shows at all; it applies to ``recordsTotal`` too. For
            a filter that differs per request, such as a user's documents, pass ``scope`` to ``fetch`` instead.
        rows: Hooks that shape each row and add ``DT_RowId`` / ``DT_RowData``.
        total_cache: Keeps ``recordsTotal`` for requests that filter: a ``TotalCache``, or any ``TotalStore``
            (``AsyncTotalStore`` for ``fetch`` only), PRD-C18. One store per cluster.
        max_result_window: The index's ``max_result_window``. A page beyond it that ends within that many rows of
            the last row is read by a reversed sort after the counts (PRD-C10); any other is refused with the real
            counts, or answered empty when ``start`` is past the last row.
    """

    def __init__(
        self,
        index: str,
        columns: Sequence[Column],
        *,
        unique_key: str | Sequence[str] | None = None,
        base_filter: Mapping[str, Any] | None = None,
        rows: RowHooks | None = None,
        total_cache: TotalStore | AsyncTotalStore | None = None,
        max_result_window: int = _MAX_RESULT_WINDOW,
    ) -> None:
        self.index = index
        self.columns = tuple(_held(column) for column in columns)
        self.base_filter = dict(base_filter) if base_filter else None
        self.rows = rows or RowHooks()
        self.total_cache = total_cache
        self.max_result_window = max_result_window
        check_declaration(self.columns, _describe)
        if unique_key is None:
            raise SchemaError(
                'An index has no primary key to break ties with. Declare one: SearchTable(..., unique_key="id") with '
                'the name of a declared column whose values are unique (a tuple of names for a key of several).'
            )
        self.unique_key = declared_key(self.columns, unique_key)
        if any(column.source == _DOCUMENT_ID for column in self.unique_key):
            raise SchemaError(
                'OpenSearch deprecates sorting on `_id` (field data). Copy the id into a `keyword` field of the '
                'documents and name that column as the key.'
            )
        if unkeyable := [column.name for column in self.unique_key if not _sortable(column)]:
            raise SchemaError(
                f'unique_key {", ".join(unkeyable)}: analysed text has no whole string to sort by. Declare the '
                'column IndexColumn(..., text="keyword") (or "text+keyword"), or name a number or a date.'
            )
        unsortable = [column.name for column in self.columns if column.orderable and not _sortable(column)]
        if unsortable:
            raise SchemaError(
                f'{", ".join(unsortable)}: analysed text has no whole string to sort by, so it cannot be orderable'
            )

    async def fetch(
        self, params: DTParams, client: _AsyncClient, *, scope: Mapping[str, Any] | None = None
    ) -> DTResponse:
        """Answer one request with an async client (``AsyncOpenSearch``).

        ``scope`` is a query that limits the documents for this request only, together with the table's
        ``base_filter``, to ``recordsTotal``, ``recordsFiltered`` and the page alike (PRD-F31). It is written in the
        terms of the stored fields, as ``base_filter`` is, and built by the server from the authenticated user and
        never from the request: a scope taken from the request would let a client choose another user's documents.
        Column hooks do not apply to it. ``None``, and an empty mapping, add nothing. Requests with equal scopes may
        share a cached total; those with different ones never do (PRD-C15).

        A mistake the user can fix is answered with its ``error_code`` (see ``DTResponse``) and logged once; only a
        page beyond the result window sends anything first, the counts of PRD-C11, and then a reversed search for a
        page near the end, none for the refusal or the empty page. Any other failure raises, a partial answer as
        ``PartialResultError``.
        """
        try:
            request = self._request(params, scope)
        except InvalidFilter as found:
            return refused(params, found)
        if request.beyond is not None:
            filtered = self._count(await client.count(index=self.index, body=request.filtered_count))
            total = await self._cached_total_async(request, filtered)
            if total is None:
                total = self._count(await client.count(index=self.index, body=cast(dict[str, Any], request.count)))
                await self._remember_async(total, request.key)
            if (page := self._reversed_page(params, request, filtered)) is None:
                return self._past_the_window(params, request.beyond, total, filtered)
            answer = await client.search(index=self.index, body=page)
            self._filtered(answer)  # raises on a partial answer; the counts that decided the page are the answer's
            return self._response(params, answer, total, filtered, reverse=True)
        answer = await client.search(index=self.index, body=request.search)
        filtered = self._filtered(answer)
        if _lower_bound(answer):
            filtered = self._count(await client.count(index=self.index, body=request.filtered_count))
        total = await self._cached_total_async(request, filtered)
        if total is None:
            total = self._count(await client.count(index=self.index, body=cast(dict[str, Any], request.count)))
            await self._remember_async(total, request.key)
        return self._response(params, answer, total, filtered)

    def fetch_sync(
        self, params: DTParams, client: _SyncClient, *, scope: Mapping[str, Any] | None = None
    ) -> DTResponse:
        """``fetch`` with a sync client (``OpenSearch``), with the same ``scope``.

        Raises:
            TypeError: the table's ``total_cache`` is an ``AsyncTotalStore``, which only ``fetch`` can await.
        """
        if self.total_cache is not None:
            require_sync_store(self.total_cache)
        try:
            request = self._request(params, scope)
        except InvalidFilter as found:
            return refused(params, found)
        if request.beyond is not None:
            filtered = self._count(client.count(index=self.index, body=request.filtered_count))
            total = self._cached_total(request, filtered)
            if total is None:
                total = self._count(client.count(index=self.index, body=cast(dict[str, Any], request.count)))
                self._remember(total, request.key)
            if (page := self._reversed_page(params, request, filtered)) is None:
                return self._past_the_window(params, request.beyond, total, filtered)
            answer = client.search(index=self.index, body=page)
            self._filtered(answer)  # raises on a partial answer; the counts that decided the page are the answer's
            return self._response(params, answer, total, filtered, reverse=True)
        answer = client.search(index=self.index, body=request.search)
        filtered = self._filtered(answer)
        if _lower_bound(answer):
            filtered = self._count(client.count(index=self.index, body=request.filtered_count))
        total = self._cached_total(request, filtered)
        if total is None:
            total = self._count(client.count(index=self.index, body=cast(dict[str, Any], request.count)))
            self._remember(total, request.key)
        return self._response(params, answer, total, filtered)

    # -- building ---------------------------------------------------------------------------------------------------

    def _request(self, params: DTParams, scope: Mapping[str, Any] | None) -> _Request:
        """Both request bodies, built before either is sent.

        Raises:
            InvalidFilter: the one of the user's mistakes that decides the answer, from the plan or from the clauses
                built here. It is raised in one place, once every clause is built. A page beyond the result window is
                no raise: it is the last step of PRD-E3, and the request holds it for the counts to run first.
        """
        mistakes = Mistakes()
        request = plan(params, self.columns, _describe, mistakes)
        # The base filter and the scope limit every count; an empty mapping is no clause, which OpenSearch would refuse
        limits = [limit for limit in (self.base_filter, dict(scope) if scope else None) if limit]
        must: list[dict[str, Any]] = list(limits)
        if request.global_search is not None:
            must.append(_compile(request.global_search))
        for column_search in request.column_searches:
            with mistakes.part():
                must.append(_compile(column_search))
        if request.search_builder is not None:
            with mistakes.part():  # a number the field refuses (step 3) waits for the plan's own, earlier mistakes
                must.append(_compile(request.search_builder))
        mistakes.raise_first()
        beyond = None
        if params.start + params.length > self.max_result_window:  # the last step of PRD-E3, so any other mistake won
            beyond = mistake(
                'datatables_page_out_of_range',
                f'This table can be paged through its first {self.max_result_window:,} rows only. Go back to an '
                'earlier page, or narrow the search so fewer rows match.',
            )
        search = {
            'query': {'bool': {'must': must}} if must else {'match_all': {}},
            'from': params.start,
            'size': params.length,
            'track_total_hits': True,
            # The id is in the hit, not in `_source`; the key is a column of another field, so the list is never empty
            '_source': [column.source for column in self.columns if column.source != _DOCUMENT_ID],
        }
        search['sort'] = _sort(request, self.unique_key)
        count = {'query': {'bool': {'must': limits}} if limits else {'match_all': {}}}
        flipped = _sort(request, self.unique_key, flip=True) if beyond else []
        key = (
            key_digest(f'{self.index} {json.dumps(limits, sort_keys=True, default=str)}')
            if self.total_cache is not None
            else ''
        )
        return _Request(search, count if request.filtered else None, beyond, flipped, key)

    # -- running ----------------------------------------------------------------------------------------------------

    def _filtered(self, answer: Mapping[str, Any]) -> int:
        if partial_answer(answer, index=self.index):
            raise PartialResultError(f'The search of {self.index} did not cover every shard.')
        total = answer['hits']['total']
        # track_total_hits makes it exact; an older engine may still answer a bare number
        return int(total['value'] if isinstance(total, Mapping) else total)

    def _cached_total(self, request: _Request, filtered: int) -> int | None:
        """The total without asking again: the search's own count when the request filters nothing, else the store."""
        if request.count is None:
            self._remember(filtered, request.key)
            return filtered
        return read_total(self.total_cache, request.key) if self.total_cache is not None else None

    def _remember(self, total: int, key: str) -> None:
        if self.total_cache is not None:
            write_total(self.total_cache, key, total)

    async def _cached_total_async(self, request: _Request, filtered: int) -> int | None:
        """``_cached_total`` for ``fetch``, which awaits an async store."""
        if request.count is None:
            await self._remember_async(filtered, request.key)
            return filtered
        return await read_total_async(self.total_cache, request.key) if self.total_cache is not None else None

    async def _remember_async(self, total: int, key: str) -> None:
        if self.total_cache is not None:
            await write_total_async(self.total_cache, key, total)

    def _count(self, answer: Mapping[str, Any]) -> int:
        if partial_answer(answer, index=self.index):
            raise PartialResultError(f'The count of {self.index} did not cover every shard.')
        return int(answer['count'])

    def _reversed_page(self, params: DTParams, request: _Request, filtered: int) -> dict[str, Any] | None:
        """The search of a page beyond the result window that the reverse sort reaches (PRD-C10): the rows
        ``start`` to ``end - 1``, ``end`` being ``min(start + length, filtered)``, are the first ones of the flipped
        sort from position ``filtered - end``, and ``from`` plus ``size`` is ``filtered - start``, within the window.
        None when ``start`` is at or past the end (an empty page, PRD-C3) or the page is in the middle (PRD-C12)."""
        if params.start >= filtered or filtered - params.start > self.max_result_window:
            return None
        end = min(params.start + params.length, filtered)
        return {**request.search, 'from': filtered - end, 'size': end - params.start, 'sort': request.flipped}

    def _past_the_window(self, params: DTParams, beyond: InvalidFilter, total: int, filtered: int) -> DTResponse:
        """The answer for a page beyond the result window, once the counts are in: an empty page when ``start`` is at
        or past the end (PRD-C3 wins), else the refusal carrying the real counts (PRD-C12)."""
        if params.start >= filtered:
            return DTResponse(draw=params.draw, recordsTotal=total, recordsFiltered=filtered, data=[])
        return refused(params, beyond).model_copy(update={'recordsTotal': total, 'recordsFiltered': filtered})

    def _response(
        self, params: DTParams, answer: Mapping[str, Any], total: int, filtered: int, *, reverse: bool = False
    ) -> DTResponse:
        hits = cast(list[Mapping[str, Any]], answer['hits']['hits'])
        if reverse:  # a reversed page comes back from the end: the rows are shown in the order asked
            hits = hits[::-1]
        data = [self.rows.apply(self._row(hit)) for hit in hits]
        return DTResponse(draw=params.draw, recordsTotal=total, recordsFiltered=filtered, data=data)

    def _row(self, hit: Mapping[str, Any]) -> dict[str, Any]:
        source = hit.get('_source') or {}
        return {
            column.name: hit.get(_DOCUMENT_ID) if column.source == _DOCUMENT_ID else _get(source, column.source)
            for column in self.columns
        }


def _lower_bound(answer: Mapping[str, Any]) -> bool:
    """Whether the search's total says only "at least this many" (PRD-C9): ``track_total_hits`` is sent, but a server
    or a proxy in between may not honour it."""
    total = answer['hits']['total']
    return isinstance(total, Mapping) and total.get('relation') == 'gte'


def _check_kinds(column: Column) -> None:
    """Raise ``SchemaError`` for a ``text`` or a ``number`` that is none of the kinds: a ``Literal`` checks nothing at
    run time, and the table would build and fail on its first filter. The message names the column, what was written
    and the accepted kinds, all of which the server declared."""
    if not isinstance(column, IndexColumn):
        return
    for field_name, written, accepted in (('text', column.text, _TEXT_KINDS), ('number', column.number, _NUMBER_KINDS)):
        if written is not None and written not in accepted:
            raise SchemaError(
                f'The column "{column.name}" declares {field_name}="{written}", which is not a kind. Declare one of '
                f'{", ".join(accepted)}.'
            )


def _held(column: Column) -> Column:
    """The column as the table holds it. One for the document id is held neither searchable nor orderable, so the
    plan refuses a filter or an order on it by its rules for any column the server declares unsearchable or
    unorderable, and the global search leaves it out. ``orderable=True`` on it is the server's mistake. A field type
    declared on a column that is no number column is the server's mistake too, and so is a kind that is none of them."""
    _check_kinds(column)
    if isinstance(column, IndexColumn) and column.number is not None and _type_of(column) != 'number':
        raise SchemaError(
            f'The column "{column.name}" declares number="{column.number}" but is not a number column, so no number '
            f'range applies to it. Declare it IndexColumn("{column.name}", type="number", number="{column.number}"), '
            'or drop number=.'
        )
    if column.source != _DOCUMENT_ID:
        return column
    if column.orderable:
        raise SchemaError(
            f'The column "{column.name}" shows the document id and cannot be orderable: OpenSearch deprecates sorting '
            'on `_id` (field data). Order by a `keyword` field instead.'
        )
    return replace(column, searchable=False, orderable=False)


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
    a number or a date as text without a script (PRD-F32, PRD-F33, PRD-F34). What can be sorted by is also what holds
    a whole string, to equal, to anchor on and to hook; a string that is analysed text only holds none (PRD-D19,
    PRD-D20)."""
    kind = _type_of(column)
    orderable = _sortable(column) if column.orderable is None else column.orderable
    return Capabilities(kind, orderable, searched=kind == 'string', value_search=True, whole_string=_sortable(column))


def _any(clauses: list[dict[str, Any]]) -> dict[str, Any]:
    return {'bool': {'should': clauses, 'minimum_should_match': 1}}


def _not(clause: dict[str, Any]) -> dict[str, Any]:
    return {'bool': {'must_not': [clause]}}


def _folds(column: Column) -> bool:
    """Whether an exact match on a string column ignores case: not when it is declared ``fold_case=False`` or a hook
    transforms what is searched for. The query is then sent plain, and the field's mapping decides (PRD-D21,
    PRD-F9)."""
    return column.fold_case and column.search_value is None


def _cased(char: str) -> bool:
    """Whether ``char`` has another case, as this interpreter's Unicode tables have it."""
    return char.lower() != char or char.upper() != char


def _cased_beyond_ascii(value: str) -> bool:
    """Whether ``value`` holds a character outside ASCII that has another case. On a ``term`` and a ``wildcard``
    query OpenSearch's ``case_insensitive`` folds ASCII letters only; on a ``regexp`` query it folds such a
    character too, from OpenSearch 3.1 on (PRD-F9). Every other value keeps the query it had, by decision: a
    ``regexp`` would fold a few characters more for it (the Kelvin sign to ``k``)."""
    return any(not char.isascii() and _cased(char) for char in value)


#: The fullwidth asterisk, question mark and backslash. A normalizer that folds to ASCII (``asciifolding``) makes
#: them ``*``, ``?`` and ``\``, in the pattern of a ``wildcard`` query too, where they are then its own syntax; a
#: backslash before the mark does not help there (measured, docs/decisions/003).
_FOLDED_TO_A_WILDCARD = frozenset({chr(0xFF0A), chr(0xFF1F), chr(0xFF3C)})


def _regexp(field: str, before: str, value: str, after: str, *, marks: bool = False) -> dict[str, Any] | None:
    """The whole string is ``value`` in any case, between the patterns ``before`` and ``after``; None for a value
    that keeps its ``term`` or ``wildcard`` query: one without a cased character outside ASCII (and, where ``marks``
    is asked, without a fullwidth mark that a ``wildcard`` would misread). A value is at most 255 code points
    (PRD-F14), the longest measured as a ``regexp`` on every server: a pattern is then at most 768 UTF-16 units,
    under the 1,000 of the default ``index.max_regex_length``.

    A character of ``value`` gets a backslash, also when it is outside ASCII: the server runs the pattern through
    the field's normalizer, which can make an ASCII bracket of a fullwidth one. The backslash makes it that
    character and nothing else, except where a normalizer turns one character into several (PRD section 5 states
    that limit). Three kinds of character get none:

    - an alphanumeric one, which is no operator of the syntax;
    - a cased one that is not alphanumeric (a circled letter): a normalizer can make it an ASCII letter, and a
      backslash before a letter is a class of the syntax or an error;
    - whitespace, which is no operator either: a normalizer that trims takes a space at the end of the pattern and
      would leave its backslash there, which is an error."""
    wanted = _cased_beyond_ascii(value) or (marks and not _FOLDED_TO_A_WILDCARD.isdisjoint(value))
    if not wanted:
        return None
    literal = ''.join(char if char.isalnum() or _cased(char) or char.isspace() else f'\\{char}' for char in value)
    return {'regexp': {field: {'value': f'{before}{literal}{after}', 'case_insensitive': True}}}


def _term(column: Column, field: str, value: str) -> dict[str, Any]:
    """The whole string equals ``value``, in any case where the column folds."""
    if not _folds(column):
        return {'term': {field: value}}
    return _regexp(field, '', value, '') or {'term': {field: {'value': value, 'case_insensitive': True}}}


def _part(column: Column, field: str, value: str, *, start: bool = False, end: bool = False) -> dict[str, Any]:
    """The whole string holds ``value`` in any case: anywhere, or at its ``start`` or its ``end``. A value with a
    fullwidth mark that a ``wildcard`` would misread is a ``regexp`` too. A hooked column's value is what its hook
    made of the text, and is sent as before."""
    pattern = f'{"" if start else "*"}{_escape_wildcard(value)}{"" if end else "*"}'
    wildcard = {'wildcard': {field: {'value': pattern, 'case_insensitive': True}}}
    if column.search_value is not None:
        return wildcard
    return _regexp(field, '' if start else '.*', value, '' if end else '.*', marks=True) or wildcard


def _escape_wildcard(value: str) -> str:
    """``value`` with the wildcard query's special characters escaped: ``\\``, ``*``, ``?``."""
    return value.replace('\\', '\\\\').replace('*', '\\*').replace('?', '\\?')


def _string_contains(column: Column, value: str) -> dict[str, Any]:
    """A string column contains ``value``: a wildcard on the whole string, a phrase of its tokens on analysed text, or
    either. A phrase also where the analyser splits one word (``a-b``): a ``match`` would find text with ``a`` alone."""
    keyword = _keyword(column)
    text_match = {'match_phrase': {column.source: value}}
    if keyword is None:
        return text_match
    wildcard = _part(column, keyword, value)
    if _kind(column) == 'keyword':
        return wildcard
    return _any([text_match, wildcard])


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
        case Contains():  # one word: the query syntax is no part of a search (PRD-F8)
            return _string_contains(column, str(node.value))
        case Range():
            bounds: dict[str, Any] = {}
            if node.lower is not None:
                bounds['gte' if node.lower.included else 'gt'] = _number(column, node.lower.value)
            if node.upper is not None:
                bounds['lte' if node.upper.included else 'lt'] = _number(column, node.upper.value)
            if node.type == 'date':  # days, in the one format every date field reads
                bounds = {**{key: str(day) for key, day in bounds.items()}, 'format': 'strict_date'}  # ISO days
            return {'range': {field: bounds}}
        case Equals() if node.type != 'string':
            return {'term': {field: _number(column, node.value)}}
        case Equals():  # the plan lets no analysed-only text through to a match that needs the whole string (PRD-D19)
            return _term(column, cast(str, _keyword(column)), str(node.value))
        case Part() if node.where == 'any':
            return _string_contains(column, str(node.value))
        case Part():
            return _part(
                column,
                cast(str, _keyword(column)),
                str(node.value),
                start=node.where == 'start',
                end=node.where == 'end',
            )
        case OneOf():
            return _one_of(node)


def _accepted(kind: str, sent: int | float) -> bool:
    """Whether OpenSearch answers ``sent`` on a field of ``kind``. A float field refuses a number that rounds to
    infinity in its format, which is where ``struct.pack`` raises ``OverflowError``. It is given a float: a whole number
    too large for one raises ``OverflowError`` in ``float`` itself, where ``struct`` would raise ``struct.error``."""
    if kind in _FLOAT_FORMATS:
        # A whole number of 39 digits in [M - 2^74, M), M = 2^128 - 2^103, rounds to exactly M as a double and is
        # refused here, where the server might round it straight to a float it holds: a conservative refusal
        try:
            struct.pack(_FLOAT_FORMATS[kind], float(sent))
        except OverflowError:
            return False
        return True
    low, high = _NUMBER_BOUNDS[kind]
    return low <= sent <= high  # exact: Python compares an int and a float without rounding either


def _number(column: Column, value: Any) -> Any:
    """``value`` as the request body carries it (``_json``), checked against the field type the column declares: a
    number OpenSearch refuses for it is the user's mistake, answered before the query (PRD-D23). The check is on what is
    sent: a number with a point goes as a float, which may round to the edge."""
    sent = _json(value)
    kind = column.number if isinstance(column, IndexColumn) else None
    if kind is None or not isinstance(sent, int | float) or _accepted(kind, sent):
        return sent
    low, high = _NUMBER_BOUNDS[kind]
    article = 'an' if kind in _AN else 'a'
    raise mistake(
        'datatables_number_out_of_range',
        f'A number in the filter on "{column.name}" must be one OpenSearch accepts for {article} {kind} field, between '
        f'{low} and {high}; use a number within that range.',
        column,
    )


def _json(value: Any) -> Any:
    """``value`` as the request body carries it: a number with a point or an exponent is a float, which is what
    OpenSearch reads a query's number as, so the body stays plain JSON (SPEC-A8.1). A zero is ``0``, as ``0`` is sent:
    ``-0.00`` would be the float -0.0, which a ``double`` field keeps apart from 0.0 (PRD-F25, docs/databases.md)."""
    if not isinstance(value, Decimal):
        return value
    return 0 if value.is_zero() else float(value)


def _one_of(node: OneOf) -> dict[str, Any]:
    """A list of exact values, and the documents without the field where the list holds an empty entry (PRD-F17,
    PRD-F20), which is never one of the values sent."""
    column = node.column
    missing = [_compile(Empty(column, node.type))] if node.empty else []
    if node.type == 'date':  # each day as the range `=` gives it
        days = [_compile(Range(column, 'date', Bound(day, True), Bound(next_day(day), False))) for day in node.values]
        return _any([*days, *missing])
    # The plan lets no list of values through on analysed text (PRD-D19), only an all-empty one, which names no field
    field = _keyword(column) if node.type == 'string' else column.source
    if field is not None and node.type == 'string' and _folds(column):
        # A terms query takes no case_insensitive, so a list of strings is one term query for each value
        return _any([*(_term(column, field, str(value)) for value in node.values), *missing])
    clauses = [
        *([{'terms': {field: [_number(column, value) for value in node.values]}}] if node.values else []),
        *missing,
    ]
    return clauses[0] if len(clauses) == 1 else _any(clauses)


def _sort(request: Plan, key: tuple[Column, ...], *, flip: bool = False) -> list[dict[str, Any]]:
    """The user's order, then the unique key (PRD-O3) in the direction of the first applied entry, ascending when
    there is none (PRD-O4); a key column the order names is not named again. A document without the field is the
    lowest value: left to itself OpenSearch puts it last in either direction. A string is sorted by its keyword
    field; the plan lets no analysed-only text through, and the key is checked at construction.

    ``flip`` gives the sort of a reversed page (PRD-C10): every entry, the key too, runs the other way, a missing value
    staying the lowest, so that the rows come back in exactly the reverse order. A field of several values is sorted by
    its least value ascending and its greatest descending, so a flipped entry names the ``mode`` of the direction it
    flips, or it would not reverse the order it flips."""
    descending = bool(request.order) and request.order[0].descending
    entries = [(item.column, item.type, item.descending) for item in request.order]
    entries += [
        (column, _type_of(column), descending)
        for column in key
        if all(column is not item.column for item in request.order)
    ]
    sort: list[dict[str, Any]] = []
    for column, kind, original in entries:
        name = (_keyword(column) if kind == 'string' else None) or column.source
        reverse = original != flip
        placed = {'order': 'desc', 'missing': '_last'} if reverse else {'order': 'asc', 'missing': '_first'}
        if flip:
            placed['mode'] = 'max' if original else 'min'
        sort.append({name: placed})
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


def partial_answer(answer: object, *, index: str) -> bool:
    """Whether a search or count answer is not known to cover every shard, and so must not be shown as complete.

    Returns True for an unverifiable answer as well as a partial one. Partial: a shard failed or the search timed out
    (OpenSearch answers 200 with the shards that did answer). Unverifiable: ``_shards`` or its counts, or
    ``timed_out``, are missing or of the wrong type, or the answer is not a mapping. It logs at ERROR when it returns
    True, with counts and names, never values. It is meant for the answers of ``search`` and ``count``.
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
