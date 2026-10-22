# Workflows

The reusable workflows here are what every Those Who Run repository's CI and release
call. Callers pin each one by the commit SHA of a release tag:

```yaml
uses: those-who-run/.github/.github/workflows/<name>.yml@<sha> # vX.Y.Z
```

Rulesets require a reusable workflow's job by name, as `<caller job> / <job>`, so the
job names below stay as they are. Adding a required input, removing or renaming one,
or changing a default's meaning is a breaking change
([AGENTS.md](../../AGENTS.md#workflows)).

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
| `python-version` | string | `3.13` | The Python that runs pre-commit and any local Python hooks. |
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
`golang.org/x/*`). dependency-review-action can only exempt a package from the licence
check, by its exact name, so those packages are listed one by one in the workflow and
the notices stay off the global list. They still get the vulnerability check.

The PR checks run on `pull_request` events only. A PR title or description edited
after the run is checked when the job re-runs.

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
| `coverage-floor` | number | `90` | The lowest total statement coverage the tests may reach, in per cent. A package without tests counts as uncovered. |
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

Keeps a repository's release PR open with release-please, runs the repository's CI
on it, and comments on it what changed since the last release in the places that
need a look. The maintainer merges the release PR by hand; that run tags the merge
commit `vX.Y.Z`, creates the GitHub release, sets the outputs and dispatches the
caller's release workflow on the new tag, which publishes. Nothing in it merges or
arms auto-merge (`tests/test_release_workflow.py`).

It reads the caller's `release-please-config.json` and
`.release-please-manifest.json`, for one package at the root of the repository.

### Inputs

| Input | Default | What it is |
| --- | --- | --- |
| `ci-workflow` | `ci.yml` | The caller's CI workflow, by file name. It is dispatched on the release branch, so needs `workflow_dispatch:` in its `on:`. |
| `release-workflow` | `release.yml` | The caller's release workflow, by file name, dispatched on the new tag after a release. It needs `workflow_dispatch:` in its `on:` and must exist at the tag. Empty for a repository with nothing to publish. |
| `watched-paths` | (none) | More paths for the digest to list, space-separated; a directory ends in `/`. |

### Outputs

| Output | On a release | Otherwise |
| --- | --- | --- |
| `release_created` | `true` | empty |
| `tag_name` | `v1.2.3` | empty |
| `version` | `1.2.3` | empty |

### Jobs

- `release-please`: every run. Opens or updates the release PR, or, on the merge
  of one, tags and releases it. `contents: write` and `pull-requests: write`.
- `digest`: every run that releases nothing, while a release PR is open. One
  comment on the release PR, edited in place, from `scripts/release_digest.py`
  in this repository at the commit the caller pinned. `contents: read` and
  `pull-requests: write`.
- `release-pr`: when release-please opened or updated the release PR. Dispatches
  `ci-workflow` on its branch. `actions: write`.
- `auto-merge-off`: every run. Turns off auto-merge on the open release PR if
  anyone armed it, with the GraphQL `disablePullRequestAutoMerge` mutation, which
  cannot merge. `pull-requests: write`.
- `release`: when this run released, and `release-workflow` is set. Dispatches
  `release-workflow` on the new tag. `actions: write`.

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
      contents: write
      pull-requests: write
      actions: write
    uses: those-who-run/.github/.github/workflows/release-please.yml@<sha> # vX.Y.Z
    with:
      ci-workflow: ci.yml
      release-workflow: release.yml
```

The calling job grants the ceiling and each reusable job narrows it.

### Publishing

Publishing runs in the caller's release workflow (`release.yml`), which
`release-please.yml` dispatches on the new tag. Its run is on the tag itself, so
`github.ref` is `refs/tags/vX.Y.Z`. A workflow called directly from the release
PR's run would see that run's ref, `main`. A tag made with the `GITHUB_TOKEN`
starts no workflow by its push, but a dispatch made with that token does, so no
other token is needed.

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

release-please opens its PR with the `GITHUB_TOKEN`. For that, the repository needs
"Allow GitHub Actions to create and approve pull requests" on. The same setting
lets a workflow's token approve a PR, so it is safe only where `main`'s ruleset
requires a code owner's approval: then an approval by `github-actions[bot]`, or by
an agent's bot account, counts for nothing, since neither is a code owner. Apply
both together:

```sh
scripts/protect-repo.sh those-who-run/<repo> --checks "<checks>" --code-owners --actions-prs
```

The repository also needs a `.github/CODEOWNERS` naming the maintainer.

A PR opened by the `GITHUB_TOKEN` starts no workflow, which is why `release-pr`
dispatches CI on it: the release PR's required checks then report. A merge by the
`GITHUB_TOKEN` would not start the release run either, so the release PR is merged
by a person, never by a workflow or auto-merge (v1's 0.7.6), and `auto-merge-off`
keeps auto-merge off.

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
built on a tag, `org.opencontainers.image.version`. There is no `latest` tag: a
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
