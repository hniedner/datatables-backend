"""check_release.py: a release's version is git-cliff's for the commit and has a dated CHANGELOG section (decision 009).

Each test builds a scratch repository (the helper of test_cliff_config.py) holding the three files the script reads,
and calls the script's `main` the way the workflows do. The cliff.toml in the repository is the real one.

`release PYPI_JSON` is the tag run and the rehearsal; `pr BASE` is the pull request check. "Nothing to bump" is not an
error for git-cliff, so the tests assert only whether the script passes, and for a refusal which version it names.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

import check_release
from test_cliff_config import ROOT
from test_cliff_config import build_repo

INIT = 'src/datatables_backend/__init__.py'


def changelog(version: str) -> str:
    return f'# Changelog\n\n## [{version}] - 2026-01-02\n\n- Something users see.\n'


def make(tmp_path: Path, tag: str, *messages: str, version: str = '0.1.1', **files: str | None) -> Path:
    """A repository whose first commit holds the source version, a dated CHANGELOG section and cliff.toml.

    `files` replaces one of the files by its path key (`changelog=`, `cliff=`, `init=`); None leaves it out."""
    held: dict[str, str | None] = {
        INIT: f"__version__ = '{version}'\n",
        'CHANGELOG.md': changelog(version),
        'cliff.toml': (ROOT / 'cliff.toml').read_text(),
    }
    for key, name in {'changelog': 'CHANGELOG.md', 'cliff': 'cliff.toml', 'init': INIT}.items():
        if key in files:
            held[name] = files[key]
    return build_repo(tmp_path, tag, *messages, files={n: t for n, t in held.items() if t is not None})


def release_commit(repo: Path, version: str, text: str | None = None) -> None:
    """The commit of a release pull request: the version and its CHANGELOG section (or `text` in its place)."""
    (repo / INIT).write_text(f"__version__ = '{version}'\n")
    (repo / 'CHANGELOG.md').write_text(text or changelog(version))
    stamp = '2026-01-02T00:00:00+00:00'
    env = {
        'PATH': os.environ['PATH'],
        'HOME': str(repo),
        'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_AUTHOR_NAME': 'T',
        'GIT_AUTHOR_EMAIL': 't@example.com',
        'GIT_COMMITTER_NAME': 'T',
        'GIT_COMMITTER_EMAIL': 't@example.com',
        'GIT_AUTHOR_DATE': stamp,
        'GIT_COMMITTER_DATE': stamp,
    }
    for args in (['add', '-A'], ['commit', '-m', f'chore: release {version}']):
        subprocess.run(['git', *args], cwd=repo, env=env, check=True, capture_output=True)


def pypi(tmp_path: Path, *versions: str) -> str:
    path = tmp_path / 'pypi.json'
    path.write_text(json.dumps({'releases': {v: [] for v in versions}}))
    return str(path)


def run(repo: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.chdir(repo)
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    return check_release.main(list(args))


@pytest.mark.parametrize('messages', [('fix: x', 'TAG:v0.1.1'), ('fix: x',)], ids=['tag at HEAD', 'no tag yet'])
def test_a_release_on_git_cliffs_version_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, messages: tuple[str, ...]
) -> None:
    """A check that refuses a correct release, or needs the tag to exist (the rehearsal has none), blocks releases.
    The CHANGELOG text quotes no commit subject: the notes cross-check never compares text."""
    repo = make(tmp_path, 'v0.1.0', *messages)
    assert run(repo, monkeypatch, 'release', pypi(tmp_path, '0.1.0')) == 0


def test_a_tag_whose_commits_need_a_minor_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without ignoring the tag at HEAD git-cliff counts v0.1.1 as released and passes it (a false pass)."""
    repo = make(tmp_path, 'v0.1.0', 'refactor!: x', 'TAG:v0.1.1')
    assert run(repo, monkeypatch, 'release', pypi(tmp_path, '0.1.0')) == 1
    assert 'computes v0.2.0' in capsys.readouterr().err


def test_a_tag_with_nothing_to_release_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """git-cliff prints the last releasing tag and exits 0 when nothing releases; that is not a pass."""
    repo = make(tmp_path, 'v0.1.0', 'docs: x', 'TAG:v0.1.1')
    assert run(repo, monkeypatch, 'release', pypi(tmp_path, '0.1.0')) == 1
    assert 'nothing to release' in capsys.readouterr().err


def test_a_rehearsal_with_the_version_already_tagged_prints_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """On main after a release nothing is pending: the rehearsal prints git-cliff's next version and checks nothing,
    or every rehearsal between two releases would fail."""
    repo = make(tmp_path, 'v0.1.0', 'fix: a', 'TAG:v0.1.1', 'fix: b')
    assert run(repo, monkeypatch, 'release', pypi(tmp_path, '0.1.0', '0.1.1')) == 0
    assert 'v0.1.2' in capsys.readouterr().out


def test_a_rehearsal_with_a_release_pending_is_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no tag for the source version a release is pending, and the rehearsal is the dry run of the tag's check."""
    repo = make(tmp_path, 'v0.1.0', 'refactor!: x')
    assert run(repo, monkeypatch, 'release', pypi(tmp_path, '0.1.0')) == 1


def test_an_unpublished_tag_counts_for_the_version_and_its_commits_are_listed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """v0.2.0 was tagged, never published, and a fix followed: the next version is v0.2.1, and the notes cross-check
    runs from the last published tag with the unpublished tags ignored, so both commits are one section, and what
    v0.1.0 already published is not in it."""
    repo = make(
        tmp_path,
        'v0.0.9',
        'fix: ancient',
        'TAG:v0.1.0',
        'refactor!: drop x',
        'TAG:v0.2.0',
        'fix: y',
        'TAG:v0.2.1',
        version='0.2.1',
    )
    assert run(repo, monkeypatch, 'release', pypi(tmp_path, '0.0.9', '0.1.0')) == 0
    out = capsys.readouterr().out
    assert 'drop x' in out
    assert ' y' in out
    assert 'ancient' not in out
    assert [line for line in out.splitlines() if line.startswith('## ')] == ['## unreleased']


@pytest.mark.parametrize(('version', 'passes'), [('0.1.1rc1', True), ('0.2.0rc1', False)])
def test_a_release_candidate_is_checked_by_its_release_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, passes: bool
) -> None:
    """git-cliff never sees an rc tag, so 0.1.1rc1 is right when 0.1.1 is due, and a literal comparison is wrong."""
    repo = make(tmp_path, 'v0.1.0', 'fix: x', version=version)
    assert (run(repo, monkeypatch, 'release', pypi(tmp_path, '0.1.0')) == 0) is passes


@pytest.mark.parametrize('version', ['0.1.1.post1', '0.1.1.dev1', '0.1', 'v0.1.1'])
def test_a_version_outside_the_release_forms_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], version: str
) -> None:
    """Only X.Y.Z and X.Y.Z with a, b or rc release; a looser pattern lets a post or dev version through."""
    repo = make(tmp_path, 'v0.1.0', 'fix: x', version=version)
    assert run(repo, monkeypatch, 'release', pypi(tmp_path, '0.1.0')) == 1
    assert 'not a release version' in capsys.readouterr().err


@pytest.mark.parametrize(
    'text',
    [
        '# Changelog\n',
        '# Changelog\n\n## [0.1.1]\n\n- x\n',
        '# Changelog\n\n## [0.1.1] - soon\n\n- x\n',
        '# Changelog\n\n## [0.1.1] - 2026-01-02\n',
    ],
    ids=['missing', 'undated', 'date not a date', 'empty'],
)
def test_the_changelog_section_must_exist_be_dated_and_hold_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    """changelog_notes accepts any ` - ...` after the version; the release needs the date, and the notes."""
    repo = make(tmp_path, 'v0.1.0', 'fix: x', changelog=text)
    assert run(repo, monkeypatch, 'release', pypi(tmp_path, '0.1.0')) == 1


def test_a_missing_cliff_toml_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """git-cliff falls back to its default rules without the file, which release `docs:` commits."""
    repo = make(tmp_path, 'v0.1.0', 'fix: x', cliff=None)
    assert run(repo, monkeypatch, 'release', pypi(tmp_path, '0.1.0')) == 1


def test_a_pull_request_that_keeps_the_version_checks_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An ordinary pull request has no CHANGELOG section for its version and may lack the files: it must not fail."""
    repo = make(tmp_path, 'v0.1.0', 'docs: x', changelog=None, cliff=None)
    assert run(repo, monkeypatch, 'pr', 'HEAD') == 0


@pytest.mark.parametrize(
    ('message', 'passes'),
    [('fix: x', True), ('refactor!: x', False), ('docs: x', False)],
)
def test_a_release_pull_request_is_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str, passes: bool
) -> None:
    """The pull request that sets 0.1.1 is checked against git-cliff's version for the commits it brings."""
    repo = make(tmp_path, 'v0.1.0', message, version='0.1.0')
    release_commit(repo, '0.1.1')
    assert (run(repo, monkeypatch, 'pr', 'HEAD~1') == 0) is passes


def test_a_release_pull_request_without_its_changelog_section_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A version bump with no dated CHANGELOG section would fail the release run: it fails the pull request."""
    repo = make(tmp_path, 'v0.1.0', 'fix: x', version='0.1.0')
    release_commit(repo, '0.1.1', '# Changelog\n')
    assert run(repo, monkeypatch, 'pr', 'HEAD~1') == 1


@pytest.mark.parametrize(
    'messages',
    [('fix: a', 'TAG:v0.1.1', 'fix: b'), ('PRD: a', 'TAG:v0.1.1', 'docs: b')],
    ids=['commits after the tag', 'nothing releases'],
)
def test_a_release_pull_request_cannot_reuse_a_tagged_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], messages: tuple[str, ...]
) -> None:
    """A version that exists as a tag on another commit is burnt: ignoring that tag, or accepting git-cliff's
    "nothing to bump" answer (the tag itself), would pass a reused version."""
    version = '0.1.1' if messages[0] == 'fix: a' else '0.1.0'
    repo = make(tmp_path, 'v0.1.0', *messages, version='0.0.9')
    release_commit(repo, version)
    assert run(repo, monkeypatch, 'pr', 'HEAD~1') == 1
    assert 'already exists' in capsys.readouterr().err
