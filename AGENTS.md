# Agents and contributors

The shared rules for every Those Who Run v2 repository are in `paddock`'s
[`AGENTS.md`](https://github.com/those-who-run/paddock/blob/main/AGENTS.md). They
apply here in full. This file adds only what is specific to `those-who-run/.github`.

## What this repository is

The organisation's community files and profile, the scripts that protect each
repository (`scripts/`), the canonical agent guard and settings that every repository
copies (`agent/`), and the reusable workflows that every repository's CI and release
call (`.github/workflows/`). A mistake here reaches every repository at once.

## Definition of done

- CI's `test` and `checks / checks` jobs are green. Locally, run what they run:

  ```sh
  pre-commit install
  pre-commit run --all-files
  python3 -m unittest discover agent/tests
  python3 -m unittest discover tests
  ```

- A change to the guard or the settings is made in `agent/` and copied unchanged to
  `.claude/`; the `guard-copy` hook fails when the two differ. Each rule the guard
  gains has a blocked example and a near miss that passes in `agent/tests/`.
- Docs change in the same PR: `agent/README.md` for the guard, this file for the
  rules below.

## Workflows

- A change to a workflow is always the maintainer's to merge, whoever wrote it.
  These workflows run with tokens in every repository's CI and release.
- Reusable workflows keep their job names stable. Rulesets require checks by name
  (`<caller job> / <reusable job>`), so renaming a job leaves every caller's `main`
  waiting for a check that never reports.
- A change to a reusable workflow's inputs (adding a required one, removing or
  renaming one, or changing a default's meaning) is a breaking change: mark the
  commit with `!` or a `BREAKING CHANGE:` footer, so release-please makes it a major
  version (a minor before 1.0).
- Every workflow has `permissions: {}` at the top and grants per job; every action
  is pinned by commit SHA with the tag in a comment; checkouts set
  `persist-credentials: false`. zizmor and actionlint check this in the commit hooks.
- Each reusable workflow has a section in
  [`.github/workflows/README.md`](.github/workflows/README.md): its inputs and its
  job names. It changes in the same PR as the workflow.

## Repository protection

`scripts/repos.json` lists every v2 repository and what it needs: its required
checks (each name as GitHub reports it, such as `checks / checks`), approvals, code
owners, Issues, release tag patterns, whether it publishes, and CodeQL's languages.
`scripts/protect-repo.sh` applies it. A new repository, or a check that a repository
starts to require, is a change to that list in a PR; the maintainer merges it and
runs the script. Agents never run it: it changes repository settings.

A publishing repository may also set `prevent_self_review`, which is false when left
out. It stays off while the maintainer is the only reviewer of the `release`
environment: GitHub won't let a reviewer approve a deployment in a run they started,
and his merge starts each release run, so with it on every release would wait for an
approval nobody can give. The environment still needs his approval, and the bot
account isn't a reviewer. Turn it on once there is a second reviewer.

## Releases

release-please keeps a release PR open from the Conventional Commits on `main`. The
maintainer merges it by hand, which tags `vX.Y.Z` and creates the GitHub release.
`release-pr.yml` does this through `release-please.yml`, the reusable workflow every
repository calls, so it is tested here first; its digest comment on the release PR
lists the changes to `agent/` and `scripts/` too. Callers pin a workflow by that tag's commit SHA:

```yaml
uses: those-who-run/.github/.github/workflows/<name>.yml@<sha> # vX.Y.Z
```
