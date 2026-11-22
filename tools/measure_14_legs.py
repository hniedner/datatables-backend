"""A measurement, not a test: what PostgreSQL, MySQL and SQL Server answer for the statements the second part of
issue 14 plans to send. It reads DTB_SQL_URL, owns the table `dtb_test_tags`, and writes one line for each case."""

from collections.abc import Callable
from decimal import Decimal
import os
import sys
from typing import Any
import uuid

from sqlalchemy import CHAR
from sqlalchemy import BigInteger
from sqlalchemy import Column as SQLColumn
from sqlalchemy import Enum
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import Uuid
from sqlalchemy import create_engine
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy import type_coerce
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session
from sqlalchemy.types import TypeDecorator

from datatables_backend import Column
from datatables_backend import parse_request
from datatables_backend.sqlalchemy import SQLTable


class Wrapped(TypeDecorator[str]):
    impl = String
    cache_ok = True


class Guid(TypeDecorator[str]):
    impl = CHAR
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: object) -> str | None:
        return None if value is None or dialect is None else uuid.UUID(value).hex


def out(text: str) -> None:
    sys.stdout.write(text + '\n')
    sys.stdout.flush()


engine = create_engine(os.environ['DTB_SQL_URL'])
dialect = engine.dialect.name
ONE = uuid.UUID('1f960000-0000-4000-8000-000000000001')
meta = MetaData()
tags = Table(
    'dtb_test_tags',
    meta,
    SQLColumn('id', Integer, primary_key=True, autoincrement=False),
    SQLColumn('n', Numeric(30, 0)),
    SQLColumn('loose', Numeric),
    SQLColumn('f', Float),
    SQLColumn('plain', String(40)),
    SQLColumn('wrapped', Wrapped(40)),
    SQLColumn('guid', Guid(32)),
    SQLColumn('uid', Uuid),
    SQLColumn('size', Enum('Small', 'LARGE', name='dtb_test_size')),
    SQLColumn('big', mysql.BIGINT(unsigned=True) if dialect == 'mysql' else BigInteger),
)
with engine.begin() as connection:
    existing = MetaData()
    existing.reflect(connection, only=lambda name, _: name == 'dtb_test_tags')
    existing.drop_all(connection)
    meta.drop_all(connection)  # the enum type an earlier run left on PostgreSQL
    meta.create_all(connection)
    connection.execute(
        tags.insert(),
        [
            {'id': 1, 'n': Decimal(2**64), 'loose': Decimal(7), 'f': 2.0**64, 'plain': 'a', 'wrapped': 'a',
             'guid': str(ONE), 'uid': ONE, 'size': 'Small', 'big': 2**64 - 1 if dialect == 'mysql' else 2**63 - 1},
            {'id': 2, 'n': None, 'loose': None, 'f': None, 'plain': '', 'wrapped': '', 'guid': None, 'uid': None,
             'size': None, 'big': None},
            {'id': 3, 'n': None, 'loose': None, 'f': None, 'plain': None, 'wrapped': None, 'guid': None, 'uid': None,
             'size': None, 'big': None},
        ],
    )  # fmt: skip
    version = '.'.join(map(str, connection.dialect.server_version_info or ()))
out(f'== {dialect} {version}, driver {engine.dialect.driver}')
for column in tags.c:
    out(f'column {column.name}: {column.type.compile(dialect=engine.dialect)}')


def short(exc: BaseException) -> str:
    return f'{type(exc).__name__}: {" ".join(str(exc).split())[:260]}'


def raw(label: str, build: Callable[[], Any]) -> None:
    """One WHERE clause, on a connection of its own: the statement as compiled, then the rows or the failure."""
    try:
        clause = build()
        text = ' '.join(str(clause.compile(dialect=engine.dialect)).split())
    except Exception as exc:  # the measurement keeps every failure
        out(f'RAW {label:46} BUILD FAILS {short(exc)}')
        return
    try:
        with engine.connect() as connection:
            rows = connection.execute(select(tags.c.id).where(clause).order_by(tags.c.id)).scalars().all()
        out(f'RAW {label:46} rows {rows}   [{text[:150]}]')
    except Exception as exc:  # the measurement keeps every failure
        out(f'RAW {label:46} FAILS {short(exc)}   [{text[:150]}]')


NAMES = ['id', 'n', 'loose', 'f', 'plain', 'wrapped', 'guid', 'uid', 'size', 'big']
table = SQLTable(tags, [Column(name) for name in NAMES])


def library(label: str, search: dict[str, dict[str, Any]] | None = None, **body: Any) -> None:
    """One request through the library as it is on main."""
    columns = [{'data': name, 'search': {'value': '', **(search or {}).get(name, {})}} for name in NAMES]
    try:
        with Session(engine) as session:
            response = table.fetch_sync(parse_request({'draw': 1, 'columns': columns, **body}), session)
        if response.error_code:
            out(f'LIB {label:46} {response.error_code}: {response.error}')
        else:
            out(f'LIB {label:46} rows {[row["id"] for row in response.data]}')
    except Exception as exc:  # the measurement keeps every failure
        out(f'LIB {label:46} RAISES {short(exc)}')


def criterion(name: str, condition: str, *values: str) -> dict[str, Any]:
    return {'searchBuilder': {'criteria': [{'condition': condition, 'origData': name, 'value': list(values)}]}}


out('-- a: a whole number on a Numeric column, as a Decimal and as the int main sends')
for name in ('n', 'loose'):
    column = tags.c[name]
    for label, number in [('2**64', 2**64), ('2**64+1', 2**64 + 1), ('2**63-1', 2**63 - 1)]:
        raw(f'{name} = Decimal({label})', lambda c=column, v=number: c == Decimal(v))
        raw(f'{name} = int {label}', lambda c=column, v=number: c == v)
        library(f'{name} = {label}', **criterion(name, '=', str(number)))
    for digits in (30, 31, 38, 39, 65, 66, 100, 400):
        number = int('9' * digits)
        raw(f'{name} = Decimal of {digits} digits', lambda c=column, v=number: c == Decimal(v))
        raw(f'{name} < Decimal of {digits} digits', lambda c=column, v=number: c < Decimal(v))
        raw(f'{name} IN (Decimal of {digits} digits)', lambda c=column, v=number: c.in_([Decimal(v)]))
    library(f'{name} = 400 digits', **criterion(name, '=', '9' * 400))
    library(f'{name} = 31 digits', **criterion(name, '=', '9' * 31))
out('-- a Float column and a whole number beyond 64 bits')
raw('f = int 2**64', lambda: tags.c.f == 2**64)
raw('f = float(2**64)', lambda: tags.c.f == float(2**64))
library('f = 2**64', **criterion('f', '=', str(2**64)))
library('f = 400 digits', **criterion('f', '=', '9' * 400))

out('-- b: BIGINT UNSIGNED (MySQL only; a signed BIGINT elsewhere)')
raw('big = 2**64-1', lambda: tags.c.big == 2**64 - 1)
raw('big = 2**63-1', lambda: tags.c.big == 2**63 - 1)
raw('big = -1', lambda: tags.c.big == -1)
library('big = 2**64-1', **criterion('big', '=', str(2**64 - 1)))
library('big = -1', **criterion('big', '=', '-1'))
out(f'the type as declared: unsigned={getattr(tags.c.big.type, "unsigned", None)}')
reflected = Table('dtb_test_tags', MetaData(), autoload_with=engine)
out(f'the type as reflected: {reflected.c.big.type!r}, unsigned={getattr(reflected.c.big.type, "unsigned", None)}')

out('-- c: an enum compared with the empty string')
raw("size = ''", lambda: tags.c.size == '')
raw("size IS NULL OR size = ''", lambda: or_(tags.c.size.is_(None), tags.c.size == ''))
library('size null', **criterion('size', 'null'))
library('size !null', **criterion('size', '!null'))
library("size multi_values ['']", {'size': {'multi_values': ['']}})

out('-- d: the stored empty string behind a type of its own')
for name in ('wrapped', 'guid', 'uid', 'plain'):
    column = tags.c[name]
    raw(f"{name} = ''", lambda c=column: c == '')
    raw(f"type_coerce({name}, String) = ''", lambda c=column: type_coerce(c, String) == '')
    raw(f"type_coerce({name}, String(40)) = ''", lambda c=column: type_coerce(c, String(40)) == '')
    raw(
        f"{name} IS NULL OR type_coerce(.., String) = ''",
        lambda c=column: or_(c.is_(None), type_coerce(c, String) == ''),
    )
    library(f'{name} null', **criterion(name, 'null'))
    library(f"{name} multi_values ['']", {name: {'multi_values': ['']}})
raw("type_coerce(guid, CHAR(32)) = ''", lambda: type_coerce(tags.c.guid, CHAR(32)) == '')

out('-- e: a list with an empty entry, as the plan would send it')
raw(
    "plain IN ('a') OR IS NULL OR = ''",
    lambda: or_(tags.c.plain.in_(['a']), tags.c.plain.is_(None), tags.c.plain == ''),
)
raw(
    "wrapped IN ('a') OR IS NULL OR coerced = ''",
    lambda: or_(tags.c.wrapped.in_(['a']), tags.c.wrapped.is_(None), type_coerce(tags.c.wrapped, String) == ''),
)
raw(
    "guid IN (one) OR IS NULL OR coerced = ''",
    lambda: or_(tags.c.guid.in_([str(ONE)]), tags.c.guid.is_(None), type_coerce(tags.c.guid, String) == ''),
)
raw('uid IN (one) OR IS NULL', lambda: or_(tags.c.uid.in_([ONE]), tags.c.uid.is_(None)))
raw("size IN ('Small') OR IS NULL", lambda: or_(tags.c.size.in_(['Small']), tags.c.size.is_(None)))
raw('n IN (Decimal 2**64) OR IS NULL', lambda: or_(tags.c.n.in_([Decimal(2**64)]), tags.c.n.is_(None)))
raw('f IN (float 2**64) OR IS NULL', lambda: or_(tags.c.f.in_([float(2**64)]), tags.c.f.is_(None)))
for name, value in [('plain', 'a'), ('wrapped', 'a'), ('guid', str(ONE)), ('uid', str(ONE)), ('size', 'Small')]:
    library(f"{name} multi_values ['', value] on main", {name: {'multi_values': ['', value]}})

with engine.begin() as connection:
    meta.drop_all(connection)
out('== done')
