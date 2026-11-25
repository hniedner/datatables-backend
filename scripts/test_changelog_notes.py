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
    file.write_text(shaped(f'The first release.\n\n{UPGRADE}\n\n{SECURITY}', '0.1.0'), encoding='utf-8')
    assert changelog_notes.main(['0.1.0', '--file', str(file)]) == 0
    assert capsys.readouterr().out.startswith('The first release')
    assert changelog_notes.main(['9.9.9', '--file', str(file)]) == 1
    assert 'no section' in capsys.readouterr().err


def test_a_version_with_two_sections_fails_rather_than_picking_one():
    with pytest.raises(changelog_notes.NotesError, match='more than one'):
        changelog_notes.notes('## [1.0.0]\n\nA.\n\n## [1.0.0]\n\nB.\n', '1.0.0')


SUMMARY = 'This release fixes one thing.'
UPGRADE = '**Upgrade impact:** none; no change to your code is needed.'
SECURITY = '**Security:** none; this release fixes no known vulnerability.'


def shaped(opening: str, version: str = '1.0.0') -> str:
    """A changelog whose one section has `opening`, then a Keep a Changelog group."""
    return f'## [{version}] - 2026-01-02\n\n{opening}\n\n### Fixed\n\n- A fix.\n'


def checked(text: str, version: str = '1.0.0') -> str:
    return changelog_notes.check_shape(changelog_notes.notes(text, version), version) or ''


def test_a_section_with_a_summary_and_both_lines_passes():
    """SPEC-Q: the shape of CONTRIBUTING.md, Releases: the notes are returned unchanged when it holds."""
    text = shaped(f'{SUMMARY}\n\n{UPGRADE}\n\n{SECURITY}')
    checked(text)
    assert changelog_notes.notes(text, '1.0.0').startswith(SUMMARY)


@pytest.mark.parametrize(
    ('opening', 'missing'),
    [
        (f'{SUMMARY}\n\n{SECURITY}', '**Upgrade impact:**'),
        (f'{SUMMARY}\n\n{UPGRADE}', '**Security:**'),
    ],
    ids=['no upgrade impact', 'no security'],
)
def test_a_missing_line_fails_and_names_the_line_and_the_fix(opening: str, missing: str):
    with pytest.raises(changelog_notes.ShapeError) as raised:
        checked(shaped(opening))
    message = str(raised.value)
    assert f'"{missing}"' in message and 'for example' in message and 'CONTRIBUTING.md, Releases' in message
    assert '"## [1.0.0]"' in message


def test_a_section_that_opens_with_a_line_instead_of_a_summary_fails():
    with pytest.raises(changelog_notes.ShapeError, match='does not open with a summary'):
        checked(shaped(f'{UPGRADE}\n\n{SECURITY}'))
    with pytest.raises(changelog_notes.ShapeError, match='does not open with a summary'):
        checked('## [1.0.0] - 2026-01-02\n\n### Fixed\n\n- A fix.\n')


@pytest.mark.parametrize(
    'upgrade',
    [
        '**Upgrade impact:**',  # nothing after it
        '**Upgrade impact:**   ',
        '  **Upgrade impact:** none.',  # indented
        '- **Upgrade impact:** none.',  # a bullet
        'See **Upgrade impact:** none.',  # mid-line
        '**Upgrade Impact:** none.',  # another spelling
    ],
)
def test_a_line_that_is_not_the_exact_marker_with_text_does_not_count(upgrade: str):
    with pytest.raises(changelog_notes.ShapeError, match='Upgrade impact'):
        checked(shaped(f'{SUMMARY}\n\n{upgrade}\n\n{SECURITY}'))


def test_a_line_after_the_first_group_does_not_count():
    """The opening is what comes before the first `###`: a line inside a group is not the statement for the release."""
    text = f'## [1.0.0] - 2026-01-02\n\n{SUMMARY}\n\n{UPGRADE}\n\n### Fixed\n\n- A fix.\n\n{SECURITY}\n'
    with pytest.raises(changelog_notes.ShapeError, match=r'"\*\*Security:\*\*"'):
        checked(text)


def test_only_the_released_version_is_checked():
    """The earlier sections stay as published: a section without the shape passes for its own version only when it has
    it, and the check of another version never reads it."""
    text = shaped(f'{SUMMARY}\n\n{UPGRADE}\n\n{SECURITY}', '0.2.0') + '\n' + shaped('Old, with no shape.', '0.1.0')
    checked(text, '0.2.0')
    with pytest.raises(changelog_notes.ShapeError):
        checked(text, '0.1.0')


def test_the_command_fails_on_a_section_without_the_shape_and_prints_one_with_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    file = tmp_path / 'CHANGELOG.md'
    file.write_text(shaped(SUMMARY), encoding='utf-8')
    assert changelog_notes.main(['1.0.0', '--file', str(file)]) == 1
    captured = capsys.readouterr()
    assert captured.out == '' and 'Upgrade impact' in captured.err
    file.write_text(shaped(f'{SUMMARY}\n\n{UPGRADE}\n\n{SECURITY}'), encoding='utf-8')
    assert changelog_notes.main(['1.0.0', '--file', str(file)]) == 0
    assert SECURITY in capsys.readouterr().out
