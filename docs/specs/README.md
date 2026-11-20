# Specs

One file per PRD section (`docs/PRD.md`, section 4): P, D, F, O, C, E, R, A, Q. Each spec statement states one
observable behaviour, in the form a behavioural test checks it: the request goes through `parse_request`, then
`fetch` or `fetch_sync` (the FastAPI adapter for section A). This README holds no statements.

## Statement form

A statement is a list item that starts `- **SPEC-X1.n** [tag]`, followed by the input and the expected answer.

- `SPEC-X1.n` states requirement `PRD-X1`; any other `PRD-...` id in its text is a cross-reference.
  `scripts/trace_check.py` (run in CI's lint job) checks that every *must* and *should* requirement has a statement,
  that every cited id exists, and that no statement id is used twice.
- A statement may add rows to the fixture or redeclare a column for itself; it says so ("given a row ...", "with
  `colour` redeclared as ..."). Otherwise it runs on the fixture as below.

## Tags

- `[all]`: every backend, SQL and OpenSearch.
- `[SQL]`: SQLite (sync and aiosqlite), PostgreSQL, SQL Server and MySQL. A named database (`[SQLite]`,
  `[PostgreSQL]`, ...) narrows it, with any collation or locale the statement needs.
- `[OS]`: OpenSearch, with the async and the sync client.
- `[core]`: backend-independent; checks `parse_request` or a model alone.
- `[FastAPI]`: through a FastAPI app using the library's dependency, with its `TestClient`.
- `[CI job]`: checked by that CI job (`install-isolation`, `minimum-versions`, `tests`, `gate`).
- `[review]`: checked by reading the code or the built wheel; no automated check names it yet.

## Answers

- `rows 1, 3`: the page holds exactly those rows, in key order unless the statement orders.
- `none`: an empty page, no error.
- `error_code X`, or just the code: answered (not raised) with `error`, `error_code` X, `recordsTotal` 0,
  `recordsFiltered` 0 and `data` `[]`. No count runs before a user error (PRD-E7). The one exception is
  `page_out_of_range`, which carries the real counts (PRD-C12).
- An exception class (`MalformedRequest`, `SchemaError`, `PartialResultError`): raised, from `parse_request`, the
  constructor or `fetch` as the statement says.
- `HTTP 422`, `HTTP 200`: the status the FastAPI app answers, with the JSON body the statement names.
- **Recording**: a SQL connection that records each statement it runs, or an OpenSearch client that records each
  call and answers it from the fixture (or as the statement says).

## The base request

DataTables' default request for the `items` columns, in column order: `draw` 1, `start` 0, `length` 10 (more than
every fixture), every column `searchable` and `orderable` true, empty searches, and no `order`. A statement changes
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
| `note` | string, nullable | searchable, orderable |
| `secret` | string with a search-value hook that prefixes `enc:` | searchable, not orderable |

| id | name | colour | price | made | at (UTC) | active | note | secret (stored) |
|---|---|---|---|---|---|---|---|---|
| 1 | `Red Apple` | `red` | 7 | 2024-03-05 | 2024-03-05T23:30Z | true | NULL / missing | `enc:Alpha` |
| 2 | `reddish pear` | `reddish` | 17 | 2024-12-31 | 2024-03-06T00:30Z | false | `''` | `enc:Beta` |
| 3 | `Blue 50% plum` | `blue` | 8 | NULL / missing | NULL / missing | NULL / missing | `x_y` | `enc:Gamma` |
| 4 | `ÄPFEL` | `RED` | NULL / missing | 2023-01-01 | 2023-01-01T12:00Z | true | `5" pipe` | `enc:Delta` |

"A row holding `w`" means a row with `w` in some searchable column.

## Open questions

Each is marked in the statement that depends on it. The statement states the reading it assumes until the owner
answers.

- **OQ1** (PRD-F38): does the SearchBuilder root group count toward the 8-group depth? SPEC-F38.1 assumes it does.
- **OQ2** (PRD-D1, PRD-R1, PRD-C6): does a row carry every declared column, or only the columns the request names?
  SPEC-D1.1 assumes every declared column.
- **OQ3** (PRD-A10): where does `draw` sit in the 422 body? SPEC-A10.1 assumes a top-level key beside `detail`.
- **OQ4** (PRD-F9): OpenSearch's `case_insensitive` term and wildcard queries fold ASCII only (to be checked on the
  container). If they do, OpenSearch matches non-ASCII letters case-sensitively, which PRD-F9 states only for SQLite.
  The specs claim no non-ASCII answer on OpenSearch until the owner decides.
