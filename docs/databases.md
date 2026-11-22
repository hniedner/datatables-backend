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

### Your own parameters count toward the 2,100 limit

- **What you see:** SQL Server refuses a statement with more than 2,100 parameters. The bounds of a request
  (PRD-F38) keep what the library binds for it to about 1,200, plus one for each column the global search covers, but
  a `base_filter` or an expression column that binds parameters of its own adds to them.
- **Example:** a `base_filter` that holds a list of 1,000 values, beside a request of the largest size.
- **How it is known:** by arithmetic from the bounds; the largest request (SPEC-C17.1) runs on this leg.
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

### A request at the bounds can pass the cluster's clause limit

This is a limit of the library, not of OpenSearch: the bounds of PRD-F38 do not count clauses.

- **What you see:** OpenSearch refuses a query with more than 1,024 clauses by default
  (`indices.query.bool.max_clause_count`). A request within every bound can still pass it: two lists of 256 values, 64
  criteria of three words each and 62 column searches of three words each make about 1,029 by the reviewer's count. The
  request fails as a server failure.
- **Example:** two `multi_values` lists of 256 strings, with a column search of three words on 62 more columns.
- **How it is known:** counted from the query the library builds; not run on a cluster. Raise the cluster's limit, or
  report a need for lower bounds.
- **Requirement:** PRD-F38.

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
  - A search text of more than 255 code points, and a search on a column with a search-value hook, are sent as
    before, so the three marks match too much or miss their own row there.
  - A search with one of the three marks folds letters as a regular expression does, which from OpenSearch 3.1 on
    is a little more than other searches fold: `k` beside such a mark also finds the Kelvin sign. Not measured for
    these values; `docs/decisions/001-opensearch-case-folding-beyond-ascii.md` holds the measurement for others.
- **Example:** on such a field holding `axyb`, `a⁇b` and `a??b`, a search for `a⁇b` finds all three.
- **How it is known:** MEASURED 2026-11-22 on OpenSearch 3.10.0 and 2.19.7
  (`docs/decisions/003-opensearch-number-ranges-and-fullwidth-marks.md`); the three marks are also checked on
  both servers by the live tests. The second and third points are read in the code, not run on a server.
- **Requirement:** PRD-F9.
