# Specs: API (A)

Statements for PRD section 4.8. Form, backends, answers and the `items` fixture: see `README.md`. Statements on the
FastAPI dependency go through a FastAPI app and its `TestClient`.

- **SPEC-A1.1** [all] One table answers request X, then request Y, then X again: the two answers to X are equal (no
  state from Y leaks).
- **SPEC-A2.1** [all] One table object answers 100 requests in turn and 20 concurrent async requests, each answer
  equal to that of a fresh table.
- **SPEC-A3.1** [all] Constructing a table with a connection, engine or client that raises on any use succeeds: the
  constructor makes no call.
- **SPEC-A4.1** [SQL, OS] `await table.fetch(params, conn)` with an `AsyncSession`, an `AsyncConnection` or an
  `AsyncOpenSearch` answers the base request with rows 1, 2, 3, 4.
- **SPEC-A5.1** [SQL, OS] `table.fetch_sync(params, conn)` with a `Session`, a `Connection` or an `OpenSearch`
  client answers the base request with rows 1, 2, 3, 4.
- **SPEC-A6.1** [SQL] Recording: for a request with a user error found while building the page query, no statement
  runs (PRD-E7); for a valid one, the counts and the page run, and no statement runs before the query is built.
- **SPEC-A7.1** [SQL] A table over a `Table`, a `join` of two tables (with a declared key) and a subquery each
  answers with the expected rows.
- **SPEC-A8.1** [OS] A table over an index name sends plain dict bodies; the package imports no `opensearch_dsl` or
  `opensearchpy.helpers.search`.
- **SPEC-A9.1** [FastAPI] A POST with a form body and a query string holding `_` and `draw=99` reads the body only:
  the answer's `draw` is the body's.
- **SPEC-A9.2** [FastAPI] A POST with a JSON object body sent as `application/x-www-form-urlencoded` is read as JSON.
- **SPEC-A9.3** [FastAPI] A GET with an empty body reads the query string.
- **SPEC-A10.1** [FastAPI] A malformed request with `draw` 5 → HTTP 422 with a body carrying `draw` 5; with `draw`
  `x` → 422 with no `draw`.
- **SPEC-A11.1** [FastAPI] SearchBuilder `=` `abc` on `price` → HTTP 200 with `error`, `error_code`
  `invalid_number`, `draw`, `recordsTotal` 4, `recordsFiltered` and `data` `[]`.
- **SPEC-A11.2** [FastAPI] Each of the four user-error codes answers HTTP 200; the dependency takes no option that
  changes the status.
