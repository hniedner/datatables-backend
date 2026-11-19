"""The private-word scan, run as the pre-commit hooks and the history check run it."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).with_name('private_word_scan.py')
WORD = 'Zebrafinch'  # planted; the scan must find it in any case and never print it


def scan(*args: str, words: Path | None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != 'DTB_PRIVATE_WORDS'}
    if words is not None:
        env['DTB_PRIVATE_WORDS'] = str(words)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], env=env, cwd=cwd, capture_output=True, text=True, check=False
    )


@pytest.fixture
def words(tmp_path: Path) -> Path:
    path = tmp_path / 'words.txt'
    path.write_text('# private words\n\nzEBRAfINCH\n', encoding='utf-8')  # line 3, in another case than WORD
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
    assert scan('--message', str(message), words=words).returncode == 1
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


def git(repo: Path, *args: str) -> None:
    subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)


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
    assert scan(str(planted), words=words).returncode == 1


def test_a_message_scan_without_a_file_cannot_check(words: Path):
    result = scan('--message', words=words)
    assert result.returncode == 2
    assert 'could not check' in result.stderr


def test_a_history_scan_outside_a_repository_cannot_check(tmp_path: Path, words: Path):
    outside = tmp_path / 'not-a-repo'
    outside.mkdir()
    result = scan('--history', words=words, cwd=outside)
    assert result.returncode == 2
    assert 'could not check' in result.stderr


def commit(repo: Path, name: str, data: bytes, message: str = 'Add a file', author: str = 'Test') -> None:
    (repo / name).write_bytes(data)
    git(repo, 'add', '.')
    git(repo, '-c', f'user.name={author}', 'commit', '-q', '-m', message)


@pytest.mark.parametrize(
    'where', ['non-ascii path', 'path and contents', 'binary blob', 'ref name', 'tag message', 'author']
)
def test_history_finds_what_gets_published_and_never_prints_it(repo: Path, words: Path, where: str):
    if where == 'non-ascii path':
        commit(repo, f'über-{WORD}.txt', f'{WORD}\n'.encode())
    elif where == 'path and contents':
        commit(repo, f'{WORD}.txt', f'{WORD}\n'.encode())
    elif where == 'binary blob':
        commit(repo, 'blob.bin', b'\x00\x01' + WORD.encode() + b'\x00')
    elif where == 'ref name':
        git(repo, 'branch', f'topic-{WORD}')
    elif where == 'tag message':
        git(repo, 'tag', '-a', 'v0', '-m', f'Release for {WORD}')
    else:
        commit(repo, 'b.txt', b'clean\n', author=f'{WORD} Person')
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


def test_a_utf16_blob_in_history_is_found(repo: Path, words: Path):
    commit(repo, 'notes.txt', f'see {WORD} here\n'.encode('utf-16'))
    assert scan('--history', words=words, cwd=repo).returncode == 1


def test_a_symlink_target_is_scanned(tmp_path: Path, words: Path):
    link = tmp_path / 'link'
    link.symlink_to(f'../{WORD}/target')
    result = scan(str(link), words=words)
    assert result.returncode == 1
    assert WORD.casefold() not in (result.stdout + result.stderr).casefold()


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
