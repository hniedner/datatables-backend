# Specs: parsing (P)

Statements for PRD section 4.1. Form, backends, answers and the `items` fixture: see `README.md`. Parsing is
backend-independent; each statement checks `parse_request` and, where it names rows, a fetch on SQLite. "The base
request" is DataTables' default request for the `items` columns with `draw` 1, `start` 0, `length` 10.

- **SPEC-P1.1** [all] The base request as a nested JSON object, as a plain mapping of flat keys, as a multi-dict
  (`getlist`), and as a list of key-value pairs parses to equal `DTParams`.
- **SPEC-P1.2** [all] A multi-dict holding `columns[0][search][multi_values][]` twice, with `red` and `blue`, reads
  both values; so does a pair list repeating the key.
- **SPEC-P2.1** [all] `columns[0][search][value]`, `columns[0].search.value`, `columns.0.search.value` and
  `columns[0].search[value]` parse to the same request.
- **SPEC-P3.1** [all] The base request plus `_`, `searchPanes_options`, `searchPanesLast` and `myAppKey` (flat and in
  a JSON body) parses to the same request as without them.
- **SPEC-P3.2** [all] A flat key `_[x]` or `myApp.x` is ignored, though its shape is not a request parameter's.
- **SPEC-P4.1** [all] An unknown field `columns[0][foo]` and `search[smart]` is ignored; the request parses as without
  it.
- **SPEC-P5.1** [all] A non-empty `search[fixed]`, `search[groups]`, `search[groupsFixed]` or
  `columns[0][search][fixed]` → `MalformedRequest`; an empty one parses.
- **SPEC-P5.2** [all] `searchPanes[colour][0]=red` → `MalformedRequest`; `searchPanes` with only empty panes, and an
  empty `searchPanes_null`, parse.
- **SPEC-P5.3** [all] `columns[0][columnControl][list][0]=x` → `MalformedRequest`; `columns[0][columnControl][search]`
  with an empty value and logic `contains` parses; with value `x`, or with logic `empty` or `notEmpty`, →
  `MalformedRequest`.
- **SPEC-P6.1** [all] A JSON body that is a list → `MalformedRequest`; `columns` as text → `MalformedRequest`;
  `columns[0][searchable]` as an object → `MalformedRequest`; `columns[0][data]` JSON `null` parses (PRD-P21).
- **SPEC-P6.2** [all] Flat keys `columns[0][data]` and `columns.0.data` with different values → `MalformedRequest`;
  `columns[0]` and `columns[2]` with no `columns[1]` → `MalformedRequest`.
- **SPEC-P6.3** [all] A flat value that is a dict, or a flat key that is not text (an int key in the mapping), →
  `MalformedRequest`.
- **SPEC-P6.4** [all] A SearchBuilder condition `without` or `regex` → `MalformedRequest`.
- **SPEC-P7.1** [all] Flat keys `columns[01][data]`, `columns[٣][data]` (Arabic-Indic digit), `columns[1234567890]`,
  `columns[0.x][data]`, `columns[0]x` and `columns[0][data][]x` → `MalformedRequest`.
- **SPEC-P7.2** [all] `columns[0][search][multi_values][]` repeated appends its values; `columns[][data]` (`[]` in
  place of a field or index) → `MalformedRequest`.
- **SPEC-P8.1** [all] `start` `1.5`, `abc` or `""`, and `length` `10.0`, → `MalformedRequest`.
- **SPEC-P9.1** [all] `start` `-1` → `MalformedRequest`; `start` `0` parses.
- **SPEC-P10.1** [all] `start` 9007199254740991 parses; 9007199254740992 → `MalformedRequest`.
- **SPEC-P10.2** [all] `draw` and `order[0][column]` 2147483647 parse (the order index then fails PRD-O1 as outside the
  columns); 2147483648 → `MalformedRequest`.
- **SPEC-P11.1** [all] A lone surrogate U+D800 in `search[value]` (JSON escape `\ud800`) or in a column's `data` →
  `MalformedRequest`.
- **SPEC-P12.1** [all] For `columns[0][searchable]` of the wrong type with a marker value, the `MalformedRequest`
  message names `columns` and holds neither the marker nor the key text; an `order` index outside the columns names
  `order`.
- **SPEC-P13.1** [all] `length` 1 and 1000 parse; 0, -1 and 1001 → `MalformedRequest`; with the maximum set to 50,
  51 → `MalformedRequest`.
- **SPEC-P14.1** [all] `columns[0][searchable]` `"true"` and `"false"` (form and JSON) read as `True` and `False`;
  `"yes"` → `MalformedRequest`.
- **SPEC-P15.1** [all] A column with `data` `name` and an empty `name` is the declared column `name`.
- **SPEC-P16.1** [all] `order[0][column]` 1 with `order[0][name]` `price` on a request whose column 1 is `name`
  orders by `name` (the index decides).
- **SPEC-P17.1** [all] `parse_request` returns a `DTParams` whose columns are `DTColumn`, orders `DTOrder`, search a
  `Search`, and SearchBuilder a tree of `SearchGroup` and `SearchCriterion`.
- **SPEC-P18.1** [all] A column whose `data` is `{"_": "name", "sort": "name"}` and `name` `name` is the declared
  column `name`; with an empty `name` it is nameless (PRD-P21).
- **SPEC-P19.1** [all] An empty request, and a DataTables 1.9 request (`sEcho`, `sSearch`, `iDisplayStart`), →
  `MalformedRequest`.
- **SPEC-P20.1** [all] `columns[0][data]` as the JSON integer 0 reads as `"0"`; with a declared column `0` it is that
  column.
- **SPEC-P21.1** [all] A nameless column (`data` null, empty `name`) first, with DataTables' default order on column
  0 and a global search `red` → rows 1, 2, 4, no error.
- **SPEC-P21.2** [all] A column with `data` `function` and empty `name` is nameless; with a declared column named
  `function`, it is that column.
- **SPEC-P21.3** [all] A column search with value `x` on a nameless column → `invalid_filter`; a SearchBuilder
  criterion with empty `origData` → `invalid_filter`.
