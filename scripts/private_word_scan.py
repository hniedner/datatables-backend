"""Refuse words that must never become public, in file paths, file contents, commit messages and git history.

The word list lives outside the repository, in the file the environment variable ``DTB_PRIVATE_WORDS`` names: one
word per line, matched case-insensitively as a substring; blank lines and lines starting with ``#`` are skipped. The
list is never committed, and a finding never prints the word: it names the word's line in the list (``#3``), and a
path or ref name is printed with each ``/``-separated part that holds a word replaced by ``***``, or as ``***``
whole when a word spans parts.

The scan fails closed: without the variable, the file or a word in it, or when git or the file system fails, it
cannot check, and says so with exit status 2, which blocks like a finding (exit status 1). That message names no
command and no path, since either could hold a word.

Usage:
    private_word_scan.py FILE...         paths and contents of the given files (the pre-commit stage)
    private_word_scan.py --message FILE  a commit message (the commit-msg stage) as git stores it: nothing below its
                                         scissors line, and no comment lines when git opened an editor for it
    private_word_scan.py --refs          every local ref's name and every annotated tag (the pre-commit and
                                         post-checkout stages: a push that only adds a ref brings no commit, so
                                         pre-commit runs no pre-push hook for it)
    private_word_scan.py --history       everything a push publishes: every ref's name and the remote ref the push
                                         writes (pre-commit's PRE_COMMIT_REMOTE_BRANCH), every annotated tag, for
                                         every commit reachable from any ref, any reflog entry or the pushed commit
                                         (PRE_COMMIT_TO_REF) its message, author, committer and tree paths, and
                                         every such object's raw bytes once (blobs decoded as files are; trees,
                                         commits and tags as text)

The reflog is read because pre-commit names only the first pushed ref with new commits. An old reflog entry holding a
word therefore blocks every push until it is gone: ``git reflog expire --expire-unreachable=now --all`` (then
``git gc --prune=now``) drops the unreachable ones.

Not covered: ``git branch NAME`` or ``git tag`` followed directly by a push with no commit or checkout between, and a
push naming a remote ref that no local ref holds (``git push origin HEAD:refs/heads/NAME``) that brings no new commit,
since no hook runs for either; and the remote names of the second and later refs in one push, which pre-commit does
not pass to the hook (their commits are still scanned).
"""

from __future__ import annotations

import codecs
from collections.abc import Iterable
from collections.abc import Iterator
import os
from pathlib import Path
import subprocess
import sys

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
    try:
        text = path.read_text(encoding='utf-8-sig')  # an editor's byte order mark must not hide word #1
    except UnicodeDecodeError:
        raise CouldNotCheckError(f'the file {VARIABLE} names is not UTF-8') from None
    words = [
        (number, line.strip().casefold())
        for number, line in enumerate(text.splitlines(), 1)
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


def other_readings(data: bytes) -> list[str]:
    """The same bytes read in the encodings ``decode`` does not try: without a byte order mark, UTF-16 at both
    alignments (UTF-16LE from the second byte reads UTF-16BE text), and cp1252 when the bytes are not UTF-8."""
    if any(data.startswith(bom) for bom, _ in _BOMS):
        return []
    readings = [data[start:].decode('utf-16-le', errors='ignore') for start in (0, 1)]
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        readings.append(data.decode('cp1252', errors='ignore'))
    return readings


def content_hits(data: bytes, words: Words) -> list[int]:
    """The list lines of the words the bytes hold in any reading but ``decode``'s."""
    return sorted({number for reading in other_readings(data) for number in hits(reading, words)})


def hits(text: str, words: Words) -> list[int]:
    """The list lines of the words ``text`` contains."""
    folded = text.casefold()
    return [number for number, word in words if word in folded]


def redact(text: str, words: Words) -> str:
    """``text`` (a path or ref name) with every ``/``-separated part that holds a private word replaced by ``***``.

    The test is the same casefolded one as ``hits``, so whatever is found is also hidden; no position is mapped back.
    A word that spans parts (one holding a ``/``) hides the whole text.
    """
    shown = '/'.join('***' if hits(part, words) else part for part in text.split('/'))
    return '***' if hits(shown, words) else shown


def report(where: str, numbers: Iterable[int]) -> str:
    return f'{where}: private word #{", #".join(map(str, numbers))}'


def scan_files(names: list[str], words: Words) -> list[str]:
    findings: list[str] = []
    for name in names:
        shown = redact(name, words)
        if found := hits(name, words):
            findings.append(report(f'{shown} (path)', found))
        path = Path(name)
        if path.is_symlink():  # the link's own text: its target, which is what git stores and publishes
            if found := hits(os.readlink(path), words):
                findings.append(report(f'{shown} (link target)', found))
            continue
        data = path.read_bytes()
        for number, line in enumerate(decode(data).splitlines(), 1):
            if found := hits(line, words):
                findings.append(report(f'{shown}:{number}', found))
        if found := content_hits(data, words):
            findings.append(report(f'{shown} (contents in another encoding)', found))
    return findings


def git(*args: str, ok: tuple[int, ...] = (0,)) -> bytes:
    """A git command's output. Raises CalledProcessError on an unexpected exit status.

    No output read here quotes a path: ``ls-tree -z``, ``rev-list --objects`` and the ``log`` formats print it raw.
    """
    result = subprocess.run(['git', *args], capture_output=True, check=False)
    if result.returncode not in ok:
        raise subprocess.CalledProcessError(result.returncode, 'git')
    return result.stdout


def fields(output: bytes) -> list[str]:
    """NUL-separated output as text, the empty fields dropped."""
    return [decode(field) for field in output.split(b'\0') if field]


def published() -> list[str]:
    """The rev-list arguments for what a push can publish: every ref, every reflog entry (pre-commit names only the
    first pushed ref with new commits, so ``git push origin main HEAD@{2}:refs/heads/x`` publishes a commit no ref
    and no variable names), and the commit pre-commit names (``PRE_COMMIT_TO_REF``), which may be in neither."""
    to_ref = os.environ.get('PRE_COMMIT_TO_REF', '')
    return ['--all', '--reflog', *([to_ref] if to_ref else [])]


def reachable_objects() -> Iterator[tuple[str, str, str, bytes]]:
    """Every object a push can publish, once: its id, kind (blob, tree, commit or tag), a path it is reachable
    under ('' for none) and its raw bytes."""
    paths: dict[str, str] = {}
    for line in decode(git('rev-list', '--objects', *published())).split('\n'):  # newline only: see below
        if not line:
            continue
        sha, _, path = line.partition(' ')  # a path may hold U+0085, U+2028 and the like, which splitlines splits
        paths.setdefault(sha, path)
    request = ''.join(f'{sha}\n' for sha in paths).encode()
    result = subprocess.run(['git', 'cat-file', '--batch'], input=request, capture_output=True, check=False)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, 'git')
    out, at = result.stdout, 0
    for sha, path in paths.items():  # cat-file answers in the order it was asked
        end = out.index(b'\n', at)
        _, kind, size = out[at:end].split()
        start = end + 1
        at = start + int(size) + 1
        yield sha, kind.decode(), path, out[start : start + int(size)]


def scan_refs(words: Words) -> list[str]:
    findings: list[str] = []
    for ref in filter(None, decode(git('for-each-ref', '--format=%(refname)')).split('\n')):  # no newline in a name
        shown = redact(ref, words)
        if found := hits(ref, words):
            findings.append(report(f'ref {shown} (name)', found))
        if decode(git('cat-file', '-t', ref)).strip() != 'tag':
            continue
        if found := hits(decode(git('cat-file', '-p', ref)), words):
            findings.append(report(f'tag {shown} (tagger or message)', found))
    return findings


def message_text(raw: str) -> str:
    """A commit message file as git will store it. Git cuts at its scissors line only where it wrote one, followed
    by its own warning line (``commit -v``, ``--cleanup=scissors``), and drops comment lines only where its editor
    hint says so, as a whole line. A ``-m`` or ``-F`` message keeps everything, as git does. The comment character is
    git's ``core.commentChar``, ``#`` by default."""
    comment = decode(git('config', '--get', 'core.commentChar', ok=(0, 1))).strip() or '#'
    if comment == 'auto':  # git picks one the message does not start a line with; '#' unless taken
        comment = '#'
    lines = raw.split('\n')
    scissors = f'{comment} ------------------------ >8 ------------------------'
    warning = f'{comment} Do not modify or remove the line above.'
    for number, line in enumerate(lines[:-1]):
        if line == scissors and lines[number + 1] == warning:
            lines = lines[:number]
            break
    hint = f"{comment} with '{comment}' will be ignored, and an empty message aborts the commit."
    if hint in lines:
        lines = [line for line in lines if not line.startswith(comment)]
    return '\n'.join(lines)


def scan_history(words: Words) -> list[str]:
    findings = scan_refs(words)
    remote = os.environ.get('PRE_COMMIT_REMOTE_BRANCH', '')  # the ref a push writes, which no local ref may hold
    if found := hits(remote, words):
        findings.append(report(f'remote ref {redact(remote, words)} (name)', found))
    for revision in decode(git('rev-list', *published())).split():
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
    for sha, kind, path, data in reachable_objects():  # each object once; a blob decoded as file mode decodes it
        other: list[int] = content_hits(data, words) if kind == 'blob' else []  # other encodings: file text only
        found = sorted({*hits(decode(data), words), *other})
        if found:  # trees hold names, tags and commits their text: a tag a tag wraps, a tree only a tag holds
            shown = redact(path, words) if path else f'{kind} {sha[:12]}'
            findings.append(report(f'{shown} ({"contents" if kind == "blob" else kind})', found))
    return findings


def main(argv: list[str]) -> int:
    try:
        words = load_words()
        if argv == ['--refs']:  # exactly, so a staged file named like a mode stays a file
            findings = scan_refs(words)
            scanned = 'every local ref'
        elif argv == ['--history']:
            findings = scan_history(words)
            scanned = 'everything on every ref'
        elif argv[:1] == ['--message']:
            if len(argv) != 2:
                raise CouldNotCheckError('--message needs the message file')
            found = hits(message_text(decode(Path(argv[1]).read_bytes())), words)
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
