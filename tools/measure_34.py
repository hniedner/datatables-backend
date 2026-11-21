"""Throwaway measurements for issue #34, run by the `opensearch` job on each leg. Never merged.

It owns the index `dtb-measure-34` and prints Markdown tables (also into the job summary):

1. which query forms fold a letter outside ASCII without a mapping change;
2. what a case-insensitive `term` and `wildcard`, as the library sends them, cost by the length of the value;
3. the library's multiplied shapes (a list of 256 values, a global search of 16 words) and the 1,100-clause refusal;
4. regular expressions that fold by construction, by length, and what `search.allow_expensive_queries` refuses.

The index is small (about 2,100 documents on one shard): the run shows the query-side cost by length, not the cost on
a large index. `heap_used` between two calls is noisy, since a collection may run in between; the breaker counters
and the errors are the firm signal.
"""

import os
import statistics
import sys
import time
from typing import Any

from opensearchpy import OpenSearch
from opensearchpy.exceptions import TransportError
from opensearchpy.helpers import bulk
from tests.conftest import request

from datatables_backend.opensearch import IndexColumn
from datatables_backend.opensearch import SearchTable

INDEX = 'dtb-measure-34'
FIELD = 'v.keyword'
LENGTHS = (1, 2, 4, 8, 16, 32, 64, 128, 255)
ASCII = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
GREEK = ''.join(chr(point) for point in range(0x391, 0x3AA) if point not in (0x3A2, 0x3A3))  # capitals, no sigma
OTHER = 'ÄÖÜÉÈÊÀÂÎÔÛÇÑÅØÆ' + GREEK  # cased letters outside ASCII
ZHUKOV = ''.join(map(chr, (0x416, 0x423, 0x41A, 0x41E, 0x412)))  # Cyrillic capitals
SOFIA = ''.join(map(chr, (0x3A3, 0x39F, 0x3A6, 0x399, 0x391)))  # Greek capitals
ALPHABETS = {
    'ascii': ASCII,
    'non-ascii': OTHER,
    'mixed': ''.join(a + b for a, b in zip(ASCII, OTHER, strict=False)),
}
# (stored, searched for): the other case of the same letters
PAIRS = [
    ('APPLE', 'apple'),
    ('ÄPFEL', 'äpfel'),
    ('bär', 'BÄR'),
    (ZHUKOV, ZHUKOV.lower()),
    (SOFIA, SOFIA.lower()),
    ('STRAẞE', 'straße'),
    ('İZMİR', 'izmir'),
]

PUNCTUATION = ''.join(char for char in map(chr, range(32, 127)) if not char.isalnum())  # the space included
DECOYS = ('ÄXZ', 'Z', 'ÄZ', 'Ä', 'ÄÄZ', 'ÄXXZ')  # what an operator left unescaped between Ä and Z could match

client = OpenSearch(hosts=[os.environ['DTB_OPENSEARCH_URL']], timeout=120)
lines: list[str] = []


class Stop(Exception):
    pass


class Dice:
    """A fixed sequence of numbers, the same on every run and leg."""

    def __init__(self) -> None:
        self.state = 34

    def below(self, bound: int) -> int:
        self.state = (self.state * 6364136223846793005 + 1442695040888963407) % 2**64
        return (self.state >> 33) % bound

    def between(self, low: int, high: int) -> int:
        return low + self.below(high - low + 1)

    def letters(self, alphabet: str, count: int) -> str:
        return ''.join(alphabet[self.below(len(alphabet))] for _ in range(count))


def say(text: str = '') -> None:
    sys.stdout.write(text + '\n')
    sys.stdout.flush()
    lines.append(text)


def table(header: list[str], rows: list[list[Any]]) -> None:
    say('| ' + ' | '.join(header) + ' |')
    say('|' + '---|' * len(header))
    for row in rows:
        say('| ' + ' | '.join(str(cell).replace('|', '/') for cell in row) + ' |')
    say()


def target(alphabet: str, length: int) -> str:
    letters = ALPHABETS[alphabet]
    return (letters * (length // len(letters) + 1))[:length]


def reason(failure: TransportError) -> str:
    info: Any = failure.info
    try:
        cause = info['error']['root_cause'][0]
        return f'{failure.status_code} {cause["type"]}: {cause["reason"]}'[:400]
    except (KeyError, IndexError, TypeError):
        return f'{failure.status_code} {failure.error}: {info!r}'[:400]


def node() -> dict[str, Any]:
    stats: Any = client.nodes.stats(metric='jvm,breaker')
    return next(iter(stats['nodes'].values()))


def snapshot() -> dict[str, int]:
    stats = node()
    pools = stats['jvm']['mem']['pools']
    return {
        'heap': stats['jvm']['mem']['heap_used_in_bytes'],
        'old_peak': pools['old']['peak_used_in_bytes'],
        'gc': sum(c['collection_count'] for c in stats['jvm']['gc']['collectors'].values()),
        'request': stats['breakers']['request']['estimated_size_in_bytes'],
        'tripped': sum(b['tripped'] for b in stats['breakers'].values()),
    }


def megabytes(count: int) -> str:
    return f'{count / 1048576:.1f}'


def run(query: dict[str, Any], runs: int = 5) -> list[Any]:
    """took min and median (ms, the server's), wall min (ms), hits, heap before and after (MB), the old pool's peak
    growth (MB), collections in between, the request breaker's estimate after (bytes), breakers tripped, the error."""
    before = snapshot()
    tooks: list[int] = []
    walls: list[float] = []
    hits: Any = ''
    error = ''
    for _ in range(runs):
        started = time.perf_counter()
        try:
            answer: Any = client.search(
                index=INDEX,
                body={'query': query, 'size': 0, 'track_total_hits': True},
                params={'request_cache': 'false'},
            )
        except TransportError as failure:
            error = reason(failure)
            break
        walls.append((time.perf_counter() - started) * 1000)
        tooks.append(answer['took'])
        hits = answer['hits']['total']['value']
    after = snapshot()
    return [
        min(tooks) if tooks else '',
        statistics.median(tooks) if tooks else '',
        f'{min(walls):.1f}' if walls else '',
        hits,
        megabytes(before['heap']),
        megabytes(after['heap']),
        megabytes(after['old_peak'] - before['old_peak']),
        after['gc'] - before['gc'],
        after['request'],
        after['tripped'] - before['tripped'],
        error,
    ]


MEASURES = ['took min', 'took med', 'wall min', 'hits', 'heap before', 'heap after', 'old peak +', 'gc', 'req est']
MEASURES += ['tripped', 'error']


def term(value: str, *, fold: bool = True) -> dict[str, Any]:
    return {'term': {FIELD: {'value': value, 'case_insensitive': True}}} if fold else {'term': {FIELD: value}}


def wildcard(pattern: str, *, fold: bool = True) -> dict[str, Any]:
    return {'wildcard': {FIELD: {'value': pattern, 'case_insensitive': fold}}}


def regexp(pattern: str, *, fold: bool) -> dict[str, Any]:
    return {'regexp': {FIELD: {'value': pattern, 'case_insensitive': fold}}}


def ids(query: dict[str, Any]) -> str:
    try:
        answer: Any = client.search(index=INDEX, body={'query': query, 'size': 20, '_source': False})
    except TransportError as failure:
        return reason(failure)
    return ' '.join(sorted(hit['_id'] for hit in answer['hits']['hits'])) or '-'


def fill() -> int:
    if client.indices.exists(index=INDEX):
        client.indices.delete(index=INDEX)
    mapping = {'properties': {'v': {'type': 'text', 'fields': {'keyword': {'type': 'keyword'}}}}}
    client.indices.create(
        index=INDEX, body={'mappings': mapping, 'settings': {'number_of_replicas': 0, 'number_of_shards': 1}}
    )
    documents: dict[str, str] = {f'pair-{stored}': stored for stored, _ in PAIRS}
    dice = Dice()
    for name, letters in ALPHABETS.items():
        for length in LENGTHS:
            documents[f't-{name}-{length}'] = target(name, length)
        longest = target(name, 255)
        both = letters + letters.lower()
        for number in range(200):  # a long prefix in common with the target, then something else, in either case
            shared = dice.between(1, 254)
            value = longest[:shared] + dice.letters(both, dice.between(1, 255 - shared))
            documents[f'p-{name}-{number}'] = value if number % 2 else value.lower()
    for char in PUNCTUATION:
        documents[f'esc-{ord(char)}'] = f'Ä{char}Z'
    documents['esc-all'] = f'Ä{PUNCTUATION}Z'
    for decoy in DECOYS:
        documents[f'decoy-{decoy}'] = decoy
    everything = ASCII + ASCII.lower() + OTHER + OTHER.lower()
    for number in range(1400):
        documents[f'f-{number}'] = dice.letters(everything, dice.between(1, 255))
    bulk(client, [{'_index': INDEX, '_id': key, 'v': value} for key, value in documents.items()])
    client.indices.refresh(index=INDEX)
    counted: Any = client.count(index=INDEX)
    return counted['count']


def folding() -> None:
    say('## 1. Which form folds a letter outside ASCII (hits are document ids, `pair-<stored>`)')
    say()
    rows: list[list[Any]] = []
    for stored, wanted in PAIRS:
        part = wanted[:3]
        for label, value, piece in (('other case', wanted, part), ('stored case', stored, stored[:3])):
            rows.append([
                stored, label, value,
                ids(term(value)),
                ids(wildcard(f'*{piece}*')),
                ids(regexp(value, fold=True)),
                ids(regexp(f'.*{piece}.*', fold=True)),
                ids({'match': {'v': {'query': value}}}),
            ])  # fmt: skip
    table(
        ['stored', 'searched in', 'value', 'term ci', 'wildcard ci *abc*', 'regexp ci whole', 'regexp ci .*abc.*',
         'match (analysed)'],
        rows,
    )  # fmt: skip


def cost() -> None:
    say('## 2. Cost by length: the value in lower case against the stored upper case')
    say()
    rows: list[list[Any]] = []
    try:
        for name in ALPHABETS:
            for length in LENGTHS:
                value = target(name, length).lower()
                shapes = {
                    'term': lambda fold, v=value: term(v, fold=fold),
                    'wildcard *v*': lambda fold, v=value: wildcard(f'*{v}*', fold=fold),
                    'wildcard v*': lambda fold, v=value: wildcard(f'{v}*', fold=fold),
                    'wildcard *v': lambda fold, v=value: wildcard(f'*{v}', fold=fold),
                }
                for shape, build in shapes.items():
                    for fold in (True, False):
                        row = run(build(fold))
                        rows.append([shape, name, length, 'ci' if fold else 'plain', *row])
                        if row[-1] or row[-2]:
                            raise Stop
    except Stop:
        say('**STOPPED: an error or a tripped breaker, last row below.**')
        say()
        raise
    finally:
        table(['shape', 'letters', 'length', 'case', *MEASURES], rows)


def multiplied() -> None:
    say("## 3. The library's multiplied shapes")
    say()
    longest = target('ascii', 255)
    values = [longest.lower()]
    for number in range(255):  # 255 more values of 255 letters, each differing from the target near its end
        suffix = ''.join(ASCII[(number // 26**place) % 26] for place in range(3)).lower()
        values.append(longest[:250].lower() + suffix + 'zz')
    rows = [
        ['bool.should of 256 ci terms, 255 letters each', *run({'bool': {'should': [term(v) for v in values]}})],
        ['terms of the same 256 values, plain', *run({'terms': {FIELD: values}})],
    ]
    table(['shape', *MEASURES], rows)

    columns = [IndexColumn('id', field='v.keyword', text='keyword')]
    columns += [IndexColumn(name, field='v') for name in ('a', 'b', 'c')]
    searched = SearchTable(INDEX, columns)
    words = ' '.join(target('ascii', 255)[start : start + 15].lower() for start in range(0, 240, 15))
    say(f'A global search of {len(words.split())} words, {len(words)} code points, over 3 text+keyword columns and one')
    say('keyword column, through `fetch_sync` (wall clock, ms, 5 runs):')
    before = snapshot()
    walls: list[float] = []
    outcome = ''
    for _ in range(5):
        started = time.perf_counter()
        try:
            response = searched.fetch_sync(request('id', 'a', 'b', 'c', search={'value': words}), client)
        except TransportError as failure:
            outcome = reason(failure)
            break
        walls.append((time.perf_counter() - started) * 1000)
        outcome = f'recordsFiltered {response.recordsFiltered}, error {response.error!r}'
    after = snapshot()
    say(f'wall min {min(walls, default=0):.1f}, max {max(walls, default=0):.1f}; {outcome}; heap before '
        f'{megabytes(before["heap"])} MB, after {megabytes(after["heap"])} MB; breakers tripped '
        f'{after["tripped"] - before["tripped"]}')  # fmt: skip
    say()

    for count in (1024, 1025, 1100):
        many = {'bool': {'should': [term(f'n{number}') for number in range(count)]}}
        say(f'- a `bool.should` of {count} case-insensitive terms: {run(many, runs=1)[-1] or "answered"}')
    say()


def classes(value: str, *, every: bool) -> str:
    """A regular expression for ``value`` in any case: a class of both cases for each letter, or only for the
    letters outside ASCII (the others are left to `case_insensitive`)."""
    return ''.join(f'[{c.lower()}{c.upper()}]' if every or not c.isascii() else c for c in value)


def by_construction() -> None:
    say('## 4. Regular expressions, by length (value in lower case, stored in upper case)')
    say()
    rows: list[list[Any]] = []
    for name in ALPHABETS:
        for length in LENGTHS:
            value = target(name, length).lower()
            forms = {
                'regexp ci, the value': (value, True),
                'classes for every letter, plain': (classes(value, every=True), False),
                'classes outside ASCII, ci': (classes(value, every=False), True),
            }
            for form, (pattern, fold) in forms.items():
                for anchor, text in (('whole', pattern), ('substring', f'.*{pattern}.*')):
                    rows.append([form, anchor, name, length, len(text), *run(regexp(text, fold=fold))])
    table(['form', 'match', 'letters', 'length', 'pattern chars', *MEASURES], rows)


def expensive() -> None:
    say('## 5. With `search.allow_expensive_queries: false`')
    say()
    client.cluster.put_settings(body={'transient': {'search.allow_expensive_queries': False}})
    try:
        rows = [
            ['term ci', ids(term('apple'))],
            ['term plain', ids(term('APPLE', fold=False))],
            ['wildcard ci *ppl*', ids(wildcard('*ppl*'))],
            ['wildcard ci app*', ids(wildcard('app*'))],
            ['regexp ci apple', ids(regexp('apple', fold=True))],
            ['regexp plain .*PPL.*', ids(regexp('.*PPL.*', fold=False))],
            ['prefix app ci', ids({'prefix': {FIELD: {'value': 'app', 'case_insensitive': True}}})],
        ]
    finally:
        client.cluster.put_settings(body={'transient': {'search.allow_expensive_queries': None}})
    table(['query', 'answer'], rows)


def escaped(value: str) -> str:
    """A backslash before every ASCII character that is not a letter or a digit."""
    return ''.join(char if char.isalnum() or not char.isascii() else f'\\{char}' for char in value)


def escaping() -> None:
    say('## 6. A backslash before every ASCII character that is not a letter or a digit (regexp ci, no flags)')
    say()
    say('Searched in the stored case, so every leg can find it. Stored: `esc-<code>` holds Ä, the character, Z;')
    say(f'`esc-all` holds Ä, all {len(PUNCTUATION)} characters, Z; decoys: {", ".join(DECOYS)}.')
    say()
    rows: list[list[Any]] = []
    for char in PUNCTUATION:
        value = f'Ä{char}Z'
        rows.append([
            ord(char), f'`{char}`',
            ids(regexp(escaped(value), fold=True)),
            ids(regexp(f'.*{escaped(value)}.*', fold=True)),
            ids(regexp(value, fold=True)),
        ])  # fmt: skip
    table(['code', 'character', 'escaped, whole', 'escaped, substring', 'left unescaped, whole'], rows)
    everything = f'Ä{PUNCTUATION}Z'
    say(f'- all at once, escaped, whole: {ids(regexp(escaped(everything), fold=True))}')
    say(f'- all at once, escaped, substring: {ids(regexp(f".*{escaped(everything)}.*", fold=True))}')
    say(f'- `Ä.*` escaped, substring: {ids(regexp(f".*{escaped('Ä.*')}.*", fold=True))}')
    say(f'- pattern length of all at once, whole: {len(escaped(everything))}')
    say()


def regexp_cost() -> None:
    say('## 7. The regexp form at the caps (cased letters outside ASCII, lower case against stored upper case)')
    say()
    longest = target('non-ascii', 255).lower()
    small = OTHER.lower()
    values = [longest]
    for number in range(255):
        suffix = ''.join(small[(number // len(small) ** place) % len(small)] for place in range(3))
        values.append(longest[:250] + suffix + small[:2])
    words = [longest[start : start + 15] for start in range(0, 240, 15)]

    def search(like: Any) -> dict[str, Any]:
        """16 words over 3 text+keyword columns and a keyword column, as the global search builds it."""
        clauses: list[dict[str, Any]] = []
        for word in words:
            analysed = [{'match': {'v': {'query': word}}}] * 3
            clauses.append({'bool': {'should': [*analysed, *([like(word)] * 4)], 'minimum_should_match': 1}})
        return {'bool': {'must': clauses}}

    rows = [
        ['256 regexp ci, whole value', *run({'bool': {'should': [regexp(v, fold=True) for v in values]}})],
        ['256 term ci, the same values (as sent today)', *run({'bool': {'should': [term(v) for v in values]}})],
        ['global search, 16 words, regexp ci .*w.*', *run(search(lambda w: regexp(f'.*{w}.*', fold=True)))],
        ['global search, 16 words, wildcard ci *w* (as sent today)', *run(search(lambda w: wildcard(f'*{w}*')))],
    ]
    table(['shape', *MEASURES], rows)
    say(f'The 16 words hold {len(" ".join(words))} code points.')
    say()


def main() -> None:
    info: Any = client.info()
    version = info['version']
    documents = fill()
    try:
        heap = megabytes(node()['jvm']['mem']['heap_max_in_bytes'])
        say(f'# Issue #34 measurements on OpenSearch {version["number"]} (Lucene {version["lucene_version"]})')
        say()
        say(f'Heap max {heap} MB (the job sets -Xms512m -Xmx512m). {documents} documents, one shard: a small index, so')
        say('this is the query-side cost by length, not the cost on a large index. Times are ms.')
        say()
        settings: Any = client.indices.get_settings(
            index=INDEX, params={'include_defaults': 'true', 'flat_settings': 'true'}
        )
        defaults = next(iter(settings.values()))['defaults']
        say(f'`index.max_regex_length` default: {defaults.get("index.max_regex_length")}')
        say()
        folding()
        try:
            cost()
            multiplied()
        except Stop:
            pass
        by_construction()
        expensive()
        escaping()
        regexp_cost()
    finally:
        client.indices.delete(index=INDEX)
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a', encoding='utf-8') as out:
                out.write('\n'.join(lines) + '\n')


main()
