# 003: OpenSearch number ranges and fullwidth marks

This is the record of one measurement made for issue #14 on 2026-11-22: what an OpenSearch number field answers to
a number outside its range, and what a `wildcard` and a `regexp` query do with a fullwidth `＊`, `？` or `＼` on a
keyword field with a normalizer. The requirements are in `docs/PRD.md`. Nothing here is a requirement.

## How it was measured

CI run 45034720139 of a throwaway branch (pull request #44, closed unmerged), on OpenSearch 3.10.0 and 2.19.7. One
script created an index, stored the documents below and sent 186 queries to each server, printing for each the ids
found or the error. The two servers answered all 186 alike.

The index had a keyword field `plain`, a keyword field `folded` with a normalizer of `asciifolding` and `lowercase`,
and four number fields: `long`, `integer`, `double` and `unsigned_long`. Not measured: other normalizers, other
number types (`short`, `byte`, `float`, `half_float`, `scaled_float`), and any server but these two.

## Numbers outside a field's range

Each number was sent in a `term` query and in a `range` query (`gte` and `lte`), and 2^64 also in a `terms` query,
whole and as a float.

| Field type | Answered | Refused with a 400 |
|---|---|---|
| `long` | up to 2^63-1 | 2^63, 2^64, 2^64 as a float, -2^64, 1e308 ("out of range for a long"); 400 digits in a `range` (a `term` answers no rows) |
| `integer` | 7 | 2^63-1 and everything larger ("out of range for an integer") |
| `unsigned_long` | up to 2^64-1 | 2^64, a negative number of that size, 1e308 |
| `double` | every finite value, 2^64 whole or as a float | 400 digits in a `term` ("supports only finite values"); a `range` answers |

So the range is the mapping's, and it differs by field type: no one bound fits every number column, and sending the
nearest float does not help, since a `long` field refuses that too. The library does not read the mapping (PRD-D8),
so it cannot know the range. The refusal reaches the application as the client's `RequestError`, a server failure,
where PRD-E7 wants a user's mistake answered with `invalid_number` before the query.

What follows from it: PRD section 5 states the limit. The fix is a column that declares its number kind (PRD-D23,
issue #47); the measurement behind it, with the kinds `byte`, `short`, `float`, `half_float` and `scaled_float` this
one did not cover, is [011](011-opensearch-number-kinds.md).

## Fullwidth marks on a normalized keyword field

Fourteen strings were stored, each in both fields, among them `a*b`, `a＊b`, `a?b`, `a？b`, `a\b`, `a＼b`, `a⁇b`
(U+2047, which `asciifolding` rewrites into two question marks) and `a??b`. Each pattern below was sent between
two `*` as a `wildcard` query with `case_insensitive`, and the escaped ones also as a `regexp` query between two
`.*`.

On the `plain` field a fullwidth mark is an ordinary character: bare or behind a backslash, in either query, the
pattern finds its own string and no other.

On the `folded` field:

| Pattern | `wildcard` finds | `regexp` finds |
|---|---|---|
| `a＊b` (bare) | every string: the normalizer made it `a*b`, a wildcard | not sent |
| `a\＊b` | nothing | `a*b` and `a＊b`, as it should |
| `a？b` (bare) | every string with one character between: the normalizer made it `a?b` | not sent |
| `a\？b` | nothing | `a?b` and `a？b`, as it should |
| `a＼b` (bare) | nothing, its own string included | not sent |
| `a\＼b` | nothing | `a\b` and `a＼b`, as it should |
| `a⁇b` (bare) | `axyb`, `a⁇b`, `a??b`: two wildcards | not sent |
| `a\⁇b` | nothing | `a?b` and `a？b`, and not `a⁇b` or `a??b` |

The same patterns with the ASCII mark behind a backslash (`a\*b`, `a\?b`, `a\\b`) find both the ASCII and the
fullwidth string on the `folded` field, as the normalizer makes them the same.

So on a normalized field a backslash before a fullwidth mark does not work in a `wildcard` query: the pattern
finds nothing. Without the backslash the mark becomes the wildcard it folds to, and the pattern matches too much;
a fullwidth backslash misses its own string. A `regexp` query with the backslash is right for all three marks. A
character that the normalizer turns into two (`⁇`) is right in no form.

What follows from it: the third part of issue #14 sends a search value that holds such a character as the
`regexp` the cased values already get (decisions file 001), and PRD section 5 keeps the limit for `⁇` and for a
value over 255 code points.

## The script

It is `tools/measure_14.py` at commit cc7f653, which pull request #44 keeps. In short: for each
pattern `client.search` with `{'wildcard': {field: {'value': '*<pattern>*', 'case_insensitive': True}}}` or the
`regexp` form, and for each number `{'term': {field: n}}`, `{'range': {field: {'gte': n}}}` and
`{'range': {field: {'lte': n}}}`; a `TransportError` is printed with its status and reason.
