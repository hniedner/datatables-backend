"""The licence exemptions in dependency-review.yml cover every version of a package (the action matches by name).

Each exemption for a wrongly listed licence was checked by hand for one version. When uv.lock moves to another, this
test fails: re-check the new version's licence, then update the version here. A test-only exemption holds only while
nothing the wheel requires depends on the package.
"""

from __future__ import annotations

from pathlib import Path
import re
import tomllib
from typing import Any

import yaml

ROOT = Path(__file__).parent.parent
CHECKED = {'aiohappyeyeballs': '2.7.1', 'protobuf': '7.36.2'}  # licence checked by hand, 2026-11-19
#: LGPL (psycopg, psycopg-binary: 3.0-only; psycopg2-binary: 2.0-or-later AND 3.0-or-later); the `containers` group's
#: PostgreSQL drivers; GPL-2.0-or-later (mysqlclient), the `mysqlclient` group's MySQL driver
TEST_ONLY = {'psycopg', 'psycopg-binary', 'psycopg2-binary', 'mysqlclient'}


def locked() -> list[dict[str, Any]]:
    return tomllib.loads((ROOT / 'uv.lock').read_text())['package']


def test_every_exemption_was_checked_for_the_locked_version():
    versions = {package['name']: package.get('version') for package in locked()}  # the project itself has none
    assert {name: versions.get(name) for name in CHECKED} == CHECKED


def test_the_exemptions_are_exactly_the_checked_and_the_test_only_packages():
    workflow = yaml.safe_load((ROOT / '.github/workflows/dependency-review.yml').read_text())
    review = next(step for step in workflow['jobs']['dependency-review']['steps'] if step.get('id') == 'review')
    listed = {
        purl.strip().removeprefix('pkg:pypi/') for purl in review['with']['allow-dependencies-licenses'].split(',')
    }
    assert listed == set(CHECKED) | TEST_ONLY


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


def test_nothing_the_wheel_requires_depends_on_a_test_only_package():
    project = next(package for package in locked() if package['name'] == 'datatables-backend')
    pending = [d['name'] for d in project.get('dependencies', [])]
    pending += [d['name'] for extra in project.get('optional-dependencies', {}).values() for d in extra]
    reached = reachable(pending)
    assert reached, 'the walk found no requirement of the wheel'
    assert not reached & TEST_ONLY


def test_the_dev_group_does_not_depend_on_a_test_only_package():
    project = next(package for package in locked() if package['name'] == 'datatables-backend')
    reached = reachable([d['name'] for d in project.get('dev-dependencies', {}).get('dev', [])])
    assert reached, 'the walk found no package of the dev group'
    assert not reached & TEST_ONLY
