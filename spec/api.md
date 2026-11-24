# Specs: API (A)

Statements for PRD section 4.8. Form, tags, answers, the base request and the `items` fixture: see `README.md`.

- **SPEC-A1.1** [all] One table answers request X (global search `red`), then request Y (column search `blue` on
  `colour`, order `price` desc), then X again: the two answers to X are equal, and on OS so are the request bodies
  sent for them (no state from Y leaks).
- **SPEC-A2.1** [all] One table object answers 100 requests in turn and 20 concurrent async requests, each answer
  equal to that of a freshly built table for the same request; on OS the request bodies sent are equal too. On SQL
  each request runs on its own `AsyncConnection`; on OS all share one `AsyncOpenSearch` client.
- **SPEC-A3.1** [all] `SQLTable(source, columns)` and the OpenSearch table (index name, columns, key) take no
  connection or client: they construct with no database or cluster reachable, and the first call a recording
  connection or client sees is made inside `fetch` or `fetch_sync`.
- **SPEC-A4.1** [all] `await table.fetch(params, conn)` with an `AsyncSession`, an `AsyncConnection` (SQL) or an
  `AsyncOpenSearch` client (OS) answers the base request with rows 1, 2, 3, 4.
- **SPEC-A5.1** [all] `table.fetch_sync(params, conn)` with a `Session`, a `Connection` (SQL) or an `OpenSearch`
  client (OS) answers the base request with rows 1, 2, 3, 4.
- **SPEC-A6.1** [SQL] Recording: a request whose user error is found only while the page query is built
  (SearchBuilder `=` `9223372036854775808` on the `BigInteger` `price`, out of its range) runs no statement, not
  even a count.
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
- **SPEC-A9.4** [FastAPI] A POST whose body is the JSON object of the base request with `draw` 2 is read as JSON
  under `application/json` (also with a charset), under `application/x-www-form-urlencoded; charset=UTF-8` (what
  DataTables' `submitAs: 'json'` sends unless the page sets a Content-Type), under `text/plain` and with none; a JSON
  body over 1 MB under the form type is read too (Starlette's form parser would refuse a field of that size).
- **SPEC-A9.5** [FastAPI] A POST whose body is the form of the base request with `draw` 2 is read as a form under
  `application/x-www-form-urlencoded`, under `multipart/form-data` (a file field beside it is ignored), under
  `text/plain` and with no Content-Type.
- **SPEC-A9.6** [FastAPI] A body that is no JSON object and no form (`{not json`, `[1]`, `"x"`, `null`, a JSON object
  with no DataTables parameter, a JSON list of pairs) → HTTP 422 whose message says to send the parameters as a JSON
  object or as a form, under each of the four Content-Types.
- **SPEC-A9.7** [FastAPI] A body nested too deep to decode (`[` or `{"a":` repeated 700,000 and 150,000 times, and a
  JSON object whose value nests 300,000 lists) → HTTP 422 under `application/json`, the form type and `text/plain`,
  never a server error; so is one of several megabytes under `application/json`.
- **SPEC-A9.8** [FastAPI] A form of 1,001 fields under `text/plain` → HTTP 422 (the 1,000 fields Starlette allows).
- **SPEC-A9.9** [FastAPI] A POST whose client disconnects before the body, or in the middle of it (ASGI `receive`
  answering `http.disconnect`) → the app returns, no exception leaves it, and the first message is HTTP 400 whose
  `detail` is the text `The request body could not be read: the client disconnected; send the request again.`, which
  names no value and says what to do, with no `draw`; it is no 422 (PRD-E6).
- **SPEC-A10.1** [FastAPI] The base request with `draw` 5 and `length` `abc` → HTTP 422 in FastAPI's default shape,
  with `draw` inside `detail`: `{"detail": {"draw": 5, ...}}` (the owner's decision of 2026-11-20); the same request
  with `draw` `x` → HTTP 422 whose `detail` holds no `draw` key.
- **SPEC-A11.1** [FastAPI] SearchBuilder `=` `abc` on `price` → HTTP 200 with `error`, `error_code`
  `invalid_number`, `draw`, `recordsTotal` 0, `recordsFiltered` 0 and `data` `[]`.
- **SPEC-A11.2** [FastAPI] Through an app over an OpenSearch table whose client is never asked (each case is refused
  before a server is asked; `page_out_of_range` exists only there, PRD-C12), each of these answers HTTP 200 with its
  code and the request's `draw`: SearchBuilder `<` `a` on `name` → `invalid_filter`; a global search holding U+0009 →
  `invalid_character`; a global search of 256 code points → `value_too_long`; a global search of 17 words →
  `too_many_words`; `=` `2024-13-01` on `made` → `invalid_date`; `=` `abc` on `price` → `invalid_number`; with
  `max_result_window` 3, `start` 4, `length` 3 → `page_out_of_range`. The dependency takes no option that changes the
  status.
- **SPEC-A11.3** [FastAPI] Through an app over an OpenSearch table of 61 text columns, a global search of 16 words
  (976 conditions) → HTTP 200 with error_code `request_too_large` and the request's `draw`.
