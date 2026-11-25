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
   API: `parse_request`, then `fetch` or `fetch_sync`.
2. **Checks.** `uv run ruff format . && uv run ruff check .`, `uv run basedpyright` and `uv run pytest` pass. CI also
   runs the SQL and OpenSearch tests on real servers (PostgreSQL, MySQL, MariaDB, SQL Server and OpenSearch).
3. **Commit headers** are conventional, `type(scope)!: subject` of at most 100 characters (types feat, fix, docs, test,
   ci, build, refactor, chore, style); the commit-msg hook and CI's `commits` job check them.
4. **Docs.** [docs/PRD.md](docs/PRD.md) and [spec/](spec/) define the behaviour. A change of behaviour updates them.

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
