"""The trace check passes a complete trace and fails on every kind of break, with one line per finding."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).with_name('trace_check.py')

PRD = """\
### 4.1 Search (F)

#### PRD-F1

A search, wrapped
onto a second line.

Its second paragraph.

- **Reason:** Why, wrapped
  onto a second line.
- **Priority:** must

#### PRD-F2

Another.

- **Reason:** Why.
- **Priority:** should

### 4.2 Later (L)

#### PRD-L1

Later.

- **Reason:** Why.
- **Priority:** later
"""

VALID = '\n\nText.\n\n- **Reason:** Why.\n- **Priority:** must\n\n'

COMPLETE = '- **SPEC-F1.1** One.\n- **SPEC-F2.1** Two, see PRD-F1.\n'


def run(
    tmp_path: Path,
    specs: dict[str, str] | None,
    prd: str | bytes | None = PRD,
    tests: dict[str, str] | None = None,
    enforce: tuple[str, ...] | None = (),
) -> subprocess.CompletedProcess[str]:
    tests_path = tmp_path / 'tests'
    tests_path.mkdir()
    for name, text in (tests or {}).items():
        (tests_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tests_path / name).write_text(text, encoding='utf-8')
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
        [
            sys.executable,
            str(SCRIPT),
            '--prd',
            str(prd_path),
            '--specs',
            str(specs_path),
            '--tests',
            str(tests_path),
            *([] if enforce is None else ['--enforce', *enforce]),
        ],
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
    'body',
    [
        '\n\nText.\n\n- **Reason:** Why.\n- **Priority:** Must\n\n',
        '\n\nText.\n\n- **Reason:** Why.\n\n',
        '\n\nText.\n\n- **Priority:** must\n\n',
        '\n\n- **Reason:** Why.\n- **Priority:** must\n\n',
        '\n\nText.\n\n- **Reason:**\n- **Priority:** must\n\n',
        '\n\nText.\n\n- **Reason:** Why.\n- **Priority:** must\n\nMore.\n\n',
        '\n\nText.\n\n- **Reason:** Why,\nunindented.\n- **Priority:** must\n\n',
        '\n\nText.\n\n- **Reason:** A.\n- **Reason:** B.\n- **Priority:** must\n\n',
        '\n\nText.\n\n- **Priority:** must\n- **Reason:** Why.\n- **Priority:** must\n\n',
    ],
)
def test_drifted_requirement_section_fails(tmp_path: Path, body: str) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd='#### PRD-F3' + body + PRD)
    assert result.returncode == 1
    assert result.stderr == 'PRD.md:1 is not a requirement section of the expected shape\n'


@pytest.mark.parametrize(
    'heading', ['### PRD-F3', '#### PRD-f3', '#### PRD-F3: a title', '   #### PRD-F3', '### More on PRD-F3']
)
def test_drifted_requirement_heading_fails(tmp_path: Path, heading: str) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd=PRD + '\n' + heading + VALID)
    assert result.returncode == 1
    assert result.stderr == f'PRD.md:{PRD.count("\n") + 2} is not a requirement heading of the expected shape\n'


@pytest.mark.parametrize('row', ['| PRD-F3 | Text. | Why. | must |', '| **[PRD-F3](#prd-f3)** | Text. | Why. | must |'])
def test_requirement_in_table_form_fails(tmp_path: Path, row: str) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd=row + '\n\n' + PRD)
    assert result.returncode == 1
    assert result.stderr == 'PRD.md:1 is a requirement row, not a section\n'


def test_duplicate_requirement_id_fails(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd=PRD + '\n#### PRD-F1' + VALID)
    assert result.returncode == 1
    assert result.stderr == 'PRD-F1 is defined 2 times in the PRD\n'


def test_no_requirement_fails(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd='# A PRD with no requirement\n')
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
    result = run(tmp_path, {'F.md': COMPLETE}, prd=b'#### PRD-F1\n\n\xff\n')
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
        '10. note naming PRD-F9',
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


def test_trailing_spaces_on_a_line_are_ignored(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd=PRD.replace('** must\n', '** must   \n'))
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


def test_statement_with_no_text_is_stray(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + '- **SPEC-F1.2**   \n'})
    assert result.returncode == 1
    assert result.stderr == 'F.md:3 holds a SPEC id outside a statement of the expected shape\n'


def test_prose_naming_requirements_in_the_prd_is_not_a_requirement(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE}, prd="PRD-F1's set, `a|b` included.\n\n" + PRD)
    assert result.returncode == 0, result.stderr


def test_directory_named_like_markdown_is_skipped(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE, 'x.md/G.md': '- **SPEC-F9.1** Not read.\n'})
    assert result.returncode == 0, result.stderr


def test_tab_indented_continuation_is_read(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': '- **SPEC-F1.1** One,\n\tsee PRD-F9.\n- **SPEC-F2.1** Two.\n'})
    assert result.returncode == 1
    assert result.stderr == 'SPEC-F1.1 cites PRD-F9, which does not exist\n'


def test_text_between_code_spans_is_not_backticked(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + '- **SPEC-F1.2** `a` SPEC-F2.1 `b`.\n'})
    assert result.returncode == 1
    assert result.stderr == 'F.md:3 names a SPEC id inside a statement outside backticks\n'


def test_code_span_wrapped_across_lines_is_not_a_span(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + '- **SPEC-F1.2** See `SPEC-F2.1\n  continued`.\n'})
    assert result.returncode == 1
    assert result.stderr == 'F.md:3 names a SPEC id inside a statement outside backticks\n'


@pytest.mark.parametrize('after', ['**Note** prose.', '2.0 is prose.'])
def test_prose_right_after_a_statement_fails(tmp_path: Path, after: str) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + after + '\n'})
    assert result.returncode == 1
    assert result.stderr == 'F.md:3 follows a statement without a blank line or indentation\n'


def test_lazy_line_after_a_continuation_fails(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': '- **SPEC-F1.1** One,\n  more,\nlazy.\n- **SPEC-F2.1** Two.\n'})
    assert result.returncode == 1
    assert result.stderr == 'F.md:3 follows a statement without a blank line or indentation\n'


def test_two_spaces_after_the_id_is_stray(tmp_path: Path) -> None:
    result = run(tmp_path, {'F.md': COMPLETE + '- **SPEC-F1.2**  Two spaces.\n'})
    assert result.returncode == 1
    assert result.stderr == 'F.md:3 holds a SPEC id outside a statement of the expected shape\n'


def test_multi_digit_ids_trace(tmp_path: Path) -> None:
    prd = PRD + '\n#### PRD-F12' + VALID
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


def test_enforced_statement_no_test_names_fails(tmp_path: Path) -> None:
    """A behaviour no test names must not pass the gate."""
    result = run(tmp_path, {'F.md': COMPLETE}, tests={'test_a.py': '"""SPEC-F9.9 is elsewhere."""\n'}, enforce=('F',))
    assert result.returncode == 1
    assert result.stderr == (
        'SPEC-F1.1 is cited by no test\n'
        'SPEC-F2.1 is cited by no test\n'
        'test_a.py cites SPEC-F9.9, which does not exist\n'
    )


def test_enforcing_one_requirement_leaves_the_others_of_its_file_alone(tmp_path: Path) -> None:
    """The rollout is by requirement, and a test's `SPEC-F1.01` is statement F1.1, as the specs count."""
    result = run(tmp_path, {'F.md': COMPLETE}, tests={'sub/test_a.py': '# SPEC-F1.01\n'}, enforce=('PRD-F1',))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ('statement', 'exempt'),
    [
        ('[review] Read.', True),
        ('[CI tests] Run.', True),
        ('[OS] Fetch. [review] Read.', False),
        ('[all] Fetch. [CI gate] Run.', False),
    ],
)
def test_only_a_statement_whose_first_tag_is_review_or_ci_needs_no_test(
    tmp_path: Path, statement: str, exempt: bool
) -> None:
    """A mixed statement carries behaviour that a test must name."""
    result = run(tmp_path, {'F.md': f'- **SPEC-F1.1** {statement}\n- **SPEC-F2.1** [review] Read.\n'}, enforce=('F',))
    assert (result.returncode == 0) == exempt, result.stderr


GOOD = 'reason="PRD-F1, issue #47", strict=True, raises=ValueError'


@pytest.mark.parametrize(
    ('source', 'finding'),
    [
        ('@pytest.mark.xfail(reason="#7", raises=ValueError)\ndef f(): ...\n', 'xfail is not strict'),
        ('@pytest.mark.xfail(reason="#7", strict=False, raises=ValueError)\ndef f(): ...\n', 'xfail is not strict'),
        ('@pytest.mark.xfail(reason="#7", strict=True)\ndef f(): ...\n', 'xfail names no expected exception (raises=)'),
        (
            '@pytest.mark.xfail(reason="PRD-F1", strict=True, raises=ValueError)\ndef f(): ...\n',
            'xfail names no issue',
        ),
        ('X = pytest.mark.xfail(strict=True, raises=ValueError)\n', 'xfail names no issue'),
        ('@pytest.mark.xfail\ndef f(): ...\n', 'xfail is not called; write xfail(reason=..., strict=True, raises=...)'),
        (
            'P = pytest.param(1, marks=pytest.mark.xfail)\n',
            'xfail is not called; write xfail(reason=..., strict=True, raises=...)',
        ),
        ('pytest.xfail("#7")\n', 'xfail() cannot be strict; use a strict mark'),
        ('from pytest import xfail\nxfail("#7")\n', 'xfail() cannot be strict; use a strict mark'),
        (
            'from pytest import mark\n@mark.xfail(reason="#7", strict=True)\ndef f(): ...\n',
            'xfail names no expected exception (raises=)',
        ),
        (f'@pytest.mark.xfail({GOOD})\ndef f(): ...\n', None),
        (f'X = pytest.mark.xfail({GOOD})\n', None),
        ('X = pytest.mark.xfail(reason="#47 and " "more", strict=True, raises=ValueError)\n', None),
    ],
)
def test_an_xfail_is_strict_names_its_exception_and_an_issue(tmp_path: Path, source: str, finding: str | None) -> None:
    """An xfail that is not strict, passes on any error, names no issue or is not a call hides a failure for good."""
    result = run(tmp_path, {'F.md': COMPLETE}, tests={'test_a.py': source})
    if finding is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode == 1
        line = 2 if source.startswith('from pytest import') else 1
        assert result.stderr == f'test_a.py:{line} {finding}\n'


def test_the_enforced_requirements_of_the_script_are_the_default(tmp_path: Path) -> None:
    """Without `--enforce` an uncited statement of every requirement the script enforces fails: an emptied
    default would pass every other test. The fixture holds one requirement for each entry of `ENFORCED`."""
    loaded = importlib.util.spec_from_file_location('trace_check_under_test', SCRIPT)
    assert loaded is not None and loaded.loader is not None
    module = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(module)
    ids = [entry[4:] if entry.startswith('PRD-') else f'{entry}1' for entry in module.ENFORCED]
    held = [rid for rid in ('F1', 'F2') if rid in ids]  # the requirements COMPLETE already holds, in its order
    fresh = [rid for rid in ids if rid not in held]
    prd = PRD + ''.join(f'\n#### PRD-{rid}' + VALID for rid in fresh)
    specs = COMPLETE + ''.join(f'- **SPEC-{rid}.1** Stated, see PRD-F1.\n' for rid in fresh)
    result = run(tmp_path, {'F.md': specs}, prd=prd, enforce=None)
    assert result.returncode == 1
    assert result.stderr == ''.join(f'SPEC-{rid}.1 is cited by no test\n' for rid in [*held, *fresh])


def test_an_enforce_entry_that_names_no_requirement_fails(tmp_path: Path) -> None:
    """A mistyped entry ('p', 'PRD-P99') would enforce nothing and look like a pass."""
    result = run(tmp_path, {'F.md': COMPLETE}, enforce=('p', 'PRD-P99', 'F'))
    assert result.returncode == 1
    assert result.stderr == (
        'enforce entry p names no PRD id or letter\n'
        'enforce entry PRD-P99 names no PRD id or letter\n'
        'SPEC-F1.1 is cited by no test\n'
        'SPEC-F2.1 is cited by no test\n'
    )


def test_missing_tests_directory_is_a_usage_error(tmp_path: Path) -> None:
    (tmp_path / 'PRD.md').write_text(PRD, encoding='utf-8')
    (tmp_path / 'specs').mkdir()
    (tmp_path / 'specs' / 'F.md').write_text(COMPLETE, encoding='utf-8')
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            '--prd',
            str(tmp_path / 'PRD.md'),
            '--specs',
            str(tmp_path / 'specs'),
            '--tests',
            str(tmp_path / 'none'),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert 'is not a directory' in result.stderr
