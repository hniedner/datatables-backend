"""Measurements for issue 47 against the cluster DTB_OPENSEARCH_URL names. Throwaway; prints one line per query.

What each OpenSearch number field type answers to a value at and just past the edge of its range, in `term`, `terms`
and `range` (gte and lte) queries: whether it answers (with the ids found) or refuses with a 400 (with the reason).
"""

import os
import sys
from typing import Any

from opensearchpy import OpenSearch
from opensearchpy.exceptions import TransportError

INDEX = 'dtb-measure-47'
FIELDS = {
    'byte': {'type': 'byte'},
    'short': {'type': 'short'},
    'integer': {'type': 'integer'},
    'long': {'type': 'long'},
    'unsigned_long': {'type': 'unsigned_long'},
    'float': {'type': 'float'},
    'half_float': {'type': 'half_float'},
    'scaled_float': {'type': 'scaled_float', 'scaling_factor': 100},
    'double': {'type': 'double'},
}
BITS = {'byte': 8, 'short': 16, 'integer': 32, 'long': 64}
FLOAT_MAX = 3.4028234663852886e38
HALF_MAX = 65504.0
SCALED_MAX = (2**63 - 1) / 100  # the largest value times the factor that still fits a long
DOUBLE_MAX = 1.7976931348623157e308
DIGITS_255 = int('9' * 255)


def bounds(kind: str) -> tuple[Any, Any]:
    """The documented low and high of ``kind``, as numbers a document can hold."""
    if kind == 'unsigned_long':
        return 0, 2**64 - 1
    if kind in BITS:
        return -(2 ** (BITS[kind] - 1)), 2 ** (BITS[kind] - 1) - 1
    return {
        'float': (-FLOAT_MAX, FLOAT_MAX),
        'half_float': (-HALF_MAX, HALF_MAX),
        'scaled_float': (-SCALED_MAX, SCALED_MAX),
        'double': (-DOUBLE_MAX, DOUBLE_MAX),
    }[kind]


def numbers(kind: str) -> list[tuple[str, Any]]:
    low, high = bounds(kind)
    out: list[tuple[str, Any]] = [('7', 7), ('7.5', 7.5), ('0', 0), ('-1', -1)]
    if kind in BITS or kind == 'unsigned_long':
        out += [
            ('high', high),
            ('high+1', high + 1),
            ('high+0.5', high + 0.5),
            ('high-0.5', high - 0.5),
            ('low', low),
            ('low-1', low - 1),
            ('low-0.5', low - 0.5),
            ('high*2', high * 2),
            ('1e308', 1e308),
            ('10**308', 10**308),
        ]
    elif kind == 'float':
        out += [
            ('max', FLOAT_MAX),
            ('max*1.0000001', FLOAT_MAX * 1.0000001),
            ('3.5e38', 3.5e38),
            ('1e39', 1e39),
            ('1e300', 1e300),
            ('-max', -FLOAT_MAX),
            ('-3.5e38', -3.5e38),
        ]
    elif kind == 'half_float':
        out += [
            ('max 65504', HALF_MAX),
            ('65519', 65519.0),
            ('65520', 65520.0),
            ('65536', 65536.0),
            ('1e5', 1e5),
            ('1e300', 1e300),
            ('-65504', -HALF_MAX),
            ('-65520', -65520.0),
        ]
    elif kind == 'scaled_float':
        out += [
            ('max', SCALED_MAX),
            ('9.3e16', 9.3e16),
            ('1e17', 1e17),
            ('1e300', 1e300),
            ('-max', -SCALED_MAX),
            ('-1e17', -1e17),
            ('0.005', 0.005),
            ('0.004', 0.004),
        ]
    else:  # double
        out += [('max', DOUBLE_MAX), ('1e308', 1e308), ('-max', -DOUBLE_MAX), ('2**64', 2**64)]
    out.append(('255 digits', DIGITS_255))
    out.append(('-255 digits', -DIGITS_255))
    return out


def say(line: str) -> None:
    sys.stdout.write(f'{line}\n')


def ask(client: OpenSearch, label: str, query: dict[str, Any]) -> None:
    try:
        answer = client.search(index=INDEX, body={'query': query, 'size': 50, 'sort': ['_doc']})
    except TransportError as error:
        say(f'{label} -> ERROR {error.status_code} {str(error.info)[:260]}')
        return
    say(f'{label} -> {sorted(hit["_id"] for hit in answer["hits"]["hits"])}')


def main() -> None:
    client = OpenSearch(hosts=[os.environ['DTB_OPENSEARCH_URL']])
    say(f'server {client.info()["version"]["number"]}')
    if client.indices.exists(index=INDEX):
        client.indices.delete(index=INDEX)
    client.indices.create(index=INDEX, body={'settings': {'number_of_replicas': 0}, 'mappings': {'properties': FIELDS}})
    docs = {'seven': dict.fromkeys(FIELDS, 7)}
    docs['high'] = {kind: bounds(kind)[1] for kind in FIELDS}
    docs['low'] = {kind: bounds(kind)[0] for kind in FIELDS}
    for doc_id, body in docs.items():
        try:
            client.index(index=INDEX, id=doc_id, body=body)
            say(f'stored {doc_id}')
        except TransportError as error:
            say(f'store {doc_id} -> ERROR {error.status_code} {str(error.info)[:260]}')
            for kind, value in body.items():  # find the field that refused its edge value
                try:
                    client.index(index=INDEX, id=f'{doc_id}-{kind}', body={kind: value})
                    say(f'stored {doc_id}-{kind}')
                except TransportError as each:
                    say(f'store {doc_id}-{kind} -> ERROR {each.status_code} {str(each.info)[:200]}')
    client.indices.refresh(index=INDEX)
    for kind in FIELDS:
        say(f'== {kind}')
        for label, value in numbers(kind):
            ask(client, f'term  {kind:13} {label}', {'term': {kind: value}})
            ask(client, f'terms {kind:13} {label}', {'terms': {kind: [value]}})
            ask(client, f'range {kind:13} gte {label}', {'range': {kind: {'gte': value}}})
            ask(client, f'range {kind:13} lte {label}', {'range': {kind: {'lte': value}}})
    client.indices.delete(index=INDEX)
    client.close()


if __name__ == '__main__':
    main()
