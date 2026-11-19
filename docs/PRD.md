# datatables-backend: product requirements

Status: draft 1 (2026-11-19). Derived from the DataTables code of an existing application, without its
application specifics.

## Problem

[DataTables](https://datatables.net) tables that page, sort and filter on the server send a fixed request: paging,
ordering, a global search, per-column searches and, with the SearchBuilder extension, a tree of AND/OR criteria. Every
Python backend has to turn that request into a query safely. Getting it wrong leads to unbounded result sets, wildcard
injection, errors that echo user input, and sorts or filters on columns the page never exposed.

What exists as of 2026-11 (researched 2026-11-19):

- `datatables_server` (SpryMedia, the DataTables authors) covers SQL through SQLAlchemy 2 Core with a sync
  `Connection`, including Editor (CRUD), SearchBuilder, SearchPanes and ColumnControl. It has no native async API and
  no search-engine backend.
- No Python library serves DataTables from OpenSearch or Elasticsearch.
- The older SQLAlchemy libraries (`sqlalchemy-datatables`, `datatables`, `sqlalchemy2-datatables`) are unmaintained.

## Goal

A framework-agnostic Python library that answers DataTables server-side requests, read-only, from several kinds of
data source. Every source shares one request model, one column declaration and one set of safety rules. Each source
is a small backend:

1. SQL through SQLAlchemy 2: async and sync.
2. OpenSearch: async and sync clients.
3. Elasticsearch, and other sources later, behind the same backend contract.

## Users

- Developers of Python web apps (FastAPI, Starlette, Flask, Django with SQLAlchemy) who show database or search-index
  data in DataTables.

## Non-goals

- Editing (DataTables Editor). `datatables_server` covers it for SQL.
- Rendering HTML or shipping JavaScript.
- Exports, audit logging, authentication and authorisation. These belong to the app. The library gives the app a
  request whose paging it can replace.
- Regular-expression search. Client regexes are refused; see R5.
- SearchPanes and ColumnControl in v1. Candidates for later.

## Requirements

### R1. Request parsing

- Accept the request as a decoded JSON object (nested) or as flat form/query parameters in bracket notation
  (`columns[0][search][value]`), which is DataTables' default `ajax` encoding, in dotted notation
  (`columns[0].search.value`, as spring-data-jpa-datatables reads it, or `columns.0.search.value`), or mixed. Every
  form reads to the same request. Strings `"true"`/`"false"` are booleans.
- Flat keys that are not the protocol's (DataTables' GET cache-buster `_`, an app's own `ajax.data` keys) are ignored,
  and so are unknown fields under a protocol key (e.g. DataTables' `search[fixed]`). A key that starts with a protocol
  parameter's name but has no readable shape is refused as malformed.
- Validate into typed models: `DTParams`, `DTColumn`, `DTOrder`, `Search`, and the recursive SearchBuilder tree.
- Refuse a malformed request with `MalformedRequest`. Its message names the parameter, never the client's value.
  Malformed means:
  - wrong structure;
  - a non-integer paging number;
  - a negative `start`;
  - `length` below 1 or above the configured maximum (default 1000), so "All" (`-1`) is refused;
  - numbers above 2³¹−1;
  - text JSON cannot encode (lone surrogates).
- A column's `name` falls back to its `data`.

### R2. Column declaration (the server's schema)

- The server declares the table's columns. Each declaration has:
  - the client-facing name;
  - the backend field, if different from the name;
  - the value type: string, number or date;
  - whether the column is searchable and whether it is orderable;
  - an optional hook that transforms a search value before it reaches the query (e.g. to encrypt or encode it).
- Any name the request uses to search, filter or order on must be a declared name; otherwise the request is refused.
  This covers column searches, the global search, `order` and every SearchBuilder `origData`. A backend field never
  becomes reachable just because the client names it.
- The declared type decides how a criterion is built. The client's SearchBuilder `type` is ignored, so a wrong
  client type cannot change how a value is matched.
- SQL infers the type from the column when none is declared. OpenSearch defaults to string.

### R3. Ordering

- `order[i][column]` indexes the request's own `columns`. An index outside them is refused, as is an order on a column
  the request marks unorderable or the server declares unorderable.
- SQL appends the primary key, or the first column if there is none, after the user's order, so pages are
  deterministic and a tie never splits across a page boundary.
- OpenSearch sorts a string on its keyword form. An order on analysed-only text is dropped with a warning, because
  OpenSearch would reject the whole search.

### R4. Filtering semantics

- **Global search:** OR across the searchable declared columns. A search over no usable column is refused rather than
  answered with every row.
- **Column searches:** combined with AND.
- **"Contains":** case-insensitive substring match on every backend. SQL escapes LIKE wildcards (`%`, `_`, `[`, `\`).
  OpenSearch escapes wildcard characters.
- **Dates:**
  - Matched by day: `=`, `<`, `<=`, `>`, `>=` and `between` refer to whole days, for date and datetime columns alike.
  - SearchBuilder's "contains" on a date column takes a year, a year-month or a day (`YYYY`, `YYYY-MM`,
    `YYYY-MM-DD`) and means that span, on every backend.
  - The global search and column searches are free text. On SQL they match a date's `YYYY-MM-DD` text; on
    OpenSearch they skip date columns in the global search, and a column search takes a year, month or day as above.
  - The maximum date (9999-12-31) is a user error, not a failed query.
- **Numbers:** parsed and bounded. Non-finite and non-numeric values are refused, and so is a value outside the
  column's SQL integer range.
- **"Empty":** NULL, plus `''` on SQL string columns. On OpenSearch it means the field is missing.
- **SearchBuilder:** the full condition set (`=`, `!=`, `<`, `<=`, `>`, `>=`, `between`, `!between`, `contains`,
  `!contains`, `starts`, `!starts`, `ends`, `!ends`, `null`, `!null`), nested groups, and AND/OR. An empty group
  adds nothing. A condition that does not fit the column's type is refused with a reason. A criterion missing its
  values is refused.
- **Multi-select:** a column search with `regex: true` whose regex is only literal alternatives (`a|b`, `^(a|b)$`,
  escaped or not) is read as an exact-match IN list, following DataTables' multi-select convention. Any other regex
  is refused. A `multi_values` list in the column's search object (an extension to the DataTables
  protocol) does the same.
- **Base filter:** an optional server filter (a SQL expression or an OpenSearch query) limits the rows the table
  shows at all. It applies to `recordsTotal`, `recordsFiltered` and the page alike.
- **OpenSearch text fields:** declared as analysed text, they take a Google-style query: implicit AND, `OR`, `NOT`/`-`
  and quoted phrases.

### R5. Errors

- A request the user can fix becomes a response whose `error` is a fixed, user-readable reason and whose `error_code`
  is `invalid_filter`. It covers:
  - an undeclared column;
  - a bad value;
  - a wrong condition for the type;
  - a regex.

  The reason never contains the user's input.
- Everything else raises: a database error, a schema the table lacks, a partial OpenSearch answer. The app maps it to
  its own error response; `DTResponse.query_failed(draw)` gives the conventional DataTables shape.
- An OpenSearch page beyond `max_result_window` (default 10,000) is refused as `invalid_filter` before anything is
  sent.
- An OpenSearch answer that does not cover every shard, or whose shard information cannot be read, is never shown as
  complete. It raises `PartialResultError`.
- Logs never contain search values or client-supplied names. A refused name is logged only if it is a declared name.
  The standard `logging` module is used, under the logger `datatables_backend`.

### R6. Counts and performance

- **SQL:**
  - `recordsTotal` is a `COUNT(*)` over the base filter.
  - `recordsFiltered` is a `COUNT(*)` over the filtered statement.
  - The page uses OFFSET/LIMIT.
  - Only the declared columns are selected.
- **OpenSearch:**
  - One search returns the page and the exact filtered count (`track_total_hits`).
  - `recordsTotal` reuses that count when the request filters nothing beyond the base filter.
  - A count that is only a lower bound is replaced by an exact count request.
- **Optional total cache:** `recordsTotal` can be cached with a caller-supplied TTL cache, keyed by the table, the
  base filter and the database or index. The library registers no global hooks on engines or pools. Invalidation is
  by TTL or by an explicit call.
- **SQL Server (2100-parameter limit):** an IN list that would push the statement over the limit is sent as one JSON
  parameter (`OPENJSON`). SQLite uses `json_each`. Other dialects bind normally.

### R7. Rows and response

- Each row is a dict keyed by the client-facing names. An optional row hook can reshape a row, e.g. through a Pydantic
  model. Optional `DT_RowId` and `DT_RowData` hooks add those keys.
- `DTResponse` has `draw`, `recordsTotal`, `recordsFiltered` and `data`, plus `error` and `error_code` when set.

### R8. API shape

- A table object holds only the server-side declaration: the source, the columns, the base filter and the hooks. It
  is built once and reused across requests.
- `await table.fetch(params, conn)` answers one request; `table.fetch_sync(params, conn)` is the sync version. The
  query is built entirely before anything runs, so a filter error is never a half-run query. Constructors do no I/O.
- **SQL:** the table takes a SQLAlchemy `FromClause` (a table, join or subquery). It accepts an `AsyncSession` or
  `AsyncConnection` (async), and a `Session` or `Connection` (sync).
- **OpenSearch:** the table takes an index name. It accepts an `AsyncOpenSearch` or `OpenSearch` client. Queries are
  plain request bodies, with no DSL dependency.
- **Optional FastAPI adapter:** a dependency that parses the request (JSON body, form or query) and answers 422 on
  `MalformedRequest`.

### R9. Packaging and quality

- The distribution is `datatables-backend` and the import is `datatables_backend`.
- Extras are `[sqlalchemy]`, `[opensearch]` and `[fastapi]`. The core depends only on Pydantic 2.
- Python 3.12 or later. Python 3.10 reached end of life in 2026-10, and the code uses PEP 695 syntax.
- Fully typed (`py.typed`), checked with basedpyright and ruff.
- Tests:
  - SQL runs against SQLite (sync and aiosqlite) by default.
  - PostgreSQL and SQL Server run in CI with containers.
  - OpenSearch is unit-tested on the request bodies the library builds and integration-tested against a container.
- MIT licence.

## Milestones

1. **v0.1:** core, SQL (async and sync), OpenSearch (async and sync), FastAPI adapter, SQLite tests. Published to
   reserve the name.
2. **v0.2:** CI with PostgreSQL, SQL Server and OpenSearch containers. Acceptance cases from the original
   application ported as behaviour tests.
3. **Later:** an Elasticsearch backend, plus SearchPanes and ColumnControl if anyone asks for them.

## Open questions

- Elasticsearch: the 8.x and 9.x Python clients changed the `body=` API. This needs a thin client adapter, decided
  when Elasticsearch work starts.
- Should the total cache offer opt-in invalidation on writes? It stays out until a user needs fresher totals than a
  TTL gives.
