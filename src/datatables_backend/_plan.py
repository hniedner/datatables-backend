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
from decimal import Decimal
from decimal import InvalidOperation
import logging
import math
import re
from typing import Any
from typing import Literal
from typing import get_args
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
from .protocol import SchemaError
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
# The bounds of a request's structure (PRD-F38). A request beyond one is refused as a whole, before it is read further
MAX_DEPTH = 8  # groups of a SearchBuilder tree, the root counted as 1
MAX_CRITERIA = 64  # in the whole tree
MAX_LIST_VALUES = 256  # in one categorical filter or `multi_values` list
MAX_VALUES = 512  # in all the lists of one request
MAX_COLUMN_SEARCHES = 64  # column searches that hold a value
#: The budget of conditions of one request (PRD-C17): one word on one column, one list value and one range each count
#: one. Measured in docs/decisions/005: a request of this many is answered by every supported database.
MAX_CONDITIONS = 960
# The bounds of a value and of search text (PRD-F14)
MAX_VALUE_LENGTH = 255  # code points of any value the client sends, as sent
MAX_SEARCH_WORDS = 16  # counted after the split; a word that reduces to nothing is not counted
SEARCH_DEPTH = f'The search builder nests more than {MAX_DEPTH} groups. Nest at most {MAX_DEPTH} groups.'
SEARCH_CRITERIA = f'The search builder holds more than {MAX_CRITERIA} criteria. Use at most {MAX_CRITERIA} criteria.'
TOO_MANY_VALUES = f'The filters hold more than {MAX_VALUES} values in all. Remove some values.'
TOO_MANY_COLUMN_SEARCHES = f'The request searches more than {MAX_COLUMN_SEARCHES} columns. Clear some column searches.'
SEARCH_TOO_LONG = (
    f'The search text holds more than {MAX_VALUE_LENGTH} characters. Shorten it to at most {MAX_VALUE_LENGTH}.'
)
SEARCH_TOO_MANY_WORDS = (
    f'The search text holds more than {MAX_SEARCH_WORDS} words. Search for at most {MAX_SEARCH_WORDS} words.'
)
REQUEST_TOO_LARGE = (
    f'The request is too large: it holds more than {MAX_CONDITIONS} conditions (each search word counts once per '
    'column searched, each selected value and each range once). Search for fewer words, search fewer columns, or '
    'select fewer values.'
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
    'datatables_tree_too_deep': ('invalid_filter', 1),
    'datatables_too_many_criteria': ('invalid_filter', 1),
    'datatables_list_too_long': ('invalid_filter', 1),
    'datatables_too_many_values': ('invalid_filter', 1),
    'datatables_too_many_column_searches': ('invalid_filter', 1),
    'datatables_value_too_long': ('value_too_long', 1),
    'datatables_too_many_words': ('too_many_words', 1),
    'datatables_request_too_large': ('request_too_large', 1),  # last of step 1: it is checked after all the others
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
    'datatables_value_surplus': ('invalid_filter', 4),  # more values than the condition takes
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
#: What a boolean takes (it holds no part to match) and what a hooked column takes (PRD-D18: a part of a transformed
#: value means nothing)
_EQUALITY_AND_EMPTY = frozenset({'=', '!=', 'null', '!null'})
#: What a string column that is analysed text only takes (PRD-D19): no whole string to equal or to anchor on
_CONTAINS_AND_EMPTY = frozenset({'contains', '!contains', 'null', '!null'})
_CONDITIONS: Mapping[ColumnType, frozenset[str]] = {
    'string': _STRING_CONDITIONS,
    'number': _NUMBER_CONDITIONS,
    'date': _DATE_CONDITIONS,
    'boolean': _EQUALITY_AND_EMPTY,
}
_BOOLEANS: Mapping[str, bool] = {'true': True, '1': True, 'false': False, '0': False}
_REGEX_SPECIAL = frozenset('.*+?[]{}()^$|')
#: The characters a categorical filter's regex may escape: the set of DataTables' ``util.escapeRegex`` (PRD-F19)
_REGEX_ESCAPED = frozenset('/.*+?|()[]{}\\$^-')
#: What counts as a space in search text (PRD-F6, PRD-F7): U+0020, LF, CR, U+2028 and U+2029
_SPACES = ' \n\r' + chr(0x2028) + chr(0x2029)
_AS_SPACE = str.maketrans(dict.fromkeys(_SPACES, ' '))
#: DataTables' smart-search split (3.1.5 ``dataTables.mjs``, :5924): a phrase is read where a word begins
_SPLIT = re.compile(r'!?["\u201C][^"\u201D]+["\u201D]|[^ ]+')
# How a number and a day are written (PRD-F25, PRD-F21, PRD-F22). `[0-9]`, not `\d`, which takes the digits of
# every script. Each pattern matches a text in one way only, so one that does not fit is refused in linear time.
#: The most digits after the decimal point a database takes in a number: PostgreSQL's `numeric` (its manual, Table 8.2,
#: https://www.postgresql.org/docs/current/datatype-numeric.html, fetched 2026-11-24); a longer exponent is the user's
#: mistake, never the driver's error
_MAX_FRACTION_DIGITS = 16383
_NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')
_DAY = re.compile(
    r'(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})'
    r'(?:[ T](?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})(?::(?P<second>[0-9]{2})(?:\.[0-9]+)?)?)?'
)
_YEAR_OR_MONTH = re.compile(r'(?P<year>[0-9]{4})(?:-(?P<month>[0-9]{2}))?')


@dataclass(frozen=True)
class Capabilities:
    """What a backend can do with a column, which the plan needs to know to decide for every backend alike.

    ``type`` is how its filters are built. ``orderable`` says whether it can be sorted by, ``searched`` whether it
    takes part in the global search, and ``value_search`` whether a column search on a number or date column is one
    value to match (OpenSearch, which has no text of a number to search in) rather than text (SQL). ``whole_string``
    says whether the column holds a whole string to match exactly, to anchor on and to compare a hook's value with: not
    a string that is analysed text only (PRD-D19, PRD-D20)."""

    type: ColumnType
    orderable: bool
    searched: bool
    value_search: bool
    whole_string: bool


@dataclass(frozen=True)
class Contains:
    """A search for text in one column. ``value`` is the user's text after the column's ``search_value`` hook."""

    column: Column
    type: ColumnType
    value: Any


@dataclass(frozen=True)
class Equals:
    """The column equals ``value``: text after the hook, a number (``int`` or ``float``) or a ``bool``."""

    column: Column
    type: ColumnType
    value: Any


@dataclass(frozen=True)
class OneOf:
    """A multi-select: the column equals one of ``values``, parsed as for ``Equals``; on a date column they are
    days, each matched as ``=`` matches a day. ``empty`` says the list also holds an empty entry, which matches a
    row without a value (PRD-F17, PRD-F20) and is never among ``values``, which may then be none at all."""

    column: Column
    type: ColumnType
    values: tuple[Any, ...]
    empty: bool = False


@dataclass(frozen=True)
class Part:
    """A string column holds ``value``: anywhere, or at its ``start`` or its ``end``."""

    column: Column
    type: ColumnType
    value: Any
    where: Literal['any', 'start', 'end']


@dataclass(frozen=True)
class Bound:
    """One end of a ``Range``: a number, or a day."""

    value: int | Decimal | date
    included: bool


@dataclass(frozen=True)
class Range:
    """The column lies between ``lower`` and ``upper``; either may be open. The bounds of a number are included or
    not as the condition says. A condition on a date is always whole days: from a day, included, up to a day, not
    included, so ``=`` is the day and the one after it."""

    column: Column
    type: ColumnType
    lower: Bound | None
    upper: Bound | None


@dataclass(frozen=True)
class Empty:
    """The column has no value (PRD-F26)."""

    column: Column
    type: ColumnType


@dataclass(frozen=True)
class Not:
    """A negative condition: every row ``node`` does not match. Each backend compiles it in one place."""

    node: Contains | Equals | Part | Range | Empty

    @property
    def column(self) -> Column:
        return self.node.column

    @property
    def type(self) -> ColumnType:
        return self.node.type


#: One condition on one column
type Filter = Contains | Equals | OneOf | Part | Range | Empty | Not


@dataclass(frozen=True)
class Group:
    """A SearchBuilder group that holds at least one criterion."""

    logic: str
    items: tuple[Filter | Group, ...]


@dataclass(frozen=True)
class Order:
    column: Column
    type: ColumnType
    descending: bool


@dataclass(frozen=True)
class Plan:
    """What one request asks for: the global search and the column searches (PRD-F1, PRD-F2), whose words are nodes of
    their own, and the SearchBuilder tree, all joined by AND."""

    global_search: Filter | Group | None
    column_searches: tuple[Filter | Group, ...]
    search_builder: Filter | Group | None
    order: tuple[Order, ...]

    @property
    def filtered(self) -> bool:
        """Whether the request narrows the rows beyond the table's base filter."""
        return bool(self.global_search or self.column_searches or self.search_builder)


def check_type(column: Column) -> None:
    """Raise ``SchemaError`` for a ``type`` that is none of the four: a ``Literal`` checks nothing at run time, and the
    table would build and then fail on a filter. The message names the column, the type written and the accepted ones;
    the name and the value are the server's, never the client's."""
    accepted = get_args(ColumnType.__value__)
    if column.type is not None and column.type not in accepted:
        raise SchemaError(
            f'The column "{column.name}" declares type={column.type!r}, which is not a type. Declare one of '
            f'{", ".join(accepted)}.'
        )


def check_name(column: Column) -> None:
    """Raise ``SchemaError`` for an empty ``name`` or ``field``: the table would build and send an empty field name to
    the server. The message names the column when it has a name."""
    if not column.name or column.field == '':
        which = f'The column "{column.name}" declares field=""' if column.name else 'A column is declared with name=""'
        raise SchemaError(f'{which}, an empty name. Give the column a non-empty name, or a field naming the source.')


def check_hook(column: Column) -> None:
    """Raise ``SchemaError`` for a ``search_value`` that cannot be called: the table would build and raise
    ``TypeError`` on the first request that searches the column."""
    if column.search_value is not None and not callable(column.search_value):
        raise SchemaError(
            f'The column "{column.name}" declares a search_value that is not callable. Pass a function that takes the '
            'search text and returns the value to compare.'
        )


def check_declaration(columns: Sequence[Column], describe: Callable[[Column], Capabilities]) -> None:
    """Raise ``SchemaError`` for a declaration the table cannot answer from: no column (PRD-D1), two of one name, whose
    second would silently replace the first (PRD-D1), a ``type`` that is none of the four, or a search-value hook on a
    column that is no string (PRD-D14) or holds no whole string to compare with (PRD-D20). The messages name only
    declared columns. ``describe`` is the backend's: SQL reads the type of a column it did not declare one for from the
    source."""
    if not columns:
        raise SchemaError('No column is declared. Declare at least one Column.')
    names: set[str] = set()
    for column in columns:
        check_type(column)
        check_name(column)
        check_hook(column)
        if column.name in names:
            raise SchemaError(
                f'Two columns are declared with the name "{column.name}". Give each its own name: '
                'Column(name, field=...) reads one field under another name.'
            )
        names.add(column.name)
    for column in columns:
        if column.search_value is None:
            continue
        able = describe(column)
        if able.type != 'string':
            raise SchemaError(
                f'The column "{column.name}" has a search_value hook but is a {able.type} column. Only a string column '
                'takes one: remove the hook.'
            )
        if not able.whole_string:
            raise SchemaError(
                f'The column "{column.name}" has a search_value hook but holds analysed text only, which a hook '
                'compares by equality. Declare it text="keyword" or "text+keyword", or remove the hook.'
            )


def declared_key(columns: Sequence[Column], key: str | Sequence[str]) -> tuple[Column, ...]:
    """The declared columns a table's ``unique_key`` names (PRD-D11), one name or several. Raises ``SchemaError`` for an
    empty key or a name that is no declared column. The messages name only what the server declared."""
    names = (key,) if isinstance(key, str) else tuple(key)
    if not names:
        raise SchemaError('unique_key is empty. Name at least one declared column whose values are unique.')
    by_name = {column.name: column for column in columns}
    if unknown := [name for name in names if name not in by_name]:
        raise SchemaError(
            f'unique_key names {", ".join(unknown)}, which no declared column has as its name. Name a declared '
            'column: unique_key reads the name the column is declared with, not its field.'
        )
    return tuple(by_name[name] for name in names)


def plan(
    params: DTParams,
    columns: Sequence[Column],
    describe: Callable[[Column], Capabilities],
    mistakes: Mistakes,
) -> Plan:
    """Check ``params`` against ``columns`` and return what it asks for. ``describe`` says what the backend can do
    with a column, so that a skip or a refusal is decided here, for every backend alike. One refusal is left to a
    backend, which alone knows what it needs: SQL's of a whole number its column cannot hold or be compared with.

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
            if (entry := _order_entry(item, params.columns[item.column], declared, describe)) is None:
                continue  # skipped, without a word: DataTables sends its default order on every request
            # A field sorts by its first entry, also when two declared columns read it
            if all(entry.column.source != earlier.column.source for earlier in order):
                order.append(entry)

    global_search: Filter | Group | None = None
    with mistakes.part():
        global_search = _global_search(params, declared, describe, hooked)

    column_searches: list[Filter | Group] = []
    listed = 0
    for key, part in _column_parts(params, declared, mistakes):
        with mistakes.part():
            if isinstance(part, SearchCriterion):
                built = _criterion(part, declared, describe, hooked)
            else:
                listed = _within_the_lists(part, declared.get(key), listed)
                _no_control_character([part.value, *(part.multi_values or [])], declared.get(key))
                _within_the_bounds_of(part, declared.get(key), describe)  # before step 4 can hide them
                column = _filterable(lookup(key, FILTER_NOT_OFFERED))
                built = _column_search(part, column, describe(column), hooked)
            if built is not None:
                column_searches.append(built)

    def node(item: SearchGroup | SearchCriterion) -> Filter | Group | None:
        if isinstance(item, SearchCriterion):
            with mistakes.part():
                return _criterion(item, declared, describe, hooked)
            return None  # a criterion with a mistake is left out, like an empty group
        built = [part for part in (node(child) for child in item.criteria) if part is not None]
        if not built:
            return None  # an empty group adds nothing
        return built[0] if len(built) == 1 else Group(item.logic, tuple(built))

    search_builder = None
    if params.searchBuilder:
        with mistakes.part():
            _within_the_bounds(params.searchBuilder)  # before `node` recurses into it
            search_builder = node(params.searchBuilder)
    # Last, so that within step 1 every other mistake is found first (PRD-E3). A part left out for a mistake is not
    # counted: such a request is answered with that mistake, and with this one once it is fixed
    built = [global_search, *column_searches, search_builder]
    if sum(_conditions(part) for part in built if part is not None) > MAX_CONDITIONS:
        with mistakes.part():
            raise mistake('datatables_request_too_large', REQUEST_TOO_LARGE)
    return Plan(global_search, tuple(column_searches), search_builder, tuple(order))


def _conditions(node: Filter | Group) -> int:
    """The conditions ``node`` holds, as the budget counts them (PRD-C17): a word on a column, a value of a list (the
    empty entry too) and a range each count one, whatever the database makes of them. The tree is at most
    ``MAX_DEPTH`` groups deep, so the recursion is short."""
    match node:
        case Group():
            return sum(_conditions(item) for item in node.items)
        case Not():
            return _conditions(node.node)
        case OneOf():
            return max(1, len(node.values) + node.empty)
        case _:
            return 1


def _global_search(
    params: DTParams,
    declared: Mapping[str, Column],
    describe: Callable[[Column], Capabilities],
    hooked: Callable[[Column, str], Any],
) -> Filter | Group | None:
    """The global search: every word found in some searchable column, or the whole text equal to the value of a hooked
    column; none for text with no word. Raises the user's mistakes."""
    text = params.search.value
    if _control_character([text]):
        raise mistake('datatables_control_character', SEARCH_CONTROL_CHARACTER)
    if len(text) > MAX_VALUE_LENGTH:  # before every refusal of step 4 that would hide it (PRD-E3)
        raise mistake('datatables_value_too_long', SEARCH_TOO_LONG)
    searched_words = _words(text)
    found: list[tuple[Column, ColumnType]] = []
    covered: set[str] = set()  # a column the request names again is searched once (PRD-F38)
    for requested in params.columns:
        # A column the server did not declare takes no part, like one that only renders: refusing it would
        # fail every search of a table that shows such a column
        column = declared.get(requested.key) if requested.searchable else None
        # Nor does a column the backend cannot search for text: a boolean on every backend ("true" as search
        # text is nothing a user reads in the cell), a number or a date on OpenSearch
        if (
            column is not None
            and column.searchable
            and (able := describe(column)).searched
            and column.name not in covered
        ):
            covered.add(column.name)
            found.append((column, able.type))
    plain: list[tuple[Column, ColumnType]] = [(column, kind) for column, kind in found if column.search_value is None]
    if plain or not found:  # the cap is lifted where every searchable column is hooked (PRD-D12)
        _within_the_words(searched_words, None, in_column=False)
    if params.search.multi_values:  # refused as a form, so its entries are never read
        raise mistake('datatables_values_refused', SEARCH_VALUES)
    if not searched_words:  # text with no word adds nothing (PRD-F6)
        return None
    if params.search.regex:
        raise mistake('datatables_regex_refused', SEARCH_REGEX)
    if not found:
        raise mistake('datatables_no_searchable_column', NO_SEARCHABLE_COLUMN)
    alternatives: list[Filter | Group] = []
    if plain:
        alternatives.append(_all('AND', [_word(plain, word, negated) for word, negated in searched_words]))
    # A hooked column equals the whole text, trimmed and transformed once (PRD-D12): no words, and no PRD-F7 rewrite
    # of a line break. Python's strip is Unicode White_Space for admitted text (as for a column search, below)
    if whole := text.strip():
        alternatives.extend(
            Equals(column, kind, hooked(column, whole)) for column, kind in found if column.search_value is not None
        )
    # Text of White_Space beyond PRD-F7's five has a word for the other columns and none for the hooked ones
    return _all('OR', alternatives) if alternatives else None


def _words(text: str) -> list[tuple[str, bool]]:
    """The words of search text and whether each is negated, as DataTables' smart search reads them (PRD-F3 to
    PRD-F7, PRD-F35): a phrase is read only where a word begins, a negated word keeps its quotes unless a matching
    pair encloses it, and a word that reduces to nothing is dropped."""
    found: list[tuple[str, bool]] = []
    for token in _SPLIT.findall(text.translate(_AS_SPACE)):
        negated = token.startswith('!')
        word = token[1:] if negated else token
        for opening, closing in (('"', '"'), ('“', '”')):
            if len(word) >= 2 and word.startswith(opening) and word.endswith(closing):
                word = word[1:-1]
                break
        if negated:
            # DataTables measures the word escaped for a regular expression, so `!-` excludes and `!x` does not
            if len(word) + sum(char in _REGEX_ESCAPED for char in word) > 1:
                found.append((word, True))
        elif word := word.replace('"', ''):
            found.append((word, False))
    return found


def _word(columns: Sequence[tuple[Column, ColumnType]], word: str, negated: bool) -> Filter | Group:
    """One word of the global search: found in some column, or, negated, in none of them."""
    if negated:
        return _all('AND', [Not(Contains(column, kind, word)) for column, kind in columns])
    return _all('OR', [Contains(column, kind, word) for column, kind in columns])


def _all(logic: Literal['AND', 'OR'], items: Sequence[Filter | Group]) -> Filter | Group:
    """``items`` joined by ``logic``; one item stands for itself."""
    return items[0] if len(items) == 1 else Group(logic, tuple(items))


def _within_the_length(texts: Iterable[str], column: Column | None, *, search: bool) -> None:
    """No value of a column's filter holds more than ``MAX_VALUE_LENGTH`` code points (PRD-F14). ``search`` says the
    text is the column's search, not a value of a list or a criterion. ``column`` is the declared column the filter
    is on, which the reason names; None for a name the server did not declare, which it never does."""
    if any(len(text) > MAX_VALUE_LENGTH for text in texts):
        if search:
            where = f'The search on "{column.name}"' if column else 'A column search'
            reason = f'{where} holds more than {MAX_VALUE_LENGTH} characters. Shorten it to at most {MAX_VALUE_LENGTH}.'
        else:
            where = f'A value in the filter on "{column.name}"' if column else 'A value in a column filter'
            reason = (
                f'{where} holds more than {MAX_VALUE_LENGTH} characters. '
                f'Shorten each value to at most {MAX_VALUE_LENGTH}.'
            )
        raise mistake('datatables_value_too_long', reason, column)


def _within_the_words(words: Sequence[tuple[str, bool]], column: Column | None, *, in_column: bool) -> None:
    """No search text holds more than ``MAX_SEARCH_WORDS`` words (PRD-F14). ``in_column`` says it is a column's
    search, whose reason names ``column``, the declared column it is on (None for a name the server did not
    declare, which it never does); else it is the global search."""
    if len(words) > MAX_SEARCH_WORDS:
        if not in_column:
            raise mistake('datatables_too_many_words', SEARCH_TOO_MANY_WORDS)
        where = f'The search on "{column.name}"' if column else 'A column search'
        reason = f'{where} holds more than {MAX_SEARCH_WORDS} words. Search for at most {MAX_SEARCH_WORDS} words.'
        raise mistake('datatables_too_many_words', reason, column)


def _is_search(search: Search, column: Column | None) -> bool:
    """Whether a column search holds anything: a list, or text that adds something (``_idle_text``). ``column`` is
    the declared column it is on; None for a name the server did not declare."""
    if not (search.value or search.multi_values):
        return False
    if not (search.regex or search.multi_values) and len(search.value) > MAX_VALUE_LENGTH:
        return True  # blank or not, it is refused as too long (PRD-F14)
    return bool(search.multi_values) or not _idle_text(search, column)


def _idle_text(search: Search, column: Column | None) -> bool:
    """Whether a column search's text adds nothing (PRD-F6): no text; with ``regex`` false, spaces only, or text that
    reduces to no word (a lone `"`, a bare `!`, `!x`). As a regex any other text is a categorical value (PRD-F16), and
    a hooked column's text has no grammar, only a whole value (PRD-D17). Text with a control character is never idle:
    it is refused (PRD-F15). ``column`` as for ``_is_search``."""
    if search.regex:
        return not search.value
    if not search.value.strip(_SPACES):
        return True
    if _control_character([search.value]):  # `!` and a tab reduce to no word, yet must be refused (PRD-F15)
        return False
    return (column is None or column.search_value is None) and not _words(search.value)


#: The name a ColumnControl criterion carries for a column that has none. A criterion with no column adds nothing
#: (PRD-F30), which an active filter must not; no declared column is called this, so ``_criterion`` refuses it as an
#: undeclared name, after the checks of step 1 (PRD-E3).
_NO_COLUMN = '\x00'

#: What each of ColumnControl's logics asks, as the SearchBuilder condition that answers it (PRD-F45): the rules of a
#: type are stated once, there. ``contains`` is the column search and is not here.
_COLUMNCONTROL_CONDITIONS: Mapping[str, Condition] = {
    'notContains': '!contains',
    'equal': '=',
    'notEqual': '!=',
    'starts': 'starts',
    'ends': 'ends',
    'empty': 'null',
    'notEmpty': '!null',
    'greater': '>',
    'greaterOrEqual': '>=',
    'less': '<',
    'lessOrEqual': '<=',
}


def _column_parts(
    params: DTParams, declared: Mapping[str, Column], mistakes: Mistakes
) -> list[tuple[str, Search | SearchCriterion]]:
    """The filters the request's columns hold, each with the request's name for its column, in the order PRD-E3 reads
    them: for each column its search, then its ColumnControl search, then its ColumnControl list. A ColumnControl
    ``contains`` is a column search and its list the ``multi_values`` of one; every other logic is the SearchBuilder
    criterion it names, with the control's text as its value. ``null`` and ``!null`` ignore what they carry (PRD-F36),
    so the text left in the input of ``empty`` and ``notEmpty`` is neither read nor measured. An idle one holds
    nothing. None if there are more than PRD-F38 allows: the request is refused as a whole, and nothing of it is
    read."""
    held: list[tuple[str, Search | SearchCriterion]] = []
    for requested in params.columns:
        if _is_search(requested.search, declared.get(requested.key)):
            held.append((requested.key, requested.search))
        control = requested.columnControl
        if control.search.logic in ('', 'contains'):
            contains = Search(value=control.search.value)
            if _is_search(contains, declared.get(requested.key)):
                held.append((requested.key, contains))
        elif control.search.value or control.search.logic in ('empty', 'notEmpty'):
            condition = _COLUMNCONTROL_CONDITIONS[control.search.logic]
            named = requested.key or _NO_COLUMN
            held.append(
                (requested.key, SearchCriterion(condition=condition, origData=named, value=[control.search.value]))
            )
        if control.list:
            held.append((requested.key, Search(multi_values=control.list)))
    if len(held) <= MAX_COLUMN_SEARCHES:
        return held
    with mistakes.part():
        raise mistake('datatables_too_many_column_searches', TOO_MANY_COLUMN_SEARCHES)
    return []


def _list_size(search: Search) -> int:
    """The exact values a column search lists: its ``multi_values``, or the alternatives of a categorical regex; none
    for any other search. A regex that is no categorical form is refused where it is read."""
    if search.multi_values:
        return len(search.multi_values)
    alternatives = _alternatives(search.value) if search.regex else None
    return len(alternatives) if alternatives is not None else 0


def _within_the_bounds_of(search: Search, column: Column | None, describe: Callable[[Column], Capabilities]) -> None:
    """Raise the mistake of a column search beyond PRD-F14's bounds: a value over ``MAX_VALUE_LENGTH`` code points, as
    sent (the text, each entry of a list, each alternative of a categorical regex), or text over
    ``MAX_SEARCH_WORDS`` words where it is split into them. It runs before any refusal of step 4 (PRD-E3). A
    boolean, and a number or date that is one value to match, are no words; nor is a hooked column's text (PRD-D17)."""
    texts = list(search.multi_values or [])
    if search.regex:
        texts += _alternatives(search.value) or []  # a regex that is no categorical form is refused where it is read
    else:
        texts.append(search.value)
    listed = bool(search.multi_values or search.regex)
    _within_the_length(texts, column, search=not listed)
    if listed:
        return
    able = describe(column) if column else None
    if column is None or (
        column.search_value is None
        and able is not None
        and able.type != 'boolean'
        and not (able.value_search and able.type in ('number', 'date'))
    ):
        _within_the_words(_words(search.value), column, in_column=True)


def _within_the_lists(search: Search, column: Column | None, listed: int) -> int:
    """The values listed so far, ``listed``, and those of ``search``, if they are within PRD-F38's bounds. ``column``
    is the declared column the search is on, which the reason names; None for a name the server did not declare,
    which it never does."""
    size = _list_size(search)
    if size > MAX_LIST_VALUES:
        where = f'The filter on "{column.name}"' if column else 'A column filter'
        raise mistake(
            'datatables_list_too_long',
            f'{where} holds more than {MAX_LIST_VALUES} values. Select at most {MAX_LIST_VALUES} values.',
            column,
        )
    if listed + size > MAX_VALUES:
        raise mistake('datatables_too_many_values', TOO_MANY_VALUES)
    return listed + size


def _within_the_bounds(root: SearchGroup) -> None:
    """Raise the mistake of a SearchBuilder tree beyond PRD-F38's bounds. No call recurses, so no depth can exhaust
    the stack: the depth is checked first and stops at the first group below level ``MAX_DEPTH``, and the criteria
    are counted only in a tree that passed, stopping at one too many."""
    groups = [(root, 1)]
    while groups:
        group, depth = groups.pop()
        if depth > MAX_DEPTH:
            raise mistake('datatables_tree_too_deep', SEARCH_DEPTH)
        groups.extend((child, depth + 1) for child in group.criteria if isinstance(child, SearchGroup))
    criteria = 0
    pending = [root]
    while pending:
        for child in pending.pop().criteria:
            if isinstance(child, SearchGroup):
                pending.append(child)
                continue
            criteria += 1
            if criteria > MAX_CRITERIA:
                raise mistake('datatables_too_many_criteria', SEARCH_CRITERIA)


def _order_entry(
    item: DTOrder,
    requested: DTColumn,
    declared: Mapping[str, Column],
    describe: Callable[[Column], Capabilities],
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
    able = describe(column)
    if not able.orderable:
        if not requested.orderable:
            return None
        raise mistake(
            'datatables_order_refused',
            f'The table cannot be sorted by "{column.name}". Sort by another column.',
            column,
        )
    return Order(column, able.type, item.dir == 'desc')


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
    describe: Callable[[Column], Capabilities],
    hooked: Callable[[Column, str], Any],
) -> Filter | None:
    """One criterion, the one mistake it reports, or None when it is unfinished and adds nothing (PRD-F30): it has no
    condition, no column, or fewer values than its condition takes, a blank one counting as missing.
    The checks run in this order: no condition or no column, nothing else is looked at; a control character in the
    values the condition reads; a name that is not declared, or one the server declares unsearchable; a condition the
    column's type does not take, reported without the value being read (so ``contains`` with ``abc`` on a number
    column is no number mistake); a value of the wrong format, or a date out of range, among the values there are;
    more values than the condition takes (PRD-F36), which are never read, also when some are blank; then a value that
    is missing, which leaves the criterion out. ``null`` and ``!null`` take none and ignore whatever they carry. A
    whole number outside the range of its SQL column is found only later, when the query is built."""
    condition = item.condition
    if condition is None or not item.origData:
        return None
    needed = 0 if condition in ('null', '!null') else 2 if condition in ('between', '!between') else 1
    # A blank value is the empty text, which SearchBuilder's own client does not apply either (PRD-F30); one space is
    # a value. Kept out here, it counts as missing below
    texts = [text for text in item.value[:needed] if text]
    column = declared.get(item.origData)
    _no_control_character(texts, column)
    _within_the_length(texts, column, search=False)
    if column is None:  # never logged or answered by its name: that is the client's text
        raise mistake('datatables_name_not_declared', FILTER_NOT_OFFERED)
    able = describe(_filterable(column))
    kind = able.type
    if condition not in _CONDITIONS[kind]:
        # The condition is one of the protocol's literals, never free text
        raise mistake(
            'datatables_condition_refused',
            f'The condition "{condition}" cannot be used on "{column.name}", which holds {_HOLDS[kind]}. '
            'Choose another condition.',
            column,
        )
    if column.search_value is not None and condition not in _EQUALITY_AND_EMPTY:  # PRD-D18, a protocol literal again
        raise mistake(
            'datatables_condition_refused',
            f'The condition "{condition}" cannot be used on "{column.name}", whose search value is transformed '
            'before it is compared. Choose =, !=, null or !null.',
            column,
        )
    if not able.whole_string and condition not in _CONTAINS_AND_EMPTY:  # PRD-D19
        raise mistake(
            'datatables_condition_refused',
            f'The condition "{condition}" cannot be used on "{column.name}", which holds analysed text only. Search '
            'the column for words, or choose contains, !contains, null or !null.',
            column,
        )
    values: Sequence[Any]
    if kind == 'string':
        values = [hooked(column, text) for text in texts]
    elif kind == 'number':
        values = [parse_number(text, column, listed=needed == 2) for text in texts]
    elif kind == 'boolean':
        values = [parse_boolean(text, column) for text in texts]
    elif condition in ('contains', '!contains'):
        values = [_day_span(text, column) for text in texts]
    else:
        values = parse_days(texts, column, listed=needed == 2)
    if needed and len(item.value) > needed:  # the values beyond those the condition takes are never read
        raise mistake(
            'datatables_value_surplus',
            f'The filter on "{column.name}" holds more values than its condition takes. Remove the filter and set '
            'it again.',
            column,
        )
    if len(texts) < needed:  # after the values there are, so that an unreadable one is the mistake answered
        return None
    if needed == 2 and values[1] < values[0]:  # a range in either order, as SearchBuilder's own filter takes it
        values = [values[1], values[0]]
    positive = _positive(column, kind, condition, values)
    return Not(positive) if condition.startswith('!') else positive


def _positive(
    column: Column, kind: ColumnType, condition: Condition, values: Sequence[Any]
) -> Equals | Part | Range | Empty:
    """What a condition matches, or for a negative one what it does not. A date is matched by whole days (PRD-F21):
    each condition is the days from one, included, up to another, not included."""
    if kind == 'date' and condition not in ('null', '!null'):
        start: date | None
        end: date | None
        if condition in ('contains', '!contains'):
            start, end = values[0]
        else:
            first, last = values[0], values[-1]
            start = None if condition in ('<', '<=') else next_day(first) if condition == '>' else first
            end = None if condition in ('>', '>=') else first if condition == '<' else next_day(last)
        return Range(
            column, kind, None if start is None else Bound(start, True), None if end is None else Bound(end, False)
        )
    match condition:
        case 'null' | '!null':
            return Empty(column, kind)
        case '=' | '!=':
            return Equals(column, kind, values[0])
        case 'contains' | '!contains':
            return Part(column, kind, values[0], 'any')
        case 'starts' | '!starts':
            return Part(column, kind, values[0], 'start')
        case 'ends' | '!ends':
            return Part(column, kind, values[0], 'end')
        case 'between' | '!between':
            return Range(column, kind, Bound(values[0], True), Bound(values[1], True))
        case '<' | '<=':
            return Range(column, kind, None, Bound(values[0], condition == '<='))
        case '>' | '>=':
            return Range(column, kind, Bound(values[0], condition == '>='), None)


def parse_number(text: str, column: Column, *, listed: bool = False) -> int | Decimal:
    """The number ``text`` names in a filter on ``column``: an ``int`` when it is a whole number written without a
    point, else a ``Decimal``, which keeps every digit written. ``listed`` says the text is one of several values (a
    list, or the two of a ``between``), where the reason must not ask for one number.

    A number is written as PRD-F25 says: in ASCII, an optional sign, digits with an optional point, an optional
    exponent, white space around it ignored. Python's own parsers take more (digits of other scripts, ``1_000``,
    ``inf``), so the spelling is checked first. A number is finite when a float holds it (``1e400`` is not), which
    is also what decides whether a backend that compares as a float can take it, and has at most 16,383 digits after
    the decimal point (``1e-20000`` is refused, the most PostgreSQL's ``numeric`` takes)."""
    stripped = text.strip()
    if not _NUMBER.fullmatch(stripped):
        raise number_unreadable(column, listed=listed)
    if stripped.lstrip('+-').isdigit():  # neither a point nor an exponent; at most 255 characters, so `int` converts it
        return int(stripped)
    try:
        number = Decimal(stripped)
    except InvalidOperation:  # an exponent beyond what a Decimal holds
        raise number_unreadable(column, listed=listed) from None
    if not math.isfinite(float(number)):
        raise number_unreadable(column, listed=listed)
    if int(number.as_tuple().exponent) < -_MAX_FRACTION_DIGITS:
        needed = (
            f'Each value in the filter on "{column.name}" must be a number'
            if listed
            else f'The filter on "{column.name}" needs a number'
        )
        raise mistake(
            'datatables_number_out_of_range',
            f'{needed} with at most {_MAX_FRACTION_DIGITS} digits after the decimal point.',
            column,
        )
    return number


def number_unreadable(column: Column, *, listed: bool) -> InvalidFilter:
    """The mistake of a value in a filter on ``column`` that is no number, or no finite one."""
    needed = (
        f'Each value in the filter on "{column.name}" must be a number'
        if listed
        else f'The filter on "{column.name}" needs one number'
    )
    return mistake('datatables_number_unreadable', f'{needed}, written like 42 or -3.5.', column)


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


def parse_days(texts: Sequence[str], column: Column, *, listed: bool) -> tuple[date, ...]:
    """The days ``texts`` name in a filter on ``column``, each written as ``_written_day`` reads it, white space
    around it ignored. ``listed`` says they are several values (a list, or the two of a ``between``), where the
    reason must not ask for one date.

    Every text's format is checked before any day's range, so the format is the mistake answered wherever it
    stands (PRD-E3). The last day there is, 9999-12-31, is refused in every condition: it has no next day to bound
    it by, and that is the user's filter, not a failed query."""
    days: list[date] = []
    for text in texts:
        day = _written_day(text.strip())
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


def next_day(day: date) -> date:
    """The day after ``day``, which ``parse_days`` and ``_day_span`` made sure exists."""
    return day + timedelta(days=1)


def _day_span(text: str, column: Column) -> tuple[date, date]:
    """The days a date "contains" on ``column`` names, from the first up to, not including, the second: ``YYYY`` a
    year, ``YYYY-MM`` a month, both in ASCII digits, or a day as ``_written_day`` reads it, so `contains` and `=`
    take the same day. A span that holds the last day there is, is refused as that day is."""
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
        return start, next_day(start)
    last = start.month if form['month'] else 12
    if (start.year, last) == (date.max.year, 12):
        raise _last_day(column)
    return start, date(start.year + 1, 1, 1) if last == 12 else date(start.year, last + 1, 1)


def _column_search(
    search: Search, column: Column, able: Capabilities, hooked: Callable[[Column, str], Any]
) -> Filter | Group | None:
    """One column's search: a list of exact values (``multi_values`` or a multi-select regex), or the words of its text
    that the column must hold, which is nothing when the text has none.
    On a boolean column a plain value is an exact value too, and so is one on a number or date column where the
    backend has no text of it to search in: one number, or the span a year, a month or a day names. On a date
    column each entry of a list is a day. A hooked column (a string, PRD-D14) is searched for its whole text, trimmed,
    by equality, and for its list entries by equality (PRD-D15, PRD-D17)."""
    kind = able.type
    if search.multi_values and not _idle_text(search, column):  # text with no word adds nothing (PRD-F6)
        # Only a client that builds its own request sends both, so this one tells the integrator what to send
        raise mistake(
            'datatables_values_refused',
            f'The filter on "{column.name}" holds both text and a list of values. Send only one of them.',
            column,
        )
    searched_words: list[tuple[str, bool]] = []
    if not (search.multi_values or search.regex):
        if column.search_value is not None:  # before the words: its text has no grammar, only a whole value (PRD-D17)
            # Python's strip is Unicode White_Space for admitted text: the characters it adds (U+001C to U+001F) are
            # control characters, which `_control_character` refuses
            whole = search.value.strip()
            return Equals(column, kind, hooked(column, whole)) if whole else None
        searched_words = _words(search.value)
        if not searched_words:  # no word is no search, on a column of any type (PRD-F6)
            return None
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
        return OneOf(column, kind, (parse_boolean(search.value, column),))
    elif able.value_search and kind == 'number':
        return Equals(column, kind, parse_number(search.value, column))
    elif able.value_search and kind == 'date':
        start, end = _day_span(search.value, column)
        return Range(column, kind, Bound(start, True), Bound(end, False))
    else:
        return _all('AND', [_word([(column, kind)], word, negated) for word, negated in searched_words])
    # An entry that holds nothing is no value: it matches a row without one (PRD-F17, PRD-F20), and no hook is
    # given it (PRD-D16). A string column takes an entry of white space as that exact value; on any other type
    # it holds nothing either
    held = [text for text in texts if (text if kind == 'string' else text.strip())]
    empty = len(held) < len(texts)
    if held and not able.whole_string:  # analysed text holds no whole string to match exactly (PRD-D19)
        raise mistake(
            'datatables_search_refused',
            f'The column "{column.name}" can be searched only for text it contains, not for a list of exact values. '
            'Type the text to search for instead.',
            column,
        )
    values: tuple[Any, ...]
    if kind == 'boolean':
        values = tuple(parse_boolean(text, column) for text in held)
    elif kind == 'date':
        values = parse_days(held, column, listed=True)
    elif kind == 'number':
        values = tuple(parse_number(text, column, listed=True) for text in held)
    else:
        values = tuple(hooked(column, text) for text in held)
    return OneOf(column, kind, values, empty)


def _alternatives(pattern: str) -> list[str] | None:
    """The exact values of a categorical filter's regex, in one of the four forms of PRD-F16: ``abc``, ``a|b``,
    ``^a$`` or ``^(a|b)$``; None for any other regex. Alternatives need the group once the anchors are there
    (``^a|b$`` anchors each value at one end only), and a backslash escapes only a character of PRD-F19's set
    (``\\d`` is a class, not the letter d). An alternative that holds nothing is the value ``''``, which means
    empty (PRD-F17): ``^(|a)$``, ``a|``, ``^()$``, ``^$``, a bare ``|``."""
    body = pattern
    alone = False  # anchored without a group, which holds one value
    # A `$` at the end is taken for the anchor also after a backslash: after escaped backslashes it is one (the
    # value `C:\` is written `^C:\\$`), and were it escaped itself, what is left would end in a lone backslash,
    # which is refused below as the unanchored `^` would be
    if body.startswith('^') and body.endswith('$'):
        body = body[1:-1]
        alone = not (body.startswith('(') and body.endswith(')'))
        if not alone:
            body = body[1:-1]
    values: list[str] = []
    current: list[str] = []
    chars = iter(body)
    for char in chars:
        if char == '\\':
            escaped = next(chars, None)
            if escaped is None or escaped not in _REGEX_ESCAPED:
                return None
            current.append(escaped)
        elif char == '|':
            if alone:
                return None
            values.append(''.join(current))
            current = []
        elif char in _REGEX_SPECIAL:
            return None
        else:
            current.append(char)
    values.append(''.join(current))
    return values
