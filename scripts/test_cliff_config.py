"""cliff.toml decides the release version and notes (decision 009, decisions 2 and 7).

Each test builds a scratch git repository, tags a release, adds commits and asks the hash-pinned git-cliff of this
environment for the next version. "Nothing to bump" is not an error: git-cliff prints the last tag again and exits 0,
so a row that expects no release expects the tag itself.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sysconfig

import pytest

from test_commit_header import TYPES

ROOT = Path(__file__).parent.parent
SCRIPTS = sysconfig.get_path('scripts')
CLIFF = Path(SCRIPTS) / 'git-cliff'

# The conventional types of the commit-header hook that release nothing.
NON_RELEASING = ['docs', 'test', 'ci', 'build', 'refactor', 'chore', 'style']


def build_repo(path: Path, tag: str, *messages: str, files: dict[str, str] | None = None) -> Path:
    """A repository with `chore: start` (holding `files`), an annotated `tag` on it, then a commit (for `TAG:x`, a tag)
    per message."""
    env = {
        'PATH': os.environ['PATH'],
        'HOME': str(path),
        'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_AUTHOR_NAME': 'T',
        'GIT_AUTHOR_EMAIL': 't@example.com',
        'GIT_COMMITTER_NAME': 'T',
        'GIT_COMMITTER_EMAIL': 't@example.com',
    }
    repo = path / 'repo'
    repo.mkdir()

    def git(*args: str, second: int) -> None:
        stamp = f'2026-01-01T00:00:{second:02}+00:00'
        subprocess.run(
            ['git', *args],
            cwd=repo,
            env={**env, 'GIT_AUTHOR_DATE': stamp, 'GIT_COMMITTER_DATE': stamp},
            check=True,
            capture_output=True,
        )

    git('init', '-b', 'main', second=0)
    for name, text in (files or {}).items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git('add', '-A', second=0)
    git('commit', '--allow-empty', '-m', 'chore: start', second=1)
    git('tag', '-a', tag, '-m', tag, second=2)
    for second, message in enumerate(messages, start=3):
        if message.startswith('TAG:'):
            git('tag', '-a', message.removeprefix('TAG:'), '-m', message, second=second)
        else:
            git('commit', '--allow-empty', '-m', message, second=second)
    return repo


def cliff(repo: Path, *args: str) -> str:
    assert CLIFF.exists(), (
        'git-cliff is missing from this environment: `uv sync` installs the `release` group (a lock environment), '
        'or add `--group release` to `uv pip install` (an environment outside the lock)'
    )
    # git-cliff warns and falls back to its default configuration when the file is missing.
    assert (ROOT / 'cliff.toml').is_file()
    run = subprocess.run(
        [str(CLIFF), '--config', str(ROOT / 'cliff.toml'), *args],
        cwd=repo,
        env={
            'PATH': os.environ['PATH'],
            'HOME': str(repo),
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_CONFIG_NOSYSTEM': '1',
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout.strip()


def bumped(tmp_path: Path, tag: str, *messages: str) -> str:
    return cliff(build_repo(tmp_path, tag, *messages), '--bumped-version')


def test_every_type_the_hook_accepts_is_covered() -> None:
    """A type added to the commit-header hook must be named here as releasing or not, so it cannot go unchecked."""
    assert set(TYPES) == {'feat', 'fix', *NON_RELEASING}


@pytest.mark.parametrize('kind', NON_RELEASING)
def test_a_type_that_does_not_release_gives_no_bump(tmp_path: Path, kind: str) -> None:
    """A parser added for one of these types, or a removed catch-all, would release a version for it."""
    assert bumped(tmp_path, 'v0.1.0', f'{kind}: x') == 'v0.1.0'


def test_free_prose_gives_no_bump(tmp_path: Path) -> None:
    """Without the catch-all git-cliff reads `PRD` as a type and releases a patch."""
    assert bumped(tmp_path, 'v0.1.0', 'PRD: shorten the header') == 'v0.1.0'


@pytest.mark.parametrize('header', ['feat: x', 'fix: x', 'feat(sql): x', 'fix(sql): x'])
def test_feat_and_fix_bump_the_patch_before_1_0(tmp_path: Path, header: str) -> None:
    """`features_always_bump_minor = true`, a missing `^feat` or `^fix` parser, or parsers that need the `:` right
    after the type (so a scope releases nothing), change this."""
    assert bumped(tmp_path, 'v0.1.0', header) == 'v0.1.1'


@pytest.mark.parametrize('message', ['refactor!: x', 'refactor: x\n\nBREAKING CHANGE: y'])
def test_a_breaking_change_bumps_the_minor_before_1_0(tmp_path: Path, message: str) -> None:
    """A breaking change on a type that releases nothing is kept by `protect_breaking_commits` (and needs
    `conventional_commits`); `breaking_always_bump_major = true` would make it 1.0.0."""
    assert bumped(tmp_path, 'v0.1.0', message) == 'v0.2.0'


def test_an_rc_tag_is_not_a_release(tmp_path: Path) -> None:
    """With no `tag_pattern`, or one that is not anchored, git-cliff fails on a v0.1.1rc1 tag."""
    assert bumped(tmp_path, 'v0.1.0', 'fix: x', 'TAG:v0.1.1rc1', 'fix: y') == 'v0.1.1'


def test_from_1_0_feat_bumps_the_minor_and_a_break_the_major(tmp_path: Path) -> None:
    """No setting of cliff.toml fails this. It guards decision 2's rule for 1.0 against a git-cliff upgrade (Dependabot
    moves the pin in uv.lock) that changes what `feat` or `fix!` do from 1.0."""
    (tmp_path / 'feat').mkdir()
    (tmp_path / 'break').mkdir()
    assert bumped(tmp_path / 'feat', 'v1.0.0', 'feat: x') == 'v1.1.0'
    assert bumped(tmp_path / 'break', 'v1.0.0', 'fix!: x') == 'v2.0.0'


def test_the_notes_mark_a_breaking_change(tmp_path: Path) -> None:
    """The release notes of decision 009 must show a break, also one on a type that releases nothing."""
    repo = build_repo(tmp_path, 'v0.1.0', 'refactor!: drop x')
    assert '**breaking:** drop x' in cliff(repo, '--unreleased')
