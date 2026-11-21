"""Throwaway for issue #34: for each ASCII punctuation character, run the live escape tests with a copy of the
source that leaves that one character without its backslash, and print which tests fail. Never merged.

`python -m tools.mutate_34 <directory>` writes the mutated copy there; with that directory on PYTHONPATH,
`python -m tools.mutate_34` runs the tests once for each character, named by DTB_UNESCAPED."""

import os
from pathlib import Path
import shutil
import sys

import pytest
from tests.test_opensearch_live import OPERATORS
from tests.test_opensearch_live import PUNCTUATION

RULE = 'char.isalnum()'
FUTURE = 'from __future__ import annotations\n'


class Failures:
    def __init__(self) -> None:
        self.names: list[str] = []

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.failed:
            self.names.append(report.nodeid.split('::', 1)[1])


def prepare(root: Path) -> None:
    shutil.copytree('src/datatables_backend', root / 'datatables_backend')
    module = root / 'datatables_backend' / 'opensearch.py'
    source = module.read_text(encoding='utf-8')
    if source.count(RULE) != 1 or source.count(FUTURE) != 1:
        raise SystemExit('the source does not hold the rule or the import line exactly once')
    mutated = source.replace(RULE, f"{RULE} or char == os.environ.get('DTB_UNESCAPED')")
    module.write_text(mutated.replace(FUTURE, f'{FUTURE}import os\n'), encoding='utf-8')


def run() -> None:
    sys.stdout.write(f'source: {sys.modules["datatables_backend.opensearch"].__file__}\n')
    tests = 'syntax_in_a_value or punctuation_in_a_value or regular_expression_of_255'
    arguments = ['tests/test_opensearch_live.py', '-q', '--tb=no', '--no-header', '-p', 'no:cacheprovider', '-k', tests]
    rows: list[str] = []
    for char in ['', *PUNCTUATION]:
        os.environ['DTB_UNESCAPED'] = char
        failures = Failures()
        pytest.main(arguments, plugins=[failures])
        mark = ' (has its own row)' if char and char in OPERATORS else ''
        label = f'{ord(char)} {char!r}{mark}' if char else 'none (control)'
        rows.append(f'UNESCAPED {label} -> {"; ".join(failures.names) or "nothing fails"}')
    sys.stdout.write('\n'.join(rows) + '\n')


if len(sys.argv) > 1:
    prepare(Path(sys.argv[1]))
else:
    run()
