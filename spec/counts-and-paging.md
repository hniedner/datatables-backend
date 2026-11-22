# Specs: counts and paging (C)

Statements for PRD section 4.5. Form, backends, answers and the `items` fixture: see `README.md`. "Recording"
means a SQL connection that records each statement it runs, or an OpenSearch client that records each call.

- **SPEC-C1.1** [all] No filter → `recordsTotal` 4; with the base filter `price` not null → `recordsTotal` 3.
- **SPEC-C1.2** [all] Global search `red` → `recordsTotal` 4 (filters do not change the total).
- **SPEC-C2.1** [all] Global search `red` → `recordsFiltered` 3; with `length` 1 → `recordsFiltered` still 3 and one
  row.
- **SPEC-C2.2** [all] Global search `zzz` → `recordsFiltered` 0, `recordsTotal` 4, `data` `[]`.
- **SPEC-C3.1** [all] `start` 4 with `length` 10 → `data` `[]`, `recordsTotal` 4, `recordsFiltered` 4, no error.
- **SPEC-C3.2** [all] `start` 2^53-1 → `data` `[]` with both counts, no error.
- **SPEC-C3.3** [OS] With `max_result_window` 2 and 4 rows, `start` 10 → an empty page with both counts, not
  `page_out_of_range`.
- **SPEC-C4.1** [SQL] Recording, with column search `!pipe` on `note`: both counts are `count(*)` statements, and
  `recordsFiltered` is 3, the rows 1, 2, 3 the page returns (row 1's NULL `note` included).
- **SPEC-C5.1** [SQL] `start` 1, `length` 2 → rows 2, 3; recording shows `LIMIT`/`OFFSET` (`OFFSET ... FETCH` on
  SQL Server) in the page statement.
- **SPEC-C6.1** [SQL] Given a table with an undeclared column `hidden`: the page statement selects no `hidden`, and
  no row holds a `hidden` key.
- **SPEC-C7.1** [OS] Recording, with a client whose search answers `hits.total` 10,000 `gte` unless the body asks
  `track_total_hits: true`, and 20,000 `eq` when it does: a request with no filter beyond the base filter sends one
  search, which returns the page, `recordsFiltered` is 20,000, and no other call is made.
- **SPEC-C8.1** [OS] Recording: with no filter beyond the base filter, one search answers; `recordsTotal` equals
  `recordsFiltered`.
- **SPEC-C8.2** [OS] Recording: with a global search, a separate count gives `recordsTotal`.
- **SPEC-C9.1** [OS] A search answering `hits.total.relation` `gte` → an exact count call is made, and its count is
  the answer.
- **SPEC-C10.1** [OS] With `max_result_window` 3 and rows 1 to 10: `start` 0, `length` 3 → rows 1, 2, 3 (ordinary).
- **SPEC-C10.2** [OS] With `max_result_window` 3 and rows 1 to 10: `start` 8, `length` 3 → rows 9, 10, in ascending
  order, fetched by a reversed sort (recording shows the reversed sort and `from` 0).
- **SPEC-C10.3** [OS container] For every `start` a reversed page can reach, its rows equal those the ordinary way
  gives with a large `max_result_window`.
- **SPEC-C10.4** [OS] `max_result_window` defaults to 10,000: with no setting and a recording client whose counts
  answer 20,000, `start` 9990, `length` 10 is sent as an ordinary page (`from` 9990); `start` 9995, `length` 10 (past
  the window both ways: 10,005 > 10,000 and 20,000 - 9995 > 10,000) → `page_out_of_range`.
- **SPEC-C11.1** [OS] Recording: a `page_out_of_range` answer runs only the count calls, before any page search.
- **SPEC-C12.1** [OS] With `max_result_window` 3 and rows 1 to 10: `start` 4, `length` 3 → error_code
  `page_out_of_range`, `recordsTotal` 10, `recordsFiltered` 10, `data` `[]`.
- **SPEC-C12.2** [OS] Recording: no call changes index settings (`put_settings` is never called).
- **SPEC-C13.1** [OS] Recording only, with scripted answers: with `max_result_window` 3, `start` 8 and `length` 3,
  the count answers 10 and the reversed page search answers the hits 11, 10 (an index that grew after the count) →
  rows 10, 11, no error, and no `create_pit` call.
- **SPEC-C14.1** [all] With a `TotalCache` and a global search `red` (so the total is a count of its own, PRD-C8):
  two requests within the TTL run one total count between them; recording shows the second ran only the page search
  (SQL: the filtered count and the page), and both answer `recordsTotal` 4.
- **SPEC-C14.2** [all] With a `TotalCache` of TTL 2 seconds and a global search `red` (so the total is a count of
  its own, PRD-C8): the first request answers `recordsTotal` 4; a request made right after inserting row 5 still
  answers 4; after the test waits past the TTL, the next request counts again and answers 5.
- **SPEC-C15.1** [SQL] Given a `team` column holding `red` in rows 1 and 3 and `green` in row 4, of 5 rows: two
  tables over the same source whose base filters differ only in the bound value (`team = 'red'`, `team = 'green'`)
  and share one cache, each sent a global search `e` → `recordsTotal` 2 and 1.
- **SPEC-C15.2** [SQL] One table and one `TotalCache`, fetched against two databases of the backend under test
  holding 4 and 2 rows, with a global search `red` → `recordsTotal` 4 and 2.
- **SPEC-C15.3** [all] Two tables over `items`, one with no base filter and one with the base filter `price` not
  null, given the same app-supplied cache key and one `TotalCache`, each sent a global search `red`: the first
  answers `recordsTotal` 4; the second answers 4 (the first's total, not its own 3) and runs no total count.
- **SPEC-C15.4** [OS] Two tables over the indexes `items_a` (4 documents) and `items_b` (2 documents), sharing one
  `TotalCache` and one recording client, each sent a global search `red` → `recordsTotal` 4 and 2.
- **SPEC-C15.5** [SQL] Given a `team` column holding `red` in rows 1 and 3 and `green` in row 4, of 5 rows: two
  tables with no base filter, each over a subquery of the same table whose `WHERE` differs only in the bound value
  (`team = 'red'`, `team = 'green'`), sharing one cache, each sent a global search `e` → `recordsTotal` 2 and 1.
- **SPEC-C15.6** [SQL] As `SPEC-C15.5`, the source being a join of the table with itself whose `ON` clause differs
  only in the bound value (`team = 'red'`, `team = 'green'`) → `recordsTotal` 2 and 1.
- **SPEC-C15.7** [SQL] A table of 5 rows with a `TotalCache` and a base filter that is true for every row, built
  from a construct SQLAlchemy can compile for the database under test and not for its default dialect → a request
  answers `recordsTotal` 5.
- **SPEC-C16.1** [all] With a `TotalCache` (TTL far longer than the test) and a global search `red`: the first
  request answers `recordsTotal` 4; after a row 5 is inserted and `TotalCache.clear()` is called, the next request
  counts again and answers 5 (without the clear it would answer 4).
- **SPEC-C16.2** [SQL] Constructing a table and a cache registers no SQLAlchemy event listener on the engine or pool.
- **SPEC-C17.1** [SQL Server] On a table of rows 1 to 3 with string columns `c1` to `c9`, each holding `r<id>`: a
  `multi_values` list on each column of `r1` and 255 values that match nothing (2,304 values, within PRD-F38 per
  list) → rows 1; recording shows each IN list sent as one `OPENJSON` parameter.
- **SPEC-C17.2** [SQLite, PostgreSQL, MySQL] The request of C17.1 (2,304 values) binds each value as its own
  parameter (no JSON parameter) and answers the same rows.
