# What each database does on its own

The library does not change how a database compares values. Where a rule of one engine changes what a user
sees, it is written down here, by engine, and so are the library's own limits on an engine, each marked as
such: what the adopter sees, an example, how it is known, and the requirement of `docs/PRD.md` it touches. "MEASURED" names the date, the version and the
CI run or the decisions file that holds the numbers; "documented, not run" means the engine's documentation says
so and no run of this project checked it.

PRD section 5 lists the same differences from the requirements' side. An engine without an entry below has none
recorded yet.

## PostgreSQL

No entry yet.

## MySQL

### An exact match ignores trailing spaces under a PAD SPACE collation

- **What you see:** on a column with a PAD SPACE collation (such as `utf8mb4_general_ci` or `utf8mb4_unicode_ci`),
  `=`, a categorical filter and a `multi_values` list treat `'a '` and `'a'` as the same value, and a value of
  spaces only matches the empty string. Under the default collation of MySQL 8 (`utf8mb4_0900_ai_ci`, NO PAD)
  they are different values.
- **Example:** `multi_values: [" "]` on a column holding `''` finds that row under a PAD SPACE collation, and no
  row under the default one.
- **How it is known:** the default collation MEASURED 2026-11-22 on MySQL 8.4.13
  (`docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md`). A PAD SPACE collation: documented,
  not run.
- **Requirement:** PRD-F20, PRD-F26.

## SQL Server

### An exact match ignores trailing spaces

- **What you see:** `=`, a categorical filter and a `multi_values` list treat `'a '` and `'a'` as the same value,
  as SQL Server compares strings, and a value of spaces only matches the empty string.
- **Example:** `multi_values: [" "]` on a column holding `''` finds that row. PostgreSQL and SQLite find none.
- **How it is known:** MEASURED 2026-11-22 on SQL Server 2022
  (`docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md`).
- **Requirement:** PRD-F20, PRD-F26.

### A very long list on an expression without a SQL type cannot be sent

This is a limit of the library, not of SQL Server: PRD-C17 does not hold for such a column. Issue #52 holds the
fix, so it may change.

- **What you see:** a request whose lists hold more than about 2,000 values in all is sent with each list as one
  JSON value (PRD-C17). For a column that is an expression without a SQL type, that statement cannot be built, and
  the request fails as a server failure.
- **Example:** a column declared over `literal_column('upper(name)')` with a `multi_values` list of 2,200 values.
  Give the expression a type to avoid it: `type_coerce(literal_column('upper(name)'), String)`, or a `cast`.
- **How it is known:** seen 2026-11-22 by compiling the statement for the `mssql` dialect (SQLAlchemy raises
  `CompileError`); not run on a server.
- **Requirement:** PRD-C17.

### A decimal holds 38 digits at most

- **What you see:** a number of more than 38 digits before the decimal point on a `Numeric` column is answered
  with `invalid_number`, whatever precision the column declares. No other database bounds such a column.
- **Example:** `=` with a whole number of 39 digits, or `1e39`, on a `Numeric` column: `invalid_number` on SQL
  Server, no rows on PostgreSQL, MySQL and SQLite.
- **How it is known:** MEASURED 2026-11-22 on SQL Server 2022 with Microsoft's ODBC Driver 18, which refuses to
  send a longer number (`docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md`).
- **Requirement:** PRD-F25.

## SQLite

### A `Numeric` column is not compared exactly

- **What you see:** a whole number outside the signed 64-bit range can match a neighbouring value on a `Numeric`
  column. SQLite
  stores no exact decimal, and SQLAlchemy sends a decimal to it as a floating-point number. PostgreSQL, MySQL and
  SQL Server compare the number exactly.
- **Example:** a `Numeric(30, 0)` column holding 18446744073709551616 (2^64): `=` 18446744073709551617 finds that
  row on SQLite and no row on the other three.
- **How it is known:** MEASURED 2026-11-22 on SQLite 3.53.1, PostgreSQL 18.7, MySQL 8.4.13 and SQL Server 2022
  (`docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md`).
- **Requirement:** PRD-F25.

## Every SQL database

### A number written with a point or an exponent is compared as a floating-point number

This is a limit of the library, not a rule of the databases: the library reads such a number as a floating-point
number before any database sees it. Issue #56 holds the fix, so it may change.

- **What you see:** on a `Numeric` column such a number is exact to about 15 significant digits only, so `=` can
  also match a neighbouring value and `between` can include or leave out a value at its bounds. A whole number
  is compared exactly (on SQLite as its entry says).
- **Example:** a `Numeric(20, 2)` column holding 12345678901234567.89: `=` 12345678901234567.89 can also find a
  row holding 12345678901234567.90.
- **How it is known:** read in the code (a number with a point or an exponent is a Python `float` before any
  backend sees it); not run on a server.
- **Requirement:** PRD-F25.

## OpenSearch

### A number outside the range of its field fails the request

This is a limit of the library, not of OpenSearch, which says clearly what it refuses. Issue #47 holds the
fix, so it may change.

- **What you see:** a number that the field's own mapping cannot hold (`long`, `integer`, `unsigned_long`) is
  refused by the server with a 400, which reaches the application as the client's `RequestError`, not as
  `invalid_number`. The library does not read the mapping, so it cannot know the range. SQL answers such a number
  on an integer column with `invalid_number`. A `double` field takes every finite number, and refuses a whole
  number of 400 digits in the same way.
- **Example:** `=` 18446744073709551616 on a column over an `integer` or a `long` field.
- **How it is known:** MEASURED 2026-11-22 on OpenSearch 3.10.0 and 2.19.7
  (`docs/decisions/003-opensearch-number-ranges-and-fullwidth-marks.md`).
- **Requirement:** PRD-F25, PRD-E7.
