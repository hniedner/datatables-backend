"""Check a release before it is built and after: standard library and git only.

``check_dist.py tag TAG`` passes when TAG is ``v`` followed by the version in ``src/datatables_backend/__init__.py``,
the file the build reads it from.

``check_dist.py dist DIR`` passes when DIR holds exactly one sdist and one wheel of that version (and nothing else but
the ``.gitignore`` that ``uv build`` writes there) and:

- each file's metadata states that version;
- every sdist member lies under ``datatables_backend-<version>/`` and is ``PKG-INFO`` or a file git tracks, so a
  build in a working checkout cannot ship an untracked file;
- no sdist member has ``local``, ``.claude`` or ``CLAUDE.local.md`` as any part of its path, at any depth (the three at
  the top of the checkout are untracked today, so the rule above already covers them; they are named for a message
  that says what went wrong);
- every wheel member lies under ``datatables_backend/`` or ``datatables_backend-<version>.dist-info/``.

Findings go to stderr, one line each, with exit status 1. Run it from anywhere inside the checkout.
"""

import argparse
import email
from pathlib import Path
from pathlib import PurePosixPath
import re
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
NAME = 'datatables_backend'
PRIVATE = ('local', '.claude', 'CLAUDE.local.md')


def source_version() -> str:
    text = (ROOT / 'src' / NAME / '__init__.py').read_text(encoding='utf-8')
    (version,) = re.findall(r"^__version__ = '([^']+)'$", text, re.MULTILINE)
    return version


def tracked_files() -> set[str]:
    listed = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True, text=True, check=True)
    return set(listed.stdout.split('\0')) - {''}


def check_tag(tag: str, version: str) -> list[str]:
    return [] if tag == f'v{version}' else [f'the tag {tag} is not v{version}, the version in the source']


def check_metadata(what: str, text: str, version: str) -> list[str]:
    stated = email.message_from_string(text)['Version']
    return [] if stated == version else [f'{what}: its metadata states version {stated}, not {version}']


def check_sdist(path: Path, version: str, tracked: set[str]) -> list[str]:
    prefix = f'{NAME}-{version}/'
    findings: list[str] = []
    with tarfile.open(path) as archive:
        for name in archive.getnames():
            inner = name.removeprefix(prefix)
            if inner == name:
                findings.append(f'sdist: {name} is outside {prefix}')
            elif set(PurePosixPath(inner).parts) & set(PRIVATE):
                findings.append(f'sdist: {inner} is private and must stay out')
            elif inner == 'PKG-INFO':
                member = archive.extractfile(name)
                assert member is not None  # noqa: S101  a name that is not a regular file fails loudly
                findings += check_metadata('sdist', member.read().decode(), version)
            elif inner not in tracked:
                findings.append(f'sdist: {inner} is not tracked by git')
        if prefix + 'PKG-INFO' not in archive.getnames():
            findings.append('sdist: no PKG-INFO')
    return findings


def check_wheel(path: Path, version: str) -> list[str]:
    info = f'{NAME}-{version}.dist-info/'
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        findings = [f'wheel: {n} is outside {NAME}/ and {info}' for n in names if not n.startswith((f'{NAME}/', info))]
        if info + 'METADATA' in names:
            findings += check_metadata('wheel', archive.read(info + 'METADATA').decode(), version)
        else:
            findings.append('wheel: no METADATA')
    return findings


def check_dist(directory: Path, version: str, tracked: set[str]) -> list[str]:
    sdist, wheel = f'{NAME}-{version}.tar.gz', f'{NAME}-{version}-py3-none-any.whl'
    found = {path.name for path in directory.iterdir()} - {'.gitignore'}
    if found != {sdist, wheel}:
        return [f'{directory} holds {sorted(found)}, not exactly {sdist} and {wheel}']
    return check_sdist(directory / sdist, version, tracked) + check_wheel(directory / wheel, version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Check a release tag, or the built sdist and wheel.')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('tag').add_argument('tag')
    commands.add_parser('dist').add_argument('directory', type=Path)
    args = parser.parse_args(argv)
    version = source_version()
    if args.command == 'tag':
        findings = check_tag(args.tag, version)
    else:
        findings = check_dist(args.directory, version, tracked_files())
    for finding in findings:
        print(finding, file=sys.stderr)
    if not findings:
        print(f'check_dist {args.command}: version {version}, nothing found')
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
