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
- **Text matches in any case.** A search, and an exact match on a string column (SearchBuilder's `=` and `!=`, a
  multi-select, a `multi_values` list), ignores case on every backend, also under a case-sensitive collation.
  **Two exceptions, for letters outside ASCII:** SQLite folds ASCII letters only, and so does OpenSearch for a
  substring and for an exact match (`äpf` and `=` `äpfel` do not find `ÄPFEL`; the whole word `äpfel` does, on an
  analysed field). The library asks for no change to a schema or mapping to do more.
- **An exact match can be sent plain.** It folds case by default, which a plain index on the column cannot serve:
  declare an indexed column with `fold_case=False`, and its exact matches are sent plain and follow the column's
  collation or the field's mapping. A column with a `search_value` hook is compared that way too.
- **A SQL column with a type of its own is filtered by what it stores.** On a column whose SQLAlchemy type converts
  values (a `TypeDecorator`), the value of an exact match goes through the type's own conversion, as in a plain
  comparison, and case is then ignored on the stored value. A search looks at the stored text and converts
  nothing. An `Enum` column is searched and compared as text.
- **The user's mistakes come back as a response, not an exception.** A mistake is answered with an `error_code` and
  an `error` text that says what is wrong and what to do about it. The text never quotes the input, and names a
  column only by its declared name. The codes are `invalid_character` for a control character such as a tab, which
  the user cannot see, `invalid_number` or `invalid_date` for a value the column cannot read or hold,
  `page_out_of_range` for an OpenSearch page beyond the result window, and `invalid_filter` for any other (an
  unknown column, a value that is not a boolean, a condition the column's type does not take, a regex search). When
  a request holds several, a control character is answered first, then a number or date mistake, then any other,
  and the page last. The list of codes is closed: a code is added only in a release that says so, and a client
  that reads a code it does not know treats it as a refusal of the request and shows `error`. Every other failure
  raises, so the app decides how to report it; `DTResponse.query_failed(draw)` gives the usual shape.
- **A request the library cannot read is the integration's mistake, not the user's.** `parse_request` raises
  `MalformedRequest`, whose message names the request parameter and never a key or value, and whose `draw` is the
  request's own when that could be read. The FastAPI dependency answers it with 422 and
  `{"detail": {"message": ..., "draw": ...}}`.
- **Requests are bounded.** Page lengths are capped (1000 by default; "All" is refused), and LIKE and wildcard
  characters in search values are escaped.

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run basedpyright
```
