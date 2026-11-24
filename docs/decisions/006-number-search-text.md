# 006: The search text of a number column

This is the record behind PRD-F23 (a number column is searched as text) for issue #71: what each SQL database writes
for a number when the library casts it to text, which searches that finds and loses, and what the library does
about it. The requirements are in `docs/PRD.md`, the spellings an adopter meets in its section 5 and in
`docs/databases.md`. Nothing here is a requirement.

## How it was measured

The search text of a number column is the database's own `CAST(column AS VARCHAR)` (`CAST(column AS CHAR)` on
MySQL), matched with a case-insensitive `LIKE '%word%'`. One script outside the repository created one table per
column type, wrote the same values, and read two things per value: the cast text, taken from the statement the
library compiles for the column, and which rows a column search through the library (`SQLTable.fetch_sync`) found
for each of 20 words.

- SQLite 3.53.1 (Python 3.14.7), PostgreSQL 18.7 and MySQL 8.4.13 (CI's image digests, on aarch64; psycopg 3 and
  PyMySQL): 2026-11-23, run locally.
- SQL Server 2022 (RTM-CU28, 16.0.4325.4, ODBC Driver 18, database at compatibility level 160): 2026-11-24, CI run
  45305352267 of a throwaway branch (pull request #133, closed unmerged), job `sqlserver`. The culture and the cost
  came from the same branch's runs 45305839538 (it failed on an identity column and measured nothing new) and
  45306324153. Those branches' logs expire with the runs; the figures are quoted here.

The columns: a double precision float (`Float(53)`), a single precision one (`Float(24)`, which is `real` on
PostgreSQL and SQL Server and `FLOAT` on MySQL), `Numeric(10, 2)` and an integer. The values: 12.5, 17.0, 0.1,
1234567.0, 1e20, 1e-7, -3.25, 0.0, 123456789.12345679 and 1.5e300 for the floats (the first eight for single
precision); 12.5, 17, 0.1, 1234567, -3.25, 0, 12.50 and 99999999.99 for the decimal. Not measured: MariaDB, other
drivers, `DECIMAL` columns of other scales, and a `real` on SQLite and SQL Server beyond the values above.

## What a double column's cast text is

| Stored value | Python's `repr` | SQLite | PostgreSQL | MySQL | SQL Server |
|---|---|---|---|---|---|
| 12.5 | 12.5 | 12.5 | 12.5 | 12.5 | 12.5 |
| 17.0 | 17.0 | 17.0 | 17 | 17 | 17 |
| 0.1 | 0.1 | 0.1 | 0.1 | 0.1 | 0.1 |
| 1234567.0 | 1234567.0 | 1234567.0 | 1234567 | 1234567 | **1.23457e+006** |
| 1e20 | 1e+20 | 1.0e+20 | 1e+20 | 1e20 | 1e+020 |
| 1e-7 | 1e-07 | 1.0e-07 | 1e-07 | 0.0000001 | 1e-007 |
| -3.25 | -3.25 | -3.25 | -3.25 | -3.25 | -3.25 |
| 0.0 | 0.0 | 0.0 | 0 | 0 | 0 |
| 123456789.12345679 | same | same | same | same | **1.23457e+008** |
| 1.5e300 | 1.5e+300 | 1.5e+300 | 1.5e+300 | 1.5e300 | 1.5e+300 |

SQLite, PostgreSQL and MySQL write every significant digit and differ in form only. SQL Server's `CAST` keeps six
significant digits and writes an exponent beyond them, so a search for `1234567` finds no row holding 1234567, and
a search for `e+`, `0` or `1e` finds the rows by their exponent text.

A single precision column: SQLite has one `REAL` type, so it is the double's. SQL Server has the same six digits
(the stored value is the single one: 0.1 reads back as 0.10000000149011612). On PostgreSQL 1234567 is
`1.234567e+06` (the other values as for a double), so a search for `1234567` finds nothing there. On MySQL the cast
text of 1234567 is `1234570`, which is also what the driver returns: what the column holds is not known.

## What a decimal and an integer column's cast text is

`Numeric(10, 2)`: PostgreSQL, MySQL and SQL Server keep the scale (`12.50`, `17.00`, `0.00`, `1234567.00`); SQLite
stores a decimal as a float, so it writes `12.5`, `17`, `0`, `1234567`, and a search for `17.0` or `12.50` finds
nothing there and the row on the other three. An integer is its digits everywhere.

## What the searches find (double column)

| Word | SQLite | PostgreSQL | MySQL | SQL Server |
|---|---|---|---|---|
| `17` | 17.0 | 17.0 | 17.0 | 17.0 |
| `17.0` | 17.0 | none | none | none |
| `12.5` | 12.5 | 12.5 | 12.5 | 12.5 |
| `1234567` | 1234567 and 123456789.1 | the same | the same | **none** |
| `0.0` | 0.0 | none | **1e-7** (text `0.0000001`) | none |
| `1e20` | none | none | 1e20 | none |
| `1e-07` | none | 1e-7 | none | none |
| `e+` | 1e20, 1.5e300 | the same | none | 1e20, 1.5e300 and the two long numbers |

## FORMAT on SQL Server

`FORMAT(x, 'R')` writes the shortest text that reads back as the same double: `12.5`, `17`, `0.1`, `1234567`,
`1E+20`, `1E-07`, `-3.25`, `0`, `123456789.12345679`, `1.5E+300`, the same for single precision (`0.1`, not
`0.100000001`). Rejected: `FORMAT(x, 'G17')` writes 17 digits (`0.10000000000000001`), `CONVERT(VARCHAR, x, 3)` the
same in exponent form (`1.2500000000000000e+001`), and a cast through `DECIMAL(38, 10)` fails on 1.5e300 with error
22003.

The culture: the session's language was `us_english`. `FORMAT(x, 'R')` and `FORMAT(x, 'R', 'en-US')` gave the same
text. `'de-DE'` and `'fr-FR'` write the decimal comma (`12,5`, `-3,25`, `123456789,12345679`). Whether another
session language changes what `FORMAT(x, 'R')` writes without the argument was not run, so the culture is named and
cannot change it. `'invariant'` is refused (error 42000). So `'en-US'` is named.

The cost: 200,000 rows of a `Float(53)` column, `COUNT(*)` with `LIKE '%7%'`, the least of 3 runs. `CAST(v AS
VARCHAR(max))`: 0.212 s, 85,012 rows. `FORMAT(v, 'R', 'en-US')`: 0.857 s, 104,188 rows. About four times on a scan,
which a cast already is: a leading wildcard over an expression uses no index with either. The rows differ because
the cast loses digits.

## The decision

- The first sentence of PRD-F23 is the owner's decision of 2026-11-24: a whole number as its digits, any other number
  as the database spells it. A spelling is not made uniform: it has no use (the search `17` finds 17.0 everywhere,
  as a substring), and a uniform form needs a different expression on each engine.
- What the library adds is the one promise that matters to a user, and only where a database breaks it: **a search for
  a number as the table shows it finds its row**, on a double precision or a decimal column. SQL Server breaks it
  for a float of more than six digits, so a column of a float type (`Float`, `REAL`, `DOUBLE`, also under a
  `TypeDecorator`) is cast there by `FORMAT(x, 'R', 'en-US')`. Every other database, and every other column type,
  keeps its own `CAST`.
- PostgreSQL's single precision `real` is not covered: its text writes 1234567 with an exponent. The adopter declares
  double precision, which `docs/databases.md` says. Fixing it would add a cast to `float8` on PostgreSQL; it can be
  added later without a change of schema.
- The test pins the double and the decimal column only (SPEC-F23.4): a test of single precision would freeze
  PostgreSQL's miss.
- Not done: a uniform text, and a `DECIMAL` cast. A request that searches a float column on SQL Server pays the
  four times on each statement that holds the search; a request without such a search does not use the expression.
