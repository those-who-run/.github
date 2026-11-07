"""The skills (`agent/skills/`) and the agent briefs (`agent/agents/`).

    python3 -m unittest discover agent/tests

These files are prose, so the tests check only what breaks silently: front matter
Claude Code can't read (the skill is then never offered), a name that doesn't match
its folder, a skill that points at one that doesn't exist, and a private value in a
file every repository copies.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
SKILLS = AGENT / "skills"
AGENTS = AGENT / "agents"
README = AGENT / "README.md"

NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
# A skill named in another skill's text, as `twr-something`.
MENTION = re.compile(r"`(twr-[a-z0-9-]+)`")
# What must never be in a public file: private addresses, a home path, an e-mail
# address other than a noreply one. Documentation addresses (192.0.2.x) pass.
PRIVATE = (
    re.compile(r"\b(10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)\b"),
    re.compile(r"/home/[a-z]"),
    re.compile(r"\b[\w.+-]+@(?!users\.noreply\.github\.com)[\w-]+\.[a-z]{2,}\b"),
)
# Names that are commands or identities, not skills.
NOT_SKILLS = {"twr-builder", "twr-release", "twr-review", "twr-fix", "twr-merge", "twr-claude", "twr-agent"}


def front_matter(path: Path) -> tuple[dict[str, str], str]:
    """The `key: value` front matter of a Markdown file, and the body after it."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: no front matter")
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        raise ValueError(f"{path}: front matter is not closed")
    fields = {}
    for line in head.splitlines():
        key, colon, value = line.partition(":")
        if not colon or not key.strip() or key != key.strip():
            raise ValueError(f"{path}: front matter line is not `key: value`: {line!r}")
        fields[key] = value.strip()
    return fields, body


def skill_dirs() -> list[Path]:
    return sorted(p for p in SKILLS.iterdir() if p.is_dir())


class Skills(unittest.TestCase):
    def test_there_are_skills(self):
        self.assertTrue(skill_dirs())

    def test_each_folder_holds_a_skill_with_its_own_name(self):
        for folder in skill_dirs():
            with self.subTest(skill=folder.name):
                fields, body = front_matter(folder / "SKILL.md")
                self.assertEqual(fields.get("name"), folder.name)
                self.assertRegex(folder.name, NAME)
                self.assertTrue(folder.name.startswith("twr-"), "a prefix keeps them apart from a user's own skills")
                self.assertTrue(body.strip())

    def test_each_description_says_when_to_use_it(self):
        # The description is all Claude Code reads when it decides whether to offer
        # the skill, and it is cut off when long.
        for folder in skill_dirs():
            with self.subTest(skill=folder.name):
                description = front_matter(folder / "SKILL.md")[0].get("description", "")
                self.assertGreater(len(description), 40)
                self.assertLess(len(description), 600)
                self.assertIn("Use it", description)

    def test_a_skill_names_only_skills_that_exist(self):
        known = {p.name for p in skill_dirs()} | {p.stem for p in AGENTS.glob("*.md")}
        for path in sorted(SKILLS.glob("*/SKILL.md")) + sorted(AGENTS.glob("*.md")):
            with self.subTest(file=str(path.relative_to(AGENT))):
                named = set(MENTION.findall(path.read_text(encoding="utf-8"))) - NOT_SKILLS
                self.assertEqual(named - known, set())

    def test_the_loop_chains_the_others(self):
        text = (SKILLS / "twr-dev" / "SKILL.md").read_text(encoding="utf-8")
        for name in ("twr-proof", "twr-test-audit", "twr-deslop", "twr-pr-ready", "twr-fix-review"):
            with self.subTest(skill=name):
                self.assertIn(f"`{name}`", text)


class Agents(unittest.TestCase):
    def test_each_brief_has_its_own_name_and_tools(self):
        briefs = sorted(AGENTS.glob("*.md"))
        self.assertTrue(briefs)
        for path in briefs:
            with self.subTest(agent=path.name):
                fields, body = front_matter(path)
                self.assertEqual(fields.get("name"), path.stem)
                self.assertGreater(len(fields.get("description", "")), 40)
                self.assertTrue(fields.get("tools"))
                self.assertTrue(body.strip())

    def test_the_reviewer_cannot_edit_or_fetch(self):
        # It writes its three files and nothing else; what it may run is the
        # runner's allow list, not this line.
        tools = {t.strip() for t in front_matter(AGENTS / "twr-reviewer.md")[0]["tools"].split(",")}
        self.assertEqual(tools, {"Read", "Grep", "Glob", "Write", "Bash"})

    def test_the_reviewer_names_every_verdict_and_lane(self):
        text = (AGENTS / "twr-reviewer.md").read_text(encoding="utf-8")
        for word in ("ready", "nits", "changes", "blocked", "skipped", "light", "standard", "sensitive"):
            with self.subTest(word=word):
                self.assertIn(f"`{word}`", text)
        self.assertIn("<!-- twr-review sha=<head> kind=<A-D> lane=<lane> -->", text)


class Public(unittest.TestCase):
    def test_no_private_values(self):
        for path in sorted(SKILLS.rglob("*.md")) + sorted(AGENTS.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for pattern in PRIVATE:
                with self.subTest(file=str(path.relative_to(AGENT)), pattern=pattern.pattern):
                    self.assertIsNone(pattern.search(text))

    def test_the_readme_lists_each_one(self):
        text = README.read_text(encoding="utf-8")
        for name in [p.name for p in skill_dirs()] + [p.stem for p in AGENTS.glob("*.md")]:
            with self.subTest(name=name):
                # assertIn would print the whole README on a miss.
                self.assertTrue(f"`{name}`" in text, f"`{name}` is not in agent/README.md")


class FrontMatter(unittest.TestCase):
    def check(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text(text, encoding="utf-8")
            return front_matter(path)

    def test_it_reads_keys_and_body(self):
        fields, body = self.check("---\nname: twr-x\ndescription: a: b\n---\n\n# Title\n")
        self.assertEqual(fields, {"name": "twr-x", "description": "a: b"})
        self.assertEqual(body.strip(), "# Title")

    def test_a_file_without_front_matter_is_refused(self):
        with self.assertRaises(ValueError):
            self.check("# Title\n")

    def test_unclosed_front_matter_is_refused(self):
        with self.assertRaises(ValueError):
            self.check("---\nname: twr-x\n")


if __name__ == "__main__":
    unittest.main()
