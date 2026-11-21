# Specs: packaging and quality (Q)

Statements for PRD section 4.9. These are checked on the built package, in CI or in review rather than through a
fetch; the tag names where each is checked (see `README.md`).

- **SPEC-Q1.1** [CI install-isolation] The built wheel's normalized distribution name is `datatables-backend`, and
  `import datatables_backend` works in an environment holding only that wheel and its core dependencies.
- **SPEC-Q2.1** [CI install-isolation] The wheel's metadata declares the extras `sqlalchemy`, `opensearch` and
  `fastapi`, and no other (the job asserts its matrix equals core plus the declared extras).
- **SPEC-Q3.1** [CI install-isolation] With only the core installed, importing `datatables_backend` and its core
  modules (`protocol`, `columns`, `_plan`, `rows`, `cache`) succeeds; with one extra installed, its backend imports
  without the others.
- **SPEC-Q3.2** [CI install-isolation] With every extra and `greenlet` installed, importing each backend loads no
  top-level package that another extra requires directly, and importing the SQL backend loads no
  `sqlalchemy.ext.asyncio`.
- **SPEC-Q4.1** [CI tests] The suite passes on each Python of the job's matrix. [review] The wheel requires Python
  `>=3.12`, and the matrix is 3.12, 3.13, 3.14 and 3.15.
- **SPEC-Q5.1** [CI minimum-versions] Each leg installs its pinned Pydantic, checks the environment with
  `uv pip check`, and passes the suite. [review] The pins are 2.10.0 on 3.12 and 3.13, 2.12.0 on 3.14 and 2.14.0 on
  3.15.
- **SPEC-Q6.1** [CI minimum-versions] Each leg passes the suite with its pinned SQLAlchemy. [review] Every leg pins
  2.0.31.
- **SPEC-Q7.1** [CI minimum-versions] Each leg passes the suite with its pinned opensearch-py. [review] Every leg
  pins 2.6.0.
- **SPEC-Q8.1** [CI minimum-versions] Each leg passes the suite with its pinned FastAPI and python-multipart.
  [review] Every leg pins 0.133.0 and 0.0.9.
- **SPEC-Q9.1** [CI minimum-versions] Each leg asserts that its pins are the floors for its Python and that the
  installed versions are the pins, and prints them. [review] The job has one leg per supported Python.
- **SPEC-Q10.1** [review] The wheel holds `datatables_backend/py.typed`; CI's types and lint jobs run `basedpyright`
  and `ruff` and fail on any error or warning.
- **SPEC-Q11.1** [review] The behaviour tests call only the public API (`parse_request`, `fetch`, `fetch_sync`, the
  FastAPI dependency); by default they run on SQLite sync and aiosqlite and on OpenSearch through the recording
  client, and on PostgreSQL, SQL Server, MySQL and OpenSearch in the container jobs, where none is skipped.
- **SPEC-Q12.1** [CI gate] The `gate` check fails unless every job it needs succeeded. [review] It needs lint,
  types, tests, install-isolation, minimum-versions, postgresql, mysql, sqlserver and opensearch, and it is the only
  required status check on `main`.
- **SPEC-Q13.1** [review] The wheel's metadata `License-Expression` is `MIT` and the wheel ships `LICENSE`.
