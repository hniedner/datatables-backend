"""The commit-msg hook `commit-header` (decision 009, section 4).

The test reads the hook from `.pre-commit-config.yaml` and runs pre-commit's own pygrep command on it, so it cannot
drift from what a commit runs. CI's refusal of `fixup!`, `squash!`, `amend!`, `Merge ` and `#` headers is shell inside
a workflow step and has no test here; its simulation is in the pull request that added it.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Any

import yaml

ROOT = Path(__file__).parent.parent

TYPES = ['feat', 'fix', 'docs', 'test', 'ci', 'build', 'refactor', 'chore', 'style']

# Each case is a whole message file, as git hands it to the hook. True: the hook accepts it.
CASES: dict[str, bool] = {
    **{f'{t}: x\n': True for t in TYPES},
    'feat!: x\n': True,
    'feat(scope)!: x\n': True,
    'build(deps-dev): bump x from 1 to 2\n': True,
    'docs(PRD-F9): x\n': True,
    'ci(a/b.c_d): x\n': True,
    'fix: revert "feat: x"\n': True,
    'feat: ' + 'x' * 94 + '\n': True,
    'feat: ' + 'x' * 95 + '\n': False,
    # 100 characters, 194 bytes: the length counts characters, not bytes.
    'feat: ' + 'é' * 94 + '\n': True,
    'feat: ' + 'é' * 95 + '\n': False,
    'feat: a\n\nbody b\n': True,
    'feat: ok\n\n' + 'y' * 300 + '\n': True,
    'feat: ' + 'x' * 95 + '\n\nbody\n': False,
    # git hands the hook the message before it strips the editor's comment lines and leading blank lines.
    '\n# a comment I typed\nfeat: edited\n\nbody\n\n# Please enter the commit message\n': True,
    'Merge pull request #1 from a/b\n': True,
    "Merge branch 'main' into x\n": True,
    'fixup! feat: x\n': True,
    'squash! feat: x\n': True,
    'amend! feat: x\n': True,
    'Feat: x\n': False,
    'feat:x\n': False,
    'feat : x\n': False,
    'feat:  x\n': False,
    'feat: \n': False,
    ' feat: x\n': False,
    'feat(): x\n': False,
    'feat(a b): x\n': False,
    'feature: x\n': False,
    'perf: x\n': False,
    'Revert "feat: x"\n': False,
    'PRD: shorten the header\n': False,
    'Bump x from 1 to 2\n': False,
    '# only a comment line\n': False,
    '# comment\n# comment\n': False,
    '': False,
    '\n\n': False,
    # Only the first line counts: a typed header in the body does not rescue free prose.
    'Free prose header\n\nfeat: in the body\n': False,
}


def config() -> dict[str, Any]:
    return yaml.safe_load((ROOT / '.pre-commit-config.yaml').read_text())


def hook() -> dict[str, Any]:
    found = [h for repo in config()['repos'] for h in repo['hooks'] if h['id'] == 'commit-header']
    assert len(found) == 1, 'the commit-msg hook `commit-header` is missing or listed twice'
    return found[0]


def test_a_header_is_accepted_or_refused(tmp_path: Path) -> None:
    """A change to the pattern, its flags or its YAML quoting changes which headers a commit may carry.

    The accepted and refused sets are the rule of section 4: a dropped `--multiline` or `--negate`, `^` for `\\A`, a
    lost or extra type, a length counted in bytes, or comment lines no longer skipped each turn a row of this table.
    """
    h = hook()
    assert h['language'] == 'pygrep'
    assert h['stages'] == ['commit-msg']
    by_file: dict[str, str] = {}
    for index, message in enumerate(CASES):
        path = tmp_path / f'case{index:02}'
        path.write_bytes(message.encode())
        by_file[path.name] = message
    # The command pre-commit itself runs for a pygrep hook (pre_commit/languages/pygrep.py).
    run = subprocess.run(
        [sys.executable, '-m', 'pre_commit.languages.pygrep', *h['args'], h['entry'], *by_file],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    refused = {by_file[line] for line in run.stdout.decode().splitlines() if line}
    assert refused == {message for message, accepted in CASES.items() if not accepted}
