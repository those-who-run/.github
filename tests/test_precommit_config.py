"""Checks on .pre-commit-config.yaml that no linter makes.

Every external hook runs code on the maintainer's machine at each commit, so it is
pinned to a commit SHA with the tag in a `frozen:` comment, as in v1
(tests/test_claude_hooks.py::test_external_hooks_are_pinned_to_a_commit). zizmor and
actionlint don't read this file, and Dependabot keeps a pin but doesn't stop a hand
edit to `rev: v6.0.0`.

Standard library only. CI's `test` job runs it, and so does the `hook-pins` commit
hook whenever the config changes:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".pre-commit-config.yaml"

# `repo: local` and `repo: meta` have no rev; every other repo must have one.
REPO = re.compile(r"^\s*-\s*repo:\s*(\S+)\s*$")
REV = re.compile(r"^\s*rev:\s*(.*)$")
# The tag as the repository names it: most start with `v`, Renovate's hooks don't.
PINNED = re.compile(r"[0-9a-f]{40}\s+# frozen: v?\d+(\.\d+)+")


def bad_pins(text: str) -> list[str]:
    """What is wrong with the external hooks' revs in a pre-commit config, if anything."""
    problems = []
    repo = None
    seen_rev = True
    for number, line in enumerate(text.splitlines(), 1):
        if m := REPO.match(line):
            if not seen_rev:
                problems.append(f"{repo}: no rev")
            repo = m.group(1)
            seen_rev = repo in ("local", "meta")
        elif m := REV.match(line):
            seen_rev = True
            if not PINNED.fullmatch(m.group(1).strip()):
                problems.append(
                    f"line {number} ({repo}): rev must be a 40-character commit SHA "
                    f"followed by `# frozen: <tag>`, not {m.group(1).strip()!r}"
                )
    if not seen_rev:
        problems.append(f"{repo}: no rev")
    return problems


class ExternalHooksArePinned(unittest.TestCase):
    def test_this_config(self):
        text = CONFIG.read_text()
        self.assertTrue(any(REV.match(x) for x in text.splitlines()), "no rev found")
        self.assertEqual(bad_pins(text), [])

    def test_a_tag_is_refused(self):
        self.assertTrue(bad_pins("repos:\n  - repo: https://x/y\n    rev: v6.0.0\n"))

    def test_a_sha_without_frozen_is_refused(self):
        self.assertTrue(bad_pins(f"repos:\n  - repo: https://x/y\n    rev: {'a' * 40}\n"))

    def test_a_short_sha_is_refused(self):
        self.assertTrue(bad_pins("repos:\n  - repo: https://x/y\n    rev: 3e8a870  # frozen: v6.0.0\n"))

    def test_a_missing_rev_is_refused(self):
        self.assertTrue(bad_pins("repos:\n  - repo: https://x/y\n    hooks: []\n"))

    def test_a_pinned_rev_and_local_pass(self):
        text = (
            "repos:\n"
            f"  - repo: https://x/y\n    rev: {'a' * 40}  # frozen: v0.11.0.1\n"
            "  - repo: local\n    hooks: []\n"
        )
        self.assertEqual(bad_pins(text), [])

    def test_a_tag_without_v_passes(self):
        text = f"repos:\n  - repo: https://x/y\n    rev: {'a' * 40}  # frozen: 44.148.3\n"
        self.assertEqual(bad_pins(text), [])

    def test_a_branch_in_the_frozen_comment_is_refused(self):
        self.assertTrue(bad_pins(f"repos:\n  - repo: https://x/y\n    rev: {'a' * 40}  # frozen: main\n"))


if __name__ == "__main__":
    unittest.main()
