"""Measurements for issue 14 against the cluster DTB_OPENSEARCH_URL names. Throwaway; prints one line per query.

1. Whether a backslash before a fullwidth mark makes it that character in a `wildcard` query, on a plain keyword
   field and on one whose normalizer folds it to the ASCII mark.
2. What a number field answers to a whole number beyond 64 bits, to the float nearest to it and to 400 digits.
"""

import os
import sys
from typing import Any

from opensearchpy import OpenSearch
from opensearchpy.exceptions import TransportError

INDEX = 'dtb-measure-14'
STAR, MARK, SLASH, DOUBLE, ARROW = chr(0xFF0A), chr(0xFF1F), chr(0xFF3C), chr(0x2047), chr(0x2192)
STORED = [
    'a*b', f'a{STAR}b', 'axyb', 'a?b', f'a{MARK}b', 'axb', 'a\\b', f'a{SLASH}b', f'a{DOUBLE}b', 'a??b', 'a.b',
    'a-b', f'a{ARROW}b', 'a b',
]  # fmt: skip
NUMBER_FIELDS = {'n_long': 'long', 'n_int': 'integer', 'n_double': 'double', 'n_ulong': 'unsigned_long'}
MAPPING: dict[str, Any] = {
    'settings': {
        'number_of_replicas': 0,
        'analysis': {'normalizer': {'folded': {'type': 'custom', 'filter': ['asciifolding', 'lowercase']}}},
    },
    'mappings': {
        'properties': {
            'plain': {'type': 'keyword'},
            'folded': {'type': 'keyword', 'normalizer': 'folded'},
            **{field: {'type': kind} for field, kind in NUMBER_FIELDS.items()},
        }
    },
}
BIG = 2**64
NUMBERS: list[tuple[str, Any]] = [
    ('7', 7),
    ('2**63-1', 2**63 - 1),
    ('2**63', 2**63),
    ('2**64-1', 2**64 - 1),
    ('2**64', BIG),
    ('float(2**64)', float(BIG)),
    ('-2**64', -BIG),
    ('float(-2**64)', float(-BIG)),
    ('1e308', 1e308),
    ('400 digits', int('9' * 400)),
    ('7.5', 7.5),
]


def say(line: str) -> None:
    sys.stdout.write(f'{line}\n')


def shown(text: str) -> str:
    """``text`` with every character outside ASCII as its code point, so a log line cannot be misread."""
    return ''.join(char if char.isascii() else f'<U+{ord(char):04X}>' for char in text)


def ask(client: OpenSearch, label: str, query: dict[str, Any]) -> None:
    try:
        answer = client.search(index=INDEX, body={'query': query, 'size': 50, 'sort': ['_doc']})
    except TransportError as error:
        reason = str(error.info)[:300]
        say(f'{label} -> ERROR {error.status_code} {shown(reason)}')
        return
    found = sorted(hit['_id'] for hit in answer['hits']['hits'])
    say(f'{label} -> {found}')


def main() -> None:
    client = OpenSearch(hosts=[os.environ['DTB_OPENSEARCH_URL']])
    say(f'server {client.info()["version"]["number"]}')
    if client.indices.exists(index=INDEX):
        client.indices.delete(index=INDEX)
    client.indices.create(index=INDEX, body=MAPPING)
    for number, value in enumerate(STORED):
        client.index(index=INDEX, id=f's{number}', body={'plain': value, 'folded': value})
        say(f'stored s{number} = {shown(value)}')
    client.index(index=INDEX, id='seven', body=dict.fromkeys(NUMBER_FIELDS, 7))
    client.index(index=INDEX, id='edge', body={'n_long': 2**63 - 1, 'n_ulong': 2**64 - 1, 'n_double': float(BIG)})
    client.indices.refresh(index=INDEX)

    say('== 1. wildcard and regexp patterns, each on the plain and on the folded field')
    wildcards = [
        'a*b', 'a\\*b', f'a{STAR}b', f'a\\{STAR}b', 'a?b', 'a\\?b', f'a{MARK}b', f'a\\{MARK}b', 'a\\\\b',
        f'a{SLASH}b', f'a\\{SLASH}b', f'a{DOUBLE}b', f'a\\{DOUBLE}b', 'a\\.b', 'a\\-b', f'a\\{ARROW}b', 'a\\ b',
        '\\a\\x\\b',
    ]  # fmt: skip
    for field in ('plain', 'folded'):
        for pattern in wildcards:
            query = {'wildcard': {field: {'value': f'*{pattern}*', 'case_insensitive': True}}}
            ask(client, f'wildcard {field:6} *{shown(pattern)}*', query)
        for pattern in (f'a\\{STAR}b', f'a\\{MARK}b', f'a\\{SLASH}b', f'a\\{DOUBLE}b'):
            query = {'regexp': {field: {'value': f'.*{pattern}.*', 'case_insensitive': True}}}
            ask(client, f'regexp   {field:6} .*{shown(pattern)}.*', query)

    say('== 2. numbers')
    for field, kind in NUMBER_FIELDS.items():
        for label, number in NUMBERS:
            ask(client, f'term  {kind:13} {label}', {'term': {field: number}})
            ask(client, f'range {kind:13} gte {label}', {'range': {field: {'gte': number}}})
            ask(client, f'range {kind:13} lte {label}', {'range': {field: {'lte': number}}})
        ask(client, f'terms {kind:13} [7, 2**64]', {'terms': {field: [7, BIG]}})
        ask(client, f'terms {kind:13} [7, float(2**64)]', {'terms': {field: [7, float(BIG)]}})
    client.indices.delete(index=INDEX)
    client.close()


if __name__ == '__main__':
    main()
