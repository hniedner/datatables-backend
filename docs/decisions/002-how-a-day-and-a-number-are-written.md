# 002: How a day and a number are written

This is the record behind the spellings PRD-F21, PRD-F22 and PRD-F25 state (PRD revision 26): what was decided, what
was read for it, and what the library accepted before. The requirements are in `docs/PRD.md`. Nothing here is a
requirement.

## The decision

Owner's decision of 2026-11-21:

- A number is written in ASCII: an optional sign, digits with an optional decimal point (`7`, `7.5`, `.5`, `5.`) and
  an optional exponent (`1e3`). White space around it is ignored. Any other spelling is `invalid_number`.
- A day is written `YYYY-MM-DD` in ASCII digits. A time of day may follow it after a space or a `T` (`HH:MM` or
  `HH:MM:SS`, the seconds with an optional fraction). It must exist and is then ignored. White space around the
  value is ignored. Any other spelling is `invalid_date`: a zone or an offset after the time among them, because the
  instant it names can fall on another UTC day than the one written.
- A year and a year-month (PRD-F22) are written in ASCII digits, and a day there as anywhere.

One stated spelling is answered alike on every backend. Before, the spellings were whatever Python's `int`, `float`
and `date.fromisoformat` happened to read, and for a year or a year-month in `contains` a pattern with `\d`, which
takes the digits of every script. The PRD did not say, and the conditions differed.

## What sends a date or a number

INFERRED: SearchBuilder is the only stock DataTables client whose date and number values the library reads. A
non-empty ColumnControl `list` and a ColumnControl `search` with a value are refused as a malformed request
(PRD-P5), and DataTables itself has no date or number input for a column filter (not checked in its source): a
classic column filter holds whatever the application's own input put there.

READ in `DataTables/SearchBuilder` at commit `184fb4dfd022d1c21da07e9b6b2db16c2c9a0b6c` (2026-09-25), not run in a
browser; each "so" below is INFERRED from the lines named:

- `src/SearchBuilder.ts`, line 465: on a server-side table the request's `searchBuilder` is `getDetails(true)`.
- `src/Criteria.ts`, `getDetails`, lines 2528 to 2568: for a column of a `moment`, `luxon` or `datetime` type, a value
  that parses in the column's display format is rewritten to `YYYY-MM-DD HH:mm:ss` (lines 2545 and 2565). A value
  that does not parse is sent as typed. This is why a time after the day is allowed: stock SearchBuilder sends one,
  after a space and without a zone.
- The same function, lines 2512 to 2527, holds a branch that would blank a plain `date` value not written
  `YYYY-MM-DD`. Its condition is `!moment && !luxon`, and those two names are the module's own functions (lines 102
  and 109), which are always truthy; the libraries themselves are in `momentLib` and `luxonLib`. So the branch never
  runs, and a plain `date` type's value is sent as the user typed it.
- The same function, lines 2571 to 2579: for a `num` type on a server-side table, every character outside
  `[0-9.\-]` is removed from each value. So an exponent, a `+` and a digit outside ASCII never arrive from
  SearchBuilder on a number column; `5.`, `.5` and an empty text can.

SvelteDT 1.0 sends no date value and parses no number or day from text (READ by its reviewer in its main at
`5ad6d9a`, not run), so it places no constraint.

## What changed

RUN on 2026-11-21 through `parse_request` and `fetch_sync`, on SQLite and a recording OpenSearch client, with the
library at `f463272` (before) and at `66a3fcf`, the commit that brought the rule (after). The review's later commits
changed the code around the readers, not what they accept: the same run after each of them gave the same "after"
for every row. A categorical filter or `multi_values` list on a date column reads each entry as `=` does since
`cf2b298`, so the rows for `=` hold for its entries.

| Spelling | Before | After |
| --- | --- | --- |
| `2024-03-05xyz`, `20240305`, `2024-W10-2` in `=`, `<`, `between` | the day | `invalid_date` |
| `2024-03-05T10:00:00Z`, `2024-03-05T10:00+02:00`, `2024-03-05 24:00`, `2024-03-05T10` in the same | the day | `invalid_date` |
| `2024-03-05T10:00`, `2024-03-05 23:59:00` in the same | the day | the day |
| `2024-03-05T10:00`, `2024-03-05 23:59:00` in `contains` and an OpenSearch column search | `invalid_date` | the day |
| a year or a year-month in Arabic-Indic digits in `contains` and an OpenSearch column search | the span | `invalid_date` |
| a full day in Arabic-Indic digits, anywhere | `invalid_date` | `invalid_date` |
| `3_1`, a number in Arabic-Indic or full-width digits | 31 | `invalid_number` |
| `+31`, `1e1`, `.5`, `5.`, `1E3`, `-.5e-2` | the number | the number |
| `²` (U+00B2), `+-7`, a whole number of 5,000 digits | `ValueError` out of `fetch` | `invalid_number` |

The 5,000 digits fail in Python, which by default converts at most 4,300 digits to an integer
(`sys.get_int_max_str_digits()`); a process that raises the limit reads a longer number.

A number is read in one function and a day in one (`parse_number` and `_written_day` in `_plan.py`), which every
condition, list and column search goes through. Each of their patterns matches a text in one way only; on a text of a
million characters that does not fit, each answers within 0.06 s (RUN on 2026-11-21 on the development machine, with
the patterns of `66a3fcf`, which the later commits did not change).
