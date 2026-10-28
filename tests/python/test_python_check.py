"""scripts/python_check.py against tests/python/fixture: the project passes every
check, and the same project broken one way at a time fails the matching check, for
the reason that check gives.

It needs uv and the network, for the Python in fixture/.python-version and the tools
in fixture/uv.lock, so it lives outside the tests that
`python3 -m unittest discover tests` finds. CI's `python-check-cases` job runs it:

    python3 -m unittest discover -s tests/python -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "python" / "fixture"
PYTHON_CHECK = ROOT / "scripts" / "python_check.py"

# A broken case must not leave error annotations or a summary on the CI run that
# tests it: those are for a caller's real failures. Nor may uv see a commit hook's
# GIT_DIR, or an environment other than the copy's own.
ENV = {
    k: v
    for k, v in os.environ.items()
    if k not in ("GITHUB_ACTIONS", "GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT", "VIRTUAL_ENV")
    and not k.startswith(("GIT_", "UV_PROJECT_ENVIRONMENT"))
}
# What a run of the fixture leaves behind locally, which a copy must not take.
LEFT_BEHIND = shutil.ignore_patterns(".venv", "__pycache__", ".*_cache")


class PythonCheck(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name) / "fixture"
        shutil.copytree(FIXTURE, self.project, ignore=LEFT_BEHIND)

    def write(self, name: str, text: str) -> None:
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def check(self, *args: str) -> tuple[int, str]:
        proc = subprocess.run(
            [sys.executable, str(PYTHON_CHECK), *args],
            cwd=self.project,
            env=ENV,
            capture_output=True,
            text=True,
        )
        return proc.returncode, proc.stdout + proc.stderr

    def synced(self) -> None:
        code, out = self.check("sync")
        self.assertEqual(code, 0, out)

    def assertFails(self, check: str, reason: str, *args: str) -> None:
        code, out = self.check(check, *args)
        self.assertEqual(code, 1, f"{check} passed:\n{out}")
        self.assertIn(reason, out)

    def test_the_fixture_passes_every_check(self):
        code, out = self.check("all")
        self.assertEqual(code, 0, out)
        self.assertIn("Coverage: 100.0% (the floor is 90%).", out)

    def test_a_stale_lock(self):
        path = self.project / "pyproject.toml"
        path.write_text(path.read_text().replace('"ruff>=0.17"]', '"ruff>=0.17", "pyyaml>=6"]', 1))
        self.assertFails("lock", "uv.lock is not up to date with pyproject.toml")

    def test_a_missing_tool(self):
        # mypy taken out of the dev group, with the lock kept in step.
        path = self.project / "pyproject.toml"
        path.write_text(path.read_text().replace('"mypy>=2.4", ', "", 1))
        subprocess.run(["uv", "lock", "--quiet"], cwd=self.project, env=ENV, check=True)
        self.assertFails("sync", "the project's environment has no mypy")

    def test_an_unformatted_file(self):
        self.synced()
        self.write("src/fixture/pit.py", "def pit( ) -> int :\n    return  1\n")
        self.assertFails("format", "ruff format would change the files above")

    def test_a_lint_finding(self):
        self.synced()
        self.write("src/fixture/pit.py", "import os\n\n\ndef pit() -> int:\n    return 1\n")
        self.assertFails("lint", "F401")

    def test_a_type_error(self):
        self.synced()
        self.write("src/fixture/pit.py", 'def pit() -> int:\n    return "one"\n')
        self.assertFails("types", "Incompatible return value type")

    def test_a_failing_test(self):
        self.synced()
        self.write("tests/test_pit.py", "def test_pit() -> None:\n    assert 1 == 2\n")
        self.assertFails("test", "pytest failed")

    def test_a_skipped_test(self):
        self.synced()
        self.write(
            "tests/test_pit.py",
            "import pytest\n\n\n@pytest.mark.skip(reason='later')\n"
            "def test_pit() -> None:\n    pass\n",
        )
        self.assertFails("test", "test skipped: tests.test_pit::test_pit")

    def test_an_expected_failure_counts_as_a_skip(self):
        self.synced()
        self.write(
            "tests/test_pit.py",
            "import pytest\n\n\n@pytest.mark.xfail(reason='later')\n"
            "def test_pit() -> None:\n    assert 1 == 2\n",
        )
        self.assertFails("test", "test skipped: tests.test_pit::test_pit")

    def test_no_tests(self):
        self.synced()
        (self.project / "tests" / "test_fixture.py").unlink()
        self.assertFails("test", "pytest collected no tests")

    def test_coverage_below_the_floor(self):
        # A module nothing calls takes total coverage under the floor.
        self.write(
            "src/fixture/fuel.py",
            "def fuel(start: float, used: float) -> float:\n"
            "    left = start - used\n    if left < 0:\n        return 0\n    return left\n",
        )
        self.synced()
        code, out = self.check("test")
        self.assertEqual(code, 1, out)
        self.assertIn("is below the floor of 90%", out)
        code, out = self.check("test", "--floor", "10")
        self.assertEqual(code, 0, out)

    def test_it_runs_only_in_a_project(self):
        (self.project / "uv.lock").unlink()
        code, out = self.check("lint")
        self.assertEqual(code, 2, out)
        self.assertIn("no pyproject.toml and uv.lock here", out)


if __name__ == "__main__":
    unittest.main()
