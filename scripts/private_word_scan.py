"""Refuse words that must never become public, in file paths, file contents and commit messages.

The word list lives outside the repository, in the file the environment variable ``DTB_PRIVATE_WORDS`` names: one
word per line, matched case-insensitively as a substring; blank lines and lines starting with ``#`` are skipped. The
list is never committed, and a finding never prints the word, only its line number in the list.

The scan fails closed: without the variable, the file or a word in it, it cannot check, and says so with exit
status 2, which blocks like a finding (exit status 1).

Usage:
    private_word_scan.py FILE...         paths and contents of the given files (the pre-commit stage)
    private_word_scan.py --message FILE  a commit message (the commit-msg stage)
    private_word_scan.py --history       every commit reachable from any ref: its tree's paths and contents, and its
                                         message
"""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

VARIABLE = 'DTB_PRIVATE_WORDS'
FOUND = 1
COULD_NOT_CHECK = 2


class CouldNotCheckError(Exception):
    """The scan has no word list to check against."""


def load_words() -> list[str]:
    """The private words, casefolded. Raises CouldNotCheckError when there are none to check against."""
    location = os.environ.get(VARIABLE)
    if not location:
        raise CouldNotCheckError(f'{VARIABLE} is not set')
    path = Path(location).expanduser()
    if not path.is_file():
        raise CouldNotCheckError(f'{VARIABLE} names no readable file')
    lines = (line.strip() for line in path.read_text(encoding='utf-8').splitlines())
    words = [line.casefold() for line in lines if line and not line.startswith('#')]
    if not words:
        raise CouldNotCheckError(f'the file {VARIABLE} names holds no word')
    return words


def hits(text: str, words: list[str]) -> list[int]:
    """The 1-based numbers of the words ``text`` contains."""
    folded = text.casefold()
    return [number for number, word in enumerate(words, 1) if word in folded]


def redact(text: str, words: list[str]) -> str:
    """``text`` with every private word in it replaced by ``***``, so a finding never prints one."""
    for word in words:
        text = re.sub(re.escape(word), '***', text, flags=re.IGNORECASE)
    return text


def report(where: str, numbers: Iterable[int]) -> str:
    return f'{where}: private word #{", #".join(map(str, numbers))}'


def scan_files(names: list[str], words: list[str]) -> list[str]:
    findings: list[str] = []
    for name in names:
        shown = redact(name, words)
        if found := hits(name, words):
            findings.append(report(f'{shown} (path)', found))
        try:
            text = Path(name).read_text(encoding='utf-8', errors='ignore')
        except (IsADirectoryError, FileNotFoundError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if found := hits(line, words):
                findings.append(report(f'{shown}:{number}', found))
    return findings


def git(*args: str) -> str:
    return subprocess.run(['git', *args], check=True, capture_output=True, text=True).stdout


def scan_history(words: list[str]) -> list[str]:
    findings: list[str] = []
    revisions = git('rev-list', '--all').split()
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.txt', delete=False) as pattern_file:
        pattern_file.write('\n'.join(words) + '\n')
    try:
        for revision in revisions:
            short = revision[:12]
            if found := hits(git('log', '-1', '--format=%B', revision), words):
                findings.append(report(f'{short} (message)', found))
            findings.extend(
                report(f'{short}:{redact(path, words)} (path)', found)
                for path in git('ls-tree', '-r', '--name-only', revision).splitlines()
                if (found := hits(path, words))
            )
            grep = subprocess.run(
                ['git', 'grep', '-I', '-i', '-F', '-l', '-f', pattern_file.name, revision],
                capture_output=True,
                text=True,
                check=False,
            )
            if grep.returncode not in (0, 1):
                raise CouldNotCheckError(f'git grep failed on {short}')
            for line in grep.stdout.splitlines():
                path = line.split(':', 1)[1]
                found = hits(git('show', f'{revision}:{path}'), words)
                findings.append(report(f'{short}:{redact(path, words)}', found))
    finally:
        Path(pattern_file.name).unlink()
    return findings


def main(argv: list[str]) -> int:
    try:
        words = load_words()
        if argv[:1] == ['--history']:
            findings = scan_history(words)
            scanned = 'every commit on every ref'
        elif argv[:1] == ['--message']:
            message = Path(argv[1]).read_text(encoding='utf-8', errors='ignore')
            findings = [report('commit message', found)] if (found := hits(message, words)) else []
            scanned = 'the commit message'
        else:
            findings = scan_files(argv, words)
            scanned = f'{len(argv)} file(s)'
    except CouldNotCheckError as error:
        print(f'private-word scan could not check: {error}', file=sys.stderr)
        return COULD_NOT_CHECK
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        return FOUND
    print(f'private-word scan: {scanned} clean')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
