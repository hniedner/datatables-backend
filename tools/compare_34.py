"""Throwaway for issue #34: the answers of `fetch_sync` over a set of values, one line each, to compare the source
of `main` with the branch's (the job runs it once with each on PYTHONPATH and prints the diff). Never merged."""

import os
import sys
import time
from typing import Any

from opensearchpy import OpenSearch
from opensearchpy.exceptions import TransportError
from tests.conftest import criterion
from tests.conftest import request
from tests.test_opensearch_live import DOCUMENTS
from tests.test_opensearch_live import DOTTED
from tests.test_opensearch_live import LONGEST
from tests.test_opensearch_live import LONGEST_PATTERN
from tests.test_opensearch_live import MAPPING
from tests.test_opensearch_live import OPERATORS
from tests.test_opensearch_live import PUNCTUATION
from tests.test_opensearch_live import REPEATED_LETTER

import datatables_backend
from datatables_backend import Column
from datatables_backend.opensearch import IndexColumn
from datatables_backend.opensearch import SearchTable

INDEX = 'dtb-compare-34'
EXTRA = {'id': 'x9', 'title': 'Extra', 'body': 'extra', 'label': ['日本語', 'STRAẞE', 'İZMİR', 'ǅungla', 'bär', 'Bär']}
KELVIN = chr(0x212A) + 'elvin'  # the Kelvin sign, which Unicode folds to k
LONG_S = chr(0x17F) + 'un'  # the long s, which Unicode folds to s
FOLDED_TO_ASCII = {'id': 'y8', 'title': 'Folded', 'body': 'folded', 'label': [KELVIN, LONG_S]}
# Cased characters that are no letters to Python: three circled letters and the combining iota subscript.
CIRCLED = {'circled d': 0x24D3, 'circled S': 0x24C8, 'circled a': 0x24D0, 'iota subscript': 0x345}
CIRCLED_STORED = {
    'id': 'z7', 'title': 'Circled', 'body': 'circled',
    'label': [*(chr(code) for code in CIRCLED.values()), *(f'Ä{chr(code)}b' for code in CIRCLED.values())],
}  # fmt: skip
VALUES = [
    'kelvin', 'KELVIN', KELVIN, 'sun', 'SUN', LONG_S,  # letters outside ASCII whose other case is an ASCII letter
    'Ä' * 256, 'ä' * 256,  # one letter more than the longest value sent as a regexp
    'apple', 'APPLE', 'pfel', 'PFEL', 'abc', LONGEST, LONGEST.lower(),  # ASCII
    'äpfel', 'ÄPFEL', 'äpf', 'ÄPF', 'bär', 'BÄR', 'Bär', 'straße', 'STRAẞE', 'izmir', 'İZMİR', 'i̇zmi̇r',  # cased
    'ǅungla', 'ǆungla', 'Ǆungla',  # a titlecase letter and its two other cases
    '日本語', '日本', '本',  # no case
    'äXz', 'ÄxZ', 'äz', 'ÄZ', 'ä', 'Ä', 'z',  # mixed, and the decoys
    REPEATED_LETTER, REPEATED_LETTER.lower(), DOTTED, DOTTED.lower(), LONGEST_PATTERN, LONGEST_PATTERN.lower(),
    *(chr(code) for code in CIRCLED.values()), *(f'Ä{chr(code)}b' for code in CIRCLED.values()),
    f'Ä{PUNCTUATION}Z', f'ä{PUNCTUATION}z', f'Ä{PUNCTUATION}', 'Ä.*', 'ä.*', '.*', 'a.*',
    *(f'Ä{char}Z' for char in OPERATORS), *(f'ä{char}z' for char in OPERATORS), *(f'a{char}z' for char in OPERATORS),
]  # fmt: skip
CONDITIONS = ('=', '!=', 'contains', '!contains', 'starts', '!starts', 'ends', '!ends')
NAMES = ('id', 'label')
TABLES = {
    'text+keyword': SearchTable(INDEX, [IndexColumn('id', text='keyword'), IndexColumn('label')]),
    'keyword': SearchTable(
        INDEX, [IndexColumn('id', text='keyword'), IndexColumn('label', field='label.keyword', text='keyword')]
    ),
    'unfolded': SearchTable(INDEX, [IndexColumn('id', text='keyword'), IndexColumn('label', fold_case=False)]),
    'hooked': SearchTable(INDEX, [IndexColumn('id', text='keyword'), Column('label', search_value=str.upper)]),
}


def answer(client: OpenSearch, table: SearchTable, body: dict[str, Any]) -> str:
    try:
        response = table.fetch_sync(request(*NAMES, **body), client)
    except TransportError as failure:
        return f'RAISED {type(failure).__name__} {failure.status_code}'
    if response.error_code:
        return f'refused {response.error_code}'
    return ' '.join(sorted(str(row['id']) for row in response.data)) or '-'


def regex_length(client: OpenSearch) -> None:
    """What `index.max_regex_length` counts: an index that allows 513, and patterns around it."""
    name = 'dtb-regex-34'
    if client.indices.exists(index=name):
        client.indices.delete(index=name)
    settings = {'number_of_replicas': 0, 'index.max_regex_length': 513}
    client.indices.create(index=name, body={'mappings': MAPPING, 'settings': settings})
    dots = '\\.' * 254
    patterns = {
        'a letter of the BMP, 513 characters': f'.*Ä{dots}.*',
        'a letter of the BMP and one more dot, 514 characters': f'.*Ä{dots}..*',
        'a letter outside the BMP, 513 code points, 514 UTF-16 units': f'.*{chr(0x10400)}{dots}.*',
    }
    try:
        for label, pattern in patterns.items():
            query = {'regexp': {'label.keyword': {'value': pattern, 'case_insensitive': True}}}
            try:
                client.search(index=name, body={'query': query})
                outcome = 'answered'
            except TransportError as failure:
                info: Any = failure.info
                outcome = str(info['error']['root_cause'][0]['reason'])[:160]
            sys.stderr.write(f'RAW max_regex_length 513, {label} (len {len(pattern)}) -> {outcome}\n')
    finally:
        client.indices.delete(index=name)


FULLWIDTH = {'(': 0xFF08, '*': 0xFF0A, '~': 0xFF5E, '?': 0xFF1F, '[': 0xFF3B, '.': 0xFF0E, '|': 0xFF5C}
FULLWIDTH |= {'(a) as one character': 0x249C, '(1) as one character': 0x2474}
FULLWIDTH |= CIRCLED


def normalizer(client: OpenSearch) -> None:
    """A keyword field with an `asciifolding` normalizer, which turns a fullwidth punctuation mark into its ASCII
    one: what the library's queries find there, next to the same values on a plain keyword field."""
    name = 'dtb-norm-34'
    if client.indices.exists(index=name):
        client.indices.delete(index=name)
    analysis = {'normalizer': {'folded': {'type': 'custom', 'filter': ['asciifolding']}}}
    fields = {'id': {'type': 'keyword'}, 'norm': {'type': 'keyword', 'normalizer': 'folded'}}
    fields['plain'] = {'type': 'keyword'}
    body = {'settings': {'number_of_replicas': 0, 'analysis': analysis}, 'mappings': {'properties': fields}}
    client.indices.create(index=name, body=body)
    stored = {f'w{code:04X}': f'Ä{chr(code)}b' for code in FULLWIDTH.values()}
    stored |= {'dxb': 'Äxb', 'db': 'Äb', 'ddb': 'ÄÄb', 'd': 'Ä', 'b': 'b', 'dxxb': 'Äxxb', 'dab': 'Äab', 'd1b': 'Ä1b'}
    stored |= {'dspb': 'Ä b', 'd_b': 'Ä_b', 'ddd': 'Ädb', 'dsb': 'ÄSb'}  # what `\d`, `\s`, `\w`, `\S` would match
    for key, value in stored.items():
        client.index(index=name, id=key, body={'id': key, 'norm': value, 'plain': value})
    client.indices.refresh(index=name)
    try:
        for field in ('norm', 'plain'):
            table = SearchTable(name, [IndexColumn('id', text='keyword'), IndexColumn(field, text='keyword')])
            for ascii_mark, code in FULLWIDTH.items():
                value = f'Ä{chr(code)}b'
                for condition in ('=', 'contains'):
                    try:
                        response = table.fetch_sync(request('id', field, **criterion(field, condition, value)), client)
                        outcome = ' '.join(sorted(str(row['id']) for row in response.data)) or '-'
                    except TransportError as failure:
                        info: Any = failure.info
                        outcome = f'RAISED {str(info["error"]["root_cause"][0]["reason"])[:110]}'
                    sys.stderr.write(f'NORM library {field} {condition} fullwidth {ascii_mark} -> {outcome}\n')
                for label, pattern in (('bare', value), ('with a backslash', f'Ä\\{chr(code)}b')):
                    query = {'regexp': {field: {'value': pattern, 'case_insensitive': True}}}
                    try:
                        hits: Any = client.search(index=name, body={'query': query, '_source': False})
                        outcome = ' '.join(sorted(hit['_id'] for hit in hits['hits']['hits'])) or '-'
                    except TransportError as failure:
                        info = failure.info
                        outcome = f'RAISED {str(info["error"]["root_cause"][0]["reason"])[:110]}'
                    sys.stderr.write(f'NORM raw regexp {field} fullwidth {ascii_mark} {label} -> {outcome}\n')
    finally:
        client.indices.delete(index=name)


def long_values(client: OpenSearch) -> None:
    """Values far longer than a search text may be, last, since one can exhaust the heap: what the server answers,
    with its reason, and the heap around it."""

    def heap() -> str:
        stats: Any = client.nodes.stats(metric='jvm,breaker')
        node = next(iter(stats['nodes'].values()))
        used = node['jvm']['mem']['heap_used_in_bytes'] / 1048576
        return f'{used:.0f} MB, parent breaker tripped {node["breakers"]["parent"]["tripped"]}'

    def send(query: dict[str, Any]) -> str:
        try:
            done: Any = client.search(index=INDEX, body={'query': query, 'size': 0})
        except TransportError as failure:
            info: Any = failure.info
            try:
                cause = info['error']['root_cause'][0]
                return f'RAISED {failure.status_code} {cause["type"]}: {str(cause["reason"])[:240]}'
            except (KeyError, IndexError, TypeError):
                return f'RAISED {failure.status_code} {info!r}'[:320]
        return f'answered, took {done["took"]} ms'

    field = 'label.keyword'
    forms = {
        'wildcard ci *v*': lambda v: {'wildcard': {field: {'value': f'*{v}*', 'case_insensitive': True}}},
        'regexp ci .*v.*': lambda v: {'regexp': {field: {'value': f'.*{v}.*', 'case_insensitive': True}}},
        'regexp ci v': lambda v: {'regexp': {field: {'value': v, 'case_insensitive': True}}},
    }
    for form, build in forms.items():
        for letter in ('b', 'ä', 'abcdefghijklmnopqrstuvwxyz', chr(0x3B8)):  # theta has three case forms
            for length in (255, 300, 400, 500):
                time.sleep(2)  # a rested node: whatever the query before left can be collected
                before = heap()
                outcome = send(build((letter * length)[:length]))
                sys.stderr.write(f'LONG {form} | {length} x {letter} -> {outcome} [heap {before} -> {heap()}]\n')


def main() -> None:
    sys.stderr.write(f'source: {datatables_backend.__file__}\n')
    client = OpenSearch(hosts=[os.environ['DTB_OPENSEARCH_URL']], timeout=120)
    normalizer(client)
    if client.indices.exists(index=INDEX):
        client.indices.delete(index=INDEX)
    client.indices.create(index=INDEX, body={'mappings': MAPPING, 'settings': {'number_of_replicas': 0}})
    for number, document in enumerate([*DOCUMENTS, EXTRA, FOLDED_TO_ASCII, CIRCLED_STORED], 1):
        client.index(index=INDEX, id=str(number), body=document)
    client.indices.refresh(index=INDEX)
    try:
        for value in ('kelvin', 'KELVIN', KELVIN, 'sun', 'SUN', LONG_S):  # what the server itself folds, sent raw
            forms = {
                'term ci': {'term': {'label.keyword': {'value': value, 'case_insensitive': True}}},
                'regexp ci': {'regexp': {'label.keyword': {'value': value, 'case_insensitive': True}}},
            }
            for form, query in forms.items():
                hits: Any = client.search(index=INDEX, body={'query': query, '_source': ['id']})
                found = sorted(hit['_source']['id'] for hit in hits['hits']['hits'])
                sys.stderr.write(f'RAW {form} {value!a} -> {" ".join(found) or "-"}\n')
        for name, table in TABLES.items():
            for number, value in enumerate(VALUES):
                shown = value if len(value) < 40 else f'{value[:12]}...({len(value)})'
                bodies: dict[str, dict[str, Any]] = {c: criterion('label', c, value) for c in CONDITIONS}
                bodies['global'] = {'search': {'value': value}}
                bodies['column'] = {'column_search': {'label': {'value': value}}}
                bodies['list'] = {'column_search': {'label': {'multi_values': [value, 'ÄPFEL']}}}
                for kind, body in bodies.items():
                    sys.stdout.write(f'{name} | {kind} | {number} {shown} -> {answer(client, table, body)}\n')
        if 'main-src' not in str(datatables_backend.__file__):  # raw queries: once is enough
            long_values(client)
    finally:
        client.indices.delete(index=INDEX)


main()
