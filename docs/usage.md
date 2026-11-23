# Options and patterns

What a table takes beyond its columns, and how to use one in an app. The rules the server applies to a request are in
[rules.md](rules.md); what each database does on its own is in [databases.md](databases.md). Each section names where
its facts come from: a requirement of [PRD.md](PRD.md) or the docstring of the class.

## A shared total cache

`recordsTotal` is a count of every row, the slowest part of a draw on a large table. A `TotalCache` keeps it for `ttl`
seconds (300 by default) and is passed to a table as `total_cache=`:

```python
from datatables_backend import TotalCache

cache = TotalCache(ttl=60)  # one instance for every table and request of the process
users = SQLTable(users_table, columns, total_cache=cache)
```

- A total can be up to `ttl` seconds old: nothing outside the process tells the cache about a write. Call
  `cache.clear()` after a write that must show at once.
- A total is kept under what the table sends: on SQL the database's URL and the count with every bound value, on
  OpenSearch the index's name and the base filter. Rows scoped by anything else share a total, so give each such
  scope a cache of its own: a database session's state (row-level security, a role, a schema search path, the current
  database), OpenSearch's document-level security, or a second cluster with the same index name.
  [databases.md](databases.md#a-shared-totalcache-cannot-tell-two-scopes-apart-that-the-session-sets) has the case.
- A bound value enters the key by its `repr`, which must not change between requests.

Source: the docstring of `TotalCache`.

## Row hooks

`RowHooks` changes the rows a table returns. Each hook takes the row as a dict keyed by the columns' client-facing
names:

```python
from datatables_backend import RowHooks

users = SQLTable(
    users_table,
    columns,
    rows=RowHooks(
        shape=lambda row: UserOut(**row).model_dump(mode='json'),  # the row the client receives
        row_id=lambda row: f'user-{row["id"]}',  # DT_RowId: the id of the row's <tr>
        row_data=lambda row: {'email': row['email']},  # DT_RowData: jQuery data() on the <tr>
    ),
)
```

- `shape` runs first; `row_id` and `row_data` read the shaped row. A falsy `row_id` or `row_data` adds nothing.
- All three are optional.

Source: the docstring of `RowHooks`.

## Page length

A request for more rows than `max_length` is refused as a malformed request (`MalformedRequest`, 422 in the FastAPI
dependency). The default is 1000, and "All" (`length` -1) is refused. Set it where the request is parsed:

```python
params = parse_request(request_json, max_length=200)
```

The FastAPI dependency calls `parse_request` with the default and has no setting for it. To refuse pages over a lower
number, check `params.length` in the handler. To allow more than 1000, write a dependency of your own that reads the
request as `datatables_backend/fastapi.py` does and calls `parse_request(data, max_length=...)`.

Source: `parse_request`'s signature and docstring; `datatables_backend/fastapi.py`; PRD-P13 for the cap and PRD-E6 for
the refusal.

## The result window of OpenSearch

OpenSearch reads at most `index.max_result_window` rows from the start of a search (10,000 by default). Give the table
the index's own value, so the library knows where a page stops being readable:

```python
reports = SearchTable('reports', columns, unique_key='id', max_result_window=50_000)
```

The library does not change the cluster's setting. What a page beyond the window does is in
[rules.md](rules.md#opensearch): the first and the last pages are served, a page in the middle of a larger result is
refused with `page_out_of_range`.

Source: the docstring of `SearchTable`; PRD-C10 and PRD-C12.

## A filter per user: build the table for the request

A table is built once and reused (PRD-A2), and its `base_filter` is fixed when it is built. A filter that differs by
user or tenant therefore needs a table for each request. Building a table opens no connection and makes no request:
`SQLTable` reads only the declared columns of the `Table` it is given, and `SearchTable` takes no client, which comes
with `fetch`.

```python
cache = TotalCache()


def users_for(user):
    return SQLTable(
        users_table,
        columns,
        base_filter=users_table.c.owner_id == user.id,
        total_cache=cache,  # shared: the bound value of the filter is part of the key
    )


response = await users_for(current_user).fetch(params, session)
```

On OpenSearch the filter is a query: `SearchTable('reports', columns, unique_key='id', base_filter={'term': {'owner':
user.id}}, total_cache=cache)`, and the cache key holds it.

- Measured: building an `SQLTable` of 12 columns with one `base_filter` took 10 microseconds on a development laptop,
  with no engine in the process (2,000 builds). A table of more columns, or a source reflected on each request, was not
  measured.
- A shared cache stays right because the filter is in its key (the docstring of `TotalCache`). Pass only values whose
  `repr` is the same on each request: a value whose `repr` holds an address never finds its total again.

## For client authors: several values in one column

DataTables' own multi-select sends a column's selection as a regular expression, and the library reads it as a list of
values (PRD-F16): `^(red|green)$`, or `red|green`, made only of literal alternatives, matches exactly any of them.

```json
{"search": {"value": "^(red|green)$", "regex": true}}
```

A client that builds its own request can send the list as `multi_values` instead, which needs no escaping and is read
the same way (it is not part of the DataTables protocol, and the global search refuses it):

```json
{"search": {"value": "", "regex": false, "multi_values": ["red", "green"]}}
```

- An empty alternative, or an empty entry in `multi_values`, means the rows without a value (PRD-F17, PRD-F26):
  `^(|red)$` is "red or empty".
- A regular expression that holds anything beyond literal alternatives is refused with `invalid_filter`: an unescaped
  character of `. * + ? | ( ) [ ] { } \ $ ^` is such a part.
- A column search that holds both a value and a non-empty `multi_values` list is refused with `invalid_filter`.
- A list is bounded: 256 values in one list, 512 in all lists of a request ([rules.md](rules.md#requests)).

Source: PRD-F16, PRD-F17, PRD-F20, PRD-F38, PRD-F39 and the `Search` model in `protocol.py`.
