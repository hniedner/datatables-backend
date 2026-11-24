"""The licence exemptions in dependency-review.yml cover every version of a package (the action matches by name).

An exemption for a wrongly listed licence (CHECKED) was checked by hand for one version. When uv.lock moves to another,
this test fails: re-check the new version's licence, then update the version here. Any other exemption (a licence the
list does not allow) must name a package that never ships: one the dependency groups need and the wheel's requirements,
extras included, do not. The test computes that set from uv.lock, so a package that comes to be needed by the wheel
fails it.
"""

from __future__ import annotations

from pathlib import Path
import re
import tomllib
from typing import Any

import yaml

ROOT = Path(__file__).parent.parent
CHECKED = {'aiohappyeyeballs': '2.7.1', 'protobuf': '7.36.2'}  # licence checked by hand, 2026-11-19


def locked() -> list[dict[str, Any]]:
    return tomllib.loads((ROOT / 'uv.lock').read_text())['package']


def project() -> dict[str, Any]:
    return next(package for package in locked() if package['name'] == 'datatables-backend')


def reachable(pending: list[str]) -> set[str]:
    """Every locked package the named ones depend on, extras included, the named ones too."""
    packages = {package['name']: package for package in locked()}
    reached: set[str] = set()
    while pending:
        name = re.sub(r'[-_.]+', '-', pending.pop()).lower()
        if name in reached:
            continue
        reached.add(name)
        package = packages.get(name, {})
        pending += [d['name'] for d in package.get('dependencies', [])]
        pending += [d['name'] for extra in package.get('optional-dependencies', {}).values() for d in extra]
    return reached


def non_shipping() -> set[str]:
    """The packages the dependency groups (dev, containers, mysqlclient) need and the wheel does not."""
    requirements = [d['name'] for d in project().get('dependencies', [])]
    requirements += [d['name'] for extra in project().get('optional-dependencies', {}).values() for d in extra]
    shipped = reachable(requirements)
    assert shipped, 'the walk found no requirement of the wheel'
    grouped = reachable([d['name'] for group in project().get('dev-dependencies', {}).values() for d in group])
    assert grouped, 'the walk found no package of a dependency group'
    return grouped - shipped


def listed() -> set[str]:
    workflow = yaml.safe_load((ROOT / '.github/workflows/dependency-review.yml').read_text())
    review = next(step for step in workflow['jobs']['dependency-review']['steps'] if step.get('id') == 'review')
    return {purl.strip().removeprefix('pkg:pypi/') for purl in review['with']['allow-dependencies-licenses'].split(',')}


def test_every_exemption_was_checked_for_the_locked_version():
    versions = {package['name']: package.get('version') for package in locked()}  # the project itself has none
    assert {name: versions.get(name) for name in CHECKED} == CHECKED


def test_an_exemption_is_a_checked_package_or_one_that_never_ships():
    exempt = listed()
    assert set(CHECKED) <= exempt
    shipping = exempt - set(CHECKED) - non_shipping()
    assert not shipping, f'exempt, yet the wheel needs them or no dependency group does: {sorted(shipping)}'
