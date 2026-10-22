"""scripts/protect-repo.sh's options, run against a stub `gh` that only records
its calls, so nothing reaches GitHub."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "protect-repo.sh"

# Answers the two reads the script makes (existing rulesets, visibility) and logs
# every call, one per line.
STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$GH_LOG"
case "$*" in
  *--jq\\ .private*) echo false ;;
esac
exit 0
"""


class ProtectRepo(unittest.TestCase):
    def run_script(self, *args):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "gh"
            stub.write_text(STUB)
            stub.chmod(0o755)
            log = Path(tmp) / "calls.log"
            log.touch()
            env = dict(os.environ, PATH=f"{tmp}{os.pathsep}{os.environ['PATH']}", GH_LOG=str(log))
            result = subprocess.run(
                ["bash", str(SCRIPT), "example/repo", *args],
                env=env, capture_output=True, text=True, check=False,
            )
            return result, log.read_text().splitlines()

    def workflow_call(self, calls):
        found = [c for c in calls if "actions/permissions/workflow" in c]
        self.assertEqual(len(found), 1, calls)
        return found[0]

    def test_actions_prs_without_code_owners_changes_nothing(self):
        result, calls = self.run_script("--approvals", "1", "--actions-prs")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--actions-prs needs --code-owners", result.stderr)
        self.assertEqual(calls, [])

    def test_actions_prs_with_no_actions_changes_nothing(self):
        result, calls = self.run_script("--code-owners", "--actions-prs", "--no-actions")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])

    def test_actions_prs_with_code_owners_lets_actions_open_prs(self):
        result, calls = self.run_script("--approvals", "1", "--code-owners", "--actions-prs")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("can_approve_pull_request_reviews=true", self.workflow_call(calls))
        self.assertTrue(any("rulesets" in c for c in calls), calls)

    def test_by_default_actions_cannot_approve(self):
        for args in ([], ["--code-owners"]):
            with self.subTest(args=args):
                result, calls = self.run_script("--approvals", "1", *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("can_approve_pull_request_reviews=false", self.workflow_call(calls))


if __name__ == "__main__":
    unittest.main()
