"""A request checked against the declared columns, as typed filters every backend compiles the same way.

What the user can get wrong is found here and while a backend builds its query, before anything runs, as an
``InvalidFilter`` with a reason that never quotes their input. No mistake stops the checking: each is kept in a
``Mistakes`` collector, and the one that decides the answer is raised when the build is complete.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from datetime import timedelta
import logging
import math
import re
from typing import Any
import unicodedata

from .columns import Column
from .columns import ColumnType
from .protocol import NO_USABLE_COLUMN
from .protocol import Condition
from .protocol import DTParams
from .protocol import DTResponse
from .protocol import ErrorCode
from .protocol import InvalidFilter
from .protocol import Search
from .protocol import SearchCriterion
from .protocol import SearchGroup

log = logging.getLogger('datatables_backend')

REGEX_REFUSED = 'Regular-expression search is not supported.'
VALUE_AND_VALUES = 'A column search takes a value or a list of values, not both.'
GLOBAL_VALUES = 'The global search takes a value, not a list of values.'
CONTROL_CHARACTER = 'The filter holds a control character, which cannot be searched for.'
NOT_A_NUMBER = 'The filter value must be a number.'
DATE_FORMAT = 'Dates must be written as YYYY-MM-DD.'
SPAN_FORMAT = 'Dates must be written as YYYY, YYYY-MM or YYYY-MM-DD.'
OUT_OF_RANGE = 'The filter value is outside the range of this column.'

#: Every mistake a user can make, by the fixed name it is logged under: the ``error_code`` it is answered with, and
#: its step in the order that decides which of a request's mistakes is answered (PRD-E3). The names are internal
#: for now. ``parse_request`` logs a malformed request under two more: ``datatables_request_malformed`` and
#: ``datatables_length_refused``.
MISTAKES: Mapping[str, tuple[ErrorCode, int]] = {
    'datatables_control_character': ('invalid_filter', 1),
    'datatables_number_unreadable': ('invalid_number', 2),
    'datatables_date_unreadable': ('invalid_date', 2),
    'datatables_number_out_of_range': ('invalid_number', 3),
    'datatables_date_out_of_range': ('invalid_date', 3),
    'datatables_name_not_declared': ('invalid_filter', 4),
    'datatables_order_refused': ('invalid_filter', 4),
    'datatables_search_refused': ('invalid_filter', 4),  # a column, or a kind of match, the declaration does not allow
    'datatables_no_searchable_column': ('invalid_filter', 4),
    'datatables_regex_refused': ('invalid_filter', 4),
    'datatables_values_refused': ('invalid_filter', 4),  # a list of values where none belongs
    'datatables_condition_refused': ('invalid_filter', 4),
    'datatables_value_missing': ('invalid_filter', 4),
    'datatables_boolean_unreadable': ('invalid_filter', 4),
    'datatables_page_out_of_range': ('page_out_of_range', 5),
}


def mistake(event: str, reason: str, column: Column | None = None) -> InvalidFilter:
    """The user's mistake ``event``, to raise inside ``Mistakes.part``. ``column`` is named in the log, so it must
    be a declared one."""
    code, step = MISTAKES[event]
    return InvalidFilter(reason, code=code, step=step, event=event, column=column.name if column else None)


class Mistakes:
    """The user's mistakes in one request.

    The plan and a backend's build run each part of the request (an order entry, a search, a criterion, a clause)
    inside ``part()``. A mistake raised there is kept and the part is left out of what is built, whole, so nothing
    built later holds half of it; the other parts are still checked. ``raise_first`` is the only way a mistake leaves
    the build: it raises the one of the lowest step, and of those the first found.
    """

    def __init__(self) -> None:
        self._found: list[InvalidFilter] = []

    @contextmanager
    def part(self) -> Iterator[None]:
        try:
            yield
        except InvalidFilter as found:
            self._found.append(found)

    def raise_first(self) -> None:
        if self._found:
            raise min(self._found, key=lambda found: found.step)


def refused(params: DTParams, found: InvalidFilter) -> DTResponse:
    """The answer to the user's mistake, logged once: never their text, and a column only by its declared name."""
    log.warning(found.event, extra={'error_code': found.code, 'column': found.column})
    return DTResponse(draw=params.draw, error=str(found), error_code=found.code)


_STRING_CONDITIONS = frozenset(
    {'=', '!=', 'contains', '!contains', 'starts', '!starts', 'ends', '!ends', 'null', '!null'}
)
_NUMBER_CONDITIONS = frozenset({'=', '!=', '<', '<=', '>', '>=', 'between', '!between', 'null', '!null'})
_DATE_CONDITIONS = _NUMBER_CONDITIONS | {'contains', '!contains'}
_CONDITIONS: Mapping[ColumnType, frozenset[str]] = {
    'string': _STRING_CONDITIONS,
    'number': _NUMBER_CONDITIONS,
    'date': _DATE_CONDITIONS,
    'boolean': frozenset({'=', '!=', 'null', '!null'}),
}
_BOOLEANS: Mapping[str, bool] = {'true': True, '1': True, 'false': False, '0': False}
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
    """A multi-select: the column equals one of ``values``. On a boolean column they are ``bool``, and a plain column
    search is one too, since a boolean has no text to contain anything."""

    column: Column
    type: ColumnType
    values: tuple[Any, ...]


@dataclass(frozen=True)
class Criterion:
    """A SearchBuilder condition. ``values`` are parsed for the column's type: text (after the hook), numbers
    (``int`` or ``float``), dates, one ``DaySpan`` for a date "contains", or a ``bool``."""

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


def plan(
    params: DTParams, columns: Sequence[Column], type_of: Callable[[Column], ColumnType], mistakes: Mistakes
) -> Plan:
    """Check ``params`` against ``columns`` and return what it asks for.

    It raises nothing for the user's mistakes: a name that is not declared, a sort or search the declaration does not
    allow, a regex, a control character, a SearchBuilder condition the column's type does not take or a value it
    cannot read are kept in ``mistakes``, and the plan holds only the parts that had none. The caller builds its
    query from it and then calls ``mistakes.raise_first()``, before anything runs.
    """
    declared = {column.name: column for column in columns}

    def lookup(name: str) -> Column:
        column = declared.get(name)
        if column is None:  # never logged or answered by its name: that is the client's text
            raise mistake('datatables_name_not_declared', NO_USABLE_COLUMN)
        return column

    def hooked(column: Column, text: str) -> Any:
        return column.search_value(text) if column.search_value else text

    order: list[Order] = []
    for item in params.order:
        with mistakes.part():
            requested = params.columns[item.column]  # parse_request refused an index outside the columns
            column = lookup(requested.key)
            if not (requested.orderable and column.orderable):
                raise mistake('datatables_order_refused', NO_USABLE_COLUMN, column)
            order.append(Order(column, type_of(column), item.dir == 'desc'))

    global_search: list[Contains] = []
    with mistakes.part():
        _no_control_character([params.search.value, *(params.search.multi_values or [])])
        if params.search.multi_values:
            raise mistake('datatables_values_refused', GLOBAL_VALUES)
        if params.search.value:
            if params.search.regex:
                raise mistake('datatables_regex_refused', REGEX_REFUSED)
            found: list[Contains] = []
            for requested in params.columns:
                if requested.searchable and requested.key:
                    column = lookup(requested.key)
                    # A boolean column takes no part: "true" as search text is nothing a user reads in the cell
                    if column.searchable and type_of(column) != 'boolean':
                        found.append(Contains(column, type_of(column), hooked(column, params.search.value)))
            if not found:
                raise mistake('datatables_no_searchable_column', NO_USABLE_COLUMN)
            global_search = found  # only once the whole search stands

    column_searches: list[Contains | AnyOf] = []
    for requested in params.columns:
        search = requested.search
        if not (search.value or search.multi_values):
            continue
        with mistakes.part():
            _no_control_character([search.value, *(search.multi_values or [])])
            column = lookup(requested.key)
            if not column.searchable:
                raise mistake('datatables_search_refused', NO_USABLE_COLUMN, column)
            column_searches.append(_column_search(search, column, type_of(column), hooked))

    def node(item: SearchGroup | SearchCriterion) -> Criterion | Group | None:
        if isinstance(item, SearchCriterion):
            with mistakes.part():
                return _criterion(item, lookup, type_of, hooked)
            return None  # a criterion with a mistake is left out, like an empty group
        built = [part for part in (node(child) for child in item.criteria) if part is not None]
        if not built:
            return None  # an empty group adds nothing
        return built[0] if len(built) == 1 else Group(item.logic, tuple(built))

    search_builder = node(params.searchBuilder) if params.searchBuilder else None
    return Plan(tuple(global_search), tuple(column_searches), search_builder, tuple(order))


def _no_control_character(texts: Iterable[str]) -> None:
    """No value the request reads holds a control character (Unicode category Cc) other than LF and CR."""
    for text in texts:
        if any(unicodedata.category(char) == 'Cc' and char not in '\n\r' for char in text):
            raise mistake('datatables_control_character', CONTROL_CHARACTER)


def _criterion(
    item: SearchCriterion,
    lookup: Callable[[str], Column],
    type_of: Callable[[Column], ColumnType],
    hooked: Callable[[Column, str], Any],
) -> Criterion:
    """One criterion, or the one mistake it reports. The checks run in this order: a control character in the values
    the condition reads; a name that is not declared; a condition the column's type does not take, reported without
    the value being read (so ``contains`` with ``abc`` on a number column is no number mistake); a value of the
    wrong format or range, among the values there are; a value that is missing."""
    condition = item.condition
    needed = 0 if condition in ('null', '!null') else 2 if condition in ('between', '!between') else 1
    texts = item.value[:needed]
    _no_control_character(texts)
    column = lookup(item.origData)
    kind = type_of(column)
    if condition not in _CONDITIONS[kind]:
        # The condition is one of the protocol's literals, never free text
        raise mistake(
            'datatables_condition_refused',
            f'Condition "{condition}" is not supported on the column "{column.name}".',
            column,
        )
    values: tuple[Any, ...]
    if kind == 'string':
        values = tuple(hooked(column, text) for text in texts)
    elif kind == 'number':
        values = tuple(parse_number(text) for text in texts)
    elif kind == 'boolean':
        values = tuple(parse_boolean(text) for text in texts)
    elif condition in ('contains', '!contains'):
        values = tuple(day_span(text) for text in texts)
    else:
        values = tuple(parse_day(text) for text in texts)
    if len(texts) < needed:  # after the values there are, so that an unreadable one is the mistake answered
        raise mistake(
            'datatables_value_missing',
            f'The filter on "{column.name}" needs {"two values" if needed == 2 else "a value"}.',
            column,
        )
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
        raise mistake('datatables_number_unreadable', NOT_A_NUMBER)
    return number


def parse_boolean(text: str) -> bool:
    """The boolean ``text`` names: ``true`` or ``false`` in any case, or ``1`` or ``0``."""
    value = _BOOLEANS.get(text.strip().lower())
    if value is None:
        raise mistake('datatables_boolean_unreadable', 'The filter value must be true or false.')
    return value


def parse_day(text: str) -> date:
    """The day ``text`` names (``YYYY-MM-DD``; a time after it is ignored). The last day there is, 9999-12-31, is
    refused in every condition: it has no next day to bound it by, and that is the user's filter, not a failed
    query."""
    try:
        day = date.fromisoformat(text.strip()[:10])
    except ValueError:
        # The parser's message quotes the value, which may be a date of birth
        raise mistake('datatables_date_unreadable', DATE_FORMAT) from None
    if day == date.max:
        raise mistake('datatables_date_out_of_range', OUT_OF_RANGE)
    return day


def next_day(day: date) -> date:
    """The day after ``day``, which ``parse_day`` made sure exists."""
    return day + timedelta(days=1)


def day_span(text: str) -> DaySpan:
    """The days a date "contains" names: ``YYYY`` a year, ``YYYY-MM`` a month, ``YYYY-MM-DD`` a day. A span that
    holds the last day there is, is refused as that day is."""
    value = text.strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        day = parse_day(value)
        return DaySpan(day, next_day(day))
    form = re.fullmatch(r'(\d{4})(?:-(\d{2}))?', value)
    try:
        start = date(int(form[1]), int(form[2] or 1), 1) if form else None
    except ValueError:  # the year 0000, or a month that does not exist
        start = None
    if form is None or start is None:
        raise mistake('datatables_date_unreadable', SPAN_FORMAT)
    last = start.month if form[2] else 12
    if (start.year, last) == (date.max.year, 12):
        raise mistake('datatables_date_out_of_range', OUT_OF_RANGE)
    return DaySpan(start, date(start.year + 1, 1, 1) if last == 12 else date(start.year, last + 1, 1))


def _column_search(
    search: Search, column: Column, kind: ColumnType, hooked: Callable[[Column, str], Any]
) -> Contains | AnyOf:
    """One column's search: a list of exact values (``multi_values`` or a multi-select regex), or text it contains.
    On a boolean column a plain value is an exact value too."""
    if search.multi_values and search.value:
        raise mistake('datatables_values_refused', VALUE_AND_VALUES, column)
    texts: Sequence[str]
    if search.multi_values:
        texts = search.multi_values
    elif search.regex:
        alternatives = _alternatives(search.value)
        if alternatives is None:
            raise mistake('datatables_regex_refused', REGEX_REFUSED, column)
        texts = alternatives
    elif kind == 'boolean':
        texts = [search.value]
    else:
        return Contains(column, kind, hooked(column, search.value))
    if kind == 'boolean':
        return AnyOf(column, kind, tuple(parse_boolean(text) for text in texts))
    return AnyOf(column, kind, tuple(hooked(column, text) for text in texts))


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
