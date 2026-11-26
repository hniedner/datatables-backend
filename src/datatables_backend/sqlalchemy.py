"""DataTables from SQL through SQLAlchemy 2, async and sync.

users = SQLTable(users_table, [Column('id'), Column('email', field='email_address'), Column('joined')])
async with AsyncSession(engine) as session:
    response = await users.fetch(params, session)
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
import logging
from typing import TYPE_CHECKING
from typing import Any
from typing import assert_never

from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import Enum
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import Join
from sqlalchemy import Numeric
from sqlalchemy import SmallInteger
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import and_
from sqlalchemy import cast as sql_cast
from sqlalchemy import false
from sqlalchemy import func
from sqlalchemy import literal
from sqlalchemy import not_
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy import type_coerce
from sqlalchemy.dialects.mssql import DATETIMEOFFSET
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.sql import FromClause
from sqlalchemy.sql import Select
from sqlalchemy.sql import operators
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.elements import ClauseElement
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.expression import FunctionElement
from sqlalchemy.sql.util import find_tables
from sqlalchemy.types import NullType
from sqlalchemy.types import TypeDecorator

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
from ._plan import without_trailing_zeros
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
from .opensearch import IndexColumn  # loads no OpenSearch client library
from .protocol import DTParams
from .protocol import DTResponse
from .protocol import InvalidFilter
from .protocol import SchemaError
from .rows import RowHooks

if TYPE_CHECKING:  # importing them needs greenlet, which a sync-only install may not have
    from sqlalchemy.ext.asyncio import AsyncConnection
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ['SQLTable']

log = logging.getLogger('datatables_backend')

type _AsyncSource = AsyncSession | AsyncConnection
type _SyncSource = Session | Connection

#: The digits SQL Server's decimal type holds at most. Its ODBC driver refuses to bind a longer number (measured)
_MSSQL_DECIMAL_DIGITS = 38
#: The databases that order NULL as the lowest value themselves. MySQL and SQL Server take no NULLS FIRST or NULLS
#: LAST; every other database is told where NULL goes
_NULL_IS_LOWEST = frozenset({'mysql', 'mariadb', 'mssql', 'sqlite'})


@dataclass(frozen=True)
class _Statements:
    total: Select[Any]
    filtered: Select[Any]
    page: Select[Any]


class SQLTable:
    """A DataTables view of a SQL table, join or subquery. Build it once; ``fetch`` answers each request.

    Args:
        source: The rows: a ``Table``, a mapped class's ``__table__``, a join or a subquery.
        columns: The columns the client may see, search and sort on; nothing else is selected.
        unique_key: The name of a declared column, or of several, whose values are unique for every row: the
            tie-breaker of every order, in place of the primary key. Without it, a source must have a primary key,
            and a subquery must select every primary-key column of each table it reads (in a join too, and in a
            ``WHERE`` or a scalar subquery); otherwise the table raises ``SchemaError``. A ``UNION`` is not checked.
        base_filter: The rows the table shows at all; it applies to ``recordsTotal`` too. For a filter that differs
            per request, such as a user's rows, pass ``scope`` to ``fetch`` instead.
        rows: Hooks that shape each row and add ``DT_RowId`` / ``DT_RowData``.
        total_cache: Keeps ``recordsTotal`` (one ``COUNT(*)`` per TTL instead of per draw): a ``TotalCache``, or any
            ``TotalStore`` (``AsyncTotalStore`` for ``fetch`` only), PRD-C18.
    """

    def __init__(
        self,
        source: FromClause,
        columns: Sequence[Column],
        *,
        unique_key: str | Sequence[str] | None = None,
        base_filter: ColumnElement[bool] | None = None,
        rows: RowHooks | None = None,
        total_cache: TotalStore | AsyncTotalStore | None = None,
    ) -> None:
        self.source = source
        self.columns = tuple(columns)
        for column in self.columns:
            if isinstance(column, IndexColumn):
                raise SchemaError(
                    f'The column "{column.name}" is an IndexColumn, which declares an OpenSearch field, and a SQL '
                    f'table ignores what it adds. Declare it as Column("{column.name}", ...) instead.'
                )
        self.base_filter = base_filter
        self.rows = rows or RowHooks()
        self.total_cache = total_cache
        missing = [column.source for column in self.columns if column.source not in source.columns]
        if missing:
            raise SchemaError(f'{source} has no column {", ".join(missing)}')
        check_declaration(self.columns, self._describe)  # after the missing columns: a type is read from the source
        _check_declared_types(self.columns, source)
        if unique_key is not None:  # a declared key wins over the source's primary key
            self._tie_breakers = [self._sql(column) for column in declared_key(self.columns, unique_key)]
        elif _key_is_not_unique(source):
            raise SchemaError(
                'The source has no primary key that is unique for every row it shows: it is a view, or a join or '
                'subquery with no primary key, or a subquery that does not select every primary-key column of each '
                'table it reads. Declare one: '
                'SQLTable(..., unique_key="id") with the name of a declared column whose values are unique (a tuple '
                'of names for a key of several).'
            )
        else:
            self._tie_breakers = list(source.primary_key)

    async def fetch(
        self, params: DTParams, source: _AsyncSource, *, scope: ColumnElement[bool] | None = None
    ) -> DTResponse:
        """Answer one request on an ``AsyncSession`` or ``AsyncConnection``.

        ``scope`` limits the rows for this request only, together with the table's ``base_filter``, to ``recordsTotal``,
        ``recordsFiltered`` and the page alike (PRD-F31). It is a condition on the source's columns, such as
        ``users.c.owner_id == user.id``, built by the server from the authenticated user and never from the request:
        a scope taken from the request would let a client choose another user's rows. Column hooks do not apply to it.
        Requests with equal scopes may share a cached total; those with different ones never do (PRD-C15).

        A mistake the user can fix is answered with its ``error_code`` (see ``DTResponse``) and logged once, before
        any statement runs; any other failure raises.
        """
        try:
            statements = self._statements(params, _dialect(source), scope)
        except InvalidFilter as found:
            return refused(params, found)
        total = await self._total(source, statements)
        filtered = (await source.execute(statements.filtered)).scalar_one()
        result = await source.execute(statements.page)
        return self._response(params, total, filtered, result.mappings().all())

    def fetch_sync(
        self, params: DTParams, source: _SyncSource, *, scope: ColumnElement[bool] | None = None
    ) -> DTResponse:
        """``fetch`` for a ``Session`` or ``Connection``, with the same ``scope``.

        Raises:
            TypeError: the table's ``total_cache`` is an ``AsyncTotalStore``, which only ``fetch`` can await.
        """
        if self.total_cache is not None:
            require_sync_store(self.total_cache)
        try:
            statements = self._statements(params, _dialect(source), scope)
        except InvalidFilter as found:
            return refused(params, found)
        total = self._total_sync(source, statements)
        filtered = source.execute(statements.filtered).scalar_one()
        rows = source.execute(statements.page).mappings().all()
        return self._response(params, total, filtered, rows)

    # -- building ---------------------------------------------------------------------------------------------------

    def _statements(self, params: DTParams, dialect: Dialect, scope: ColumnElement[bool] | None) -> _Statements:
        """Every statement of the request, built before any runs: a bad filter never leaves a half-run query.

        Raises:
            InvalidFilter: the one of the user's mistakes that decides the answer, from the plan or from the
                conditions built here. It is raised in one place, once every condition is built.
        """
        mistakes = Mistakes()
        request = plan(params, self.columns, self._describe, mistakes)
        total = select(func.count()).select_from(self.source)
        limits = [limit for limit in (self.base_filter, scope) if limit is not None]
        total = total.where(*limits)

        filtered_rows = select(*(self._sql(column).label(column.name) for column in self.columns)).select_from(
            self.source
        )
        filtered_rows = filtered_rows.where(*limits)
        filtered_rows = filtered_rows.where(*self._conditions(request, dialect, mistakes))
        mistakes.raise_first()
        filtered = select(func.count()).select_from(filtered_rows.subquery())
        page = self._ordered(filtered_rows, request, dialect).offset(params.start).limit(params.length)
        return _Statements(total, filtered, page)

    def _sql(self, column: Column) -> ColumnElement[Any]:
        return self.source.columns[column.source]

    def _describe(self, column: Column) -> Capabilities:
        """Every SQL column can be sorted by unless it is declared not to, and searched as text (PRD-F23) unless it
        is a boolean, whose text is nothing a user reads in the cell. Every SQL column holds a whole value to match."""
        kind = self._type_of(column)
        return Capabilities(
            kind, column.orderable is not False, searched=kind != 'boolean', value_search=False, whole_string=True
        )

    def _type_of(self, column: Column) -> ColumnType:
        if column.type is not None:
            return column.type
        sql_type = _sql_type(self._sql(column))
        if isinstance(sql_type, Boolean):  # SQL Server's BIT too; a reflected MySQL TINYINT(1) is an Integer
            return 'boolean'
        if isinstance(sql_type, Integer | Numeric | Float):
            return 'number'
        if isinstance(sql_type, Date | DateTime):
            return 'date'
        return 'string'

    def _conditions(self, request: Plan, dialect: Dialect, mistakes: Mistakes) -> list[ColumnElement[bool]]:
        """The request's conditions. One that holds a mistake (a number the column cannot hold) is left out, and
        the mistake kept in ``mistakes``."""
        conditions: list[ColumnElement[bool]] = []
        filters: list[Filter | Group] = [] if request.global_search is None else [request.global_search]
        filters.extend(request.column_searches)
        if request.search_builder is not None:
            filters.append(request.search_builder)
        for node in filters:
            built = self._compile(node, dialect, mistakes)
            if built is not None:
                conditions.append(built)
        return conditions

    def _contains(self, search: Contains) -> ColumnElement[bool]:
        column = self._sql(search.column)
        pattern = f'%{_escape_like(str(search.value))}%'
        sql_type = _sql_type(column)
        if isinstance(sql_type, Date | DateTime):
            return _day_text(_utc(column) if _aware(sql_type) else column).ilike(pattern, escape='\\')
        return _text(column).ilike(pattern, escape='\\')

    def _compile(self, node: Filter | Group, dialect: Dialect, mistakes: Mistakes) -> ColumnElement[bool] | None:
        """The condition of one node of the plan; None for a condition that holds a mistake (a number outside the
        column's range, kept in ``mistakes``) and for a group left with none."""
        if isinstance(node, Group):
            built = (self._compile(item, dialect, mistakes) for item in node.items)
            parts = [part for part in built if part is not None]
            if not parts:
                return None
            return and_(*parts) if node.logic == 'AND' else or_(*parts)
        with mistakes.part():
            return self._condition(node, dialect)
        return None

    def _condition(self, node: Filter, dialect: Dialect) -> ColumnElement[bool]:
        """One condition on one column.

        Raises:
            InvalidFilter: a number of the condition is one the column cannot be compared with (``_number``).
        """
        column = self._sql(node.column)

        def number(value: Any, *, listed: bool) -> Any:
            return _number(column, value, node.column, dialect, listed=listed)

        match node:
            case Not():
                negated = not_(self._condition(node.node, dialect))
                # NULL is not equal to, does not contain and is not between anything, so it passes every negative
                # condition (PRD-F37), where SQL's own NOT would drop it. "Not empty" is the one it does not pass
                return negated if isinstance(node.node, Empty) else or_(negated, column.is_(None))
            case Contains():
                return self._contains(node)
            case Empty():
                return _empty(column, node.type, dialect)
            case Equals() if node.type == 'number':
                return column == number(node.value, listed=False)
            case Equals() if node.type == 'boolean':
                return column == _boolean(column, node.value)
            case Equals():
                # The value is folded by the database too: Python lowers letters that SQLite's lower() leaves alone
                fold = _folds(node.column, column)
                return _lower(column) == func.lower(node.value) if fold else column == node.value
            case Part():
                escaped = _escape_like(str(node.value))
                pattern = {'any': f'%{escaped}%', 'start': f'{escaped}%', 'end': f'%{escaped}'}[node.where]
                return _text(column).ilike(pattern, escape='\\')
            case Range() if node.type == 'number':
                several = node.lower is not None and node.upper is not None  # the two values of a `between`
                return _between(column, node.lower, node.upper, lambda value: number(value, listed=several))
            case Range():
                return _days(column, node.lower, node.upper)
            case OneOf():
                values: list[Any] = list(node.values)
                if node.type == 'number':
                    values = [number(value, listed=True) for value in values]
                elif node.type == 'boolean':
                    values = [_boolean(column, value) for value in values]
                return _one_of(node, column, values, dialect)
            case _:
                assert_never(node)

    def _ordered(self, stmt: Select[Any], request: Plan, dialect: Dialect) -> Select[Any]:
        """The user's order with NULL as the lowest value, then the unique key (the declared one, else the primary
        key) in the direction of the first entry: the same rows come in the same order, a tie never splits across
        pages, and a descending order is the exact reverse of the ascending one."""
        descending = bool(request.order) and request.order[0].descending
        ordered = [self._sql(item.column) for item in request.order]
        for column, item in zip(ordered, request.order, strict=True):
            stmt = stmt.order_by(self._by(column, item.descending, dialect))
        for key in self._tie_breakers:
            # A key column the user already orders by is not named again: SQL Server refuses a column named twice.
            # It is told by the column itself, since a join names its columns `<table>_<column>`, not by their keys
            if all(key is not column for column in ordered):
                stmt = stmt.order_by(self._by(key, descending, dialect))
        return stmt

    def _by(self, column: ColumnElement[Any], descending: bool, dialect: Dialect) -> ColumnElement[Any]:
        """One term of the order. Where NULL goes is written only where it must be: not on a database that orders
        NULL lowest itself, and not for a column that holds none, since on PostgreSQL an index in the default order
        does not serve an order that says where NULL goes. Only a column of a table, declared not nullable (a
        primary key's are), is known to hold none: a join or a subquery can give NULL for any column, and so can an
        expression."""
        term = column.desc() if descending else column.asc()
        never_null = isinstance(self.source, Table) and getattr(column, 'nullable', True) is False
        if never_null or dialect.name in _NULL_IS_LOWEST:
            return term
        return term.nulls_last() if descending else term.nulls_first()

    # -- running ----------------------------------------------------------------------------------------------------

    def _total_key(self, source: _AsyncSource | _SyncSource, total: Select[Any]) -> str:
        """The database (its URL without the password) and the count as this database runs it, with every bound
        value of the source, the base filter and the request's scope: two scopes that differ only in a value never
        share a total."""
        bind = _bind(source)
        compiled = total.compile(dialect=bind.dialect)
        values = sorted(compiled.params.items(), key=lambda item: item[0])
        return key_digest(f'{bind.engine.url.render_as_string(hide_password=True)} {compiled} {values!r}')

    async def _total(self, source: _AsyncSource, statements: _Statements) -> int:
        store = self.total_cache
        key = self._total_key(source, statements.total) if store is not None else ''
        if store is not None and (cached := await read_total_async(store, key)) is not None:
            return cached
        count = (await source.execute(statements.total)).scalar_one()
        if store is not None:
            await write_total_async(store, key, count)
        return count

    def _total_sync(self, source: _SyncSource, statements: _Statements) -> int:
        store = self.total_cache
        key = self._total_key(source, statements.total) if store is not None else ''
        if store is not None and (cached := read_total(store, key)) is not None:
            return cached
        count = source.execute(statements.total).scalar_one()
        if store is not None:
            write_total(store, key, count)
        return count

    def _response(self, params: DTParams, total: int, filtered: int, rows: Sequence[Mapping[Any, Any]]) -> DTResponse:
        return DTResponse(
            draw=params.draw,
            recordsTotal=total,
            recordsFiltered=filtered,
            data=[self.rows.apply(dict(row)) for row in rows],
        )


# -- SQL helpers ---


def _escape_like(value: str) -> str:
    """``value`` with LIKE's wildcards escaped by ``\\``: ``%``, ``_``, and ``[`` (a character class on SQL Server)."""
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_').replace('[', '\\[')


def _bind(source: _AsyncSource | _SyncSource) -> Any:
    """The engine or connection behind a session, or the connection itself."""
    get_bind = getattr(source, 'get_bind', None)  # a Session or an AsyncSession
    return get_bind() if get_bind else source


def _dialect(source: _AsyncSource | _SyncSource) -> Dialect:
    return _bind(source).dialect


def _key_is_not_unique(source: FromClause) -> bool:
    """Whether the source's primary key cannot be trusted to tell its rows apart (PRD-D11): SQLAlchemy reports none,
    or a table under it has none, or it does not hold every primary-key column of each table it reads. A join is
    checked side by side, since its reduced key drops a column equal to one it joins on. Every table a subquery
    reads counts, also in a ``WHERE`` or a scalar subquery, so such a subquery is refused though its own key may be
    whole: declare ``unique_key`` there. A ``UNION`` is not checked."""
    if isinstance(source, Join):
        return _key_is_not_unique(source.left) or _key_is_not_unique(source.right)
    primary_key = getattr(source, 'primary_key', None)
    tables = find_tables(source)
    if not primary_key or any(not table.primary_key for table in tables):
        return True
    return any(
        not any(column in kept.proxy_set for kept in primary_key) for table in tables for column in table.primary_key
    )


def _stored_type(column: ColumnElement[Any]) -> Any:
    """The type the column stores, seen through every ``TypeDecorator`` around it."""
    stored = column.type
    while isinstance(stored, TypeDecorator):
        stored = stored.impl_instance
    return stored


def _check_declared_types(columns: Sequence[Column], source: FromClause) -> None:
    """A declared type that does not fit the column's SQL type is the integrator's mistake (PRD-D22): a string
    column declared a number, a date or a boolean, and a date column declared a number. Every other declaration,
    and a column whose type is none of these, is left to the database."""
    for column in columns:
        declared = column.type
        stored = _stored_type(source.columns[column.source])
        if declared is None:
            continue
        if isinstance(stored, String):
            fits, mismatch = 'string', declared != 'string'
        elif isinstance(stored, Date | DateTime):
            fits, mismatch = 'date', declared == 'number'
        else:
            continue
        if mismatch:
            raise SchemaError(
                f'The column "{column.name}" is declared {declared} but its SQL type is {stored}, which the databases '
                f'compare differently or refuse: declare it {fits} to compare it as it is stored, or give the column '
                f'(or its expression, by a cast) a {declared} type.'
            )


def _sql_type(column: ColumnElement[Any]) -> Any:
    """The column's SQL type, seen through a ``TypeDecorator`` to the type it stores."""
    return getattr(column.type, 'impl_instance', column.type)


def _is_text(column: ColumnElement[Any]) -> bool:
    """Whether the column holds text. A ``TypeDecorator`` without ``python_type`` is not assumed to."""
    try:
        return issubclass(column.type.python_type, str)
    except NotImplementedError:
        return False


def _number(
    column: ColumnElement[Any], value: int | Decimal, declared: Column, dialect: Dialect, *, listed: bool
) -> Any:
    """``value`` as the column compares it, by the type the column stores (PRD-F25).

    An integer column has a range (as SQL Server's and PostgreSQL's integers have it): 16 bits for a
    ``SmallInteger``, 64 for a ``BigInteger``, 32 for any other ``Integer``, from zero up where the type says it is
    unsigned (MySQL's). On SQL Server a ``Numeric`` column, and an integer column given a number with a fraction (not
    through a ``TypeDecorator`` that converts it), takes a number of at most 38 digits in all, before and after the
    decimal point (a fraction's trailing zeros not counted), whatever the column declares: its driver refuses a
    longer one. A number outside either is the user's mistake. No other database bounds a ``Numeric`` column here: a
    filter such as ``< 1000`` on a ``Numeric(5, 2)`` is a fair question, and they answer it.

    A number goes as the column compares. To an exact ``Numeric`` column it goes as a ``Decimal`` with every digit
    written, when it has a point or an exponent or lies outside the signed 64-bit range that a driver sends a whole
    number in. To an integer column a number with a fraction goes as a ``Decimal`` too (the nearest float through a
    ``TypeDecorator`` that converts it), and a whole number written with a point (``8.0``) as that whole number. To
    any other column (a ``Float``, an expression without a type) it goes as the nearest float, which holds every
    number a request may carry (at most 255 characters, below 1.8e308).

    The reason names ``declared`` and the bounds, which are the column's, not the user's; ``listed`` says the value
    is one of several (a list, or the two of a ``between``), as for ``parse_number``."""
    sql_type = _sql_type(column)
    needed = (
        f'Each value in the filter on "{declared.name}" must be a number'
        if listed
        else f'The filter on "{declared.name}" needs a number'
    )
    if isinstance(sql_type, Integer):
        if _fraction(value):  # no integer equals it: an exact decimal compares as it is, a converting type's as a float
            if isinstance(column.type, TypeDecorator):
                # Bound as a float of its own: as the decorator's type, psycopg 3 and asyncpg would cast it to the
                # impl's INTEGER, which rounds 7.5 to 8
                return literal(float(value), Float())
            return _decimal(column, value, needed, declared, dialect)
        number = int(value)  # 8.0 is the whole number 8
        bits = 16 if isinstance(sql_type, SmallInteger) else 64 if isinstance(sql_type, BigInteger) else 32
        low, high = -(2 ** (bits - 1)), 2 ** (bits - 1) - 1
        if getattr(sql_type, 'unsigned', False):
            low, high = 0, 2**bits - 1
        if not low <= number <= high:
            raise mistake('datatables_number_out_of_range', f'{needed} between {low} and {high}.', declared)
        return number
    if not (isinstance(sql_type, Numeric) and not isinstance(sql_type, Float)):  # a Float is a Numeric to SQLAlchemy
        return float(value) if isinstance(value, Decimal) or not -(2**63) <= value < 2**63 else value
    return _decimal(column, value, needed, declared, dialect)


def _fraction(value: int | Decimal) -> bool:
    """Whether ``value`` has a fraction, which no whole number equals."""
    return isinstance(value, Decimal) and value != value.to_integral_value()


def _decimal(
    column: ColumnElement[Any], value: int | Decimal, needed: str, declared: Column, dialect: Dialect
) -> int | Decimal | ColumnElement[Any]:
    """``value`` for a column that compares exactly: a whole number within 64 bits as an ``int`` (the driver's own
    integer), any other as a ``Decimal`` with every digit written. SQL Server refuses a number of more than 38 digits
    in all, which is the user's mistake here (``_number``)."""
    if isinstance(value, Decimal):
        value = without_trailing_zeros(value)
    if dialect.name == 'mssql' and _digits(value) > _MSSQL_DECIMAL_DIGITS:
        raise mistake(
            'datatables_number_out_of_range',
            f'{needed} with at most {_MSSQL_DECIMAL_DIGITS} digits in all, before and after the decimal point; '
            'shorten the number.',
            declared,
        )
    if isinstance(value, int) and -(2**63) <= value < 2**63:
        return value
    if isinstance(column.type, TypeDecorator):  # it converts what it is given, so the column's own type must see it
        return Decimal(value)
    # Bound as a scale-free NUMERIC: asyncpg would cast a Decimal compared with the column to the column's own
    # NUMERIC(p, s), and a number with more digits than that overflowed it
    return literal(Decimal(value), Numeric())


def _digits(value: int | Decimal) -> int:
    """The digits of ``value`` before and after the decimal point, as SQL Server's decimal counts them (at most 38),
    the zeros at the end of a whole number included."""
    if isinstance(value, int):
        return len(str(abs(value))) if value else 0
    _, digits, point = value.as_tuple()
    exponent = int(point)  # a finite number's
    return max(len(digits) + exponent, 0) + max(-exponent, 0)


def _boolean(column: ColumnElement[Any], value: bool) -> bool | int:
    """``value`` as the column holds it: 1 or 0 for a number column declared boolean (a MySQL ``TINYINT(1)``), which
    PostgreSQL does not compare with a boolean."""
    return value if isinstance(_sql_type(column), Boolean) else int(value)


def _empty(column: ColumnElement[Any], declared: ColumnType, dialect: Dialect) -> ColumnElement[bool]:
    """ "Empty" (PRD-F26): no value, and the empty string too where the column stores text, whatever type it is
    declared as. What it stores is its SQL type on this database, seen through every ``TypeDecorator`` around it:
    one may store another type on another database, as SQLAlchemy's GUID recipe does (a ``CHAR`` here, a ``UUID``
    on PostgreSQL). A string type holds text, and an ``Enum`` only when ``''`` is one of its values, since
    PostgreSQL refuses to compare a native enum with any other text. A column of another type (a ``Uuid``, a
    number, a date) is never compared with text. Only for an expression without a SQL type does the declaration
    decide.

    The empty string is compared with what is stored, not sent through a ``TypeDecorator``, whose conversion is
    written for the values of its column and may refuse ``''``."""
    stored: Any = column.type
    while isinstance(stored, TypeDecorator):
        stored = stored.load_dialect_impl(dialect)
    if isinstance(stored, Enum):
        holds_text = '' in stored.enums
    elif isinstance(stored, NullType):
        holds_text = declared == 'string'
    else:
        holds_text = isinstance(stored, String)
    if not holds_text:
        return column.is_(None)
    text = type_coerce(column, stored) if isinstance(column.type, TypeDecorator) else column
    return or_(column.is_(None), text == '')


def _between(
    column: ColumnElement[Any], lower: Bound | None, upper: Bound | None, value: Callable[[Any], Any]
) -> ColumnElement[bool]:
    """The column lies between the bounds, each included or not as it says; one of them may be open. ``value``
    gives a bound's value as the column compares it."""
    parts: list[ColumnElement[bool]] = []
    if lower is not None:
        parts.append(column >= value(lower.value) if lower.included else column > value(lower.value))
    if upper is not None:
        parts.append(column <= value(upper.value) if upper.included else column < value(upper.value))
    return and_(*parts) if len(parts) == 2 else parts[0]


def _folds(declared: Column, column: ColumnElement[Any]) -> bool:
    """Whether an exact match on a string column ignores case: its own SQL type is a string type (``String`` and
    its family, an ``Enum`` among them), it is not declared ``fold_case=False``, and no hook transforms what is
    searched for. Otherwise the comparison is sent plain, and the column's collation decides (PRD-D21, PRD-F9).

    A column with a type of its own (a ``TypeDecorator``, SQLAlchemy's ``Uuid``) is never folded: such a type
    converts values and compares them in its own way, which a fold would bypass. Neither is an expression declared
    without a type, which the library cannot know to be text, nor a column of another type declared a string."""
    return declared.fold_case and declared.search_value is None and isinstance(column.type, String)


def _text(column: ColumnElement[Any]) -> ColumnElement[Any]:
    """The column as text, for a search or a fold: itself when it holds text, else cast. An ``Enum`` is cast too:
    PostgreSQL stores one as a type of its own, which has no text functions. A float column (``Float``, ``REAL``,
    ``DOUBLE``, also under a ``TypeDecorator``; a ``Numeric`` is none) is written by ``_number_text``, so that no
    database writes it with fewer digits than the table shows (PRD-F23)."""
    if _is_text(column) and not isinstance(column.type, Enum):
        return column
    if isinstance(_sql_type(column), Float):
        return _number_text(column)
    return sql_cast(column, String)


def _lower(column: ColumnElement[Any]) -> ColumnElement[Any]:
    """The column in lower case, as the database folds it, so that a case-sensitive collation matches too."""
    return func.lower(_text(column))


def _days(column: ColumnElement[Any], lower: Bound | None, upper: Bound | None) -> ColumnElement[bool]:
    """The column's day lies between two days, whether it holds dates or datetimes: a datetime is compared with the
    midnight that begins the day, in UTC where the column carries a zone."""
    sql_type = _sql_type(column)

    def at(day: date) -> date | datetime:
        if not isinstance(sql_type, DateTime):
            return day
        midnight = datetime(day.year, day.month, day.day)
        return midnight.replace(tzinfo=UTC) if _aware(sql_type) else midnight

    return _between(column, lower, upper, at)


def _one_of(node: OneOf, column: ColumnElement[Any], values: list[Any], dialect: Dialect) -> ColumnElement[bool]:
    """A list of exact values (``values``: the node's, as the column compares them), and the rows without a value
    where the list holds an empty entry (PRD-F17, PRD-F20), which is never one of the values sent. On a date
    column the row's day is one of the list's days. A column whose SQL type is a ``Date`` holds a day and nothing
    else, so its days are one IN list. A column of any other type, a datetime or one only declared a date, may hold
    a time, so each entry gets the day's two bounds, as ``=`` gives it: two parameters each, which PRD-F38's
    bounds keep within every database's limit (PRD-C17)."""
    parts: list[ColumnElement[bool]] = []
    if node.type == 'date' and not isinstance(_sql_type(column), Date):
        parts += [_days(column, Bound(day, True), Bound(next_day(day), False)) for day in node.values]
    elif node.type == 'number' and values:
        # Each number is typed as `=` types it, by the column's own rules for a value of its Python type.
        # SQLAlchemy would type a whole list by its first entry: `1, 18446744073709551616` made both an INTEGER on
        # PostgreSQL, and SQLite binds no Decimal. A number with a fraction equals no whole number, and a cast to
        # INTEGER would round it to one, so on an integer column it is left out, as `=` leaves it to match nothing
        whole = isinstance(_sql_type(column), Integer)
        held = [value for raw, value in zip(node.values, values, strict=True) if not (whole and _fraction(raw))]
        typed = [
            value
            if isinstance(value, ClauseElement)
            else literal(value, column.type.coerce_compared_value(operators.eq, value))
            for value in held
        ]
        parts.append(column.in_(typed) if held else false())
    elif values:
        if node.type == 'string' and _folds(node.column, column):
            parts.append(_lower(column).in_([func.lower(value) for value in values]))
        else:
            parts.append(column.in_(values))
    if node.empty:
        parts.append(_empty(column, node.type, dialect))
    return or_(*parts)


def _aware(sql_type: Any) -> bool:
    """Whether a column of this type carries a time zone (PRD-F21): a ``DateTime(timezone=True)``, or SQL Server's
    own ``DATETIMEOFFSET``, which a reflected table has and which does not set that flag."""
    return isinstance(sql_type, DATETIMEOFFSET) or (
        isinstance(sql_type, DateTime) and bool(getattr(sql_type, 'timezone', False))
    )


class _utc(FunctionElement[datetime]):  # noqa: N801
    """A timezone-aware column as its UTC time, where the database would otherwise give its day in another zone
    (PRD-F42): PostgreSQL in the session's, SQL Server in the offset the value was stored with. Elsewhere it is the
    column itself: SQLite and a MySQL ``DATETIME`` store no offset, and a MySQL ``TIMESTAMP`` is read through the
    session's ``time_zone``, which the app sets (PRD-F21)."""

    type = DateTime()
    inherit_cache = True


@compiles(_utc)
def _utc_default(element: _utc, compiler: SQLCompiler, **kw: Any) -> str:
    return compiler.process(element.clauses, **kw)


# Not measured: coverage is taken on the SQLite leg and this runs only on PostgreSQL, where
# test_the_global_search_finds_a_timezone_aware_column_by_its_utc_day runs it
@compiles(_utc, 'postgresql')
def _utc_postgresql(element: _utc, compiler: SQLCompiler, **kw: Any) -> str:  # pragma: no cover
    return f"({compiler.process(element.clauses, **kw)} AT TIME ZONE 'UTC')"


# Not measured: coverage is taken on the SQLite leg and this runs only on SQL Server, where
# test_a_column_search_finds_a_value_stored_at_another_offset_by_its_utc_day runs it
@compiles(_utc, 'mssql')
def _utc_mssql(element: _utc, compiler: SQLCompiler, **kw: Any) -> str:  # pragma: no cover
    return f"SWITCHOFFSET({compiler.process(element.clauses, **kw)}, '+00:00')"


class _day_text(FunctionElement[str]):  # noqa: N801
    """A date or datetime column as ``YYYY-MM-DD`` text, so a free-text search can match a day."""

    type = String()
    inherit_cache = True


@compiles(_day_text)
def _day_text_default(element: _day_text, compiler: SQLCompiler, **kw: Any) -> str:
    return f'CAST({compiler.process(element.clauses, **kw)} AS VARCHAR(10))'


@compiles(_day_text, 'sqlite')
def _day_text_sqlite(element: _day_text, compiler: SQLCompiler, **kw: Any) -> str:
    return f"strftime('%Y-%m-%d', {compiler.process(element.clauses, **kw)})"


@compiles(_day_text, 'mssql')
def _day_text_mssql(element: _day_text, compiler: SQLCompiler, **kw: Any) -> str:
    return f'CONVERT(VARCHAR(10), {compiler.process(element.clauses, **kw)}, 23)'


@compiles(_day_text, 'postgresql')
def _day_text_postgresql(element: _day_text, compiler: SQLCompiler, **kw: Any) -> str:
    return f"to_char({compiler.process(element.clauses, **kw)}, 'YYYY-MM-DD')"


@compiles(_day_text, 'mysql')
@compiles(_day_text, 'mariadb')
def _day_text_mysql(element: _day_text, compiler: SQLCompiler, **kw: Any) -> str:
    return f"DATE_FORMAT({compiler.process(element.clauses, **kw)}, '%%Y-%%m-%%d')"


class _number_text(FunctionElement[str]):  # noqa: N801
    """A float column as text, so a free-text search can match the number as the table shows it (PRD-F23). The
    database's own cast, except on SQL Server, whose ``CAST`` keeps six digits (``1.23457e+006`` for 1234567) and
    would lose the row to a search for the number shown. Not on a ``Numeric``, which every database writes whole."""

    type = String()
    inherit_cache = True


@compiles(_number_text)
def _number_text_default(element: _number_text, compiler: SQLCompiler, **kw: Any) -> str:
    return compiler.process(sql_cast(element.clauses.clauses[0], String), **kw)


# Not measured: coverage is taken on the SQLite leg and this runs only on SQL Server, where
# test_a_number_as_the_table_shows_it_finds_its_row runs it. FORMAT without a culture follows the session's language
# (Microsoft's documentation, not run here); with 'de-DE' it writes a comma, and it refuses 'invariant' (measured,
# docs/decisions/006)
@compiles(_number_text, 'mssql')
def _number_text_mssql(element: _number_text, compiler: SQLCompiler, **kw: Any) -> str:  # pragma: no cover
    return f"FORMAT({compiler.process(element.clauses, **kw)}, 'R', 'en-US')"
