# Changelog

All notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com),
and the versioning policy is in the README: until 1.0, a minor release (0.x) may break the Python API and lists each such
change here; the wire format and the error codes change only in a release whose notes say so.

## [0.1.0rc1] - 2026-11-23

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

### Known differences from the documentation

- On an OpenSearch column of analysed text only (`text='text'`), `=`, `starts` and `ends` are still answered (as a phrase
  or a substring), although the README and `docs/rules.md` say they are refused. Fixed by
  [#75](https://github.com/hniedner/datatables-backend/issues/75); this entry goes when it lands.

[0.1.0rc1]: https://github.com/hniedner/datatables-backend/releases/tag/v0.1.0rc1
