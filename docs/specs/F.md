# Specs: filtering and search (F)

Statements for PRD section 4.3. Form, backends, answers and the `items` fixture: see `README.md`.

## Smart search

- **SPEC-F1.1** [all] Global search `pipe red` → rows 4: `pipe` matches only `note`, `red` matches `colour`, in one
  row.
- **SPEC-F1.2** [SQL] Global search `red 7` → rows 1, 2 (row 2: `reddish` and the price text `17`, PRD-F23).
- **SPEC-F1.3** [OS] Global search `red 7` → none: the number column takes no part (PRD-F32), so `7` matches nothing.
- **SPEC-F2.1** [all] Column search on `name` with `red pear` → rows 2.
- **SPEC-F2.2** [all] Column search on `colour` with `red pipe` → none: every word must match within that one column
  (matching across the row would give rows 4).
- **SPEC-F3.1** [all] Global search `pear reddish` → rows 2: words match in any order.
- **SPEC-F3.2** [all] On a table of rows whose only searchable column `note` holds 5 `a b x`, 6 `a b`, 7 `xby`, 8
  `x b y`, 9 `xa b` and 10 `x a b`: global search `"a" "b"x` → rows 5, 9, 10 (words `a`, `b`, `x`); `x"b"y` → rows
  7 (the word `xby`); `x"a b"` → rows 9 (`xa` and `b`, not 10); `"a""b"` → rows 5, 6, 9, 10 (`a` and `b`).
- **SPEC-F4.1** [all] Global search `"red apple"` → rows 1; `"apple red"` → none: a phrase sits inside one column.
- **SPEC-F4.2** [all] Global search `“red apple”` (U+201C, U+201D) → rows 1.
- **SPEC-F4.3** [all] Given rows whose `note` is `a b”`, `a b` and `“a b`: global search `"a b”` → only the `a b”`
  row; `“a b"` searches `“a b` → only the `“a b` row.
- **SPEC-F4.4** [all] Column search on `name` with `50"` → rows 3 (it searches `50`); `"plum` (unclosed) searches
  `plum` → rows 3.
- **SPEC-F4.5** [all] Global search `!5"` → rows 1, 2, 3: it excludes rows holding `5"`, not rows holding `5` (row 3
  holds `50`).
- **SPEC-F4.6** [all] Given rows whose `note` is `x “abc` and `abc`: global search `“abc` (an unclosed curly quote)
  → the `x “abc` row only; the quote stays as text.
- **SPEC-F4.7** [all] Given rows whose `note` is `"a b”` and `a b”`: global search `!"a b”` excludes the row holding
  `"a b”` and keeps the row holding only `a b”`: a negated mixed pair keeps its straight quote.
- **SPEC-F5.1** [all] Global search `!reddish` → rows 1, 3, 4.
- **SPEC-F5.2** [all] Column search on `note` with `!pipe` → rows 1, 2, 3: row 1's NULL (missing on OS) contains no
  word, so it passes.
- **SPEC-F5.3** [all] Global search `red !apple` → rows 2, 4.
- **SPEC-F6.1** [all] Global search `!"red apple"` → rows 2, 3, 4.
- **SPEC-F6.2** [all] Each of `"`, `"""`, `!""`, `""`, `!`, `!x` and three spaces, as the global search, as a
  column search on `name` and as a column search on `note`, → rows 1, 2, 3, 4, with no error (on `note`, an empty
  word kept as a condition would drop row 1's NULL).
- **SPEC-F7.1** [all] Global search `red` + LF + `apple`, and the same with CR, U+2028 or U+2029 in place of LF,
  → rows 1, as `red apple` does.
- **SPEC-F7.2** [all] Given rows whose `note` is `a` + LF + `b` and `a b`: `multi_values` `["a\nb"]` on `note` →
  the `a` + LF + `b` row only; SearchBuilder `=` `a\nb` on `note` answers the same.
- **SPEC-F8.1** [all] Global search `red OR blue` → the rows holding `red`, `or` and `blue` → none in the fixture
  (no union of `red` and `blue`).
- **SPEC-F8.2** [all] Given a row whose `note` is `-red`: global search `-red` → that row only (not rows 1, 2, 4,
  which hold `red` without the `-`).
- **SPEC-F8.3** [all] Global search `red*` and `re?` → none on the fixture alone; a row whose `note` is `red*` is
  found by `red*`.
- **SPEC-F8.4** [all] SearchBuilder `contains` `a*b` on `note` matches a row whose `note` is `xa*by` and not `xaby`.
- **SPEC-F8.5** [SQL] SearchBuilder `contains` `x_y` on `note` → rows 3, not a row whose `note` is `xay`; `contains`
  `50%` on `name` → rows 3, not a row whose `name` is `50 plum`.

## Case, accents, wildcards

- **SPEC-F9.1** [all] Global search `RED` → rows 1, 2, 4; `apple` → rows 1.
- **SPEC-F9.2** [all] SearchBuilder `=` `Red` on `colour` → rows 1, 4; `!=` `Red` → rows 2, 3.
- **SPEC-F9.3** [all] Categorical `^(RED)$` on `colour`, and `multi_values` `["rEd"]`, → rows 1, 4.
- **SPEC-F9.4** [all] SearchBuilder `contains` `APP`, `starts` `rEd`, `ends` `PLUM` on `name` → rows 1; rows 1, 2;
  rows 3. Their negations answer the complements.
- **SPEC-F9.5** [PostgreSQL, MySQL with `utf8mb4_bin`, SQL Server with a `_CS_` collation] The statements F9.1 to
  F9.4 hold unchanged on a case-sensitive collation.
- **SPEC-F9.6** [SQLite] Global search `äpfel` → none (SQLite folds ASCII only); `ÄPFEL` → rows 4.
- **SPEC-F9.7** [PostgreSQL in a UTF-8 non-C locale (`en_US.UTF-8`), SQL Server with a `_CI_` collation, MySQL
  with `utf8mb4_0900_ai_ci`] Global search `äpfel` → rows 4. A claim for the container run.
- **SPEC-F9.9** [OS container] Whole word: global search `äpfel` against row 4's `ÄPFEL`. Open: decided by the live
  run (issue #11); no answer is claimed (OQ4 in `README.md`).
- **SPEC-F9.10** [OS container] Substring: global search `äpf` against row 4's `ÄPFEL`. Open: decided by the live run
  (issue #11); no answer is claimed (OQ4).
- **SPEC-F9.11** [OS container] Exact match: SearchBuilder `=` `äpfel` on `name` against row 4's `ÄPFEL`. Open: decided by
  the live run (issue #11); no answer is claimed (OQ4).
- **SPEC-F9.8** [all] Column search on `secret` with `alpha` → none; with `Alpha` → rows 1 (a hooked column compares
  exactly, PRD-D17).
- **SPEC-F10.1** [SQLite, PostgreSQL with its default collation] Global search `apfel` → none: the library folds no
  accents.
- **SPEC-F10.2** [MySQL with `utf8mb4_0900_ai_ci`] Global search `apfel` → rows 4: the collation, not the library,
  folds the accent.
- **SPEC-F11.1** [all] Global search `50%` → rows 3; given a row whose `name` is `50 plum`, it is not matched.
- **SPEC-F11.2** [all] Column search on `note` with `x_y` → rows 3; a row whose `note` is `xay` is not matched.
- **SPEC-F11.3** [SQL] Given rows whose `note` is `[a]`, `a`, `a\b` and `ab`: column search `[a]` → the `[a]` row;
  `a\b` → the `a\b` row.
- **SPEC-F11.4** [OS] Given rows whose `note` is `a*b`, `axb`, `a?b`: column search `a*b` → the `a*b` row; `a?b` → the
  `a?b` row.
- **SPEC-F11.5** [all] Global search `ple` → rows 1 (a word matches as a substring).
- **SPEC-F11.6** [OS] On a text-only `name` column: column search `red` → rows 1, not row 2 (`reddish`).

## Combining and refusing

- **SPEC-F12.1** [all] Column search `red` on `colour` and `apple` on `name` → rows 1.
- **SPEC-F12.2** [all] Column search `red` on `colour` and global search `pear` → rows 2; with global `plum` → none.
- **SPEC-F13.1** [all] On a table whose every column is declared unsearchable, a global search `red` → error_code
  `invalid_filter`.
- **SPEC-F13.2** [OS] On a table with only number, date and boolean columns, a global search `red` → error_code
  `invalid_filter`.
- **SPEC-F13.3** [all] On a table whose every column is declared unsearchable, an empty global search → rows 1, 2,
  3, 4 (nothing is asked).
- **SPEC-F14.1** [all] A global search of one word of 255 code points is answered; 256 → error_code
  `invalid_filter`. The same holds for a word of 255 supplementary-plane characters (each one code point) and 256 of
  them.
- **SPEC-F14.2** [all] A global search of 16 words is answered; 17 → error_code `invalid_filter`; the same for a
  column search on `name`.
- **SPEC-F14.3** [all] 16 words plus a quoted phrase of two words is 17 words → `invalid_filter`; 15 words plus a
  quoted phrase of two words is 16 → answered (counting the phrase as two words would refuse it); 16 words plus `"`,
  `!""` and `!x` is 16 → answered.
- **SPEC-F15.1** [all] U+0000 in the global search → error_code `invalid_filter`; so do U+0009 (TAB), U+001B and
  U+007F.
- **SPEC-F15.2** [all] U+0000 in a column search, in a SearchBuilder `=` value, in a categorical value and in a
  `multi_values` entry → error_code `invalid_filter`, before any query runs.
- **SPEC-F15.3** [all] LF and CR in a global search are accepted (PRD-F7).

## Categorical filters and `multi_values`

- **SPEC-F16.1** [all] Column search on `colour` with `regex: true` and `red` → rows 1, 4 (not row 2: an exact
  match).
- **SPEC-F16.2** [all] With `regex: true`: `red|blue`, `^red$` and `^(red|blue)$` on `colour` → rows 1, 3, 4; rows 1,
  4; rows 1, 3, 4.
- **SPEC-F16.3** [all] With `regex: true`, given a row whose `note` is `a-b/c`: `^(a-b/c)$` and `^(a\-b\/c)$` → that
  row.
- **SPEC-F17.1** [all] With `regex: true`: `^(|x_y)$` on `note` → rows 1, 3 on OS (missing, `x_y`); rows 1, 2, 3 on
  SQL (NULL, `''`, `x_y`).
- **SPEC-F17.2** [all] With `regex: true`: `^(|red)$` on `colour` → rows 1, 4 (no row is empty).
- **SPEC-F18.1** [all] With `regex: true`, each of `^a|b$`, `a.b`, `a*`, `(a)`, `[ab]`, `a{2}`, `\d`, `\w`, `\n`,
  `a+`, `a?` and `^(a|b)` → error_code `invalid_filter`.
- **SPEC-F19.1** [all] With `regex: true`, every character of `/ . * + ? | ( ) [ ] { } \ $ ^ -` escaped by a
  backslash inside `^(...)$` matches itself: given a row whose `note` is `/.*+?|()[]{}\$^-` (the whole set),
  `^(\/\.\*\+\?\|\(\)\[\]\{\}\\\$\^\-)$` → that row only.
- **SPEC-F20.1** [all] `multi_values` `["red", "blue"]` on `colour` → rows 1, 3, 4.
- **SPEC-F20.2** [all] `multi_values` `["", "x_y"]` on `note` → rows 1, 3 on OS; rows 1, 2, 3 on SQL.
- **SPEC-F20.3** [all] A column search on `colour` with an empty value and an empty `multi_values` list is idle →
  rows 1, 2, 3, 4.

## Dates

- **SPEC-F21.1** [all] SearchBuilder on `made`: `=` `2024-03-05` → rows 1; `<` `2024-03-05` → rows 4; `<=` → rows 1,
  4; `>` → rows 2; `>=` → rows 1, 2; `between` `2024-01-01` and `2024-03-05` → rows 1.
- **SPEC-F21.2** [all] SearchBuilder on `at`: `=` `2024-03-05` → rows 1; `=` `2024-03-06` → rows 2 (UTC days, though
  the two instants are an hour apart).
- **SPEC-F21.3** [PostgreSQL] With the session `TimeZone` set to `America/Los_Angeles`, F21.2 holds unchanged.
- **SPEC-F21.4** [SQL] With `at` redeclared as a naive `DateTime` and row 1 storing `2024-03-05T10:00`:
  SearchBuilder `=` `2024-03-05` on `at` → rows 1.
- **SPEC-F21.5** [all] Categorical `^(2024-03-05)$` and `multi_values` `["2024-03-05"]` on `made` → rows 1; on `at`
  `["2024-03-06"]` → rows 2.
- **SPEC-F21.6** [all] `multi_values` `["2024-13-01"]` or `["05.03.2024"]` on `made` → error_code `invalid_date`.
- **SPEC-F21.7** [all] `multi_values` `[""]` on `made` → rows 3 (NULL on SQL, missing on OS).
- **SPEC-F21.8** [SQLite] Given a row whose `at` is written as the aware value `2024-03-06T01:00+02:00` (the instant
  2024-03-05T23:00Z): `=` `2024-03-06` matches it and `=` `2024-03-05` does not, since the offset is dropped and the
  stored `01:00` is read as UTC.
- **SPEC-F22.1** [all] SearchBuilder `contains` on `made`: `2024` → rows 1, 2; `2024-03` → rows 1; `2024-03-05` →
  rows 1; `!contains` `2024` → rows 3, 4.
- **SPEC-F22.2** [all] SearchBuilder `contains` on `made` with `2024-3`, `24` or `2024-03-5` → error_code
  `invalid_date`.
- **SPEC-F22.3** [all] SearchBuilder `contains` `2024-03-06` on `at` → rows 2 (UTC span).
- **SPEC-F22.4** [OS] Column search on `made` with `2024-12` → rows 2.
- **SPEC-F23.1** [SQL] Global search `17` → rows 2; column search `7` on `price` → rows 1, 2.
- **SPEC-F23.2** [SQL] Column search `2024-12` on `made` → rows 2; `03-05` → rows 1 (date text is `YYYY-MM-DD`).
- **SPEC-F23.3** [SQL] Global search `2023` → rows 4 (the `made` and `at` text).
- **SPEC-F24.1** [all] SearchBuilder on `made` with the maximum date as a bound → error_code `invalid_date`, not a
  database error, in each of: `=`, `!=`, `<`, `<=`, `>`, `>=` with `9999-12-31`; `between` and `!between` with
  `9999-12-31` as the first or the second bound; `contains` and `!contains` with `9999-12-31`, `9999-12` or `9999`.
- **SPEC-F25.1** [all] SearchBuilder on `price`: `=` `abc`, `NaN`, `inf`, `-Infinity` or `1e999` → error_code
  `invalid_number`.
- **SPEC-F25.2** [SQL] On `price` (a `BigInteger`), `=` `9223372036854775808` → error_code `invalid_number`;
  `9223372036854775807` is answered. With `price` redeclared as a `SmallInteger`, `=` `32768` → `invalid_number`.
- **SPEC-F26.1** [SQL] SearchBuilder `null` on `note` → rows 1, 2; `!null` → rows 3, 4.
- **SPEC-F26.2** [OS] SearchBuilder `null` on `note` → rows 1 (a stored `''` is not missing); `!null` → rows 2, 3, 4.

## SearchBuilder

- **SPEC-F27.1** [all] On `name` (string), `<`, `<=`, `>`, `>=`, `between` and `!between` → error_code
  `invalid_filter`.
- **SPEC-F27.2** [all] On `price` (number), `contains`, `!contains`, `starts`, `!starts`, `ends` and `!ends` →
  error_code `invalid_filter`.
- **SPEC-F27.3** [all] On `made` (date), `starts` and `ends` → error_code `invalid_filter`; `contains` `2024` →
  rows 1, 2 (PRD-F22).
- **SPEC-F27.4** [all] The criterion's `type` is ignored: `<` `8` on `price` sent with `type: "string"` → rows 1
  (honouring the client's type would refuse `<` with `invalid_filter`).
- **SPEC-F27.5** [all] SearchBuilder condition `without` → `MalformedRequest` (PRD-P6).
- **SPEC-F27.6** [all] On `price`: `<` `8` → rows 1; `between` `7` and `8` → rows 1, 3; `!between` `7` and `8` →
  rows 2, 4 (PRD-F37).
- **SPEC-F28.1** [all] Group OR of `colour = blue` and `price = 7` → rows 1, 3.
- **SPEC-F28.2** [all] Group AND of `colour = red` and a nested OR group of `price = 7` and `price null` → rows 1, 4.
- **SPEC-F28.3** [all] An empty group adds nothing: alone → rows 1, 2, 3, 4; nested in an AND group with
  `colour = blue` → rows 3; nested in an OR group with `colour = blue` → rows 3 (an empty group read as true would
  give every row).
- **SPEC-F30.1** [all] `=` on `name` with no value, and `between` on `price` with one value → error_code
  `invalid_filter`.
- **SPEC-F31.1** [all] With the base filter `price` not null: no filter → rows 1, 2, 3, `recordsTotal` 3,
  `recordsFiltered` 3; global search `red` → rows 1, 2, `recordsTotal` 3, `recordsFiltered` 2.
- **SPEC-F32.1** [OS] Global search `17` → none; `2024` → none (the number and date columns are skipped).
- **SPEC-F33.1** [OS] Column search on `price`: `7` → rows 1 (not 17).
- **SPEC-F33.2** [OS] Column search on `price`: `7 8`, `!7`, `"7"` and `abc` → error_code `invalid_number`.
- **SPEC-F33.3** [all] Categorical `^(7|8)$` and `multi_values` `["7", "8"]` on `price` → rows 1, 3.
- **SPEC-F33.4** [all] `multi_values` `["x"]` on `price` → error_code `invalid_number`; `[""]` → rows 4.
- **SPEC-F34.1** [OS] Column search on `made`: `2024` → rows 1, 2; `2024-03-05` → rows 1.
- **SPEC-F34.2** [OS] Column search on `made`: `2024 2023`, `!2024`, `"2024"` and `2024-13` → error_code
  `invalid_date`.
- **SPEC-F35.1** [all] Given rows 5 (`note` `a-b`) and 6 (`note` `a.b`), every other column of both but `id` NULL
  or missing: global search `!-` → rows 3, 6 on SQL (the date text of rows 1, 2, 4 holds `-`) and rows 1, 2, 3, 4, 6
  on OS (dates take no part, PRD-F32); `!.` → rows 1, 2, 3, 4, 5 on all.
- **SPEC-F35.2** [all] Global search `!x` → rows 1, 2, 3, 4 (adds nothing).
- **SPEC-F36.1** [all] `=` on `name` with two values, and `between` on `price` with three → error_code
  `invalid_filter`.
- **SPEC-F36.2** [all] `null` on `note` with the values `[]` → rows 1, 2 on SQL, rows 1 on OS; with `[""]` → the
  same; with `abc` and a value holding U+0000 → the same, no refusal (PRD-F15 does not apply).
- **SPEC-F36.3** [all] `null` with a value that is not text → `MalformedRequest` (PRD-P6).
- **SPEC-F37.1** [all] On `note`: `!=` `x_y` → rows 1, 2, 4; `!contains` `pipe` → rows 1, 2, 3; `!starts` `5` →
  rows 1, 2, 3; `!ends` `y` → rows 1, 2, 4. Row 1 (NULL or missing) satisfies each.
- **SPEC-F37.2** [all] On `price`: `!=` `7` → rows 2, 3, 4; `!between` `0` and `100` → rows 4.
- **SPEC-F38.1** [all] A SearchBuilder tree 8 groups deep, the root group counted as depth 1, is answered; 9 deep →
  error_code `invalid_filter`.
- **SPEC-F38.2** [all] 64 criteria in all are answered; 65 → error_code `invalid_filter`.
- **SPEC-F38.3** [all] A categorical filter or `multi_values` list of 256 values is answered; 257 → error_code
  `invalid_filter`. A categorical regex of 256 values is longer than 255 code points; that is intended, since its
  values are exact values, not search text (PRD-F7), and PRD-F38 allows 256 of them.
- **SPEC-F38.4** [all] A tree 10,000 groups deep → error_code `invalid_filter`, with no `RecursionError`.
- **SPEC-F39.1** [all] A column search on `colour` with value `red` and `multi_values` `["blue"]` → error_code
  `invalid_filter`.
- **SPEC-F40.1** [all] A global search with `multi_values` `["red"]` → error_code `invalid_filter`; with `[]` → rows
  1, 2, 3, 4.

## Searchable, timezone text, booleans

- **SPEC-F41.1** [all] A request column `extra` (searchable, undeclared) with global search `red` → rows 1, 2, 4.
- **SPEC-F41.2** [all] With `name` declared unsearchable: global search `apple` → none; a column search `apple` on
  `name` → error_code `invalid_filter`, whatever the request's `searchable`; a SearchBuilder criterion on `name` → error_code
  `invalid_filter`.
- **SPEC-F41.3** [all] With `name` marked `searchable: false` in the request: global search `apple` → none; column
  search `apple` on `name` → rows 1.
- **SPEC-F41.4** [all] A column search with a value, or a non-empty `multi_values` list, on the undeclared `extra` →
  error_code `invalid_filter`; an empty one is answered (PRD-D6).
- **SPEC-F42.1** [PostgreSQL] With the session `TimeZone` `America/Los_Angeles`: global search `2024-03-05` → rows 1
  (not row 2); column search `2024-03-06` on `at` → rows 2.
- **SPEC-F42.2** [SQL Server] Given `at` as `DATETIMEOFFSET` with row 1 stored as `2024-03-06T04:30+05:00`: column
  search `2024-03-05` on `at` → rows 1.
- **SPEC-F43.1** [all] SearchBuilder `=` on `active` with `true`, `TRUE`, `True` or `1` → rows 1, 4; with `false` or
  `0` → rows 2.
- **SPEC-F43.2** [all] `!=` `true` on `active` → rows 2, 3 (NULL passes, PRD-F37); `null` → rows 3; `!null` → rows
  1, 2, 4.
- **SPEC-F43.3** [all] On `active`: `=` with `yes`, `2`, `t` or `''`, and any of `<`, `between`, `contains`,
  `starts` → error_code `invalid_filter`, never a database error.
- **SPEC-F44.1** [all] Global search `true` → none (the boolean column is skipped; no other column holds `true`).
  On a table whose only searchable column is `active`, global search `x` → error_code `invalid_filter` (no usable
  column, PRD-F13).
- **SPEC-F44.2** [all] Column search on `active`: `true` → rows 1, 4; `0` → rows 2; `yes` → error_code
  `invalid_filter`.
- **SPEC-F44.3** [all] Categorical `^(true|0)$` on `active` → rows 1, 2, 4; `multi_values` `["", "1"]` → rows 1, 3,
  4.
