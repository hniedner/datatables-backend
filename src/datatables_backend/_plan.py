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
from datetime import time
from datetime import timedelta
import logging
import math
import re
from typing import Any
import unicodedata

from .columns import Column
from .columns import ColumnType
from .protocol import Condition
from .protocol import DTColumn
from .protocol import DTOrder
from .protocol import DTParams
from .protocol import DTResponse
from .protocol import ErrorCode
from .protocol import InvalidFilter
from .protocol import Search
from .protocol import SearchCriterion
from .protocol import SearchGroup

log = logging.getLogger('datatables_backend')

# The reasons. Each is written for the person at the table, in plain words: what is wrong, and what to do about it
# as far as that is known here. A reason never holds what they typed (PRD-E4): it names a column only by its
# declared name, and a limit only when the server holds it. The reasons with a column's name are built where they
# are raised.
SEARCH_CONTROL_CHARACTER = (
    'The search box holds an invisible control character, such as a tab. Delete its text and type it again.'
)
SEARCH_REGEX = 'The search box does not take regular expressions. Search for plain text.'
# Only a client that builds its own request sends a list here, so this one tells the integrator what to send
SEARCH_VALUES = 'The search box takes text, not a list of values. Send the text as the search value.'
NO_SEARCHABLE_COLUMN = (
    'No column of this table can be searched from the search box. Clear it and use a column filter instead.'
)
SORT_NOT_OFFERED = 'The table was asked to sort by a column the server does not offer. Sort by another column.'
FILTER_NOT_OFFERED = 'A filter is on a column the server does not offer. Remove that filter.'
_HOLDS: Mapping[ColumnType, str] = {
    'string': 'text',
    'number': 'numbers',
    'date': 'dates',
    'boolean': 'true or false',
}

#: Every mistake a user can make, by the fixed name it is logged under: the ``error_code`` it is answered with, and
#: its step in the order that decides which of a request's mistakes is answered (PRD-E3). The names are internal
#: for now. ``parse_request`` logs a malformed request under two more: ``datatables_request_malformed`` and
#: ``datatables_length_refused``.
MISTAKES: Mapping[str, tuple[ErrorCode, int]] = {
    'datatables_control_character': ('invalid_character', 1),
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
# How a number and a day are written (PRD-F25, PRD-F21, PRD-F22). `[0-9]`, not `\d`, which takes the digits of
# every script. Each pattern matches a text in one way only, so one that does not fit is refused in linear time.
_NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')
_DAY = re.compile(
    r'(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})'
    r'(?:[ T](?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})(?::(?P<second>[0-9]{2})(?:\.[0-9]+)?)?)?'
)
_YEAR_OR_MONTH = re.compile(r'(?P<year>[0-9]{4})(?:-(?P<month>[0-9]{2}))?')


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
    search is one too, since a boolean has no text to contain anything. On a date column they are days, each matched
    as ``=`` matches it, and None for an empty entry, which means empty (PRD-F21); ``on_day`` gives each as a
    criterion."""

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
    params: DTParams,
    columns: Sequence[Column],
    type_of: Callable[[Column], ColumnType],
    orderable: Callable[[Column], bool],
    mistakes: Mistakes,
) -> Plan:
    """Check ``params`` against ``columns`` and return what it asks for. ``type_of`` and ``orderable`` give a
    column's type and whether it can be sorted by where its declaration leaves that to the backend.

    It raises nothing for the user's mistakes: a name that is not declared, a sort or search the declaration does not
    allow, a regex, a control character, a SearchBuilder condition the column's type does not take or a value it
    cannot read are kept in ``mistakes``, and the plan holds only the parts that had none. The caller builds its
    query from it and then calls ``mistakes.raise_first()``, before anything runs.
    """
    declared = {column.name: column for column in columns}

    def lookup(name: str, reason: str) -> Column:
        column = declared.get(name)
        if column is None:  # never logged or answered by its name: that is the client's text
            raise mistake('datatables_name_not_declared', reason)
        return column

    def hooked(column: Column, text: str) -> Any:
        return column.search_value(text) if column.search_value else text

    order: list[Order] = []
    for item in params.order:
        with mistakes.part():
            if (entry := _order_entry(item, params.columns[item.column], declared, type_of, orderable)) is None:
                continue  # skipped, without a word: DataTables sends its default order on every request
            if all(entry.column is not earlier.column for earlier in order):  # a column sorts by its first entry
                order.append(entry)

    global_search: list[Contains] = []
    with mistakes.part():
        if _control_character([params.search.value]):
            raise mistake('datatables_control_character', SEARCH_CONTROL_CHARACTER)
        if params.search.multi_values:  # refused as a form, so its entries are never read
            raise mistake('datatables_values_refused', SEARCH_VALUES)
        if params.search.value:
            if params.search.regex:
                raise mistake('datatables_regex_refused', SEARCH_REGEX)
            found: list[Contains] = []
            for requested in params.columns:
                # A column the server did not declare takes no part, like one that only renders: refusing it would
                # fail every search of a table that shows such a column
                column = declared.get(requested.key) if requested.searchable else None
                # Nor does a boolean column: "true" as search text is nothing a user reads in the cell
                if column is not None and column.searchable and type_of(column) != 'boolean':
                    found.append(Contains(column, type_of(column), hooked(column, params.search.value)))
            if not found:
                raise mistake('datatables_no_searchable_column', NO_SEARCHABLE_COLUMN)
            global_search = found  # only once the whole search stands

    column_searches: list[Contains | AnyOf] = []
    for requested in params.columns:
        search = requested.search
        if not (search.value or search.multi_values):
            continue
        with mistakes.part():
            _no_control_character([search.value, *(search.multi_values or [])], declared.get(requested.key))
            column = _filterable(lookup(requested.key, FILTER_NOT_OFFERED))
            column_searches.append(_column_search(search, column, type_of(column), hooked))

    def node(item: SearchGroup | SearchCriterion) -> Criterion | Group | None:
        if isinstance(item, SearchCriterion):
            with mistakes.part():
                return _criterion(item, declared, type_of, hooked)
            return None  # a criterion with a mistake is left out, like an empty group
        built = [part for part in (node(child) for child in item.criteria) if part is not None]
        if not built:
            return None  # an empty group adds nothing
        return built[0] if len(built) == 1 else Group(item.logic, tuple(built))

    search_builder = node(params.searchBuilder) if params.searchBuilder else None
    return Plan(tuple(global_search), tuple(column_searches), search_builder, tuple(order))


def _order_entry(
    item: DTOrder,
    requested: DTColumn,
    declared: Mapping[str, Column],
    type_of: Callable[[Column], ColumnType],
    orderable: Callable[[Column], bool],
) -> Order | None:
    """One order entry, None for one that is skipped, or the mistake it reports. The server decides what can be
    sorted by; the request's ``orderable`` only says whether an entry that cannot apply is a mistake or DataTables'
    default order, which it sends whatever the column's ``orderable`` says.

    Skipped: an entry on a column that only renders (no name, or ``function``, how DataTables sends a function
    ``data``, unless the server declares that name), and one the request marks unorderable on a name the server did
    not declare or declares unorderable."""
    column = declared.get(requested.key)
    if column is None:
        nameless = not requested.name and requested.data in (None, '', 'function')
        if nameless or not requested.orderable:
            return None
        raise mistake('datatables_name_not_declared', SORT_NOT_OFFERED)  # never by its name: the client's text
    if not orderable(column):
        if not requested.orderable:
            return None
        raise mistake(
            'datatables_order_refused',
            f'The table cannot be sorted by "{column.name}". Sort by another column.',
            column,
        )
    return Order(column, type_of(column), item.dir == 'desc')


def _filterable(column: Column) -> Column:
    """``column``, which a column search or a criterion is on, unless the server declares it unsearchable."""
    if not column.searchable:
        raise mistake(
            'datatables_search_refused',
            f'The column "{column.name}" cannot be filtered. Remove the filter on it.',
            column,
        )
    return column


def _control_character(texts: Iterable[str]) -> bool:
    """Whether a value the request reads holds a control character (Unicode category Cc) other than LF and CR."""
    return any(unicodedata.category(char) == 'Cc' and char not in '\n\r' for text in texts for char in text)


def _no_control_character(texts: Iterable[str], column: Column | None) -> None:
    """No value of a column's filter holds a control character. ``column`` is the declared column the filter is on,
    which the reason names; None for a name the server did not declare, which it never does."""
    if _control_character(texts):
        where = f'The filter on "{column.name}"' if column else 'A column filter'
        raise mistake(
            'datatables_control_character',
            f'{where} holds an invisible control character, such as a tab. Delete its value and type it again.',
            column,
        )


def _criterion(
    item: SearchCriterion,
    declared: Mapping[str, Column],
    type_of: Callable[[Column], ColumnType],
    hooked: Callable[[Column, str], Any],
) -> Criterion:
    """One criterion, or the one mistake it reports. The checks run in this order: a control character in the values
    the condition reads; a name that is not declared, or one the server declares unsearchable; a condition the
    column's type does not take, reported without
    the value being read (so ``contains`` with ``abc`` on a number column is no number mistake); a value of the
    wrong format, or a date out of range, among the values there are; a value that is missing. A whole number outside
    the range of its SQL column is found only later, when the query is built, so within one criterion a missing
    value is reported before it."""
    condition = item.condition
    needed = 0 if condition in ('null', '!null') else 2 if condition in ('between', '!between') else 1
    texts = item.value[:needed]
    column = declared.get(item.origData)
    _no_control_character(texts, column)
    if column is None:  # never logged or answered by its name: that is the client's text
        raise mistake('datatables_name_not_declared', FILTER_NOT_OFFERED)
    kind = type_of(_filterable(column))
    if condition not in _CONDITIONS[kind]:
        # The condition is one of the protocol's literals, never free text
        raise mistake(
            'datatables_condition_refused',
            f'The condition "{condition}" cannot be used on "{column.name}", which holds {_HOLDS[kind]}. '
            'Choose another condition.',
            column,
        )
    values: tuple[Any, ...]
    if kind == 'string':
        values = tuple(hooked(column, text) for text in texts)
    elif kind == 'number':
        values = tuple(parse_number(text, column, listed=needed == 2) for text in texts)
    elif kind == 'boolean':
        values = tuple(parse_boolean(text, column) for text in texts)
    elif condition in ('contains', '!contains'):
        values = tuple(day_span(text, column) for text in texts)
    else:
        values = parse_days(texts, column, listed=needed == 2)
    if len(texts) < needed:  # after the values there are, so that an unreadable one is the mistake answered
        raise mistake(
            'datatables_value_missing',
            f'The filter on "{column.name}" needs {"two values" if needed == 2 else "a value"}.',
            column,
        )
    return Criterion(column, kind, condition, values)


def parse_number(text: str, column: Column, *, listed: bool = False) -> int | float:
    """The number ``text`` names in a filter on ``column``: an ``int`` when it is a whole number written without a
    point, else a ``float``. ``listed`` says the text is one of several values (a list, or the two of a
    ``between``), where the reason must not ask for one number.

    A number is written as PRD-F25 says: in ASCII, an optional sign, digits with an optional point, an optional
    exponent, white space around it ignored. Python's own parsers take more (digits of other scripts, ``1_000``,
    ``inf``), so the spelling is checked first."""
    stripped = text.strip()

    def unreadable() -> InvalidFilter:
        needed = (
            f'Each value in the filter on "{column.name}" must be a number'
            if listed
            else f'The filter on "{column.name}" needs one number'
        )
        return mistake('datatables_number_unreadable', f'{needed}, written like 42 or -3.5.', column)

    if not _NUMBER.fullmatch(stripped):
        raise unreadable()
    if stripped.lstrip('+-').isdigit():  # neither a point nor an exponent
        try:
            return int(stripped)
        except ValueError:  # more digits than Python converts to an integer
            raise unreadable() from None
    number = float(stripped)
    if not math.isfinite(number):
        raise unreadable()
    return number


def parse_boolean(text: str, column: Column) -> bool:
    """The boolean ``text`` names in a filter on ``column``: ``true`` or ``false`` in any case, or ``1`` or ``0``."""
    value = _BOOLEANS.get(text.strip().lower())
    if value is None:
        raise mistake(
            'datatables_boolean_unreadable',
            f'The filter on "{column.name}" needs true or false (1 or 0 also work).',
            column,
        )
    return value


def _last_day(column: Column) -> InvalidFilter:
    """The year 9999 in the reason is the calendar's own limit, never read from what the user typed."""
    return mistake(
        'datatables_date_out_of_range',
        f'The filter on "{column.name}" reaches the last day of the year 9999, which cannot be searched. '
        'Use an earlier date.',
        column,
    )


def parse_days(
    texts: Sequence[str], column: Column, *, listed: bool, blank_is_empty: bool = False
) -> tuple[date | None, ...]:
    """The days ``texts`` name in a filter on ``column``, each written as ``_written_day`` reads it, white space
    around it ignored. ``listed`` says they are several values (a list, or the two of a ``between``), where the
    reason must not ask for one date. With ``blank_is_empty`` a text that holds nothing is None, an empty entry of a
    list; otherwise no day is None.

    Every text's format is checked before any day's range, so the format is the mistake answered wherever it
    stands (PRD-E3). The last day there is, 9999-12-31, is refused in every condition: it has no next day to bound
    it by, and that is the user's filter, not a failed query."""
    days: list[date | None] = []
    for text in texts:
        value = text.strip()
        if blank_is_empty and not value:
            days.append(None)
            continue
        day = _written_day(value)
        if day is None:
            needed = (
                f'Each value in the filter on "{column.name}" must be a date'
                if listed
                else f'The filter on "{column.name}" needs a date'
            )
            raise mistake(
                'datatables_date_unreadable', f'{needed} that exists, written as YYYY-MM-DD, like 2024-03-05.', column
            )
        days.append(day)
    if date.max in days:
        raise _last_day(column)
    return tuple(days)


def _written_day(value: str) -> date | None:
    """The day ``value`` writes as PRD-F21 says, or None: ``YYYY-MM-DD`` in ASCII digits, and after it, at most, a
    space or a ``T`` and a time of day that exists (``HH:MM`` or ``HH:MM:SS``, the seconds with an optional
    fraction), which is ignored. SearchBuilder sends such a time; a zone or an offset after it is refused, since the
    instant it names can fall on another UTC day."""
    form = _DAY.fullmatch(value)
    if form is None:
        return None
    try:
        time(int(form['hour'] or 0), int(form['minute'] or 0), int(form['second'] or 0))
        return date(int(form['year']), int(form['month']), int(form['day']))
    except ValueError:  # a day or a time of day that does not exist
        return None


def on_day(column: Column, day: date | None) -> Criterion:
    """One entry of a list on a date column as the criterion that matches it: ``=`` for a day, "empty" for None."""
    if day is None:
        return Criterion(column, 'date', 'null', ())
    return Criterion(column, 'date', '=', (day,))


def next_day(day: date) -> date:
    """The day after ``day``, which ``parse_days`` and ``day_span`` made sure exists."""
    return day + timedelta(days=1)


def day_span(text: str, column: Column) -> DaySpan:
    """The days a date "contains" on ``column`` names: ``YYYY`` a year, ``YYYY-MM`` a month, both in ASCII digits,
    or a day as ``_written_day`` reads it, so `contains` and `=` take the same day. A span that holds the last day
    there is, is refused as that day is."""
    value = text.strip()
    form = _YEAR_OR_MONTH.fullmatch(value)
    try:
        start = date(int(form['year']), int(form['month'] or 1), 1) if form else _written_day(value)
    except ValueError:  # the year 0000, or a month that does not exist
        start = None
    if start is None:
        raise mistake(
            'datatables_date_unreadable',
            f'The filter on "{column.name}" needs a year, a month or a day that exists, written as YYYY, YYYY-MM '
            'or YYYY-MM-DD, like 2024-03.',
            column,
        )
    if form is None:
        if start == date.max:
            raise _last_day(column)
        return DaySpan(start, next_day(start))
    last = start.month if form['month'] else 12
    if (start.year, last) == (date.max.year, 12):
        raise _last_day(column)
    return DaySpan(start, date(start.year + 1, 1, 1) if last == 12 else date(start.year, last + 1, 1))


def _column_search(
    search: Search, column: Column, kind: ColumnType, hooked: Callable[[Column, str], Any]
) -> Contains | AnyOf:
    """One column's search: a list of exact values (``multi_values`` or a multi-select regex), or text it contains.
    On a boolean column a plain value is an exact value too. On a date column each entry of a list is a day, and
    the column's ``search_value`` hook is not applied to it, as it is not applied to a criterion's date."""
    if search.multi_values and search.value:
        # Only a client that builds its own request sends both, so this one tells the integrator what to send
        raise mistake(
            'datatables_values_refused',
            f'The filter on "{column.name}" holds both text and a list of values. Send only one of them.',
            column,
        )
    texts: Sequence[str]
    if search.multi_values:
        texts = search.multi_values
    elif search.regex:
        alternatives = _alternatives(search.value)
        if alternatives is None:
            raise mistake(
                'datatables_regex_refused',
                f'The filter on "{column.name}" does not take regular expressions. Remove these characters and '
                'search for plain text: . * + ? [ ] { } ( ) ^ $ | \\',
                column,
            )
        texts = alternatives
    elif kind == 'boolean':
        texts = [search.value]
    else:
        return Contains(column, kind, hooked(column, search.value))
    if kind == 'boolean':
        return AnyOf(column, kind, tuple(parse_boolean(text, column) for text in texts))
    if kind == 'date':  # read here, so an unreadable entry is found before a backend builds anything
        return AnyOf(column, kind, parse_days(texts, column, listed=True, blank_is_empty=True))
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
