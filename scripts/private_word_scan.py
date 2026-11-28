"""Refuse words that must never become public, in file paths, file contents, commit messages and git history.

The word list lives outside the repository, in the file the environment variable ``DTB_PRIVATE_WORDS`` names: one
word per line, matched as a substring after both sides are folded (NFKD, casefold, NFKD) and every run of whitespace,
line breaks included, is one space, so case, canonically or compatibly equal spellings and the wrapping of a two-word
name do not matter; blank lines and lines starting with ``#`` are skipped. The list is never committed, and a
finding never prints the word: it names the word's line in the list (``#3``), and a path or ref name is printed with
each ``/``-separated part that holds a word in any reading replaced by ``***``, or as ``***`` whole when a word spans
parts; nothing of such a part is printed, so no word shows with a letter dropped.

The scan fails closed: without the variable, a readable UTF-8 file, or a usable word in it, or when a word in it is
refused, or when git or the file system fails, or an object cannot be read, it cannot check, and says so with exit
status 2, which blocks like a finding (exit status 1). That message names no path and no command that ran, since
either could hold a word.

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
the hook (their objects are still scanned). Also not covered: escaped forms of a word (JSON ``\\uXXXX``, URL encoding,
HTML entities); words inside compressed containers (zip-based office files, PDF streams, ``.gz``); a gitlink path
at commit time, since the commit-stage hooks filter submodules out (``.gitmodules`` itself is scanned; the pre-push
history scan finds the path); the Turkish dotted and dotless I, which casefolding does not equate with ``i``; and
anything authored on GitHub itself or pushed from a clone without the hooks, since no server-side scan exists.

The commit message is cut and stripped only where git's own commit template shows git will, and only in English: git
must run with an English ``LC_MESSAGES`` for its template lines to be recognised. Under another locale the scan reads
more than git keeps, which can wrongly block a commit (a delete of a word-named file) but never lets a word through.
A known gap of the commit-msg hook: a ``-m``/``-F`` message that itself holds git's template lines (the scissors line
followed by its warning line, or the editor hint as a whole line) is cut or stripped by the scan, while git stores it
as given, so a word after those lines passes the commit-msg hook. The pre-push scan reads the stored message and
refuses it.
"""

from __future__ import annotations

import bisect
import codecs
from collections.abc import Iterable
from collections.abc import Iterator
import itertools
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata

VARIABLE = 'DTB_PRIVATE_WORDS'
FOUND = 1
COULD_NOT_CHECK = 2

type Words = list[tuple[int, str]]  # (line in the list, squeezed word)

_BOMS = [
    (codecs.BOM_UTF32_LE, 'utf-32'),
    (codecs.BOM_UTF32_BE, 'utf-32'),
    (codecs.BOM_UTF8, 'utf-8-sig'),
    (codecs.BOM_UTF16_LE, 'utf-16'),
    (codecs.BOM_UTF16_BE, 'utf-16'),
]


class CouldNotCheckError(Exception):
    """The scan cannot check: no usable word list (none readable, not UTF-8, a format, control or separator character
    (Unicode Cf, Cc, Zl, Zp), a replacement character U+FFFD or a note after a word, or no word in it), a
    ``--message`` call that does not name exactly one file, or an object could not be read. (A failing git or file
    system raises CalledProcessError or OSError, which ``main`` answers the same way.)"""


def load_words() -> Words:
    """The private words with their line numbers in the list. Raises CouldNotCheckError when there is no usable list
    or no word in it, or a word is refused (``_word``)."""
    location = os.environ.get(VARIABLE)
    if not location:
        raise CouldNotCheckError(f'{VARIABLE} is not set: set it to the path of the word list')
    try:
        path = Path(location).expanduser()  # raises RuntimeError for ``~user`` of an unknown user
    except RuntimeError:
        raise CouldNotCheckError(f'{VARIABLE} names no readable file: point it at the word list') from None
    if not path.is_file():
        raise CouldNotCheckError(f'{VARIABLE} names no readable file: point it at the word list')
    words = [
        (number, _word(number, line))
        for number, line in enumerate(_read_list(path).split('\n'), 1)  # not splitlines(), which splits at U+2028 too
        if line.strip() and not line.strip().startswith('#')
    ]
    if not words:
        raise CouldNotCheckError(f'the file {VARIABLE} names holds no word: add the private words, one per line')
    return words


def _word(number: int, line: str) -> str:
    """The squeezed word of a list line. Raises CouldNotCheckError on an invisible character inside it (Unicode category
    Cf, such as a zero-width space pasted from a web page, Cc, such as the tab of ``word<TAB>note``, or the line and
    paragraph separators Zl and Zp): a word holding one misses the text typed without it, so the list is refused for
    its owner to fix, not guessed at. The message names the line and never the word. A word that needs U+200C or
    U+200D (Persian, Indic) is refused too, so it cannot be listed; the default-ignorable characters outside these
    categories (variation selectors) are not checked.

    It also refuses U+FFFD, which a list in another encoding holds where each of its non-ASCII letters was once it is
    opened as UTF-8 and saved again: the word would match only text that lost the same letters, never the word as
    typed. And it refuses whitespace followed by ``#`` (``word  # note``), a note the list does not support: it would
    load as one word that never matches. So a word cannot hold whitespace and then ``#``; a phrase with plain spaces
    and a ``#`` inside a word are fine."""
    word = line.strip()
    if re.search(r'\s#', fold(word)):  # first: a note after a tab gets the note's fix, which keeps the note
        raise CouldNotCheckError(
            f'word list line {number} holds a note after the word: put the note on its own line, starting with #'
        )
    if any(unicodedata.category(character) in {'Cf', 'Cc', 'Zl', 'Zp'} for character in word):
        raise CouldNotCheckError(
            f'word list line {number} holds an invisible character: retype the word alone on its line'
        )
    if '\ufffd' in word:
        raise CouldNotCheckError(
            f'word list line {number} holds a replacement character (a letter lost when the list was converted to '
            'UTF-8): retype the word'
        )
    return squeeze(word)


def _read_list(path: Path) -> str:
    """The word list's text. Raises CouldNotCheckError when the file cannot be read, or is not UTF-8.

    A NUL counts as not UTF-8: ASCII text saved as UTF-16 with no byte order mark is valid UTF-8 with a NUL between
    its letters. ``_word`` would refuse it too, but would tell the owner to retype a word instead of saving the file
    as UTF-8."""
    not_utf8 = CouldNotCheckError(f'the file {VARIABLE} names is not UTF-8: save it as UTF-8')
    try:
        # a byte order mark that starts a line is a file artifact (lists joined with ``cat``); one inside a line is left
        # for ``_word`` to refuse, since dropping it would join two words into one that matches neither
        text = path.read_text(encoding='utf-8').replace('\n\ufeff', '\n').removeprefix('\ufeff')
    except UnicodeDecodeError:
        raise not_utf8 from None
    except OSError:
        raise CouldNotCheckError('the word list cannot be read: check its permissions') from None
    if '\0' in text:
        raise not_utf8
    return text


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


def fold(text: str) -> str:
    """Text in the one form words are compared in: NFKD, casefolded, then NFKD again, since Unicode does not promise
    that casefolding keeps NFKD. Canonically or compatibly equal spellings (a decomposed `ü`, fullwidth letters) then
    compare equal, and the form is decomposed so that a combining mark after a word cannot merge with its last
    letter."""
    # Defensive: on Python 3.12-3.15 (Unicode 15.0-17.0) casefolding NFKD text always gives NFKD, so dropping the outer
    # NFKD changes nothing today and that mutant is equivalent.
    return unicodedata.normalize('NFKD', unicodedata.normalize('NFKD', text).casefold())


def squeeze(text: str) -> str:
    """The text folded, with every run of whitespace (spaces, tabs, line breaks) turned into one space and none at
    the ends, so a private name written in two words matches however a document wraps or spaces it."""
    return ' '.join(fold(text).split())


def hits(text: str, words: Words) -> list[int]:
    """The list lines of the words ``text`` contains."""
    squeezed = squeeze(text)
    return [number for number, word in words if word in squeezed]


def line_hits(text: str, words: Words) -> dict[int, set[int]]:
    """The list lines of the words ``text`` contains, by the line of ``text`` each match starts on, so a phrase whose
    words a line break separates is found and reported at the line it starts on. Lines are counted at newlines only,
    as git and editors count them, not at the form feed or U+2028 that ``str.splitlines()`` also breaks at."""
    kept = [(number, piece) for number, line in enumerate(text.split('\n'), 1) if (piece := squeeze(line))]
    squeezed = ' '.join(piece for _, piece in kept)
    starts = list(itertools.accumulate((len(piece) + 1 for _, piece in kept), initial=0))
    found: dict[int, set[int]] = {}
    for number, word in words:
        for at in _occurrences(squeezed, word):
            found.setdefault(kept[bisect.bisect_right(starts, at) - 1][0], set()).add(number)
    return found


def _occurrences(text: str, word: str) -> Iterator[int]:
    """The offsets ``word`` starts at in ``text``."""
    at = text.find(word)
    while at >= 0:
        yield at
        at = text.find(word, at + 1)


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
    return [finding for name in names for finding in _file_findings(name, words)]


def _file_findings(name: str, words: Words) -> list[str]:
    """What one file's path, and its contents or link target, hold."""
    findings: list[str] = []
    raw = os.fsencode(name)  # the name's bytes, as git stores them, whatever their encoding
    shown = redact(raw, words)
    if found := text_hits(raw, words):
        findings.append(report(f'{shown} (path)', found))
    path = Path(name)
    if path.is_symlink():  # the link's own text: its target, which is what git stores and publishes
        if found := text_hits(os.fsencode(os.readlink(path)), words):
            findings.append(report(f'{shown} (link target)', found))
        return findings
    return findings + _content_findings(shown, path.read_bytes(), words)


def _content_findings(shown: str, data: bytes, words: Words) -> list[str]:
    """What a file's bytes hold as ``decode`` reads them, by the line each match starts on, then in the other
    encodings."""
    findings: list[str] = []
    for number, found in sorted(line_hits(decode(data), words).items()):
        findings.append(report(f'{shown}:{number}', sorted(found)))
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
    """The objects of ``git cat-file --batch`` output: id, kind and raw bytes.

    Raises CouldNotCheckError on ``<id> missing``: an object listed a moment ago was pruned (a ``gc`` running beside
    a push), or is corrupt (git exits 0 and prints ``missing`` for an object whose header it cannot unpack), so the
    scan cannot say it read every object. It does the same when a header is not ``<id> <kind> <size>`` with a size of
    digits, or the byte after the declared size is not the newline git writes (a corrupt object whose size is wrong):
    reading on would take the next bytes for a header and skip an object, or, at the end of the output, pass an
    object git could not read whole. A wrong size whose declared end falls on a newline passes when that newline ends
    the output or comes before a line that is a valid header. The id and the kind are git's own and are not checked."""
    at = 0
    while at < len(out):
        end = out.index(b'\n', at)
        parts = out[at:end].split()
        if not _is_header(parts):
            raise _unreadable()
        sha, kind, size = parts
        start = end + 1
        stop = start + int(size)
        if out[stop : stop + 1] != b'\n':
            raise _unreadable()
        at = stop + 1
        yield sha.decode(), kind.decode(), out[start:stop]


def _is_header(parts: list[bytes]) -> bool:
    """Whether the fields are the ``<id> <kind> <size>`` git prints before an object, with a size of digits."""
    return len(parts) == 3 and parts[2].isdigit()


def _unreadable() -> CouldNotCheckError:
    return CouldNotCheckError(
        'an object could not be read (pruned while the scan ran, or corrupt): run it again, and if it repeats, '
        'check the repository with git fsck'
    )


def scan_refs(words: Words) -> list[str]:
    """Every local ref's name, and every object a ref that names no commit brings that no remote-tracking ref holds.

    A ref to a commit needs no walk: a push that brings a new commit runs the pre-push hook, and one that brings none
    sends no object. A tag (on a tag, a blob, a tree or a commit) or a ref to a blob or tree can be pushed with no new
    commit, so the objects it brings are read here: a tag's tagger and message, a tag it wraps, a blob, a tree's names
    and every whole path under a tree."""
    refs = git('for-each-ref', '--format=%(objecttype) %(*objecttype) %(refname)')
    lines = filter(None, refs.split(b'\n'))  # no newline in a name
    return [finding for line in lines for finding in _ref_findings(line, words)]


def _ref_findings(line: bytes, words: Words) -> list[str]:
    """What one ref holds: its name, and unless it names a commit, the objects it brings and, when it names or
    peels to a tree, the whole paths under that tree."""
    findings: list[str] = []
    kind, peeled, ref = line.split(b' ', 2)
    shown = redact(ref, words)
    if found := text_hits(ref, words):
        findings.append(report(f'ref {shown} (name)', found))
    if kind == b'commit':
        return findings
    if kind == b'tree' or peeled == b'tree':  # no commit holds the tree, so no commit shows its whole paths
        findings.extend(path_findings(shown, ref, words))
    return findings + _brought_findings(ref, shown, words)


def _brought_findings(ref: bytes, shown: str, words: Words) -> list[str]:
    """What the objects that ``ref`` brings and no remote-tracking ref holds contain, and the whole paths of each such
    commit."""
    findings: list[str] = []
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
    return list(dict.fromkeys(findings))  # a commit a tag brings is also in the store: report it once


def _scan(argv: list[str], words: Words) -> tuple[list[str], str]:
    """The findings of the mode ``argv`` asks for, and what was scanned. Raises what ``main`` turns into "could not
    check": CouldNotCheckError, and the CalledProcessError and OSError of git and the file system."""
    if argv[:1] == ['--files']:  # the hook's marker: whatever follows is a path, even one named like a mode
        return scan_files(argv[1:], words), f'{len(argv) - 1} file(s)'
    if argv == ['--refs']:  # exactly, so a file named like a mode, given without the marker, stays a file
        return scan_refs(words), 'every local ref'
    if argv == ['--history']:
        return scan_history(words), 'every object in the repository'
    if argv[:1] == ['--message']:
        return _message_scan(argv, words), 'the commit message'
    return scan_files(argv, words), f'{len(argv)} file(s)'


def _message_scan(argv: list[str], words: Words) -> list[str]:
    """The findings of a commit message file. Raises CouldNotCheckError unless ``argv`` names exactly one file.

    Reading the file and running git can fail too, as ``_scan`` says.
    """
    if len(argv) != 2:
        raise CouldNotCheckError('--message needs exactly one message file')
    found = text_hits(message_text(Path(argv[1]).read_bytes()), words)
    return [report('commit message', found)] if found else []


def main(argv: list[str]) -> int:
    try:
        findings, scanned = _scan(argv, load_words())
    except CouldNotCheckError as error:
        print(f'private-word scan could not check: {error}', file=sys.stderr)
        return COULD_NOT_CHECK
    except (subprocess.CalledProcessError, OSError):  # not the command that ran or a path: either could hold a word
        print(
            'private-word scan could not check: git or the file system failed: check that git runs here (git fsck '
            'in the repository) and that the word list and every file given to the scan can be read',
            file=sys.stderr,
        )
        return COULD_NOT_CHECK
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        return FOUND
    print(f'private-word scan: {scanned} clean')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
