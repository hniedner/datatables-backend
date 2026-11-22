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

**Servers.** The library is built for OpenSearch 3.1 and later. CI runs the live tests on 3.10.0 and on 2.19.7, and
they pass on both. OpenSearch before 2.19 is not tested. 3.0 and 2.19 differ in two ways:

- **3.0 and 2.19 fold ASCII letters only** in a substring search and an exact match (see "Text matches in any
  case").
- **2.19 is slow on case-insensitive exact matches of long values.** A list of 256 values of 255 ASCII letters took
  2.5 and 3.8 s in two runs on 2.19.7, and 0.16 and 0.20 s on 3.10.0; one such value, 14 ms and 1 ms. The answers
  are the same on both. A column declared `fold_case=False` sends its exact matches plain, 2 ms for the same list,
  and gives up the fold for them: they then match case as the field's mapping decides.

**What the fold of letters outside ASCII costs.** A value with such a letter is a regular-expression query. On
3.10.0 a list of 256 such values of 255 letters took 0.37 s, where the same list as term queries, which do not fold
those letters, took 0.06 s; a global search of 16 such words took 166 ms, against 111 ms as wildcards. On 2.19.7 the
list took 2.8 s as regular expressions and 2.7 s as terms. (All figures: 512 MB of heap, about 2,000 documents on
one shard, the least of 5 runs: the cost of the query, not of a large index.)

**On every version**, a substring search is a wildcard or regular-expression query, and so is an exact match whose
value has a cased character outside ASCII (on a column that folds, up to 255 code points): the cluster must allow
them (`search.allow_expensive_queries`, on by default), and `index.max_regex_length` (1,000 by default, counted in
UTF-16 units) must not be set under a regular expression's length, which is up to 768. A cluster that refuses the
query raises, as any server failure does. These limits hold on every version, 3.0 and 2.19 included:

- A value without a cased character outside ASCII folds ASCII letters only: `k` does not find the Kelvin sign, `s`
  not the long s. A substring or an exact match with `i` does not find a dotted capital `İ`.
- A value longer than 255 code points folds ASCII letters only.
- **A normalizer that rewrites a character into punctuation.** On a keyword field with such a normalizer
  (`asciifolding` does it to `⒜`, `⑴`, `⁇`), a value that holds a cased character outside ASCII and such a
  character can fail, miss its own row or match other rows. A `lowercase` normalizer is not affected.
- **A normalizer that trims.** On a keyword field with a `trim` normalizer, a search text with a cased character
  outside ASCII and a space at its start or end finds fewer rows than the same text without the space: the space is
  kept where the search is open (`contains`, a global or column search, `starts` with a space at the end, `ends`
  with one at the start). `Müller ` with the space finds only values that hold `Müller ` followed by more text. An
  exact match, and the anchored end of `starts` and `ends`, are trimmed as the field is.

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
  multi-select, a `multi_values` list), ignores case on every backend, also under a case-sensitive collation. In
  SQL, which columns count as string columns for an exact match is narrower than it sounds: see "Only a plain
  string column is folded" below. **Two exceptions, for letters outside ASCII:** SQLite folds ASCII letters only, and
  so does OpenSearch before 3.1 for a substring and for an exact match (`äpf` and `=` `äpfel` find `ÄPFEL` from
  OpenSearch 3.1 on, not on 3.0 or 2.19; the whole word `äpfel` finds it on every version, on an analysed field).
  Some values fold ASCII letters only on every OpenSearch version: see "On every version" in the OpenSearch
  section. The library asks for no change to a schema or mapping to do more.
- **An exact match can be sent plain.** It folds case by default, which a plain index on the column cannot serve:
  declare an indexed column with `fold_case=False`, and its exact matches are sent plain and follow the column's
  collation or the field's mapping. A column with a `search_value` hook is compared that way too.
- **Only a plain string column is folded.** In SQL an exact match ignores case on a column whose SQLAlchemy type
  is a string type (`String`, `Text`, `Unicode`, `CHAR` and the like) or an `Enum`, which is searched and compared
  as text. **The exceptions:** a column with a type of its own (a `TypeDecorator`, SQLAlchemy's `Uuid`) compares as
  that type compares, since it converts values in its own way; an expression declared without a type, and a
  column of another type declared `type='string'`, are compared as sent. To have an expression folded, give it a
  string type: `func.upper(users.c.name, type_=String)` or `literal_column('upper(name)', String)`. A SQL function
  called through `func` has no type unless it is given one.
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
