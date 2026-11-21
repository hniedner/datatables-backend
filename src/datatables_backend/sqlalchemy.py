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
import json
import logging
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import Enum
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import Numeric
from sqlalchemy import SmallInteger
from sqlalchemy import String
from sqlalchemy import Unicode
from sqlalchemy import UnicodeText
from sqlalchemy import and_
from sqlalchemy import bindparam
from sqlalchemy import cast as sql_cast
from sqlalchemy import func
from sqlalchemy import not_
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Dialect
from sqlalchemy.exc import CompileError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.sql import FromClause
from sqlalchemy.sql import Select
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.compiler import StrSQLCompiler
from sqlalchemy.sql.elements import BindParameter
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.expression import FunctionElement
from sqlalchemy.sql.visitors import InternalTraversal

from ._plan import AnyOf
from ._plan import Contains
from ._plan import Criterion
from ._plan import DaySpan
from ._plan import Group
from ._plan import Mistakes
from ._plan import Plan
from ._plan import mistake
from ._plan import next_day
from ._plan import parse_number
from ._plan import plan
from ._plan import refused
from .cache import TotalCache
from .columns import Column
from .columns import ColumnType
from .protocol import DTParams
from .protocol import DTResponse
from .protocol import InvalidFilter
from .protocol import SchemaError
from .rows import RowHooks

if TYPE_CHECKING:  # importing them needs greenlet, which a sync-only install may not have
    from sqlalchemy.ext.asyncio import AsyncConnection
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger('datatables_backend')

type AsyncSource = AsyncSession | AsyncConnection
type SyncSource = Session | Connection

#: SQL Server refuses a statement with more than 2,100 bound parameters; this leaves room for the driver and paging
MSSQL_PARAMETER_BUDGET = 2000


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
        base_filter: The rows the table shows at all; it applies to ``recordsTotal`` too.
        rows: Hooks that shape each row and add ``DT_RowId`` / ``DT_RowData``.
        total_cache: Caches ``recordsTotal`` (one ``COUNT(*)`` per TTL instead of per draw).
    """

    def __init__(
        self,
        source: FromClause,
        columns: Sequence[Column],
        *,
        base_filter: ColumnElement[bool] | None = None,
        rows: RowHooks | None = None,
        total_cache: TotalCache | None = None,
    ) -> None:
        self.source = source
        self.columns = tuple(columns)
        self.base_filter = base_filter
        self.rows = rows or RowHooks()
        self.total_cache = total_cache
        missing = [column.source for column in self.columns if column.source not in source.columns]
        if missing:
            raise SchemaError(f'{source} has no column {", ".join(missing)}')

    async def fetch(self, params: DTParams, source: AsyncSource) -> DTResponse:
        """Answer one request on an ``AsyncSession`` or ``AsyncConnection``.

        A mistake the user can fix is answered with its ``error_code`` (see ``DTResponse``) and logged once, before
        any statement runs; any other failure raises.
        """
        try:
            statements = self._statements(params, _dialect(source))
        except InvalidFilter as found:
            return refused(params, found)
        total = await self._total(source, statements)
        filtered = (await source.execute(statements.filtered)).scalar_one()
        result = await source.execute(statements.page)
        return self._response(params, total, filtered, result.mappings().all())

    def fetch_sync(self, params: DTParams, source: SyncSource) -> DTResponse:
        """``fetch`` for a ``Session`` or ``Connection``."""
        try:
            statements = self._statements(params, _dialect(source))
        except InvalidFilter as found:
            return refused(params, found)
        total = self._total_sync(source, statements)
        filtered = source.execute(statements.filtered).scalar_one()
        rows = source.execute(statements.page).mappings().all()
        return self._response(params, total, filtered, rows)

    # -- building ---------------------------------------------------------------------------------------------------

    def _statements(self, params: DTParams, dialect: Dialect) -> _Statements:
        """Every statement of the request, built before any runs: a bad filter never leaves a half-run query.

        Raises:
            InvalidFilter: the one of the user's mistakes that decides the answer, from the plan or from the
                conditions built here. It is raised in one place, once every condition is built.
        """
        mistakes = Mistakes()
        request = plan(params, self.columns, self._type_of, mistakes)
        total = select(func.count()).select_from(self.source)
        if self.base_filter is not None:
            total = total.where(self.base_filter)

        def build(in_list: _InList) -> Select[Any]:
            stmt = select(*(self._sql(column).label(column.name) for column in self.columns)).select_from(self.source)
            if self.base_filter is not None:
                stmt = stmt.where(self.base_filter)
            return stmt.where(*self._conditions(request, in_list, mistakes))

        filtered_rows = build(_bound_in)
        mistakes.raise_first()  # from here on the request holds no mistake, so a second build finds none either
        if dialect.name == 'mssql' and _parameter_count(filtered_rows) > MSSQL_PARAMETER_BUDGET:
            filtered_rows = build(_json_in)
        filtered = select(func.count()).select_from(filtered_rows.subquery())
        page = self._ordered(filtered_rows, request).offset(params.start).limit(params.length)
        return _Statements(total, filtered, page)

    def _sql(self, column: Column) -> ColumnElement[Any]:
        return self.source.columns[column.source]

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

    def _conditions(self, request: Plan, in_list: _InList, mistakes: Mistakes) -> list[ColumnElement[bool]]:
        """The request's conditions. One that holds a mistake (a number the column cannot read or hold) is left
        out, and the mistake kept in ``mistakes``."""
        conditions: list[ColumnElement[bool]] = []
        if request.global_search:
            conditions.append(or_(*(self._contains(search) for search in request.global_search)))
        for search in request.column_searches:
            if isinstance(search, AnyOf):
                column = self._sql(search.column)
                with mistakes.part():
                    if search.type == 'boolean':
                        values = [_boolean(column, value) for value in search.values]
                    elif search.type == 'number':
                        numbers = [parse_number(value, search.column, listed=True) for value in search.values]
                        values = [_number(column, number, search.column, listed=True) for number in numbers]
                    else:
                        values = list(search.values)
                    fold = search.type == 'string' and _folds(search.column, column)
                    conditions.append(in_list(column, values, fold))
            else:
                conditions.append(self._contains(search))
        if request.search_builder is not None:
            built = self._node(request.search_builder, mistakes)
            if built is not None:
                conditions.append(built)
        return conditions

    def _contains(self, search: Contains) -> ColumnElement[bool]:
        column = self._sql(search.column)
        pattern = f'%{escape_like(str(search.value))}%'
        if isinstance(_sql_type(column), Date | DateTime):
            return _day_text(column).ilike(pattern, escape='\\')
        return _text(column).ilike(pattern, escape='\\')

    def _node(self, node: Criterion | Group, mistakes: Mistakes) -> ColumnElement[bool] | None:
        """The condition of a criterion or group; None for a criterion that holds a mistake (a number outside the
        column's range, kept in ``mistakes``) and for a group left with none."""
        if isinstance(node, Group):
            parts = [part for item in node.items if (part := self._node(item, mistakes)) is not None]
            if not parts:
                return None
            return and_(*parts) if node.logic == 'AND' else or_(*parts)
        column = self._sql(node.column)
        if node.type == 'number':
            with mistakes.part():
                several = node.condition in ('between', '!between')
                values = [_number(column, value, node.column, listed=several) for value in node.values]
                return _compare(node.condition, column, values, column)
            return None
        if node.type == 'date':
            return _date_criterion(node, column)
        if node.type == 'boolean':
            return _boolean_criterion(node, column)
        return _string_criterion(node, column, fold=_folds(node.column, column))

    def _ordered(self, stmt: Select[Any], request: Plan) -> Select[Any]:
        """The user's order, then the primary key, so the same rows come in the same order and a tie never splits
        across pages."""
        ordered: set[str] = set()
        for item in request.order:
            column = self._sql(item.column)
            ordered.add(item.column.source)
            stmt = stmt.order_by(column.desc() if item.descending else column.asc())
        for key in _primary_key(self.source):
            if key.key not in ordered:
                stmt = stmt.order_by(key.asc())
        return stmt

    # -- running ----------------------------------------------------------------------------------------------------

    def _total_key(self, source: AsyncSource | SyncSource) -> str:
        """The database (its URL without the password), the source and the base filter with its values."""
        url = _bind(source).engine.url.render_as_string(hide_password=True)
        key = f'{url} {self.source}'
        if self.base_filter is not None:
            compiled = self.base_filter.compile()
            key += f' WHERE {compiled} {sorted(compiled.params.items(), key=lambda item: item[0])}'
        return key

    async def _total(self, source: AsyncSource, statements: _Statements) -> int:
        key = self._total_key(source) if self.total_cache else ''
        if self.total_cache and (cached := self.total_cache.get(key)) is not None:
            return cached
        count = (await source.execute(statements.total)).scalar_one()
        if self.total_cache:
            self.total_cache.put(key, count)
        return count

    def _total_sync(self, source: SyncSource, statements: _Statements) -> int:
        key = self._total_key(source) if self.total_cache else ''
        if self.total_cache and (cached := self.total_cache.get(key)) is not None:
            return cached
        count = source.execute(statements.total).scalar_one()
        if self.total_cache:
            self.total_cache.put(key, count)
        return count

    def _response(self, params: DTParams, total: int, filtered: int, rows: Sequence[Mapping[Any, Any]]) -> DTResponse:
        return DTResponse(
            draw=params.draw,
            recordsTotal=total,
            recordsFiltered=filtered,
            data=[self.rows.apply(dict(row)) for row in rows],
        )


# -- SQL helpers ---


def escape_like(value: str) -> str:
    """``value`` with LIKE's wildcards escaped by ``\\``: ``%``, ``_``, and ``[`` (a character class on SQL Server)."""
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_').replace('[', '\\[')


def _bind(source: AsyncSource | SyncSource) -> Any:
    """The engine or connection behind a session, or the connection itself."""
    get_bind = getattr(source, 'get_bind', None)  # a Session or an AsyncSession
    return get_bind() if get_bind else source


def _dialect(source: AsyncSource | SyncSource) -> Dialect:
    return _bind(source).dialect


def _primary_key(source: FromClause) -> list[ColumnElement[Any]]:
    """The primary key's columns, or the first column of a source that declares none."""
    key = list(getattr(source, 'primary_key', None) or [])
    return key or [next(iter(source.columns))]


def _sql_type(column: ColumnElement[Any]) -> Any:
    """The column's SQL type, seen through a ``TypeDecorator`` to the type it stores."""
    return getattr(column.type, 'impl_instance', column.type)


def _is_text(column: ColumnElement[Any]) -> bool:
    """Whether the column holds text. A ``TypeDecorator`` without ``python_type`` is not assumed to."""
    try:
        return issubclass(column.type.python_type, str)
    except NotImplementedError:
        return False


def _number(column: ColumnElement[Any], value: Any, declared: Column, *, listed: bool) -> Any:
    """``value`` checked against the column's integer range (as SQL Server's and PostgreSQL's integers have it): 16
    bits for a ``SmallInteger``, 64 for a ``BigInteger``, 32 for any other ``Integer``. A column of another type has
    no range here. The reason names ``declared`` and the bounds, which are the column's, not the user's; ``listed``
    says the value is one of several (a list, or the two of a ``between``), as for ``parse_number``."""
    sql_type = _sql_type(column)
    if not isinstance(sql_type, Integer) or not isinstance(value, int | float):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            return value
        value = int(value)
    bits = 16 if isinstance(sql_type, SmallInteger) else 64 if isinstance(sql_type, BigInteger) else 32
    low, high = -(2 ** (bits - 1)), 2 ** (bits - 1) - 1
    if not low <= value <= high:
        needed = (
            f'Each value in the filter on "{declared.name}" must be a number'
            if listed
            else f'The filter on "{declared.name}" needs a number'
        )
        raise mistake('datatables_number_out_of_range', f'{needed} between {low} and {high}.', declared)
    return value


def _boolean(column: ColumnElement[Any], value: bool) -> bool | int:
    """``value`` as the column holds it: 1 or 0 for a number column declared boolean (a MySQL ``TINYINT(1)``), which
    PostgreSQL does not compare with a boolean."""
    return value if isinstance(_sql_type(column), Boolean) else int(value)


def _boolean_criterion(node: Criterion, column: ColumnElement[Any]) -> ColumnElement[bool]:
    match node.condition:
        case 'null':
            return column.is_(None)
        case '!null':
            return column.is_not(None)
        case '=':
            return column == _boolean(column, node.values[0])
        case '!=':  # NULL is not the value either
            return or_(column != _boolean(column, node.values[0]), column.is_(None))
        case _:  # pragma: no cover - unreachable: the plan admits no other condition for a boolean
            raise AssertionError(node.condition)


def _empty(column: ColumnElement[Any]) -> ColumnElement[bool]:
    """ "Empty": no value, and on a text column also the empty string. A number or date is never compared with text."""
    if _is_text(column):
        return or_(column.is_(None), column == '')
    return column.is_(None)


def _compare(
    condition: str, column: ColumnElement[Any], values: list[Any], empty_of: ColumnElement[Any]
) -> ColumnElement[bool]:
    """The comparison conditions shared by numbers and the day bounds of dates."""
    match condition:
        case '=':
            return column == values[0]
        case '!=':
            return column != values[0]
        case '<':
            return column < values[0]
        case '<=':
            return column <= values[0]
        case '>':
            return column > values[0]
        case '>=':
            return column >= values[0]
        case 'between':
            return and_(column >= values[0], column <= values[1])
        case '!between':
            return not_(and_(column >= values[0], column <= values[1]))
        case 'null':
            return _empty(empty_of)
        case '!null':
            return not_(_empty(empty_of))
        case _:  # the plan admits no other condition for a number
            raise AssertionError(condition)


def _folds(declared: Column, column: ColumnElement[Any]) -> bool:
    """Whether an exact match on a string column ignores case: its own SQL type is a string type (``String`` and
    its family, an ``Enum`` among them), it is not declared ``fold_case=False``, and no hook transforms what is
    searched for. Otherwise the comparison is sent plain, and the column's collation decides (PRD-D21, PRD-F9).

    A column with a type of its own (a ``TypeDecorator``, SQLAlchemy's ``Uuid``) or of a type the library cannot
    tell (an expression declared without one) is never folded: such a type converts values and compares them in
    its own way, which a fold would bypass."""
    return declared.fold_case and declared.search_value is None and isinstance(column.type, String)


def _text(column: ColumnElement[Any]) -> ColumnElement[Any]:
    """The column as text, for a search or a fold: itself when it holds text, else cast. An ``Enum`` is cast too:
    PostgreSQL stores one as a type of its own, which has no text functions."""
    if _is_text(column) and not isinstance(column.type, Enum):
        return column
    return sql_cast(column, String)


def _lower(column: ColumnElement[Any]) -> ColumnElement[Any]:
    """The column in lower case, as the database folds it, so that a case-sensitive collation matches too."""
    return func.lower(_text(column))


def _string_criterion(node: Criterion, column: ColumnElement[Any], *, fold: bool) -> ColumnElement[bool]:
    if node.condition in ('null', '!null'):
        return _empty(column) if node.condition == 'null' else not_(_empty(column))
    value = node.values[0]
    if node.condition in ('=', '!='):
        # The value is folded by the database too: Python lowers letters that SQLite's lower() leaves alone
        left, right = (_lower(column), func.lower(value)) if fold else (column, value)
        return left == right if node.condition == '=' else left != right
    escaped = escape_like(str(value))
    pattern = {
        'contains': f'%{escaped}%',
        'starts': f'{escaped}%',
        'ends': f'%{escaped}',
    }[node.condition.lstrip('!')]
    match = _text(column).ilike(pattern, escape='\\')
    return not_(match) if node.condition.startswith('!') else match


def _date_criterion(node: Criterion, column: ColumnElement[Any]) -> ColumnElement[bool]:
    """Dates compare by day, whether the column holds dates or datetimes; a negated condition leaves out NULL."""
    sql_type = _sql_type(column)
    with_time = isinstance(sql_type, DateTime)
    aware = with_time and bool(getattr(sql_type, 'timezone', False))

    def at(day: date) -> date | datetime:
        if not with_time:
            return day
        midnight = datetime(day.year, day.month, day.day)
        return midnight.replace(tzinfo=UTC) if aware else midnight

    def within(start: date, end: date) -> ColumnElement[bool]:
        return and_(column >= at(start), column < at(end))

    match node.condition:
        case 'null':
            return column.is_(None)
        case '!null':
            return column.is_not(None)
        case 'contains' | '!contains':
            span = cast(DaySpan, node.values[0])
            inside = within(span.start, span.end)
            return inside if node.condition == 'contains' else not_(inside)
        case '=' | '!=':
            inside = within(node.values[0], next_day(node.values[0]))
            return inside if node.condition == '=' else not_(inside)
        case '<':
            return column < at(node.values[0])
        case '<=':
            return column < at(next_day(node.values[0]))
        case '>':
            return column >= at(next_day(node.values[0]))
        case '>=':
            return column >= at(node.values[0])
        case 'between' | '!between':
            inside = within(node.values[0], next_day(node.values[1]))
            return inside if node.condition == 'between' else not_(inside)
        case _:  # the plan admits no other condition for a date
            raise AssertionError(node.condition)


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


# -- IN lists within SQL Server's parameter limit ---------------------------------------------------------------------

#: An IN list over a column and its values; the flag says whether both sides are compared in lower case.
type _InList = Callable[[ColumnElement[Any], Sequence[Any], bool], ColumnElement[bool]]


def _bound_in(column: ColumnElement[Any], values: Sequence[Any], fold: bool) -> ColumnElement[bool]:
    if fold:
        return _lower(column).in_([func.lower(value) for value in values])
    return column.in_(values)


# Not measured: it runs only on SQL Server; the sqlserver leg's long-list tests run it
def _json_in(column: ColumnElement[Any], values: Sequence[Any], fold: bool) -> ColumnElement[bool]:  # pragma: no cover
    return _JsonValuesIn(column, bindparam(None, json.dumps(list(values)), type_=String()), fold)


def _parameter_count(stmt: Select[Any]) -> int:
    from sqlalchemy.engine.default import DefaultDialect  # noqa: PLC0415

    compiled = cast(
        SQLCompiler,
        stmt.compile(dialect=DefaultDialect(paramstyle='qmark'), compile_kwargs={'render_postcompile': True}),
    )
    return len(compiled.positiontup or ())


class _JsonValuesIn(ColumnElement[bool]):
    """``column IN (<the values of one bound JSON array>)``, expanded by the database (``OPENJSON``, ``json_each``).
    With ``fold``, SQL Server compares the column and each value in lower case."""

    __visit_name__ = 'json_values_in'
    inherit_cache = True
    _traverse_internals = [  # noqa: RUF012
        ('column', InternalTraversal.dp_clauseelement),
        ('parameter', InternalTraversal.dp_clauseelement),
        ('fold', InternalTraversal.dp_boolean),
    ]
    type = Boolean()
    _is_implicitly_boolean = True

    # Not measured: it runs only on SQL Server; the sqlserver leg's long-list tests run it
    def __init__(  # pragma: no cover
        self, column: ColumnElement[Any], parameter: BindParameter[str], fold: bool
    ) -> None:
        self.column = column
        self.parameter = parameter
        self.fold = fold


@compiles(_JsonValuesIn)
def _compile_json_in(element: _JsonValuesIn, compiler: SQLCompiler, **kw: Any) -> str:
    column, parameter = compiler.process(element.column, **kw), compiler.process(element.parameter, **kw)
    if isinstance(compiler, StrSQLCompiler):  # str(statement): a placeholder, never the values
        return f'{column} IN (<values of the JSON array {parameter}>)'
    raise CompileError(f'A filter on this many values is not supported on {compiler.dialect.name}')


@compiles(_JsonValuesIn, 'sqlite')
def _compile_json_in_sqlite(element: _JsonValuesIn, compiler: SQLCompiler, **kw: Any) -> str:
    value = 'CAST(value AS TEXT)' if isinstance(element.column.type, String) else 'value'
    column, parameter = compiler.process(element.column, **kw), compiler.process(element.parameter, **kw)
    return f'{column} IN (SELECT {value} FROM json_each({parameter}))'  # noqa: S608


# Not measured: it runs only on SQL Server; the sqlserver leg's long-list tests run it
@compiles(_JsonValuesIn, 'mssql')
def _compile_json_in_mssql(element: _JsonValuesIn, compiler: SQLCompiler, **kw: Any) -> str:  # pragma: no cover
    column_type = _sql_type(element.column)  # the type a TypeDecorator wraps: the values are cast as it stores them
    if isinstance(column_type, String):
        # No length, so a longer value is not cut down to one that matches (a bare CHAR is 30 characters), and no
        # COLLATE, which a cast does not take: the column's own collation still compares
        cast_as = 'NVARCHAR(max)' if isinstance(column_type, Unicode | UnicodeText) else 'VARCHAR(max)'
    else:
        cast_as = compiler.dialect.type_compiler_instance.process(column_type)
    # TRY_CAST: a value that does not convert matches nothing, as a mismatched bound parameter would
    value = f'TRY_CAST(value AS {cast_as})'
    target = element.column
    if element.fold:
        target, value = _lower(target), f'lower({value})'
    column, parameter = compiler.process(target, **kw), compiler.process(element.parameter, **kw)
    return f'{column} IN (SELECT {value} FROM OPENJSON(CAST({parameter} AS NVARCHAR(max))))'  # noqa: S608
