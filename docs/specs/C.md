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
- **SPEC-C4.1** [SQL] Recording: the counts are `SELECT count(*)` statements; `recordsFiltered` equals the number of
  rows a full fetch of the filter returns, rows with NULL in every searched column included.
- **SPEC-C5.1** [SQL] `start` 1, `length` 2 → rows 2, 3; recording shows `LIMIT`/`OFFSET` (`OFFSET ... FETCH` on
  SQL Server) in the page statement.
- **SPEC-C6.1** [SQL] Given a table with an undeclared column `hidden`: the page statement selects no `hidden`, and
  no row holds a `hidden` key.
- **SPEC-C7.1** [OS] Recording: a request with a filter sends one search carrying both the page and
  `track_total_hits: true`, whose `hits.total` gives `recordsFiltered`.
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
- **SPEC-C10.4** [OS] `max_result_window` defaults to 10,000: with no setting, `start` 9990, `length` 10 is ordinary.
- **SPEC-C11.1** [OS] Recording: a `page_out_of_range` answer runs only the count calls, before any page search.
- **SPEC-C12.1** [OS] With `max_result_window` 3 and rows 1 to 10: `start` 4, `length` 3 → error_code
  `page_out_of_range`, `recordsTotal` 10, `recordsFiltered` 10, `data` `[]`.
- **SPEC-C12.2** [OS] Recording: no call changes index settings (`put_settings` is never called).
- **SPEC-C13.1** [OS] Given a client whose count answers 10 and whose reversed page then sees 11 documents, the
  answer is served (no point-in-time is opened, no error): recording shows no `create_pit`.
- **SPEC-C14.1** [all] With a TTL cache: two requests in the TTL run one total count; recording shows the second
  request ran no total count, and both answer `recordsTotal` 4.
- **SPEC-C14.2** [all] After the TTL ends, the next request counts again and sees a newly inserted row.
- **SPEC-C15.1** [SQL] Two tables over the same source whose base filters differ only in a bound value
  (`tenant = 1`, `tenant = 2`) and share one cache → each answers its own `recordsTotal`.
- **SPEC-C15.2** [all] The same table on two databases or indexes sharing one cache → each answers its own total; an
  app-supplied key decides when given.
- **SPEC-C16.1** [all] `cache.clear()` (or the explicit call) makes the next request count again.
- **SPEC-C16.2** [SQL] Constructing a table and a cache registers no SQLAlchemy event listener on the engine or pool.
- **SPEC-C17.1** [SQL Server] On a table with 9 string columns, a `multi_values` list of 256 values on each (2,304
  values, within PRD-F38 per list) → answered with the matching rows; recording shows each IN list sent as one
  `OPENJSON` parameter.
- **SPEC-C17.2** [SQLite, PostgreSQL] `multi_values` of 256 values binds normally (no JSON parameter) and answers.
