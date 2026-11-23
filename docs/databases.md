# What each database does on its own

The library does not change how a database compares values. Where a rule of one engine changes what a user
sees, it is written down here, by engine, and so are the library's own limits on an engine, each marked as
such: what the adopter sees, an example, how it is known, and the requirement of `docs/PRD.md` it touches. "MEASURED" names the date, the version and the
CI run or the decisions file that holds the numbers; "documented, not run" means the engine's documentation says
so and no run of this project checked it.

PRD section 5 lists the same differences from the requirements' side. An engine without an entry below has none
recorded yet.

## PostgreSQL

No entry yet.

## MySQL

### An exact match ignores trailing spaces under a PAD SPACE collation

- **What you see:** on a column with a PAD SPACE collation (such as `utf8mb4_general_ci` or `utf8mb4_unicode_ci`),
  `=`, a categorical filter and a `multi_values` list treat `'a '` and `'a'` as the same value, and a value of
  spaces only matches the empty string. Under the default collation of MySQL 8 (`utf8mb4_0900_ai_ci`, NO PAD)
  they are different values.
- **Example:** `multi_values: [" "]` on a column holding `''` finds that row under a PAD SPACE collation, and no
  row under the default one.
- **How it is known:** the default collation MEASURED 2026-11-22 on MySQL 8.4.13
  (`docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md`). A PAD SPACE collation: documented,
  not run.
- **Requirement:** PRD-F20, PRD-F26.

## SQL Server

### An exact match ignores trailing spaces

- **What you see:** `=`, a categorical filter and a `multi_values` list treat `'a '` and `'a'` as the same value,
  as SQL Server compares strings, and a value of spaces only matches the empty string.
- **Example:** `multi_values: [" "]` on a column holding `''` finds that row. PostgreSQL and SQLite find none.
- **How it is known:** MEASURED 2026-11-22 on SQL Server 2022
  (`docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md`).
- **Requirement:** PRD-F20, PRD-F26.

### A statement of more than 2,097 parameters is refused

- **What you see:** SQL Server 2022 with ODBC Driver 18 answered a statement of 2,097 bound parameters and refused
  2,098 (error 8003 from the server). The budget of PRD-C17 keeps the largest statement of a request, the page's,
  at 1,538 parameters (two for each day of a list on a column that may hold a time and for each range, one for every
  other condition, and the page's offset and limit), which leaves 559 for a `base_filter` and for expression
  columns. Those bind in all three statements (the total, the filtered count and the page) and are stated here, not
  budgeted: a `base_filter` that binds more than 559 parameters can make a request at the budget fail on SQL Server.
- **Example:** the global search `ann` over 2,098 columns binds 2,098 parameters in the filtered count and is refused;
  over 2,097 columns the count is answered and its page, which binds two more, is refused.
- **How it is known:** MEASURED 2026-11-22 on SQL Server 2022 with ODBC Driver 18 (CI runs 45083578654 and
  45083913250, `docs/decisions/005-the-largest-request-the-bounds-allow.md`).
- **Requirement:** PRD-C17.

### A decimal holds 38 digits at most

- **What you see:** a number of more than 38 digits before the decimal point on a `Numeric` column is answered
  with `invalid_number`, whatever precision the column declares. No other database bounds such a column.
- **Example:** `=` with a whole number of 39 digits, or `1e39`, on a `Numeric` column: `invalid_number` on SQL
  Server, no rows on PostgreSQL, MySQL and SQLite.
- **How it is known:** MEASURED 2026-11-22 on SQL Server 2022 with Microsoft's ODBC Driver 18, which refuses to
  send a longer number (`docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md`).
- **Requirement:** PRD-F25.

## SQLite

### A `Numeric` column is not compared exactly

- **What you see:** a whole number outside the signed 64-bit range can match a neighbouring value on a `Numeric`
  column. SQLite
  stores no exact decimal, and SQLAlchemy sends a decimal to it as a floating-point number. PostgreSQL, MySQL and
  SQL Server compare the number exactly.
- **Example:** a `Numeric(30, 0)` column holding 18446744073709551616 (2^64): `=` 18446744073709551617 finds that
  row on SQLite and no row on the other three.
- **How it is known:** MEASURED 2026-11-22 on SQLite 3.53.1, PostgreSQL 18.7, MySQL 8.4.13 and SQL Server 2022
  (`docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md`).
- **Requirement:** PRD-F25.

### An expression deeper than 1,000 is refused

- **What you see:** SQLite refuses a statement whose expression is deeper than 1,000 ("Expression tree is too large
  (maximum depth 1000)"). SQLite reads a chain of conditions joined by AND or OR from the left, so each adds a level.
  Across the shapes of a request measured (a chain of words, the global search over many columns, negated words,
  criteria, nested groups, lists of days), the depth was at most 9 above the number of conditions, so the budget of
  960 conditions stays inside it. A `base_filter` or an expression column that is deep adds levels of its own, stated
  here, not budgeted. A SQLite build may set another depth (`SQLITE_MAX_EXPR_DEPTH`): documented, not run.
- **Example:** a negated word of the global search over 991 columns is answered and over 992 is refused.
- **How it is known:** MEASURED 2026-11-22 on SQLite 3.53.1 with Python 3.14.7, the build limit 1,000 read from the
  error text (`docs/decisions/005-the-largest-request-the-bounds-allow.md`).
- **Requirement:** PRD-C17.

## Every SQL database

### A number written with a point or an exponent is compared as a floating-point number

This is a limit of the library, not a rule of the databases: the library reads such a number as a floating-point
number before any database sees it. Issue #56 holds the fix, so it may change.

- **What you see:** on a `Numeric` column such a number is exact to about 15 significant digits only, so `=` can
  also match a neighbouring value and `between` can include or leave out a value at its bounds. A whole number
  is compared exactly (on SQLite as its entry says).
- **Example:** a `Numeric(20, 2)` column holding 12345678901234567.89: `=` 12345678901234567.89 can also find a
  row holding 12345678901234567.90.
- **How it is known:** read in the code (a number with a point or an exponent is a Python `float` before any
  backend sees it); not run on a server.
- **Requirement:** PRD-F25.

### A shared `TotalCache` cannot tell two scopes apart that the session sets

This is a limit of the library, not a rule of the databases: the cache keeps a total under the database's URL and
the count statement with its bound values, and it sees nothing else.

- **What you see:** two requests whose rows differ only by the state of their database session share one cached
  `recordsTotal`, so one scope can be shown the total of another. That state is row-level security or `SET ROLE`
  on PostgreSQL, a tenant per schema through `search_path` or SQLAlchemy's `schema_translate_map`, and `USE` of
  another database on MySQL or SQL Server. A scope written into the source or the base filter is told apart. Give
  each such scope a `TotalCache` of its own.
- **Example:** one engine, one `SQLTable` with a cache, and `SET ROLE` per request with a row-level security
  policy: the second role is answered the first role's total until the TTL ends.
- **How it is known:** read in the code (the key is built from the URL and the compiled count); not run on a
  server.
- **Requirement:** PRD-C15.

## OpenSearch

### A shared `TotalCache` cannot tell two clusters or two users apart

This is a limit of the library, not of OpenSearch: the cache keeps a total under the index's name and the base
filter, and it sees nothing else.

- **What you see:** two clusters that hold an index of the same name, and two users whose documents differ by
  document-level security, share one cached `recordsTotal`. Give each cluster, and each such user or role, a
  `TotalCache` of its own.
- **Example:** one `SearchTable` over `docs` with a cache, used with the clients of two clusters: the second
  cluster is answered the first cluster's total until the TTL ends.
- **How it is known:** read in the code (the key is built from the index name and the base filter); not run on a
  server.
- **Requirement:** PRD-C15.

### A number outside the range of its field fails the request

This is a limit of the library, not of OpenSearch, which says clearly what it refuses. Issue #47 holds the
fix, so it may change.

- **What you see:** a number that the field's own mapping cannot hold (`long`, `integer`, `unsigned_long`) is
  refused by the server with a 400, which reaches the application as the client's `RequestError`, not as
  `invalid_number`. The library does not read the mapping, so it cannot know the range. SQL answers such a number
  on an integer column with `invalid_number`. A `double` field takes every finite number, and refuses a whole
  number of 400 digits in the same way.
- **Example:** `=` 18446744073709551616 on a column over an `integer` or a `long` field.
- **How it is known:** MEASURED 2026-11-22 on OpenSearch 3.10.0 and 2.19.7
  (`docs/decisions/003-opensearch-number-ranges-and-fullwidth-marks.md`).
- **Requirement:** PRD-F25, PRD-E7.

### A text field mapped without positions refuses a phrase

A word on analysed text is a `match_phrase`, which needs the token positions the default mapping stores.

- **What you see:** a `text` field mapped with `index_options` below `positions` (`docs` or `freqs`) makes the server
  refuse the search, which reaches the application as the client's `RequestError`.
- **Example:** a column over a `text` field with `"index_options": "freqs"`, searched for any word.
- **How it is known:** from the server's documentation of `index_options`; not run, and not a default mapping.
- **Requirement:** PRD-F11.

### A character that a normalizer folds to `*`, `?` or `\` can be searched as a wildcard

A keyword field with a normalizer that folds to ASCII (`asciifolding`) turns some characters into `*`, `?` or `\`,
in what it stores and in a search pattern, where they are then the pattern's own syntax. The library does not
read the mapping. It sends a substring search (the global search, a column search, `contains`, `starts`, `ends`
and their negations) with a fullwidth `＊` (U+FF0A), `？` (U+FF1F) or `＼` (U+FF3C) in a form the server reads as
that character, so it finds the rows holding the ASCII or the fullwidth character there. What is left is a limit
of the library.

- **What you see:**
  - A character the normalizer turns into several, as `⁇` (U+2047) into `??`, matches too much in a substring
    search. No form of query measured is right for it.
  - Another character that a normalizer folds to `*`, `?` or `\` is not covered: only the three above are.
  - A search text of more than 255 code points, and the global search on a column with a search-value hook, are sent
    as before, so the three marks match too much or miss their own row there.
  - A search with one of the three marks folds letters as a regular expression does, which from OpenSearch 3.1 on
    is a little more than other searches fold: `k` beside such a mark also finds the Kelvin sign. Not measured for
    these values; `docs/decisions/001-opensearch-case-folding-beyond-ascii.md` holds the measurement for others.
- **Example:** on such a field holding `axyb`, `a⁇b` and `a??b`, a search for `a⁇b` finds all three.
- **How it is known:** MEASURED 2026-11-22 on OpenSearch 3.10.0 and 2.19.7
  (`docs/decisions/003-opensearch-number-ranges-and-fullwidth-marks.md`); the three marks are also checked on
  both servers by the live tests. The second and third points are read in the code, not run on a server.
- **Requirement:** PRD-F9.

### OpenSearch's default limit of fields caps the searchable columns before the budget does

- **What you see:** an index takes 1,000 fields by default (`index.mapping.total_fields.limit`), and a text+keyword
  field is two of them (the field and its multi-field). A table of searchable columns on distinct fields therefore
  reaches that limit, about 500 text+keyword columns, before the budget of 960 conditions. The budget itself keeps the
  widest `bool` the library builds at 960 clauses, within the default `indices.query.bool.max_clause_count` of 1,024,
  which a server counts per `bool`.
- **Example:** the live test of the budget reads 960 columns that name one field, so it does not try the field limit.
- **How it is known:** the clause limit MEASURED 2026-11-22 on OpenSearch 3.10.0 and 2.19.7
  (`docs/decisions/005-the-largest-request-the-bounds-allow.md`); the field limit: documented, not run.
- **Requirement:** PRD-C17.

## Every backend

### A global search costs one condition per word and column, and the budget does not count what the adopter adds

- **What you see:** a request holds at most 960 conditions (PRD-C17): one word on one column, one list value and one
  range each count one, and a larger request is refused with `request_too_large`. A wide table's global search is the
  cost of words times the columns it covers. When nothing else is asked, the global search allows:

  | Words | 16 | 8 | 4 | 2 | 1 |
  |---|---|---|---|---|---|
  | Columns it covers | 60 | 120 | 240 | 480 | 960 |

  Every word of a column search, list value, range and criterion takes one from that. The columns are those that take
  part in the global search: declared searchable and searchable as text (a boolean is not, nor is a number or a date
  on OpenSearch). A hooked column counts one however many words the text holds. A `base_filter` and an expression
  column add to what the database receives and are stated here, not budgeted.
- **Example:** 16 words over a table of 61 searchable columns hold 976 conditions and are refused; 15 words hold 915.
- **How it is known:** the number is MEASURED (`docs/decisions/005-the-largest-request-the-bounds-allow.md`); the
  table is its arithmetic.
- **Requirement:** PRD-C17, PRD-E2.
