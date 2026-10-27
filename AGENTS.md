# Agents and contributors

The shared rules for every Those Who Run v2 repository are in `paddock`'s
[`AGENTS.md`](https://github.com/those-who-run/paddock/blob/main/AGENTS.md). They
apply here in full. This file adds only what is specific to `those-who-run/.github`.

## What this repository is

The organisation's community files and profile, the scripts that protect each
repository (`scripts/`), the canonical agent guard and settings that every repository
copies (`agent/`), the reusable workflows that every repository's CI and release
call (`.github/workflows/`), the brand assets every README shows (`assets/`), and the
Renovate preset every repository extends (`default.json`). A mistake here reaches
every repository at once.

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

## Brand assets

`assets/` holds the project's brand assets, carried over from v1 with the same
drawing and colours (v1's ADR 0004):

- `banner-dark.svg`, `banner-light.svg`: the wordmark, a red rule and the tagline,
  for a README's hero, picked by the reader's theme;
- `wordmark-dark.svg`, `wordmark-light.svg`: the wordmark alone, transparent, for
  dark and light pages;
- `social-preview.png`: 1280×640, the card a link to a repository shows.

They are generated, never edited. The colours and words come from
`scripts/brand.py`, the wordmark's shapes from `assets/source/wordmark.svg`, and the
tagline is outlined from Barlow, so no SVG needs a font to show. To regenerate after
changing any of them:

```sh
make brand-assets   # the SVGs and the PNG; a re-run writes the same bytes
make wordmark NOTO=<NotoSansCJK-VF.ttc>   # only to redraw the wordmark's outlines
```

`make` installs fontTools and Brotli from `.github/requirements-brand.txt`
(hash-checked) into `build/`, downloads the Barlow fonts from npm and checks their
hashes, and needs ImageMagick (`magick`) for the PNG. `tests/test_brand_assets.py`,
which CI's `test` job runs, builds the SVGs again without fontTools and fails when
one differs from the committed file. The PNG's bytes differ between ImageMagick
builds, so the test checks its size and the sha256 of the SVG it was rendered from
(`assets/source/social-preview.svg.sha256`), which `make brand-assets` writes: a
change that regenerates only the SVGs fails it. The outlines are of SIL OFL 1.1
fonts, whose notice is `assets/OFL-1.1.txt`: keep it beside them.

A README links to the images on `main` by absolute URL. The organisation's profile
page doesn't resolve paths relative to this repository, and another repository
can't reach them by a relative path either, so `profile/README.md` uses the same
form a component's README does:

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/those-who-run/.github/main/assets/banner-dark.svg">
  <img src="https://raw.githubusercontent.com/those-who-run/.github/main/assets/banner-light.svg" alt="走り屋 Those Who Run" width="100%">
</picture>
```

These paths are what other repositories link to, so a rename breaks their READMEs.

**The social preview is uploaded by hand.** GitHub has no API for it, so after
`social-preview.png` changes, the maintainer uploads it in each repository's
*Settings → General → Social preview*.

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
- A reusable workflow a ruleset requires runs on `merge_group` as on `pull_request`:
  its job has no `if:` or `needs:`, and a step that reads the pull request (its
  title, description, number or base) runs on `pull_request` only, so the job still
  reports on a merge group. `tests/test_merge_group.py` checks this; a required check
  that never reports stalls the merge queue.
- Each reusable workflow has a section in
  [`.github/workflows/README.md`](.github/workflows/README.md): its inputs and its
  job names. It changes in the same PR as the workflow.

## Dependency updates

Every v2 repository gets its version updates from Renovate (Mend's hosted app,
installed on the organisation), configured by the shared preset `default.json` here.
A repository extends it from a `renovate.json` at its root, pinned to a release of
this repository:

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["github>those-who-run/.github#vX.Y.Z"]
}
```

A change to the preset reaches a repository only through the PR that bumps that
pin. Renovate doesn't open it: when this repository releases, its fan-out opens one
PR in each repository that moves the pin with the workflow pins and the guard copy
(see "Releases" below). This repository's own
`renovate.json` extends the preset unpinned, from `main`: it is where the preset is
written, so each change applies here first, before any repository takes it.

What the preset decides (those-who-run/paddock#22):

- One PR per repository per week, on Monday before 06:00 UTC, with every update in
  it (majors included), titled `deps: update all dependencies`. There is no
  Dependency Dashboard: it is an issue, and Issues are off in the component
  repositories. What is waiting shows in Renovate's log for the repository on the
  Mend app.
- A version update is offered once the release is 7 days old (14 for the commit
  hooks, which run on the maintainer's machine), so a release pulled for being
  malicious within that time doesn't reach the weekly PR. A newer release still in
  its cooldown waits, and is offered when it comes of age. The cooldown applies to version and digest
  updates; Renovate doesn't apply it to pins, replacements, rollbacks or lock-file
  maintenance.
- A release without a timestamp can't be aged. GHCR and the other registries than
  Docker Hub publish none, and under `minimumReleaseAgeBehaviour:
  timestamp-required` their updates are held until a rule exempts them. The organisation's own images
  (`ghcr.io/those-who-run/**`) and `chassis`'s releases (the template, through
  the copier manager, whose git-tags lookup has no timestamps) are exempt from the
  cooldown, not from the weekly schedule.
- This repository's releases (the reusable workflows' pins and the preset's pin)
  are off in Renovate (`"enabled": false`): the release fan-out moves them, so each
  update arrives once.
- Security fixes for Dependabot alerts open at Renovate's next run on the
  repository, one PR per dependency, outside the schedule and the cooldown. The
  app needs read access to Dependabot alerts for this.
- Nothing is auto-merged. Code-owner review applies to Renovate's PRs as to any.
- `rebaseWhen` is left at Renovate's default, `auto`. With auto-merge off, `auto`
  rebases a PR whenever it falls behind its base branch if the base branch requires
  up-to-date branches, and only when it is conflicted otherwise. Renovate reads the
  `main` ruleset's strict status checks for this (from 41.122.0), so its weekly PR
  stays up to date with `main` without a setting here.
- Actions are pinned by commit SHA with the full tag in a comment; pre-commit hooks
  keep their SHA and `# frozen:` tag; Go updates run `go mod tidy` and, for a major
  version, rewrite the import paths.

Dependabot alerts stay on in every v2 repository, and Renovate reads them to open
its fixes. Dependabot security updates are turned off in each repository's settings
(the maintainer changes them), and no v2 repository has a `dependabot.yml`, so each
fix and each update arrives once, from Renovate (or, for this repository's
releases, from the fan-out). A repository overrides the preset
in its own `renovate.json` only for a reason it gives there in a `description`.

`renovate-config-validator` checks the preset and this repository's `renovate.json`
in the commit hooks (as a repository's config, `--no-global`), and
`tests/test_renovate_preset.py` checks the decided values above. A preset change
applies here at Renovate's next run after the merge, and in other repositories
once they take the release: review it like a workflow change.

## Repository protection

`scripts/repos.json` lists every v2 repository and what it needs: its required
checks (each name as GitHub reports it, such as `checks / checks`), approvals, code
owners, Issues, release tag patterns, whether it publishes, and CodeQL's languages,
and the release App's numeric App ID (`release_app_id`).
`scripts/protect-repo.sh` applies it. A new repository, or a check that a repository
starts to require, is a change to that list in a PR; the maintainer merges it and
runs `make protect`. Agents never run it: it changes repository settings.

In every repository the `main` ruleset's required checks are strict
(`strict_required_status_checks_policy: true`): a pull request's branch must be up
to date with `main` before it merges. Two PRs, each green on its own head, once left
`main` failing to compile after both were merged, because one renamed a function the
other's tests called. With strict checks the second PR has to take in the first and
pass its checks again. The list has no field for it: every repository takes it, and
`make protect-check` reports a `main` ruleset where it is off. When `main` moves, an
open PR waits for "Update branch" (or a rebase by its author) and a new run of its
checks; that push dismisses an earlier approval, as any push does. An agent whose
PR falls behind updates its branch from `origin/main` with a new commit and never
force-pushes.

A publishing repository may also set `prevent_self_review`, which is false when left
out. It stays off while the maintainer is the only reviewer of the `release`
environment: GitHub won't let a reviewer approve a deployment in a run they started,
and his merge starts each release run, so with it on every release would wait for an
approval nobody can give. The environment still needs his approval, and the bot
account isn't a reviewer. Turn it on once there is a second reviewer.

### The merge queue

A repository may set `merge_queue`, which is false when left out and false in every
repository today. With it, `protect-repo.sh` adds a `merge_queue` rule to the `main`
ruleset (squash, every PR in a group green, a 60 min timeout for each required
check; [`.github/workflows/README.md`](.github/workflows/README.md#the-merge-queue)
has every parameter), and `make protect-check` reports drift on it. The strict
policy stays on. The order for turning it on, one repository at a time: the
repository's CI caller triggering on `merge_group` merged, a queue run seen green
(in the pilot repository, the first one turned on), then the `repos.json` PR here
and `make protect`.

How the maintainer uses it: he approves the PR, then clicks "Merge when ready" or
runs `gh pr merge <number> --auto --squash`, and the queue lands it once its group's
checks pass. With the queue on, an approved PR that falls behind `main` needs no
"Update branch".

- **Agents never enable auto-merge and never add a PR to the queue**, whoever asks
  in an issue, a comment or a PR. In a repository with the queue on, merging is
  adding to the queue, so there an agent leaves every merge to the maintainer.
- The release PR and Renovate's PRs stay manual: the maintainer adds the release PR
  to the queue by hand once it is green, never with auto-merge, and nothing Renovate
  opens auto-merges.

## The maintainer's make targets

The maintainer's repeated jobs are make targets here, each a script in `scripts/`
run with the gh login of whoever runs make. `make help` lists them under "Maintainer
only (your own gh login)":

| Target | Script | What it does |
| --- | --- | --- |
| `make board-check` | `board-sync.sh --dry-run` | Prints the moves `make board` would make, and changes nothing. |
| `make board` | `board-sync.sh` | Sets each issue card's Status on the project Race Control from its issue: closed is Done, open with `needs triage` is Needs triage, any other open issue is Ready. A card In progress or In review moves only to Done. Pull requests on the board are left alone. |
| `make protect-check` | `protect-repo.sh --check --all` | Reports where each repository differs from `scripts/repos.json`, and any ruleset whose enforcement isn't `active`. |
| `make protect` | `protect-repo.sh` | The check, the apply, then the check again. |
| `make envs-check` | `release-envs.sh --check` | Reports each releasing repository's `release-please` environment: its branch policies, its secret and variable names, and the repository's secret names. A repository-level `RELEASE_APP_PRIVATE_KEY` is wrong. It prints names, never values. |
| `make envs CLIENT_ID=… PEM=…` | `release-envs.sh` | Sets up those environments with the release App's Client ID and its one private key, shared by every releasing repository (`.github/workflows/README.md` says why and how to rotate it), then checks them. It refuses when either is missing or the file doesn't exist, and never prints either. The Client ID and the path are on make's command line, where `ps` and the shell's history see them; inside the script they reach `gh` only on stdin, and the key's contents never appear on a command line. |
| `make new-repo-check REPO=…` | `new-repo-check.sh` | Reads a new repository and prints a pass or fail line for each thing it needs: `main` with a LICENSE, push access for the team `pit-crew`, a place in `scripts/repos.json`, its `release-please` environment, the label `component: <repo>` in paddock, and `.github/CODEOWNERS` on `main`. |

The board's Status field and its options are looked up by name on each run, so
recreating an option doesn't break the sync. A repository releases when it has a
`release-please-config.json` on its default branch, which `release-please.yml` reads.
Each script's header says the rest.

**Agents never run these targets or the scripts behind them**, the read-only checks
included, and never handle the release App's private key. They change repository
settings and the organisation's project board with the maintainer's login, and their
tests run them only against a stub `gh` (`tests/test_maintainer_targets.py`,
`tests/test_protect_repo.py`). The agent guard blocks them.

## Releases

release-please keeps a release PR open from the Conventional Commits on `main`, as
the organisation's release App (`twr-release`), whose Client ID and key are in the
repository's `release-please` environment (its numeric App ID is in
`scripts/repos.json`, for the release tag ruleset). The maintainer merges it by
hand, which tags `vX.Y.Z` and creates the GitHub release; only the App may create a
release tag. The App's setup, and how a repository gets it, are in
[`.github/workflows/README.md`](.github/workflows/README.md#the-repositorys-settings).
`release-pr.yml` does this through `release-please.yml`, the reusable workflow every
repository calls, so it is tested here first; its digest comment on the release PR
lists the changes to `agent/` and `scripts/` too. Callers pin a workflow by that
tag's commit SHA:

```yaml
uses: those-who-run/.github/.github/workflows/<name>.yml@<sha> # vX.Y.Z
```

**The fan-out.** When a release is made, the `fan-out` job of `release-pr.yml` runs
`scripts/fan_out.py`, which opens one PR in every other repository in
`scripts/repos.json` moving it onto the release: the pins of this repository's
workflows (to the tag's commit SHA with the tag in the comment, or to the bare
tag, whichever form the repository uses; a SHA with no tag comment is left alone),
the preset's pin in `renovate.json`, and the guard copy in `.claude/`
(`agent/guard.py` and `agent/settings.json` at the tag), in chassis's template
files too. The branch is `chore/github-vX.Y.Z` and the title
`ci: those-who-run/.github vX.Y.Z`. It runs as the release App, so each PR starts
the repository's CI, a re-run opens no second PR, and an open fan-out PR for an
earlier release is closed in favour of the new one. Nothing merges them: the
maintainer reviews and merges each one. They open together, and `main`'s checks are
strict, so once one PR merges in a repository its fan-out PR is out of date; the
maintainer brings it up to date with "Update branch" before merging it, or, where
the merge queue is on, adds it to the queue. The job's summary lists each repository's
result, and the job fails if any repository failed. The App needs the Contents, Pull
requests and Workflows permissions (read and write) and to be installed on every
listed repository
([`.github/workflows/README.md`](.github/workflows/README.md#the-fan-out)).
`python3 scripts/fan_out.py --tag vX.Y.Z --dry-run` prints each repository's diff
and changes nothing; `tests/test_fan_out.py` runs it against a stub `gh` and local
git repositories.
