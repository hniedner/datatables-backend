"""The library against a reference evaluator (issues #205, #206 and #217): generated rows and requests, answered by
`fetch_sync` and by `fetch` over the same loaded rows, and by `tests/reference_evaluator.py`, which is written from
`docs/PRD.md` and `spec/` and never from `src`. Each of the two gives the same rows in the same order with the same
counts as the evaluator, or the same error code. A difference is a finding (a gap in the specification, a defect in
the library or an engine fact), reported and never silenced by narrowing the generator.

The legs: the database of DTB_SQL_URL and DTB_SQL_ASYNC_URL where they are set (CI's container legs; DTB_SQL_DIALECT
must name its dialect, so a run that fell back to SQLite fails), else an SQLite file opened by both drivers. Each leg is
compared with the evaluator's reading of its own database (`engine_for`), never with another leg;
`tests/test_reference_opensearch.py` runs the same property on OpenSearch. The examples: under the `ci` profile 500 on
SQLite (about 3 seconds for both drivers, measured) and `SERVER_EXAMPLES`, 300, on a SQL server, where each example
also pays about eight round trips (a delete and an insert, then three statements for each driver), so that a leg grows
by well under a minute (#206). `explore` keeps its own count everywhere. Measured on a local Podman VM (CI is expected
at about twice that), the test's call time under `ci`: PostgreSQL 2.4 to 2.8 s, MySQL 2.9 s, MariaDB 2.4 to 2.5 s;
under `explore` (5,000 examples): PostgreSQL 41 s, MySQL 48 s, MariaDB 41 s. SQL Server and the mysqlclient leg are not
yet measured: first in CI.

PRD-O6, the order of text, is the database's collation. Each leg measures it once: the values of `STRINGS`, stored in
`s`, ranked by `DENSE_RANK() OVER (ORDER BY s)` on the table's own column, written without any COLLATE, so the rank is
the column's collation and a library that added a COLLATE of its own would be found. On SQLite the measured rank must
equal code-point order (its `BINARY` collation); on every database the measured rank is the evaluator's sort key.

PRD-F23, a decimal's search text, is the database's own spelling. Each leg measures it once: the values of `DECIMALS`,
stored in `m`, read back by the dialect's own `CAST(m AS ...)` written in raw SQL (`measured_decimal_text`, never the
library's expression), and asserts it against the documented spelling (`DECIMAL_TEXT`: fixed scale on PostgreSQL,
MySQL, MariaDB and SQL Server, `0.30`; SQLite's text of what it stores, `0.3` for 0.30, `0` for 0.00 and
`12345678901234568` for each of the first three; PRD section 5, `docs/databases.md`). A mismatch is a finding to
report, not to model. The measured text is the evaluator's.

The table: `dtb_test_tags`, which the SQL tests own: `id` (Integer, the key), `s` and `t` (String), `n` (BigInteger),
`d` (Date), `b` (Boolean) and `m` (`Numeric(20, 2)`, the six values of `docs/decisions/010-decimal-numbers.md`, declared
to the library as a number column), 0 to 8 rows inserted in a random key order. The request: the global search, column
searches (smart, categorical, `multi_values`), ColumnControl's search and list, a SearchBuilder tree of at most 5 drawn
criteria and up to 5 more that `joined` adds for the scenarios below, 0 to 3 order entries (often a skipped one first,
then one applied in the other direction, now and then over rows that tie on the applied column with a short page that
cuts the tie, half of those with no filter to thin the tie out), paging past the end included; on any column, now and
then column search text that PRD-F6 reduces to no word, alone or beside a list; columns named twice, nameless columns
and an undeclared one; a server declaration with at least one unorderable column, and mistakes of every step of PRD-E3.
The rates are set so that half or more of the requests are answered (60% of the valid examples on the `ci` seed of
SQLite) and most outcomes and paths are reached at least about 30 times in 500 examples; one seed varies around that,
and the seed follows the test's source, so an edit of the test changes it (`uv run pytest
tests/test_reference_properties.py --hypothesis-show-statistics` prints its events). The paths that tell known mutants
apart (M4 to M10b in the events) are drawn on purpose, each in about 1% or more of the examples; on the `ci` seed of
SQLite M4 3%, M5b 2%, M6 1%, M7 2% (`between`) and 2% (`!between`), M10 3% (the last day read before a number mistake of
step 2) and M10b 5%. The paths of `compared`, on the same seed (over 2,000 examples of another seed in brackets): a
word holding `\\`, `%`, `_` or `[` that finds a row and leaves out another 1% (1%), `%` or `_` alone over number text
only 1% (1%), PAD SPACE changing the answer 1% (3%), 2^63 on `n` as the only mistake 1% (2%), a column search by date
text that finds a row and leaves out another 0.4% (1%); on `m` (#217), comparing a decimal exactly or as SQLite stores
it changes the answer 5% (0.5%), the spelling of PRD-F23 changes it 3% (1%), and SQL Server's 38 digits change it 0.2%
(1%; the hand tests of SPEC-F25.12 pin the bound). A rate moves by several points from one seed to the next, and each
of the other paths also has its example. `n` holds 2^53 and 2^53 + 1, which a double cannot tell apart, so a number
bound as a double finds its neighbour: about once in sixteen `outranked` puts one in a row and the other in a criterion,
and a request sends one of them beside a row holding one in 4% of the `ci` examples, 6% over the 2,000 (without that
scenario 5,000 `explore` examples missed the mutant that binds an integer column's number as a double).

The examples live in `EXAMPLES`, one home: the smallest case of each mutant and rare case above (`NEIGHBOURS` among
them), one of each `compared` scenario, and on `m` the reading of each step of PRD-E3 a decimal can fail: not finite
(step 2) in a criterion, a categorical filter and a list, which the random draw reaches on the last two only after
about 2,400 `explore` examples; more than 16,383 digits after the point (step 3); 39 digits on SQL Server (step 3.5),
and 38 after a zero before the point, which it answers.
The other examples on `m` are one for each reading the legs tell apart (a decimal compared exactly, as SQLite stores
it and as a double; its spelling; its order). One more pins what a server leg caught: a list of two numbers, which
MariaDB compares as doubles when they are bound as text (a mutant only `explore` caught on the servers). Two pin the
16,383 limit read by the value, which a mutant of the library or of the evaluator breaks on every seed: `0e-16384`,
zero having no fraction, and `1.0e-16383`, the number 1e-16383. The defect this property found on OpenSearch, `-0.00`
kept apart from zero, is pinned by #219's tests, not here.
`TYPED_EXAMPLES` names those whose answer on OpenSearch hangs on the backend, its query or the 64-bit range of a `long`
declaration, which `tests/test_reference_opensearch.py` imports; those the shared plan answers alike on every backend
(`%` alone over number text, PRD-F32 and PRD-F13; the decimal refusals; `0.` and 39 digits then the last day; `0.` and
38 ones) are left out, and so is the example of `1.0e-16383`, which is not defined where the leg runs on OpenSearch
(`DECIMAL_BOUNDS`).

Rare cases, and what carries each:
- a regex over 255 code points that is no categorical filter: drawn in about 1 to 3 of 1,000 random examples, none
  on the current `ci` seed; carried by an example on the property and by the hand-written tests;
- an entry of 256 code points on a list on the global search: about 1 to 3 in 1,000 random examples, none on the
  current `ci` seed; carried by an example and by the hand-written tests (`explore` found mutants of these two cases
  only after 1,200 to 1,500 examples);
- a list of more than 256 entries on the global search: about 3% of the `ci` examples, and an example;
- an unfinished criterion holding a step-1 value: about 1 in 100 random examples, 7 on the current `ci` seed; an
  example;
- ColumnControl's `contains` of more than 16 words: about 3% of the `ci` examples, and an example;
- the order within step 3 of PRD-E3 (the maximum date of PRD-F24 as a bound before a number the column's own type
  does not take): the examples and the hand-written tests, since the random draw seldom holds both mistakes at once.

A choice is drawn by `chance` and `rarely` of `tests/conftest.py`: Hypothesis draws the first of a list of choices far
more often than the others.

Covered (PRD): the smart search's grammar (F3, F4, F6, F7 for LF and CR, F8, F35); the global and column searches (F1,
F2, F11, F12, F13, F41, F44, and F23: on SQL a number or date column is searched as its text); ASCII case folding (F9);
categorical filters and lists (F16 to F20, F33, F39, F40); SearchBuilder (F21 for days, F22, F24, F25, F26, F27, F28,
F30, F36, F37, F43); ColumnControl (F45); ordering (O1 to O4, O8, and O6 as each leg's measured collation); counts and
paging (C1 to C3); the error codes and which one a request with several mistakes gets (E2, E3, F14, F15); section 5's
PAD SPACE, which each SQL leg measures and asserts against the dialect's documented comparison; on a `Numeric` column
(#217) F25's reading of a number with a point or an exponent (a decimal; not finite at step 2, more than 16,383 digits
after the point at step 3, more than 38 digits in all on SQL Server at the column's own step, E3), its comparison (exact
on the four servers, as SQLite stores a decimal on SQLite, section 5) and F23's spelling, which each SQL leg measures.
The OpenSearch leg adds typed search (F32, F33, F34), F26's reading there, where a NULL is a missing field and `''` a
value, D23 on a `long` field, and `m` as a `double` field: a number compared as a double.

A risk, not modelled: on SQL Server LIKE ignores trailing blanks of the matched non-Unicode value (Microsoft's
documentation), so a criterion `ends` `a` over a stored `'a '` may match there while the evaluator says no
(PRD-F11: a word matches as a substring; section 5 states trailing-space insensitivity only for an exact match). If
that leg reports it, it is a finding to triage (an entry in section 5 and docs/databases.md, or a library fix), never a
narrowed generator.

Left out: case folding beyond ASCII and every character outside ASCII (curly quotes, U+2028 and U+2029 included);
datetimes and time zones, and a time of day after a day; a number with a fraction or an exponent on an integer column
(`id`, `n`) and on a `Float` column; hooked columns, `fold_case=False` and `field=`; a base filter and a scope; the size
bounds (PRD-F38's, which a list on the global search does not meet, see below, and `request_too_large`); what is
OpenSearch's only (`page_out_of_range`, PRD-C10 to PRD-C13, PRD-O5, PRD-O7); the total cache; the `error` texts and the
log records; the types a database adds of its own.

Generator restrictions, each with its reason:
- A number beyond 32 bits is sent only to `n`, the BigInteger, and to `m`, the `Numeric`; never to `id`, an Integer,
  whose width the evaluator does not model. The evaluator reads every integer column with a BigInteger's range, the
  mistake of the column's own type that PRD-E3 answers in step 3 after the maximum date of PRD-F24 as a bound.
- A number with a fraction or an exponent is sent only to `m`, so a non-finite number on an integer column is never
  compared here (the evaluator reads it as step 2 there too; the library's code for it on an integer column is pinned
  by the test of SPEC-F25.1 and its step by the OpenSearch hand test of SPEC-E3.15, on a number column without a kind);
  no curly quote and no character outside ASCII: out of scope above.
- `m` holds only the six values of decision 010 and NULL: SQLite stores a `Numeric` as an integer or a floating-point
  number and compares it inexactly beyond about 15 significant digits (PRD section 5, `docs/databases.md` "A decimal is
  not compared exactly"), and the evaluator models its text (`sqlite_text`) for these values only.
- On MySQL and MariaDB no number is answered on `m` whose 31 to 16,383 digits after the point a floating-point reading
  would compare otherwise than its exact value: they compare such a number as a double (`docs/databases.md` "`= 1e-400`
  finds a row holding zero on MySQL and MariaDB"). `0.` and 38 or 39 ones are sent (SQL Server's bound), which compare
  with every cell of `m` as their double does; `1e-16384` is refused at step 3 (PRD-F25, PRD-E3). `1.0e-16383` is
  within the limit and is sent only where the evaluator models the engine (see `DECIMAL_BOUNDS`).
- No exponent beyond what a `Decimal` holds: the evaluator does not read one (it raises `OutOfScope`, unless the number
  is not zero and the exponent positive, which is not finite and step 2).
- A request column's `name` is always empty, so its name is its `data` (PRD-P15), and an order entry sends no `name`:
  those readings are `parse_request`'s, which the parse properties and the hand-written tests cover.
- ColumnControl's search is always sent with `type` `text`: the date control is refused while parsing (PRD-F45), which
  `tests/test_parse_properties.py` covers.
- No SearchBuilder tree near PRD-F38's bounds, no column list near 256 values and no request near PRD-C17's 960
  conditions: out of scope above. A list on the global search is sent with 257 to 600 entries, since PRD-F40 exempts
  it from PRD-F38's bounds.

A step-1 value (a control character, 256 code points, 17 words) is also sent where nothing reads it, and the evaluator
answers as the requirements say (#205): in an unfinished criterion (PRD-F14: "A value no condition reads, a surplus
value or that of `null`, is not measured"; PRD-F15 looks only at "a SearchBuilder value its condition reads
(PRD-F36)"); in a regex that is no categorical filter (PRD-F14 measures alternatives "after unescaping and not the raw
pattern", and PRD-F18 refuses any other regex); and in a list on the global search, whose entries PRD-F40 does not
read, so PRD-F14 and PRD-F15 do not apply to them and PRD-F38's bounds on lists do not count them: now and then such a
list holds more than 256, or more than 512, entries beside a mistake of step 2, which answers. ColumnControl's
`contains` is the column search of PRD-F2 (PRD-F45), so PRD-F14's word cap applies to it: more than 16 words are
`too_many_words`.

Where the PRD is silent the evaluator must raise a marked gap rather than guess; no rule needs one today, since every
gap met so far is decided (#205), and a gap found later is added back as a marked `SpecGap`.
"""

import asyncio
from collections import Counter
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import replace
from datetime import date
from decimal import Decimal
from decimal import InvalidOperation
import json
import os
from pathlib import Path
from typing import Any
from typing import Literal
import unicodedata

from hypothesis import event
from hypothesis import example
from hypothesis import given
from hypothesis import strategies as st
import pytest
from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import Column as SQLColumn
from sqlalchemy import Date
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import create_engine
from sqlalchemy import text as sql_text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine as SQLEngine
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from datatables_backend import Column
from datatables_backend import DTParams
from datatables_backend import DTResponse
from datatables_backend import parse_request
from datatables_backend.sqlalchemy import SQLTable

from .conftest import SQL_URLS
from .conftest import async_engine
from .conftest import chance
from .conftest import rarely
from .conftest import server_examples
from .reference_evaluator import SQLITE
from .reference_evaluator import Answer
from .reference_evaluator import Declared
from .reference_evaluator import Engine
from .reference_evaluator import Kind
from .reference_evaluator import Refusal
from .reference_evaluator import categorical
from .reference_evaluator import code_points
from .reference_evaluator import evaluate
from .reference_evaluator import fixed_scale
from .reference_evaluator import sqlite_text
from .reference_evaluator import words

KINDS: dict[str, Kind] = {'id': 'number', 's': 'string', 't': 'string', 'n': 'number', 'd': 'date', 'b': 'boolean',
                         'm': 'decimal'}  # fmt: skip
NAMES = list(KINDS)

#: The SQL tests' own table (tests/conftest.py, TEST_TABLES), dropped and created by the module fixture `leg`, which
#: replaces its rows per example. The key is no IDENTITY on SQL Server, so an example inserts its own keys.
reference_rows = Table(
    'dtb_test_tags',
    MetaData(),
    SQLColumn('id', Integer, primary_key=True, autoincrement=False),
    SQLColumn('s', String(20)),
    SQLColumn('t', String(20)),
    SQLColumn('n', BigInteger),
    SQLColumn('d', Date),
    SQLColumn('b', Boolean),
    SQLColumn('m', Numeric(20, 2)),
)
#: The examples of the property on a SQL server leg under the `ci` profile; SQLite runs the profile's 500
SERVER_EXAMPLES = 300

#: `'a '` ends in a space: under PAD SPACE it equals `'a'` (PRD section 5)
STRINGS = ['', ' ', 'a', 'a ', 'A', 'ab', 'Ab c', 'b a', 'red', 'Red', 'RED', 'reddish', 'x_y', '5%', 'a-b', '-', '7a',
           '[a]', 'a\\b', '50']  # fmt: skip
DATES = [date(2023, 1, 1), date(2024, 3, 5), date(2024, 3, 6), date(2024, 12, 31)]
#: The numbers of `n`, 2^53 and 2^53 + 1 among them: a double cannot tell those two apart, so a number bound as a
#: double finds its neighbour as well
NUMBERS = [-7, 0, 7, 17, 70, 2**53, 2**53 + 1]
#: The cells of `m`, a `Numeric(20, 2)`: the six values of `docs/decisions/010-decimal-numbers.md`. A float cannot tell
#: the first three apart, so SQLite stores all three as 12345678901234568 (PRD section 5)
DECIMALS = [Decimal('12345678901234567.89'), Decimal('12345678901234567.90'), Decimal('12345678901234568.00'),
            Decimal('0.30'), Decimal('0.31'), Decimal('0.00')]  # fmt: skip
#: Numbers sent to `m`: the pool's own, spelled otherwise (trailing zeros, an exponent, `-0.00`), and numbers the
#: comparison tells apart: 0.305 and 0.30000000000000001 hold more digits than the column, 12345678901234567 is whole
#: and the integer neighbour that a float cannot tell from the first three; `1.5e3` is an exponent, between the small
#: cells and the large ones
DECIMAL_OPERANDS = ['12345678901234567.89', '0.30000000000000001', '12345678901234567.90', '12345678901234568',
                    '0.305', '12345678901234567', '12345678901234567.8900', '-0.00', '3.1e-1', '0.3', '0.31', '0.3000',
                    '0', '00.31', '1.5e3']  # fmt: skip
#: Numbers at a bound: 39 digits and `0.` with 39 digits are over SQL Server's 38, `0.` with 38 digits is not (the
#: zero before the point is not counted); `1e-16384` holds more digits after the point than any column (step 3), while
#: `0e-16384` is zero, which has no fraction; `1e400` and `1e999` are not finite (step 2) (PRD-F25, PRD-E3).
#: `1.0e-16383` is the same number as `1e-16383`, so it is within the 16,383 limit; an engine that reads it as a double
#: finds the row holding 0.00 (PRD section 5): the evaluator models that for SQLite, not for MySQL and MariaDB, so it
#: is not sent there, nor on OpenSearch (`tests/test_opensearch.py` pins that a column search accepts it); SQL Server
#: refuses it by its 38 digits
DECIMAL_BOUNDS = ['0.' + '1' * 39, '1' * 39, '0.' + '1' * 38, '1e-16384', '0e-16384', '1e400', '1e999']
TINY_NUMBER_MODELLED = os.environ.get('DTB_SQL_DIALECT', 'sqlite') in (
    'sqlite',
    'postgresql',
    'mssql',
) and not os.environ.get('DTB_OPENSEARCH_URL')
if TINY_NUMBER_MODELLED:
    DECIMAL_BOUNDS.append('1.0e-16383')
#: Search words over a decimal's text (PRD-F23), each found in one spelling and not the other when a row holds the
#: value it names (of `DECIMALS`, the scenario sets the first, one of the next two and the fourth; `68.00`, `.9` and
#: `7.90` need the draw to give a row that value): `4568` in SQLite's text of the first three (`12345678901234568`) and
#: in the fixed-scale one of 12345678901234568.00 only, the others in the fixed-scale text only
DECIMAL_WORDS = ['.30', '4568', '68.00', '567.8', '.9', '7.90']
CELLS: dict[str, st.SearchStrategy[Any]] = {
    's': st.none() | st.sampled_from(STRINGS),
    't': st.none() | st.sampled_from(STRINGS),
    'n': st.none() | st.sampled_from(NUMBERS),
    'd': st.none() | st.sampled_from(DATES),
    'b': st.none() | st.booleans(),
    'm': st.none() | st.sampled_from(DECIMALS),
}


@st.composite
def table_rows(draw: st.DrawFn) -> list[dict[str, Any]]:
    count = draw(st.sampled_from([3, 4, 5, 6, 7, 8, 0, 1, 2, 3, 4, 5, 6, 7, 8, 0]))
    keys = draw(st.permutations(range(1, count + 1)))  # inserted in any order; the key decides (PRD-O4)
    return [{'id': key, **{name: draw(cell) for name, cell in CELLS.items()}} for key in keys]


@st.composite
def declarations(draw: st.DrawFn) -> tuple[Declared, ...]:
    """Each column searchable and orderable but one time in twelve, and one of them always unorderable."""
    unorderable = draw(st.sampled_from(NAMES))
    return tuple(
        (name, KINDS[name], not draw(chance(12)), name != unorderable and not draw(chance(12))) for name in NAMES
    )


#: A cell that holds a value, by column
VALUED: dict[str, st.SearchStrategy[Any]] = {
    's': st.sampled_from(STRINGS),
    't': st.sampled_from(STRINGS),
    'n': st.sampled_from(NUMBERS),
    'd': st.sampled_from(DATES),
    'b': st.booleans(),
    'm': st.sampled_from(DECIMALS),
}
#: The values of a range, low to high, by column
RANGED: dict[str, list[Any]] = {'n': NUMBERS, 'd': DATES}
#: A word that ColumnControl's `contains` finds in some cells, by type
CONTAINED: dict[str, list[str]] = {'string': ['a', 'red', 'b', 'x_y', '-'], 'number': ['7', '0', '1', '-'],
                                   'date': ['2024', '03', '-', '12-31'],
                                   'decimal': ['.3', '0', '68', '.89']}  # fmt: skip
BIG = '9223372036854775808'  # 2^63, one past a BigInteger (PRD-F25, step 3)
LAST = '9999-12-31'  # the last day there is (PRD-F24)
#: The values a filter sends, by column: most of them read, some not (`abc`, `2024-13-01`, `yes`, and seldom a
#: number or a day out of range)
VALUES: dict[str, st.SearchStrategy[str]] = {
    's': st.sampled_from([*STRINGS, 'x', 'zz', 'REDDISH', 'A-B']),
    't': st.sampled_from([*STRINGS, 'x', 'zz', 'REDDISH', 'A-B']),
    'id': rarely(st.sampled_from(['1', '2', '3', '5', '8', ' 2 ', '+3', '-1', '4']), st.just('abc'), 6),
    'n': rarely(st.sampled_from(['-7', '0', '7', '17', '70', '8', ' 7 ', '+7', '-7', '0', '7', '17', '70', str(2**53),
                                 str(2**53 + 1)]),
                st.sampled_from(['abc', '1_000', '7 7', BIG]), 10),
    'd': rarely(st.sampled_from(['2023-01-01', '2024-03-05', '2024-03-06', '2024-12-31', ' 2024-03-05 ', '2024-03-05',
                                 '2024-12-31', '2024-03-06', '2023-01-01']),
                st.sampled_from(['2024-13-01', '2024', '2024-02-30', '9999-12-31']), 10),
    'b': rarely(st.sampled_from(['true', 'false', '1', '0', 'TRUE', ' 0 ', 'true', 'false', '1', '0']),
                st.sampled_from(['yes', '   ', 'on']), 8),
    'm': rarely(st.sampled_from(DECIMAL_OPERANDS), st.sampled_from([*DECIMAL_BOUNDS, 'abc']), 10),
}  # fmt: skip
VALUES['extra'] = VALUES['s']
#: A day, a month or a year for `contains` on a date (PRD-F22), and now and then one that cannot be read
SPANS = rarely(st.sampled_from(['2024', '2023', '2024-03', '2024-12', '2024-03-05', ' 2024 ']),
               st.sampled_from(['2024-3', '24', '2024-13', '9999', '9999-12']), 5)  # fmt: skip

WORDS = ['a', 'A', 'b', 'red', 'Red', 'RED', '7', '17', '2024', '2024-03', '03-05', '-', '_', '%', 'x_y', '5%', '[a]',
         '\\', 'ab', 'a-b', 'e', 'zz']  # fmt: skip
TOKENS = [*WORDS, *WORDS, *(f'!{word}' for word in WORDS), '"a b"', '!"a b"', '"b a"', '"', '""', '!', '!x', '5"',
          '!5"', 'x"a', '"a" "b"x']  # fmt: skip
JOINERS = st.sampled_from([' ', ' ', ' ', ' ', '  ', '\n', '\r'])
#: Search text: 1 or 2 tokens, seldom 3
text = st.builds(lambda tokens, joiner: joiner.join(tokens),
                 st.lists(st.sampled_from(TOKENS), min_size=1, max_size=2) | st.lists(st.sampled_from(TOKENS),
                                                                                      min_size=3, max_size=3),
                 JOINERS)  # fmt: skip


def step_one(common: st.SearchStrategy[str], one_in: int, many_words: bool = True) -> st.SearchStrategy[str]:
    """``common``, and about once in ``one_in`` draws each a mistake of step 1 (PRD-E3): a control character, 256 code
    points and, for search text, 17 words. One draw per kind: a single choice among them would favour its first."""
    control = st.sampled_from(['a\x00', 'a\tb', 'b\x7f'])
    common = rarely(rarely(common, control, max(2, one_in * 2 // 3)), st.just('x' * 256), one_in)
    return rarely(common, st.just(' '.join(['a'] * 17)), one_in) if many_words else common


SEARCHED: dict[str, st.SearchStrategy[str]] = {
    'string': text,
    'number': st.sampled_from(['7', '17', '-', '-7', '0', '1', 'abc', '!7', '"7"', '7 0']) | text,
    'date': st.sampled_from(['2024', '2024-03', '03-05', '-', '2023', '12-31', '!2024', '2024 03']) | text,
    'decimal': st.sampled_from([*DECIMAL_WORDS, '0.305', '!.30', '1e400']) | text,
    'boolean': rarely(st.sampled_from(['true', 'false', '1', '0', 'TRUE', ' 0 ', 'true', 'false', 'yes', '   ']),
                      st.sampled_from(['true false', '!true', '"true"', '!x']), 8),
}  # fmt: skip
CONDITIONS: dict[Kind, list[str]] = {
    'string': ['=', '!=', 'contains', '!contains', 'starts', '!starts', 'ends', '!ends', 'null', '!null'],
    'number': ['=', '!=', '<', '<=', '>', '>=', 'between', '!between', 'null', '!null'],
    'decimal': ['=', '!=', '<', '<=', '>', '>=', 'between', '!between', 'null', '!null'],
    'date': ['=', '!=', '<', '<=', '>', '>=', 'between', '!between', 'contains', '!contains', 'null', '!null'],
    'boolean': ['=', '!=', 'null', '!null'],
}
ALL_CONDITIONS = sorted({condition for conditions in CONDITIONS.values() for condition in conditions})
LOGICS: dict[Kind, list[str]] = {
    'string': ['contains', 'notContains', 'equal', 'notEqual', 'starts', 'ends', 'empty', 'notEmpty'],
    'number': ['contains', 'equal', 'notEqual', 'greater', 'greaterOrEqual', 'less', 'lessOrEqual', 'empty',
               'notEmpty'],
    'decimal': ['contains', 'equal', 'notEqual', 'greater', 'greaterOrEqual', 'less', 'lessOrEqual', 'empty',
                'notEmpty'],
    'date': ['contains', 'notContains', 'equal', 'notEqual', 'greater', 'greaterOrEqual', 'less', 'lessOrEqual',
             'empty', 'notEmpty'],
    'boolean': ['contains', 'equal', 'notEqual', 'empty', 'notEmpty'],
}  # fmt: skip
ALL_LOGICS = sorted({logic for logics in LOGICS.values() for logic in logics})
NEGATIVE = {'!=', '!contains', '!starts', '!ends', '!between', 'notEqual', 'notContains'}


def escaped(value: str) -> str:
    """A value escaped by DataTables' `util.escapeRegex` set (PRD-F19)."""
    return ''.join(f'\\{character}' if character in '/.*+?|()[]{}\\$^-' else character for character in value)


#: Column search text that PRD-F6 reduces to no word (`!a` and `!x` are words PRD-F35 drops), now and then with a
#: control character, which PRD-F15 refuses first
NO_WORD = rarely(st.sampled_from(['   ', '"', '""', '"""', '!""', '!', '!x', '!a', ' \n ']),
                 st.sampled_from(['!\x01', '!\t']), 12)  # fmt: skip


def values_of(name: str) -> st.SearchStrategy[list[str]]:
    """The entries of a list: one to three values, now and then an empty one."""
    return st.lists(rarely(VALUES[name], st.just(''), 6), min_size=1, max_size=3)


@st.composite
def column_search(draw: st.DrawFn, name: str, refused: bool, active: bool = False) -> dict[str, Any]:
    """A column's search: mostly idle, on a column that cannot be searched almost always; smart text, a categorical
    regex, a list, or now and then a value beside a list. On any column, now and then text with no word (PRD-F6),
    alone or beside a list."""
    kind = KINDS.get(name, 'string')
    if draw(chance(3 if refused else 8)):
        no_word = {'value': draw(NO_WORD), 'regex': False}
        beside = not refused and draw(st.booleans())  # on a column that cannot be searched the list is refused
        return {**no_word, 'multi_values': draw(values_of(name))} if beside else no_word
    if not active and not draw(chance(32 if refused else 4)):
        return {'value': '', 'regex': False}
    form = draw(st.sampled_from(['text', 'text', 'text', 'regex', 'multi', 'multi']))
    values = draw(values_of(name))
    if form == 'multi':
        both = draw(chance(16))
        return {'value': draw(st.sampled_from(WORDS)) if both else '', 'regex': False, 'multi_values': values}
    if form == 'regex':
        anchored = '^(' + '|'.join(map(escaped, values)) + ')$'
        shapes = st.sampled_from([anchored, '|'.join(map(escaped, values)), f'^{escaped(values[0])}$', '^()$', '|'])
        other = rarely(st.sampled_from(['a.b', '(a)', '^a|b$', '\\d', 'a$']), st.just('a.' + 'x' * 256), 3)
        return {'value': draw(rarely(shapes, other, 6)), 'regex': True}
    return {'value': draw(step_one(SEARCHED[kind], 20, many_words=kind != 'boolean')), 'regex': False}


@st.composite
def columncontrol(draw: st.DrawFn, name: str, refused: bool, active: bool = False) -> dict[str, Any]:
    """ColumnControl's part: mostly none; a search of a logic that fits the column, now and then one that does not, or
    a list. When it must be active, its `contains` half the time, beside the column's own search (PRD-F45)."""
    kind = KINDS.get(name, 'string')
    if not active and not draw(chance(48 if refused else 6)):
        return {}
    contains = active and draw(st.booleans())
    if not contains and draw(chance(3)):
        return {'list': draw(values_of(name))}
    logic = 'contains' if contains else draw(rarely(st.sampled_from(LOGICS[kind]), st.sampled_from(ALL_LOGICS), 16))
    if logic in ('empty', 'notEmpty'):
        value = ''
    elif logic == 'contains':
        value = draw(step_one(SEARCHED[kind], 12, many_words=kind != 'boolean'))
    elif logic == 'notContains' and kind == 'date':
        value = draw(SPANS)
    else:
        value = draw(step_one(VALUES[name], 32, many_words=False))
    return {'search': {'value': value, 'logic': logic, 'type': 'text'}}


@st.composite
def criterion(draw: st.DrawFn) -> dict[str, Any]:
    """A SearchBuilder criterion: mostly a condition that fits its column with the values it takes; now and then one
    that does not fit, a value too many or too few, an empty value, a column not declared, or an unfinished one."""
    name = draw(rarely(st.sampled_from(NAMES), st.just('extra'), 48))
    kind = KINDS.get(name, 'string')
    if draw(chance(24)):  # unfinished: no condition, or no column
        unfinished = draw(st.sampled_from([{'condition': ''}, {'origData': ''}, {}]))
        value = draw(step_one(VALUES[name], 2, many_words=False))  # read by no condition (PRD-F14, PRD-F15)
        return {'origData': name, 'data': name, 'type': 'string', 'value': [value], **unfinished}
    condition = draw(rarely(st.sampled_from(CONDITIONS[kind]), st.sampled_from(ALL_CONDITIONS), 16))
    takes = 2 if condition in ('between', '!between') else 0 if condition in ('null', '!null') else 1
    count = draw(rarely(st.just(takes), st.sampled_from([0, takes + 1]), 24))
    value = SPANS if kind == 'date' and condition in ('contains', '!contains') else VALUES[name]
    values = draw(st.lists(step_one(value, 48, many_words=False), min_size=count, max_size=count))
    return {'origData': name, 'data': name, 'condition': condition, 'type': 'string', 'value': values}


def groups_of(child: st.SearchStrategy[Any]) -> st.SearchStrategy[dict[str, Any]]:
    return st.fixed_dictionaries({'logic': st.sampled_from(['AND', 'OR']), 'criteria': st.lists(child, max_size=3)})


#: The root holds at least one node, as SearchBuilder sends it once a condition is added
tree = st.fixed_dictionaries({
    'logic': st.sampled_from(['AND', 'OR']),
    'criteria': st.lists(st.recursive(criterion(), groups_of, max_leaves=4), min_size=1, max_size=3),
})  # fmt: skip


def nameless(column: dict[str, Any]) -> bool:
    return not column['name'] and (column['data'] in (None, '', 'function') or isinstance(column['data'], dict))


@st.composite
def request_columns(draw: st.DrawFn, server: dict[str, tuple[bool, bool]]) -> list[dict[str, Any]]:
    """The request's columns, with their searches: declared ones, now and then one named twice, a nameless one and an
    undeclared one."""
    names: list[str | None] = list(draw(st.lists(st.sampled_from(NAMES), min_size=1, max_size=5, unique=True)))
    if draw(chance(3)):
        names.append(draw(st.sampled_from(names)))  # a column named twice
    if draw(chance(4)):
        names.append(None)  # a nameless column
    if draw(chance(8)):
        names.append('extra')  # a column the server does not declare
    columns: list[dict[str, Any]] = []
    for name in draw(st.permutations(names)):
        searchable, orderable = server.get(name or '', (False, False))
        refused = name not in server or not searchable
        # The request mostly agrees with the server on what can be sorted, as a configured table does
        request_orderable = orderable != draw(chance(6)) if name in server else draw(chance(8))
        column = {
            'data': draw(st.sampled_from([None, 'function', None, {'_': 's'}])) if name is None else name,
            'name': '',
            'searchable': not draw(chance(8)),
            'orderable': request_orderable,
            'search': draw(column_search(name or 'extra', refused)),
            'columnControl': draw(columncontrol(name or 'extra', refused)),
        }
        columns.append(column)
    searched = [index for index, column in enumerate(columns)
                if isinstance(column['data'], str) and server.get(column['data'], (False, False))[0]]  # fmt: skip
    if searched and draw(chance(4)):  # two filters on one column: its search and ColumnControl's
        index = draw(st.sampled_from(searched))
        columns[index]['search'] = draw(column_search(columns[index]['data'], refused=False, active=True))
        columns[index]['columnControl'] = draw(columncontrol(columns[index]['data'], refused=False, active=True))
    return columns


@st.composite
def added_criteria(draw: st.DrawFn) -> list[dict[str, Any]]:
    """Criteria added to the tree now and then: a value the column cannot read, so that steps 2 and 3 of PRD-E3 meet
    the others, and a range written high to low, which PRD-F27 reads in either order."""
    added: list[dict[str, Any]] = []
    for name, unreadable, one_in in (('d', ['2024-13-01', '2024', '9999-12-31', '05.03.2024'], 12),
                                     ('n', ['abc', '7.5.1', BIG], 18)):  # fmt: skip
        if draw(chance(one_in)):
            condition = draw(st.sampled_from(['=', '<', '!=', '>=']))
            added.append({'origData': name, 'data': name, 'condition': condition,
                          'value': [draw(st.sampled_from(unreadable))]})  # fmt: skip
    for condition in ('between', '!between'):
        if draw(chance(4)):
            name = draw(st.sampled_from(['n', 'd']))
            pool = [str(number) for number in NUMBERS] if name == 'n' else [day.isoformat() for day in DATES]
            low, high = sorted(draw(st.lists(st.sampled_from(range(len(pool))), min_size=2, max_size=2, unique=True)))
            added.append({'origData': name, 'data': name, 'condition': condition, 'value': [pool[high], pool[low]]})
    return added


@st.composite
def cases(draw: st.DrawFn) -> tuple[list[dict[str, Any]], tuple[Declared, ...], dict[str, Any]]:
    """Rows, a declaration and a request body."""
    rows = draw(table_rows())
    decl = draw(declarations())
    server = {name: (searchable, orderable) for name, _, searchable, orderable in decl}
    columns = draw(request_columns(server))

    def skipped(column: dict[str, Any]) -> bool:
        key = column['data'] if isinstance(column['data'], str) else None
        return nameless(column) or (not column['orderable'] and not server.get(key or '', (False, False))[1])

    entry = st.fixed_dictionaries({'column': st.integers(0, len(columns) - 1), 'dir': st.sampled_from(['asc', 'desc'])})
    order = draw(st.lists(entry, max_size=3))
    skips = [index for index, column in enumerate(columns) if skipped(column)]
    paging: dict[str, int] = {}
    unfiltered = False
    applies = [index for index, column in enumerate(columns)
               if isinstance(column['data'], str) and server.get(column['data'], (False, False))[1]]  # fmt: skip
    if skips and applies and not draw(chance(4)):  # a skipped entry first, then one applied, in the other direction
        direction = draw(st.sampled_from(['asc', 'desc']))
        other = 'desc' if direction == 'asc' else 'asc'
        # the key cannot tie, so another column is applied where there is one
        applied = draw(st.sampled_from([index for index in applies if columns[index]['data'] != 'id'] or applies))
        order = [{'column': draw(st.sampled_from(skips)), 'dir': direction},
                 {'column': applied, 'dir': other}, *order[:1]]  # fmt: skip
        tied = columns[applied]['data']
        if tied != 'id' and len(rows) >= 2:
            # A tie on the applied column that a short page cuts: only the key, in the applied entry's direction,
            # orders the tied rows (PRD-O3, PRD-O8)
            value = draw(CELLS[tied])
            for row in rows:
                row[tied] = value
            order = order[:2]
            paging = {'start': draw(st.integers(0, len(rows) - 2)), 'length': draw(st.integers(1, 2))}
            unfiltered = draw(st.booleans())  # half the time no filter thins the tie out
    body: dict[str, Any] = {
        'draw': 1,
        'start': draw(st.sampled_from([0, 0, 0, 1, 2, 3, 5, 8, 9, 20])),
        'length': draw(st.integers(1, 10)),
        'columns': columns,
        'order': order,
        'search': {'value': draw(step_one(rarely(st.just(''), text, 3), 20)), 'regex': draw(chance(24))},
    }
    body.update(paging)
    if draw(chance(32)):
        # PRD-F40: its entries are not read, so PRD-F14 and PRD-F15 do not apply to them
        body['search']['multi_values'] = draw(st.lists(step_one(VALUES['s'], 3, many_words=False), max_size=2))
    if draw(chance(3)):
        body['searchBuilder'] = draw(tree)
    if added := draw(added_criteria()):
        body.setdefault('searchBuilder', {'logic': 'AND', 'criteria': []})['criteria'].extend(added)
    focused(draw, rows, server=server, body=body, applies=applies, unfiltered=unfiltered)
    outranked(draw, rows, server=server, body=body)
    compared(draw, rows, server=server, body=body)
    return rows, decl, body


#: Text that a LIKE pattern would read as a wildcard or an escape, and words of it (PRD-F11, PRD-F19)
LIKE_CELLS = ['a\\b', '5%', 'x_y', '[a]', 'ab', 'a', '50', None]
LIKE_WORDS = ['\\', '%', '_', '[', '[a]', 'a\\b', '5%', 'x_y']


def compared(
    draw: st.DrawFn, rows: list[dict[str, Any]], *, server: dict[str, tuple[bool, bool]], body: dict[str, Any]
) -> None:
    """Now and then a request whose answer hangs on how a database compares or searches text, each from a few rows set
    for it, with the other filters cleared. At most one per request, so that none undoes another: about once in seven
    and a half for the LIKE words, PAD SPACE, a date's text and a decimal, once in fifteen for the others. PRD-F11,
    PRD-F19: a search word holding `\\`, `%`, `_` or `[`, over cells holding them, is plain text. PRD-F23: `%` or `_`
    alone as a global word over number text only matches no row. PRD section 5: a test of `''`, `' '`, `'a'` and `'a '`
    that PAD SPACE answers otherwise. PRD-F25, PRD-D23: 2^63 on `n` as the request's only mistake. PRD-F23: a column
    search on a date column by a part of its `YYYY-MM-DD` text. PRD-F23, PRD-F25, PRD section 5: a filter on `m`, over
    rows that may hold two of the three numbers a float cannot tell apart and 0.30 (`decimal`). The `%` and the 2^63
    scenarios drop the order entries, since one may hold a mistake of its own."""
    columns = body['columns']
    present = {column['data']: index for index, column in enumerate(columns) if isinstance(column['data'], str)}
    searchable = [name for name in present if server.get(name, (False, False))[0]]

    def clear() -> None:
        body['search'] = {'value': '', 'regex': False}
        body.pop('searchBuilder', None)
        for column in columns:
            column['search'], column['columnControl'] = {'value': '', 'regex': False}, {}

    texts = [name for name in ('s', 't') if name in searchable]
    scenario = draw(st.sampled_from([None] * 5 + ['like', 'like', 'decimal', 'wildcard', 'decimal', 'pad', 'pad',
                                                  'beyond', 'date', 'date']))  # fmt: skip
    if scenario == 'like' and rows and 's' in searchable:  # PRD-F11, PRD-F19
        for row, cell in zip(rows, draw(st.permutations(LIKE_CELLS)), strict=False):
            row['s'] = cell
        clear()
        word = draw(st.sampled_from(LIKE_WORDS))
        column = columns[present['s']]
        if draw(st.booleans()):
            column['searchable'] = True
            body['search'] = {'value': word, 'regex': False}
        else:
            column['search'] = {'value': word, 'regex': False}
    if scenario == 'wildcard' and rows and 'n' in searchable:  # PRD-F23: a wildcard over number text only
        clear()
        body['order'] = []
        for column in columns:
            column['searchable'] = column['data'] == 'n'
        body['search'] = {'value': draw(st.sampled_from(['%', '_'])), 'regex': False}
        draw(st.sampled_from(rows))['n'] = draw(st.sampled_from([7, 17, -3]))
    if scenario == 'pad' and rows and texts:  # PRD section 5, PRD-F26
        name = draw(st.sampled_from(texts))
        for row in rows:
            row[name] = draw(st.sampled_from([' ', '', 'a', 'a ', None]))
        clear()
        padded(draw, columns[present[name]], body)
    if scenario == 'beyond' and 'n' in searchable:  # PRD-F25, PRD-D23: 2^63 alone
        clear()
        body['order'] = []  # an order entry may hold a mistake of its own
        form = draw(st.sampled_from(['criterion', 'list', 'equal']))
        if form == 'criterion':
            joined(body, {'origData': 'n', 'data': 'n', 'condition': draw(st.sampled_from(['=', '<', '!='])),
                          'value': [BIG]})  # fmt: skip
        elif form == 'list':
            columns[present['n']]['search'] = {'value': '', 'regex': False, 'multi_values': [BIG]}
        else:
            columns[present['n']]['columnControl'] = {'search': {'value': BIG, 'logic': 'equal', 'type': 'text'}}
    if scenario == 'date' and rows and 'd' in searchable:  # PRD-F23: a date searched by its text
        dated(draw, rows)
        clear()
        word = draw(st.sampled_from(['2024-03', '2024', '03-05', '2024-12', '-03-', '2023-01-01']))
        columns[present['d']]['search'] = {'value': word, 'regex': False}
    if scenario == 'decimal' and rows and server['m'][0]:  # PRD-F23, PRD-F25, PRD section 5
        clear()
        decimal(draw, rows, columns=columns, present=present, orderable=server['m'][1], body=body)


def dated(draw: st.DrawFn, rows: list[dict[str, Any]]) -> None:
    """Days for `d` that a search by date text tells apart: 2024-03-05 and 2024-12-31 among them, so that most words
    find a row and leave out another."""
    for row in rows:
        row['d'] = draw(st.sampled_from([date(2024, 3, 5), date(2024, 12, 31), date(2023, 1, 1), None]))
    draw(st.sampled_from(rows))['d'] = date(2024, 3, 5)
    draw(st.sampled_from(rows))['d'] = date(2024, 12, 31)


def decimal(
    draw: st.DrawFn,
    rows: list[dict[str, Any]],
    *,
    columns: list[dict[str, Any]],
    present: dict[str, int],
    orderable: bool,
    body: dict[str, Any],
) -> None:
    """Rows that may hold two of the three numbers a float cannot tell apart and 0.30, and a filter on `m` whose answer
    hangs on how a number is compared with a `Numeric` or how a decimal is spelled: a criterion, a list or
    ColumnControl's comparison with a number of `DECIMAL_OPERANDS` (half the time one of the first five, one in four
    one of the others and one in four one of `DECIMAL_BOUNDS`), or a column or global search with a word of
    `DECIMAL_WORDS`."""
    if 'm' not in present:  # the request names `m` last, after the columns its order entries point at
        present['m'] = len(columns)
        columns.append({'data': 'm', 'name': '', 'searchable': True, 'orderable': orderable,
                        'search': {'value': '', 'regex': False}, 'columnControl': {}})  # fmt: skip
    for row in rows:
        row['m'] = draw(st.sampled_from([*DECIMALS, None]))
    # two of the three a float cannot tell apart, and 0.30, whose text differs most between the spellings
    draw(st.sampled_from(rows))['m'] = DECIMALS[0]
    draw(st.sampled_from(rows))['m'] = draw(st.sampled_from(DECIMALS[1:3]))
    draw(st.sampled_from(rows))['m'] = DECIMALS[3]
    # half the time one of the first five, which an exact comparison and SQLite's tell apart over the rows set above,
    # or which a rounding to the column's scale would find (`0.305`, SPEC-F25.11)
    common = rarely(st.sampled_from(DECIMAL_OPERANDS[:5]), st.sampled_from(DECIMAL_OPERANDS[5:]), 3)
    operand = rarely(common, st.sampled_from(DECIMAL_BOUNDS), 4)
    form = draw(st.sampled_from(['criterion', 'column', 'global', 'criterion', 'list', 'column', 'control']))
    column = columns[present['m']]
    if form == 'criterion':
        condition = draw(st.sampled_from(['=', '!=', '<', '<=', '>', '>=', 'between', '!between']))
        count = 2 if condition in ('between', '!between') else 1
        joined(body, {'origData': 'm', 'data': 'm', 'condition': condition,
                      'value': draw(st.lists(operand, min_size=count, max_size=count))})  # fmt: skip
    elif form == 'list':
        column['search'] = {
            'value': '',
            'regex': False,
            'multi_values': draw(st.lists(operand, min_size=1, max_size=2)),
        }
    elif form == 'control':
        logic = draw(st.sampled_from(['equal', 'notEqual', 'greater', 'lessOrEqual']))
        column['columnControl'] = {'search': {'value': draw(operand), 'logic': logic, 'type': 'text'}}
    elif form == 'column':
        column['search'] = {'value': draw(st.sampled_from(DECIMAL_WORDS)), 'regex': False}
    else:
        column['searchable'] = True
        body['search'] = {'value': draw(st.sampled_from(DECIMAL_WORDS)), 'regex': False}


def padded(draw: st.DrawFn, column: dict[str, Any], body: dict[str, Any]) -> None:
    """A test of `column`'s text that PAD SPACE may answer otherwise (PRD section 5): NULL or not, `=` or `!=` a value,
    a list, or ColumnControl's empty, not empty or equal."""
    name = column['data']
    form = draw(st.sampled_from(['null', '!null', '=', '!=', 'list', 'empty', 'equal']))
    value = draw(st.sampled_from([' ', 'a', 'a ']))
    if form == 'list':
        column['search'] = {'value': '', 'regex': False, 'multi_values': draw(st.sampled_from([[''], [' '], ['a'], ['a ']]))}  # fmt: skip
    elif form in ('empty', 'equal'):
        logic = {'empty': draw(st.sampled_from(['empty', 'notEmpty'])), 'equal': 'equal'}[form]
        column['columnControl'] = {'search': {'value': value if form == 'equal' else '', 'logic': logic, 'type': 'text'}}  # fmt: skip
    else:
        joined(body, {'origData': name, 'data': name, 'condition': form,
                      'value': [] if form in ('null', '!null') else [value]})  # fmt: skip


def outranked(
    draw: st.DrawFn, rows: list[dict[str, Any]], *, server: dict[str, tuple[bool, bool]], body: dict[str, Any]
) -> None:
    """Now and then a request that tells more mutants apart, each only adding to the request's filters (a criterion
    joins the tree under an AND), so a range `focused` wrote stays. M5b, about once in three: a one-word global search
    taken from one cell, over at least two searchable columns: a word may be found in any of them (PRD-F1). M10, about
    once in eight: the last day beside a mistake of step 1 or 2, in either order (PRD-E3: the earlier step answers);
    M10b, about once in twelve: the last day beside a global search with no searchable column, a mistake of step 4
    (PRD-E3: step 3 answers). About once in sixteen: 2^53 or 2^53 + 1 in a row and the other in a criterion on `n`,
    which a number bound as a double cannot tell apart (PRD-F25). About once in twenty: a list of more than 256, or more
    than 512, entries on the global search beside a number mistake of step 2, which answers: PRD-F40 reads no entry of
    that list, and PRD-F38's bounds on lists do not count them."""
    columns = body['columns']
    searched = sorted({column['data'] for column in columns if isinstance(column['data'], str) and column['searchable']
                       and server.get(column['data'], (False, False))[0] and KINDS[column['data']] != 'boolean'})  # fmt: skip
    if len(searched) >= 2 and rows and draw(chance(3)):
        name, row = draw(st.sampled_from(searched)), draw(st.sampled_from(rows))
        cell = '' if row[name] is None else str(row[name])  # a date's str is its ISO day
        begin = draw(st.integers(0, max(0, len(cell) - 1)))
        word = cell[begin : begin + draw(st.integers(1, 3))]
        if word.strip() == word and word and ' ' not in word:
            body.update(search={'value': word, 'regex': False}, start=0)
            if draw(st.booleans()):  # half the time no column filter hides the row
                for column in columns:
                    column['search'], column['columnControl'] = {'value': '', 'regex': False}, {}
    last_day = {'origData': 'd', 'data': 'd', 'condition': draw(st.sampled_from(['=', '<', '>='])), 'value': [LAST]}
    if draw(chance(8)):
        # mostly a number mistake of step 2 read after the last day: an earlier step answers though read later
        other = {'origData': 'n', 'data': 'n', 'condition': '=',
                 'value': [draw(rarely(st.sampled_from(['abc', '1_000', '7 7']), st.sampled_from(['a\x00', 'x' * 256]), 6))]}  # fmt: skip
        joined(body, *([other, last_day] if draw(chance(4)) else [last_day, other]))
    elif draw(chance(12)):
        joined(body, last_day)
        body['search'] = {'value': draw(st.sampled_from(['a', 'red', '7'])), 'regex': False}
        for column in columns:
            column['searchable'] = False
    if rows and draw(chance(16)):
        # 2^53 in a row, its neighbour in a criterion: a number bound as a double is the other one (PRD-F25)
        held, sent = draw(st.permutations([2**53, 2**53 + 1]))
        draw(st.sampled_from(rows))['n'] = held
        joined(body, {'origData': 'n', 'data': 'n', 'condition': draw(st.sampled_from(['=', '!=', '<', '>=', '>'])),
                      'value': [str(sent)]})  # fmt: skip
    if draw(chance(20)):
        entries = draw(st.sampled_from([257, 300, 513, 600]))
        body['search'] = {**body['search'], 'multi_values': [f'v{index}' for index in range(entries)]}
        joined(body, {'origData': 'n', 'data': 'n', 'condition': '=', 'value': ['abc']})


def joined(body: dict[str, Any], *criteria: dict[str, Any]) -> None:
    """``criteria`` added to the request's tree under an AND, the tree it held kept whole: beside the root's own
    criteria when the root is an AND group, else beside the root, one level deeper (far within PRD-F38's 8)."""
    existing = body.get('searchBuilder')
    if existing and existing['logic'] == 'AND':
        existing['criteria'] = [*existing['criteria'], *criteria]
    else:
        body['searchBuilder'] = {'logic': 'AND', 'criteria': [*([existing] if existing else []), *criteria]}


def focused(
    draw: st.DrawFn,
    rows: list[dict[str, Any]],
    *,
    server: dict[str, tuple[bool, bool]],
    body: dict[str, Any],
    applies: list[int],
    unfiltered: bool,
) -> None:
    """Now and then one of three requests that tell a mutant of the library apart (M4 and M6 about once in four, M7
    once in eight), half of them with no other filter (and with ``unfiltered`` none either): M4, an ascending order
    over NULLs and values with a short page (PRD-O2); M7, a range written high to low over a row inside it (PRD-F27);
    M6, a list and ColumnControl's `contains` on one column, ANDed (PRD-F45)."""
    columns = body['columns']
    nulls_first = draw(chance(4)) and [index for index in applies if columns[index]['data'] != 'id']
    anded = draw(chance(4)) and [
        index
        for index, column in enumerate(columns)
        if isinstance(column['data'], str)
        and server.get(column['data'], (False, False))[0]
        and KINDS[column['data']] != 'boolean'
    ]
    reversed_range = draw(chance(8)) and bool(rows)
    if unfiltered or (bool(nulls_first or anded or reversed_range) and draw(st.booleans())):
        body['search'] = {'value': '', 'regex': False}
        body.pop('searchBuilder', None)
        for column in columns:
            column['search'], column['columnControl'] = {'value': '', 'regex': False}, {}
    if nulls_first:
        index = draw(st.sampled_from(nulls_first))
        name = columns[index]['data']
        for row in rows:
            row[name] = None if draw(st.booleans()) else draw(VALUED[name])
        body.update(
            order=[{'column': index, 'dir': 'asc'}], start=draw(st.integers(0, 2)), length=draw(st.integers(1, 3))
        )
    if reversed_range:
        name = draw(st.sampled_from(['n', 'd']))
        pool = RANGED[name]
        low, high = sorted(draw(st.lists(st.sampled_from(range(len(pool))), min_size=2, max_size=2, unique=True)))
        draw(st.sampled_from(rows))[name] = pool[draw(st.integers(low, high))]
        bounds = [str(pool[high]), str(pool[low])]  # a date's str is its ISO day
        condition = draw(st.sampled_from(['between', '!between']))
        joined(body, {'origData': name, 'data': name, 'condition': condition, 'value': bounds})
    if anded:
        index = draw(st.sampled_from(anded))
        name = columns[index]['data']
        columns[index]['search'] = {'value': '', 'regex': False, 'multi_values': draw(values_of(name))}
        # now and then more than 16 words, which PRD-F14 refuses in this column search too (PRD-F45)
        word = draw(rarely(st.sampled_from(CONTAINED[KINDS[name]]), st.just(' '.join(['a'] * 17)), 8))
        columns[index]['columnControl'] = {'search': {'value': word, 'logic': 'contains', 'type': 'text'}}


def engine_for(
    dialect: str,
    order_key: Callable[[str], Any] | None = None,
    pad_space: bool | None = None,
    decimal_text: Callable[[Decimal], str] | None = None,
) -> Engine:
    """The evaluator's reading of a backend, by the name SQLAlchemy gives its dialect, or `opensearch`. The flags
    follow the requirements and the measured engine facts:
    - `typed_search` (PRD-F32, PRD-F33, PRD-F34): OpenSearch only; SQL searches a number or a date as its text
      (PRD-F23).
    - `empty_string_is_empty` (PRD-F26): every SQL database; on OpenSearch only a missing field is empty.
    - `pad_space` (PRD section 5, `docs/databases.md`, `docs/decisions/004-numbers-and-the-empty-string-on-the-sql-
      servers.md`): SQL Server (MEASURED on 2022) and MariaDB's default collation (MEASURED on 12.3); not PostgreSQL,
      SQLite or MySQL's default collation (NO PAD).
    - `order_key` (PRD-O6): the collation the leg measures on its backend, passed as ``order_key`` (on SQLite it must
      equal code-point order, its `BINARY`, the default when none is passed); without one, ordering by text is out of
      scope there.
    - `numeric` (PRD-F25, PRD section 5, `docs/decisions/010-decimal-numbers.md`): `exact` on PostgreSQL, MySQL,
      MariaDB and SQL Server; `sqlite` on SQLite; `double` on OpenSearch, whose `m` is a `double` field.
    - `decimal_digits` (PRD-F25, PRD-E3): 38 on SQL Server, no bound elsewhere.
    - `decimal_text` (PRD-F23, PRD section 5): the leg's measured spelling, passed as ``decimal_text``; on a SQL
      dialect, by default its documented one (`DECIMAL_TEXT`).
    A leg that measured PAD SPACE passes it as ``pad_space``, which then wins over the dialect's documented one."""
    if dialect not in PAD_SPACE:
        raise ValueError(f'no reading of the backend {dialect}')
    pads = PAD_SPACE[dialect] if pad_space is None else pad_space
    if dialect == 'opensearch':
        return Engine(
            typed_search=True, empty_string_is_empty=False, pad_space=pads, order_key=order_key, numeric='double'
        )
    spelled = decimal_text or DECIMAL_TEXT[dialect]
    return Engine(
        pad_space=pads,
        order_key=order_key or (code_points if dialect == 'sqlite' else None),
        numeric='sqlite' if dialect == 'sqlite' else 'exact',
        decimal_digits=38 if dialect == 'mssql' else None,
        decimal_text=spelled,
    )


#: PRD section 5 and `docs/databases.md`: whether a backend's default comparison of text ignores trailing spaces. SQL
#: Server (MEASURED on 2022) and MariaDB's default collation (MEASURED on 12.3) are PAD SPACE; MySQL's default
#: collation is NO PAD; PostgreSQL and SQLite tell `'a '` from `'a'`; OpenSearch compares a `keyword` as stored.
PAD_SPACE = {'sqlite': False, 'postgresql': False, 'mysql': False, 'mariadb': True, 'mssql': True, 'opensearch': False}
#: PRD-F23 and PRD section 5 ("a decimal keeps its trailing zeros except on SQLite"), `docs/databases.md`: a decimal's
#: search text as each SQL dialect documents it
DECIMAL_TEXT: dict[str, Callable[[Decimal], str]] = {
    'sqlite': sqlite_text,
    'postgresql': fixed_scale,
    'mysql': fixed_scale,
    'mariadb': fixed_scale,
    'mssql': fixed_scale,
}
#: Each dialect's own SQL that reads `m` back as text, for `measured_decimal_text`
DECIMAL_AS_TEXT = {
    'sqlite': 'SELECT id, CAST(m AS TEXT) FROM dtb_test_tags',
    'postgresql': 'SELECT id, CAST(m AS VARCHAR) FROM dtb_test_tags',
    'mysql': 'SELECT id, CAST(m AS CHAR) FROM dtb_test_tags',
    'mariadb': 'SELECT id, CAST(m AS CHAR) FROM dtb_test_tags',
    'mssql': 'SELECT id, CAST(m AS VARCHAR(60)) FROM dtb_test_tags',
}


def answer_of(response: DTResponse) -> Answer | Refusal:
    """A library response as the evaluator gives an answer: the error code, or the page's keys and both counts."""
    if response.error_code:
        return Refusal(response.error_code)
    return Answer(tuple(row['id'] for row in response.data), response.recordsTotal, response.recordsFiltered)


def measured_pad_space(connection: Connection) -> bool:
    """PRD section 5, measured: whether the database finds a stored `' '` by a plain `= ''`, written without any
    COLLATE, so that it is the comparison the column has."""
    connection.execute(reference_rows.insert(), [{'id': 1, 's': ''}, {'id': 2, 's': ' '}])
    found = connection.execute(sql_text("SELECT COUNT(*) FROM dtb_test_tags WHERE s = ''")).scalar_one()
    connection.execute(reference_rows.delete())
    return found == 2


def measured_decimal_text(connection: Connection, dialect: str) -> dict[Decimal, str]:
    """PRD-F23, measured: the text of each value of `DECIMALS` stored in `m`, read back by the dialect's own `CAST`
    written in raw SQL here, never the library's expression, so that a library that spelled a decimal otherwise than
    the database would be found."""
    connection.execute(reference_rows.insert(), [{'id': key, 'm': value} for key, value in enumerate(DECIMALS, 1)])
    texts = {DECIMALS[key - 1]: text for key, text in connection.execute(sql_text(DECIMAL_AS_TEXT[dialect]))}
    connection.execute(reference_rows.delete())
    return texts


def collation_ranks(connection: Connection) -> dict[str, int]:
    """PRD-O6 as the database orders: the rank of each value of `STRINGS` under `DENSE_RANK() OVER (ORDER BY s)`,
    read from `s` of the table itself and written without any COLLATE, so that it is the collation the column has. A
    library that added a COLLATE of its own would order otherwise, and the property would find it."""
    connection.execute(reference_rows.insert(), [{'id': key, 's': value} for key, value in enumerate(STRINGS, 1)])
    query = sql_text('SELECT s, DENSE_RANK() OVER (ORDER BY s) FROM dtb_test_tags')
    ranks: dict[str, int] = {row[0]: row[1] for row in connection.execute(query)}
    connection.execute(reference_rows.delete())
    assert sorted(ranks) == sorted(STRINGS), 'the database did not return every value of STRINGS as stored'
    return ranks


@dataclass(frozen=True)
class Leg:
    """The database a run reads, through both the sync and the async drivers, and the evaluator's reading of it."""

    sync: SQLEngine
    async_: AsyncEngine
    #: The one event loop of the module: an async engine's connections belong to the loop they were opened in
    runner: asyncio.Runner
    reading: Engine


@pytest.fixture(scope='module')
def leg(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Leg]:
    """The database of DTB_SQL_URL and DTB_SQL_ASYNC_URL, else an SQLite file both drivers open (an in-memory one
    cannot be shared with aiosqlite). `dtb_test_tags` is dropped first and at the end, and created with the columns
    above. The dialect must be DTB_SQL_DIALECT's, so a run that fell back to SQLite fails."""
    path: Path = tmp_path_factory.mktemp('reference') / 'reference.db'
    urls = SQL_URLS if SQL_URLS[0] else (f'sqlite:///{path}', f'sqlite+aiosqlite:///{path}')
    sync = create_engine(urls[0])
    try:
        dialect = sync.dialect.name
        assert dialect == os.environ.get('DTB_SQL_DIALECT', 'sqlite'), (
            'the property runs on another database than asked'
        )
        with sync.begin() as connection:
            reference_rows.drop(connection, checkfirst=True)
            reference_rows.create(connection)
        try:
            with sync.begin() as connection:
                pads = measured_pad_space(connection)
                ranks = collation_ranks(connection)
                spelled = measured_decimal_text(connection, dialect)
            assert pads == PAD_SPACE[dialect], (
                f'{dialect} compares trailing spaces otherwise than PRD section 5 and docs/databases.md say (PAD SPACE '
                f'documented {PAD_SPACE[dialect]}, measured {pads}): a finding to report, not to model'
            )
            documented = {value: DECIMAL_TEXT[dialect](value) for value in DECIMALS}
            assert spelled == documented, (
                f'{dialect} spells a decimal otherwise than PRD-F23, PRD section 5 and docs/databases.md say '
                f'(documented {sorted(documented.values())}, measured {sorted(spelled.values())}): a finding to '
                'report, not to model'
            )
            if dialect == 'sqlite':
                # SQLite's BINARY collation is code-point order, the evaluator's default
                assert ranks == {value: rank for rank, value in enumerate(sorted(STRINGS), 1)}
            async_ = async_engine(urls[1])  # its connections are opened inside `runner`'s loop, on first use
            runner = asyncio.Runner()
            try:
                yield Leg(sync, async_, runner, engine_for(dialect, ranks.__getitem__, pads, spelled.__getitem__))
            finally:
                runner.run(async_.dispose())
                runner.close()
        finally:
            with sync.begin() as connection:
                reference_rows.drop(connection, checkfirst=True)
    finally:
        sync.dispose()


def library(
    leg: Leg, decl: tuple[Declared, ...], rows: list[dict[str, Any]], params: DTParams
) -> tuple[Answer | Refusal, Answer | Refusal]:
    """The answers of `fetch_sync` and of `fetch` over the rows, loaded once, as the evaluator gives one."""
    table = SQLTable(
        reference_rows,
        [
            Column(name, type=declared_type(kind), searchable=searchable, orderable=orderable)
            for name, kind, searchable, orderable in decl
        ],
    )
    with leg.sync.begin() as connection:
        connection.execute(reference_rows.delete())
        if rows:
            connection.execute(reference_rows.insert(), rows)
    with Session(leg.sync) as session:
        synced = answer_of(table.fetch_sync(params, session))

    async def fetched() -> DTResponse:
        async with AsyncSession(leg.async_) as session:
            return await table.fetch(params, session)

    return synced, answer_of(leg.runner.run(fetched()))


def declared_type(kind: Kind) -> Literal['string', 'number', 'date', 'boolean']:
    """The library's type of a declared kind: the evaluator's `decimal` is a number column (PRD-D3)."""
    return 'number' if kind == 'decimal' else kind


def outcome(rows: list[dict[str, Any]], answer: Answer | Refusal) -> str:
    if isinstance(answer, Refusal):
        return f'error {answer.code}'
    if answer.ids:
        return 'answered with rows'
    if not rows:
        return 'empty: no rows'
    if not answer.records_filtered:
        return 'empty: the filters match no row'
    return 'empty: start past the end, rows matched'


def criteria_of(node: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for child in node.get('criteria', []):
        found.extend(criteria_of(child) if 'criteria' in child or 'logic' in child else [child])
    return found


def reversed_bounds(leaf: dict[str, Any]) -> bool:
    """Whether a criterion's two values are a range written high to low (PRD-F27): numbers and decimals as numbers, days
    as days."""
    kind = KINDS.get(leaf['origData'])
    if len(leaf['value']) != 2 or kind not in ('number', 'decimal', 'date'):
        return False
    low, high = (value.strip() for value in leaf['value'])
    try:
        if kind == 'number':
            return int(low) > int(high)
        if kind == 'decimal':
            return Decimal(low) > Decimal(high)
        return date.fromisoformat(low) > date.fromisoformat(high)
    except (ValueError, InvalidOperation):
        return False


def paths(
    rows: list[dict[str, Any]], decl: tuple[Declared, ...], body: dict[str, Any], engine: Engine = SQLITE
) -> list[str]:
    """The paths of an answered request that the generator is tuned to reach."""
    filters: Counter[str] = Counter()
    negated: set[str] = set()
    for column in body['columns']:
        name = column['data'] if isinstance(column['data'], str) else ''
        search, control = column['search'], column['columnControl']
        if search['value'].strip() or search.get('multi_values'):
            filters[name] += 1
            if not search['regex'] and any(word.startswith('!') and len(word) > 2 for word in search['value'].split()):
                negated.add(name)
        if control.get('list'):
            filters[name] += 1
        if 'search' in control and (control['search']['value'] or control['search']['logic'] in ('empty', 'notEmpty')):
            filters[name] += 1
            if control['search']['logic'] in NEGATIVE:
                negated.add(name)
    leaves = [leaf for leaf in criteria_of(body.get('searchBuilder', {})) if leaf.get('condition') and leaf['origData']]
    for leaf in leaves:
        filters[leaf['origData']] += 1
        if leaf['condition'] in NEGATIVE:
            negated.add(leaf['origData'])
    if any(word.startswith('!') and len(word) > 2 for word in body['search']['value'].split()):
        negated.update(name for name in KINDS if name != 'b')
    reached = []
    if leaves:
        reached.append('path: a SearchBuilder tree')
    reached.extend(
        f'path: a reversed {leaf["condition"]} over rows'
        for leaf in leaves
        if rows and leaf['condition'] in ('between', '!between') and reversed_bounds(leaf)
    )
    if any(count >= 2 for name, count in filters.items() if name in KINDS):
        reached.append('path: two filters on one column')
    if any(row[name] is None or row[name] == '' for row in rows for name in negated if name in KINDS):
        reached.append("path: a negation beside NULL or ''")
    ordered = [body['columns'][entry['column']]['data'] for entry in body['order']]
    if any(name in filters for name in ordered if isinstance(name, str)):
        reached.append('path: an order on a filtered column')
    server = {name: orderable for name, _, _, orderable in decl}
    if len(body['order']) >= 2:
        first, second = body['order'][:2]
        column = body['columns'][first['column']]
        key = column['data'] if isinstance(column['data'], str) else None
        skipped = nameless(column) or (not column['orderable'] and not server.get(key or '', False))
        then = body['columns'][second['column']]
        applied = isinstance(then['data'], str) and server.get(then['data'], False)
        if skipped and applied and second['dir'] != first['dir']:
            reached.append('path: a skipped first order entry, then one applied in the other direction')
            if tie_cut(rows, decl, body, then['data'], engine):
                reached.append('path: ... and a tie on the applied column that the page cuts')
    return list(dict.fromkeys([*reached, *no_word_paths(decl, body), *survivor_paths(rows, decl, body, engine)]))


def survivor_paths(
    rows: list[dict[str, Any]], decl: tuple[Declared, ...], body: dict[str, Any], engine: Engine = SQLITE
) -> list[str]:
    """The paths that tell three mutants of the library apart, each read from the evaluator's answers to the request
    and to the request with one part changed. M4: under an ascending first order, putting the NULLs last instead of
    first would change the page (PRD-O2). M6: dropping either a column search or list, or ColumnControl's `contains`
    on the same column, would change the answer (PRD-F45: they are ANDed). M7: a range written high to low keeps or
    drops a row that the unswapped reading would not (PRD-F27)."""
    reached: list[str] = []
    answer = evaluate(decl, rows, engine=engine, params=parse_request(body))
    first = first_applied(decl, body)
    if first is not None and first[1] == 'asc':
        whole = evaluate(
            decl, rows, engine=engine, params=parse_request({**body, 'start': 0, 'length': max(1, len(rows))})
        )
        if isinstance(whole, Answer):
            empty = {row['id'] for row in rows if row[first[0]] is None}
            last = [key for key in whole.ids if key not in empty] + [key for key in whole.ids if key in empty]
            window = slice(body['start'], body['start'] + body['length'])
            if whole.ids[window] != tuple(last[window]):
                reached.append('path M4: under an ascending order, NULLs last would change the page')
    for index, column in enumerate(body['columns']):
        search, control = column['search'], column['columnControl'].get('search', {})
        plain = search.get('multi_values') or (search['value'] and (search['regex'] or any(words(search['value']))))
        if plain and control.get('logic') == 'contains' and control.get('value'):
            without_plain, without_control = deepcopy(body), deepcopy(body)
            without_plain['columns'][index]['search'] = {'value': '', 'regex': False}
            del without_control['columns'][index]['columnControl']['search']
            if any(
                evaluate(decl, rows, engine=engine, params=parse_request(other)) != answer
                for other in (without_plain, without_control)
            ):
                reached.append(
                    'path M6: dropping a column search or list, or a ColumnControl contains beside it, '
                    'would change the answer'
                )
    for position, leaf in enumerate(criteria_of(body.get('searchBuilder', {}))):
        if leaf.get('condition') in ('between', '!between') and reversed_bounds(leaf):
            unswapped = deepcopy(body)
            target = criteria_of(unswapped['searchBuilder'])[position]
            target.clear()  # the unswapped reading: `between` high to low holds nothing, `!between` everything
            target.update(
                {
                    'logic': 'AND' if leaf['condition'] == 'between' else 'OR',
                    'criteria': [
                        {'origData': leaf['origData'], 'condition': condition} for condition in ('null', '!null')
                    ],
                }
            )
            if evaluate(decl, rows, engine=engine, params=parse_request(unswapped)) != answer:
                reached.append(f'path M7: a reversed {leaf["condition"]} changes the answer')
    if isinstance(answer, Answer) and found_in_one_column(rows, decl, body, answer, engine):
        reached.append('path M5b: a global word found in one searchable column of a row and not the others')
    return reached


def found_in_one_column(
    rows: list[dict[str, Any]], decl: tuple[Declared, ...], body: dict[str, Any], answer: Answer, engine: Engine
) -> bool:
    """Whether a one-word global search finds a row of the page through one of two or more searched columns only: a
    word must be found in some column, not in each (PRD-F1). Read from the evaluator: the request is run once per
    searched column, with every other column marked unsearchable, and a page row that exactly one run finds counts."""
    positive, negated = words(body['search']['value'])
    searchable = {name for name, kind, flag, _ in decl if flag and kind != 'boolean'}
    searched = {column['data'] for column in body['columns']
                if isinstance(column['data'], str) and column['searchable'] and column['data'] in searchable}  # fmt: skip
    if len(positive) != 1 or negated or len(searched) < 2:
        return False
    found: Counter[Any] = Counter()
    for name in searched:
        alone = {**body, 'start': 0, 'length': max(1, len(rows)), 'columns': [
            {**column, 'searchable': column['searchable'] and column['data'] == name} for column in body['columns']]}  # fmt: skip
        run = evaluate(decl, rows, engine=engine, params=parse_request(alone))
        if isinstance(run, Answer):
            found.update(run.ids)
    return any(found[key] == 1 for key in answer.ids)


def ranking_paths(
    rows: list[dict[str, Any]],
    decl: tuple[Declared, ...],
    body: dict[str, Any],
    answer: Answer | Refusal,
    engine: Engine = SQLITE,
) -> list[str]:
    """M10: the last day there is (step 3 of PRD-E3) read first, then a number mistake of step 2 later in the tree,
    which answers `invalid_number`: the shape where an answer by read order alone would give `invalid_date`. M10b:
    the last day beside a refusal of step 4, where the last day answers. Each read from the evaluator, a criterion
    alone over the same columns."""
    leaves = criteria_of(body.get('searchBuilder') or {})

    def alone(*criteria: dict[str, Any]) -> Answer | Refusal:
        return evaluate(decl, rows, engine=engine, params=parse_request({**body, 'search': {'value': ''}, 'columns': [
            {**column, 'search': {'value': '', 'regex': False}, 'columnControl': {}} for column in body['columns']],
            'order': [], 'searchBuilder': {'logic': 'AND', 'criteria': list(criteria)}}))  # fmt: skip

    last = [position for position, leaf in enumerate(leaves)
            if leaf.get('origData') == 'd' and LAST in leaf.get('value', []) and alone(leaf) == Refusal('invalid_date')]  # fmt: skip
    if not last or not isinstance(answer, Refusal):
        return []
    if answer.code == 'invalid_number' and any(
        leaf.get('origData') == 'n' and alone(leaf) == Refusal('invalid_number') for leaf in leaves[last[0] + 1 :]
    ):
        return ['path M10: a number mistake of step 2 read after the last day answers']
    without = evaluate(
        decl,
        rows,
        engine=engine,
        params=parse_request({key: value for key, value in body.items() if key != 'searchBuilder'}),
    )
    if answer.code == 'invalid_date' and without == Refusal('invalid_filter'):
        return ['path M10b: the last day answers before a refusal of step 4']
    return []


def events(
    rows: list[dict[str, Any]],
    decl: tuple[Declared, ...],
    body: dict[str, Any],
    answer: Answer | Refusal,
    engine: Engine = SQLITE,
) -> list[str]:
    """Every event of one example: its outcome, the shapes it sends, and the paths it reaches, each read from the
    evaluator for ``engine``."""
    reached = [
        outcome(rows, answer),
        *shapes(body),
        *ranking_paths(rows, decl, body, answer, engine),
        *compared_paths(rows, decl, body, answer, engine),
    ]
    neighbours = {2**53, 2**53 + 1}
    if any(row['n'] in neighbours for row in rows) and any(str(number) in repr(body) for number in neighbours):
        reached.append('shape: 2^53 or 2^53 + 1 sent beside a row that holds one of them')
    return reached + (paths(rows, decl, body, engine) if isinstance(answer, Answer) else [])


def compared_paths(
    rows: list[dict[str, Any]],
    decl: tuple[Declared, ...],
    body: dict[str, Any],
    answer: Answer | Refusal,
    engine: Engine,
) -> list[str]:
    """The paths of `compared`'s scenarios, each read from the evaluator's answer. PAD SPACE (PRD section 5): the
    answer differs from the one under the opposite `pad_space`. PRD-F11, PRD-F19: a word holding `\\`, `%`, `_` or `[`
    finds a row and leaves another out. PRD-F23: `%` or `_` alone as the global search over number or date text only,
    with a value there that a wildcard would match. PRD-F25, PRD-D23: 2^63 is the only mistake, so the answer is that
    of the request with 7 in its place but for it. PRD-F23: a column search on a date column finds a row and leaves out
    another that holds a day. PRD-F23, PRD-F25, PRD section 5: the paths of `decimal_paths` on `m`."""
    reached: list[str] = []
    params = parse_request(body)
    flipped = replace(engine, pad_space=not engine.pad_space)
    if evaluate(decl, rows, engine=flipped, params=params) != answer:
        reached.append('path: PAD SPACE would change the answer')
    reached.extend(decimal_paths(rows, decl, params, answer, engine))
    kinds = {name: kind for name, kind, _, _ in decl}
    searchable = {name for name, _, flag, _ in decl if flag}
    plain = [column for column in body['columns'] if not column['search']['regex'] and column['search']['value']]
    texts = [body['search']['value'], *(column['search']['value'] for column in plain)]
    if (
        isinstance(answer, Answer)
        and 0 < answer.records_filtered < answer.records_total
        and any(set(word) & set('\\%_[') for text in texts for word in words(text)[0])
    ):
        reached.append('path: a word holding \\, %, _ or [ finds a row and leaves out another')
    globally = {column['data'] for column in body['columns'] if isinstance(column['data'], str)
                and column['searchable'] and column['data'] in searchable and kinds[column['data']] != 'boolean'}  # fmt: skip
    positive, negated = words(body['search']['value'])
    if (isinstance(answer, Answer) and positive and set(positive) <= {'%', '_'} and not negated and globally
            and all(kinds[name] in ('number', 'decimal', 'date') for name in globally)
            and any(row[name] is not None for row in rows for name in globally)):  # fmt: skip
        reached.append('path: % or _ alone as the global search over number or date text only')
    if answer == Refusal('invalid_number') and BIG in json.dumps(body):
        fixed = json.loads(json.dumps(body).replace(BIG, '7'))
        if isinstance(evaluate(decl, rows, engine=engine, params=parse_request(fixed)), Answer):
            reached.append('path: 2^63 on n is the only mistake')
    days = sum(row['d'] is not None for row in rows)
    if (
        isinstance(answer, Answer)
        and 0 < answer.records_filtered < days
        and any(column['data'] == 'd' and words(column['search']['value'])[0] for column in plain)
    ):
        reached.append('path: a column search by date text finds a row and leaves out another')
    return reached


def decimal_paths(
    rows: list[dict[str, Any]], decl: tuple[Declared, ...], params: DTParams, answer: Answer | Refusal, engine: Engine
) -> list[str]:
    """The paths of a request on `m`, each read from the evaluator with one reading of the engine flipped (PRD-F25,
    PRD section 5): an exact comparison against SQLite's (OpenSearch's doubles against an exact one); the fixed-scale
    spelling against SQLite's (PRD-F23); SQL Server's 38 digits against no bound."""
    reached: list[str] = []
    numeric = 'sqlite' if engine.numeric == 'exact' else 'exact'
    if evaluate(decl, rows, params, engine=replace(engine, numeric=numeric)) != answer:
        reached.append('path: comparing a decimal exactly or as stored changes the answer')
    spelled = fixed_scale if engine.numeric == 'sqlite' else sqlite_text
    if evaluate(decl, rows, params, engine=replace(engine, decimal_text=spelled)) != answer:
        reached.append("path: a decimal's spelling changes the answer")
    digits = None if engine.decimal_digits else 38
    if evaluate(decl, rows, params, engine=replace(engine, decimal_digits=digits)) != answer:
        reached.append("path: SQL Server's 38 digits change the answer")
    return reached


def first_applied(decl: tuple[Declared, ...], body: dict[str, Any]) -> tuple[str, str] | None:
    """The column and direction of the first order entry applied in an answered request (PRD-O1, PRD-O8)."""
    orderable = {name: flag for name, _, _, flag in decl}
    for entry in body['order']:
        column = body['columns'][entry['column']]
        if not nameless(column) and isinstance(column['data'], str) and orderable.get(column['data'], False):
            return column['data'], entry['dir']
    return None


def shapes(body: dict[str, Any]) -> list[str]:
    """Values of step 1 that no condition reads, and ColumnControl text over the word cap, as sent (#205)."""

    def bad(value: str) -> bool:
        control = (character not in '\n\r' and unicodedata.category(character) == 'Cc' for character in value)
        return len(value) > 255 or any(control)

    reached: list[str] = []
    listed = body['search'].get('multi_values') or []
    if len(listed) > 256:
        reached.append('shape: a list of more than 256 entries on the global search')
    if any(len(value) > 255 for value in listed):
        reached.append('shape: an entry of more than 255 code points on a list on the global search')
    if any(bad(value) for value in listed):
        reached.append('shape: a list on the global search holding a step-1 value')
    if any(not (leaf.get('condition') and leaf.get('origData')) and any(map(bad, leaf['value']))
           for leaf in criteria_of(body.get('searchBuilder', {}))):  # fmt: skip
        reached.append('shape: an unfinished criterion holding a step-1 value')
    for column in body['columns']:
        value = column['search']['value']
        if column['search']['regex'] and len(value) > 255 and categorical(value) is None:
            reached.append('shape: a regex over 255 code points that is no categorical filter')
        control = column['columnControl'].get('search', {})
        if control.get('logic') == 'contains' and len(control['value'].split()) > 16:
            reached.append('shape: a ColumnControl contains of more than 16 words')
    return list(dict.fromkeys(reached))


def no_word_paths(decl: tuple[Declared, ...], body: dict[str, Any]) -> list[str]:
    """The paths of column search text that PRD-F6 reduces to no word."""
    reached: list[str] = []
    searchable = {name: flag for name, _, flag, _ in decl}
    for column in body['columns']:
        search = column['search']
        if not search['regex'] and search['value'] and not any(words(search['value'])):
            reached.append('path: a column search with no word')
            if search.get('multi_values'):
                reached.append('path: a column search with no word beside a list')
            key = column['data'] if isinstance(column['data'], str) else None
            if nameless(column) or not searchable.get(key or '', False):
                reached.append('path: a column search with no word on a column that cannot be searched')
    return reached


def tie_cut(
    rows: list[dict[str, Any]], decl: tuple[Declared, ...], body: dict[str, Any], name: str, engine: Engine
) -> bool:
    """Whether rows that tie on ``name`` stand both inside and outside the page: only the tie-breaker's direction
    decides which of them the page shows (PRD-O3)."""
    whole = evaluate(decl, rows, engine=engine, params=parse_request({**body, 'start': 0, 'length': max(1, len(rows))}))
    if not isinstance(whole, Answer):
        return False
    values = {row['id']: row[name] for row in rows}
    window = range(body['start'], body['start'] + body['length'])
    sequence = [values[key] for key in whole.ids]
    return any(
        sequence.count(value) >= 2
        and {index in window for index, other in enumerate(sequence) if other == value} == {True, False}
        for value in set(sequence)
    )


DECLARED: tuple[Declared, ...] = tuple((name, KINDS[name], True, True) for name in NAMES)


def three_rows(**third: Any) -> list[dict[str, Any]]:
    """Rows 1 to 3, every cell NULL but those given for row 3."""
    return [{**dict.fromkeys(NAMES), 'id': key, **(third if key == 3 else {})} for key in (1, 2, 3)]


def request_of(*columns: dict[str, Any], **body: Any) -> dict[str, Any]:
    """A request body over the named columns, each searchable and orderable with an idle search, as the generator
    shapes them."""
    shaped = [
        {
            'data': name,
            'name': '',
            'searchable': True,
            'orderable': True,
            'search': {'value': '', 'regex': False},
            'columnControl': {},
            **fields,
        }
        for name, fields in ((column.pop('data'), column) for column in columns)
    ]
    return {'draw': 1, 'start': 0, 'length': 10, 'search': {'value': ''}, 'order': [], 'columns': shaped, **body}


def tree_of(*criteria: tuple[str, str, list[str]]) -> dict[str, Any]:
    return {'logic': 'AND', 'criteria': [{'origData': name, 'condition': condition, 'value': values}
                                         for name, condition, values in criteria]}  # fmt: skip


#: Rows holding 2^53 and 2^53 + 1 in `n`, and `n = 2^53 + 1`: only row 2 holds it. A number bound as a double is 2^53,
#: so it finds row 1 too (PRD-F25)
NEIGHBOURS = (
    [{**dict.fromkeys(NAMES), 'id': 1, 'n': 2**53}, {**dict.fromkeys(NAMES), 'id': 2, 'n': 2**53 + 1}],
    DECLARED,
    {
        'draw': 1, 'start': 0, 'length': 10, 'search': {'value': ''}, 'order': [],
        'columns': [{'data': 'n', 'name': '', 'searchable': True, 'orderable': True, 'columnControl': {},
                     'search': {'value': '', 'regex': False}}],
        'searchBuilder': {'logic': 'AND', 'criteria': [{'origData': 'n', 'condition': '=', 'value': [str(2**53 + 1)]}]},
    },
)  # fmt: skip


#: The pinned examples of the property, one per case the random draw reaches seldom or that a mutant needed; the
#: OpenSearch leg takes `TYPED_EXAMPLES` of them (tests/test_reference_opensearch.py)
EXAMPLES: dict[str, tuple[list[dict[str, Any]], tuple[Declared, ...], dict[str, Any]]] = {
    'neighbours': NEIGHBOURS,
    # M4: d ascending, length 1: the NULL rows come before row 3's day (PRD-O2), so row 1 is the page
    'm4': (
        three_rows(d=date(2023, 1, 1)),
        DECLARED,
        request_of({'data': 'd'}, length=1, order=[{'column': 0, 'dir': 'asc'}]),
    ),
    # M6: a list `[""]` on n (rows 1 and 2, NULL) and ColumnControl `contains` 7 on n (row 3) are ANDed (PRD-F45): no
    # row; either alone, or the two ORed, would find one
    'm6': (
        three_rows(n=7),
        DECLARED,
        request_of(
            {
                'data': 'n',
                'search': {'value': '', 'regex': False, 'multi_values': ['']},
                'columnControl': {'search': {'value': '7', 'logic': 'contains', 'type': 'text'}},
            }
        ),
    ),
    # M7: `!between` 0 and -7 is the range -7 to 0 (PRD-F27): row 3's -7 is in it, the NULL rows pass (PRD-F37)
    'm7': (
        three_rows(n=-7),
        DECLARED,
        request_of({'data': 'n'}, searchBuilder=tree_of(('n', '!between', ['0', '-7']))),
    ),
    # PRD-E3, step 3: the last day comes before a number past a BigInteger, in either order: `invalid_date`
    'step3 big first': (
        three_rows(),
        DECLARED,
        request_of({'data': 'n'}, {'data': 'd'}, searchBuilder=tree_of(('n', '=', [BIG]), ('d', '<', ['9999-12-31']))),
    ),
    'step3 last first': (
        three_rows(),
        DECLARED,
        request_of({'data': 'n'}, {'data': 'd'}, searchBuilder=tree_of(('d', '<', ['9999-12-31']), ('n', '=', [BIG]))),
    ),
    # M10: a mistake of step 2 comes before the last day (step 3), in either order: `invalid_number` (PRD-E3)
    'm10 last first': (
        three_rows(),
        DECLARED,
        request_of({'data': 'n'}, {'data': 'd'}, searchBuilder=tree_of(('d', '=', [LAST]), ('n', '=', ['abc']))),
    ),
    'm10 abc first': (
        three_rows(),
        DECLARED,
        request_of({'data': 'n'}, {'data': 'd'}, searchBuilder=tree_of(('n', '=', ['abc']), ('d', '=', [LAST]))),
    ),
    # M10b: the last day (step 3) comes before a global search with no searchable column (step 4): `invalid_date`
    'm10b': (
        three_rows(),
        DECLARED,
        request_of(
            {'data': 'd', 'searchable': False}, search={'value': 'a'}, searchBuilder=tree_of(('d', '=', [LAST]))
        ),
    ),
    # M5b: `ab` is in row 3's `s` and in no `t`: a word may be found in any searched column (PRD-F1), so row 3
    'm5b': (three_rows(s='ab', t='x'), DECLARED, request_of({'data': 's'}, {'data': 't'}, search={'value': 'ab'})),
    # A regex over 255 code points that is no categorical filter is refused, not measured (PRD-F14, PRD-F18)
    'long regex': (
        three_rows(),
        DECLARED,
        request_of({'data': 's', 'search': {'value': 'a.' + 'x' * 256, 'regex': True}}),
    ),
    # A list on the global search is refused unread (PRD-F40): a control character in it is no `invalid_character`
    'global list control': (
        three_rows(),
        DECLARED,
        request_of({'data': 's'}, search={'value': '', 'multi_values': ['a\x00']}),
    ),
    # An entry of 256 code points on the global list: PRD-F40 reads no entry, so it is no `value_too_long`
    'global list long entry': (
        three_rows(),
        DECLARED,
        request_of({'data': 's'}, search={'value': '', 'multi_values': ['x' * 256]}),
    ),
    # An unfinished criterion (no condition) holding a control character: no condition reads it (PRD-F14, PRD-F15), and
    # it adds nothing (PRD-F30), so every row is answered
    'unfinished control': (
        three_rows(),
        DECLARED,
        request_of(
            {'data': 's'},
            searchBuilder={'logic': 'AND', 'criteria': [{'origData': 's', 'condition': '', 'value': ['a\x00']}]},
        ),
    ),
    # 600 entries on the global list: PRD-F40 reads none and PRD-F38's bounds do not count them, so the list is a step-4
    # refusal and the step-2 number mistake answers: `invalid_number` (PRD-E3)
    'global list 600': (
        three_rows(),
        DECLARED,
        request_of(
            {'data': 'n'},
            search={'value': '', 'multi_values': [f'v{i}' for i in range(600)]},
            searchBuilder=tree_of(('n', '=', ['abc'])),
        ),
    ),
    # ColumnControl's `contains` is a column search (PRD-F45): 17 words are `too_many_words` (PRD-F14)
    'columncontrol 17 words': (
        three_rows(),
        DECLARED,
        request_of(
            {
                'data': 's',
                'columnControl': {'search': {'value': ' '.join(['a'] * 17), 'logic': 'contains', 'type': 'text'}},
            }
        ),
    ),
    # The entry on `id` is skipped (unorderable on both sides), so `s` desc is the first applied entry, and its tie
    # (every `s` NULL) is broken by the key descending: row 3 comes first (PRD-O3, PRD-O8)
    'skipped first order': (
        [{'id': key, 's': None, 't': None, 'n': None, 'd': None, 'b': None} for key in (1, 2, 3)],
        (
            ('id', 'number', True, False),
            ('s', 'string', True, True),
            ('t', 'string', True, True),
            ('n', 'number', True, True),
            ('d', 'date', True, True),
            ('b', 'boolean', True, True),
        ),
        {
            'draw': 1,
            'start': 0,
            'length': 1,
            'search': {'value': ''},
            'columns': [
                {
                    'data': name,
                    'name': '',
                    'searchable': True,
                    'orderable': name == 's',
                    'columnControl': {},
                    'search': {'value': '', 'regex': False},
                }
                for name in ('id', 's')
            ],
            'order': [{'column': 0, 'dir': 'asc'}, {'column': 1, 'dir': 'desc'}],
        },
    ),
}


def rows_of(name: str, *cells: Any) -> list[dict[str, Any]]:
    """Rows 1, 2, ... holding ``cells`` in column ``name``, every other cell NULL."""
    return [{**dict.fromkeys(NAMES), 'id': key, name: cell} for key, cell in enumerate(cells, 1)]


EXAMPLES |= {
    # PRD-F11, PRD-F19: the word `\` is plain text, not a LIKE escape: only row 1 holds it
    'backslash': (rows_of('s', 'a\\b', '5%', 'ab'), DECLARED, request_of({'data': 's'}, search={'value': '\\'})),
    # PRD-F23: `%` alone over number text only: no number's text holds `%`, so no row (on a typed-search engine `n` is
    # skipped, PRD-F32, so no column is left: `invalid_filter`, PRD-F13)
    'percent over numbers': (rows_of('n', 7, 17, None), DECLARED,
                             request_of({'data': 'n'}, {'data': 's', 'searchable': False}, search={'value': '%'})),
    # PRD-F26 and PRD section 5: `null` on `s` holding ' ', '' and 'a': row 2 on SQL; rows 1 and 2 under PAD SPACE;
    # none where only a missing field is empty
    'pad space': (rows_of('s', ' ', '', 'a'), DECLARED,
                  request_of({'data': 's'}, searchBuilder=tree_of(('s', 'null', [])))),
    # PRD-F25, PRD-D23: 2^63 on `n` is the request's only mistake: `invalid_number`
    'beyond 64 bits': (three_rows(), DECLARED, request_of({'data': 'n'}, searchBuilder=tree_of(('n', '=', [BIG])))),
    # PRD-F23: `2024-03` as a column search on `d` finds the day written 2024-03-05 (PRD-F34: the span of March on a
    # typed-search engine): row 1
    'date text': (rows_of('d', date(2024, 3, 5), date(2024, 12, 31), None), DECLARED,
                  request_of({'data': 'd', 'search': {'value': '2024-03', 'regex': False}})),
    # PRD-F25 and PRD section 5, over the six values of decision 010 in rows 1 to 6: `= 12345678901234567.89` is row 1
    # where a decimal is compared exactly, rows 1 to 3 on SQLite and as doubles
    'decimal equal': (rows_of('m', *DECIMALS, None), DECLARED,
                      request_of({'data': 'm'}, searchBuilder=tree_of(('m', '=', ['12345678901234567.89'])))),
    # 0.30000000000000001 has more digits than the column: no row where it is exact, row 4 (0.30) as a float
    'decimal finer than the column': (
        rows_of('m', *DECIMALS, None), DECLARED,
        request_of({'data': 'm'}, searchBuilder=tree_of(('m', '=', ['0.30000000000000001'])))),
    # 12345678901234567 is whole and fits 64 bits: no row on SQL (SQLite compares it as an integer with
    # 12345678901234568); a double cannot tell it from the first three, rows 1 to 3
    'decimal whole neighbour': (rows_of('m', *DECIMALS, None), DECLARED,
                                request_of({'data': 'm'}, searchBuilder=tree_of(('m', '=', ['12345678901234567'])))),
    # PRD-F23: `.30` is in the fixed-scale text of 0.30 (row 4) and not in SQLite's `0.3`; on OpenSearch it is the
    # number 0.3 (PRD-F33): row 4
    'decimal spelling': (rows_of('m', *DECIMALS, None), DECLARED,
                         request_of({'data': 'm', 'search': {'value': '.30', 'regex': False}})),
    # PRD-O2, PRD-O3: rows 1 to 3 hold 12345678901234568.00, .90 and .89, ascending: 3, 2, 1 compared exactly; a tie
    # broken by the key, 1, 2, 3, on SQLite and as doubles
    'decimal order': (rows_of('m', DECIMALS[2], DECIMALS[1], DECIMALS[0]), DECLARED,
                      request_of({'data': 'm'}, order=[{'column': 0, 'dir': 'asc'}])),
    # PRD-F25, PRD-E3: `0.` and 39 digits is over SQL Server's 38 (step 3.5), so the last day (step 3) answers there
    # though read later: `invalid_date` everywhere. It pins the step (3.5 after 3), not the count of the zero before
    # the point, which the next example and the hand tests of SPEC-F25.12 pin
    'decimal 39 digits then the last day': (rows_of('m', *DECIMALS), DECLARED,
                                            request_of({'data': 'm'}, {'data': 'd'}, searchBuilder=tree_of(
                                                ('m', '<', ['0.' + '1' * 39]), ('d', '<', [LAST])))),
    # PRD-F25: `0.` and 38 ones is 38 digits, the zero before the point not counted, so it is answered on SQL Server
    # too: no row on every engine. An oracle that counted that zero would refuse it there
    'decimal 38 digits after a zero': (rows_of('m', *DECIMALS), DECLARED, request_of(
        {'data': 'm'}, searchBuilder=tree_of(('m', '=', ['0.' + '1' * 38])))),
    # Not finite (step 2) after the last day (step 3): `invalid_number` (PRD-E3)
    'decimal not finite': (rows_of('m', *DECIMALS), DECLARED,
                           request_of({'data': 'm'}, {'data': 'd'}, searchBuilder=tree_of(
                               ('d', '<', [LAST]), ('m', '=', ['1e400'])))),
    # More than 16,383 digits after the point is a limit of every column (step 3), read before the last day here:
    # `invalid_number`
    'decimal fraction limit': (rows_of('m', *DECIMALS), DECLARED,
                               request_of({'data': 'm'}, {'data': 'd'}, searchBuilder=tree_of(
                                   ('m', '=', ['1e-16384']), ('d', '<', [LAST])))),
    # PRD-F25, PRD-E3: a value that is not finite is one its column cannot read (step 2), also as a categorical filter
    # (PRD-F16) or a list entry (PRD-F20), which read a number as `=` does: `invalid_number`
    'decimal not finite in a categorical filter': (three_rows(), DECLARED, request_of(
        {'data': 'm', 'search': {'value': '^(1e400)$', 'regex': True}})),
    'decimal not finite in a list': (three_rows(), DECLARED, request_of(
        {'data': 'm', 'search': {'value': '', 'regex': False, 'multi_values': ['1e400']}})),
    # PRD-F20, PRD-F25: a list of two numbers matches each exactly where a decimal is compared exactly: row 1 only
    # (0.30000000000000001 is no value of the column); rows 1 and 4 on SQLite and as doubles, where 12345678901234567.89
    # and .90 are one number. Catches a number bound as text, which MariaDB compares as doubles in a list of two or more
    # (`docs/databases.md`, "A list of two or more text constants is compared with a `DECIMAL` column as doubles")
    'decimal list of two': (rows_of('m', DECIMALS[0], None, None, DECIMALS[1]), DECLARED, request_of(
        {'data': 'm', 'search': {'value': '', 'regex': False,
                                 'multi_values': ['12345678901234567.89', '0.30000000000000001']}})),
    # PRD-F25: the 16,383 limit is of the value, and zero has no fraction: `0e-16384` is answered, as zero, on every
    # engine
    'decimal zero past the fraction limit': (rows_of('m', *DECIMALS), DECLARED, request_of(
        {'data': 'm'}, searchBuilder=tree_of(('m', '=', ['0e-16384'])))),
}  # fmt: skip
if TINY_NUMBER_MODELLED:
    # PRD-F25: `1.0e-16383` is the number 1e-16383, within the 16,383 limit: no row on PostgreSQL (exact), the row
    # holding 0.00 on SQLite (as a float, PRD section 5), refused by SQL Server's 38 digits (step 3.5; SPEC-F25.16's
    # hand test pins the refusal, not its step); where it is not sent, see `DECIMAL_BOUNDS`
    EXAMPLES['decimal one at the fraction limit'] = (
        rows_of('m', *DECIMALS),
        DECLARED,
        request_of({'data': 'm'}, searchBuilder=tree_of(('m', '=', ['1.0e-16383']))),
    )
#: The examples whose answer on OpenSearch hangs on the backend, its query or the 64-bit range of a `long`
#: declaration, which OpenSearch's leg pins as well; the others are left out for the reasons in the module docstring
TYPED_EXAMPLES = [
    EXAMPLES[name]
    for name in ('neighbours', 'beyond 64 bits', 'm4', 'm5b', 'm6', 'm7', 'step3 big first', 'step3 last first',
                 'skipped first order', 'backslash', 'pad space', 'date text', 'decimal equal',
                 'decimal finer than the column', 'decimal whole neighbour', 'decimal spelling', 'decimal order',
                 'decimal list of two', 'decimal zero past the fraction limit')
]  # fmt: skip


def pinned[T](cases: Iterable[T]) -> Callable[[Callable[..., None]], Callable[..., None]]:
    """``@example`` once for each of ``cases``, in their order."""

    def decorate(test: Callable[..., None]) -> Callable[..., None]:
        for case in reversed(list(cases)):
            test = example(case)(test)
        return test

    return decorate


@server_examples(SERVER_EXAMPLES, on_server=bool(SQL_URLS[0]))
@given(cases())
@pinned(EXAMPLES.values())
def test_the_library_answers_as_the_reference_evaluator(
    leg: Leg, case: tuple[list[dict[str, Any]], Any, dict[str, Any]]
):
    """The rows, their order and both counts, or the error code, of `fetch_sync` and of `fetch` over the same loaded
    rows equal the evaluator's for the database the run is on. Catches any rule of PRD section 4.3 to 4.6 in scope
    where the library and the requirements part, in a combination no hand-written test lists, on either driver. The
    examples are `EXAMPLES`, each with its reason beside it."""
    rows, decl, body = case
    params = parse_request(body)
    expected = evaluate(decl, rows, params, engine=leg.reading)
    for name in events(rows, decl, body, expected, leg.reading):
        event(name)
    synced, fetched = library(leg, decl, rows, params)
    assert synced == expected, '`fetch_sync` and the reference evaluator answer differently'
    assert fetched == expected, '`fetch` and the reference evaluator answer differently'
