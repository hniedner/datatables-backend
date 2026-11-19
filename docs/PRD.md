# datatables-backend: product requirements

- **Product:** datatables-backend, a read-only Python library that answers DataTables server-side requests from SQL
  (SQLAlchemy 2, async and sync) and OpenSearch, with one request model, one column declaration and one set of
  safety rules for every source.
- **Status:** draft 2 (2026-11-19), for the owner's approval. It replaces draft 1 (R1 to R9); section 9 maps every
  draft-1 item to its place here. Once approved, a requirement changes only by the owner.
- **Order of work:** PRD (approved) -> specs with numbered statements -> behavioural tests from the outside in -> code
  by TDD. Every spec statement traces to a requirement here, and every *must* has statements.
- **Fact labels:** READ (a document read on 2026-11-19), MEASURED (a command run on 2026-11-19), INFERRED (reasoning;
  to be checked before relying on it). A requirement without a label states a decision, not a fact.

## 1. The problem and who has it

[DataTables](https://datatables.net) tables that page, sort and filter on the server send a fixed request: paging,
ordering, a global search, per-column searches and, with the SearchBuilder extension, a tree of AND/OR criteria. Every
Python backend has to turn that request into a query safely. Getting it wrong leads to unbounded result sets, wildcard
injection, errors that echo user input, and sorts or filters on columns the page never exposed.

What exists (READ, researched 2026-11-19):

- `datatables_server` (SpryMedia, the DataTables authors) covers SQL through SQLAlchemy 2 Core with a sync
  `Connection`, including Editor (CRUD), SearchBuilder, SearchPanes and ColumnControl. It has no native async API and
  no search-engine backend.
- No Python library serves DataTables from OpenSearch or Elasticsearch.
- The older SQLAlchemy libraries (`sqlalchemy-datatables`, `datatables`, `sqlalchemy2-datatables`) are unmaintained.

## 2. Users

- **SvelteDT, the reference frontend** ([hniedner/sveltedt](https://github.com/hniedner/sveltedt), public). Its
  connection helper (its PRD-B20) speaks the DataTables 1.10-and-later protocol in the bracketed form, the dotted form
  or a JSON body. Its local data source (its PRD-B21) defines the search and order semantics a user sees when the
  same table runs in the browser; this library gives the same answers on the server wherever a database allows it.
- **Any DataTables 2.x client.** The library keeps serving DataTables' own request, with no SvelteDT-only parameter
  required.
- **Developers of Python web apps** (FastAPI, Starlette, Flask, Django with SQLAlchemy) who show database or
  search-index data in a table.

## 3. Goals and non-goals

**Goals**

1. One request model and one column declaration for every source; each source is a small backend: SQL through
   SQLAlchemy 2 (async and sync), OpenSearch (async and sync clients), and later Elasticsearch behind the same
   contract.
2. A search, filter and order mean the same on every backend, and the same as SvelteDT's local data source, except
   where the database decides (text order, accent folding), which the specs state per database.
3. Every user mistake is found before anything runs and answered with a stable code and a fixed text that never
   quotes the input.

**Non-goals**

- Editing (DataTables Editor). `datatables_server` covers it for SQL.
- Rendering HTML or shipping JavaScript.
- Exports, audit logging, authentication and authorisation. These belong to the app. The library gives the app a
  request whose paging it can replace.
- Regular-expression search. A client regex is refused, except the categorical convention of PRD-F9.
- Natural or ICU text ordering in the library ("item2" before "item10", locale-aware ties). Text order is the
  database's collation (PRD-O5): an ordering the library imposed would need per-database collations or extensions,
  which it does not create, and would not be portable across SQLite, PostgreSQL, SQL Server and OpenSearch.
- Creating database extensions or collations (e.g. `unaccent`, ICU collations). An app that wants them declares a
  column as an expression (PRD-D6).
- SearchPanes and ColumnControl. Candidates for later (PRD-L2).

## 4. Requirements

Priority: **must** (in 1.0), **should** (in 1.0 unless the owner moves it), **later** (after 1.0).

Ids: `PRD-P` parsing, `PRD-D` column declaration (the server's schema, which every other group reads), `PRD-F`
filtering and search, `PRD-O` ordering, `PRD-C` counts and paging, `PRD-E` errors, `PRD-R` rows and the response,
`PRD-A` API, `PRD-Q` packaging and quality, `PRD-L` later.

### 4.1 Parsing (P)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-P1 | A request is read from a decoded JSON object (nested), or from flat form or query parameters as a mapping or as key-value pairs with repeated `key[]` entries. | DataTables posts JSON when `ajax.data` returns a JSON string or `ajax.submitAs` is `json` (READ, DataTables 3.1.5 reference), and form or query parameters by default. | must |
| PRD-P2 | Flat keys are read in bracket notation (`columns[0][search][value]`), dotted notation (`columns[0].search.value` or `columns.0.search.value`) or a mix, and every form reads to the same request. | DataTables sends brackets by default; spring-data-jpa-datatables reads dotted names (READ, its README); SvelteDT's helper sends either (its PRD-B20). A dotted request once lost every column silently (MEASURED, fixed on `fix/dotted-form`). | must |
| PRD-P3 | A flat key that does not start with a request parameter's name (DataTables' GET cache-buster `_`, an app's own `ajax.data` keys) is ignored, and so is an unknown field under a request key (e.g. `search[fixed]`). | DataTables 3.1.5 sends `_` on every GET and `search[fixed]`, `search[groups]` and `columns[i][search][fixed]` when used (READ, its source); refusing them would break real clients. | must |
| PRD-P4 | A request whose structure cannot be read raises `MalformedRequest`, whose message names the parameter and never the client's key or value: a wrong structure, a key that starts with a request parameter's name but has no readable shape, a non-integer paging number, a negative `start`, a number above its bound (PRD-P5), or text JSON cannot encode (a lone surrogate). | A structural error is a client or integration bug, not something the user can change; the FastAPI adapter answers it 422 (PRD-A6). | must |
| PRD-P5 | `start` is accepted up to 2^53-1; `draw`, column and order indexes up to 2^31-1. | SvelteDT pages up to 2^53-1, the largest integer JavaScript holds exactly (its PRD-B9); 2^31-1 refused its last pages. | must |
| PRD-P6 | `length` is between 1 and a configured maximum (default 1000); "All" (`-1`) is refused as malformed. | An unbounded page is the first way a table query goes wrong; SvelteDT pages at most 1000 rows. | must |
| PRD-P7 | The strings `"true"` and `"false"` read as booleans. | Form and query encodings carry only strings. | must |
| PRD-P8 | A column's `name` falls back to its `data`. | DataTables sends an empty `name` unless the page sets one. | must |
| PRD-P9 | `order[i][column]` indexes the request's own `columns`; an `order[i][name]` beside it is accepted and the index decides. | DataTables 2.x and SvelteDT send both (its PRD-B20); one rule avoids two answers to one request. | must |
| PRD-P10 | The request is validated into typed models (`DTParams`, `DTColumn`, `DTOrder`, `Search`, the recursive SearchBuilder tree). | A typed request is what the planner and every backend read; nothing downstream sees raw input. | must |

### 4.2 Column declaration (D)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-D1 | The server declares the table's columns; each has a client-facing name, the backend field when it differs, a value type (string, number or date), whether it is searchable and whether it is orderable. | The server, not the client, decides what can be seen, searched and sorted. | must |
| PRD-D2 | Any name the request uses to search, filter or order on (column searches, the global search, `order`, every SearchBuilder `origData`) must be a declared name; otherwise the request is refused (PRD-E1). | A backend field never becomes reachable just because the client names it. | must |
| PRD-D3 | The declared type decides how a criterion is built; the client's SearchBuilder `type` is ignored. | A wrong client type cannot change how a value is matched. | must |
| PRD-D4 | SQL infers the type from the column when none is declared; OpenSearch defaults to string. | Most SQL columns carry their type; an index mapping is not read at request time. | must |
| PRD-D5 | A column may declare a hook that transforms a search value before it reaches the query (e.g. to encrypt or encode it as stored). | Some columns are stored transformed; the user still types the plain value. | must |
| PRD-D6 | A SQL column may be declared as an expression over the source (e.g. `unaccent(name)` or a `COLLATE` clause), searched and ordered like any column. | Accent folding and collation follow the database (PRD-F4, PRD-O5); an app that wants more uses its database's means without the library creating extensions. | should |
| PRD-D7 | An OpenSearch table declares a unique key field, used to break ties in order. | OpenSearch has no primary key; without one, equal sort values reorder between pages (PRD-O3). | must |

### 4.3 Filtering and search (F)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-F1 | The global search is a smart search in which the row is the unit: every word must be found in at least one of the row's searchable columns, and different words may match different columns. Example: "red 7" finds a row whose colour is red and whose price is 7. | DataTables' smart search and SvelteDT's local data source behave so (its PRD-B21); a whole-text match in one column misses rows a user expects. | must |
| PRD-F2 | A column search is the same smart search within that one column: every word must match in it. | One syntax for both search boxes, as in SvelteDT. | must |
| PRD-F3 | Smart-search syntax, the same on every backend: words in any order (AND); a "quoted phrase" is one word that must sit inside one column; `!word` excludes (in the global search: no searchable column of the row contains it; in a column search: the column does not contain it); a newline, U+2028 or U+2029 counts as a space. OpenSearch's `OR`, `NOT` and `-` query syntax is dropped. | One syntax everywhere, as the frontend defines it; a second syntax on one backend conflicts with `!` and surprises users. | must |
| PRD-F4 | Matching is case-insensitive on every backend. Accent folding is whatever the database's collation gives; the specs state each database's behaviour. | Case-insensitivity is portable; accent folding is not without extensions the library does not create (non-goals, PRD-D6). | must |
| PRD-F5 | A word matches as a substring; LIKE wildcards (`%`, `_`, `[`, `\`) and OpenSearch wildcard characters in it are escaped. | A user's `%` or `*` is text, never a pattern; wildcard injection is a known failure. | must |
| PRD-F6 | Column searches combine with AND, with each other and with the global search. | DataTables' meaning. | must |
| PRD-F7 | A search over no usable column is refused (PRD-E1), never answered with every row. | An empty OR silently matching everything hides a mistake. | must |
| PRD-F8 | Search text holding a NUL or another control character (except newline, U+2028 and U+2029, read as spaces by PRD-F3) is refused before any query runs. | A NUL truncated a LIKE pattern on SQLite and returned wrong rows (MEASURED); SvelteDT refuses control characters at the field. | must |
| PRD-F9 | A categorical filter is a column search with `regex: true` and the value `^(v1\|v2)$`, each value regex-escaped; it matches the values exactly (an IN list). An empty alternative means NULL or `''`. Any other regex is refused. | DataTables' multi-select convention; a single value sent as plain text would silently become a substring match ("red" finding "reddish"). | must |
| PRD-F10 | A `multi_values` list in a column's search object is accepted as the same exact match. | An extension that needs no regex escaping, for clients that build the request themselves. | must |
| PRD-F11 | Dates are matched by day: `=`, `<`, `<=`, `>`, `>=` and `between` refer to whole days, for date and datetime columns alike. | A user filters by calendar day, not by instant. | must |
| PRD-F12 | SearchBuilder's "contains" on a date column, and a column search on an OpenSearch date column, take a year, a year-month or a day (`YYYY`, `YYYY-MM`, `YYYY-MM-DD`) and mean that span, on every backend. | A partial date names a span; substring matching on date text depends on the database's date format. | must |
| PRD-F13 | In the global search and SQL column searches, a date column matches by its `YYYY-MM-DD` text; the OpenSearch global search skips date columns. | Free text cannot be a date span; SQL can match date text, OpenSearch cannot without a script. | must |
| PRD-F14 | The maximum date (9999-12-31) as a bound is a user error, not a failed query. | Its next day does not exist, and a user can change it. | must |
| PRD-F15 | Numbers are parsed and bounded: non-finite and non-numeric values are refused, and so is a value outside the column's SQL integer range. | A database error on an out-of-range number is a user mistake surfacing late. | must |
| PRD-F16 | "Empty" means NULL plus `''` on SQL string columns; on OpenSearch, a missing field. | Users do not tell NULL from an empty string. | must |
| PRD-F17 | SearchBuilder: the full condition set (`=`, `!=`, `<`, `<=`, `>`, `>=`, `between`, `!between`, `contains`, `!contains`, `starts`, `!starts`, `ends`, `!ends`, `null`, `!null`), nested groups and AND/OR. An empty group adds nothing; a condition that does not fit the column's type, or a criterion missing its values, is refused with a reason. | Typed comparisons and range filters for any DataTables 2.x client. | must |
| PRD-F18 | An optional base filter (a SQL expression or an OpenSearch query) limits the rows the table shows at all, for `recordsTotal`, `recordsFiltered` and the page alike. | Row-level scoping belongs to the server, not the client. | must |

### 4.4 Ordering (O)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-O1 | An order on an index outside the request's columns, on a column the request marks unorderable, or on one the server declares unorderable, is refused (PRD-E1). | The server decides what can be sorted. | must |
| PRD-O2 | NULL is the lowest value: first ascending, last descending, on every backend. | One answer on every database (their defaults differ: PostgreSQL puts NULL last ascending), as SvelteDT's local source sorts empty values first. | must |
| PRD-O3 | After the user's order, a tie-breaker on a unique key (SQL: the primary key, or the first column if there is none; OpenSearch: the declared key of PRD-D7) follows the last sort column's direction, so descending is the exact reverse of ascending. | Pages are deterministic, a tie never splits across a page boundary, and reversing the order reverses the rows (SvelteDT's local source does so). | must |
| PRD-O4 | OpenSearch sorts a string on its keyword form; an order on analysed-only text is dropped with a warning. | OpenSearch rejects the whole search on an analysed text sort. | must |
| PRD-O5 | Text order is the database's collation; the library adds no collation of its own. | Portable and predictable per database; see the non-goals. | must |

### 4.5 Counts and paging (C)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-C1 | `recordsTotal` counts the rows under the base filter; `recordsFiltered` counts the rows after every filter. | DataTables' meaning, which SvelteDT shows as the pager's totals (its PRD-B26). | must |
| PRD-C2 | A `start` past the end answers an empty page with both counts. | The client recovers by paging back; an error would hide the counts. | must |
| PRD-C3 | SQL counts with `COUNT(*)`, pages with OFFSET/LIMIT and selects only the declared columns. | Undeclared columns never leave the database. | must |
| PRD-C4 | OpenSearch returns the page and the exact filtered count in one search; `recordsTotal` reuses it when nothing beyond the base filter applies; a lower-bound count is replaced by an exact count. | One round trip where possible, never an approximate total. | must |
| PRD-C5 | An OpenSearch page beyond `max_result_window` (default 10,000) that lies near the end is served by reversing the sort and the page. Needs proof in the OpenSearch container tests. | "Last page" on a large index is an ordinary press in SvelteDT's pager. | must |
| PRD-C6 | An OpenSearch page beyond `max_result_window` that the reverse sort cannot reach (a middle page) is refused with `page_out_of_range` before anything is sent. | OpenSearch would reject it; a refusal the frontend can explain beats a failed query. | must |
| PRD-C7 | `recordsTotal` can be cached by a caller-supplied TTL cache, keyed by the table, the base filter and the database or index; invalidation is by TTL or an explicit call; the library registers no global hooks on engines or pools. | An unfiltered count of a large table is costly; global hooks would surprise an app. | must |
| PRD-C8 | On SQL Server, an IN list that would exceed the 2100-parameter limit is sent as one JSON parameter (`OPENJSON`); SQLite uses `json_each`; other dialects bind normally. | Large categorical filters must not fail on a driver limit. | must |

### 4.6 Errors (E)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-E1 | A request the user can fix is answered, not raised: the response carries a fixed `error` text and a stable `error_code`. | The user changes the filter; the app needs no error handling for it. | must |
| PRD-E2 | The `error_code` values form a documented, stable set: `invalid_filter` (an undeclared or unusable column, a wrong condition for the type, a refused regex, refused search text), `invalid_date`, `invalid_number` and `page_out_of_range`; `query_failed` is set only by an app (PRD-E6). A new code is added only by the owner. | A frontend maps codes to its own messages; SvelteDT can show the reason at the field. | must |
| PRD-E3 | An `error` text is fixed per case and never quotes the user's input. | Echoing input invites injection into logs and pages. | must |
| PRD-E4 | A structural error raises `MalformedRequest` (PRD-P4), never `invalid_filter`. | The two are different failures: one the user fixes, one the integrator fixes. | must |
| PRD-E5 | Every user mistake is found while parsing, planning or building the query, before anything runs. | A filter error is never a half-run query. | must |
| PRD-E6 | Everything else raises: a database error, a schema the table lacks, a partial OpenSearch answer. `DTResponse.query_failed(draw)` gives the conventional shape for an app that catches it. | A server failure is not the user's to fix and must not look like an empty result. | must |
| PRD-E7 | An OpenSearch answer that does not cover every shard, or whose shard information cannot be read, raises `PartialResultError`. | A partial answer is never shown as complete. | must |
| PRD-E8 | Logs never contain search values or client-supplied names; a refused name is logged only if the server declared it. Logging uses the standard `logging` module under the logger `datatables_backend`. | Search text can be personal data; client names are untrusted. | must |

### 4.7 Rows and the response (R)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-R1 | Each row is a dict keyed by the client-facing names. | The client never sees backend field names. | must |
| PRD-R2 | An optional row hook can reshape a row (e.g. through a Pydantic model); optional hooks add `DT_RowId` and `DT_RowData`. | Apps shape rows without post-processing the response. | must |
| PRD-R3 | `DTResponse` has `draw` (echoed), `recordsTotal`, `recordsFiltered` and `data`, plus `error` and `error_code` when set. | DataTables' response, extended only by the error code. | must |

### 4.8 API (A)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-A1 | A table object holds only the server-side declaration (the source, the columns, the base filter and the hooks), is built once and reused across requests; constructors do no I/O. | Declarations are configuration, safe at import time. | must |
| PRD-A2 | `await table.fetch(params, conn)` answers one request; `table.fetch_sync(params, conn)` is the sync version. | Async and sync apps alike. | must |
| PRD-A3 | The query is built entirely before anything runs. | Together with PRD-E5, a filter error is never a half-run query. | must |
| PRD-A4 | SQL: the table takes a SQLAlchemy `FromClause` (a table, join or subquery) and accepts an `AsyncSession` or `AsyncConnection` (async) and a `Session` or `Connection` (sync). | What SQLAlchemy apps hold. | must |
| PRD-A5 | OpenSearch: the table takes an index name and accepts an `AsyncOpenSearch` or `OpenSearch` client; queries are plain request bodies, with no DSL dependency. | No extra dependency beyond the client. | must |
| PRD-A6 | An optional FastAPI dependency parses the request (JSON body, form or query) and answers 422 on `MalformedRequest`. | The most common async Python web framework, with the structural/user split of PRD-E4. | must |

### 4.9 Packaging and quality (Q)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-Q1 | The distribution is `datatables-backend`, the import `datatables_backend`; extras `[sqlalchemy]`, `[opensearch]` and `[fastapi]`. | One install per need. | must |
| PRD-Q2 | The core (`protocol`, `columns`, `_plan`, `rows`, `cache`) imports only Pydantic and the standard library; each backend imports only its own extra; `sqlalchemy.ext.asyncio` is imported only for type checking. | A sync-only or single-extra install must work; `sqlalchemy.ext.asyncio` needs `greenlet`. | must |
| PRD-Q3 | Supported floors: Python 3.12+; Pydantic 2.10+ (2.12+ on Python 3.14); SQLAlchemy 2.0.31+; opensearch-py 2.6+; FastAPI 0.133+ and python-multipart 0.0.9+. Each floor is tested on every supported Python. | Pydantic 2.7-2.9 fail on the PEP 695 aliases, 2.11 has no 3.14 wheel, SQLAlchemy 2.0.30 fails on 3.13, and FastAPI 0.133 is the first without a starlette<1.0 cap (MEASURED, CI branch). | must |
| PRD-Q4 | Fully typed (`py.typed`), checked with basedpyright and ruff. | Users' type checkers see the real API. | must |
| PRD-Q5 | Tests go through the public API (`parse_request`, then `fetch` or `fetch_sync`): SQL on SQLite (sync and aiosqlite) by default, PostgreSQL and SQL Server in CI containers, OpenSearch on the request bodies built and against a container. | Behaviour, not implementation, is what users rely on; dialect differences only show on real databases. | must |
| PRD-Q6 | CI runs lint, types, tests on Python 3.12-3.14, the minimum versions, and an install-isolation check of the built wheel; one required `gate` check blocks merges to main. | Every claim above is checked on every change. | must |
| PRD-Q7 | MIT licence. | Permissive; the repository's `LICENSE`. | must |

### 4.10 Later (L)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-L1 | An Elasticsearch backend behind the same contract. | Same users as OpenSearch; the 8.x/9.x clients changed the `body=` API, so it needs a thin client adapter. | later |
| PRD-L2 | SearchPanes and ColumnControl, if users ask for them. | DataTables extensions with their own request parts. | later |

## 5. Release criteria for 1.0

1. Every *must* is met and traces to spec statements whose tests ran and passed; no skipped test.
2. CI's `gate` is green at the release commit: lint, types, tests on 3.12-3.14, minimum versions, install isolation.
3. The PostgreSQL, SQL Server and OpenSearch container tests pass, including the proof for PRD-C5.
4. SvelteDT's recorded request fixtures (its PRD-W11), once they exist, parse with `parse_request` to the request each
   fixture states, and their answers map in SvelteDT's contract tests.
5. Every question in section 7 is answered and in the specs.
6. The package is published with the release workflow.

Before 1.0: v0.1 (core, SQL, OpenSearch, FastAPI adapter, SQLite tests) is published to reserve the name; v0.2 adds
the container tests and an acceptance suite.

## 6. Changes from draft 1

- Search: draft 1's global search matched the whole text in one column (OR across columns) and OpenSearch text took
  `OR`/`NOT`/`-`; now smart search with the row as the unit and one syntax (PRD-F1 to PRD-F3).
- Ordering: NULL placement was each database's default and the SQL tie-breaker always ascended; now NULL lowest and
  the tie-breaker follows the last direction (PRD-O2, PRD-O3), with a declared OpenSearch key (PRD-D7).
- Paging: `start` up to 2^53-1 (PRD-P5); OpenSearch deep pages near the end served, middle pages refused with
  `page_out_of_range` instead of `invalid_filter` (PRD-C5, PRD-C6).
- Errors: `invalid_date`, `invalid_number` and `page_out_of_range` beside `invalid_filter` (PRD-E2).
- New: the dotted form and the ignore rules (PRD-P2, PRD-P3), control-character refusal (PRD-F8), the empty
  categorical alternative (PRD-F9), expression columns (PRD-D6), the floors (PRD-Q3) and CI (PRD-Q6).

## 7. Open questions for the owner

1. OpenSearch middle pages: should a sequential `search_after` route, or raising `max_result_window` per index, remove
   the limit of PRD-C6? SvelteDT's pager can reach any page, so users will hit it by an ordinary press.
2. Which HTTP status does an answered user error (PRD-E1) carry: 200 with `error_code`, as DataTables expects, or a
   4xx the app chooses? SvelteDT must not show a refusal as "No data" (its PRD-B15).
3. A column the request marks searchable but the server did not declare: refuse the global search (today), or leave
   that column out of it?
4. Dotted and dotless i: SvelteDT's local source treats them alike in every locale (its PRD-B21, difference 3);
   should the server, or does PRD-F4 (the database decides) cover it?
5. Is `!word` the only exclusion, or is `-word` accepted as well (DataTables' smart search uses `!`)?
6. Does a 422 for `MalformedRequest` need a body carrying `draw`, for a client that matches answers by `draw`?
7. Should the total cache offer opt-in invalidation on writes? It stays out until a user needs fresher totals than a
   TTL gives.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Smart search per word multiplies the LIKE terms (words x columns) on large tables. | The specs bound the number of words; the container tests measure PostgreSQL and SQL Server plans. |
| Accent folding and text order differ per database, so server and local results differ. | Non-goals say so; the specs state each database's behaviour; PRD-D6 gives apps their database's means. |
| Reverse-sort paging (PRD-C5) is wrong under ties or a changing index. | It depends on PRD-O3's unique tie-breaker and needs the container proof before release. |

## 9. Trace from draft 1

| Draft 1 | Here |
|---|---|
| R1 request parsing | PRD-P1, P4, P6, P7, P8, P10 (bracket form now within P2; "numbers above 2^31-1" now P5, with `start` raised) |
| R2 column declaration | PRD-D1 to D5 |
| R3 ordering | PRD-P9 (index authority), PRD-O1, O3 (tie-breaker, now direction-following), O4 |
| R4 global search, column searches, "contains" | PRD-F1 to F3 (changed), F6, F7, F5 |
| R4 dates, numbers, "empty", SearchBuilder, base filter | PRD-F11 to F18 |
| R4 multi-select, `multi_values` | PRD-F9, F10 |
| R4 OpenSearch Google-style text query | dropped by the owner's decision of 2026-11-19 (PRD-F3) |
| R5 errors | PRD-E1 to E3, E6 to E8; the `max_result_window` refusal is now PRD-C6 with `page_out_of_range` |
| R6 counts, total cache, SQL Server parameters | PRD-C1, C3, C4, C7, C8 |
| R7 rows and response | PRD-R1 to R3 |
| R8 API shape | PRD-A1 to A6 |
| R9 packaging and quality | PRD-Q1, Q3 (floors raised), Q4, Q5, Q7 |
| Milestones v0.1, v0.2 | Section 5, before 1.0 |
| Milestone "later" | PRD-L1, L2 |
| Open questions (Elasticsearch adapter, write invalidation) | PRD-L1; section 7, question 7 |
