# datatables-backend

Server-side processing for [DataTables](https://datatables.net): paging, sorting, the global and column searches,
and [SearchBuilder](https://datatables.net/extensions/searchbuilder/) criteria. Data can come from:

- **SQL**: SQLAlchemy 2, async or sync.
- **OpenSearch**: async or sync client.

The library is read-only and framework-agnostic.

> Status: pre-alpha. The API may change before 0.1.0. See [docs/PRD.md](docs/PRD.md) for the scope and design.

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
        IndexColumn('body', text='text'),  # analysed only: free-text query syntax, no sorting
        Column('pages', type='number'),
        Column('created', type='date'),
        Column('published', type='boolean'),  # a column is a string unless declared: the mapping is not read
    ],
)
response = await reports.fetch(params, AsyncOpenSearch(hosts=[...]))
```

## FastAPI

```python
from datatables_backend.fastapi import DataTablesParams


@app.post('/users/table')
async def users_table(params: DataTablesParams, session: SessionDep) -> dict[str, Any]:
    return (await users.fetch(params, session)).to_dict()
```

## What the server decides

- **Only declared columns can be used.** The request can search, filter and sort only on the columns the server
  declares. Any other name is refused.
- **The declared type decides how a filter is built.** A column is a `string`, a `number`, a `date` or a `boolean`;
  SQL reads the type from the column unless it is declared (a MySQL `TINYINT(1)` read by reflection is a number, so
  declare it `boolean`). The client's SearchBuilder `type` is ignored. A boolean column takes `true` or `false` in
  any case, or `1` or `0`, is matched exactly, and takes no part in the global search.
- **The user's mistakes come back as a response, not an exception.** An unknown column, a value that is not a number,
  date or boolean, a condition the column's type does not take, or a regex search is answered with
  `error_code: "invalid_filter"` and a reason that never quotes the input. Every other failure raises, so the app
  decides how to report it; `DTResponse.query_failed(draw)` gives the usual shape.
- **Requests are bounded.** Page lengths are capped (1000 by default; "All" is refused), and LIKE and wildcard
  characters in search values are escaped.

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run basedpyright
```
