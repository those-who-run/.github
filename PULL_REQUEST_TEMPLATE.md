<!-- Title: a Conventional Commit, e.g. `feat(ecu): …` or `fix: …`. It becomes the squashed commit. -->

## What changed

AI assistance: none / <!-- the tool, and what it did -->

New direct dependencies: none <!-- or each one, with the reason for it -->

## How it was checked

<!-- One row per acceptance criterion, in the issue's order. Proof is the kind of evidence: `unit`, `recording`, `parity`, `e2e`, `live` or `car`. Where names the test or the command, and the recording when there is one. An agent never claims `live` or `car`: a criterion only the maintainer can check says so and stays open. -->

| Criterion | Proof | Where |
|---|---|---|
| AC1 <!-- short name --> | unit | <!-- `path/to/the_test.go`, `TestName` --> |
| AC2 <!-- short name --> | car | Not run here: the maintainer checks it in a session |

- [ ] The command that matches CI (the repository's `AGENTS.md` names it) is green
- [ ] Docs updated, or none needed because:

<!-- If a test was deleted, skipped or loosened, add a line that starts with `Tests weakened:` and says which test and why. Leave it out otherwise. -->

Closes those-who-run/paddock#N.
<!-- Only when every acceptance criterion is met. Otherwise: Part of those-who-run/paddock#N (which criteria are left, and why). -->
