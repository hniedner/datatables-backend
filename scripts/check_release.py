"""Check that a release's version is git-cliff's for the commit and has a dated CHANGELOG section (decision 009).

Run from the top of a checkout with full history and tags, by the environment that holds git-cliff
(``uv run --frozen --only-group release python scripts/check_release.py ...``). Standard library, git and git-cliff.

``release PYPI_JSON`` is the tag run and the rehearsal. PYPI_JSON is https://pypi.org/pypi/datatables-backend/json.
If the tag ``v<__version__>`` exists on an earlier commit than HEAD, no release is pending (main after a release): it
prints git-cliff's next version and checks nothing. Otherwise (the tag points at HEAD, or does not exist yet) it runs
the checks below, then prints the commits since the last published release, for the person approving the deployment to
compare with the CHANGELOG section. That print compares nothing and never fails on its content: the CHANGELOG is
written by hand and a break on a skipped type renders under its own type.

``pr BASE`` is the pull request check: if ``__version__`` is the one at BASE it is not a release pull request and
nothing is checked; otherwise the checks below run.

The checks, each finding a line on stderr that names the condition and the fix, exit status 1:

- the version is X.Y.Z, or X.Y.Z with a, b or rc and a number;
- the tag ``v<version>`` does not exist on another commit (a version once tagged is burnt);
- CHANGELOG.md has one non-empty section ``## [<version>] - YYYY-MM-DD``;
- cliff.toml exists (without it git-cliff silently applies its default rules);
- ``git-cliff --bumped-version`` is ``v`` and the release part of the version. The tag ``v<version>`` is ignored when it
  points at HEAD, since git-cliff would otherwise count that tag as released. "Nothing to bump" is not an error for
  git-cliff: it prints the last releasing tag and exits 0, which is never the version of a release.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import sysconfig

import changelog_notes

INIT = Path('src/datatables_backend/__init__.py')
CLIFF = Path(sysconfig.get_path('scripts')) / 'git-cliff'
VERSION = re.compile(r"^__version__ = '(.*)'$", re.MULTILINE)
RELEASE = re.compile(r'^(\d+\.\d+\.\d+)(?:(?:a|b|rc)\d+)?$')
FINAL_TAG = re.compile(r'^v\d+\.\d+\.\d+$')


def git(*args: str) -> str:
    return subprocess.run(['git', *args], check=True, capture_output=True, text=True).stdout


def cliff(*args: str) -> str:
    run = subprocess.run([str(CLIFF), '--config', 'cliff.toml', *args], check=True, capture_output=True, text=True)
    return run.stdout.strip()


def tagged_elsewhere(tag: str) -> bool:
    """The tag exists, on a commit other than HEAD."""
    return tag in git('tag', '--list').split() and tag not in git('tag', '--points-at', 'HEAD').split()


def version_in(text: str) -> str:
    match = VERSION.search(text)
    return match[1] if match else ''


def findings(version: str) -> list[str]:
    release = RELEASE.match(version)
    if not release:
        return [f'the version {version!r} in {INIT} is not a release version: set X.Y.Z, or X.Y.Z with a1, b1 or rc1']
    tag = f'v{version}'
    found: list[str] = []
    if tagged_elsewhere(tag):
        found.append(f'the tag {tag} already exists on another commit: a released or burnt version is never reused')
    found += changelog_findings(version)
    if not Path('cliff.toml').is_file():
        return [*found, 'cliff.toml is missing: git-cliff would apply its default rules, which release docs: commits']
    at_head = tag in git('tag', '--points-at', 'HEAD').split()
    ignore = ['--ignore-tags', f'^{re.escape(tag)}$'] if at_head else []
    computed = cliff(*ignore, '--bumped-version')
    if computed != f'v{release[1]}':
        if computed in git('tag', '--list').split():
            found.append(
                f'nothing to release: no feat, fix or breaking commit since the last release ({computed}); '
                'release nothing, or merge the change that releases first'
            )
        else:
            found.append(
                f'git-cliff computes {computed} for this commit, not v{release[1]}: '
                f'set the version and the CHANGELOG heading to {computed.removeprefix("v")}'
            )
    return found


def changelog_findings(version: str) -> list[str]:
    text = Path('CHANGELOG.md').read_text(encoding='utf-8')
    try:
        changelog_notes.notes(text, version)
    except changelog_notes.NotesError as error:
        return [f'{error}: write the notes of the release under "## [{version}] - YYYY-MM-DD"']
    if not re.search(rf'^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$', text, re.MULTILINE):
        return [f'CHANGELOG.md: the heading of {version} has no date: write "## [{version}] - YYYY-MM-DD"']
    return []


def commits_since_published(published: set[str]) -> str:
    """git-cliff's notes from the newest published final tag; unpublished tags are ignored, so they are one section."""
    finals = sorted(
        (t for t in git('tag', '--merged', 'HEAD').split() if FINAL_TAG.match(t)),
        key=lambda t: tuple(int(part) for part in t[1:].split('.')),
    )
    unpublished = [t for t in finals if t[1:] not in published]
    start = next((t for t in reversed(finals) if t[1:] in published), None)
    args = ['--ignore-tags', '^(' + '|'.join(re.escape(t) for t in unpublished) + ')$'] if unpublished else []
    return cliff(*args, *([f'{start}..HEAD'] if start else []))


def report(found: list[str]) -> int:
    for line in found:
        print(line, file=sys.stderr)
    return 1 if found else 0


def release_run(version: str, published: set[str]) -> int:
    tag = f'v{version}'
    if tagged_elsewhere(tag):
        print(f'{tag} exists on an earlier commit: no release is pending, nothing is checked.')
        print(f"git-cliff's next version for the commits since: {cliff('--bumped-version')}")
        return 0
    if found := findings(version):
        return report(found)
    print(f"{tag} is git-cliff's version for this commit, with a dated CHANGELOG section.")
    print('The commits since the last published release, to compare with that section (nothing is checked):')
    print(commits_since_published(published))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a release's version against git-cliff and CHANGELOG.md.")
    modes = parser.add_subparsers(dest='mode', required=True)
    modes.add_parser('release', help='the tag run and the rehearsal').add_argument('pypi', type=Path)
    modes.add_parser('pr', help='a pull request').add_argument('base', help='the base commit')
    args = parser.parse_args(argv)
    version = version_in(INIT.read_text(encoding='utf-8'))
    if args.mode == 'release':
        return release_run(version, set(json.loads(args.pypi.read_text(encoding='utf-8'))['releases']))
    if version_in(git('show', f'{args.base}:{INIT.as_posix()}')) == version:
        print(f"{version} is also the base's version: not a release pull request, nothing to check.")
        return 0
    return report(findings(version))


if __name__ == '__main__':
    sys.exit(main())
