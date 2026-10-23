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
- Each reusable workflow has a section in
  [`.github/workflows/README.md`](.github/workflows/README.md): its inputs and its
  job names. It changes in the same PR as the workflow.

## Dependency updates

Every v2 repository gets its version updates from Renovate (Mend's hosted app,
installed on the organisation), configured by the shared preset `default.json` here.
A repository extends it from a `renovate.json` at its root:

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["github>those-who-run/.github"]
}
```

What the preset decides (those-who-run/paddock#22):

- One PR per repository per week, on Monday before 06:00 UTC, with every update in
  it (majors included), titled `deps: update all dependencies`. The Dependency
  Dashboard issue lists what is waiting.
- A release is offered only once it is 7 days old (14 for the commit hooks, which
  run on the maintainer's machine), so one pulled for being malicious never
  reaches a PR. A newer release still in its cooldown waits on the dashboard.
- Security fixes for Dependabot alerts open at once, in a PR of their own, outside
  the schedule and the cooldown.
- Nothing is auto-merged. Code-owner review applies to Renovate's PRs as to any.
- Actions are pinned by commit SHA with the full tag in a comment; pre-commit hooks
  keep their SHA and `# frozen:` tag; Go updates run `go mod tidy`.

Dependabot stays on for security alerts only, which Renovate reads to open its
fixes. A repository has no `dependabot.yml` version updates, so there are no
duplicate PRs. This repository itself still has Dependabot's version updates
(`.github/dependabot.yml`) until it moves to the preset. A repository overrides the preset in its own `renovate.json` only
for a reason it gives there in a `description`.

`renovate-config-validator` checks the preset in the commit hooks (as a
repository's config, `--no-global`), and `tests/test_renovate_preset.py` checks the
decided values above. A change to the preset reaches every repository at
Renovate's next run, without a release: review it like a workflow change.

## Repository protection

`scripts/repos.json` lists every v2 repository and what it needs: its required
checks (each name as GitHub reports it, such as `checks / checks`), approvals, code
owners, Issues, release tag patterns, whether it publishes, and CodeQL's languages,
and the release App's numeric App ID (`release_app_id`).
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
