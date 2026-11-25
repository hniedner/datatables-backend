"""Print one version's release notes from CHANGELOG.md: standard library only.

``changelog_notes.py VERSION`` prints the section ``## [VERSION]`` of ``CHANGELOG.md`` without its heading, up to the
next ``## [`` heading. A GitHub release shows the notes on their own, so the link reference definitions that sit at the
end of the file (``[#71]: https://...``) would not resolve ``[#71]``: the ones the section uses are appended, in the
order the file defines them, and the file's own definitions are never part of the section.

It exits with status 1 and a message on stderr when the section is missing, holds nothing, or does not open in the
fixed shape of CONTRIBUTING.md, Releases: a summary, then a ``**Upgrade impact:**`` and a ``**Security:**`` line, before
the first ``###`` group. Only the version asked for is read, so the sections already published stay as they are.
``--file`` names another changelog, for the tests. It writes nothing.
"""

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
DEFINITION = re.compile(r'^\[([^\]]+)\]:[ \t]+\S+.*$')
LABEL = re.compile(r'\[([^\[\]]+)\]')


class NotesError(Exception):
    """The changelog has no usable entry for the version."""


class ShapeError(NotesError):
    """The section does not open in the fixed shape of CONTRIBUTING.md, Releases."""


#: What a section's opening must hold besides its summary, with an example for the message
LINES = {
    '**Upgrade impact:**': '**Upgrade impact:** none; no change to your code is needed.',
    '**Security:**': '**Security:** none; this release fixes no known vulnerability.',
}


def check_shape(text: str, version: str) -> None:
    """Check that ``text``, the notes of ``version``, opens with a summary, a ``**Upgrade impact:**`` line and a
    ``**Security:**`` line, each at the start of a line with text after it, before the first ``### `` group.

    Raises:
        ShapeError: the first of the three that is missing, with the line to add.
    """
    opening = re.split(r'^### ', text, maxsplit=1, flags=re.MULTILINE)[0]
    first = next((line for line in opening.splitlines() if line.strip()), '')
    section = f'CHANGELOG.md: the section "## [{version}]"'
    if not first or first.startswith(tuple(LINES)):
        raise ShapeError(
            f'{section} does not open with a summary: start it with a short paragraph on what changes for adopters, '
            'before the "**Upgrade impact:**" and "**Security:**" lines. See CONTRIBUTING.md, Releases.'
        )
    for marker, example in LINES.items():
        if not re.search(rf'^{re.escape(marker)}[ \t]+\S', opening, re.MULTILINE):
            raise ShapeError(
                f'{section} has no "{marker}" line in its opening (before the first "###"): add one after the '
                f'summary, for example "{example}" See CONTRIBUTING.md, Releases.'
            )


def notes(text: str, version: str) -> str:
    lines = text.splitlines()
    heading = re.compile(rf'^## \[{re.escape(version)}\](?: - .*)?$')
    starts = [i for i, line in enumerate(lines) if heading.match(line)]
    if not starts:
        raise NotesError(f'CHANGELOG.md has no section "## [{version}]"')
    if len(starts) > 1:
        raise NotesError(f'CHANGELOG.md has more than one section "## [{version}]"')
    end = next((i for i in range(starts[0] + 1, len(lines)) if lines[i].startswith('## [')), len(lines))
    definitions = {m.group(1).lower(): line for line in lines if (m := DEFINITION.match(line))}
    body = '\n'.join(line for line in lines[starts[0] + 1 : end] if not DEFINITION.match(line)).strip()
    if not body:
        raise NotesError(f'CHANGELOG.md: the section "## [{version}]" is empty')
    used = [definitions[label.lower()] for label in dict.fromkeys(LABEL.findall(body)) if label.lower() in definitions]
    order = list(definitions.values())
    used.sort(key=order.index)
    return body + ('\n\n' + '\n'.join(used) if used else '') + '\n'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print a version's section of CHANGELOG.md.")
    parser.add_argument('version', help='for example 0.1.0rc3, without the v')
    parser.add_argument('--file', type=Path, default=ROOT / 'CHANGELOG.md')
    args = parser.parse_args(argv)
    try:
        text = notes(args.file.read_text(encoding='utf-8'), args.version)
        check_shape(text, args.version)
        sys.stdout.write(text)
    except NotesError as error:
        sys.stderr.write(f'{error}\n')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
