# Workflows

The reusable workflows here are what every Those Who Run repository's CI and release
call. Callers pin each one by the commit SHA of a release tag:

```yaml
uses: those-who-run/.github/.github/workflows/<name>.yml@<sha> # vX.Y.Z
```

Rulesets require a reusable workflow's job by name, as `<caller job> / <job>`, so the
job names below stay as they are. Changing an input is a breaking change
([AGENTS.md](../../AGENTS.md#workflows)).

## checks.yml

The language-neutral checks every repository runs on a PR: dependency review, the
repository's commit hooks on every file, gitleaks over the PR's commits, the PR title
as a Conventional Commit, and a `Tests weakened:` line in the PR's description when
the caller's test guard reports a weakened test. A repository's language checks come
from their own workflow, called beside this one as a separate required check. A
repository with no language, such as `paddock`, calls only this.

Job: `checks`. Called from a caller job named `checks`, the required check is
`checks / checks`.

| Input | Type | Default | What it does |
| --- | --- | --- | --- |
| `python-version` | string | `3.13` | The Python that runs pre-commit and any local Python hooks. |
| `go-version-file` | string | empty, no Go | A `go.mod` or `.go-version` to install Go from, for local hooks that run Go tools. |
| `setup` | string | empty | Shell commands run before the commit hooks, to install what local hooks need. |
| `commit-types` | string | release-please's types | The types a PR title may use, separated by spaces. |
| `dependency-review` | boolean | `true` | Review the dependencies a PR adds or changes; needs the dependency graph. |
| `tests-weakened` | boolean | `false` | The caller's test guard found a weakened test; the PR's description then needs `Tests weakened: <reason>`. |

The caller grants the job what it uses, and passes no secrets:

```yaml
jobs:
  checks:
    permissions:
      contents: read
      pull-requests: read # the weakened-tests check reads the PR's description
    uses: those-who-run/.github/.github/workflows/checks.yml@<sha> # vX.Y.Z
```

The commit hooks are the caller's own `.pre-commit-config.yaml`. pre-commit itself
(hash-pinned in [`requirements-ci.txt`](../requirements-ci.txt)) and the `gitleaks-pr`
hook (in this repository's [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml))
come from this repository at the pinned commit, so every caller scans with the same
versions.

The PR checks run on `pull_request` events only. A PR title or description edited
after the run is checked when the job re-runs.
