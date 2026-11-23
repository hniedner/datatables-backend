# datatables-backend

[![CI](https://github.com/hniedner/datatables-backend/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/hniedner/datatables-backend/actions/workflows/ci.yml)
[![CodeQL](https://github.com/hniedner/datatables-backend/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/hniedner/datatables-backend/security/code-scanning)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/hniedner/datatables-backend/badge)](https://scorecard.dev/viewer/?uri=github.com/hniedner/datatables-backend)
[![codecov](https://codecov.io/gh/hniedner/datatables-backend/graph/badge.svg)](https://codecov.io/gh/hniedner/datatables-backend)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https://raw.githubusercontent.com/hniedner/datatables-backend/main/pyproject.toml)](https://github.com/hniedner/datatables-backend/blob/main/pyproject.toml)
[![Licence: MIT](https://img.shields.io/github/license/hniedner/datatables-backend)](https://github.com/hniedner/datatables-backend/blob/main/LICENSE)

Server-side processing for [DataTables](https://datatables.net): paging, sorting, the global and column searches,
and [SearchBuilder](https://datatables.net/extensions/searchbuilder/) criteria. Data can come from:

- **SQL**: SQLAlchemy 2, async or sync.
- **OpenSearch**: async or sync client.

The library is read-only and framework-agnostic.

> Status: pre-alpha. The API may change before 0.1.0. See [docs/PRD.md](https://github.com/hniedner/datatables-backend/blob/main/docs/PRD.md) for the scope and design.

If you need a SQL backend with editing (DataTables Editor), use SpryMedia's own
[`datatables_server`](https://datatables.net/manual/python). This library adds:

- async SQLAlchemy;
- a search-engine backend;
- the same server-declared columns and safety rules on every source.

## Install

```sh
pip install "datatables-backend[sqlalchemy]"   # SQL
pip install "datatables-backend[opensearch]"   # OpenSearch (installs opensearch-py)
pip install "datatables-backend[fastapi]"      # the FastAPI dependency
```

## SQL

```python
from datatables_backend import Column, parse_request
from datatables_backend.sqlalchemy import SQLTable

users = SQLTable(
    users_table,  # a Table, join or subquery
    [Column('id'), Column('email', field='email_address'), Column('joined')],
    base_filter=users_table.c.active.is_(True),  # rows the table shows at all
)

params = parse_request(request_json)  # or the flat form/query parameters
async with AsyncSession(engine) as session:
    response = await users.fetch(params, session)  # fetch_sync() for a Session or Connection
return response.to_dict()
```

## OpenSearch

```python
from datatables_backend import Column
from datatables_backend.opensearch import IndexColumn, SearchTable

reports = SearchTable(
    'reports',
    [
        IndexColumn('id', text='keyword'),
        IndexColumn('title', text='text+keyword'),  # the dynamic-mapping default
        IndexColumn('body', text='text'),  # analysed only: no sorting
        Column('pages', type='number'),
        Column('created', type='date'),
        Column('published', type='boolean'),  # a column is a string unless declared: the mapping is not read
    ],
    unique_key='id',  # required: an index has no primary key to break ties with
)
response = await reports.fetch(params, AsyncOpenSearch(hosts=[...]))
```

**The unique key.** `unique_key` names a declared column (a tuple of names for a key of several) whose values are
unique for every document; OpenSearch needs it, since an index has no primary key to break ties with, and on SQL it
replaces the primary key. Its rules, the document `_id` and paging past the window are in [the OpenSearch
rules](https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md#opensearch).

## FastAPI

```python
from datatables_backend.fastapi import DataTablesParams


@app.post('/users/table')
async def users_table(params: DataTablesParams, session: SessionDep) -> dict[str, Any]:
    return (await users.fetch(params, session)).to_dict()
```

## What the server decides

The library does not change how a database compares values. Each rule below is stated in full in
[docs/rules.md](https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md), and what each engine does on its own in [docs/databases.md](https://github.com/hniedner/datatables-backend/blob/main/docs/databases.md).

- [Columns and ordering](https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md#columns-and-ordering): only declared columns are used, the server decides what
  sorts, NULL is lowest, and a descending order is the exact reverse.
- [Types and matching](https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md#types-and-matching): the declared type builds the filter, a date is matched by day,
  a day and a number have one spelling, and text matches in any case.
- [Errors](https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md#errors): a user's mistake comes back as a response with one of eight stable codes (the list is
  closed) and a text that says how to fix it; any other failure raises.
- [Requests](https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md#requests): bounded in length, lists, tree depth and conditions; a request the library cannot
  read raises `MalformedRequest`, answered 422 by the FastAPI dependency.
- [OpenSearch](https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md#opensearch) and [SQL](https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md#sql): the unique key, the document id, last pages,
  analysed text.

**Exceptions to "no surprise", each with its reason in the rules:**

- An OpenSearch column declared `text='text'` has no whole string to sort by, so it is unorderable.
- On PostgreSQL a column that can hold NULL needs its index declared `NULLS FIRST` to serve a sort.
- A column that carries a time zone is matched by its UTC day; SQLite and MySQL `DATETIME` store no offset.
- SQLite, and OpenSearch before 3.1, fold ASCII letters only.
- A SQL column with a type of its own, or none, is compared as sent, not folded.

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run basedpyright
```

## Licence

MIT. See [LICENSE](https://github.com/hniedner/datatables-backend/blob/main/LICENSE).
