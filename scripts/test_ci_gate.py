"""Invariants of the workflows: gate waits for every job, and a rehearsal runs the release check."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent


def test_gate_needs_every_other_job() -> None:
    """`gate` is ci.yml's one required check: a job it does not need blocks neither a merge nor a release."""
    jobs = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    assert set(jobs) - {'gate'} == set(jobs['gate']['needs'])


def test_the_release_check_runs_in_a_rehearsal() -> None:
    """An `if: github.ref_type == 'tag'` on the check would skip it in a rehearsal, the dry run of the tag's check."""
    steps = yaml.safe_load((ROOT / '.github/workflows/release.yml').read_text())['jobs']['check-tag']['steps']
    checks = [step for step in steps if 'check_release.py release' in step.get('run', '')]
    assert len(checks) == 1
    assert 'if' not in checks[0]
