# Specs: parsing (P)

Statements for PRD section 4.1. Form, tags, answers, the base request and the `items` fixture: see `README.md`.
Statements tagged `[core]` check `parse_request` alone; those tagged `[all]` also fetch and name rows.

- **SPEC-P1.1** [core] The base request as a nested JSON object, as a plain mapping of flat keys, as a multi-dict
  (`getlist`), and as a list of key-value pairs parses to equal `DTParams`.
- **SPEC-P1.2** [core] A multi-dict holding `columns[0][search][multi_values][]` twice, with `red` and `blue`, reads
  both values; so does a pair list repeating the key.
- **SPEC-P1.3** [core] The request DataTables builds (`ajaxParameters`, dataTables.mjs:5618-5681) for four columns: an
  integer `data` (the default, the column's index), a name, an object and `null`; booleans; `fixed`, `groups` and
  `groupsFixed` as empty arrays; an order entry with its `name`: as a JSON object, and as the form DataTables writes
  from it (`String(n)`, `true`/`false`, `null` as empty), parses to equal `DTParams`, and nothing in it is refused.
- **SPEC-P2.1** [core] `columns[0][search][value]`, `columns[0].search.value`, `columns.0.search.value` and
  `columns[0].search[value]` parse to the same request.
- **SPEC-P3.1** [core] The base request plus `_`, `searchPanes_options`, `searchPanesLast` and `myAppKey` (flat and
  in a JSON body) parses to the same request as without them.
- **SPEC-P3.2** [core] A flat key `_[x]` or `myApp.x` is ignored, though its shape is not a request parameter's.
- **SPEC-P4.1** [core] Unknown fields `columns[0][foo]` and `search[smart]` are ignored; the request parses as
  without them.
- **SPEC-P4.2** [core] A SearchBuilder criterion with `value1` and `value2` beside `value` parses to the same
  request as without them.
- **SPEC-P5.1** [core] A non-empty `search[fixed]`, `search[groups]`, `search[groupsFixed]` or
  `columns[0][search][fixed]` → `MalformedRequest`; an empty one (JSON `[]`, or the key absent in a form) parses.
- **SPEC-P5.2** [core] `searchPanes[colour][0]=red` → `MalformedRequest`; `searchPanes` with only empty panes (JSON
  `{"colour": []}`), and an empty `searchPanes_null` (JSON `{}`), parse.
- **SPEC-P5.3** [core] An idle ColumnControl search (an empty value and a logic other than `empty` and `notEmpty`:
  `contains`, `starts`, `greater`, or none), the date control's idle shape (`type` `date`, `mask` empty) and a list
  `{}` parse, as JSON and as a form, and add nothing: the request equals the same one without them in everything but
  its `columnControl` fields.
- **SPEC-P5.4** [core] An active ColumnControl search (`contains` with a value; `empty`; `notEmpty`; `greater` with a
  value) and a list (JSON `{"0":"a","1":"b"}`; form `[list][0]=a&[list][1]=b`; a JSON array `["a","b"]` is the same
  list) parse and are kept, not dropped, and the JSON and the form give equal `DTParams`.
- **SPEC-P5.5** [core] A ColumnControl search whose logic is outside the set (`like`, `Contains`), with a value and with
  none, as JSON and as a form, raises `MalformedRequest` with the message `Invalid columns parameter: a ColumnControl
  search sent a logic the server does not answer. Offer only contains, notContains, equal, notEqual, starts, ends,
  empty and notEmpty (a number control also greater, greaterOrEqual, less and lessOrEqual), or remove the control from
  the table.` and nothing the client sent.
- **SPEC-P5.6** [core] A list entry `true`, `null`, `1.5` or `{}` raises `MalformedRequest` with the message `Invalid
  columns parameter: a ColumnControl list holds a value that is neither text nor a whole number. Give each option a text
  or whole-number value.`; the entry `1`, as a JSON number and as form text, reads as the text `1`.
- **SPEC-P5.7** [core] An active ColumnControl search with `type` `date` (a value, `empty` or `notEmpty`), as JSON and
  as a form, raises `MalformedRequest` with the message `Invalid columns parameter: ColumnControl's date search is not
  answered. Remove the date control from this column, or give the column a text search control.`; the idle date shape
  parses (`SPEC-P5.3`).
- **SPEC-P5.8** [core] A ColumnControl search with a value and no logic (an empty logic, or none sent) is refused, as
  JSON and as a form, where one with an empty value is idle (`SPEC-P5.3`): `MalformedRequest` with the message `Invalid
  columns parameter: a ColumnControl search sent a value without a logic. Send the control's logic with its value.`
- **SPEC-P5.9** [core] A ColumnControl list given as an object is read by sorting its keys, never by building a list as
  long as the largest: the JSON `{"0":"a","999999999":"b"}` reads as `a`, `b` at once (a gap is closed up). A key that
  is no index (`x`, `1.5`, empty, `-1`, `01`, ten digits), and a list that is neither an array nor an object, raise
  `MalformedRequest` with the message `Invalid columns parameter: a ColumnControl list is neither an array nor an
  object keyed by whole-number indexes. Send an array, or an object keyed 0, 1, 2 and so on.` A form whose indexes have
  a gap is `MalformedRequest` as for every index (`SPEC-P6.2`), at once.
- **SPEC-P6.1** [core] A decoded JSON list of objects or of numbers (`[{"draw": 1}]`, `[1]`) → `MalformedRequest`
  (a list of key-value pairs is valid, PRD-P1); `columns` as text → `MalformedRequest`;
  `columns[0][searchable]` as an object → `MalformedRequest`; `columns[0][data]` JSON `null` parses (PRD-P21).
- **SPEC-P6.2** [core] Flat keys `columns[0][data]` and `columns.0.data` with different values → `MalformedRequest`;
  `columns[0]` and `columns[2]` with no `columns[1]` → `MalformedRequest`.
- **SPEC-P6.3** [core] A flat value that is a dict, or a flat key that is not text (an int key in the mapping), →
  `MalformedRequest`.
- **SPEC-P6.4** [core] A SearchBuilder condition `without` or `regex` → `MalformedRequest`.
- **SPEC-P6.5** [core] `order[0][dir]` other than exactly `asc` or `desc` (`up`, `ASC`, or empty) →
  `MalformedRequest` (the reviewer's decision of 2026-11-20 in `README.md`).
- **SPEC-P6.6** [core] A SearchBuilder group whose `logic` is neither `AND` nor `OR` (for example `XOR`) →
  `MalformedRequest` with the message `Invalid searchBuilder parameter.`: ColumnControl's `logic` is a field of its own,
  so its refusal texts leave this one unchanged.
- **SPEC-P7.1** [core] On a request whose only column is `columns[0]`, a flat key at the next free index in a bad
  form, where a lenient parser would read it as column 1, → `MalformedRequest`: `columns[01][data]` and
  `columns[١][data]` (U+0661, an Arabic-Indic one). So do `columns[1234567890][data]` (10 digits),
  `columns[<5000 ASCII digits>][data]` (a `MalformedRequest`, not a `ValueError` from converting the index),
  `columns[0.x][data]`, `columns[0]x` and `columns[0][data][]x`.
- **SPEC-P7.2** [core] `columns[0][search][multi_values][]` repeated appends its values; `columns[][data]` (`[]` in
  place of an index) → `MalformedRequest`.
- **SPEC-P8.1** [core] `draw`, `start`, `length` and `order[0][column]` as the form text `1.0`, ` 1`, `+1`, `1e0`, `abc`
  or empty, or as `U+0661` (an Arabic-Indic digit one), as the JSON number `1.0` or `1.5`, → `MalformedRequest` naming
  that parameter.
- **SPEC-P8.3** [core] The same four as the JSON integer 1, as the form text `1`, and as the form text `01`, parse.
- **SPEC-P8.2** [core] The JSON boolean `true` as `draw`, `start`, `length` or `order[0][column]`, and `false` as
  `start`, → `MalformedRequest` naming that parameter (read as integers they would be 1 and 0).
- **SPEC-P9.1** [core] `start` `-1` → `MalformedRequest`; `start` `0` parses.
- **SPEC-P9.2** [core] `draw` `-1` → `MalformedRequest`; `draw` `0` parses.
- **SPEC-P10.1** [core] `start` 9007199254740991 parses; 9007199254740992 → `MalformedRequest`.
- **SPEC-P10.2** [core] `draw` 2147483647 parses; 2147483648 → `MalformedRequest`.
- **SPEC-P11.1** [core] A lone surrogate U+D800 in `search[value]` (the JSON escape `\ud800`) or in a column's
  `data` → `MalformedRequest`.
- **SPEC-P11.2** [core] A valid surrogate pair in `search[value]` (the JSON escape `\ud83d\ude00`) is read as the one
  character U+1F600; the request parses.
- **SPEC-P11.3** [core] A lone surrogate U+D800 in a column search value, in a SearchBuilder criterion's value and in a
  `multi_values` entry → `MalformedRequest`.
- **SPEC-P12.1** [core] For `columns[0][searchable]` sent with the value `MARKER_V`, once under the bracket key
  `columns[0][searchable]` and once under the dotted key `columns.0.searchable`, the `MalformedRequest` message names
  the parameter `columns` and contains neither `MARKER_V` nor either form of the key; an `order` index outside the
  columns names `order`.
- **SPEC-P12.2** [core] The `MalformedRequest` message of a refused number, boolean, `dir` or `data` names the
  parameter and says what to send: `Invalid draw parameter: send a whole number, as DataTables does.`, `Invalid start
  parameter: send a whole number from 0 to 9007199254740991.`, `Invalid columns parameter: searchable must be true or
  false.` (`orderable`, `regex`, and for `data` text, a whole number, an object or null), `Invalid order parameter:
  dir must be asc or desc.`; an order index outside the columns says it must name one of the request's columns. A
  field is named only from the protocol's own, never a client's key, and the value is never shown.
- **SPEC-P13.1** [core] `length` 1 and 1000 parse; 0, -1 and 1001 → `MalformedRequest`; with the maximum set to 50,
  51 → `MalformedRequest`.
- **SPEC-P14.1** [core] `columns[0][searchable]`, `columns[0][orderable]`, `columns[0][search][regex]` and
  `search[regex]` as the JSON booleans, and as the text `true` and `false` (form and JSON), read as `True` and
  `False`; as `"yes"`, `"True"`, `"TRUE"`, `"on"`, `"1"`, the empty text, the JSON integers 0, 1 and 2, or `null`, →
  `MalformedRequest` (naming `columns`, or `search` for the global `regex`).
- **SPEC-P15.1** [all] A column with `data` `name` and an empty `name` is the declared column `name`: a column search
  `pear` on it → rows 2.
- **SPEC-P15.2** [all] A request column is the declared column whose name equals its `name`, or its `data` when
  `name` is empty, compared exactly: another case (`Name`), a space after it (`name `), or the source field of a
  renamed column name no declared column, so a column search with a value on each → error_code `invalid_filter`.
  With `name` `price` and `data` `id`, the column is `price`: an order asc on it → rows 4, 1, 3, 2.
- **SPEC-P16.1** [all] `order[0][column]` 3 with `order[0][name]` `id`, on a request whose column 3 is `price`,
  orders by `price`: asc → rows 4, 1, 3, 2 (the index decides).
- **SPEC-P17.1** [core] `parse_request` returns a `DTParams` whose columns are `DTColumn`, orders `DTOrder`, search a
  `Search`, and SearchBuilder a tree of `SearchGroup` and `SearchCriterion`.
- **SPEC-P18.1** [all] A column whose `data` is `{"_": "name", "sort": "name"}` and `name` `name` is the declared
  column `name` (a column search `pear` on it → rows 2); with an empty `name` it is nameless (PRD-P21), so a column
  search `pear` on it → error_code `invalid_filter`.
- **SPEC-P19.1** [core] An empty request, and a DataTables 1.9 request (`sEcho`, `sSearch`, `iDisplayStart`), →
  `MalformedRequest`.
- **SPEC-P18.2** [core] A column whose `data` is `{"_": "x", "display": "y"}`, as JSON and as `columns[0][data][_]`,
  parses; its `data` is empty text, and its key is its `name` (empty when it has none).
- **SPEC-P20.1** [core] `columns[0][data]` as the JSON integer 0 parses to a column whose `data` is `"0"`.
- **SPEC-P20.2** [all] With `Column('0', field='id', type='number')` declared and `columns[0][data]` the JSON
  integer 0: a column search `3` on it → rows 3.
- **SPEC-P21.1** [all] A nameless column (`data` null, empty `name`) first, with DataTables' default order on column
  0 and a global search `red` → rows 1, 2, 4, no error.
- **SPEC-P21.2** [all] A column with `data` `function` and an empty `name` is nameless: an order on it is skipped
  (rows 1, 2, 3, 4); with `Column('function', field='id', type='number')` declared, it is that column: an order
  desc on it → rows 4, 3, 2, 1.
- **SPEC-P21.3** [all] A column search with value `x` on a nameless column → error_code `invalid_filter`.
