# datatables-backend

A read-only DataTables server-side library for several data sources. `docs/PRD.md` is the source of truth for scope,
requirements and milestones. Notes about the application this was derived from live only in the untracked
`CLAUDE.local.md`; they must never reach a tracked file or a commit message.

## Commands

- `uv sync`
- `uv run pytest`
- `uv run ruff format . && uv run ruff check .`
- `uv run basedpyright`
- `uv run pre-commit install` once per clone (it installs the pre-commit, commit-msg, pre-push and post-checkout
  hooks); `uv run pre-commit run --all-files` runs every hook by hand.
- `uv run pytest --cov=datatables_backend --cov=scripts --cov-branch --cov-report=xml && uv run diff-cover coverage.xml
  --compare-branch=origin/main --fail-under=95` checks the changed lines close to CI (CI's 3.14 leg compares with the
  PR base or the push's previous commit).
- `uv audit --locked` checks every package in `uv.lock` against the OSV advisory database, as the `Audit` workflow
  does daily; its header in `.github/workflows/audit.yml` says how a finding is dealt with.
- Container tests (CI runs them; locally they need the servers): `uv sync --group containers`, then
  `uv run pytest tests/test_sql.py` with `DTB_SQL_URL` and `DTB_SQL_ASYNC_URL` naming a database called `dtb`,
  `dtb_test` or `dtb_test_...` (e.g. `postgresql+psycopg://...` and `postgresql+psycopg_async://...`) and
  `DTB_SQL_DIALECT` its dialect, and on PostgreSQL `PGTZ` and `TZ` naming zones far from UTC (CI uses
  `Pacific/Kiritimati` and `America/Adak`), or the timezone test fails; or `uv run pytest
  tests/test_opensearch_live.py` with `DTB_OPENSEARCH_URL`. SQL Server also needs Microsoft's ODBC Driver 18. The
  SQL tests own the tables `dtb_test_people` and `dtb_test_tags`, and the live OpenSearch test owns the index
  `dtb-test-docs`: each run drops them first.

The private-word hooks read a word list kept outside the repository, from the file `DTB_PRIVATE_WORDS` names. Without
it they fail closed ("could not check"), so set it before committing; never read or print the list. CI skips them, and
nothing scans what is authored on GitHub itself. A commit or checkout fails while a local ref name holds a word, and a
push fails while any object in the repository does, reachable or not: drop a leftover with `git stash drop`, then
`git reflog expire --expire-unreachable=now --all` and `git gc --prune=now`. `scripts/private_word_scan.py`'s
docstring lists what the scans read and what they do not cover.

The supported range is Python 3.12+ (tested on 3.12–3.15), Pydantic 2.10+ (2.12+ on 3.14 and 2.14+ on 3.15, the first
with a wheel for each), SQLAlchemy 2.0.31+ (2.0.30 fails to import on 3.13), opensearch-py 2.6+, FastAPI 0.133+ and
python-multipart 0.0.9+. FastAPI 0.133 is the first release without a `starlette<1.0` cap, so it is the oldest that runs
with starlette 1.2+ (whose TestClient uses `httpx2`, which the dev group asks for). It still allows starlette 0.40.0,
whose TestClient uses `httpx`. To check the minimum versions, build a resolver-consistent environment and run pytest
with its own interpreter, as CI's `minimum-versions` job does. Its 3.12 leg, as below, also forces the lowest starlette
through an override and adds `httpx`, as the 3.15 leg does. For another Python, change `-p 3.12` to it; on 3.13 and 3.14
drop the override and `httpx`; on 3.14 pin `pydantic==2.12.0`, on 3.15 `pydantic==2.14.0`:

```sh
uv venv -p 3.12 <scratch>/venv-min
printf 'starlette==0.40.0\n' > <scratch>/overrides.txt
uv pip install -p <scratch>/venv-min --override <scratch>/overrides.txt -e . --group dev sqlalchemy==2.0.31 \
  pydantic==2.10.0 fastapi==0.133.0 python-multipart==0.0.9 opensearch-py==2.6.0 httpx
uv pip check -p <scratch>/venv-min
<scratch>/venv-min/bin/python -c 'from importlib.metadata import version as v
for p in ("sqlalchemy", "pydantic", "fastapi", "python-multipart", "opensearch-py", "starlette"): print(p, v(p))'
<scratch>/venv-min/bin/python -m pytest
```

Don't use `uv run --with ... pytest`: the project's `pytest` script runs the project interpreter, which does not see the
`--with` overlay (seen with uv 0.13.7 in a fresh checkout). The explicit environment above is checked with
`uv pip check` and prints the versions that run the tests.

## Rules

- The core (`protocol`, `columns`, `_plan`, `rows`, `cache`) imports only Pydantic and the standard library. Each
  backend imports only its own extra. `sqlalchemy.ext.asyncio` is imported only under `TYPE_CHECKING`, because it
  needs `greenlet`.
- Every user mistake is found while parsing, planning (`_plan.plan()`) or building the query, before the page is
  queried; only the counts of PRD-C11 may run first. It is answered with one of the stable codes of PRD-E2
  (`invalid_filter`, `invalid_date`, `invalid_number`, `page_out_of_range`), as HTTP 200 in the FastAPI adapter
  (PRD-A11). A structural error raises `MalformedRequest`, answered 422. Any other failure raises.
- A message or log line never contains a search value or a name the client sent. A name is logged only when the
  server declared it.
- Tests go through the public API: `parse_request`, then `fetch` or `fetch_sync`. They run on real SQLite (sync and
  aiosqlite) and against a recording OpenSearch client; CI also runs the SQL tests on PostgreSQL, MySQL and SQL
  Server (`DTB_SQL_URL`, `DTB_SQL_ASYNC_URL`, `DTB_SQL_DIALECT`) and `tests/test_opensearch_live.py` on OpenSearch
  (`DTB_OPENSEARCH_URL`). A backend gap the PRD has yet to close is pinned as today's result, or as a `strict=True`
  xfail naming the expected exception, citing the PRD requirement; nothing is skipped silently. Before relying on a
  new test, check that it fails when the behaviour is broken.
- No application specifics: no field lists, no domain names, no app logging or config.
- Design test: maximum functionality with maximum portability and no surprise for the adopter. An exception is
  stated in the PRD and the README with its reason. Nothing asks the consumer to change a schema or a mapping.
- Optimise for the current release of OpenSearch and SQLAlchemy, with a bias to the most used one. An older
  supported release may need configuration or pay a stated performance or feature penalty.
- A decision that is not a requirement, and the measurements behind a requirement, go to
  `docs/decisions/NNN-title.md`; the PRD's rationale stays short and points there.

## Status (2026-11-21)

- **Milestone 1 (v0.1) is code-complete.** The GitHub repo `hniedner/datatables-backend` is public. Publishing still
  needs the user's explicit OK.
- **PRD draft 2 is approved and merged** (`docs/PRD.md`). The roadmap lives in the GitHub milestones v0.1, v0.2, v0.3
  and v1.0 and their issues.
- **Done:** CI (lint, types, tests on 3.12–3.15, minimum versions, install isolation) and container tests on
  PostgreSQL 18, MySQL 8.4, SQL Server 2022 and OpenSearch 3.10 and 2.19.
- **Next (v0.2):** the PRD's layout (#39), then the PRD v2 changes test-first (#12, #14, #26–#29) and the
  acceptance suite (#15, sources in `CLAUDE.local.md`).
- **Retired predecessor:** `sqlalchemy2-datatables` 0.7.0 is on PyPI. Its GitHub repo is archived, and its README
  and its `DeprecationWarning` link to this repo.
