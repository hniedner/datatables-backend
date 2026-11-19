"""The licence exemptions in dependency-review.yml cover every version of a package (the action matches by name).

Each exemption was checked by hand for one version. When uv.lock moves to another, this test fails: re-check the
new version's licence, then update the version here.
"""

from __future__ import annotations

from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).parent.parent
CHECKED = {'aiohappyeyeballs': '2.7.1', 'protobuf': '7.36.2'}  # licence checked by hand, 2026-11-19


def test_every_exemption_was_checked_for_the_locked_version():
    packages = tomllib.loads((ROOT / 'uv.lock').read_text())['package']
    locked = {package['name']: package.get('version') for package in packages}  # the project itself has none
    assert {name: locked.get(name) for name in CHECKED} == CHECKED


def test_the_exemptions_are_exactly_the_checked_packages():
    workflow = (ROOT / '.github/workflows/dependency-review.yml').read_text()
    listed = re.search(r'allow-dependencies-licenses: (.*)', workflow)
    assert listed is not None
    assert {purl.strip().removeprefix('pkg:pypi/') for purl in listed.group(1).split(',')} == set(CHECKED)
