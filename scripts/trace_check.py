"""Check that the specs trace to the PRD: standard library only.

A requirement is a PRD table row ``| PRD-X1 | ... | must |``. A spec statement is a Markdown list item starting
``- **SPEC-X1.n**``; it states requirement PRD-X1, and every other ``PRD-...`` id in its text is a citation.

The check fails (exit status 1, one line per finding) when:
- a *must* or *should* requirement has no statement (a *later* one needs none);
- a statement states or cites a requirement id the PRD does not hold;
- a statement id is defined more than once.

Usage: trace_check.py [--prd docs/PRD.md] [--specs docs/specs]
"""

import argparse
from collections import Counter
from pathlib import Path
import re
import sys

REQUIREMENT = re.compile(r'^\| (PRD-[A-Z]+\d+) \|.*\| (must|should|later) \|\s*$')
STATEMENT = re.compile(r'^\s*- \*\*SPEC-([A-Z]+\d+)\.(\d+)\*\*(.*)$')
CITATION = re.compile(r'\bPRD-[A-Z]+\d+\b')


def requirements(prd: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in prd.read_text(encoding='utf-8').splitlines():
        if match := REQUIREMENT.match(line):
            found[match[1]] = match[2]
    return found


def statements(specs: Path) -> list[tuple[str, str, set[str]]]:
    """Each statement as (its id, the requirement it states, the ids it cites)."""
    return [
        (f'SPEC-{match[1]}.{match[2]}', f'PRD-{match[1]}', set(CITATION.findall(match[3])))
        for path in sorted(specs.glob('*.md'))
        for line in path.read_text(encoding='utf-8').splitlines()
        if (match := STATEMENT.match(line))
    ]


def check(prd: Path, specs: Path) -> list[str]:
    known = requirements(prd)
    stated = statements(specs)
    findings: list[str] = []
    covered = {requirement for _, requirement, _ in stated}
    findings.extend(
        f'{requirement} has no spec statement'
        for requirement, priority in known.items()
        if priority != 'later' and requirement not in covered
    )
    for statement, requirement, cited in stated:
        findings.extend(
            f'{statement} cites {missing}, which does not exist'
            for missing in sorted({requirement} | cited)
            if missing not in known
        )
    counts = Counter(statement for statement, _, _ in stated)
    findings.extend(f'{statement} is defined {count} times' for statement, count in counts.items() if count > 1)
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description='Check that the specs trace to the PRD.')
    parser.add_argument('--prd', type=Path, default=Path('docs/PRD.md'))
    parser.add_argument('--specs', type=Path, default=Path('docs/specs'))
    args = parser.parse_args()
    findings = check(args.prd, args.specs)
    for finding in findings:
        print(finding)
    if findings:
        return 1
    print(f'trace check: {len(statements(args.specs))} statements, every requirement traced')
    return 0


if __name__ == '__main__':
    sys.exit(main())
