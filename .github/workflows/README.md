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
