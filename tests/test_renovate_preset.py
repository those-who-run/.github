"""The decided values in default.json, the Renovate preset every v2 repository extends.

renovate-config-validator (a commit hook) checks that the preset is valid Renovate
config, but not what it decides, and it passes a duration it can't read, such as
`"minimumReleaseAge": "seven days"`. These tests hold the decisions from
those-who-run/paddock#22: one PR per repository per week, a cooldown of at least 7
days, security fixes at once, no auto-merge, actions pinned by digest, and the
gomod and pre-commit managers on. Changing one is a decision, made in the open
with the test.

Standard library only. CI's `test` job runs it, and so does the `hook-pins`
commit hook:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESET = ROOT / "default.json"

MIN_COOLDOWN_DAYS = 7
DURATION = re.compile(r"^(\d+) (day|days|week|weeks)$")


def days(duration: str) -> int:
    """A Renovate duration in whole days. Only days and weeks: a cooldown in
    hours or minutes is too short to be meant, and fails here."""
    m = DURATION.fullmatch(duration)
    if not m:
        raise ValueError(f"not a duration in days or weeks: {duration!r}")
    n = int(m.group(1))
    return n * 7 if m.group(2).startswith("week") else n


def walk(value, path="$"):
    """Every (path, key, value) in a JSON document."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield path, key, child
            yield from walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from walk(child, f"{path}[{i}]")


class Durations(unittest.TestCase):
    def test_days_and_weeks(self):
        self.assertEqual(days("7 days"), 7)
        self.assertEqual(days("1 day"), 1)
        self.assertEqual(days("2 weeks"), 14)

    def test_anything_else_is_refused(self):
        for text in ("seven days", "12 hours", "7days", "", "7 days ago"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                days(text)


class RenovatePreset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.preset = json.loads(PRESET.read_text())

    def test_one_pr_per_repository_per_week(self):
        p = self.preset
        self.assertIn("group:all", p["extends"])
        # Majors would otherwise get a PR of their own, even inside a group.
        self.assertIs(p["separateMajorMinor"], False)
        self.assertIs(p["separateMultipleMajor"], False)
        # Lock file maintenance opens a PR of its own on its own schedule.
        self.assertIs(p["lockFileMaintenance"]["enabled"], False)
        # Weekly: cron entries on one day of the week, any day of any month.
        self.assertTrue(p["schedule"])
        weekdays = set()
        for entry in p["schedule"]:
            minute, _hour, dom, month, dow = entry.split()
            self.assertEqual((minute, dom, month), ("*", "*", "*"), entry)
            self.assertRegex(dow, r"^[0-6]$", entry)
            weekdays.add(dow)
        self.assertEqual(len(weekdays), 1, p["schedule"])

    def test_cooldown_is_at_least_seven_days(self):
        p = self.preset
        self.assertGreaterEqual(days(p["minimumReleaseAge"]), MIN_COOLDOWN_DAYS)
        # `strict` holds a version until it is old enough; `flexible` or `none`
        # would open a PR for one still in its cooldown.
        self.assertEqual(p["internalChecksFilter"], "strict")
        # No rule shortens it; only security fixes skip it (below).
        for path, key, value in walk(p):
            if key == "minimumReleaseAge" and not path.startswith("$.vulnerabilityAlerts"):
                with self.subTest(path=path):
                    self.assertGreaterEqual(days(value), MIN_COOLDOWN_DAYS)
            if key == "internalChecksFilter":
                with self.subTest(path=path):
                    self.assertEqual(value, "strict")

    def test_security_fixes_open_at_once(self):
        v = self.preset["vulnerabilityAlerts"]
        self.assertIs(v["enabled"], True)
        self.assertEqual(v["schedule"], [])
        self.assertIsNone(v["minimumReleaseAge"])
        self.assertIsNone(v["groupName"])
        self.assertIs(v["automerge"], False)

    def test_nothing_is_auto_merged(self):
        p = self.preset
        self.assertIs(p["automerge"], False)
        self.assertIs(p["platformAutomerge"], False)
        for path, key, value in walk(p):
            if key in ("automerge", "platformAutomerge"):
                with self.subTest(path=f"{path}.{key}"):
                    self.assertIs(value, False)
        self.assertFalse(
            [x for x in p.get("extends", []) if "automerge" in x.lower()],
            "a preset that turns auto-merge on",
        )

    def test_actions_are_pinned_by_digest_with_the_tag(self):
        self.assertIn("helpers:pinGitHubActionDigestsToSemver", self.preset["extends"])

    def test_managers(self):
        p = self.preset
        # Renovate turns the pre-commit manager off unless it is asked for.
        self.assertIs(p["pre-commit"]["enabled"], True)
        self.assertIn("gomodTidy", p["postUpdateOptions"])
        # A list of managers would turn off every manager it leaves out.
        self.assertNotIn("enabledManagers", p)
        for path, key, value in walk(p):
            if isinstance(value, dict) and value.get("enabled") is False:
                with self.subTest(path=f"{path}.{key}"):
                    self.assertEqual(key, "lockFileMaintenance")

    def test_commits_are_deps(self):
        p = self.preset
        self.assertEqual(p["semanticCommits"], "enabled")
        self.assertEqual(p["semanticCommitType"], "deps")
        self.assertIsNone(p["semanticCommitScope"])

    def test_dependency_dashboard(self):
        # It lists the updates still in their cooldown, which `strict` holds back.
        self.assertIs(self.preset["dependencyDashboard"], True)


if __name__ == "__main__":
    unittest.main()
