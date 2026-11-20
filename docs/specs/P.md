# Specs: parsing (P)

Statements for PRD section 4.1. Form, tags, answers, the base request and the `items` fixture: see `README.md`.
Statements tagged `[core]` check `parse_request` alone; those tagged `[all]` also fetch and name rows.

- **SPEC-P1.1** [core] The base request as a nested JSON object, as a plain mapping of flat keys, as a multi-dict
  (`getlist`), and as a list of key-value pairs parses to equal `DTParams`.
- **SPEC-P1.2** [core] A multi-dict holding `columns[0][search][multi_values][]` twice, with `red` and `blue`, reads
  both values; so does a pair list repeating the key.
- **SPEC-P2.1** [core] `columns[0][search][value]`, `columns[0].search.value`, `columns.0.search.value` and
  `columns[0].search[value]` parse to the same request.
- **SPEC-P3.1** [core] The base request plus `_`, `searchPanes_options`, `searchPanesLast` and `myAppKey` (flat and
  in a JSON body) parses to the same request as without them.
- **SPEC-P3.2** [core] A flat key `_[x]` or `myApp.x` is ignored, though its shape is not a request parameter's.
- **SPEC-P4.1** [core] Unknown fields `columns[0][foo]` and `search[smart]` are ignored; the request parses as
  without them.
- **SPEC-P5.1** [core] A non-empty `search[fixed]`, `search[groups]`, `search[groupsFixed]` or
  `columns[0][search][fixed]` → `MalformedRequest`; an empty one parses.
- **SPEC-P5.2** [core] `searchPanes[colour][0]=red` → `MalformedRequest`; `searchPanes` with only empty panes, and
  an empty `searchPanes_null`, parse.
- **SPEC-P5.3** [core] `columns[0][columnControl][list][0]=x` → `MalformedRequest`;
  `columns[0][columnControl][search]` with an empty value and logic `contains` parses; with value `x`, or with logic
  `empty` or `notEmpty`, → `MalformedRequest`.
- **SPEC-P6.1** [core] A JSON body that is a list → `MalformedRequest`; `columns` as text → `MalformedRequest`;
  `columns[0][searchable]` as an object → `MalformedRequest`; `columns[0][data]` JSON `null` parses (PRD-P21).
- **SPEC-P6.2** [core] Flat keys `columns[0][data]` and `columns.0.data` with different values → `MalformedRequest`;
  `columns[0]` and `columns[2]` with no `columns[1]` → `MalformedRequest`.
- **SPEC-P6.3** [core] A flat value that is a dict, or a flat key that is not text (an int key in the mapping), →
  `MalformedRequest`.
- **SPEC-P6.4** [core] A SearchBuilder condition `without` or `regex` → `MalformedRequest`.
- **SPEC-P7.1** [core] Flat keys `columns[01][data]`, `columns[٣][data]` (an Arabic-Indic digit),
  `columns[1234567890][data]`, `columns[0.x][data]`, `columns[0]x` and `columns[0][data][]x` → `MalformedRequest`.
- **SPEC-P7.2** [core] `columns[0][search][multi_values][]` repeated appends its values; `columns[][data]` (`[]` in
  place of an index) → `MalformedRequest`.
- **SPEC-P8.1** [core] `start` as the form text `1.5`, `abc` or empty, as the JSON number `1.5`, and `length` as the
  form text `10.0` or the JSON number `10.5` → `MalformedRequest`.
- **SPEC-P9.1** [core] `start` `-1` → `MalformedRequest`; `start` `0` parses.
- **SPEC-P10.1** [core] `start` 9007199254740991 parses; 9007199254740992 → `MalformedRequest`.
- **SPEC-P10.2** [core] `draw` 2147483647 parses; 2147483648 → `MalformedRequest`.
- **SPEC-P11.1** [core] A lone surrogate U+D800 in `search[value]` (the JSON escape `\ud800`) or in a column's
  `data` → `MalformedRequest`.
- **SPEC-P12.1** [core] For `columns[0][searchable]` sent with the value `MARKER_V` under the key text
  `columns[0][searchable]`, the `MalformedRequest` message names the parameter `columns` and contains neither
  `MARKER_V` nor the key text `columns[0][searchable]`; an `order` index outside the columns names `order`.
- **SPEC-P13.1** [core] `length` 1 and 1000 parse; 0, -1 and 1001 → `MalformedRequest`; with the maximum set to 50,
  51 → `MalformedRequest`.
- **SPEC-P14.1** [core] `columns[0][searchable]` `"true"` and `"false"` (form and JSON) read as `True` and `False`;
  `"yes"` → `MalformedRequest`.
- **SPEC-P15.1** [all] A column with `data` `name` and an empty `name` is the declared column `name`: a column search
  `pear` on it → rows 2.
- **SPEC-P16.1** [all] `order[0][column]` 3 with `order[0][name]` `id`, on a request whose column 3 is `price`,
  orders by `price`: asc → rows 4, 1, 3, 2 (the index decides).
- **SPEC-P17.1** [core] `parse_request` returns a `DTParams` whose columns are `DTColumn`, orders `DTOrder`, search a
  `Search`, and SearchBuilder a tree of `SearchGroup` and `SearchCriterion`.
- **SPEC-P18.1** [all] A column whose `data` is `{"_": "name", "sort": "name"}` and `name` `name` is the declared
  column `name` (a column search `pear` on it → rows 2); with an empty `name` it is nameless (PRD-P21), so a column
  search `pear` on it → error_code `invalid_filter`.
- **SPEC-P19.1** [core] An empty request, and a DataTables 1.9 request (`sEcho`, `sSearch`, `iDisplayStart`), →
  `MalformedRequest`.
- **SPEC-P20.1** [core] `columns[0][data]` as the JSON integer 0 parses to a column whose `data` is `"0"`.
- **SPEC-P20.2** [all] With `Column('0', field='id', type='number')` declared and `columns[0][data]` the JSON integer 0: a column
  search `3` on it → rows 3.
- **SPEC-P21.1** [all] A nameless column (`data` null, empty `name`) first, with DataTables' default order on column
  0 and a global search `red` → rows 1, 2, 4, no error.
- **SPEC-P21.2** [all] A column with `data` `function` and an empty `name` is nameless: an order on it is skipped
  (rows 1, 2, 3, 4); with `Column('function', field='id', type='number')` declared, it is that column: an order desc on it → rows
  4, 3, 2, 1.
- **SPEC-P21.3** [all] A column search with value `x` on a nameless column → error_code `invalid_filter`; a
  SearchBuilder criterion with an empty `origData` → error_code `invalid_filter`.
