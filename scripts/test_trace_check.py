"""The trace check passes a complete trace and fails on every kind of break, with one line per finding."""

import os
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


def run(
    tmp_path: Path, specs: dict[str, str] | None, prd: str | bytes | None = PRD
) -> subprocess.CompletedProcess[str]:
    prd_path = tmp_path / 'PRD.md'
    if isinstance(prd, bytes):
        prd_path.write_bytes(prd)
    elif prd is not None:
        prd_path.write_text(prd, encoding='utf-8')
    specs_path = tmp_path / 'specs'
    if specs is not None:
        specs_path.mkdir()
        for name, text in specs.items():
            path = specs_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
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
    assert result.stderr == 'SPEC-F3.1 states PRD-F3, which does not exist\n'


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
    assert result.stderr == 'PRD.md:6 is not a requirement row of the expected shape\n'


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
    assert 'is not a directory' in result.stderr


def test_missing_prd_is_a_usage_error(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd=None)
    assert result.returncode == 2
    assert 'cannot read the input' in result.stderr


def test_undecodable_input_is_a_usage_error(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd=b'| PRD-F1 | \xff | Why. | must |\n')
    assert result.returncode == 2
    assert 'cannot read the input' in result.stderr
    assert 'utf-8' in result.stderr


def test_spec_id_on_a_continuation_line_fails(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': '- **SPEC-F1.1** One, as\n  SPEC-F2.1 says.\n- **SPEC-F2.1** Two.\n'})
    assert result.returncode == 1
    assert result.stderr == 'F.md:2 names a SPEC id inside a statement outside backticks\n'


def test_spec_id_inside_a_statement_fails(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': '- **SPEC-F1.1** One, as SPEC-F2.1 says.\n- **SPEC-F2.1** Two.\n'})
    assert result.returncode == 1
    assert result.stderr == 'F.md:1 names a SPEC id inside a statement outside backticks\n'


def test_backticked_spec_id_inside_a_statement_passes(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': '- **SPEC-F1.1** One, as `SPEC-F2.1` says.\n- **SPEC-F2.1** Two.\n'})
    assert result.returncode == 0, result.stderr


def test_lazy_continuation_line_fails(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': '- **SPEC-F1.1** One, wrapped\nonto PRD-F9.\n- **SPEC-F2.1** Two.\n'})
    assert result.returncode == 1
    assert result.stderr == 'F.md:2 follows a statement without a blank line or indentation\n'


def test_spec_id_on_a_lazy_line_is_also_reported(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': '- **SPEC-F1.1** One, as\nSPEC-F2.1 says.\n- **SPEC-F2.1** Two.\n'})
    assert result.returncode == 1
    assert result.stderr == (
        'F.md:2 follows a statement without a blank line or indentation\n'
        'F.md:2 holds a SPEC id outside a statement of the expected shape\n'
    )


@pytest.mark.parametrize(
    'after',
    [
        '   ',
        '# Heading naming PRD-F9',
        '1. note naming PRD-F9',
        '* note naming PRD-F9',
        '+ note naming PRD-F9',
        '1) note naming PRD-F9',
    ],
)
def test_statement_ends_at_a_blank_heading_or_other_item(tmp_path: Path, after: str) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + after + '\nProse naming PRD-F9.\n'})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('name', ['sub/X.md', 'notes.txt'])
def test_only_markdown_directly_in_the_directory_is_read(tmp_path: Path, name: str) -> None:
    result = run(tmp_path, {'F.md': COMPLETE, name: '- **SPEC-F9.1** Not read.\n'})
    assert result.returncode == 0, result.stderr


def test_indented_statement_is_stray(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + '\n  - **SPEC-F1.2** Indented.\n'})
    assert result.returncode == 1
    assert result.stderr == 'F.md:4 holds a SPEC id outside a statement of the expected shape\n'


@pytest.mark.parametrize(
    'row',
    [
        '  | PRD-F3 | Text. | Why. | must |',
        '| **PRD-F3** | Text. | Why. | must |',
        '| `PRD-F3` | Text. | Why. | must |',
        '| PRD-F3 |  | Why. | must |',
        '| PRD-f3 | Text. | Why. | must |',
        '| PRD-3 | Text. | Why. | must |',
        '| ~~PRD-F3~~ | Text. | Why. | must |',
        '| PRD-F3 |   | Why. | must |',
        '| PRD-F | Text. | Why. | must |',
        'PRD-F3 | Text. | Why. | must |',
        '| [PRD-F3](#f3) | Text. | Why. | must |',
        '| _PRD-F3_ | Text. | Why. | must |',
        '| PRD-F3 Text. Why. must',
        '| PRD-F3',
    ],
)
def test_more_drifted_requirement_rows_fail(tmp_path: Path, row: str) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd=PRD + row + '\n')
    assert result.returncode == 1
    assert result.stderr == 'PRD.md:6 is not a requirement row of the expected shape\n'


def test_trailing_spaces_on_a_row_are_ignored(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd=PRD.replace('| must |\n', '| must |   \n'))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('bad', ['PRD-f9', 'PRD-9', 'PRD-F', 'PRD-F1..F99', 'PRD-F1-2', 'PRD-F1a'])
def test_malformed_citation_fails(tmp_path: Path, bad: str) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + f'- **SPEC-F1.2** See {bad}.\n'})
    assert result.returncode == 1
    assert result.stderr == f'SPEC-F1.2 cites the malformed id {bad}\n'


def test_malformed_spec_id_fails(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + '- **SPEC-Fx.2** Bad id.\n'})
    assert result.returncode == 1
    assert result.stderr == 'F.md:3 holds a SPEC id outside a statement of the expected shape\n'


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
    result = run(tmp_path, {'F.md': COMPLETE + '\n' + line + '\n'})
    assert result.returncode == 1
    assert result.stderr == 'F.md:4 holds a SPEC id outside a statement of the expected shape\n'


def test_backticked_spec_id_is_text(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + '\nThe form is `- **SPEC-X1.n**`.\n'})
    assert result.returncode == 0, result.stderr


def test_readme_is_not_read(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': '- **SPEC-F1.1** One.\n', 'README.md': '- **SPEC-F2.1** Example.\n'})
    assert result.returncode == 1
    assert result.stderr == 'PRD-F2 has no spec statement\n'


def test_backticked_citation_is_checked(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + '- **SPEC-F1.2** See `PRD-F9`.\n'})
    assert result.returncode == 1
    assert result.stderr == 'SPEC-F1.2 cites PRD-F9, which does not exist\n'


def test_multi_digit_ids_trace(tmp_path: Path) -> None:
    prd = PRD + '| PRD-F12 | Twelve. | Why. | must |\n'
    result = run(tmp_path, {'F.md': COMPLETE + '- **SPEC-F12.1** Twelve, see PRD-F12.\n'}, prd=prd)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not hasattr(os, 'geteuid') or os.geteuid() == 0, reason='root reads any directory')
def test_unreadable_specs_directory_is_a_usage_error(tmp_path: Path) -> None:
    specs = tmp_path / 'specs'
    specs.mkdir()
    (specs / 'F.md').write_text(COMPLETE, encoding='utf-8')
    (tmp_path / 'PRD.md').write_text(PRD, encoding='utf-8')
    specs.chmod(0)
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), '--prd', str(tmp_path / 'PRD.md'), '--specs', str(specs)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        specs.chmod(0o755)
    assert result.returncode == 2
    assert 'cannot read the input' in result.stderr
