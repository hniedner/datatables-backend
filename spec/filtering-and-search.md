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
  row; `“a b"` searches `“a b` → only the `“a b` row. On OpenSearch the column is a keyword column; on analysed
  text `SPEC-F11.9` holds.
- **SPEC-F4.4** [all] Column search on `name` with `50"` → rows 3 (it searches `50`); `"plum` (unclosed) searches
  `plum` → rows 3.
- **SPEC-F4.5** [all] Global search `!5"` → rows 1, 2, 3: it excludes rows holding `5"`, not rows holding `5` (row 3
  holds `50`).
- **SPEC-F4.6** [all] Given rows whose `note` is `x “abc` and `abc`: global search `“abc` (an unclosed curly quote)
  → the `x “abc` row only; the quote stays as text. On OpenSearch the column is a keyword column; on analysed text
  `SPEC-F11.9` holds.
- **SPEC-F4.7** [all] Given rows whose `note` is `"a b”` and `a b”`: global search `!"a b”` excludes the row holding
  `"a b”` and keeps the row holding only `a b”`: a negated mixed pair keeps its straight quote. On OpenSearch the
  column is a keyword column; on analysed text `SPEC-F11.9` holds.
- **SPEC-F4.8** [all] Given rows whose `note` is ` a ` (spaces at both ends), `xax` and `a`: global search `" a "` →
  the ` a ` row only: a phrase keeps the spaces inside its quotes. On OpenSearch the column is a keyword column; on
  analysed text `SPEC-F11.9` holds.
- **SPEC-F4.9** [all] Global search `"apple red` (an unclosed quote) → rows 1, as `apple red`: the quote opens no
  phrase, and each word must match on its own (the phrase `apple red` gives none, `SPEC-F4.1`).
- **SPEC-F5.1** [all] Global search `!reddish` → rows 1, 3, 4.
- **SPEC-F5.2** [all] Column search on `note` with `!pipe` → rows 1, 2, 3: row 1's NULL (missing on OS) contains no
  word, so it passes.
- **SPEC-F5.3** [all] Global search `red !apple` → rows 2, 4.
- **SPEC-F6.1** [all] Global search `!"red apple"` → rows 2, 3, 4.
- **SPEC-F6.2** [all] Each of `"`, `"""`, `!""`, `""`, `!`, `!x` and three spaces, as the global search, as a
  column search on `name` and as a column search on `note`, → rows 1, 2, 3, 4, with no error (on `note`, an empty
  word kept as a condition would drop row 1's NULL).
- **SPEC-F6.3** [all] A value made of U+0020, LF, CR, U+2028 and U+2029 and nothing else, as the global search and as
  a column search on `active`, → rows 1, 2, 3, 4.
- **SPEC-F6.4** [all] With `regex: true`, the value of F6.3 is a categorical value: as a column search on `colour`
  → none (the exact value, which no row holds); on `price` → rows 4 (on a column that is no string it holds
  nothing, so it means empty).
- **SPEC-F6.5** [all] Column search on `note` with `!pipe ""` → rows 1, 2, 3, as `!pipe` (`SPEC-F5.2`): the empty
  quote adds nothing next to other words (an empty word kept as a condition would drop row 1's NULL).
- **SPEC-F6.6** [all] Each of three spaces, `"`, `""`, `"""`, `!""`, `!` and `!x`, as a column search with `regex`
  false on the undeclared `extra`, on a column with no name, on `name` declared unsearchable, and beside
  `multi_values` `["red"]` on `colour`, is answered as the request without that text (rows 1, 2, 3, 4; beside the
  list rows 1, 4, as the list alone), never refused (PRD-F41, PRD-F39); with `regex: true` three spaces in those
  places stay `invalid_filter` (`SPEC-F6.4`). Such text holding a control character, `!` + U+0001 or `!` + tab, is
  error_code `invalid_character` on `name` and on `extra` (PRD-F15).
- **SPEC-F7.1** [all] Global search `red` + LF + `apple`, and the same with CR, U+2028 or U+2029 in place of LF,
  → rows 1, as `red apple` does.
- **SPEC-F7.2** [all] Given rows whose `note` is `a` + LF + `b` and `a b`: `multi_values` `["a\nb"]` on `note` →
  the `a` + LF + `b` row only; SearchBuilder `=` `a\nb` on `note` answers the same.
- **SPEC-F7.3** [all] Given rows whose `note` is `aa bb`, `aabb` and `bb`: global search `aa` + U+0020, LF, CR, U+2028
  or U+2029 + `bb` → the `aa bb` and `aabb` rows, each time: the five characters split words and nothing else does.
- **SPEC-F8.1** [all] Global search `red OR blue` → the rows holding `red`, `or` and `blue` → none in the fixture
  (no union of `red` and `blue`).
- **SPEC-F8.2** [all] Given a row whose `note` is `-red`: global search `-red` → that row only (not rows 1, 2, 4,
  which hold `red` without the `-`). On OpenSearch the column is a keyword column; on analysed text `SPEC-F11.9`
  holds.
- **SPEC-F8.3** [all] Global search `red*` and `re?` → none on the fixture alone; a row whose `note` is `red*` is
  found by `red*`.
- **SPEC-F8.4** [all] SearchBuilder `contains` `a*b` on `note` matches a row whose `note` is `xa*by` and not `xaby`.
- **SPEC-F8.5** [SQL] SearchBuilder `contains` `x_y` on `note` → rows 3, not a row whose `note` is `xay`; `contains`
  `50%` on `name` → rows 3, not a row whose `name` is `50 plum`.
- **SPEC-F8.6** [all] Global search `red NOT apple`, and the same with `not`, → none: `not` is a word (as a
  negation it would give rows 2, 4, as `red !apple` does, `SPEC-F5.3`).

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
- **SPEC-F9.8** [SQLite, PostgreSQL, MySQL with `utf8mb4_bin`, SQL Server with a `_CS_` collation, OS] Column search
  on `secret` with `alpha` → none; with `Alpha` → rows 1 (the library adds no folding on a hooked column, so its
  case-sensitive collation compares, PRD-D17).
- **SPEC-F9.9** [OS container] Global search `äpfel` → rows 4: a whole word, which the analysed text of `name`
  folds (OQ4 in `README.md`). (On a server from 3.1 on the keyword field's regular expression finds the row as
  well, so only a server before 3.1 proves the analysed fold: in CI the 2.19 leg.)
- **SPEC-F9.10** [OS container] Global search `äpf` → rows 4 from OpenSearch 3.1 on, none on 3.0 and 2.19; `ÄPF` →
  rows 4 on every version: a substring with a cased character outside ASCII is matched on the whole string by a
  case-insensitive regular expression, which folds such a letter from 3.1 on (OQ4).
- **SPEC-F9.11** [OS container] SearchBuilder `=` `äpfel` on `name` → rows 4 from OpenSearch 3.1 on, none on 3.0 and
  2.19; `=` `ÄPFEL` → rows 4 on every version. Given one more row whose `name` is `bär`: `=` `BÄR` → that row from
  3.1 on, none on 3.0 and 2.19 (OQ4).
- **SPEC-F9.12** [SQLite, PostgreSQL] Given `colour` declared as the expression `upper(colour)`: with a string
  type, SearchBuilder `=` `rEd` → rows 1, 4; without a type, `=` `rEd` → none and `=` `RED` → rows 1, 4.
- **SPEC-F9.13** [OS] A value with a character outside ASCII that has a case (a letter, a circled letter, a Roman
  numeral), small or capital, first or not (`ä.b`, `b.ä`, `B.Ä`, a circled `ⓓ` alone), is sent as a `regexp` query
  with `case_insensitive`: the whole value for `=`, open at both ends for `contains`, at its end for `starts`, at
  its start for `ends`. A negated condition is the same query in a `must_not`: `!starts` `ä` is the `must_not` of
  `ä.*`. Every character of the value that is neither alphanumeric, nor cased, nor whitespace, a fullwidth bracket
  as well as `.`, is behind a backslash; a cased character that is no letter (the circled `ⓓ`) and whitespace (a
  space, an ideographic space) are sent bare. Each value of a list gets its own query.
- **SPEC-F9.14** [OS] A value outside ASCII without case (`日本.`) is sent as the `term` an ASCII value gets.
- **SPEC-F9.15** [OS] With `ä` on a column declared `fold_case=False`: `=` is a plain `term`, and `ends` a `regexp`,
  as on any column.
- **SPEC-F9.16** [OS container] ASCII punctuation in a value beside a cased character outside ASCII is matched as
  written: `=` with every ASCII character that is not a letter or a digit, line feed, carriage return and the space
  included, finds only the row holding them; `=` with `@`, `\` or `~` between two letters finds only its row.
- **SPEC-F9.17** [OS container] On each server of PRD-Q14: `=` and `contains` with 255 ASCII letters in another case
  than stored → the row; `contains` with a value of 255 code points whose letter is outside ASCII → the row, for
  one letter repeated 255 times, for a letter outside the BMP and 254 dots (a pattern of 514 UTF-16 units), and for
  a letter outside the BMP and 254 characters outside the BMP that each get a backslash (768 units, the longest
  pattern a value makes).
- **SPEC-F9.18** [OS] A value longer than 255 code points is sent as the `term` or `wildcard` it would be without
  its cased character outside ASCII: a letter outside the BMP and 254 dots, 255 code points, is a `regexp`; with 255
  dots, 256 code points, a `term`.
- **SPEC-F9.19** [OS] SearchBuilder `contains` with `a`, a fullwidth `＊` (U+FF0A), `？` (U+FF1F) or `＼` (U+FF3C),
  and `b` on a keyword column is sent as a `regexp` query with `case_insensitive`, open at both ends, the mark
  behind a backslash. `=` with `a＊b` is the `term` it was, and `contains` with `＊` and 255 letters `a`, 256 code
  points, the `wildcard` it was.
- **SPEC-F9.20** [OS container] Given a keyword field with a normalizer of `asciifolding` and `lowercase`, holding
  `a*b` in row 1, `a＊b` in row 2, `a?b` in row 3, `a？b` in row 4, and `a\b`, `a＼b` and `axyb` in row 5:
  SearchBuilder `contains` `a＊b` → rows 1, 2; `a？b` → rows 3, 4; `a＼b` → rows 5.
- **SPEC-F10.1** [SQLite, PostgreSQL with its default collation] Global search `apfel` → none: the library folds no
  accents.
- **SPEC-F10.2** [MySQL with `utf8mb4_0900_ai_ci`] Global search `apfel` → rows 4: the collation, not the library,
  folds the accent.
- **SPEC-F11.1** [all] Global search `50%` → rows 3; given a row whose `name` is `50 plum`, it is not matched. On
  OpenSearch the column is a keyword column; on analysed text `SPEC-F11.9` holds.
- **SPEC-F11.2** [all] Column search on `note` with `x_y` → rows 3; a row whose `note` is `xay` is not matched.
- **SPEC-F11.3** [SQL] Given rows whose `note` is `[a]`, `a`, `a\b` and `ab`: column search `[a]` → the `[a]` row;
  `a\b` → the `a\b` row.
- **SPEC-F11.4** [OS] Given rows whose `note` is `a*b`, `axb`, `a?b`: column search `a*b` → the `a*b` row; `a?b` → the
  `a?b` row. On OpenSearch the column is a keyword column; on analysed text `SPEC-F11.9` holds.
- **SPEC-F11.5** [all] Global search `ple` → rows 1 (a word matches as a substring).
- **SPEC-F11.6** [OS] On a text-only `name` column: column search `red` → rows 1, not row 2 (`reddish`).
- **SPEC-F11.7** [OS] Given rows whose text is `a`, `b`, `a-b`, `b a` and `a b`, on a text column and on a
  text+keyword column: column search `a-b`, and SearchBuilder `contains` `a-b`, → the `a-b` and `a b` rows (a phrase of
  the word's tokens, not `a` alone); `!a-b` → the other three.
- **SPEC-F11.8** [SQL] Given a row whose `name` is `anna_100%` and one whose `name` is `annaX100Y`: SearchBuilder
  `starts` `anna_` → the first row only, and `ends` `00%` → the first row only; `!starts` `anna_` and `!ends` `00%` →
  every row but the first. A `_` and a `%` in the value are text, not wildcards. Given rows whose `name` is `a[bc` and
  `abc`: `starts` `a[b` → the first row only.
- **SPEC-F11.9** [OS] On a text+keyword column a word also matches the analysed text as a phrase of its tokens
  (PRD-F11), so what the analyser drops does not tell rows apart: given rows whose text is `-red` and `red`, global
  search `-red` → both rows; given rows ` a ` and `a`, `" a "` → both; given rows `a b”` and `a b`, `"a b”` → both;
  given rows `50%` and `50 plum`, global search `50%` → both; given rows `a*b` and `a?b`, column search `a*b` → both.

## Combining and refusing

- **SPEC-F12.1** [all] Column search `red` on `colour` and `apple` on `name` → rows 1.
- **SPEC-F12.2** [all] Column search `red` on `colour` and global search `pear` → rows 2; with global `plum` → none.
- **SPEC-F12.3** [all] Column search `red` on `colour` and SearchBuilder `=` `7` on `price` → rows 1 (the rows of the
  column search that the criterion keeps); the same column search beside a group OR of `price = 17` and `price` null
  → rows 4. The tree is a part of the request like the searches: it narrows them, it never widens them.
- **SPEC-F13.1** [all] On a table whose every column is declared unsearchable, a global search `red` → error_code
  `invalid_filter`.
- **SPEC-F13.2** [OS] On a table with only number, date and boolean columns, a global search `red` → error_code
  `invalid_filter`.
- **SPEC-F13.3** [all] On a table whose every column is declared unsearchable, an empty global search → rows 1, 2,
  3, 4 (nothing is asked).
- **SPEC-F13.4** [all] On a table whose every column is declared unsearchable, a global search that PRD-F6 reduces
  to no word, a lone `"`, is idle → rows 1, 2, 3, 4 (nothing is asked).
- **SPEC-F14.1** [all] A global search of one word of 255 code points is answered; 256 → error_code
  `value_too_long`. The same holds for a word of 255 supplementary-plane characters (each one code point) and 256 of
  them.
- **SPEC-F14.2** [all] A global search of 16 words is answered; 17 → error_code `too_many_words`; the same for a
  column search on `name`.
- **SPEC-F14.3** [all] 16 words plus a quoted phrase of two words is 17 words → `too_many_words`; 15 words plus a
  quoted phrase of two words is 16 → answered (counting the phrase as two words would refuse it); 16 words plus `"`,
  `!""` and `!x` is 16 → answered.
- **SPEC-F14.4** [all] Every value is bound at 255 code points: a column search, a `multi_values` entry, an
  alternative of a categorical filter, a value a SearchBuilder condition reads (`=`, `contains`, `starts`, `between`'s
  second value, a number, a date, a boolean) → answered at 255, error_code `value_too_long` at 256; the same for a
  number of 4,301 digits and for 5,000 zeros, which are no `invalid_number`. A surplus value of a condition and the
  value of `null` are not measured: `null` with a value of 300 code points is answered.
- **SPEC-F14.5** [all] Step 1 is found before every refusal of step 4 in the same part of a request: 17 words with
  `regex` true, 17 words and no searchable column, a global search of 256 code points with `multi_values`, and a
  column search of 256 code points on a name the server does not declare, each answer the code of step 1, not
  `invalid_filter`. Within one filter, a control character in any value comes before the length of any value, which
  comes before the words: `multi_values` `["a" x 256, "b\x01"]` → `invalid_character`.
- **SPEC-F14.6** [all] A column search on `name` with `regex: true` and `a.` followed by 256 `x` → error_code
  `invalid_filter`, not `value_too_long`: the unescaped `.` makes it no categorical filter, so PRD-F14 measures no
  alternative (after unescaping, not the raw pattern) and PRD-F18 refuses it as any other regex. A global search with
  `regex` true is still measured whole as the global value (`a.` + 256 `x` → `value_too_long`; `a. ` + 17 words →
  `too_many_words`, `SPEC-F14.5`), whereas the same text as a column regex is refused as a regex first
  (`invalid_filter`); a control character in a column regex is still found (`a.` + U+0001 → `invalid_character`), as
  PRD-F15 has no "not the raw pattern" clause.
- **SPEC-F15.1** [all] U+0000 in the global search → error_code `invalid_character`; so do U+0009 (TAB), U+001B and
  U+007F.
- **SPEC-F15.2** [all] U+0000 in a column search, in a SearchBuilder `=` value, in a categorical value and in a
  `multi_values` entry → error_code `invalid_character`, before any query runs.
- **SPEC-F15.3** [all] LF and CR in a global search are accepted (PRD-F7).

## Categorical filters and `multi_values`

- **SPEC-F16.1** [all] Column search on `colour` with `regex: true` and `red` → rows 1, 4 (not row 2: an exact
  match).
- **SPEC-F16.2** [all] With `regex: true`: `red|blue`, `^red$` and `^(red|blue)$` on `colour` → rows 1, 3, 4; rows 1,
  4; rows 1, 3, 4.
- **SPEC-F16.3** [all] With `regex: true`, given a row whose `note` is `a-b/c`: `^(a-b/c)$` and `^(a\-b\/c)$` → that
  row.
- **SPEC-F16.4** [all] A column search on `colour` with an empty value and `regex: true` is idle → rows 1, 2, 3, 4
  (no value, so no categorical filter; the reviewer's decision of 2026-11-20 in `README.md`).
- **SPEC-F17.1** [all] With `regex: true`: `^(|x_y)$` on `note` → rows 1, 3 on OS (missing, `x_y`); rows 1, 2, 3 on
  SQL (NULL, `''`, `x_y`).
- **SPEC-F17.2** [all] With `regex: true`: `^(|red)$` on `colour` → rows 1, 4 (no row is empty).
- **SPEC-F17.3** [all] With `regex: true`: `^()$` on `note`, a group holding only the empty alternative, → rows 1, 2
  on SQL (NULL, `''`) and rows 1 on OS (missing).
- **SPEC-F17.4** [all] With `regex: true` on `note`: `^$` and a bare `|` → rows 1, 2 on SQL and rows 1 on OS, as F17.3
  gives; `x_y|` and `^(x_y||zzz)$` → rows 1, 2, 3 on SQL and rows 1, 3 on OS, as F17.1 gives.
- **SPEC-F18.1** [all] With `regex: true`, each of `^a|b$`, `a.b`, `a*`, `(a)`, `[ab]`, `a{2}`, `\d`, `\w`, `\n`,
  `a+`, `a?` and `^(a|b)` → error_code `invalid_filter`.
- **SPEC-F18.2** [all] A global search with `search[regex]` true and a value holding at least one word, `red` or
  `^(red)$`, → error_code `invalid_filter`: the categorical forms of PRD-F16 are column searches only, so any regex in
  the global search is "any other regex".
- **SPEC-F18.3** [all] A global search with `search[regex]` true and an empty value, or one that PRD-F6 reduces to
  nothing (three spaces, a lone `"`), is idle: the base request with it → rows 1, 2, 3, 4, as without the flag
  (the reviewer's decision of 2026-11-20 in `README.md`).
- **SPEC-F19.1** [all] With `regex: true`, every character of `/ . * + ? | ( ) [ ] { } \ $ ^ -` escaped by a
  backslash inside `^(...)$` matches itself: given a row whose `note` is `/.*+?|()[]{}\$^-` (the whole set),
  `^(\/\.\*\+\?\|\(\)\[\]\{\}\\\$\^\-)$` → that row only.
- **SPEC-F19.2** [all] With `regex: true`, given a row whose `note` is `C:\`: each of `^C:\\$`, `^(C:\\)$` and `C:\\`
  → that row (after an escaped backslash the `$` is the anchor).
- **SPEC-F20.1** [all] `multi_values` `["red", "blue"]` on `colour` → rows 1, 3, 4.
- **SPEC-F20.2** [all] `multi_values` `["", "x_y"]` on `note` → rows 1, 3 on OS; rows 1, 2, 3 on SQL.
- **SPEC-F20.3** [all] A column search on `colour` with an empty value and an empty `multi_values` list is idle →
  rows 1, 2, 3, 4.
- **SPEC-F20.4** [SQL] `multi_values` `[" "]` on `note` → never row 1: on a string column a space is a value, not an
  empty entry. [SQLite, PostgreSQL] and [MySQL, default collation] → none. [SQL Server] → row 2, whose `''` SQL
  Server compares equal to a space (PRD section 5).

## Dates

- **SPEC-F21.1** [all] SearchBuilder on `made`: `=` `2024-03-05` → rows 1; `<` `2024-03-05` → rows 4; `<=` → rows 1,
  4; `>` → rows 2; `>=` → rows 1, 2; `between` `2024-01-01` and `2024-03-05` → rows 1.
- **SPEC-F21.2** [all] SearchBuilder on `at`: `=` `2024-03-05` → rows 1; `=` `2024-03-06` → rows 2 (UTC days, though
  the two instants are an hour apart).
- **SPEC-F21.3** [PostgreSQL] With the session `TimeZone` set to a zone far from UTC, F21.2 holds unchanged.
- **SPEC-F21.4** [SQL] With `at` redeclared as a naive `DateTime` and row 1 storing `2024-03-05T10:00`:
  SearchBuilder `=` `2024-03-05` on `at` → rows 1.
- **SPEC-F21.5** [all] Categorical `^(2024-03-05)$` and `multi_values` `["2024-03-05"]` on `made` → rows 1; on `at`
  `["2024-03-06"]` → rows 2. [SQL] The same holds on an expression without a SQL type that is declared a date and
  holds a time.
- **SPEC-F21.6** [all] `multi_values` `["2024-13-01"]` or `["05.03.2024"]` on `made` → error_code `invalid_date`.
- **SPEC-F21.7** [all] `multi_values` `[""]` on `made` → rows 3 (NULL on SQL, missing on OS). An entry of only white
  space is empty too: white space around a value is ignored first.
- **SPEC-F21.8** [SQLite] Given a row whose `at` is written as the aware value `2024-03-06T01:00+02:00` (the instant
  2024-03-05T23:00Z): `=` `2024-03-06` matches it and `=` `2024-03-05` does not, since the offset is dropped and the
  stored `01:00` is read as UTC.
- **SPEC-F21.9** [all] A day followed by a time of day, `2024-03-05 10:00:00.250` or `2024-03-05T23:59`, and a day
  with white space around it, is read as `2024-03-05`: SearchBuilder `=` on `made` with it answers as with the day
  alone. (A spelling is read by the plan, which every backend shares; this and the other statements on spellings,
  F21.10, F22.5 and F25.3, are checked where the value is read, on SQL.)
- **SPEC-F21.10** [all] SearchBuilder `=` on `made` with `2024-03-05xyz`, `2024-03-05T10:00Z`, `20240305`,
  `2024-03-05T10:00+02:00`, `2024-03-05 24:00`, `2024-03-05T10`, a time in digits outside ASCII or only white space
  → error_code `invalid_date`; so does `contains` with the day in digits outside ASCII.
- **SPEC-F21.11** [all] SearchBuilder on `at` compares the UTC day for every range condition, as `=` does (F21.2):
  `>` `2024-03-05` finds row 2 and not row 1; `<` `2024-03-06` finds row 1 and not row 2; `>=` `2024-03-06` finds row 2
  and not row 1; `<=` `2024-03-05` finds row 1 and not row 2; `between` `2024-03-05` and `2024-03-05` finds row 1 and
  not row 2 (both ends are whole days); `!between` of the same keeps row 2 and every row without an `at`.
- **SPEC-F21.12** [MySQL, MariaDB] Given a `TIMESTAMP` column holding 23:30 UTC on 2026-07-15, written in a session at
  `+00:00`: in a session at `+02:00`, where it reads as 01:30 on the 16th, SearchBuilder `=` `2026-07-16` → that row
  and `=` `2026-07-15` → none; back in a session at `+00:00`, `=` `2026-07-15` → that row and `=` `2026-07-16` → none.
- **SPEC-F22.1** [all] SearchBuilder `contains` on `made`: `2024` → rows 1, 2; `2024-03` → rows 1; `2024-03-05` →
  rows 1; `!contains` `2024` → rows 3, 4.
- **SPEC-F22.2** [all] SearchBuilder `contains` on `made` with `2024-3`, `24` or `2024-03-5` → error_code
  `invalid_date`.
- **SPEC-F22.3** [all] SearchBuilder `contains` `2024-03-06` on `at` → rows 2 (UTC span).
- **SPEC-F22.4** [OS] Column search on `made` with `2024-12` → rows 2.
- **SPEC-F22.5** [all] SearchBuilder `contains` on `made` reads `2024-03-05 10:00:00` as `=` reads it, the day
  `2024-03-05`, and a year with white space around it as the year; `2024` in digits outside ASCII, and
  `2024-03-05xyz` → error_code `invalid_date`.
- **SPEC-F23.1** [SQL] Global search `17` → rows 2; column search `7` on `price` → rows 1, 2.
- **SPEC-F23.2** [SQL] Column search `2024-12` on `made` → rows 2; `03-05` → rows 1 (date text is `YYYY-MM-DD`).
- **SPEC-F23.3** [SQL] Global search `2023` → rows 4 (the `made` and `at` text).
- **SPEC-F23.4** [SQL] Given a double-precision column and a `Numeric(10, 2)` column, each holding 17, 12.5, 1234567
  and -3.25: a column search `7` → the rows of 17 and 1234567; `12.5` → the row of 12.5; `1234567` → the row of
  1234567; `-3.25` → the row of -3.25. A search for a number as the table shows it finds its row.
- **SPEC-F24.1** [all] SearchBuilder on `made` with the maximum date as a bound → error_code `invalid_date`, not a
  database error, in each of: `=`, `!=`, `<`, `<=`, `>`, `>=` with `9999-12-31`; `between` and `!between` with
  `9999-12-31` as the first or the second bound; `contains` and `!contains` with `9999-12-31`, `9999-12` or `9999`.
- **SPEC-F25.1** [all] SearchBuilder on `price`: `=` `abc` or `1e999` (written as a number, and not finite) →
  error_code `invalid_number`. So does a whole number of 5,000 digits: it is no failed request.
- **SPEC-F25.2** [SQL] On `price` (a `BigInteger`), `=` `9223372036854775808` → error_code `invalid_number`;
  `9223372036854775807` is answered. With `price` redeclared as a `SmallInteger`, `=` `32768` → `invalid_number`.
  Given a row whose `price` is -9223372036854775807: `=` `-9223372036854775807` → that row (a whole number is
  compared as the integer written, not as a float, which would hold -9223372036854775808).
- **SPEC-F25.3** [all] SearchBuilder `=` on `price` reads `+7`, `7.`, `.7E+1` and `7` with white space around it as
  the number 7. With `1_000`, a second sign (`--7.5`), or `7` in a digit outside ASCII (the full-width `７`,
  U+FF17) → error_code `invalid_number`.
- **SPEC-F25.4** [SQL] A whole number outside the signed 64-bit range. With `price` redeclared as a `Float`, given
  a row whose `price` is 18446744073709551616 (2^64): `=` `18446744073709551616`, and that number as a
  `multi_values` entry, → that row; `=` `9223372036854775808` (2^63) → none; `=` with a whole number of 255
  digits → none (a whole number of 256 digits is `value_too_long`). With `price` an expression without a SQL type,
  declared a number: `=` `18446744073709551616` → that row.
- **SPEC-F25.5** [SQL] With `price` redeclared as a `Numeric(32, 2)`, given a row whose `price` is
  18446744073709551616: `=` `18446744073709551616` → that row; `<` with a whole number of 31 digits → that row (the
  declared precision bounds nothing). `=` `18446744073709551617` [PostgreSQL, MySQL, SQL Server] → none; [SQLite]
  → that row (PRD section 5). `=` with a whole number of 39 digits, and `=` `1e39`, [SQLite, PostgreSQL, MySQL] →
  none; [SQL Server] → error_code `invalid_number`.
- **SPEC-F25.6** [SQL] With `price` redeclared as MySQL's unsigned `BIGINT`: `=` `-1` → error_code `invalid_number`.
  [MySQL] Given a row whose `price` is 18446744073709551615: `=` `18446744073709551615` → that row.
- **SPEC-F25.7** [SQL] With `price` redeclared as a `Numeric(32, 2)`, given a row whose `price` is
  18446744073709551616: a `multi_values` list of `1` and `18446744073709551616` → that row, on SQLite too (which
  sends the nearest float).
- **SPEC-F25.8** [SQLite] With `active`, a Boolean column declared a number: a `multi_values` list of `2` → answered,
  no row. (PostgreSQL and SQL Server refuse such a comparison themselves.)
- **SPEC-F25.9** [all] SearchBuilder `=` on `price` with `nan`, `NaN`, `inf`, `-inf` or `Infinity` → error_code
  `invalid_number`: Python's own number reader takes these texts, and no column holds such a number (PRD-F25).
- **SPEC-F25.10** [all] On `price`, an integer column, given a row whose `price` is 8: `>` `7.5` finds it, `<` `7.5`
  does not, `=` `8.0` finds it, `=` `7.5` finds nothing, `between` `7.5` and `8.5` finds it: a number with a fraction
  is compared as that number, not cut to a whole one.
- **SPEC-F25.11** [SQL] With `n`, a `Numeric(20, 2)` column, given rows 12345678901234567.89, 12345678901234567.90 and
  12345678901234568.00: `=` `12345678901234567.89` → the first row only, and `<` `12345678901234567.90` → the first
  row and every row below it, through `fetch` and `fetch_sync`; `=` `0.305` → no row. On SQLite, which stores the
  column as a float or an integer, the three rows are one number and are found together (PRD section 5).
- **SPEC-F25.12** [SQL] With the same `n`: a number of 38 digits in all (before and after the point, a fraction's
  trailing zeros not counted) is answered on every database. [SQL Server] A number of 39 or more digits → error_code
  `invalid_number` for every condition, never the driver's own error; so is a fraction of 39 or more digits in all (`8.`
  followed by 38 ones) on `i`, a plain `Integer` column that compares it exactly.
- **SPEC-F25.13** [SQLite] With `n`, a `TypeDecorator` over `Numeric` that stores hundredths (1.5 as 150), given rows
  1.5 and 2: `=` `1.5` → the first row, `=` `2` and `=` `2.0` → the second, `=` `3.5` → none: the number goes through
  the column's own type.
- **SPEC-F25.14** [all] `=` `1e-16384` → error_code `invalid_number` with `The filter on "i" needs a number with at most
  16383 digits after the decimal point.`; `1e-16383` is answered, except on SQL Server, whose 38 digits refuse it on a
  `Numeric` column, or on an integer column not behind a `TypeDecorator` (`SPEC-F25.12`, `SPEC-F25.16`). A nonzero
  number with a negative exponent beyond what a decimal holds (`1e-9999999999999999999`, also with `E`) gets the same
  reason; `1e9999999999999999999` and `0e-9999999999999999999` (zero has no fraction) get `The filter on "i" needs one
  number, written like 42 or -3.5.`
- **SPEC-F25.15** [OS] With `n`, a number column without a kind and then declared a `long`: SearchBuilder `=`, `>=`
  and `<` with `-0.00` send the body of `0`, and a `multi_values` list of `-0.00` and `7` sends that of `0` and
  `7` on the column without a kind. With `n` over a `double` field, given a document holding 0.0: `=` and `<=`
  with `-0.00` → that document.
- **SPEC-F25.16** [all] With a number column (`exact`, a `Numeric`, on SQL; on OpenSearch a number column, which gets a
  float, or `0` for zero): the limit of 16383 digits after the point is of the value, not of how it is written: `=`
  `1e-16383`, `1.0e-16383`, `100e-16385` and `0e-16384` are answered (a fraction's trailing zeros are not counted and
  zero has no fraction), and `1e-16384`, `1.0e-16384`, `-1.0e-16384` and `1.5e-16383` → error_code `invalid_number` (the
  last digit is past the limit, whatever the first; the plain spellings `1e-16383` and `1e-16384` are those of
  `SPEC-F25.14`, here as the control for the others). The refusals hold whatever the column; on SQL they are checked on
  `i`, with compiled PostgreSQL and SQL Server statements. [SQL Server] The first three answered → error_code
  `invalid_number`, by its 38 digits (`SPEC-F25.12`); `0e-16384` is answered. [PostgreSQL] The number reaches the
  database with its sign and its digits, the zeros of its fraction left out: `-1.0e-16383` is bound as `-1E-16383`
  (checked on a compiled statement of `n`, a `Numeric(20, 2)`).
- **SPEC-F26.1** [SQL] SearchBuilder `null` on `note` → rows 1, 2; `!null` → rows 3, 4.
- **SPEC-F26.2** [OS] SearchBuilder `null` on `note` → rows 1 (a stored `''` is not missing); `!null` → rows 2, 3, 4.
- **SPEC-F26.3** [SQL] `null` finds the rows holding `''` beside those holding NULL on: a column behind a
  `TypeDecorator` over a string type, also one over another `TypeDecorator`; `note` declared a date; an expression
  without a SQL type, declared a string; an `Enum` column that names `''` as a value. It finds the NULL rows only on:
  the same expression declared a date; an `Enum` column without `''` among its values; a `Uuid` column; a column
  behind a `TypeDecorator` that stores a UUID as `CHAR(32)` and on PostgreSQL as its `UUID` type (SQLAlchemy's
  GUID recipe), which holds no `''`.
- **SPEC-F26.4** [SQL] `multi_values` `[""]` on a `Uuid` column, and on a column behind a `TypeDecorator` that
  refuses to convert `''`, → the rows holding NULL (and `''` where the stored type is text); the entry is never
  sent as a value.
- **SPEC-F26.5** [SQL] On a source that turns the texts `NULL` (row 6) and `-` (row 7) of `email` into NULL, with
  `NULLIF` or with a `CASE`: `null` and `!null`, a list holding `""`, the cell and the ascending order agree on which of
  those rows are empty, which are the NULL rows and the rows holding `''`, plus the sentinel rows the expression
  converts; on the plain column `null` finds neither sentinel.

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
- **SPEC-F27.7** [all] SearchBuilder sends `value1` and `value2` beside `value`; they are ignored: `=` on `name` with
  `Red Apple` and them → rows 1.
- **SPEC-F27.8** [all] On `made` (date), `!starts` and `!ends` → error_code `invalid_filter`, as `starts` and `ends`
  are (F27.3).
- **SPEC-F27.9** [all] The two values of `between` and `!between` are a range in either order: on `price`, `between`
  `17` and `8` → rows 2, 3, as `between` `8` and `17` (the numbers are compared, not their texts), and `!between` `17`
  and `8` → rows 1, 4 (PRD-F37); on `made` (date), `between` `2024-12-31` and `2024-03-05` → rows 1, 2 (both ends
  whole days).
- **SPEC-F28.1** [all] Group OR of `colour = blue` and `price = 7` → rows 1, 3.
- **SPEC-F28.2** [all] Group AND of `colour = red` and a nested OR group of `price = 17` and `price null` → rows 4
  (dropping the nested group would give rows 1, 4; OR throughout would give rows 1, 2, 4).
- **SPEC-F28.3** [all] An empty group adds nothing: alone → rows 1, 2, 3, 4; nested in an AND group with
  `colour = blue` → rows 3; nested in an OR group with `colour = blue` → rows 3 (an empty group read as true would
  give every row).
- **SPEC-F30.1** [all] A criterion with no condition (the key missing, or `""`) adds nothing: alone → rows 1, 2, 3,
  4; in an AND group with `colour = blue` → rows 3; in an OR group with `colour = blue` → rows 3 (read as true it
  would give every row in the OR group, read as false none in the AND group).
- **SPEC-F30.2** [all] A criterion with no column (`origData` missing, or `""`) adds nothing, with the three results
  of `SPEC-F30.1`.
- **SPEC-F30.3** [all] `=` on `name` with no `value` key or with `[]`, and `between` on `price` with one value, add
  nothing, with the three results of `SPEC-F30.1`.
- **SPEC-F30.4** [all] `between` on `price` with the one value `abc` → error_code `invalid_number`: a value that is
  there but unreadable is refused, not left out.
- **SPEC-F30.5** [all] A value that is the empty text counts as missing, as in SearchBuilder's client: `=` on `name`,
  `price` or `made` with `[""]`, and `between` on `price` with `["", "7"]` or `["7", ""]`, add nothing, with the
  three results of `SPEC-F30.1`. A value of one space is a value: `=` on `name` with `[" "]` → no row. A surplus is
  refused first: `=` on `name` with `["", ""]` → error_code `invalid_filter`.
- **SPEC-F30.6** [all] As a form sends it, the condition `undefined` is no condition: a new criterion
  (`condition`, `data` and `origData` the text `undefined`), one with a column and the condition `undefined`, and one
  with the condition `=` and no `value` key each add nothing, with the three results of `SPEC-F30.1`.
- **SPEC-F31.1** [all] With the base filter `price` not null: no filter → rows 1, 2, 3, `recordsTotal` 3,
  `recordsFiltered` 3; global search `red` → rows 1, 2, `recordsTotal` 3, `recordsFiltered` 2.
- **SPEC-F31.2** [all] With the base filter `price` not null and the scope `id` above 1 (SQL `id > 1`, OS `range id gt
  1`) passed to `fetch`: no filter → rows 2, 3, `recordsTotal` 2, `recordsFiltered` 2; global search `red` → row 2,
  `recordsTotal` 2, `recordsFiltered` 1; global search `apple` → no rows, `recordsTotal` 2, `recordsFiltered` 0.
  The two apply together: the base filter alone gives rows 1, 2, 3 and the scope alone rows 2, 3, 4.
- **SPEC-F31.3** [all] A scope of `None`, and on OS an empty mapping, answers exactly as no scope does: on SQL the same
  rows and counts, on OS the same request bodies sent.
- **SPEC-F32.1** [OS] Global search `17` → none; `2024` → none (the number and date columns are skipped).
- **SPEC-F33.1** [OS] Column search on `price`: `7` → rows 1 (not 17).
- **SPEC-F33.2** [OS] Column search on `price`: `7 8`, `!78`, `"7"` and `abc` → error_code `invalid_number`.
- **SPEC-F33.3** [all] Categorical `^(7|8)$` and `multi_values` `["7", "8"]` on `price` → rows 1, 3.
- **SPEC-F33.4** [all] `multi_values` `["x"]` on `price` → error_code `invalid_number`; `[""]` → rows 4.
- **SPEC-F33.5** [all] `multi_values` `[" ", "7"]` on `price` → rows 1, 4 (white space is an empty entry).
- **SPEC-F34.1** [OS] Column search on `made`: `2024` → rows 1, 2; `2024-03-05` → rows 1; `2024-03-05 10:00:00`, a
  day with a time after it (PRD-F22) → rows 1.
- **SPEC-F34.2** [OS] Column search on `made`: `2024 2023`, `!2024`, `"2024"` and `2024-13` → error_code
  `invalid_date`.
- **SPEC-F34.3** [OS] PRD-F6 reads the whole text first, on `made` and on `price` alike: `!7`, a negation PRD-F35
  drops, holds no word and adds nothing (the request sends what it sends without that text); `!x 2024` and `"" 2024`
  hold a word beside `!` or quotes, are read as one term and are error_code `invalid_date` on `made`, as `!x 7` and
  `"" 7` are `invalid_number` on `price` (PRD-F33), and nothing is sent.
- **SPEC-F35.1** [all] Given rows 5 (`note` `a-b`) and 6 (`note` `a.b`), every other column of both but `id` NULL
  or missing: global search `!-` → rows 3, 6 on SQL (the date text of rows 1, 2, 4 holds `-`) and rows 1, 2, 3, 4, 6
  on OS (dates take no part, PRD-F32); `!.` → rows 1, 2, 3, 4, 5 on all.
- **SPEC-F35.2** [all] Global search `!x` → rows 1, 2, 3, 4 (adds nothing).
- **SPEC-F36.1** [all] `=` on `name` with two values, and `between` on `price` with three → error_code
  `invalid_filter`.
- **SPEC-F36.2** [all] `null` on `note` with the values `[]` → rows 1, 2 on SQL, rows 1 on OS; with `[""]` → the
  same; with `abc` and a value holding U+0000 → the same, no refusal (PRD-F15 does not apply).
- **SPEC-F36.3** [all] `null` with a value that is not text → `MalformedRequest` (PRD-P6).
- **SPEC-F36.4** [all] `=` on `name` with `Bob` and a second value holding U+0000 → error_code `invalid_filter` (the
  surplus value is not read, so no `invalid_character`); `=` on `price` with `abc` and `1` → error_code
  `invalid_number` (the value the condition reads is checked first, PRD-E3).
- **SPEC-F37.1** [all] On `note`: `!=` `x_y` → rows 1, 2, 4; `!contains` `pipe` → rows 1, 2, 3; `!starts` `5` →
  rows 1, 2, 3; `!ends` `y` → rows 1, 2, 4. Row 1 (NULL or missing) satisfies each.
- **SPEC-F37.2** [all] On `price`: `!=` `7` → rows 2, 3, 4; `!between` `0` and `100` → rows 4.
- **SPEC-F37.3** [all] On `made` (date): `!=` `2024-03-05`, and `!between` `2024-03-05` and `2024-03-05`, keep row 3,
  whose `made` is NULL or missing.
- **SPEC-F38.1** [all] A SearchBuilder tree 8 groups deep, the root group counted as depth 1, is answered; 9 deep →
  error_code `invalid_filter`. A root group holding 9 groups side by side, each with one criterion, is answered: they
  are 2 deep.
- **SPEC-F38.2** [all] 64 criteria in all are answered; 65 → error_code `invalid_filter`, also when they are spread
  over two groups of 32 and 33 in the root.
- **SPEC-F38.3** [all] A categorical filter or `multi_values` list of 256 values is answered; 257 → error_code
  `invalid_filter`. A categorical regex of 256 values is longer than 255 code points; that is intended, since its
  values are exact values, not search text (PRD-F7), and PRD-F38 allows 256 of them.
- **SPEC-F38.4** [all] A tree 10,000 groups deep → `MalformedRequest`, with no `RecursionError`: the parser refuses
  a tree deeper than it can walk (PRD-P6).
- **SPEC-F38.5** [all] Two lists of 256 values each, on `name` and `email` (SQL) or `title` and `author` (OS), are
  answered; with a third list of one value on `team` (SQL) or `id` (OS) → error_code `invalid_filter`: all the lists
  of one request together hold at most 512 values.
- **SPEC-F38.6** [all] The column `name` (SQL) or `title` (OS) named 64 times in the request, each with the search
  `Ann`, is answered, also with 100 more columns, half of them with no search and half with a search of two spaces;
  named 65 times → error_code `invalid_filter`. A column search that holds a value counts; a column without one, or
  with spaces only (PRD-F6), does not.
- **SPEC-F38.7** [all] A global search `ann` on a request that names `name` (SQL) or `title` (OS) 1,000 times asks the
  database or the cluster exactly what it asks when the column is named once.
- **SPEC-F38.8** [all] The column `name` named twice in the request, one entry `searchable` true and the other
  false, in either order, with the other columns marked not searchable: a global search `Apple` → rows 1, found in
  `name` alone. With both entries false → error_code `invalid_filter` (PRD-F13).
- **SPEC-F39.1** [all] A column search on `colour` with value `red` and `multi_values` `["blue"]` → error_code
  `invalid_filter` (a value with a word, see `SPEC-F6.6`).
- **SPEC-F40.1** [all] A global search with `multi_values` `["red"]` → error_code `invalid_filter`; with `[]` → rows
  1, 2, 3, 4.
- **SPEC-F40.2** [all] A global search with `multi_values` holding one entry with the control character U+0001, or
  one entry of 256 characters → error_code `invalid_filter`, not `invalid_character` or `value_too_long`: the form is
  refused without its entries being read (PRD-F40).
- **SPEC-F40.3** [all] A global search with `multi_values` holding 600 entries (more than PRD-F38's 256 per list and
  512 per request) beside SearchBuilder `=` `abc` on `price` → error_code `invalid_number`: the global list is neither
  read nor counted, so the mistake of step 2 is answered; the same global list alone → `invalid_filter` (PRD-F40).

## Searchable, timezone text, booleans

- **SPEC-F41.1** [all] A request column `extra` (searchable, undeclared) with global search `red` → rows 1, 2, 4.
- **SPEC-F41.2** [all] With `name` declared unsearchable: global search `apple` → none; a column search `apple` on
  `name` → error_code `invalid_filter`, whatever the request's `searchable`; a SearchBuilder criterion on `name` →
  error_code `invalid_filter`.
- **SPEC-F41.3** [all] With `name` marked `searchable: false` in the request: global search `apple` → none; column
  search `apple` on `name` → rows 1.
- **SPEC-F41.4** [all] A column search with a value (a value with a word, see `SPEC-F6.6`), or a non-empty
  `multi_values` list, on the undeclared `extra` → error_code `invalid_filter`; an empty one is answered (PRD-D6).
- **SPEC-F42.1** [PostgreSQL] With the session `TimeZone` `America/Los_Angeles`: global search `2024-03-05` → rows 1
  (not row 2); column search `2024-03-06` on `at` → rows 2.
- **SPEC-F42.2** [SQL Server] Given `at` as `DATETIMEOFFSET` with row 1 stored as `2024-03-06T04:30+05:00`: column
  search `2024-03-05` on `at` → rows 1.
- **SPEC-F43.1** [all] SearchBuilder `=` on `active` with `true`, `TRUE`, `True` or `1` → rows 1, 4; with `false` or
  `0` → rows 2.
- **SPEC-F43.2** [all] `!=` `true` on `active` → rows 2, 3 (NULL passes, PRD-F37); `null` → rows 3; `!null` → rows
  1, 2, 4.
- **SPEC-F43.3** [all] On `active`: `=` with `yes`, `2` or `t`, and any of `<`, `between`, `contains`,
  `starts` → error_code `invalid_filter`, never a database error.
- **SPEC-F43.4** [all] Whitespace around a boolean value is ignored, as around a number or a date: `=` on `active`
  with ` true ` → rows 1, 4; with ` 0 ` → rows 2; with three spaces → error_code `invalid_filter`.
- **SPEC-F44.1** [all] Global search `true` → none (the boolean column is skipped; no other column holds `true`).
  On a table whose only searchable column is `active`, global search `x` → error_code `invalid_filter` (no usable
  column, PRD-F13). On SQLite, MySQL and SQL Server, where a boolean's text holds no `true`, only this second clause
  catches a boolean column the global search fails to skip.
- **SPEC-F44.2** [all] Column search on `active`: `true` → rows 1, 4; `0` → rows 2; `yes` → error_code
  `invalid_filter`.
- **SPEC-F44.3** [all] Categorical `^(true|0)$` on `active` → rows 1, 2, 4; `multi_values` `["", "1"]` → rows 1, 3,
  4.
- **SPEC-F44.4** [all] `multi_values` `[" ", "1"]` on `active` → rows 1, 3, 4 (white space is an empty entry).
- **SPEC-F45.1** [core] A ColumnControl search is read with its value and its logic (`ann`, `contains`), and a list with
  its entries as text in index order: the JSON `{"1":"b","0":""}` and the form `[list][1]=b&[list][0]=` both give `""`
  then `b`, the empty entry being the client's "Empty" option.
- **SPEC-F45.2** [all] ColumnControl `contains` `red` on `colour` gives the rows of a column search `red`; on a number
  column, a date column and a text column alike. On OS it sends the body of that column search, whose rows the live
  module pins (`test_a_column_search`).
- **SPEC-F45.3** [all] On `colour`: `equal` `red` → rows 1, 4; `notEqual` `red` → 2, 3; `starts` `red` → 1, 2, 4; `ends`
  `ish` → 2; `notContains` `red` → 3; `equal` of two spaces → no rows, since a value of white space is a value
  (PRD-F30) and `notEqual` of it → every row. On OS each sends the body of the SearchBuilder condition it names (`=`,
  `!=`, `starts`, `ends`, `!contains`) on a text column, and `equal` that of `=` on a number and a date column, whose
  rows the live module pins (`test_searchbuilder_conditions`); the other logics are refused there (`SPEC-F45.8`).
- **SPEC-F45.4** [all] `empty` on `note` → rows 1, 2 and `notEmpty` → 3, 4 on SQL (`SPEC-F26.1`). On OS they send the
  bodies of `null` and `!null`, whose rows the live module pins (`test_searchbuilder_conditions`, on `author`;
  `test_a_stored_empty_text_is_not_a_missing_field`, `SPEC-F26.2`).
- **SPEC-F45.5** [all] A list `red`, `blue` on `colour` → rows 1, 3, 4 (`SPEC-F20.1`); a list `""`, `x_y` on `note` → rows
  1, 2, 3 on SQL (`SPEC-F20.2`). On OS a list sends the body of the same `multi_values`, on text, number and date
  columns, whose rows the live module pins (`test_multi_values_match_exactly`, and
  `test_an_empty_alternative_or_entry_matches_the_documents_without_the_field` for the empty entry).
- **SPEC-F45.6** [all] A column search `red` and a ColumnControl `starts` `reddish` on `colour` → row 2 (AND); a column
  search, a ColumnControl search and a ColumnControl list on one column are three clauses, ANDed, and a ColumnControl
  search and list on one column are not refused as a value beside a list (PRD-F39). When two of them are refused at the
  same step, the answer is the first in that order: the plain search's message, then the ColumnControl search's, then
  the list's (PRD-E3).
- **SPEC-F45.7** [all] `empty` and `notEmpty` carrying the text `x`, and carrying 300 characters, answer as `empty` and
  `notEmpty` alone: no `value_too_long`, and on OS the clause is that of `null` and `!null`.
- **SPEC-F45.8** [all] `starts`, `ends` and `notContains` on `price`, and `starts` on `made` → `invalid_filter`, and
  `equal` `abc` on `price`
  and a list `abc` → `invalid_number`. [OS] On the analysed `body`: `equal`, `notEqual`, `starts`, `ends` and a list of
  values → `invalid_filter` with nothing sent; `contains`, `notContains`, `empty`, `notEmpty` and a list of `""` alone
  are answered (`SPEC-D19.1`).
- **SPEC-F45.9** [SQL] On the hooked `secret`: `contains` `Alpha` → row 1, `equal` and a list as well; `starts` →
  `invalid_filter`; `empty` and `notEmpty` are answered (PRD-D17, PRD-D18).
- **SPEC-F45.10** [SQL] A list of 256 is answered and one of 257 → `invalid_filter`, as are three lists of 256 (PRD-F38);
  60 searches of 16 words are answered and a 61st word → `request_too_large` with nothing run (PRD-C17); a search and a
  list of one column are two of the 64 column searches: 32 columns of both are answered, and a 33rd column's search
  → `invalid_filter`; 70 columns each with ColumnControl's idle search and empty list count for nothing and are
  answered.
- **SPEC-F45.11** [SQL] A control character in a ColumnControl value (`contains`, `equal`, a list entry) →
  `invalid_character`, and a value over 255 code points → `value_too_long`, each before the `invalid_filter` of another
  column's refused logic, and also on a column with no name, whose own refusal is of a later step (PRD-E3).
- **SPEC-F45.12** [SQL] A ColumnControl search or list on a column the server declares unsearchable, on one it does not
  declare and on one with no name → `invalid_filter` (PRD-F41), never dropped.
- **SPEC-F45.13** [all] On `price`: `greater` `7` → rows 2, 3; `greaterOrEqual` `8` → 2, 3; `less` `8` → 1; `lessOrEqual`
  `8` → 1, 3. Row 4, with no price, is in none (section 5). On OS each sends the body of `>`, `>=`, `<` and `<=`,
  whose rows the live module pins, the missing value left out (`test_searchbuilder_conditions` on `pages`).
- **SPEC-F45.14** [SQL] `greater` `abc` on `price` → `invalid_number`; `greater` on a text column → `invalid_filter`.
- **SPEC-F45.15** [SQL] `notEqual` `7` on `price` → rows 2, 3, 4: the row with no price passes (PRD-F37).
- **SPEC-F45.16** [all] `greater`, `greaterOrEqual`, `less` and `lessOrEqual` with `type` `text` on `made` answer as
  SearchBuilder `>`, `>=`, `<` and `<=` on it, compared as days: `greater` `2024-03-05` → rows 2; `greaterOrEqual` →
  1, 2; `less` → 4; `lessOrEqual` → 1, 4. Row 3, with no date, is in none. On OS each sends the body of that
  condition, whose rows the live module pins (`test_searchbuilder_conditions` on `created`). On `name` (string) each
  → `invalid_filter`, as `SPEC-F27.1`.
