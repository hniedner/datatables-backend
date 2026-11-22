# 004: Numbers and the empty string on the SQL servers

This is the record of one measurement made for issue #14 on 2026-11-22: what PostgreSQL, MySQL and SQL Server
answer to a whole number beyond 64 bits, to a number wider than a `Numeric` column, and to the empty string
compared with a column that is no plain string. PRD-F25 and PRD-F26 rest on it. The requirements are in
`docs/PRD.md`. Nothing here is a requirement.

## How it was measured

CI run 45041889791 of a throwaway branch (pull request #55, closed unmerged), on PostgreSQL 18.7 with psycopg,
MySQL 8.4.13 with PyMySQL and SQL Server 2022 (16.0.4325.4) with pyodbc and Microsoft's ODBC Driver 18. One script,
`tools/measure_14_legs.py` at commit 5bb6901, which that pull request keeps, created a table and sent two kinds of
request to each server: a SQLAlchemy statement with one condition, and a request through the library as it was on
main at 06e9d88. The SQLite results are from the same script run locally (SQLite 3.53.1).

The table held a `Numeric(30, 0)`, a `Numeric` without a precision, a `Float`, a `String`, a `TypeDecorator` over
`String` that converts nothing, a `TypeDecorator` over `CHAR(32)` that converts a UUID to its hex digits and
refuses any other text, SQLAlchemy's `Uuid`, an `Enum` (a native type on PostgreSQL and MySQL) and a BIGINT,
unsigned on MySQL. Not measured: MariaDB, other drivers, and a decimal with a fraction.

## A whole number beyond 64 bits

SQLAlchemy binds a Python `int` as an integer, whatever the column is.

| Sent to a `Numeric` or `Float` column | PostgreSQL | MySQL | SQL Server | SQLite |
|---|---|---|---|---|
| the `int` 2^64, as main sent it | fails: "bigint out of range" (the parameter is cast `::BIGINT`) | answered | fails in the driver: `OverflowError: int too big to convert` | fails in the driver: `OverflowError` |
| a `Decimal` of 2^64, on the `Numeric` column holding 2^64 | the row | the row | the row | the row |
| a `Decimal` of 2^64+1, on the same column | no row | no row | no row | the row |
| a `float` of 2^64, on the `Float` column holding it | the row | the row | the row | the row |

So main failed the request on three of the four databases, with an error of the database or the driver, for a
number a user can type. A `Decimal` is compared exactly on the three servers. On SQLite it is not: SQLAlchemy's
`Numeric` binds a decimal as a float there (its bind processor is `to_float`; on the other three dialects it has
none), since SQLite stores no exact decimal, so 2^64+1 finds the row that holds 2^64.

## A number wider than the column

A `Decimal` of 30, 31, 38, 39, 65, 66, 100 and 400 digits was compared with both `Numeric` columns, with `=`, `<`
and `IN`.

| Digits | PostgreSQL | MySQL | SQL Server |
|---|---|---|---|
| 30 to 38 | answered | answered | answered |
| 39 to 400 | answered | answered | fails in the driver: HY104 "Invalid precision value (0) (SQLBindParameter)" |

SQL Server's decimal type holds 38 digits at most, and its driver refuses to bind a longer number, on the column
declared `Numeric(30, 0)` and on the one without a precision alike. PostgreSQL and MySQL take a number of any of
these widths and compare it.

What follows from it (PRD-F25): a whole number with more digits than the column's precision less its scale is
refused as out of range on every database, since the column cannot hold it; on SQL Server the bound is never more
than 38, whatever the column declares; a `Numeric` column without a declared precision has no bound elsewhere.

## An unsigned integer on MySQL

A `BIGINT UNSIGNED` column holding 2^64-1 answers `= 2^64-1` with the row. The SQLAlchemy type says `unsigned=True`
of itself, as declared (`mysql.BIGINT(unsigned=True)`) and as reflected from the database. Main refused the number
with the signed range in its text.

## The empty string

| Compared with `''` | PostgreSQL | MySQL | SQL Server |
|---|---|---|---|
| a native enum without `''` among its values | fails: "invalid input value for enum" | answered, no row | answered, no row (a `VARCHAR` there) |
| a `Uuid` column | fails: "invalid input syntax for type uuid" | fails in SQLAlchemy's bind (`'str' object has no attribute 'hex'`) | fails: "Conversion failed when converting from a character string" |
| the converting `TypeDecorator`, as `column == ''` | fails in the type's own bind (`ValueError`) | the same | the same |
| the same column, as `type_coerce(column, String) == ''` | answered | answered | answered |
| a `Uuid` column, as `type_coerce(column, String) == ''` | fails: "operator does not exist: uuid = character varying" | answered | fails: "Conversion failed" |

So on main "empty" on a native PostgreSQL enum failed the request, and a list with an empty entry failed it on a
`Uuid` column and behind a converting `TypeDecorator` on every server. A column that stores text behind a
`TypeDecorator` can be compared with `''` when the comparison is made with the stored type, which keeps the type's
conversion out of it. A `Uuid` cannot be compared with text at all.

What follows from it (PRD-F26): the stored type decides whether `''` counts as empty. A string type does, also
behind a `TypeDecorator`; an enum only when `''` is one of its values; any other type is only ever tested for NULL.

A list with an empty entry, as the library now sends it (the values in an `IN`, then `OR column IS NULL`, and
`OR column = ''` where the column stores text), was answered by all three servers on each of these columns.
