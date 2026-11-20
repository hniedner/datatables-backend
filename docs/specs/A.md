# Specs: API (A)

Statements for PRD section 4.8. Form, tags, answers, the base request and the `items` fixture: see `README.md`.

- **SPEC-A1.1** [all] One table answers request X (global search `red`), then request Y (column search `blue` on
  `colour`, order `price` desc), then X again: the two answers to X are equal (no state from Y leaks).
- **SPEC-A2.1** [all] One table object answers 100 requests in turn and 20 concurrent async requests, each answer
  equal to that of a freshly built table for the same request.
- **SPEC-A3.1** [all] `SQLTable(source, columns)` and the OpenSearch table (index name, columns, key) take no
  connection or client: they construct with no database or cluster reachable, and the first call a recording
  connection or client sees is made inside `fetch` or `fetch_sync`.
- **SPEC-A4.1** [all] `await table.fetch(params, conn)` with an `AsyncSession`, an `AsyncConnection` (SQL) or an
  `AsyncOpenSearch` client (OS) answers the base request with rows 1, 2, 3, 4.
- **SPEC-A5.1** [all] `table.fetch_sync(params, conn)` with a `Session`, a `Connection` (SQL) or an `OpenSearch`
  client (OS) answers the base request with rows 1, 2, 3, 4.
- **SPEC-A6.1** [SQL] Recording: a request whose user error is found while the page query is built (SearchBuilder
  `=` `abc` on `price`) runs no statement.
- **SPEC-A7.1** [SQL] Given a second table `stock` (`id`, `item_id`, `qty`) holding (1, 1, 5) and (2, 3, 0): a table
  over `items` joined to `stock` on `item_id`, with `stock.id` declared as the key, answers rows for items 1 and 3; a
  table over the subquery of `items` where `price > 7` answers rows 2, 3.
- **SPEC-A8.1** [OS] The OpenSearch backend's source imports nothing from `opensearchpy` (checked by reading its
  imports), and every body a recording client receives is a plain `dict` of JSON values.
- **SPEC-A9.1** [FastAPI] A POST with a form body holding `draw=5` and a query string holding `_` and `draw=99` reads
  the body only: the answer's `draw` is 5.
- **SPEC-A9.2** [FastAPI] A POST with a JSON object body sent as `application/x-www-form-urlencoded` is read as JSON.
- **SPEC-A9.3** [FastAPI] A GET with no body, and a POST with an empty body, read the query string.
- **SPEC-A10.1** [FastAPI] A malformed request with `draw` 5 → HTTP 422 whose JSON body carries `"draw": 5` as a
  top-level key beside `detail`; with `draw` `x` → HTTP 422 with no `draw` key. Where `draw` sits is open question
  OQ3 in `README.md`.
- **SPEC-A11.1** [FastAPI] SearchBuilder `=` `abc` on `price` → HTTP 200 with `error`, `error_code`
  `invalid_number`, `draw`, `recordsTotal` 0, `recordsFiltered` 0 and `data` `[]`.
- **SPEC-A11.2** [FastAPI] Each of the four user-error codes answers HTTP 200; the dependency takes no option that
  changes the status.
