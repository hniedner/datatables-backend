# Specs: column declaration (D)

Statements for PRD section 4.2. Form, backends, answers and the `items` fixture: see `README.md`.

- **SPEC-D1.1** [all] A request naming the declared columns in any order, or only a subset of them, is answered;
  each row holds every declared column's client name, whichever the request names (the owner's decision of
  2026-11-20).
- **SPEC-D1.2** [all] Constructing a table with two columns of one name, or with no column → `SchemaError`.
- **SPEC-D1.3** [all] A request with no columns, no search and no order is answered with every row, and
  `recordsFiltered` equals `recordsTotal`; each row holds every declared column.
- **SPEC-D2.1** [all] With the stored field renamed `colour_code` and the column declared
  `Column('colour', field='colour_code')`: a column search `blue` on `colour` → rows 3, and rows carry the key
  `colour`, never `colour_code`.
- **SPEC-D2.2** [SQL] `SQLTable(source, [Column('id'), Column('nope')])` over a source without a field `nope` →
  `SchemaError` naming `nope` when the table is built; it is never an answer to a request.
- **SPEC-D3.1** [all] The declared type decides: SearchBuilder `<` `8` on `price` (declared number) → rows 1; on
  `colour` (declared string) → `invalid_filter`.
- **SPEC-D4.1** [all] With `name` declared unsearchable: global search `apple` → none (row 1 is not found through
  `name`); a column search `apple` on `name` → `invalid_filter` (PRD-F41).
- **SPEC-D5.1** [all] A column declared unorderable: an order the request marks orderable on it is `invalid_filter`
  (PRD-O1).
- **SPEC-D6.1** [all] A column search with a value on undeclared `extra` → `invalid_filter`; with an empty value →
  answered (rows 1, 2, 3, 4).
- **SPEC-D6.2** [all] A SearchBuilder criterion whose `origData` is undeclared → `invalid_filter`, at any depth of
  nested groups, even beside a criterion that matches rows.
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
- **SPEC-D11.1** [OS] Constructing an OpenSearch table without a unique key → `SchemaError` naming `unique_key=`; so
  does a key that is not a declared column, an empty one, or one on analysed text only (no whole string to sort by).
  A key declared `orderable=False` is accepted: it breaks ties, and the client cannot order by it.
- **SPEC-D11.2** [SQL] Constructing a table over a table, view, join or subquery that SQLAlchemy reports no primary
  key for, without a declared key → `SchemaError` naming `unique_key=`; with a key it is accepted; over a source with
  a primary key, no key need be declared. A declared key that is not a declared column raises too.
- **SPEC-D11.3** [SQL] A subquery that selects some but not all of a table's primary-key columns reports a part of the
  key, which is not unique: without a declared key it is a `SchemaError` naming `unique_key=`, and with every key
  column selected it is accepted. The same holds for a subquery inside a join. (A `UNION` is not checked.)
- **SPEC-D11.4** [all] A unique key of several columns breaks ties in the order of its names, each in the direction
  of the first applied entry (PRD-O3); a column the order already names is not named again.
- **SPEC-D11.5** [OS] `_id` cannot be the unique key: `unique_key='_id'` with no declared column is an unknown name
  (`SchemaError`), and a key column whose field is `_id` raises `SchemaError` that says OpenSearch deprecates
  sorting on `_id` (field data) and to copy the id into a `keyword` field of the documents and name that column.
- **SPEC-D11.6** [OS] A column declared for `_id` reads its value from `hit['_id']`, which `_source` does not
  hold, and `_source` is never asked for it. It is neither searchable nor orderable: a column search or a criterion
  on it is refused as for any unsearchable column, the global search leaves it out, an order on it is refused (or
  skipped where the request marks it unorderable), and `orderable=True` on it is a `SchemaError`.
- **SPEC-D12.1** [all] Global search `Alpha` → rows 1 (the whole text equals the hooked value); `Alph` → none.
- **SPEC-D12.2** [all] Global search `red Alpha` → none: neither do both words match the other columns, nor does the
  whole text `red Alpha` equal a hooked value.
- **SPEC-D12.3** [all] Global search `  Alpha  ` (spaces, U+00A0 or U+3000 around it) → rows 1 (trimmed once); the
  hook is called once per request with `Alpha`.
- **SPEC-D12.4** [all] Global search `!Alpha` → rows 1, 2, 3, 4 (no other column holds `alpha`, and `!word`
  ignores the hooked column); `!apple` → rows 2, 3, 4.
- **SPEC-D12.5** [all] On a table whose only searchable column is `secret`, given a row 5 whose `secret` is stored as
  `enc:` + 20 one-letter words (`a b c ... t`): a global search of those 20 words → rows 5 (no word cap; matched by
  equality); a global search of 256 code points → `value_too_long`.
- **SPEC-D12.6** [all] On a table with one hooked and one other searchable column: a global search of 17 words →
  `too_many_words`.
- **SPEC-D12.7** [all] Given a row 5 whose `secret` is stored as `enc:a` + LF + `b` and whose every other column but
  `id` is NULL or missing: global search `a` + LF + `b` → rows 5 (the hooked equality, since
  the LF is not rewritten for it; no fixture row holds both `a` and `b` in its other columns).
- **SPEC-D12.8** [all] A global search of `Alpha` with spaces around it to 256 code points in all (5 after
  trimming) → `value_too_long`: the 255-code-point bound applies to the raw text.
- **SPEC-D12.9** [all] On a table whose only searchable column is `secret`, given a row 5 whose `secret` is stored as
  `enc:!Alpha`: global search `!Alpha` → rows 5 (a word, even a negated one, is compared whole by the hooked
  equality); global search `""` or `!x` → every row, and the hook is not called (text with no word adds nothing).
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
  `value_too_long`.
- **SPEC-D17.3** [all] Given a row whose `secret` is stored as `enc:a` + LF + `b`: a column search on `secret` with
  `a` + LF + `b` → that row; with `a b` → none (LF is not rewritten for the equality).
- **SPEC-D18.1** [all] Given rows 5 (`secret` NULL or missing) and 6 (`secret` `''`), SearchBuilder on `secret`:
  `=` `Alpha` → rows 1; `!=` `Alpha` → rows 2, 3, 4, 5, 6 (PRD-F37); `null` → rows 5, 6 on SQL and rows 5 on OS;
  `!null` → rows 1, 2, 3, 4 on SQL and rows 1, 2, 3, 4, 6 on OS.
- **SPEC-D18.2** [all] SearchBuilder `contains`, `starts`, `ends` and their negations on `secret` → `invalid_filter`.
- **SPEC-D19.1** [OS] On a text-only `name`: a categorical filter, a `multi_values` list that holds a value, and
  SearchBuilder `=`, `!=`, `starts`, `!starts`, `ends`, `!ends` → `invalid_filter`.
- **SPEC-D19.2** [OS] On a text-only `name`: a global search `apple`, a column search `apple`, SearchBuilder `contains`
  and `!contains`, and `null`/`!null` are answered.
- **SPEC-D19.3** [OS] On a text-only `name`: `multi_values` `[""]`, a list of only an empty entry, is answered
  with the rows whose `name` is missing; it means empty and matches no value exactly (PRD-F20).
- **SPEC-D20.1** [OS] Declaring a hook on a text-only column → `SchemaError` at construction.
- **SPEC-D21.1** [SQLite, PostgreSQL] With `colour` declared `fold_case=False`: SearchBuilder `=` `Red` → none; `=`
  `red` → rows 1; `multi_values` `["RED"]` → rows 4.
- **SPEC-D21.2** [OS] With `colour` declared `fold_case=False`: SearchBuilder `=` `Red` sends a `term` query without
  `case_insensitive`, and `multi_values` `["red", "blue"]` one `terms` query.
- **SPEC-D21.3** [all] With `colour` declared `fold_case=False`: SearchBuilder `contains` `RED` → rows 1, 2, 4 (a
  search still ignores case, PRD-F9).
- **SPEC-D22.1** [SQL] Each of these raises `SchemaError` at construction, naming the column: a `String` column
  declared `number`, `date` or `boolean`; a `Date` and a `DateTime` column declared `number`; a `TypeDecorator`
  storing a `String` declared `number`.
- **SPEC-D22.2** [SQL] Each of these constructs and answers as before: an `Integer` and a `Boolean` column declared
  `string`, a `Boolean` declared `number`, an expression without a type declared `number`, a `Uuid` declared
  `string`.
- **SPEC-D23.1** [OS] A number column declared with each kind that refuses a number (all but `double` and
  `scaled_float`), SearchBuilder `=`: a value OpenSearch answers is sent and answered, and one it refuses is answered
  with `invalid_number` and nothing sent. The pairs are the last value answered and the first refused (the last and
  first whole numbers for `half_float`), but for the `half_float` pair of `65519.99` and `65519.999`, which straddles
  its edge: `byte`, `short` and `integer` 2147483647 and 2147483648, -2147483648 and -2147483649; `long` 2^63-1 and
  2^63, -2^63 and -2^63-1; `unsigned_long` 2^64-1 and 2^64, 0 and -1; `float` 3.4028235677973362e38 and
  3.4028235677973366e38 and their negatives; `half_float` 65519 and 65520 and their negatives, and `65519.99` and
  `65519.999`. Live, on both servers: the same value through an undeclared column is the server's own 400, and `byte`
  and `short` are refused at 2^31 as the integer is. A `half_float` field reads a number as a 32-bit float before it
  rounds to half, so between 65519 and 65520 its edge is, computed from that, 65519.998046875, the tie that rounds to
  65520.0: `65519.99` is sent and `65519.999`, which reads as the float 65520.0, is refused with `invalid_number` and
  nothing sent, where the server's own answer is a 400 (`[half_float] supports only finite values, but got [65520.0]`).
- **SPEC-D23.2** [OS] With the kinds `integer`, `half_float` and `unsigned_long`, each way a number reaches the query is
  checked, and the first value refused of the high whole-number pair of `SPEC-D23.1` (the low one for `unsigned_long`)
  is refused by every one: SearchBuilder `=`, `!=`, `<`, `<=`, `>`, `>=`, `between` and `!between`, a column search, a
  `multi_values` list, and ColumnControl's `equal` and its list. Live: a list of -1 on an undeclared `unsigned_long`
  answers the document holding 2^64-1, and a declared one refuses it.
- **SPEC-D23.3** [OS] A `byte` and a `short` answer `<=` with 128, 40000 and -129: they refuse only beyond an
  `integer`'s range.
- **SPEC-D23.4** [OS] The refusal of a number of 47 digits, for each kind that refuses one (all but `double` and
  `scaled_float`), names the field type, says `within that range`, and holds no part of the value.
- **SPEC-D23.5** [OS] On a `long`, `=` 9223372036854775807.5 and 9223372036854775806.5 are refused (each is sent as
  the float 2^63, which the server refuses), and 9223372036854774784.5 is sent.
- **SPEC-D23.6** [OS] A number column with no kind: `=` 2^64 is sent as it is and answered with no error from the
  library. Live: a column over an `integer` field fails with the client's `RequestError`; a `scaled_float` answers
  1e300 with the document holding its largest value.
- **SPEC-D23.7** [OS] A number column without a kind: `=` a number of 255 digits is sent, one of 256 is
  `value_too_long`, and 1e400 is `invalid_number`. Live: a `float` field refuses 255 digits, so the declared kind
  answers `invalid_number` and the undeclared column fails with `RequestError`.
- **SPEC-D23.8** [OS] A SearchBuilder with `created = xx` (a date mistake, step 2) and an `integer` column `=` 2147483648
  (a range mistake, step 3) is answered `invalid_date`.
- **SPEC-D23.9** [OS] `number=` on a text column and on a date column raises `SchemaError` at construction, naming the
  column and, for the text column, `type="number"`.
- **SPEC-D23.10** [OS, SQL] (on SQL, the `type=`) A `number=` or a `text=` other than `None` that is none of its kinds,
  and a `type=` that is none of the four, raises `SchemaError` at construction, naming the column, what was written and
  the accepted ones. Before, the table usually built and a filter that carries a number failed with a `KeyError` (a
  `number=`), or any SearchBuilder condition did (a `type=`), or a whole-string condition or an order was wrongly
  refused, and contains, a column search and the global search matched analysed text only, with no substring inside a
  word (a `text=` on a string column).
- **SPEC-D23.11** [OS] A number column declared `double` or `scaled_float` builds, and `=` with 1e308, -1e308 and a
  number of 255 digits on `double` (the last as an integer), and with 1e300 and -1e300 on `scaled_float`, sends each as
  it is.

- **SPEC-D23.12** [OS] On a string column, `IndexColumn(text=None)`, the default, is read as `text+keyword`: the same
  request sends the same body for `text=None`, an explicit `text='text+keyword'` and a plain `Column`.
- **SPEC-D23.13** [OS] A `text` kind (`keyword`, `text` or `text+keyword`) on a number, date or boolean column raises
  `SchemaError` at construction, naming the column and saying to drop `text=` or declare the column a string.
- **SPEC-D23.14** [OS, SQL] A `search_value` that is not callable raises `SchemaError` at construction, naming the
  column and saying to pass a function. Before, a non-callable `search_value` on a string column that holds a whole
  string built; the first request that compared a search value with the column (`=`, `!=`, a column search or the global
  search) raised `TypeError`, or, for a falsy value such as `''` or `0`, the column was silently compared by whole value
  with the text unchanged.
- **SPEC-D23.15** [OS, SQL] A column declared with an empty name (or a name of `None`), or an empty `field`, raises
  `SchemaError` at construction, naming what was written and the fix. Before, on OpenSearch an empty name with no
  `field` built a table that sent an empty field name (`null` for a name of `None`), and an empty `field` was silently
  read as the name on both backends; on SQL an empty name with no `field` was refused only as a missing column, with no
  fix, and a name of `None` with no `field` raised SQLAlchemy's `ArgumentError`.
- **SPEC-D23.16** [OS, SQL] A `search_value` on a column declared `searchable=False` raises `SchemaError` at
  construction, naming the column and saying to remove the hook or declare the column searchable; on OpenSearch the
  same for a column that reads the document id (`_id`), whose message only says to remove the hook, since `searchable`
  is ignored on the id. Before, the table built and the hook never ran: a global search, a column search, a SearchBuilder
  condition and ColumnControl's `contains` never called it on that column. A hook on a column that is no string, or that
  holds analysed text only, is refused first, by its own message (`SPEC-D14.1`, `SPEC-D20.1`).
- **SPEC-D23.17** [OS] A `text` kind on a column that reads the document id, and on a column declared
  `searchable=False` and `orderable=False` that is not in `unique_key`, raises `SchemaError` at construction, naming the
  column and saying to drop `text=`. Before, the table built and no string field of the column was read. A `text` on the
  key stays (its sort ends every order), and so does one on a `searchable=False` column with the default `orderable`:
  `text=` picks the field the column is sorted by and decides whether it can be sorted at all. A stated limit:
  `searchable=False`, the default `orderable` and `text='text'` builds, though `text='text'` then only turns ordering
  off; declare `orderable=False` to say so.
- **SPEC-D23.18** [OS] A `number` kind on a column declared `searchable=False` raises `SchemaError` at construction,
  naming the column and saying to drop `number=` or declare the column searchable. Before, the table built and the kind,
  which only bounds the numbers a filter sends, was never read.
- **SPEC-D23.19** [OS] Each of these builds: `text='keyword'` on a `searchable=False` column with the default
  `orderable`; `searchable=False` with `orderable=False` and no `text`; `text='text'` on a `searchable=False` column; a
  hook on a searchable column; a `number` kind on a searchable column; and the key declared
  `IndexColumn('id', text='keyword', searchable=False, orderable=False)`, whose search sorts by `id`.
