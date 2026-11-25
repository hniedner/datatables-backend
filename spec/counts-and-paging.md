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
- **SPEC-C9.2** [OS] Recording, with a global search: a search answering `gte` → two count calls, the filtered set's
  query (its count is `recordsFiltered`) and the base filter's (the total, PRD-C8); with no filter beyond the base
  filter, one call, whose count is both.
- **SPEC-C10.1** [OS] With `max_result_window` 3 and rows 1 to 10: `start` 0, `length` 3 → rows 1, 2, 3 (ordinary).
- **SPEC-C10.2** [OS] With `max_result_window` 3 and rows 1 to 10: `start` 8, `length` 3 → rows 9, 10, in ascending
  order, fetched by a reversed sort (recording shows the reversed sort and `from` 0).
- **SPEC-C10.3** [OS container] For every `start` a reversed page can reach, its rows equal those the ordinary way
  gives with a large `max_result_window`.
- **SPEC-C10.4** [OS] `max_result_window` defaults to 10,000: with no setting and a recording client whose counts
  answer 20,000, `start` 9990, `length` 10 is sent as an ordinary page (`from` 9990); `start` 9995, `length` 10 (past
  the window both ways: 10,005 > 10,000 and 20,000 - 9995 > 10,000) → `page_out_of_range`.
- **SPEC-C10.5** [OS] Recording, with `max_result_window` W 3 and F 10 rows: a reversed page asks for `from`
  `max(0, F - start - length)` and `size` `min(length, F - start)`, so that from + size = F - start <= W: `start` 7,
  `length` 3 (the first reversed page, start = F - W) → `from` 0, `size` 3; `start` 7, `length` 2 → `from` 1, `size`
  2; `start` 8, `length` 3 → `from` 0, `size` 2; `start` 9 (the last row) → `from` 0, `size` 1; the rows come in
  ascending order. `start` 6 is refused, `start` 10 is the empty page, and `start` 1 with `length` 2 (start + length =
  W) is an ordinary page with no count.
- **SPEC-C10.6** [OS] Recording: a reversed page flips every entry of the order, the unique key included, and keeps a
  missing value the lowest (`asc` with `missing` `_first`, `desc` with `_last`); each flipped entry carries the
  `mode` of its original direction (`min` for `asc`, `max` for `desc`), which OpenSearch sorts a field of several
  values by, so that the flip reverses exactly the order it flips. An ordinary body carries no `mode`.
- **SPEC-C11.1** [OS] Recording: a `page_out_of_range` answer runs only the count calls, before any page search.
- **SPEC-C12.1** [OS] With `max_result_window` 3 and rows 1 to 10: `start` 4, `length` 3 → error_code
  `page_out_of_range`, `recordsTotal` 10, `recordsFiltered` 10, `data` `[]`.
- **SPEC-C12.2** [OS] Recording: no call changes index settings (`put_settings` is never called).
- **SPEC-C12.3** [OS] Recording: with `max_result_window` 3, a global search, a filtered count of 7 and a total of 40,
  `start` 2 and `length` 3 → `page_out_of_range`, `recordsFiltered` 7, `recordsTotal` 40: two count calls, told apart
  by their query (the order is no part of the contract), and no page search.
- **SPEC-C12.4** [OS] Recording: C12.3 with a `TotalCache`: the second request within the TTL runs one count call, the
  filtered set's.
- **SPEC-C12.5** [OS container] With the scope `exists pages` (4 documents) and `max_result_window` 2, ordered by `id`:
  `start` 3 and `length` 1, the reversed last page, → row `e5` and counts 4 and 4; `start` 1 and `length` 2 →
  `page_out_of_range`, `recordsTotal` 4 (not 5, the index's count) and `recordsFiltered` 4.
- **SPEC-C13.1** [OS] Recording only, with scripted answers: with `max_result_window` 3, `start` 8 and `length` 3,
  the count answers 10 and the reversed page search answers the hits 11, 10 (an index that grew after the count) →
  rows 10, 11, no error, and no `create_pit` call.
- **SPEC-C14.1** [all] With a `TotalCache` and a global search `red` (so the total is a count of its own, PRD-C8):
  two requests within the TTL run one total count between them; recording shows the second ran only the page search
  (SQL: the filtered count and the page), and both answer `recordsTotal` 4.
- **SPEC-C14.2** [all] With a `TotalCache` of TTL 0.3 seconds and a global search `red` (so the total is a count of
  its own, PRD-C8): the first request answers `recordsTotal` 4; a request made right after inserting row 5 still
  answers 4; after the test waits 0.35 seconds, past the TTL, the next request counts again and answers 5.
- **SPEC-C14.3** [OS] Recording: with a `TotalCache`, an unfiltered request (whose search count is the total, PRD-C8)
  fills the cache: a global search right after it runs no total count, and answers that `recordsTotal`.
- **SPEC-C14.4** [SQL] Given 5 rows with `id` 1 to 5: three tables over the same source whose base filters are
  `id <= 1`, `id <= 2` and `id <= 3` (F1, F2, F3), sharing one `TotalCache` of TTL 60 seconds and `max_entries` 2, each
  sent a global search `red`. The requests F1, F2, F3, F2, F1, F3 answer `recordsTotal` 1, 2, 3, 2, 1, 3 and run 3, 3,
  3, 2, 3, 2 statements (a miss runs the total, the filtered count and the page, a hit the last two): the third drops
  F1, the oldest; the fourth reads F2 and does not make it newer; the fifth drops F2; the sixth finds F3 (PRD-C16).
  OpenSearch only calls `get` and `put` of the same class, so one backend proves the rule.
- **SPEC-C14.5** [SQL] As `SPEC-C14.4` with a TTL of 0.3 seconds: F1, F2, then the test waits 0.35 seconds, then F1,
  F3, F1 answer `recordsTotal` 1, 2, 1, 3, 1 and run 3, 3, 3, 3, 2 statements: a total counted again after its expiry
  is the newest, so F3 drops F2 and the last F1 finds its fresh total.
- **SPEC-C14.6** [all] `TotalCache(max_entries=0)` and `TotalCache(max_entries=-1)` raise `ValueError`, whose message
  names `max_entries`; `TotalCache().max_entries` is 10,000.
- **SPEC-C15.1** [SQL] Given a `team` column holding `red` in rows 1 and 3 and `green` in row 4, of 5 rows: two
  tables over the same source whose base filters differ only in the bound value (`team = 'red'`, `team = 'green'`)
  and share one cache, each sent a global search `e` → `recordsTotal` 2 and 1.
- **SPEC-C15.2** [SQL] One table and one `TotalCache`, fetched against two SQLite databases holding 4 and 2 rows,
  with a global search `red` → `recordsTotal` 4 and 2; the key holds the database URL on every dialect, so SQLite
  proves it for all (PRD-C15).
- **SPEC-C15.3** [OS] Two tables over the same index whose base filters differ only in the bound value (`term team
  red`, `term team green`), sharing one `TotalCache` and one recording client that counts 4 documents for `red` and 2
  for `green`, each sent a global search `red` → `recordsTotal` 4 and 2, and two total counts sent, not one.
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
- **SPEC-C15.8** [SQL] As `SPEC-C15.1`, with one table that has no base filter and one cache, and the scopes `team =
  'red'` and `team = 'green'` passed to `fetch`, each sent a global search `e`, in turn through `fetch_sync` and through
  `fetch` → `recordsTotal` 2 and 1.
- **SPEC-C15.9** [OS] Recording: one table with no base filter and one `TotalCache`, and a client that counts 4
  documents for the scope `term team red` and 2 for `term team green`. An unfiltered request with the first scope
  fills the cache (PRD-C8); a global search `red` with the second sends a total count of its own, whose body holds its
  scope, and answers `recordsTotal` 2, not 4.
- **SPEC-C15.10** [OS] Recording, with the table, cache and client of `SPEC-C15.9`: requests with a global search `red`
  and the scopes `term team red`, `term team green`, `term team red` answer `recordsTotal` 4, 2, 4 and send two total
  counts, each body holding its own scope; the third is served from the cache.
- **SPEC-C15.11** [OS] Recording, with the table and cache of `SPEC-C15.9` and a client whose search and count calls
  yield to the event loop before answering, as `SPEC-C15.10`'s do by their query: two `fetch` calls with a global search
  `red`, one per scope, run together (`asyncio.gather`) answer `recordsTotal` 4 and 2, each its own, and each total
  count sent holds its own scope: the cache key belongs to the request, not to the table, whichever request is half
  way when the other starts.
- **SPEC-C16.1** [all] With a `TotalCache` (TTL far longer than the test) and a global search `red`: the first
  request answers `recordsTotal` 4; after a row 5 is inserted and `TotalCache.clear()` is called, the next request
  counts again and answers 5 (without the clear it would answer 4).
- **SPEC-C16.2** [SQL] Constructing a table and a cache registers no SQLAlchemy event listener on the engine or pool.
- **SPEC-C18.1** [all] A plain class with `get` and `put` (not a `TotalCache`) as `total_cache`, through `fetch_sync`:
  [SQL] an unfiltered request twice, a row deleted between them, with a scope whose bound value is a distinctive
  string → `recordsTotal` 5 both times; [OS] recording, a global search `x` twice with a scope holding a distinctive
  string → one total count sent and `recordsTotal` 40 both times. Every key the store was given is 64 hexadecimal
  characters, and none contains the distinctive string (PRD-C18: a store sees a digest, never a bound value).
- **SPEC-C18.2** [all] As `SPEC-C18.1` through `fetch` with a store whose `get` and `put` are coroutine functions, and
  with a store whose methods are plain functions returning awaitables: the same answers and the same keys.
- **SPEC-C18.3** [all] As `SPEC-C18.1`, through `fetch_sync` and through `fetch`, with a store whose `__len__` returns 0:
  the total is still served from it on the second request.
- **SPEC-C18.4** [all] `fetch_sync` with a store whose methods are coroutine functions raises `TypeError` whose message
  contains `call fetch`, before any statement or search is sent. With a store whose methods are plain functions
  returning awaitables, it raises the same on a request that calls `get` and on one that only calls `put` ([OS], an
  unfiltered request; [SQL] either).
- **SPEC-C17.1** [all] A request of exactly 960 conditions is answered (PRD-C17). [SQL] Nearly the most binds: lists of
  256 and 255 days on `seen` and `seen_tz`, which may hold a time (two parameters each), 64 criteria `between` `0` and
  `99` on `id`, 24 column searches of the 16 words of `LARGEST_WORDS` and one of a word, on columns that read
  `email_address` → row 1; 1,537 parameters with the page's two, within SQL Server's measured 2,097. [SQL] The deepest
  expression: the global search `!ann` over 960 columns that read `email_address` → 4 rows (a row with no email passes a
  negation), within SQLite's depth of 1,000. [OS] The global search `x` over 960 columns that read `title` → answered,
  on both servers.
- **SPEC-C17.2** [all] A request of 961 conditions is answered with error_code `request_too_large` and `data` `[]`, and
  nothing is sent to the database or the cluster. [SQL] The global search `ann` over 961 columns; the global search
  `!ann` over 961 columns (a negation counts as its word does); and the request of `SPEC-C17.1`'s most binds with an
  empty entry added to the list of 255 days (an empty entry counts as a value). [OS] The global search `ann` over 961
  columns.
- **SPEC-C17.3** [OS] The global search `ann` over 960 columns that read `title` (text+keyword) sends one `should`
  for each column in one `bool`, each a `bool` of two clauses: 1,920 leaf queries and a widest `bool` of 960, within
  the default `indices.query.bool.max_clause_count` of 1,024.
- **SPEC-C17.4** [SQL] For each kind of condition (a word of the global search and a negated one, a word of a column
  search, a list of strings, of numbers, of days of a `Date` column and of a column that may hold a time, a list with
  an empty entry, and every SearchBuilder condition on a string, a number, a date and a datetime), compiled on SQLite,
  PostgreSQL, MySQL and SQL Server, the filtered statement binds at most two parameters for each condition and the
  page's statement two more.
