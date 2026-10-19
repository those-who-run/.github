# Contributing

Thanks for looking. Those Who Run is a personal project, built in the open, mostly in small releases planned on the project board. Issues and pull requests are welcome, but there's no promise they'll be picked up quickly.

## Before you start

- **Read the issue format.** Every issue is a spec: [`docs/ISSUE_FORMAT.md`](https://github.com/those-who-run/those-who-run/blob/main/docs/ISSUE_FORMAT.md). A bug report needs what you did, what you expected, what happened, and the version (`those-who-run --version`).
- **Talk first for anything big.** Open an issue before a large change, so we can agree the approach.
- **Decisions that affect several issues** are recorded as ADRs in [`docs/adr/`](https://github.com/those-who-run/those-who-run/tree/main/docs/adr).

## Making a change

- `make venv`, then `make check` must be green: lint, types, tests, the web checks and Playwright.
- Telemetry behaviour is pinned by a recording of a real drive ([`tests/fixtures/`](https://github.com/those-who-run/those-who-run/tree/main/tests/fixtures)). A change to what the engineer says or detects comes with a fixture.
- Docs change in the same pull request as the code they describe.
- Commits and pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:` …). Releases are cut from them.
- Write in British English, in plain sentences.

## Licence

By contributing you agree that your work is licensed under the project's licence, GPL-3.0-only.
