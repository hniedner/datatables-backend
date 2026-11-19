# datatables-backend

A read-only DataTables server-side library for several data sources. `docs/PRD.md` is the source of truth for scope,
requirements and milestones. Notes about the application this was derived from live only in the untracked
`CLAUDE.local.md`; they must never reach a tracked file or a commit message.

## Commands

- `uv sync`
- `uv run pytest`
- `uv run ruff format . && uv run ruff check .`
- `uv run basedpyright`

The supported range is Python 3.12+, SQLAlchemy 2.0.30+ and Pydantic 2.7+. To check the minimum versions, run
`UV_PROJECT_ENVIRONMENT=<scratch>/venv312 uv run -p 3.12 --with 'sqlalchemy==2.0.30' --with 'pydantic==2.7.0' pytest`.

## Rules

- The core (`protocol`, `columns`, `_plan`, `rows`, `cache`) imports only Pydantic and the standard library. Each
  backend imports only its own extra. `sqlalchemy.ext.asyncio` is imported only under `TYPE_CHECKING`, because it
  needs `greenlet`.
- Every user mistake is found in `_plan.plan()` or while a backend builds its query, before anything runs. It is
  raised as `InvalidFilter` and answered as `invalid_filter`. Any other failure raises.
- A message or log line never contains a search value or a name the client sent. A name is logged only when the
  server declared it.
- Tests go through the public API: `parse_request`, then `fetch` or `fetch_sync`. They run on real SQLite (sync and
  aiosqlite) and against a recording OpenSearch client. Before relying on a new test, check that it fails when the
  behaviour is broken.
- No application specifics: no field lists, no domain names, no app logging or config.

## Status (2026-11-19)

- **Milestone 1 (v0.1) is code-complete.** The GitHub repo `hniedner/datatables-backend` is public. Pushing and
  publishing still need the user's explicit OK.
- **Next: milestone 2.**
  - Set up GitHub Actions CI covering lint, types, and tests on 3.12–3.14, plus the minimum versions.
  - Add container tests against PostgreSQL, SQL Server and OpenSearch.
  - Port the original application's acceptance cases (paths in `CLAUDE.local.md`) as behaviour tests.
- **Retired predecessor:** `sqlalchemy2-datatables` 0.7.0 is on PyPI. Its GitHub repo is archived, and its README
  and its `DeprecationWarning` link to this repo.
