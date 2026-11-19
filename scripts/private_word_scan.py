"""Refuse words that must never become public, in file paths, file contents, commit messages and git history.

The word list lives outside the repository, in the file the environment variable ``DTB_PRIVATE_WORDS`` names: one
word per line, matched case-insensitively as a substring; blank lines and lines starting with ``#`` are skipped. The
list is never committed, and a finding never prints the word: it names the word's line in the list (``#3``), and a
path holding a word is printed with the word replaced by ``***``.

The scan fails closed: without the variable, the file or a word in it, or when git or the file system fails, it
cannot check, and says so with exit status 2, which blocks like a finding (exit status 1). That message names no
command and no path, since either could hold a word.

Usage:
    private_word_scan.py FILE...         paths and contents of the given files (the pre-commit stage)
    private_word_scan.py --message FILE  a commit message (the commit-msg stage)
    private_word_scan.py --history       everything a push publishes: every ref's name, every annotated tag, and
                                         for every commit reachable from any ref its message, author and
                                         committer, and its tree's paths and contents (binary blobs included)
"""

from __future__ import annotations

import codecs
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

type Words = list[tuple[int, str]]  # (line in the list, casefolded word)

_BOMS = [
    (codecs.BOM_UTF32_LE, 'utf-32'),
    (codecs.BOM_UTF32_BE, 'utf-32'),
    (codecs.BOM_UTF8, 'utf-8-sig'),
    (codecs.BOM_UTF16_LE, 'utf-16'),
    (codecs.BOM_UTF16_BE, 'utf-16'),
]


class CouldNotCheckError(Exception):
    """The scan cannot check: no word list, or git or the file system failed."""


def load_words() -> Words:
    """The private words with their line numbers in the list. Raises CouldNotCheckError when there are none."""
    location = os.environ.get(VARIABLE)
    if not location:
        raise CouldNotCheckError(f'{VARIABLE} is not set')
    path = Path(location).expanduser()
    if not path.is_file():
        raise CouldNotCheckError(f'{VARIABLE} names no readable file')
    words = [
        (number, line.strip().casefold())
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1)
        if line.strip() and not line.strip().startswith('#')
    ]
    if not words:
        raise CouldNotCheckError(f'the file {VARIABLE} names holds no word')
    return words


def decode(data: bytes) -> str:
    """Text by its byte order mark when it has one, else UTF-8; bytes that decode to nothing are dropped."""
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return data.decode(encoding, errors='ignore')
    return data.decode('utf-8', errors='ignore')


def hits(text: str, words: Words) -> list[int]:
    """The list lines of the words ``text`` contains."""
    folded = text.casefold()
    return [number for number, word in words if word in folded]


def redact(text: str, words: Words) -> str:
    """``text`` with every private word in it replaced by ``***``, so a finding never prints one."""
    for _, word in words:
        text = re.sub(re.escape(word), '***', text, flags=re.IGNORECASE)
    return text


def report(where: str, numbers: Iterable[int]) -> str:
    return f'{where}: private word #{", #".join(map(str, numbers))}'


def scan_files(names: list[str], words: Words) -> list[str]:
    findings: list[str] = []
    for name in names:
        shown = redact(name, words)
        if found := hits(name, words):
            findings.append(report(f'{shown} (path)', found))
        path = Path(name)
        if not path.is_file():
            continue
        for number, line in enumerate(decode(path.read_bytes()).splitlines(), 1):
            if found := hits(line, words):
                findings.append(report(f'{shown}:{number}', found))
    return findings


def git(*args: str, ok: tuple[int, ...] = (0,)) -> bytes:
    """A git command's output; paths unquoted. Raises CalledProcessError on an unexpected exit status."""
    result = subprocess.run(['git', '-c', 'core.quotePath=false', *args], capture_output=True, check=False)
    if result.returncode not in ok:
        raise subprocess.CalledProcessError(result.returncode, 'git')
    return result.stdout


def fields(output: bytes) -> list[str]:
    """NUL-separated output as text, the empty fields dropped."""
    return [decode(field) for field in output.split(b'\0') if field]


def scan_history(words: Words) -> list[str]:
    findings: list[str] = []
    for ref in decode(git('for-each-ref', '--format=%(refname)')).splitlines():  # ref names hold no newline
        shown = redact(ref, words)
        if found := hits(ref, words):
            findings.append(report(f'ref {shown} (name)', found))
        if decode(git('cat-file', '-t', ref)).strip() != 'tag':
            continue
        if found := hits(decode(git('cat-file', '-p', ref)), words):
            findings.append(report(f'tag {shown} (tagger or message)', found))
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.txt', delete=False) as pattern_file:
        pattern_file.write('\n'.join(word for _, word in words) + '\n')
    try:
        for revision in decode(git('rev-list', '--all')).split():
            short = revision[:12]
            if found := hits(decode(git('log', '-1', '--format=%B', revision)), words):
                findings.append(report(f'{short} (message)', found))
            if found := hits(' '.join(fields(git('log', '-1', '--format=%an%x00%ae%x00%cn%x00%ce', revision))), words):
                findings.append(report(f'{short} (author or committer)', found))
            findings.extend(
                report(f'{short}:{redact(path, words)} (path)', found)
                for path in fields(git('ls-tree', '-r', '-z', '--name-only', revision))
                if (found := hits(path, words))
            )
            listed = git('grep', '-z', '-a', '-i', '-F', '-l', '-f', pattern_file.name, revision, ok=(0, 1))
            for match in fields(listed):
                path = match.split(':', 1)[1]
                found = hits(decode(git('show', f'{revision}:{path}')), words)
                findings.append(report(f'{short}:{redact(path, words)}', found))
    finally:
        Path(pattern_file.name).unlink()
    return findings


def main(argv: list[str]) -> int:
    try:
        words = load_words()
        if argv[:1] == ['--history']:
            findings = scan_history(words)
            scanned = 'everything on every ref'
        elif argv[:1] == ['--message']:
            if len(argv) != 2:
                raise CouldNotCheckError('--message needs the message file')
            found = hits(decode(Path(argv[1]).read_bytes()), words)
            findings = [report('commit message', found)] if found else []
            scanned = 'the commit message'
        else:
            findings = scan_files(argv, words)
            scanned = f'{len(argv)} file(s)'
    except CouldNotCheckError as error:
        print(f'private-word scan could not check: {error}', file=sys.stderr)
        return COULD_NOT_CHECK
    except (subprocess.CalledProcessError, OSError):  # no command line or path: either could hold a word
        print('private-word scan could not check: git or the file system failed', file=sys.stderr)
        return COULD_NOT_CHECK
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        return FOUND
    print(f'private-word scan: {scanned} clean')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
