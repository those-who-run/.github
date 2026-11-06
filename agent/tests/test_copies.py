"""The manifest of what each repository copies from here (`agent/copies.json`).

    python3 -m unittest discover agent/tests

The manifest maps a file of this repository to where its copy lives in every other
one. Whatever copies or compares the files reads it, at the release a repository
pins, so a file missing from it is silently never copied, and a wrong destination
is written in every repository at once. These tests keep it complete and its
destinations inside `.claude/`.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
ROOT = AGENT.parent
MANIFEST = AGENT / "copies.json"

# The guard's two copies, as every repository holds them since before the manifest.
# A reader given a release with no manifest copies exactly these.
GUARD = {
    "agent/guard.py": ".claude/hooks/guard.py",
    "agent/settings.json": ".claude/settings.json",
}
# Everything under these is copied, to the same path under `.claude/`.
COPIED_WHOLE = ("agent/skills/", "agent/agents/")


class Pairs(list):
    """A JSON object as its pairs in the file's order, so a repeated key and the
    order of the keys can be checked; a dict would hide both."""


def pairs() -> Pairs:
    return json.loads(MANIFEST.read_text(encoding="utf-8"), object_pairs_hook=Pairs)


def copies() -> dict[str, str]:
    return dict(pairs())


def files_under(prefix: str) -> set[str]:
    return {p.relative_to(ROOT).as_posix() for p in (ROOT / prefix).rglob("*") if p.is_file() or p.is_symlink()}


def copied_whole() -> set[str]:
    return set().union(*(files_under(prefix) for prefix in COPIED_WHOLE))


class Manifest(unittest.TestCase):
    def test_it_is_an_object_of_paths_with_sorted_keys(self):
        listed = pairs()
        self.assertIsInstance(listed, Pairs, "agent/copies.json must be a JSON object")
        for source, destination in listed:
            with self.subTest(source=source):
                self.assertIsInstance(destination, str)
        keys = [source for source, _ in listed]
        self.assertEqual(len(keys), len(set(keys)), "a source is listed twice")
        self.assertEqual(keys, sorted(keys), "keep the keys sorted, so a diff shows only what changed")

    def test_no_two_sources_share_a_destination(self):
        destinations = list(copies().values())
        self.assertEqual(len(destinations), len(set(destinations)))

    def test_every_source_is_a_regular_file_under_agent(self):
        for source in copies():
            with self.subTest(source=source):
                self.assertTrue(source.startswith("agent/"))
                self.assertNotIn("..", source.split("/"))
                path = ROOT / source
                self.assertFalse(path.is_symlink(), "a symbolic link has no bytes of its own to copy")
                self.assertTrue(path.is_file())

    def test_every_skill_and_agent_file_is_listed(self):
        # A skill added without its line would pass every other test and never
        # reach a repository.
        self.assertTrue(copied_whole())
        self.assertEqual(copied_whole() - set(copies()), set())

    def test_the_guard_entries_are_as_every_repository_has_them(self):
        listed = copies()
        for source, destination in GUARD.items():
            with self.subTest(source=source):
                self.assertEqual(listed.get(source), destination)

    def test_a_skill_or_agent_file_keeps_its_path_under_claude(self):
        for source, destination in copies().items():
            if source.startswith(COPIED_WHOLE):
                with self.subTest(source=source):
                    self.assertEqual(destination, ".claude/" + source[len("agent/"):])

    def test_every_destination_stays_under_claude(self):
        for source, destination in copies().items():
            with self.subTest(source=source):
                self.assertTrue(destination.startswith(".claude/"))
                self.assertNotIn("\\", destination)
                for part in destination.split("/"):
                    self.assertNotIn(part, ("", ".", ".."))

    def test_nothing_else_from_agent_is_listed(self):
        # lanes.json, lanes.py, guard.example.json, the README and the tests stay
        # here: a repository's guard.json is its own, and the rest is read from a
        # release of this repository.
        self.assertEqual(set(copies()), set(GUARD) | copied_whole())


if __name__ == "__main__":
    unittest.main()
