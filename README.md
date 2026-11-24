# datatables-backend

[![CI](https://github.com/hniedner/datatables-backend/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/hniedner/datatables-backend/actions/workflows/ci.yml)
[![CodeQL](https://github.com/hniedner/datatables-backend/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/hniedner/datatables-backend/actions/workflows/github-code-scanning/codeql)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/hniedner/datatables-backend/badge)](https://scorecard.dev/viewer/?uri=github.com/hniedner/datatables-backend)
[![codecov](https://codecov.io/gh/hniedner/datatables-backend/graph/badge.svg)](https://codecov.io/gh/hniedner/datatables-backend)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https://raw.githubusercontent.com/hniedner/datatables-backend/main/pyproject.toml)](https://github.com/hniedner/datatables-backend/blob/main/pyproject.toml)
[![Licence: MIT](https://img.shields.io/github/license/hniedner/datatables-backend)](https://github.com/hniedner/datatables-backend/blob/main/LICENSE)

Server-side processing for [DataTables](https://datatables.net): paging, sorting, the global and column searches,
and [SearchBuilder](https://datatables.net/manual/extensions/searchbuilder/) criteria. Data can come from:

- **SQL**: SQLAlchemy 2, async or sync.
- **OpenSearch**: async or sync client.

The library is read-only and framework-agnostic.

> Status: 0.1.1, alpha (PyPI classifier "Development Status :: 3 - Alpha"). Before 1.0, a patch release (0.x.y) may add
> features and never breaks; a minor release (0.x) may break the Python API and lists each such change in
> [CHANGELOG.md][changelog], so `~=0.1.1` takes every feature and fix and no break. The same holds on the wire: a patch
> release may answer a request that an earlier one refused, and nothing else changes, except that a patch release may
> refuse a request to close a security hole, and its notes say so; what an accepted request means, the shape of the
> answer and the set of error codes change only in a minor release whose notes say so. See [docs/PRD.md][prd] for the
> scope and design.

If you need a SQL backend with editing (DataTables Editor), use SpryMedia's own
[`datatables_server`](https://pypi.org/project/datatables-server/). This library adds:

- async SQLAlchemy;
- a search-engine backend;
- the same server-declared columns and safety rules on every source.

datatables-backend is not affiliated with or endorsed by DataTables or SpryMedia.

## Install

```sh
pip install "datatables-backend[sqlalchemy]"        # SQL, sync
pip install "datatables-backend[sqlalchemy-async]"  # SQL, async (adds greenlet): also your async driver, e.g. asyncpg
pip install "datatables-backend[opensearch]"        # OpenSearch, sync (installs opensearch-py)
pip install "datatables-backend[opensearch-async]"  # OpenSearch, async (adds aiohttp)
pip install "datatables-backend[fastapi]"           # the FastAPI dependency
```

Import from `datatables_backend` and from its `sqlalchemy`, `opensearch` and `fastapi` modules; any other module is
internal.

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

A filter that differs per user goes to `fetch(params, session, scope=...)`, not to the table (`SearchTable.fetch` takes
the same keyword).

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
        Column('published', type='boolean'),  # a string unless declared: declare every date, number, boolean
    ],
    unique_key='id',  # required: an index has no primary key to break ties with
)
response = await reports.fetch(params, AsyncOpenSearch(hosts=['http://localhost:9200']))
```

**The unique key.** `unique_key` names a declared column (a tuple of names for a key of several) whose values are
unique for every document; OpenSearch needs it, since an index has no primary key to break ties with, and on SQL it
replaces the primary key. Its rules, the document `_id` and paging past the window are in [the OpenSearch
rules][rules-opensearch].

## FastAPI

```python
from datatables_backend.fastapi import DataTablesParams


@app.post('/users/table')
async def users_table(params: DataTablesParams, session: SessionDep) -> dict[str, Any]:
    return (await users.fetch(params, session)).to_dict()
```

On the page, DataTables' default POST (a form) is read as it comes, so the client needs no `data` or `contentType`:

```js
DataTable.ext.errMode = (settings, techNote, message) => console.warn(message); // no alert(); the page shows the error

const table = new DataTable('#users', {
  serverSide: true,
  processing: true,
  ajax: { url: '/users/table', type: 'POST' },
  columns: [{ data: 'id' }, { data: 'email' }, { data: 'joined' }],
});

table.on('xhr', (e, settings, json) => {
  document.querySelector('#users-error').textContent = json.error ?? ''; // json.error_code is the stable code
});
```

A user's mistake comes back as HTTP 200 with `error` and `error_code` in the answer. DataTables (checked in 3.1.5)
shows an `error` in a browser `alert()` unless `DataTable.ext.errMode` says otherwise, and `errMode: 'none'` would also
silence its own configuration warnings, so the example logs them. `#users-error` is an element of yours.

ColumnControl's text, number and list filters are answered, and an active search of its date control is refused with a
message that says so. They work with DataTables' default POST and with `submitAs: 'json'`, and not with an `ajax.data`
that returns a string: ColumnControl adds its filters after `ajax.data` has run, so they would never leave the browser.
Under server-side processing a list shows the options you give it as static `options` on the control:

```js
columns: [
  {
    data: 'status',
    columnControl: [[{
      extend: 'searchList',
      options: [{ label: 'Open', value: 'open' }, { label: 'Closed', value: 'closed' }],
    }]],
  },
],
```

A value is text or a whole number. A list with static `options` shows them and ignores options in the answer
([decision 007][decision-007]); an app whose options come from the data adds
`columnControl: {<column>: [{label, value}]}` to the answer itself (see [#183][issue-183]), and an answer with
`columnControl: {}` hides a list that has none.

Options (`TotalCache`, `RowHooks`, page limits, `scope=` on `fetch` for a per-user filter): [docs/usage.md][usage].

## Tested with

CI runs the tests on Python 3.12 to 3.15 and on SQLite (sync, and async with aiosqlite); PostgreSQL 18 (psycopg 3, sync
and async; psycopg2 with asyncpg); MySQL 8.4 (pymysql, aiomysql; also mysqlclient 2.3, sync only); MariaDB 12.3
(pymysql, aiomysql); SQL Server 2022 (pyodbc, aioodbc, ODBC Driver 18); and OpenSearch 3.10.0 and 2.19.7. It also runs
the oldest supported releases: SQLAlchemy 2.0.31, Pydantic 2.10, opensearch-py 2.6 and FastAPI 0.133.

## What the server decides

The library does not change how a database compares values. Each rule below is stated in full in
[docs/rules.md][rules], and what each engine does on its own in [docs/databases.md][databases].

- [Columns and ordering][rules-columns]: only declared columns are used, the server decides what sorts, NULL is
  lowest, and a descending order is the exact reverse (one exception below).
- [Types and matching][rules-types]: the declared type builds the filter, a date is matched by day, a day and a number
  have one spelling, and text matches in any case.
- [Errors][rules-errors]: a user's mistake comes back as a response with one of eight stable codes (the list is
  closed) and a text that says how to fix it; any other failure raises.
- [Requests][rules-requests]: bounded in length, lists, tree depth and conditions; a request the library cannot read
  raises `MalformedRequest`, answered 422 by the FastAPI dependency.
- [OpenSearch][rules-opensearch] and [SQL][rules-sql]: the unique key, the document id, last pages, analysed text.

**Exceptions to "no surprise", each with its reason in the rules:**

- On OpenSearch, an order on a field of several values: descending is not the exact reverse of ascending.
- An OpenSearch column declared `text='text'` has no whole string to sort by, so it is unorderable.
- On PostgreSQL a column that can hold NULL needs its index declared `NULLS FIRST` to serve a sort.
- A column that carries a time zone is matched by its UTC day; SQLite and MySQL `DATETIME` store no offset.
- SQLite, and OpenSearch before 3.1, fold ASCII letters only.
- A SQL column with a type of its own, or none, is compared as sent, not folded.
- On OpenSearch a page in the middle of more than `max_result_window` rows is refused (`page_out_of_range`), where the
  first and last pages work and SQL serves every page ([paging past the window][rules-opensearch]).
- An OpenSearch column of analysed text only answers the word search, `contains`, `null`, `!null` and empty lists,
  where SQL answers every condition ([the rule][rules-opensearch]).
- OpenSearch's global search skips number and date columns, and every backend's skips boolean columns
  ([the rule][rules-types]).
- On OpenSearch a stored `""` is not empty, where on SQL it is ([the rule][rules-opensearch]).
- On OpenSearch a number outside its field's mapped range fails the request, a stated limit of this library
  ([the entry][db-number-range], [issue #47][issue-47]).

[prd]: https://github.com/hniedner/datatables-backend/blob/main/docs/PRD.md
[changelog]: https://github.com/hniedner/datatables-backend/blob/main/CHANGELOG.md
[db-number-range]: https://github.com/hniedner/datatables-backend/blob/main/docs/databases.md#a-number-outside-the-range-of-its-field-fails-the-request
[issue-47]: https://github.com/hniedner/datatables-backend/issues/47
[decision-007]: https://github.com/hniedner/datatables-backend/blob/main/docs/decisions/007-columncontrol-wire-form.md#the-lists-options-under-server-side-processing-167
[issue-183]: https://github.com/hniedner/datatables-backend/issues/183
[usage]: https://github.com/hniedner/datatables-backend/blob/main/docs/usage.md
[rules]: https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md
[databases]: https://github.com/hniedner/datatables-backend/blob/main/docs/databases.md
[rules-columns]: https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md#columns-and-ordering
[rules-types]: https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md#types-and-matching
[rules-errors]: https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md#errors
[rules-requests]: https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md#requests
[rules-opensearch]: https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md#opensearch
[rules-sql]: https://github.com/hniedner/datatables-backend/blob/main/docs/rules.md#sql

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run basedpyright
```

Commit headers are conventional, `type(scope)!: subject` of at most 100 characters (types feat, fix, docs, test, ci,
build, refactor, chore, style); the commit-msg hook and CI's `commits` job check them.

Licence: MIT ([LICENSE](https://github.com/hniedner/datatables-backend/blob/main/LICENSE)).
