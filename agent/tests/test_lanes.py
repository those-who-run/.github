"""The lane classifier (`agent/lanes.py`) and its patterns (`agent/lanes.json`).

    python3 -m unittest discover agent/tests

The lane decides how closely a pull request is read, so each rule has an example
that lands in the lane and a near miss that doesn't. The cases double as the
examples for anything that ports the classifier: the same input must give the same
lane.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

AGENT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("agent_lanes", AGENT / "lanes.py")
assert _spec and _spec.loader
lanes = importlib.util.module_from_spec(_spec)
sys.modules["agent_lanes"] = lanes
_spec.loader.exec_module(lanes)

CONFIG = lanes.load()
ECU = "those-who-run/ecu"


def lane(changes, repo=ECU):
    return lanes.classify(repo, changes, CONFIG)


class Patterns(unittest.TestCase):
    def match(self, pattern, path):
        return bool(lanes.compile_pattern(pattern).fullmatch(path))

    def test_a_star_stays_in_one_directory(self):
        self.assertTrue(self.match("docs/*.md", "docs/board.md"))
        self.assertFalse(self.match("docs/*.md", "docs/adr/0001.md"))

    def test_a_double_star_crosses_directories(self):
        self.assertTrue(self.match("deploy/**", "deploy/base/nats/kustomization.yaml"))
        self.assertFalse(self.match("deploy/**", "docs/deploy/readme.txt"))

    def test_a_leading_double_star_also_matches_at_the_root(self):
        self.assertTrue(self.match("**/go.mod", "go.mod"))
        self.assertTrue(self.match("**/go.mod", "tools/go.mod"))
        self.assertFalse(self.match("**/go.mod", "tools/go.mod.bak"))

    def test_the_whole_path_must_match(self):
        self.assertFalse(self.match("AGENTS.md", "docs/AGENTS.md.txt"))
        self.assertFalse(self.match("**/Dockerfile", "docs/Dockerfile-notes.md"))

    def test_a_dot_is_a_dot(self):
        self.assertFalse(self.match(".github/**", "xgithub/workflows/ci.yml"))

    def test_bad_patterns_are_refused(self):
        for pattern in ("", "/etc/passwd", "a\\b"):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                lanes.compile_pattern(pattern)


class Sensitive(unittest.TestCase):
    def test_each_sensitive_kind(self):
        for path in (
            ".github/workflows/ci.yml",
            ".claude/hooks/guard.py",
            "AGENTS.md",
            "CLAUDE.md",
            ".github/CODEOWNERS",
            "docs/adr/0015-something.md",
            "proto/twr/v1/frame.proto",
            "subjects.yaml",
            "deploy/base/ecu/deployment.yaml",
            "Dockerfile",
            "go.mod",
            "tools/go.mod",
            "pyproject.toml",
            "web/package.json",
            "internal/store/migrate.go",
            "renovate.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(lane([("M", path)]), "sensitive")

    def test_one_sensitive_file_makes_the_change_sensitive(self):
        self.assertEqual(lane([("M", "README.md"), ("M", "race/race.go"), ("M", "go.mod")]), "sensitive")

    def test_sensitive_wins_over_docs(self):
        # AGENTS.md and an ADR are Markdown, and still the maintainer's to read.
        self.assertEqual(lane([("M", "AGENTS.md")]), "sensitive")
        self.assertEqual(lane([("A", "docs/adr/0015-something.md")]), "sensitive")

    def test_a_lock_file_alone_is_not_sensitive(self):
        self.assertEqual(lane([("M", "go.sum")]), "standard")
        self.assertEqual(lane([("M", "uv.lock")]), "standard")

    def test_near_misses(self):
        self.assertEqual(lane([("M", "internal/deploy/render.go")]), "standard")
        self.assertEqual(lane([("M", "internal/proto/decode.go")]), "standard")

    def test_a_repository_adds_its_own(self):
        self.assertEqual(lane([("M", "README.md")], repo="those-who-run/.github"), "sensitive")
        self.assertEqual(lane([("M", "README.md.jinja")], repo="those-who-run/chassis"), "sensitive")
        self.assertEqual(lane([("M", "README.md")], repo="those-who-run/chassis"), "light")

    def test_the_repository_name_ignores_case(self):
        self.assertEqual(lane([("M", "README.md")], repo="Those-Who-Run/.GitHub"), "sensitive")


class Light(unittest.TestCase):
    def test_docs_only(self):
        self.assertEqual(lane([("M", "README.md"), ("A", "docs/board.md"), ("D", "docs/old.md")]), "light")

    def test_tests_added_only(self):
        self.assertEqual(lane([("A", "race/grid_test.go"), ("A", "race/testdata/grid.jsonl.gz")]), "light")
        self.assertEqual(lane([("A", "tests/test_links.py"), ("A", "web/src/laps.test.tsx")]), "light")

    def test_docs_and_added_tests_together(self):
        self.assertEqual(lane([("M", "README.md"), ("A", "race/grid_test.go")]), "light")

    def test_an_edited_test_is_not_light(self):
        self.assertEqual(lane([("M", "race/grid_test.go")]), "standard")

    def test_a_deleted_test_is_not_light(self):
        self.assertEqual(lane([("D", "race/grid_test.go")]), "standard")

    def test_a_renamed_test_is_not_light(self):
        changes = lanes.parse_name_status("R100\trace/grid_test.go\trace/start_test.go\n")
        self.assertEqual(lane(changes), "standard")

    def test_code_beside_docs_is_standard(self):
        self.assertEqual(lane([("M", "README.md"), ("M", "race/race.go")]), "standard")


class Standard(unittest.TestCase):
    def test_component_code(self):
        self.assertEqual(lane([("M", "race/race.go"), ("A", "race/grid_test.go")]), "standard")

    def test_an_empty_change_is_not_light(self):
        self.assertEqual(lane([]), "standard")


class NameStatus(unittest.TestCase):
    def test_plain_lines(self):
        self.assertEqual(
            lanes.parse_name_status("M\tREADME.md\nA\trace/grid_test.go\n\n"),
            [("M", "README.md"), ("A", "race/grid_test.go")],
        )

    def test_a_rename_gives_both_paths(self):
        self.assertEqual(
            lanes.parse_name_status("R087\tdocs/a.md\tproto/a.md\n"),
            [("R", "docs/a.md"), ("R", "proto/a.md")],
        )

    def test_a_rename_into_a_sensitive_path_is_sensitive(self):
        self.assertEqual(lane(lanes.parse_name_status("R087\tdocs/a.md\tproto/a.md\n")), "sensitive")

    def test_other_text_is_refused(self):
        for text in ("README.md\n", "M\n", "X\tREADME.md\n", "R100\tonly-one\n", "M\ta\tb\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                lanes.parse_name_status(text)


class Config(unittest.TestCase):
    def load(self, config):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lanes.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            return lanes.load(path)

    def test_the_committed_config_loads(self):
        self.assertEqual(sorted(k for k in CONFIG if k in lanes.KEYS), sorted(lanes.KEYS))

    def test_the_guard_and_the_workflows_are_sensitive_everywhere(self):
        # These two are what the ladder itself stands on.
        for path in (".claude/settings.json", ".github/workflows/release.yml"):
            self.assertEqual(lane([("M", path)], repo="those-who-run/anything"), "sensitive")

    def test_a_missing_list_is_refused(self):
        with self.assertRaises(ValueError):
            self.load({"sensitive": [".github/**"], "docs": ["**/*.md"]})

    def test_a_bad_pattern_is_refused(self):
        with self.assertRaises(ValueError):
            self.load({"sensitive": ["/abs"], "docs": ["**/*.md"], "tests": ["tests/**"]})

    def test_a_repository_may_only_add_sensitive_patterns(self):
        # A repository can't declare its own code light.
        with self.assertRaises(ValueError):
            self.load({
                "sensitive": [".github/**"], "docs": ["**/*.md"], "tests": ["tests/**"],
                "repositories": {"those-who-run/ecu": {"docs": ["**"]}},
            })


class CommandLine(unittest.TestCase):
    def run_main(self, stdin, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin)):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = lanes.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_it_prints_the_lane(self):
        code, out, _ = self.run_main("M\tREADME.md\n", "--repo", ECU)
        self.assertEqual((code, out), (0, "light\n"))

    def test_bad_input_exits_2_and_prints_no_lane(self):
        code, out, err = self.run_main("nonsense\n", "--repo", ECU)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("name-status", err)


if __name__ == "__main__":
    unittest.main()
