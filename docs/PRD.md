# datatables-backend: product requirements

- **Product:** datatables-backend, a read-only Python library that answers DataTables server-side requests from SQL
  (SQLAlchemy 2, async and sync) and OpenSearch, with one request model, one column declaration and one set of
  safety rules for every source.
- **Status:** draft 2, approved by the owner on 2026-11-19, revision 39 of 2026-11-22 (revision 21 was revision 20
  with the owner's answers to section 8 folded in, all of section 8 answered; revision 22 holds the changes he
  approved since: PRD-E2, PRD-E3, PRD-E4, PRD-F15, PRD-A11, a clause of PRD-F43, and PRD-F36's wording, which
  follows PRD-F15's code; revision 23 adds PRD-D21 and changes PRD-F9 for columns the library does not fold, for
  the SQL column types it folds and for OpenSearch's ASCII-only fold, with lines in section 5; revision 24 changes
  PRD-F9 for OpenSearch, which folds letters outside ASCII from 3.1 on, and adds PRD-Q14 for the supported
  OpenSearch servers, with lines in section 5; revision 25 is revision 24 in a new layout, one section per
  requirement, no requirement changed; revision 26, approved by the owner on 2026-11-21, states how a day and a
  number are written, in PRD-F21, PRD-F22 and PRD-F25; revision 27, approved by the owner on 2026-11-22, changes
  the wording of PRD-O8, PRD-E13, PRD-P8 and PRD-P9 and the reason of PRD-O2; revision 28, approved by the owner on
  2026-11-22, changes PRD-F6, PRD-F17, PRD-F21, PRD-F25, PRD-F26, PRD-F33 and PRD-F44 and the reason of PRD-O8, with
  lines in section 5 and the milestones in section 6; revision 29, approved by the owner on 2026-11-22 after the
  review of the same change, changes PRD-F20, PRD-F25, PRD-D19, PRD-E3 and PRD-E4, the reasons of PRD-F25 and
  PRD-F26, lines in section 5 and the sentence on milestones in section 6; revision 30, approved by the owner on
  2026-11-22, changes who may change a requirement, in the last sentences of this block; revision 31, approved by
  the owner on 2026-11-22, changes the first sentence of PRD-C15 (no app-supplied key); revision 32, approved by the
  owner on 2026-11-22, changes the first sentences of PRD-F38 and PRD-C17; revision 33 adds a clause to PRD-F11 (a
  word on analysed text matches as a phrase of its tokens), with its line in section 5; revision 34 gives the bounds
  of PRD-F14 their own codes, `value_too_long` and `too_many_words`, as the owner decided on 2026-11-22, in PRD-F14,
  PRD-E2, PRD-E3 and PRD-A11, extends the 255-code-point bound to every value the client sends, and drops PRD-F25's
  rule of 308 digits, which that bound makes unreachable, with lines in section 5; revision 35 gives PRD-C17 its
  budget of 960 conditions, the owner's sentence of 2026-11-22 and the new code `request_too_large`, in PRD-C17,
  PRD-E2, PRD-E3 and PRD-A11, with a pointer in PRD-F38 and a line in section 5; revision 36 adds a clause to PRD-D1
  (a table declares a column, and no two share a name); revision 37 adds a clause to PRD-D12 (text whose every token
  adds nothing under PRD-F6 adds nothing there either, and text with a word, a negated one included, is compared
  whole); revision 38 changes PRD-F30 (an unfinished SearchBuilder criterion adds nothing instead of being refused) and
  a clause of PRD-P6, with a line in section 5; PRD-F36's reason follows; revision 39 adds clauses to PRD-D11 (the key
  is `unique_key`, a declared column or a tuple of them, which replaces the primary key when declared; an OpenSearch key
  is sortable; a SQL subquery selects every primary-key column of each table it reads) and to its reason). It replaces
  draft 1 (R1 to R9); section 10 maps every draft-1 item to its place here. The first sentence and the priority of a
  requirement, a goal or non-goal, a public name and an error code change only by the owner. A requirement's further
  clauses, its reason and section 5 change in PR review by the reviewer, and the PR body lists each such change for the
  owner to read. New facts about a database engine go to `docs/databases.md`, not into this document.
- **Order of work:** PRD (approved) -> specs with numbered statements -> behavioural tests from the outside in -> code
  by TDD. Every spec statement traces to a requirement here, and every *must* has statements.
- **Fact labels:** READ (a document or source read on 2026-11-19), MEASURED (a command run on 2026-11-19), INFERRED
  (reasoning; to be checked before relying on it). A requirement without a label states a decision, not a fact.

## 1. The problem and who has it

[DataTables](https://datatables.net) tables that page, sort and filter on the server send a fixed request: paging,
ordering, a global search, per-column searches and, with the SearchBuilder extension, a tree of AND/OR criteria. Every
Python backend has to turn that request into a query safely. Getting it wrong leads to unbounded result sets, wildcard
injection, errors that echo user input, and sorts or filters on columns the page never exposed.

What exists (READ, researched 2026-11-19):

- `datatables_server` (SpryMedia, the DataTables authors) covers SQL through SQLAlchemy 2 Core with a sync
  `Connection`, including Editor (CRUD), SearchBuilder, SearchPanes and ColumnControl. It has no native async API and
  no search-engine backend.
- No Python library serves DataTables from OpenSearch or Elasticsearch.
- The older SQLAlchemy libraries (`sqlalchemy-datatables`, `datatables`, `sqlalchemy2-datatables`) are unmaintained.

## 2. Users

- **SvelteDT, the reference frontend** ([hniedner/sveltedt](https://github.com/hniedner/sveltedt), public). Its
  connection helper (its PRD-B20) speaks the DataTables 1.10-and-later protocol in the bracketed form, the dotted form
  or a JSON body. Its local data source (its PRD-B21) defines the search and order semantics a user sees when the
  same table runs in the browser; this library gives the same answers on the server except where section 5 says.
- **Any DataTables 2.x client.** The library keeps serving DataTables' own request, with no SvelteDT-only parameter
  required.
- **Developers of Python web apps** (FastAPI, Starlette, Flask, Django with SQLAlchemy) who show database or
  search-index data in a table.

## 3. Goals and non-goals

**Goals**

1. One request model and one column declaration for every source; each source is a small backend: SQL through
   SQLAlchemy 2 (async and sync), OpenSearch (async and sync clients), and later Elasticsearch behind the same
   contract.
2. A search, filter and order mean the same on every backend, and the same as SvelteDT's local data source, except
   where the database decides (section 5).
3. Every user mistake is found before the page is queried and answered with a stable code and a fixed text that never
   quotes the input.

**Non-goals**

- Editing (DataTables Editor). `datatables_server` covers it for SQL.
- Rendering HTML or shipping JavaScript.
- Exports, audit logging, authentication and authorisation. These belong to the app. The library gives the app a
  request whose paging it can replace.
- Regular-expression search. A client regex is refused, except the categorical forms of PRD-F16.
- Natural or ICU text ordering in the library ("item2" before "item10", locale-aware ties). Text order is the
  database's collation (PRD-O6): an ordering the library imposed would need per-database collations or extensions,
  which it does not create, and would not be portable across SQLite, PostgreSQL, SQL Server, MySQL and OpenSearch.
- Creating database extensions or collations (e.g. `unaccent`, ICU collations). An app that wants them declares a
  column as an expression (PRD-D10).
- DataTables' fixed and grouped searches, SearchPanes selections and ColumnControl filters. They are refused, not
  answered (PRD-P5); SearchPanes and ColumnControl are candidates for later (PRD-L2).
- SearchBuilder's array conditions (on columns of array values). Only string, number, date and boolean conditions are
  answered (PRD-F27).

## 4. Requirements

Priority: **must** (in 1.0), **should** (in 1.0 unless the owner moves it), **later** (after 1.0).

Ids: `PRD-P` parsing, `PRD-D` column declaration (the server's schema, which every other group reads), `PRD-F`
filtering and search, `PRD-O` ordering, `PRD-C` counts and paging, `PRD-E` errors, `PRD-R` rows and the response,
`PRD-A` API, `PRD-Q` packaging and quality, `PRD-L` later. Each id holds one behaviour.

A **request parameter** is one of `draw`, `start`, `length`, `columns`, `order`, `search`, `searchBuilder`,
`searchPanes` and `searchPanes_null`. A flat key's **first segment** is its text before the first `.`, `[` or `]`.

### 4.1 Parsing (P)

#### PRD-P1

A request is read from a decoded JSON object (nested), or from flat form or query parameters: a mapping, a multi-dict
(anything with `getlist`, every value of a repeated key read), or key-value pairs with repeated `key[]` entries.

- **Reason:** DataTables posts JSON when `ajax.submitAs` is `json` (READ, DataTables 3.1.5 reference) and form or query
  parameters by default; Starlette, Werkzeug and Django hand form data over as multi-dicts.
- **Priority:** must

#### PRD-P2

Flat keys are read in bracket notation (`columns[0][search][value]`), dotted notation (`columns[0].search.value` or
`columns.0.search.value`) or a mix, and every form reads to the same request.

- **Reason:** DataTables sends brackets by default; spring-data-jpa-datatables reads dotted names (READ, its README);
  SvelteDT's helper sends either (its PRD-B20).
- **Priority:** must

#### PRD-P3

A top-level key that is not a request parameter is ignored, in every form: in a JSON body, and as a flat key whose first
segment is not a request parameter (DataTables' cache-buster `_`, SearchPanes' `searchPanes_options` and
`searchPanesLast`, an app's own `ajax.data` keys).

- **Reason:** DataTables appends `_` to every request under its default `cache: false` (READ, its 3.1.5 `queryParams`);
  SearchPanes sends its options on every request (READ, SearchPanes 3.0.1); apps add their own keys.
- **Priority:** must

#### PRD-P4

An unknown field under a request parameter is ignored, except as PRD-P5 says.

- **Reason:** Clients add fields over time; an unknown field asks for nothing the library answers.
- **Priority:** must

#### PRD-P5

A filter the library does not answer raises `MalformedRequest` (PRD-E6) unless it is empty or idle: a non-empty
`search[fixed]`, `search[groups]`, `search[groupsFixed]` or `columns[i][search][fixed]`; a non-empty pane in
`searchPanes` or `searchPanes_null` (SearchPanes sends an empty pane on every request); a non-empty
`columns[i][columnControl][list]`; a `columns[i][columnControl][search]` with a value or the `empty` or `notEmpty`
logic.

- **Reason:** DataTables omits empty arrays (READ, its 3.1.5 serializer), and ColumnControl sends an idle search on
  every request, counting it active only with a value or those logics (READ, ColumnControl 2.1.3); dropping an active
  filter would answer fewer filters than asked, with no sign.
- **Priority:** must

#### PRD-P6

A request whose structure cannot be read (a top-level value that is not an object, a request parameter, or a field under
one, of the wrong type (a `null` `data` excepted, PRD-P21), a flat key that is not text, an object value among flat
keys, keys that collide or leave a gap in an index, an `order` index outside the request's columns (PRD-O1), a
SearchBuilder condition outside the 16 the protocol model knows, such as its array condition `without` (a missing or
empty condition, or the text `undefined`, is none: the criterion is unfinished, PRD-F30)) raises `MalformedRequest`.

- **Reason:** A structural error is an integration bug, not something the user can change.
- **Priority:** must

#### PRD-P7

A flat key whose first segment is a request parameter must be `[part]` and `.part` segments, a bracket segment holding
no `.`, an index being `0` or ASCII digits not starting with 0, at most 9 digits, and `[]` appending a value, never a
field; any other shape raises `MalformedRequest`. The 9-digit rule bounds the indexes in keys; numeric values are
bounded by PRD-P10.

- **Reason:** A key the parser cannot read must not be half-read; Unicode, leading-zero and over-long indexes once
  crashed the parser (MEASURED).
- **Priority:** must

#### PRD-P8

A paging number or an order index that is not an integer raises `MalformedRequest`; a JSON `true` or `false` is not an
integer.

- **Reason:** Paging must be exact; a boolean read as 1 or 0 would page or sort as nobody asked.
- **Priority:** must

#### PRD-P9

A negative `start` or `draw` raises `MalformedRequest`; a `draw` of 0 is accepted.

- **Reason:** No page lies before the first, and no client counts its draws below zero (SvelteDT starts at 1, READ in
  its specs; DataTables INFERRED).
- **Priority:** must

#### PRD-P10

The numeric values are bounded: `start` is accepted up to 2^53-1, and `draw` and `order[i][column]` up to 2^31-1; a
larger value raises `MalformedRequest`. The indexes in flat keys are bounded by PRD-P7.

- **Reason:** SvelteDT pages up to 2^53-1, the largest integer JavaScript holds exactly (its PRD-B9); the other numbers
  never need more.
- **Priority:** must

#### PRD-P11

Text in a request parameter that JSON cannot encode (a lone surrogate) raises `MalformedRequest`.

- **Reason:** No database driver can encode it.
- **Priority:** must

#### PRD-P12

A `MalformedRequest` message names the request parameter and never the client's key or value; an `order` index outside
the request's columns (PRD-O1) names `order`.

- **Reason:** Client text in messages invites injection into logs and pages.
- **Priority:** must

#### PRD-P13

`length` is between 1 and a configured maximum (default 1000); any other value, "All" (`-1`) included, raises
`MalformedRequest`.

- **Reason:** An unbounded page is the first way a table query goes wrong; SvelteDT pages at most 1000 rows (its
  `docs/departures.md`, DEP-13).
- **Priority:** must

#### PRD-P14

The strings `"true"` and `"false"` read as booleans.

- **Reason:** Form and query encodings carry only strings.
- **Priority:** must

#### PRD-P15

A column's `name` falls back to its `data`.

- **Reason:** DataTables sends an empty `name` unless the page sets one.
- **Priority:** must

#### PRD-P16

`order[i][column]` indexes the request's own `columns`; an `order[i][name]` beside it is accepted and the index decides.

- **Reason:** DataTables 2.x and SvelteDT send both (its PRD-B20); one rule avoids two answers to one request.
- **Priority:** must

#### PRD-P17

The request is validated into typed models (`DTParams`, `DTColumn`, `DTOrder`, `Search`, the recursive SearchBuilder
tree).

- **Reason:** Nothing downstream sees raw input.
- **Priority:** must

#### PRD-P18

A column's `data` may be an object (DataTables' orthogonal data); the column's identity is then its `name`. An object
`data` with an empty `name` is a nameless column, handled as PRD-P21 says.

- **Reason:** DataTables sends an object `data` for orthogonal columns; such a column has no other name the server can
  match, and a column nothing filters or sorts needs none.
- **Priority:** must

#### PRD-P19

A request holding no request parameter at all (an empty request, or DataTables 1.9's `sEcho`/`sSearch`/`iDisplayStart`
form) raises `MalformedRequest`; it is never answered as an empty request.

- **Reason:** An unread request answered unfiltered with `draw` 0 is dropped silently by DataTables.
- **Priority:** must

#### PRD-P20

An integer `columns[i][data]` is read as its decimal text.

- **Reason:** DataTables defaults `data` to the column index, an integer.
- **Priority:** must

#### PRD-P21

A column's `data` of JSON `null` reads as empty text. A nameless column has an empty `name` and a `data` that is empty,
`null`, an object, or the text `function` (how DataTables sends a function `data`) when no declared column has the name
`function`; if one does, the column is that one, as PRD-P15 and PRD-D6 say. The global search skips a nameless column,
and an `order` entry on one is skipped (this wins over PRD-O1); a column search on it with a value or a non-empty
`multi_values` list, or a SearchBuilder criterion with an empty `origData`, is refused with `invalid_filter` (PRD-E1),
as on an undeclared name (PRD-D6, PRD-F41).

- **Reason:** DataTables sends `data: null` for a column that only renders (JSON `null`; a form request leaves the value
  empty), and sends its default order `[[0, 'asc']]` whatever `orderable` says (READ, DataTables 3.1.5 `dataTables.mjs`:
  a function `data` sent as the text `function` at :5619-5622 and :5628, which would otherwise be an undeclared name
  refusing every global search; the default at :12404, `sortFlatten` at :4794-4836), so a table whose first column only
  renders would be refused on its first request. DataTables' own client sorts such a column by its rendered output or
  its `sort` key (READ, `dataGet` at :7994), which the server does not have: the server holds no value to sort such a
  column by, so the entry is dropped and the remaining order and PRD-O3's tie-breaker decide. A search on a nameless
  column names nothing the server holds, which the user can change.
- **Priority:** must

### 4.2 Column declaration (D)

#### PRD-D1

The server declares the table's columns, each with the name the client uses. A table declares at least one column, and
no two share a name; constructing one otherwise raises `SchemaError`.

- **Reason:** The server, not the client, decides what exists; the name keys the request and each response row.
- **Priority:** must

#### PRD-D2

A column may name the backend field it reads when that differs from the client's name.

- **Reason:** Client names need not reveal the schema.
- **Priority:** must

#### PRD-D3

A column declares a value type: string, number, date or boolean.

- **Reason:** The type decides how a value is matched (PRD-D7).
- **Priority:** must

#### PRD-D4

A column declares whether it is searchable.

- **Reason:** Some columns must not be searched.
- **Priority:** must

#### PRD-D5

A column declares whether it is orderable.

- **Reason:** Some columns must not be sorted.
- **Priority:** must

#### PRD-D6

Any name the request uses to search, filter or order on (column searches, the global search, `order`, every
SearchBuilder `origData`) must be a declared name; otherwise the request is refused (PRD-E1). The exceptions: the global
search leaves out every undeclared column, and a column search on one counts only with a value or a non-empty
`multi_values` list (PRD-F41); an order entry the request marks unorderable on an undeclared name is skipped (PRD-O1);
the global search and `order` skip a nameless column (PRD-P21).

- **Reason:** A backend field never becomes reachable just because the client names it.
- **Priority:** must

#### PRD-D7

The declared type decides how a criterion is built; the client's SearchBuilder `type` is ignored.

- **Reason:** A wrong client type cannot change how a value is matched.
- **Priority:** must

#### PRD-D8

SQL infers the type from the column when none is declared (a SQLAlchemy `Boolean`, SQL Server `BIT` included, is
boolean; a MySQL `TINYINT(1)` read by reflection is a number, so a boolean there is declared boolean); OpenSearch
defaults to string, and a field with a boolean mapping is declared boolean, since the mapping is not read.

- **Reason:** Most SQL columns carry their type; an index mapping is not read at request time. SQLAlchemy's `mssql.BIT`
  is a subclass of `Boolean`, and `mysql.TINYINT` one of `Integer`, not of `Boolean` (MEASURED, SQLAlchemy 2.1.9); that
  reflection gives a `TINYINT(1)` column `TINYINT` is INFERRED.
- **Priority:** must

#### PRD-D9

A column may declare a hook that transforms a search value before it reaches the query (e.g. to encrypt or encode it as
stored).

- **Reason:** Some columns are stored transformed; the user still types the plain value.
- **Priority:** must

#### PRD-D10

A SQL column may be declared as an expression over the source (e.g. `unaccent(name)` or a `COLLATE` clause), searched
and ordered like any column.

- **Reason:** Accent folding and collation follow the database (PRD-F10, PRD-O6); an app that wants more uses its
  database's means.
- **Priority:** should

#### PRD-D11

A table whose source has no primary key (every OpenSearch index; a SQL join or subquery without one) declares a unique
key; constructing it without one raises `SchemaError`. The key is `unique_key`, the name of a declared column or a tuple
of names for a composite key, and it replaces the primary key as the tie-breaker when declared. An OpenSearch key must
be sortable (not analysed text only). On SQL, without `unique_key` a source must have a primary key, and a subquery
must select every primary-key column of each table it reads (in a join too, and in a `WHERE` or a scalar subquery);
otherwise constructing it raises `SchemaError`. A `UNION` is not checked.

- **Reason:** The tie-breaker of PRD-O3 must be unique; the first column of a join is not. A declared column is read
  with the type and sort field the table already knows; a bare field name would need the mapping read.
- **Priority:** must

#### PRD-D12

In the global search, a column with a search-value hook (PRD-D9) takes no part in per-word matching: a row matches when
its other columns satisfy the smart search, or when the hooked column equals the whole search text, trimmed and
transformed once. `!word` ignores hooked columns, in that a negated word never excludes a row by one, and blank text
adds nothing. Text whose every token adds nothing under PRD-F6 adds nothing here either; text with a word, a negated one
included, is compared whole. When every searchable column is hooked, the equality alone decides, and PRD-F13 does not
apply. Trimmed means leading and trailing Unicode White_Space removed; PRD-F7's line-break rewriting does not apply to
the whole text, which is an exact value; PRD-F14's 255-code-point bound applies to the raw text, before trimming; its
16-word cap is lifted only when every searchable column is hooked, and otherwise refuses the whole request.

- **Reason:** A transformed (e.g. encrypted) value cannot be matched word by word or as a substring; the whole text is
  what a user types to find one value. Owner's decision of 2026-11-19.
- **Priority:** must

#### PRD-D13

An OpenSearch string column's kind is `keyword`, `text+keyword` (the default, the mapping OpenSearch's dynamic mapping
gives a string, with the keyword field `<field>.keyword`) or text-only; the keyword field is used for sorting and exact
matches.

- **Reason:** The backend never reads the index mapping at request time (PRD-D8, PRD-A3), so the sort (PRD-O5, PRD-O7)
  and exact-match (PRD-F16, PRD-F20, PRD-F27, PRD-D19) rules need the field kinds declared.
- **Priority:** must

#### PRD-D14

A column with a search-value hook must be of string type; declaring a hook on another type raises `SchemaError`.

- **Reason:** The hook transforms text the user types.
- **Priority:** must

#### PRD-D15

A categorical filter or `multi_values` list on a hooked column transforms each value and matches by equality.

- **Reason:** Each value is stored transformed.
- **Priority:** must

#### PRD-D16

An empty alternative on a hooked column is not transformed; it means empty, as PRD-F26 defines it.

- **Reason:** "Empty" is a property of the stored value, not text to transform.
- **Priority:** must

#### PRD-D17

A column search on a hooked column compares the whole search text, trimmed and transformed once, by equality, with no
smart-search grammar; trimming and PRD-F7 apply as PRD-D12 says, and so does PRD-F14's 255-code-point bound on the raw
text, with no word cap on the hooked column's own column search.

- **Reason:** As PRD-D12: a transformed value matches only as a whole. Owner's decision of 2026-11-19.
- **Priority:** must

#### PRD-D18

On a hooked column, SearchBuilder answers `=`, `!=`, `null` and `!null`; any other condition is refused with
`invalid_filter`.

- **Reason:** `null` and `!null` need no value, and equality compares the transformed whole; a range or substring of a
  transformed value means nothing. Owner's decision of 2026-11-19.
- **Priority:** must

#### PRD-D19

On a text-only OpenSearch string column (PRD-D13), a categorical filter, a `multi_values` list and the SearchBuilder
conditions `=`, `!=`, `starts`, `!starts`, `ends`, `!ends`, `contains` and `!contains` are refused with
`invalid_filter`, except a categorical filter or `multi_values` list of only empty entries, which means empty (PRD-F20)
and is answered; beyond that only the analysed smart search (global and column) and `null`/`!null` are answered. A
hooked column cannot be text-only (PRD-D20).

- **Reason:** Exact, prefix, suffix and substring matches need the unanalysed form, which a text-only column does not
  have; answering them on analysed tokens would return different rows than asked.
- **Priority:** must

#### PRD-D20

A hooked OpenSearch column (PRD-D9) must have a keyword field (`keyword` or `text+keyword`, PRD-D13); declaring a hook
on a text-only column raises `SchemaError` at construction.

- **Reason:** A hooked column compares by equality (PRD-D12, PRD-D15, PRD-D17, PRD-D18), which a text-only column cannot
  answer (PRD-D19); refusing the declaration leaves no request without a defined answer.
- **Priority:** must

#### PRD-D21

A string column can be declared with `fold_case=False`. Its exact matches (`=`, `!=`, categorical filters,
`multi_values`) are then compared as sent: a plain comparison in SQL and a plain `term`/`terms` query on OpenSearch, so
the result follows the column's collation or the field's mapping. The default is `fold_case=True` (PRD-F9). Searches are
not affected.

- **Reason:** A folded comparison cannot use a plain index on the column; the library must not make a consumer change
  their database schema to keep an exact filter fast. Owner's decision of 2026-11-21.
- **Priority:** must

### 4.3 Filtering and search (F)

#### PRD-F1

The global search is a smart search in which the row is the unit: every word must be found in at least one of the row's
searchable columns, and different words may match different columns. Example: "red 7" finds a row whose colour is red
and whose price is 7.

- **Reason:** DataTables' smart search and SvelteDT's local data source behave so (its PRD-B21); a whole-text match in
  one column misses rows a user expects.
- **Priority:** must

#### PRD-F2

A column search is a smart search within that one column: every word must match in it.

- **Reason:** One syntax for both search boxes, as in SvelteDT.
- **Priority:** must

#### PRD-F3

Words are separated by spaces. A phrase (PRD-F4) is read only where a word begins (at the start, after a space, or right
after a phrase), and it ends at its closing quote: `"a" "b"x` gives `a`, `b` and `x`; `x"b"y` gives `xby`; `x"a b"`
gives `xa` and `b`; `"a""b"` gives `a` and `b`. Words match in any order, and every word must match.

- **Reason:** DataTables' smart search (READ, its 3.1.5 source: the split pattern at :5924; the examples MEASURED in
  node).
- **Priority:** must

#### PRD-F4

A phrase in straight double quotes, or in curly quotes (U+201C ... U+201D), is one word, which must sit inside one
column. A phrase opened by one kind of quote and closed by the other is one word too, its space kept; in a positive word
its straight quote is removed and its curly quote stays as text (`"a b”` searches `a b”`, `“a b"` searches `“a b`), and
a negated mixed pair keeps its straight quote. Any other straight double quote in a positive word is removed (`5"`
searches `5`; an unclosed `"abc` searches `abc`); a negated word keeps it (`!5"` excludes `5"`). An unclosed curly quote
stays as text.

- **Reason:** DataTables' smart search exactly (READ, DataTables 3.1.5 `dataTables.mjs`: the split pattern
  `!?["\u201C][^"\u201D]+["\u201D]|[^ ]+` at :5924, which also takes a mixed pair; the quote stripping at :5936-5943,
  which needs a matching pair; a negated word recorded at :5948 before `word.replace(/"/g, '')` at :5953 runs on
  positive words; the negated mixed pair MEASURED in node).
- **Priority:** must

#### PRD-F5

`!word` excludes: in the global search, no searchable column of the row contains the word; in a column search, the
column does not contain it. A NULL column contains no word, so it passes `!word` on every backend.

- **Reason:** DataTables' `!`; a SQL `NOT (... LIKE ...)` drops NULL rows unless written for them (MEASURED on SQLite).
- **Priority:** must

#### PRD-F6

`!"phrase"` excludes the phrase; a token that reduces to the empty word (a lone `"`, `"""`, `!""`, `""`, a bare `!`, or
a `!` word that PRD-F35 drops) adds nothing, and so does search text of spaces only: U+0020 and what PRD-F7 counts as
spaces (LF, CR, U+2028, U+2029), in the global search and in a column search on a column of any type, on every backend.
A tab stays a control character (PRD-F15). With `regex: true` a column search of spaces is a categorical value
(PRD-F16): the exact value on a string column, empty on any other.

- **Reason:** DataTables reduces them to an empty word (READ, its 3.1.5 source); a user who types a space must not get
  no rows.
- **Priority:** must

#### PRD-F7

In search text (the global and column searches), LF, CR, U+2028 and U+2029 count as spaces; exact values (categorical,
`multi_values`, SearchBuilder) are never rewritten.

- **Reason:** SvelteDT's local source does so (its PRD-B21, difference 4); an exact value must stay the value sent.
- **Priority:** must

#### PRD-F8

No other syntax is read: `OR`, `NOT`, `-` and wildcard characters are ordinary text on every backend, OpenSearch
analysed text and SearchBuilder "contains" included.

- **Reason:** One syntax everywhere; a second syntax on one backend conflicts with `!` and surprises users.
- **Priority:** must

#### PRD-F9

Matching is case-insensitive on every backend, for searches (global, column, `contains`, `starts`, `ends` and their
negations) and exact matches on string columns (`=`, `!=`, categorical, `multi_values`) alike (boolean values follow
PRD-F43; numbers and dates have no case), also under a case-sensitive collation; SQLite folds ASCII letters only; for a
substring and an exact match OpenSearch folds ASCII letters only on 3.0 and 2.19 and letters outside ASCII too from 3.1
on (PRD-Q14), where a value without a character outside ASCII that has a case (a letter, a circled letter, a Roman
numeral), and a value longer than 255 code points, still fold ASCII letters only; on no version does a substring or an
exact match with `i` find a dotted capital İ; a whole word on an analysed column (`text` or `text+keyword`, PRD-D13)
folds as the field's analyser does; on a column with a search-value hook (PRD-D9, PRD-D17, PRD-D18), and in the exact
matches of a column declared with `fold_case=False` (PRD-D21), whose searches fold as on any column, the library adds no
case folding: the comparison follows the column's collation or the field's mapping. In SQL the library folds a column
whose own SQL type is a string type or an `Enum`; a column with a type of its own (a `TypeDecorator`, SQLAlchemy's
`Uuid`) and an expression declared without a type are compared and searched as sent.

- **Reason:** Case-insensitivity is portable, and the spec picks each database's means; SQLite's LIKE folds ASCII only
  (MEASURED: `'ÄPFEL' LIKE '%äpfel%'` is false). On a `term` and a `wildcard` query OpenSearch's `case_insensitive`
  folds ASCII letters only, on every version measured; on a `regexp` query it folds letters outside ASCII from 3.1.0 on,
  and not on 3.0.0 or 2.19.7 (MEASURED 2026-11-21 against a stored `ÄPFEL` on 2.19.7 and 3.0.0 to 3.10.0, CI run
  44942700768). So a value of at most 255 code points with a cased character outside ASCII is sent as a case-insensitive
  `regexp`, and every other value as before. The limits of that query form are in section 5. No normalizer is asked of
  the consumer's mapping (owner's decisions of 2026-11-21). The measurements are in
  `docs/decisions/001-opensearch-case-folding-beyond-ascii.md`. Exact matches on string columns are implemented as
  `lower()` on both sides in SQL and as `case_insensitive` `term` or `regexp` queries on OpenSearch, a `terms` list
  becoming a `bool` of them (INFERRED). Owner's decision of 2026-11-19 for exact matches. In SQL an `Enum` column is
  cast to text for the fold and for a search, on every dialect; a type of its own converts and compares values in its
  own way, which a fold would bypass, and an expression is folded once it is given a string type. Owner's decision of
  2026-11-21.
- **Priority:** must

#### PRD-F10

The library applies no accent folding and no dotted/dotless-i folding of its own; whether such letters match is the
column's database collation.

- **Reason:** Not portable without extensions the library does not create (non-goals, PRD-D10).
- **Priority:** must

#### PRD-F11

A word matches as a substring, except on a text-only OpenSearch column, where it is an analysed match (`red` does not
find `reddish`; PRD-D19); LIKE wildcards (`%`, `_`, `[`, `\`) and OpenSearch wildcard characters in it are escaped. On
analysed text a word, and a SearchBuilder `contains` value, matches as a phrase of its tokens, so `foo-bar` finds
`foo bar` but not `foo` alone.

- **Reason:** A user's `%` or `*` is text, never a pattern; wildcard injection is a known failure.
- **Priority:** must

#### PRD-F12

Column searches combine with AND, with each other and with the global search.

- **Reason:** DataTables' meaning.
- **Priority:** must

#### PRD-F13

A search over no usable column is refused (PRD-E1), never answered with every row.

- **Reason:** An empty OR silently matching everything hides a mistake.
- **Priority:** must

#### PRD-F14

Search text holds at most 255 code points and 16 words; longer text is refused with `value_too_long`, and text of more
words with `too_many_words`. Words are counted after PRD-F4's split: a quoted phrase is one word, and a token that
reduces to the empty word (a lone `"`, `"""`, `!""`, `""`, a bare `!`, or a `!` word that PRD-F35 drops) is not counted
and adds nothing. The 16-word cap is lifted only where PRD-D12 and PRD-D17 say. The 255-code-point bound is the bound of
every value the client sends, measured on the text as sent, before trimming and before a hook: a column search, each
entry of a `multi_values` list, each alternative of a categorical filter (PRD-F16), after unescaping and not the raw
pattern, and each value a SearchBuilder condition reads (PRD-F36), a number or a date as well. A value no condition
reads, a surplus value or that of `null`, is not measured. The 16-word cap applies to text that is split into words, not
to a list, a SearchBuilder value, or a value read as one number, date or boolean.

- **Reason:** Each word adds a condition per searchable column; SvelteDT's field allows 255 characters.
- **Priority:** must

#### PRD-F15

A value holding a Unicode control character (category Cc) other than LF and CR is refused with `invalid_character`
before any query runs: the global search, a column search, a SearchBuilder value its condition reads (PRD-F36), a
categorical value and a `multi_values` entry alike.

- **Reason:** A NUL truncated a LIKE pattern on SQLite and matched wrong rows (MEASURED); SvelteDT refuses control
  characters at the field.
- **Priority:** must

#### PRD-F16

A categorical filter is a column search with `regex: true` whose regex is a single value or literal alternatives: `abc`,
`a|b`, `^a$` or `^(a|b)$`, where no value holds an unescaped character of the set `. * + ? | ( ) [ ] { } \ $ ^` (`|` and
the anchors serve only as structure; `-` and `/` are accepted escaped or not; only escapes of PRD-F19's set are read, so
any other backslash escape such as `\d`, `\w` or `\n` is refused under PRD-F18); it matches the values exactly (an IN
list).

- **Reason:** DataTables' multi-select convention, as draft 1 accepted it; a single value sent as plain text would
  silently become a substring match ("red" finding "reddish").
- **Priority:** must

#### PRD-F17

An empty alternative in a categorical filter means empty, as PRD-F26 defines it, at any position: `^(|a)$`, `^(a|)$`,
`^(a||b)$` and `a|`. A filter that holds no value at all means empty alone: `^()$`, `^$` and a bare `|`.

- **Reason:** A frontend's "(empty)" option needs a value to send.
- **Priority:** must

#### PRD-F18

Any other regex, an unescaped metacharacter included, is refused.

- **Reason:** Regular-expression search is a non-goal.
- **Priority:** must

#### PRD-F19

The recommended form for a sender is `^(v1|v2)$` with each value escaped by DataTables' `util.escapeRegex` set:
`/ . * + ? | ( ) [ ] { } \ $ ^ -`.

- **Reason:** Escaped values carry any text unambiguously (READ, the set in DataTables 3.1.5's source).
- **Priority:** must

#### PRD-F20

A `multi_values` list in a column's search object is accepted as the same exact match; an empty entry means empty, as
PRD-F26 defines it. An entry of only white space is that exact value on a string column, and an empty entry on a
column of any other type; so is such an alternative of a categorical filter.

- **Reason:** An extension that needs no regex escaping, for clients that build the request themselves.
- **Priority:** must

#### PRD-F21

Dates are matched by day: `=`, `<`, `<=`, `>`, `>=` and `between` refer to whole days, for date and datetime columns
alike. For a timezone-aware column the day is its UTC day, on every backend (OpenSearch included). A timezone-aware
column is a SQL column whose type carries a time zone (e.g. `DateTime(timezone=True)`), or any OpenSearch date column,
which OpenSearch stores as a UTC instant. On SQLite and in a MySQL `DATETIME`, which store no offset, the stored time is
read as UTC, so the app must write UTC there: SQLAlchemy and PyMySQL drop an aware value's offset without converting it
(SQLite MEASURED; PyMySQL INFERRED). A MySQL `TIMESTAMP` is read through the session `time_zone`, so it gives the UTC
day only when the app sets that zone to `+00:00`. A non-empty categorical value or `multi_values` entry on a date column
is a day (`YYYY-MM-DD`) and matches as `=` does, on every backend; an unreadable one is `invalid_date`; an empty one,
or one of only white space, means empty, as PRD-F26 defines it (PRD-F17, PRD-F20). A day is written `YYYY-MM-DD` in
ASCII digits. A time of day may follow it after a space or a `T` (`HH:MM` or `HH:MM:SS`, the seconds with an optional
fraction): it must exist and is then ignored, since SearchBuilder sends
`YYYY-MM-DD HH:mm:ss` for a column of its `moment`, `luxon` or `datetime` type (READ in its source, not run in a
browser). White space around the value is ignored. Any other spelling is `invalid_date`, among them a digit outside
ASCII, `20240305`, a week date, a zone or offset after the time and any other text after the day.

- **Reason:** A user filters by calendar day, not by instant. The MySQL `TIMESTAMP` behaviour is INFERRED from the MySQL
  documentation, not measured here. One stated spelling is answered alike on every backend; an offset is refused rather
  than ignored, because the instant it names can fall on another UTC day. The reading and the comparison with the
  earlier behaviour are in docs/decisions/002-how-a-day-and-a-number-are-written.md.
- **Priority:** must

#### PRD-F22

SearchBuilder's "contains" on a date column, a criterion SearchBuilder itself does not send, and a column search on an
OpenSearch date column (other than a categorical filter or `multi_values` list, PRD-F21), take a year, a year-month or a
day (`YYYY`, `YYYY-MM`, `YYYY-MM-DD`) and mean that span, on every backend. For a timezone-aware column the span is in
UTC (PRD-F21). The year and the year-month are written in ASCII digits, and the day as PRD-F21 writes it, a time after
it included.

- **Reason:** A partial date names a span; substring matching on date text depends on the database's date format.
- **Priority:** must

#### PRD-F23

On SQL, a number or a date column takes part in the global and column searches as text (categorical filters and
`multi_values` lists on a number or date column excepted, PRD-F21, PRD-F33): a number as its canonical text, a date as
`YYYY-MM-DD`; "7" finds 17.

- **Reason:** As in SvelteDT's search text, which holds every column as text.
- **Priority:** must

#### PRD-F24

The maximum date (9999-12-31) as a bound is a user error, not a failed query.

- **Reason:** Its next day does not exist, and a user can change it.
- **Priority:** must

#### PRD-F25

A number is parsed and bounded: a non-finite or non-numeric value is refused, and so is a value outside the column's SQL
integer range. An unsigned MySQL integer column is bounded by its unsigned range. On SQL Server a number with more than
38 digits before the decimal point is out of range on a `Numeric` column, whatever precision the column declares
(MEASURED 2026-11-22: its driver refuses a longer number); no other database bounds a `Numeric` column, and a declared
precision bounds nothing. On a SQL column that is not an integer, a whole number outside the signed 64-bit range is
compared as the column compares: exactly on a `Numeric` column (see section 5 for SQLite), as its digits on a column
that stores text, and as the nearest floating-point number on a `Float` column and on an expression without a type. A
number is a value like any other (PRD-F14), so one of more than 255 characters is `value_too_long`. A number is written
in ASCII: an optional sign, digits with an optional decimal point (`7`, `7.5`, `.5`, `5.`) and an optional exponent
(`1e3`); white space around it is ignored. Any other spelling is `invalid_number`, among them a digit outside ASCII, a
digit separator (`1_000`, `1,000`) and a second sign.

- **Reason:** A database error on an out-of-range number is a user mistake surfacing late. One stated spelling is
  answered alike on every backend. The reading and the comparison with the earlier behaviour are in
  docs/decisions/002-how-a-day-and-a-number-are-written.md. The measurements on PostgreSQL, MySQL and SQL Server are
  in docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md.
- **Priority:** must

#### PRD-F26

"Empty" means NULL on SQL, and also `''` on a SQL column that stores text, whatever type the column is declared as: one
whose SQL type is a string type (also behind a `TypeDecorator`; an `Enum` only when `''` is one of its values), or an
expression without a SQL type that is declared a string. On OpenSearch it is a missing field.

- **Reason:** Users do not tell NULL from an empty string. What each database answers when a column that stores no
  text is compared with `''` is in docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md.
- **Priority:** must

#### PRD-F27

SearchBuilder's conditions are answered by the column's declared type. A string column takes `=`, `!=`, `contains`,
`!contains`, `starts`, `!starts`, `ends`, `!ends`, `null`, `!null`. A number column takes `=`, `!=`, `<`, `<=`, `>`,
`>=`, `between`, `!between`, `null`, `!null`. A date column takes the number set plus `contains` and `!contains`, which
mean the year, month or day span of PRD-F22. A boolean column takes `=`, `!=`, `null`, `!null` (PRD-F43). These sets are
narrowed by PRD-D18 and PRD-D19. A condition that does not fit the column's type is a user mistake, refused with
`invalid_filter` and a reason; a condition outside the 16 is structural (PRD-P6).

- **Reason:** Typed comparisons and range filters for any DataTables 2.x client; a wrong condition is a user mistake
  (draft 2's PRD-F29, merged here).
- **Priority:** must

#### PRD-F28

SearchBuilder groups nest and join their criteria with AND or OR; an empty group adds nothing.

- **Reason:** SearchBuilder's tree.
- **Priority:** must

#### PRD-F30

An unfinished SearchBuilder criterion adds nothing, as an empty group does (PRD-F28): one with no condition (missing,
empty text, or the text `undefined`, as a form sends it), no column, or without the value or values its condition
takes. A value that is empty text counts as missing, as in SearchBuilder's client; white space is a value. A criterion
with a value it cannot read, or with more values than its condition takes (PRD-F36), is still refused.

- **Reason:** A criterion the user has not finished must neither refuse the request nor match every row. A new
  SearchBuilder criterion starts without a condition or a column, SearchBuilder's client sends it with every draw and
  does not apply it, and DataTables 3 writes its `undefined` as that text in a form.
- **Priority:** must

#### PRD-F31

An optional base filter (a SQL expression or an OpenSearch query) limits the rows the table shows at all, for
`recordsTotal`, `recordsFiltered` and the page alike.

- **Reason:** Row-level scoping belongs to the server, not the client.
- **Priority:** must

#### PRD-F32

The OpenSearch global search skips number and date columns.

- **Reason:** OpenSearch cannot match a number or date as text without a script.
- **Priority:** must

#### PRD-F33

An OpenSearch column search on a number column, other than a categorical filter or `multi_values` list, takes one number
and matches it exactly; several words, `!` or quotes are refused with `invalid_number`. A non-empty categorical value or
`multi_values` entry on a number column is a number and matches as `=` does, on every backend; an unreadable one is
`invalid_number`; an empty one, or one of only white space, means empty, as PRD-F26 defines it (PRD-F17, PRD-F20).

- **Reason:** No text form to match a substring in; smart-search syntax has no meaning there.
- **Priority:** must

#### PRD-F34

An OpenSearch column search on a date column, other than a categorical filter or `multi_values` list (PRD-F21), takes
one date term, written as PRD-F22 says (`YYYY`, `YYYY-MM`, or a day with an optional time after it); any other text,
among it several terms, `!` or quotes, is refused with `invalid_date`.

- **Reason:** A date span is one term; smart-search syntax has no meaning there.
- **Priority:** must

#### PRD-F35

`!` excludes a word only when the word, escaped by DataTables' `util.escapeRegex`, is longer than one character: `!-`
and `!.` exclude, `!x` adds nothing.

- **Reason:** DataTables' smart search exactly: it escapes the text first and negates a word only `if (word.length > 1)`
  (READ, DataTables 3.1.5 `dataTables.mjs` :5913 and :5948); the server and SvelteDT's local source share DataTables'
  grammar.
- **Priority:** must

#### PRD-F36

A SearchBuilder criterion carrying more values than its condition takes is refused with `invalid_filter`. `null` and
`!null` take no value and ignore every value entry they carry (`[]`, `['']` or leftovers): no entry is checked or
transformed, PRD-F15 included, so they are never refused for one; a structural error (PRD-P6, PRD-P11) still raises.

- **Reason:** Silently using the first value answers a different question than asked; `null` and `!null` read no
  value, so an entry cannot change their answer.
- **Priority:** must

#### PRD-F37

NULL satisfies every negative SearchBuilder condition (`!=`, `!contains`, `!starts`, `!ends`, `!between`) on every
backend.

- **Reason:** A NULL value is not equal to, does not contain, start or end with, and is not between anything, as `!word`
  passes NULL (PRD-F5); DataTables' client-side SearchBuilder agrees for strings and dates (section 5).
- **Priority:** must

#### PRD-F38

A SearchBuilder tree nests at most 8 groups deep and holds at most 64 criteria in all; a categorical filter or
`multi_values` list holds at most 256 values, and all lists of one request together at most 512; a request holds at
most 64 column searches with a value; beyond any bound the request is refused with `invalid_filter`, except a tree
deeper than the parser reads, which is malformed (PRD-P6). The global search covers each declared column once, however
often the request names it. All the conditions of a request together are bounded by PRD-C17.

- **Reason:** Every criterion and value is query work, and walking the tree must never recurse without bound.
- **Priority:** must

#### PRD-F39

A column search holding both a value and a non-empty `multi_values` list is refused with `invalid_filter`.

- **Reason:** One of the two would be dropped silently.
- **Priority:** must

#### PRD-F40

A global search carrying a `multi_values` list is refused with `invalid_filter`; an empty list is idle.

- **Reason:** `multi_values` belongs to a column search; on the global search the list would be dropped silently.
- **Priority:** must

#### PRD-F41

Searchable is decided by both sides. The global search skips a column that either the request or the server marks
unsearchable, and a request-searchable column the server did not declare, so a rendered extra column cannot break every
global search. A column search with a value or a non-empty `multi_values` list on a column the server declares
unsearchable is refused with `invalid_filter`, whatever the request's `searchable` says; so is a SearchBuilder criterion
on such a column. On a column the request marks unsearchable and the server declares searchable, a column search is
answered: the server decides. A column search with a value or a non-empty `multi_values` list, a SearchBuilder
criterion, or an order entry the request marks orderable, naming an undeclared column, stays refused (PRD-D6, PRD-O1).

- **Reason:** DataTables' client gives a column marked `searchable: false` empty search text, so its global search skips
  it (READ, DataTables 3.1.5 `dataTables.mjs` :5987-5998); the server decides what may be searched (PRD-D4), and a
  request cannot widen it.
- **Priority:** must

#### PRD-F42

The day text of a timezone-aware column, in the global or a column search, is its UTC day, the day PRD-F21 defines for
the criteria (and PRD-F22's spans).

- **Reason:** A row must fall on one day whichever way it is searched; PostgreSQL's `to_char` renders a `timestamptz` in
  the session zone, and SQL Server's `CONVERT(VARCHAR(10), col, 23)` a `DATETIMEOFFSET` in its stored offset, so either
  could give the text another day than the criteria.
- **Priority:** must

#### PRD-F43

On a boolean column, SearchBuilder's `=` and `!=` take only `true` or `false` (case-insensitive) or `1` or `0`, ignoring
whitespace around the value as around a number or a date; `null` and `!null` work as on any type (PRD-F26, PRD-F36). Any
other condition or value is refused with `invalid_filter` before anything runs. Every backend gives the same answer
(with PRD-F37 for `!=`).

- **Reason:** Draft 1 read a boolean column as text, so each backend answered differently (MEASURED, container probe run
  44638017591): the query raised a database error on PostgreSQL for `1`, `true` and `abc`, and on SQL Server for `abc`;
  MySQL matched the false rows for `true` and `abc`; OpenSearch matched nothing. Owner's decision of 2026-11-19.
- **Priority:** must

#### PRD-F44

The global search skips a boolean column. A column search on one, with a value, a categorical filter or a `multi_values`
list, takes the values of PRD-F43 and matches them exactly; any other value is refused with `invalid_filter`. An empty
alternative or `multi_values` entry, or one of only white space, means empty, as PRD-F26 defines it (PRD-F17, PRD-F20).

- **Reason:** "true" as search text means nothing a user reads in the cell; a column search names the value. Owner's
  decision of 2026-11-19.
- **Priority:** must

### 4.4 Ordering (O)

#### PRD-O1

An order on an index outside the request's columns raises `MalformedRequest` (PRD-P6): only an integration bug sends
one. For a column in the request, the request's `orderable` and the server's declaration decide together: request
orderable and server unorderable, the order is refused (PRD-E1); request unorderable and server orderable, the order is
answered; both unorderable, the entry is skipped. An entry the request marks unorderable on an undeclared name is
skipped, not refused, so an undeclared column that only renders cannot fail a first request; an entry the request marks
orderable on an undeclared name is refused (PRD-D6), which surfaces a misconfigured table. An entry on a nameless column
is skipped (PRD-P21). When every entry is skipped, PRD-O4 applies (ascending).

- **Reason:** The server decides what can be sorted. DataTables sends its default order `[[0, 'asc']]` even when that
  column is `orderable: false` (READ, DataTables 3.1.5 `dataTables.mjs`: `sortFlatten` ignores `orderable` at
  :4794-4836, and the request sends `orderable` at :5631), and its own client sorts by such a column; refusing would
  fail a table whose first column is not orderable on its first request.
- **Priority:** must

#### PRD-O2

NULL is the lowest value: first ascending, last descending, on every backend. On a boolean column false sorts before
true, so ascending is NULL, false, true.

- **Reason:** One answer on every database (their defaults differ: PostgreSQL puts NULL last ascending, and OpenSearch
  a missing value last in either direction, INFERRED from its documentation), as SvelteDT's local source sorts empty
  values first. Each backend's native order puts false before true (MEASURED: the tests assert it on SQLite,
  PostgreSQL 18, MySQL 8.4, SQL Server 2022 and OpenSearch 3.10 and 2.19). On PostgreSQL a column that can hold NULL is
  ordered with `NULLS FIRST` or `NULLS LAST`, which an index in the default order does not serve (INFERRED from
  PostgreSQL's documentation), so an app that sorts a large table by such a column declares its index `NULLS FIRST`; a
  column that cannot hold NULL is ordered plainly and keeps its index.
- **Priority:** must

#### PRD-O3

After the user's order, last, a tie-breaker on a unique key (the primary key, or the key PRD-D11 declares) follows the
direction of the first applied order entry (PRD-O8; a skipped entry does not decide), so descending is the exact reverse
of ascending.

- **Reason:** Pages are deterministic, a tie never splits across a page boundary, and reversing the order reverses the
  rows.
- **Priority:** must

#### PRD-O4

With no order in the request, the rows come in the tie-breaker's ascending order.

- **Reason:** A stable first page.
- **Priority:** must

#### PRD-O5

A text-only OpenSearch string column (PRD-D13) is unorderable by default, so an order on it is refused or skipped as
PRD-O1 says; declaring it orderable raises `SchemaError` at construction.

- **Reason:** OpenSearch rejects the whole search on an analysed-text sort, so an entry the request marks orderable is
  refused rather than sent; a request-unorderable entry is skipped as PRD-O1 says.
- **Priority:** must

#### PRD-O6

Text order is the database's collation; the library adds no collation of its own.

- **Reason:** Portable and predictable per database; see the non-goals.
- **Priority:** must

#### PRD-O7

OpenSearch sorts a string column on its keyword field (PRD-D13).

- **Reason:** Sorting needs the unanalysed form.
- **Priority:** must

#### PRD-O8

Order entries apply in request order, the first applied entry primary; a skipped entry (PRD-O1, PRD-P21) drops out, and
the next applied entry takes its place. An entry on a column that an earlier applied entry already orders by is skipped
too, whatever its direction: it can change nothing.

- **Reason:** DataTables sends a multi-column order with its primary column first (INFERRED from its multi-column
  ordering, where shift-click adds a column after the others). A column named twice sorts by its first entry alone, and
  leaving the second out bounds the entries applied by the declared columns: SQLite refuses an ORDER BY of 2,000 terms
  (MEASURED 2026-11-22: a request with 1,999 order entries was answered, one with 2,000 failed in the database). SQL
  Server refuses an ORDER BY that names a column twice (MEASURED 2026-11-22 on SQL Server 2022: error 169, "A column has
  been specified more than once in the order by list").
- **Priority:** must

### 4.5 Counts and paging (C)

#### PRD-C1

`recordsTotal` counts the rows under the base filter.

- **Reason:** DataTables' meaning; SvelteDT shows it as the pager's unfiltered total (its PRD-B26).
- **Priority:** must

#### PRD-C2

`recordsFiltered` counts the rows after every filter.

- **Reason:** DataTables' meaning; SvelteDT's table and pager total.
- **Priority:** must

#### PRD-C3

A `start` at or past the end answers an empty page with both counts; this wins over PRD-C12.

- **Reason:** The client recovers by paging back; an error would hide the counts.
- **Priority:** must

#### PRD-C4

SQL counts with `COUNT(*)`.

- **Reason:** Exact counts.
- **Priority:** must

#### PRD-C5

SQL pages with OFFSET/LIMIT.

- **Reason:** Every supported dialect has it.
- **Priority:** must

#### PRD-C6

SQL selects only the declared columns.

- **Reason:** Undeclared columns never leave the database.
- **Priority:** must

#### PRD-C7

OpenSearch returns the page and the exact filtered count in one search.

- **Reason:** One round trip where possible.
- **Priority:** must

#### PRD-C8

On OpenSearch, `recordsTotal` reuses the filtered count when nothing beyond the base filter applies.

- **Reason:** The second count would give the same number.
- **Priority:** must

#### PRD-C9

On OpenSearch, a count that is only a lower bound is replaced by an exact count.

- **Reason:** Never an approximate total.
- **Priority:** must

#### PRD-C10

An OpenSearch page is served when `start + length <= max_result_window` (the ordinary way) or
`recordsFiltered - start <= max_result_window` (by reversing the sort and the page). `max_result_window` defaults to
10,000 and can be set per table.

- **Reason:** "Last page" on a large index is an ordinary press in SvelteDT's pager; the reverse sort reaches it within
  the window.
- **Priority:** must

#### PRD-C11

To decide PRD-C10, the filtered count, plus the total count when PRD-C8 does not reuse it, may run before the page
query; these are the only queries that may run before a refusal. The filtered count is a `_count` of the search's own
query, so it is exact whatever a search's total says (PRD-C9).

- **Reason:** Telling a near-end page from a middle one needs the total.
- **Priority:** must

#### PRD-C12

Any other OpenSearch page beyond `max_result_window` is refused with `page_out_of_range`, and the answer carries the
real `recordsTotal` and `recordsFiltered`. The library never changes cluster settings such as `max_result_window`.

- **Reason:** OpenSearch would reject it; the counts let a client offer a way back (SvelteDT's helper may or may not use
  them). Owner's decision of 2026-11-19 for 1.0: the first and last pages always work (PRD-C10, PRD-C11), and a
  sequential `search_after` route waits until users hit the limit.
- **Priority:** must

#### PRD-C13

A reverse-sorted page may drift if the index changes between the count and the page, as offset paging always can; no
point-in-time is held.

- **Reason:** A point-in-time per request costs a server resource for a drift offset paging already has.
- **Priority:** must

#### PRD-C14

`recordsTotal` can be cached by a caller-supplied TTL cache.

- **Reason:** An unfiltered count of a large table is costly.
- **Priority:** must

#### PRD-C15

Two tables that differ only in a bound value (e.g. per tenant), in the source or in the base filter, never share a
cached total: the cache key covers the count's statement or query with its bound values, and the database (its URL)
or the index (its name).

- **Reason:** A shared total would show one tenant another's count.
- **Priority:** must

#### PRD-C16

A cached total ends by its TTL or by an explicit call; the library registers no global hooks on engines or pools.

- **Reason:** Global hooks would surprise an app.
- **Priority:** must

#### PRD-C17

One budget of conditions per request, the same on every backend, keeps every request the library builds within
every supported database's limits.

One word on one column, one list value and one range each count one condition, and a request of more than 960 is
refused with `request_too_large` before anything is queried (PRD-E3). The global search counts a word once for each
column it covers, so alone it allows 16 words over 60 columns, 8 over 120, 4 over 240, 2 over 480 and 1 over 960;
every column search word, list value, range and criterion takes one from that. A `base_filter` and an expression
column add conditions of their own, which the budget does not count.

- **Reason:** A request a client can send must never fail on a database's limit: SQL Server's parameters or SQLite's
  expression depth. The number is measured, not computed (`docs/decisions/005-the-largest-request-the-bounds-allow.md`),
  and the table above is what it allows of a wide table's global search, which costs words times columns.
- **Priority:** must

### 4.6 Errors (E)

#### PRD-E1

A request the user can fix is answered, not raised: the response carries an `error` text and an `error_code`.

- **Reason:** The user changes the filter; the app needs no error handling for it.
- **Priority:** must

#### PRD-E2

The user-error codes are `invalid_filter`, `invalid_character`, `value_too_long`, `too_many_words`,
`request_too_large`, `invalid_date`, `invalid_number` and `page_out_of_range`, documented and stable. The list is
closed: a release answers only the codes its documentation lists, and a code is added only in a release that says so. A
client treats a code it does not know as a refusal of the request and shows `error`.

- **Reason:** A frontend maps codes to its own messages; SvelteDT can show the reason at the field. A control character
  has a code of its own because the user cannot see it, so a frontend needs to say more than "invalid filter" (owner's
  decision of 2026-11-21). A client written for an earlier list must still handle a later one.
- **Priority:** must

#### PRD-E3

The code follows the first mistake in this order: (1) a control character (PRD-F15) is `invalid_character`, a value over
255 code points (PRD-F14) is `value_too_long`, search text of more than 16 words (PRD-F14) is `too_many_words`, and a
structure over the bounds (PRD-F38) is `invalid_filter`; within step 1 the first mistake is the one found first in the
order the request is read, the global search, then the column searches in the request's order, then the SearchBuilder
tree (its structure, then its criteria in document order), and within one filter a control character in any of its
values, then any value's length, then the words; last in step 1, after all of those, a request of more than 960
conditions (PRD-C17) is `request_too_large`, counted over the parts the plan holds, so a part left out for a mistake
is not counted, and a request over the budget only through such a part is answered with that mistake, and with
`request_too_large` once it is fixed; (2) a value whose format a date or number column cannot read is
`invalid_date` or `invalid_number`, including several words, `!` or quotes on an OpenSearch date column (PRD-F34) or
number column (PRD-F33); (3) a readable value outside the range is `invalid_date` for the maximum date (PRD-F24) and
`invalid_number` for a number its SQL column does not take (PRD-F25: outside an integer's range, signed or unsigned, or
of more than 38 digits before the decimal point on a `Numeric` column on SQL Server); (4) every other filter error, a
boolean value outside PRD-F43's set included, is `invalid_filter`; (5) only then, a page of PRD-C12 is
`page_out_of_range`, since it needs a valid count.

- **Reason:** One code per kind of mistake, decidable when a request holds several.
- **Priority:** must

#### PRD-E4

An `error` text names the exact condition and how to fix it, as far as that is known when the mistake is found. It is
fixed per case apart from declared names and limits the server holds (an integer column's bounds, the digits a number
may have, the result window, the last date there is), and never quotes the user's input.

- **Reason:** A user can act only on a text that says what is wrong and what to do (owner's rule of 2026-11-21). Echoing
  input invites injection into logs and pages; a declared name and a limit are the server's own text.
- **Priority:** must

#### PRD-E5

`query_failed` is set only by an app, through `DTResponse.query_failed(draw)`, the conventional shape for a failure it
catches.

- **Reason:** The library never answers its own failures.
- **Priority:** must

#### PRD-E6

A structural error raises `MalformedRequest`, never `invalid_filter`. `MalformedRequest` carries the request's `draw`
when it parsed as a valid integer, and none otherwise (PRD-A10).

- **Reason:** One the integrator fixes, the other the user.
- **Priority:** must

#### PRD-E7

Every user mistake is found while parsing, planning or building the query, before the page is queried (the count of
PRD-C11 may run first).

- **Reason:** A filter error is never a half-run query.
- **Priority:** must

#### PRD-E8

Everything else raises: a database error, a schema the table lacks, a partial OpenSearch answer.

- **Reason:** A server failure is not the user's to fix and must not look like an empty result.
- **Priority:** must

#### PRD-E9

An OpenSearch answer that does not cover every shard, or whose shard information cannot be read, raises
`PartialResultError`.

- **Reason:** A partial answer is never shown as complete.
- **Priority:** must

#### PRD-E10

Logs never contain search values or client-supplied names.

- **Reason:** Search text can be personal data; client names are untrusted.
- **Priority:** must

#### PRD-E11

A refused name is logged only if the server declared it.

- **Reason:** A declared name is the server's own text.
- **Priority:** must

#### PRD-E12

Logging uses the standard `logging` module under the logger `datatables_backend`.

- **Reason:** Apps route it like any library's logs.
- **Priority:** must

#### PRD-E13

Each refusal, a `MalformedRequest` raised or a user error answered (PRD-E1), logs exactly one WARNING with a fixed event
name per case, holding no client text and only server-declared names (PRD-E10, PRD-E11). A skip the requirements allow
logs nothing: an idle column or filter (PRD-P5), a column the global search leaves out (PRD-F32, PRD-F41, PRD-F44), an
order entry skipped (PRD-O1, PRD-O8), a nameless column (PRD-P21). No answer is partial: a request is answered in full
or refused.

- **Reason:** An operator sees every refusal once and can count them by event; an allowed skip is normal traffic
  (DataTables sends its default order and idle searches on every request), and logging it would bury the refusals.
- **Priority:** must

### 4.7 Rows and the response (R)

#### PRD-R1

Each row is a dict keyed by the client-facing names.

- **Reason:** The client never sees backend field names.
- **Priority:** must

#### PRD-R2

An optional row hook can reshape a row (e.g. through a Pydantic model).

- **Reason:** Apps shape rows without post-processing the response.
- **Priority:** must

#### PRD-R3

An optional hook adds `DT_RowId` to a row.

- **Reason:** DataTables' row id.
- **Priority:** must

#### PRD-R4

An optional hook adds `DT_RowData` to a row.

- **Reason:** DataTables' row data attributes.
- **Priority:** must

#### PRD-R5

`DTResponse` has exactly `draw`, `recordsTotal`, `recordsFiltered` and `data`, plus `error` and `error_code` when set.

- **Reason:** DataTables' response, extended only by the error code; an unused field invites drift.
- **Priority:** must

#### PRD-R6

`draw` echoes the request's `draw`.

- **Reason:** DataTables drops an answer whose `draw` is not the latest.
- **Priority:** must

#### PRD-R7

Row values are returned as the backend reads them, keyed as PRD-R1 says and changed only by the row hooks (PRD-R2 to
PRD-R4); the library never HTML-escapes a value. Escaping is the frontend's or the app's job.

- **Reason:** DataTables renders `data` as HTML unless told otherwise, so a value is safe only where it is rendered;
  escaping on the server would double-escape for a client that escapes, and the library renders no HTML (a non-goal).
- **Priority:** must

### 4.8 API (A)

#### PRD-A1

A table object holds only the server-side declaration: the source, the columns, the base filter and the hooks.

- **Reason:** No request state leaks between requests.
- **Priority:** must

#### PRD-A2

A table object is built once and reused across requests.

- **Reason:** Declarations are configuration.
- **Priority:** must

#### PRD-A3

Constructors do no I/O.

- **Reason:** Safe at import time.
- **Priority:** must

#### PRD-A4

`await table.fetch(params, conn)` answers one request.

- **Reason:** Async apps.
- **Priority:** must

#### PRD-A5

`table.fetch_sync(params, conn)` answers one request.

- **Reason:** Sync apps.
- **Priority:** must

#### PRD-A6

The page query is built entirely before anything runs, apart from the count of PRD-C11.

- **Reason:** With PRD-E7, a filter error is never a half-run query.
- **Priority:** must

#### PRD-A7

SQL: the table takes a SQLAlchemy `FromClause` (a table, join or subquery) and accepts an `AsyncSession` or
`AsyncConnection` (async) and a `Session` or `Connection` (sync).

- **Reason:** What SQLAlchemy apps hold.
- **Priority:** must

#### PRD-A8

OpenSearch: the table takes an index name and accepts an `AsyncOpenSearch` or `OpenSearch` client; queries are plain
request bodies, with no DSL dependency.

- **Reason:** No extra dependency beyond the client.
- **Priority:** must

#### PRD-A9

An optional FastAPI dependency parses the request from a non-empty body when there is one, otherwise from the query
string; the two are never merged. A body that decodes to a JSON object is read as JSON whatever its Content-Type; any
other body is read as a form.

- **Reason:** DataTables appends its cache-buster `_` to the query string of every request, POST included (READ,
  DataTables 3.1.5 `dataTables.mjs`: `queryParams` adds `_` for any method when `cache === false` at :826, and its
  server-side request sets `cache: false` at :5550), so a non-empty query string does not mean the request is there;
  merging would mix two sources. DataTables 3.1.5's `submitAs: 'json'` keeps the form Content-Type unless one is set
  (READ: its `ajax()` sets JSON only when `!options.contentType`, and its defaults carry form-urlencoded), so choosing
  the parser by Content-Type would read the JSON as a form.
- **Priority:** must

#### PRD-A10

The FastAPI dependency answers 422 on `MalformedRequest`. The body carries `draw` whenever `draw` parsed as a valid
integer (PRD-P10), and leaves it out when `draw` itself is the malformed part.

- **Reason:** The structural/user split of PRD-E6; a client that matches answers by `draw` can still match a 422.
  Owner's decision of 2026-11-19.
- **Priority:** must

#### PRD-A11

The FastAPI dependency answers an answered user error (`invalid_filter`, `invalid_character`, `value_too_long`,
`too_many_words`, `request_too_large`, `invalid_date`, `invalid_number`, `page_out_of_range`) with HTTP 200, carrying
`error` and `error_code` beside `draw`, `recordsTotal`, `recordsFiltered` and an empty `data`. There is no option to
change the status; an app that wants a 4xx maps the response in its own route. A malformed request stays 422 (PRD-A10).

- **Reason:** DataTables reads the error only from a successful response (READ, DataTables 3.1.5 `dataTables.mjs`:
  `buildAjax`'s success callback reads `json.error` at :5512; its `error` handler at :5552 fires `xhr` with a null
  `json`, logs the generic "Ajax error" (tech note 7) at :5559 and does not redraw, so the old rows stay).
  DataTables-native server libraries answer 200 with `error` the same way. Owner's decision of 2026-11-19 (section 8,
  question 2).
- **Priority:** must

### 4.9 Packaging and quality (Q)

#### PRD-Q1

The distribution is `datatables-backend` and the import `datatables_backend`.

- **Reason:** One name.
- **Priority:** must

#### PRD-Q2

Extras are `[sqlalchemy]`, `[opensearch]` and `[fastapi]`.

- **Reason:** One install per need.
- **Priority:** must

#### PRD-Q3

The core (`protocol`, `columns`, `_plan`, `rows`, `cache`) imports only Pydantic and the standard library; each backend
imports only its own extra; `sqlalchemy.ext.asyncio` is imported only for type checking.

- **Reason:** A sync-only or single-extra install must work; `sqlalchemy.ext.asyncio` needs `greenlet`.
- **Priority:** must

#### PRD-Q4

Python 3.12 or later, tested on 3.12 to 3.15.

- **Reason:** The code uses PEP 695 syntax; 3.10 reached end of life in 2026-10.
- **Priority:** must

#### PRD-Q5

Pydantic 2.10 or later; 2.12 or later on Python 3.14; 2.14 or later on Python 3.15.

- **Reason:** 2.7 to 2.9 fail on the PEP 695 aliases; 2.12 and 2.14 are the first with a wheel for 3.14 and 3.15
  (MEASURED).
- **Priority:** must

#### PRD-Q6

SQLAlchemy 2.0.31 or later.

- **Reason:** 2.0.30 fails to import on Python 3.13 (MEASURED).
- **Priority:** must

#### PRD-Q7

opensearch-py 2.6 or later.

- **Reason:** The oldest tested.
- **Priority:** must

#### PRD-Q8

FastAPI 0.133 or later and python-multipart 0.0.9 or later.

- **Reason:** FastAPI 0.133 is the first without a `starlette<1.0` cap (READ, PyPI metadata).
- **Priority:** must

#### PRD-Q9

Each floor is tested on every supported Python.

- **Reason:** A floor never tested is a guess.
- **Priority:** must

#### PRD-Q10

Fully typed (`py.typed`), checked with basedpyright and ruff.

- **Reason:** Users' type checkers see the real API.
- **Priority:** must

#### PRD-Q11

Tests go through the public API (`parse_request`, then `fetch` or `fetch_sync`): SQL on SQLite (sync and aiosqlite) by
default, PostgreSQL, SQL Server and MySQL in CI containers, OpenSearch on the request bodies built and against a
container.

- **Reason:** Behaviour, not implementation, is what users rely on; dialect differences only show on real databases, and
  the code already has a MySQL path.
- **Priority:** must

#### PRD-Q12

CI runs lint, types, tests on every supported Python, the minimum versions, and an install-isolation check of the built
wheel; one required `gate` check blocks merges to main.

- **Reason:** Every claim above is checked on every change.
- **Priority:** must

#### PRD-Q13

MIT licence.

- **Reason:** Permissive; the repository's `LICENSE`.
- **Priority:** must

#### PRD-Q14

OpenSearch from 3.1 on is supported. OpenSearch 3.0 and 2.19 are supported with two limits: on both, a substring and an
exact match fold ASCII letters only (PRD-F9), while the limits of the query form that folds from 3.1 on apply to them as
well (a cluster that refuses expensive queries, a normalizer; section 5); on 2.19, a case-insensitive exact match takes
time that grows with the length of the value and the size of the list. A 3.x release and a 2.19 release are each tested
by a live leg of CI that runs the whole live suite. OpenSearch before 2.19 is not tested.

- **Reason:** Owner's decision of 2026-11-21: the library focuses on 3.x, with limited compatibility for 2.x. The legs
  run 3.10.0 and 2.19.7, and every live test passes on both. For 2.19's cost, OpenSearch's documentation warns for a
  case-insensitive `term` that "In OpenSearch 2.x and earlier, complexity can increase exponentially with the number of
  characters, leading to high heap memory usage and reduced performance." (READ 2026-11-21, its Term query page).
  MEASURED 2026-11-21 (CI runs 44941728065 and 44942700768; 512 MB of heap, about 2,000 documents on one shard, the
  least of 5 runs: the cost of a query by its length, not of a large index): a list of 256 values of 255 ASCII letters,
  which is 256 case-insensitive terms, took 2,540 and 3,760 ms on 2.19.7 in the two runs, and 155 and 195 ms on 3.10.0.
  The other measurements are in `docs/decisions/001-opensearch-case-folding-beyond-ascii.md`.
- **Priority:** must

### 4.10 Later (L)

#### PRD-L1

An Elasticsearch backend behind the same contract.

- **Reason:** Same users as OpenSearch; the 8.x/9.x clients changed the `body=` API, so it needs a thin client adapter.
- **Priority:** later

#### PRD-L2

SearchPanes and ColumnControl.

- **Reason:** DataTables extensions with their own request parts; planned for a milestone after 1.0, when a frontend
  needs them.
- **Priority:** later

## 5. Stated differences

The server cannot give every answer SvelteDT's local source or DataTables' client side gives, and the backends differ
where a database decides. Each difference is stated here; every other answer is the same.

| Difference | Between | Requirement |
|---|---|---|
| Text order is the database's collation, not an ICU collator's digit-group order with case, width and kana ties. | Server and SvelteDT's local source (its PRD-B21) | PRD-O6 |
| An order entry on a nameless column, on one both sides mark unorderable, or on an undeclared name the request marks unorderable, is skipped; DataTables' client sorts by it (its rendered output, READ `dataTables.mjs` :7994; it ignores `orderable`, :4794-4836). | Server and DataTables' client | PRD-O1, PRD-P21 |
| The global search skips a column the request marks searchable but the server did not declare or declares unsearchable; DataTables' client searches it. | Server and DataTables' client | PRD-F41 |
| A timezone-aware column on SQLite or in a MySQL `DATETIME` stores no offset, so its stored time is read as UTC and the app must write UTC; a MySQL `TIMESTAMP` gives the UTC day only under a `+00:00` session `time_zone` (INFERRED from the MySQL documentation); PostgreSQL and SQL Server store the instant. | SQLite and MySQL, and the other backends | PRD-F21 |
| The global search skips a boolean column; DataTables' client and SvelteDT's local source search its rendered text. | Server, and DataTables' client and SvelteDT's local source | PRD-F44 |
| A nameless column takes no part in the global search; DataTables' client searches its rendered text (READ `dataTables.mjs` :7994). | Server and DataTables' client | PRD-P21 |
| A column search on a column the request marks unsearchable but the server declares searchable is answered; DataTables' client gives such a column empty search text (READ `dataTables.mjs` :5987-5998, which its column searches read at :5803-5805 and :5879), so there it matches nothing. | Server and DataTables' client | PRD-F41 |
| Accent folding is the database's; DataTables' client folds accents by default, as does SvelteDT's local source. | Server, and DataTables' client and SvelteDT's local source | PRD-F10 |
| Dotted and dotless i match only where the database's collation makes them match; SvelteDT treats them alike in every locale (its PRD-B21, difference 3). | Server and SvelteDT's local source | PRD-F10 |
| On SQLite, case folds for ASCII letters only. | SQLite and PostgreSQL, MySQL, SQL Server | PRD-F9 |
| On OpenSearch 3.0 and 2.19, case folds for ASCII letters only in a substring search and in an exact match; from 3.1 on it folds letters outside ASCII too. So `äpf` and `=` `äpfel` find `ÄPFEL` from 3.1 on and not before. A whole word on an analysed column folds as its analyser does on every version: the word `äpfel` finds `ÄPFEL` everywhere. | OpenSearch before 3.1 and OpenSearch from 3.1 on; OpenSearch before 3.1 and PostgreSQL, MySQL, SQL Server | PRD-F9, PRD-Q14 |
| On every OpenSearch version a value longer than 255 code points folds ASCII letters only in a substring search and in an exact match, and so does a value without a cased character outside ASCII: `k` does not find the Kelvin sign, `s` not the long s. A substring or an exact match with `i` does not find a dotted capital `İ`; the analysed word does. | OpenSearch from 3.1 on, by the value; for the long value also OpenSearch and PostgreSQL, MySQL, SQL Server | PRD-F9 |
| On OpenSearch a substring search is a wildcard or regular-expression query, and so is an exact match whose value has a cased character outside ASCII and at most 255 code points, on a column that folds (not one declared `fold_case=False`, nor one with a search-value hook). A cluster with `search.allow_expensive_queries` off refuses both kinds of query, and one with `index.max_regex_length` set under a regular expression's length (up to 768 UTF-16 units; the default is 1,000) refuses that one; the request then fails as any server failure does. | OpenSearch clusters by their settings | PRD-F9 |
| On an OpenSearch keyword field whose normalizer rewrites a character into ASCII punctuation (`asciifolding` does it to `⒜`, `⑴`, `⁇`), a value that holds a cased character outside ASCII and such a character can fail, miss its own row or match other rows. This holds on every version, 3.0 and 2.19 included. A `lowercase` normalizer is not affected. | OpenSearch fields by their normalizer | PRD-F9 |
| On an OpenSearch keyword field whose normalizer trims, a search text with a cased character outside ASCII and a space at its start or end finds fewer rows than the same text without the space: the space is kept where the search is open (`contains`, a global or column search, `starts` with a space at the end, `ends` with one at the start). An exact match, and the anchored end of `starts` and `ends`, are trimmed as the field is. This holds on every version. | OpenSearch fields by their normalizer | PRD-F9 |
| An exact match on a column declared with `fold_case=False`, or with a search-value hook, matches case as its database collation or its OpenSearch mapping decides, so the same request can match different rows on different databases. | Between backends | PRD-D21, PRD-F9 |
| In SQL an exact match on a column with a type of its own (a `TypeDecorator`, `Uuid`) or on an expression without a type is not folded: it compares as that type compares. | Plain string columns and other SQL columns | PRD-F9 |
| A categorical filter matches whole values, case-insensitively on a string column (PRD-F9); DataTables' client reads the same value as an unanchored regex, a substring, also case-insensitive by default (READ, DataTables 3.1.5 `dataTables.mjs`: `filterCreateSearch` defaults `caseInsensitive: true` at :5902 and sets the `i` flag at :5965). The server sides with SvelteDT, whose categorical filter is exact (its PRD-B7). | Server and DataTables' client | PRD-F16, PRD-F17 |
| On OpenSearch a stored `""` is not missing, so not empty; on SQL it is empty. | SQL and OpenSearch | PRD-F26 |
| A hooked column matches whole values by equality; SvelteDT's local source matches substrings. | Server and SvelteDT's local source | PRD-D12, PRD-D17 |
| LF, CR, U+2028 and U+2029 count as spaces in search text; DataTables' client splits words on spaces only. The server follows SvelteDT (its PRD-B21, difference 4). | Server and DataTables' client | PRD-F7 |
| On SQL, number and date columns take part in the global and column searches as text (categorical filters and `multi_values` lists excepted, which match as `=` on every backend); OpenSearch's global search skips them. | SQL and OpenSearch | PRD-F23, PRD-F32 |
| An OpenSearch number column search, other than a categorical filter or `multi_values` list, is an exact match; on SQL it matches the number's text. | SQL and OpenSearch | PRD-F23, PRD-F33 |
| An OpenSearch date column search, other than a categorical filter or `multi_values` list, takes one date term; on SQL it is a smart search of the date's text. | SQL and OpenSearch | PRD-F22, PRD-F23, PRD-F34 |
| DataTables' client-side SearchBuilder reads an empty number as 0 (`+''`), so there NULL fails `!= 0` and `!between` over a range holding 0; the server lets NULL satisfy every negative condition. | Server and DataTables' client-side SearchBuilder (READ, SearchBuilder 2.1.1 `numConditions`, its `!=` and `!between`) | PRD-F37 |
| A page beyond `max_result_window` that is not near either end is refused on OpenSearch; SQL serves every page. | SQL and OpenSearch | PRD-C12 |
| A text-only OpenSearch string column cannot be ordered; every SQL string column can. | SQL and OpenSearch | PRD-O5 |
| A text-only OpenSearch string column answers only the analysed search, `null`/`!null` and a list of only empty entries; SQL answers every condition. | SQL and OpenSearch | PRD-D19 |
| Search text of more than 16 words is refused with `too_many_words`; SvelteDT's local source answers it. | Server and SvelteDT's local source | PRD-F14 |
| A value over 255 code points is refused with `value_too_long` on every backend. | Every backend | PRD-F14 |
| A request of more than 960 conditions (a word counts once per column searched, a list value and a range once each) is refused with `request_too_large` on every backend; SvelteDT's local source answers it. | Server and SvelteDT's local source | PRD-C17 |
| On a text-only OpenSearch string column a word is an analysed match (`red` does not find `reddish`). On any analysed column (text-only or text+keyword) a word the analyser splits also matches as a phrase of its tokens: `foo-bar` finds `foo bar` and `!foo-bar` excludes it, and `foo` alone is not found. SQL matches substrings only, so it does neither. | SQL and OpenSearch | PRD-F11 |
| On OpenSearch a number outside the range of the field's own mapping (`long`, `integer`, `unsigned_long`) is refused by the server, so the request fails as any server failure does, a limit of this library, not of OpenSearch; SQL answers such a number on an integer column with `invalid_number`. A `double` field takes every finite number. The measurements are in docs/decisions/003-opensearch-number-ranges-and-fullwidth-marks.md; issue #47 holds the fix. | SQL and OpenSearch | PRD-F25, PRD-E7 |
| On SQLite a whole number outside the signed 64-bit range is not compared exactly with a `Numeric` column: SQLAlchemy binds a decimal as a floating-point number there, since SQLite stores no exact decimal, so the number can match a neighbouring value. PostgreSQL, MySQL and SQL Server compare it exactly (MEASURED 2026-11-22). | SQLite and PostgreSQL, MySQL, SQL Server | PRD-F25 |
| A number written with a decimal point or an exponent is compared with a SQL `Numeric` column as a floating-point number, so it is exact to about 15 significant digits only, a limit of this library, not of the databases; a whole number is compared exactly (on SQLite as the row above says). Issue #56 holds the fix. | Numbers with a point or an exponent, and whole numbers | PRD-F25 |
| On SQL Server, and on MySQL with a PAD SPACE collation (such as `utf8mb4_general_ci` or `utf8mb4_unicode_ci`), an exact match ignores trailing spaces, as the database compares strings: `'a '` equals `'a'`, and a value of spaces only equals the empty string. PostgreSQL, SQLite and MySQL's default collation (`utf8mb4_0900_ai_ci`, NO PAD) tell them apart. MEASURED 2026-11-22 for SQL Server 2022, PostgreSQL 18.7, MySQL 8.4.13 with its default collation and SQLite (docs/decisions/004-numbers-and-the-empty-string-on-the-sql-servers.md); not measured for a PAD SPACE collation. | SQL Server, MySQL with a PAD SPACE collation; PostgreSQL, SQLite | PRD-F20, PRD-F26 |

## 6. Release criteria for 1.0

1. Every *must* is met and traces to spec statements whose tests ran and passed; no skipped test.
2. CI's `gate` is green at the release commit: lint, types, tests on every supported Python, minimum versions, install
   isolation.
3. The PostgreSQL, SQL Server, MySQL and OpenSearch container tests pass, including the proof that the reverse-sort
   paging of PRD-C10 returns the same rows as the ordinary way.
4. SvelteDT's recorded request fixtures (its PRD-W11), once they exist, parse with `parse_request` to the request each
   fixture states, and their answers map in SvelteDT's contract tests.
5. Every question in section 8 is answered and in the specs.
6. The package is published with the release workflow.

Before 1.0 the work is grouped in GitHub milestones, whose names count them in due-date order and are not versions; the
first release is published as version 0.1.0.

## 7. Changes from draft 1

- Search: draft 1's global search matched the whole text in one column (OR across columns) and OpenSearch text took
  `OR`/`NOT`/`-`; now smart search with the row as the unit and one syntax (PRD-F1 to PRD-F8, PRD-F35), case-insensitive
  everywhere but SQLite's non-ASCII (PRD-F9), with bounds (PRD-F14) and control characters refused (PRD-F15); number and
  date columns in search are stated per backend (PRD-F23, PRD-F32 to PRD-F34); the global search leaves out a
  request-searchable column the server did not declare, where draft 1 refused the whole search (PRD-F41); the day
  text of a timezone-aware column is its UTC day, where PostgreSQL rendered it in the session zone and SQL Server
  rendered a `DATETIMEOFFSET` in its stored offset (PRD-F42).
- Categorical: draft 1's forms kept, with the refused character set named; a backslash escape outside PRD-F19's set
  (`\d`, `\w`, `\n`) and an alternation anchored without a group (`^a|b$`), which draft 1's code read as literal
  values, are now refused (PRD-F16, PRD-F18); the empty alternative (PRD-F17) and a recommended sender form
  (PRD-F19).
- Exact matches: draft 1's exact matches compared as the column's collation does (case-sensitive on PostgreSQL, SQLite
  and OpenSearch keyword fields); now case-insensitive on string columns on every backend, except on a hooked column
  (PRD-F9).
- SearchBuilder: an unfinished criterion adds nothing instead of being refused (PRD-F30), as SearchBuilder's own
  client sends one and an empty group adds nothing (PRD-F28); a criterion with too many values is refused (PRD-F36),
  and NULL satisfies every negative condition (PRD-F37); the conditions each declared type takes are listed
  (PRD-F27), and a condition outside the 16 the protocol model knows, such as SearchBuilder's array `without`, is a
  structural error (PRD-P6); a criterion on a column the server declares unsearchable is refused, which draft 1's
  code did not check (PRD-F41).
- Ordering: NULL placement was each database's default and the SQL tie-breaker always ascended; now NULL lowest and the
  tie-breaker follows the first applied entry's direction (PRD-O2, PRD-O3, PRD-O8); a source without a primary key
  declares a unique key (PRD-D11); an order on analysed-only text is refused instead of dropped, or skipped when the
  request marks it unorderable (PRD-O5); an order the request marks unorderable is answered or skipped instead of
  refused, and skipped even on an undeclared name, so DataTables' default order never fails a first request
  (PRD-O1); an order index outside the request's columns raises `MalformedRequest` instead of `invalid_filter`
  (PRD-O1, PRD-P6); text order is stated as the database's (PRD-O6).
- Paging: `start` up to 2^53-1, and `draw` refused above 2^31-1, which draft 1 stated but its code did not check
  (PRD-P10); OpenSearch pages near the end served by reverse sort, middle pages refused
  with `page_out_of_range` and the real counts instead of `invalid_filter` (PRD-C10 to PRD-C13); a start past the end
  answers an empty page with counts (PRD-C3); SQLite no longer listed as binding IN lists as JSON (PRD-C17).
- API: a non-empty body is read instead of the query string, and a body that decodes to a JSON object is read as JSON
  whatever its Content-Type (PRD-A9, changed from draft 1's R8).
- Errors: `invalid_character`, `value_too_long`, `too_many_words`, `request_too_large`, `invalid_date`, `invalid_number`
  and `page_out_of_range` beside `invalid_filter`, with a precedence (PRD-E2, PRD-E3), and texts that say what is wrong
  and how to fix it (PRD-E4); the response's unused `metadata` field goes (PRD-R5).
- New: an integer `data` (PRD-P20), a `null` `data` and nameless columns (PRD-P21), declared OpenSearch field kinds
  (PRD-D13), the hook-column rules (PRD-D14 to PRD-D18), the text-only OpenSearch refusals (PRD-D19), a hooked
  OpenSearch column's keyword field (PRD-D20), the structure bounds (PRD-F38), object `data` (PRD-P18),
  `order[i][name]` (PRD-P16), expression columns (PRD-D10), the order of several entries (PRD-O8), one warning per
  refusal and none per allowed skip (PRD-E13), rows never HTML-escaped (PRD-R7), MySQL in the container tests
  (PRD-Q11) and CI (PRD-Q12).
- Owner's decisions of 2026-11-19: a column with a search-value hook takes equality only (PRD-D12, PRD-D17, PRD-D18); a
  `boolean` column type in milestone 2 (PRD-D3, PRD-D8, PRD-F43, PRD-F44); exact matches on string columns case-insensitive (PRD-F9).
- Boolean columns: draft 1 treated them as text, which raised a database error on PostgreSQL (and on SQL Server for
  non-numeric text), matched the false rows on MySQL for 'true', and matched nothing on OpenSearch (MEASURED,
  container probe run 44638017591); now a boolean type (PRD-F43, PRD-F44).

## 8. Open questions for the owner

1. Closed 2026-11-19 (owner's ruling): the OpenSearch paging limit stays for 1.0. The first and last pages always
   work, a middle page beyond `max_result_window` answers `page_out_of_range`, the library never changes cluster
   settings, and a `search_after` route waits until users hit the limit (PRD-C12).
2. Closed 2026-11-19 (owner's ruling): an answered user error (PRD-E1) is HTTP 200 with `error` and `error_code`,
   alongside `draw`, `recordsTotal`, `recordsFiltered` and an empty `data`, as DataTables expects; there is no adapter
   option, a malformed request stays 422 with `draw` when it was readable (question 4), and an app that wants a 4xx
   maps it in its own route (PRD-A11).
3. Closed 2026-11-19 (owner's ruling): the global search leaves a request-searchable column the server did not declare
   out, as it does an unsearchable one; a column search with a value or a non-empty `multi_values` list, a
   SearchBuilder criterion, or an order entry the request marks orderable naming it is refused (PRD-F41, PRD-D6,
   PRD-O1).
4. Closed 2026-11-19 (owner's ruling): yes; the 422 body carries `draw` whenever it parsed as a valid integer, and
   leaves it out when `draw` itself is malformed (PRD-A10, PRD-E6).
5. Closed 2026-11-19 (decided under the owner's delegation): no write-aware cache; the total cache stays TTL-only until
   a user needs fresher totals than a TTL gives.
6. Closed 2026-11-19 (decided under the owner's delegation): the unreached SQLite `json_each` compiler for IN lists is
   removed in the TDD stage, as its own commit (PRD-C17).

**Governance.** A new user-error code (PRD-E2) is added only by the owner, since frontends depend on the set.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Smart search per word multiplies the LIKE terms (words x columns) on large tables. | PRD-F14 bounds the words and PRD-C17 the conditions of a request, 960 in all; the container tests measure PostgreSQL, SQL Server and MySQL plans. |
| Accent folding and text order differ per database, so server and local results differ. | Section 5 states it; the specs state each database's behaviour; PRD-D10 gives apps their database's means. |
| Reverse-sort paging (PRD-C10) is wrong under ties or a changing index. | It depends on PRD-O3's unique tie-breaker and PRD-C13, and needs the container proof (section 6). |

## 10. Trace from draft 1

| Draft 1 | Here |
|---|---|
| R1 request parsing (JSON, bracket, dotted or mixed; multi-dicts) | PRD-P1, P2 |
| R1 request keys, ignored keys and fields | PRD-P3, P4 |
| R1 key shapes and indexes | PRD-P7 |
| R1 unanswered filters refused unless empty or idle | PRD-P5 |
| R1 malformed: no DataTables parameter, non-text flat key, object value among flat keys | PRD-P19, P6 |
| R1 typed models | PRD-P17 |
| R1 `MalformedRequest`, message names the parameter | PRD-P6, P12 |
| R1 malformed: wrong structure, non-integer paging, negative start, length, numbers above 2^31-1, lone surrogates | PRD-P6, P8, P9, P13, P10 (start raised to 2^53-1), P11 |
| R1 "true"/"false", name falls back to data | PRD-P14, P15 |
| R2 column declaration and its parts | PRD-D1 to D5, D9 |
| R2 undeclared names refused; declared type decides; SQL inference | PRD-D6, D7, D8 |
| R2 value types (a boolean column read as text) | PRD-D3, D8 (boolean added), F43, F44 |
| R3 order index refused / unorderable | PRD-P16 (index authority), PRD-O1 (changed: draft 1 refused an order the request marks unorderable, and answered an index outside the columns with `invalid_filter`, now `MalformedRequest`) |
| R3 SQL tie-breaker on the primary key | PRD-O3 (now direction-following), O4, D11 |
| R3 OpenSearch keyword sort; analysed text dropped with a warning | PRD-O7, PRD-O5 (now refused, or skipped when the request also marks it unorderable), PRD-D13 |
| R4 global search, column searches, "contains" | PRD-F1 to F3 (changed), F9, F12, F13, F11 |
| R4 dates (SQL date text; OpenSearch global search skips dates) | PRD-F21, F22, F23, F24, F32 |
| R4 numbers, "empty" | PRD-F25, F26 |
| R4 SearchBuilder | PRD-F27 (PRD-F29 merged into it), F28, F30; an unknown condition: PRD-P6 |
| R4 multi-select, `multi_values` | PRD-F16, F18, F20, F39, F40 |
| R4 base filter | PRD-F31 |
| R4 OpenSearch Google-style text query | dropped by the owner's decision of 2026-11-19 (PRD-F8) |
| R5 user errors answered, fixed reason, no input quoted | PRD-E1, E2, E3, E4 |
| R5 everything else raises; `query_failed` | PRD-E8, E5 |
| R5 `max_result_window` refusal | PRD-C12 (now `page_out_of_range`; counts may now run first, PRD-C11) |
| R5 partial OpenSearch answer | PRD-E9 |
| R5 logging | PRD-E10, E11, E12 |
| R6 SQL counts, paging, columns | PRD-C1, C2, C4, C5, C6 |
| R6 OpenSearch counts | PRD-C7, C8, C9 |
| R6 total cache | PRD-C14, C15, C16 |
| R6 SQL Server parameter limit | PRD-C17 (SQLite JSON binding dropped; the bounds of PRD-F38 alone did not keep within it, so PRD-C17 became a budget) |
| R7 rows and response | PRD-R1 to R6 |
| R8 API shape | PRD-A1 to A10 (A9 changed) |
| R9 distribution, extras | PRD-Q1, Q2 |
| R9 Pydantic-only core (from the project rules) | PRD-Q3 |
| R9 Python, floors | PRD-Q4 to Q9 |
| R9 typed, tests, licence | PRD-Q10, Q11, Q13 |
| Milestones v0.1, v0.2 | Section 6, before 1.0 |
| Milestone "later" | PRD-L1, L2 |
| Open questions (Elasticsearch adapter, write invalidation) | PRD-L1; section 8, question 5 |
