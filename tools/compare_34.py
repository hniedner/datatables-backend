"""Throwaway for issue #34: the answers of `fetch_sync` over a set of values, one line each, to compare the source
of `main` with the branch's (the job runs it once with each on PYTHONPATH and prints the diff). Never merged."""

import os
import sys
from typing import Any

from opensearchpy import OpenSearch
from opensearchpy.exceptions import TransportError
from tests.conftest import criterion
from tests.conftest import request
from tests.test_opensearch_live import DOCUMENTS
from tests.test_opensearch_live import LONGEST
from tests.test_opensearch_live import LONGEST_BEYOND_ASCII
from tests.test_opensearch_live import LONGEST_PATTERN
from tests.test_opensearch_live import MAPPING
from tests.test_opensearch_live import OPERATORS
from tests.test_opensearch_live import PUNCTUATION

import datatables_backend
from datatables_backend import Column
from datatables_backend.opensearch import IndexColumn
from datatables_backend.opensearch import SearchTable

INDEX = 'dtb-compare-34'
EXTRA = {'id': 'x9', 'title': 'Extra', 'body': 'extra', 'label': ['日本語', 'STRAẞE', 'İZMİR', 'ǅungla', 'bär', 'Bär']}
VALUES = [
    'apple', 'APPLE', 'pfel', 'PFEL', 'abc', LONGEST, LONGEST.lower(),  # ASCII
    'äpfel', 'ÄPFEL', 'äpf', 'ÄPF', 'bär', 'BÄR', 'Bär', 'straße', 'STRAẞE', 'izmir', 'İZMİR', 'i̇zmi̇r',  # cased
    'ǅungla', 'ǆungla', 'Ǆungla',  # a titlecase letter and its two other cases
    '日本語', '日本', '本',  # no case
    'äXz', 'ÄxZ', 'äz', 'ÄZ', 'ä', 'Ä', 'z',  # mixed, and the decoys
    LONGEST_BEYOND_ASCII, LONGEST_BEYOND_ASCII.lower(), LONGEST_PATTERN, LONGEST_PATTERN.lower(),
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


def main() -> None:
    sys.stderr.write(f'source: {datatables_backend.__file__}\n')
    client = OpenSearch(hosts=[os.environ['DTB_OPENSEARCH_URL']], timeout=120)
    if client.indices.exists(index=INDEX):
        client.indices.delete(index=INDEX)
    client.indices.create(index=INDEX, body={'mappings': MAPPING, 'settings': {'number_of_replicas': 0}})
    for number, document in enumerate([*DOCUMENTS, EXTRA], 1):
        client.index(index=INDEX, id=str(number), body=document)
    client.indices.refresh(index=INDEX)
    try:
        for name, table in TABLES.items():
            for number, value in enumerate(VALUES):
                shown = value if len(value) < 40 else f'{value[:12]}...({len(value)})'
                bodies: dict[str, dict[str, Any]] = {c: criterion('label', c, value) for c in CONDITIONS}
                bodies['global'] = {'search': {'value': value}}
                bodies['column'] = {'column_search': {'label': {'value': value}}}
                bodies['list'] = {'column_search': {'label': {'multi_values': [value, 'ÄPFEL']}}}
                for kind, body in bodies.items():
                    sys.stdout.write(f'{name} | {kind} | {number} {shown} -> {answer(client, table, body)}\n')
    finally:
        client.indices.delete(index=INDEX)


main()
