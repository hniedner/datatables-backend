# 009: Release automation (draft for the owner)

This is the evaluation for issue #45 (part 45a): how to meet the owner's release model of 2026-11-22. The build is #76.
SvelteDT (its PRD-S7) needs the same answers, so this note is shared with it. **Nothing here is decided.** Once the
owner has chosen, his choices are recorded in this file and the options he rejected are cut to one line each.

Labels: READ (a repository file or an issue, with its place), RUN (a command run on 2026-11-24, named), FETCHED (a
primary source, with its URL, fetched 2026-11-24), INFERRED (reasoning from the facts given), NOT ESTABLISHED (to
verify before #76 relies on it; all are listed at the end).

## The model, and where today's repository stands

The model (READ, #45 body, not reopened here): after CI has passed on a push to `main`, a workflow computes the next
version from the conventional commit messages since the last release, creates the tag `v<version>` and a GitHub
release with a changelog generated from those commits, and pushes no commit to `main`. The publish runs in a run on
that tag, publishes the build of that tag, stays in the `pypi` environment and waits for the owner. The job that tags
cannot publish; the job that publishes cannot tag. An existing tool, no release script of our own. A rehearsal that
publishes nothing stays possible (READ, #76 body). Owner rule of 2026-11-23 (READ, #76 comment): the workflow creates
the GitHub release as a draft before the publish job and makes it public after PyPI accepted the upload; its notes are
the version's section of CHANGELOG.md.

| Fact | Source |
|---|---|
| `release.yml` publishes on a hand-pushed `v*` tag, and a manual run on `main` is the rehearsal; its jobs are `check-tag`, `ci` (all of ci.yml), `draft-release` (`contents: write`), `publish` (`pypi` environment, `id-token: write` only), `publish-release` | READ, `.github/workflows/release.yml:1-19, 30-149` |
| That path published 0.1.0rc2 to 0.1.0; #45's "has never run" is outdated | READ, #76 comment of 2026-11-24; RUN, `git for-each-ref refs/tags`: `v0.1.0rc1` to `v0.1.0`, all annotated tags |
| PyPI's trusted publisher is `release.yml` (top level only) | READ, `release.yml:108-109` |
| The version is written by hand: `__version__ = '0.1.0'`; hatchling reads it | READ, `src/datatables_backend/__init__.py:8`, `pyproject.toml:1-3, 13, 86-87` |
| `check_dist.py tag` compares the tag with that source version; `check_dist.py dist` checks the files against it | READ, `scripts/check_dist.py:3-7, 50-53, 85-86, 151-169` |
| Release notes come from `CHANGELOG.md`, hand-written Keep a Changelog prose with an `[Unreleased]` section | READ, `scripts/changelog_notes.py:3-6`, `CHANGELOG.md:1-7` |
| README promises every breaking change "is listed in CHANGELOG.md" and names the version ("Status: 0.1.0") | READ, `README.md:18-19` |
| The PRD's packaging requirements (PRD-Q1 to Q14) say nothing about tags or the release path; only criterion 6 of section 6 names "the release workflow". #45's "the PRD's packaging requirements describe the hand-pushed tag" is not so; release.yml's comments do | READ, `docs/PRD.md:1531-1638, 1858` |
| `pypi` environment: deployments from the tag pattern `v*` only, the owner as required reviewer, `can_admins_bypass: false`, `prevent_self_review: false` | RUN, `gh api .../environments/pypi` and `.../environments/pypi/deployment-branch-policies` |
| Tag ruleset "release tags" on `refs/tags/v*`: deletion, non-fast-forward and update blocked, no bypass actor; creation is not restricted | RUN, `gh api .../rulesets/27804950` |
| `main` ruleset: pull request required, merge method **merge only** (`allowed_merge_methods: ["merge"]`), required checks `gate` and `dependency-review`, strict (branch up to date) | RUN, `gh api .../rulesets/27673398` |
| Repository: merge commits on, squash and rebase off; merge commit title `MERGE_MESSAGE` ("Merge pull request #N from ..."), body `PR_TITLE` | RUN, `gh api repos/hniedner/datatables-backend` |
| Commits are free prose, headers often over 100 characters (e.g. `f314d7c`); `main` also holds "Merge branch 'main' into ..." commits from updating a branch | RUN, `git log --oneline --no-merges -12 main`, `git log --oneline --merges -10 main` |
| 25 commits since `v0.1.0`, none conventional | RUN, `git rev-list --count v0.1.0..main` |
| Dependabot already writes conventional headers (`ci(deps): ...`, `build(deps): ...`) | READ, `.github/dependabot.yml:7-10, 25-27` |
| Actions are pinned by commit SHA with a version comment; zizmor runs in pre-commit | READ, every `uses:` in `.github/workflows/`, `.pre-commit-config.yaml:118-123` |

## 1. The tool

"Pushes no commit to `main`" rules out every mode that commits a version or opens a release pull request.

| Tool | Can it tag and create a release from a runner without a commit? | 0.x bump rule | How it is pinned |
|---|---|---|---|
| release-please | **No.** It "maintains Release PRs"; a release is made "when you're ready to tag a release, simply merge the release PR". No documented mode tags without a merged release PR (FETCHED, github.com/googleapis/release-please and github.com/googleapis/release-please-action). **Ruled out.** | not needed | JS action by SHA |
| python-semantic-release (PSR) 10.7.0 | **Yes.** With `--no-commit --tag --push` it tags the existing HEAD, pushes only the tag and creates the VCS release; it still edits and stages the version files and changelog locally, uncommitted (FETCHED, `src/semantic_release/cli/commands/version.py` on the `master` branch of python-semantic-release/python-semantic-release). Its release is created public, so the owner's draft-first rule needs `--no-vcs-release` and today's `gh release create --draft`. | `minor_tags = feat`, `patch_tags = fix, perf`, other types no release; merge commits ignored by default (`ignore_merge_commits = True`) (FETCHED, `.../commit_parser/conventional/options.py`). Breaking changes: a `BREAKING CHANGE:` paragraph (FETCHED, docs `concepts/commit_parsing`). `allow_zero_version` defaults to false (the first release would be 1.0.0); `major_on_zero = false` keeps a breaking change at 0.x a minor bump (FETCHED, docs `configuration/configuration`). | The action is a Docker action built at run time from `src/gh_action/Dockerfile` (FETCHED, `action.yml`): a `python:3.14-slim-trixie` base by tag, apt packages and `pip install -r requirements.txt --pre` unpinned (FETCHED, the Dockerfile). A SHA pin of the action therefore does not pin what runs. As a CLI from `uv.lock` it is hash-pinned, but it brings ~12 runtime dependencies (gitpython, requests, python-gitlab, jinja2, click, rich, `pydantic~=2.0`, ...) (FETCHED, pypi.org/pypi/python-semantic-release/json). Python only. |
| git-cliff 2.14.2 | It computes and prints; it does not create tags or releases (`--bumped-version` "Prints bumped version for unreleased changes") (FETCHED, git-cliff.org/docs/usage/args). The tag and the draft release are two commands the workflow already runs today (`git push` of a tag, `gh release create --draft`). | `fix` patch, `feat` minor, breaking (`!`) major (FETCHED, git-cliff.org/docs/usage/bump-version). For 0.x: `features_always_bump_minor = true` and `breaking_always_bump_major = false` give feat minor and breaking minor (FETCHED, git-cliff.org/docs/configuration/bump). | On PyPI as `git-cliff`: binary wheels for Linux, macOS and Windows, **no dependencies** (FETCHED, pypi.org/pypi/git-cliff/2.14.2/json). So it goes in a uv dependency group, hash-pinned by `uv.lock` and moved by Dependabot's `uv` updates with their 7-day cooldown (READ, `dependabot.yml:21-31`). On npm as `git-cliff` for SvelteDT (FETCHED, git-cliff.org/docs/installation/npm). |
| None (a regex of our own) | Ruled out by "no release script of our own" (READ, #45). | | |

**Recommendation: git-cliff, from a uv dependency group of its own (`release`).** Reasons: one tool and one
configuration (`cliff.toml`) for both projects, since SvelteDT can run the same tool from npm; no dependencies, so
nothing joins the project's own resolution (PSR would co-resolve `pydantic~=2.0` with the library's floors); it does
the one thing the model needs from a tool (version and notes), while the tagging and the draft release stay in steps
the repository already has. PSR's Docker action is out under the repository's pinning rule, because its pin does not
pin what runs.

**Costs:** one new dev-side dependency (`git-cliff`, in a `release` group, not in `dev`); one new config file
(`cliff.toml`); about five lines of workflow shell that tag and dispatch (section 2). `perf` releases nothing in
git-cliff's default rules (only `fix`, `feat`, breaking are named, FETCHED, bump-version page; a custom regex exists
for minor and major only, FETCHED, configuration/bump): either `perf` is written as `fix(perf)`, or `perf` is dropped
from the allowed types. Recommendation: keep `perf` out of the list here, since this library has no performance
promise of its own.

**The bump table** (recommended, the same under both tools):

| Commit | Before 1.0 | From 1.0 |
|---|---|---|
| `feat:` | minor (0.2.0 to 0.3.0) | minor |
| `fix:` | patch | patch |
| `feat!:`, `fix!:`, any `!` or `BREAKING CHANGE:` footer | **minor** (the README's policy: a 0.x minor may break the Python API, READ `README.md:18-19`) | major |
| `docs`, `test`, `ci`, `build`, `refactor`, `chore`, `style`, `revert` | no release | no release |

A runtime floor raised in `pyproject.toml` changes what adopters install, so it is a `fix:` or `feat:` (or `!`), not
`build:`; Dependabot's `build(deps)` touches only `uv.lock` and correctly releases nothing.

## 2. How the tag starts the publish run

FETCHED, docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow ("Triggering
a workflow from a workflow"): "events triggered by the `GITHUB_TOKEN` will not create a new workflow run", and
"`workflow_dispatch` and `repository_dispatch` events always create workflow runs"; to trigger a workflow otherwise
"you can use a GitHub App installation access token or a personal access token instead of `GITHUB_TOKEN`". The same
rule and exceptions: FETCHED, docs.github.com/en/actions/concepts/security/github_token, which also states that
"The `GITHUB_TOKEN` secret is a GitHub App installation access token."

So a tag pushed with `GITHUB_TOKEN` does **not** start `release.yml`'s `push: tags` trigger.

The environment's rule: "The deployment branch or tag rule is matched against the `GITHUB_REF` of the workflow run"
(FETCHED, docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments). Here the only rule
is the tag pattern `v*` (RUN, above).

| Option | Ref of the publish job | Accepted by `pypi` (`v*` tags)? | Cost |
|---|---|---|---|
| A. The tagging job pushes the tag, then runs `gh workflow run release.yml --ref v<version>` with `GITHUB_TOKEN` | `workflow_dispatch`: `GITHUB_REF` is the "Branch or tag that received dispatch", `GITHUB_SHA` its last commit (FETCHED, docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), so `refs/tags/v<version>` | **Yes** | The tagging job needs `actions: write` (the dispatch endpoint needs "Actions" write, FETCHED, docs.github.com/en/rest/authentication/permissions-required-for-github-apps) besides `contents: write`. No new identity, no secret. PyPI's publisher stays `release.yml`. |
| B. A GitHub App token pushes the tag; `release.yml` starts on `push: tags` as today | `refs/tags/v<version>` | **Yes** | A new identity: register an App, store its client ID as a variable and its private key as a secret, install it (FETCHED, docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/making-authenticated-api-requests-with-a-github-app-in-a-github-actions-workflow); plus the `actions/create-github-app-token` action. The owner owns the App and its key, rotates it, and a stored key is what trusted publishing was chosen to avoid. A personal token is the same with the owner's own account behind it: worse. |
| C. One workflow: the publish job in the tagging run | The tagging run is on `main` (a `workflow_run` run has `GITHUB_REF` = default branch, FETCHED, events page) | **No**, and it breaks "the publish runs in a run on that tag" | none, ruled out |
| D. `repository_dispatch` | NOT ESTABLISHED (not fetched); it carries no ref of the caller's choosing | treated as C | ruled out |

**Recommendation: A.** It meets the environment's rule with the token the run already has. The dispatched run is a
`workflow_dispatch` on the tag, so `release.yml` tells a release from a rehearsal by `github.ref_type == 'tag'`
instead of `github.event_name == 'push'` (READ, `release.yml:76-77, 114-115`). INFERRED: the run executes
`release.yml` as the tag holds it, since its `GITHUB_REF` and `GITHUB_SHA` are the tag's; the fetched pages do not
say which file version a dispatch runs (NOT ESTABLISHED, verify in the first dispatch). The job that dispatches has no
`id-token`, and the publish job has no `contents` or `actions` permission: the separation holds.

**Cost:** `actions: write` on one job (it can also cancel and re-run workflows). Keeping `push: tags` in `release.yml`
as well is the owner's choice in section 7.

## 3. The version from the tag at build time

The backend is hatchling, reading `__version__` from the source (READ, `pyproject.toml:1-3, 86-87`). With no commit to
`main`, the version has to come from the tag.

| Option | Facts | Verdict |
|---|---|---|
| hatch-vcs 0.5.0 (`[tool.hatch.version] source = "vcs"`) | "uses your preferred version control system (like Git)", options `tag-pattern`, `fallback-version`, `raw-options` (FETCHED, github.com/ofek/hatch-vcs). Requires `hatchling>=1.1.0` and `setuptools-scm>=8.2.0` (FETCHED, pypi.org/pypi/hatch-vcs/json); setuptools-scm 10.3.4 requires `vcs-versioning`, `packaging`, `setuptools` (FETCHED, pypi.org/pypi/setuptools-scm/json). setuptools-scm: on a clean tag the version is `{tag}`; N commits later `{next_version}.dev{distance}+g{hash}`; dirty adds `+dYYYYMMDD`; PKG-INFO is a version source, which is what a wheel built from the sdist reads; a shallow repository warns by default (FETCHED, setuptools-scm.readthedocs.io/en/latest/usage/ and /config/). | **Recommended** |
| hatchling's built-in `env` source | No new dependency, but an unset variable raises `RuntimeError("environment variable ... is not set")` (FETCHED, `backend/src/hatchling/version/source/env.py` in pypa/hatch). `uv sync` builds the project, so every checkout and every CI job would need the variable. | Rejected: a surprise for every contributor |
| Stamp the version into the file at build time | The sdist would then differ from the commit, which `check_dist.py` refuses (READ, `check_dist.py:18`) | Rejected |

What changes with hatch-vcs:

- **A build from the tag** (the publish run): version = the tag without `v` (FETCHED: `{tag}` on a clean tag; INFERRED
  that the default `tag-pattern` drops the `v`, verify). The `dist` job must see the tag: today it checks out with the
  default depth (READ, `ci.yml:242-248`); give `dist` `fetch-depth: 0`. Whether a depth-1 checkout of a tag ref holds
  the tag is NOT ESTABLISHED.
- **A build from a commit that is not a tag** (the rehearsal, every pull request): a `.devN+g<hash>` version. PyPI
  refuses a local version, so a rehearsal can never produce an uploadable name (INFERRED).
- **From the sdist** (uv builds the sdist, then the wheel from it, FETCHED, docs.astral.sh/uv/concepts/projects/build/):
  setuptools-scm reads PKG-INFO, so both files carry the same version without git.
- **`__version__`** becomes `importlib.metadata.version('datatables-backend')` (standard library, so PRD-Q3 holds).
  `dist-check` already asserts that `__version__` equals the installed metadata (READ, `ci.yml:294-295`).
- **uv's cache**: by default uv rebuilds a local project only when `pyproject.toml` (or `setup.py`/`setup.cfg`)
  changes; for a version from tags it documents `cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true,
  tags = true } }]` (FETCHED, docs.astral.sh/uv/concepts/cache/). Without it a local checkout reports a stale version.
- **`scripts/check_dist.py`**: `tag TAG` goes (the version can no longer differ from the tag). `dist DIR` takes the
  expected version as an argument (the tag without `v` in a tag run); without a tag it requires the sdist and wheel to
  state the same version and that version to be a `.dev` version. The members-against-the-commit rules stay as they
  are; hatch-vcs writes no version file unless asked (INFERRED: its README shows the `version-file` build hook as a setting
  of its own), so the sdist
  still holds only tracked files plus PKG-INFO. `release.yml`'s `sed` of `__version__` (READ, `release.yml:54`) goes.

**Costs:** four packages join the build environment (hatch-vcs, setuptools-scm, vcs-versioning, setuptools), unpinned
as hatchling is today (READ, `ci.yml:235-238`: "the build backend and its dependencies are installed unpinned"). They
can be hash-pinned with `[tool.uv] build-constraint-dependencies` and `require-build-hashes` (FETCHED, uv build page);
whether Dependabot moves such pins is NOT ESTABLISHED, so the recommendation keeps today's posture: unpinned, judged by
`dist-check` from a job they never ran in. The build stays as offline-safe as today, which is not offline: the build
environment is fetched from PyPI now and will be then. Whether `uv lock --check` (the `uv-lock` hook, READ,
`.pre-commit-config.yaml:107-112`) stays stable when tags move is NOT ESTABLISHED.

## 4. Commit convention and its check

This is the largest consequence for daily work.

**What the tool reads.** `main` is merge-only by ruleset (RUN, above), so it holds three kinds of commits: the PR's
own commits, GitHub's "Merge pull request #N from ..." commits (header from `MERGE_MESSAGE`, body the PR title), and
"Merge branch 'main' into ..." commits from updating a branch. PSR ignores merge commits by default (FETCHED, options
above); git-cliff skips them with a `commit_parsers` entry `{ message = "^Merge", skip = true }` (FETCHED,
git-cliff.org/docs/configuration/git, the `require_conventional` example uses that form) and offers `merge_commit` in
the commit context (FETCHED, git-cliff.org/docs/templating/context).

| Option | What changes for every commit and PR | Repository setting | Verdict |
|---|---|---|---|
| a. **Every non-merge commit** is conventional, header at most 100 characters (SvelteDT's rule) | Every commit header becomes `type(scope)!: subject`, at most 100 characters; today's long descriptive headers move into the body. Review-round commits are typed by what they change (`test:`, `docs:`, `fix:`). A commit made in GitHub's web editor ("Update x.py") fails the check and is reworded. | none | **Recommended** |
| b. **The PR title** is conventional and becomes the merge commit's header; the tool reads merge commits only | Branch commits stay free prose; one changelog entry per PR | merge commit title `PR_TITLE`, body `PR_BODY`; the check must rerun on a title edit (`pull_request` type `edited`) | Possible; reading only merge commits in git-cliff (skip on `merge_commit = false`) and whether a skip also removes a commit from the bump are NOT ESTABLISHED |
| c. **Squash merges**, PR title as the commit | One commit per PR on `main` | squash on, merge off, in the repository **and** the `main` ruleset | Not recommended: the separate commits the project relies on (a red test before its fix, a removal "as its own commit", READ `docs/PRD.md:1931`, e.g. `f56b814`) vanish from `main`. No tracked file records why squash is off (RUN, grep of CLAUDE.md, PRD, spec); that reason is the owner's. |

**Recommendation: (a)**, for parity with SvelteDT and no change to the repository's settings. In the generated
list only `feat`, `fix` and breaking commits appear, so a `fix:` from a review round of an unreleased feature shows as a
fix line; the hand-written prose (section 8, the changelog) stays the primary text.

**The check**, both places, one rule:

- Locally: a commit-msg hook in `.pre-commit-config.yaml` of pre-commit's built-in `pygrep` language, `args:
  [--negate, --multiline]` ("To require all files to match, use `args: [--negate]`"; commit-msg hooks get the message
  file, FETCHED, pre-commit.com). The regex admits the types, an optional `(scope)`, `!`, a header of at most 100
  characters, and git's own `Merge `, `Revert "` headers. No new dependency and no script. It runs **after**
  `private-words-message`, which keeps `fail_fast` and runs first (READ, `.pre-commit-config.yaml:7-25`), so a
  message holding a private word stops before any other hook sees it. `default_install_hook_types` already installs
  `commit-msg` (READ, `.pre-commit-config.yaml:3`).
- In CI: a `commits` job in `ci.yml`, needed by `gate`, that runs the same hook on each non-merge commit of the
  pull request (`base..head`) or of the push (`before..after`); a manual or `workflow_call` run has no range and the
  job says so, as the coverage step does (READ, `ci.yml:84-90`). Whether `pre-commit run --hook-stage commit-msg
  --commit-msg-filename F` is the right call is NOT ESTABLISHED (not fetched). SvelteDT's `commits` step runs in its
  gate only; its pre-push hook runs the secret scans and not `commits` (READ,
  sveltedt `scripts/check-repo.ts:2456-2493, 3038-3115`, `.githooks/pre-push`). The same holds here: CI is the check
  that binds.
- The private-word hooks stay unchanged and keep working: the new hook neither reads nor prints the word list. What
  GitHub authors (merge commits, PR titles) stays unscanned, as today (READ, `CLAUDE.md:31-33`); the generated notes
  quote only commit headers already public on GitHub (INFERRED).

**Costs:** every contributor writes shorter, typed headers; one hook and one CI job; the types are listed in two
places (the hook's regex and `cliff.toml`), so a test that the two lists agree belongs in #76 (SvelteDT's decision
001 asks for the same, READ, sveltedt `docs/decisions/001-quality-tooling.md:273-283`). The 25 free-prose commits
since `v0.1.0` give no bump: see section 8, step 0.

## 5. `workflow_run` or a job after `gate` in ci.yml

FETCHED, events page: `workflow_run` "will only trigger a workflow run if the workflow file exists on the default
branch"; its `GITHUB_SHA` is the "Last commit on default branch" and `GITHUB_REF` the default branch; "A workflow run
is triggered regardless of the conclusion of the previous workflow" (so `github.event.workflow_run.conclusion` must be
checked); it "is able to access secrets and write tokens"; at most three levels of chaining. The run object carries
`head_branch`, `head_sha`, `event`, `conclusion` (FETCHED, docs.github.com/en/rest/actions/workflow-runs, "Get a
workflow run"). zizmor's `dangerous-triggers` audit flags `workflow_run` (FETCHED, docs.zizmor.sh/audits/); Scorecard's
Dangerous-Workflow flags `workflow_run` only together with a checkout of pull-request code (FETCHED,
raw.githubusercontent.com/ossf/scorecard/main/docs/checks.md).

| | A job after `gate` in ci.yml | A new workflow on `workflow_run` of CI |
|---|---|---|
| Runs after CI passed on `main` | `needs: gate` plus `if: github.event_name == 'push' && github.ref == 'refs/heads/main' && needs.gate.outputs.passed == 'true'` | `if: conclusion == 'success' && event == 'push' && head_branch == 'main'`, same repository; checks out `head_sha` |
| Concurrency | ci.yml's group cancels in progress (READ, `ci.yml:22-24`): a newer push to `main` can cancel the job between pushing the tag and dispatching | its own group, `cancel-in-progress: false`; a newer pending run replaces an older pending one (FETCHED, workflow-syntax page), which is harmless since the newer commit holds the older |
| Permissions | ci.yml is also called by `release.yml` (READ, `ci.yml:8-12`, `release.yml:59-67`). A job asking `contents: write` and `actions: write` inside it would make `release.yml`'s `ci` job grant them, or fail; which one is NOT ESTABLISHED. Either way the release run's CI would hold write permissions it never needs | only this workflow's one job has them |
| Definition | the commit's own ci.yml | `main`'s copy, which is what should decide tagging |
| Rehearsal | unchanged | unchanged (the rehearsal is `release.yml` on `main`, section 8) |
| Lint cost | none | one `# zizmor: ignore[dangerous-triggers]` with its reason: the run checks out a commit already on `main`, never pull-request code |

**Recommendation: `workflow_run`** in a new `.github/workflows/tag.yml`. **Cost:** one workflow file and one zizmor
ignore with a reason.

## 6. Tag protection beyond the environment

Today: "release tags" blocks deletion, update and non-fast-forward on `refs/tags/v*`, with no bypass actor (RUN).
Anyone with write access can still **create** a `v*` tag. What stops a hostile one is the `pypi` environment (owner
approval, READ `release.yml:26-29`).

FETCHED, docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets:
"Restrict creations: If selected, only users with bypass permissions can create branches or tags whose name matches
the pattern". FETCHED, .../creating-rulesets-for-a-repository: bypass may go to repository admins, the maintain or
write role, teams, "GitHub Apps" and Dependabot. The page does not list `GITHUB_TOKEN` or github-actions. The
`GITHUB_TOKEN` is an App installation token (section 2), and the required checks name the Actions integration
`15368` (RUN, `main` ruleset), but whether that App can be put on a bypass list is NOT ESTABLISHED.

| Option | Effect on section 2 |
|---|---|
| Keep the ruleset as it is | Option A works unchanged. A wrong tag cannot be moved or deleted, so a tag created by mistake stays (and burns its version: section 7). |
| Add "restrict creations", bypass: repository admin role | Blocks option A's `GITHUB_TOKEN` push unless the Actions App can bypass (NOT ESTABLISHED). Works for a hand-pushed tag by the owner. |
| Add "restrict creations", bypass: our own App | Works, but needs option B's App. |

**Recommendation: keep the ruleset as it is.** The environment's required reviewer already guards every publish, and
restricting creation would either need an App or rest on a fact not established. Revisit if a collaborator with write
access joins. **Cost:** none.

## 7. The trigger: automatic after CI, or a hand-pushed tag

SvelteDT's PRD-S7 releases automatically after CI on `main` with a computed version, the publish waiting for the
owner in the `npm` environment (READ, sveltedt `docs/PRD.md:840-849`). Here a release is a hand-pushed tag today
(READ, `release.yml:3-10`). #45's model is the automatic one.

| | Automatic after CI (the model) | Hand-pushed tag, with version-from-tag and generated notes |
|---|---|---|
| Who starts a release | every merge to `main` whose commits include a `feat`, `fix` or `!` | the owner, by pushing `v<version>` |
| Version | computed by git-cliff | the owner types it; `check-tag` refuses a tag that differs from `git cliff --bumped-version` |
| Needs | `tag.yml` (sections 2, 5), `actions: write` | nothing new: `push: tags` already starts `release.yml` |
| Pending approvals | one per releasable merge, each with its own permanent tag | one per tag the owner pushes |
| A rejected or abandoned approval | the tag stays (the ruleset blocks deletion), the draft release stays, the version is never on PyPI; the next release takes the next number | the owner simply does not push |
| Same as SvelteDT | yes | no |

**Recommendation: automatic, as the model says, with one rule that removes its main surprise: one release in
flight.** `tag.yml` creates no tag while the latest `v*` tag's GitHub release is still a draft (one `gh release view`
call). Merges made meanwhile are released by the next merge after the publish, or by re-running `tag.yml` by hand.
After a rejection the owner deletes the draft release (the tag stays, its number is skipped), which unblocks the next
run. Pre-releases (`rc`) are not produced by the automatic path; the 0.1.0 series already proved the pipeline. If the
owner wants a hand path for `rc` tags too, `release.yml` keeps its `push: tags` trigger; the cost is that a hand tag
then also becomes "the last release" for git-cliff, whose reading of a PEP 440 tag like `v0.1.0rc4` is NOT
ESTABLISHED (a `tag_pattern` of final versions only avoids it). **Cost:** the rule above, about four lines in
`tag.yml`; whether a job waiting for approval counts as running for concurrency was not needed by this rule and is
NOT ESTABLISHED.

## 8. Proposal and size of #76

**The changelog.** The model says the notes are generated from commits; the owner's rule of 2026-11-23 says they are
the version's CHANGELOG.md section; and with no commit to `main` the workflow cannot write CHANGELOG.md. The README
promises the breaking changes are listed there. Recommendation: the notes are CHANGELOG.md's `[Unreleased]` section as
the tag holds it (the adopter-facing prose, written in each PR as today), followed by git-cliff's list of the
`feat`/`fix`/breaking commits since the last tag. The PR after a release renames `[Unreleased]` to `[x.y.z] - date`;
a CI check fails while the latest `v*` tag has no section of its own. **Cost:** one human step per release and one
check; the owner's rule of 2026-11-23 is amended to "the `[Unreleased]` section plus the generated list". The literal
reading (generated notes only, CHANGELOG.md frozen at 0.1.0 and the README's promise reworded) costs less work but
moves the explanation of each breaking change into commit bodies, which a 100-character header rule does not reach.

**The pipeline** (recommendation only):

0. Before #76 lands: release the pending `[Unreleased]` work (scope, ColumnControl, the cache bound, the 400) as 0.2.0
   by today's hand path, so the first automatic run starts from a tag with no free-prose commits after it. Otherwise
   that run finds 25 unconventional commits and no bump.
1. A PR merges to `main`; CI runs as today.
2. `tag.yml` (`on: workflow_run` of CI, `types: [completed]`, `branches: [main]`; workflow `permissions: {}`; group
   `tag`, no cancel). One job, `permissions: contents: write, actions: write`, no environment: checks the conclusion,
   event and branch; checks out `head_sha` with `fetch-depth: 0`, `persist-credentials: false`; runs `git cliff
   --bumped-version` from the `release` group; stops if there is nothing to release or a draft release is in flight;
   creates the annotated tag `v<version>` on `head_sha` and pushes it with `GITHUB_TOKEN`; runs `gh workflow run
   release.yml --ref v<version>`.
3. `release.yml` (`on: workflow_dispatch`, plus `push: tags` only if the owner keeps a hand path): `check-tag`
   (`contents: read`: the commit is on `main`; on `main` without a tag, the rehearsal prints the next version, the tag
   it would create and the notes); `ci` (all of ci.yml; `dist` builds with hatch-vcs, `dist-check` compares with the
   tag); `draft-release` (`contents: write`; notes as above; condition `github.ref_type == 'tag'`); `publish` (`pypi`,
   `id-token: write` only, waits for the owner); `publish-release` (`contents: write`). The tagging job has no
   `id-token`; the publish job has neither `contents` nor `actions`.

**Files #76 would change:**

| File | Change |
|---|---|
| `.github/workflows/tag.yml` | new (section 2, 5, 7) |
| `.github/workflows/release.yml` | triggers, the release-or-rehearsal condition, the rehearsal's next-version step, notes from `[Unreleased]` plus git-cliff, `sed` of `__version__` removed |
| `.github/workflows/ci.yml` | `commits` job and its place in `gate`; `fetch-depth: 0` for `dist` and `dist-check`; the dist check's version argument; the changelog-section check |
| `cliff.toml` | new: types, 0.x bump rules, `tag_pattern`, skip `^Merge`, the notes template |
| `pyproject.toml`, `uv.lock` | `hatch-vcs` in `build-system.requires`, `source = "vcs"`, `[tool.uv] cache-keys`, a `release` group with `git-cliff` |
| `src/datatables_backend/__init__.py` | `__version__` from `importlib.metadata` |
| `scripts/check_dist.py`, `scripts/test_check_dist.py` | `tag` goes; `dist` takes the expected version |
| `scripts/changelog_notes.py`, its test | reads `[Unreleased]`; the check that the latest tag has its section |
| `.pre-commit-config.yaml` | the commit-msg `pygrep` hook |
| `docs/PRD.md`, `spec/`, `scripts/trace_check.py` inputs | a packaging requirement for the release path and the commit convention (its first sentence is the owner's); section 6, criterion 6 |
| `README.md` | the status line stops naming a version; how a release happens; the changelog promise if the owner changes it |
| `CHANGELOG.md` header, `CLAUDE.md` | the convention and the release path |

INFERRED: this is more than the two-day ceiling under which #45 was split (READ, #45 comment of 2026-11-22). A split
that keeps each part shippable: (i) version from the tag (hatch-vcs, check_dist, `__version__`); (ii) the commit
convention and its checks; (iii) `tag.yml`, the dispatch and the changelog path, with the rehearsal.

## 9. Owner decides

1. Tool: **git-cliff** from a uv `release` group (not PSR's Docker action; release-please ruled out).
2. Bump before 1.0: `feat` and breaking -> minor, `fix` -> patch, other types nothing; `perf` not an allowed type here.
3. Publish trigger: **option A**, the tagging job dispatches `release.yml` on the tag with `GITHUB_TOKEN` (no App).
4. Version source: **hatch-vcs**, build environment unpinned as today; `check_dist.py tag` goes.
5. Commit convention: **every non-merge commit** conventional, header at most 100 characters; merges stay the only
   merge method.
6. Check: a `pygrep` commit-msg hook after the private-word hook, and a `commits` job in CI needed by `gate`.
7. After CI: **`workflow_run`** in a new `tag.yml`, not a job in ci.yml.
8. Tag ruleset: **unchanged** (no "restrict creations").
9. Trigger: **automatic**, with one release in flight; no automatic `rc`; keep or drop the hand `push: tags` path
   (recommendation: drop it, a re-run of `release.yml` on an existing tag covers a failed publish).
10. Notes: **`[Unreleased]` prose plus the generated list**, the heading renamed in the next PR and checked in CI;
    this amends the rule of 2026-11-23.
11. Release 0.2.0 by today's path before #76 lands.
12. Split #76 into the three parts of section 8.

## Not established (to verify before relying on it)

- git-cliff: whether `--bumped-version` bumps a patch for commits of non-releasing types (`docs`, `test`, ...) and what
  it prints when nothing is to be released; whether a `skip` parser also removes a commit from the bump; how it reads a
  PEP 440 tag such as `v0.1.0rc4`.
- Which copy of `release.yml` a `workflow_dispatch` with `--ref v<version>` runs (INFERRED: the tag's).
- Whether the GitHub Actions App (`GITHUB_TOKEN`) can be a ruleset bypass actor.
- Whether a workflow called with `workflow_call` that holds a job asking for more permissions than the caller grants
  fails or runs.
- hatch-vcs: that the default `tag-pattern` drops the `v`; whether a depth-1 checkout of a tag ref holds the tag; that
  the untracked `dist/` does not make the tree dirty; the version a shallow non-tag checkout gets.
- Whether `uv lock --check` stays stable with a version from tags, and whether Dependabot moves
  `build-constraint-dependencies`.
- `pre-commit run --hook-stage commit-msg --commit-msg-filename` as the CI call.
- How long a job waits for a required reviewer before it fails (the environments page states 30 days only for the
  wait timer), and whether a job waiting for approval counts as running for concurrency.
- Scorecard's `checks.md` was fetched from its raw file; its rendered page answered 503.
