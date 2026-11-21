"""The release check passes a matching tag and clean artefacts, and names each thing that is wrong with either."""

import io
from pathlib import Path
import tarfile
import zipfile

import pytest

import check_dist

VERSION = check_dist.source_version()
SDIST = f'datatables_backend-{VERSION}'
INFO = f'datatables_backend-{VERSION}.dist-info'
GOOD_SDIST = {f'{SDIST}/PKG-INFO': f'Name: x\nVersion: {VERSION}\n', f'{SDIST}/pyproject.toml': ''}
GOOD_WHEEL = {'datatables_backend/__init__.py': '', f'{INFO}/METADATA': f'Name: x\nVersion: {VERSION}\n'}


def write_sdist(path: Path, members: dict[str, str]) -> None:
    with tarfile.open(path, 'w:gz') as archive:
        for name, text in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(text.encode())
            archive.addfile(info, io.BytesIO(text.encode()))


def write_wheel(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, 'w') as archive:
        for name, text in members.items():
            archive.writestr(name, text)


def build(directory: Path, sdist: dict[str, str] = GOOD_SDIST, wheel: dict[str, str] = GOOD_WHEEL) -> Path:
    write_sdist(directory / f'{SDIST}.tar.gz', sdist)
    write_wheel(directory / f'datatables_backend-{VERSION}-py3-none-any.whl', wheel)
    return directory


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
    build(tmp_path, sdist={**GOOD_SDIST, f'{SDIST}/{member}': ''})
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == f'sdist: {finding}\n'


def test_sdist_member_outside_its_directory_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    build(tmp_path, sdist={**GOOD_SDIST, 'pyproject.toml': ''})
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == f'sdist: pyproject.toml is outside {SDIST}/\n'


def test_wheel_member_outside_the_package_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    build(tmp_path, wheel={**GOOD_WHEEL, 'tests/__init__.py': ''})
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == f'wheel: tests/__init__.py is outside datatables_backend/ and {INFO}/\n'


def test_metadata_of_another_version_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    other = 'Name: x\nVersion: 9.9.9\n'
    build(tmp_path, sdist={f'{SDIST}/PKG-INFO': other}, wheel={f'{INFO}/METADATA': other})
    assert check_dist.main(['dist', str(tmp_path)]) == 1
    assert capsys.readouterr().err == (
        f'sdist: its metadata states version 9.9.9, not {VERSION}\n'
        f'wheel: its metadata states version 9.9.9, not {VERSION}\n'
    )


def test_missing_metadata_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    build(tmp_path, sdist={f'{SDIST}/pyproject.toml': ''}, wheel={'datatables_backend/__init__.py': ''})
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
