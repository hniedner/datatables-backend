"""The private-word scan, run as the pre-commit hooks and the history check run it."""

from __future__ import annotations

import codecs
from collections.abc import Callable
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unicodedata

import pytest
import yaml

SCRIPT = Path(__file__).with_name('private_word_scan.py')
WORD = 'Zebrafinch'  # planted; the scan must find it in any case and never print it
SECOND = 'Otterbrook'  # the list's second word, on line 5


def scan(
    *args: str, words: Path | None, cwd: Path | None = None, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != 'DTB_PRIVATE_WORDS'} | (extra or {})
    if words is not None:
        env['DTB_PRIVATE_WORDS'] = str(words)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args], env=env, cwd=cwd, capture_output=True, text=True, check=False
    )
    # Python exits 1 on an uncaught exception, the status of a finding: a crash must not pass a test of a refusal
    assert 'Traceback' not in result.stderr, result.stderr
    return result


@pytest.fixture
def words(tmp_path: Path) -> Path:
    path = tmp_path / 'words.txt'
    path.write_text('# private words\n\nzEBRAfINCH\n# another\nOtterbrook\n', encoding='utf-8')  # WORD on line 3
    return path


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding='utf-8')
    return path


def test_a_planted_word_in_a_file_fails_and_is_never_printed(tmp_path: Path, words: Path):
    planted = write(tmp_path / 'notes.md', f'line one\nsee {WORD.upper()} here\n')
    result = scan(str(planted), words=words)
    assert result.returncode == 1
    assert f'{planted}:2: private word #3' in result.stderr
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_planted_word_in_a_path_fails_and_the_path_is_redacted(tmp_path: Path, words: Path):
    planted = write(tmp_path / f'{WORD}.txt', f'{WORD}\n')
    result = scan(str(planted), words=words)
    assert result.returncode == 1
    assert '/*** (path): private word #3' in result.stderr  # the whole path part holding the word
    assert '/***:1: private word #3' in result.stderr
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_clean_files_pass(tmp_path: Path, words: Path):
    result = scan(str(write(tmp_path / 'clean.md', 'nothing here\n')), words=words)
    assert result.returncode == 0, result.stderr


def test_a_planted_word_in_a_commit_message_fails(tmp_path: Path, words: Path):
    message = write(tmp_path / 'COMMIT_EDITMSG', f'Port the {WORD} cases\n')
    result = scan('--message', str(message), words=words)
    assert result.returncode == 1
    assert 'commit message: private word #3' in result.stderr
    assert scan('--message', str(write(tmp_path / 'clean', 'Fix a test\n')), words=words).returncode == 0


@pytest.mark.parametrize('case', ['unset', 'missing file', 'no words'])
def test_the_scan_fails_closed_without_a_word_list(tmp_path: Path, case: str):
    clean = write(tmp_path / 'clean.md', 'nothing here\n')
    word_file = {
        'unset': None,
        'missing file': tmp_path / 'absent.txt',
        'no words': write(tmp_path / 'empty.txt', '# only a comment\n\n'),
    }[case]
    result = scan(str(clean), words=word_file)
    assert result.returncode == 2
    assert 'could not check' in result.stderr


def git(repo: Path, *args: str, env: dict[str, str] | None = None, input: str | None = None) -> str:  # noqa: A002
    return subprocess.run(
        ['git', '-C', str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        input=input,
        env={**os.environ, **(env or {})},
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'user.email', 'test@example.org')
    git(repo, 'config', 'user.name', 'Test')
    write(repo / 'a.txt', 'clean\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-q', '-m', 'First')
    return repo


def test_a_clean_history_passes(repo: Path, words: Path):
    result = scan('--history', words=words, cwd=repo)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('where', ['contents', 'path', 'message', 'other branch'])
def test_a_planted_word_anywhere_in_history_fails(repo: Path, words: Path, where: str):
    if where == 'other branch':
        git(repo, 'switch', '-q', '-c', 'side')
    name = f'{WORD}.txt' if where == 'path' else 'b.txt'
    write(repo / name, f'{WORD}\n' if where in ('contents', 'other branch') else 'clean\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-q', '-m', f'Add {WORD}' if where == 'message' else 'Add b')
    if where == 'other branch':
        git(repo, 'switch', '-q', 'main')
    result = scan('--history', words=words, cwd=repo)
    assert result.returncode == 1
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_utf16_file_is_read_by_its_byte_order_mark(tmp_path: Path, words: Path):
    planted = tmp_path / 'notes.txt'
    planted.write_bytes(f'see {WORD} here\n'.encode('utf-16'))
    result = scan(str(planted), words=words)
    assert result.returncode == 1
    assert f'{planted}:1: private word #3' in result.stderr  # read by the mark, not only as a second reading


def test_a_message_scan_without_a_file_cannot_check(words: Path):
    result = scan('--message', words=words)
    assert result.returncode == 2
    assert 'could not check' in result.stderr


def test_a_scan_of_no_file_still_needs_the_word_list():
    result = scan(words=None)
    assert result.returncode == 2
    assert 'could not check' in result.stderr


def test_a_message_scan_of_two_files_cannot_check(tmp_path: Path, words: Path):
    first = write(tmp_path / 'first.txt', 'A clean message\n')
    second = write(tmp_path / 'second.txt', f'A message with {WORD}\n')
    result = scan('--message', str(first), str(second), words=words)
    assert result.returncode == 2
    assert 'could not check' in result.stderr


def test_an_indented_comment_line_of_a_message_is_kept_as_git_keeps_it(repo: Path, words: Path):
    """Git strips only a line that starts with `#`; `  # word` stays in the message. The editor's hint line is what
    shows git will strip the comments, so the scan strips them too."""
    hint = "# with '#' will be ignored, and an empty message aborts the commit."
    message = write(repo / 'COMMIT_EDITMSG', f'Add a file\n\n  # {WORD}\n{hint}\n')
    result = scan('--message', str(message), words=words, cwd=repo, extra={'GIT_CONFIG_GLOBAL': os.devnull})
    assert result.returncode == 1, result.stderr
    assert 'private word #' in result.stderr


def test_a_cp1252_word_is_found_where_latin_1_reads_a_control_character(tmp_path: Path):
    """`Š` is byte 0x8A in cp1252, a control character in Latin-1, and no UTF-8 at all."""
    listed = write(tmp_path / 'words.txt', 'Šumava\n')
    planted = tmp_path / 'notes.txt'
    planted.write_bytes('see Šumava here\n'.encode('cp1252'))
    result = scan(str(planted), words=listed)
    assert result.returncode == 1, result.stderr
    assert 'private word #' in result.stderr


def test_a_broken_git_config_cannot_check_a_message(tmp_path: Path, words: Path):
    broken = write(tmp_path / 'gitconfig', '[core\n')
    message = write(tmp_path / 'message.txt', 'A clean message\n')
    result = scan('--message', str(message), words=words, extra={'GIT_CONFIG_GLOBAL': str(broken)})
    assert result.returncode == 2
    assert 'could not check' in result.stderr


def test_a_ref_whose_objects_cannot_be_listed_cannot_check(repo: Path, words: Path):
    git(repo, 'tag', 'tree-tag', 'HEAD^{tree}')
    blob = git(repo, 'rev-parse', 'HEAD:a.txt').strip()
    (repo / '.git' / 'objects' / blob[:2] / blob[2:]).chmod(0o644)
    (repo / '.git' / 'objects' / blob[:2] / blob[2:]).unlink()  # the tree names a blob that is gone
    result = scan('--refs', words=words, cwd=repo)
    assert result.returncode == 2
    assert 'could not check' in result.stderr


def test_a_history_scan_outside_a_repository_cannot_check(tmp_path: Path, words: Path):
    outside = tmp_path / 'not-a-repo'
    outside.mkdir()
    result = scan('--history', words=words, cwd=outside)
    assert result.returncode == 2
    assert 'could not check' in result.stderr


def commit(
    repo: Path, name: str, data: bytes, message: str = 'Add a file', *, env: dict[str, str] | None = None
) -> None:
    (repo / name).write_bytes(data)
    git(repo, 'add', '.')
    git(repo, 'commit', '-q', '-m', message, env=env)


# Where the word is planted in each case. 'non-ascii path' plants it in the path only, and 'author' and 'committer'
# each leave the other one clean.
_PLANTERS: dict[str, Callable[[Path], object]] = {
    'non-ascii path': lambda repo: commit(repo, f'über-{WORD}.txt', b'clean\n'),
    'path and contents': lambda repo: commit(repo, f'{WORD}.txt', f'{WORD}\n'.encode()),
    'binary blob': lambda repo: commit(repo, 'blob.bin', b'\x00\x01' + WORD.encode() + b'\x00'),
    'ref name': lambda repo: git(repo, 'branch', f'topic-{WORD}'),
    'tag message': lambda repo: git(repo, 'tag', '-a', 'v0', '-m', f'Release for {WORD}'),
    'author': lambda repo: commit(repo, 'b.txt', b'clean\n', env={'GIT_AUTHOR_NAME': f'{WORD} Person'}),
    'committer': lambda repo: commit(repo, 'b.txt', b'clean\n', env={'GIT_COMMITTER_NAME': f'{WORD} Person'}),
    'author email': lambda repo: commit(
        repo, 'b.txt', b'clean\n', env={'GIT_AUTHOR_EMAIL': f'{WORD.lower()}@example.org'}
    ),
    'committer email': lambda repo: commit(
        repo, 'b.txt', b'clean\n', env={'GIT_COMMITTER_EMAIL': f'{WORD.lower()}@example.org'}
    ),
}


@pytest.mark.parametrize('where', list(_PLANTERS))
def test_history_finds_what_gets_published_and_never_prints_it(repo: Path, words: Path, where: str):
    _PLANTERS[where](repo)
    result = scan('--history', words=words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


FOLDED = 'GÄRTNERSTRASSE'  # a list entry that casefolds to the same text as the planted spelling below
PLANTED = 'Gärtnerstraße'  # casefold: 'gärtnerstrasse'; a case-insensitive regex of FOLDED does not match it


@pytest.fixture
def folded_words(tmp_path: Path) -> Path:
    return write(tmp_path / 'folded.txt', f'{FOLDED}\n')


def never_printed(result: subprocess.CompletedProcess[str]) -> bool:
    return FOLDED.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_casefolded_word_in_a_path_is_found_and_redacted(tmp_path: Path, folded_words: Path):
    planted = write(tmp_path / f'old-{PLANTED}.txt', 'clean\n')
    result = scan(str(planted), words=folded_words)
    assert result.returncode == 1
    assert never_printed(result), result.stderr


@pytest.mark.parametrize('where', ['path', 'ref name', 'contents'])
def test_a_casefolded_word_in_history_is_found_and_redacted(repo: Path, folded_words: Path, where: str):
    if where == 'path':
        commit(repo, f'old-{PLANTED}.txt', b'clean\n')
    elif where == 'ref name':
        git(repo, 'branch', f'topic-{PLANTED}')
    else:
        commit(repo, 'notes.txt', f'see {PLANTED}\n'.encode())
    result = scan('--history', words=folded_words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert never_printed(result), result.stderr


def test_a_symlink_target_is_scanned(tmp_path: Path, words: Path):
    link = tmp_path / 'link'
    link.symlink_to(f'../{WORD}/target')
    result = scan(str(link), words=words)
    assert result.returncode == 1
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_link_named_with_a_word_and_aimed_at_one_never_prints_either(tmp_path: Path, words: Path):
    link = tmp_path / f'{WORD}-link'
    link.symlink_to(f'../{WORD}/target')
    result = scan(str(link), words=words)
    assert result.returncode == 1
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_symlink_target_in_latin_1_is_found(tmp_path: Path):
    latin_1_words = write(tmp_path / 'latin1-words.txt', 'Grünspecht\n')
    link = tmp_path / 'link'
    os.symlink(b'../Gr\xfcnspecht/target', link)  # the target's bytes are Latin-1, which is no UTF-8
    result = scan(str(link), words=latin_1_words)
    assert result.returncode == 1
    assert 'grünspecht' not in (result.stdout + result.stderr).casefold()


def test_a_file_that_is_not_the_last_one_given_is_scanned(tmp_path: Path, words: Path):
    planted = write(tmp_path / 'planted.md', f'see {WORD} here\n')
    clean = write(tmp_path / 'clean.md', 'nothing\n')
    assert scan(str(planted), str(clean), words=words).returncode == 1


def test_a_symlink_to_a_file_that_exists_is_scanned_as_a_link(tmp_path: Path, words: Path):
    (tmp_path / f'{WORD}-notes').mkdir()
    write(tmp_path / f'{WORD}-notes' / 'target', 'clean\n')
    link = tmp_path / 'link'
    link.symlink_to(f'{WORD}-notes/target')  # the target exists and holds no word; only its path has one
    result = scan(str(link), words=words)
    assert result.returncode == 1
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_word_list_that_is_missing_is_never_named(tmp_path: Path):
    result = scan('--files', words=tmp_path / f'{WORD}.txt')  # the path holds a word, and no file is there
    assert result.returncode == 2
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


@pytest.mark.parametrize('namespace', ['refs/notes', 'refs/remotes/origin'])
def test_a_ref_named_with_a_word_outside_heads_and_tags_is_found(repo: Path, words: Path, namespace: str):
    git(repo, 'update-ref', f'{namespace}/{WORD}', 'HEAD')
    result = scan('--refs', words=words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert 'private word #' in result.stderr
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_word_is_folded_with_its_compatibility_form_first(tmp_path: Path):
    """U+03D2 decomposes to a capital upsilon, which casefolds to the lower-case one the text holds."""
    listed = write(tmp_path / 'words.txt', '\u03d2x\n')
    planted = write(tmp_path / 'notes.md', 'see \u03c5x here\n')
    result = scan(str(planted), words=listed)
    assert result.returncode == 1, result.stderr
    assert 'private word #' in result.stderr


def test_a_tag_on_a_tree_named_with_a_word_is_found_and_never_printed(repo: Path, words: Path):
    commit(repo, f'{WORD}.txt', b'clean\n')
    git(repo, 'tag', f'tree-{WORD}', 'HEAD^{tree}')
    result = scan('--refs', words=words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert '/***:*** (path)' in result.stderr  # the whole path under the tree is found, not only the tag's name
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_blob_that_ends_in_the_word_is_found(repo: Path, words: Path):
    commit(repo, 'b.txt', WORD.encode())  # no byte after the word
    assert scan('--history', words=words, cwd=repo).returncode == 1


def test_a_symlink_named_with_a_word_is_found_whatever_its_target(tmp_path: Path, words: Path):
    link = tmp_path / f'{WORD}-link'
    link.symlink_to('clean-target')
    result = scan(str(link), words=words)
    assert result.returncode == 1
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_list_word_with_spaces_around_it_still_matches(tmp_path: Path):
    padded = write(tmp_path / 'padded.txt', f'  {WORD}  \n')
    planted = write(tmp_path / 'notes.md', f'see {WORD} here\n')
    assert scan(str(planted), words=padded).returncode == 1


def test_a_symlink_target_in_history_is_scanned(repo: Path, words: Path):
    (repo / 'link').symlink_to(f'../{WORD}/target')
    git(repo, 'add', '.')
    git(repo, 'commit', '-q', '-m', 'Add a link')
    assert scan('--history', words=words, cwd=repo).returncode == 1


def test_a_missing_message_file_cannot_check_and_is_not_printed(tmp_path: Path, words: Path):
    result = scan('--message', str(tmp_path / f'missing-{WORD}.txt'), words=words)
    assert result.returncode == 2
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_word_list_that_is_not_utf8_cannot_check(tmp_path: Path):
    latin1 = tmp_path / 'latin1.txt'
    latin1.write_bytes('Gärtner\n'.encode('latin-1'))
    result = scan(str(write(tmp_path / 'clean.md', 'clean\n')), words=latin1)
    assert result.returncode == 2
    assert 'not UTF-8' in result.stderr


@pytest.mark.parametrize('where', ['branch', 'tag name', 'tag message'])
def test_a_word_in_a_local_ref_fails_every_commit_and_is_never_printed(repo: Path, words: Path, where: str):
    """The commit-stage check: a ref-only push brings no commit, so the pre-push hooks never run for it."""
    assert scan('--refs', words=words, cwd=repo).returncode == 0
    if where == 'branch':
        git(repo, 'branch', f'topic-{WORD}')
    elif where == 'tag name':
        git(repo, 'tag', f'v-{WORD}')
    else:
        git(repo, 'tag', '-a', 'v0', '-m', f'Release for {WORD}')
    result = scan('--refs', words=words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_the_ref_scan_reads_no_history(repo: Path, words: Path):
    commit(repo, 'b.txt', f'{WORD}\n'.encode(), message=f'Add {WORD}')
    assert scan('--refs', words=words, cwd=repo).returncode == 0


def test_a_finding_names_the_line_of_the_word_in_the_list(tmp_path: Path, words: Path):
    planted = write(tmp_path / 'notes.md', f'see {SECOND} here\n')
    result = scan(str(planted), words=words)
    assert result.returncode == 1
    assert f'{planted}:1: private word #5' in result.stderr
    assert SECOND.casefold() not in (result.stdout + result.stderr).casefold()


SLASHED = 'Acme/Widget'  # a list word holding the path separator


@pytest.mark.parametrize('where', ['path', 'ref name', 'history path'])
def test_a_word_holding_a_slash_is_never_printed(tmp_path: Path, repo: Path, where: str):
    slashed_words = write(tmp_path / 'slashed.txt', f'{SLASHED.lower()}\n')
    if where == 'path':
        (tmp_path / 'Acme').mkdir()
        result = scan(str(write(tmp_path / 'Acme' / 'Widget.txt', 'clean\n')), words=slashed_words)
    elif where == 'ref name':
        git(repo, 'branch', SLASHED)
        result = scan('--refs', words=slashed_words, cwd=repo)
    else:
        (repo / 'Acme').mkdir()
        commit(repo, 'Acme/Widget.txt', b'clean\n')
        result = scan('--history', words=slashed_words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert SLASHED.casefold() not in (result.stdout + result.stderr).casefold(), result.stderr


def test_a_blob_reachable_only_through_a_tag_is_scanned(repo: Path, words: Path):
    (repo / 'loose.txt').write_text(f'{WORD}\n', encoding='utf-8')
    blob = git(repo, 'hash-object', '-w', 'loose.txt').strip()
    (repo / 'loose.txt').unlink()
    git(repo, 'tag', 'just-a-blob', blob)
    result = scan('--history', words=words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


@pytest.mark.skipif(os.name != 'posix' or os.geteuid() == 0, reason='root reads any file')
def test_an_unreadable_file_cannot_check(tmp_path: Path, words: Path):
    locked = write(tmp_path / 'locked.md', 'clean\n')
    locked.chmod(0)
    try:
        result = scan(str(locked), words=words)
    finally:
        locked.chmod(0o600)
    assert result.returncode == 2
    assert 'could not check' in result.stderr


def test_a_utf32_file_is_read_by_its_byte_order_mark(tmp_path: Path, words: Path):
    planted = tmp_path / 'notes.txt'
    planted.write_bytes(f'see {WORD} here\n'.encode('utf-32'))
    assert scan(str(planted), words=words).returncode == 1


def test_a_utf32_blob_in_history_is_found(repo: Path, words: Path):
    """No other reading covers UTF-32, so only the byte order mark finds the word in a blob."""
    commit(repo, 'notes.txt', f'see {WORD} here\n'.encode('utf-32'))
    assert scan('--history', words=words, cwd=repo).returncode == 1


def test_a_word_list_saved_with_a_byte_order_mark_keeps_its_first_word(tmp_path: Path):
    listed = tmp_path / 'bom.txt'
    listed.write_bytes(f'{WORD}\n'.encode('utf-8-sig'))
    result = scan(str(write(tmp_path / 'notes.md', f'see {WORD}\n')), words=listed)
    assert result.returncode == 1, result.stdout


@pytest.mark.parametrize('where', ['message', 'author'])
def test_history_reads_commits_on_every_branch(repo: Path, words: Path, where: str):
    """The word is only in the side branch's commit, not in any file, so only the commit scan can find it."""
    git(repo, 'switch', '-q', '-c', 'side')
    if where == 'message':
        commit(repo, 'b.txt', b'clean\n', message=f'Add {WORD}')
    else:
        commit(repo, 'b.txt', b'clean\n', env={'GIT_AUTHOR_NAME': f'{WORD} Person'})
    git(repo, 'switch', '-q', 'main')
    result = scan('--history', words=words, cwd=repo)
    assert result.returncode == 1, result.stderr


@pytest.mark.parametrize(
    ('word', 'data'),
    [
        ('Grünkohl', 'see grünkohl here\n'.encode('cp1252')),
        (WORD, f'see {WORD} here\n'.encode('utf-16-le')),
        (WORD, f'see {WORD} here\n'.encode('utf-16-be')),
    ],
    ids=['cp1252', 'utf-16-le without bom', 'utf-16-be without bom'],
)
def test_a_file_in_another_encoding_without_a_byte_order_mark_is_read(tmp_path: Path, word: str, data: bytes):
    listed = write(tmp_path / 'list.txt', f'{word}\n')
    planted = tmp_path / 'notes.txt'
    planted.write_bytes(data)
    result = scan(str(planted), words=listed)
    assert result.returncode == 1, result.stdout
    assert word.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_blob_in_another_encoding_without_a_byte_order_mark_is_read(repo: Path, words: Path):
    commit(repo, 'notes.txt', f'see {WORD} here\n'.encode('utf-16-le'))
    assert scan('--history', words=words, cwd=repo).returncode == 1


SCISSORS = '# ------------------------ >8 ------------------------'


def editor_hint(comment: str = '#') -> str:
    """The hint git writes into a message it opens in an editor; only then does it strip comment lines."""
    return (
        f'{comment} Please enter the commit message for your changes. Lines starting\n'
        f"{comment} with '{comment}' will be ignored, and an empty message aborts the commit.\n"
    )


@pytest.mark.parametrize(
    'template',
    [
        f'Remove a leaked file\n\n{editor_hint()}#\n# Changes to be committed:\n#\tdeleted:    {WORD}.txt\n',
        f'Drop a line\n{SCISSORS}\n# Do not modify or remove the line above.\ndiff --git a/a.txt b/a.txt\n-{WORD}\n',
    ],
    ids=['status comment', 'verbose diff'],
)
def test_git_s_own_lines_in_a_message_file_are_not_the_message(tmp_path: Path, words: Path, template: str):
    result = scan('--message', str(write(tmp_path / 'COMMIT_EDITMSG', template)), words=words)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    'template',
    [
        f'Fix\n{SCISSORS}\n{WORD}\n# Do not modify or remove the line above.\n',  # the warning line must follow at once
        f'Fix\n{SCISSORS} and more\n# Do not modify or remove the line above.\n{WORD}\n',  # git writes the line whole
        f'Fix\n{SCISSORS}\n# Do not modify or remove the line above. And more.\n{WORD}\n',  # and the warning too
        f'Fix\n {SCISSORS}\n# Do not modify or remove the line above.\n{WORD}\n',  # nothing before it either
        f'Fix\n{SCISSORS}\n # Do not modify or remove the line above.\n{WORD}\n',  # and not before the warning
    ],
    ids=[
        'scissors and warning apart',
        'scissors line with more on it',
        'warning line with more on it',
        'scissors line with a space before it',
        'warning line with a space before it',
    ],
)
def test_a_message_is_cut_only_at_git_s_whole_scissors_pair(tmp_path: Path, words: Path, template: str):
    result = scan('--message', str(write(tmp_path / 'COMMIT_EDITMSG', template)), words=words)
    assert result.returncode == 1, result.stderr


def test_a_message_is_cut_at_git_s_scissors_pair_and_not_above_it(tmp_path: Path, words: Path):
    template = f'Fix {WORD}\n{SCISSORS}\n# Do not modify or remove the line above.\ndiff --git a/a.txt b/a.txt\n'
    result = scan('--message', str(write(tmp_path / 'COMMIT_EDITMSG', template)), words=words)
    assert result.returncode == 1, result.stderr  # the word above the pair is the message


def test_the_scissors_pair_uses_git_s_comment_character(repo: Path, words: Path):
    git(repo, 'config', 'core.commentChar', ';')
    warning = 'Do not modify or remove the line above.'
    cut = write(repo / 'cut', f'Fix\n; ------------------------ >8 ------------------------\n; {warning}\n{WORD}\n')
    uncut = write(repo / 'uncut', f'Fix\n{SCISSORS}\n# {warning}\n{WORD}\n')  # `#` lines: not git's pair here
    assert scan('--message', str(cut), words=words, cwd=repo).returncode == 0
    assert scan('--message', str(uncut), words=words, cwd=repo).returncode == 1


def test_the_message_comment_character_is_git_s(repo: Path, words: Path):
    git(repo, 'config', 'core.commentChar', ';')
    dropped = write(repo / 'dropped', f'Remove a file\n{editor_hint(";")};\tdeleted: {WORD}.txt\n')
    kept = write(repo / 'kept', f'{editor_hint(";")}# {WORD} is a heading here\n')
    assert scan('--message', str(dropped), words=words, cwd=repo).returncode == 0
    assert scan('--message', str(kept), words=words, cwd=repo).returncode == 1


def test_an_automatic_comment_character_reads_as_the_default(repo: Path, words: Path):
    git(repo, 'config', 'core.commentChar', 'auto')
    message = write(repo / 'msg', f'Fix\n{editor_hint()}# {WORD}\n')
    assert scan('--message', str(message), words=words, cwd=repo).returncode == 0


def test_a_push_to_a_remote_ref_named_with_a_word_fails(repo: Path, words: Path):
    """pre-commit names the remote ref a push writes; no local ref has to hold that name."""
    result = scan('--history', words=words, cwd=repo, extra={'PRE_COMMIT_REMOTE_BRANCH': f'refs/heads/{WORD}-x'})
    assert result.returncode == 1, result.stderr
    assert 'remote ref refs/heads/*** (name): private word #3' in result.stderr
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_sharp_s_in_a_list_word_matches_its_double_s_spelling(tmp_path: Path):
    listed = write(tmp_path / 'list.txt', 'Straßenbahn\n')
    assert scan(str(write(tmp_path / 'notes.md', 'see STRASSENBAHN\n')), words=listed).returncode == 1


def hooks_config(repo: Path) -> None:
    """The repository's word hooks, run by this interpreter instead of `uv run`, so the test needs no project."""
    config = yaml.safe_load((SCRIPT.parent.parent / '.pre-commit-config.yaml').read_text(encoding='utf-8'))
    hooks = [hook for entry in config['repos'] for hook in entry['hooks'] if hook['id'].startswith('private-words')]
    for hook in hooks:
        hook['entry'] = hook['entry'].replace(
            'uv run --frozen python scripts/private_word_scan.py', f'"{sys.executable}" "{SCRIPT}"'
        )
    kept = {'default_stages': config['default_stages'], 'repos': [{'repo': 'local', 'hooks': hooks}]}
    (repo / '.pre-commit-config.yaml').write_text(yaml.safe_dump(kept))


@pytest.mark.parametrize(
    ('hook', 'stage'),
    [
        ('private-words', 'pre-commit'),
        ('private-words-message', 'commit-msg'),
        ('private-words-refs', 'pre-commit'),
        ('private-words-refs', 'post-checkout'),
        ('private-words-history', 'pre-push'),
    ],
)
def test_each_word_hook_runs_at_its_stage(repo: Path, words: Path, hook: str, stage: str):
    hooks_config(repo)
    options: list[str] = []
    if hook == 'private-words':
        write(repo / f'{WORD}.txt', 'clean\n')
        git(repo, 'add', '.')
    elif hook == 'private-words-message':
        options = ['--commit-msg-filename', str(write(repo / 'MSG', f'Add {WORD}\n'))]
    elif hook == 'private-words-refs':
        git(repo, 'branch', f'topic-{WORD}')
        if stage == 'post-checkout':
            options = ['--from-ref', 'HEAD', '--to-ref', 'HEAD', '--checkout-type', '1']
    else:
        git(repo, 'tag', '-a', 'v0', '-m', f'Release for {WORD}')
        options = ['--from-ref', 'HEAD', '--to-ref', 'HEAD', '--remote-branch', 'refs/heads/main']
    result = subprocess.run(
        [sys.executable, '-m', 'pre_commit', 'run', hook, '--hook-stage', stage, *options],
        cwd=repo,
        env={**os.environ, 'DTB_PRIVATE_WORDS': str(words)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'private word #3' in result.stdout
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_comment_line_in_a_message_given_on_the_command_line_is_scanned(repo: Path, words: Path):
    """`commit -m` and `-F` keep `#` lines (git strips comments only from a message it opened in an editor)."""
    hooks_config(repo)
    git(repo, 'add', '.')  # the config, so the commit has something to commit
    subprocess.run(
        [sys.executable, '-m', 'pre_commit', 'install', '--hook-type', 'commit-msg'],
        cwd=repo, check=True, capture_output=True,
    )  # fmt: skip
    result = subprocess.run(
        ['git', 'commit', '-q', '-m', f'Fix\n\n# {WORD}'],
        cwd=repo, env={**os.environ, 'DTB_PRIVATE_WORDS': str(words)}, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'commit message: private word #3' in result.stdout + result.stderr  # the message hook refused it
    assert git(repo, 'log', '-1', '--format=%s').strip() == 'First'  # nothing was committed


@pytest.mark.parametrize('mode', ['--refs', '--history'])
def test_a_file_named_like_a_mode_is_still_a_file(tmp_path: Path, words: Path, mode: str):
    """pre-commit passes staged paths, and a root file may be named `--refs` or `--history`."""
    write(tmp_path / mode, 'clean\n')
    write(tmp_path / 'notes.md', f'see {WORD}\n')
    assert scan(mode, 'notes.md', words=words, cwd=tmp_path).returncode == 1


@pytest.mark.parametrize('where', ['message', 'contents'])
def test_a_commit_only_the_reflog_holds_is_scanned(repo: Path, words: Path, where: str):
    """pre-commit names only the first pushed ref with new commits, so `git push origin main HEAD@{2}:refs/heads/x`
    can publish a commit no ref holds and no variable names: the scan reads every object in the store."""
    if where == 'message':
        commit(repo, 'b.txt', b'clean\n', message=f'Add {WORD}')
    else:
        commit(repo, 'b.txt', f'see {WORD}\n'.encode())
    git(repo, 'reset', '-q', '--hard', 'HEAD~1')
    result = scan('--history', words=words, cwd=repo)
    assert result.returncode == 1, result.stderr


def loose_commit(repo: Path) -> str:
    """A commit no ref or reflog holds (`git commit-tree`, as `git stash create` makes one); its file holds WORD."""
    blob = git(repo, 'hash-object', '-w', '--stdin', input=f'see {WORD}\n').strip()
    tree = git(repo, 'mktree', input=f'100644 blob {blob}\tb.txt\n').strip()
    return git(repo, 'commit-tree', tree, '-p', 'HEAD', '-m', 'Loose').strip()


def test_a_commit_no_ref_or_reflog_holds_is_scanned(repo: Path, words: Path):
    loose_commit(repo)
    result = scan('--history', words=words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_loose_commit_pushed_as_a_second_refspec_is_refused_by_the_hook(tmp_path: Path, repo: Path, words: Path):
    """pre-commit names one pushed ref to the hook; `git push origin main <loose>:refs/heads/wip` sends another."""
    hooks_config(repo)
    git(repo, 'add', '.')
    git(repo, 'commit', '-q', '-m', 'Add the hook config')
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'init', '-q', '--bare', str(remote))
    git(repo, 'remote', 'add', 'origin', str(remote))
    subprocess.run(
        [sys.executable, '-m', 'pre_commit', 'install', '--hook-type', 'pre-push'],
        cwd=repo, check=True, capture_output=True,
    )  # fmt: skip
    loose = loose_commit(repo)
    result = subprocess.run(
        ['git', 'push', 'origin', 'main', f'{loose}:refs/heads/wip'],
        cwd=repo, env={**os.environ, 'DTB_PRIVATE_WORDS': str(words)}, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode != 0, result.stdout + result.stderr
    assert 'private word #3' in result.stdout + result.stderr  # found, not a crash that also blocks the push
    assert git(remote, 'for-each-ref') == ''  # nothing reached the remote
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_staged_file_named_like_a_mode_is_scanned_by_the_hook(repo: Path, words: Path):
    hooks_config(repo)
    write(repo / '--refs', f'see {WORD}\n')
    git(repo, 'add', '--', '--refs')
    result = subprocess.run(
        [sys.executable, '-m', 'pre_commit', 'run', 'private-words', '--hook-stage', 'pre-commit'],
        cwd=repo, env={**os.environ, 'DTB_PRIVATE_WORDS': str(words)}, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'private word #3' in result.stdout


def test_an_editor_commit_removing_a_word_named_file_goes_through(repo: Path, words: Path):
    """git lists the deleted file in its editor template; the message it stores holds only what the editor wrote."""
    commit(repo, f'{WORD}.txt', b'clean\n')  # before the hooks: the file is the leak this commit removes
    hooks_config(repo)
    subprocess.run(
        [sys.executable, '-m', 'pre_commit', 'install', '--hook-type', 'commit-msg'],
        cwd=repo, check=True, capture_output=True,
    )  # fmt: skip
    git(repo, 'rm', '-q', f'{WORD}.txt')
    editor = write(repo.parent / 'editor.py', 'import sys\np = sys.argv[1]\nt = open(p).read()\n'
                   "open(p, 'w').write('Remove a leaked file\\n' + t)\n")  # fmt: skip
    env = {**os.environ, 'DTB_PRIVATE_WORDS': str(words), 'GIT_EDITOR': f'"{sys.executable}" "{editor}"'}
    result = subprocess.run(
        ['git', 'commit', '-q'],
        cwd=repo, env={**env, 'LC_ALL': 'C', 'LANGUAGE': 'C'}, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, 'log', '-1', '--format=%s').strip() == 'Remove a leaked file'


def test_a_tree_no_ref_and_no_commit_holds_is_read(repo: Path, words: Path):
    """`git push origin main <tree>:refs/tags/t` sends a tree that no commit and no ref holds."""
    blob = git(repo, 'hash-object', '-w', '--stdin', input='clean\n').strip()
    git(repo, 'mktree', input=f'100644 blob {blob}\t{WORD}.txt\n')
    result = scan('--history', words=words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert 'tree ' in result.stderr  # the tree's own finding: no ref and no commit leads to it
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_staged_symlink_to_a_word_is_scanned_by_the_hook(repo: Path, words: Path):
    hooks_config(repo)
    (repo / 'link').symlink_to(f'../{WORD}/target')  # a target that need not exist: git stores the link's text
    git(repo, 'add', 'link')
    result = subprocess.run(
        [sys.executable, '-m', 'pre_commit', 'run', 'private-words', '--hook-stage', 'pre-commit'],
        cwd=repo, env={**os.environ, 'DTB_PRIVATE_WORDS': str(words)}, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'link (link target): private word #3' in result.stdout


@pytest.mark.parametrize(
    'message',
    [
        f'subject\n\n{SCISSORS}\n{WORD} below',  # a scissors line git did not write: -m keeps all of it
        f"Quote git's hint: # with '#' will be ignored, and an empty message aborts the commit.\n# {WORD}",
    ],
    ids=['scissors', 'quoted hint'],
)
def test_a_message_given_on_the_command_line_is_scanned_whole(repo: Path, words: Path, message: str):
    hooks_config(repo)
    git(repo, 'add', '.')
    subprocess.run(
        [sys.executable, '-m', 'pre_commit', 'install', '--hook-type', 'commit-msg'],
        cwd=repo, check=True, capture_output=True,
    )  # fmt: skip
    result = subprocess.run(
        ['git', 'commit', '-q', '-m', message],
        cwd=repo, env={**os.environ, 'DTB_PRIVATE_WORDS': str(words)}, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'private word #3' in result.stdout + result.stderr  # found, not a crash that also aborts the commit
    assert git(repo, 'log', '-1', '--format=%s').strip() == 'First'


NON_ASCII = 'Grünspecht'  # a made-up list word whose `ü` is one byte in Latin-1


@pytest.fixture
def non_ascii_words(tmp_path: Path) -> Path:
    return write(tmp_path / 'non-ascii.txt', f'{NON_ASCII}\n')


def git_bytes(repo: Path, *args: str, input: bytes) -> str:  # noqa: A002
    """A git command fed raw bytes, for names and messages that are not UTF-8."""
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, input=input).stdout.decode()


@pytest.mark.parametrize('where', ['commit message', 'file name', 'message file'])
def test_a_latin1_word_is_found(tmp_path: Path, repo: Path, non_ascii_words: Path, where: str):
    """UTF-8 decoding drops the Latin-1 `ü`, so only the other readings show the word."""
    latin1 = NON_ASCII.encode('latin-1')
    if where == 'commit message':
        message = tmp_path / 'msg'
        message.write_bytes(b'Add ' + latin1 + b' notes\n')
        git(repo, '-c', 'i18n.commitEncoding=ISO-8859-1', 'commit', '-q', '--allow-empty', '-F', str(message))
        result = scan('--history', words=non_ascii_words, cwd=repo)
    elif where == 'file name':
        blob = git(repo, 'hash-object', '-w', '--stdin', input='clean\n').strip()
        tree = git_bytes(repo, 'mktree', '-z', input=f'100644 blob {blob}\t'.encode() + latin1 + b'.txt\0').strip()
        git(repo, 'commit-tree', tree, '-m', 'Loose')
        result = scan('--history', words=non_ascii_words, cwd=repo)
    else:
        message = tmp_path / 'COMMIT_EDITMSG'
        message.write_bytes(b'Add ' + latin1 + b' notes\n')
        result = scan('--message', str(message), words=non_ascii_words)
    assert result.returncode == 1, result.stderr
    shown = (result.stdout + result.stderr).casefold()
    assert NON_ASCII.casefold() not in shown
    assert 'grnspecht' not in shown  # nor the word less the letter decoding drops


def test_a_latin_1_word_spanning_two_parts_of_a_path_is_never_printed(tmp_path: Path, repo: Path):
    """Only the whole path, read one byte per letter (`ü` is one byte), holds the word, so the path is hidden whole."""
    spanning = write(tmp_path / 'spanning.txt', 'Grün/Specht\n')
    blob = git(repo, 'hash-object', '-w', '--stdin', input='clean\n').strip()
    inner = git(repo, 'mktree', input=f'100644 blob {blob}\tSpecht.txt\n').strip()
    outer = git_bytes(repo, 'mktree', '-z', input=f'040000 tree {inner}\t'.encode() + 'Grün'.encode('latin-1') + b'\0')
    git(repo, 'tag', 'just-a-tree', outer.strip())
    result = scan('--refs', words=spanning, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert 'specht' not in (result.stdout + result.stderr).casefold()


def test_a_tag_object_only_the_store_holds_is_read(repo: Path, words: Path):
    """A push can send a tag object by its id after its ref is deleted."""
    git(repo, 'tag', '-a', 'gone', '-m', f'Release for {WORD}')
    git(repo, 'tag', '-d', 'gone')
    result = scan('--history', words=words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert 'private word #' in result.stderr
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_replaced_commit_is_read_as_stored(tmp_path: Path, repo: Path):
    """A push sends the original objects, whatever `git replace` shows locally."""
    slashed_words = write(tmp_path / 'slashed.txt', f'{SLASHED.lower()}\n')
    first = git(repo, 'rev-parse', 'HEAD').strip()
    (repo / 'Acme').mkdir()
    commit(repo, 'Acme/Widget.txt', b'clean\n')
    git(repo, 'replace', git(repo, 'rev-parse', 'HEAD').strip(), first)
    result = scan('--history', words=slashed_words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert SLASHED.casefold() not in (result.stdout + result.stderr).casefold()


@pytest.mark.parametrize('tag', ['lightweight', 'annotated'])
def test_a_whole_path_in_a_tree_only_a_tag_holds_is_read(tmp_path: Path, repo: Path, tag: str):
    """No commit holds the tree, so only the tree a ref names shows `Acme/Widget` whole."""
    slashed_words = write(tmp_path / 'slashed.txt', f'{SLASHED.lower()}\n')
    blob = git(repo, 'hash-object', '-w', '--stdin', input='clean\n').strip()
    inner = git(repo, 'mktree', input=f'100644 blob {blob}\tWidget.txt\n').strip()
    outer = git(repo, 'mktree', input=f'040000 tree {inner}\tAcme\n').strip()
    git(repo, 'tag', *(['-a', '-m', 'Clean'] if tag == 'annotated' else []), 'just-a-tree', outer)
    result = scan('--history', words=slashed_words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert SLASHED.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_packed_unreachable_object_is_read(repo: Path, words: Path):
    loose_commit(repo)
    git(repo, 'repack', '-adkq')  # -k keeps the unreachable objects, now in a pack
    git(repo, 'prune-packed')
    assert not list((repo / '.git' / 'objects').glob('[0-9a-f][0-9a-f]/*'))  # no loose object is left
    result = scan('--history', words=words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def leaked(output: str, *listed: str) -> bool:
    """Whether the output shows a word, or the word less any one letter (what UTF-8 decoding of Latin-1 leaves).
    Object ids are taken out first: hex digits can spell a short remainder such as `caf`."""
    shown = re.sub(r'[0-9a-f]{12,40}', '', output).casefold()
    return any(
        variant in shown
        for word in listed
        for variant in [word.casefold()] + [word.casefold()[:i] + word.casefold()[i + 1 :] for i in range(len(word))]
    )


MIXED = {  # list words and a two-part path: (words, first part, second part)
    'two words': ((WORD, NON_ASCII), WORD.encode(), NON_ASCII.encode('latin-1') + b'.txt'),
    'one word in both encodings': (('café',), 'café'.encode(), 'café'.encode('latin-1') + b'.txt'),
}


def load_scanner(monkeypatch: pytest.MonkeyPatch, listed: Path):
    """The script as a module, to patch it in process: for a file name the file system here may refuse to create
    (APFS takes UTF-8 only), for an answer from git that only a race or a corrupt object gives, or for git output
    fed straight to `batch()`, such as an unreadable object's answer."""
    spec = importlib.util.spec_from_file_location('private_word_scan', SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('DTB_PRIVATE_WORDS', str(listed))
    return module


@pytest.mark.parametrize('where', ['history', 'files'])
@pytest.mark.parametrize('case', list(MIXED))
def test_a_path_mixing_encodings_hides_every_part_with_a_word(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch, case: str, where: str
):
    listed_words, first, second = MIXED[case]
    listed = write(tmp_path / 'mixed.txt', ''.join(f'{word}\n' for word in listed_words))
    if where == 'history':
        blob = git(repo, 'hash-object', '-w', '--stdin', input='clean\n').strip()
        inner = git_bytes(repo, 'mktree', '-z', input=f'100644 blob {blob}\t'.encode() + second + b'\0').strip()
        outer = git_bytes(repo, 'mktree', '-z', input=f'040000 tree {inner}\t'.encode() + first + b'\0').strip()
        git(repo, 'commit-tree', outer, '-m', 'Loose')
        result = scan('--history', words=listed, cwd=repo)
        assert result.returncode == 1, result.stderr
        output = result.stdout + result.stderr
    else:  # in process, with a synthetic name: no file is created
        scanner = load_scanner(monkeypatch, listed)
        monkeypatch.setattr(Path, 'is_symlink', lambda _: False)
        monkeypatch.setattr(Path, 'read_bytes', lambda _: b'clean\n')
        output = '\n'.join(scanner.scan_files([os.fsdecode(first + b'/' + second)], scanner.load_words()))
    assert '***/*** (path)' in output, output
    assert not leaked(output, *listed_words), output


_ID = '0' * 40
_UNREADABLE = {  # what batch() may get
    # git's own answers for an unreadable object
    'missing': f'{_ID} missing\n',  # pruned after it was listed (a `gc` beside a push), or corrupt
    'size one past the end': f'{_ID} blob 6\nshort\n',  # the body is 5 bytes, the output ends on its newline
    'size past the end': f'{_ID} blob 200\nshort\n',  # a valid zlib stream whose object header declares too much
    # (a byte flipped in the body's part of the stream makes git exit 128 instead, which main answers as a git failure)
    'size ending on a space': f'{_ID} blob 5\nhello {_ID} blob 1\nx\n',  # a body longer than its size: no newline
    # lines git never prints, read as a header after a wrong declared end
    'size that is no number': f'{_ID} blob {WORD}\nshort\n',
    'a fourth field': f'{_ID} blob 5 more\nhello\n',
}


@pytest.mark.parametrize('answer', list(_UNREADABLE))
def test_an_object_git_cannot_read_cannot_check(
    words: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], answer: str
):
    """In process, with the bytes given: a race cannot be staged. The table comments say which rows are git's own
    answers and which are lines a wrong declared end makes the parser read as a header."""
    scanner = load_scanner(monkeypatch, words)
    printed = _UNREADABLE[answer].encode()
    monkeypatch.setattr(scanner, 'git', lambda *args, **_: printed if 'cat-file' in args else b'')
    assert scanner.main(['--history']) == scanner.COULD_NOT_CHECK
    shown = capsys.readouterr().err
    assert 'could not check: an object could not be read' in shown  # not the generic git failure
    assert 'git fsck' in shown  # and it says what to do
    assert WORD.casefold() not in shown.casefold()


@pytest.mark.parametrize('encoding', ['latin-1', 'utf-16-le'])
def test_a_tag_message_in_another_encoding_fails_the_ref_scan(
    tmp_path: Path, repo: Path, non_ascii_words: Path, encoding: str
):
    """A ref-only push runs no pre-push hook, so the ref scan is the only check a tag gets."""
    message = tmp_path / 'tag-message'
    message.write_bytes(f'Release {NON_ASCII}\n'.encode(encoding))
    git(repo, 'tag', '-a', 'v1', '-F', str(message))
    result = scan('--refs', words=non_ascii_words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert not leaked(result.stdout + result.stderr, NON_ASCII)


def test_a_latin1_ref_name_is_found_and_hidden(repo: Path, non_ascii_words: Path):
    """Written into packed-refs, since a file system may refuse the name as a loose ref (Linux allows one)."""
    head = git(repo, 'rev-parse', 'HEAD').strip()
    (repo / '.git' / 'packed-refs').write_bytes(
        f'{head} refs/heads/topic-'.encode() + NON_ASCII.encode('latin-1') + b'\n'
    )
    assert git(repo, 'for-each-ref', '--format=%(objectname)').count(head) == 2  # main and the packed ref
    result = scan('--refs', words=non_ascii_words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert 'ref refs/heads/*** (name)' in result.stderr
    assert not leaked(result.stdout + result.stderr, NON_ASCII)


def test_a_tag_on_a_deleted_tag_brings_the_inner_tag_to_the_ref_scan(repo: Path, words: Path):
    """`git push origin outer` sends the inner tag object too, and it brings no commit, so no pre-push hook runs."""
    git(repo, 'tag', '-a', 'inner', '-m', f'Notes on {WORD}')
    git(repo, 'tag', '-a', 'outer', 'inner', '-m', 'Clean')
    git(repo, 'tag', '-d', 'inner')
    result = scan('--refs', words=words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


@pytest.mark.parametrize('tag', ['annotated', 'lightweight'])
def test_a_tag_on_a_blob_brings_the_blob_to_the_ref_scan(repo: Path, words: Path, tag: str):
    blob = git(repo, 'hash-object', '-w', '--stdin', input=f'see {WORD}\n').strip()
    git(repo, 'tag', *(['-a', '-m', 'Clean'] if tag == 'annotated' else []), 'just-a-blob', blob)
    result = scan('--refs', words=words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


@pytest.mark.parametrize('tag', ['annotated', 'lightweight'])
def test_a_tag_on_a_tree_brings_its_whole_paths_to_the_ref_scan(tmp_path: Path, repo: Path, tag: str):
    slashed_words = write(tmp_path / 'slashed.txt', f'{SLASHED.lower()}\n')
    blob = git(repo, 'hash-object', '-w', '--stdin', input='clean\n').strip()
    inner = git(repo, 'mktree', input=f'100644 blob {blob}\tWidget.txt\n').strip()
    outer = git(repo, 'mktree', input=f'040000 tree {inner}\tAcme\n').strip()
    git(repo, 'tag', *(['-a', '-m', 'Clean'] if tag == 'annotated' else []), 'just-a-tree', outer)
    result = scan('--refs', words=slashed_words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert SLASHED.casefold() not in (result.stdout + result.stderr).casefold()


def with_remote(tmp_path: Path, repo: Path) -> Path:
    """A bare remote holding main; pushed without hooks, as a published history is."""
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'init', '-q', '--bare', str(remote))
    git(repo, 'remote', 'add', 'origin', str(remote))
    git(repo, 'push', '-q', 'origin', 'main')
    return remote


def test_the_ref_scan_skips_what_a_remote_already_holds(tmp_path: Path, repo: Path, words: Path):
    """A word already published is the history scan's to report; a tag on that history adds nothing new."""
    commit(repo, 'b.txt', f'see {WORD}\n'.encode())
    with_remote(tmp_path, repo)
    git(repo, 'tag', '-a', 'v1', '-m', 'Clean')
    result = scan('--refs', words=words, cwd=repo)
    assert result.returncode == 0, result.stderr


def test_a_lightweight_tag_on_an_unpushed_commit_is_refused_by_the_pre_push_hook(
    tmp_path: Path, repo: Path, words: Path
):
    """The ref scan leaves a ref to a commit alone: a push that brings a new commit runs the pre-push hook."""
    hooks_config(repo)
    git(repo, 'add', '.')
    git(repo, 'commit', '-q', '-m', 'Add the hook config')
    remote = with_remote(tmp_path, repo)
    subprocess.run(
        [sys.executable, '-m', 'pre_commit', 'install', '--hook-type', 'pre-push'],
        cwd=repo, check=True, capture_output=True,
    )  # fmt: skip
    commit(repo, 'b.txt', f'see {WORD}\n'.encode())
    git(repo, 'tag', 'wip')
    git(repo, 'reset', '-q', '--hard', 'HEAD~1')  # only the tag holds the commit
    result = subprocess.run(
        ['git', 'push', 'origin', 'wip'],
        cwd=repo, env={**os.environ, 'DTB_PRIVATE_WORDS': str(words)}, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode != 0, result.stdout + result.stderr
    assert 'private word #3' in result.stdout + result.stderr  # found, not a crash that also blocks the push
    assert git(remote, 'tag') == ''  # nothing reached the remote
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


def test_a_ref_scan_outside_a_repository_cannot_check(tmp_path: Path, words: Path):
    outside = tmp_path / 'not-a-repo'
    outside.mkdir()
    result = scan('--refs', words=words, cwd=outside)
    assert result.returncode == 2
    assert 'could not check: git or the file system failed' in result.stderr
    assert 'git fsck' in result.stderr  # and it says what to do


def test_a_file_that_cannot_be_read_cannot_check(tmp_path: Path, words: Path):
    unreadable = tmp_path / 'a-directory'  # read_bytes on it raises OSError, as a file with no read permission does
    unreadable.mkdir()
    result = scan(str(unreadable), words=words)
    assert result.returncode == 2
    assert 'every file given to the scan can be read' in result.stderr  # the word list was read fine


BOMMED = {  # list word and planted bytes: a byte order mark in front of text in another encoding
    'utf-16 mark before ascii': (WORD, codecs.BOM_UTF16_LE + f'hello {WORD}\n'.encode()),
    'utf-8 mark before latin-1': (NON_ASCII, codecs.BOM_UTF8 + b'see ' + NON_ASCII.encode('latin-1') + b'\n'),
    'utf-16 mark before utf-8': (NON_ASCII, codecs.BOM_UTF16_LE + b'see ' + NON_ASCII.encode() + b'\n'),
    'utf-16 mark right before the word': (WORD, codecs.BOM_UTF16_LE + f'{WORD} is the title\n'.encode()),
}


@pytest.mark.parametrize('where', ['file', 'history', 'path'])
@pytest.mark.parametrize('case', list(BOMMED))
def test_a_byte_order_mark_does_not_hide_the_other_readings(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch, case: str, where: str
):
    word, data = BOMMED[case]
    listed = write(tmp_path / 'list.txt', f'{word}\n')
    if where == 'file':
        planted = tmp_path / 'notes.txt'
        planted.write_bytes(data)
        result = scan(str(planted), words=listed)
        assert result.returncode == 1, result.stdout
        output = result.stdout + result.stderr
    elif where == 'history':
        commit(repo, 'notes.txt', data)
        result = scan('--history', words=listed, cwd=repo)
        assert result.returncode == 1, result.stdout
        output = result.stdout + result.stderr
    else:  # in process, with a synthetic name: no file is created
        scanner = load_scanner(monkeypatch, listed)
        monkeypatch.setattr(Path, 'is_symlink', lambda _: False)
        monkeypatch.setattr(Path, 'read_bytes', lambda _: b'clean\n')
        output = '\n'.join(scanner.scan_files([os.fsdecode(b'docs/' + data.rstrip(b'\n'))], scanner.load_words()))
        assert 'docs/*** (path)' in output, output
    assert not leaked(output, word), output


def test_a_printed_path_keeps_every_byte(monkeypatch: pytest.MonkeyPatch, words: Path):
    """A name in an encoding the scan does not read (Mac Roman `ü` is 0x9F) is printed escaped, never with a letter
    dropped: the word less a letter is still the word to a reader."""
    scanner = load_scanner(monkeypatch, words)
    monkeypatch.setattr(Path, 'is_symlink', lambda _: False)
    monkeypatch.setattr(Path, 'read_bytes', lambda _: f'see {WORD}\n'.encode())
    output = '\n'.join(scanner.scan_files([os.fsdecode(b'docs/Gr\x9fnspecht.txt')], scanner.load_words()))
    assert 'docs/Gr\\x9fnspecht.txt:1: private word #3' in output, output
    assert 'grnspecht' not in output.casefold()


def test_a_latin1_word_below_git_s_scissors_line_is_not_the_message(tmp_path: Path, non_ascii_words: Path):
    """`commit -v` puts the diff below the scissors line, and git drops it, whatever its encoding."""
    message = tmp_path / 'COMMIT_EDITMSG'
    message.write_bytes(
        f'Drop a line\n{SCISSORS}\n# Do not modify or remove the line above.\ndiff --git a/a.txt b/a.txt\n-'.encode()
        + NON_ASCII.encode('latin-1')
        + b'\n'
    )
    result = scan('--message', str(message), words=non_ascii_words)
    assert result.returncode == 0, result.stderr


def stand_in_config(repo: Path) -> None:
    """The repository's hook config with every hook but the word hooks replaced, in place and at its stages, by a
    stand-in that prints the file names it is given and fails, as trailing-whitespace's `Fixing <name>` does."""
    config = yaml.safe_load((SCRIPT.parent.parent / '.pre-commit-config.yaml').read_text(encoding='utf-8'))
    stand_in = f'"{sys.executable}" -c "import sys; print(*sys.argv[1:]); sys.exit(1)"'
    repos = []
    for entry in config['repos']:
        hooks = []
        for hook in entry['hooks']:
            if hook['id'].startswith('private-words'):
                scanner = f'"{sys.executable}" "{SCRIPT}"'
                entry_line = hook['entry'].replace('uv run --frozen python scripts/private_word_scan.py', scanner)
                hooks.append(hook | {'entry': entry_line})
            else:
                kept = {'stages': hook['stages']} if 'stages' in hook else {}
                hooks.append({'id': hook['id'], 'name': hook['id'], 'entry': stand_in, 'language': 'system'} | kept)
        repos.append({'repo': 'local', 'hooks': hooks})
    (repo / '.pre-commit-config.yaml').write_text(yaml.safe_dump({**config, 'repos': repos}))


def test_a_word_named_staged_file_shows_in_no_other_hook_s_output(repo: Path, words: Path):
    stand_in_config(repo)
    write(repo / f'{WORD}.txt', 'clean\n')
    git(repo, 'add', '.')
    result = subprocess.run(
        [sys.executable, '-m', 'pre_commit', 'run', '--hook-stage', 'pre-commit'],
        cwd=repo, env={**os.environ, 'DTB_PRIVATE_WORDS': str(words)}, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'private word #3' in result.stdout
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold(), result.stdout


FULLWIDTH = ''.join(chr(ord(letter) + 0xFEE0) for letter in WORD)


@pytest.mark.parametrize(
    ('listed', 'planted'),
    [
        (NON_ASCII, unicodedata.normalize('NFD', NON_ASCII)),  # `ü` as `u` and a combining diaeresis
        (WORD, FULLWIDTH),
        (WORD, f'{WORD}̂'),  # a combining circumflex that composes with the last `h` into `ĥ`
        (unicodedata.normalize('NFD', NON_ASCII), NON_ASCII),  # the variant spelling in the list
        (FULLWIDTH, WORD),
    ],
    ids=['NFD', 'fullwidth', 'combining mark after the word', 'NFD in the list', 'fullwidth in the list'],
)
def test_a_canonically_equal_spelling_is_found(tmp_path: Path, listed: str, planted: str):
    listed_words = write(tmp_path / 'listed.txt', f'{listed}\n')
    result = scan(str(write(tmp_path / 'notes.md', f'see {planted} here\n')), words=listed_words)
    assert result.returncode == 1, result.stderr
    assert 'private word #1' in result.stderr


def test_a_decomposed_word_in_a_path_part_is_redacted(tmp_path: Path, non_ascii_words: Path):
    decomposed = unicodedata.normalize('NFD', NON_ASCII)
    planted = write(tmp_path / f'{decomposed}.txt', 'clean\n')
    result = scan(str(planted), words=non_ascii_words)
    assert result.returncode == 1, result.stderr
    assert '/*** (path): private word #1' in result.stderr
    shown = unicodedata.normalize('NFC', result.stdout + result.stderr).casefold()
    assert NON_ASCII.casefold() not in shown, result.stderr


def test_a_gitlink_path_is_found_by_the_history_scan(repo: Path, words: Path):
    head = git(repo, 'rev-parse', 'HEAD').strip()
    git(repo, 'update-index', '--add', '--cacheinfo', f'160000,{head},{WORD}')
    git(repo, 'commit', '-q', '-m', 'Clean')
    result = scan('--history', words=words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert '(path): private word #3' in result.stderr
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


@pytest.mark.parametrize('target', ['tree', 'commit'])
def test_a_history_finding_is_printed_once(tmp_path: Path, repo: Path, target: str):
    slashed_words = write(tmp_path / 'slashed.txt', f'{SLASHED.lower()}\n')
    blob = git(repo, 'hash-object', '-w', '--stdin', input='clean\n').strip()
    inner = git(repo, 'mktree', input=f'100644 blob {blob}\tWidget.txt\n').strip()
    outer = git(repo, 'mktree', input=f'040000 tree {inner}\tAcme\n').strip()
    tagged = outer if target == 'tree' else git(repo, 'commit-tree', outer, '-m', 'Loose').strip()
    git(repo, 'tag', '-a', '-m', 'Clean', 'held', tagged)
    result = scan('--history', words=slashed_words, cwd=repo)
    assert result.returncode == 1, result.stderr
    findings = result.stderr.splitlines()
    assert len(findings) == len(set(findings)), result.stderr


def test_the_ref_scan_reads_whole_paths_in_a_commit_a_tag_brings(tmp_path: Path, repo: Path):
    """No tree object holds `Acme/Widget` whole, so only the commit's path walk finds it."""
    slashed_words = write(tmp_path / 'slashed.txt', f'{SLASHED.lower()}\n')
    (repo / 'Acme').mkdir()
    commit(repo, 'Acme/Widget.txt', b'clean\n')
    git(repo, 'tag', '-a', '-m', 'Clean', 'wip')
    git(repo, 'reset', '-q', '--hard', 'HEAD~1')  # only the tag holds the commit
    result = scan('--refs', words=slashed_words, cwd=repo)
    assert result.returncode == 1, result.stderr
    assert '(path): private word #1' in result.stderr
    assert SLASHED.casefold() not in (result.stdout + result.stderr).casefold()


@pytest.mark.skipif(os.name != 'posix' or os.geteuid() == 0, reason='root reads any file')
def test_an_unreadable_word_list_has_its_own_message(tmp_path: Path, words: Path):
    words.chmod(0)
    try:
        result = scan(str(write(tmp_path / 'clean.md', 'clean\n')), words=words)
    finally:
        words.chmod(0o600)
    assert result.returncode == 2
    assert 'private-word scan could not check: the word list cannot be read' in result.stderr


def test_a_plain_install_creates_every_hook_the_config_names(tmp_path: Path):
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    isolated = {'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}  # no global `core.hooksPath`
    git(scratch, 'init', '-q', env=isolated)
    shutil.copy(SCRIPT.parent.parent / '.pre-commit-config.yaml', scratch)
    result = subprocess.run(
        [sys.executable, '-m', 'pre_commit', 'install'],
        cwd=scratch, env={**os.environ, **isolated}, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    installed = {path.name for path in (scratch / '.git' / 'hooks').iterdir() if not path.name.endswith('.sample')}
    assert installed >= {'pre-commit', 'commit-msg', 'pre-push', 'post-checkout'}
