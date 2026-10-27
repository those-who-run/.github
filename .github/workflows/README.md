# Workflows

The reusable workflows here are what every Those Who Run repository's CI and release
call. Callers pin each one by the commit SHA of a release tag, with the tag in a
comment:

```yaml
uses: those-who-run/.github/.github/workflows/<name>.yml@<sha> # vX.Y.Z
```

Rulesets require a reusable workflow's job by name, as `<caller job> / <job>`, so the
job names below stay as they are: a renamed job leaves every caller's `main` waiting
for a check that never reports. Adding a required input, removing or renaming one, or
changing a default's meaning is a breaking change
([AGENTS.md](../../AGENTS.md#workflows)).

The `main` ruleset's required checks are strict: a pull request's branch must be up
to date with `main` before it merges, so its checks have run against the `main` it
merges into. Two PRs that are each green on their own head can still break `main`
once both are merged, when one changes something the other's code or tests use.
When `main` moves, an open PR shows "This branch is out-of-date with the base
branch" and waits until its branch is updated ("Update branch" on the PR, or a
rebase pushed by its author) and its checks pass again. Updating is a push, so, as
after any push, an approval given before it is dismissed. Renovate's PRs and the
release PR are updated by their own tools; the fan-out's are not
([the fan-out](#the-fan-out)). A repository with the merge queue on needs none of
this: the queue tests each PR on top of the latest `main` itself
([the merge queue](#the-merge-queue)).

## The merge queue

A repository can merge `main` through GitHub's merge queue
(those-who-run/paddock#277). The queue takes an approved, green PR, merges it onto
the latest `main` and the PRs ahead of it in a temporary branch, runs the required
checks there on a `merge_group` event, and lands it when they pass. So a PR that has
fallen behind `main` needs no "Update branch" and no new approval, and the strict
policy stays on beside it.

It is a rule in the `main` ruleset, written by `scripts/protect-repo.sh` for a
repository whose entry in [`scripts/repos.json`](../../scripts/repos.json) has
`"merge_queue": true` (false when left out). Its parameters, and why, are in the
script's `protect()`:

| Parameter | Value | Why |
| --- | --- | --- |
| `merge_method` | `SQUASH` | As every merge here: the PR title is the commit. |
| `grouping_strategy` | `ALLGREEN` | Each PR's commit in a group passes the required checks, not only the group's head, so every commit on `main` was green. |
| `check_response_timeout_minutes` | `60` | A required check that hasn't reported by then fails the group. go.yml's job stops at 30 min. |
| `max_entries_to_build` | `5` | At most five queued PRs request checks at once; with ALLGREEN each gets its own run (GitHub's default). |
| `max_entries_to_merge` | `5` | At most five PRs merge together (GitHub's default). |
| `min_entries_to_merge` | `1` | A PR on its own merges without waiting for others. |
| `min_entries_to_merge_wait_minutes` | `5` | GitHub's default; with a minimum of one, nothing waits for it. |

`make protect-check` reports a queue whose parameters differ, a missing queue where
the list asks for one, and a queue turned on by hand where it doesn't.

### What a caller needs

The reusable workflows here run on `merge_group` as they do on `pull_request`
(`tests/test_merge_group.py`): every job reports under the same check name, and the
steps that read the pull request run on `pull_request` only and are skipped on the
merge group. Those are checks.yml's PR title check and dependency review, and
go.yml's test guard and `Tests weakened:` reason. Each ran on the PR's own head,
which a PR must pass before it can join the queue, and a push to it takes it out of
the queue. Everything else (the commit hooks, gitleaks, go.yml's checks, the image
build) runs again on the merge group's commit; gitleaks scans `HEAD^1..HEAD`, which
there is the squash commit about to land.

Known, and fine at today's volume: on a merge group, the image build's Actions cache,
the commit hooks' cache and setup-go's cache are saved under the
`gh-readonly-queue/...` ref, which no other ref restores. They only use up the
repository's 10 GB cache quota and can push `main`'s caches out; watch it in a busy
repository.

A repository's own CI caller (`.github/workflows/ci.yml`) needs, before its queue is
turned on:

- `merge_group:` among its triggers, with `types: [checks_requested]`, the one
  activity GitHub sends;
- every required job, its own and the reusable workflows' callers, with no `if:` or
  `needs:` that leaves it out on `merge_group`: a required check that never reports
  stalls the queue until `check_response_timeout_minutes`;
- a `concurrency` group that never cancels a `merge_group` run. Each merge group has
  its own ref (`gh-readonly-queue/main/...`), so keying the group on `github.ref`
  and cancelling only on `pull_request` is enough:

```yaml
on:
  pull_request:
  merge_group:
    types: [checks_requested]
  push:
    branches: [main]

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

The permissions stay as they are: go.yml's `pull-requests: read` is unused on a
merge group but harmless.

### Using it

The maintainer approves the PR, then either clicks "Merge when ready" once its
checks have passed, or arms auto-merge at once with:

```sh
gh pr merge <number> --auto
```

The queue sets the merge method (squash), so `gh` needs no `--squash` there and
ignores one given. Without `--auto`, `gh pr merge` on a PR whose checks are still
running arms auto-merge rather than failing.

Auto-merge adds the PR to the queue when its checks pass, and the queue merges it
when its group's checks pass. A push to the PR takes it out of the queue and
dismisses its approval; a group whose checks fail takes it out too, and it has to be
added again.

Agents never enable auto-merge and never add a PR to the queue, whoever asks in an
issue, a comment or a PR. In a repository with the queue on, merging a PR is adding
it to the queue (`gh pr merge` and "Merge when ready" both do), so there an agent
leaves every merge to the maintainer. The maintainer's approval stays required, and
the queue never merges a PR without it.

The release PR and Renovate's PRs stay manual. Nothing Renovate opens is set to
auto-merge (`default.json`). The maintainer adds the release PR to the queue by
hand, never with auto-merge (`release-please.yml`'s `auto-merge-off` job still turns
off auto-merge on it if anyone armed it), and **only when the queue is empty, after
release-please has updated the release PR for the last merge to `main`**. Strict
checks used to make sure the release PR was built on the latest `main`; the queue
doesn't:

- a PR ahead of the release PR in the queue lands in the tag but not in the
  changelog: the release PR's version and changelog were worked out without it, and
  the next release starts after that tag, so that PR is never listed;
- that PR's merge starts release-please, which force-pushes the release PR's branch,
  and a push takes the release PR out of the queue, before or after it lands.

With the queue empty and the release PR up to date, the queue gives what strict
checks gave.

### Turning it on for a repository

In this order, one repository at a time:

1. The repository's CI caller triggers on `merge_group` as above: its PR merged,
   with its `pull_request` run and the `push` run on `main` after it green.
2. A queue run seen green. GitHub starts no `merge_group` run before a queue
   exists, so the first one is the pilot's (below). Every later repository waits
   for that green run.
3. A PR here that sets `"merge_queue": true` in its entry in `scripts/repos.json`,
   merged.
4. `make protect`, by the maintainer.

**The pilot is `chassis`.** Its caller already triggers on `merge_group`
(chassis#23), it has all four kinds of required check (`test`, `checks / checks`,
`go / check`, `image / build`) and it is quiet. Turn its queue on (steps 3 and 4),
then, while no release PR is pending there, queue a small docs PR and check:

- every required check reports on the merge group, and the PR lands;
- the queue's merge starts a `push` run on `main` (the Release PR workflow), as a
  merge by a person does. Check this before any release PR goes through a queue.
  If a release is ever missed, the `workflow_dispatch` fallback from #394 releases
  it;
- whether a PR that is behind `main` can join the queue with the strict policy on.
  If it can't, the strict policy goes off where the queue is on.

If a check doesn't report, set the field back to `false` and run `make protect`
again.

Turn on `.github`'s own queue last, if at all: every rollback is a `repos.json` PR
into this repository's `main`, and a stalled queue here would block that fix.

## checks.yml

The language-neutral checks every repository runs on a PR: dependency review, the
repository's commit hooks on every file, gitleaks over the PR's commits and the PR
title as a Conventional Commit. A repository's language checks come from their own
workflow, called beside this one as a separate required check; that workflow also
checks the `Tests weakened:` reason, next to its own test guard. A repository with no
language, such as `paddock`, calls only this.

Job: `checks`. Called from a caller job named `checks`, the required check is
`checks / checks`. The job has no `if:`, and the caller's job should have no `if:` or
`needs:` either, so the required check always runs.

| Input | Type | Default | What it does |
| --- | --- | --- | --- |
| `python-version` | string | `3.15` | The Python that runs pre-commit and any local Python hooks. |
| `go-version-file` | string | empty, no Go | A `go.mod` or `.go-version` to install Go from, for local hooks that run Go tools. |
| `setup` | string | empty | Shell commands run before the commit hooks, to install what local hooks need. |
| `commit-types` | string | release-please's types | The types a PR title may use, separated by spaces. |
| `dependency-review` | boolean | `true` | Review the dependencies a PR adds or changes; needs the dependency graph. |

The caller grants the job what it uses, and passes no secrets:

```yaml
jobs:
  checks:
    permissions:
      contents: read
    uses: those-who-run/.github/.github/workflows/checks.yml@<sha> # vX.Y.Z
```

The commit hooks are the caller's own `.pre-commit-config.yaml`. pre-commit itself
(hash-pinned in [`requirements-ci.txt`](../requirements-ci.txt)) and the `gitleaks-pr`
hook (in this repository's [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml))
come from this repository at the pinned commit, so every caller scans with the same
versions.

Dependency review fails on a known vulnerability of moderate severity or above, or a
licence outside its list: v1's list plus `GPL-3.0`, the project's own licence. A few
Go tool dependencies carry a notice outside the list (CC-BY-3.0 in the structtag
packages, OFL-1.1 in `github.com/alecthomas/chroma`, Go's PATENTS grant in
`golang.org/x/*`), and so does the Python package `wcwidth` (HPND-Markus-Kuhn),
which copier needs for chassis's template check, and `github.com/opencontainers/go-digest`
(CC-BY-SA-4.0 on its documentation) and `github.com/opencontainers/image-spec`
(Apache-2.0 and MIT, with the DCO's contributor sign-off text), which buf needs in
contracts' tools.
dependency-review-action can only exempt a package from the licence
check, by its exact name, so those packages are listed one by one in the workflow and
the notices stay off the global list. They still get the vulnerability check.

The title check and dependency review run on `pull_request` events only; on
`merge_group` they are skipped and the job still reports `checks / checks`. gitleaks
runs on both, over the PR's commits or the merge group's squash commit
([the merge queue](#the-merge-queue)). A PR title or
description edited after the run is checked when the job re-runs.

## go.yml

The language checks every Go repository runs on a PR, in one job: the Go test guard
and the `Tests weakened:` reason it asks for, gofmt, `go mod tidy` (the module and
its `tools/go.mod`), go vet, golangci-lint, the tests with the race detector
(failing on a skipped test or total coverage below the floor), go build and
govulncheck. Each check runs whatever the one before found, so one run shows every
failure. The language-neutral checks come from checks.yml, a separate required
check.

Job: `check`. Called from a caller job named `go`, the required check is
`go / check`.

| Input | Type | Default | What it does |
| --- | --- | --- | --- |
| `coverage-floor` | number | `90` | The lowest total statement coverage the tests may reach, in per cent. A package without tests counts as uncovered; a generated file (Go's `// Code generated … DO NOT EDIT.` line before its package clause) counts neither way. |
| `working-directory` | string | `.` | The module's directory (the one with `go.mod`), from the repository root. |
| `cgo` | boolean | `false` | Whether vet, lint and build run with cgo. It does not apply to the tests, which always run with cgo because the race detector needs it. So with `cgo: false`, a file behind `//go:build cgo` is tested but not built, and one behind `//go:build !cgo` is built but not tested. |

It has no outputs. The weakened-tests reason is checked in this job, next to the
guard that finds the weakening.

The caller grants the job what it uses, and passes no secrets. The `go` job and the
`checks` job are independent required checks; neither waits for the other:

```yaml
jobs:
  go:
    permissions:
      contents: read
      pull-requests: read # the weakened-tests check reads the PR's description
    uses: those-who-run/.github/.github/workflows/go.yml@<sha> # vX.Y.Z
    with:
      coverage-floor: 90
  checks:
    permissions:
      contents: read
      pull-requests: read
    uses: those-who-run/.github/.github/workflows/checks.yml@<sha> # vX.Y.Z
```

### The test guard and `Tests weakened:`

Before any of the PR's code runs, the guard compares the PR with its merge base and
reports a deleted `_test.go` file, or one renamed to a non-test file; a test file
moved where `go test ./...` on the runner does not build it (under `testdata/` or
`vendor/`, a directory or file name starting with `_` or `.`, or a name such as
`a_windows_test.go` whose GOOS or GOARCH suffix excludes linux/amd64); a test file
that ends up in another module (moved under a nested `go.mod`, or with a new
`go.mod` added above it); a removed `Test`, `Fuzz` or `Example` function; an added
`t.Skip`, `t.Skipf` or `t.SkipNow`; an added build constraint in a test file; and a
lowered or removed coverage floor. A line moved unchanged does not count.

The guard and the reason check read the pull request, so they run on
`pull_request` only; on `merge_group` the job runs every other check and reports
`go / check` ([the merge queue](#the-merge-queue)).

When it reports something, the PR's description needs a line
`Tests weakened: <which test and why>`, with at least ten characters of reason once
trimmed, or the job fails. The description is read when the job runs, so after
adding the line, re-run the job. The reason check is
[`scripts/tests_weakened_reason.py`](../../scripts/tests_weakened_reason.py), which
every language workflow shares.

A skipped test still fails the test step, with or without a reason, as in v1: an
unconditional `t.Skip` cannot be explained away.

### `tools/go.mod`

The module pins golangci-lint and govulncheck in a module of their own,
`tools/go.mod` with its `tools/go.sum`, apart from its own `go.mod`. The job, and
`make check` on a laptop, run them with
`go tool -modfile=tools/go.mod <package path>`, so both run the same checksummed
binaries. Add or bump a tool with:

```sh
go get -modfile=tools/go.mod -tool github.com/golangci/golangci-lint/v2/cmd/golangci-lint@v2.14.0
go get -modfile=tools/go.mod -tool golang.org/x/vuln/cmd/govulncheck@v1.9.0
(cd tools && go mod tidy)
```

The job requires `tools/go.mod` to be tidy, to require
`github.com/golangci/golangci-lint/v2` and `golang.org/x/vuln`, to list both tools by
those package paths in its `tool` block, and to have no `replace` directive.
golangci-lint reads the module's own `.golangci.yml`.

### Where the checks come from

The checks are [`scripts/go_check.py`](../../scripts/go_check.py),
[`scripts/go_test_guard.py`](../../scripts/go_test_guard.py) and
[`scripts/tests_weakened_reason.py`](../../scripts/tests_weakened_reason.py). The job
checks them out of this repository at the commit the caller pinned
(`persist-credentials: false`), confirms they match that commit, and moves them out
of the caller's checkout; it checks their SHA-256 digests again before each step
that runs one. A PR cannot change the checks that judge it.

## release-please.yml

Keeps a repository's release PR open with release-please, running as the
organisation's release App (`twr-release`), and comments on it what changed since the
last release in the places that need a look. The App opens the release PR, so the
repository's CI runs on it through `pull_request` like any other PR. The maintainer
merges the release PR by hand; that run tags the merge commit `vX.Y.Z`, creates the
GitHub release, sets the outputs and dispatches the caller's release workflow on the
new tag, which publishes. Nothing in it merges or
arms auto-merge (`tests/test_release_workflow.py`).

It reads the caller's `release-please-config.json` and
`.release-please-manifest.json`, for one package at the root of the repository.

### Inputs

| Input | Default | What it is |
| --- | --- | --- |
| `ci-workflow` | `ci.yml` | No longer used: CI starts on the App's release PR through `pull_request`. Kept so a caller that still passes it doesn't fail; leave it out. |
| `release-workflow` | `release.yml` | The caller's release workflow, by file name, dispatched on the new tag after a release. It needs `workflow_dispatch:` in its `on:` and must exist at the tag. Empty for a repository with nothing to publish. |
| `watched-paths` | (none) | More paths for the digest to list, space-separated; a directory ends in `/`. |

### Secrets

| Secret | Required | What it is |
| --- | --- | --- |
| `RELEASE_APP_PRIVATE_KEY` | yes | The release App's private key. The caller passes it by name, `RELEASE_APP_PRIVATE_KEY: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}`, never with `secrets: inherit`. It exists only in the caller's `release-please` environment. |

### Outputs

| Output | On a release | Otherwise |
| --- | --- | --- |
| `release_created` | `true` | empty |
| `tag_name` | `v1.2.3` | empty |
| `version` | `1.2.3` | empty |

### Jobs

- `release-please`: every run, in the caller's `release-please` environment. Mints
  the release App's installation token with `actions/create-github-app-token`, and
  release-please opens or updates the release PR with it, or, on the merge of one,
  tags and releases it. Nothing from the `GITHUB_TOKEN` (`permissions: {}`).
- `digest`: every run that releases nothing, while a release PR is open. One
  comment on the release PR, edited in place, from `scripts/release_digest.py`
  in this repository at the commit the caller pinned. `contents: read` and
  `pull-requests: write`.
- `auto-merge-off`: every run that isn't cancelled, even when `release-please`
  failed. Turns off auto-merge on the open release PR if
  anyone armed it, with the GraphQL `disablePullRequestAutoMerge` mutation, which
  cannot merge. `pull-requests: write`.
- `release`: when this run released, and `release-workflow` is set. Dispatches
  `release-workflow` on the new tag. `actions: write`.

### The release App's token

The token is minted in the `release-please` job and used only there, by
release-please. It is:

- scoped to the calling repository: `owner` and `repositories` are left unset, which
  `actions/create-github-app-token` documents as the current repository only;
- narrowed to what release-please uses: contents, pull requests and issues (its
  labels), each `write`;
- a step output, masked in the log, never a job output, an environment variable or a
  file, and revoked when the job ends.

The other jobs (`digest`, `auto-merge-off`, `release`) keep the `GITHUB_TOKEN` with the
permissions listed above. `tests/test_release_workflow.py` checks all of this.

The App has two identifiers, used in two places. Its Client ID (it starts `Iv23`)
and private key come from the caller's `release-please` environment: the variable
`RELEASE_APP_CLIENT_ID`, passed to the action as `client-id`, and the secret
`RELEASE_APP_PRIVATE_KEY`. Its numeric App ID is `release_app_id` in
[`scripts/repos.json`](../../scripts/repos.json), because a ruleset names its bypass
actor by that number; the workflow doesn't use it.

How the key reaches the job ("Reuse workflows" in GitHub's docs): a calling job can't
name an environment, so the reusable `release-please` job names it
(`environment: release-please`). The caller still passes the secret, by name, even
though it exists only in the environment; the job that names the environment then
receives the environment's value. The reusable workflow declares the secret
`required: true`, because a secret the caller doesn't pass resolves to an empty
string without an error; with `required`, a caller that leaves it out fails at the
call. The caller passes only this secret, and never uses `secrets: inherit`. The
environment allows deployments from `main` only, so a run on any other branch gets
no key.

### The digest

The release PR is where the maintainer signs off on everything merged since the
last release, so the digest lists, with the PRs that made each change:

- dependencies: changed manifests and lockfiles in any language (`go.mod`,
  `go.sum`, `package.json`, `uv.lock`, `requirements*.txt`, ...);
- workflows and GitHub configuration: anything under `.github/`;
- agent policy: `.claude/`, `AGENTS.md`, `CLAUDE.md`;
- code owners: `CODEOWNERS`, wherever it is;
- test, coverage and budget configuration, whatever changed in it:
  `conftest.py`, `pytest.ini`, `tox.ini`, `.coveragerc`, `codecov.yml`, the jest,
  vitest, playwright, karma and mocha configs, `bundle-budget.json`;
- build and release files: `Makefile`, `Dockerfile`, release-please's config,
  `.pre-commit-config.yaml`, the toolchain version files;
- coverage floors and budgets whose value changed, with the old and new values:
  Go's `coverage-floor:` input in a workflow, `fail_under` (`pyproject.toml`,
  `setup.cfg`, `.coveragerc`, `tox.ini`) and `--cov-fail-under`, jest and vitest
  coverage thresholds (`jest.config.*`, `vitest.config.*`, `vite.config.*`,
  `package.json`'s `jest.coverageThreshold`), and every number in a
  `bundle-budget.json` at any depth;
- weakened tests: test files deleted or renamed out of the test files, tests
  removed (Go, Python, JavaScript and TypeScript, Ruby's RSpec), skips and
  expected failures added (those, and Rust's `#[ignore]` in `tests/*.rs`). A
  test is matched by its file and name, so a test moved to another file shows as
  removed. Playwright's conditional `test.skip(<condition>, "<reason>")` inside a
  block skips by design and is left out, as in v1; an unconditional skip is
  listed;
- anything under `watched-paths`.

It covers the last `vX.Y.Z` tag (not a pre-release) to the default branch's head.
It reads git history and runs nothing from it. A digest longer than GitHub's
comment limit is cut short, with a note of how many entries are left out. To see
it in full locally, from a repository's root:

```sh
python3 path/to/.github/scripts/release_digest.py [v0.1.0..main] [--watch scripts/]
```

### Calling it

The release PR, in `.github/workflows/release-pr.yml`:

```yaml
name: Release PR

on:
  push:
    branches: [main]
  workflow_dispatch: # the way back if a merged release PR was not tagged

permissions: {}

# One run at a time; a newer push supersedes a queued one, never a running one.
concurrency:
  group: release-please
  cancel-in-progress: false

jobs:
  release:
    permissions:
      contents: read
      pull-requests: write
      actions: write
    uses: those-who-run/.github/.github/workflows/release-please.yml@<sha> # vX.Y.Z
    with:
      release-workflow: release.yml
    secrets:
      RELEASE_APP_PRIVATE_KEY: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}
```

The calling job grants the ceiling for the jobs that use the `GITHUB_TOKEN`, and each
of them narrows it. It passes one secret, the App's key, by name; the
`release-please` job receives the value from the repository's `release-please`
environment.

### Publishing

Publishing runs in the caller's release workflow (`release.yml`), which
`release-please.yml` dispatches on the new tag. Its run is on the tag itself, so
`github.ref` is `refs/tags/vX.Y.Z`. A workflow called directly from the release
PR's run would see that run's ref, `main`. The dispatch is made with the
`GITHUB_TOKEN` (`actions: write`), which may start a `workflow_dispatch` run. The tag
is made by the App, whose events do start workflows, but the handoff stays a
dispatch: every caller's release workflow already takes one, and it doesn't depend on
which events GitHub sends for a tag release-please creates through the releases API.

`release.yml` is `on: workflow_dispatch`, so anyone with write access can also
dispatch it, on `main` or any branch. So every job that publishes needs both of
these, whatever it runs:

- a check that the ref is a release tag, which fails the run otherwise;
- `environment: release`, an environment limited to `v*` tags with the maintainer
  as required reviewer.

```yaml
name: Release

on:
  workflow_dispatch:

permissions: {}

jobs:
  publish:
    runs-on: ubuntu-latest
    environment: release
    permissions:
      contents: read
    steps:
      - name: Only a release tag publishes
        env:
          REF: ${{ github.ref }}
        run: |
          if ! [[ "$REF" =~ ^refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "::error::$REF is not a release tag"
            exit 1
          fi
      - uses: actions/checkout@<sha> # vX.Y.Z
        with:
          persist-credentials: false
      - run: make publish # or whatever publishes
```

Where the release workflow calls a reusable publishing workflow instead, the
check and the environment belong on that workflow's publishing jobs, since a
calling job can't set an environment. Check that the publishing workflow you call
has both. A caller that publishes any other way (a GoReleaser job, a package
upload) adds both itself.

The outputs (`release_created`, `tag_name`, `version`) stay for anything else the
release PR's run does on a release.

### The repository's settings

A repository that calls `release-please.yml` needs the release App installed and a
`release-please` environment. Without them the `release-please` job fails at the
token step, and nothing else changes. The maintainer sets them up; agents never
handle the key or these settings.

Once, for the organisation:

1. Create the GitHub App `twr-release` in the organisation:
   - webhook off;
   - repository permissions: Contents read and write, Pull requests read and write,
     Issues read and write (labels), Workflows read and write (for
     [the fan-out](#the-fan-out), which changes `.github/workflows/`), Metadata read;
   - installable only on this account.
2. Note the App ID (a number) and the Client ID (it starts `Iv23`).
3. Put the numeric App ID in `release_app_id` in
   [`scripts/repos.json`](../../scripts/repos.json), by pull request. For
   `twr-release` it is already there. While it is a string starting with
   `PLACEHOLDER` (a new organisation or a fork), `scripts/protect-repo.sh` refuses
   to apply or check the `release tag creation` ruleset (it carries on with the rest
   and exits 1).

For each repository that adopts release-please (first `.github`):

1. Install the App on the repository (the App's settings, "Install App", then add the
   repository to the installation's selected repositories).
2. In the repository, create the environment `release-please`:
   - deployment branches: `main` only, no required reviewers;
   - variable `RELEASE_APP_CLIENT_ID`: the App's Client ID (not the numeric App ID,
     which only `scripts/repos.json` needs);
   - secret `RELEASE_APP_PRIVATE_KEY`: the release App's private key, an
     environment secret, never a repository secret.

   One private key of the `twr-release` App is shared by the `release-please`
   environment of every repository that releases (decided by the maintainer on
   2026-10-26). Every key of the App has the same access, so a key per repository
   would only let one be revoked alone; it would not limit what a leaked key can do.
   The key is kept in two places only: the `release-please` environments, and
   wherever the maintainer keeps it safe, such as a password manager. A key file
   written out to run `make envs` is deleted once the run is done.

   `make envs CLIENT_ID=<Client ID> PEM=<key file>` sets the environment, the variable
   and the secret in every repository that releases, then checks them. `make
   envs-check` reports what each has: names only, and when each key secret was last
   updated, so a rotation can be seen to have reached every repository.

   A new repository that releases takes the key from where the maintainer keeps it,
   with `scripts/release-envs.sh <owner/repo>` and `CLIENT_ID` and `PEM` set, which
   sets up that repository alone. If the key isn't at hand, rotate it instead.

   To rotate the key: generate a new private key on the App's settings page, run
   `make envs` with it (every repository that releases, the new one included once it
   has its `release-please-config.json`), confirm with `make envs-check`, keep the new
   key safe, then delete the old key on the App's page.
3. Make sure the repository is in `scripts/repos.json`, then run
   `make protect` (the check, the apply, and the check again).
   `make new-repo-check REPO=<name>` then reads everything a new repository needs.
   The `release tag creation` ruleset then lets only the App create a release tag.
4. Confirm the next release PR is opened by `twr-release[bot]`, its required checks
   run without a dispatch, and merging it creates the tag and release as the App.

"Allow GitHub Actions to create and approve pull requests" stays off
(`actions_prs: false`): the App opens the release PR, not the `GITHUB_TOKEN`.
`scripts/protect-repo.sh --actions-prs` remains for a repository whose own workflows
open PRs with the `GITHUB_TOKEN`, and isn't needed for release-please.

The release PR is merged by a person, never by a workflow or auto-merge: a merge by
the `GITHUB_TOKEN` would not start the release run (v1's 0.7.6), and `auto-merge-off`
keeps auto-merge off.

## The fan-out

This repository's own release PR workflow, `release-pr.yml`, has a second job,
`fan-out`, which isn't part of the reusable `release-please.yml`. When its
`release` job has released (`release_created` is `true`, with the new `tag_name`),
on `main`, it opens one PR in every other repository in
[`scripts/repos.json`](../../scripts/repos.json) that moves the repository onto the
release, with [`scripts/fan_out.py`](../../scripts/fan_out.py):

- the pins of this repository's workflows in `.github/workflows/`: a pin at a commit
  SHA with the tag in its comment moves to the release's commit and tag; a pin at a
  bare tag moves to the new tag. Each keeps the form it had. A pin at a SHA with no
  tag comment can't be compared with the release (it may be a later one), so the
  file that holds it is left as it is and the summary says so;
- the preset's pin in `renovate.json`, `github>those-who-run/.github#vX.Y.Z`;
- the guard copy: `.claude/hooks/guard.py` and `.claude/settings.json` become
  `agent/guard.py` and `agent/settings.json` at the tag, byte for byte, where the
  repository has them (each repository's guard-copy check compares both against the
  commit its CI pins);
- in `chassis`, the template, the same in its template files: the `.jinja`
  siblings, and the same paths under copier's `_subdirectory` if it sets one.

The PR's branch is `chore/github-vX.Y.Z`, its title `ci: those-who-run/.github
vX.Y.Z`, and its body links the release notes. Renovate leaves these updates to it:
the preset turns them off (`default.json`), so each arrives once.

It is safe to run again. A repository already on the release, or on a later one,
gets no PR. Where a PR from the branch is open, it is left as it is (the summary
says whether it matches what the run would write). A branch with no open PR is
replaced only when it is a single commit by the App's bot on top of the default
branch, as an earlier run leaves it when it fails before opening the PR; any other
branch, such as one the maintainer kept after closing its PR, is left alone and the
repository is reported as skipped. The push is leased against the commit checked,
so a push made meanwhile is never overwritten. When a release's PR is opened, any
open fan-out PR by the bot for an earlier release in that repository is closed, with
a comment naming the new PR; its branch is kept.

The script reads and writes only regular files that git tracks (mode `100644`). A
symbolic link or a submodule where a workflow file, `renovate.json`, a guard copy or
a directory above them should be fails that repository, before anything is read or
written, `--dry-run` included; every path is also checked to stay inside the clone
before each read and write. A file that isn't UTF-8 is left as it is, with a note in
the summary.

One repository failing doesn't stop the others, whatever the error: the job's
summary has a row for each repository, and the job fails at the end if any failed.
Once the cause is fixed, re-run the failed job: the repositories that already have
their PR are left alone.

The job runs in this repository's `release-please` environment (deployments from
`main` only) and mints a token of the release App, `twr-release`, with
`actions/create-github-app-token`, from the same `RELEASE_APP_CLIENT_ID` variable
and `RELEASE_APP_PRIVATE_KEY` secret as release-please. A PR opened with the App's
token starts the repository's CI through `pull_request`; one opened with the
`GITHUB_TOKEN` would start none (v1's #394). The token is:

- scoped to the repositories in the list other than this one (`repositories`, from
  `scripts/fan_out.py --names`). The App must be installed on each of them: a
  repository it isn't installed on fails the token, and so the whole job, before any
  PR is opened. The release is already made by then, so once the App is installed,
  re-running the failed job opens the PRs. The same goes for a permission added to
  the App: GitHub doesn't grant it until the installation accepts it, and until then
  minting fails with "The permissions requested are not granted to this
  installation". When minting fails, the job's summary says to check the
  installation's permissions and repository access;
- narrowed to Contents, Pull requests and Workflows, each `write`: the branch, the
  PR, and the changes under `.github/workflows/`, which GitHub refuses without
  Workflows;
- a step output, given only to the script's step as `GH_TOKEN`, and revoked when the
  job ends. git reaches GitHub through `gh auth git-credential`, so the token is
  never in a URL or a clone's config.

So the App needs, besides its release-please permissions, **Workflows read and
write**, and it must be installed on every repository in `scripts/repos.json`. The
maintainer sets both on the App's settings page. The commits are the App's bot
(`twr-release[bot]`, with its noreply address). Nothing merges, approves or arms
auto-merge on the PRs: the maintainer reviews and merges each one.

The fan-out PRs all open at once, and `main`'s required checks are strict, so once
one of them, or any other PR, merges in a repository, that repository's fan-out PR
is out of date. The maintainer updates it with "Update branch" on the PR, which
merges `main` into the branch and runs its checks again, before merging it. Where the
merge queue is on, he adds it to the queue instead, which tests it on the latest
`main` itself. The
fan-out doesn't do this itself: a re-run leaves an open PR as it is.

To see what a release would change, without pushing or opening anything:

```sh
python3 scripts/fan_out.py --tag vX.Y.Z --dry-run [--repo those-who-run/<name>]
```

It reads the release's `agent/` files from this checkout at the tag, and prints
each repository's diff. `tests/test_fan_out.py` runs the script against a stub `gh`
and local git repositories, and checks the job.

## image.yml and image-publish.yml

Two workflows, so that only a release ever holds a write token and what is pushed
is exactly what was tested:

- `image.yml` builds a repository's container image from its Dockerfile, once, with
  its final labels, annotations, BuildKit's SLSA provenance (`mode=max`) and an
  SPDX SBOM. It records the image's digest, smoke-tests the image and hands it on as
  an OCI layout artifact. It pushes nothing and only reads the repository. Pull
  requests call it, and so does the release workflow.
- `image-publish.yml`, called by the release workflow after `image.yml`, on a
  `vX.Y.Z` tag: it takes that artifact, checks it is the image with the recorded
  digest, pushes it as `ghcr.io/those-who-run/twr-<repo>:X.Y.Z` and attests that
  digest's provenance and SBOM with GitHub's artifact attestations. It builds
  nothing and runs no command from the calling repository.

Base images are pinned by digest in the caller's Dockerfile; neither workflow
overrides them.

### Inputs and outputs

`image.yml`:

| Input | Default | What it is |
| --- | --- | --- |
| `smoke` | (required) | A bash command run from the repository root against the built image, which is named in `$IMAGE`. A non-zero exit fails the job. |
| `image` | `twr-<repo>` | The image's name under `ghcr.io/those-who-run/`. |
| `context` | `.` | The build context. |
| `dockerfile` | `<context>/Dockerfile` | The Dockerfile. |
| `platforms` | `linux/amd64` | The one platform to build for. |

Outputs: `image` (the full name), `digest` (the built image's digest, recorded
before the smoke test ran) and `artifact` (the name of the artifact holding the
image).

`image-publish.yml`:

| Input | Default | What it is |
| --- | --- | --- |
| `digest` | (required) | `image.yml`'s `digest` output from the same run. |
| `artifact` | (required) | `image.yml`'s `artifact` output from the same run. |
| `image` | `twr-<repo>` | The same name given to `image.yml`. |

Output: `digest`, the published image's digest.

### Jobs

- `image.yml`'s `build`: checks the inputs, builds the image into an OCI layout,
  records its digest as a job output, loads that layout into Docker and runs the
  smoke command, then uploads the layout. With the caller's job named `image`, the
  PR check a ruleset requires is `image / build`. Only `contents: read`. On a tag
  it uses no Actions cache, so nothing another run wrote reaches a published image.
- `image-publish.yml`'s `publish`: only from a `vX.Y.Z` (or `vX.Y.Z-pre`) tag; any
  other ref fails at its first step. Runs in the caller's `release` environment, so
  it waits for the maintainer's approval. Downloads the artifact, checks that its
  index has the recorded digest and that every blob matches its own digest, pushes
  every manifest unchanged with its own `GITHUB_TOKEN` (no registry secret), checks
  the registry has that digest, and attests the provenance and the SBOM, stored in
  the caller's repository and pushed to the registry next to the image. It is the
  only job with `packages: write`, `id-token: write` and `attestations: write`.

### Calling them

Each calling job grants the ceiling and the reusable job narrows it. On a pull
request, `image.yml` alone, with read access only:

```yaml
permissions: {}

jobs:
  image:
    permissions:
      contents: read
    uses: those-who-run/.github/.github/workflows/image.yml@<sha> # vX.Y.Z
    with:
      smoke: docker run --rm "$IMAGE" --version
```

On a release, both, from a run on the tag. A called workflow sees its caller's ref,
so the run that calls them is started on the tag: once release-please has tagged,
its workflow dispatches the repository's release workflow with `--ref vX.Y.Z`. A
`workflow_dispatch` made with the `GITHUB_TOKEN` starts a run; it needs
`actions: write`. In the release-please workflow, after the release-please job:

```yaml
  release:
    needs: release-please
    if: needs.release-please.outputs.release_created == 'true'
    runs-on: ubuntu-latest
    permissions:
      actions: write
    steps:
      - name: Start the release workflow on the tag
        env:
          GH_TOKEN: ${{ github.token }}
          TAG: ${{ needs.release-please.outputs.tag_name }}
        run: gh workflow run release.yml --repo "$GITHUB_REPOSITORY" --ref "$TAG"
```

The release workflow, `release.yml`, then runs on `refs/tags/vX.Y.Z`, builds and
smoke-tests with `image.yml`, and publishes that build with `image-publish.yml`,
both pinned at the same SHA:

```yaml
on:
  workflow_dispatch:

permissions: {}

jobs:
  image:
    permissions:
      contents: read
    uses: those-who-run/.github/.github/workflows/image.yml@<sha> # vX.Y.Z
    with:
      smoke: docker run --rm "$IMAGE" --version
  publish:
    needs: image
    permissions:
      contents: read
      packages: write
      id-token: write
      attestations: write
    uses: those-who-run/.github/.github/workflows/image-publish.yml@<sha> # vX.Y.Z
    with:
      digest: ${{ needs.image.outputs.digest }}
      artifact: ${{ needs.image.outputs.artifact }}
```

`build` runs before the `release` environment asks for approval, so a broken image
never reaches the approval prompt. The artifact is kept 30 days on a tag, the
longest an approval can wait. Dispatched on anything but a `vX.Y.Z` tag, `publish`
fails before it downloads anything. The calling repository needs a `release`
environment (the maintainer as required reviewer, deployments limited to `v*`
tags).

### Images

Each image carries `org.opencontainers.image.source` (as a label on the image and
an annotation on its index) pointing at the calling repository, so its package on
GitHub links to that repository, plus `org.opencontainers.image.revision` and, when
built on a tag, `org.opencontainers.image.version`. Built on a tag, the image also
gets the version as the build argument `VERSION` (`1.2.3` for `v1.2.3`), so a
Dockerfile that declares `ARG VERSION=dev` can build it into the binary, for
example with `go build -ldflags "-X main.version=${VERSION}"`. A pull request's
build passes no `VERSION`, and the Dockerfile's default applies. There is no `latest` tag: a
deployment pins the version it runs. A new package is private until the maintainer
makes it public, once, after its first push.

### Verifying an image

A reusable workflow signs its attestations as itself, not as the calling
workflow, so name the publishing workflow as the signer. `--repo` and
`--source-ref` require the attestation to come from that repository's release tag:

```sh
gh attestation verify oci://ghcr.io/those-who-run/twr-<repo>:X.Y.Z \
  --repo those-who-run/<repo> \
  --source-ref refs/tags/vX.Y.Z \
  --signer-workflow those-who-run/.github/.github/workflows/image-publish.yml
```

That checks the build provenance. Add
`--predicate-type https://spdx.dev/Document/v2.3` to check the SBOM attestation.
