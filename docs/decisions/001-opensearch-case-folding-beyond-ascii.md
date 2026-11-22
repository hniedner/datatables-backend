# 001: Case folding beyond ASCII on OpenSearch

This is the record behind PRD-F9 (for OpenSearch) and PRD-Q14: what was decided, and what was measured for it. The
requirements are in `docs/PRD.md`, the limits an adopter meets in its section 5 and in the README. Nothing here is a
requirement.

Every measurement below was made on 2026-11-21 in CI, on OpenSearch servers with 512 MB of heap and a small index
on one shard. "Twelve servers" are 2.19.7, 3.0.0, 3.1.0, 3.2.0, 3.3.2 and 3.4.0 to 3.10.0. A run is named by its
number in the repository's GitHub Actions; the runs of the measuring branch expire with their logs, so the figures
are quoted here and in pull request #37, which also holds the measuring scripts.

## The decision

Owner's decisions of 2026-11-21:

- The library focuses on OpenSearch 3.x, with limited compatibility for 2.x.
- A value with a cased character outside ASCII is sent as a case-insensitive `regexp` query, so that a substring
  search and an exact match fold such a character from OpenSearch 3.1 on.
- Every other value keeps its `term` or `wildcard` query, although a `regexp` would fold a few characters more for
  it (the Kelvin sign to `k`).
- No normalizer is asked of the consumer's mapping.

## Which query form folds what

On a `term` and a `wildcard` query OpenSearch's `case_insensitive` folds ASCII letters only, on every version
measured. On a `regexp` query it folds letters outside ASCII from 3.1.0 on. Measured against a stored `ÄPFEL` (run
44942700768): the regexps `äpfel` and `.*äpf.*` find it on 3.1.0, 3.2.0, 3.3.2 and 3.4.0 to 3.10.0, which run
Lucene 10.2.1 to 10.6.0, and not on 3.0.0 (Lucene 10.1.0) or 2.19.7 (Lucene 9.12.3). The analysed word `äpfel`
finds it on all twelve. A case-insensitive `term` and `wildcard` miss it on all twelve.

So a value of at most 255 code points with a cased character outside ASCII is sent as a case-insensitive `regexp`,
and every other value as before.

### Against the answers before the change

On fields without a normalizer, the answers of the final source were compared with those of `main` (commit
fa7de30) over 3,080 requests a server: 70 values, four tables (text+keyword, keyword, `fold_case=False`, a hooked
column), eight conditions, global search, column search and a list. None differs on 2.19.7 and 3.0.0. From 3.1.0
on the same 338 differ on each server: 203 by rows gained in a match, 135 by rows lost in its negation, none in
another way (run 44952673787, which ran the source and tests of commit 3b8ae84 from a measuring branch).

### What stays unfolded

A value without a cased character outside ASCII stays a `term` or a `wildcard`, so it finds no stored letter
outside ASCII that Unicode folds to an ASCII one: `kelvin` does not find the Kelvin sign, `sun` not the long s
(2.19.7, 3.0.0 and 3.10.0, run 44944069007). Sent as a `regexp`, `kelvin` and `sun` would find them on 3.10.0.

`izmir` does not find `İZMİR` as a substring or an exact match in any of the three query forms; the analysed word
does, on all twelve servers (run 44942700768).

## The backslash rule

A character of the value gets a backslash, which makes it that character and nothing else, unless a normalizer
turns one character into several (see "What a normalizer breaks"). The server runs a
pattern through a keyword field's normalizer, so the rule reaches beyond ASCII: behind its backslash a fullwidth
mark that an `asciifolding` normalizer turns into an ASCII one stays that character (2.19.7 and 3.10.0, run
44945819109: the fullwidth forms of `( * ~ ? [ . |` are found exactly by `=` and by `contains`).

Three kinds of character get no backslash:

- **An alphanumeric character**, which is no operator of the syntax.
- **A cased character that is no letter**, such as a circled `ⓓ`. The normalizer turns it into `d`, and behind a
  backslash that is a class of digits or an error (2.19.7 and 3.10.0, run 44949013109: with a backslash `ⓓ` finds
  a row holding `1`, and `ⓐ` fails with "invalid character class"; bare, each answers as `main` does).
- **Whitespace.** A normalizer that trims takes a space at the end of the pattern and would leave the backslash
  last, which is an error (2.19.7 and 3.10.0, run 44952007115: `=` with `ä ` failed with "unexpected
  end-of-string"). Bare, it answers as `main` does, with the fold from 3.1 on (run 44952673787). A no-break space,
  an ideographic space and a line separator, sent bare inside a value, each find exactly their own row on a plain
  keyword field and on an `asciifolding` field (same run).

Without a backslash, sixteen ASCII characters are operators of a regular expression on 2.19.7, and fifteen on
3.0.0 to 3.10.0, where a bare `~` is that character (run 44952673787).

## The length a `regexp` is sent for

A value longer than 255 code points, PRD-F14's longest search text, is sent as the `term` or `wildcard` it was
before. Beyond 255 no value was measured as a `regexp` on every server, and a server refuses a long pattern for the
effort of making its automaton:

- One letter repeated, open at both ends: 300 answered and 400 refused, as `wildcard` and as `regexp` alike, on
  2.19.7 and 3.10.0, with `b`, `ä` and `θ` (runs 44946785496 and 44949013109).
- The 26 letters of the alphabet in turn, open at both ends: answered at 500 (run 44949013109).
- 255 of one letter are answered on every server from 2.19.7 to 3.10.0 (run 44952673787).

`index.max_regex_length` counts UTF-16 units (run 44944336174) and is 1,000 by default. A pattern is at most 768
units, and that one is answered on all twelve servers (run 44952673787).

## Settings that refuse the query

A `regexp` is an "expensive query", as the `wildcard` of every substring search is. A cluster with
`search.allow_expensive_queries` off refuses both (2.19.7 and 3.10.0), and with this change also an exact match
whose value has a cased character outside ASCII.

## What a normalizer breaks

These limits are new with the `regexp` form and hold on every version, 3.0 and 2.19 included, where the form folds
nothing in return. Measured on 2.19.7 and 3.10.0, each against `main` (runs 44952007115 and 44952673787):

- **`asciifolding`, a character it turns into ASCII punctuation.** Where `main` answered each with the value's own
  row: `=` with `Ä⒜b` fails (`⒜` becomes `(a)`); `=` with `Ä⑴b` and with `Ä⁇b` miss their own row and find others
  (`⑴` becomes `(1)`, `⁇` becomes `??`).
- **A normalizer that trims.** A space next to an open end of the pattern is not trimmed, so `contains` with `ä `
  finds one row where `main` found five.
- **`lowercase`** changes no answer (run 44952007115, 70 requests a server).

In the other direction the `wildcard` of `main` matches too much on an `asciifolding` field for a fullwidth `＊` or
`？`, where the `regexp` is right (run 44945819109).

## The supported servers and what 2.19 costs

The CI legs run 3.10.0 and 2.19.7, and every live test passes on both. The suite also passed once on each of 3.0.0
to 3.9.0 (run 44952673787).

OpenSearch's documentation warns for a case-insensitive `term` that "In OpenSearch 2.x and earlier, complexity can
increase exponentially with the number of characters, leading to high heap memory usage and reduced performance."
(read on 2026-11-21, its Term query page).

Measured in runs 44941728065 and 44942700768, on about 2,000 documents, the server's `took`, the least of 5 runs.
The index is small, so this is the cost of a query by its length, not of a large index.

| Query | 2.19.7 | 3.10.0 |
|---|---|---|
| case-insensitive `term` of 16, 64 and 255 ASCII letters | 2, 4 and 14 ms, about linear up to 255 | at most 1 ms |
| case-insensitive `wildcard` of 255 letters | 8 ms | 8 ms |
| a list of 256 values of 255 ASCII letters, which is 256 such terms (two runs) | 2,540 and 3,760 ms, with 103 garbage collections over the 5 runs of the slower | 155 and 195 ms |
| the same list as one plain `terms` query, as a column declared `fold_case=False` sends it (PRD-D21) | 2 ms | 1 ms |
| the list with letters outside ASCII, as 256 `regexp` queries | 2,763 ms | 365 ms |
| the same values as case-insensitive terms | 2,668 ms | 61 ms, and misses the value |
| a global search of 16 words of such letters, 255 code points, as `regexp` | 66 ms | 166 ms |
| the same as wildcards | 99 ms | 111 ms |

No query failed and no circuit breaker tripped.

## Not measured

- How PostgreSQL, MySQL and SQL Server treat the Kelvin sign, the long s and `İ`.
- Searching `İ` against a stored `i`.
- `Ä⒈b` on an `asciifolding` field beside a row that a bare `.` would also match: the fixture held none, so a
  broader match is not shown and not excluded.
- Normalizers other than `asciifolding`, `trim` and `lowercase`.
- A large index.
