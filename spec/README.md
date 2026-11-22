# Specs

One file per PRD section (`docs/PRD.md`, section 4), named by its topic. The letter in a statement id
(`SPEC-F9.1`) names the file:

| Letter | File | Topic |
|---|---|---|
| P | `parsing.md` | parsing |
| D | `column-declaration.md` | column declaration |
| F | `filtering-and-search.md` | filtering and search |
| O | `ordering.md` | ordering |
| C | `counts-and-paging.md` | counts and paging |
| E | `errors.md` | errors |
| R | `rows-and-response.md` | rows and the response |
| A | `api.md` | API |
| Q | `packaging-and-quality.md` | packaging and quality |

Each spec statement states one observable behaviour, in the form a behavioural test checks it: the request goes
through `parse_request`, then `fetch` or `fetch_sync`, or through the FastAPI adapter for PRD-A9 to PRD-A11. Section
Q is checked in CI or in review. This README holds no statements.

## Statement form

A statement is one paragraph: a list item that starts `- **SPEC-X1.n** [tag]`, followed by the input and the
expected answer, wrapped onto indented lines. It ends at a blank line, a heading or another unindented list item; a
second paragraph under it is not read.

- `SPEC-X1.n` states requirement `PRD-X1`; any other `PRD-...` id in its text, backticked or not, is a
  cross-reference, written as one id (`PRD-F21`), never a range.
- Each PRD requirement is a section: the heading `#### PRD-X1`, the requirement's text, then the list items
  `- **Reason:** ...` and `- **Priority:** must` (or `should`, `later`), the priority last;
  `scripts/trace_check.py` reads no other form. No other heading of the PRD names a `PRD-` id.
- A statement refers to another statement in backticks (`` `SPEC-F21.2` ``) or by its bare number (F21.2). A code
  span stays on one line.
- A statement may add rows to the fixture or redeclare a column for itself; it says so ("given a row ...", "with
  the stored field renamed ..."). Otherwise it runs on the fixture as below.

`scripts/trace_check.py` (run in CI's lint job) reports, one line each:

- a PRD heading that holds `PRD-` but is not a requirement heading of the expected shape, a requirement section of
  the wrong shape, a requirement in table form (a line whose first cell starts with `PRD-`), a requirement id
  defined twice, or no requirement at all;
- no statement at all, the text `SPEC-` outside backticks anywhere but a statement's start, or an unindented line
  right after a statement;
- a *must* or *should* requirement with no statement (a *later* one needs none);
- a statement that states or cites a requirement id the PRD does not hold, or cites a malformed `PRD-` id;
- a statement id defined more than once.

An unreadable PRD or specs directory is a usage error.

## Tags

- `[all]`: every backend, SQL and OpenSearch.
- `[SQL]`: SQLite (sync and aiosqlite), PostgreSQL, SQL Server and MySQL. A named database (`[SQLite]`,
  `[PostgreSQL]`, ...) narrows it, with any collation or locale the statement needs.
- `[OS]`: OpenSearch through a recording client (async and sync), which answers from the fixture or as the
  statement says. On it a statement checks the request bodies sent and the answer built from the hits given; the
  row answers are checked on the live cluster (decided below).
- `[OS container]`: OpenSearch against the live cluster of CI's `opensearch` job (`tests/test_opensearch_live.py`).
- A list (`[SQLite, PostgreSQL]`) means each backend named; a narrowed tag (`[OS, SQL join]`) means OpenSearch and a
  SQL table over a join. A tag in the middle of a statement applies to the clauses after it.
- `[core]`: backend-independent; checks `parse_request` or a model alone.
- `[FastAPI]`: through a FastAPI app using the library's dependency, with its `TestClient`, over the SQLite `items`
  fixture unless the statement names another backend.
- `[CI job]`: checked by that CI job (`install-isolation`, `minimum-versions`, `tests`, `gate`, `opensearch`).
- `[review]`: checked by reading the code, the CI configuration, the built wheel or the repository settings; no
  automated check names it yet.

## Answers

- `answered`: `fetch` returns a response with no `error_code`; its rows are checked only when the statement names
  them.
- `parses`: `parse_request` returns without raising.
- `rows 1, 3`: answered, and the page holds exactly those rows, in key order unless the statement orders.
- `none`: answered with an empty page.
- `skipped`: an order entry or a column the request names is answered as if it were absent.
- `error_code X`, or just the code: returned, not raised, as a response with `error`, `error_code` X,
  `recordsTotal` 0, `recordsFiltered` 0 and `data` `[]` (decided below). The one exception is `page_out_of_range`,
  which carries the real counts (PRD-C12).
- An exception class (`MalformedRequest`, `SchemaError`, `PartialResultError`): raised, from `parse_request`, the
  constructor or `fetch` as the statement says.
- `HTTP 422`, `HTTP 200`: the status the FastAPI app answers, with the JSON body the statement names.
- **Recording**: a SQL connection that records each statement it runs, or an OpenSearch client that records each
  call and answers it from the fixture (or as the statement says).

## The base request

DataTables' default request for the `items` columns, in column order: each column's `data` is its name and its
`name` is empty, `draw` 1, `start` 0, `length` 10 (more than every fixture), every column `searchable` and
`orderable` true, empty searches, and no `order`. A statement changes
only the part it names.

## The fixture table `items`

Primary key `id`. On OpenSearch the index holds the same documents, `id` is the declared unique key, and every string
column is `text+keyword` unless a statement says otherwise.

| Column | Type | Declared |
|---|---|---|
| `id` | number | searchable, orderable |
| `name` | string | searchable, orderable |
| `colour` | string | searchable, orderable |
| `price` | number: SQL `BigInteger` (64-bit), OS `long` | searchable, orderable |
| `made` | date | searchable, orderable |
| `at` | timezone-aware datetime | searchable, orderable |
| `active` | boolean | searchable, orderable |
| `note` | string | searchable, orderable |
| `secret` | string with a search-value hook that prefixes `enc:` | searchable, not orderable |

| id | name | colour | price | made | at (UTC) | active | note | secret (stored) |
|---|---|---|---|---|---|---|---|---|
| 1 | `Red Apple` | `red` | 7 | 2024-03-05 | 2024-03-05T23:30Z | true | NULL / missing | `enc:Alpha` |
| 2 | `reddish pear` | `reddish` | 17 | 2024-12-31 | 2024-03-06T00:30Z | false | `''` | `enc:Beta` |
| 3 | `Blue 50% plum` | `blue` | 8 | NULL / missing | NULL / missing | NULL / missing | `x_y` | `enc:Gamma` |
| 4 | `ÄPFEL` | `RED` | NULL / missing | 2023-01-01 | 2023-01-01T12:00Z | true | `5" pipe` | `enc:Delta` |

Every column but `id` is nullable (missing on OpenSearch), so a row a statement adds holds NULL wherever it gives no
value. "A row holding `w`" means a row with `w` in some searchable column.

## Decided questions (owner, 2026-11-20)

- **OQ1** (PRD-F38): the SearchBuilder root group counts as depth 1 (F38.1).
- **OQ2** (PRD-D1, PRD-R1, PRD-C6): a row carries every declared column, whatever the request lists (D1.1, R7.2).
- **OQ3** (PRD-A10): the 422 body keeps FastAPI's default shape, with `draw` inside `detail`:
  `{"detail": {"draw": 5, ...}}` (A10.1). The owner changed this on 2026-11-20 from a top-level `draw`, which would
  have needed every app to register an exception handler shipped by the library.
- **User-error counts** (reviewer's decision, 2026-11-20): a user-error response other than `page_out_of_range`
  carries `recordsTotal` 0 and `recordsFiltered` 0, as today's `DTResponse` defaults give, even when the counts of
  PRD-C11 ran before the refusal (PRD-E7 allows them).
- **OpenSearch row answers** (reviewer's decision, 2026-11-20): the recording client returns canned hits, so a
  statement naming rows cannot fail on it, and an emulator would repeat the code's own assumptions. On the recording
  client, an `[OS]` statement checks the request bodies and the answer built from the hits given. Every `[all]` or
  `[OS]` statement that names rows is also run against the live cluster in `tests/test_opensearch_live.py`. A
  statement whose hits are scripted is checked on the recording client only.
- **Sort direction** (reviewer's decision, 2026-11-20): `order[i][dir]` must be exactly `asc` or `desc`; anything
  else is `MalformedRequest`, a structural error, since DataTables and SvelteDT send only those words (P6.5).
- **Empty global regex** (reviewer's decision, 2026-11-20): a global search with `search[regex]` true whose value is
  empty, or reduced to nothing by PRD-F6, is idle, since the flag has nothing to apply to (PRD-F6) and a
  regex-configured client sends it on every request (F18.3).
- **Empty column value with regex** (reviewer's decision, 2026-11-20): a column search with an empty value is idle
  whatever its `regex` flag, since PRD-D6 and PRD-F41 count a column search only with a value, so no categorical
  filter exists for PRD-F16 and PRD-F17 to read (F16.4).
- **Declared key** (reviewer's decision, 2026-11-20): when a table declares a unique key, that key is the
  tie-breaker of PRD-O3, even when SQLAlchemy reports a primary key for the source, since PRD-O3 asks for a
  tie-breaker on a unique key and a join's reduced SQLAlchemy primary key need not be unique (O3.5).
- **OQ4** (PRD-F9; measured on OpenSearch 2.19.7 and 3.0.0 to 3.10.0 and decided by the owner on 2026-11-21):
  non-ASCII case folding on OpenSearch. A string `contains` on a `text+keyword` column is a `match` on the analysed
  text or a case-insensitive query on the keyword field, and an exact match is a case-insensitive query on it. The
  standard analyser folds a whole word, letters outside ASCII included (F9.9). `case_insensitive` folds ASCII
  letters only on a `term` and a `wildcard` query, on every version; on a `regexp` query it folds letters outside
  ASCII from 3.1.0 on (Lucene 10.2.1), and not on 3.0.0 (Lucene 10.1.0) or 2.19.7 (Lucene 9.12.3). So a value with
  a cased character outside ASCII is sent as a `regexp` (F9.13), and a substring (F9.10) and an exact match (F9.11)
  in another case find it from 3.1 on. A value without such a letter stays a `term` or a `wildcard` and folds ASCII
  letters only: `k` does not find the Kelvin sign, `s` not the long s. A substring or an exact match with `i` does
  not find a dotted capital `İ` in any of the three forms; the analysed word does. The library asks for no
  normalizer in the consumer's mapping. The measurements are in
  `docs/decisions/001-opensearch-case-folding-beyond-ascii.md`.

## Open questions

None.
