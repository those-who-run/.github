"""scripts/go_test_guard.py: each way of weakening Go tests is reported, and a near
miss that only looks like one (a moved test, a raised floor) is not.

Each case builds a small git repository with a base commit and a change on a
branch, as a PR has. Standard library and git only:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("go_test_guard", ROOT / "scripts" / "go_test_guard.py")
guard = importlib.util.module_from_spec(_spec)
# dataclasses look the module up by name while it loads.
sys.modules["go_test_guard"] = guard
_spec.loader.exec_module(guard)

LAP_TEST = """package lap

import "testing"

func TestLap(t *testing.T) {
\tif Lap() != 1 {
\t\tt.Fatal("wrong lap")
\t}
}

func TestBest(t *testing.T) {
\tt.Log("best")
}
"""

CALLER = """jobs:
  go:
    uses: those-who-run/.github/.github/workflows/go.yml@0000 # v0.2.0
    with:
      coverage-floor: 90
"""

# git with no user or system config, so a maintainer's settings change nothing. A
# commit hook runs these tests with GIT_DIR, GIT_INDEX_FILE and the like set to the
# repository being committed to; left in place, the throwaway repositories' git
# commands would act on that one instead, so every other GIT_ variable goes.
GIT_ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


class CleanGit(unittest.TestCase):
    """The guard's own git commands see GIT_ENV, not the hook's environment."""

    def setUp(self):
        patcher = unittest.mock.patch.dict(os.environ, GIT_ENV, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class Repo:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.path, env=GIT_ENV, check=True, capture_output=True, text=True
        ).stdout

    def write(self, name: str, text: str) -> None:
        path = self.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def mv(self, old: str, new: str) -> None:
        (self.path / new).parent.mkdir(parents=True, exist_ok=True)
        self.git("mv", old, new)

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)


class GoTestGuard(CleanGit):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Repo(tmp.name)
        self.repo.write("lap/lap.go", "package lap\n\nfunc Lap() int { return 1 }\n")
        self.repo.write("lap/lap_test.go", LAP_TEST)
        self.repo.write(".github/workflows/ci.yml", CALLER)
        self.repo.write("Makefile", "COVERAGE_FLOOR ?= 90\n")
        self.repo.write("go.mod", "module example.com/lap\n\ngo 1.27.0\n")
        self.repo.commit("base")
        self.repo.git("checkout", "-q", "-b", "pr")

    def found(self) -> list[str]:
        self.repo.commit("change")
        return [f.what for f in guard.weakenings("main", "pr", cwd=str(self.repo.path))]

    def edit(self, name: str, old: str, new: str) -> None:
        text = (self.repo.path / name).read_text()
        self.assertIn(old, text)
        self.repo.write(name, text.replace(old, new, 1))

    # Near misses: changes that keep every test.
    def test_a_change_to_code_only_weakens_nothing(self):
        self.edit("lap/lap.go", "return 1", "return 1 // the first lap")
        self.assertEqual(self.found(), [])

    def test_a_test_added_weakens_nothing(self):
        self.edit("lap/lap_test.go", "func TestBest", "func TestNew(t *testing.T) {}\n\nfunc TestBest")
        self.assertEqual(self.found(), [])

    def test_a_test_moved_to_another_file_weakens_nothing(self):
        self.edit("lap/lap_test.go", "func TestBest(t *testing.T) {\n\tt.Log(\"best\")\n}\n", "")
        self.repo.write(
            "lap/best_test.go",
            'package lap\n\nimport "testing"\n\nfunc TestBest(t *testing.T) {\n\tt.Log("best")\n}\n',
        )
        self.assertEqual(self.found(), [])

    def test_a_raised_floor_weakens_nothing(self):
        self.edit(".github/workflows/ci.yml", "coverage-floor: 90", "coverage-floor: 95")
        self.assertEqual(self.found(), [])

    def test_a_test_file_renamed_to_a_test_file_weakens_nothing(self):
        self.repo.mv("lap/lap_test.go", "lap/laps_test.go")
        self.assertEqual(self.found(), [])

    def test_a_test_file_moved_to_another_package_weakens_nothing(self):
        self.repo.mv("lap/lap_test.go", "pit/lap_test.go")
        self.assertEqual(self.found(), [])

    def test_a_test_file_renamed_for_the_runners_platform_weakens_nothing(self):
        self.repo.mv("lap/lap_test.go", "lap/lap_linux_amd64_test.go")
        self.assertEqual(self.found(), [])

    def test_a_test_file_named_after_a_platform_without_a_suffix_weakens_nothing(self):
        # go/build reads only the part after the first `_`: this is not constrained.
        self.repo.mv("lap/lap_test.go", "lap/windows_test.go")
        self.assertEqual(self.found(), [])

    def test_a_new_go_mod_away_from_the_tests_weakens_nothing(self):
        self.repo.write("tools/go.mod", "module example.com/lap/tools\n\ngo 1.27.0\n")
        self.assertEqual(self.found(), [])

    def test_a_test_file_moved_inside_testdata_weakens_nothing(self):
        self.repo.write("lap/testdata/case_test.go", "package lap\n")
        self.repo.commit("a fixture")
        self.repo.git("branch", "-f", "main")
        self.repo.mv("lap/testdata/case_test.go", "lap/testdata/old_case_test.go")
        self.assertEqual(self.found(), [])

    # Each weakening, one at a time.
    def test_a_deleted_test_file(self):
        (self.repo.path / "lap/lap_test.go").unlink()
        self.assertEqual(self.found(), ["test file deleted"])

    def test_a_test_file_renamed_away(self):
        self.repo.mv("lap/lap_test.go", "lap/lap_cases.go")
        self.assertEqual(
            self.found(), ["test file renamed to lap/lap_cases.go, which is not a test file"]
        )

    def test_a_removed_test_function(self):
        self.edit("lap/lap_test.go", "func TestBest(t *testing.T) {\n\tt.Log(\"best\")\n}\n", "")
        self.assertEqual(self.found(), ["test removed: TestBest"])

    def test_a_removed_fuzz_function(self):
        self.edit(
            "lap/lap_test.go", "func TestBest", "func FuzzLap(f *testing.F) {}\n\nfunc TestBest"
        )
        self.repo.commit("a fuzz test")
        self.repo.git("branch", "-f", "main")
        self.edit("lap/lap_test.go", "func FuzzLap(f *testing.F) {}\n\n", "")
        self.assertEqual(self.found(), ["test removed: FuzzLap"])

    def test_a_removed_example_function(self):
        self.edit("lap/lap_test.go", "func TestBest", "func ExampleLap() {}\n\nfunc TestBest")
        self.repo.commit("an example")
        self.repo.git("branch", "-f", "main")
        self.edit("lap/lap_test.go", "func ExampleLap() {}\n\n", "")
        self.assertEqual(self.found(), ["test removed: ExampleLap"])

    def test_a_renamed_test_function(self):
        # A new name is a new test: the old one is reported, to be explained.
        self.edit("lap/lap_test.go", "func TestBest", "func TestFastest")
        self.assertEqual(self.found(), ["test removed: TestBest"])

    def test_a_test_file_renamed_for_another_platform(self):
        self.repo.mv("lap/lap_test.go", "lap/lap_windows_test.go")
        self.assertEqual(
            self.found(),
            [
                "test file moved where go test ./... skips it: lap/lap_windows_test.go, "
                "a file name that builds only for windows, not on the runner"
            ],
        )

    def test_a_test_file_renamed_for_another_architecture(self):
        self.repo.mv("lap/lap_test.go", "lap/lap_linux_arm64_test.go")
        self.assertEqual(
            self.found(),
            [
                "test file moved where go test ./... skips it: lap/lap_linux_arm64_test.go, "
                "a file name that builds only for linux/arm64, not on the runner"
            ],
        )

    def test_a_test_file_moved_into_testdata(self):
        self.repo.mv("lap/lap_test.go", "lap/testdata/lap_test.go")
        self.assertEqual(
            self.found(),
            [
                "test file moved where go test ./... skips it: lap/testdata/lap_test.go, "
                "under a testdata directory"
            ],
        )

    def test_a_test_file_moved_into_vendor(self):
        self.repo.mv("lap/lap_test.go", "vendor/lap/lap_test.go")
        self.assertEqual(
            self.found(),
            [
                "test file moved where go test ./... skips it: vendor/lap/lap_test.go, "
                "under a vendor directory"
            ],
        )

    def test_a_test_file_moved_under_an_underscore_directory(self):
        self.repo.mv("lap/lap_test.go", "_old/lap_test.go")
        self.assertEqual(
            self.found(),
            [
                "test file moved where go test ./... skips it: _old/lap_test.go, "
                "under _old/, a directory starting with _"
            ],
        )

    def test_a_test_file_moved_under_a_new_nested_go_mod(self):
        self.repo.mv("lap/lap_test.go", "sub/lap_test.go")
        self.repo.write("sub/go.mod", "module example.com/sub\n\ngo 1.27.0\n")
        self.assertEqual(
            self.found(), ["test file now in another module: sub/lap_test.go is under sub/go.mod"]
        )

    def test_a_new_go_mod_above_a_test_file(self):
        self.repo.write("lap/go.mod", "module example.com/lap/lap\n\ngo 1.27.0\n")
        self.assertEqual(
            self.found(), ["test file now in another module: lap/lap_test.go is under lap/go.mod"]
        )

    def test_a_removed_suite_method(self):
        self.edit(
            "lap/lap_test.go", "func TestBest", "func (s *Suite) TestPit() {}\n\nfunc TestBest"
        )
        self.repo.commit("a suite method")
        self.repo.git("branch", "-f", "main")
        self.edit("lap/lap_test.go", "func (s *Suite) TestPit() {}\n\n", "")
        self.assertEqual(self.found(), ["test removed: TestPit"])

    def test_a_skip(self):
        self.edit("lap/lap_test.go", '\tt.Log("best")', '\tt.Skip("flaky")\n\tt.Log("best")')
        self.assertEqual(self.found(), ['skip added: t.Skip("flaky")'])

    def test_a_skipf_and_skipnow(self):
        self.edit("lap/lap_test.go", '\tt.Log("best")', '\tt.Skipf("%d", 1)\n\tt.SkipNow()')
        self.assertEqual(
            self.found(), ['skip added: t.Skipf("%d", 1)', "skip added: t.SkipNow()"]
        )

    def test_a_skip_in_code_that_is_not_a_test_is_not_reported(self):
        self.edit("lap/lap.go", "func Lap", "// Skip(x) is not a test's skip.\nfunc Lap")
        self.assertEqual(self.found(), [])

    def test_a_build_constraint(self):
        self.edit("lap/lap_test.go", "package lap", "//go:build integration\n\npackage lap")
        self.assertEqual(
            self.found(), ["build constraint added to a test file: //go:build integration"]
        )

    def test_an_old_style_build_constraint(self):
        self.edit("lap/lap_test.go", "package lap", "// +build integration\n\npackage lap")
        self.assertEqual(
            self.found(), ["build constraint added to a test file: // +build integration"]
        )

    def test_a_lowered_floor_in_the_caller(self):
        self.edit(".github/workflows/ci.yml", "coverage-floor: 90", "coverage-floor: 85")
        self.assertEqual(self.found(), ["coverage floor lowered: 90% -> 85%"])

    def test_a_removed_floor_in_the_caller(self):
        self.edit(".github/workflows/ci.yml", "    with:\n      coverage-floor: 90\n", "")
        self.assertEqual(self.found(), ["coverage floor removed: 90%"])

    def test_a_lowered_floor_in_the_makefile(self):
        self.edit("Makefile", "COVERAGE_FLOOR ?= 90", "COVERAGE_FLOOR ?= 80.5")
        self.assertEqual(self.found(), ["coverage floor lowered: 90% -> 80.5%"])


class Output(CleanGit):
    """What go.yml reads: `weakened=` in $GITHUB_OUTPUT, and exit 0 either way."""

    def run_main(self, weaken: bool) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Repo(os.path.join(tmp.name, "repo"))
        repo.write("lap_test.go", LAP_TEST)
        repo.commit("base")
        repo.git("checkout", "-q", "-b", "pr")
        repo.write("lap_test.go", LAP_TEST.replace("\tt.Log", "\tt.Skip()\n\tt.Log") if weaken else LAP_TEST + "\n")
        repo.commit("change")
        out = os.path.join(tmp.name, "output")
        cwd = os.getcwd()
        os.chdir(repo.path)
        self.addCleanup(os.chdir, cwd)
        env = {"GITHUB_OUTPUT": out, "GITHUB_STEP_SUMMARY": os.path.join(tmp.name, "summary")}
        with unittest.mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(guard.main(["main", "pr"]), 0)
        return Path(out).read_text()

    def test_weakened(self):
        self.assertEqual(self.run_main(True), "weakened=true\n")

    def test_not_weakened(self):
        self.assertEqual(self.run_main(False), "weakened=false\n")


if __name__ == "__main__":
    unittest.main()
