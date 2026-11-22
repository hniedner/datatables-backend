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
counted over the leaves or over every child. A count per `bool` fits, since the widest holds 256, but it was not
varied to confirm. Not measured: a `bool` of more than 1,024 clauses, a changed limit, other servers.

## SQL

Compiled on the PostgreSQL, MySQL, SQL Server and SQLite dialects, the filtered count binds the same number of
parameters on each: 2,144 + 16 x C, where C is the number of columns the global search covers (the page's statement
binds two more, its offset and its limit). 2,144 is the two lists of days (1,024), the criteria (128) and the 62
column searches (992). C = 65 gives 3,184 and C = 103 gives 3,792.

| Database | Result |
|---|---|
| SQL Server 2022, ODBC Driver 18 | refused at C = 65: error 07002 "COUNT field incorrect", the limit of 2,100 parameters |
| PostgreSQL, MySQL | answered |
| SQLite | refused: "Expression tree is too large (maximum depth 1000)", with more than 1,000 conditions joined by AND |

So the bounds alone keep a request within SQL Server's parameters at no number of columns: the request is over 2,100
at C = 0. The threshold of SQLite's depth was not measured.
