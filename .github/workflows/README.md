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
