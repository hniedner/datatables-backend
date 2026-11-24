"""The release notes are a version's changelog section, cut at the next heading, with the links they use."""

from pathlib import Path

import pytest

import changelog_notes

CHANGELOG = """# Changelog

## [Unreleased]

## [0.2.0] - 2026-12-01

Changes since 0.1.0.

### Fixed

- A fix ([#71]) and a second link, [see the text][tag].

## [0.1.0] - 2026-11-30

The first release ([#75]).

## [0.0.9]

## [0.0.8] - 2026-11-01

- Old.

[0.2.0]: https://example.org/releases/0.2.0
[0.1.0]: https://example.org/releases/0.1.0
[#71]: https://example.org/issues/71
[#75]: https://example.org/issues/75
[Tag]: https://example.org/tag
"""


def test_a_section_is_cut_at_the_next_heading_and_has_no_heading():
    text = changelog_notes.notes(CHANGELOG, '0.1.0')
    assert text.startswith('The first release')
    assert '## [' not in text
    assert 'Changes since 0.1.0.' not in text and 'Old.' not in text


def test_a_missing_entry_fails_and_names_the_heading():
    with pytest.raises(changelog_notes.NotesError, match=r'no section "## \[0\.3\.0\]"'):
        changelog_notes.notes(CHANGELOG, '0.3.0')


def test_a_version_is_matched_whole_not_as_a_prefix():
    with pytest.raises(changelog_notes.NotesError, match='no section'):
        changelog_notes.notes(CHANGELOG, '0.1')


def test_an_empty_entry_fails():
    with pytest.raises(changelog_notes.NotesError, match='is empty'):
        changelog_notes.notes(CHANGELOG, '0.0.9')
    with pytest.raises(changelog_notes.NotesError, match='is empty'):  # only the file's definitions: still empty
        changelog_notes.notes('## [1.0.0]\n\n[x]: https://x\n', '1.0.0')


def test_the_link_references_a_section_uses_are_carried_in_the_files_order():
    text = changelog_notes.notes(CHANGELOG, '0.2.0')
    assert text.endswith('\n\n[#71]: https://example.org/issues/71\n[Tag]: https://example.org/tag\n')
    assert '[#75]' not in text and '[0.2.0]:' not in text


def test_a_section_that_uses_no_link_carries_none():
    assert changelog_notes.notes(CHANGELOG, '0.0.8') == '- Old.\n'


def test_the_last_section_does_not_swallow_the_files_definitions():
    text = changelog_notes.notes(CHANGELOG, '0.0.8')
    assert 'example.org' not in text


def test_the_command_prints_the_notes_and_fails_with_a_message(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    file = tmp_path / 'CHANGELOG.md'
    file.write_text(CHANGELOG, encoding='utf-8')
    assert changelog_notes.main(['0.1.0', '--file', str(file)]) == 0
    assert capsys.readouterr().out.startswith('The first release')
    assert changelog_notes.main(['9.9.9', '--file', str(file)]) == 1
    assert 'no section' in capsys.readouterr().err


def test_a_version_with_two_sections_fails_rather_than_picking_one():
    with pytest.raises(changelog_notes.NotesError, match='more than one'):
        changelog_notes.notes('## [1.0.0]\n\nA.\n\n## [1.0.0]\n\nB.\n', '1.0.0')
