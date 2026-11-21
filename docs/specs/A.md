# Specs: API (A)

Statements for PRD section 4.8. Form, tags, answers, the base request and the `items` fixture: see `README.md`.

- **SPEC-A1.1** [all] One table answers request X (global search `red`), then request Y (column search `blue` on
  `colour`, order `price` desc), then X again: the two answers to X are equal (no state from Y leaks).
- **SPEC-A2.1** [all] One table object answers 100 requests in turn and 20 concurrent async requests, each answer
  equal to that of a freshly built table for the same request. On SQL each request runs on its own
  `AsyncConnection`; on OS all share one `AsyncOpenSearch` client.
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
  over `items` joined to `stock` on `items.id = stock.item_id`, whose columns the join keys as `items_<col>` and
  `stock_<col>`, with every fixture column declared over its join column (`Column('id', field='items_id')`,
  `Column('name', field='items_name')`, ...) and `stock_id` declared as the key, answers two rows carrying `id` 1
  and 3; a table over the subquery of `items` where `price > 7` answers rows
  2, 3.
- **SPEC-A8.1** [OS] Every body a recording client receives is a plain `dict` of JSON values. [review] No query DSL
  is imported: no `opensearchpy.helpers` query or search classes, no `opensearch_dsl`.
- **SPEC-A9.1** [FastAPI] A POST whose form body is the base request with `draw` 5, and whose query string holds
  `_`, `draw=99` and `search[value]=zzz`, reads the body only: the answer's `draw` is 5 and its rows are 1, 2, 3, 4
  (a merge would apply `zzz` and empty the page).
- **SPEC-A9.2** [FastAPI] A POST whose body is the JSON object of the base request with `draw` 6, sent as
  `application/x-www-form-urlencoded`, is read as JSON: the answer's `draw` is 6.
- **SPEC-A9.3** [FastAPI] With the base request with `draw` 8 as the query string, a GET with no body and a POST
  with an empty body each answer HTTP 200 with `draw` 8 and rows 1, 2, 3, 4.
- **SPEC-A10.1** [FastAPI] A malformed request with `draw` 5 → HTTP 422 whose JSON body carries `"draw": 5` as a
  top-level key beside `detail` (the owner's decision of 2026-11-20); with `draw` `x` → HTTP 422 with no `draw`
  key.
- **SPEC-A11.1** [FastAPI] SearchBuilder `=` `abc` on `price` → HTTP 200 with `error`, `error_code`
  `invalid_number`, `draw`, `recordsTotal` 0, `recordsFiltered` 0 and `data` `[]`.
- **SPEC-A11.2** [FastAPI] Through an app over an OpenSearch table with a recording client (`page_out_of_range`
  exists only there, PRD-C12), each of these answers HTTP 200 with its code: SearchBuilder `<` `a` on `name` →
  `invalid_filter`; `=` `2024-13-01` on `made` → `invalid_date`; `=` `abc` on `price` → `invalid_number`; with
  `max_result_window` 3 and rows 1 to 10, `start` 4, `length` 3 → `page_out_of_range`. The dependency takes no option
  that changes the status.
