# Specs: ordering (O)

Statements for PRD section 4.4. Form, backends, answers and the `items` fixture: see `README.md`. Here the answer
lists rows in the order they come back.

- **SPEC-O1.1** [all] `order[0][column]` 8 on a request with 8 columns (indexes 0 to 7) → `MalformedRequest` naming
  `order`.
- **SPEC-O1.2** [all] An order on `price`, request `orderable: true`, server-declared unorderable → error_code
  `invalid_filter`.
- **SPEC-O1.3** [all] An order on `price` desc, request `orderable: false`, server-declared orderable → rows 2, 3, 1,
  4 (answered).
- **SPEC-O1.4** [all] An order on `secret` (server-unorderable) with request `orderable: false` → skipped: rows 1, 2,
  3, 4, no error.
- **SPEC-O1.5** [all] An order on an undeclared column `extra` with request `orderable: false` → skipped: rows 1, 2,
  3, 4; with `orderable: true` → error_code `invalid_filter` (PRD-D6).
- **SPEC-O1.6** [all] An order on a nameless column (`data` null, empty `name`) → skipped: rows 1, 2, 3, 4, whatever
  its `orderable` (PRD-P21).
- **SPEC-O1.7** [all] DataTables' first request (`order[0][column]` 0, `dir` asc) on a table whose column 0 is
  undeclared and request-unorderable → rows 1, 2, 3, 4, no error.
- **SPEC-O2.1** [all] Order `price` asc → rows 4, 1, 3, 2 (NULL first); desc → rows 2, 3, 1, 4 (NULL last).
- **SPEC-O2.2** [all] Order `made` asc → rows 3, 4, 1, 2; desc → rows 2, 1, 4, 3.
- **SPEC-O2.3** [all] Order `active` asc → rows 3, 2, 1, 4 (NULL, false, true; ties by key); desc → rows 4, 1, 2, 3.
- **SPEC-O2.4** [PostgreSQL] O2.1 holds though PostgreSQL's default puts NULL last ascending.
- **SPEC-O3.1** [all] Given rows 5 and 6 with `colour` `blue` like row 3: order `colour` asc → the blue rows come as
  3, 5, 6; desc → 6, 5, 3 (the key follows the first applied entry).
- **SPEC-O3.2** [all] For every `length` from 1 to 6 over rows 1 to 6 ordered by `colour` asc, the pages joined give
  each row exactly once, and the order desc gives exactly the reverse sequence.
- **SPEC-O3.3** [all] With rows 5 and 6 as in O3.1: order `secret` desc (request-unorderable, server-unorderable, so
  skipped) then `colour` asc → the blue rows come as 3, 5, 6: the key follows `colour`, not the skipped entry.
- **SPEC-O3.4** [OS, SQL join] A table over a join or an OpenSearch index declares a unique key; with two rows equal
  in the ordered column, the tie is broken by that key.
- **SPEC-O4.1** [all] No order in the request → rows 1, 2, 3, 4; with rows inserted in the order 3, 1, 4, 2, still
  rows 1, 2, 3, 4.
- **SPEC-O4.2** [all] An order whose every entry is skipped → rows 1, 2, 3, 4.
- **SPEC-O5.1** [OS] A text-only `name`: order on it with request `orderable: true` → error_code `invalid_filter`
  and no search sent; with `orderable: false` → skipped, rows 1, 2, 3, 4.
- **SPEC-O5.2** [OS] Declaring a text-only string column orderable → `SchemaError` at construction.
- **SPEC-O6.1** [SQLite] Order `name` asc under the default `BINARY` collation → rows 3, 1, 2, 4 (`Blue`, `Red`,
  `reddish`, `ÄPFEL` by code point): the database's order, with no collation of the library's. Order `colour` asc
  → rows 4, 3, 1, 2 (`RED`, `blue`, `red`, `reddish`); a case-folding order would give 3, 1, 4, 2.
- **SPEC-O6.2** [SQL] The compiled ORDER BY of a string column holds no `COLLATE` clause the app did not declare
  (PRD-D10).
- **SPEC-O7.1** [OS] Order `name` asc sends a sort on `name.keyword`; on a `keyword` column, on the field itself.
- **SPEC-O8.1** [all] Order `active` desc then `price` asc → rows 4, 1, 2, 3: the true rows first, among them row
  4's NULL price before row 1's 7; then false; then NULL.
- **SPEC-O8.2** [all] Order `price` asc then `active` desc → rows 4, 1, 3, 2 (the first entry decides, the second
  only breaks ties).
- **SPEC-O8.3** [all] Order `secret` asc marked `orderable: false` (skipped: unorderable on both sides), then `price`
  desc → rows 2, 3, 1, 4: `price` is the primary order.
