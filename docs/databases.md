# What each database does on its own

The library does not change how a database compares values. Where a rule of one engine changes what a user
sees, it is written down here, by engine, and so are the library's own limits on an engine, each marked as
such. An entry says what the adopter sees, gives an example, says how it is known, and names the requirement of
`docs/PRD.md` it touches. Where the library states the full rule, it stays in [rules.md](rules.md) or in the PRD,
and the entry gives one line and a link. How an entry is known is one of:

- **MEASURED:** a run of this project. It names the date, the version and the CI run or the decisions file that
  holds the numbers, or the test that pins it and the CI job that runs that test.
- **documented, not run:** the vendor's documentation says so (its URL is given) and no run of this project checked it.
- **READ:** read in this project's code (file and line), not run on a server.
- **unknown:** nobody has measured it or found a source for it; it is listed so that nobody assumes.

PRD section 5 lists the same differences from the requirements' side. Each of its differences that names an engine
or an OpenSearch setting has an entry below.

## PostgreSQL

### An index in the default order does not serve a sort on a column that can hold NULL

- **What you see:** the library orders NULL first ascending on every backend. PostgreSQL puts it last ascending, so
  for a column that can hold NULL the library writes `NULLS FIRST` (`NULLS LAST` descending), and a B-tree index in
  the default order cannot serve that sort: declare the index `NULLS FIRST`. A column declared `nullable=False`
  keeps its plain index. The rule is in [Columns and ordering](rules.md#columns-and-ordering).
- **Example:** `ORDER BY age NULLS FIRST` on a large table is sorted, not read from `CREATE INDEX ON people (age)`;
  `CREATE INDEX ON people (age NULLS FIRST)` can serve it, and the descending order too.
- **How it is known:** the clause the library writes is pinned by `tests/test_sql.py`
  (`test_null_placement_is_written_only_where_the_database_needs_it`), which CI's `postgresql` and
  `postgresql-psycopg2-asyncpg` jobs run. The index is documented, not run: PostgreSQL's manual says a B-tree index is
  stored ascending with nulls last by default, and that one stored ascending with nulls first serves `ASC NULLS FIRST`
  and `DESC NULLS LAST` (https://www.postgresql.org/docs/current/indexes-ordering.html, opened 2026-11-23).
- **Requirement:** PRD-O2.

### A `real` column writes 1234567 with an exponent in its search text

- **What you see:** a single-precision column (`real`, SQLAlchemy's `Float(24)`) writes 1234567 as `1.234567e+06`
  in its search text, so the table shows 1234567 and a search for `1234567` finds nothing. A double precision
  column writes it as `1234567`. Declare the column double precision (`Float(53)`, `DOUBLE_PRECISION`).
- **Example:** a `real` column holding 1234567: the column search `1234567` finds no row, and `1.234567` finds it.
- **How it is known:** MEASURED 2026-11-23 on PostgreSQL 18.7
  (`docs/decisions/006-number-search-text.md`). The library does not pin it: its test holds a double precision and
  a decimal column only.
- **Requirement:** PRD-F23.

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

### A `DATETIME` keeps no offset, and a `TIMESTAMP` gives the UTC day only in a UTC session

- **What you see:** a time-zone-aware column on MySQL is read as UTC when it is a `DATETIME`, which stores no offset, so
  the application must write UTC. A `TIMESTAMP` is stored as UTC and read back in the session's `time_zone`, so it gives
  the UTC day only when that is `+00:00`. PostgreSQL and SQL Server store the instant. The rule is in
  [Types and matching](rules.md#types-and-matching).
- **Example:** a `TIMESTAMP` stored as 23:30 UTC on the 15th is 01:30 on the 16th in a session at `+02:00`, so a
  search for the 15th can miss it there.
- **How it is known:** MySQL's manual says it converts `TIMESTAMP` values to UTC for storage and back to the current
  time zone for retrieval, and does not for `DATETIME`
  (https://dev.mysql.com/doc/refman/8.4/en/datetime.html, opened 2026-11-23). That the library's day follows the
  session zone is MEASURED on MySQL and on MariaDB by `tests/test_sql.py`,
  `test_a_mysql_timestamp_is_read_through_the_session_time_zone` (SPEC-F21.12): a value stored at 23:30 UTC on the 15th
  is found by the 16th in a session at `+02:00` and by the 15th in a UTC one. CI's other MySQL tests run the session and
  Python in UTC, so `test_a_timezone_aware_column_compares_by_utc_day` shows only that the column works.
- **Requirement:** PRD-F21.

## MariaDB

### The default collation ignores trailing spaces and accents

- **What you see:** MariaDB 12.3's default collation, `utf8mb4_uca1400_ai_ci`, is PAD SPACE and accent-insensitive,
  unlike MySQL 8.4's default (`utf8mb4_0900_ai_ci`, NO PAD). So `=`, a categorical filter and a `multi_values` list
  treat `'a '` and `'a'` as the same value, a value of spaces only matches the empty string, and `apfel` finds `Äpfel`
  ([What a database folds beyond ASCII letters and accents](#what-a-database-folds-beyond-ascii-letters-and-accents)).
  A column under a `_nopad_` collation (`utf8mb4_uca1400_nopad_ai_ci`) compares as MySQL's default does: documented,
  not run.
- **Example:** `multi_values: [" "]` on a column holding `''` finds that row.
- **How it is known:** MEASURED 2026-11-24 on MariaDB 12.3.3 (image digest pinned in CI's `mariadb` job): the server's
  `@@collation_server` is `utf8mb4_uca1400_ai_ci`, and `information_schema.COLLATIONS` lists `uca1400_ai_ci` as
  `PAD SPACE` and `uca1400_nopad_ai_ci` as `NO PAD`. Pinned by `tests/test_sql.py`
  (`test_white_space_is_a_value_on_a_string_column`, `test_the_library_folds_no_accent_the_collation_does_not`).
- **Requirement:** PRD-F20, PRD-F26.

### The dialect name is the URL's

- **What you see:** SQLAlchemy names the dialect `mariadb` only for a `mariadb+` URL (`mariadb+pymysql://`,
  `mariadb+aiomysql://`); a `mysql+` URL against a MariaDB server keeps the name `mysql`. The library's MariaDB
  branches (its NULL ordering and its day text) follow the name, so name the server in the URL.
- **Example:** `mysql+pymysql://` against MariaDB takes the MySQL branches.
- **How it is known:** the `mariadb+` URLs MEASURED 2026-11-24 (the dialect name read in a local run against 12.3.3,
  CI's `mariadb` job); a `mysql+` URL against a MariaDB server: READ in SQLAlchemy's `_mariadb_shim`, not run.
- **Requirement:** PRD-Q2.

## SQL Server

### An exact match ignores trailing spaces

- **What you see:** `=`, a categorical filter and a `multi_values` list treat `'a '` and `'a'` as the same value,
  as SQL Server compares strings, and a value of spaces only matches the empty string.
- **Example:** `multi_values: [" "]` on a column holding `''` finds that row. PostgreSQL and SQLite find none.
- **How it is known:** MEASURED 2026-11-22 on SQL Server 2022
  (`docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md`).
- **Requirement:** PRD-F20, PRD-F26.

### A statement of more than 2,097 parameters is refused

- **What you see:** SQL Server 2022 with ODBC Driver 18 answered a statement of 2,097 bound parameters and refused
  2,098 (error 8003 from the server). The budget of PRD-C17 keeps the largest statement of a request, the page's,
  at 1,538 parameters (two for each day of a list on a column that may hold a time and for each range, one for every
  other condition, and the page's offset and limit), which leaves 559 for a `base_filter`, a `scope` and for expression
  columns. Those bind in all three statements (the total, the filtered count and the page) and are stated here, not
  budgeted: a `base_filter` and a `scope` that together bind more than 559 parameters can make a request at the budget
  fail on SQL Server.
- **Example:** the global search `ann` over 2,098 columns binds 2,098 parameters in the filtered count and is refused;
  over 2,097 columns the count is answered and its page, which binds two more, is refused.
- **How it is known:** MEASURED 2026-11-22 on SQL Server 2022 with ODBC Driver 18 (CI runs 45083578654 and
  45083913250, `docs/decisions/005-the-largest-request-the-bounds-allow.md`).
- **Requirement:** PRD-C17.

### A decimal holds 38 digits at most

- **What you see:** a number of more than 38 digits in all, before and after the decimal point (a fraction's trailing
  zeros not counted), on a `Numeric` column is answered with `invalid_number`, whatever precision the column declares.
  No other database bounds such a column.
- **Example:** `=` with a whole number of 39 digits, or `1e39`, or `0.` and 39 ones, on a `Numeric` column:
  `invalid_number` on SQL Server, no rows on PostgreSQL, MySQL and SQLite.
- **How it is known:** MEASURED 2026-11-22 (whole numbers) and 2026-11-24 (numbers with a point) on SQL Server 2022
  with Microsoft's ODBC Driver 18, which refuses to send a longer decimal with `HY104`
  (`docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md`,
  `docs/decisions/010-decimal-numbers.md`); the boundary of 38 is pinned by `tests/test_sql.py`.
- **Requirement:** PRD-F25.

### A float's search text is written by `FORMAT`, which costs about four times a cast

- **What you see:** SQL Server's `CAST` writes a float with six digits (`1.23457e+006` for 1234567), which a search
  for the number the table shows would miss. The library searches a float column (`Float`, `REAL`, `DOUBLE`) by
  `FORMAT(x, 'R', 'en-US')` instead, which writes the shortest text that reads back as the same number. On a full
  scan that costs about four times a cast. A decimal and an integer column keep their `CAST`.
- **Example:** 200,000 rows of a `Float(53)` column, `LIKE '%7%'`: 0.21 s by `CAST`, 0.86 s by
  `FORMAT(..., 'R', 'en-US')`.
- **How it is known:** MEASURED 2026-11-24 on SQL Server 2022 (16.0.4325.4), CI runs 45305352267 (the texts) and
  45306324153 (the cost, the least of 3 runs; the culture) (`docs/decisions/006-number-search-text.md`).
- **Requirement:** PRD-F23.

### A database at a lower compatibility level is not tested

- **What you see:** CI runs SQL Server 2022 on a database at compatibility level 160, the level a new database gets
  from the server (the workflow sets none), and a database at a lower level is not run. The SQL the library writes
  only for SQL Server is three functions: `SWITCHOFFSET(..., '+00:00')` to give a time-zone-aware column's UTC time,
  `CONVERT(VARCHAR(10), ..., 23)` for its day text and `FORMAT(..., 'R', 'en-US')` for a float's search text. None
  is tied to a level that Microsoft's pages name (`FORMAT`'s page was not opened). The SQL that SQLAlchemy's SQL
  Server dialect writes (the page's offset and limit, for one) is SQLAlchemy's, and its needs are not checked here:
  unknown.
- **Example:** a database set to `COMPATIBILITY_LEVEL = 120` is the case no run of this project covers.
- **How it is known:** the level is MEASURED 2026-11-24 on SQL Server 2022 (16.0.4325.4), CI run 45305352267
  (`docs/decisions/006-number-search-text.md`). The functions are READ in `src/datatables_backend/sqlalchemy.py:637`,
  `:659` and `:692`, and the job in `.github/workflows/ci.yml` (job `sqlserver`, no level set). Documented, not run:
  the pages for [SWITCHOFFSET](https://learn.microsoft.com/en-us/sql/t-sql/functions/switchoffset-transact-sql) and
  [CAST and CONVERT](https://learn.microsoft.com/en-us/sql/t-sql/functions/cast-and-convert-transact-sql) (both opened
  2026-11-23) name no compatibility level for `SWITCHOFFSET` or for style 23. The second names levels only for the
  default style of `time` and `datetime2` (110).
- **Requirement:** PRD-F21, PRD-F42, PRD-F23.

## SQLite

### A search folds ASCII letters only

- **What you see:** a search, and an exact match, ignore case for the letters A to Z only; `ä` does not find `Ä` on
  SQLite, where PostgreSQL (in a UTF-8 locale), MySQL and SQL Server find it. The rule is in
  [Types and matching](rules.md#types-and-matching), and the other engines are in the table of
  [What a database folds beyond ASCII](#what-a-database-folds-beyond-ascii-letters-and-accents).
- **Example:** a column holding `Äpfel`: the global search `äpfel` finds no row on SQLite, and `ÄPFEL` finds it.
- **How it is known:** the global search is pinned by `tests/test_sql.py`
  (`test_the_global_search_finds_a_capital_beyond_ascii_by_its_own_case_and_a_small_one_where_the_database_folds`),
  which CI's `tests` job runs on SQLite. An exact match is READ (`sqlalchemy.py:304` and `:551-553`: `lower(column) =
  lower(value)`), and SQLite's manual says `lower()` converts ASCII characters only
  (https://www.sqlite.org/lang_corefunc.html, opened 2026-11-23): documented, not run for the exact match.
- **Requirement:** PRD-F9.

### A time-zone-aware column keeps no offset

- **What you see:** SQLite stores the text it is given, so a value written with an offset is read back without it, as
  UTC. The application must write UTC. The rule is in [Types and matching](rules.md#types-and-matching).
- **Example:** `2026-07-16 01:00:00+02:00` (23:00 UTC on the 15th) is read as 01:00 UTC on the 16th: `=` `2026-07-16`
  finds it and `=` `2026-07-15` does not.
- **How it is known:** pinned by `tests/test_sql.py`
  (`test_an_aware_value_written_with_an_offset_is_read_without_it_on_sqlite`), which CI's `tests` job runs.
- **Requirement:** PRD-F21.

### A `Numeric` column is not compared exactly

- **What you see:** a whole number outside the signed 64-bit range can match a neighbouring value on a `Numeric`
  column. SQLite stores no exact decimal, and SQLAlchemy sends a decimal to it as a floating-point number.
  PostgreSQL, MySQL and SQL Server compare the number exactly.
- **Example:** a `Numeric(30, 0)` column holding 18446744073709551616 (2^64): `=` 18446744073709551617 finds that
  row on SQLite and no row on the other three.
- **How it is known:** MEASURED 2026-11-22 on SQLite 3.53.1, PostgreSQL 18.7, MySQL 8.4.13 and SQL Server 2022
  (`docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md`).
- **Requirement:** PRD-F25.

### An expression deeper than 1,000 is refused

- **What you see:** SQLite refuses a statement whose expression is deeper than 1,000 ("Expression tree is too large
  (maximum depth 1000)"). SQLite reads a chain of conditions joined by AND or OR from the left, so each adds a level.
  Across the shapes of a request measured (a chain of words, the global search over many columns, negated words,
  criteria, nested groups, lists of days), the depth was at most 9 above the number of conditions, so the budget of
  960 conditions stays inside it. A `base_filter`, a `scope` or an expression column that is deep adds levels of its
  own, stated here, not budgeted. A SQLite build may set another depth (`SQLITE_MAX_EXPR_DEPTH`): documented, not run.
- **Example:** a negated word of the global search over 991 columns is answered and over 992 is refused.
- **How it is known:** MEASURED 2026-11-22 on SQLite 3.53.1 with Python 3.14.7, the build limit 1,000 read from the
  error text (`docs/decisions/005-the-largest-request-the-bounds-allow.md`).
- **Requirement:** PRD-C17.

## Every SQL database

### What a database folds beyond ASCII letters and accents

- **What you see:** the library asks the database to ignore case and adds no folding or collation of its own, so what
  `äpfel` and `apfel` find in a column holding `Äpfel` is the engine's. The same letter in its own other case
  (`ÄPFEL`) is found everywhere. The rule is in [Types and matching](rules.md#types-and-matching).

  | Engine | `äpfel` finds `Äpfel` | `apfel` finds `Äpfel` |
  |---|---|---|
  | SQLite | no: ASCII letters only | no |
  | PostgreSQL | yes in a UTF-8 locale; in a `C` or `POSIX` locale: READ, not run | no |
  | MySQL 8.4 | yes | yes under `utf8mb4_0900_ai_ci` |
  | SQL Server 2022 | yes | no under `Latin1_General_CI_AS` |
  | MariaDB 12.3 | yes | yes under `utf8mb4_uca1400_ai_ci` |
  | any other collation of MySQL, MariaDB or SQL Server | unknown | unknown |

  Whether the dotted and the dotless i match is unknown on every engine: no test reads it.
- **Example:** a MySQL column under `utf8mb4_0900_ai_ci` holding `Äpfel` is found by the global search `apfel`; the
  same column on PostgreSQL, SQLite or SQL Server is not.
- **How it is known:** the second column is pinned by `tests/test_sql.py`
  (`test_the_global_search_finds_a_capital_beyond_ascii_by_its_own_case_and_a_small_one_where_the_database_folds`)
  and the third by `test_the_library_folds_no_accent_the_collation_does_not`, which CI's `tests` (SQLite),
  `postgresql`, `mysql`, `mariadb` and `sqlserver` jobs run. The tests declare the collation of the MySQL, MariaDB
  and SQL Server column as the table says and keep the default of SQLite and PostgreSQL (the image's `en_US.utf8`);
  the `C` locale is read in the helper `folds_letters_beyond_ascii` and is not run.
- **Requirement:** PRD-F9, PRD-F10.

### An exact match on a column with a type of its own, or on an untyped expression, is not folded

- **What you see:** the library folds an exact match only on a column whose SQL type is a string type or an `Enum`. A
  `TypeDecorator`, `Uuid` and an expression declared without a type are compared as sent, as the database compares
  them. The rule, and how to give an expression a string type, are in
  [Types and matching](rules.md#types-and-matching).
- **Example:** `upper(kind)` over a column with a case-sensitive collation, declared without a type: `=` `rEd` finds no
  row holding `RED`; given `String`, it finds them.
- **How it is known:** pinned by `tests/test_sql.py`
  (`test_an_exact_match_on_an_expression_ignores_case_once_it_has_a_string_type` and
  `test_a_column_with_a_type_of_its_own_is_compared_as_its_type_compares`), which every SQL job of CI runs. The
  tests declare a case-sensitive collation on each engine.
- **Requirement:** PRD-F9.

### A number's search text is the database's own spelling of it

- **What you see:** the table shows a float in its shortest form, and a search for that finds the row on a double
  precision and a decimal column on every engine. The other spellings are each engine's own: `17.0` for a whole
  float on SQLite only, an exponent as `1.0e+20`, `1e+20`, `1e20` or `1E+20`, `0.0000001` for 1e-7 on MySQL, and a
  decimal's trailing zeros (`12.50`), which SQLite drops. A search for `17.0` finds the float 17.0 on SQLite and a
  decimal 17.00 elsewhere. A single precision `real` is not covered on PostgreSQL (see its entry). The rule is in
  [Types and matching](rules.md#types-and-matching).
- **Example:** a `Numeric(10, 2)` column holding 12.50: the search `12.50` finds it on PostgreSQL, MySQL and SQL
  Server and not on SQLite, which writes `12.5`; the search `12.5` finds it on all four.
- **How it is known:** MEASURED 2026-11-23 on SQLite 3.53.1, PostgreSQL 18.7 and MySQL 8.4.13, and 2026-11-24 on
  SQL Server 2022 (`docs/decisions/006-number-search-text.md`); the double and the decimal case are pinned by
  `tests/test_sql.py` (`test_a_number_as_the_table_shows_it_finds_its_row`), which every SQL job of CI runs.
- **Requirement:** PRD-F23.

### `= 1e-400` finds a row holding zero on MySQL and MariaDB

This is a limit of the databases, not of the library: the library sends the number exactly.

- **What you see:** a number with more digits after the decimal point than a MySQL or MariaDB `DECIMAL` holds (30)
  is compared as a floating-point number there (INFERRED), so a number such as `1e-400` equals zero and `=` finds a
  row holding 0.00 on a `Numeric` column. PostgreSQL answers exactly (no row), and SQL Server refuses the number (more
  than 38 digits). On SQLite, on a `Float` column and on OpenSearch the number is a float, which is zero there too.
- **Example:** a `Numeric(20, 2)` column holding 0.00: `= 1e-400` finds it on MySQL 8.4 and MariaDB 12.3.
- **How it is known:** only `= 1e-400` was MEASURED, on 2026-11-24 on MySQL 8.4.13 (PyMySQL and mysqlclient) and
  MariaDB 12.3.3 (`docs/decisions/010-decimal-numbers.md`); the rule is INFERRED from MySQL's manual, which gives a
  `DECIMAL` at most 30 digits after the point (<https://dev.mysql.com/doc/refman/8.4/en/numeric-type-syntax.html>,
  fetched 2026-11-24).
- **Requirement:** PRD-F25.

### A fraction on a `TypeDecorator` over an integer is compared as a float

This is a limit of the library: such a type converts what it is given, and a number with a fraction has no whole
number to give it.

- **What you see:** a number with a fraction (`> 7.5`) on a column whose SQLAlchemy type is a `TypeDecorator` over an
  integer is compared as a float, without the decorator's conversion. Bound as the decorator's type, psycopg 3 and
  asyncpg would cast it to the impl's `INTEGER`, which rounds it (`> 7.5` would miss 8).
- **Example:** a decorator over `Integer` holding 8: `> 7.5` finds it, `= 7.5` finds nothing.
- **How it is known:** RUN 2026-11-24 as a compile: psycopg 3 renders `col > %(p)s::INTEGER` and asyncpg
  `col > $1::INTEGER` for the decorator's own bind, and no cast for a float bound as `Float`; the rounding of
  PostgreSQL's cast is INFERRED, and CI's psycopg 3 leg shows it (`tests/test_sql.py`,
  `test_a_fraction_on_a_converting_integer_type_is_compared_as_a_float`).
- **Requirement:** PRD-F25.

### A shared `TotalCache` cannot tell two scopes apart that the session sets

This is a limit of the library, not a rule of the databases: the cache keeps a total under the database's URL and
the count statement with its bound values, and it sees nothing else.

- **What you see:** two requests whose rows differ only by the state of their database session share one cached
  `recordsTotal`, so one scope can be shown the total of another. That state is row-level security or `SET ROLE`
  on PostgreSQL, a tenant per schema through `search_path` or SQLAlchemy's `schema_translate_map`, and `USE` of
  another database on MySQL or SQL Server. A scope written into the source, the base filter or the `scope` of `fetch`
  is told apart. Give each such scope a `TotalCache` of its own.
- **Example:** one engine, one `SQLTable` with a cache, and `SET ROLE` per request with a row-level security
  policy: the second role is answered the first role's total until the TTL ends.
- **How it is known:** read in the code (the key is built from the URL and the compiled count); not run on a
  server.
- **Requirement:** PRD-C15.

## OpenSearch

### A `text+keyword` unique key skips long values

- **What you see:** the `.keyword` subfield that dynamic mapping adds to a string has `ignore_above: 256`, so a
  document whose value is longer than 256 characters has no value in it. As a `unique_key` that document sorts as if
  it had none, with every other document that lacks one, and the tie-breaker no longer tells them apart.
- **What to do:** declare the key `IndexColumn(..., text='keyword')` (a `keyword` field has no such limit by default),
  or use a number.
- **How it is known:** documented, not run. OpenSearch's page on `ignore_above` says a string longer than the limit is
  not indexed and so is not in the index or doc values, and stays in `_source`
  (https://docs.opensearch.org/latest/mappings/mapping-parameters/ignore-above/, opened 2026-11-23). That dynamic
  mapping sets the limit to 256 on the `.keyword` subfield is from the OpenSearch documentation of dynamic mapping,
  whose page was not opened for this entry.
- **Requirement:** PRD-D11.

### Sorting on the document `_id` is deprecated

- **What you see:** a sort on `_id` is accepted on a default cluster (`indices.id_field_data.enabled` is `true`),
  `desc` is the exact reverse of `asc`, and after it the nodes report field data held for `_id` (300 bytes on a
  5-document index, none before). The first such sort logs, on both versions: `[DEPRECATION][o.o.d.i.m.IdFieldMapper]
  Loading the fielddata on the _id field is deprecated and will be removed in future versions. If you require sorting
  or aggregating on this field you should also include the id in the body of your documents, and map this field as a
  keyword field that has [doc_values] enabled`. So the library does not let `_id` be the `unique_key`, nor a column
  be ordered by it: a public name must not rest on a feature the current release says it will remove.
- **What to do:** copy the id into a `keyword` field of the documents at ingest (OpenSearch's own advice) and name that
  column as the key. A declared `Column('_id')` still shows the id (it reads `hit['_id']`, which needs no field data)
  but is neither searchable nor orderable.
- **How it is known:** MEASURED on OpenSearch 3.10.0 and 2.19.7 on a CI index of 5 documents, recorded with #96
  (commit 557168a, 2026-11-23; no CI run id was kept): the default setting, the accepted sort and its exact reverse,
  the 300 bytes of field data, and the deprecation line in the server's log.
- **Requirement:** PRD-D11.

### A field of several values sorts by another value in each direction

- **What you see:** OpenSearch sorts a document whose sort field holds several values by its least value when the sort
  is ascending and by its greatest when it is descending (the default sort `mode`). So for such a field a descending
  order is not the exact reverse of the ascending one (PRD-O3 holds for fields of one value). A reversed last page
  (PRD-C10) names the `mode` of the direction it flips, so it shows the rows the ordinary page shows at that position.
- **Example:** in the live index of the tests, `label` holds several values in two documents. By their least values the
  ascending order is c3, d4, a1, e5, b2; by their greatest values the descending order is d4, a1, b2, e5, c3, not
  b2, e5, a1, d4, c3.
- **How it is known:** READ, not observed: derived from the test data and the documented default `mode`. Without the
  `mode`, `label` ascending at `start` 2, `length` 2 would show b2, a1 instead of a1, e5. The live test of SPEC-C10.3
  compares reversed pages with ordinary ones for keyword, `text+keyword`, integer, date and boolean sort fields on
  both servers (CI's `opensearch` job).
- **Requirement:** PRD-C10, PRD-O3.

### A shared `TotalCache` cannot tell two clusters or two users apart

This is a limit of the library, not of OpenSearch: the cache keeps a total under the index's name and the base
filter, and it sees nothing else.

- **What you see:** two clusters that hold an index of the same name, and two users whose documents differ by
  document-level security, share one cached `recordsTotal`. Give each cluster, and each such user or role, a
  `TotalCache` of its own.
- **Example:** one `SearchTable` over `docs` with a cache, used with the clients of two clusters: the second
  cluster is answered the first cluster's total until the TTL ends.
- **How it is known:** read in the code (the key is built from the index name, the base filter and the request's
  scope); not run on a server.
- **Requirement:** PRD-C15.

### A number outside the range of an undeclared field fails the request

This is a limit of the library, not of OpenSearch, which says clearly what it refuses. Declare the field's kind and
it goes away (PRD-D23).

- **What you see:** a number that the field's own mapping cannot hold (`integer`, `long`, `unsigned_long`, `float`,
  `half_float`) is refused by the server with a 400, which reaches the application as the client's `RequestError`, not
  as `invalid_number`. The library does not read the mapping, so a column that declares no kind cannot know the range.
  `IndexColumn('pages', type='number', number='integer')` makes the library answer `invalid_number` before the query,
  with the range in the message and never the value. A `double` field takes every number of at most 255 characters,
  which is what every request holds, and so does a `scaled_float`.
- **Two answers are wrong without an error:** `-1` in a list (`multi_values`, ColumnControl's list) on an
  `unsigned_long` field finds the document holding 2^64-1, which OpenSearch reads it as; and a number beyond a
  `scaled_float`'s range finds the document holding its largest value. A declared `unsigned_long` refuses the first; a
  `scaled_float` has no kind, since nothing fails.
- **`byte` and `short`:** OpenSearch refuses them only beyond an `integer`'s range and answers 128 on a `byte`, so a
  declared `byte` or `short` carries the `integer` bounds. SQL bounds a `SmallInteger` by 16 bits (PRD-F25) and refuses
  40000; OpenSearch answers 40000 on a `short`, and the library follows each engine.
- **Example:** `=` 18446744073709551616 on a column over an `integer` or a `long` field.
- **How it is known:** MEASURED 2026-11-22 and 2026-11-25 on OpenSearch 3.10.0 and 2.19.7
  (`docs/decisions/003-opensearch-number-ranges-and-fullwidth-marks.md`,
  `docs/decisions/011-opensearch-number-kinds.md`).
- **Requirement:** PRD-F25, PRD-E7, PRD-D23.

### A `double`, `float` or `half_float` field keeps -0.0 apart from 0.0

This is a rule of OpenSearch. The library sends `0`, as it does for `0`, for every number whose double is zero: one the
user writes as zero with a sign or a point (`-0.00`), and one too close to zero for a double (`-1e-400`, `-2e-324`
round to -0.0, as `1e-400` rounds to zero: "`= 1e-400` finds a row holding zero on MySQL and MariaDB" above). On a
column declared `float` or `half_float` (`IndexColumn(number=...)`) it sends `0` for a number that rounds to zero in
that format too (`-1e-50` on a `float` column, `-1e-10` on a `half_float` one, which are no zero as a double). A column
over such a field that declares no kind still sends the number, which the field reads as -0.0, so `= -1e-50` there
misses a document holding 0.0: the library does not read the mapping.

- **What you see:** on a `double` or a `float` field (and on a `half_float` one, RUN by CI below), -0.0 and 0.0 are
  two values in a `term`, `terms` and `range` query: `{"term": {"m": -0.0}}` does not find a document holding 0.0, and
  neither does `{"range": {"m": {"gte": -0.0, "lte": -0.0}}}`; a body sent with 0.0 finds it. A document holding -0.0
  is found by a body of -0.0, which the library sends for no number a user writes, and not by `= 0`, `= -0.00` or
  `= -1e-400`; `< 0` and `<= 0` find it,
  `>= 0` does not. A `long` or an `integer` field reads -0.0 as 0. That -0.0 sorts below 0.0 is INFERRED from the range
  results, not measured as an order. Sending `0` also changes what `-1e-400` finds: `=`, a column search and a list
  entry no longer find a document holding -0.0, and `<`, `>`, `>=`, `!=`, `between` and `!between` now answer as for
  `0`: `>` and `!=` no longer find a document holding 0.0, `>=` no longer finds one holding -0.0, `<` and `!=` now
  find one holding -0.0, and a range follows its bounds as `0` gives them.
- **Example:** a `double` field holding 0.0: `= -0.00`, `<= -0.00`, `= -1e-400` and `<= -1e-400` find it (they found
  nothing while the library sent -0.0, and `>` and `!=` with either found it, which they no longer do); a `double`
  field holding -0.0: `= 0` finds nothing.
- **How it is known:** MEASURED 2026-11-25 on OpenSearch 3.10.0 (Lucene 10.6.0) on a scratch index holding 0.0 and
  -0.0 in a `double` and a `float` field: `term` -0.0 finds only -0.0 and `term` 0.0 and 0 only 0.0, on both fields;
  `range` `gte`/`lte` -0.0 only -0.0, `gte`/`lte` 0.0 only 0.0, `gt` -0.0 and `gte` 0.0 not -0.0, `lt` 0.0 and `lte`
  -0.0 -0.0 and not 0.0; on a `long` and an `integer` field holding 0, `term` -0.0 and `range` `gte`/`lte` -0.0 find
  it. RUN 2026-11-25 on 2.19.7, before the library sent `0` for `-1e-400`: `= -1e-400` and `<= -1e-400` missed a 0.0
  on a `double` field, and `= -1e-400` found a stored -0.0. `tests/test_opensearch_live.py`
  (`test_minus_zero_finds_a_zero_on_a_double_field`) fails for `=` and `<=` without the library's zero and passes with
  it, for `-0.00`; `-1e-400` now sends the same body for `=`
  (`test_a_number_whose_double_is_zero_sends_the_body_of_zero`), so its result follows from that run, and for `<=` that is INFERRED from the shared code. The results for `<`, `>`,
  `>=`, `!=`, `between` and `!between` are INFERRED from the `term` and `range` measurements above. CI's `opensearch`
  job runs both live tests on 3.10 and 2.19. RUN by CI
  (2026-11-26 UTC) on 3.10.0 and 2.19.7: before the library sent `0` for them, `= -1e-50` and `<= -1e-50` on a `float`
  field and `= -1e-10` and `<= -1e-10` on a `half_float` field, each against a document holding 0.0, found nothing
  (`test_a_number_that_rounds_to_zero_in_a_float_field_finds_a_zero`, then red); with the library sending `0` for them
  (`test_a_number_that_rounds_to_zero_in_a_float_field_sends_the_body_of_zero`) the same live test passed on both.
- **Requirement:** PRD-F25.

### A boolean field is a boolean column only when it is declared boolean

A column is a string column unless it is declared, because the library does not read the mapping (PRD-D8).

- **What you see:** a column over a `boolean` field, declared as a string or as `IndexColumn(..., text='keyword')`,
  matches nothing on `=` (a string column queries a `.keyword` subfield the field lacks) or, for a keyword column,
  fails the request with the client's `RequestError` for a term other than `true` or `false`.
- **What to do:** declare the column `Column('active', type='boolean')`. `1` and `0` are then read as `true` and
  `false`, and any other word is refused with `invalid_filter` (PRD-F43).
- **How it is known:** pinned by `tests/test_opensearch_live.py`, which CI's `opensearch` job runs on a live cluster.
- **Requirement:** PRD-D8, PRD-F43.

### A date or number field takes part in the global search unless it is declared

A column is a string column unless it is declared, because the library does not read the mapping (PRD-D8), and the
global search puts a string column's text on its field.

- **What you see:** a column over a `date` field, declared as a plain `Column('created')`, sends the search text to
  that field as a `match_phrase`. The server refuses it with a 400 (it failed to parse the date field), which reaches
  the application as the client's `RequestError`, for any global search text. A column over a number field declared
  the same way is expected to fail alike.
- **Example:** the global search `apple` over a table that declares `Column('created')` on a `date` field.
- **What to do:** declare every date and number column `Column('created', type='date')` or
  `Column('pages', type='number')`. A declared date or number column takes no part in the global search (see
  [The global search skips number and date columns](#the-global-search-skips-number-and-date-columns)).
- **How it is known:** date: pinned by `tests/test_opensearch_live.py` (an undeclared column over a `date` field raises
  `RequestError`; the declared case is `test_the_global_search`, whose table declares `created` a date), which CI's
  `opensearch` job runs on a live cluster; the same failure was seen on OpenSearch 3.10.0 in an adopter trial. The
  query the library sends was recorded: a `match_phrase` on the field. Number: read from the same query, not run.
- **Requirement:** PRD-D8, PRD-F32.

### A text field mapped without positions refuses a phrase

A word on analysed text is a `match_phrase`, which needs the token positions the default mapping stores.

- **What you see:** a `text` field mapped with `index_options` below `positions` (`docs` or `freqs`) makes the server
  refuse the search, which reaches the application as the client's `RequestError`.
- **Example:** a column over a `text` field with `"index_options": "freqs"`, searched for any word.
- **How it is known:** documented, not run, and not a default mapping: OpenSearch's page on `index_options` says
  `positions` is required for phrase queries, that `freqs` does not support them, and that a `text` field is indexed
  with `positions` by default (https://docs.opensearch.org/latest/mappings/mapping-parameters/index-options/, opened
  2026-11-23).
- **Requirement:** PRD-F11.

### A character that a normalizer folds to `*`, `?` or `\` can be searched as a wildcard

A keyword field with a normalizer that folds to ASCII (`asciifolding`) turns some characters into `*`, `?` or `\`,
in what it stores and in a search pattern, where they are then the pattern's own syntax. The library does not
read the mapping. It sends a substring search (the global search, a column search, `contains`, `starts`, `ends`
and their negations) with a fullwidth `＊` (U+FF0A), `？` (U+FF1F) or `＼` (U+FF3C) in a form the server reads as
that character, so it finds the rows holding the ASCII or the fullwidth character there. What is left is a limit
of the library.

- **What you see:**
  - A character the normalizer turns into several, as `⁇` (U+2047) into `??`, matches too much in a substring
    search. No form of query measured is right for it.
  - Another character that a normalizer folds to `*`, `?` or `\` is not covered: only the three above are.
  - A search with one of the three marks folds letters as a regular expression does, which from OpenSearch 3.1 on
    is a little more than other searches fold: `k` beside such a mark also finds the Kelvin sign. Not measured for
    these values; `docs/decisions/001-opensearch-case-folding-beyond-ascii.md` holds the measurement for others.
- **Example:** on such a field holding `axyb`, `a⁇b` and `a??b`, a search for `a⁇b` finds all three.
- **How it is known:** MEASURED 2026-11-22 on OpenSearch 3.10.0 and 2.19.7
  (`docs/decisions/003-opensearch-number-ranges-and-fullwidth-marks.md`); the three marks are also checked on
  both servers by the live tests. The second and third points are read in the code, not run on a server.
- **Requirement:** PRD-F9.

### OpenSearch's default limit of fields caps the searchable columns before the budget does

- **What you see:** an index takes 1,000 fields by default (`index.mapping.total_fields.limit`), and a text+keyword
  field is two of them (the field and its multi-field). A table of searchable columns on distinct fields therefore
  reaches that limit, about 500 text+keyword columns, before the budget of 960 conditions. The budget itself keeps the
  widest `bool` the library builds at 960 clauses, within the default `indices.query.bool.max_clause_count` of 1,024,
  which a server counts per `bool`.
- **Example:** the live test of the budget reads 960 columns that name one field, so it does not try the field limit.
- **How it is known:** the clause limit MEASURED 2026-11-22 on OpenSearch 3.10.0 and 2.19.7
  (`docs/decisions/005-the-largest-request-the-bounds-allow.md`); the field limit: documented, not run.
- **Requirement:** PRD-C17.

### OpenSearch 3.0 and 2.19 fold ASCII letters only, and 2.19 is slow on long exact matches

The library is built for OpenSearch 3.1 and later. CI runs the live tests on 3.10.0 and on 2.19.7, and they pass on
both. OpenSearch before 2.19 is not tested. The suite also passed once on each of 3.0.0 to 3.9.0.

- **What you see:** on 3.0 and 2.19 a substring search and an exact match fold ASCII letters only, so `äpf` and `=`
  `äpfel` find `ÄPFEL` from 3.1 on and not before; the whole word `äpfel` on an analysed field finds it on every
  version. 2.19 is also slow on case-insensitive exact matches of long values, with the same answers. A column
  declared `fold_case=False` sends its exact matches plain and gives up the fold for them. The rule is in
  [Types and matching](rules.md#types-and-matching).
- **Example:** a list of 256 values of 255 ASCII letters took 2.5 and 3.8 s in two runs on 2.19.7, and 0.16 and
  0.20 s on 3.10.0; one such value, 14 ms and 1 ms. The same list declared `fold_case=False` took 2 ms.
- **How it is known:** MEASURED 2026-11-21 in CI on the twelve servers 2.19.7, 3.0.0 and 3.1.0 to 3.10.0 (the suite
  passed once on 3.0.0 to 3.9.0, run 44952673787), and by the live tests on 3.10.0 and 2.19.7 on every CI run
  (`docs/decisions/001-opensearch-case-folding-beyond-ascii.md`).
- **Requirement:** PRD-F9, PRD-Q14.

### The fold of letters outside ASCII costs a regular-expression query

- **What you see:** a value with a cased letter outside ASCII is sent as a regular-expression query, which costs more
  than the term query that does not fold such letters.
- **Example:** on 3.10.0 a list of 256 such values of 255 letters took 0.37 s, against 0.06 s as term queries; a
  global search of 16 such words took 166 ms, against 111 ms as wildcards. On 2.19.7 the list took 2.8 s as regular
  expressions and 2.7 s as terms. (All figures: 512 MB of heap, about 2,000 documents on one shard, the least of 5
  runs: the cost of the query, not of a large index.)
- **How it is known:** MEASURED 2026-11-21 in CI
  (`docs/decisions/001-opensearch-case-folding-beyond-ascii.md`).
- **Requirement:** PRD-F9.

### A substring search needs a cluster that allows expensive queries

- **What you see:** a substring search is a wildcard or regular-expression query, and so is an exact match whose
  value has a cased character outside ASCII (on a column that folds, up to 255 code points). The cluster must allow
  them (`search.allow_expensive_queries`, on by default), and `index.max_regex_length` (1,000 by default, counted in
  UTF-16 units) must not be set under a regular expression's length, which is up to 768. A cluster that refuses the
  query raises, as any server failure does. This holds on every version, 3.0 and 2.19 included.
- **Example:** with `search.allow_expensive_queries` off, the cluster refuses a `contains` search and an exact match
  whose value has a cased character outside ASCII.
- **How it is known:** MEASURED 2026-11-21 in CI: the setting off refuses both kinds on 2.19.7 and 3.10.0, and
  `index.max_regex_length` counts UTF-16 units (run 44944336174); a pattern of 768 units is answered on all twelve
  servers (run 44952673787). `docs/decisions/001-opensearch-case-folding-beyond-ascii.md`.
- **Requirement:** PRD-F9.

### Some values fold ASCII letters only on every version

- **What you see:** a value without a cased character outside ASCII folds ASCII letters only: `k` does not find the
  Kelvin sign, `s` not the long s, and a substring or an exact match with `i` does not find a dotted capital `İ`
  (the analysed word does). A value longer than 255 code points folds ASCII letters only too. This holds on every
  version, 3.0 and 2.19 included. The rule is in [Types and matching](rules.md#types-and-matching).
- **Example:** a column holding the Kelvin sign: the search `k` finds no row.
- **How it is known:** the Kelvin sign, the long s and `İ`: MEASURED 2026-11-21 in CI on 2.19.7, 3.0.0 and 3.10.0
  (run 44944069007). A value over 255 code points: READ, it is sent as the `term` or `wildcard` that does not fold
  beyond ASCII (decision 001, "A value longer than 255 code points").
  `docs/decisions/001-opensearch-case-folding-beyond-ascii.md`.
- **Requirement:** PRD-F9.

### A normalizer that rewrites a character into punctuation can fail a search

- **What you see:** on a keyword field with such a normalizer (`asciifolding` does it to `⒜`, `⑴`, `⁇`), a value that
  holds a cased character outside ASCII and such a character can fail, miss its own row or match other rows. A
  `lowercase` normalizer is not affected. The entry "A character that a normalizer folds to `*`, `?` or `\` can be
  searched as a wildcard", above, says which characters folded to `*`, `?` or `\` a search still reads as a wildcard.
- **Example:** on an `asciifolding` field, `=` with `Ä⒜b` fails, and `=` with `Ä⑴b` and with `Ä⁇b` miss their own
  row and find others.
- **How it is known:** MEASURED 2026-11-21 on OpenSearch 2.19.7 and 3.10.0 (runs 44952007115 and 44952673787,
  `docs/decisions/001-opensearch-case-folding-beyond-ascii.md`).
- **Requirement:** PRD-F9.

### A normalizer that trims keeps a space at the end of an open search

- **What you see:** on a keyword field with a `trim` normalizer, a search text with a cased character outside ASCII
  and a space at its start or end finds fewer rows than the same text without the space: the space is kept where the
  search is open (`contains`, a global or column search, `starts` with a space at the end, `ends` with one at the
  start). An exact match, and the anchored end of `starts` and `ends`, are trimmed as the field is.
- **Example:** `Müller ` with the space finds only values that hold `Müller ` followed by more text; in the
  measurement `contains` with `ä ` found one row where the plain query found five.
- **How it is known:** MEASURED 2026-11-21 on OpenSearch 2.19.7 and 3.10.0 (runs 44952007115 and 44952673787,
  `docs/decisions/001-opensearch-case-folding-beyond-ascii.md`).
- **Requirement:** PRD-F9.

### A stored empty string is not an empty value

- **What you see:** "empty" is a missing field on OpenSearch, so `null` does not find a stored `""` and `!null` does;
  on SQL a column that stores text is empty when it holds NULL or `''`. The rule is in
  [the OpenSearch notes](rules.md#opensearch) ("A stored empty string").
- **Example:** a document whose `author` is `""` beside one with no `author`: `null` finds only the second.
- **How it is known:** MEASURED, pinned by `tests/test_opensearch_live.py`
  (`test_a_stored_empty_text_is_not_a_missing_field`), which CI's `opensearch` job runs on 3.10.0 and 2.19.7.
- **Requirement:** PRD-F26.

### A text that stands for a missing value stays a value: there is no expression source

- **What you see:** a load that writes a text such as `NULL` or `-` for what is missing leaves a keyword value, and
  `null` does not find it, `!null` does, and the cell shows the text. On SQL the source of a column can be an
  expression that turns the text into NULL ([the recipe](rules.md#sql), "A text that stands for a missing
  value").
  OpenSearch has no expression source: a `SearchTable` reads the stored field as it is.
- **What to do:** leave the field out when indexing. An ingest pipeline with a `remove` processor whose `if` tests for
  the sentinel does it for new documents; documents already indexed hold the text until they are reindexed through the
  same pipeline, which is a reindex of the existing data. The library changes no mapping and no document.
- **Example:** a document whose `author` is `"NULL"` beside one with no `author`: `null` finds only the second, until
  the first is indexed without the field.
- **How it is known:** INFERRED from the rule of PRD-F26 and the pinned behaviour above (`null` is a missing field); the
  pipeline was not run for this note. No search-time alternative is promised.
- **Requirement:** PRD-F26.

### The global search skips number and date columns

- **What you see:** on OpenSearch the global search leaves out a number or a date column, because a number or a date
  cannot be matched as text without a script. On SQL they take part, as text. The rule is in
  [Types and matching](rules.md#types-and-matching).
- **Example:** a number column holding 17: the global search `7` finds the row on SQL and no row on OpenSearch.
- **How it is known:** SQL: pinned by `tests/test_sql.py` (`test_the_global_search_matches_a_number_by_its_text`),
  which every SQL job of CI runs. OpenSearch: pinned by `tests/test_opensearch.py`
  (`test_the_global_search_fits_each_string_mapping_and_skips_numbers_and_dates`), which checks the query the library
  sends through a recording client; no live test runs it.
- **Requirement:** PRD-F23, PRD-F32.

### A column search on a number or a date column takes one term

- **What you see:** an OpenSearch column search on a number column takes one number and matches it exactly, and one on
  a date column takes one date term (`YYYY`, `YYYY-MM` or a day); several words, `!` or quotes are refused with
  `invalid_number` or `invalid_date` (PRD-F33, PRD-F34 hold the rule). On SQL the same search is a smart search of
  the number's or the date's text.
- **Example:** `12` on a number column finds the rows holding 12 on OpenSearch, and also 120 and 312 on SQL; `12 13`
  is `invalid_number` on OpenSearch. `2026-07` finds July 2026 on both, and `!2026-07` is `invalid_date` on OpenSearch.
- **How it is known:** OpenSearch: MEASURED, pinned by `tests/test_opensearch_live.py` (`test_a_column_search`, a number
  matched exactly, a month and a year on a date column), which CI's `opensearch` job runs, and the refusals by
  `tests/test_opensearch.py` (`test_a_column_search_a_number_or_date_column_cannot_read_has_its_code`) through a
  recording client. SQL: the text of a number is pinned by `test_a_column_search_on_a_number_or_a_date_reads_its_text`
  in `tests/test_sql.py`.
- **Requirement:** PRD-F22, PRD-F23, PRD-F33, PRD-F34.

### A page in the middle of a large result is refused

- **What you see:** OpenSearch reads at most `max_result_window` rows from the start of a search, 10,000 by default,
  so a page in the middle of a larger result is refused with `page_out_of_range` and the real counts, where SQL
  serves every page. The rule is in [the OpenSearch notes](rules.md#opensearch) ("Paging past the window").
- **Example:** 50,000 matching documents, `start` 20,000: `page_out_of_range`, with `recordsTotal` and
  `recordsFiltered`; the last page of the same result is answered.
- **How it is known:** the library's refusal and the reversed last page are pinned by `tests/test_opensearch_live.py`
  (`test_the_counts_before_a_refusal_are_the_ones_the_search_reports`,
  `test_a_page_read_by_the_reversed_sort_is_the_page_the_ordinary_way_gives`), which CI's `opensearch` job runs with a
  small window given to the table, so the cluster's own limit is never reached. The default of 10,000 is documented,
  not run: "Default is 10000", and the setting limits `from` + `size`
  (https://docs.opensearch.org/latest/install-and-configure/configuring-opensearch/index-settings/, opened
  2026-11-23). A table window above the index's own setting is not run.
- **Requirement:** PRD-C12.

### A text-only column cannot be ordered, and answers some conditions only

- **What you see:** a column over an analysed `text` field with no `keyword` subfield (declared `text='text'`) has no
  whole string, so it cannot be ordered and it refuses `=`, `starts`, `ends` and the other conditions that need one,
  where SQL orders and answers every condition. The rule is in [the OpenSearch notes](rules.md#opensearch)
  ("A column of analysed text only").
- **Example:** `=` `red fox` on a column declared `text='text'` is `invalid_filter`; `contains` `red fox` is
  answered.
- **How it is known:** pinned by `tests/test_opensearch.py` through a recording client
  (`test_an_order_on_analysed_text_is_refused_or_skipped_as_the_request_marks_it`,
  `test_analysed_text_declared_orderable_is_a_schema_error`,
  `test_a_condition_that_needs_a_whole_string_is_refused_on_analysed_text`,
  `test_analysed_text_still_answers_words_contains_null_and_an_empty_list`); not run against a server here.
- **Requirement:** PRD-O5, PRD-D19.

### A word on analysed text is a phrase of its tokens

- **What you see:** on an analysed column, text or `text+keyword`, a word the analyser splits also matches as a phrase
  of its tokens, and `!` excludes it; on a text-only column a word is an analysed match, so `red` does not find
  `reddish`. SQL matches substrings only. What the analyser drops, such as quote marks, `%`, `*`, `?`, a leading `-`
  and padding, does not tell rows apart either: `-red` also finds `red`, and `50%` finds `50 plum`. A keyword column
  tells them apart. The rule is in [the OpenSearch notes](rules.md#opensearch) ("Analysed text").
- **Example:** `a-b` finds `a b` and `!a-b` leaves it out; `a` alone is not found by `a-b`.
- **How it is known:** MEASURED, pinned by `tests/test_opensearch_live.py`
  (`test_a_word_the_analyser_splits_is_a_phrase_of_its_tokens`,
  `test_what_the_analyser_drops_does_not_tell_rows_apart_on_text_and_keyword` and, for the keyword column that does tell
  them apart, `test_what_an_analyser_drops_tells_rows_apart_on_a_keyword_column`), which CI's `opensearch` job runs on
  3.10.0 and 2.19.7.
- **Requirement:** PRD-F11.

## Every backend

### An exact match on a column that is not folded follows the collation or the mapping

- **What you see:** a column declared `fold_case=False`, or one with a `search_value` hook, is matched exactly as
  sent. The result follows the column's collation or the field's mapping, so the same request can match different
  rows on different databases. The rule is in [Types and matching](rules.md#types-and-matching) ("An exact match can
  be sent plain").
- **Example:** a column holding `Red`, `red` and `RED`, declared `fold_case=False`: `=` `red` finds one row under a
  case-sensitive collation (PostgreSQL's default) and all three under a case-insensitive one (MySQL's default
  `utf8mb4_0900_ai_ci`).
- **How it is known:** pinned under a case-sensitive collation by `tests/test_sql.py`
  (`test_an_exact_match_on_a_column_declared_without_the_fold_is_compared_as_sent`), which every SQL job of CI runs;
  the tests declare that collation on each engine, so they do not show the difference between engines. That
  `utf8mb4_0900_ai_ci` is case-insensitive is documented, not run: MySQL's manual says `_ci` in a collation name is
  case-insensitive and names `utf8mb4_0900_ai_ci` "explicitly case-insensitive and accent-insensitive"
  (https://dev.mysql.com/doc/refman/8.4/en/charset-collation-names.html, opened 2026-11-23).
- **Requirement:** PRD-D21, PRD-F9.

### Only the drivers that CI runs are tested

- **What you see:** the drivers and versions CI runs are listed under [Tested with](../README.md#tested-with); a
  driver or a MariaDB server not listed there is not run by this project.
- **Example:** the `mariadb` connector (`mariadb+mariadbconnector://`) is not run: SQLAlchemy has no async form of it,
  and the sync one needs MariaDB's C connector. MariaDB is run through pymysql and aiomysql, with the
  `mariadb+pymysql` and `mariadb+aiomysql` URLs, which make SQLAlchemy's dialect name `mariadb`; the `mysql+` URLs
  against a MariaDB server name it `mysql` and are not run. `mysqlclient` is run sync only, against MySQL 8.4.
- **How it is known:** READ in `.github/workflows/ci.yml` (jobs `tests`, `postgresql`, `postgresql-psycopg2-asyncpg`,
  `mysql`, `mysql-mysqlclient`, `mariadb`, `sqlserver`, `opensearch`).
- **Requirement:** PRD-Q2 (the database's own driver is the adopter's to install).

### A global search costs one condition per word and column, and the budget does not count what the adopter adds

- **What you see:** a request holds at most 960 conditions (PRD-C17): one word on one column, one list value and one
  range each count one, and a larger request is refused with `request_too_large`. A wide table's global search is the
  cost of words times the columns it covers. When nothing else is asked, the global search allows:

  | Words | 16 | 8 | 4 | 2 | 1 |
  |---|---|---|---|---|---|
  | Columns it covers | 60 | 120 | 240 | 480 | 960 |

  Every word of a column search, list value, range and criterion takes one from that. The columns are those that take
  part in the global search: declared searchable and searchable as text (a boolean is not, nor is a number or a date
  on OpenSearch). A hooked column counts one however many words the text holds. A `base_filter`, a `scope` and an
  expression column add to what the database receives and are stated here, not budgeted.
- **Example:** 16 words over a table of 61 searchable columns hold 976 conditions and are refused; 15 words hold 915.
- **How it is known:** the number is MEASURED (`docs/decisions/005-the-largest-request-the-bounds-allow.md`); the
  table is its arithmetic.
- **Requirement:** PRD-C17, PRD-E2.
