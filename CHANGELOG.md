# Changelog

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
