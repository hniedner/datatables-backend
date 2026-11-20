"""The trace check passes a complete trace and fails on each of its three kinds of break."""

from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).with_name('trace_check.py')

PRD = """\
| Id | Requirement | Reason | Priority |
|---|---|---|---|
| PRD-F1 | A search. | Why. | must |
| PRD-F2 | Another. | Why. | should |
| PRD-L1 | Later. | Why. | later |
"""


def run(tmp_path: Path, spec: str) -> subprocess.CompletedProcess[str]:
    prd = tmp_path / 'PRD.md'
    prd.write_text(PRD, encoding='utf-8')
    specs = tmp_path / 'specs'
    specs.mkdir()
    (specs / 'F.md').write_text(spec, encoding='utf-8')
    return subprocess.run(
        [sys.executable, str(SCRIPT), '--prd', str(prd), '--specs', str(specs)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_complete_trace_passes(tmp_path: Path) -> None:
    result = run(tmp_path, '- **SPEC-F1.1** One.\n- **SPEC-F2.1** Two, see PRD-F1.\n')
    assert result.returncode == 0, result.stdout + result.stderr


def test_requirement_without_statement_fails(tmp_path: Path) -> None:
    result = run(tmp_path, '- **SPEC-F1.1** One.\n')
    assert result.returncode == 1
    assert 'PRD-F2 has no spec statement' in result.stdout


def test_statement_citing_unknown_requirement_fails(tmp_path: Path) -> None:
    result = run(tmp_path, '- **SPEC-F1.1** One, see PRD-F9.\n- **SPEC-F2.1** Two.\n- **SPEC-F3.1** Three.\n')
    assert result.returncode == 1
    assert 'SPEC-F1.1 cites PRD-F9, which does not exist' in result.stdout
    assert 'SPEC-F3.1 cites PRD-F3, which does not exist' in result.stdout


def test_duplicate_statement_id_fails(tmp_path: Path) -> None:
    result = run(tmp_path, '- **SPEC-F1.1** One.\n- **SPEC-F2.1** Two.\n- **SPEC-F1.1** Again.\n')
    assert result.returncode == 1
    assert 'SPEC-F1.1 is defined 2 times' in result.stdout
