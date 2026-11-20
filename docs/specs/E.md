# Specs: errors (E)

Statements for PRD section 4.6. Form, backends, answers and the `items` fixture: see `README.md`. Log statements read
the records of the logger `datatables_backend` (pytest's `caplog`).

- **SPEC-E1.1** [all] SearchBuilder `=` `abc` on `price`, with `draw` 7 → `fetch` and `fetch_sync` return (do not
  raise) a `DTResponse` with `draw` 7, `error_code` `invalid_number`, a non-empty `error`, `data` `[]`, and
  `recordsTotal` 4.
- **SPEC-E1.2** [all] An answered user error does not raise `InvalidFilter` out of `fetch` or `fetch_sync`.
- **SPEC-E2.1** [all] Every user error the specs name answers one of exactly `invalid_filter`, `invalid_date`,
  `invalid_number`, `page_out_of_range`; no other `error_code` is produced by the library.
- **SPEC-E2.2** [all] The four codes are stable: each case in F, D and C that names a code answers that code, and the
  four codes are listed in the documentation of `DTResponse.error_code`.
- **SPEC-E3.1** [all] A request holding both a U+0000 in the global search and `=` `abc` on `price` → `invalid_filter`
  (step 1 before step 2).
- **SPEC-E3.2** [all] A request holding a 17-word global search and `=` `2024-13-01` on `made` → `invalid_filter`;
  holding a SearchBuilder tree 9 deep and `=` `abc` on `price` → `invalid_filter`.
- **SPEC-E3.3** [all] A request holding `=` `abc` on `price` and `contains` `x` on `active` → `invalid_number`
  (step 2 before step 4); holding `=` `2024-13-01` on `made` and an undeclared column search → `invalid_date`.
- **SPEC-E3.4** [all] A request holding `=` `9223372036854775808` on `price` (step 3) and `<` on `name` (step 4) →
  `invalid_number`; `=` `9999-12-31` on `made` and `yes` on `active` → `invalid_date`.
- **SPEC-E3.5** [OS] A page beyond `max_result_window` (PRD-C12) that also holds `=` `yes` on `active` →
  `invalid_filter`, not `page_out_of_range`.
- **SPEC-E3.6** [OS] Column search `7 8` on `price` → `invalid_number`; `!2024` on `made` → `invalid_date` (step 2).
- **SPEC-E4.1** [all] For each user-error case, two requests that differ only in the offending value (`abc` vs
  `xyz`, `2024-13-01` vs `2025-14-02`) answer the same `error` text, and the text contains neither value.
- **SPEC-E4.2** [all] A refusal on the declared column `price` may name `price`; a refusal on an undeclared column
  `secret_field` answers a text that does not contain `secret_field`.
- **SPEC-E5.1** [all] `DTResponse.query_failed(5)` gives `draw` 5, `error_code` `query_failed`, a non-empty `error`,
  `recordsTotal` 0, `recordsFiltered` 0 and `data` `[]`.
- **SPEC-E5.2** [all] When the connection raises during `fetch`, the call raises; it never returns `query_failed`.
- **SPEC-E6.1** [all] `parse_request` with `length` `abc` and `draw` `3` raises `MalformedRequest` whose `draw` is 3;
  it is not answered as `invalid_filter`.
- **SPEC-E6.2** [all] `parse_request` with `draw` `x` raises `MalformedRequest` whose `draw` is None; with `draw`
  `2147483648` (over PRD-P10) its `draw` is None.
- **SPEC-E6.3** [all] `MalformedRequest` is not a subclass of `InvalidFilter`, nor `InvalidFilter` of
  `MalformedRequest`.
- **SPEC-E7.1** [SQL] With a connection that records every statement it runs: a request that ends in a user error
  runs no statement (no page, no count).
- **SPEC-E7.2** [OS] With a recording client: a user error other than `page_out_of_range` sends no search; a
  `page_out_of_range` answer sends only the counts of PRD-C11, never a page search.
- **SPEC-E8.1** [SQL] A table declared over a column the database lacks → `fetch` raises the database error; it is
  not answered.
- **SPEC-E8.2** [SQL] A connection whose execute raises `OperationalError` → `fetch` and `fetch_sync` raise it.
- **SPEC-E8.3** [OS] A client that raises a transport error → `fetch` raises it.
- **SPEC-E9.1** [OS] A search answer with `_shards.failed` 1 → `PartialResultError`; with `_shards.successful` below
  `_shards.total` → `PartialResultError`.
- **SPEC-E9.2** [OS] A search answer with no `_shards`, or with `_shards.total` not an integer → `PartialResultError`.
- **SPEC-E9.3** [OS] A count answer that does not cover every shard → `PartialResultError`.
- **SPEC-E10.1** [all] For every refusal in the specs, no log record of any level holds the search value or a
  client-sent column name (a request built with a marker string in each value and in an undeclared name; the marker
  appears in no record's message or arguments).
- **SPEC-E11.1** [all] A refusal on the declared `price` logs a record naming `price`; a refusal on the undeclared
  `secret_field` logs a record that does not name it.
- **SPEC-E12.1** [all] Every record the library emits comes from the logger `datatables_backend` or a child of it,
  through the standard `logging` module; the library adds no handler.
- **SPEC-E13.1** [all] Each `MalformedRequest` raised and each user error answered logs exactly one WARNING; the
  same case twice logs the same event name twice.
- **SPEC-E13.2** [all] A request with DataTables' default order on an unorderable column, an idle SearchPanes pane,
  an idle ColumnControl search, a nameless column and a request-searchable undeclared column logs nothing.
- **SPEC-E13.3** [all] A request holding two mistakes logs one WARNING (for the code answered) and answers no rows.
