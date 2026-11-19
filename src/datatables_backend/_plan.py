"""A request checked against the declared columns, as typed filters every backend compiles the same way.

Everything the user can get wrong is found here, before a backend builds or runs anything, and raised as
``InvalidFilter`` with a reason that never quotes their input.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from datetime import timedelta
import logging
import math
import re
from typing import Any

from .columns import Column
from .columns import ColumnType
from .protocol import NO_USABLE_COLUMN
from .protocol import Condition
from .protocol import DTParams
from .protocol import InvalidFilter
from .protocol import Search
from .protocol import SearchCriterion
from .protocol import SearchGroup

log = logging.getLogger('datatables_backend')

REGEX_REFUSED = 'Regular-expression search is not supported.'
VALUE_AND_VALUES = 'A column search takes a value or a list of values, not both.'
GLOBAL_VALUES = 'The global search takes a value, not a list of values.'
DATE_FORMAT = 'Dates must be written as YYYY-MM-DD.'
OUT_OF_RANGE = 'The filter value is outside the range of this column.'

_STRING_CONDITIONS = frozenset(
    {'=', '!=', 'contains', '!contains', 'starts', '!starts', 'ends', '!ends', 'null', '!null'}
)
_NUMBER_CONDITIONS = frozenset({'=', '!=', '<', '<=', '>', '>=', 'between', '!between', 'null', '!null'})
_DATE_CONDITIONS = _NUMBER_CONDITIONS | {'contains', '!contains'}
_CONDITIONS: Mapping[ColumnType, frozenset[str]] = {
    'string': _STRING_CONDITIONS,
    'number': _NUMBER_CONDITIONS,
    'date': _DATE_CONDITIONS,
}
_REGEX_SPECIAL = frozenset('.*+?[]{}()^$|')


@dataclass(frozen=True)
class DaySpan:
    """The days a date "contains" names: ``start`` up to, not including, ``end`` (a year, a month or a day)."""

    start: date
    end: date


@dataclass(frozen=True)
class Contains:
    """A substring search on one column. ``value`` is the user's text after the column's ``search_value`` hook."""

    column: Column
    type: ColumnType
    value: Any


@dataclass(frozen=True)
class AnyOf:
    """A multi-select: the column equals one of ``values``."""

    column: Column
    type: ColumnType
    values: tuple[Any, ...]


@dataclass(frozen=True)
class Criterion:
    """A SearchBuilder condition. ``values`` are parsed for the column's type: text (after the hook), numbers
    (``int`` or ``float``), dates, or one ``DaySpan`` for a date "contains"."""

    column: Column
    type: ColumnType
    condition: Condition
    values: tuple[Any, ...]


@dataclass(frozen=True)
class Group:
    """A SearchBuilder group that holds at least one criterion."""

    logic: str
    items: tuple[Criterion | Group, ...]


@dataclass(frozen=True)
class Order:
    column: Column
    type: ColumnType
    descending: bool


@dataclass(frozen=True)
class Plan:
    """What one request asks for. ``global_search`` is joined by OR; everything else by AND."""

    global_search: tuple[Contains, ...]
    column_searches: tuple[Contains | AnyOf, ...]
    search_builder: Criterion | Group | None
    order: tuple[Order, ...]

    @property
    def filtered(self) -> bool:
        """Whether the request narrows the rows beyond the table's base filter."""
        return bool(self.global_search or self.column_searches or self.search_builder)


def plan(params: DTParams, columns: Sequence[Column], type_of: Callable[[Column], ColumnType]) -> Plan:
    """Check ``params`` against ``columns`` and return what it asks for.

    Raises:
        InvalidFilter: the request uses a name that is not declared, sorts or searches where the declaration does not
            allow it, sends a regex, or a SearchBuilder condition the column's type does not take or a value it cannot
            read.
    """
    declared = {column.name: column for column in columns}

    def lookup(name: str) -> Column:
        column = declared.get(name)
        if column is None:
            # Never the name: it is the client's text
            log.warning('datatables_name_not_declared')
            raise InvalidFilter(NO_USABLE_COLUMN)
        return column

    def hooked(column: Column, text: str) -> Any:
        return column.search_value(text) if column.search_value else text

    order: list[Order] = []
    for item in params.order:
        if not 0 <= item.column < len(params.columns):
            raise InvalidFilter(NO_USABLE_COLUMN)
        requested = params.columns[item.column]
        column = lookup(requested.key)
        if not (requested.orderable and column.orderable):
            log.warning('datatables_order_refused', extra={'column': column.name})
            raise InvalidFilter(NO_USABLE_COLUMN)
        order.append(Order(column, type_of(column), item.dir == 'desc'))

    global_search: list[Contains] = []
    if params.search.multi_values:
        raise InvalidFilter(GLOBAL_VALUES)
    if params.search.value:
        if params.search.regex:
            raise InvalidFilter(REGEX_REFUSED)
        for requested in params.columns:
            if requested.searchable and requested.key:
                column = lookup(requested.key)
                if column.searchable:
                    global_search.append(Contains(column, type_of(column), hooked(column, params.search.value)))
        if not global_search:
            raise InvalidFilter(NO_USABLE_COLUMN)

    column_searches: list[Contains | AnyOf] = []
    for requested in params.columns:
        search = requested.search
        if not (search.value or search.multi_values):
            continue
        column = lookup(requested.key)
        if not column.searchable:
            raise InvalidFilter(NO_USABLE_COLUMN)
        column_searches.append(_column_search(search, column, type_of(column), hooked))

    def node(item: SearchGroup | SearchCriterion) -> Criterion | Group | None:
        if isinstance(item, SearchCriterion):
            return _criterion(item, lookup(item.origData), type_of, hooked)
        built = [part for part in (node(child) for child in item.criteria) if part is not None]
        if not built:
            return None  # an empty group adds nothing
        return built[0] if len(built) == 1 else Group(item.logic, tuple(built))

    search_builder = node(params.searchBuilder) if params.searchBuilder else None
    return Plan(tuple(global_search), tuple(column_searches), search_builder, tuple(order))


def _criterion(
    item: SearchCriterion,
    column: Column,
    type_of: Callable[[Column], ColumnType],
    hooked: Callable[[Column, str], Any],
) -> Criterion:
    kind = type_of(column)
    condition = item.condition
    if condition not in _CONDITIONS[kind]:
        # The condition is one of the protocol's literals, never free text
        raise InvalidFilter(f'Condition "{condition}" is not supported on the column "{column.name}".')
    needed = 0 if condition in ('null', '!null') else 2 if condition in ('between', '!between') else 1
    if len(item.value) < needed:
        raise InvalidFilter(f'The filter on "{column.name}" needs {"two values" if needed == 2 else "a value"}.')
    texts = item.value[:needed]
    values: tuple[Any, ...]
    if kind == 'string':
        values = tuple(hooked(column, text) for text in texts)
    elif kind == 'number':
        values = tuple(parse_number(text) for text in texts)
    elif condition in ('contains', '!contains'):
        values = (day_span(texts[0]),)
    else:
        values = tuple(parse_day(text) for text in texts)
    return Criterion(column, kind, condition, values)


def parse_number(text: str) -> int | float:
    """The number ``text`` names: an ``int`` when it is a whole number written without a point, else a ``float``."""
    stripped = text.strip()
    if stripped.lstrip('+-').isdigit():
        return int(stripped)
    try:
        number = float(stripped)
    except ValueError:
        number = math.nan
    if not math.isfinite(number):
        raise InvalidFilter('The filter value must be a number.')
    return number


def parse_day(text: str) -> date:
    """The day ``text`` names (``YYYY-MM-DD``; a time after it is ignored)."""
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        # The parser's message quotes the value, which may be a date of birth
        raise InvalidFilter(DATE_FORMAT) from None


def next_day(day: date) -> date:
    """The day after ``day``; there is none after 9999-12-31, which is the user's filter, not a failed query."""
    try:
        return day + timedelta(days=1)
    except OverflowError:
        raise InvalidFilter(OUT_OF_RANGE) from None


def day_span(text: str) -> DaySpan:
    """The days a date "contains" names: ``YYYY`` a year, ``YYYY-MM`` a month, ``YYYY-MM-DD`` a day."""
    value = text.strip()
    try:
        if re.fullmatch(r'\d{4}', value):
            year = int(value)
            return DaySpan(date(year, 1, 1), date(year + 1, 1, 1) if year < 9999 else _overflow())
        if re.fullmatch(r'\d{4}-\d{2}', value):
            year, month = (int(part) for part in value.split('-'))
            start = date(year, month, 1)
            end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
            return DaySpan(start, end)
    except ValueError:
        raise InvalidFilter('Dates must be written as YYYY, YYYY-MM or YYYY-MM-DD.') from None
    day = parse_day(value) if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) else None
    if day is None:
        raise InvalidFilter('Dates must be written as YYYY, YYYY-MM or YYYY-MM-DD.')
    return DaySpan(day, next_day(day))


def _overflow() -> date:
    raise InvalidFilter(OUT_OF_RANGE)


def _column_search(
    search: Search, column: Column, kind: ColumnType, hooked: Callable[[Column, str], Any]
) -> Contains | AnyOf:
    """One column's search: a list of exact values (``multi_values`` or a multi-select regex), or text it contains."""
    if search.multi_values and search.value:
        raise InvalidFilter(VALUE_AND_VALUES)
    if search.multi_values:
        return AnyOf(column, kind, tuple(hooked(column, v) for v in search.multi_values))
    if search.regex:
        alternatives = _alternatives(search.value)
        if alternatives is None:
            raise InvalidFilter(REGEX_REFUSED)
        return AnyOf(column, kind, tuple(hooked(column, v) for v in alternatives))
    return Contains(column, kind, hooked(column, search.value))


def _alternatives(pattern: str) -> list[str] | None:
    """The exact values of a multi-select's regex (``a|b``, ``^(a|b)$``, ``^a$``, escaped or not); None for any
    other regex."""
    body = pattern
    if body.startswith('^') and body.endswith('$') and not body.endswith('\\$'):
        body = body[1:-1]
        if body.startswith('(') and body.endswith(')'):
            body = body[1:-1]
    values: list[str] = []
    current: list[str] = []
    chars = iter(body)
    for char in chars:
        if char == '\\':
            escaped = next(chars, None)
            if escaped is None:
                return None
            current.append(escaped)
        elif char == '|':
            values.append(''.join(current))
            current = []
        elif char in _REGEX_SPECIAL:
            return None
        else:
            current.append(char)
    values.append(''.join(current))
    if any(value == '' for value in values):
        return None
    return values
