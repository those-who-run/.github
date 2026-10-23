"""Checks on this repository's own .github/workflows/ci.yml.

ci.yml takes `workflow_dispatch` for one reason: release-please's PR starts no
workflows, so release-please.yml dispatches CI on the release branch. A dispatched
run on any other ref fails in `test`'s first step. These tests run that step's script
against a few refs, and check that the required jobs carry no `if:` or `needs:` that
could leave them skipped.

Standard library only (tests/workflow_yaml.py). CI's `test` job runs it:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import os
import subprocess
import unittest

from workflow_yaml import WORKFLOWS, jobs, step

WORKFLOW = WORKFLOWS / "ci.yml"
GUARD = "Dispatched only on a release-please branch"


class CiWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text()
        self.jobs = jobs(self.text)

    def run_guard(self, ref: str) -> subprocess.CompletedProcess:
        script = step(self.jobs["test"], GUARD)["run"]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GITHUB_"))}
        env["GITHUB_REF"] = ref
        return subprocess.run(
            ["bash", "-e", "-c", script], env=env, capture_output=True, text=True, check=False,
        )

    def test_required_jobs_always_run(self) -> None:
        self.assertEqual(set(self.jobs), {"test", "checks"})
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                self.assertNotIn("if", job["keys"])
                self.assertNotIn("needs", job["keys"])

    def test_the_guard_is_the_first_step_and_runs_on_dispatch_only(self) -> None:
        first = next(line for line in self.jobs["test"]["steps"] if line.startswith("      - "))
        self.assertEqual(first.strip(), f"- name: {GUARD}")
        self.assertEqual(
            step(self.jobs["test"], GUARD).get("if"), "github.event_name == 'workflow_dispatch'"
        )

    def test_a_release_please_branch_passes(self) -> None:
        result = self.run_guard("refs/heads/release-please--branches--main")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_any_other_ref_fails(self) -> None:
        for ref in (
            "refs/heads/main",
            "refs/heads/feature/release-please--x",
            "refs/heads/release-please-branches",
            "refs/tags/release-please--branches--main",
            "",
        ):
            with self.subTest(ref=ref):
                result = self.run_guard(ref)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("::error::", result.stdout)


if __name__ == "__main__":
    unittest.main()
