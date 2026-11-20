# Specs: errors (E)

Statements for PRD section 4.6. Form, tags, answers and the `items` fixture: see `README.md`. Log statements read the
records of the logger `datatables_backend` (pytest's `caplog`); a record's **event name** is its message as logged
(`record.msg`), a fixed snake_case text per case such as `datatables_request_malformed`.

- **SPEC-E1.1** [all] SearchBuilder `=` `abc` on `price`, with `draw` 7 → `fetch` and `fetch_sync` return (do not
  raise) a `DTResponse` with `draw` 7, `error_code` `invalid_number`, a non-empty `error`, `data` `[]`,
  `recordsTotal` 0 and `recordsFiltered` 0 (no count runs before the refusal, PRD-E7).
- **SPEC-E1.2** [all] SearchBuilder `<` `a` on `name` → `fetch` and `fetch_sync` return the answer with error_code
  `invalid_filter`; `InvalidFilter` does not propagate out of either.
- **SPEC-E2.1** [all] Across every user-error case in the specs, `fetch` answers one of exactly `invalid_filter`,
  `invalid_date`, `invalid_number`, `page_out_of_range`; it never answers another code (`query_failed` is the app's,
  PRD-E5).
- **SPEC-E2.2** [all] The four codes are stable: each case in P, D, F, O and C that names a code answers that code,
  and the four are listed in the documentation of `DTResponse.error_code`.
- **SPEC-E3.1** [all] A request holding both a U+0000 in the global search and `=` `abc` on `price` → `invalid_filter`
  (step 1 before step 2).
- **SPEC-E3.2** [all] A request holding a 17-word global search and `=` `2024-13-01` on `made` → `invalid_filter`;
  holding a SearchBuilder tree 9 deep and `=` `abc` on `price` → `invalid_filter`.
- **SPEC-E3.3** [all] A request holding SearchBuilder `=` `abc` on `price` and `contains` `x` on `active` →
  `invalid_number` (step 2 before step 4); holding `=` `2024-13-01` on `made` and a column search `x` on the
  undeclared `extra` → `invalid_date`.
- **SPEC-E3.4** [SQL] A request holding SearchBuilder `=` `9223372036854775808` on `price` (step 3) and SearchBuilder
  `<` `a` on `name` (step 4) → `invalid_number`; SearchBuilder `=` `9999-12-31` on `made` and a column search `yes`
  on `active` → `invalid_date`.
- **SPEC-E3.5** [OS] With `max_result_window` 3 and rows 1 to 10, `start` 4, `length` 3 (a page PRD-C12 refuses) and
  SearchBuilder `=` `yes` on `active` → `invalid_filter`, not `page_out_of_range`.
- **SPEC-E3.6** [OS] Column search `7 8` on `price` → `invalid_number`; `!2024` on `made` → `invalid_date` (step 2).
- **SPEC-E4.1** [all] For each user-error case, two requests that differ only in the offending value (`abc` vs
  `xyz`, `2024-13-01` vs `2025-14-02`) answer the same `error` text, and the text contains neither value.
- **SPEC-E4.2** [all] A refusal on the declared column `price` may name `price`; a refusal on an undeclared column
  `secret_field` answers a text that does not contain `secret_field`.
- **SPEC-E5.1** [core] `DTResponse.query_failed(5)` gives `draw` 5, `error_code` `query_failed`, a non-empty `error`,
  `recordsTotal` 0, `recordsFiltered` 0 and `data` `[]`.
- **SPEC-E5.2** [all] When the connection or client raises during `fetch`, the call raises; it never returns
  `query_failed`.
- **SPEC-E6.1** [core] `parse_request` with `length` `abc` and `draw` `3` raises `MalformedRequest` whose `draw` is 3;
  it is not answered as `invalid_filter`.
- **SPEC-E6.2** [core] `parse_request` with `draw` `x` raises `MalformedRequest` whose `draw` is None; with `draw`
  `2147483648` (over PRD-P10) its `draw` is None.
- **SPEC-E6.3** [core] `MalformedRequest` is not a subclass of `InvalidFilter`, nor `InvalidFilter` of
  `MalformedRequest`.
- **SPEC-E7.1** [SQL] Recording: a request that ends in a user error runs no statement (no page, no count).
- **SPEC-E7.2** [OS] Recording: a user error other than `page_out_of_range` sends no call; a `page_out_of_range`
  answer sends only the counts of PRD-C11, never a page search.
- **SPEC-E8.1** [SQL] The table's SQLAlchemy metadata declares a column `ghost` that the database table lacks →
  `fetch` raises the database error; it is not answered.
- **SPEC-E8.2** [SQL] A connection whose execute raises `OperationalError` → `fetch` and `fetch_sync` raise it.
- **SPEC-E8.3** [OS] A client that raises a transport error → `fetch` raises it.
- **SPEC-E9.1** [OS] A search answer with `_shards.failed` 1 → `PartialResultError` raised; with `_shards.successful`
  below `_shards.total` → `PartialResultError` raised.
- **SPEC-E9.2** [OS] A search answer with no `_shards`, or with `_shards.total` not an integer → `PartialResultError`
  raised.
- **SPEC-E9.3** [OS] A count answer that does not cover every shard → `PartialResultError` raised.
- **SPEC-E10.1** [all] For every refusal in the specs, no log record of any level holds the search value or a
  client-sent column name: with a marker string in each value and in an undeclared name, the marker appears in no
  record's message, arguments or extra fields.
- **SPEC-E11.1** [all] A refusal on the undeclared `secret_field` logs a record that does not name it.
- **SPEC-E12.1** [all] Every record the library emits comes from the logger `datatables_backend` or a child of it,
  through the standard `logging` module.
- **SPEC-E13.1** [all] Each `MalformedRequest` raised and each user error answered logs exactly one WARNING record;
  the same case twice logs the same event name twice, and two different cases (an undeclared column search and a
  refused order) log different event names.
- **SPEC-E13.2** [all] A request that holds DataTables' default order on a column unorderable on both sides, an idle
  SearchPanes pane, an idle ColumnControl search, a nameless column, a request-searchable undeclared column and a
  global search `red` (skipping the boolean `active`, PRD-F44, and on OS the number and date columns, PRD-F32) logs
  no record.
- **SPEC-E13.3** [all] A request holding two mistakes logs one WARNING (for the code answered) and answers no rows.
