# 009: Release automation (draft for the owner)

This is the evaluation for issue #45 (part 45a): how to meet the owner's release model of 2026-11-22. The build is #76.
SvelteDT (its PRD-S7) needs the same answers, so this note is shared with it; each section says whether it is
**shared** or **Python only**. **Nothing here is decided.** Once the owner has chosen, his choices are recorded in this
file and the options he rejected are cut to one line each.

Labels: READ (a repository file or an issue, with its place), RUN (a command run on 2026-11-24, named; git-cliff runs
used `uvx --from git-cliff==2.14.2` on a scratch clone of `main` at `0daf2f4` or on scratch repositories, all removed
afterwards), FETCHED (a primary source, with its URL, fetched 2026-11-24), INFERRED (reasoning from the facts given),
NOT ESTABLISHED (to verify before #76 relies on it; all are listed at the end).

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
| PyPI's trusted publisher is `release.yml` (top level only) | READ, `release.yml:108-109` |
| The version is written by hand: `__version__ = '0.1.0'`; hatchling reads it | READ, `src/datatables_backend/__init__.py:8`, `pyproject.toml:1-3, 13, 86-87` |
| `check_dist.py tag` compares the tag with that source version; `check_dist.py dist` checks the files against it | READ, `scripts/check_dist.py:3-7, 50-53, 85-86, 151-169` |
| Release notes come from `CHANGELOG.md`, hand-written Keep a Changelog prose with an `[Unreleased]` section | READ, `scripts/changelog_notes.py:3-6`, `CHANGELOG.md:1-7` |
| README promises every breaking change "is listed in CHANGELOG.md" and names the version ("Status: 0.1.0") | READ, `README.md:18-19` |
| `pypi` environment: deployments from the tag pattern `v*` only, the owner as required reviewer, `can_admins_bypass: false`, `prevent_self_review: false` | RUN, `gh api .../environments/pypi` and `.../environments/pypi/deployment-branch-policies` |
| Tag ruleset "release tags" on `refs/tags/v*`: deletion, non-fast-forward and update blocked, no bypass actor; creation is not restricted | RUN, `gh api .../rulesets/27804950` |
| `main` ruleset: pull request required, merge method **merge only** (`allowed_merge_methods: ["merge"]`), required checks `gate` and `dependency-review`, strict (branch up to date) | RUN, `gh api .../rulesets/27673398` |
| Repository: merge commits on, squash and rebase off; merge commit title `MERGE_MESSAGE` ("Merge pull request #N from ..."), body `PR_TITLE` | RUN, `gh api repos/hniedner/datatables-backend` |
| Since `v0.1.0`: 27 commits, of which 17 are not merges. Headers are free prose, often over 100 characters (e.g. `f314d7c`). git-cliff's default configuration parses 3 of the 27 as conventional, with the types `PRD` and `Changelog` ("PRD: shorten the header ...", "Changelog: ..."), and skips 24 | RUN, `git rev-list --count v0.1.0..HEAD` and `--no-merges`; `git-cliff --unreleased --context` |
| Dependabot already writes conventional headers (`ci(deps): ...`, `build(deps): ...`) | READ, `.github/dependabot.yml:7-10, 25-27` |
| Actions are pinned by commit SHA with a version comment; zizmor runs in pre-commit | READ, every `uses:` in `.github/workflows/`, `.pre-commit-config.yaml:118-123` |
| `uv.lock` records no version for the project (it is dynamic): the entry is `name = "datatables-backend"`, `source = { editable = "." }`, then `dependencies` | READ, `uv.lock:638-640` |

**Findings for the owner** (#45's text differs from what the repository shows):

- #45 says the tag path of `release.yml` "has never run". It has: it published 0.1.0rc2 to 0.1.0 (READ, #76
  comment of 2026-11-24; RUN, `git for-each-ref refs/tags`: `v0.1.0rc1` to `v0.1.0`, all annotated).
- #45 says "the PRD's packaging requirements ... describe the hand-pushed tag". PRD-Q1 to Q14 say nothing about tags
  or the release path; only criterion 6 of section 6 names "the release workflow" (READ, `docs/PRD.md:1531-1638,
  1858`). The hand-pushed tag is described in `release.yml`'s comments.

## 1. The tool (shared)

"Pushes no commit to `main`" rules out every mode that commits a version or opens a release pull request.

| Tool | Can it tag and create a release from a runner without a commit? | How it is pinned |
|---|---|---|
| release-please | **No.** It "maintains Release PRs"; a release is made "when you're ready to tag a release, simply merge the release PR". No documented mode tags without a merged release PR (FETCHED, github.com/googleapis/release-please and github.com/googleapis/release-please-action). **Ruled out.** | JS action by SHA |
| python-semantic-release (PSR) 10.7.0 | **Yes.** With `--no-commit --tag --push` it tags the existing HEAD, pushes only the tag and creates the VCS release; it still edits and stages the version files and changelog locally, uncommitted (FETCHED, `src/semantic_release/cli/commands/version.py` on the `master` branch of python-semantic-release/python-semantic-release). Its release is created public, so the owner's draft-first rule needs `--no-vcs-release` and today's `gh release create --draft`. It ignores merge commits by default (`ignore_merge_commits = True`); `feat` minor, `fix`/`perf` patch, other types no release (FETCHED, `.../commit_parser/conventional/options.py`). | The action is a Docker action built at run time (FETCHED, `action.yml`). Its `requirements.txt` pins PSR itself (`python-semantic-release == 10.7.0`), but the image is `FROM python:3.14-slim-trixie` (a tag), `apt install` takes current packages, and `pip install --upgrade pip setuptools wheel` and `pip install --pre -r requirements.txt` resolve PSR's dependencies unpinned (FETCHED, `src/gh_action/requirements.txt` and `src/gh_action/Dockerfile`). So a SHA pin of the action does not pin what runs. As a CLI from `uv.lock` it is hash-pinned, at the cost of about 12 more runtime packages in the lock (gitpython, requests, python-gitlab, jinja2, click, rich, ...) (FETCHED, pypi.org/pypi/python-semantic-release/json). Python only. |
| git-cliff 2.14.2 | It computes and prints; it creates no tag and no release (`--bumped-version` "Prints bumped version for unreleased changes", FETCHED, git-cliff.org/docs/usage/args). The tag and the draft release are steps the workflow runs itself (section 8). | On PyPI as `git-cliff`: binary wheels for Linux, macOS and Windows, **no dependencies** (FETCHED, pypi.org/pypi/git-cliff/2.14.2/json): a uv dependency group, hash-pinned by `uv.lock`, moved by Dependabot's `uv` updates with their 7-day cooldown (READ, `dependabot.yml:21-31`). On npm as `git-cliff` for SvelteDT (FETCHED, git-cliff.org/docs/installation/npm). |
| None (a regex of our own) | Ruled out by "no release script of our own" (READ, #45). | |

**Recommendation: git-cliff, from a uv dependency group of its own (`release`).** Reasons: one tool and one
configuration (`cliff.toml`) for both projects, since SvelteDT can run it from npm; no dependencies; it does the one
thing the model needs from a tool (version and notes), while tagging and the draft release stay in steps the
repository already has. PSR's Docker action is out under the repository's pinning rule, because its pin does not pin
what runs.

**How git-cliff bumps, measured (RUN).** Without configuration it bumps **every** parsed commit that is not `feat` or
breaking as a patch: on `main` today `git-cliff --bumped-version` prints `v0.1.1`, from the three free-prose headers
that parse as types `PRD` and `Changelog`; a lone `docs:` commit after a tag gives `v0.1.1` too. The source agrees: a
commit not skipped sets `Patch` by default, and `no_increment_regex` is tested **before** the breaking check and
`continue`s (FETCHED, `crates/next_version/src/version_increment.rs` on `main` of release-plz/release-plz, the crate
git-cliff calls; the regex is matched against the conventional type, or the whole message if it is not conventional).
Configurations run on scratch repositories with `v0.1.0` and then the commits named:

| `cliff.toml` | `docs:` only | `docs:` and `refactor!:` | `docs(readme):` and `refactor:` with a `BREAKING CHANGE:` footer |
|---|---|---|---|
| default rules (with the 0.x settings below) | `v0.1.1` (wrong) | `v0.2.0` | not run |
| `no_increment_regex = "^(build\|chore\|ci\|docs\|refactor\|style\|test)"` (documented: "Configures commit types that should not increment the version", FETCHED, git-cliff.org/docs/configuration/bump) | `v0.1.0`, "nothing to bump" | **`v0.1.0`: the breaking change is lost** | not run |
| `filter_commits = true` with parsers for `feat` and `fix` only | not run | **`v0.1.0`: lost** | not run |
| **parsers `^feat` and `^fix` grouped, then `{ message = ".*", skip = true }`, with `protect_breaking_commits = true`** | `v0.1.0`, "nothing to bump" (RUN with the other types listed in a skip parser instead of `.*`; with `.*`, RUN on `main`'s free-prose history, same result) | **`v0.2.0`** | **`v0.2.0`**, listed and marked breaking |

The reviewer reported 0.1.1 for the two naive fixes; on my scratch history they gave 0.1.0 ("nothing to bump"). Both
results mean the same: the breaking change is dropped. The last row keeps it: `protect_breaking_commits` means "any
breaking changes will be protected against being skipped due to any commit parser" (FETCHED,
git-cliff.org/docs/configuration/git). The same configuration on `main` today gives `v0.1.0`, "nothing to bump" (RUN),
because the free-prose headers fall to the catch-all skip. This differs from the reviewer's proposed fix (allow `!`
only on `feat` and `fix`): it needs no extra commit rule.

**The bump table** (recommended), with `features_always_bump_minor = true` and `breaking_always_bump_major = false`
(FETCHED, configuration/bump: when false, a breaking change "bumps the minor version if the major version is 0"):

| Commit | Before 1.0 | From 1.0 |
|---|---|---|
| `feat:` | minor (0.2.0 to 0.3.0) | minor |
| `fix:` | patch (RUN: `fix(plan):` after `v0.1.0` gives `v0.1.1`) | patch |
| any type with `!` or a `BREAKING CHANGE:` footer | **minor** (the README's policy: a 0.x minor may break the Python API, READ `README.md:18-19`) | major (flip `breaking_always_bump_major` at 1.0) |
| any other type, and anything that does not parse | no release | no release |

`perf` is not listed: it releases nothing under this configuration; a performance change adopters should get is a
`fix:`. A runtime floor raised in `pyproject.toml` changes what adopters install, so it is a `fix:` or `feat:` (or `!`),
not `build:`; Dependabot's `build(deps)` touches only `uv.lock` and releases nothing.

**Costs:** one dependency (`git-cliff`, in a `release` group, not in `dev`); one config file (`cliff.toml`); a test in
#76 that runs the pinned git-cliff with `cliff.toml` on a scratch repository and fails unless `refactor!:` and a
`BREAKING CHANGE:` footer each give a minor bump and `docs:` gives none, so a later edit of `cliff.toml` cannot drop a
breaking change silently. If the owner wants a second guard, the commit rule can also allow `!` and the footer only on
`feat` and `fix` (the reviewer's proposal); with the configuration above it is not needed for correctness.

## 2. How the tag starts the publish run (shared)

FETCHED, docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow ("Triggering
a workflow from a workflow"): "events triggered by the `GITHUB_TOKEN` will not create a new workflow run", and
"`workflow_dispatch` and `repository_dispatch` events always create workflow runs"; otherwise "you can use a GitHub
App installation access token or a personal access token instead of `GITHUB_TOKEN`". The same rule: FETCHED,
docs.github.com/en/actions/concepts/security/github_token ("The `GITHUB_TOKEN` secret is a GitHub App installation
access token"). So a tag created with `GITHUB_TOKEN` does **not** start `release.yml`'s `push: tags` trigger.

The environment's rule: "The deployment branch or tag rule is matched against the `GITHUB_REF` of the workflow run"
(FETCHED, docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments). Here the only rule
is the tag pattern `v*` (RUN, above).

| Option | Ref of the publish job | Accepted by `pypi` (`v*` tags)? | Cost |
|---|---|---|---|
| A. The tagging job creates the tag, then runs `gh workflow run release.yml --ref v<version>` with `GITHUB_TOKEN` | `workflow_dispatch`: `GITHUB_REF` is the "Branch or tag that received dispatch" (FETCHED, docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), so `refs/tags/v<version>`; and "Each workflow run will use the version of the workflow that is present in the associated commit SHA or Git ref of the event" (FETCHED, docs.github.com/en/actions/concepts/workflows-and-actions/workflows), so the tag's `release.yml` runs | **Yes** | `actions: write` on the tagging job (the dispatch endpoint needs "Actions" write, FETCHED, docs.github.com/en/rest/authentication/permissions-required-for-github-apps). No new identity, no secret. PyPI's publisher stays `release.yml`. The dispatch answers 200 with `workflow_run_id`, `run_url` and `html_url` (FETCHED, docs.github.com/en/rest/actions/workflows, "Create a workflow dispatch event"), so the tagging job can print the link to the release run. |
| B. A GitHub App token pushes the tag; `release.yml` starts on `push: tags` as today | `refs/tags/v<version>` | **Yes** | A new identity: register an App, store its client ID as a variable and its private key as a secret, install it (FETCHED, docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/making-authenticated-api-requests-with-a-github-app-in-a-github-actions-workflow), plus `actions/create-github-app-token`. The owner owns the App and its key and rotates it; a stored key is what trusted publishing was chosen to avoid. A personal token is the same with the owner's own account behind it: worse. |
| C. One workflow: the publish job in the tagging run | `workflow_run` has `GITHUB_REF` = default branch (FETCHED, events page) | **No**, and it breaks "the publish runs in a run on that tag" | ruled out |
| D. `repository_dispatch` | `GITHUB_REF`: "Default branch"; `GITHUB_SHA`: "Last commit on default branch" (FETCHED, events page) | **No** | ruled out |

**Recommendation: A.** It meets the environment's rule with the token the run already has. `release.yml` then tells
a release from a rehearsal by `github.ref_type == 'tag'` instead of `github.event_name == 'push'` (READ,
`release.yml:76-77, 114-115`). The job that dispatches has no `id-token`; the publish job has no `contents` or
`actions` permission: the separation holds. **Cost:** `actions: write` on one job (it can also cancel and re-run
workflows). Whether `release.yml` keeps `push: tags` for a hand path is the owner's choice in section 7.

## 3. The version from the tag at build time (Python only)

The backend is hatchling, reading `__version__` from the source (READ, `pyproject.toml:1-3, 86-87`). With no commit to
`main`, the version has to come from the tag.

| Option | Facts | Verdict |
|---|---|---|
| hatch-vcs 0.5.0 (`[tool.hatch.version] source = "vcs"`) | "uses your preferred version control system (like Git)", options `tag-pattern`, `fallback-version`, `raw-options` (FETCHED, github.com/ofek/hatch-vcs). Requires `hatchling>=1.1.0` and `setuptools-scm>=8.2.0` (FETCHED, pypi.org/pypi/hatch-vcs/json); setuptools-scm 10.3.4 requires `vcs-versioning`, `packaging`, `setuptools` (FETCHED, pypi.org/pypi/setuptools-scm/json). setuptools-scm: on a clean tag the version is `{tag}`; N commits later `{next_version}.dev{distance}+g{hash}`; dirty adds `+dYYYYMMDD`; PKG-INFO is a version source, which is what a wheel built from the sdist reads; a shallow repository warns by default (FETCHED, setuptools-scm.readthedocs.io/en/latest/usage/ and /config/). | **Recommended** |
| hatchling's built-in `env` source | No new dependency, but an unset variable raises `RuntimeError("environment variable ... is not set")` (FETCHED, `backend/src/hatchling/version/source/env.py` in pypa/hatch). `uv sync` builds the project, so every checkout and every CI job would need the variable. | Rejected: a surprise for every contributor |
| Stamp the version into the file at build time | The sdist would then differ from the commit, which `check_dist.py` refuses (READ, `check_dist.py:18`) | Rejected |

What changes with hatch-vcs:

- **A build from the tag** (the publish run): version = the tag without `v` (FETCHED: `{tag}` on a clean tag;
  INFERRED that the default `tag-pattern` drops the `v`, verify). Give `dist` `fetch-depth: 0` (today it checks out
  with the default depth, READ, `ci.yml:242-248`); whether a depth-1 checkout of a tag ref holds the tag is NOT
  ESTABLISHED.
- **A build from a commit that is not a tag** (the rehearsal, every pull request): a `.devN+g<hash>` version. PyPI
  refuses a local version, so a rehearsal cannot produce an uploadable name (INFERRED).
- **From the sdist** (uv builds the sdist, then the wheel from it, FETCHED, docs.astral.sh/uv/concepts/projects/build/):
  setuptools-scm reads PKG-INFO, so both files carry the same version without git.
- **`__version__`** becomes `importlib.metadata.version('datatables-backend')` (standard library, so PRD-Q3 holds).
  `dist-check`'s assertion that `__version__` equals the installed metadata (READ, `ci.yml:294-295`) can then no
  longer fail and goes; its check that the wheel's file name carries the metadata version stays.
- **uv's cache**: uv rebuilds a local project only when `pyproject.toml` (or `setup.py`/`setup.cfg`) changes; for a
  version from tags it documents `cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true, tags = true }
  }]` (FETCHED, docs.astral.sh/uv/concepts/cache/). Without it a local checkout reports a stale version.
- **`uv.lock`** holds no project version today (READ, above), so a new tag cannot change the lock.
- **`scripts/check_dist.py`**: `tag TAG` goes (the version can no longer differ from the tag). `dist DIR` takes the
  expected version as an argument (the tag without `v` in a tag run); without a tag it requires the sdist and wheel to
  state the same `.dev` version. The members-against-the-commit rules stay; hatch-vcs writes no version file unless
  its `version-file` hook is configured (INFERRED from its README), so the sdist still holds only tracked files plus
  PKG-INFO. `release.yml`'s `sed` of `__version__` (READ, `release.yml:54`) goes.

**Costs:** four packages join the build environment (hatch-vcs, setuptools-scm, vcs-versioning, setuptools), unpinned
as hatchling is today (READ, `ci.yml:235-238`). They can be hash-pinned with `[tool.uv] build-constraint-dependencies`
and `require-build-hashes` (FETCHED, uv build page); whether Dependabot moves such pins is NOT ESTABLISHED, so the
recommendation keeps today's posture: unpinned, judged by `dist-check` from a job they never ran in. The build stays
as offline-safe as today, which is not offline.

**SvelteDT's counterpart:** npm publishes a folder "containing a program described by a package.json file" or a
tarball of it (FETCHED, docs.npmjs.com/cli/v11/commands/npm-publish), and SvelteDT's `package.json` holds `"version":
"0.0.0"` (READ, sveltedt `package.json:4`). So the version is stamped into `package.json` in the release run before
packing, without a commit or tag: `npm --no-git-tag-version version <version>` ("Setting this to false results in no
commit being made at all", FETCHED, docs.npmjs.com/cli/v11/commands/npm-version). SvelteDT packs with pnpm and
publishes that tarball (READ, sveltedt `docs/decisions/001-quality-tooling.md:188-190`); its `ci.yml` has no pack or
publish step yet (RUN, grep of sveltedt `.github/workflows/ci.yml`), so where the stamp goes is its own build's
choice.

## 4. Commit convention and its check (shared rule; the hook mechanics are Python only)

This is the largest consequence for daily work.

**What the tool reads.** `main` is merge-only by ruleset (RUN, above), so it holds the PR's own commits, GitHub's
"Merge pull request #N from ..." commits and "Merge branch 'main' into ..." commits from updating a branch. With the
recommended `cliff.toml`, merge headers are unconventional and are dropped by `filter_unconventional`, or else fall to
the catch-all skip; a separate `{ message = "^Merge", skip = true }` would be redundant (harmless). PSR ignores merges by
default (FETCHED, section 1).

| Option | What changes for every commit and PR | Repository setting | Verdict |
|---|---|---|---|
| a. **Every non-merge commit** is conventional, header at most 100 characters (SvelteDT's rule) | Every header becomes `type(scope)!: subject`, at most 100 characters; today's long descriptive headers move into the body. Review-round commits are typed by what they change (`test:`, `docs:`, `fix:`). A commit made in GitHub's web editor ("Update x.py") or a revert made with GitHub's button (`Revert "..."`) fails the check and is reworded. | none | **Recommended** |
| b. **The PR title** is conventional and becomes the merge commit's header; the tool reads merge commits only | Branch commits stay free prose; one changelog entry per PR | merge commit title `PR_TITLE`, body `PR_BODY`; the check must rerun on a title edit (`pull_request` type `edited`) | Possible; reading only merge commits in git-cliff (skip on `merge_commit = false`) is NOT ESTABLISHED |
| c. **Squash merges**, PR title as the commit | One commit per PR on `main` | squash on, merge off, in the repository **and** the `main` ruleset | Not recommended: the separate commits the project relies on (a red test before its fix, a removal "as its own commit", READ `docs/PRD.md:1931`, e.g. `f56b814`) vanish from `main`. No tracked file records why squash is off (RUN, grep of CLAUDE.md, PRD, spec); that reason is the owner's. |

**Recommendation: (a)**, for parity with SvelteDT and no change to the repository's settings.

**The rule** (one regex): a type out of `feat`, `fix`, `docs`, `test`, `ci`, `build`, `refactor`, `chore`, `style`;
an optional `(scope)`; an optional `!`; `: ` and a subject; a header of at most 100 characters. git's own `Merge `
headers pass. A revert is written as a typed commit (`fix: revert ...`, or `fix!:`/`feat!:` when it takes back a
released feature), because git's `Revert "..."` header would be dropped as unconventional (INFERRED from
`filter_unconventional`) and so would never release or appear in the notes. `fixup!`, `squash!` and `amend!` headers
pass the local hook, so `git commit --fixup` keeps working, and the CI job refuses them, so none reaches `main`
(SvelteDT's rule too, READ, sveltedt `scripts/check-repo.ts:2405-2407, 2434-2435`).

**The check:**

- Locally: a commit-msg hook in `.pre-commit-config.yaml` of pre-commit's built-in `pygrep` language, `args:
  [--negate, --multiline]` ("To require all files to match, use `args: [--negate]`"; commit-msg hooks get the message
  file, FETCHED, pre-commit.com). No new dependency and no script. It runs **after** `private-words-message`, which
  keeps `fail_fast` and runs first (READ, `.pre-commit-config.yaml:7-25`), so a message holding a private word stops
  before any other hook sees it. `default_install_hook_types` already installs `commit-msg` (READ,
  `.pre-commit-config.yaml:3`).
- In CI: a `commits` job in `ci.yml`, needed by `gate`, that writes each non-merge commit's message of the pull
  request (`base..head`) or of the push (`before..after`) to a file and runs only the new hook by its id: `pre-commit
  run <id> --hook-stage commit-msg --commit-msg-filename <file>` (RUN, `uvx --from pre-commit==4.7.0 pre-commit run
  --help`, the version `uv.lock` holds: `[hook]` "A single hook-id to run" and `--commit-msg-filename`). Running one id
  means `private-words-message` does not run there, as CI skips the private-word hooks today (READ, `ci.yml:112-114`).
  The job also refuses `fixup!`/`squash!`/`amend!` headers. A manual or `workflow_call` run has no range and the job
  says so, as the coverage step does (READ, `ci.yml:84-90`).
- SvelteDT's `commits` step runs in its gate only; its pre-push hook runs the secret scans, not `commits` (READ,
  sveltedt `scripts/check-repo.ts:2456-2493, 3038-3115`, `.githooks/pre-push`). Here, too, CI is the check that binds.
- The private-word hooks stay unchanged and keep working: the new hook neither reads nor prints the word list. What
  GitHub authors (merge commits, PR titles) stays unscanned, as today (READ, `CLAUDE.md:31-33`).

**Costs:** every contributor writes shorter, typed headers; one hook and one CI job; the types are listed in two
places (the hook's regex and `cliff.toml`), so a test that the two lists agree belongs in #76 (SvelteDT's decision
001 asks for the same, READ, sveltedt `docs/decisions/001-quality-tooling.md:273-283`).

## 5. `workflow_run` or a job after `gate` in ci.yml (shared)

FETCHED, events page: `workflow_run` "will only trigger a workflow run if the workflow file exists on the default
branch"; its `GITHUB_SHA` is the "Last commit on default branch" and `GITHUB_REF` the default branch; "A workflow run
is triggered regardless of the conclusion of the previous workflow" (so the conclusion must be checked); it "is able
to access secrets and write tokens"; at most three levels of chaining. The run object carries `head_branch`,
`head_sha`, `event`, `conclusion` (FETCHED, docs.github.com/en/rest/actions/workflow-runs, "Get a workflow run").
zizmor's `dangerous-triggers` audit flags `workflow_run` (FETCHED, docs.zizmor.sh/audits/); Scorecard's
Dangerous-Workflow flags `workflow_run` only together with a checkout of pull-request code (FETCHED,
raw.githubusercontent.com/ossf/scorecard/main/docs/checks.md).

| | A job after `gate` in ci.yml | A new workflow on `workflow_run` of CI |
|---|---|---|
| Runs after CI passed on `main` | `needs: gate` plus `if: github.event_name == 'push' && github.ref == 'refs/heads/main' && needs.gate.outputs.passed == 'true'` | `if: conclusion == 'success' && event == 'push' && head_branch == 'main'`, same repository; checks out `head_sha` |
| Concurrency | ci.yml's group cancels in progress (READ, `ci.yml:22-24`): a newer push to `main` can cancel the job between creating the tag and dispatching | its own group, `cancel-in-progress: false`; a newer pending run replaces an older pending one (FETCHED, docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax), harmless since the newer commit holds the older |
| Permissions | ci.yml is also called by `release.yml` (READ, `ci.yml:8-12`, `release.yml:59-67`). A job asking `contents: write` and `actions: write` inside it would make `release.yml`'s `ci` job grant them, or fail; which one is NOT ESTABLISHED. Either way the release run's CI would hold write permissions it never needs | only this workflow's one job has them |
| Definition | the commit's own ci.yml | `main`'s copy, which is what should decide tagging |
| Lint cost | none | one `# zizmor: ignore[dangerous-triggers]` with its reason: the run checks out a commit already on `main`, never pull-request code |

**Recommendation: `workflow_run`** in a new `.github/workflows/tag.yml`. **Cost:** one workflow file and one zizmor
ignore with a reason.

## 6. Tag protection beyond the environment (shared)

Today: "release tags" blocks deletion, update and non-fast-forward on `refs/tags/v*`, with no bypass actor (RUN).
Anyone with write access can still **create** a `v*` tag. What stops a hostile one is the `pypi` environment (owner
approval, READ `release.yml:26-29`).

FETCHED, docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets:
"Restrict creations: If selected, only users with bypass permissions can create branches or tags whose name matches
the pattern". FETCHED, .../creating-rulesets-for-a-repository: bypass may go to repository admins, the maintain or
write role, teams, "GitHub Apps" and Dependabot; `GITHUB_TOKEN` and github-actions are not listed. The required checks
name the Actions integration `15368` (RUN, `main` ruleset), but whether that App can be put on a bypass list is NOT
ESTABLISHED.

| Option | Effect on section 2 |
|---|---|
| Keep the ruleset as it is | Option A works unchanged. A tag cannot be moved or deleted, so a tag whose publish is rejected stays (section 7). |
| Add "restrict creations", bypass: repository admin role | Blocks option A's `GITHUB_TOKEN` unless the Actions App can bypass (NOT ESTABLISHED). Works for a hand-pushed tag by the owner. |
| Add "restrict creations", bypass: our own App | Works, but needs option B's App. |

**Recommendation: keep the ruleset as it is.** The environment's required reviewer already guards every publish, and
restricting creation would either need an App or rest on a fact not established. Revisit if a collaborator with write
access joins. **Cost:** none.

## 7. The trigger: automatic after CI, or a hand-pushed tag (shared)

SvelteDT's PRD-S7 releases automatically after CI on `main` with a computed version, the publish waiting for the
owner in the `npm` environment (READ, sveltedt `docs/PRD.md:840-849`). Here a release is a hand-pushed tag today
(READ, `release.yml:3-10`). #45's model is the automatic one.

| | Automatic after CI (the model) | Hand-pushed tag, with version-from-tag and generated notes |
|---|---|---|
| Who starts a release | every merge to `main` whose commits include a `feat`, `fix` or breaking change | the owner, by pushing `v<version>` |
| Version | computed by git-cliff | the owner types it; `check-tag` refuses a tag that differs from git-cliff's |
| Needs | `tag.yml` (sections 2, 5), `actions: write` | nothing new: `push: tags` already starts `release.yml` |
| Pending approvals | one per releasable merge, each with its own permanent tag | one per tag the owner pushes |
| A rejected or abandoned approval | the tag stays (the ruleset blocks deletion), the draft release stays, the version is never on PyPI, and the tag becomes git-cliff's last release, so its commits never appear in a later generated list (INFERRED: `--unreleased` "Processes the commits that do not belong to a tag", FETCHED, usage/args) | the owner does not push |
| A job waiting for approval | waits "up to 30 days on environment approvals"; a run lasts at most 35 days including the wait (FETCHED, docs.github.com/en/actions/reference/limits) | same |
| Same as SvelteDT | yes | no |

**Recommendation: automatic, as the model says, with one release in flight.** Before it creates a tag, `tag.yml`
looks up the release of the latest `v*` tag by name (`gh release view v<latest> --json isDraft`; without a tag,
`gh release view` shows the latest release, which may skip a draft: NOT ESTABLISHED, so name the tag) and creates
nothing while that release is a draft. Commits merged meanwhile are released by the next merge after the publish. To
release them without waiting for a merge, the owner dispatches `tag.yml` on `main`: that run has no `workflow_run`
payload, so it works on `main`'s latest commit and must check itself that CI's run for that commit concluded
`success`. After a rejected publish the owner deletes the draft release (the tag stays and its number is skipped),
which unblocks the next run. Pre-releases (`rc`) are not produced by the automatic path. If the owner keeps a hand path
for `rc` tags, `cliff.toml` **must** set `tag_pattern = "^v[0-9]+\\.[0-9]+\\.[0-9]+$"`: with an rc tag `v0.1.1rc1` as
the latest tag, git-cliff exits 1 with "Semver error: `unexpected character 'v' while parsing major version number`";
with that `tag_pattern` it ignores the rc tag and computes `v0.2.0` (RUN, scratch repository). The pattern costs
nothing and belongs in `cliff.toml` either way. **Cost:** about eight lines in `tag.yml` (the draft look-up and the
dispatch path's CI check).

## 8. Proposal and size of #76

**The changelog (Python only; SvelteDT has no CHANGELOG.md).** The model says the notes are generated from commits;
the owner's rule of 2026-11-23 says they are the version's CHANGELOG.md section; with no commit to `main` the
workflow cannot write CHANGELOG.md; and the README promises breaking changes are listed there.

| Option | How | Cost |
|---|---|---|
| P. **The releasing PR writes the section** (the reviewer's proposal) | The `commits` job also runs `git-cliff --bumped-version` on the PR. `main` is strict (up to date), so the PR's merge commit is what `main` will hold and the output is the version the merge would release (INFERRED). Rule: if it prints a new version X, CHANGELOG.md must have `## [X]` holding the changes; otherwise the changes go under `[Unreleased]`. `draft-release` keeps reading the `## [X]` section (`changelog_notes.py X`), optionally followed by git-cliff's list for the tag. | The owner's rule of 2026-11-23 holds as written. No rename PR. On the tag, git-cliff prints the tag itself ("nothing to bump", RUN above), so the check passes inside `release.yml`'s `ci` too. A PR that turns a pending patch into a minor renames the section. A rejected version's section stays in CHANGELOG.md and needs a line saying it was never published. |
| Q. `[Unreleased]` at the tag plus the generated list, renamed by the next PR, with a CI check that the latest tag has its section (the earlier draft) | as named | **Deadlocks**: in `release.yml`'s `ci` on the tag CHANGELOG.md has only `[Unreleased]`, so `gate` fails and nothing publishes; every PR is red between a tag and the rename PR; it demands a section after a rejected release. Workable only with the check kept out of the `workflow_call` path; it would amend the owner's rule, if he agrees. |
| R. Generated notes only, CHANGELOG.md frozen at 0.1.0 | git-cliff renders the notes | Least work, but would change the owner's rule and the README's promise, and moves the explanation of each breaking change into commit bodies. |

**Recommendation: P.** It keeps the owner's rule literally, has no deadlock and no extra PR, and puts the
adopter-facing prose in the PR that causes the release, where the reviewer sees it. The date in `## [X] - date` is
unknown until the merge; `changelog_notes.py` already accepts a heading without one (READ,
`scripts/changelog_notes.py:28`), so whether the rule asks for a date is the owner's call.

**Step 0, before #76's tagging part lands** (owner's choice). With the recommended `cliff.toml`, a run on today's
history computes nothing (RUN: "nothing to bump", section 1); with git-cliff's defaults it would compute 0.1.1.

| Option | Effect |
|---|---|
| 0a. Release the pending `[Unreleased]` work (scope, ColumnControl, the cache bound, the 400) as 0.2.0 by today's hand path | It reaches adopters before #76; needs the owner's OK to publish. |
| 0b. No hand release | The first `feat:` after #76 computes 0.2.0 (the 0.x rule), and under option P that PR moves the `[Unreleased]` prose into `## [0.2.0]`. The pending work waits for that PR. |

Recommendation: 0b unless the owner wants the pending work out sooner; it costs no extra release.

**The pipeline** (recommendation only):

1. A PR merges to `main`; CI runs as today, including `commits` (section 4) and the changelog rule (option P).
2. `tag.yml` (`on: workflow_run` of CI, `types: [completed]`, `branches: [main]`, plus `workflow_dispatch`; workflow
   `permissions: {}`; group `tag`, no cancel). One job, `permissions: contents: write, actions: write`, no
   environment: checks the conclusion, event and branch (or, when dispatched, CI's conclusion for `main`'s head);
   checks out that commit with `fetch-depth: 0` and `persist-credentials: false`; runs `uv run --frozen --only-group
   release git-cliff --bumped-version` (`--only-group`: "The project and its dependencies will be omitted", FETCHED,
   docs.astral.sh/uv/reference/cli/, so no build backend runs in a job that holds write tokens); compares the output
   with the latest `v*` tag and stops if they are equal (RUN: with nothing to release git-cliff prints a warning on
   stderr and the **last** tag, e.g. `v0.1.0`, on stdout, and exits 0; the output already carries the `v`); stops if
   that tag's release is a draft (section 7); creates the annotated tag through the REST API with `gh api` (`POST
   /repos/{owner}/{repo}/git/tags`, then `POST .../git/refs`; both need "Contents" write, FETCHED, permissions page),
   so no git credential is stored in the checkout; runs `gh workflow run release.yml --ref v<version>` and prints the
   run's link.
3. `release.yml` (`on: workflow_dispatch`, plus `push: tags` only if the owner keeps a hand path): `check-tag`
   (`contents: read`: the commit is on `main`; on `main` without a tag, the rehearsal prints the next version, the tag
   it would create and the notes); `ci` (all of ci.yml; `dist` builds with hatch-vcs, `dist-check` compares with the
   tag); `draft-release` (`contents: write`; condition `github.ref_type == 'tag'`); `publish` (`pypi`, `id-token:
   write` only, waits for the owner); `publish-release` (`contents: write`). The tagging job has no `id-token`; the
   publish job has neither `contents` nor `actions`.

**Files #76 would change:**

| File | Change |
|---|---|
| `.github/workflows/tag.yml` | new (sections 2, 5, 7) |
| `.github/workflows/release.yml` | triggers, the release-or-rehearsal condition, the rehearsal's next-version step, `sed` of `__version__` removed |
| `.github/workflows/ci.yml` | `commits` job (with the changelog rule) and its place in `gate`; `fetch-depth: 0` for `dist` and `dist-check`; the dist check's version argument; the `__version__` assertion dropped |
| `cliff.toml` | new: parsers, `protect_breaking_commits`, 0.x bump rules, `tag_pattern`, the notes template |
| `pyproject.toml`, `uv.lock` | `hatch-vcs` in `build-system.requires`, `source = "vcs"`, `[tool.uv] cache-keys`, a `release` group with `git-cliff` |
| `src/datatables_backend/__init__.py` | `__version__` from `importlib.metadata` |
| `scripts/check_dist.py`, `scripts/test_check_dist.py` | `tag` goes; `dist` takes the expected version |
| a test of `cliff.toml` | `refactor!:` and a `BREAKING CHANGE:` footer bump minor, `docs:` nothing |
| `.pre-commit-config.yaml` | the commit-msg `pygrep` hook |
| `docs/PRD.md`, `spec/` | a packaging requirement for the release path and the commit convention (its first sentence is the owner's, READ, `CLAUDE.md:91-92`); section 6, criterion 6 |
| `README.md` | the status line stops naming a version; how a release happens |
| `CHANGELOG.md` header, `CLAUDE.md` | the convention and the release path |

INFERRED: this is more than the two-day ceiling under which #45 was split (READ, #45 comment of 2026-11-22). A split
that keeps each part shippable, in this order:

1. **(ii) The convention and its checks** first: the hook, the `commits` job, `cliff.toml` and its test. Nothing in
   the release path changes, and every commit after it is readable by git-cliff.
2. **(i) The version from the tag** (hatch-vcs, `__version__`, `check_dist.py`). Shippable on today's hand-pushed path
   only if the same part also removes `release.yml`'s rehearsal `sed` of `__version__` and replaces `check_dist.py
   tag`; otherwise the next hand release breaks.
3. **(iii) `tag.yml`, the dispatch, the changelog rule, the rehearsal**: needs (i) and (ii), and the owner's first
   sentence of the new PRD requirement.

## 9. Owner decides

1. Tool. Recommendation: **git-cliff** from a uv `release` group (not PSR's Docker action; release-please ruled out).
2. Bump. Recommendation: `feat` and any breaking change -> minor before 1.0, `fix` -> patch, everything else nothing;
   `cliff.toml` with a catch-all skip and `protect_breaking_commits`, guarded by a test.
3. Publish trigger. Recommendation: **option A**, the tagging job dispatches `release.yml` on the tag with
   `GITHUB_TOKEN` (no App).
4. Version source. Recommendation: **hatch-vcs**, build environment unpinned as today; `check_dist.py tag` goes.
5. Commit convention. Recommendation: **every non-merge commit** conventional, header at most 100 characters; merges
   stay the only merge method; reverts written as typed commits.
6. Check. Recommendation: a `pygrep` commit-msg hook after the private-word hook, and a `commits` job in CI needed by
   `gate` that also refuses `fixup!` commits.
7. After CI. Recommendation: **`workflow_run`** in a new `tag.yml`, not a job in ci.yml.
8. Tag ruleset. Recommendation: **unchanged** (no "restrict creations").
9. Trigger. Recommendation: **automatic**, one release in flight; no automatic `rc`; drop the hand `push: tags` path
   (a dispatch of `release.yml` on an existing tag covers a failed publish); the final-only `tag_pattern` either way.
10. Notes. Recommendation: **option P**, the releasing PR writes `## [X]`, checked in CI; the 2026-11-23 rule stays
    as written. Whether the heading needs a date: the owner's call.
11. Step 0. Recommendation: **0b**, no hand release; 0a if the pending work should reach adopters before #76.
12. #76. Recommendation: split into (ii), (i), (iii), in that order.

## Not established (to verify before relying on it)

- Whether the GitHub Actions App (`GITHUB_TOKEN`) can be a ruleset bypass actor.
- Whether a workflow called with `workflow_call` that holds a job asking for more permissions than the caller grants
  fails or runs.
- hatch-vcs: that the default `tag-pattern` drops the `v`; whether a depth-1 checkout of a tag ref holds the tag; that
  the untracked `dist/` does not make the tree dirty; the version a shallow non-tag checkout gets.
- Whether Dependabot moves `build-constraint-dependencies`.
- Whether `gh release view` without a tag skips a draft (section 7 avoids relying on it by naming the tag).
- git-cliff: reading only merge commits (option b of section 4); not needed by the recommendation.
- Scorecard's `checks.md` was fetched from its raw file; its rendered page answered 503.
