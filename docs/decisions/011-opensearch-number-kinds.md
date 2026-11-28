# 011: What an OpenSearch number field refuses

This is the record of one measurement made for issue #47 on 2026-11-25: what each OpenSearch number field type
answers to a value at and just past the edge of its range. It extends the table of
[003](003-opensearch-number-ranges-and-fullwidth-marks.md). The requirements are in `docs/PRD.md`. Nothing here is a
requirement.

## How it was measured

CI run 45639230811 of a throwaway branch (pull request #199, closed unmerged), on OpenSearch 3.10.0 and 2.19.7. One
script (`tools/measure_47.py` of that branch) created an index with nine number fields, stored three documents, and
sent 131 values to each field in a `term`, a `terms` and two `range` queries (`gte` and `lte`), printing for each the
ids found or the error. The two servers answered all 524 queries per server alike.

The documents were `seven` (7 in every field), `high` (the largest value of the type, in every field) and `low` (the
smallest). `scaled_float` had `scaling_factor` 100. Values were sent as JSON numbers: a whole number as an integer, any
other as a float. Not measured: other scaling factors, the exact edge of `byte` and `short` (see below), and any
server but these two.

## What each field type does

| Field type | Answers | Refused with a 400 |
|---|---|---|
| `byte`, `short` | every whole number up to the edge of an `integer`, 128 and 254 on a `byte` included (`=` finds nothing, `>=` nothing, `<=` every row) | 1e308, 10^308 and 255 digits, "out of range for an integer"; the edge itself, 2^31, was not sent |
| `integer` | -2147483648 to 2147483647 | 2147483648, -2147483649, and every larger value ("out of range for an integer"); a fraction past the edge in a `range` (2147483647.5), which a `term` answers with no rows |
| `long` | -2^63 to 2^63-1 | 2^63, -2^63-1, 2^64, 1e308, 10^308 and 255 digits ("out of range for a long"); a fraction just below the top edge (2^63 - 0.5), because the float nearest to it is 2^63 (`term` and `range` alike) |
| `unsigned_long` | 0 to 2^64-1 | -1, 2^64, 1e308, 10^308 and 255 digits ("out of range for an unsigned long"); -0.5 in a `range` (a `term` answers no rows); 2^64 - 0.5, as for `long` |
| `float` | -3.4028234663852886e38 to 3.4028234663852886e38 | 3.4028238e38, 3.5e38, 1e39, 1e300, 255 digits, and the negatives ("[float] supports only finite values, but got [Infinity]") |
| `half_float` | -65504 to 65504, and 65519 | 65520, 65536, 1e5, 1e300, 255 digits, and the negatives ("[half_float] supports only finite values") |
| `scaled_float` (factor 100) | everything, 255 digits and 1e300 included | nothing |
| `double` | everything up to 255 digits, 1e308 and 2^64 included | nothing |

Three more facts:

- `terms` answers as `term` everywhere but one place: `terms` with -1 on an `unsigned_long` answers the document
  holding 2^64-1 instead of refusing, the number read as its unsigned wrap-around. It is a wrong answer without an
  error, the one the measurement found.
- A `scaled_float` takes a value whose scaled form passes a `long` without complaint: 1e300 finds the document that
  holds the largest value. It is a wrong match without an error, and no request fails.
- The edge of a `float` is where a number rounds to infinity, which is where Python's `struct.pack('<f', x)` raises
  `OverflowError`; for `half_float` the server and `struct.pack('<e', x)` agree at 65519 and 65520. For `float` the
  measurement sent only the largest value and 3.4028238e38, so the exact rounding edge, 3.4028235677973366e38, is
  checked by the live test (below).

## What follows from it

The owner decided on 2026-11-25, and PRD-D23 states it:

- A value outside a kind's range is refused with `invalid_number` before the query for `integer`, `long`,
  `unsigned_long`, `float` and `half_float`: the server refuses them all. `scaled_float` and `double` refuse nothing,
  so the measurement gave them no kind. The owner later approved both as kinds checked as a double, so that declaring
  the mapping type of any number field builds; they still refuse nothing.
- `byte` and `short` refuse only where OpenSearch does, beyond an `integer`'s range, and carry its bounds. A `byte` kind
  that refused 128 would take away an answer the library gives without a kind (`=` finds nothing, `<=` every row), and
  there is no failure to prevent there. SQL bounds a `SmallInteger` by 16 bits and refuses 40000; OpenSearch answers it
  on a `short`; the library follows each engine, and neither backend changes.
- The check is on the number as the request sends it: a number with a point goes as a float, so a fraction that
  rounds to the edge is refused, as the server does. `9223372036854775807.0`, written with a point, is therefore refused
  on a `long`; an integral decimal is not sent as an integer.
- A value of at most 255 characters (PRD-F14) is finite as a double, so `double` and `scaled_float` columns need no
  check, and the library already refuses a number that is not finite as a double with `invalid_number`.
- The two answers without an error are stated limits for an undeclared column (PRD section 5, `docs/databases.md`): a
  declared `unsigned_long` refuses `-1`, and a `scaled_float` has no kind.
- The live test `test_a_declared_kind_refuses_where_the_server_does_and_answers_where_it_answers` sends each edge pair
  of the table above through a declared and an undeclared column on both servers, so the exact edges the library
  carries (the `byte` and `short` edge at 2^31 and the `float` rounding edge) are checked against the server on every
  CI run.

## The script

It is `tools/measure_47.py` at commit 28f9d78, which pull request #199 keeps (closed unmerged). In short: for each
field type a list of values at and past its edge, and for each value `{'term': {field: n}}`, `{'terms': {field: [n]}}`
and `{'range': {field: {'gte': n}}}` or `lte`; a `TransportError` is printed with its status and the first 260
characters of its reason, which cuts the reason of a value of 255 digits short.
