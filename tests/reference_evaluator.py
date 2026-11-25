"""A reference evaluator: the answer a request should get, computed over rows held in memory from the requirements of
`docs/PRD.md` and the statements of `spec/`, never from the library's code (issue #205).

`evaluate(decl, rows, params)` gives the page's row keys in order with both counts (`Answer`), or the code of the user's
mistake (`Refusal`). Where the requirements leave an answer open it must not guess: it would raise a marked gap,
reported in #205. No rule needs one today, since every gap met so far is decided; a gap found later is added back as a
marked `SpecGap` exception. A request it was not written for (a number with a fraction on an integer column, for one)
raises `OutOfScope`. It is test code: nothing in `src` imports it. What it covers, and what it leaves out, is listed in
`tests/test_reference_properties.py`.

By default every answer is the SQL one on SQLite: number and date columns are searched as their text (PRD-F23), case
folds for ASCII letters only (PRD-F9), a stored `''` is empty on a string column (PRD-F26), an exact match keeps
trailing spaces, and text orders by code point, which is SQLite's default `BINARY` collation (PRD-O6). `Engine` holds
what another backend reads differently, as properties with their PRD clauses; a test maps a backend to one. Folding
beyond ASCII is not among them: the evaluator's text is ASCII.

A declaration is a tuple of `(name, type, searchable, orderable)`; the first column is the table's unique key, the
tie-breaker of PRD-O3, and every row holds every declared name. The type `number` is an SQL `BigInteger` (whole
numbers only); `decimal` is a number column the evaluator alone names, an exact `Numeric` on SQL whose cells are
`Decimal`s (#217), which a test declares to the library as `type='number'`.
"""

from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from datetime import timedelta
from decimal import Decimal
from decimal import InvalidOperation
import re
import string
from typing import Any
from typing import Literal
import unicodedata

from datatables_backend import DTColumn
from datatables_backend import DTParams
from datatables_backend import Search
from datatables_backend import SearchCriterion
from datatables_backend import SearchGroup

type Kind = Literal['string', 'number', 'decimal', 'date', 'boolean']
type Declared = tuple[str, Kind, bool, bool]
type Row = Mapping[str, Any]
type Predicate = Callable[[Row], bool]


@dataclass(frozen=True)
class Answer:
    """An answered request: the key of each row of the page, in order, and the two counts."""

    ids: tuple[Any, ...]
    records_total: int
    records_filtered: int


@dataclass(frozen=True)
class Refusal:
    """A request refused for a user's mistake: the stable code only (PRD-E2); the message is not modelled."""

    code: str


def code_points(text: str) -> str:
    """PRD-O6 on SQLite: text in code-point order, its default `BINARY` collation."""
    return text


def _stored_on_sqlite(value: Decimal) -> int | float:
    """PRD section 5 (`docs/databases.md`, "A `Numeric` column is not compared exactly"): SQLite stores a `Numeric` as a
    floating-point number, and as an integer when that number is whole and fits 64 bits (its NUMERIC affinity), so
    12345678901234567.89 is stored as 12345678901234568."""
    number = float(value)
    return int(number) if number.is_integer() and -(2**63) <= number < 2**63 else number


def sqlite_text(value: Decimal) -> str:
    """PRD-F23 on SQLite: a decimal's search text is the text of what SQLite stores (`_stored_on_sqlite`), an integer
    as its digits and a floating-point number with 15 significant digits, so 0.30 reads `0.3` and 0.00 reads `0`
    (`docs/decisions/006-number-search-text.md`)."""
    stored = _stored_on_sqlite(value)
    return str(stored) if isinstance(stored, int) else format(stored, '.15g')


def fixed_scale(value: Decimal) -> str:
    """PRD-F23 and PRD section 5 on PostgreSQL, MySQL, MariaDB and SQL Server: a `Numeric(20, 2)` keeps its scale, so
    0.3 reads `0.30` and 0 reads `0.00` (`docs/decisions/006-number-search-text.md`)."""
    return f'{value:.2f}'


@dataclass(frozen=True)
class Engine:
    """What the requirements leave to a backend, as properties: every rule reads a flag, never an engine's name. The
    default is SQLite's reading, which the property against SQLite uses."""

    #: False, PRD-F23: on SQL a number or a date column takes part in the global and column searches as its text.
    #: True: PRD-F32, the global search skips number and date columns; PRD-F33, a column search on a number column
    #: takes one number and matches it exactly; PRD-F34, one on a date column takes one date term, PRD-F22's span. Each
    #: is read as one value: several words, `!` or quotes are refused at step 2 (PRD-E3), and no word cap applies
    #: (PRD-F14). PRD-F45 reads ColumnControl's `contains` as that column search.
    typed_search: bool = False
    #: PRD-F26: True on SQL, where a stored `''` is empty beside NULL; False where only a missing field is empty
    #: (OpenSearch), so a stored `''` is a value.
    empty_string_is_empty: bool = True
    #: PRD section 5 (`docs/databases.md`, `docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md`):
    #: an exact match, a list entry and the empty test ignore trailing spaces, so a stored `' '` equals `''`.
    pad_space: bool = False
    #: PRD-O6: text order is the database's collation, given as a sort key of a text value; equal keys tie, and the
    #: unique key then breaks the tie (PRD-O3). None: the order is not known, and ordering by text raises
    #: `OutOfScope`.
    order_key: Callable[[str], Any] | None = code_points
    #: PRD-F25 and PRD section 5, how a number is compared with a `decimal` column: `exact`, a decimal comparison
    #: (PostgreSQL, MySQL, MariaDB, SQL Server, `docs/decisions/010-decimal-numbers.md`); `sqlite`, the cell as SQLite
    #: stores it (`_stored_on_sqlite`) and the number as an integer when it is whole and fits 64 bits, else as a
    #: floating-point number, compared as SQLite compares an integer with a floating-point number, exactly; `double`,
    #: both as floating-point numbers (OpenSearch, a `double` field).
    numeric: Literal['exact', 'sqlite', 'double'] = 'sqlite'
    #: PRD-F25, PRD-E3: on SQL Server a number of more than this many digits in all, a fraction's trailing zeros and
    #: the zero before the point of a number below one not counted, is the column's own step-3 mistake; None elsewhere.
    decimal_digits: int | None = None
    #: PRD-F23: a decimal cell's search text, the database's own spelling; each leg measures it and passes it here.
    decimal_text: Callable[[Decimal], str] = sqlite_text


SQLITE = Engine()


class OutOfScope(AssertionError):
    """A request the evaluator was not written for: the generator must not send it."""


class _Refused(Exception):
    """A mistake found while a value is read: its step in PRD-E3's order and its code. Step 3 is split as PRD-E3 says:
    the maximum date of PRD-F24 as a bound, or a number with more digits after the point than PRD-F25 allows, is 3; a
    number the column's own type does not take (an SQL integer column's range, PRD-F25) is 3.5, after every mistake of
    3 in the request. A number of more than 16,383 digits after the point reaches 3 on a `decimal` column; more than 38
    digits in all on SQL Server reaches 3.5 there."""

    def __init__(self, step: float, code: str) -> None:
        super().__init__(code)
        self.step = step
        self.code = code


_INVALID_FILTER = 'invalid_filter'
_NO_VALUE = object()

# -- reading values: numbers, days, spans and booleans (PRD-F21, PRD-F22, PRD-F24, PRD-F25, PRD-F43) ---

_WHOLE = re.compile(r'[+-]?[0-9]+')
_NUMBER = re.compile(r'[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')
_DAY = re.compile(r'([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[ T]([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.[0-9]+)?)?)?')
_YEAR = re.compile(r'[0-9]{4}')
_MONTH = re.compile(r'([0-9]{4})-([0-9]{2})')


def _number(text: str) -> int:
    """PRD-F25: ASCII digits with an optional sign, white space around ignored; a whole number outside the signed
    64-bit range of a `BigInteger` is a mistake of the column's own type, in step 3 after the maximum date of PRD-F24
    as a bound and a number with more digits after the point than PRD-F25 allows (PRD-E3). Every number column is read
    as a BigInteger: the generator sends a number that large only to one, so the narrower range of an Integer column is
    not modelled. A number that is not finite is one the column cannot read, a mistake of step 2 (the reviewer's
    ruling for #217); any other number with a fraction or an exponent is out of scope on this type."""
    written = text.strip()
    if _WHOLE.fullmatch(written):
        number = int(written)
        if not -(2**63) <= number < 2**63:
            raise _Refused(3.5, 'invalid_number')
        return number
    if _NUMBER.fullmatch(written):
        _finite(written)
        raise OutOfScope('a number with a fraction or an exponent on an integer column')
    raise _Refused(2, 'invalid_number')


def _finite(written: str) -> None:
    """PRD-F25: a number is finite when a floating-point number holds it, so `1e400` is refused, at step 2 (the
    reviewer's ruling for #217: a value its column cannot read)."""
    if float(written) in (float('inf'), float('-inf')):
        raise _Refused(2, 'invalid_number')


#: PRD-F25: the most digits after the point a number may have (PostgreSQL's limit)
_FRACTION_DIGITS = 16383


def _decimal_reader(engine: 'Engine') -> Callable[[str], int | Decimal]:
    """PRD-F25 on a `decimal` column: the spelling of `_number`; a whole number written without a point or an exponent
    is an `int` of any size, any other number a `Decimal` that keeps every digit written, its fraction's trailing zeros
    dropped. Not finite: step 2. More than 16,383 digits after the point: a limit of every column, step 3 (PRD-E3).
    More than ``engine.decimal_digits`` digits in all (SQL Server's 38; a fraction's trailing zeros and the zero before
    the point of a number below one not counted): the column's own mistake, step 3.5 (PRD-E3). An exponent beyond what
    a `Decimal` holds is out of scope (the generator does not send one)."""

    def read(text: str) -> int | Decimal:
        written = text.strip()
        if not _NUMBER.fullmatch(written):
            raise _Refused(2, 'invalid_number')
        _finite(written)
        try:
            sign, digits, exponent = Decimal(written).as_tuple()
        except InvalidOperation:
            raise OutOfScope('a number a Decimal cannot read') from None
        assert isinstance(exponent, int)
        while exponent < 0 and len(digits) > 1 and digits[-1] == 0:  # a fraction's trailing zeros dropped, no rounding
            digits, exponent = digits[:-1], exponent + 1
        number = Decimal(0) if not any(digits) else Decimal((sign, digits, exponent))
        fraction = max(0, -exponent) if any(digits) else 0
        if fraction > _FRACTION_DIGITS:
            raise _Refused(3, 'invalid_number')
        if engine.decimal_digits is not None:
            whole = abs(int(number))
            if len(str(whole)) * (whole != 0) + fraction > engine.decimal_digits:
                raise _Refused(3.5, 'invalid_number')
        return int(written) if _WHOLE.fullmatch(written) else number

    return read


def _day(text: str) -> date:
    """PRD-F21: `YYYY-MM-DD` in ASCII digits, a day that exists, optionally a time of day after a space or a `T` that
    must exist and is ignored; white space around is ignored."""
    found = _DAY.fullmatch(text.strip())
    if not found:
        raise _Refused(2, 'invalid_date')
    year, month, day, hour, minute, second = found.groups()
    if hour is not None and (int(hour) > 23 or int(minute) > 59 or (second is not None and int(second) > 59)):
        raise _Refused(2, 'invalid_date')
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        raise _Refused(2, 'invalid_date') from None


def _bound(text: str) -> date:
    """A day used as a bound or an operand: the last day there is cannot be one (PRD-F24, step 3 of PRD-E3)."""
    day = _day(text)
    if day == date.max:
        raise _Refused(3, 'invalid_date')
    return day


def _span(text: str) -> tuple[date, date]:
    """PRD-F22: a year, a year-month or a day, and the span of days it names; a span that reaches the last day there
    is is refused as PRD-F24 says."""
    written = text.strip()
    try:
        if _YEAR.fullmatch(written):
            first, last = date(int(written), 1, 1), date(int(written), 12, 31)
        elif found := _MONTH.fullmatch(written):
            year, month = int(found.group(1)), int(found.group(2))
            first = date(year, month, 1)
            last = date(year + 1, 1, 1) - timedelta(days=1) if month == 12 else date(year, month + 1, 1) - timedelta(1)
        else:
            first = last = _day(written)
    except (ValueError, OverflowError):
        raise _Refused(2, 'invalid_date') from None
    if last == date.max:
        raise _Refused(3, 'invalid_date')
    return first, last


def _boolean(text: str) -> bool:
    """PRD-F43: `true`, `false` in any case, `1` or `0`, white space around ignored; any other value is a mistake of
    step 4."""
    written = text.strip().lower()
    if written in ('true', '1'):
        return True
    if written in ('false', '0'):
        return False
    raise _Refused(4, _INVALID_FILTER)


# -- text: folding, the cell's text, emptiness (PRD-F9, PRD-F23, PRD-F26) ---

_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _fold(text: str) -> str:
    """PRD-F9 on SQLite: ASCII letters fold, nothing else does."""
    return text.translate(_ASCII_LOWER)


def _text(kind: Kind, value: Any, engine: Engine) -> str | None:
    """PRD-F23: the text a SQL search reads, a whole number as its digits, a decimal as the database spells it and a
    day as `YYYY-MM-DD`."""
    if value is None:
        return None
    if kind == 'date':
        return value.isoformat()
    if kind == 'decimal':
        return engine.decimal_text(value)
    return str(value)


def _as_cell(kind: Kind, value: Any, engine: Engine) -> Any:
    """A cell as the engine compares and orders it: a `decimal` one as `Engine.numeric` says, any other as it is."""
    if kind != 'decimal' or value is None or engine.numeric == 'exact':
        return value
    return _stored_on_sqlite(value) if engine.numeric == 'sqlite' else float(value)


def _as_operand(kind: Kind, value: Any, engine: Engine) -> Any:
    """A read number as the engine binds it against a `decimal` column (`Engine.numeric`); any other value as read."""
    if kind != 'decimal' or engine.numeric == 'exact':
        return value
    if engine.numeric == 'sqlite' and isinstance(value, int) and -(2**63) <= value < 2**63:
        return value
    return float(value)


def _empty(kind: Kind, value: Any, engine: Engine) -> bool:
    """PRD-F26: empty is NULL, and on a column that stores text also `''` where the engine says so; under PAD SPACE a
    value of spaces only equals `''`."""
    if value is None:
        return True
    return kind == 'string' and engine.empty_string_is_empty and _exactly(value, engine) == ''


def _exactly(text: str, engine: Engine) -> str:
    """Text as an exact match compares it: folded (PRD-F9), and without its trailing spaces under PAD SPACE (PRD
    section 5)."""
    folded = _fold(text)
    return folded.rstrip(' ') if engine.pad_space else folded


# -- the smart search: words, phrases and negations (PRD-F3, PRD-F4, PRD-F5, PRD-F6, PRD-F7, PRD-F35) ---

#: PRD-F7: what counts as a space in search text
_LINE_BREAKS = str.maketrans(dict.fromkeys('\n\r\u2028\u2029', ' '))
#: DataTables' split pattern (PRD-F4)
_TOKEN = re.compile('!?["\u201c][^"\u201d]+["\u201d]|[^ ]+')
_STRAIGHT_PAIR = re.compile('"(.*)"', re.DOTALL)
_CURLY_PAIR = re.compile('\u201c(.*)\u201d', re.DOTALL)
#: DataTables' `util.escapeRegex` set (PRD-F19)
_ESCAPED = frozenset('/.*+?|()[]{}\\$^-')


def words(text: str) -> tuple[list[str], list[str]]:
    """The positive and the negated words of search text. PRD-F7: LF, CR, U+2028 and U+2029 are spaces. PRD-F3: a
    phrase starts where a word does and ends at its closing quote. PRD-F4: a matching pair of quotes is stripped, and
    any other straight quote of a positive word is removed; a negated word keeps it. PRD-F35: a negated word excludes
    only when, escaped, it is longer than one character. PRD-F6: a token that reduces to nothing adds nothing."""
    positive: list[str] = []
    negated: list[str] = []
    for token in _TOKEN.findall(text.translate(_LINE_BREAKS)):
        negate = token.startswith('!')
        word = token[1:] if negate else token
        pair = _STRAIGHT_PAIR if word.startswith('"') else _CURLY_PAIR if word.startswith('\u201c') else None
        if pair and (found := pair.fullmatch(word)):
            word = found.group(1)
        if negate:
            if sum(2 if character in _ESCAPED else 1 for character in word) > 1:
                negated.append(word)
            continue
        if word := word.replace('"', ''):
            positive.append(word)
    return positive, negated


def _searched(kind: Kind, column: str, text: str, engine: Engine) -> Predicate | None:
    """PRD-F2: every word must be found in this one column, and no negated word (PRD-F5: NULL holds no word). PRD-F11:
    a word matches as a substring, wildcards as text. None when the text holds no word (PRD-F6)."""
    positive, negated = words(text)
    if not positive and not negated:
        return None
    wanted, unwanted = [_fold(word) for word in positive], [_fold(word) for word in negated]

    def matches(row: Row) -> bool:
        cell = _text(kind, row[column], engine)
        if cell is None:
            return not wanted
        folded = _fold(cell)
        return all(word in folded for word in wanted) and not any(word in folded for word in unwanted)

    return matches


def _typed(kind: Kind, column: str, text: str, reading: '_Reading') -> Predicate | None:
    """A column search on a number or a date column of a typed-search engine. PRD-F6 decides the whole text first:
    text that reduces to no word adds nothing. Text that holds a word is read as one value: PRD-F33, one number,
    matched exactly, else `invalid_number`; PRD-F34, one date term, the span of PRD-F22, else `invalid_date`. Several
    words, `!` or quotes are no number or date term, so they are refused (step 2 of PRD-E3)."""
    positive, negated = words(text)
    if not positive and not negated:
        return None
    if kind in ('number', 'decimal'):
        number = reading.read(_reader(kind, '=', reading.engine), text)
        if number is _NO_VALUE:
            return None
        operand = _as_operand(kind, number, reading.engine)
        return lambda row: row[column] is not None and _as_cell(kind, row[column], reading.engine) == operand
    span = reading.read(_span, text)
    if span is _NO_VALUE:
        return None
    first, last = span
    return lambda row: row[column] is not None and first <= row[column] <= last


# -- categorical filters (PRD-F16, PRD-F17, PRD-F18, PRD-F19) ---

_META = frozenset('.*+?|()[]{}\\$^')


def categorical(pattern: str) -> list[str] | None:
    """The values of a categorical filter, unescaped, or None for any other regex (PRD-F18). PRD-F16: `abc`, `a|b`,
    `^a$` or `^(a|b)$`, a value holding no unescaped character of the metacharacter set; only escapes of PRD-F19's
    set are read. PRD-F17: an empty alternative stands for empty, and `^()$`, `^$` and `|` hold only that."""
    tokens: list[tuple[str, bool]] = []
    index = 0
    while index < len(pattern):
        if pattern[index] == '\\':
            if index + 1 == len(pattern) or pattern[index + 1] not in _ESCAPED:
                return None
            tokens.append((pattern[index + 1], True))
            index += 2
        else:
            tokens.append((pattern[index], False))
            index += 1

    def bare(token: tuple[str, bool], character: str) -> bool:
        return token == (character, False)

    alternatives_allowed = True
    if tokens and bare(tokens[0], '^'):
        if len(tokens) < 2 or not bare(tokens[-1], '$'):
            return None
        tokens = tokens[1:-1]
        if len(tokens) >= 2 and bare(tokens[0], '(') and bare(tokens[-1], ')'):
            tokens = tokens[1:-1]
        else:
            alternatives_allowed = False
    elif tokens and bare(tokens[-1], '$'):
        return None
    alternatives: list[list[str]] = [[]]
    for character, escaped in tokens:
        if not escaped and character == '|':
            if not alternatives_allowed:
                return None
            alternatives.append([])
        elif not escaped and character in _META:
            return None
        else:
            alternatives[-1].append(character)
    return [''.join(alternative) for alternative in alternatives]


# -- SearchBuilder conditions (PRD-F27, PRD-F30, PRD-F36, PRD-F37) ---

_STRING_CONDITIONS = frozenset(
    ['=', '!=', 'contains', '!contains', 'starts', '!starts', 'ends', '!ends', 'null', '!null']
)
_NUMBER_CONDITIONS = frozenset(['=', '!=', '<', '<=', '>', '>=', 'between', '!between', 'null', '!null'])
#: PRD-F27: the conditions each declared type takes
_CONDITIONS: dict[Kind, frozenset[str]] = {
    'string': _STRING_CONDITIONS,
    'number': _NUMBER_CONDITIONS,
    'decimal': _NUMBER_CONDITIONS,
    'date': _NUMBER_CONDITIONS | {'contains', '!contains'},
    'boolean': frozenset(['=', '!=', 'null', '!null']),
}


def _arity(condition: str) -> int:
    """PRD-F36: how many values a condition reads."""
    return 0 if condition in ('null', '!null') else 2 if condition in ('between', '!between') else 1


def _reader(kind: Kind, condition: str, engine: Engine) -> Callable[[str], Any]:
    if kind == 'number':
        return _number
    if kind == 'decimal':
        return _decimal_reader(engine)
    if kind == 'date':
        return _span if condition in ('contains', '!contains') else _bound
    if kind == 'boolean':
        return _boolean
    return str


def _compared(kind: Kind, column: str, condition: str, values: Sequence[Any], engine: Engine) -> Predicate:
    """A condition that fits the column, over read values. PRD-F9: text folds; `=` and `!=` are exact matches, which
    PAD SPACE compares without trailing spaces (PRD section 5). PRD-F21: days compare as days, and PRD-F22's span is a
    range of days. PRD-F37: NULL satisfies every negative condition and no positive one."""
    if condition in ('null', '!null'):
        return lambda row: _empty(kind, row[column], engine) == (condition == 'null')
    negated = condition.startswith('!')
    base = condition[1:] if negated else condition  # `!=` is the negation of `=`
    if kind == 'string':
        value = _fold(values[0])
        exact = _exactly(values[0], engine)
        tests: dict[str, Callable[[str], bool]] = {
            '=': lambda cell: _exactly(cell, engine) == exact,
            'contains': lambda cell: value in cell,
            'starts': lambda cell: cell.startswith(value),
            'ends': lambda cell: cell.endswith(value),
        }
        test: Callable[[Any], bool] = lambda cell: tests[base](cell if base == '=' else _fold(cell))  # noqa: E731
    elif base in ('between', 'contains'):
        low, high = values[0] if base == 'contains' else values  # a span, or the two bounds
        test = lambda cell: low <= cell <= high  # noqa: E731
    else:
        operand = values[0]
        test = {
            '=': lambda cell: cell == operand,
            '<': lambda cell: cell < operand,
            '<=': lambda cell: cell <= operand,
            '>': lambda cell: cell > operand,
            '>=': lambda cell: cell >= operand,
        }[base]

    def matches(row: Row) -> bool:
        cell = row[column]
        if cell is None:
            return negated
        return test(_as_cell(kind, cell, engine)) != negated

    return matches


# -- the walk over a request, in PRD-E3's read order ---


class _Reading:
    """The mistakes found while a request is read. Each part of the request (the global search, each
    column's plain search, ColumnControl search and list, each criterion, each order entry) has a place in PRD-E3's
    read order; a mistake is kept with its step and that place."""

    def __init__(self, engine: Engine) -> None:
        self.mistakes: list[tuple[float, int, str]] = []
        self.place = 0
        self.engine = engine

    def next_part(self) -> None:
        self.place += 1

    def refuse(self, step: float = 4, code: str = _INVALID_FILTER) -> None:
        self.mistakes.append((step, self.place, code))

    def read(self, reader: Callable[[str], Any], text: str) -> Any:
        try:
            return reader(text)
        except _Refused as mistake:
            self.refuse(mistake.step, mistake.code)
            return _NO_VALUE


def _key(column: DTColumn, declared: Mapping[str, Declared]) -> str | None:
    """PRD-P15: a column's name falls back to its `data`. PRD-P21: None for a nameless column, whose `data` is empty
    (null and an object read as empty) or the text `function` when no column is declared by that name."""
    if column.name:
        return column.name
    if not column.data or (column.data == 'function' and 'function' not in declared):
        return None
    return column.data


def _exact(kind: Kind, column: str, values: Sequence[str], reading: _Reading) -> Predicate | None:
    """A categorical filter's values or a list (`multi_values`, ColumnControl's list) matched exactly. PRD-F9: text
    folds. PRD-F20: an empty entry means empty, an entry of only white space is that value on a string column and
    empty on any other. PRD-F21, PRD-F33, PRD-F44: a value on a date, number or boolean column is read as `=` reads it;
    every entry is read, so each mistake is found. PAD SPACE compares text without trailing spaces (PRD section 5)."""
    engine = reading.engine
    wanted: list[Any] = []
    empty = False
    failed = False
    for value in values:
        if value == '' or (kind != 'string' and not value.strip()):
            empty = True
        elif kind == 'string':
            wanted.append(_exactly(value, engine))
        else:
            read = reading.read(_reader(kind, '=', engine), value)
            failed = failed or read is _NO_VALUE
            wanted.append(read if read is _NO_VALUE else _as_operand(kind, read, engine))
    if failed:
        return None

    def matches(row: Row) -> bool:
        cell = row[column]
        if empty and _empty(kind, cell, engine):
            return True
        if cell is None:
            return False
        return (_exactly(cell, engine) if kind == 'string' else _as_cell(kind, cell, engine)) in wanted

    return matches


def _boolean_searched(column: str, text: str, reading: _Reading) -> Predicate | None:
    """PRD-F44: a column search on a boolean column takes only a value of PRD-F43's (white space around ignored) and
    matches it exactly; any other text is refused, several words, `!` and quotes included. Text that reduces to no
    word adds nothing (PRD-F6)."""
    positive, negated = words(text)
    if not positive and not negated:
        return None
    value = reading.read(_boolean, text)
    if value is _NO_VALUE:
        return None
    return lambda row: row[column] == value


def _global(params: DTParams, declared: Mapping[str, Declared], reading: _Reading) -> Predicate | None:
    """PRD-F1: every word is found in some searchable column of the row, and no negated word in any (PRD-F5). PRD-F41:
    the columns are those the request names that both sides mark searchable and the server declares; PRD-F38: a column
    the request names twice is covered if any of its entries is searchable. PRD-F44 leaves out boolean columns, PRD-P21
    nameless ones, and PRD-F32 number and date columns on a typed-search engine. PRD-F6: text that reduces to no word
    is idle, also with no usable column; otherwise PRD-F13: a search over no usable column is refused. PRD-F18: any
    regex in the global search is refused, unless the text holds no word (the reviewer's decision of 2026-11-20).
    PRD-F40: a list is refused, an empty one idle."""
    reading.next_part()
    search = params.search
    if search.multi_values:
        reading.refuse()
        return None
    positive, negated = words(search.value)
    flags: dict[str, list[bool]] = {}
    for column in params.columns:
        key = _key(column, declared)
        skipped = ('boolean', 'number', 'decimal', 'date') if reading.engine.typed_search else ('boolean',)
        if key is not None and key in declared and declared[key][2] and declared[key][1] not in skipped:
            flags.setdefault(key, []).append(column.searchable)
    columns = [key for key, marks in flags.items() if any(marks)]
    if not positive and not negated:
        return None
    if search.regex:
        reading.refuse()
        return None
    wanted, unwanted = [_fold(word) for word in positive], [_fold(word) for word in negated]

    if not columns:
        reading.refuse()
        return None

    def matches(row: Row) -> bool:
        texts = [_fold(text) for key in columns
                 if (text := _text(declared[key][1], row[key], reading.engine)) is not None]  # fmt: skip
        found = all(any(word in text for text in texts) for word in wanted)
        return found and not any(word in text for word in unwanted for text in texts)

    return matches


def _plain(search: Search, key: str | None, found: Declared | None, reading: _Reading) -> Predicate | None:
    """One column's search. PRD-F6: with `regex` false, text that reduces to no word (spaces only, a lone quote, a `!`
    word PRD-F35 drops) is idle, as an empty value is, wherever it is sent (a control character in it was refused
    first, PRD-F15). PRD-D6, PRD-F41, PRD-P21: one with a value or a non-empty list on a column the server does not
    declare searchable, or on a nameless one, is refused without its value being read (PRD-E3). PRD-F39: a value
    beside a list is refused. PRD-F20: a list; PRD-F16: a categorical filter, any other regex refused (PRD-F18);
    PRD-F44: a boolean column; on a typed-search engine PRD-F33 and PRD-F34 a number or a date column; PRD-F2 and
    PRD-F23: the smart search of the column's text."""
    reading.next_part()
    values = search.multi_values or []
    holds_value = bool(search.value) if search.regex else any(words(search.value))
    if not holds_value and not values:
        return None
    if key is None or found is None or not found[2]:
        reading.refuse()
        return None
    if holds_value and values:
        reading.refuse()
        return None
    kind = found[1]
    if values:
        return _exact(kind, key, values, reading)
    if search.regex:
        alternatives = categorical(search.value)
        if alternatives is None:
            reading.refuse()
            return None
        return _exact(kind, key, alternatives, reading)
    return _column_searched(kind, key, search.value, reading)


def _column_searched(kind: Kind, column: str, text: str, reading: _Reading) -> Predicate | None:
    """The column search of PRD-F2 on a column of any type (and ColumnControl's `contains`, PRD-F45): PRD-F44 on a
    boolean column, PRD-F33 and PRD-F34 on a number or a date column of a typed-search engine, else the smart search
    of the column's text (PRD-F23)."""
    if kind == 'boolean':
        return _boolean_searched(column, text, reading)
    if reading.engine.typed_search and kind in ('number', 'decimal', 'date'):
        return _typed(kind, column, text, reading)
    return _searched(kind, column, text, reading.engine)


#: PRD-F45: the SearchBuilder condition each ColumnControl logic is read as; `contains` is a column search
_LOGICS = {
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


def _condition(kind: Kind, column: str, condition: str, values: Sequence[str], reading: _Reading) -> Predicate | None:
    """A condition that fits its column, with the values it reads, none of them empty: each value is read (PRD-F21,
    PRD-F22, PRD-F25, PRD-F43). PRD-F27: the two values of `between` and `!between` are a range in either order,
    compared as read (numbers as numbers, dates as days)."""
    read = [reading.read(_reader(kind, condition, reading.engine), value) for value in values]
    if any(value is _NO_VALUE for value in read):
        return None
    read = [_as_operand(kind, value, reading.engine) for value in read]
    if condition in ('between', '!between'):
        read = sorted(read)
    return _compared(kind, column, condition, read, reading.engine)


def _columncontrol(
    column: DTColumn, key: str | None, found: Declared | None, reading: _Reading
) -> list[Predicate | None]:
    """PRD-F45: ColumnControl's search, then its list, each a clause of its own beside the plain search (AND). A
    search is idle when its value is empty and its logic is neither `empty` nor `notEmpty`, a list when it is empty.
    On a column that cannot be searched an active one is refused (PRD-F41)."""
    key = key if found is not None and found[2] else None  # None: a column that cannot be searched (PRD-F41)
    found_kind: Kind = found[1] if found else 'string'
    clauses: list[Predicate | None] = []
    reading.next_part()
    search = column.columnControl.search
    if search.value or search.logic in ('empty', 'notEmpty'):
        if key is None:
            reading.refuse()
            clauses.append(None)
        elif search.logic == 'contains':
            clauses.append(_column_searched(found_kind, key, search.value, reading))
        else:
            condition = _LOGICS[search.logic]
            if condition not in _CONDITIONS[found_kind]:
                reading.refuse()
                clauses.append(None)
            else:
                values = [] if condition in ('null', '!null') else [search.value]
                clauses.append(_condition(found_kind, key, condition, values, reading))
    reading.next_part()
    entries = column.columnControl.list
    if entries:
        if key is None:
            reading.refuse()
        else:
            clauses.append(_exact(found_kind, key, entries, reading))
    return clauses


def _criterion(criterion: SearchCriterion, declared: Mapping[str, Declared], reading: _Reading) -> Predicate | None:
    """One SearchBuilder criterion. PRD-F30: one with no condition, no column, or without the values its condition
    takes (an empty value counts as missing) adds nothing. PRD-D6, PRD-F41: one on a column the server does not
    declare searchable is refused, and PRD-F27: one whose condition does not fit the declared type, each without its
    value being read (PRD-E3). PRD-F36: more values than the condition takes is refused, after the values it takes are
    read; `null` and `!null` read none. PRD-E3: a criterion on a refused column or with a condition that does not fit
    is refused even without its value; one without a condition adds nothing, since nothing can be checked (PRD-F30)."""
    reading.next_part()
    condition, name, values = criterion.condition, criterion.origData, criterion.value
    if not name:
        return None
    found = declared.get(name)
    fits = found is not None and found[2] and condition in _CONDITIONS[found[1]]
    if condition is None:
        return None
    arity = _arity(condition)
    surplus = arity > 0 and len(values) > arity
    taken = values[:arity]
    missing = len(taken) < arity or '' in taken
    if not fits:
        reading.refuse()
        return None
    assert found is not None
    if surplus:
        for value in taken:
            if value:
                reading.read(_reader(found[1], condition, reading.engine), value)
        reading.refuse()
        return None
    if missing:
        return None
    return _condition(found[1], name, condition, taken, reading)


def _group(group: SearchGroup, declared: Mapping[str, Declared], reading: _Reading) -> Predicate | None:
    """PRD-F28: a group joins its children with AND or OR; an empty group, and one whose every child adds nothing,
    adds nothing (PRD-F30)."""
    children: list[Predicate] = []
    for node in group.criteria:
        child = (
            _group(node, declared, reading) if isinstance(node, SearchGroup) else _criterion(node, declared, reading)
        )
        if child is not None:
            children.append(child)
    if not children:
        return None
    if group.logic == 'AND':
        return lambda row: all(child(row) for child in children)
    return lambda row: any(child(row) for child in children)


def _ordering(params: DTParams, declared: Mapping[str, Declared], reading: _Reading) -> list[tuple[str, bool]]:
    """PRD-O1: an entry is applied when the server declares its column orderable; refused when the request marks it
    orderable and the server does not, or the name is undeclared; skipped when both mark it unorderable, when the
    request marks an undeclared name unorderable, or on a nameless column (PRD-P21). PRD-O8: an entry on a column an
    earlier applied entry orders by is skipped. A refused entry is a mistake of step 4 (PRD-E3). The pairs are the
    column and whether it is descending."""
    applied: list[tuple[str, bool]] = []
    for entry in params.order:
        reading.next_part()
        column = params.columns[entry.column]
        key = _key(column, declared)
        if key is None:
            continue
        found = declared.get(key)
        server = found is not None and found[3]
        if column.orderable and not server:
            reading.refuse()
        elif server and all(key != name for name, _ in applied):
            applied.append((key, entry.dir == 'desc'))
    return applied


def _sorted(
    rows: Sequence[Row], applied: list[tuple[str, bool]], declared: Mapping[str, Declared], unique: str, engine: Engine
) -> list[Row]:
    """PRD-O8: the first applied entry is primary. PRD-O2: NULL is the lowest value, false before true. PRD-O6: text in
    the database's collation, the engine's sort key (code-point order on SQLite). A decimal orders as the engine
    compares it (`Engine.numeric`): on SQLite 12345678901234567.89 and .90 tie. PRD-O3, PRD-O4: the unique key breaks
    ties, equal sort keys included, in the first applied entry's direction, ascending when none is applied."""

    def key(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if engine.order_key is None:
            raise OutOfScope('an order by text where the collation is not known')
        return engine.order_key(value)

    first_descending = applied[0][1] if applied else False
    ordered = sorted(rows, key=lambda row: row[unique], reverse=first_descending)
    for name, descending in reversed(applied):
        kind: Kind = declared[name][1]

        def sort_key(row: Row, name: str = name, kind: Kind = kind) -> Any:
            return (0, 0) if row[name] is None else (1, key(_as_cell(kind, row[name], engine)))

        ordered.sort(key=sort_key, reverse=descending)
    return ordered


# -- step 1 of PRD-E3: control characters, lengths, words ---


def _control(text: str) -> bool:
    """PRD-F15: a Unicode control character (Cc) other than LF and CR."""
    return any(unicodedata.category(character) == 'Cc' and character not in '\n\r' for character in text)


def _step_one_filters(
    params: DTParams, declared: Mapping[str, Declared], engine: Engine
) -> Iterator[tuple[list[str], list[str], list[str]]]:
    """The filters of the request in PRD-E3's read order, each as the values a control character is looked for in,
    the values whose length is measured and the texts whose words are counted. PRD-F14 measures each value a condition
    reads as sent: "A value no condition reads, a surplus value or that of `null`, is not measured", and PRD-F15 looks
    for a control character only in "a SearchBuilder value its condition reads (PRD-F36)", so neither looks at the
    values of an unfinished criterion: one with no condition or no column (PRD-F30), which reads none. A criterion
    with a condition and a column but too few values (a `between` with one) is still measured, value by value
    (PRD-F14, PRD-F30). PRD-F14 measures a categorical filter's alternatives "after unescaping and not
    the raw pattern", and PRD-F18 refuses any other regex, so that one is not measured. Words are counted in search
    text, ColumnControl's `contains` included, which is the column search of PRD-F2 (PRD-F45), so PRD-F14's word cap
    applies to it; not in a list, nor in a value read as one (a boolean, and on a typed-search engine a number or a
    date, PRD-F33, PRD-F34). PRD-F40: the entries of a list on the global search are
    not read, so PRD-F14 and PRD-F15 do not apply to them and PRD-F38's bounds on lists do not count them (those
    bounds are out of scope here, so nothing counts them)."""
    search = params.search
    yield [search.value], [search.value], [search.value]
    for column in params.columns:
        key = _key(column, declared)
        kind = declared[key][1] if key is not None and key in declared else 'string'
        one_value = ('boolean', 'number', 'decimal', 'date') if engine.typed_search else ('boolean',)
        plain = column.search
        values = plain.multi_values or []
        measured = [plain.value, *values]
        if plain.regex and plain.value:
            measured = [*(categorical(plain.value) or []), *values]
        texts = [] if plain.regex or kind in one_value else [plain.value]
        yield [plain.value, *values], measured, texts
        control = column.columnControl.search
        active = control.logic not in ('empty', 'notEmpty') and control.value
        searched = [control.value] if active else []
        yield searched, searched, searched if control.logic == 'contains' and kind not in one_value else []
        yield column.columnControl.list, column.columnControl.list, []
    if params.searchBuilder is not None:
        for criterion in _criteria(params.searchBuilder):
            if criterion.condition is not None and criterion.origData:
                taken = criterion.value[: _arity(criterion.condition)]
                yield taken, taken, []


def _criteria(group: SearchGroup) -> Iterator[SearchCriterion]:
    for node in group.criteria:
        if isinstance(node, SearchGroup):
            yield from _criteria(node)
        else:
            yield node


def _step_one(params: DTParams, declared: Mapping[str, Declared], engine: Engine) -> str | None:
    """PRD-E3 step 1: the first filter in read order with a mistake answers; within one filter a control character in
    any value comes first (PRD-F15), then any value over 255 code points, then more than 16 words (PRD-F14). The
    bounds of PRD-F38 and PRD-C17 are left out: the generator stays far within them."""
    for checked, measured, texts in _step_one_filters(params, declared, engine):
        if any(_control(value) for value in checked):
            return 'invalid_character'
        if any(len(value) > 255 for value in measured):
            return 'value_too_long'
        if any(sum(map(len, words(text))) > 16 for text in texts):
            return 'too_many_words'
    return None


def evaluate(
    decl: Sequence[Declared], rows: Sequence[Row], params: DTParams, engine: Engine = SQLITE
) -> Answer | Refusal:
    """The answer the requirements give `params` over `rows`. PRD-E3: a mistake of step 1 in read order, else the
    mistake of the lowest step, the first in read order within it. PRD-F12: every filter joins with AND. PRD-C1,
    PRD-C2: the counts before and after the filters. PRD-C3: a start at or past the end gives an empty page and both
    counts. ``engine`` says what the requirements leave to the backend; the default is SQLite's reading."""
    declared = {column[0]: column for column in decl}
    if (code := _step_one(params, declared, engine)) is not None:
        return Refusal(code)
    reading = _Reading(engine)
    clauses = [_global(params, declared, reading)]
    for column in params.columns:
        key = _key(column, declared)
        found = declared.get(key) if key is not None else None
        clauses.append(_plain(column.search, key, found, reading))
        clauses.extend(_columncontrol(column, key, found, reading))
    if params.searchBuilder is not None:
        clauses.append(_group(params.searchBuilder, declared, reading))
    applied = _ordering(params, declared, reading)
    if reading.mistakes:
        return Refusal(min(reading.mistakes, key=lambda mistake: mistake[:2])[2])
    kept = [clause for clause in clauses if clause is not None]
    matched = [row for row in rows if all(clause(row) for clause in kept)]
    page = _sorted(matched, applied, declared, decl[0][0], engine)[params.start : params.start + params.length]
    return Answer(tuple(row[decl[0][0]] for row in page), len(rows), len(matched))
