# Specs: packaging and quality (Q)

Statements for PRD section 4.9. These are checked on the built package and in CI rather than through a fetch; each
names the check that fails when the requirement breaks.

- **SPEC-Q1.1** [package] The built wheel's metadata `Name` is `datatables-backend`, and `import datatables_backend`
  works in an environment holding only that wheel and its core dependencies.
- **SPEC-Q2.1** [package] The wheel's metadata declares the extras `sqlalchemy`, `opensearch` and `fastapi`, and no
  other.
- **SPEC-Q3.1** [CI install-isolation] With only the core installed, importing `datatables_backend` and its core
  modules succeeds and loads no `sqlalchemy`, `opensearchpy`, `fastapi` or `greenlet` module; with one extra
  installed, its backend imports without the others.
- **SPEC-Q3.2** [CI install-isolation] With the `sqlalchemy` extra and no `greenlet`, the SQL backend imports and
  answers `fetch_sync`.
- **SPEC-Q4.1** [package] The wheel requires Python `>=3.12`; CI's test job runs on 3.12, 3.13, 3.14 and 3.15.
- **SPEC-Q5.1** [CI minimum-versions] The suite passes with Pydantic 2.10.0 on 3.12 and 3.13, 2.12.0 on 3.14 and
  2.14.0 on 3.15.
- **SPEC-Q6.1** [CI minimum-versions] The suite passes with SQLAlchemy 2.0.31 on every supported Python.
- **SPEC-Q7.1** [CI minimum-versions] The suite passes with opensearch-py 2.6.0 on every supported Python.
- **SPEC-Q8.1** [CI minimum-versions] The suite passes with FastAPI 0.133.0 and python-multipart 0.0.9 on every
  supported Python.
- **SPEC-Q9.1** [CI] The minimum-versions job has one leg per supported Python, each printing the installed floor
  versions it tested.
- **SPEC-Q10.1** [package, CI] The wheel holds `datatables_backend/py.typed`; CI runs `basedpyright` and `ruff` and
  fails on any finding.
- **SPEC-Q11.1** [CI] The behaviour tests call only the public API (`parse_request`, `fetch`, `fetch_sync`, the
  FastAPI dependency); they run on SQLite sync and aiosqlite by default and on PostgreSQL, SQL Server, MySQL and
  OpenSearch in the container jobs, where none is skipped.
- **SPEC-Q12.1** [CI] The `gate` check depends on lint, types, tests, minimum-versions and install-isolation, and is
  the one required check on `main`.
- **SPEC-Q13.1** [package] The wheel's metadata licence is `MIT` and it ships `LICENSE`.
