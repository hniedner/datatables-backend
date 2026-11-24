"""A general invariant of ci.yml: its one required check waits for every other job."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent


def test_gate_needs_every_other_job() -> None:
    """`gate` is ci.yml's one required check: a job it does not need blocks neither a merge nor a release."""
    jobs = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    assert set(jobs) - {'gate'} == set(jobs['gate']['needs'])
