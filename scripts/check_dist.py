"""Check a release before it is built and after: standard library and git only.

``check_dist.py tag TAG`` passes when TAG is ``v`` followed by the version in ``src/datatables_backend/__init__.py``,
the file the build reads it from.

``check_dist.py dist DIR`` passes when DIR holds exactly one sdist and one wheel of that version (and nothing else but
the ``.gitignore`` that ``uv build`` writes there) and:

- no member name appears more than once in the sdist or in the wheel (reading a member by name returns its last
  copy, and which copy an installer keeps depends on its reader);
- each file's metadata states that version, and every requirement it states names a package that the committed
  ``pyproject.toml`` declares as a dependency or under an extra;
- every sdist member lies under ``datatables_backend-<version>/`` and is ``PKG-INFO`` or a file git tracks, so a
  build in a working checkout cannot ship an untracked file;
- no sdist member has ``local``, ``.claude`` or ``CLAUDE.local.md`` as any part of its path, at any depth (the three at
  the top of the checkout are untracked today, so the rule above already covers them; they are named for a message
  that says what went wrong);
- every sdist member but ``PKG-INFO`` is, byte for byte, the file the commit (``HEAD``) holds;
- every wheel member lies under ``datatables_backend/`` or ``datatables_backend-<version>.dist-info/``;
- the wheel's ``datatables_backend/`` holds exactly the files git tracks under ``src/datatables_backend/``, each as
  the commit holds it;
- the wheel's dist-info holds nothing but ``METADATA``, ``WHEEL``, ``RECORD`` and ``licenses/LICENSE``, the last as
  the commit holds ``LICENSE``.

The build writes ``PKG-INFO``, ``METADATA``, ``WHEEL`` and ``RECORD`` itself, so git has nothing to compare them
with: of their content only the version and the requirement names are checked.

Findings go to stderr, one line each, with exit status 1. Run it from anywhere inside the checkout.
"""

import argparse
from collections import Counter
import email
from pathlib import Path
from pathlib import PurePosixPath
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parent.parent
NAME = 'datatables_backend'
PRIVATE = ('local', '.claude', 'CLAUDE.local.md')
GENERATED = ('METADATA', 'WHEEL', 'RECORD')  # the dist-info files the build writes itself
TWICE = 'appears more than once'


def source_version() -> str:
    text = (ROOT / 'src' / NAME / '__init__.py').read_text(encoding='utf-8')
    (version,) = re.findall(r"^__version__ = '([^']+)'$", text, re.MULTILINE)
    return version


def tracked_files() -> set[str]:
    listed = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True, text=True, check=True)
    return set(listed.stdout.split('\0')) - {''}


def committed(path: str) -> bytes | None:
    """The file as HEAD holds it; None when HEAD holds no such file."""
    shown = subprocess.run(['git', '-C', str(ROOT), 'show', f'HEAD:{path}'], capture_output=True, check=False)
    return shown.stdout if shown.returncode == 0 else None


def repeated(names: list[str]) -> list[str]:
    return sorted(name for name, count in Counter(names).items() if count > 1)


def canonical(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def requirement_name(requirement: str) -> str:
    return canonical(re.split(r'[^A-Za-z0-9._-]', requirement.strip(), maxsplit=1)[0])


def declared_requirements() -> set[str]:
    project = tomllib.loads((committed('pyproject.toml') or b'').decode())['project']  # no such file: a KeyError
    extras = [r for requirements in project['optional-dependencies'].values() for r in requirements]
    return {requirement_name(r) for r in [*project['dependencies'], *extras]}


def check_tag(tag: str, version: str) -> list[str]:
    return [] if tag == f'v{version}' else [f'the tag {tag} is not v{version}, the version in the source']


def check_metadata(what: str, text: str, version: str) -> list[str]:
    metadata = email.message_from_string(text)
    stated = metadata['Version']
    findings = [] if stated == version else [f'{what}: its metadata states version {stated}, not {version}']
    required = {requirement_name(r) for r in metadata.get_all('Requires-Dist') or []}
    return findings + [
        f'{what}: its metadata requires {name}, which pyproject.toml does not declare'
        for name in sorted(required - declared_requirements())
    ]


def check_sdist(path: Path, version: str, tracked: set[str]) -> list[str]:
    prefix = f'{NAME}-{version}/'
    with tarfile.open(path) as archive:
        findings = [f'sdist: {name.removeprefix(prefix)} {TWICE}' for name in repeated(archive.getnames())]
        for name in dict.fromkeys(archive.getnames()):
            inner = name.removeprefix(prefix)
            member = archive.extractfile(name)
            content = member.read() if member else None  # None: not a regular file
            if inner == name:
                findings.append(f'sdist: {name} is outside {prefix}')
            elif set(PurePosixPath(inner).parts) & set(PRIVATE):
                findings.append(f'sdist: {inner} is private and must stay out')
            elif inner == 'PKG-INFO':
                findings += check_metadata('sdist', (content or b'').decode(), version)
            elif inner not in tracked:
                findings.append(f'sdist: {inner} is not tracked by git')
            elif content != committed(inner):
                findings.append(f'sdist: {inner} is not the file as committed')
        if prefix + 'PKG-INFO' not in archive.getnames():
            findings.append('sdist: no PKG-INFO')
    return findings


def check_wheel(path: Path, version: str, tracked: set[str]) -> list[str]:
    info = f'{NAME}-{version}.dist-info/'
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        findings = [f'wheel: {name} {TWICE}' for name in repeated(names)]
        for name in dict.fromkeys(names):
            if name.startswith(f'{NAME}/'):
                source = f'src/{name}'
            elif name == f'{info}licenses/LICENSE':
                source = 'LICENSE'
            elif name.startswith(info):
                if name.removeprefix(info) not in GENERATED:
                    findings.append(f'wheel: {name} is not a file a wheel of this project holds')
                continue
            else:
                findings.append(f'wheel: {name} is outside {NAME}/ and {info}')
                continue
            if archive.read(name) != committed(source):
                findings.append(f'wheel: {name} is not {source} as committed')
        package = {p.removeprefix('src/') for p in tracked if p.startswith(f'src/{NAME}/')}
        findings += [f'wheel: {name} is missing' for name in sorted(package - set(names))]
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
    return check_sdist(directory / sdist, version, tracked) + check_wheel(directory / wheel, version, tracked)


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
