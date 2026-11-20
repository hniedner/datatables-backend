"""Refuse words that must never become public, in file paths, file contents, commit messages and git history.

The word list lives outside the repository, in the file the environment variable ``DTB_PRIVATE_WORDS`` names: one
word per line, matched case-insensitively as a substring; blank lines and lines starting with ``#`` are skipped. The
list is never committed, and a finding never prints the word: it names the word's line in the list (``#3``), and a
path or ref name is printed with each ``/``-separated part that holds a word in any reading replaced by ``***``, or as
``***`` whole when a word spans parts; nothing of such a part is printed, so no word shows with a letter dropped.

The scan fails closed: without the variable, the file or a word in it, or when git or the file system fails, it
cannot check, and says so with exit status 2, which blocks like a finding (exit status 1). That message names no
command and no path, since either could hold a word.

Usage:
    private_word_scan.py --files FILE... paths and contents of the given files (the pre-commit stage; without
                                         ``--files``, the arguments are taken as files too unless they are exactly
                                         one of the modes below)
    private_word_scan.py --message FILE  a commit message (the commit-msg stage) as git stores it
    private_word_scan.py --refs          every local ref's name, and every object a ref that names no commit (a tag,
                                         or a ref to a blob or tree) brings that no remote-tracking ref holds: tags
                                         it wraps, the commits, trees and blobs under it, and every whole path in
                                         them (the pre-commit and post-checkout stages: a push that only adds a ref
                                         brings no commit, so pre-commit runs no pre-push hook for it; a ref to a
                                         commit brings a new commit, and with it the pre-push hook, or no object)
    private_word_scan.py --history       everything a push can publish: every ref's name and the remote ref the push
                                         writes (pre-commit's PRE_COMMIT_REMOTE_BRANCH), every annotated tag, every
                                         object in the repository, reachable or not, and every whole path in the
                                         tree of each commit and of each tree a ref or tag names. A push can send
                                         any object, and pre-commit names one pushed ref to the hook, so the store
                                         is scanned whole.

Every text the scan reads (a file's contents, path or link target, an object, a path in a tree, a ref name, a tag, a
commit message file) is read from its bytes as UTF-8 (or by its byte order mark) and also in the encodings
``other_readings`` tries, so a Latin-1 or UTF-16 word is found in any of them; after a byte order mark, the bytes
that follow it are read in all of them too, since a mark need not match the text behind it. Only the word list is
read as UTF-8 alone. Objects are read as stored: ``git replace`` is ignored, since a push sends the originals.

A leftover object holding a word (a dropped commit, a reflog entry) therefore blocks every push until it is gone:
``git reflog expire --expire-unreachable=now --all`` and then ``git gc --prune=now`` drop the unreachable ones. A
stash is held by ``refs/stash``, so drop it first (``git stash drop``).

Not covered: ``git branch NAME`` or ``git tag`` followed directly by a push with no commit or checkout between, and a
push naming a remote ref that no local ref holds (``git push origin HEAD:refs/heads/NAME``) that brings no new commit,
since no hook runs for either; and the remote names of the other refs in one push, which pre-commit does not pass to
the hook (their objects are still scanned).

The commit message is cut and stripped only where git's own commit template shows git will, and only in English: git
must run with an English ``LC_MESSAGES`` for its template lines to be recognised. Under another locale the scan reads
more than git keeps, which can wrongly block a commit (a delete of a word-named file) but never lets a word through.
A known gap of the commit-msg hook: a ``-m``/``-F`` message that itself holds git's template lines (the scissors line
followed by its warning line, or the editor hint as a whole line) is cut or stripped by the scan, while git stores it
as given, so a word after those lines passes the commit-msg hook. The pre-push scan reads the stored message and
refuses it.
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
    """The same bytes read in the encodings ``decode`` does not try: UTF-16 at both alignments (UTF-16LE from the
    second byte reads UTF-16BE text), and cp1252 when the bytes are not UTF-8. After a byte order mark, which a file
    may carry in front of text in another encoding, the bytes that follow it are read so too, and as UTF-8."""
    for bom, _ in _BOMS:
        if data.startswith(bom):
            rest = data[len(bom) :]
            return [rest.decode('utf-8', errors='ignore'), *other_readings(rest)]
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


def text_hits(data: bytes, words: Words) -> list[int]:
    """The list lines of the words the bytes hold in any reading."""
    return sorted({*hits(decode(data), words), *content_hits(data, words)})


def redact(name: bytes, words: Words) -> str:
    """A path or ref name, as raw bytes, with every ``/``-separated part that holds a private word in any reading
    replaced by ``***``.

    The test is ``text_hits``, the one that finds the word, so whatever is found is hidden, and nothing of a part a
    reading other than UTF-8 finds a word in is printed (``decode`` would print the word less its dropped letters). A
    word that spans parts (one holding a ``/``) hides the whole name. What is printed is UTF-8 with every other byte
    escaped (``\\x9f``), so a byte in an encoding the scan does not read is shown, not dropped.
    """
    parts = [b'***' if text_hits(part, words) else part for part in name.split(b'/')]
    joined = b'/'.join(parts)
    return '***' if text_hits(joined, words) else joined.decode('utf-8', errors='backslashreplace')


def report(where: str, numbers: Iterable[int]) -> str:
    return f'{where}: private word #{", #".join(map(str, numbers))}'


def scan_files(names: list[str], words: Words) -> list[str]:
    findings: list[str] = []
    for name in names:
        raw = os.fsencode(name)  # the name's bytes, as git stores them, whatever their encoding
        shown = redact(raw, words)
        if found := text_hits(raw, words):
            findings.append(report(f'{shown} (path)', found))
        path = Path(name)
        if path.is_symlink():  # the link's own text: its target, which is what git stores and publishes
            if found := text_hits(os.fsencode(os.readlink(path)), words):
                findings.append(report(f'{shown} (link target)', found))
            continue
        data = path.read_bytes()
        for number, line in enumerate(decode(data).splitlines(), 1):
            if found := hits(line, words):
                findings.append(report(f'{shown}:{number}', found))
        if found := content_hits(data, words):
            findings.append(report(f'{shown} (contents in another encoding)', found))
    return findings


def git(*args: str | bytes, ok: tuple[int, ...] = (0,), input: bytes | None = None) -> bytes:  # noqa: A002
    """A git command's output. Raises CalledProcessError on an unexpected exit status."""
    environment = os.environ | {'GIT_NO_REPLACE_OBJECTS': '1'}  # the objects a push sends, not their replacements
    result = subprocess.run(['git', *args], capture_output=True, check=False, env=environment, input=input)
    if result.returncode not in ok:
        raise subprocess.CalledProcessError(result.returncode, 'git')
    return result.stdout


def all_objects() -> Iterator[tuple[str, str, bytes]]:
    """Every object in the repository, reachable or not: its id, kind (blob, tree, commit or tag) and raw bytes.

    A push can send any object it is told to (``git push origin main <id>:refs/heads/x``), and pre-commit names one
    pushed ref to the hook, so the store is scanned whole rather than walked from refs."""
    return batch(git('cat-file', '--batch-all-objects', '--batch', '--unordered'))


def batch(out: bytes) -> Iterator[tuple[str, str, bytes]]:
    """The objects of ``git cat-file --batch`` output: id, kind and raw bytes."""
    at = 0
    while at < len(out):
        end = out.index(b'\n', at)
        sha, kind, size = out[at:end].split()
        start = end + 1
        at = start + int(size) + 1
        yield sha.decode(), kind.decode(), out[start : start + int(size)]


def scan_refs(words: Words) -> list[str]:
    """Every local ref's name, and every object a ref that names no commit brings that no remote-tracking ref holds.

    A ref to a commit needs no walk: a push that brings a new commit runs the pre-push hook, and one that brings none
    sends no object. A tag (on a tag, a blob, a tree or a commit) or a ref to a blob or tree can be pushed with no new
    commit, so the objects it brings are read here: a tag's tagger and message, a tag it wraps, a blob, a tree's names
    and every whole path under a tree."""
    findings: list[str] = []
    refs = git('for-each-ref', '--format=%(objecttype) %(*objecttype) %(refname)')
    for line in filter(None, refs.split(b'\n')):  # no newline in a name
        kind, peeled, ref = line.split(b' ', 2)
        shown = redact(ref, words)
        if found := text_hits(ref, words):
            findings.append(report(f'ref {shown} (name)', found))
        if kind == b'commit':
            continue
        if kind == b'tree' or peeled == b'tree':  # no commit holds the tree, so no commit shows its whole paths
            findings.extend(path_findings(shown, ref, words))
        listed = git('rev-list', '--objects', ref, '--not', '--remotes')
        ids = b''.join(entry.split(b' ', 1)[0] + b'\n' for entry in listed.splitlines())  # `<id> [<path>]`
        for sha, object_kind, data in batch(git('cat-file', '--batch', input=ids)):
            if found := text_hits(data, words):
                findings.append(report(f'{object_kind} {sha[:12]} (brought by ref {shown})', found))
            if object_kind == 'commit':
                findings.extend(path_findings(sha[:12], sha, words))
    return findings


def message_text(raw: bytes) -> bytes:
    """A commit message file's bytes, cut at git's scissors line and stripped of comment lines only where git's own
    commit template (its scissors warning line, its editor hint as a whole line) shows git will. The bytes are cut, not
    a decoded text, so every reading sees only what git keeps (a ``commit -v`` diff below the scissors line is dropped
    whatever its encoding); the template lines are matched as UTF-8, so a message file in another encoding is read
    whole. Under another locale the scan reads more than git keeps, which can wrongly block a commit but never lets a
    word through. A known gap: a ``-m``/``-F`` message that itself holds those template lines is cut or stripped here,
    while git stores it whole, so a word after them passes; the pre-push scan reads the stored message and refuses
    it. The comment character is git's ``core.commentChar``, ``#`` by default."""
    comment = git('config', '--get', 'core.commentChar', ok=(0, 1)).strip() or b'#'
    if comment == b'auto':  # git picks one the message does not start a line with; '#' unless taken
        comment = b'#'
    lines = raw.split(b'\n')
    scissors = comment + b' ------------------------ >8 ------------------------'
    warning = comment + b' Do not modify or remove the line above.'
    for number, line in enumerate(lines[:-1]):
        if line == scissors and lines[number + 1] == warning:
            lines = lines[:number]
            break
    hint = comment + b" with '" + comment + b"' will be ignored, and an empty message aborts the commit."
    if hint in lines:
        lines = [line for line in lines if not line.startswith(comment)]
    return b'\n'.join(lines)


def path_findings(where: str, tree: str | bytes, words: Words) -> list[str]:
    """Every whole path under a tree-ish: a word holding a `/` spans the trees that store its parts."""
    return [
        report(f'{where}:{redact(raw, words)} (path)', found)
        for raw in filter(None, git('ls-tree', '-r', '-z', '--name-only', tree).split(b'\0'))
        if (found := text_hits(raw, words))
    ]


def scan_history(words: Words) -> list[str]:
    findings = scan_refs(words)
    remote = os.fsencode(os.environ.get('PRE_COMMIT_REMOTE_BRANCH', ''))  # the ref a push writes; no local ref holds it
    if found := text_hits(remote, words):
        findings.append(report(f'remote ref {redact(remote, words)} (name)', found))
    for sha, kind, data in all_objects():  # a commit holds its message, author and committer; a tree its names
        if found := text_hits(data, words):
            findings.append(report(f'{kind} {sha[:12]}', found))  # an object has no single path to show
        if kind == 'commit':
            findings.extend(path_findings(sha[:12], sha, words))
    refs = git('for-each-ref', '--format=%(objecttype) %(*objecttype) %(refname)')
    for line in filter(None, refs.split(b'\n')):  # a tree a ref or tag names has no commit to reach its paths
        kind, peeled, ref = line.split(b' ', 2)
        if kind == b'tree' or (kind == b'tag' and peeled == b'tree'):
            findings.extend(path_findings(redact(ref, words), ref, words))
    return findings


def main(argv: list[str]) -> int:
    try:
        words = load_words()
        if argv[:1] == ['--files']:  # the hook's marker: whatever follows is a path, even one named like a mode
            findings = scan_files(argv[1:], words)
            scanned = f'{len(argv) - 1} file(s)'
        elif argv == ['--refs']:  # exactly, so a file named like a mode, given without the marker, stays a file
            findings = scan_refs(words)
            scanned = 'every local ref'
        elif argv == ['--history']:
            findings = scan_history(words)
            scanned = 'every object in the repository'
        elif argv[:1] == ['--message']:
            if len(argv) != 2:
                raise CouldNotCheckError('--message needs the message file')
            found = text_hits(message_text(Path(argv[1]).read_bytes()), words)
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
