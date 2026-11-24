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
- The cache holds at most `max_entries` totals (10,000 by default, about 2.6 MB; PRD-C14). When it is full, the total
  stored longest ago is dropped, and a read changes nothing. With more distinct totals inside one `ttl` than that, most
  requests count again, so set `max_entries` to at least the number of tables, tenants or users that each have their own
  total.

Source: the docstring of `TotalCache`; decision [008](decisions/008-total-cache-eviction.md).

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

## Names for code around the table

Three names serve code that sits beside a table, not inside `fetch`:

- `datatables_backend.MAX_PAGE_LENGTH` is the default `max_length` of `parse_request` (1000), for a route of your own
  that bounds a page by the same number.
- `datatables_backend.ErrorCode` is the type of `DTResponse.error_code` (the library's codes; `DTResponse.query_failed`
  adds `'query_failed'`). A later release may add a code, so handling keeps a default branch.
- `datatables_backend.opensearch.partial_answer(answer, *, index=...)` says whether a `search` or `count` answer you
  ran yourself is not known to cover every shard: it returns True for an unverifiable answer as well as a partial
  one, and logs at ERROR when it does. `fetch` already applies it to its own answers.

Source: the docstrings of `partial_answer`, `parse_request` and `DTResponse`.

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
  `repr` is the same on each request: a value whose `repr` holds an address never finds its total again, and one entry
  is stored for each request until `max_entries` (the cache stays bounded).

## The page's request: a form or JSON

The FastAPI dependency reads the request from the body when there is one, and from the query string when there is
none; it never merges the two (DataTables adds a cache-buster `_` to the query of every request, POST included). A
body that is a JSON object is read as JSON whatever its Content-Type; any other body is read as a form. DataTables'
default POST is a form, so `ajax: { url, type: 'POST' }` needs nothing more, as the README shows. To send JSON instead:

```js
ajax: { url: '/users/table', type: 'POST', submitAs: 'json' },
```

DataTables 3.1.5 keeps the form's Content-Type for `submitAs: 'json'` unless the page sets another (READ,
`dataTables.mjs`: the default `contentType` at line 621, `submitAs` setting JSON only if none is set at lines 641-646;
not run), so the JSON travels under the form type. **0.1.0rc1 reads a JSON body only under
`Content-Type: application/json`**, and answers this request with a 422: with rc1, add `contentType: 'application/json'`
beside `submitAs`. The release after it reads the body whatever its Content-Type, and `submitAs: 'json'` alone is
enough.

Use `submitAs` rather than building the JSON yourself in `ajax.data`: hooks such as ColumnControl's `preXhr` run after
`ajax.data` and add their parts to the request object, which would by then be a string. (READ from DataTables 3.1.5,
`dataTables.mjs`: the order of `ajax.data` and `preXhr` at lines 5531-5572; not run.)

A form under a form Content-Type of more than 1,000 fields is refused by Starlette with HTTP 400 (`Too many fields`),
before the library reads it; a form under any other Content-Type is read within the same 1,000 fields and answered 422
beyond them. A table sends six fields for each column, so that is about 165 columns (measured through the FastAPI test
client: 160 columns passed, 170 were refused; the same request as JSON passed at 500). Send JSON for a table that wide.
A JSON body has no such limit, and nothing here limits the size of a body: 4.5 MB of JSON (40,000 columns) was read in
0.35 s through the test client. A limit on the body belongs to the server or the proxy in front of the app.

Source: `parse_datatables_request` in `datatables_backend/fastapi.py`; SPEC-A9.1 to A9.8; `tests/test_readme.py`.

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

## Coming from sqlalchemy2-datatables

`sqlalchemy2-datatables` (import name `datatables`) is retired; 0.7.0 is its last release, and its notice points here.
This is the same job in this library. The left column is that package's own README (0.7.0, read on PyPI).

| sqlalchemy2-datatables | datatables-backend |
| --- | --- |
| `DataTable(request_params=params, table=table, column_names=[...], engine=engine, callbacks=callbacks)`, built for each request | `SQLTable(table, [Column('id'), ...], rows=RowHooks(...))`, built once (or per request, see above); `params = parse_request(request_json)` |
| `datatable.output_result()` | `(await table.fetch(params, session)).to_dict()`; `fetch_sync` for a sync `Session` or `Connection` |
| an `Engine` | a `Session` or `Connection`; `AsyncSession` or `AsyncConnection` for the async `fetch` |
| `column_names`, the names of the table's columns | `Column(name, field=..., type=...)`: the server declares each column the client may see, search and sort |
| `callbacks` (a `DTDataCallbacks`, for the `DT_*` attributes) | `RowHooks(row_id=..., row_data=..., shape=...)` |

What differs in behaviour, each stated in [rules.md](rules.md):

- A column the server did not declare cannot be filtered or sorted by; the request is answered with an error.
- A request needs a `length` of 1 to 1000. "All" (`-1`) is refused (`MalformedRequest`), where the old package's
  notice says its default `length` of `-1` reads the whole table.
- `LIKE` wildcards in a search value are escaped, and a client's regular expression is not run (the categorical
  forms in the section above are the exception: literal alternatives only).
- A user's mistake comes back in the response, with an `error_code` and a text that says how to fix it. A failure of
  the database raises in your application; its message is not sent to the client.
- The response holds `draw`, `recordsTotal`, `recordsFiltered` and `data` (and `error` and `error_code` for a
  mistake). The old output also held `start` and `length`.
