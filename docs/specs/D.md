# Specs: column declaration (D)

Statements for PRD section 4.2. Form, backends, answers and the `items` fixture: see `README.md`.

- **SPEC-D1.1** [all] A request naming the declared columns in any order and subset is answered; each row holds
  exactly the declared columns' client names.
- **SPEC-D2.1** [all] `Column('colour', field=...)` over a backend field named `colour_code` → a column search on
  `colour` filters `colour_code`, and rows carry the key `colour`, never `colour_code`.
- **SPEC-D3.1** [all] The same text `7` on a column declared string and on one declared number: SearchBuilder `<`
  answers on the number column and is refused with `invalid_filter` on the string column (the declared type
  decides).
- **SPEC-D4.1** [all] A column declared unsearchable: a global search skips it and a column search on it is
  `invalid_filter` (PRD-F41).
- **SPEC-D5.1** [all] A column declared unorderable: an order the request marks orderable on it is `invalid_filter`
  (PRD-O1).
- **SPEC-D6.1** [all] A column search with a value on undeclared `extra` → `invalid_filter`; with an empty value →
  answered (rows 1, 2, 3, 4).
- **SPEC-D6.2** [all] A SearchBuilder criterion whose `origData` is undeclared → `invalid_filter`.
- **SPEC-D6.3** [all] A global search with a request-searchable undeclared column → answered (rows 1, 2, 4 for
  `red`); an order on it marked unorderable → skipped; marked orderable → `invalid_filter`.
- **SPEC-D7.1** [all] SearchBuilder `=` `7` on `price` sent with `type: "string"` → rows 1; `contains` `7` on `price`
  sent with `type: "string"` → `invalid_filter` (the client's type is ignored).
- **SPEC-D8.1** [SQL] Without a declared type: an `Integer` column is a number, a `Date` a date, a `Boolean` (SQL
  Server `BIT` included) a boolean, a `String` a string.
- **SPEC-D8.2** [MySQL] A reflected `TINYINT(1)` column without a declared type is a number: SearchBuilder `<` `1` is
  answered; declared boolean, `=` `true` is answered.
- **SPEC-D8.3** [OS] A column without a declared type is a string: SearchBuilder `<` on it → `invalid_filter`.
- **SPEC-D9.1** [all] A column search on `secret` with `Alpha` calls the hook once with `Alpha` and matches the stored
  `enc:Alpha` → rows 1.
- **SPEC-D10.1** [SQL] A column declared as `lower(name)` is searched and ordered by that expression: order asc gives
  the expression's order; a column search `red apple` → rows 1.
- **SPEC-D11.1** [OS] Constructing an OpenSearch table without a unique key → `SchemaError`.
- **SPEC-D11.2** [SQL] Constructing a table over a join or a subquery without a primary key and without a declared
  key → `SchemaError`; over a table with a primary key, no key need be declared.
- **SPEC-D12.1** [all] Global search `Alpha` → rows 1 (the whole text equals the hooked value); `Alph` → none from
  `secret`.
- **SPEC-D12.2** [all] Global search `red Alpha` → none: neither do both words match the other columns, nor does the
  whole text `red Alpha` equal a hooked value.
- **SPEC-D12.3** [all] Global search `  Alpha  ` (spaces, U+00A0 or U+3000 around it) → rows 1 (trimmed once); the
  hook is called once per request with `Alpha`.
- **SPEC-D12.4** [all] Global search `!Alpha` → rows 1, 2, 3, 4 (no other column holds `alpha`, and `!word`
  ignores the hooked column); `!apple` → rows 2, 3, 4.
- **SPEC-D12.5** [all] On a table whose every searchable column is hooked: a global search of 20 words equal to a
  stored value is answered (no word cap) and matches by equality; one of 256 code points → `invalid_filter`.
- **SPEC-D12.6** [all] On a table with one hooked and one other searchable column: a global search of 17 words →
  `invalid_filter`.
- **SPEC-D12.7** [all] Global search `a` + LF + `b` on a hooked column storing `enc:a` + LF + `b` matches by the
  whole text (LF is not rewritten to a space for the equality).
- **SPEC-D13.1** [OS] A `text+keyword` column (the default) sorts and matches exactly on `<field>.keyword`; a
  `keyword` column on `<field>`; recording shows the field names.
- **SPEC-D14.1** [all] Declaring a hook on a number, date or boolean column → `SchemaError` at construction.
- **SPEC-D15.1** [all] `multi_values` `["Alpha", "Beta"]` and categorical `^(Alpha|Beta)$` on `secret` → rows 1, 2;
  the hook is called once per value.
- **SPEC-D16.1** [all] `multi_values` `[""]` on `secret` → the rows where `secret` is empty (NULL or `''` on SQL,
  missing on OS); the hook is not called for the empty value.
- **SPEC-D17.1** [all] Column search on `secret` with `Alpha`, ` Alpha `, and `"Alpha"`: `Alpha` and ` Alpha ` → rows
  1; `"Alpha"` → none (no smart-search grammar: the quotes are part of the value).
- **SPEC-D17.2** [all] Column search on `secret` with 20 words is answered (no word cap); with 256 code points →
  `invalid_filter`.
- **SPEC-D18.1** [all] SearchBuilder on `secret`: `=` `Alpha` → rows 1; `!=` `Alpha` → rows 2, 3, 4; `null` → none;
  `!null` → rows 1, 2, 3, 4.
- **SPEC-D18.2** [all] SearchBuilder `contains`, `starts`, `ends` and their negations on `secret` → `invalid_filter`.
- **SPEC-D19.1** [OS] On a text-only `name`: a categorical filter, a `multi_values` list, and SearchBuilder `=`,
  `!=`, `starts`, `!starts`, `ends`, `!ends`, `contains`, `!contains` → `invalid_filter`.
- **SPEC-D19.2** [OS] On a text-only `name`: a global search `apple`, a column search `apple`, and `null`/`!null` are
  answered.
- **SPEC-D20.1** [OS] Declaring a hook on a text-only column → `SchemaError` at construction.
