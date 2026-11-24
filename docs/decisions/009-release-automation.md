# 009: Release automation

**Status: decided (owner, 2026-11-24, following the advisor).** The decisions are in section 9.

This is the evaluation for issue #45 (part 45a): how releases are made once #76 is built. SvelteDT (its PRD-S7) needs
the same answers, so this note is shared with it; each section says whether it is **shared** or **Python only**. The
options and their reasons in sections 1 to 8 stay as the record of the choice, including the ones not chosen. Sections
2, 3 and 5, and every part marked "automatic trigger", are recorded options: they are not built.

**The model was put to the owner again** (section 7), and he chose that he starts releases. Under the automatic
trigger a rejected or abandoned approval leaves a permanent tag, a draft release and a skipped version, which has
already happened once by hand (0.1.0rc1); at this project's merge rate an approval would be pending almost always; and
adopters are better served by fewer, coherent releases.

Labels: READ (a repository file, an issue or a PR, with its place), RUN (a command run on 2026-11-24, named; git-cliff
runs used `uvx --from git-cliff==2.14.2` on a scratch clone of `main` at `0daf2f4` or on scratch repositories, all
removed afterwards), RUN by the independent review (a command the independent review of this draft ran on
2026-11-24, not re-run here), FETCHED (a primary source, with its URL, fetched 2026-11-24), INFERRED (reasoning from
the facts given), NOT ESTABLISHED (to verify before #76 relies on it; all are listed at the end).

## The model, and where today's repository stands

The model (READ, #45 body; the owner's decision of 2026-11-22): after CI has passed on a push to `main`, a workflow
computes the next version from the conventional commit messages since the last release, creates the tag `v<version>`
and a GitHub release with a changelog generated from those commits, and pushes no commit to `main`. The publish runs
in a run on that tag, publishes the build of that tag, stays in the `pypi` environment and waits for the owner. The
job that tags cannot publish; the job that publishes cannot tag. An existing tool, no release script of our own. A
rehearsal that publishes nothing stays possible (READ, #76 body). Owner rule of 2026-11-23 (READ, #76 comment): the
workflow creates the GitHub release as a draft before the publish job and makes it public after PyPI accepted the
upload; its notes are the version's section of CHANGELOG.md.

| Fact | Source |
|---|---|
| `release.yml` publishes on a hand-pushed `v*` tag, and a manual run on `main` is the rehearsal; its jobs are `check-tag`, `ci` (all of ci.yml), `draft-release` (`contents: write`), `publish` (`pypi` environment, `id-token: write` only), `publish-release` | READ, `.github/workflows/release.yml:1-19, 30-149` |
| PyPI's trusted publisher is `release.yml` (top level only) | READ, `release.yml:108-109` |
| The version is written by hand: `__version__ = '0.1.0'`; hatchling reads it | READ, `src/datatables_backend/__init__.py:8`, `pyproject.toml:1-3, 13, 86-87` |
| 0.1.0's release PR changed `__version__`, the classifier, the README status line and CHANGELOG.md's `## [0.1.0] - 2026-11-24` (4 files) | READ, PR #169 (`gh pr view 169`) |
| `check_dist.py tag` compares the tag with that source version; `check_dist.py dist` checks the files against it | READ, `scripts/check_dist.py:3-7, 50-53, 85-86, 151-169` |
| Release notes come from `CHANGELOG.md`, hand-written Keep a Changelog prose with an `[Unreleased]` section | READ, `scripts/changelog_notes.py:3-6`, `CHANGELOG.md:1-7` |
| 0.1.0rc1 was "Tagged but not published: its deployment was rejected before upload" | READ, `CHANGELOG.md:143, 177` |
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

## 1. The tool and the bump rule (shared)

"Pushes no commit to `main`" rules out every mode that commits a version or opens a release pull request.

| Tool | Can it tag and create a release from a runner without a commit? | How it is pinned |
|---|---|---|
| release-please | **No.** It "maintains Release PRs"; a release is made "when you're ready to tag a release, simply merge the release PR". No documented mode tags without a merged release PR (FETCHED, github.com/googleapis/release-please and github.com/googleapis/release-please-action). **Ruled out.** | JS action by SHA |
| python-semantic-release (PSR) 10.7.0 | **Yes.** With `--no-commit --tag --push` it tags the existing HEAD, pushes only the tag and creates the VCS release; it still edits and stages the version files and changelog locally, uncommitted (FETCHED, `src/semantic_release/cli/commands/version.py` on the `master` branch of python-semantic-release/python-semantic-release). Its release is created public, so the owner's draft-first rule needs `--no-vcs-release` and today's `gh release create --draft`. It ignores merge commits by default; `feat` minor, `fix`/`perf` patch, other types no release (FETCHED, `.../commit_parser/conventional/options.py`). | A Docker action built at run time (FETCHED, `action.yml`). Its `requirements.txt` pins PSR itself (`python-semantic-release == 10.7.0`), but the image is `FROM python:3.14-slim-trixie` (a tag), `apt install` takes current packages, and `pip install --upgrade pip setuptools wheel` and `pip install --pre -r requirements.txt` resolve PSR's dependencies unpinned (FETCHED, `src/gh_action/requirements.txt` and `src/gh_action/Dockerfile`). So a SHA pin of the action does not pin what runs. As a CLI from `uv.lock` it is hash-pinned, at the cost of about 12 more runtime packages in the lock (FETCHED, pypi.org/pypi/python-semantic-release/json). Python only. |
| git-cliff 2.14.2 | It computes and prints; it creates no tag and no release (`--bumped-version` "Prints bumped version for unreleased changes", FETCHED, git-cliff.org/docs/usage/args). | On PyPI as `git-cliff`: binary wheels for Linux, macOS and Windows, **no dependencies** (FETCHED, pypi.org/pypi/git-cliff/2.14.2/json): a uv dependency group, hash-pinned by `uv.lock`, moved by Dependabot's `uv` updates with their 7-day cooldown (READ, `dependabot.yml:21-31`). On npm as `git-cliff` for SvelteDT (FETCHED, git-cliff.org/docs/installation/npm). |
| None (a regex of our own) | Ruled out by "no release script of our own" (READ, #45). | |

**Recommendation: git-cliff, from a uv dependency group of its own (`release`),** under either trigger of section 7.
One tool and one `cliff.toml` for both projects; no dependencies; it gives the version and the notes and nothing else.
PSR's Docker action is out under the repository's pinning rule, because its pin does not pin what runs.

**How git-cliff bumps (RUN).** Without configuration it bumps **every** parsed commit that is not `feat` or breaking as
a patch: on `main` today `git-cliff --bumped-version` prints `v0.1.1`, from the three free-prose headers. The source
agrees: a commit not skipped sets `Patch` by default, and `no_increment_regex` is tested **before** the breaking check
and `continue`s (FETCHED, `crates/next_version/src/version_increment.rs` on `main` of release-plz/release-plz, the
crate git-cliff calls). Measured on scratch repositories (`v0.1.0`, then the commits named):

| `cliff.toml` | `docs:` only | `docs:` and `refactor!:` | `refactor:` with a `BREAKING CHANGE:` footer |
|---|---|---|---|
| default rules | `v0.1.1` (wrong) | `v0.2.0` | not run |
| `no_increment_regex` for the non-releasing types (documented: "Configures commit types that should not increment the version", FETCHED, git-cliff.org/docs/configuration/bump) | `v0.1.0` | **`v0.1.0`: the breaking change is lost** | not run |
| `filter_commits = true` with parsers for `feat` and `fix` only | not run | **`v0.1.0`: lost** | not run |
| **parsers `^feat` and `^fix`, then `{ message = ".*", skip = true }`, with `protect_breaking_commits = true`** | `v0.1.0` | **`v0.2.0`** | **`v0.2.0`**, listed and marked breaking |

The reviewer reported 0.1.1 for the two naive fixes; on my scratch history they gave 0.1.0. Both mean the breaking
change is dropped. The last row keeps it: `protect_breaking_commits` means "any breaking changes will be protected
against being skipped due to any commit parser" (FETCHED, git-cliff.org/docs/configuration/git). On `main` today that
configuration gives "nothing to bump" (RUN), as the free-prose headers fall to the catch-all skip. It needs no extra
commit rule; restricting `!` to `feat` and `fix` (the reviewer's proposal) stays available as a second guard.

**The 0.x bump rule.** git-cliff: with `features_always_bump_minor = false`, "a feature bumps the patch version if the
major version is 0"; with `breaking_always_bump_major = false`, "a breaking change bumps the minor version if the major
version is 0" (FETCHED, git-cliff.org/docs/configuration/bump).

| Rule | `feat` | `fix` | breaking (`!` or footer) | other types, free prose |
|---|---|---|---|---|
| Earlier draft: `features_always_bump_minor = true` | minor | patch | minor | nothing |
| **Recommended: `features_always_bump_minor = false`, `breaking_always_bump_major = false`** | **patch** | patch | **minor** | nothing |

RUN, the recommended `cliff.toml` (the catch-all row above, plus `tag_pattern = "^v[0-9]+\\.[0-9]+\\.[0-9]+$"`,
`features_always_bump_minor = false`, `breaking_always_bump_major = false`), each case a scratch repository with an
annotated `v0.1.0` and then one commit; `git-cliff --config cliff.toml --bumped-version` printed:

| Commit after `v0.1.0` | Output |
|---|---|
| `feat: a new option` | `INFO git_cliff > Using configuration from: .../cliff.toml` then `v0.1.1` |
| `fix: a wrong bound` | `INFO ... Using configuration from: .../cliff.toml` then `v0.1.1` |
| `refactor!: drop an old name` | `INFO ... Using configuration from: .../cliff.toml` then `v0.2.0` |
| `docs: a note` | `INFO ... Using configuration from: .../cliff.toml`, `WARN git_cliff > There is nothing to bump`, then `v0.1.0` |
| `PRD: shorten the header` | `INFO ... Using configuration from: .../cliff.toml`, `WARN git_cliff > There is nothing to bump`, then `v0.1.0` |

So a plain `feat:` bumps only a patch, and a breaking change at 0.x a minor, as documented. From 1.0 the same file
gives the usual rule without any change: after an annotated `v1.0.0`, `feat: another option` gave `v1.1.0` and
`fix!: a changed default` gave `v2.0.0` (RUN). Whether and when to go to 1.0 is a decision for then.

**Recommendation: the recommended rule.** The README says a 0.x minor may break the Python API (READ, `README.md:18-19`).
Under this rule a patch never breaks, so an adopter's `~=0.2.0` takes features and fixes and never a break, and a minor
means "read the changelog". **Cost:** the README's policy sentence must then say once that before 1.0 a patch release
may add features and never breaks, and a minor release may break. That sentence is the owner's, decided with this rule;
this draft does not edit the README.

`perf` is not a type here: a performance change adopters should get is a `fix:`. A runtime floor raised in
`pyproject.toml` is a `fix:` or `feat:` (or `!`), not `build:`; Dependabot's `build(deps)` touches only `uv.lock` and
releases nothing.

**Costs of git-cliff:** one dependency (`git-cliff`, in a `release` group, not in `dev`); one `cliff.toml`; a test in
#76 that runs the pinned git-cliff with `cliff.toml` on a scratch repository and fails unless `refactor!:` and a
`BREAKING CHANGE:` footer give a minor, `feat:` and `fix:` a patch, and `docs:` nothing, so a later edit of `cliff.toml`
cannot drop a breaking change silently.

## 2. How the tag starts the publish run (shared; automatic trigger only)

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
| A. The tagging job creates the tag, then runs `gh workflow run release.yml --ref v<version>` with `GITHUB_TOKEN` | `workflow_dispatch`: `GITHUB_REF` is the "Branch or tag that received dispatch" (FETCHED, docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), so `refs/tags/v<version>`; and "Each workflow run will use the version of the workflow that is present in the associated commit SHA or Git ref of the event" (FETCHED, docs.github.com/en/actions/concepts/workflows-and-actions/workflows), so the tag's `release.yml` runs | **Yes** | `actions: write` on the tagging job (the dispatch endpoint needs "Actions" write, FETCHED, docs.github.com/en/rest/authentication/permissions-required-for-github-apps). No new identity, no secret. PyPI's publisher stays `release.yml`. The dispatch answers 200 with `workflow_run_id`, `run_url` and `html_url` (FETCHED, docs.github.com/en/rest/actions/workflows, "Create a workflow dispatch event"). |
| B. A GitHub App token pushes the tag; `release.yml` starts on `push: tags` as today | `refs/tags/v<version>` | **Yes** | A new identity: register an App, store its client ID as a variable and its private key as a secret, install it (FETCHED, docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/making-authenticated-api-requests-with-a-github-app-in-a-github-actions-workflow), plus `actions/create-github-app-token`. The owner owns the App and rotates its key; a stored key is what trusted publishing was chosen to avoid. A personal token is worse. |
| C. One workflow: the publish job in the tagging run | `workflow_run` has `GITHUB_REF` = default branch (FETCHED, events page) | **No**, and it breaks "the publish runs in a run on that tag" | ruled out |
| D. `repository_dispatch` | `GITHUB_REF`: "Default branch"; `GITHUB_SHA`: "Last commit on default branch" (FETCHED, events page) | **No** | ruled out |

**Recommendation: A.** It meets the environment's rule with the token the run already has; `release.yml` tells a
release from a rehearsal by `github.ref_type == 'tag'` instead of `github.event_name == 'push'` (READ,
`release.yml:76-77, 114-115`). The job that dispatches has no `id-token`; the publish job has no `contents` or
`actions` permission. **Cost:** `actions: write` on one job (it can also cancel and re-run workflows).

## 3. The version from the tag at build time (Python only; automatic trigger only)

The backend is hatchling, reading `__version__` from the source (READ, `pyproject.toml:1-3, 86-87`). With no commit to
`main`, the version has to come from the tag. Under the owner-started trigger (section 7) none of this section is
needed: the release PR writes the version, as today.

| Option | Facts | Verdict |
|---|---|---|
| hatch-vcs 0.5.0 (`[tool.hatch.version] source = "vcs"`) | Options `tag-pattern`, `fallback-version`, `raw-options` (FETCHED, github.com/ofek/hatch-vcs). Requires `hatchling>=1.1.0` and `setuptools-scm>=8.2.0` (FETCHED, pypi.org/pypi/hatch-vcs/json); setuptools-scm 10.3.4 requires `vcs-versioning`, `packaging`, `setuptools` (FETCHED, pypi.org/pypi/setuptools-scm/json). setuptools-scm: on a clean tag `{tag}`; N commits later `{next_version}.dev{distance}+g{hash}`; dirty adds `+dYYYYMMDD`; PKG-INFO is a version source; a shallow repository warns by default; without `fallback_version` it "will error if it fails to detect the version" (FETCHED, setuptools-scm.readthedocs.io/en/latest/usage/ and /config/). The default `tag-pattern` drops the `v`; a depth-1 checkout of a tag ref gives the tag's version; an untracked `dist/` does not change the version (RUN by the independent review). | **Recommended** |
| hatchling's built-in `env` source | No new dependency, but an unset variable raises `RuntimeError("environment variable ... is not set")` (FETCHED, `backend/src/hatchling/version/source/env.py` in pypa/hatch); `uv sync` builds the project, so every checkout and CI job would need the variable. | Rejected |
| Stamp the version into the file at build time | The sdist would then differ from the commit, which `check_dist.py` refuses (READ, `check_dist.py:18`) | Rejected |

What changes with hatch-vcs: `dist` gets `fetch-depth: 0` (READ, `ci.yml:242-248`, default depth today); a build from
a non-tag commit gets a `.devN+g<hash>` version, which PyPI refuses (INFERRED), so a rehearsal cannot produce an
uploadable name; a wheel built from the sdist reads PKG-INFO (uv builds the sdist, then the wheel from it, FETCHED,
docs.astral.sh/uv/concepts/projects/build/); a build from a git-less source archive has no version unless a fallback
is set (INFERRED from setuptools-scm's "will error"); `__version__` becomes `importlib.metadata.version(...)`
(standard library, PRD-Q3 holds) and `dist-check`'s `__version__` assertion (READ, `ci.yml:294-295`) can no longer
fail and goes; `[tool.uv] cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true, tags = true } }]`
(FETCHED, docs.astral.sh/uv/concepts/cache/) keeps a local checkout's version fresh; `uv.lock` holds no project
version (READ, above), so tags cannot change it; `check_dist.py tag` goes, `dist DIR` takes the expected version; the
rehearsal's `sed` of `__version__` (READ, `release.yml:54`) goes.

**Costs:** four packages join the build environment (hatch-vcs, setuptools-scm, vcs-versioning, setuptools), unpinned
as hatchling is today (READ, `ci.yml:235-238`); hash pins are possible with `build-constraint-dependencies` (FETCHED,
uv build page), but whether Dependabot moves them is NOT ESTABLISHED.

**SvelteDT's counterpart:** npm publishes a folder "containing a program described by a package.json file" or a
tarball of it (FETCHED, docs.npmjs.com/cli/v11/commands/npm-publish), and SvelteDT's `package.json` holds `"version":
"0.0.0"` (READ, sveltedt `package.json:4`). Under an automatic trigger the version is stamped before packing with `npm
--no-git-tag-version version <version>` ("Setting this to false results in no commit being made at all", FETCHED,
docs.npmjs.com/cli/v11/commands/npm-version). SvelteDT packs with pnpm and publishes that tarball (READ, sveltedt
`docs/decisions/001-quality-tooling.md:188-190`); its `ci.yml` has no pack or publish step yet (RUN, grep).

## 4. Commit convention and its check (shared rule; the hook mechanics are Python only)

This is the consequence for daily work, under either trigger: git-cliff can only compute or check a version from
typed commits.

**What the tool reads.** `main` is merge-only by ruleset (RUN, above), so it holds the PR's own commits, GitHub's
"Merge pull request #N from ..." commits and "Merge branch 'main' into ..." commits. With the recommended `cliff.toml`
merge headers are dropped by `filter_unconventional` or the catch-all skip; a separate `^Merge` skip would be
redundant (harmless).

| Option | What changes for every commit and PR | Repository setting | Verdict |
|---|---|---|---|
| a. **Every non-merge commit** is conventional, header at most 100 characters (SvelteDT's rule) | Every header becomes `type(scope)!: subject`, at most 100 characters; today's long descriptive headers move into the body. Review-round commits are typed by what they change. A commit from GitHub's web editor ("Update x.py") or its revert button (`Revert "..."`) fails the check and is reworded. | none | **Recommended** |
| b. **The PR title** is conventional and becomes the merge commit's header; the tool reads merge commits only | Branch commits stay free prose; one changelog entry per PR | merge commit title `PR_TITLE`, body `PR_BODY`; the check reruns on a title edit | Possible; reading only merge commits in git-cliff is NOT ESTABLISHED |
| c. **Squash merges**, PR title as the commit | One commit per PR on `main` | squash on, merge off, in the repository **and** the `main` ruleset | Not recommended: the separate commits the project relies on (a red test before its fix, a removal "as its own commit", READ `docs/PRD.md:1931`, e.g. `f56b814`) vanish from `main`. No tracked file records why squash is off (RUN, grep); that reason is the owner's. |

**The rule** (one regex): a type out of `feat`, `fix`, `docs`, `test`, `ci`, `build`, `refactor`, `chore`, `style`;
an optional `(scope)`; an optional `!`; `: ` and a subject; at most 100 characters. git's own `Merge ` headers pass. A
revert is a typed commit (`fix: revert ...`, or `!` when it takes back a released feature), since git's `Revert "..."`
header would be dropped as unconventional (INFERRED). `fixup!`, `squash!`, `amend!` pass the local hook, so `git commit
--fixup` keeps working, and the CI job refuses them (SvelteDT's rule too, READ, sveltedt `scripts/check-repo.ts:2405-2407,
2434-2435`).

**The check:** locally a commit-msg hook of pre-commit's built-in `pygrep` language, `args: [--negate, --multiline]`
("To require all files to match, use `args: [--negate]`"; commit-msg hooks get the message file, FETCHED,
pre-commit.com), after `private-words-message`, which keeps `fail_fast` and runs first (READ,
`.pre-commit-config.yaml:7-25`); `commit-msg` is already installed (READ, `.pre-commit-config.yaml:3`). In CI a
`commits` job, needed by `gate`, runs only that hook by its id on each non-merge commit of the PR or push: `pre-commit
run <id> --hook-stage commit-msg --commit-msg-filename <file>` (RUN, `uvx --from pre-commit==4.7.0 pre-commit run
--help`, the version `uv.lock` holds: `[hook]` "A single hook-id to run", `--commit-msg-filename`), so
`private-words-message` does not run there, as CI skips the private-word hooks today (READ, `ci.yml:112-114`). SvelteDT's
`commits` step runs in its gate only, not in its pre-push hook (READ, sveltedt `scripts/check-repo.ts:2456-2493,
3038-3115`). The private-word hooks are unchanged; what GitHub authors stays unscanned, as today (READ,
`CLAUDE.md:31-33`).

**Costs:** every contributor writes shorter, typed headers; one hook and one CI job; the types are listed in the hook's
regex and in `cliff.toml`, so a test that the lists agree belongs in #76 (SvelteDT's decision 001 asks the same, READ,
sveltedt `docs/decisions/001-quality-tooling.md:273-283`).

## 5. `workflow_run` or a job after `gate` in ci.yml (shared; automatic trigger only)

FETCHED, events page: `workflow_run` "will only trigger a workflow run if the workflow file exists on the default
branch"; its `GITHUB_SHA` is the "Last commit on default branch"; "A workflow run is triggered regardless of the
conclusion of the previous workflow"; it "is able to access secrets and write tokens". The run object carries
`head_branch`, `head_sha`, `event`, `conclusion` (FETCHED, docs.github.com/en/rest/actions/workflow-runs). zizmor's
`dangerous-triggers` audit flags `workflow_run` (FETCHED, docs.zizmor.sh/audits/); Scorecard's Dangerous-Workflow flags
it only with a checkout of pull-request code (FETCHED, raw.githubusercontent.com/ossf/scorecard/main/docs/checks.md).

| | A job after `gate` in ci.yml | A new workflow on `workflow_run` of CI |
|---|---|---|
| Concurrency | ci.yml's group cancels in progress (READ, `ci.yml:22-24`): a newer push can cancel the job between creating the tag and dispatching | its own group, `cancel-in-progress: false` |
| Permissions | ci.yml is also called by `release.yml` (READ, `ci.yml:8-12`, `release.yml:59-67`); a write-permission job inside it would make the release run's CI grant them, or fail (NOT ESTABLISHED which) | only this workflow's one job has them |
| Definition | the commit's own ci.yml | `main`'s copy |
| Lint cost | none | one `# zizmor: ignore[dangerous-triggers]` with its reason |

**Recommendation: `workflow_run`** in a new `tag.yml`. **Cost:** one workflow file and one zizmor ignore.

## 6. Tag protection beyond the environment (shared)

Today: "release tags" blocks deletion, update and non-fast-forward on `refs/tags/v*`, with no bypass actor (RUN);
anyone with write access can still create a `v*` tag, and the `pypi` environment's reviewer stops a hostile one (READ,
`release.yml:26-29`). "Restrict creations: If selected, only users with bypass permissions can create branches or tags
whose name matches the pattern" (FETCHED, docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets);
bypass may go to repository admins, roles, teams, "GitHub Apps" and Dependabot, not listed: `GITHUB_TOKEN` (FETCHED,
.../creating-rulesets-for-a-repository). Whether the Actions App can be a bypass actor is NOT ESTABLISHED, so "restrict
creations" would block section 2's option A unless an own App is added.

**Recommendation: keep the ruleset as it is.** Revisit if a collaborator with write access joins. **Cost:** none.

## 7. Who starts a release (shared): the owner, or every merge

SvelteDT's PRD-S7 releases automatically after CI on `main` (READ, sveltedt `docs/PRD.md:840-849`); #45's model does
the same. Both options below are complete.

| | **Owner starts** (the smaller scope) | **Every merge starts** (the model) |
|---|---|---|
| How | Changes go under `[Unreleased]` as today. One release PR renames it to `## [X] - <date>` and sets `__version__`, as #169 did for 0.1.0 (READ). After it merges the owner pushes `v<X>`; `release.yml` runs as today. | After CI passes on `main`, `tag.yml` computes the version, creates the tag and dispatches `release.yml` on it (sections 2, 5). |
| Version | the release PR writes it; CI on that PR fails unless it equals `git-cliff --bumped-version` for the PR's merge commit (`main` is strict, so the PR is up to date, INFERRED); `check-tag` also refuses a tag whose version differs from `git-cliff --ignore-tags "^<tag>$" --bumped-version` (RUN: on a commit tagged `v0.2.0` whose commits give a patch, that command printed `v0.1.1`); only the new tag is ignored there, never an unpublished older one (see "A version that was tagged but not published" below) | computed by git-cliff |
| Notes | the `## [X]` section, as the owner's rule of 2026-11-23 says | the PR that causes the release writes `## [X]` (section 8, option P) |
| A rejected, abandoned or expired approval | the owner pushed the tag knowingly; it stays (the ruleset blocks deletion), as with 0.1.0rc1 (READ, `CHANGELOG.md:177`); the version is skipped | the tag stays, the draft release stays, the version is skipped. Once the tag exists the owner's choice is approve or burn. |
| Afterwards | under both: the owner deletes the draft release, if one exists; the tag stays, and "A version that was tagged but not published" below brings its commits into the next release | same |
| Pending approvals | one per release the owner starts | one per releasable merge; at this project's merge rate (RUN, `git log --merges --first-parent v0.1.0..0daf2f4`: 8 pull requests merged into `main` since `v0.1.0`, all on 2026-11-24) one would be pending almost always |
| A job waiting for approval | waits "up to 30 days on environment approvals" (FETCHED, docs.github.com/en/actions/reference/limits); past that it fails, leaving the tag and the draft release (INFERRED), cleaned up as in the row above | same; and while that draft stands, `tag.yml` creates nothing, so its log and job summary name the blocking draft release and the `release.yml` run waiting on it |
| New in #76 | part (ii) of section 8 (the convention, the hook, the `commits` job, `cliff.toml` and its test) plus the two version checks | part (ii) plus `tag.yml`, `workflow_run`, the dispatch, hatch-vcs (4 build packages, `importlib.metadata`, uv `cache-keys`, the `check_dist.py` rewrite) and the changelog rule |
| Not needed | none of sections 2, 3, 5; none of hatch-vcs's open questions | |
| Same as SvelteDT | no | yes |

**Recommendation: the owner starts a release.** Under the automatic trigger every merge with a `feat` or `fix` makes a
tag that cannot be removed, so the owner can only approve or burn a version; that has already happened once
(0.1.0rc1); at this merge rate an approval would be pending almost always; and adopters are served by fewer, coherent
releases. **This would amend the owner's decision of 2026-11-22 (#45's model) and depart from SvelteDT's PRD-S7; the
choice is his alone.** **Cost:** one release PR per release (four files for 0.1.0) and a hand-pushed tag, as today.
**Revisit when** release PRs become a burden, or a second maintainer joins.

If the owner keeps the automatic trigger, the rest stands as drafted: one release in flight (`tag.yml` looks up the
latest tag's release by name, `gh release view v<latest> --json isDraft`, and creates nothing while it is a draft; a
dispatch of `tag.yml` on `main` releases pending commits after a publish and checks CI's conclusion for `main`'s head
itself; after a rejection the owner deletes the draft release), no automatic `rc`, and `tag_pattern =
"^v[0-9]+\\.[0-9]+\\.[0-9]+$"` in `cliff.toml`, which is needed under either trigger while rc tags exist: with
`v0.1.1rc1` as the latest tag git-cliff exits 1 with "Semver error: `unexpected character 'v' while parsing major
version number`", and with the pattern it ignores the rc tag (RUN).

**A version that was tagged but not published** (both triggers, since a hand-pushed tag can be rejected too). git-cliff
treats every final `v*` tag as a release, so the rejected tag's commits would fall out of the next release's notes.
`ignore_tags`: "While `skip_tags` drop commits from the changelog, `ignore_tags` include ignored commits into the next
tag" (FETCHED, git-cliff.org/docs/configuration/git). RUN on a scratch repository with the recommended `cliff.toml`:
annotated `v0.1.0`, then `refactor!: drop an old name` tagged `v0.2.0` (rejected), then `fix: a wrong bound`:

| Command (`git-cliff --config cliff.toml ...`) | Output after the `INFO ... Using configuration from: .../cliff.toml` line |
|---|---|
| `--bumped-version` | `v0.2.1` |
| `--ignore-tags "^v0.2.0$" --bumped-version` | `v0.2.0` |
| `--unreleased` | `## unreleased`, `- a wrong bound` |
| `--ignore-tags "^v0.2.0$" --unreleased` | `## unreleased`, `- a wrong bound` (the rejected tag's commit is **not** included) |
| `--ignore-tags "^v0.2.0$" v0.1.0..HEAD` | `## unreleased`, `- drop an old name (breaking)`, `- a wrong bound` |
| `--ignore-tags "^v0.2.0$" --bump --tag v0.2.1 v0.1.0..HEAD` | `WARN git_cliff > There is nothing to bump`, `## v0.2.1`, `- drop an old name (breaking)`, `- a wrong bound` |

Two results differ from what was expected. With `--ignore-tags`, `--bumped-version` computes from `v0.1.0` again and
gives `v0.2.0`, a tag that already exists and cannot be reused; and `--unreleased` still stops at the rejected tag.
So the two uses split: the **version** is computed without `--ignore-tags` (`v0.2.1`, still a minor step from the last
published 0.1.0, since the rejected tag carried the break), and the **notes** use `--ignore-tags` with the range from
the last published tag (`v0.1.0..HEAD`), which lists both commits. The rehearsal uses the same notes command. Under
the owner-started trigger the notes are CHANGELOG.md's section, so the rejected version's `## [X]` prose is merged into
the next release PR's section; the generated list is only a cross-check there.

The list of unpublished tags is the final `v*` tags minus the versions PyPI lists. `https://pypi.org/pypi/datatables-backend/json`
answers a plain read: its `releases` are `0.1.0`, `0.1.0rc2`, `0.1.0rc3`, `0.1.0rc4`, and `info.version` is `0.1.0`
(FETCHED); 0.1.0rc1, the rejected one, is absent, as the CHANGELOG says. **Limit:** a hand-kept list is a step someone
forgets. The release job builds it from that URL each time, or `check-tag` refuses a tag whose notes range would start
at an unpublished tag. Cost: one read of PyPI in the release run and the rehearsal (INFERRED: a few lines of `gh`/`jq`
style shell, no new tool).

## 8. Proposal and size of #76

**Step 0: release what is waiting** (both triggers). `[Unreleased]` on `main` holds (READ, `CHANGELOG.md:7-40`): the
total cache bound `TotalCache(max_entries=...)` (#50); `scope=` on `fetch` and `fetch_sync` (#79, marked "Not
breaking"); ColumnControl's per-column filters answered on both backends (#163, #164, #165); and the FastAPI
dependency's HTTP 400 on a client disconnect (#151).

The next release is 0.1.1 under the recommended rule, since nothing in it breaks: an application on 0.1.0 can take
`scope=`, ColumnControl, the cache bound and the disconnect fix without changing code. Under the draft's rule the same
release would be 0.2.0.

- ColumnControl: answered where it was refused, a loosening; nothing that was answered before answers differently.
- The cache bound: it costs time, not results; a dropped total is recounted to the same number.
- The disconnect 400: a fix; nobody can rely on a 500 sent into a dead connection.
- `scope=`: a new optional keyword (READ, `CHANGELOG.md:13-15`).

**Recommendation: release it now by today's hand path** (a release PR as #169, then the owner's tag). It needs the
owner's explicit OK to publish.

**If the owner starts releases** (recommended): #76 shrinks to part (ii) below plus the two version checks of section
7, and its rehearsal stays as today, printing git-cliff's next version besides the notes. Files: `.pre-commit-config.yaml`
(the hook), `.github/workflows/ci.yml` (the `commits` job, the release-PR version check, `gate`), `release.yml`
(`check-tag`'s git-cliff comparison), `cliff.toml` and its test, `pyproject.toml`/`uv.lock` (the `release` group),
`docs/PRD.md` and `spec/` (a requirement for the release path and the convention, its first sentence the owner's,
READ, `CLAUDE.md:91-92`), `README.md` (the policy sentence of section 1), `CHANGELOG.md` header, `CLAUDE.md`.

The rehearsal runs the release check only while a release is pending (no tag `v<version>` yet); otherwise it prints
git-cliff's next version.

**If the automatic trigger is kept**, the changelog has three options:

| Option | How | Cost |
|---|---|---|
| P. **The releasing PR writes the section** | The `commits` job runs `git-cliff --bumped-version` on the PR; `main` is strict, so this is the version the merge would release (INFERRED). If it prints a new version X, CHANGELOG.md must have `## [X] - <date>`; otherwise the changes go under `[Unreleased]`. | The owner's rule of 2026-11-23 holds as written. No rename PR. On the tag git-cliff prints the tag itself ("nothing to bump", RUN), so the check passes in `release.yml`'s `ci`. A rejected version's section stays and needs a line saying it was never published. |
| Q. `[Unreleased]` at the tag, renamed by the next PR, checked in CI | as named | **Deadlocks**: in `release.yml`'s `ci` CHANGELOG.md has only `[Unreleased]`, so `gate` fails; every PR is red between a tag and the rename PR. It would amend the owner's rule, if he agrees. |
| R. Generated notes only, CHANGELOG.md frozen | git-cliff renders the notes | Would change the owner's rule and the README's promise. |

Recommendation for that case: P. The pipeline: (1) a PR merges, CI runs with `commits` and rule P; (2) `tag.yml`
(`workflow_run` of CI on `main`, plus `workflow_dispatch`; one job with `contents: write` and `actions: write`; checks
the conclusion, checks out the commit with `fetch-depth: 0` and `persist-credentials: false`; runs `uv run --frozen
--only-group release git-cliff --bumped-version`, where `--only-group` means "The project and its dependencies will be
omitted", FETCHED, docs.astral.sh/uv/reference/cli/, so no build backend runs in a job holding write tokens; stops when
the output equals the latest tag, since with nothing to release git-cliff prints a warning and the **last** tag and
exits 0, RUN; stops while that tag's release is a draft; creates the annotated tag with `gh api` `POST .../git/tags`
then `POST .../git/refs`, both "Contents" write, FETCHED, permissions page, so no git credential is stored; dispatches
`release.yml` on the tag); (3) `release.yml` on the tag: `check-tag`, `ci` (hatch-vcs build), `draft-release`,
`publish` (`pypi`), `publish-release`. Files beyond the owner-started list: `tag.yml`, `release.yml` triggers and
conditions, `ci.yml` (`fetch-depth: 0` for `dist`, the version argument, the `__version__` assertion dropped),
`pyproject.toml` (hatch-vcs, `cache-keys`), `__init__.py`, `check_dist.py` and its test. That is more than the two-day
ceiling under which #45 was split (INFERRED; READ, #45 comment of 2026-11-22); the order would be (ii) the convention
and its checks; (i) the version from the tag, shippable on the hand path only if the same part removes the rehearsal's
`sed` and replaces `check_dist.py tag`; (iii) `tag.yml`, the dispatch, rule P and the rehearsal, which need (i), (ii)
and the owner's first sentence of the new PRD requirement.

## 9. Decided (owner, 2026-11-24, following the advisor)

1. **The owner starts releases, through a release PR** (section 7). This amends the model of 2026-11-22 (#45) and
   departs from SvelteDT's PRD-S7. Reason: a merge-started release leaves an unremovable tag that can only be approved
   or burnt, as 0.1.0rc1 showed; an approval would be pending almost always; adopters get fewer, coherent releases.
2. **Before 1.0, `feat` and `fix` give a patch and a breaking change gives a minor** (`features_always_bump_minor =
   false`, `breaking_always_bump_major = false`; from 1.0 the same file gives feat minor, fix patch, breaking major,
   RUN, section 1). **The next release is 0.1.1.** Reason: a 0.x patch never breaks, so `~=0.1.0` takes features and
   fixes, and a minor means "read the changelog". The README's policy sentence (before 1.0 a patch release may add
   features and never breaks, a minor release may break) is the owner's; it is written in #76 part (ii) and he
   approves it there.
3. **Conventional commits are adopted**: every non-merge commit, header at most 100 characters (section 4). It
   changes every commit from now on. Reason: git-cliff can only compute or check a version from typed commits; it is
   SvelteDT's rule; no merge setting changes.
4. **"Done as recommended" stands**: git-cliff from a uv `release` group with the catch-all skip,
   `protect_breaking_commits`, the final-only `tag_pattern` and the bump test (section 1); the `pygrep` commit-msg
   hook after the private-word hook plus the CI `commits` job, which also refuses `fixup!` commits (section 4); the
   tag ruleset unchanged (section 6); an unpublished tag handled as measured in section 7. Dispatch option A
   (section 2) applies only if a tagging workflow is ever built.
5. **Step 0: release 0.1.1 now by today's hand path** (section 8). The tag and the PyPI approval need the owner's
   explicit OK at that time.
6. **Security: who can approve a publish.** Facts: the `pypi` environment has a branch policy and one required
   reviewer, `hniedner`, with `prevent_self_review` false (RUN, `gh api
   repos/hniedner/datatables-backend/environments/pypi`). The `gh` login on this machine is the account `hniedner`,
   with the token scopes `gist`, `read:org`, `repo`, `workflow` (RUN, `gh auth status`; no token printed). "Required
   reviewers with read access to the repository contents and deployments can use this endpoint", `POST
   /repos/{owner}/{repo}/actions/runs/{run_id}/pending_deployments` with `state: approved`, and classic tokens "need
   the repo scope" (FETCHED, docs.github.com/en/rest/actions/workflow-runs, "Review pending deployments for a workflow
   run"). So an agent session using the owner's `gh` login could approve a PyPI publish as the owner (INFERRED; the
   endpoint was not called).

   The fine-grained token page lists (FETCHED,
   docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens):
   `POST /repos/{owner}/{repo}/actions/runs/{run_id}/pending_deployments`, write, under "Deployments" only (the
   "Actions" section lists only its GET); `PUT /repos/{owner}/{repo}/environments/{environment_name}`, `POST
   /repos/{owner}/{repo}/rulesets` and `PUT /repos/{owner}/{repo}/rulesets/{ruleset_id}`, write, under
   "Administration". All three match what was expected.

   Decided:
   - **(a) Now:** the owner adds a deny rule in his Claude Code settings for the `pending_deployments` and run-approval
     API calls. Its exact form is his to check against the settings documentation. It is only as strong as the
     harness, not a GitHub control.
   - **Then:** the sessions move from the owner's classic `repo` token to a fine-grained token without the
     "Deployments" and "Administration" permissions, so the token itself cannot approve a deployment or change an
     environment or a ruleset. The owner approves in the browser. Before the switch, the list below is checked.
   - Both are the owner's, by hand. No session creates, stores or switches a token, or tests the approval call.
   - **(b)** `check-tag` refuses a version with no `## [X] - <date>` section in CHANGELOG.md on `main` (it runs before
     `publish` through `needs`; `publish` runs no repository code, READ, `release.yml:105`). It is built as part of
     `check-tag`, but it is not counted as a defence against this risk: a session that can approve a deployment can
     also merge a CHANGELOG section.
   - A GitHub-side control (a second required reviewer, or `prevent_self_review`) would lock the owner out of his own
     releases on a one-owner repository (INFERRED); revisit when a second maintainer joins.
7. **#76 is part (ii) of section 8** (the convention, the hook, the `commits` job, `cliff.toml` and its test, the
   README sentence of item 2) **plus `check-tag`** (the git-cliff version comparison, the CHANGELOG-section check of
   item 6 (b), and the notes with `--ignore-tags` over the range from the last published tag, as measured in section
   7) **and the release-PR version check.** The (i)/(iii) split, `tag.yml`, hatch-vcs and option P are not built.

## To verify before the token switch (the owner's)

**To verify: not tested.** The calls the sessions use routinely, with the fine-grained permission each needs. Every
row is from the fine-grained token page named in item 6 (FETCHED 2026-11-24); no API was called to test them. `gh`
subcommands may use GraphQL instead of these REST routes (NOT ESTABLISHED per command), so each is checked once after
the switch.

| Use (`gh` command or `gh api` call) | REST route on the page | Permission |
|---|---|---|
| Merge a PR (`gh pr merge`) | `PUT /repos/{owner}/{repo}/pulls/{pull_number}/merge` | Contents: write |
| View a PR (`gh pr view`) | `GET /repos/{owner}/{repo}/pulls/{pull_number}` | Contents: read, or Pull requests: read |
| List and view runs (`gh run list`, `gh run view`, `gh run watch`) | `GET .../actions/runs`, `GET .../actions/runs/{run_id}` | Actions: read |
| Run logs (`gh run view --log`, `gh api .../logs`) | `GET .../actions/runs/{run_id}/logs`, `GET .../actions/jobs/{job_id}/logs` | Actions: read |
| Start a workflow (`gh workflow run`, the rehearsal) | `POST .../actions/workflows/{workflow_id}/dispatches` | Actions: write |
| Re-run a run (`gh run rerun`) | `POST .../actions/runs/{run_id}/rerun` | Actions: write |
| Read an issue (`gh issue view`) | `GET .../issues/{issue_number}` | Issues: read |
| Create an issue (`gh issue create`) | `POST .../issues` | Issues: write |
| Edit or close an issue (`gh issue edit`, `gh issue close`) | `PATCH .../issues/{issue_number}` | Issues: write, or Pull requests: write |
| Comment (`gh issue comment`, `gh pr comment`) | `POST .../issues/{issue_number}/comments` | Issues: write, or Pull requests: write |
| Code-scanning alerts: read, dismiss | `GET .../code-scanning/alerts`, `PATCH .../code-scanning/alerts/{alert_number}` | Code scanning alerts: read, write |
| Dependabot alerts: read, dismiss | `GET .../dependabot/alerts`, `PATCH .../dependabot/alerts/{alert_number}` | Dependabot alerts: read, write |
| Secret-scanning alerts: read, dismiss | `GET .../secret-scanning/alerts`, `PATCH .../secret-scanning/alerts/{alert_number}` | Secret scanning alerts: read, write |
| Environment read (`gh api .../environments/pypi`, its branch policies) | `GET .../environments/{environment_name}`, `GET .../environments/{environment_name}/deployment-branch-policies` | Actions: read |
| Releases: view, create, edit (`gh release view/create/edit`) | `GET .../releases/tags/{tag}`, `POST .../releases`, `PATCH .../releases/{release_id}` | Contents: read / write (create and edit also listed under Workflows: write) |
| Rulesets: read | `GET .../rulesets`, `GET .../rulesets/{ruleset_id}` | Metadata: read |
| Repository settings: read | `GET /repos/{owner}/{repo}` | Metadata: read |
| Branch protection: read | `GET .../branches/{branch}/protection` | **Administration: read**. Leaving out Administration loses this read; the repository uses rulesets and has no classic protection on `main` (RUN, 404 "Branch not protected"), so the ruleset reads above cover it. |

Every row asked for was found on the page; none is marked "not found".

## Not established (to verify before relying on it)

- The exact form of a Claude Code deny rule that catches the `pending_deployments` and run-approval calls (item 6).
- Which REST or GraphQL calls each `gh` subcommand makes, and so whether the table above is complete for it.
- That a job past the 30-day approval limit fails and leaves a draft release as described (section 7).
- Automatic trigger only, not built: whether the GitHub Actions App (`GITHUB_TOKEN`) can be a ruleset bypass actor;
  whether a `workflow_call` job asking for more permissions than the caller grants fails or runs; the hatch-vcs version
  of a shallow non-tag checkout; whether Dependabot moves `build-constraint-dependencies`; whether `gh release view`
  without a tag skips a draft.
- git-cliff reading only merge commits (option b of section 4); not needed by the decision.
- Scorecard's `checks.md` was fetched from its raw file; its rendered page answered 503.
