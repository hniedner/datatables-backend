"""MEASUREMENT ONLY, NOT FOR MERGE (#71): what a number column's search text is on SQL Server, and what a column search
finds in it. It skips unless the run is on SQL Server and then fails on purpose, so that the whole table is in the
failure message of the log."""

from decimal import Decimal
import os

import pytest
from sqlalchemy import Column as SQLColumn
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import Numeric
from sqlalchemy import Table
from sqlalchemy import create_engine
from sqlalchemy import select
from sqlalchemy.orm import Session

from datatables_backend import Column
from datatables_backend import parse_request
from datatables_backend.sqlalchemy import SQLTable
from datatables_backend.sqlalchemy import _text

from .conftest import SQL_URLS

FLOATS = [12.5, 17.0, 0.1, 1234567.0, 1e20, 1e-7, -3.25, 0.0, 123456789.123456789, 1.5e300]
NUMERICS = [
    Decimal('12.5'),
    Decimal(17),
    Decimal('0.1'),
    Decimal(1234567),
    Decimal('-3.25'),
    Decimal(0),
    Decimal('12.50'),
    Decimal('99999999.99'),
]
INTEGERS = [12, 17, 1234567, -3, 0]
WORDS = [
    '7', '17', '17.0', '12.5', '12.50', '1234567', '0.1', '.5', '3.25', '0', '0.0', '1e', 'e+', 'E', '-3.25',
    '100000000000000000000', '1e20', '1e-07', '0.0000001', '123456789.12345679',
]  # fmt: skip
CASES = [
    ('double', Float(53), FLOATS),
    ('single', Float(24), FLOATS[:8]),
    ('numeric', Numeric(10, 2), NUMERICS),
    ('integer', Integer(), INTEGERS),
]
#: Other ways to write a float as text, to see what a per-dialect expression could give
PROBES = [
    'CONVERT(VARCHAR(50), v, 3)',
    "FORMAT(v, 'R')",
    "FORMAT(v, 'G17')",
    'CAST(CAST(v AS DECIMAL(38, 10)) AS VARCHAR(60))',
]


def request(word: str) -> object:
    columns = [
        {'data': n, 'name': '', 'searchable': True, 'orderable': True,
         'search': {'value': word if n == 'v' else '', 'regex': False}}
        for n in ('id', 'v')
    ]  # fmt: skip
    return parse_request({'draw': 1, 'start': 0, 'length': 100, 'columns': columns})


@pytest.mark.skipif(os.environ.get('DTB_SQL_DIALECT') != 'mssql', reason='measures SQL Server only')
def test_measure_the_search_text_of_a_number_column():
    engine = create_engine(SQL_URLS[0])
    out: list[str] = []
    with engine.connect() as connection:
        for label, sql in (
            ('SERVER', 'SELECT @@VERSION'),
            ('COMPATIBILITY_LEVEL', 'SELECT compatibility_level FROM sys.databases WHERE name = DB_NAME()'),
        ):
            try:
                out.append(f'{label} {connection.exec_driver_sql(sql).scalar()!r}')
            except Exception as error:
                connection.rollback()
                out.append(f'{label} FAILED {type(error).__name__}')
        for kind, sql_type, values in CASES:
            metadata = MetaData()
            table = Table(
                f'dtb_test_f71_{kind}',
                metadata,
                SQLColumn('id', Integer, primary_key=True, autoincrement=False),
                SQLColumn('v', sql_type),
            )
            metadata.drop_all(connection)
            metadata.create_all(connection)
            connection.execute(table.insert(), [{'id': i, 'v': v} for i, v in enumerate(values, 1)])
            connection.commit()  # a failed probe rolls back, and must not take the rows with it
            compiled = select(_text(table.c.v)).compile(dialect=engine.dialect)
            out.append(f'\n== mssql / {kind}   column type: {sql_type.compile(dialect=engine.dialect)}')
            out.append(f'   search text SQL: {str(compiled).splitlines()[0]}')
            for i, raw, shown in connection.execute(
                select(table.c.id, table.c.v, _text(table.c.v)).order_by(table.c.id)
            ):
                out.append(f'   {i:>2}  python {values[i - 1]!r:<28} fetched {raw!r:<28} search text {shown!r}')
            if kind in ('double', 'single'):
                for probe in PROBES:
                    try:
                        sql = f'SELECT {probe} FROM {table.name} ORDER BY id'  # noqa: S608
                        rows = connection.exec_driver_sql(sql).all()
                        out.append(f'   PROBE {probe}: {[r[0] for r in rows]}')
                    except Exception as error:
                        connection.rollback()
                        out.append(f'   PROBE {probe}: FAILED {type(error).__name__} {str(error)[:120]!r}')
            sqltable = SQLTable(table, [Column('id'), Column('v')])
            session = Session(connection)
            out.append('   word -> ids found')
            for word in WORDS:
                response = sqltable.fetch_sync(request(word), session)  # type: ignore[arg-type]
                found = [row['id'] for row in response.data] if response.error is None else f'ERROR {response.error}'
                out.append(f'   {word!r:<26} {found}')
            session.close()
            metadata.drop_all(connection)
        connection.commit()
    engine.dispose()
    pytest.fail('MEASUREMENT (#71), fails on purpose:\n' + '\n'.join(out), pytrace=False)
