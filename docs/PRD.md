# datatables-backend: product requirements

- **Product:** datatables-backend, a read-only Python library that answers DataTables server-side requests from SQL
  (SQLAlchemy 2, async and sync) and OpenSearch, with one request model, one column declaration and one set of
  safety rules for every source.
- **Status:** draft 2, revision 7 (2026-11-19), for the owner's approval. It replaces draft 1 (R1 to R9); section 10
  maps every draft-1 item to its place here. Once approved, a requirement changes only by the owner.
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
- SearchBuilder's array conditions (on columns of array values). Only string, number and date conditions are answered
  (PRD-F27).

## 4. Requirements

Priority: **must** (in 1.0), **should** (in 1.0 unless the owner moves it), **later** (after 1.0).

Ids: `PRD-P` parsing, `PRD-D` column declaration (the server's schema, which every other group reads), `PRD-F`
filtering and search, `PRD-O` ordering, `PRD-C` counts and paging, `PRD-E` errors, `PRD-R` rows and the response,
`PRD-A` API, `PRD-Q` packaging and quality, `PRD-L` later. Each id holds one behaviour.

A **request parameter** is one of `draw`, `start`, `length`, `columns`, `order`, `search`, `searchBuilder`,
`searchPanes` and `searchPanes_null`. A flat key's **first segment** is its text before the first `.`, `[` or `]`.

### 4.1 Parsing (P)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-P1 | A request is read from a decoded JSON object (nested), or from flat form or query parameters: a mapping, a multi-dict (anything with `getlist`, every value of a repeated key read), or key-value pairs with repeated `key[]` entries. | DataTables posts JSON when `ajax.submitAs` is `json` (READ, DataTables 3.1.5 reference) and form or query parameters by default; Starlette, Werkzeug and Django hand form data over as multi-dicts. | must |
| PRD-P2 | Flat keys are read in bracket notation (`columns[0][search][value]`), dotted notation (`columns[0].search.value` or `columns.0.search.value`) or a mix, and every form reads to the same request. | DataTables sends brackets by default; spring-data-jpa-datatables reads dotted names (READ, its README); SvelteDT's helper sends either (its PRD-B20). | must |
| PRD-P3 | A top-level key that is not a request parameter is ignored, in every form: in a JSON body, and as a flat key whose first segment is not a request parameter (DataTables' cache-buster `_`, SearchPanes' `searchPanes_options` and `searchPanesLast`, an app's own `ajax.data` keys). | DataTables appends `_` to every request under its default `cache: false` (READ, its 3.1.5 `queryParams`); SearchPanes sends its options on every request (READ, SearchPanes 3.0.1); apps add their own keys. | must |
| PRD-P4 | An unknown field under a request parameter is ignored, except as PRD-P5 says. | Clients add fields over time; an unknown field asks for nothing the library answers. | must |
| PRD-P5 | A filter the library does not answer raises `MalformedRequest` (PRD-E6) unless it is empty or idle: a non-empty `search[fixed]`, `search[groups]`, `search[groupsFixed]` or `columns[i][search][fixed]`; a non-empty pane in `searchPanes` or `searchPanes_null` (SearchPanes sends an empty pane on every request); a non-empty `columns[i][columnControl][list]`; a `columns[i][columnControl][search]` with a value or the `empty` or `notEmpty` logic. | DataTables omits empty arrays (READ, its 3.1.5 serializer), and ColumnControl sends an idle search on every request, counting it active only with a value or those logics (READ, ColumnControl 2.1.3); dropping an active filter would answer fewer filters than asked, with no sign. | must |
| PRD-P6 | A request whose structure cannot be read (a top-level value that is not an object, a request parameter, or a field under one, of the wrong type (a `null` `data` excepted, PRD-P21), a flat key that is not text, an object value among flat keys, keys that collide or leave a gap in an index) raises `MalformedRequest`. | A structural error is an integration bug, not something the user can change. | must |
| PRD-P7 | A flat key whose first segment is a request parameter must be `[part]` and `.part` segments, a bracket segment holding no `.`, an index being `0` or ASCII digits not starting with 0, at most 9 digits, and `[]` appending a value, never a field; any other shape raises `MalformedRequest`. The 9-digit rule bounds the indexes in keys; numeric values are bounded by PRD-P10. | A key the parser cannot read must not be half-read; Unicode, leading-zero and over-long indexes once crashed the parser (MEASURED). | must |
| PRD-P8 | A paging number that is not an integer raises `MalformedRequest`. | Paging must be exact. | must |
| PRD-P9 | A negative `start` raises `MalformedRequest`. | No page lies before the first. | must |
| PRD-P10 | The numeric values are bounded: `start` is accepted up to 2^53-1, and `draw` and `order[i][column]` up to 2^31-1; a larger value raises `MalformedRequest`. The indexes in flat keys are bounded by PRD-P7. | SvelteDT pages up to 2^53-1, the largest integer JavaScript holds exactly (its PRD-B9); the other numbers never need more. | must |
| PRD-P11 | Text in a request parameter that JSON cannot encode (a lone surrogate) raises `MalformedRequest`. | No database driver can encode it. | must |
| PRD-P12 | A `MalformedRequest` message names the request parameter and never the client's key or value. | Client text in messages invites injection into logs and pages. | must |
| PRD-P13 | `length` is between 1 and a configured maximum (default 1000); any other value, "All" (`-1`) included, raises `MalformedRequest`. | An unbounded page is the first way a table query goes wrong; SvelteDT pages at most 1000 rows (its `docs/departures.md`, DEP-13). | must |
| PRD-P14 | The strings `"true"` and `"false"` read as booleans. | Form and query encodings carry only strings. | must |
| PRD-P15 | A column's `name` falls back to its `data`. | DataTables sends an empty `name` unless the page sets one. | must |
| PRD-P16 | `order[i][column]` indexes the request's own `columns`; an `order[i][name]` beside it is accepted and the index decides. | DataTables 2.x and SvelteDT send both (its PRD-B20); one rule avoids two answers to one request. | must |
| PRD-P17 | The request is validated into typed models (`DTParams`, `DTColumn`, `DTOrder`, `Search`, the recursive SearchBuilder tree). | Nothing downstream sees raw input. | must |
| PRD-P18 | A column's `data` may be an object (DataTables' orthogonal data); the column's identity is then its `name`. An object `data` with an empty `name` is a nameless column, handled as PRD-P21 says. | DataTables sends an object `data` for orthogonal columns; such a column has no other name the server can match, and a column nothing filters or sorts needs none. | must |
| PRD-P19 | A request holding no request parameter at all (an empty request, or DataTables 1.9's `sEcho`/`sSearch`/`iDisplayStart` form) raises `MalformedRequest`; it is never answered as an empty request. | An unread request answered unfiltered with `draw` 0 is dropped silently by DataTables. | must |
| PRD-P20 | An integer `columns[i][data]` is read as its decimal text. | DataTables defaults `data` to the column index, an integer. | must |
| PRD-P21 | A column's `data` of JSON `null` reads as empty text. A nameless column has an empty `name` and a `data` that is empty, `null` or an object. The global search skips it, and an `order` entry on it is skipped (this wins over PRD-O1); a column search on it, or a SearchBuilder criterion with an empty `origData`, is refused with `invalid_filter` (PRD-E1), as an undeclared name is (PRD-D6). | DataTables sends `data: null` for a column that only renders (JSON `null`; a form request leaves the value empty), and sends its default order `[[0, 'asc']]` whatever `orderable` says (READ, DataTables 3.1.5 `dataTables.mjs`: the default at :12404, `sortFlatten` at :4794-4836), so a table whose first column only renders would be refused on its first request. DataTables' own client sorts such a column by its rendered output or its `sort` key (READ, `dataGet` at :7994), which the server does not have: the server holds no value to sort such a column by, so the entry is dropped and PRD-O3's tie-breaker decides. A search on a nameless column names nothing the server holds, which the user can change. | must |

### 4.2 Column declaration (D)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-D1 | The server declares the table's columns, each with the name the client uses. | The server, not the client, decides what exists. | must |
| PRD-D2 | A column may name the backend field it reads when that differs from the client's name. | Client names need not reveal the schema. | must |
| PRD-D3 | A column declares a value type: string, number or date. | The type decides how a value is matched (PRD-D7). | must |
| PRD-D4 | A column declares whether it is searchable. | Some columns must not be searched. | must |
| PRD-D5 | A column declares whether it is orderable. | Some columns must not be sorted. | must |
| PRD-D6 | Any name the request uses to search, filter or order on (column searches, the global search, `order`, every SearchBuilder `origData`) must be a declared name; otherwise the request is refused (PRD-E1). | A backend field never becomes reachable just because the client names it. | must |
| PRD-D7 | The declared type decides how a criterion is built; the client's SearchBuilder `type` is ignored. | A wrong client type cannot change how a value is matched. | must |
| PRD-D8 | SQL infers the type from the column when none is declared; OpenSearch defaults to string. | Most SQL columns carry their type; an index mapping is not read at request time. | must |
| PRD-D9 | A column may declare a hook that transforms a search value before it reaches the query (e.g. to encrypt or encode it as stored). | Some columns are stored transformed; the user still types the plain value. | must |
| PRD-D10 | A SQL column may be declared as an expression over the source (e.g. `unaccent(name)` or a `COLLATE` clause), searched and ordered like any column. | Accent folding and collation follow the database (PRD-F10, PRD-O6); an app that wants more uses its database's means. | should |
| PRD-D11 | A table whose source has no primary key (every OpenSearch index; a SQL join or subquery without one) declares a unique key; constructing it without one raises `SchemaError`. | The tie-breaker of PRD-O3 must be unique; the first column of a join is not. | must |
| PRD-D12 | In the global search, a column with a search-value hook (PRD-D9) takes no part in per-word matching: a row matches when its other columns satisfy the smart search, or when the hooked column equals the whole search text, trimmed and transformed once. `!word` ignores hooked columns, and blank text adds nothing. When every searchable column is hooked, the equality alone decides, and PRD-F13 does not apply. Trimmed means leading and trailing Unicode White_Space removed; PRD-F7's line-break rewriting does not apply to the whole text, which is an exact value; PRD-F14's 255-code-point bound applies; its 16-word cap is lifted only when every searchable column is hooked, and otherwise refuses the whole request. | A transformed (e.g. encrypted) value cannot be matched word by word or as a substring; the whole text is what a user types to find one value. Owner's decision of 2026-11-19. | must |
| PRD-D13 | An OpenSearch string column's kind is `keyword`, `text+keyword` (the default, the mapping OpenSearch's dynamic mapping gives a string, with the keyword field `<field>.keyword`) or text-only; the keyword field is used for sorting and exact matches. | The backend never reads the index mapping at request time (PRD-D8, PRD-A3), so the sort (PRD-O5, PRD-O7) and exact-match (PRD-F16, PRD-F20, PRD-F27, PRD-D19) rules need the field kinds declared. | must |
| PRD-D14 | A column with a search-value hook must be of string type; declaring a hook on another type raises `SchemaError`. | The hook transforms text the user types. | must |
| PRD-D15 | A categorical filter or `multi_values` list on a hooked column transforms each value and matches by equality. | Each value is stored transformed. | must |
| PRD-D16 | An empty alternative on a hooked column is not transformed; it means empty, as PRD-F26 defines it. | "Empty" is a property of the stored value, not text to transform. | must |
| PRD-D17 | A column search on a hooked column compares the whole search text, trimmed and transformed once, by equality, with no smart-search grammar; trimming and PRD-F7 apply as PRD-D12 says, and so does PRD-F14's 255-code-point bound, with no word cap on the hooked column's own column search. | As PRD-D12: a transformed value matches only as a whole. Owner's decision of 2026-11-19. | must |
| PRD-D18 | On a hooked column, SearchBuilder answers `=`, `!=`, `null` and `!null`; any other condition is refused with `invalid_filter`. | `null` and `!null` need no value, and equality compares the transformed whole; a range or substring of a transformed value means nothing. Owner's decision of 2026-11-19. | must |
| PRD-D19 | On a text-only OpenSearch string column (PRD-D13), a categorical filter, a `multi_values` list and the SearchBuilder conditions `=`, `!=`, `starts`, `!starts`, `ends`, `!ends`, `contains` and `!contains` are refused with `invalid_filter`; only the analysed smart search (global and column) and `null`/`!null` are answered. A hooked column cannot be text-only (PRD-D20). | Exact, prefix, suffix and substring matches need the unanalysed form, which a text-only column does not have; answering them on analysed tokens would return different rows than asked. | must |
| PRD-D20 | A hooked OpenSearch column (PRD-D9) must have a keyword field (`keyword` or `text+keyword`, PRD-D13); declaring a hook on a text-only column raises `SchemaError` at construction. | A hooked column compares by equality (PRD-D12, PRD-D15, PRD-D17, PRD-D18), which a text-only column cannot answer (PRD-D19); refusing the declaration leaves no request without a defined answer. | must |

### 4.3 Filtering and search (F)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-F1 | The global search is a smart search in which the row is the unit: every word must be found in at least one of the row's searchable columns, and different words may match different columns. Example: "red 7" finds a row whose colour is red and whose price is 7. | DataTables' smart search and SvelteDT's local data source behave so (its PRD-B21); a whole-text match in one column misses rows a user expects. | must |
| PRD-F2 | A column search is a smart search within that one column: every word must match in it. | One syntax for both search boxes, as in SvelteDT. | must |
| PRD-F3 | Words are separated by spaces. A phrase (PRD-F4) is read only where a word begins (at the start, after a space, or right after a phrase), and it ends at its closing quote: `"a" "b"x` gives `a`, `b` and `x`; `x"b"y` gives `xby`; `x"a b"` gives `xa` and `b`; `"a""b"` gives `a` and `b`. Words match in any order, and every word must match. | DataTables' smart search (READ, its 3.1.5 source: the split pattern at :5924; the examples MEASURED in node). | must |
| PRD-F4 | A phrase in straight double quotes, or in curly quotes (U+201C ... U+201D), is one word, which must sit inside one column. A phrase opened by one kind of quote and closed by the other is one word too, its space kept; in a positive word its straight quote is removed and its curly quote stays as text (`"a b”` searches `a b”`, `“a b"` searches `“a b`), and a negated mixed pair keeps its straight quote. Any other straight double quote in a positive word is removed (`5"` searches `5`; an unclosed `"abc` searches `abc`); a negated word keeps it (`!5"` excludes `5"`). An unclosed curly quote stays as text. | DataTables' smart search exactly (READ, DataTables 3.1.5 `dataTables.mjs`: the split pattern `!?["\u201C][^"\u201D]+["\u201D]\|[^ ]+` at :5924, which also takes a mixed pair; the quote stripping at :5936-5943, which needs a matching pair; a negated word recorded at :5948 before `word.replace(/"/g, '')` at :5953 runs on positive words; the negated mixed pair MEASURED in node). | must |
| PRD-F5 | `!word` excludes: in the global search, no searchable column of the row contains the word; in a column search, the column does not contain it. A NULL column contains no word, so it passes `!word` on every backend. | DataTables' `!`; a SQL `NOT (... LIKE ...)` drops NULL rows unless written for them (MEASURED on SQLite). | must |
| PRD-F6 | `!"phrase"` excludes the phrase; a token that reduces to the empty word (a lone `"`, `"""`, `!""`, `""`, a bare `!`, or a `!` word that PRD-F35 drops) adds nothing, and so does search text of spaces only. | DataTables reduces them to an empty word (READ, its 3.1.5 source); a user who types a space must not get no rows. | must |
| PRD-F7 | In search text (the global and column searches), LF, CR, U+2028 and U+2029 count as spaces; exact values (categorical, `multi_values`, SearchBuilder) are never rewritten. | SvelteDT's local source does so (its PRD-B21, difference 4); an exact value must stay the value sent. | must |
| PRD-F8 | No other syntax is read: `OR`, `NOT`, `-` and wildcard characters are ordinary text on every backend, OpenSearch analysed text and SearchBuilder "contains" included. | One syntax everywhere; a second syntax on one backend conflicts with `!` and surprises users. | must |
| PRD-F9 | Matching is case-insensitive on every backend, also under a case-sensitive collation where one is configured (SQL Server, MySQL); the one exception is that SQLite folds ASCII letters only. | Case-insensitivity is portable, and the spec picks each database's means; SQLite's LIKE folds ASCII only (MEASURED: `'ÄPFEL' LIKE '%äpfel%'` is false). | must |
| PRD-F10 | The library applies no accent folding and no dotted/dotless-i folding of its own; whether such letters match is the column's database collation. | Not portable without extensions the library does not create (non-goals, PRD-D10). | must |
| PRD-F11 | A word matches as a substring, except on a text-only OpenSearch column, where it is an analysed match (`red` does not find `reddish`; PRD-D19); LIKE wildcards (`%`, `_`, `[`, `\`) and OpenSearch wildcard characters in it are escaped. | A user's `%` or `*` is text, never a pattern; wildcard injection is a known failure. | must |
| PRD-F12 | Column searches combine with AND, with each other and with the global search. | DataTables' meaning. | must |
| PRD-F13 | A search over no usable column is refused (PRD-E1), never answered with every row. | An empty OR silently matching everything hides a mistake. | must |
| PRD-F14 | Search text holds at most 255 code points and 16 words; longer text is refused with `invalid_filter`. Words are counted after PRD-F4's split: a quoted phrase is one word, and a token that reduces to the empty word (a lone `"`, `"""`, `!""`, `""`, a bare `!`, or a `!` word that PRD-F35 drops) is not counted and adds nothing. The 16-word cap is lifted only where PRD-D12 and PRD-D17 say. | Each word adds a condition per searchable column; SvelteDT's field allows 255 characters. | must |
| PRD-F15 | A value holding a Unicode control character (category Cc) other than LF and CR is refused with `invalid_filter` before any query runs: the global search, a column search, a SearchBuilder value, a categorical value and a `multi_values` entry alike. | A NUL truncated a LIKE pattern on SQLite and matched wrong rows (MEASURED); SvelteDT refuses control characters at the field. | must |
| PRD-F16 | A categorical filter is a column search with `regex: true` whose regex is a single value or literal alternatives: `abc`, `a\|b`, `^a$` or `^(a\|b)$`, where no value holds an unescaped character of the set `. * + ? \| ( ) [ ] { } \ $ ^` (`\|` and the anchors serve only as structure; `-` and `/` are accepted escaped or not; only escapes of PRD-F19's set are read, so any other backslash escape such as `\d`, `\w` or `\n` is refused under PRD-F18); it matches the values exactly (an IN list). | DataTables' multi-select convention, as draft 1 accepted it; a single value sent as plain text would silently become a substring match ("red" finding "reddish"). | must |
| PRD-F17 | An empty alternative in a categorical filter means empty, as PRD-F26 defines it. | A frontend's "(empty)" option needs a value to send. | must |
| PRD-F18 | Any other regex, an unescaped metacharacter included, is refused. | Regular-expression search is a non-goal. | must |
| PRD-F19 | The recommended form for a sender is `^(v1\|v2)$` with each value escaped by DataTables' `util.escapeRegex` set: `/ . * + ? \| ( ) [ ] { } \ $ ^ -`. | Escaped values carry any text unambiguously (READ, the set in DataTables 3.1.5's source). | must |
| PRD-F20 | A `multi_values` list in a column's search object is accepted as the same exact match; an empty entry means empty, as PRD-F26 defines it. | An extension that needs no regex escaping, for clients that build the request themselves. | must |
| PRD-F21 | Dates are matched by day: `=`, `<`, `<=`, `>`, `>=` and `between` refer to whole days, for date and datetime columns alike. | A user filters by calendar day, not by instant. | must |
| PRD-F22 | SearchBuilder's "contains" on a date column, a criterion SearchBuilder itself does not send, and a column search on an OpenSearch date column, take a year, a year-month or a day (`YYYY`, `YYYY-MM`, `YYYY-MM-DD`) and mean that span, on every backend. | A partial date names a span; substring matching on date text depends on the database's date format. | must |
| PRD-F23 | On SQL, a number or a date column takes part in the global and column searches as text: a number as its canonical text, a date as `YYYY-MM-DD`; "7" finds 17. | As in SvelteDT's search text, which holds every column as text. | must |
| PRD-F24 | The maximum date (9999-12-31) as a bound is a user error, not a failed query. | Its next day does not exist, and a user can change it. | must |
| PRD-F25 | A number is parsed and bounded: a non-finite or non-numeric value is refused, and so is a value outside the column's SQL integer range. | A database error on an out-of-range number is a user mistake surfacing late. | must |
| PRD-F26 | "Empty" means NULL plus `''` on SQL string columns; on OpenSearch, a missing field. | Users do not tell NULL from an empty string. | must |
| PRD-F27 | SearchBuilder's string, number and date conditions are answered: `=`, `!=`, `<`, `<=`, `>`, `>=`, `between`, `!between`, `contains`, `!contains`, `starts`, `!starts`, `ends`, `!ends`, `null`, `!null`. | Typed comparisons and range filters for any DataTables 2.x client. | must |
| PRD-F28 | SearchBuilder groups nest and join their criteria with AND or OR; an empty group adds nothing. | SearchBuilder's tree. | must |
| PRD-F29 | A SearchBuilder condition that does not fit the column's type is refused with a reason. | A wrong condition is a user mistake. | must |
| PRD-F30 | A SearchBuilder criterion missing its values is refused. | A half-built criterion must not match everything. | must |
| PRD-F31 | An optional base filter (a SQL expression or an OpenSearch query) limits the rows the table shows at all, for `recordsTotal`, `recordsFiltered` and the page alike. | Row-level scoping belongs to the server, not the client. | must |
| PRD-F32 | The OpenSearch global search skips number and date columns. | OpenSearch cannot match a number or date as text without a script. | must |
| PRD-F33 | An OpenSearch column search on a number column takes one number and matches it exactly; several words, `!` or quotes are refused with `invalid_number`. | No text form to match a substring in; smart-search syntax has no meaning there. | must |
| PRD-F34 | An OpenSearch column search on a date column takes one date term (`YYYY`, `YYYY-MM` or `YYYY-MM-DD`); several words, `!` or quotes are refused with `invalid_date`. | A date span is one term; smart-search syntax has no meaning there. | must |
| PRD-F35 | `!` excludes a word only when the word, escaped by DataTables' `util.escapeRegex`, is longer than one character: `!-` and `!.` exclude, `!x` adds nothing. | DataTables' smart search exactly: it escapes the text first and negates a word only `if (word.length > 1)` (READ, DataTables 3.1.5 `dataTables.mjs` :5913 and :5948); the server and SvelteDT's local source share DataTables' grammar. | must |
| PRD-F36 | A SearchBuilder criterion carrying more values than its condition takes is refused with `invalid_filter`. | Silently using the first value answers a different question than asked, as a missing value would (PRD-F30). | must |
| PRD-F37 | NULL satisfies every negative SearchBuilder condition (`!=`, `!contains`, `!starts`, `!ends`, `!between`) on every backend. | A NULL value is not equal to, does not contain, start or end with, and is not between anything, as `!word` passes NULL (PRD-F5); DataTables' client-side SearchBuilder agrees for strings and dates (section 5). | must |
| PRD-F38 | A SearchBuilder tree nests at most 8 groups deep and holds at most 64 criteria in all, and a categorical filter or `multi_values` list holds at most 256 values; beyond any bound the request is refused with `invalid_filter`. | Every criterion and value is query work, and walking the tree must never recurse without bound. | must |
| PRD-F39 | A column search holding both a value and a non-empty `multi_values` list is refused with `invalid_filter`. | One of the two would be dropped silently. | must |
| PRD-F40 | A global search carrying a `multi_values` list is refused with `invalid_filter`; an empty list is idle. | `multi_values` belongs to a column search; on the global search the list would be dropped silently. | must |

### 4.4 Ordering (O)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-O1 | An order on an index outside the request's columns is refused (PRD-E1). For a column in the request, the request's `orderable` and the server's declaration decide together: request orderable and server unorderable, the order is refused (PRD-E1); request unorderable and server orderable, the order is answered; both unorderable, the entry is skipped. An entry on a nameless column is skipped (PRD-P21). | The server decides what can be sorted. DataTables sends its default order `[[0, 'asc']]` even when that column is `orderable: false` (READ, DataTables 3.1.5 `dataTables.mjs`: `sortFlatten` ignores `orderable` at :4794-4836, and the request sends `orderable` at :5631), and its own client sorts by such a column; refusing would fail a table whose first column is not orderable on its first request. | must |
| PRD-O2 | NULL is the lowest value: first ascending, last descending, on every backend. | One answer on every database (their defaults differ: PostgreSQL puts NULL last ascending), as SvelteDT's local source sorts empty values first. | must |
| PRD-O3 | After the user's order, a tie-breaker on a unique key (the primary key, or the key PRD-D11 declares) follows the last sort column's direction, so descending is the exact reverse of ascending. | Pages are deterministic, a tie never splits across a page boundary, and reversing the order reverses the rows. | must |
| PRD-O4 | With no order in the request, the rows come in the tie-breaker's ascending order. | A stable first page. | must |
| PRD-O5 | A text-only OpenSearch string column (PRD-D13) is unorderable by default, so an order on it is refused (PRD-O1); declaring it orderable raises `SchemaError` at construction. | OpenSearch rejects the whole search on an analysed text sort; a silent drop would answer a different order than asked. | must |
| PRD-O6 | Text order is the database's collation; the library adds no collation of its own. | Portable and predictable per database; see the non-goals. | must |
| PRD-O7 | OpenSearch sorts a string column on its declared keyword field (PRD-D13). | Sorting needs the unanalysed form. | must |

### 4.5 Counts and paging (C)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-C1 | `recordsTotal` counts the rows under the base filter. | DataTables' meaning; SvelteDT shows it as the pager's unfiltered total (its PRD-B26). | must |
| PRD-C2 | `recordsFiltered` counts the rows after every filter. | DataTables' meaning; SvelteDT's table and pager total. | must |
| PRD-C3 | A `start` at or past the end answers an empty page with both counts; this wins over PRD-C12. | The client recovers by paging back; an error would hide the counts. | must |
| PRD-C4 | SQL counts with `COUNT(*)`. | Exact counts. | must |
| PRD-C5 | SQL pages with OFFSET/LIMIT. | Every supported dialect has it. | must |
| PRD-C6 | SQL selects only the declared columns. | Undeclared columns never leave the database. | must |
| PRD-C7 | OpenSearch returns the page and the exact filtered count in one search. | One round trip where possible. | must |
| PRD-C8 | On OpenSearch, `recordsTotal` reuses the filtered count when nothing beyond the base filter applies. | The second count would give the same number. | must |
| PRD-C9 | On OpenSearch, a count that is only a lower bound is replaced by an exact count. | Never an approximate total. | must |
| PRD-C10 | An OpenSearch page is served when `start + length <= max_result_window` (the ordinary way) or `recordsFiltered - start <= max_result_window` (by reversing the sort and the page). `max_result_window` defaults to 10,000 and can be set per table. | "Last page" on a large index is an ordinary press in SvelteDT's pager; the reverse sort reaches it within the window. | must |
| PRD-C11 | To decide PRD-C10, the filtered count, plus the total count when PRD-C8 does not reuse it, may run before the page query; these are the only queries that may run before a refusal. | Telling a near-end page from a middle one needs the total. | must |
| PRD-C12 | Any other OpenSearch page beyond `max_result_window` is refused with `page_out_of_range`, and the answer carries the real `recordsTotal` and `recordsFiltered`. | OpenSearch would reject it; the counts let a client offer a way back (SvelteDT's helper may or may not use them). | must |
| PRD-C13 | A reverse-sorted page may drift if the index changes between the count and the page, as offset paging always can; no point-in-time is held. | A point-in-time per request costs a server resource for a drift offset paging already has. | must |
| PRD-C14 | `recordsTotal` can be cached by a caller-supplied TTL cache. | An unfiltered count of a large table is costly. | must |
| PRD-C15 | Two base filters that differ only in their bound values (e.g. per tenant) never share a cached total: the cache key covers the table, the base filter with its bound values, and the database or index, or the app supplies the key. | A shared total would show one tenant another's count. | must |
| PRD-C16 | A cached total ends by its TTL or by an explicit call; the library registers no global hooks on engines or pools. | Global hooks would surprise an app. | must |
| PRD-C17 | On SQL Server, an IN list that would exceed the 2100-parameter limit is sent as one JSON parameter (`OPENJSON`); other dialects, SQLite included, bind normally. | Large categorical filters must not fail on a driver limit. | must |

### 4.6 Errors (E)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-E1 | A request the user can fix is answered, not raised: the response carries an `error` text and an `error_code`. | The user changes the filter; the app needs no error handling for it. | must |
| PRD-E2 | The user-error codes are `invalid_filter`, `invalid_date`, `invalid_number` and `page_out_of_range`, documented and stable. | A frontend maps codes to its own messages; SvelteDT can show the reason at the field. | must |
| PRD-E3 | The code follows the first mistake in this order: (1) a control character (PRD-F15), text over the bounds (PRD-F14) or a structure over the bounds (PRD-F38) is `invalid_filter`; (2) a value whose format the column cannot read is `invalid_date` or `invalid_number`, including several words, `!` or quotes on an OpenSearch date column (PRD-F34) or number column (PRD-F33); (3) a readable value outside the range is `invalid_date` for the maximum date (PRD-F24) and `invalid_number` outside the integer range (PRD-F25); (4) every other filter error is `invalid_filter`; (5) only then, a page of PRD-C12 is `page_out_of_range`, since it needs a valid count. | One code per kind of mistake, decidable when a request holds several. | must |
| PRD-E4 | An `error` text is fixed per case apart from declared names, and never quotes the user's input. | Echoing input invites injection into logs and pages. | must |
| PRD-E5 | `query_failed` is set only by an app, through `DTResponse.query_failed(draw)`, the conventional shape for a failure it catches. | The library never answers its own failures. | must |
| PRD-E6 | A structural error raises `MalformedRequest`, never `invalid_filter`. | One the integrator fixes, the other the user. | must |
| PRD-E7 | Every user mistake is found while parsing, planning or building the query, before the page is queried (the count of PRD-C11 may run first). | A filter error is never a half-run query. | must |
| PRD-E8 | Everything else raises: a database error, a schema the table lacks, a partial OpenSearch answer. | A server failure is not the user's to fix and must not look like an empty result. | must |
| PRD-E9 | An OpenSearch answer that does not cover every shard, or whose shard information cannot be read, raises `PartialResultError`. | A partial answer is never shown as complete. | must |
| PRD-E10 | Logs never contain search values or client-supplied names. | Search text can be personal data; client names are untrusted. | must |
| PRD-E11 | A refused name is logged only if the server declared it. | A declared name is the server's own text. | must |
| PRD-E12 | Logging uses the standard `logging` module under the logger `datatables_backend`. | Apps route it like any library's logs. | must |

### 4.7 Rows and the response (R)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-R1 | Each row is a dict keyed by the client-facing names. | The client never sees backend field names. | must |
| PRD-R2 | An optional row hook can reshape a row (e.g. through a Pydantic model). | Apps shape rows without post-processing the response. | must |
| PRD-R3 | An optional hook adds `DT_RowId` to a row. | DataTables' row id. | must |
| PRD-R4 | An optional hook adds `DT_RowData` to a row. | DataTables' row data attributes. | must |
| PRD-R5 | `DTResponse` has exactly `draw`, `recordsTotal`, `recordsFiltered` and `data`, plus `error` and `error_code` when set. | DataTables' response, extended only by the error code; an unused field invites drift. | must |
| PRD-R6 | `draw` echoes the request's `draw`. | DataTables drops an answer whose `draw` is not the latest. | must |

### 4.8 API (A)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-A1 | A table object holds only the server-side declaration: the source, the columns, the base filter and the hooks. | No request state leaks between requests. | must |
| PRD-A2 | A table object is built once and reused across requests. | Declarations are configuration. | must |
| PRD-A3 | Constructors do no I/O. | Safe at import time. | must |
| PRD-A4 | `await table.fetch(params, conn)` answers one request. | Async apps. | must |
| PRD-A5 | `table.fetch_sync(params, conn)` answers one request. | Sync apps. | must |
| PRD-A6 | The page query is built entirely before anything runs, apart from the count of PRD-C11. | With PRD-E7, a filter error is never a half-run query. | must |
| PRD-A7 | SQL: the table takes a SQLAlchemy `FromClause` (a table, join or subquery) and accepts an `AsyncSession` or `AsyncConnection` (async) and a `Session` or `Connection` (sync). | What SQLAlchemy apps hold. | must |
| PRD-A8 | OpenSearch: the table takes an index name and accepts an `AsyncOpenSearch` or `OpenSearch` client; queries are plain request bodies, with no DSL dependency. | No extra dependency beyond the client. | must |
| PRD-A9 | An optional FastAPI dependency parses the request from a non-empty body when there is one, otherwise from the query string; the two are never merged. A body that decodes to a JSON object is read as JSON whatever its Content-Type; any other body is read as a form. | DataTables appends its cache-buster `_` to the query string of every request, POST included (READ, DataTables 3.1.5 `dataTables.mjs`: `queryParams` adds `_` for any method when `cache === false` at :826, and its server-side request sets `cache: false` at :5550), so a non-empty query string does not mean the request is there; merging would mix two sources. DataTables 3.1.5's `submitAs: 'json'` keeps the form Content-Type unless one is set (READ: its `ajax()` sets JSON only when `!options.contentType`, and its defaults carry form-urlencoded), so choosing the parser by Content-Type would read the JSON as a form. | must |
| PRD-A10 | The FastAPI dependency answers 422 on `MalformedRequest`. | The structural/user split of PRD-E6. | must |

### 4.9 Packaging and quality (Q)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-Q1 | The distribution is `datatables-backend` and the import `datatables_backend`. | One name. | must |
| PRD-Q2 | Extras are `[sqlalchemy]`, `[opensearch]` and `[fastapi]`. | One install per need. | must |
| PRD-Q3 | The core (`protocol`, `columns`, `_plan`, `rows`, `cache`) imports only Pydantic and the standard library; each backend imports only its own extra; `sqlalchemy.ext.asyncio` is imported only for type checking. | A sync-only or single-extra install must work; `sqlalchemy.ext.asyncio` needs `greenlet`. | must |
| PRD-Q4 | Python 3.12 or later, tested on 3.12 to 3.15. | The code uses PEP 695 syntax; 3.10 reached end of life in 2026-10. | must |
| PRD-Q5 | Pydantic 2.10 or later; 2.12 or later on Python 3.14; 2.14 or later on Python 3.15. | 2.7 to 2.9 fail on the PEP 695 aliases; 2.12 and 2.14 are the first with a wheel for 3.14 and 3.15 (MEASURED). | must |
| PRD-Q6 | SQLAlchemy 2.0.31 or later. | 2.0.30 fails to import on Python 3.13 (MEASURED). | must |
| PRD-Q7 | opensearch-py 2.6 or later. | The oldest tested. | must |
| PRD-Q8 | FastAPI 0.133 or later and python-multipart 0.0.9 or later. | FastAPI 0.133 is the first without a `starlette<1.0` cap (READ, PyPI metadata). | must |
| PRD-Q9 | Each floor is tested on every supported Python. | A floor never tested is a guess. | must |
| PRD-Q10 | Fully typed (`py.typed`), checked with basedpyright and ruff. | Users' type checkers see the real API. | must |
| PRD-Q11 | Tests go through the public API (`parse_request`, then `fetch` or `fetch_sync`): SQL on SQLite (sync and aiosqlite) by default, PostgreSQL, SQL Server and MySQL in CI containers, OpenSearch on the request bodies built and against a container. | Behaviour, not implementation, is what users rely on; dialect differences only show on real databases, and the code already has a MySQL path. | must |
| PRD-Q12 | CI runs lint, types, tests on every supported Python, the minimum versions, and an install-isolation check of the built wheel; one required `gate` check blocks merges to main. | Every claim above is checked on every change. | must |
| PRD-Q13 | MIT licence. | Permissive; the repository's `LICENSE`. | must |

### 4.10 Later (L)

| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-L1 | An Elasticsearch backend behind the same contract. | Same users as OpenSearch; the 8.x/9.x clients changed the `body=` API, so it needs a thin client adapter. | later |
| PRD-L2 | SearchPanes and ColumnControl. | DataTables extensions with their own request parts; planned for a milestone after 1.0, when a frontend needs them. | later |
| PRD-L3 | A `boolean` declared column type, where `true` and `false` (case-insensitive) match and other text is refused with `invalid_filter`; until then, the spec states today's behaviour for boolean columns. | Boolean columns are common; owner's decision of 2026-11-19. | later |

## 5. Stated differences

The server cannot give every answer SvelteDT's local source or DataTables' client side gives, and the backends differ
where a database decides. Each difference is stated here; every other answer is the same.

| Difference | Between | Requirement |
|---|---|---|
| Text order is the database's collation, not an ICU collator's digit-group order with case, width and kana ties. | Server and SvelteDT's local source (its PRD-B21) | PRD-O6 |
| Accent folding is the database's; DataTables' client folds accents by default, as does SvelteDT's local source. | Server, and DataTables' client and SvelteDT's local source | PRD-F10 |
| Dotted and dotless i match only where the database's collation makes them match; SvelteDT treats them alike in every locale (its PRD-B21, difference 3). | Server and SvelteDT's local source | PRD-F10 |
| On SQLite, case folds for ASCII letters only. | SQLite and every other backend | PRD-F9 |
| A categorical filter matches values exactly; DataTables' client reads the same value as an unanchored regex, a substring. The server sides with SvelteDT, whose categorical filter is exact (its PRD-B7). | Server and DataTables' client | PRD-F16, PRD-F17 |
| On OpenSearch a stored `""` is not missing, so not empty; on SQL it is empty. | SQL and OpenSearch | PRD-F26 |
| A hooked column matches whole values by equality; SvelteDT's local source matches substrings. | Server and SvelteDT's local source | PRD-D12, PRD-D17 |
| LF, CR, U+2028 and U+2029 count as spaces in search text; DataTables' client splits words on spaces only. The server follows SvelteDT (its PRD-B21, difference 4). | Server and DataTables' client | PRD-F7 |
| On SQL, number and date columns take part in the global and column searches as text; OpenSearch's global search skips them. | SQL and OpenSearch | PRD-F23, PRD-F32 |
| An OpenSearch number column search is an exact match; on SQL it matches the number's text. | SQL and OpenSearch | PRD-F23, PRD-F33 |
| An OpenSearch date column search takes one date term; on SQL it is a smart search of the date's text. | SQL and OpenSearch | PRD-F22, PRD-F23, PRD-F34 |
| DataTables' client-side SearchBuilder reads an empty number as 0 (`+''`), so there NULL fails `!= 0` and `!between` over a range holding 0; the server lets NULL satisfy every negative condition. | Server and DataTables' client-side SearchBuilder (READ, SearchBuilder 2.1.1 `numConditions`, its `!=` and `!between`) | PRD-F37 |
| A page beyond `max_result_window` that is not near either end is refused on OpenSearch; SQL serves every page. | SQL and OpenSearch | PRD-C12 |
| A text-only OpenSearch string column cannot be ordered; every SQL string column can. | SQL and OpenSearch | PRD-O5 |
| A text-only OpenSearch string column answers only the analysed search and `null`/`!null`; SQL answers every condition. | SQL and OpenSearch | PRD-D19 |
| Search text of more than 16 words is refused; SvelteDT's local source answers it. | Server and SvelteDT's local source | PRD-F14 |
| On a text-only OpenSearch string column a word is an analysed match (`red` does not find `reddish`); SQL matches substrings. | SQL and OpenSearch | PRD-F11 |

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

Before 1.0: v0.1 (core, SQL, OpenSearch, FastAPI adapter, SQLite tests) is published to reserve the name; v0.2 adds
the container tests and an acceptance suite.

## 7. Changes from draft 1

- Search: draft 1's global search matched the whole text in one column (OR across columns) and OpenSearch text took
  `OR`/`NOT`/`-`; now smart search with the row as the unit and one syntax (PRD-F1 to PRD-F8, PRD-F35), case-insensitive
  everywhere but SQLite's non-ASCII (PRD-F9), with bounds (PRD-F14) and control characters refused (PRD-F15); number and
  date columns in search are stated per backend (PRD-F23, PRD-F32 to PRD-F34).
- Categorical: draft 1's forms kept, with the refused character set named, the empty alternative (PRD-F17) and a
  recommended sender form (PRD-F19).
- SearchBuilder: a criterion with too many values is refused (PRD-F36), and NULL satisfies every negative condition
  (PRD-F37).
- Ordering: NULL placement was each database's default and the SQL tie-breaker always ascended; now NULL lowest and the
  tie-breaker follows the last direction (PRD-O2, PRD-O3); a source without a primary key declares a unique key
  (PRD-D11); an order on analysed-only text is refused instead of dropped (PRD-O5); an order the request marks
  unorderable is answered or skipped instead of refused, so DataTables' default order never fails a first request
  (PRD-O1); text order is stated as the database's (PRD-O6).
- Paging: `start` up to 2^53-1 (PRD-P10); OpenSearch pages near the end served by reverse sort, middle pages refused
  with `page_out_of_range` and the real counts instead of `invalid_filter` (PRD-C10 to PRD-C13); a start past the end
  answers an empty page with counts (PRD-C3); SQLite no longer listed as binding IN lists as JSON (PRD-C17).
- API: a non-empty body is read instead of the query string, and a body that decodes to a JSON object is read as JSON
  whatever its Content-Type (PRD-A9, changed from draft 1's R8); the parser reads multi-dicts (PRD-P1).
- Errors: `invalid_date`, `invalid_number` and `page_out_of_range` beside `invalid_filter`, with a precedence (PRD-E2,
  PRD-E3); the response's unused `metadata` field goes (PRD-R5).
- New: a request with no DataTables parameter refused (PRD-P19), an integer `data` (PRD-P20), a `null` `data` and
  nameless columns (PRD-P21), declared OpenSearch field kinds (PRD-D13), the hook-column rules (PRD-D14 to PRD-D18), the
  text-only OpenSearch refusals (PRD-D19), a hooked OpenSearch column's keyword field (PRD-D20), the structure bounds
  (PRD-F38), a value with `multi_values` refused (PRD-F39), a global search with `multi_values` refused (PRD-F40), the
  dotted form, the ignore rules and the refusal of filters the library does not answer (PRD-P2 to PRD-P5, PRD-P7),
  object `data` (PRD-P18), `order[i][name]` (PRD-P16), expression columns (PRD-D10), the floors (PRD-Q4 to PRD-Q9),
  MySQL in the container tests (PRD-Q11) and CI (PRD-Q12).
- Owner's decisions of 2026-11-19: a column with a search-value hook takes equality only (PRD-D12, PRD-D17, PRD-D18); a
  `boolean` column type later (PRD-L3).

## 8. Open questions for the owner

1. OpenSearch middle pages: should a sequential `search_after` route, or raising `max_result_window` per index, remove
   the limit of PRD-C12? SvelteDT's pager can reach any page, so users will hit it by an ordinary press.
2. Which HTTP status does an answered user error (PRD-E1) carry: 200 with `error_code`, as DataTables expects, or a
   4xx the app chooses? SvelteDT must not show a refusal as "No data" (its PRD-B15).
3. A column the request marks searchable but the server did not declare: refuse the global search (today), or leave
   that column out of it?
4. Does a 422 for `MalformedRequest` need a body carrying `draw`, for a client that matches answers by `draw`?
5. Should the total cache offer opt-in invalidation on writes? It stays out until a user needs fresher totals than a
   TTL gives.
6. The SQLite `json_each` compiler for IN lists is unreached (only SQL Server uses the JSON form): remove it unless a
    statement needs it.

**Governance.** A new user-error code (PRD-E2) is added only by the owner, since frontends depend on the set.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Smart search per word multiplies the LIKE terms (words x columns) on large tables. | PRD-F14 bounds the words; the container tests measure PostgreSQL, SQL Server and MySQL plans. |
| Accent folding and text order differ per database, so server and local results differ. | Section 5 states it; the specs state each database's behaviour; PRD-D10 gives apps their database's means. |
| Reverse-sort paging (PRD-C10) is wrong under ties or a changing index. | It depends on PRD-O3's unique tie-breaker and PRD-C13, and needs the container proof (section 6). |

## 10. Trace from draft 1

| Draft 1 | Here |
|---|---|
| R1 request parsing (JSON or bracket form) | PRD-P1, P2 |
| R1 typed models | PRD-P17 |
| R1 `MalformedRequest`, message names the parameter | PRD-P6, P12 |
| R1 malformed: wrong structure, non-integer paging, negative start, length, numbers above 2^31-1, lone surrogates | PRD-P6, P8, P9, P13, P10 (start raised to 2^53-1), P11 |
| R1 "true"/"false", name falls back to data | PRD-P14, P15 |
| R2 column declaration and its parts | PRD-D1 to D5, D9 |
| R2 undeclared names refused; declared type decides; SQL inference | PRD-D6, D7, D8 |
| R3 order index refused / unorderable | PRD-P16 (index authority), PRD-O1 |
| R3 SQL tie-breaker on the primary key | PRD-O3 (now direction-following), O4, D11 |
| R3 OpenSearch keyword sort; analysed text dropped with a warning | PRD-O7, PRD-O5 (now refused), PRD-D13 |
| R4 global search, column searches, "contains" | PRD-F1 to F3 (changed), F9, F12, F13, F11 |
| R4 dates (SQL date text; OpenSearch global search skips dates) | PRD-F21, F22, F23, F24, F32 |
| R4 numbers, "empty" | PRD-F25, F26 |
| R4 SearchBuilder | PRD-F27 to F30 |
| R4 multi-select, `multi_values` | PRD-F16, F18, F20 |
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
| R6 SQL Server parameter limit | PRD-C17 (SQLite JSON binding dropped) |
| R7 rows and response | PRD-R1 to R6 |
| R8 API shape | PRD-A1 to A10 (A9 changed) |
| R9 distribution, extras | PRD-Q1, Q2 |
| R9 Pydantic-only core (from the project rules) | PRD-Q3 |
| R9 Python, floors | PRD-Q4 to Q9 (floors raised) |
| R9 typed, tests, licence | PRD-Q10, Q11, Q13 |
| Milestones v0.1, v0.2 | Section 6, before 1.0 |
| Milestone "later" | PRD-L1, L2 |
| Open questions (Elasticsearch adapter, write invalidation) | PRD-L1; section 8, question 5 |
