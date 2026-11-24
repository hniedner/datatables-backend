"""Print one version's release notes from CHANGELOG.md: standard library only.

``changelog_notes.py VERSION`` prints the section ``## [VERSION]`` of ``CHANGELOG.md`` without its heading, up to the
next ``## [`` heading. A GitHub release shows the notes on their own, so the link reference definitions that sit at the
end of the file (``[#71]: https://...``) would not resolve ``[#71]``: the ones the section uses are appended, in the
order the file defines them, and the file's own definitions are never part of the section.

It exits with status 1 and a message on stderr when the section is missing or holds nothing. ``--file`` names another
changelog, for the tests. It writes nothing.
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
        sys.stdout.write(notes(args.file.read_text(encoding='utf-8'), args.version))
    except NotesError as error:
        sys.stderr.write(f'{error}\n')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
