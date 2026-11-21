"""The release check passes a matching tag and clean artefacts, and names each thing that is wrong with either."""

import io
from pathlib import Path
import subprocess
import tarfile
import zipfile

import pytest

import check_dist

VERSION = check_dist.source_version()
SDIST = f'datatables_backend-{VERSION}'
INFO = f'datatables_backend-{VERSION}.dist-info'
METADATA = (
    f'Name: x\nVersion: {VERSION}\nRequires-Dist: pydantic>=2.10\n'
    "Requires-Dist: Python_Multipart>=0.0.9; extra == 'fastapi'\n"
)


def git(*args: str) -> bytes:
    return subprocess.run(['git', '-C', str(check_dist.ROOT), *args], capture_output=True, check=True).stdout


# The artefacts of a clean build, with every file as the commit holds it.
PACKAGE = git('ls-files', '-z', 'src/datatables_backend').decode().rstrip('\0').split('\0')
GOOD_SDIST: dict[str, bytes] = {
    f'{SDIST}/PKG-INFO': METADATA.encode(),
    f'{SDIST}/pyproject.toml': git('show', 'HEAD:pyproject.toml'),
}
GOOD_WHEEL: dict[str, bytes] = {
    **{path.removeprefix('src/'): git('show', f'HEAD:{path}') for path in PACKAGE},
    f'{INFO}/METADATA': METADATA.encode(),
    f'{INFO}/WHEEL': b'',
    f'{INFO}/RECORD': b'',
    f'{INFO}/licenses/LICENSE': git('show', 'HEAD:LICENSE'),
}


Members = dict[str, bytes] | list[tuple[str, bytes]]  # a list can hold one name twice


def pairs(members: Members) -> list[tuple[str, bytes]]:
    return list(members.items()) if isinstance(members, dict) else members


def write_sdist(path: Path, members: Members) -> None:
    with tarfile.open(path, 'w:gz') as archive:
        for name, content in pairs(members):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def write_wheel(path: Path, members: Members) -> None:
    with zipfile.ZipFile(path, 'w') as archive:
        for name, content in pairs(members):
            archive.writestr(name, content)


def build(directory: Path, sdist: Members = GOOD_SDIST, wheel: Members = GOOD_WHEEL) -> Path:
    write_sdist(directory / f'{SDIST}.tar.gz', sdist)
    write_wheel(directory / f'datatables_backend-{VERSION}-py3-none-any.whl', wheel)
    return directory


def without(members: dict[str, bytes], name: str) -> dict[str, bytes]:
    return {key: content for key, content in members.items() if key != name}


def test_tag_of_the_source_version_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert check_dist.main(['tag', f'v{VERSION}']) == 0
    assert capsys.readouterr().out == f'check_dist tag: version {VERSION}, nothing found\n'


@pytest.mark.parametrize('tag', [VERSION, f'v{VERSION}.post1', f'V{VERSION}', 'v', 'main'])
def test_any_other_tag_fails(tag: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert check_dist.main(['tag', tag]) == 1
    assert capsys.readouterr().err == f'the tag {tag} is not v{VERSION}, the version in the source\n'


def test_clean_artefacts_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / '.gitignore').write_text('*\n')  # `uv build` writes one
    assert check_dist.main(['dist', str(build(tmp_path))]) == 0
    captured = capsys.readouterr()
    assert captured.err == ''
    assert captured.out == f'check_dist dist: version {VERSION}, nothing found\n'


@pytest.mark.parametrize(
    ('member', 'finding'),
    [
        ('local/notes.md', 'local/notes.md is private and must stay out'),
        ('.claude/settings.json', '.claude/settings.json is private and must stay out'),
        ('CLAUDE.local.md', 'CLAUDE.local.md is private and must stay out'),
        ('src/local/x.py', 'src/local/x.py is private and must stay out'),
        ('untracked-scratch.txt', 'untracked-scratch.txt is not tracked by git'),
    ],
)
def test_sdist_member_that_must_stay_out_fails(
    member: str, finding: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(tmp_path, sdist={**GOOD_SDIST, f'{SDIST}/{member}': b''})
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == f'sdist: {finding}\n'


def test_sdist_member_outside_its_directory_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    build(tmp_path, sdist={**GOOD_SDIST, 'pyproject.toml': b''})
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == f'sdist: pyproject.toml is outside {SDIST}/\n'


def test_sdist_member_that_differs_from_the_commit_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    changed = GOOD_SDIST[f'{SDIST}/pyproject.toml'] + b'# added by the build\n'
    build(tmp_path, sdist={**GOOD_SDIST, f'{SDIST}/pyproject.toml': changed})
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == 'sdist: pyproject.toml is not the file as committed\n'


def test_sdist_member_that_appears_twice_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The altered copy comes first and the committed one last: reading the member by name returns the last.
    name = f'{SDIST}/pyproject.toml'
    build(tmp_path, sdist=[(name, GOOD_SDIST[name] + b'# added by the build\n'), *GOOD_SDIST.items()])
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == 'sdist: pyproject.toml appears more than once\n'


def test_wheel_member_that_appears_twice_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    name = 'datatables_backend/cache.py'
    with pytest.warns(UserWarning, match='Duplicate name'):  # zipfile's own warning when it writes one
        build(tmp_path, wheel=[(name, GOOD_WHEEL[name] + b'# added by the build\n'), *GOOD_WHEEL.items()])
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == f'wheel: {name} appears more than once\n'


def test_wheel_member_outside_the_package_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    build(tmp_path, wheel={**GOOD_WHEEL, 'tests/__init__.py': b''})
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == f'wheel: tests/__init__.py is outside datatables_backend/ and {INFO}/\n'


@pytest.mark.parametrize(
    ('member', 'source'),
    [
        ('datatables_backend/cache.py', 'src/datatables_backend/cache.py'),
        ('datatables_backend/added.py', 'src/datatables_backend/added.py'),  # the commit holds no such file
        (f'{INFO}/licenses/LICENSE', 'LICENSE'),
    ],
)
def test_wheel_member_that_differs_from_the_commit_fails(
    member: str, source: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(tmp_path, wheel={**GOOD_WHEEL, member: GOOD_WHEEL.get(member, b'') + b'# added by the build\n'})
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == f'wheel: {member} is not {source} as committed\n'


def test_wheel_without_a_file_of_the_package_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    build(tmp_path, wheel=without(GOOD_WHEEL, 'datatables_backend/cache.py'))
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == 'wheel: datatables_backend/cache.py is missing\n'


def test_wheel_with_another_file_in_its_dist_info_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    build(tmp_path, wheel={**GOOD_WHEEL, f'{INFO}/entry_points.txt': b'[console_scripts]\nx = y:z\n'})
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == f'wheel: {INFO}/entry_points.txt is not a file a wheel of this project holds\n'


def test_requirement_that_pyproject_does_not_declare_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    added = (METADATA + 'Requires-Dist: Added.Package>=1\n').encode()
    build(
        tmp_path,
        sdist={**GOOD_SDIST, f'{SDIST}/PKG-INFO': added},
        wheel={**GOOD_WHEEL, f'{INFO}/METADATA': added},
    )
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == (
        'sdist: its metadata requires added-package, which pyproject.toml does not declare\n'
        'wheel: its metadata requires added-package, which pyproject.toml does not declare\n'
    )


def test_metadata_of_another_version_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    other = b'Name: x\nVersion: 9.9.9\n'
    build(
        tmp_path,
        sdist={**GOOD_SDIST, f'{SDIST}/PKG-INFO': other},
        wheel={**GOOD_WHEEL, f'{INFO}/METADATA': other},
    )
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == (
        f'sdist: its metadata states version 9.9.9, not {VERSION}\n'
        f'wheel: its metadata states version 9.9.9, not {VERSION}\n'
    )


def test_missing_metadata_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    build(
        tmp_path,
        sdist=without(GOOD_SDIST, f'{SDIST}/PKG-INFO'),
        wheel=without(GOOD_WHEEL, f'{INFO}/METADATA'),
    )
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == 'sdist: no PKG-INFO\nwheel: no METADATA\n'


@pytest.mark.parametrize('change', ['no wheel', 'no sdist', 'second sdist', 'other version', 'stray file'])
def test_anything_but_one_sdist_and_one_wheel_of_the_version_fails(
    change: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(tmp_path)
    sdist = tmp_path / f'{SDIST}.tar.gz'
    wheel = tmp_path / f'datatables_backend-{VERSION}-py3-none-any.whl'
    if change == 'no wheel':
        wheel.unlink()
    elif change == 'no sdist':
        sdist.unlink()
    elif change == 'second sdist':
        write_sdist(tmp_path / 'datatables_backend-9.9.9.tar.gz', GOOD_SDIST)
    elif change == 'other version':
        sdist.rename(tmp_path / 'datatables_backend-9.9.9.tar.gz')
        wheel.rename(tmp_path / 'datatables_backend-9.9.9-py3-none-any.whl')
    else:
        (tmp_path / 'notes.txt').write_text('')
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    holds = sorted(path.name for path in tmp_path.iterdir())
    assert capsys.readouterr().err == f'{tmp_path} holds {holds}, not exactly {sdist.name} and {wheel.name}\n'
