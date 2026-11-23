# What the server decides

The library does not change how a database compares values. Where a database's own rule changes what a user sees
(trailing spaces on SQL Server and under a MySQL PAD SPACE collation, SQLite's inexact decimals), and where the
library itself has a limit on one engine, [databases.md](databases.md) says so, engine by engine.

## Columns and ordering

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

## Types and matching

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
  Some values fold ASCII letters only on every OpenSearch version: see "On every version" in
  [the OpenSearch notes](databases.md#on-every-version). The library asks for no change to a schema or mapping to do more.
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
- **Some columns take no part in the global search.** A boolean column never does, on any backend. A number or a date
  column does on SQL, as text (a number as its canonical text, a date as `YYYY-MM-DD`: "7" finds 17), and does not on
  OpenSearch.

## Errors

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

The codes, in the order of precedence above:

| Code | When | What the user does |
| --- | --- | --- |
| `invalid_character` | The search text holds a control character, such as a tab. | "Delete its text and type it again." |
| `value_too_long` | A value the client sends holds more than 255 characters. | "Shorten it to at most 255." |
| `too_many_words` | Search text holds more than 16 words. | "Search for at most 16 words." |
| `request_too_large` | The request holds more than 960 conditions. | "Search for fewer words, search fewer columns, or select fewer values." |
| `invalid_number` | A value a number column cannot read or hold. | "written like 42 or -3.5." |
| `invalid_date` | A value a date column cannot read or hold. | "written as YYYY-MM-DD, like 2024-03-05." |
| `invalid_filter` | Any other mistake: an unknown column, a value that is not a boolean, a condition the column's type does not take, a regex search. | The `error` text says which and how to fix it. |
| `page_out_of_range` | An OpenSearch page beyond the result window that the last-page read does not reach. | "Go back to an earlier page, or narrow the search so fewer rows match." |

The "what the user does" column quotes the sentence of the `error` text that tells the user what to do; the text
before it names the condition.

## Requests

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

## OpenSearch

**The unique key.** `unique_key` names a declared column (a tuple of names for a key of several) whose values are
unique for every document. Every order ends with it, in the direction of the first sort entry, and a request without
an order comes in its ascending order, so pages are stable. Declare it `IndexColumn(..., text='keyword')` or as a
number. A key is a declared column, so its value is sent in every row; `searchable=False` and `orderable=False` keep it
out of the client's controls. A table without a key raises `SchemaError`.

**The document id.** An index whose documents hold no unique field needs one: copy the id into a `keyword` field at
ingest, which is OpenSearch's own advice, and name that column as the key. The document's own `_id` cannot be the key,
because OpenSearch deprecates sorting on it (`docs/databases.md`); naming it raises `SchemaError`. A declared
`Column('_id')` shows the id (it reads the hit's id, and fills `DT_RowId` through `RowHooks`) but cannot be searched or
sorted: `searchable` is ignored on it, and `orderable=True` raises `SchemaError`.

**Paging past the window.** OpenSearch reads at most `max_result_window` rows (10,000 by default; give the table the
index's own) from the start of a search. The first pages are read as they come, and so are the last: a page that ends
within that many rows of the last row is read by reversing the sort (every entry, the unique key included) and is shown
in order, so "last page" works on a result of any size. A page in the middle of a larger result is refused with
`page_out_of_range` and the real counts, and the library never changes the cluster's setting. A last page is read after
a count, so it can drift if the index changes in between, as offset paging always can (no point in time is held). A
field of several values sorts by its least value ascending and its greatest descending (`docs/databases.md`); a reversed
page keeps the ordinary page's rows all the same.

**Analysed text.** On an analysed column a word is a phrase of its tokens, the nearest to SQL's substring: `foo-bar`
finds `foo bar` but not `foo` alone. Analysed text holds no spaces, so a search for a space finds nothing on an
analysed-only column.

**A column of analysed text only.** An OpenSearch column declared `text='text'` answers the word search (a word is an
analysed match: `red` does not find `reddish`), `contains` and `!contains` (the phrase of the words), `null` and
`!null`, and a categorical filter or `multi_values` list of only empty entries, which means empty. A categorical
filter, a `multi_values` list and the conditions `=`, `!=`, `starts`, `ends` and their negations are refused with
`invalid_filter`; SQL answers every condition. A column with an exact match to make needs a keyword field: declare it
`text='text+keyword'` (or `'keyword'`). A `search_value` hook on a column of analysed text only raises `SchemaError`,
since a hook compares by equality.

**A stored empty string.** "Empty" is a missing field on OpenSearch, so a stored `""` is not empty there; on SQL a
column that stores text is empty when it holds NULL or `''`.

## SQL

SQL takes the same `unique_key`, which replaces the primary key when declared. Without it, a source must have a primary
key, and a subquery must select every primary-key column of each table it reads (in a join too, and in a `WHERE` or a
scalar subquery); otherwise it raises `SchemaError`. A `UNION` is not checked: declare `unique_key` on a subquery over
one.
