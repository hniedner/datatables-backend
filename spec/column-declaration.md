# Specs: column declaration (D)

Statements for PRD section 4.2. Form, backends, answers and the `items` fixture: see `README.md`.

- **SPEC-D1.1** [all] A request naming the declared columns in any order, or only a subset of them, is answered;
  each row holds every declared column's client name, whichever the request names (the owner's decision of
  2026-11-20).
- **SPEC-D2.1** [all] With the stored field renamed `colour_code` and the column declared
  `Column('colour', field='colour_code')`: a column search `blue` on `colour` → rows 3, and rows carry the key
  `colour`, never `colour_code`.
- **SPEC-D3.1** [all] The declared type decides: SearchBuilder `<` `8` on `price` (declared number) → rows 1; on
  `colour` (declared string) → `invalid_filter`.
- **SPEC-D4.1** [all] With `name` declared unsearchable: global search `apple` → none (row 1 is not found through
  `name`); a column search `apple` on `name` → `invalid_filter` (PRD-F41).
- **SPEC-D5.1** [all] A column declared unorderable: an order the request marks orderable on it is `invalid_filter`
  (PRD-O1).
- **SPEC-D6.1** [all] A column search with a value on undeclared `extra` → `invalid_filter`; with an empty value →
  answered (rows 1, 2, 3, 4).
- **SPEC-D6.2** [all] A SearchBuilder criterion whose `origData` is undeclared → `invalid_filter`.
- **SPEC-D6.3** [all] A global search with a request-searchable undeclared column → answered (rows 1, 2, 4 for
  `red`); an order on it marked unorderable → skipped; marked orderable → `invalid_filter`.
- **SPEC-D7.1** [all] SearchBuilder `<` `8` on `price` sent with `type: "string"` → rows 1; `contains` `7` on
  `price` sent with `type: "string"` → `invalid_filter` (the client's type is ignored both ways).
- **SPEC-D8.1** [SQL] The fixture's `price` (a `BigInteger`), `made` (a `Date`), `active` (a `Boolean`; on SQL Server a
  `BIT`) and `name` (a `String`), declared without a type: on `price` SearchBuilder `<` `8` → rows 1 and `contains`
  `7` → `invalid_filter`; on `made` `contains` `2024` → rows 1, 2; on `active` `=` `true` → rows 1, 4 and `<` →
  `invalid_filter`; on `name` `contains` `app` → rows 1 and `<` → `invalid_filter`.
- **SPEC-D8.2** [MySQL] Given a table `flags` (`id`, `flag TINYINT(1)`) holding (1, 1) and (2, 0), reflected: with
  `flag` declared without a type it is a number, so SearchBuilder `<` `1` → rows 2; with `flag` declared boolean,
  `=` `true` → rows 1.
- **SPEC-D8.3** [OS] `price` (mapped `long`) declared without a type is a string: SearchBuilder `<` `8` on it →
  `invalid_filter` (inferring the type from the mapping would answer rows 1).
- **SPEC-D9.1** [all] A column search on `secret` with `Alpha` calls the hook once with `Alpha` and matches the stored
  `enc:Alpha` → rows 1.
- **SPEC-D10.1** [SQLite] A column declared as the expression `lower(colour)` is ordered by it: order asc → rows 3,
  1, 4, 2, where the plain `colour` gives 4, 3, 1, 2 (PRD-O6); a column search `red` on it → rows 1, 2, 4.
- **SPEC-D11.1** [OS] Constructing an OpenSearch table without a unique key → `SchemaError`.
- **SPEC-D11.2** [SQL] Constructing a table over a join or a subquery without a primary key and without a declared
  key → `SchemaError`; over a table with a primary key, no key need be declared.
- **SPEC-D12.1** [all] Global search `Alpha` → rows 1 (the whole text equals the hooked value); `Alph` → none.
- **SPEC-D12.2** [all] Global search `red Alpha` → none: neither do both words match the other columns, nor does the
  whole text `red Alpha` equal a hooked value.
- **SPEC-D12.3** [all] Global search `  Alpha  ` (spaces, U+00A0 or U+3000 around it) → rows 1 (trimmed once); the
  hook is called once per request with `Alpha`.
- **SPEC-D12.4** [all] Global search `!Alpha` → rows 1, 2, 3, 4 (no other column holds `alpha`, and `!word`
  ignores the hooked column); `!apple` → rows 2, 3, 4.
- **SPEC-D12.5** [all] On a table whose only searchable column is `secret`, given a row 5 whose `secret` is stored as
  `enc:` + 20 one-letter words (`a b c ... t`): a global search of those 20 words → rows 5 (no word cap; matched by
  equality); a global search of 256 code points → `invalid_filter`.
- **SPEC-D12.6** [all] On a table with one hooked and one other searchable column: a global search of 17 words →
  `invalid_filter`.
- **SPEC-D12.7** [all] Given a row 5 whose `secret` is stored as `enc:a` + LF + `b` and whose every other column but
  `id` is NULL or missing: global search `a` + LF + `b` → rows 5 (the hooked equality, since
  the LF is not rewritten for it; no fixture row holds both `a` and `b` in its other columns).
- **SPEC-D12.8** [all] A global search of `Alpha` with spaces around it to 256 code points in all (5 after
  trimming) → `invalid_filter`: the 255-code-point bound applies to the raw text.
- **SPEC-D13.1** [OS] A `text+keyword` column (the default) sorts and matches exactly on `<field>.keyword`; a
  `keyword` column on `<field>`; recording shows the field names.
- **SPEC-D14.1** [all] Declaring a hook on a number, date or boolean column → `SchemaError` at construction.
- **SPEC-D15.1** [all] `multi_values` `["Alpha", "Beta"]` and categorical `^(Alpha|Beta)$` on `secret` → rows 1, 2;
  the hook is called once per value.
- **SPEC-D16.1** [all] Given rows 5 (`secret` NULL or missing) and 6 (`secret` `''`): `multi_values` `[""]` on
  `secret` → rows 5, 6 on SQL, rows 5 on OS (PRD-F26); the hook is not called for the empty value.
- **SPEC-D17.1** [all] Column search on `secret` with `Alpha`, ` Alpha `, and `"Alpha"`: `Alpha` and ` Alpha ` → rows
  1; `"Alpha"` → none (no smart-search grammar: the quotes are part of the value).
- **SPEC-D17.2** [all] Column search on `secret` with 20 words is answered (no word cap); with 256 code points →
  `invalid_filter`.
- **SPEC-D17.3** [all] Given a row whose `secret` is stored as `enc:a` + LF + `b`: a column search on `secret` with
  `a` + LF + `b` → that row; with `a b` → none (LF is not rewritten for the equality).
- **SPEC-D18.1** [all] Given rows 5 (`secret` NULL or missing) and 6 (`secret` `''`), SearchBuilder on `secret`:
  `=` `Alpha` → rows 1; `!=` `Alpha` → rows 2, 3, 4, 5, 6 (PRD-F37); `null` → rows 5, 6 on SQL and rows 5 on OS;
  `!null` → rows 1, 2, 3, 4 on SQL and rows 1, 2, 3, 4, 6 on OS.
- **SPEC-D18.2** [all] SearchBuilder `contains`, `starts`, `ends` and their negations on `secret` → `invalid_filter`.
- **SPEC-D19.1** [OS] On a text-only `name`: a categorical filter, a `multi_values` list that holds a value, and
  SearchBuilder `=`, `!=`, `starts`, `!starts`, `ends`, `!ends`, `contains`, `!contains` → `invalid_filter`.
- **SPEC-D19.2** [OS] On a text-only `name`: a global search `apple`, a column search `apple`, and `null`/`!null` are
  answered.
- **SPEC-D19.3** [OS] On a text-only `name`: `multi_values` `[""]`, a list of only an empty entry, is answered
  with the rows whose `name` is missing; it means empty and matches no value exactly (PRD-F20).
- **SPEC-D20.1** [OS] Declaring a hook on a text-only column → `SchemaError` at construction.
- **SPEC-D21.1** [SQLite, PostgreSQL] With `colour` declared `fold_case=False`: SearchBuilder `=` `Red` → none; `=`
  `red` → rows 1; `multi_values` `["RED"]` → rows 4.
- **SPEC-D21.2** [OS] With `colour` declared `fold_case=False`: SearchBuilder `=` `Red` sends a `term` query without
  `case_insensitive`, and `multi_values` `["red", "blue"]` one `terms` query.
- **SPEC-D21.3** [all] With `colour` declared `fold_case=False`: SearchBuilder `contains` `RED` → rows 1, 2, 4 (a
  search still ignores case, PRD-F9).
