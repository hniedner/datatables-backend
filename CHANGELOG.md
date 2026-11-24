# Changelog

All notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com),
and the versioning policy is in the README: until 1.0, a minor release (0.x) may break the Python API and lists each such
change here; the wire format and the error codes change only in a release whose notes say so.

## [0.1.0] - 2026-11-24

The first release: the code of 0.1.0rc4, with its version, and one documentation addition.

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

### What it does

- Reads a DataTables server-side request (the form DataTables posts, a JSON body, or a query string) and answers it:
  paging, ordering, the global search, column searches and SearchBuilder criteria. It is read-only and
  framework-agnostic.
- Takes its data from SQL through SQLAlchemy 2 (sync and async) or from OpenSearch (sync and async client). The tests
  run on SQLite, PostgreSQL 18, MySQL 8.4, SQL Server 2022 and OpenSearch 2.19 and 3.10.
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

[0.1.0]: https://github.com/hniedner/datatables-backend/releases/tag/v0.1.0
[0.1.0rc4]: https://github.com/hniedner/datatables-backend/releases/tag/v0.1.0rc4
[0.1.0rc3]: https://github.com/hniedner/datatables-backend/releases/tag/v0.1.0rc3
[0.1.0rc2]: https://github.com/hniedner/datatables-backend/releases/tag/v0.1.0rc2
[0.1.0rc1]: https://github.com/hniedner/datatables-backend/releases/tag/v0.1.0rc1
[#46]: https://github.com/hniedner/datatables-backend/issues/46
[#58]: https://github.com/hniedner/datatables-backend/issues/58
[#71]: https://github.com/hniedner/datatables-backend/issues/71
[#75]: https://github.com/hniedner/datatables-backend/issues/75
[#152]: https://github.com/hniedner/datatables-backend/issues/152
