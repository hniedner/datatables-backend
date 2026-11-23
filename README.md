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
unique for every document. Every order ends with it, in the direction of the first sort entry, and a request without
an order comes in its ascending order, so pages are stable. Declare it `IndexColumn(..., text='keyword')` or as a
number. A key is a declared column, so its value is sent in every row; `searchable=False` and `orderable=False` keep it
out of the client's controls. A table without a key raises `SchemaError`.

**The document id as the key.** An index whose documents hold no unique field can name the document's own id:
`unique_key='_id'` (alone or in a tuple) needs no declared column, and nothing in the mapping or the ingest changes. It
is the key of last resort: a sort on `_id` uses field data held in memory, where a keyword field is read from disk, and
the key is applied to every search, so prefer a `keyword` field where there is one. A cluster that sets
`indices.id_field_data.enabled` to `false` refuses the sort; that is raised, not answered as the user's mistake (see
`docs/databases.md`). To show the id, declare `Column('_id')`: it reads the hit's id and can be sorted by, but the
document id is not searchable, and `searchable` is ignored on it.

SQL takes the same `unique_key`, which replaces the primary key when declared. Without it, a source must have a primary
key, and a subquery must select every primary-key column of each table it reads (in a join too, and in a `WHERE` or a
scalar subquery); otherwise it raises `SchemaError`. A `UNION` is not checked: declare `unique_key` on a subquery over
one.

**Analysed text.** On an analysed column a word is a phrase of its tokens, the nearest to SQL's substring: `foo-bar`
finds `foo bar` but not `foo` alone. Analysed text holds no spaces, so a search for a space finds nothing on an
analysed-only column.

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
  [What each database does on its own](docs/databases.md) says which characters folded to `*`, `?` or `\` a search
  still reads as a wildcard.
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

The library does not change how a database compares values. Where a database's own rule changes what a user sees
(trailing spaces on SQL Server and under a MySQL PAD SPACE collation, SQLite's inexact decimals), and where the
library itself has a limit on one engine, [docs/databases.md](docs/databases.md) says so, engine by engine.

- **Only declared columns can be used.** The request can search, filter and sort only on the columns the server
  declares. A filter or a sort on any other name is refused. A column the page only renders (one without a name, or
  with a name the server did not declare) is not a mistake: the global search leaves it out, and DataTables'
  default order on it is skipped. For a column with a name the server did not declare, the order is skipped only
  when the request marks the column unorderable; otherwise it is refused, which shows a misconfigured table.
- **The server decides what can be sorted and searched.** A sort on a column declared `orderable=False` is refused,
  unless the request itself marks the column unorderable: DataTables sends its default order on the first column
  whatever that column allows, so such an entry is skipped. A column the request marks unorderable and the server
  allows is sorted by. `orderable` is `None` by default, which means yes, **with one exception:** an OpenSearch
  string column declared `text='text'` has no whole string to sort by, so it is unorderable, and declaring it
  `orderable=True` raises `SchemaError`. A column named twice in an order is sorted by its first entry.
- **NULL is the lowest value, and a descending order is the exact reverse of the ascending one.** NULL, or a
  missing field on OpenSearch, comes first ascending and last descending on every backend. A tie is broken by the
  unique key (the declared `unique_key`, else a SQL source's primary key), in the direction of the first sort entry.
  **One exception:** on PostgreSQL the order of a column that can hold NULL says where NULL goes (`NULLS FIRST`,
  `NULLS LAST`), which an index in the default order does not serve: to sort a large table by such a column,
  declare its index `NULLS FIRST`. A column declared `nullable=False` on a table (a primary key's columns are) is
  ordered plainly and keeps its index.
- **The declared type decides how a filter is built.** A column is a `string`, a `number`, a `date` or a `boolean`;
  SQL reads the type from the column unless it is declared (a MySQL `TINYINT(1)` read by reflection is a number, so
  declare it `boolean`). The client's SearchBuilder `type` is ignored. A boolean column takes `true` or `false` in
  any case, or `1` or `0`, is matched exactly, and takes no part in the global search.
- **A date is matched by day, and a timezone-aware column by its UTC day.** `=`, `<`, `between` and the other
  conditions refer to whole days, on a date and a date-time column alike. For a column that carries a time zone
  (`DateTime(timezone=True)`, SQL Server's `DATETIMEOFFSET`, any OpenSearch date field) the day is the UTC day, in
  the conditions and in the day text a search matches, whatever the database session's zone or the offset a value
  was stored with. **The exceptions:** SQLite and a MySQL `DATETIME` store no offset, so write UTC there, and a
  MySQL `TIMESTAMP` gives the UTC day only when the session `time_zone` is `+00:00`. A multi-select or
  `multi_values` list on a date column matches each entry as `=` does, and an empty `multi_values` entry means the
  rows without a date.
- **A day and a number have one spelling** where a value is read as one: in a SearchBuilder condition, in a
  multi-select or `multi_values` list, and in an OpenSearch column search on a date or number column. (A plain text
  search in SQL matches a date's or number's text and refuses nothing.) A number is an optional sign, digits with an
  optional decimal point and an optional exponent, in ASCII: `7`, `-7.5`, `.5`, `1e3`. A day is `YYYY-MM-DD` in ASCII
  digits; a time of day may follow it after a space or a `T` (`2024-03-05 10:00:00`, as SearchBuilder sends it for a
  formatted column): it must exist and is then ignored. White space around either is ignored. Any other spelling is
  `invalid_number` or `invalid_date`: digits of another script, `1_000`, `20240305`. **A zone or an offset after the
  time is refused, not ignored** (`...T10:00Z`, `...T10:00+02:00`): the instant it names can fall on another UTC day
  than the one written, so send the day.
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
- **The user's mistakes come back as a response, not an exception.** A mistake is answered with an `error_code` and an
  `error` text that says what is wrong and what to do about it. The text never quotes the input, and names a column only
  by its declared name. The codes are `invalid_character` for a control character such as a tab, which the user cannot
  see, `value_too_long` for a value of more than 255 characters (code points), `too_many_words` for search text of more
  than 16 words, `request_too_large` for a request of more than 960 conditions (a search word counts once for each
  column searched, a selected value and a range once each; 16 words over 60 columns reach it), `invalid_number` or
  `invalid_date` for a value the column cannot read or hold, `page_out_of_range` for an OpenSearch page beyond the
  result window, and `invalid_filter` for any other (an unknown column, a value that is not a boolean, a condition the
  column's type does not take, a regex search). A `page_out_of_range` answer carries the real `recordsTotal` and
  `recordsFiltered`: the library runs the counts first (one or two count calls, never a page search), and a start at or
  past the last row answers an empty page with both counts instead. When a request holds several, a control character, a
  value that is too long, text of too many words and a structure over the bounds are answered first (the first the
  request holds in reading order: the global search, then the column searches, then the SearchBuilder tree), and a
  request of too many conditions after them, then a number or date mistake, then any other, and the page last. The list
  of codes is closed: a code is added only in a release that says so, and a client that reads a code it does not know
  treats it as a refusal of the request and shows `error`. Every other failure raises, so the app decides how to report
  it; `DTResponse.query_failed(draw)` gives the usual shape.
- **A request the library cannot read is the integration's mistake, not the user's.** `parse_request` raises
  `MalformedRequest`, whose message names the request parameter and never a key or value, and whose `draw` is the
  request's own when that could be read. The FastAPI dependency answers it with 422 and
  `{"detail": {"message": ..., "draw": ...}}`.
- **Requests are bounded.** Page lengths are capped (1000 by default; "All" is refused), `start` goes up to
  2^53-1 and `draw` from 0 to 2^31-1, and LIKE and wildcard characters in search values are escaped. A request
  holds at most 256 values in one list and 512 in all its lists, at most 64 column searches, and a SearchBuilder tree
  of at most 8 groups deep and 64 criteria; one beyond them is answered with `invalid_filter`. One budget of 960
  conditions per request, the same on every backend, keeps every request within every supported database's limits (a
  request over it is answered with `request_too_large` before anything is queried); a `base_filter` and expression
  columns add to what the database receives and are not counted, see `docs/databases.md` for what that leaves. If you
  need more, please report it.

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run basedpyright
```
