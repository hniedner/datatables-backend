# Specs

One file per PRD section (`docs/PRD.md`, section 4): P, D, F, O, C, E, R, A, Q. Each spec statement states one
observable behaviour, in the form a behavioural test checks it: the request goes through `parse_request`, then
`fetch` or `fetch_sync` (the FastAPI adapter for section A).

## Statement form

`- **SPEC-X1.n** [backends] Given ... ; when ... ; then ...`

- `SPEC-X1.n` states requirement `PRD-X1`; any other `PRD-...` id in the text is a cross-reference.
  `scripts/trace_check.py` checks that every *must* and *should* requirement has a statement, that every cited id
  exists, and that no statement id is used twice.
- **Backends:** `all` (SQL and OpenSearch), `SQL` (SQLite sync and aiosqlite, PostgreSQL, SQL Server, MySQL), a
  named database, or `OS` (OpenSearch, async and sync clients).
- **Answers:** `rows 1, 3` (the page holds exactly those fixture rows, in key order unless the statement orders);
  `none` (an empty page, no error); `error_code X` (answered with `error`, `error_code` X, both counts and an empty
  `data`, PRD-E1); `MalformedRequest` (raised, PRD-E6); `SchemaError` (raised at construction).
- Unless a statement says otherwise, the request is valid apart from the part named, `length` covers every row, and
  no order is sent.

## The fixture table `items`

Primary key `id`. On OpenSearch the index has the same documents, `id` is the declared unique key, and every string
column is `text+keyword` unless a statement says otherwise.

| Column | Type | Declared |
|---|---|---|
| `id` | number | searchable, orderable |
| `name` | string | searchable, orderable |
| `colour` | string | searchable, orderable |
| `price` | number (integer) | searchable, orderable |
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

A statement that needs other rows names them; "a row holding `w`" means a row with `w` in some searchable column.
