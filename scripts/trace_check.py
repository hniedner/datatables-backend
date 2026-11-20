"""Check that the specs trace to the PRD: standard library only.

A requirement is a PRD table row ``| PRD-X1 | requirement | reason | must |`` (priority ``must``, ``should`` or
``later``). Any row starting with a ``PRD-`` id is a candidate, and one that does not have exactly that shape is a
finding, so a drifted row is never dropped silently.

A spec statement is a list item in a ``*.md`` file directly in the specs directory (not in a subdirectory, and not
``README.md``), starting ``- **SPEC-X1.n**``. It states requirement PRD-X1. Its text runs on over the indented lines
that follow it, up to a blank line, a heading or the next item, and every other ``PRD-`` id in that text is a
citation. Statement numbers compare as integers, so ``SPEC-F1.01`` is ``SPEC-F1.1``. Any other ``SPEC-`` id outside
backticks is a finding.

Findings, one line each on stderr, exit status 1:
- a requirement row of the wrong shape, a requirement id defined twice, or no requirement at all;
- no statement at all, or a ``SPEC-`` id outside a statement of the expected shape;
- a *must* or *should* requirement with no statement (a *later* one needs none);
- a statement that states or cites a requirement id the PRD does not hold;
- a statement id defined more than once.

An unreadable PRD or specs directory is a usage error (exit status 2).

Usage: trace_check.py [--prd docs/PRD.md] [--specs docs/specs]
"""

import argparse
from collections import Counter
from pathlib import Path
import re
import sys

CANDIDATE = re.compile(r'^\s*\|\s*PRD-')
CELL = r'(?:[^|\\]|\\.)+'
REQUIREMENT = re.compile(rf'^\| (PRD-[A-Z]+\d+) \|{CELL}\|{CELL}\| (must|should|later) \|$')
STATEMENT = re.compile(r'^- \*\*SPEC-([A-Z]+)(\d+)\.(\d+)\*\* (.*)$')
CONTINUATION = re.compile(r'^\s+\S')
SPEC_ID = re.compile(r'SPEC-[A-Za-z]+\d')
CITATION = re.compile(r'\bPRD-[A-Z]+\d+\b')
BACKTICKED = re.compile(r'`[^`]*`')


def requirements(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """The requirement rows as (id, priority), and the findings on the PRD's rows."""
    rows: list[tuple[str, str]] = []
    findings: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if match := REQUIREMENT.match(line):
            rows.append((match[1], match[2]))
        elif CANDIDATE.match(line):
            findings.append(f'PRD row {number} is not a requirement row of the expected shape')
    return rows, findings


def statements(files: list[tuple[str, str]]) -> tuple[list[tuple[str, str, set[str]]], list[str]]:
    """Each statement as (its id, the requirement it states, the ids it cites), and the findings on stray ids."""
    found: list[tuple[str, str, set[str]]] = []
    findings: list[str] = []
    for name, text in files:
        current: list[str] | None = None
        for number, line in enumerate(text.splitlines(), 1):
            if match := STATEMENT.match(line):
                letters, requirement, index, rest = match.groups()
                current = [rest]
                found.append((f'SPEC-{letters}{requirement}.{int(index)}', f'PRD-{letters}{requirement}', set()))
                stray = rest
            elif current is not None and CONTINUATION.match(line):
                current.append(line)
                stray = line
            else:
                current = None
                stray = line
            if current is not None:
                found[-1][2].update(CITATION.findall(line))
            if SPEC_ID.search(BACKTICKED.sub('', stray)):
                findings.append(f'{name}:{number} holds a SPEC id outside a statement of the expected shape')
    return found, findings


def check(prd_text: str, files: list[tuple[str, str]]) -> tuple[list[str], str]:
    """The findings, and the success line."""
    rows, findings = requirements(prd_text)
    stated, stray = statements(files)
    findings.extend(stray)
    if not rows:
        findings.append('the PRD holds no requirement')
    if not stated:
        findings.append('the specs hold no statement')
    counts = Counter(requirement for requirement, _ in rows)
    findings.extend(f'{rid} is defined {count} times in the PRD' for rid, count in counts.items() if count > 1)
    priority = dict(rows)
    covered = {requirement for _, requirement, _ in stated}
    findings.extend(
        f'{rid} has no spec statement' for rid, level in priority.items() if level != 'later' and rid not in covered
    )
    for statement, requirement, cited in stated:
        findings.extend(
            f'{statement} cites {missing}, which does not exist'
            for missing in sorted({requirement} | cited)
            if missing not in priority
        )
    ids = Counter(statement for statement, _, _ in stated)
    findings.extend(f'{statement} is defined {count} times' for statement, count in ids.items() if count > 1)
    levels = Counter(level for _, level in rows)
    summary = (
        f'trace check: {len(rows)} requirements ({levels["must"]} must, {levels["should"]} should, '
        f'{levels["later"]} later), {len(stated)} statements, all traced'
    )
    return findings, summary


def main() -> int:
    parser = argparse.ArgumentParser(description='Check that the specs trace to the PRD.')
    parser.add_argument('--prd', type=Path, default=Path('docs/PRD.md'))
    parser.add_argument('--specs', type=Path, default=Path('docs/specs'))
    args = parser.parse_args()
    try:
        prd_text = args.prd.read_text(encoding='utf-8')
        if not args.specs.is_dir():
            parser.error(f'{args.specs} is not a directory')
        files = [
            (path.name, path.read_text(encoding='utf-8'))
            for path in sorted(args.specs.glob('*.md'))
            if path.name != 'README.md'
        ]
    except (OSError, UnicodeDecodeError) as error:
        parser.error(f'cannot read the input: {error}')
    findings, summary = check(prd_text, files)
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        return 1
    print(summary)
    return 0


if __name__ == '__main__':
    sys.exit(main())
