# Changelog

All notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com),
and the versioning policy is in the README.

## [Unreleased]

### Fixed

- SearchBuilder's `between` and `!between` take their two values in either order, as SearchBuilder's own filter does:
  `between` `8` and `7` is the range from 7 to 8. Before, a reversed pair answered no row for `between` and every row,
  rows without a value too, for `!between`. Numbers are compared as numbers and dates as days, so `10` and `9` are taken
  as the range from 9 to 10, though `10` sorts first as text.
- A column search whose text has no word, such as a lone `"`, a bare `!` or `!x`, adds nothing, so the request is
  answered as if it were not sent, on a column that is undeclared, nameless or declared unsearchable, and beside a
  `multi_values` list, which alone then answers. ColumnControl's `contains`, a column search, is covered too. Before,
  each of these was refused with `invalid_filter`, and so was text of spaces only beside such a list. A column search
  with `regex: true` is unchanged: spaces there are still a categorical value. So is text other than spaces on a column
  with a `search_value` hook, whose text is one whole value (PRD-D17), and text holding a control character is still
  refused with `invalid_character`.
- On OpenSearch, a number written as zero with a sign, such as `-0.00`, is sent as `0`, as `0` is. On a document
  holding 0.0 in a `double` or `float` field, the library sent -0.0, which such a field keeps apart from 0.0: so
  SearchBuilder `=` and `<=` with `-0.00`, a column search of `-0.00` and a list entry of `-0.00` missed the document,
  and `>` and `!=` with `-0.00` wrongly found it. Each now answers as for `0`.

## [0.1.3] - 2026-11-25

This release lets an application keep the `recordsTotal` count in a store of its own, such as Redis, which is handed
only a hashed key and never a value from a request. It also lets an OpenSearch number column say what type its field
has: a number outside that type's range then answers `invalid_number`, a mistake the user can fix, where it failed the
whole request with the client's own error before.

**Upgrade impact:** none. Both additions are optional: a table with no `total_cache` or with a `TotalCache`, and a number
column that declares no type, behave as in 0.1.2, and no change to your code is needed.

**Security:** none; this release fixes no known vulnerability.

### Added

- `total_cache=` takes any object with `get` and `put`: `TotalStore`, or `AsyncTotalStore` for `fetch`, both exported
  from `datatables_backend`. `TotalCache` is the in-process one and is unchanged for a caller. The store is handed an
  opaque SHA-256 digest of the key, never a bound value. `fetch_sync` raises a `TypeError` for an async store: before
  it queries anything when the store's methods are coroutine functions, at the first store call when a plain method
  returns an awaitable ([#162]).
- `IndexColumn(..., type='number', number=...)` declares an OpenSearch number field's mapping type: `byte`, `short`,
  `integer`, `long`, `unsigned_long`, `float` or `half_float` (`NumberKind`, exported from
  `datatables_backend.opensearch`). A number OpenSearch refuses for that type is answered with `invalid_number` before
  the query, where it failed the request with the client's `RequestError`; the message names the range and never the
  value. `byte` and `short` refuse only beyond an `integer`'s range, as OpenSearch does. A column that declares no kind
  behaves as before, and `number=` on a column that is no number column raises `SchemaError`. A list of `-1` on an
  undeclared `unsigned_long` still finds the document holding 2^64-1, which OpenSearch reads it as; a declared kind
  refuses it ([#47]).

## [0.1.2] - 2026-11-25

This release makes number filters on SQL `Numeric` columns exact, fixes an error on PostgreSQL with asyncpg, and stops
a silent rounding on PostgreSQL with psycopg 3 or asyncpg. An application on 0.1.1 can upgrade without changing code;
a few numbers that no database can represent are now refused with `invalid_number` (see Fixed).

### Fixed

- A number filter on a `Numeric` column through `fetch` on PostgreSQL with asyncpg raised `numeric field overflow` when
  the number had more digits than the column declares (31 nines on a `Numeric(32, 2)`); it
  now answers, as the sync drivers did.
- A number written with a point or an exponent (`12345678901234567.89`, `3.1e-1`) is now compared exactly with a
  `Numeric` column on PostgreSQL, MySQL, MariaDB and SQL Server. It went as a float before, so `=` on a column holding
  `12345678901234567.89`, `...67.90` and `...68.00` found all three rows, and a filter on a number of 17 or more digits
  could match a neighbour or miss the row. A patch release may correct an answer that was wrong, and this is one: a
  filter that matched a neighbour now matches the number asked for. SQLite is unchanged, since it stores a `Numeric` as
  a float or an integer; a `Float` column and OpenSearch still compare as floats.
- On SQL Server, a number of more than 38 digits in all, before and after the decimal point, that is compared with a
  `Numeric` column (or with an integer column, for a number with a fraction) is now refused with `invalid_number` ("at
  most 38 digits in all ... shorten the number") for every condition, where such a number written with a point or an
  exponent was answered as a float before. A `Float` column is not refused. The request no longer fails in the driver
  for a number that is a decimal.
- `invalid_number` is now also the answer, on every database, for a number written with an exponent beyond what a
  decimal holds (`1e9999999999999999999999`) and for one with more than 16,383 digits after the decimal point, the most
  PostgreSQL's `numeric` takes (`1e-20000`); a negative exponent was read as zero before. On PostgreSQL, `= 1e-400` on a
  `Numeric` column now finds no row, where it matched a row holding zero. On MySQL and MariaDB it still does (see
  docs/databases.md).
- On PostgreSQL with psycopg 3 or asyncpg, a filter with a fraction on an integer column of a converting type (a
  SQLAlchemy `TypeDecorator` over an integer) was rounded to a whole number, so `> 7.5` missed a row holding 8; it is
  now compared as the number (as a float, without the type's conversion).
- A number with a fraction on an integer column (`= 8.0000000000000000001`) is compared as written on PostgreSQL,
  MySQL, MariaDB and SQL Server, where a float of it was the whole number 8.

## [0.1.1] - 2026-11-24

This release answers ColumnControl's per-column filters, adds `scope=`, a per-request filter such as a user's rows,
bounds the total cache, and answers HTTP 400 when a client disconnects while the request body is read. An application
on 0.1.0 can upgrade without changing code.

### Added

- `TotalCache(max_entries=...)`: a bound on the totals kept, 10,000 by default, the one stored longest ago dropped first
  ([#50]). A `max_entries` below 1 raises `ValueError`.
- `scope=` on `fetch` and `fetch_sync` of `SQLTable` and `SearchTable`: a filter for one request, such as a user's rows,
  ANDed with `base_filter` and kept apart in the total cache, so one table and one cache serve every user ([#79]). Not
  breaking.
- ColumnControl's per-column filters are answered on both backends ([#163], [#164], [#165]; PRD-F45): its text, number
  and list controls, with every logic (`contains`, `notContains`, `equal`, `notEqual`, `starts`, `ends`, `empty`,
  `notEmpty`, and the number control's `greater`, `greaterOrEqual`, `less`, `lessOrEqual`). Each answers as the column
  search or SearchBuilder condition it matches, so the type rules are those of that condition. They count toward the
  existing bounds (64 column searches, 256 values in a list and 512 in all, 960 conditions), and their mistakes come
  back as a normal answer (HTTP 200) with the usual codes, `invalid_filter` and `invalid_number` among them, as a column
  search's do. Unlike the browser, `less` and `lessOrEqual` leave out a row with no value (PRD section 5). They work
  with DataTables' default POST and with `submitAs: 'json'`, and not with an `ajax.data` that returns a string, since
  ColumnControl adds its filters after it has run (README).

### Changed

- A request with an active ColumnControl search or list is no longer refused as a whole: until now any active one raised
  `MalformedRequest` ("Invalid columns parameter."), the 422 of the FastAPI adapter. What is still refused raises
  `MalformedRequest` with a message that says what to change: an active search of ColumnControl's date control, a logic
  outside the set, a value with no logic, a list entry that is neither text nor a whole number, and a list that is no
  array and no object keyed by indexes.
- A `TotalCache` that would hold more than 10,000 distinct totals now drops the oldest (a request for it counts again),
  where it grew without limit before. Pass a larger `max_entries` to keep more.
- The FastAPI dependency answers HTTP 400 when the client disconnects while the request body is read (a user who leaves
  the page, a proxy that times out), with the `detail` "The request body could not be read: the client disconnected;
  send the request again." ([#151]; PRD-A9). Until now Starlette's `ClientDisconnect` escaped the dependency, so the
  server tried to send a 500 into the dead connection and logged an unhandled-exception traceback for each one; an app
  that counts 5xx answers sees these become 400s, and its logs lose the tracebacks. It is no 422, which stays the answer
  to a `MalformedRequest`.

## [0.1.0] - 2026-11-24

The first release.

### What it does

- Reads a DataTables server-side request (the form DataTables posts, a JSON body, or a query string) and answers it:
  paging, ordering, the global search, column searches and SearchBuilder criteria. It is read-only and
  framework-agnostic.
- Takes its data from SQL through SQLAlchemy 2 (sync and async) or from OpenSearch (sync and async client). The tests
  run on SQLite, PostgreSQL 18, MySQL 8.4, MariaDB 12.3, SQL Server 2022 and OpenSearch 2.19 and 3.10.
- The server declares the columns, their types and what can be searched and sorted. A request cannot reach any other
  column, and the same rules and bounds apply on every source: capped page length, escaped wildcards, bounded lists and
  filters, no regular expressions run for a client.
- A user's mistake comes back as a normal response with one of eight stable error codes and a text that says how to fix
  it; any other failure raises, so the application decides how to report it.
- Pages are stable: every order ends with a unique key (the primary key on SQL, a declared `unique_key` on OpenSearch).
  OpenSearch serves the last pages of a large result by reversing the sort.
- Optional pieces: a cache for `recordsTotal`, row hooks (`DT_RowId`, `DT_RowData`, a row shaper), and a FastAPI
  dependency that reads the request.
- Installs by extra: `sqlalchemy`, `sqlalchemy-async`, `opensearch`, `opensearch-async` and `fastapi`.
- Not answered: DataTables' fixed and grouped searches, SearchPanes and ColumnControl filters. A request that carries one
  is refused (a malformed request, 422 in the FastAPI dependency) rather than answered with fewer filters.

### Added

- `docs/rules.md` states the 422 answer of the FastAPI dependency for a malformed request, with each shape the tests
  pin ([#152]).

### Changed

- Since 0.1.0rc3, `SQLTable` refuses a declared type that cannot fit the column's SQL type with a `SchemaError` when the
  table is built ([#46]): a table that constructed on 0.1.0rc3 or earlier may be refused. The refused cases are a
  string column declared `number`, `date` or `boolean`, and a date or date-time column declared `number`; the message
  names the column and both fixes.

## [0.1.0rc4] - 2026-11-24

Changes since 0.1.0rc3.

### Added

- `datatables_backend.ErrorCode` and `datatables_backend.MAX_PAGE_LENGTH`, and
  `datatables_backend.opensearch.partial_answer(answer, *, index)`, for code that works beside a table (see
  `docs/usage.md`).
- `docs/databases.md` says that an OpenSearch date or number field declared as a plain column fails a global search
  with the client's `RequestError`, and that declaring it `type='date'` or `type='number'` fixes it; the README's
  example says to declare every date, number and boolean field.

### Fixed

- Five dead or moved links, in the README and in a docstring of `protocol.py`.

### Changed

A tightening is a breaking change, and each is listed here with what to do.

- `SQLTable` raises `SchemaError` when it is built for a column whose declared type does not fit its SQL type: a
  string column (a string type, an `Enum`, or a `TypeDecorator` that stores one) declared `number`, `date` or
  `boolean`, and a date or date-time column declared `number`. Such a column was answered differently on each
  database (`=` 5 on text found '5' on SQLite, '5', '05' and '5.0' on MySQL) or the server refused the comparison
  (PostgreSQL). The message names the column and both fixes: declare it `string` or `date`, or give the column or
  its expression, by a cast, a type of the declared kind. Every other declaration, an integer or a
  boolean column declared `string` included, is accepted as before ([#46]).

## [0.1.0rc3] - 2026-11-24

Changes since 0.1.0rc2.

### Added

- CI also runs the tests on MariaDB 12.3 (pymysql, aiomysql) and through mysqlclient 2.3 on MySQL 8.4; the README's
  "Tested with" lists them, and `docs/databases.md` has MariaDB's default collation ([#58]).

### Fixed

- On SQL Server, a search for a number found no row when the column was a float of more than six digits: `CAST` writes
  1234567 as `1.23457e+006`, so the search `1234567` missed it, and the digits of the exponent matched instead. A column
  of a float type is cast by `FORMAT(x, 'R', 'en-US')` there now, so a number as the table shows it finds its row
  ([#71]; the measurements are in `docs/decisions/006-number-search-text.md`). The search costs about four times as
  much on a scan of a float column. PostgreSQL's single-precision `real` still writes an exponent, which
  `docs/databases.md` says.

### Changed

A tightening is a breaking change, and each is listed here with what to do.

- Each public module has an `__all__`: `datatables_backend`, `datatables_backend.sqlalchemy` (`SQLTable`),
  `datatables_backend.opensearch` (`SearchTable`, `IndexColumn`, `TextKind`) and `datatables_backend.fastapi`
  (`DataTablesParams`, `parse_datatables_request`). Import from these; any other module is internal.
- `InvalidFilter` is no longer exported by `datatables_backend`: it never reaches an application, since a user's mistake
  is an answer.
- The helpers each backend module defined without an underscore are internal now and renamed: `escape_like`,
  `escape_wildcard`, `partial_answer`, `MSSQL_DECIMAL_DIGITS`, `MAX_RESULT_WINDOW`, `DOCUMENT_ID`, the FastAPI
  adapter's `MALFORMED`, `FORM_FIELD_LIMIT` and `FORM_TYPES`, and the type aliases `AsyncSource`, `SyncSource`,
  `SyncClient` and `AsyncClient`. Annotate with the type of your own session or client instead.
- `SQLTable` raises `SchemaError` for an `IndexColumn`, which declares an OpenSearch field and which a SQL table
  ignored. Declare it as `Column(...)`.

## [0.1.0rc2] - 2026-11-23

Fixes and tightenings since 0.1.0rc1, which was tagged but not published.

### Fixed

- The FastAPI dependency read a JSON body only under `Content-Type: application/json`. DataTables' `ajax.submitAs: 'json'`
  alone keeps the form's Content-Type, so such a table was answered 422. The body is read whatever its Content-Type now:
  a JSON object as JSON, any other body as a form.
- A JSON request whose columns set no `data` raised `MalformedRequest`: DataTables sends the column's index, an integer,
  in that case, and so it does with `ajax.submitAs: 'json'`. It is read now, as the text of the integer.
- A column whose `data` is an object (DataTables' orthogonal data) raised `MalformedRequest`, in JSON and in a form. It is
  read now, as a column named by its `name`, or as a nameless one without.

### Changed

A tightening is a breaking change, and each is listed here with what to send.

- A request with no body is read from its query string whatever its method; a POST with an empty body was answered
  422. (Nothing that was answered before is answered differently: a body still wins over the query string, and the two
  are never merged.)

- A number in the request (`draw`, `start`, `length`, `order[i][column]`) must be a JSON integer, or ASCII digits with an
  optional minus, as DataTables writes it. `1.0`, `1.5`, ` 1`, `+1` and the JSON number `1.0` now raise
  `MalformedRequest`; they were read as 1.
- A boolean (`searchable`, `orderable`, `regex`) must be a JSON boolean or exactly the text `true` or `false`, as
  DataTables writes it. `True`, `yes`, `on`, `1` and the JSON numbers 0 and 1 now raise `MalformedRequest`; they were
  read as booleans. A client that writes `True` (Python's `requests` does, for a form) must send `true`.
- The message of a `MalformedRequest` says what to send, for example `Invalid draw parameter: send a whole number, as
  DataTables does.` It still names no key or value of the client.
- On an OpenSearch column of analysed text only (`text='text'`), `=`, `!=`, `starts`, `!starts`, `ends`, `!ends` and a
  list of values are now refused with `invalid_filter`, where they were answered as a phrase; `contains` and `!contains`
  are still answered. A `search_value` hook on such a column is now a `SchemaError` at construction ([#75]).

## [0.1.0rc1] - 2026-11-23

Tagged but not published: its deployment was rejected before upload, for the fixes in 0.1.0rc2.

The first release, a release candidate: install it with `pip install --pre datatables-backend`.

What it does: see [0.1.0].

[Unreleased]: https://github.com/hniedner/datatables-backend/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/hniedner/datatables-backend/releases/tag/v0.1.3
[0.1.2]: https://github.com/hniedner/datatables-backend/releases/tag/v0.1.2
[0.1.1]: https://github.com/hniedner/datatables-backend/releases/tag/v0.1.1
[0.1.0]: https://github.com/hniedner/datatables-backend/releases/tag/v0.1.0
[0.1.0rc4]: https://github.com/hniedner/datatables-backend/releases/tag/v0.1.0rc4
[0.1.0rc3]: https://github.com/hniedner/datatables-backend/releases/tag/v0.1.0rc3
[0.1.0rc2]: https://github.com/hniedner/datatables-backend/releases/tag/v0.1.0rc2
[0.1.0rc1]: https://github.com/hniedner/datatables-backend/releases/tag/v0.1.0rc1
[#46]: https://github.com/hniedner/datatables-backend/issues/46
[#47]: https://github.com/hniedner/datatables-backend/issues/47
[#50]: https://github.com/hniedner/datatables-backend/issues/50
[#58]: https://github.com/hniedner/datatables-backend/issues/58
[#71]: https://github.com/hniedner/datatables-backend/issues/71
[#75]: https://github.com/hniedner/datatables-backend/issues/75
[#79]: https://github.com/hniedner/datatables-backend/issues/79
[#151]: https://github.com/hniedner/datatables-backend/issues/151
[#152]: https://github.com/hniedner/datatables-backend/issues/152
[#162]: https://github.com/hniedner/datatables-backend/issues/162
[#163]: https://github.com/hniedner/datatables-backend/issues/163
[#164]: https://github.com/hniedner/datatables-backend/issues/164
[#165]: https://github.com/hniedner/datatables-backend/issues/165
