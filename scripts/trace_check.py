"""Check that the specs trace to the PRD: standard library only.

A requirement is one PRD section. Its heading is exactly ``#### PRD-X1``, from the line's first column, and the
section runs to the next heading line. It holds the requirement's text, which is one or more lines that do not start
with the Reason or the Priority label (so several paragraphs, a list or a quote are text), then one list item
``- **Reason:** text`` whose further lines are indented by spaces, then as its last line ``- **Priority:** must`` (or
``should`` or ``later``). Blank lines are not counted.

A heading line is any line whose first character after white space is ``#``. The rule is by line, not by Markdown:
a ``#`` line inside a code fence, or a wrapped line of text that starts with ``#``, is a heading line too, so no
line of a requirement's section, its reason's further lines included, has ``#`` as its first character after white
space. A heading line that holds ``PRD-`` and is not a requirement's heading
(another level, indented, with more text) is a finding, so a heading names a requirement id only when it is that
requirement's own. A table line whose first cell starts with ``PRD-``, decorated or linked, which was the layout
before the sections, is a finding too. A requirement written in neither form (a bold line, a list item) is not seen;
it surfaces only when a statement states it.

A spec statement is one paragraph: a list item in a ``*.md`` file directly in the specs directory (not in a
subdirectory, and not ``README.md``) that starts ``- **SPEC-X1.n** `` followed by text, plus the indented lines that
follow it. It ends at a blank line, a heading or another unindented list item; a second paragraph is not read, and an
unindented line that would continue it is a finding. The statement states requirement PRD-X1, and every other
``PRD-`` id in its text, backticked or not, is a citation; a citation not of the shape ``PRD-X1`` (a range
``PRD-F1..F9``, ``PRD-F1a``) is a finding. Statement numbers compare as integers, so ``SPEC-F1.01`` is ``SPEC-F1.1``.
The text ``SPEC-`` anywhere else is a finding, unless it is in backticks: a statement refers to another as
`` `SPEC-F1.1` ``. A code span stays on one line; one wrapped across lines is not read as a code span.

Findings, one line each on stderr, exit status 1:
- a requirement heading or section of the wrong shape, a requirement in table form, a requirement id defined twice,
  or no requirement at all;
- no statement at all, the text ``SPEC-`` outside backticks anywhere but a statement's start, or an unindented line
  right after a statement;
- a *must* or *should* requirement with no statement (a *later* one needs none);
- a statement that states or cites a requirement id the PRD does not hold, or cites a malformed ``PRD-`` id;
- a statement id defined more than once;
- a statement of an enforced requirement that no test names, unless its first tag is ``[review]`` or ``[CI <job>]``
  (a statement whose first tag is a behaviour tag, ``[all]`` or ``[OS]``, needs a test even if a later tag says
  ``[review]``). A test names a statement by its id (``SPEC-P1.1``) anywhere in a ``*.py`` file under the tests
  directory; ``SPEC-F1.01`` is ``SPEC-F1.1``. A test may cite a statement written against another fixture, if it
  exercises the behaviour the statement states. ``ENFORCED`` holds the enforced PRD ids or letters; ``--enforce``
  replaces it;
- a test file naming a statement that no spec holds;
- an ``xfail`` in the tests that is not a call of a mark with ``strict=True``, ``raises=`` and a ``reason`` that names
  an issue (``#47``): a bare mark, ``pytest.xfail()`` (it cannot be strict) and ``xfail`` imported by name are
  findings too. That the issue is still open is not checked: a fixed behaviour makes a strict xfail fail, and the
  reviewer reads the rest at each issue close;
- an ``--enforce`` entry (or ``ENFORCED`` one) that is neither a PRD id nor the letter of one.

An unreadable PRD, specs or tests directory is a usage error (exit status 2).

Usage: trace_check.py [--prd docs/PRD.md] [--specs spec] [--tests tests] [--enforce ID ...]
"""

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from itertools import takewhile
from pathlib import Path
import re
import sys

# PRD ids (`PRD-F25`) or letters (`F`) whose statements a test must cite; each acceptance issue adds its own
ENFORCED = ('P', 'R')
TABLE_ROW = re.compile(r'^\s*\|\s*[*_`~\[]*PRD-')
HEADING_LINE = re.compile(r'^\s*#')
HEADING = re.compile(r'^#### (PRD-[A-Z]+\d+)$')
SECTION = re.compile(
    r'(?:(?!- \*\*(?:Reason|Priority):\*\*).+\n)+'
    r'- \*\*Reason:\*\* .+\n(?: +\S.*\n)*'  # \S: a line's spaces match in one way only, or matching is exponential
    r'- \*\*Priority:\*\* (must|should|later)\n'
)
STATEMENT = re.compile(r'^- \*\*SPEC-([A-Z]+)(\d+)\.(\d+)\*\* (\S.*)$')
CONTINUATION = re.compile(r'^\s+\S')
ENDS_STATEMENT = re.compile(r'^(\s*$|#|[-*+] |\d+[.)] )')
LOOSE_CITATION = re.compile(r'PRD-[\w.-]*[\w-]')
CITATION = re.compile(r'PRD-[A-Z]+\d+')
BACKTICKED = re.compile(r'`[^`]*`')
EXEMPT = re.compile(r'\[(?:review\]|CI [\w-]+\])')
SPEC_ID = re.compile(r'SPEC-([A-Z]+)(\d+)\.(\d+)')
ISSUE = re.compile(r'#\d+')


@dataclass
class Statement:
    id: str
    requirement: str
    cited: set[str] = field(default_factory=set[str])
    malformed: list[str] = field(default_factory=list[str])
    exempt: bool = False


def requirements(name: str, text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """The requirements as (id, priority), and the findings on the PRD's sections."""
    rows: list[tuple[str, str]] = []
    findings: list[str] = []
    lines = [line.rstrip() for line in text.splitlines()]
    for number, line in enumerate(lines, 1):
        if TABLE_ROW.match(line):
            findings.append(f'{name}:{number} is a requirement row, not a section')
        if not HEADING_LINE.match(line) or 'PRD-' not in line:
            continue
        heading = HEADING.match(line)
        if not heading:
            findings.append(f'{name}:{number} is not a requirement heading of the expected shape')
            continue
        below = takewhile(lambda after: not HEADING_LINE.match(after), lines[number:])
        if section := SECTION.fullmatch(''.join(f'{part}\n' for part in below if part)):
            rows.append((heading[1], section[1]))
        else:
            findings.append(f'{name}:{number} is not a requirement section of the expected shape')
    return rows, findings


def statements(files: list[tuple[str, str]]) -> tuple[list[Statement], list[str]]:
    """The statements, and the findings on stray ids and unindented continuations."""
    found: list[Statement] = []
    findings: list[str] = []
    for name, text in files:
        current: Statement | None = None
        for number, line in enumerate(text.splitlines(), 1):
            body = line
            if match := STATEMENT.match(line):
                letters, requirement, index, body = match.groups()
                current = Statement(f'SPEC-{letters}{requirement}.{int(index)}', f'PRD-{letters}{requirement}')
                current.exempt = bool(EXEMPT.match(body))
                found.append(current)
            elif current is None or not CONTINUATION.match(line):
                if current is not None and not ENDS_STATEMENT.match(line):
                    findings.append(f'{name}:{number} follows a statement without a blank line or indentation')
                current = None
            if 'SPEC-' in BACKTICKED.sub('', body):
                if current is not None:
                    findings.append(f'{name}:{number} names a SPEC id inside a statement outside backticks')
                else:
                    findings.append(f'{name}:{number} holds a SPEC id outside a statement of the expected shape')
            if current is not None:
                for token in LOOSE_CITATION.findall(body):
                    if CITATION.fullmatch(token):
                        current.cited.add(token)
                    else:
                        current.malformed.append(token)
    return found, findings


def xfail_findings(name: str, text: str) -> list[str]:
    """The xfail uses of a test file that are not a strict mark naming its exception and an issue."""
    tree = ast.parse(text)
    calls = {id(node.func): node for node in ast.walk(tree) if isinstance(node, ast.Call)}
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute | ast.Name) or getattr(node, 'attr', getattr(node, 'id', '')) != 'xfail':
            continue
        where = f'{name}:{node.lineno} xfail'
        owner = node.value if isinstance(node, ast.Attribute) else None
        owner_name = getattr(owner, 'attr', getattr(owner, 'id', None))
        if (call := calls.get(id(node))) is None:
            findings.append(f'{where} is not called; write xfail(reason=..., strict=True, raises=...)')
        elif owner_name != 'mark':
            findings.append(f'{where}() cannot be strict; use a strict mark')
        else:
            keywords = {keyword.arg: keyword.value for keyword in call.keywords}
            strict, reason = keywords.get('strict'), keywords.get('reason')
            if not (isinstance(strict, ast.Constant) and strict.value is True):
                findings.append(f'{where} is not strict')
            elif 'raises' not in keywords:
                findings.append(f'{where} names no expected exception (raises=)')
            elif not (isinstance(reason, ast.Constant) and ISSUE.search(str(reason.value))):
                findings.append(f'{where} names no issue')
    return findings


def check(
    prd_name: str,
    prd_text: str,
    files: list[tuple[str, str]],
    tests: list[tuple[str, str]],
    enforce: list[str],
) -> tuple[list[str], str]:
    """The findings, and the success line."""
    rows, findings = requirements(prd_name, prd_text)
    stated, stray = statements(files)
    findings.extend(stray)
    if not rows:
        findings.append('the PRD holds no requirement')
    if not stated:
        findings.append('the specs hold no statement')
    counts = Counter(requirement for requirement, _ in rows)
    findings.extend(f'{rid} is defined {count} times in the PRD' for rid, count in counts.items() if count > 1)
    priority = dict(rows)
    covered = {statement.requirement for statement in stated}
    findings.extend(
        f'{rid} has no spec statement' for rid, level in priority.items() if level != 'later' and rid not in covered
    )
    for statement in stated:
        if statement.requirement not in priority:
            findings.append(f'{statement.id} states {statement.requirement}, which does not exist')
        findings.extend(
            f'{statement.id} cites {missing}, which does not exist'
            for missing in sorted(statement.cited)
            if missing not in priority
        )
        findings.extend(f'{statement.id} cites the malformed id {bad}' for bad in statement.malformed)
    known_ids = {rid for rid, _ in rows}
    known_letters = {rid[4:].rstrip('0123456789') for rid in known_ids}
    findings.extend(
        f'enforce entry {entry} names no PRD id or letter'
        for entry in enforce
        if entry not in known_ids | known_letters
    )
    cited_by = {
        f'SPEC-{letters}{number}.{int(index)}': name
        for name, text in tests
        for letters, number, index in SPEC_ID.findall(text)
    }
    for statement in stated:
        letters = statement.requirement[4:].rstrip('0123456789')
        enforced = statement.requirement in enforce or letters in enforce
        if enforced and not statement.exempt and statement.id not in cited_by:
            findings.append(f'{statement.id} is cited by no test')
    known = {statement.id for statement in stated}
    findings.extend(
        f'{name} cites {cited}, which does not exist' for cited, name in cited_by.items() if cited not in known
    )
    for name, text in tests:
        findings.extend(xfail_findings(name, text))
    ids = Counter(statement.id for statement in stated)
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
    parser.add_argument('--specs', type=Path, default=Path('spec'))
    parser.add_argument('--tests', type=Path, default=Path('tests'))
    parser.add_argument('--enforce', nargs='*', default=list(ENFORCED))
    args = parser.parse_args()
    try:
        prd_text = args.prd.read_text(encoding='utf-8')
        if not args.specs.is_dir():
            parser.error(f'{args.specs} is not a directory')
        files = [
            (path.name, path.read_text(encoding='utf-8'))
            for path in sorted(args.specs.iterdir())
            if path.suffix == '.md' and path.name != 'README.md' and not path.is_dir()
        ]
        if not args.tests.is_dir():
            parser.error(f'{args.tests} is not a directory')
        tests = [
            (path.relative_to(args.tests).as_posix(), path.read_text(encoding='utf-8'))
            for path in sorted(args.tests.rglob('*.py'))
        ]
    except (OSError, UnicodeDecodeError) as error:
        parser.error(f'cannot read the input: {error}')
    findings, summary = check(args.prd.name, prd_text, files, tests, args.enforce)
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        return 1
    print(summary)
    return 0


if __name__ == '__main__':
    sys.exit(main())
