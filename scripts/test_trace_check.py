"""The trace check passes a complete trace and fails on every kind of break, with one line per finding."""

from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).with_name('trace_check.py')

PRD = """\
| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-F1 | A search, `a\\|b`. | Why. | must |
| PRD-F2 | Another. | Why. | should |
| PRD-L1 | Later. | Why. | later |
"""

COMPLETE = '- **SPEC-F1.1** One.\n- **SPEC-F2.1** Two, see PRD-F1.\n'


def run(tmp_path: Path, specs: dict[str, str] | None, prd: str | None = PRD) -> subprocess.CompletedProcess[str]:
    prd_path = tmp_path / 'PRD.md'
    if prd is not None:
        prd_path.write_text(prd, encoding='utf-8')
    specs_path = tmp_path / 'specs'
    if specs is not None:
        specs_path.mkdir()
        for name, text in specs.items():
            (specs_path / name).write_text(text, encoding='utf-8')
    return subprocess.run(
        [sys.executable, str(SCRIPT), '--prd', str(prd_path), '--specs', str(specs_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_complete_trace_passes_and_counts(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE})
    assert result.returncode == 0, result.stderr
    assert result.stderr == ''
    assert result.stdout == 'trace check: 3 requirements (1 must, 1 should, 1 later), 2 statements, all traced\n'


def test_findings_are_exact_lines_on_stderr(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': '- **SPEC-F1.1** One, see PRD-F9.\n- **SPEC-F1.1** Again.\n'})
    assert result.returncode == 1
    assert result.stdout == ''
    assert result.stderr == (
        'PRD-F2 has no spec statement\nSPEC-F1.1 cites PRD-F9, which does not exist\nSPEC-F1.1 is defined 2 times\n'
    )


def test_statement_citing_unknown_requirement_fails(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + '- **SPEC-F3.1** Three.\n'})
    assert result.returncode == 1
    assert result.stderr == 'SPEC-F3.1 cites PRD-F3, which does not exist\n'


def test_statement_of_a_later_requirement_is_allowed(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + '- **SPEC-L1.1** Later.\n'})
    assert result.returncode == 0, result.stderr


def test_every_spec_file_is_read(tmp_path: Path) -> None:
    result = run(tmp_path, {'A.md': '- **SPEC-F1.1** One.\n', 'B.md': '- **SPEC-F2.1** Two.\n'})
    assert result.returncode == 0, result.stderr


def test_duplicate_across_files_fails(tmp_path: Path) -> None:
    result = run(tmp_path, {'A.md': COMPLETE, 'B.md': '- **SPEC-F2.1** Two again.\n'})
    assert result.returncode == 1
    assert result.stderr == 'SPEC-F2.1 is defined 2 times\n'


def test_statement_numbers_compare_as_integers(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + '- **SPEC-F1.01** Same id.\n'})
    assert result.returncode == 1
    assert result.stderr == 'SPEC-F1.1 is defined 2 times\n'


def test_citation_on_a_continuation_line_is_checked(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': '- **SPEC-F1.1** One, wrapped\n  onto PRD-F9.\n- **SPEC-F2.1** Two.\n'})
    assert result.returncode == 1
    assert result.stderr == 'SPEC-F1.1 cites PRD-F9, which does not exist\n'


def test_continuation_ends_at_a_blank_line(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + '\nProse naming PRD-F9.\n'})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    'row',
    [
        '| PRD-F3 | Text. | Why. | Must |',
        '|PRD-F3| Text. | Why. | must |',
        '| PRD-F3 | Text. | Why. | **must** |',
        '| PRD-F3  | Text. | Why. | must |',
        '| PRD-F3 | Text. | Why. | must | extra |',
    ],
)
def test_drifted_requirement_row_fails(tmp_path: Path, row: str) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd=PRD + row + '\n')
    assert result.returncode == 1
    assert result.stderr == 'PRD row 6 is not a requirement row of the expected shape\n'


def test_duplicate_requirement_id_fails(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd=PRD + '| PRD-F1 | Again. | Why. | later |\n')
    assert result.returncode == 1
    assert result.stderr == 'PRD-F1 is defined 2 times in the PRD\n'


def test_no_requirement_fails(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd='# A PRD whose table moved\n')
    assert result.returncode == 1
    assert 'the PRD holds no requirement\n' in result.stderr


def test_no_statement_fails(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': '# Nothing yet\n'})
    assert result.returncode == 1
    assert 'the specs hold no statement\n' in result.stderr


def test_missing_specs_directory_is_a_usage_error(tmp_path: Path) -> None:
    result = run(tmp_path, None)
    assert result.returncode == 2


def test_missing_prd_is_a_usage_error(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd=None)
    assert result.returncode == 2


@pytest.mark.parametrize(
    'line',
    [
        '* **SPEC-F1.2** Star bullet.',
        '1. **SPEC-F1.2** Numbered.',
        '**SPEC-F1.2:** No bullet.',
        '-  **SPEC-F1.2** Two spaces.',
        '- **SPEC-F1** No number.',
    ],
)
def test_malformed_statement_line_fails(tmp_path: Path, line: str) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + line + '\n'})
    assert result.returncode == 1
    assert result.stderr == 'F.md:3 holds a SPEC id outside a statement of the expected shape\n'


def test_backticked_spec_id_is_text(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + 'The form is `- **SPEC-X1.n**`.\n'})
    assert result.returncode == 0, result.stderr


def test_readme_is_not_read(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': '- **SPEC-F1.1** One.\n', 'README.md': '- **SPEC-F2.1** Example.\n'})
    assert result.returncode == 1
    assert result.stderr == 'PRD-F2 has no spec statement\n'
