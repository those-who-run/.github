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
        "Test, coverage and budget configuration": (
            ["conftest.py", "tests/conftest.py", "pytest.ini", "tox.ini", ".coveragerc",
             "codecov.yml", ".github/codecov.yml", "web/jest.config.js", "vitest.config.ts",
             "web/playwright.config.ts", "karma.conf.js", ".mocharc.yml", ".mocharc",
             "web/bundle-budget.json"],
            ["docs/conftest.md", "pytest.ini.md", "src/jest.ts", "budget.json"],
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

    def test_a_test_moved_to_another_file_is_listed(self):
        # Matched by file and name, so a move shows as a removal; a look at the PR
        # tells it from a lost test.
        r = self.base()
        r.commit(
            "refactor: split (#9)",
            {
                "x/add_test.go": GO_TEST.replace("func TestSub(t *testing.T) {}\n", ""),
                "x/sub_test.go": 'package x\n\nimport "testing"\n\nfunc TestSub(t *testing.T) {}\n',
            },
        )
        self.assertEqual(self.weakened(r), "`x/add_test.go`: test removed: `TestSub`")

    def test_a_removed_test_is_not_hidden_by_one_of_the_same_name_elsewhere(self):
        r = self.repo(
            {
                "a/test_x.py": "def test_parse():\n    assert True\n",
                "b/test_y.py": "def test_other():\n    assert True\n",
            }
        )
        r.commit(
            "test: shuffle (#10)",
            {
                "a/test_x.py": "def test_kept():\n    assert True\n",
                "b/test_y.py": "def test_other():\n    assert True\n\n\n"
                "def test_parse():\n    assert 1\n",
            },
        )
        self.assertEqual(self.weakened(r), "`a/test_x.py`: test removed: `test_parse`")

    def test_a_skip_added_is_not_hidden_by_the_same_line_removed_elsewhere(self):
        skipped = 'func TestA(t *testing.T) {\n\tt.Skip("slow")\n}\n'
        r = self.repo({"a_test.go": skipped, "b_test.go": "func TestB(t *testing.T) {\n}\n"})
        r.commit(
            "test: swap (#11)",
            {
                "a_test.go": "func TestA(t *testing.T) {\n}\n",
                "b_test.go": 'func TestB(t *testing.T) {\n\tt.Skip("slow")\n}\n',
            },
        )
        self.assertIn("`b_test.go`: skip or expected failure added", self.weakened(r))

    def test_near_miss_a_renamed_test_file_keeps_its_tests(self):
        r = self.base()
        r.commit(
            "refactor: rename",
            {"x/add_test.go": None, "x/sum_test.go": GO_TEST.replace("1, 1) != 2", "2, 2) != 4")},
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


    def test_a_rust_ignore_in_an_integration_test(self):
        r = self.repo({"tests/it.rs": "#[test]\nfn works() {}\n"})
        r.commit("test: later (#12)", {"tests/it.rs": '#[test]\n#[ignore = "slow"]\nfn works() {}\n'})
        self.assertIn('`tests/it.rs`: skip or expected failure added: `#[ignore = "slow"]`', self.weakened(r))

    def test_ruby_a_removed_example_and_an_xit(self):
        spec = 'describe "x" do\n  it "adds" do\n  end\n\n  it "subtracts" do\n  end\nend\n'
        r = self.repo({"spec/x_spec.rb": spec})
        r.commit(
            "test: fewer (#13)",
            {"spec/x_spec.rb": 'describe "x" do\n  xit "adds" do\n  end\nend\n'},
        )
        text = self.weakened(r)
        self.assertIn("`spec/x_spec.rb`: test removed: `subtracts`", text)
        self.assertIn("`spec/x_spec.rb`: skip or expected failure added: `xit \"adds\" do`", text)

    def test_near_miss_a_skip_in_a_comment(self):
        r = self.base()
        r.commit(
            "docs: a comment",
            {
                "tests/test_t.py": PY_TEST + "# Use @pytest.mark.skip only with a reason.\n",
                "web/add.test.ts": JS_TEST + "// it.skip(...) needs an issue\n",
            },
        )
        self.assertEqual(self.weakened(r), "")


TS_TESTS = 'test("works", () => {});\n'


class ConditionalSkips(Base):
    """Playwright's conditional test.skip inside a block skips by design (v1's #373)."""

    def change(self, added: str) -> str:
        r = self.repo({"web/a.test.ts": TS_TESTS})
        r.commit("test: skips (#14)", {"web/a.test.ts": TS_TESTS + added})
        return flagged(r.digest(), "Tests weakened")

    def test_a_conditional_skip_in_a_body_is_not_listed(self):
        body = (
            'test("drags", async ({ page }, testInfo) => {\n'
            '  test.skip(testInfo.project.name !== "phone", "touch: the phone project");\n'
            "  test.skip(!process.env.LIVE, 'needs the live source');\n"
            "  test.fixme(isWebKit, 'webkit drag');\n"
            "});\n"
        )
        self.assertEqual(self.change(body), "")

    def test_an_unconditional_skip_is_listed(self):
        for line in (
            '  test.skip(true, "always skipped");',  # a condition that is always true
            'test.skip(isPhone, "at the top level");',  # not inside a block
            '  test.skip("a title", async () => {});',  # a title: the test is skipped
            "  test.skip();",  # bare
        ):
            with self.subTest(line=line):
                self.assertEqual(
                    self.change(line + "\n"),
                    f"`web/a.test.ts`: skip or expected failure added: {release_digest.code(line.strip())}",
                )


class Limits(Base):
    """A coverage floor or budget that changes value shows old and new."""

    def limits(self, before: dict[str, str], after: dict[str, str]) -> str:
        r = self.repo(before)
        r.commit("chore: lower it (#15)", after)
        return flagged(r.digest(), "Coverage floors and budgets")

    def test_each_kind_shows_old_and_new(self):
        cases = {
            "go coverage-floor": (
                ".github/workflows/ci.yml",
                "jobs:\n  go:\n    with:\n      coverage-floor: 80\n",
                "jobs:\n  go:\n    with:\n      coverage-floor: 70\n",
                "`coverage-floor` 80 -> 70",
            ),
            "pyproject.toml": (
                "pyproject.toml",
                "[tool.coverage.report]\nfail_under = 90\n",
                "[tool.coverage.report]\nfail_under = 85\n",
                "`fail_under` 90 -> 85",
            ),
            "setup.cfg": (
                "setup.cfg",
                "[coverage:report]\nfail_under = 90\n",
                "[coverage:report]\nfail_under = 60\n",
                "`fail_under` 90 -> 60",
            ),
            ".coveragerc": (
                ".coveragerc",
                "[report]\nfail_under = 80\n",
                "[report]\nfail_under = 10\n",
                "`fail_under` 80 -> 10",
            ),
            "tox.ini": (
                "tox.ini",
                "[coverage:report]\nfail_under = 75\n",
                "[coverage:report]\nfail_under = 50\n",
                "`fail_under` 75 -> 50",
            ),
            "pytest.ini": (
                "pytest.ini",
                "[pytest]\naddopts = --cov --cov-fail-under=95\n",
                "[pytest]\naddopts = --cov\n",
                "`--cov-fail-under` 95 -> unset",
            ),
            "jest.config.js": (
                "web/jest.config.js",
                "module.exports = {\n  coverageThreshold: { global: { lines: 90, branches: 80 } },\n};\n",
                "module.exports = {\n  coverageThreshold: { global: { lines: 70, branches: 80 } },\n};\n",
                "`lines` 90 -> 70",
            ),
            "package.json": (
                "package.json",
                '{"jest": {"coverageThreshold": {"global": {"statements": 90}}}}\n',
                '{"jest": {"coverageThreshold": {"global": {"statements": 50}}}}\n',
                "`coverageThreshold/global/statements` 90 -> 50",
            ),
            "vitest.config.ts": (
                "vitest.config.ts",
                "export default {\n  test: { coverage: { thresholds: { functions: 85 } } },\n};\n",
                "export default {\n  test: { coverage: { thresholds: { functions: 40 } } },\n};\n",
                "`functions` 85 -> 40",
            ),
            "bundle-budget.json": (
                "web/bundle-budget.json",
                '{"entry": 118955, "routes": {"src/a.tsx": 5000}}\n',
                '{"entry": 150000, "routes": {"src/a.tsx": 5000}}\n',
                "`entry` 118955 -> 150000",
            ),
        }
        for kind, (path, before, after, change) in cases.items():
            with self.subTest(kind=kind):
                self.assertEqual(
                    self.limits({path: before}, {path: after}), f"`{path}`: {change}"
                )

    def test_near_miss_a_file_changed_with_its_floor_kept(self):
        before = "[tool.coverage.report]\nfail_under = 90\n"
        self.assertEqual(
            self.limits({"pyproject.toml": before}, {"pyproject.toml": before + "show_missing = true\n"}),
            "",
        )

    def test_a_new_floor_and_a_removed_one(self):
        self.assertEqual(
            self.limits({"README.md": "x\n"}, {".coveragerc": "[report]\nfail_under = 80\n"}),
            "`.coveragerc`: `fail_under` unset -> 80",
        )

    def test_the_test_configuration_is_listed_whatever_changed(self):
        r = self.repo({"README.md": "x\n"})
        r.commit("test: collect less (#16)", {"conftest.py": 'collect_ignore = ["b"]\n'})
        r.commit("ci: codecov (#17)", {"codecov.yml": "coverage:\n  status: off\n"})
        r.commit("test: playwright (#18)", {"web/playwright.config.ts": "export default {};\n"})
        self.assertEqual(
            r.digest().sections["Test, coverage and budget configuration"],
            [
                ("A `codecov.yml`", ["#17"]),
                ("A `conftest.py`", ["#16"]),
                ("A `web/playwright.config.ts`", ["#18"]),
            ],
        )


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


    def test_a_long_digest_is_truncated_under_githubs_limit(self):
        d = release_digest.Digest("v0.1.0", "HEAD")
        for i in range(3000):
            d.add("Dependencies", release_digest.code(f"svc{i:04}/" + "x" * 40 + "/go.sum"), ["#1"])
        text = release_digest.markdown(d)
        self.assertLess(len(text), 65_536)
        self.assertIn("**Truncated:**", text)
        shown = text.count("\n- ")
        self.assertIn(f"{3000 - shown} more entries are not shown", text)
        self.assertTrue(text.startswith(release_digest.MARKER))

    def test_a_short_digest_is_not_truncated(self):
        d = release_digest.Digest("v0.1.0", "HEAD")
        d.add("Dependencies", "`go.sum`", ["#1"])
        self.assertNotIn("Truncated", release_digest.markdown(d))


class Batching(Base):
    def test_one_git_log_for_the_changed_files(self):
        r = self.repo({f"f{i}.txt": "x\n" for i in range(20)})
        r.commit("chore: many (#2)", {f"f{i}.txt": "y\n" for i in range(20)} | {"go.mod": "module x\n"})
        calls = []
        real = release_digest._git

        def counting(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        with mock.patch.object(release_digest, "_git", counting):
            d = r.digest()
        self.assertEqual(d.sections["Dependencies"], [("A `go.mod`", ["#2"])])
        self.assertEqual(sum(1 for a in calls if a[0] == "log"), 1)


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
