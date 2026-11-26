# 010: Numbers with a point or an exponent on the SQL databases

This is the record behind PRD-F25 for issue #56: what each SQL database does with a number written with a decimal
point or an exponent, sent as a float and sent as a `Decimal`, and what the library does about it. The requirements
are in `docs/PRD.md`, the limits an adopter meets in its section 5 and in `docs/databases.md`. Nothing here is a
requirement.

## How it was measured

CI run 45502948086 of a throwaway branch (`measure/56-decimal-binds`, pull request #185, a draft closed unmerged), on
PostgreSQL 18.7 (psycopg 3 and psycopg2 with asyncpg), MySQL 8.4.13 (PyMySQL and mysqlclient, with aiomysql), MariaDB
12.3.3 (PyMySQL, aiomysql), SQL Server 2022 (16.0.4325.4, pyodbc and aioodbc, ODBC Driver 18) and SQLite 3.46.1
(pysqlite and aiosqlite). One test created a `Numeric(20, 2)` column `n` and a `Float(53)` column `f` holding the same
six values and, for each case below, sent one condition with three binds (a Python `float`, a `Decimal` typed by the
column, and a `Decimal` typed by a `Numeric()` without precision or scale), sync and async, and the same request
through the library as it was on 01307bf, the branch's base (main) ("library today"), which is before #187. #187 has
since fixed the asyncpg overflow that the table shows for `< 9999...` of 25 digits on the column-typed bind. The
branch's logs expire with the run; the tables are quoted below.

The stored values, read back from every server: 12345678901234567.89, 12345678901234567.90, 12345678901234568.00,
0.30, 0.31 and 0.00 (rows 1 to 6). SQLite reads back 12345678901234568.00 for the first three. Not measured: other
drivers, `DECIMAL` columns of other scales, and the exact boundary of 38 digits on SQL Server, which the tests of
SPEC-F25.12 pin.

## What the databases do

- **A float cannot tell the first three rows apart** (RUN, every server and SQLite): `= 12345678901234567.89` finds
  all three rows on PostgreSQL, MariaDB, SQL Server and SQLite, and row 3 only on MySQL. As a `Decimal` the
  servers find row 1 and no other; SQLite finds all three whatever the bind, because it stores the column as an
  integer (`12345678901234568`), and SQLAlchemy's bind processor for a `Numeric` there is `to_float`.
- **asyncpg casts by the type of the value** (RUN): a `Decimal` typed by the column is sent as `$1::NUMERIC(20, 2)`,
  the column's own precision and scale, so `= 0.305` finds row 5 (0.31), `= 1e-7` finds row 6 (0.00), `> 0.305` loses
  row 5, and a number of 25 digits or more fails with a database error. Typed `Numeric()` it is `$1::NUMERIC` and
  answers as psycopg does. A float goes as `$1::FLOAT`. The same cast made `< 9999...` of 31 digits on a
  `Numeric(32, 2)` fail on main (fixed in #187 by binding through `Numeric()`).
- **SQL Server's driver refuses a long decimal** (RUN): a `Decimal` of 44 digits (`0.31` followed by 40 zeros),
  `1e-400`, `1e400` and numbers of 47 and 68 digits fail in pyodbc and aioodbc with `HY104`, "Invalid precision value
  (0)", the same refusal decision 004 measured for a whole number. A float is answered, except `1e400`, `NaN` and `Infinity`, which fail as `ProgrammingError 42000`; as a `Decimal`
  the last two fail with `DataError 22018`. The library refuses all three before any bind.
- **MySQL and MariaDB answer a number of 47 or 68 digits** (RUN), and `= 1e-400` finds the row holding 0.00 as a
  `Decimal` too. INFERRED: the manual gives a `DECIMAL` at most 30 digits after the point (MySQL 8.4,
  https://dev.mysql.com/doc/refman/8.4/en/numeric-type-syntax.html, FETCHED 2026-11-24: "The maximum number of digits
  (M) for DECIMAL is 65. The maximum number of supported decimals (D) is 30."), so a number with more is compared as
  a double there, which equals zero. This is a limit of the database, stated in `docs/databases.md`.
- **A `Float` column** takes the same answer from a float and from a `Decimal` in most cases and differs in a few
  of the extreme ones (3 cases on PostgreSQL, 8 on SQL Server); the library sends a float there, as before.

## The decision

- `parse_number` returns an `int` for a whole number written without a point or an exponent, as before, and a
  `Decimal` otherwise, which keeps every digit written. A number is finite when a float holds it, so `1e400` is
  still `invalid_number`; an exponent beyond what a `Decimal` holds is `invalid_number` too (it was read as zero when
  negative). So is a number of more than 16,383 digits after the point, the most PostgreSQL's `numeric` takes (its
  manual, Table 8.2, <https://www.postgresql.org/docs/current/datatype-numeric.html>, FETCHED 2026-11-24), which would
  otherwise reach the database as a driver error. The limit is of the value, not of how it is written: a fraction's
  trailing zeros are not counted and zero has no fraction, so `1.0e-16383` is answered like `1e-16383`, and
  `parse_number` returns it without those zeros (a number within the limit is returned as written, which no backend can tell: each
  drops the zeros again or sends a float).
- An exact `Numeric` column gets a `Decimal` typed `Numeric()`, a fraction's trailing zeros removed (and zero as
  zero), so that SQL Server's count of 38 digits does not depend on how many zeros were typed and every database gets
  the same number. An exponent is left as written: a `Decimal` of `1.5e3` answered on SQL Server (RUN, sync and async),
  so nothing needs it rewritten. A column behind a `TypeDecorator` is compared through its own type, which converts
  the number as it does any other. An integer column gets the whole number or, for a fraction, a `Decimal` typed
  `Numeric()` (a float of 8.0000000000000000001 is 8.0, which an integer column holds); a list leaves such a fraction
  out. A `Float` column and an expression without a type get a float. OpenSearch gets a float in its request body
  (SPEC-A8.1); it parses a query's number as a double for its float types (not measured).
- On SQL Server a number of more than 38 digits in all, before and after the point, is refused as `invalid_number`
  for every condition, with the limit and the fix in the reason, never the driver's error. The advisor's ruling of
  2026-11-24.
- Not done: carrying the written text in the plan (more plumbing than a `Decimal` for the same information), and a
  `Decimal` only above 15 digits (two representations by digit count, and a column holding more digits than the query
  is still cast to a float on PostgreSQL).
- Shipped as a patch release, the owner's ruling: an answer that was wrong is corrected, and the notes say so.

## The tables

### postgresql 18.7: sync psycopg, async psycopg

Column `n`, `Numeric(20, 2)`. A cell is the sync answer, or `sync / async` where they differ. "Column" is a Decimal typed by the column; "scale-free" a Decimal typed `Numeric()`.

| Case | float | Decimal, column | Decimal, scale-free |
|---|---|---|---|
| `= 12345678901234567.89` | [1, 2, 3] | [1] | [1] |
| `= 12345678901234567.90` | [1, 2, 3] | [2] | [2] |
| `between 12345678901234567.89 12345678901234567.89` | [1, 2, 3] | [1] | [1] |
| `< 12345678901234567.90` | [4, 5, 6] | [1, 4, 5, 6] | [1, 4, 5, 6] |
| `> 12345678901234567.89` | [] | [2, 3] | [2, 3] |
| `= 12345678901234567.891` | [1, 2, 3] | [] | [] |
| `= 0.305` | [] | [] | [] |
| `> 0.305` | [1, 2, 3, 5] | [1, 2, 3, 5] | [1, 2, 3, 5] |
| `< 0.305` | [4, 6] | [4, 6] | [4, 6] |
| `= 0.3` | [4] | [4] | [4] |
| `= 1e-7` | [] | [] | [] |
| `= 3.1e-1` | [5] | [5] | [5] |
| `< 1.5e3` | [4, 5, 6] | [4, 5, 6] | [4, 5, 6] |
| `= 0.31000000..(44)` | [5] | [5] | [5] |
| `< 9999999999..(25)` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= 1e-400` | [6] | [] | [] |
| `> 1e-400` | [1, 2, 3, 4, 5] | [1, 2, 3, 4, 5] | [1, 2, 3, 4, 5] |
| `< 1e400` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= 0.11111111..(47)` | [] | [] | [] |
| `< 1000000000..(68)` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= NaN` | [] | [] | [] |
| `< Infinity` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |

### postgresql 18.7: sync psycopg2, async asyncpg

Column `n`, `Numeric(20, 2)`. A cell is the sync answer, or `sync / async` where they differ. "Column" is a Decimal typed by the column; "scale-free" a Decimal typed `Numeric()`.

| Case | float | Decimal, column | Decimal, scale-free |
|---|---|---|---|
| `= 12345678901234567.89` | [3] / [1, 2, 3] | [1] | [1] |
| `= 12345678901234567.90` | [3] / [1, 2, 3] | [2] | [2] |
| `between 12345678901234567.89 12345678901234567.89` | [3] / [1, 2, 3] | [1] | [1] |
| `< 12345678901234567.90` | [1, 2, 4, 5, 6] / [4, 5, 6] | [1, 4, 5, 6] | [1, 4, 5, 6] |
| `> 12345678901234567.89` | [] | [2, 3] | [2, 3] |
| `= 12345678901234567.891` | [3] / [1, 2, 3] | [] / [1] | [] |
| `= 0.305` | [] | [] / [5] | [] |
| `> 0.305` | [1, 2, 3, 5] | [1, 2, 3, 5] / [1, 2, 3] | [1, 2, 3, 5] |
| `< 0.305` | [4, 6] | [4, 6] | [4, 6] |
| `= 0.3` | [4] | [4] | [4] |
| `= 1e-7` | [] | [] / [6] | [] |
| `= 3.1e-1` | [5] | [5] | [5] |
| `< 1.5e3` | [4, 5, 6] | [4, 5, 6] | [4, 5, 6] |
| `= 0.31000000..(44)` | [5] | [5] | [5] |
| `< 9999999999..(25)` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] / DBAPIError | [1, 2, 3, 4, 5, 6] |
| `= 1e-400` | [6] | [] / [6] | [] |
| `> 1e-400` | [1, 2, 3, 4, 5] | [1, 2, 3, 4, 5] | [1, 2, 3, 4, 5] |
| `< 1e400` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] / DBAPIError | [1, 2, 3, 4, 5, 6] |
| `= 0.11111111..(47)` | [] | [] | [] |
| `< 1000000000..(68)` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] / DBAPIError | [1, 2, 3, 4, 5, 6] |
| `= NaN` | [] | [] | [] |
| `< Infinity` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] / DBAPIError | [1, 2, 3, 4, 5, 6] |

### mysql 8.4.13: sync pymysql, async aiomysql

Column `n`, `Numeric(20, 2)`. A cell is the sync answer, or `sync / async` where they differ. "Column" is a Decimal typed by the column; "scale-free" a Decimal typed `Numeric()`.

| Case | float | Decimal, column | Decimal, scale-free |
|---|---|---|---|
| `= 12345678901234567.89` | [3] | [1] | [1] |
| `= 12345678901234567.90` | [3] | [2] | [2] |
| `between 12345678901234567.89 12345678901234567.89` | [3] | [1] | [1] |
| `< 12345678901234567.90` | [1, 2, 4, 5, 6] | [1, 4, 5, 6] | [1, 4, 5, 6] |
| `> 12345678901234567.89` | [] | [2, 3] | [2, 3] |
| `= 12345678901234567.891` | [3] | [] | [] |
| `= 0.305` | [] | [] | [] |
| `> 0.305` | [1, 2, 3, 5] | [1, 2, 3, 5] | [1, 2, 3, 5] |
| `< 0.305` | [4, 6] | [4, 6] | [4, 6] |
| `= 0.3` | [4] | [4] | [4] |
| `= 1e-7` | [] | [] | [] |
| `= 3.1e-1` | [5] | [5] | [5] |
| `< 1.5e3` | [4, 5, 6] | [4, 5, 6] | [4, 5, 6] |
| `= 0.31000000..(44)` | [5] | [5] | [5] |
| `< 9999999999..(25)` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= 1e-400` | [6] | [6] | [6] |
| `> 1e-400` | [1, 2, 3, 4, 5] | [1, 2, 3, 4, 5] | [1, 2, 3, 4, 5] |
| `< 1e400` | ProgrammingError | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= 0.11111111..(47)` | [] | [] | [] |
| `< 1000000000..(68)` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= NaN` | ProgrammingError | ProgrammingError | ProgrammingError |
| `< Infinity` | ProgrammingError | ProgrammingError | ProgrammingError |

### mysql 8.4.13: sync mysqldb, async aiomysql

Column `n`, `Numeric(20, 2)`. A cell is the sync answer, or `sync / async` where they differ. "Column" is a Decimal typed by the column; "scale-free" a Decimal typed `Numeric()`.

| Case | float | Decimal, column | Decimal, scale-free |
|---|---|---|---|
| `= 12345678901234567.89` | [3] | [1] | [1] |
| `= 12345678901234567.90` | [3] | [2] | [2] |
| `between 12345678901234567.89 12345678901234567.89` | [3] | [1] | [1] |
| `< 12345678901234567.90` | [1, 2, 4, 5, 6] | [1, 4, 5, 6] | [1, 4, 5, 6] |
| `> 12345678901234567.89` | [] | [2, 3] | [2, 3] |
| `= 12345678901234567.891` | [3] | [] | [] |
| `= 0.305` | [] | [] | [] |
| `> 0.305` | [1, 2, 3, 5] | [1, 2, 3, 5] | [1, 2, 3, 5] |
| `< 0.305` | [4, 6] | [4, 6] | [4, 6] |
| `= 0.3` | [4] | [4] | [4] |
| `= 1e-7` | [] | [] | [] |
| `= 3.1e-1` | [5] | [5] | [5] |
| `< 1.5e3` | [4, 5, 6] | [4, 5, 6] | [4, 5, 6] |
| `= 0.31000000..(44)` | [5] | [5] | [5] |
| `< 9999999999..(25)` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= 1e-400` | [6] | [6] | [6] |
| `> 1e-400` | [1, 2, 3, 4, 5] | [1, 2, 3, 4, 5] | [1, 2, 3, 4, 5] |
| `< 1e400` | ProgrammingError | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= 0.11111111..(47)` | [] | [] | [] |
| `< 1000000000..(68)` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= NaN` | ProgrammingError | OperationalError / ProgrammingError | OperationalError / ProgrammingError |
| `< Infinity` | ProgrammingError | OperationalError / ProgrammingError | OperationalError / ProgrammingError |

### mariadb 12.3.3: sync pymysql, async aiomysql

Column `n`, `Numeric(20, 2)`. A cell is the sync answer, or `sync / async` where they differ. "Column" is a Decimal typed by the column; "scale-free" a Decimal typed `Numeric()`.

| Case | float | Decimal, column | Decimal, scale-free |
|---|---|---|---|
| `= 12345678901234567.89` | [1, 2, 3] | [1] | [1] |
| `= 12345678901234567.90` | [1, 2, 3] | [2] | [2] |
| `between 12345678901234567.89 12345678901234567.89` | [1, 2, 3] | [1] | [1] |
| `< 12345678901234567.90` | [4, 5, 6] | [1, 4, 5, 6] | [1, 4, 5, 6] |
| `> 12345678901234567.89` | [] | [2, 3] | [2, 3] |
| `= 12345678901234567.891` | [1, 2, 3] | [] | [] |
| `= 0.305` | [] | [] | [] |
| `> 0.305` | [1, 2, 3, 5] | [1, 2, 3, 5] | [1, 2, 3, 5] |
| `< 0.305` | [4, 6] | [4, 6] | [4, 6] |
| `= 0.3` | [4] | [4] | [4] |
| `= 1e-7` | [] | [] | [] |
| `= 3.1e-1` | [5] | [5] | [5] |
| `< 1.5e3` | [4, 5, 6] | [4, 5, 6] | [4, 5, 6] |
| `= 0.31000000..(44)` | [5] | [5] | [5] |
| `< 9999999999..(25)` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= 1e-400` | [6] | [6] | [6] |
| `> 1e-400` | [1, 2, 3, 4, 5] | [1, 2, 3, 4, 5] | [1, 2, 3, 4, 5] |
| `< 1e400` | ProgrammingError | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= 0.11111111..(47)` | [] | [] | [] |
| `< 1000000000..(68)` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= NaN` | ProgrammingError | ProgrammingError | ProgrammingError |
| `< Infinity` | ProgrammingError | ProgrammingError | ProgrammingError |

### mssql 16.0.4325.4: sync pyodbc, async aioodbc

Column `n`, `Numeric(20, 2)`. A cell is the sync answer, or `sync / async` where they differ. "Column" is a Decimal typed by the column; "scale-free" a Decimal typed `Numeric()`.

| Case | float | Decimal, column | Decimal, scale-free |
|---|---|---|---|
| `= 12345678901234567.89` | [1, 2, 3] | [1] | [1] |
| `= 12345678901234567.90` | [1, 2, 3] | [2] | [2] |
| `between 12345678901234567.89 12345678901234567.89` | [1, 2, 3] | [1] | [1] |
| `< 12345678901234567.90` | [4, 5, 6] | [1, 4, 5, 6] | [1, 4, 5, 6] |
| `> 12345678901234567.89` | [] | [2, 3] | [2, 3] |
| `= 12345678901234567.891` | [1, 2, 3] | [] | [] |
| `= 0.305` | [] | [] | [] |
| `> 0.305` | [1, 2, 3, 5] | [1, 2, 3, 5] | [1, 2, 3, 5] |
| `< 0.305` | [4, 6] | [4, 6] | [4, 6] |
| `= 0.3` | [4] | [4] | [4] |
| `= 1e-7` | [] | [] | [] |
| `= 3.1e-1` | [5] | [5] | [5] |
| `< 1.5e3` | [4, 5, 6] | [4, 5, 6] | [4, 5, 6] |
| `= 0.31000000..(44)` | [5] | DBAPIError HY104 | DBAPIError HY104 |
| `< 9999999999..(25)` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= 1e-400` | [6] | DBAPIError HY104 | DBAPIError HY104 |
| `> 1e-400` | [1, 2, 3, 4, 5] | DBAPIError HY104 | DBAPIError HY104 |
| `< 1e400` | ProgrammingError 42000 | DBAPIError HY104 | DBAPIError HY104 |
| `= 0.11111111..(47)` | [] | DBAPIError HY104 | DBAPIError HY104 |
| `< 1000000000..(68)` | [1, 2, 3, 4, 5, 6] | DBAPIError HY104 | DBAPIError HY104 |
| `= NaN` | ProgrammingError 42000 | DataError 22018 | DataError 22018 |
| `< Infinity` | ProgrammingError 42000 | DataError 22018 | DataError 22018 |

### sqlite 3.46.1: sync pysqlite, async aiosqlite

Column `n`, `Numeric(20, 2)`. A cell is the sync answer, or `sync / async` where they differ. "Column" is a Decimal typed by the column; "scale-free" a Decimal typed `Numeric()`.

| Case | float | Decimal, column | Decimal, scale-free |
|---|---|---|---|
| `= 12345678901234567.89` | [1, 2, 3] | [1, 2, 3] | [1, 2, 3] |
| `= 12345678901234567.90` | [1, 2, 3] | [1, 2, 3] | [1, 2, 3] |
| `between 12345678901234567.89 12345678901234567.89` | [1, 2, 3] | [1, 2, 3] | [1, 2, 3] |
| `< 12345678901234567.90` | [4, 5, 6] | [4, 5, 6] | [4, 5, 6] |
| `> 12345678901234567.89` | [] | [] | [] |
| `= 12345678901234567.891` | [1, 2, 3] | [1, 2, 3] | [1, 2, 3] |
| `= 0.305` | [] | [] | [] |
| `> 0.305` | [1, 2, 3, 5] | [1, 2, 3, 5] | [1, 2, 3, 5] |
| `< 0.305` | [4, 6] | [4, 6] | [4, 6] |
| `= 0.3` | [4] | [4] | [4] |
| `= 1e-7` | [] | [] | [] |
| `= 3.1e-1` | [5] | [5] | [5] |
| `< 1.5e3` | [4, 5, 6] | [4, 5, 6] | [4, 5, 6] |
| `= 0.31000000..(44)` | [5] | [5] | [5] |
| `< 9999999999..(25)` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= 1e-400` | [6] | [6] | [6] |
| `> 1e-400` | [1, 2, 3, 4, 5] | [1, 2, 3, 4, 5] | [1, 2, 3, 4, 5] |
| `< 1e400` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= 0.11111111..(47)` | [] | [] | [] |
| `< 1000000000..(68)` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
| `= NaN` | [] | [] | [] |
| `< Infinity` | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] | [1, 2, 3, 4, 5, 6] |
