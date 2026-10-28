"""Checks on this repository's own .github/workflows/ci.yml.

ci.yml runs on pull requests, on pushes to main and in the merge queue
(`merge_group`), nothing else; a queue whose required checks never report never
merges, and only a superseded PR run is cancelled. release-please's PR is opened by
the organisation's release App, so it starts CI through `pull_request` like any
other PR, and there is no dispatch on the release branch to allow. These tests check
the triggers, and that the required jobs carry no `if:` or `needs:` that could leave
them skipped.

Standard library only (tests/workflow_yaml.py). CI's `test` job runs it:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import re
import unittest

from workflow_yaml import WORKFLOWS, jobs

WORKFLOW = WORKFLOWS / "ci.yml"
TRIGGER = re.compile(r"^  ([a-z_]+):")


def triggers(text: str) -> set[str]:
    """The events in a workflow's top-level `on:`."""
    found, inside = set(), False
    for line in text.splitlines():
        if line.startswith("on:"):
            inside = True
            continue
        if inside and line and not line.startswith((" ", "#")):
            break
        if inside and (m := TRIGGER.match(line)):
            found.add(m.group(1))
    return found


def dispatch_problems(text: str) -> list[str]:
    """What is left of the release branch's dispatch: the trigger, or a step for it."""
    problems = []
    if "workflow_dispatch" in triggers(text):
        problems.append("ci.yml takes workflow_dispatch")
    if re.search(r"workflow_dispatch|release-please--", text):
        problems.append("ci.yml still handles a dispatch on a release-please branch")
    return problems


class CiWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text()
        self.jobs = jobs(self.text)

    def test_required_jobs_always_run(self) -> None:
        # `test` and `checks` are the required jobs; `go-fixture` and `go-check-cases`
        # test go.yml. A new job means changing this list.
        self.assertEqual(set(self.jobs), {"test", "checks", "go-fixture", "go-check-cases"})
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                self.assertNotIn("if", job["keys"])
                self.assertNotIn("needs", job["keys"])

    def test_it_runs_on_pull_requests_main_and_the_merge_queue_only(self) -> None:
        self.assertEqual(triggers(self.text), {"pull_request", "merge_group", "push"})
        self.assertIn("  merge_group:\n    types: [checks_requested]\n", self.text)
        self.assertIn("  push:\n    branches: [main]\n", self.text)

    def test_only_a_superseded_pr_run_is_cancelled(self) -> None:
        self.assertIn("  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n", self.text)

    def test_there_is_no_dispatch_on_a_release_branch(self) -> None:
        self.assertEqual(dispatch_problems(self.text), [])

    def test_a_dispatch_trigger_or_guard_is_caught(self) -> None:
        with_trigger = self.text.replace("  pull_request:\n", "  pull_request:\n  workflow_dispatch:\n", 1)
        self.assertIn("ci.yml takes workflow_dispatch", dispatch_problems(with_trigger))
        with_guard = self.text.replace(
            "    steps:\n",
            "    steps:\n      - if: github.event_name == 'workflow_dispatch'\n"
            "        run: case \"$GITHUB_REF\" in refs/heads/release-please--*) ;; esac\n",
            1,
        )
        self.assertIn(
            "ci.yml still handles a dispatch on a release-please branch", dispatch_problems(with_guard)
        )


if __name__ == "__main__":
    unittest.main()
