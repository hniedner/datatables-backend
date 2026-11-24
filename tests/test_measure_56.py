# -- TEMPORARY measurement for #56 (throwaway branch, draft PR closed unmerged; never merged) ----------------------
# What each SQL driver does with a number written with a point or an exponent, bound to a `Numeric(20, 2)` and a
# `Float(53)` column: as a float (main today), as a Decimal typed by the column (`column == Decimal`, which asyncpg
# casts to the column's NUMERIC(20, 2)), and as a Decimal typed by a scale-free `Numeric()`. Then the same texts
# through the library as main answers them. It records and never fails; the table goes to the job summary.
# It owns `dtb_test_stamps`, which conftest's `database` fixture drops before each test.
import asyncio as _m56_asyncio
from collections.abc import Callable as _M56Callable
from decimal import Decimal as _M56Decimal
import os as _m56_os
import sys as _m56_sys
from typing import Any as _M56Any

import sqlalchemy as _m56_sa
from sqlalchemy.ext.asyncio import AsyncSession as _M56AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as _m56_async_engine
from sqlalchemy.pool import NullPool as _M56NullPool

from datatables_backend import Column as _M56Column
from datatables_backend import DTParams as _M56DTParams
from datatables_backend import DTResponse as _M56DTResponse
from datatables_backend import parse_request as _m56_parse
from datatables_backend.sqlalchemy import SQLTable as _M56SQLTable

_M56_ROWS = [  # id, the Numeric(20, 2) value, the Float(53) value
    (1, '12345678901234567.89', 12345678901234567.89),
    (2, '12345678901234567.90', 12345678901234567.90),
    (3, '12345678901234568.00', 12345678901234568.0),
    (4, '0.30', 0.1 + 0.2),
    (5, '0.31', 0.3),
    (6, '0', 0.0),
]
_M56_CASES = [  # condition, values as a user types them
    ('=', ['12345678901234567.89']),
    ('=', ['12345678901234567.90']),
    ('between', ['12345678901234567.89', '12345678901234567.89']),
    ('<', ['12345678901234567.90']),
    ('>', ['12345678901234567.89']),
    ('=', ['12345678901234567.891']),  # a fraction longer than the scale
    ('=', ['0.305']),
    ('>', ['0.305']),
    ('<', ['0.305']),
    ('=', ['0.3']),
    ('=', ['1e-7']),  # an exponent a driver may write as such
    ('=', ['3.1e-1']),  # 0.31 written with an exponent
    ('<', ['1.5e3']),
    ('=', ['0.31' + '0' * 40]),  # 0.31 with trailing zeros: 42 digits as written
    ('<', ['9' * 25]),  # a whole number beyond 64 bits and wider than the column: a Decimal on main already
    ('=', ['1e-400']),  # a float of 0, a Decimal that is not 0
    ('>', ['1e-400']),
    ('<', ['1e400']),  # a finite Decimal, no finite float
    ('=', ['0.' + '1' * 45]),  # 45 digits after the point: wider than SQL Server's 38
    ('<', ['1' + '0' * 65 + '.5']),  # 67 digits: wider than MySQL's 65
    ('=', ['NaN']),
    ('<', ['Infinity']),
]
_M56_BINDS = ('float', 'Decimal, column type', 'Decimal, Numeric()')


def _m56_condition(column: _M56Any, condition: str, values: list[str], bind: str) -> _M56Any:
    def value(text: str) -> _M56Any:
        number = _M56Decimal(text)
        if bind == 'float':
            return float(number)
        if bind == 'Decimal, column type':
            return number
        return _m56_sa.literal(number, _m56_sa.Numeric())

    first = value(values[0])
    match condition:
        case '=':
            return column == first
        case '<':
            return column < first
        case '>':
            return column > first
        case _:
            return column.between(first, value(values[1]))


def _m56_outcome(run: _M56Callable[[_M56Any], _M56Any], argument: _M56Any) -> str:
    try:
        return str(run(argument))
    except Exception as error:  # a measurement records every failure
        first = (str(error).splitlines() or [''])[0]
        return f'{type(error).__name__}: {first[:110]}'


def _m56_params(column: str, condition: str, values: list[str]) -> _M56DTParams:
    columns = [
        {'data': name, 'name': '', 'searchable': True, 'orderable': True, 'search': {'value': '', 'regex': False}}
        for name in ('id', 'n', 'f')
    ]
    criteria = [{'condition': condition, 'origData': column, 'value': values}]
    return _m56_parse(
        {
            'draw': 1, 'start': 0, 'length': 10, 'columns': columns, 'order': [{'column': 0, 'dir': 'asc'}],
            'searchBuilder': {'logic': 'AND', 'criteria': criteria},
        }
    )  # fmt: skip


def _m56_answer(response: _M56DTResponse) -> _M56Any:
    return response.error_code or [row['id'] for row in response.data]


def test_measure_56_decimal_binds(database: tuple[str, str]):
    sync_url, async_url = database
    table = _m56_sa.Table(
        'dtb_test_stamps',
        _m56_sa.MetaData(),
        _m56_sa.Column('id', _m56_sa.Integer, primary_key=True, autoincrement=False),
        _m56_sa.Column('n', _m56_sa.Numeric(20, 2)),
        _m56_sa.Column('f', _m56_sa.Float(53)),
    )
    engine = _m56_sa.create_engine(sync_url)
    with engine.begin() as connection:
        table.create(connection)
        connection.execute(table.insert(), [{'id': i, 'n': _M56Decimal(n), 'f': f} for i, n, f in _M56_ROWS])
        version = '.'.join(map(str, connection.dialect.server_version_info or ()))
        stored = connection.execute(_m56_sa.select(table.c.id, table.c.n).order_by(table.c.id)).all()
    async_engine = _m56_async_engine(async_url, poolclass=_M56NullPool)  # each asyncio.run is a new event loop
    library = _M56SQLTable(table, [_M56Column('id'), _M56Column('n'), _M56Column('f')])

    def sync_ids(where: _M56Any) -> list[int]:
        with engine.connect() as connection:
            return [row[0] for row in connection.execute(_m56_sa.select(table.c.id).where(where).order_by(table.c.id))]

    async def async_ids(where: _M56Any) -> list[int]:
        async with async_engine.connect() as connection:
            result = await connection.execute(_m56_sa.select(table.c.id).where(where).order_by(table.c.id))
            return [row[0] for row in result]

    def library_sync(params: _M56DTParams) -> _M56Any:
        with engine.connect() as connection:
            return _m56_answer(library.fetch_sync(params, connection))

    async def library_async(params: _M56DTParams) -> _M56Any:
        async with _M56AsyncSession(async_engine) as session:
            return _m56_answer(await library.fetch(params, session))

    lines = [
        f'### #56 on {engine.dialect.name} {version}: sync {engine.driver}, async {async_engine.dialect.driver}',
        f'Stored Numeric(20, 2), read back: `{[(i, str(n)) for i, n in stored]}`',
        '',
        '| column | case | bind | sync | async |',
        '|---|---|---|---|---|',
    ]
    for column_name in ('n', 'f'):
        for condition, values in _M56_CASES:
            case = f'{condition} {" ".join(v if len(v) < 24 else v[:10] + f"..({len(v)})" for v in values)}'
            for bind in _M56_BINDS:
                where = _m56_condition(table.c[column_name], condition, values, bind)
                sync = _m56_outcome(sync_ids, where)
                asy = _m56_outcome(lambda clause: _m56_asyncio.run(async_ids(clause)), where)
                lines.append(f'| {column_name} | `{case}` | {bind} | {sync} | {asy} |')
            params = _m56_params(column_name, condition, values)
            sync = _m56_outcome(library_sync, params)
            asy = _m56_outcome(lambda request: _m56_asyncio.run(library_async(request)), params)
            lines.append(f'| {column_name} | `{case}` | library today | {sync} | {asy} |')
    _m56_asyncio.run(async_engine.dispose())
    engine.dispose()
    report = '\n'.join(lines) + '\n'
    _m56_sys.stdout.write(report)
    if summary := _m56_os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(summary, 'a', encoding='utf-8') as handle:
            handle.write(report)
