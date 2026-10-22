"""scripts/release_digest.py lists what a release changes in the places that need a look.

Each category it flags has a case that is flagged and a near miss that is not, made
in a throwaway git repository. Standard library only; CI's `test` job runs it:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "release_digest", ROOT / "scripts" / "release_digest.py"
)
assert _spec and _spec.loader
release_digest = importlib.util.module_from_spec(_spec)
sys.modules["release_digest"] = release_digest  # dataclasses look their module up
_spec.loader.exec_module(release_digest)

# The test repositories ignore the machine's git config (signing, hooks, identity).
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


class Repo:
    """A git repository in a temporary directory, released at v0.1.0."""

    def __init__(self, files: dict[str, str]):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = self._tmp.name
        self.git("init", "-q", "-b", "main")
        self.commit("chore: start", files)
        self.git("tag", "v0.1.0")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.path, check=True, capture_output=True, text=True
        ).stdout

    def commit(self, subject: str, files: dict[str, str | None]) -> None:
        for name, text in files.items():
            p = Path(self.path, name)
            if text is None:
                self.git("rm", "-q", name)
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            self.git("add", name)
        self.git("commit", "-q", "--allow-empty", "-m", subject)

    def digest(self, *watch: str):
        return release_digest.digest("v0.1.0", "HEAD", cwd=self.path, watch=watch)

    def close(self) -> None:
        self._tmp.cleanup()


def flagged(d, section: str) -> str:
    return "\n".join(what for what, _ in d.sections.get(section, []))


class Base(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        # A commit hook runs these tests with GIT_DIR, GIT_INDEX_FILE and the like set
        # to the repository being committed to; left in place, the throwaway
        # repositories' git commands would act on that one instead.
        for name in [k for k in os.environ if k.startswith("GIT_") and k not in GIT_ENV]:
            del os.environ[name]

    def repo(self, files: dict[str, str]) -> Repo:
        r = Repo(files)
        self.addCleanup(r.close)
        return r


class Categories(unittest.TestCase):
    """Which section a changed path goes in; None is a near miss."""

    cases = {
        "Dependencies": (
            ["go.mod", "svc/go.sum", "web/package-lock.json", "uv.lock", "requirements-ci.txt",
             ".github/requirements-ci.txt"],
            ["docs/go.mod.md", "requirements.md", "src/package.go"],
        ),
        "Workflows and GitHub configuration": (
            [".github/workflows/ci.yml", ".github/dependabot.yml"],
            ["github/workflows/ci.yml", "docs/.github.md", "src/github.go"],
        ),
        "Agent policy": (
            [".claude/settings.json", ".claude/hooks/guard.py", "AGENTS.md", "web/CLAUDE.md"],
            ["docs/agents.md", "src/claude/client.go", ".claude"],
        ),
        "Code owners": (
            ["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"],
            ["docs/CODEOWNERS.md", "codeowners.txt"],
        ),
        "Build and release files": (
            ["Makefile", "Dockerfile", "web/Dockerfile.dev", "build/rules.mk",
             "release-please-config.json", ".release-please-manifest.json",
             ".pre-commit-config.yaml", ".go-version"],
            ["docs/makefile.md", "src/build.go", "Dockerfiles.md"],
        ),
    }  # fmt: skip

    def test_each_category_and_its_near_misses(self):
        for section, (hits, misses) in self.cases.items():
            for path in hits:
                with self.subTest(path=path):
                    self.assertEqual(release_digest.category(path), section)
            for path in misses:
                with self.subTest(path=path):
                    self.assertIsNone(release_digest.category(path))

    def test_watched_paths_only_when_asked(self):
        watch = ("scripts/", "agent/guard.py")
        self.assertEqual(release_digest.category("scripts/x.sh", watch), "Other watched paths")
        self.assertEqual(release_digest.category("agent/guard.py", watch), "Other watched paths")
        self.assertIsNone(release_digest.category("scripts/x.sh"))
        self.assertIsNone(release_digest.category("agent/guard.py.bak", watch))
        self.assertIsNone(release_digest.category("scriptsx/y.sh", watch))


class WatchedFiles(Base):
    def test_changed_files_are_listed_with_their_prs(self):
        r = self.repo({"README.md": "hi\n", "go.mod": "module x\n"})
        r.commit("deps: a new module (#7)", {"go.mod": "module x\nrequire y v1\n"})
        r.commit("ci: a workflow (#8)", {".github/workflows/ci.yml": "on: push\n"})
        r.commit("chore: the agent rules (#9)", {"AGENTS.md": "rules\n"})
        r.commit("chore: owners", {".github/CODEOWNERS": "* @someone\n"})
        r.commit("build: a Makefile (#11)", {"Makefile": "all:\n"})
        r.commit("docs: the readme (#12)", {"README.md": "hello\n"})
        d = r.digest()
        self.assertEqual(
            d.sections["Dependencies"], [("M `go.mod`", ["#7"])]
        )
        self.assertEqual(
            d.sections["Workflows and GitHub configuration"],
            [("A `.github/workflows/ci.yml`", ["#8"])],
        )
        self.assertEqual(d.sections["Agent policy"], [("A `AGENTS.md`", ["#9"])])
        # A direct push has no (#N): its subject stands in, as code.
        self.assertEqual(
            d.sections["Code owners"], [("A `.github/CODEOWNERS`", ["`chore: owners`"])]
        )
        self.assertEqual(d.sections["Build and release files"], [("A `Makefile`", ["#11"])])
        self.assertNotIn("README.md", release_digest.markdown(d))

    def test_a_rename_out_of_a_watched_place_is_listed(self):
        r = self.repo({".github/workflows/ci.yml": "on: push\n"})
        r.commit("ci: move it (#3)", {".github/workflows/ci.yml": None, "old/ci.yml": "on: push\n"})
        self.assertIn("old/ci.yml", flagged(r.digest(), "Workflows and GitHub configuration"))

    def test_nothing_watched_says_so(self):
        r = self.repo({"README.md": "hi\n"})
        r.commit("docs: words (#2)", {"README.md": "hello\n"})
        d = r.digest()
        self.assertTrue(d.empty)
        text = release_digest.markdown(d)
        self.assertTrue(text.startswith(release_digest.MARKER))
        self.assertIn("Nothing in the watched places changed", text)

    def test_extra_watched_paths(self):
        r = self.repo({"scripts/a.sh": "true\n"})
        r.commit("fix: the script (#4)", {"scripts/a.sh": "false\n"})
        self.assertTrue(r.digest().empty)
        self.assertIn("scripts/a.sh", flagged(r.digest("scripts/"), "Other watched paths"))


GO_TEST = """package x

import "testing"

func TestAdd(t *testing.T) {
\tif add(1, 1) != 2 {
\t\tt.Fatal("add")
\t}
}

func TestSub(t *testing.T) {}
"""

PY_TEST = """import unittest


class T(unittest.TestCase):
    def test_one(self):
        self.assertTrue(True)

    def test_two(self):
        self.assertTrue(True)
"""

JS_TEST = """test('adds', () => {
  expect(1 + 1).toBe(2);
});

it("subtracts", () => {});
"""


class WeakenedTests(Base):
    def base(self) -> Repo:
        return self.repo(
            {
                "x/add_test.go": GO_TEST,
                "tests/test_t.py": PY_TEST,
                "web/add.test.ts": JS_TEST,
                "tests/fixtures/data.json": "{}\n",
                "docs/testing.md": "How we test.\n",
            }
        )

    def weakened(self, r: Repo) -> str:
        return flagged(r.digest(), "Tests weakened")

    def test_a_deleted_test_file(self):
        r = self.base()
        r.commit("test: drop it (#5)", {"x/add_test.go": None})
        d = r.digest()
        self.assertEqual(
            d.sections["Tests weakened"], [("`x/add_test.go`: test file deleted", ["#5"])]
        )

    def test_near_miss_a_deleted_fixture(self):
        r = self.base()
        r.commit("test: tidy", {"tests/fixtures/data.json": None})
        self.assertEqual(self.weakened(r), "")

    def test_a_test_file_renamed_out_of_the_tests(self):
        r = self.base()
        r.commit("refactor: move (#6)", {"x/add_test.go": None, "x/add_cases.go": GO_TEST})
        self.assertIn("renamed out of the test files", self.weakened(r))

    def test_near_miss_a_test_file_renamed_to_another_test_file(self):
        r = self.base()
        r.commit("refactor: move", {"x/add_test.go": None, "x/sum_test.go": GO_TEST})
        self.assertEqual(self.weakened(r), "")

    def test_a_removed_test_in_each_language(self):
        r = self.base()
        r.commit(
            "test: fewer (#7)",
            {
                "x/add_test.go": GO_TEST.replace("func TestSub(t *testing.T) {}\n", ""),
                "tests/test_t.py": PY_TEST.split("    def test_two")[0],
                "web/add.test.ts": JS_TEST.split('it("subtracts"')[0],
            },
        )
        d = r.digest()
        found = dict(d.sections["Tests weakened"])
        self.assertEqual(found["`x/add_test.go`: test removed: `TestSub`"], ["#7"])
        self.assertIn("`tests/test_t.py`: test removed: `test_two`", found)
        self.assertIn("`web/add.test.ts`: test removed: `subtracts`", found)

    def test_near_miss_a_test_moved_to_another_file(self):
        r = self.base()
        r.commit(
            "refactor: split",
            {
                "x/add_test.go": GO_TEST.replace("func TestSub(t *testing.T) {}\n", ""),
                "x/sub_test.go": 'package x\n\nimport "testing"\n\nfunc TestSub(t *testing.T) {}\n',
            },
        )
        self.assertEqual(self.weakened(r), "")

    def test_near_miss_a_test_changed_but_kept(self):
        r = self.base()
        r.commit("test: stricter", {"x/add_test.go": GO_TEST.replace("1, 1) != 2", "2, 2) != 4")})
        self.assertEqual(self.weakened(r), "")

    def test_a_skip_added_in_each_language(self):
        r = self.base()
        r.commit(
            "test: skip some (#8)",
            {
                "x/add_test.go": GO_TEST.replace("{}\n", '{\n\tt.Skip("flaky")\n}\n'),
                "tests/test_t.py": PY_TEST.replace(
                    "    def test_two", "    @unittest.expectedFailure\n    def test_two"
                ),
                "web/add.test.ts": JS_TEST.replace('it("subtracts"', 'it.skip("subtracts"'),
            },
        )
        text = self.weakened(r)
        self.assertIn('`x/add_test.go`: skip or expected failure added: `t.Skip("flaky")`', text)
        self.assertIn("`@unittest.expectedFailure`", text)
        self.assertIn("it.skip(", text)

    def test_near_miss_a_skip_outside_the_tests(self):
        r = self.base()
        r.commit("docs: skipping", {"docs/testing.md": 'Use t.Skip("why") and it.skip(.\n'})
        self.assertEqual(self.weakened(r), "")

    def test_near_miss_a_skip_that_only_moved(self):
        r = self.repo({"a_test.go": 'func TestA(t *testing.T) {\n\tt.Skip("slow")\n}\n'})
        r.commit(
            "refactor: move",
            {"a_test.go": None, "b_test.go": 'func TestA(t *testing.T) {\n\tt.Skip("slow")\n}\n'},
        )
        self.assertEqual(self.weakened(r), "")

    def test_near_miss_a_skip_removed(self):
        r = self.repo({"a_test.go": 'func TestA(t *testing.T) {\n\tt.Skip("slow")\n}\n'})
        r.commit("test: run it", {"a_test.go": "func TestA(t *testing.T) {\n}\n"})
        self.assertEqual(self.weakened(r), "")


class Markdown(Base):
    def test_sections_in_order_and_names_as_code(self):
        r = self.repo({"README.md": "hi\n"})
        r.commit("ci: x (#2)", {".github/workflows/a`b.yml": "on: push\n"})
        r.commit("deps: y (#3)", {"go.mod": "module x\n"})
        text = release_digest.markdown(r.digest())
        self.assertLess(text.index("**Dependencies**"), text.index("**Workflows"))
        # A backtick in a name can't close the code span.
        self.assertIn("`.github/workflows/a'b.yml`", text)
        self.assertIn("Read the list below before you merge", text)


class Range(Base):
    def test_from_the_last_release_not_a_pre_release(self):
        r = self.repo({"README.md": "hi\n"})
        r.commit("feat: more", {"README.md": "more\n"})
        r.git("tag", "v0.2.0-rc.1")
        self.assertEqual(release_digest.last_release(r.path), "v0.1.0")
        r.git("tag", "v0.2.0")
        self.assertEqual(release_digest.last_release(r.path), "v0.2.0")

    def test_no_release_yet_starts_at_the_first_commit(self):
        r = self.repo({"Makefile": "all:\n"})
        r.git("tag", "-d", "v0.1.0")
        since = release_digest.last_release(r.path)
        self.assertEqual(since, release_digest.EMPTY_TREE)
        d = release_digest.digest(since, "HEAD", cwd=r.path)
        self.assertEqual(d.sections["Build and release files"], [("A `Makefile`", ["`chore: start`"])])
        self.assertIn("the first commit", release_digest.markdown(d))


if __name__ == "__main__":
    unittest.main()
