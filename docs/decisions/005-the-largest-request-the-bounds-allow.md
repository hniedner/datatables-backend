# 005: The largest request the bounds allow

This is the record of one measurement made for issue #69 on 2026-11-22: how many clauses and parameters the largest
request PRD-F14 and PRD-F38 allow makes, and what each server does with it. The requirements are in `docs/PRD.md`.
Nothing here is a requirement.

## The request

`largest_request` in `tests/conftest.py`: a global search of 16 words, 62 column searches of 16 words, two lists of
256 values (two exact values of days on SQL, one `term` each on OpenSearch) and 64 SearchBuilder criteria, over 64
columns that the global search covers (`largest_search_request` for OpenSearch, `largest_sql_request` for SQL).

## OpenSearch

The recording client (`tests/test_opensearch.py`) counts what is sent, over 64 text+keyword columns: 4,672 leaf
queries (2,048 global, 1,984 column searches, 512 list `term` queries, 128 criteria), 6,834 clauses of `bool` queries
(a leaf or another `bool` each) and 256 in the widest `bool`.

Sent to a server (CI run 45078213951 of pull request #88, `tests/test_opensearch_live.py`), OpenSearch 3.10.0 and
2.19.7 both answered it, with the default `indices.query.bool.max_clause_count` of 1,024. The limit is evidently not
counted over the leaves or over every child.

A control settled it (CI run 45080243788, both servers): a single `bool` of 1,024 `should` clauses on a mapped field
of an index that holds documents was answered, and one of 1,025 was refused. So the limit is counted per `bool`. The
widest `bool` the library builds is max(C, 256), C being the columns the global search covers (one `should` clause
for each, whatever its kind) and 256 a list of values, so the clause limit needs no entry in `docs/databases.md`
below 1,025 searchable columns (the index's own limit of fields comes first, see its entry). The control was a one-off measurement of the server and is not kept as a test; the live test of
the largest request stays as the guard. Not measured: a changed limit, other servers.

## SQL

Compiled on the PostgreSQL, MySQL, SQL Server and SQLite dialects, the filtered count binds the same number of
parameters on each: 2,144 + 16 x C, where C is the number of columns the global search covers (the page's statement
binds two more, its offset and its limit). 2,144 is the two lists of days (1,024), the criteria (128) and the 62
column searches (992). C = 65 gives 3,184 and C = 103 gives 3,792.

| Database | Result |
|---|---|
| SQL Server 2022, ODBC Driver 18 | refused at C = 65 (3,184 parameters): error 07002 "COUNT field incorrect" |
| PostgreSQL, MySQL | answered |
| SQLite | refused: "Expression tree is too large (maximum depth 1000)", with more than 1,000 conditions joined by AND |

So the bounds alone keep a request within SQL Server's parameters at no number of columns: the request is over 2,100
at C = 0. Issue #89 closed the gap with a budget of conditions, below; the measurements for it follow. After it, the
request above is refused with `request_too_large` before anything is queried, and the tests that built it are gone.

## The budget (issue #89, 2026-11-22)

The owner decided a budget of conditions (PRD-C17): one word on one column, one list value and one range each count
one. The number is 960, which is 16 words over 60 columns. The measurements below bound it from above: SQLite's
depth allows 991 - 9 = 982 conditions at the worst shape, and SQL Server's parameters allow 2,097 - 2 = 2,095 in the
filtered statement, where the worst request under 960 binds 1,536.

### What a condition binds

Read from the code and checked by `test_a_condition_binds_at_most_two_parameters` (every kind, on SQLite,
PostgreSQL, MySQL and SQL Server, compiled): a condition binds one parameter, except a range and a day of a list on a
column that may hold a time, which bind two. The worst request under the budget binds 512 x 2 (days) + 64 x 2
(ranges) + 384 = 1,536 parameters in the filter and 1,538 in the page.

### SQL Server 2022, ODBC Driver 18, the parameters of one statement

A test built a global search of one word over N columns, so that N parameters are bound in the filtered count and
N + 2 in the page. Parameters actually sent, per statement (total, filtered count, page), and the answer; CI run
45083913250 (the first, 45083578654, tried 2,098 and 2,099 only, and both were refused):

| N | filtered count | page |
|---|---|---|
| 2,080 to 2,095 | answered | answered (up to 2,097 parameters) |
| 2,096 | answered | refused, 2,098 parameters |
| 2,097 | answered | refused, 2,099 parameters |
| 2,098 | refused | not sent |
| 2,099 | refused | not sent |

Every refusal is error 8003 from the server ("The incoming request has too many parameters. The server supports a
maximum of 2100 parameters"). So SQL Server answered a statement of 2,097 bound parameters and refused 2,098. The gap
to the 2,100 of the message is not explained here. The budget's largest statement binds 1,538, which leaves
2,097 - 1,538 = 559 parameters for a `base_filter` and for expression columns, which bind in all three statements.
The first measurement of this decision was error 07002 "COUNT field incorrect" at 3,184 parameters; no run in the
range above showed it.

### SQLite, the depth of an expression

SQLite 3.53.1 with Python 3.14.7: the build's limit is 1,000, read from the error text ("maximum depth 1000"). SQLAlchemy
renders a word as `lower(x) LIKE lower(?) ESCAPE '\'`, so a leaf is three levels high and a chain adds one level per
condition. Conditions counted as the budget counts them; "answered" is the largest and "refused" the next, found by
bisection in a script outside the repository:

| shape | answered | refused |
|---|---|---|
| words of column searches, joined by AND | 997 | 998 |
| the global search, one word over C columns | 993 | 994 |
| the global search over C columns and 64 `contains` criteria | 993 | 994 |
| the global search over C columns and 64 `!between` criteria | 993 | 994 |
| the global search over C columns and 64 `!between` criteria in 8 nested groups | 1,048 | 1,049 |
| a negated word of the global search over C columns | 991 | 992 |
| two lists of 256 days first, then words | 1,252 | 1,253 |

The depth was at most 9 above the number of conditions in any shape, the worst being a negated word. The shapes that
count more than they are deep are those whose conditions are not on the chain. 960 + 9 = 969, 31 inside the limit. A
SQLite build with another `SQLITE_MAX_EXPR_DEPTH` was not measured.

### OpenSearch

The widest `bool` of a request at the budget is one word over 960 columns: 960 clauses, each a `bool` of two (a
`match_phrase` and a `wildcard`) when the column is text+keyword. That is within the default
`indices.query.bool.max_clause_count` of 1,024, which the control above showed to be counted per `bool`
(`test_the_widest_bool_of_a_request_of_the_budget_holds_at_most_the_default_clause_limit`). The live test of the
budget (960 columns that name one field) is answered on 3.10.0 and 2.19.7. The index's default limit of 1,000 fields
(a text+keyword field is two) caps the searchable columns on distinct fields before the budget: documented, not run.
