"""Checks on .github/workflows/checks.yml that no linter makes.

Every repository's ruleset requires checks.yml's job by name, as `<caller job> /
checks`. A rename leaves every caller's `main` waiting for a check that never
reports, and a job-level `if:` or `needs:` can leave the required check skipped. The
job also asks for no more than it reads.

Standard library only (tests/workflow_yaml.py). CI's `test` job runs it:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import unittest

from workflow_yaml import WORKFLOWS, jobs

WORKFLOW = WORKFLOWS / "checks.yml"


class ChecksWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text()
        self.jobs = jobs(self.text)

    def test_the_job_is_checks(self) -> None:
        self.assertEqual(set(self.jobs), {"checks"})

    def test_nothing_granted_at_the_top(self) -> None:
        self.assertRegex(self.text, r"(?m)^permissions: \{\}\s*$")

    def test_the_job_reads_contents_only(self) -> None:
        self.assertIn("permissions", self.jobs["checks"]["keys"])
        self.assertEqual(self.jobs["checks"]["permissions"], {"contents": "read"})

    def test_the_job_always_runs(self) -> None:
        keys = self.jobs["checks"]["keys"]
        self.assertNotIn("if", keys)
        self.assertNotIn("needs", keys)

    def test_the_parser_sees_a_change(self) -> None:
        # A parser that saw nothing would pass the checks above.
        changed = self.text.replace(
            "  checks:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n",
            "  checks:\n    runs-on: ubuntu-latest\n    if: false\n    permissions:\n"
            "      contents: read\n      pull-requests: write\n",
        )
        self.assertNotEqual(changed, self.text)
        job = jobs(changed)["checks"]
        self.assertEqual(job["keys"].get("if"), "false")
        self.assertEqual(job["permissions"].get("pull-requests"), "write")


if __name__ == "__main__":
    unittest.main()
