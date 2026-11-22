# Specs: errors (E)

Statements for PRD section 4.6. Form, tags, answers and the `items` fixture: see `README.md`. Log statements read the
records of the logger `datatables_backend` (pytest's `caplog`); a record's **event name** is its message as logged
(`record.msg`), a fixed snake_case text per case such as `datatables_request_malformed`.

- **SPEC-E1.1** [all] SearchBuilder `=` `abc` on `price`, with `draw` 7 → `fetch` and `fetch_sync` return (do not
  raise) a `DTResponse` with `draw` 7, `error_code` `invalid_number`, a non-empty `error`, `data` `[]`,
  `recordsTotal` 0 and `recordsFiltered` 0.
- **SPEC-E1.2** [all] SearchBuilder `<` `a` on `name` → `fetch` and `fetch_sync` return the answer with error_code
  `invalid_filter`; `InvalidFilter` does not propagate out of either.
- **SPEC-E2.1** [all] Across every user-error case in the specs, `fetch` answers one of exactly `invalid_filter`,
  `invalid_character`, `invalid_date`, `invalid_number`, `page_out_of_range`; it never answers another code
  (`query_failed` is the app's, PRD-E5).
- **SPEC-E2.2** [all] The five codes are stable: each case in P, D, F, O and C that names a code answers that code.
  [review] The five are listed in the docstring of `DTResponse`.
- **SPEC-E2.3** [review] The docstring of `DTResponse` and the README say that the list of codes is closed, that a
  code is added only in a release that says so, and that a client treats a code it does not know as a refusal of
  the request and shows `error`.
- **SPEC-E3.1** [all] A request holding both a U+0000 in the global search and `=` `abc` on `price` →
  `invalid_character` (step 1 before step 2).
- **SPEC-E3.2** [all] A request holding a 17-word global search and `=` `2024-13-01` on `made` → `invalid_filter`;
  holding a SearchBuilder tree 10 groups deep counting the root and `=` `abc` on `price` → `invalid_filter`.
- **SPEC-E3.3** [all] A request holding SearchBuilder `=` `abc` on `price` and `contains` `x` on `active` →
  `invalid_number` (step 2 before step 4); holding `=` `2024-13-01` on `made` and a column search `x` on the
  undeclared `extra` → `invalid_date`.
- **SPEC-E3.4** [SQL] A request holding SearchBuilder `=` `9223372036854775808` on `price` (step 3) and SearchBuilder
  `<` `a` on `name` (step 4) → `invalid_number`.
- **SPEC-E3.5** [OS] Given rows 5 to 10 besides the fixture, with `max_result_window` 3, `start` 4, `length` 3 (a
  page PRD-C12 refuses) and SearchBuilder `=` `yes` on `active` → `invalid_filter`, not `page_out_of_range`.
- **SPEC-E3.6** [OS] Column search `7 8` on `price` → `invalid_number`; `!2024` on `made` → `invalid_date` (step 2).
- **SPEC-E3.7** [all] A request holding SearchBuilder `=` `9999-12-31` on `made` (step 3) and a column search `yes` on
  `active` (step 4) → `invalid_date`.
- **SPEC-E3.8** [all] One part of a request (an order entry, the global search, one column search, one criterion)
  reports one mistake, and a condition or form that does not fit the column is reported without its value being
  read: SearchBuilder `contains` `abc` on `price` → `invalid_filter`, not `invalid_number`; a column search on
  `price` with the value `abc` and `multi_values` `["x"]` → `invalid_filter` (the reviewer's decision of
  2026-11-21).
- **SPEC-E4.1** [all] For each user-error case, two requests that differ only in the offending value (`abc` vs
  `xyz`, `2024-13-01` vs `2025-14-02`) answer the same `error` text, and the text contains neither value. A limit
  the server holds (an integer column's bounds, the result window, the year 9999 of the last day there is) is the
  server's text and may appear, also when the user typed the same digits.
- **SPEC-E4.2** [all] A refusal on an undeclared column `secret_field` answers a text that does not contain
  `secret_field`. (A declared name such as `price` is the server's own text and may appear.)

Each statement from E4.3 on gives the whole `error` text of one mistake: it names the condition and what to do about
it, as far as that is known when the mistake is found. A text is changed only together with its statement.

- **SPEC-E4.3** [all] A global search holding a tab (U+0009) → `invalid_character` with
  `The search box holds an invisible control character, such as a tab. Delete its text and type it again.`
- **SPEC-E4.4** [all] A column search on `name` holding a tab, or a SearchBuilder value on `name` holding one →
  `The filter on "name" holds an invisible control character, such as a tab. Delete its value and type it again.`
- **SPEC-E4.5** [all] A column search holding a tab on the undeclared `extra`, or a SearchBuilder value holding one
  on `extra` → `invalid_character`, not the refusal of the name, with
  `A column filter holds an invisible control character, such as a tab. Delete its value and type it again.`
- **SPEC-E4.6** [all] SearchBuilder `=` `abc` on `price` → `invalid_number` with
  `The filter on "price" needs one number, written like 42 or -3.5.`
- **SPEC-E4.7** [all] SearchBuilder `=` `15/07/1980` on `made` → `invalid_date` with
  `The filter on "made" needs a date that exists, written as YYYY-MM-DD, like 2024-03-05.`
- **SPEC-E4.8** [all] SearchBuilder `contains` `1980-13` on `made`, or `contains` `1980-02-30` (a day that does not
  exist, where a year or a month would do too) → `invalid_date` with
  `The filter on "made" needs a year, a month or a day that exists, written as YYYY, YYYY-MM or YYYY-MM-DD, like 2024-03.`
- **SPEC-E4.9** [SQL] SearchBuilder `=` `9223372036854775808` on `price` → `invalid_number` with
  `The filter on "price" needs a number between -9223372036854775808 and 9223372036854775807.` The bounds are those
  of the column's own width: -32768 and 32767 on a 16-bit integer column, -2147483648 and 2147483647 on a 32-bit one.
- **SPEC-E4.10** [all] SearchBuilder `<` `9999-12-31` on `made`, or `contains` `9999` → `invalid_date` with
  `The filter on "made" reaches the last day of the year 9999, which cannot be searched. Use an earlier date.`
- **SPEC-E4.11** [all] An order entry on the undeclared `extra`, marked `orderable: true` →
  `The table was asked to sort by a column the server does not offer. Sort by another column.`
- **SPEC-E4.12** [all] A column search `x` on the undeclared `extra` →
  `A filter is on a column the server does not offer. Remove that filter.`
- **SPEC-E4.13** [all] An order entry on `secret` (server-unorderable), marked `orderable: true` →
  `The table cannot be sorted by "secret". Sort by another column.`
- **SPEC-E4.14** [all] With `note` declared unsearchable, a column search `x` on `note` →
  `The column "note" cannot be filtered. Remove the filter on it.`
- **SPEC-E4.15** [all] A global search `x` on a request whose only column is `active`, [OS] or on a table declaring
  only `price` and `made` →
  `No column of this table can be searched from the search box. Clear it and use a column filter instead.`
- **SPEC-E4.16** [all] A global search `a.*` with `regex` true →
  `The search box does not take regular expressions. Search for plain text.`
- **SPEC-E4.17** [all] A column search `A.+` with `regex` true on `name` →
  `The filter on "name" does not take regular expressions. Remove these characters and search for plain text: . * + ? [ ] { } ( ) ^ $ | \`
- **SPEC-E4.18** [all] A global search with `multi_values` `["x"]` →
  `The search box takes text, not a list of values. Send the text as the search value.`
- **SPEC-E4.19** [all] A column search on `price` with the value `abc` and `multi_values` `["x"]` →
  `The filter on "price" holds both text and a list of values. Send only one of them.`
- **SPEC-E4.20** [all] SearchBuilder `contains` `3` on `price` →
  `The condition "contains" cannot be used on "price", which holds numbers. Choose another condition.` A string
  column `holds text`, a date column `holds dates`, a boolean column `holds true or false`.
- **SPEC-E4.21** [all] SearchBuilder `=` with no value on `name` → `The filter on "name" needs a value.`;
  `between` with one value on `price` → `The filter on "price" needs two values.`
- **SPEC-E4.22** [all] SearchBuilder `=` `yes` on `active` →
  `The filter on "active" needs true or false (1 or 0 also work).`
- **SPEC-E4.23** [OS] With `note` declared as analysed text without a keyword field, a column search on `note` with
  `multi_values` `["a"]` →
  `The column "note" can be searched only for text it contains, not for a list of exact values. Type the text to search for instead.`
- **SPEC-E4.24** [OS] With `max_result_window` 3, `start` 4 and `length` 3 → `page_out_of_range` with
  `This table can be paged through its first 3 rows only. Go back to an earlier page, or narrow the search so fewer rows match.`
- **SPEC-E4.25** [all] A column search on `price` with `multi_values` `["7", "abc"]`, or SearchBuilder `between` `1`
  and `abc` on `price` → `invalid_number` with
  `Each value in the filter on "price" must be a number, written like 42 or -3.5.` (several values are not told to
  be one number, as E4.6 tells a single value; [OS] a column search `7 8` on `price` is a single value and answers
  E4.6's text).
- **SPEC-E4.26** [SQL] A column search on `price` with `multi_values` `["7", "9223372036854775808"]`, or SearchBuilder
  `between` `1` and `9223372036854775808` on `price` → `invalid_number` with
  `Each value in the filter on "price" must be a number between -9223372036854775808 and 9223372036854775807.`
- **SPEC-E4.27** [all] A column search on `made` with `multi_values` `["2024-03-05", "05.03.2024"]`, or SearchBuilder
  `between` `2024-01-01` and `abc` on `made` → `invalid_date` with
  `Each value in the filter on "made" must be a date that exists, written as YYYY-MM-DD, like 2024-03-05.` (several
  values are not told to be one date, as E4.7 tells a single value).
- **SPEC-E4.28** [all] SearchBuilder `=` on `name` with the two values `Bob` and `Ann` → `invalid_filter` with
  `The filter on "name" holds more values than its condition takes. Remove the filter and set it again.`
- **SPEC-E4.29** [SQL] With `price` redeclared as a `Numeric(32, 2)`: `=` with a whole number of 31 digits →
  `invalid_number` with `The filter on "price" needs a number with at most 30 digits before the decimal point.`;
  `between` `1` and that number →
  `Each value in the filter on "price" must be a number with at most 30 digits before the decimal point.`
- **SPEC-E5.1** [core] `DTResponse.query_failed(5)` gives `draw` 5, `error_code` `query_failed`, a non-empty `error`,
  `recordsTotal` 0, `recordsFiltered` 0 and `data` `[]`.
- **SPEC-E5.2** [all] When the connection or client raises during `fetch`, the call raises; it never returns
  `query_failed`.
- **SPEC-E6.1** [core] `parse_request` with `length` `abc` and `draw` `3` raises `MalformedRequest` whose `draw` is 3;
  it is not answered as `invalid_filter`.
- **SPEC-E6.2** [core] `parse_request` with `draw` `x` raises `MalformedRequest` whose `draw` is None; with `draw`
  `2147483648` (over PRD-P10) its `draw` is None.
- **SPEC-E6.3** [core] `MalformedRequest` is not a subclass of `InvalidFilter`, nor `InvalidFilter` of
  `MalformedRequest`.
- **SPEC-E7.1** [SQL] Recording: a request that ends in a user error runs no statement (no page, no count).
- **SPEC-E7.2** [OS] Recording: a user error other than `page_out_of_range` sends at most the counts of PRD-C11 and
  never a page search; a `page_out_of_range` answer sends only those counts, never a page search.
- **SPEC-E8.1** [SQL] The table's SQLAlchemy metadata has a column `ghost` that the database table lacks, and the
  table declares `Column('ghost')` → `fetch` raises the database error; it is not answered.
- **SPEC-E8.2** [SQL] A connection whose execute raises `OperationalError` → `fetch` and `fetch_sync` raise it.
- **SPEC-E8.3** [OS] A client that raises a transport error → `fetch` raises it.
- **SPEC-E9.1** [OS] A search answer with `_shards.failed` 1 → `PartialResultError` raised; with `_shards.successful`
  below `_shards.total`, `_shards.failed` 0 and `timed_out` false → `PartialResultError` raised.
- **SPEC-E9.2** [OS] A search answer with no `_shards`, or with `_shards.total` not an integer → `PartialResultError`
  raised.
- **SPEC-E9.3** [OS] A count answer that does not cover every shard → `PartialResultError` raised.
- **SPEC-E10.1** [all] For every refusal in the specs, no log record of any level holds the search value or an
  undeclared column name the client sent: with a marker string in each value and in an undeclared name, the marker
  appears in no record's message, arguments or extra fields.
- **SPEC-E11.1** [all] A refusal on the undeclared `secret_field` logs a record that does not name it.
- **SPEC-E12.1** [all] Every record the library emits comes from the logger `datatables_backend` or a child of it,
  through the standard `logging` module.
- **SPEC-E13.1** [all] Each `MalformedRequest` raised and each user error answered logs exactly one WARNING record;
  the same case twice logs the same event name twice, and two different cases log different event names: a column
  search `x` on the undeclared `extra`, and an order on `secret` marked `orderable: true` (server-unorderable).
- **SPEC-E13.2** [all] A request whose column 0 is `secret`, marked `orderable: false` (so DataTables' default order
  on column 0 is skipped: unorderable on both sides), with an idle SearchPanes pane (JSON `"searchPanes":
  {"colour": []}`), an idle ColumnControl search (an empty value, logic `contains`),
  a nameless column, a request-searchable undeclared column and a global search `red` (skipping the boolean
  `active`, PRD-F44, and on OS the number and date columns, PRD-F32) logs no record.
- **SPEC-E13.3** [all] The request of E3.1 (two mistakes) logs exactly one WARNING, for the code answered, and
  answers `data` `[]`.
