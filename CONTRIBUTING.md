# Contributing

Thank you for helping. This page says how to report a problem, how to propose a change and what a pull request needs
to be accepted.

## Bugs and feature requests

Open a [GitHub issue](https://github.com/hniedner/datatables-backend/issues). A suspected vulnerability does not go in
an issue: report it as [SECURITY.md](SECURITY.md) describes.

## Changes

Send a pull request against `main`. Keep it small, with one concern each. Discuss a larger change in an issue first, so
that the work is not wasted.

## What a pull request needs

1. **Tests.** Every change of behaviour comes with tests that fail without the change. Write them through the public
   API: `parse_request`, then `fetch` or `fetch_sync`. Properties use Hypothesis. CrossHair was tried on the number
   parser (2026-11-25, 0.0.114) and left out: on a free text it stopped with an error inside its own regex support, and
   on a bounded alphabet it ran five minutes without a finding, where Hypothesis ran 50,000 examples in nine seconds
   and found nothing either.
2. **Checks.** `uv run ruff format . && uv run ruff check .`, `uv run basedpyright` and `uv run pytest` pass. CI also
   runs the SQL and OpenSearch tests on real servers (PostgreSQL, MySQL, MariaDB, SQL Server and OpenSearch).
3. **Commit headers** are conventional, `type(scope)!: subject` of at most 100 characters (types feat, fix, docs, test,
   ci, build, refactor, chore, style); the commit-msg hook and CI's `commits` job check them. A commit made with an AI
   assistant names it in a `Co-Authored-By` line.
4. **Docs.** [docs/PRD.md](docs/PRD.md) and [spec/](spec/) define the behaviour. A change of behaviour updates them.

## Releases

The maintainer starts a release through a release pull request, which writes the version's section of
[CHANGELOG.md](CHANGELOG.md) under `## [X.Y.Z] - YYYY-MM-DD`. That section is the release's notes, word for word, so it
is written for a reader who has not followed the project. It opens in a fixed shape, before the first `###` group of
Keep a Changelog:

```markdown
## [0.1.3] - 2026-12-01

A short paragraph on what changes for adopters.

**Upgrade impact:** none; no change to your code is needed.

**Security:** none; this release fixes no known vulnerability.

### Added

- ...
```

- The summary comes first. The two lines start at the beginning of a line, exactly as written, with text after them:
  whether the upgrade breaks anything and what an adopter must change (normally nothing in a patch, per the README's
  status line), and the vulnerabilities the release fixes with their advisory or CVE ids, or "none".
- A release is refused without the summary or either line, and the message says which one to add. The rule is
  `check_shape` in `scripts/changelog_notes.py`; `scripts/check_release.py` applies it where a release is pending: on
  the release pull request, in the rehearsal of a pending release and in the tag run, before anything is built.
- The script cannot know the security line is true. The author of the release pull request checks the repository's
  security advisories and its Dependabot and code-scanning alerts before writing it.
- Only the version being released is checked: the sections already published stay as they are.
- Once a release is published, its files and tag are final (see
  [decision 009](docs/decisions/009-release-automation.md), section 10).

## Hooks

`uv run pre-commit install` installs the project's git hooks, and `uv run pre-commit run --all-files` runs them all.
Four of them (`private-words`, `private-words-message`, `private-words-refs`, `private-words-history`) read a word list
that only the maintainer has, and fail without it: the first two on every commit, the third on every checkout and
switch, the fourth on every push. Skip exactly those four, as [CI](.github/workflows/ci.yml) does. Set it once in the
shell you use for the clone, and it covers commit, checkout and push:

```sh
export SKIP=private-words,private-words-message,private-words-refs,private-words-history
```

## Licence

You contribute under the project's [MIT licence](LICENSE): what you send is licensed as the project is. There is no
contributor licence agreement.
