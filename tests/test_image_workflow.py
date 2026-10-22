"""Checks on .github/workflows/image.yml that no linter makes.

zizmor checks that permissions are declared and actions pinned, but not which job
holds which write permission. image.yml publishes images for every repository, so
the write permissions, and the `release` environment that gates them, belong to one
job: `publish`. A change that hands them to another job, or moves `publish` out of
the environment, fails here.

Standard library only, so it reads the workflow line by line and relies on its
two-space indentation, which actionlint keeps valid. CI's `test` job runs it:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "image.yml"

JOB = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
PERMISSION = re.compile(r"^      ([a-z-]+):\s*(read|write|none)\b")
KEY = re.compile(r"^    ([a-z-]+):(.*)$")
PINNED = re.compile(r"^\s*(?:-\s*)?uses:\s*\S+@[0-9a-f]{40}\s+# v\d+(\.\d+)*\s*$")

PUBLISHING = {"packages", "id-token", "attestations"}


def jobs(text: str) -> dict[str, dict]:
    """Each job's permissions and other top-level keys, from the `jobs:` block."""
    found: dict[str, dict] = {}
    job = None
    in_jobs = False
    in_permissions = False
    for line in text.splitlines():
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if m := JOB.match(line):
            job = m.group(1)
            found[job] = {"permissions": {}, "keys": {}}
            in_permissions = False
            continue
        if job is None:
            continue
        if m := KEY.match(line):
            key, value = m.group(1), m.group(2).strip()
            found[job]["keys"][key] = value
            in_permissions = key == "permissions"
            continue
        if in_permissions and (m := PERMISSION.match(line)):
            found[job]["permissions"][m.group(1)] = m.group(2)
    return found


class ImageWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text()
        self.jobs = jobs(self.text)

    def test_stable_job_names(self) -> None:
        # Rulesets require `<caller job> / build`; renaming breaks every caller.
        self.assertEqual(set(self.jobs), {"build", "publish"})

    def test_nothing_granted_at_the_top(self) -> None:
        self.assertRegex(self.text, r"(?m)^permissions: \{\}\s*$")

    def test_only_publish_has_write_permissions(self) -> None:
        for name, job in self.jobs.items():
            writes = {p for p, level in job["permissions"].items() if level == "write"}
            with self.subTest(job=name):
                if name == "publish":
                    self.assertEqual(writes, PUBLISHING)
                else:
                    self.assertEqual(writes, set())

    def test_every_job_declares_its_permissions(self) -> None:
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                self.assertIn("permissions", job["keys"])

    def test_publish_runs_in_release_after_build_and_only_with_push(self) -> None:
        keys = self.jobs["publish"]["keys"]
        self.assertEqual(keys.get("environment"), "release")
        self.assertEqual(keys.get("needs"), "build")
        self.assertEqual(keys.get("if"), "inputs.push")

    def test_actions_pinned_by_sha(self) -> None:
        uses = [line for line in self.text.splitlines() if re.match(r"^\s*(-\s*)?uses:", line)]
        self.assertTrue(uses)
        for line in uses:
            with self.subTest(line=line.strip()):
                self.assertRegex(line, PINNED)

    def test_the_parser_sees_a_misplaced_write(self) -> None:
        # A parser that saw nothing would pass the checks above.
        moved = self.text.replace(
            "  build:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n",
            "  build:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n"
            "      id-token: write\n",
        )
        self.assertNotEqual(moved, self.text)
        self.assertEqual(jobs(moved)["build"]["permissions"].get("id-token"), "write")


if __name__ == "__main__":
    unittest.main()
