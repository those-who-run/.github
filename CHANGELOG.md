# Changelog

## [0.2.12](https://github.com/those-who-run/.github/compare/v0.2.11...v0.2.12) (2026-10-28)


### Features

* **protect:** a merge queue on main, per repository, and shared workflows ready for merge_group ([#50](https://github.com/those-who-run/.github/issues/50)) ([541d918](https://github.com/those-who-run/.github/commit/541d918397c7ae8d953acd905e7e1f5891f43b34))
* **protect:** shakedown-fangio requires the Go and image checks and publishes ([c972766](https://github.com/those-who-run/.github/commit/c9727669b1d9171901b323784bb90f72675bb5e0))

## [0.2.11](https://github.com/those-who-run/.github/compare/v0.2.10...v0.2.11) (2026-10-27)


### Features

* **protect:** require branches to be up to date before merging ([#48](https://github.com/those-who-run/.github/issues/48)) ([30ac986](https://github.com/those-who-run/.github/commit/30ac986dc61a7b2ffdbfdfe4c28e808f1d0bf82f))

## [0.2.10](https://github.com/those-who-run/.github/compare/v0.2.9...v0.2.10) (2026-10-26)


### Bug Fixes

* **release:** fan-out review fixes ([#43](https://github.com/those-who-run/.github/issues/43)) ([411dbd7](https://github.com/those-who-run/.github/commit/411dbd7a9e1af70b1c4cfc3ddea34181c981e548))

## [0.2.9](https://github.com/those-who-run/.github/compare/v0.2.8...v0.2.9) (2026-10-26)


### Features

* **release:** open a PR in every repository when .github releases ([#41](https://github.com/those-who-run/.github/issues/41)) ([4f33ef0](https://github.com/those-who-run/.github/commit/4f33ef04880d6ffbd308300d8f793f21d3c0d47f))

## [0.2.8](https://github.com/those-who-run/.github/compare/v0.2.7...v0.2.8) (2026-10-26)


### Bug Fixes

* **checks:** allow golang.org/x/xerrors by name ([#39](https://github.com/those-who-run/.github/issues/39)) ([00e00a5](https://github.com/those-who-run/.github/commit/00e00a5ea0650591e8fd12f65f0cd65ae2d1579e))
* **scripts:** guard noexec, shared key text and release environment checks ([#38](https://github.com/those-who-run/.github/issues/38)) ([f837194](https://github.com/those-who-run/.github/commit/f8371945372c05473f99da5b02744662842e8cb1))

## [0.2.7](https://github.com/those-who-run/.github/compare/v0.2.6...v0.2.7) (2026-10-26)


### Bug Fixes

* **scripts:** review findings on the maintainer targets ([#35](https://github.com/those-who-run/.github/issues/35)) ([4a2e320](https://github.com/those-who-run/.github/commit/4a2e32003715053e5c4a1785d3832bf0c8cf8bed))

## [0.2.6](https://github.com/those-who-run/.github/compare/v0.2.5...v0.2.6) (2026-10-26)


### Features

* **scripts:** maintainer make targets: board sync, protection, release environments, new-repo check ([#33](https://github.com/those-who-run/.github/issues/33)) ([e51f806](https://github.com/those-who-run/.github/commit/e51f8069d232e223dcc6bb9b1067fa717d6a6cac))


### Bug Fixes

* **checks:** allow github.com/opencontainers/image-spec by name ([#32](https://github.com/those-who-run/.github/issues/32)) ([d439c44](https://github.com/those-who-run/.github/commit/d439c4472369eccc88fcabd95521fb58e3a619c9))

## [0.2.5](https://github.com/those-who-run/.github/compare/v0.2.4...v0.2.5) (2026-10-26)


### Bug Fixes

* **go:** follow go/ast's rule for the generated-file header; no floor without statements ([#29](https://github.com/those-who-run/.github/issues/29)) ([cbe524f](https://github.com/those-who-run/.github/commit/cbe524fb4908e1be05fb7c797eb54852b1adb6a5))

## [0.2.4](https://github.com/those-who-run/.github/compare/v0.2.3...v0.2.4) (2026-10-24)


### Features

* **go:** leave generated files out of coverage; no Dependency Dashboard; allow go-digest ([#27](https://github.com/those-who-run/.github/issues/27)) ([2fcb865](https://github.com/those-who-run/.github/commit/2fcb865113a8900fd214c3824748f55dff2234aa))

## [0.2.3](https://github.com/those-who-run/.github/compare/v0.2.2...v0.2.3) (2026-10-24)


### Features

* **image:** pass the release version to the build as VERSION ([#23](https://github.com/those-who-run/.github/issues/23)) ([67ca431](https://github.com/those-who-run/.github/commit/67ca4313511e5deb20b7db5dce1e99f5fe69f901))

## [0.2.2](https://github.com/those-who-run/.github/compare/v0.2.1...v0.2.2) (2026-10-24)


### Bug Fixes

* allow wcwidth's licence, and lift the cooldown for chassis's releases ([#21](https://github.com/those-who-run/.github/issues/21)) ([f2c54f3](https://github.com/those-who-run/.github/commit/f2c54f3fba509df7a0a186e45d36e97826f4a0d4))

## [0.2.1](https://github.com/those-who-run/.github/compare/v0.2.0...v0.2.1) (2026-10-24)


### Features

* brand assets in assets/, with the banner on the org profile ([#15](https://github.com/those-who-run/.github/issues/15)) ([e0011c5](https://github.com/those-who-run/.github/commit/e0011c5e675cee1f746a23099e1a9ebe21e58219))
* **renovate:** the shared Renovate preset every v2 repository extends ([#16](https://github.com/those-who-run/.github/issues/16)) ([56c9f62](https://github.com/those-who-run/.github/commit/56c9f62a23feff0251e6e11569cdc57dc74dd14e))

## [0.2.0](https://github.com/those-who-run/.github/compare/v0.1.1...v0.2.0) (2026-10-23)


### ⚠ BREAKING CHANGES

* **release:** a repository that calls release-please.yml needs the release App installed and a release-please environment (main only) with the variable RELEASE_APP_ID and the secret RELEASE_APP_PRIVATE_KEY. The ci-workflow input is no longer used; it is still accepted.

### CI

* **release:** release-please runs as the organisation's GitHub App ([#13](https://github.com/those-who-run/.github/issues/13)) ([07c1b5a](https://github.com/those-who-run/.github/commit/07c1b5a1fe0c08b4263495c903dd46776ef80c4f))

## [0.1.1](https://github.com/those-who-run/.github/compare/v0.1.0...v0.1.1) (2026-10-23)


### Features

* **scripts:** protect every repository from one list ([#11](https://github.com/those-who-run/.github/issues/11)) ([1f54319](https://github.com/those-who-run/.github/commit/1f543199a66b056227a28fe077e7b8bcf4aa4330))
* **workflows:** a reusable release-please workflow with a release digest ([#10](https://github.com/those-who-run/.github/issues/10)) ([e187e0e](https://github.com/those-who-run/.github/commit/e187e0e04990f9d8dfebddda428202a276b5caac))
* **workflows:** checks.yml, the checks every repository runs ([#7](https://github.com/those-who-run/.github/issues/7)) ([a8c7a8e](https://github.com/those-who-run/.github/commit/a8c7a8ed2fdc69fee2afde65d7048f3fec900d48))
* **workflows:** go.yml, lint, test and build for Go ([#9](https://github.com/those-who-run/.github/issues/9)) ([583e948](https://github.com/those-who-run/.github/commit/583e948a650f92f491c8d10c246707b6bd65805a))
* **workflows:** image.yml, images with SBOM, provenance and attestations ([#6](https://github.com/those-who-run/.github/issues/6)) ([8823592](https://github.com/those-who-run/.github/commit/8823592fbe1edb132878ee57e41241993b56a8bf))

## 0.1.0 (2026-10-22)


### Features

* a script that applies the org's standard protection to a repository ([e3d2f07](https://github.com/those-who-run/.github/commit/e3d2f07e5fdde838f95e0cce8314172b73250d32))
* protect-repo.sh can require an approving review ([#2](https://github.com/those-who-run/.github/issues/2)) ([2d5f8d0](https://github.com/those-who-run/.github/commit/2d5f8d04e6a5a8d7dea5a78a26a818bf30a5efed))
