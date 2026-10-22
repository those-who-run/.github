"""The commit types the commit hooks allow are the ones release-please knows.

A type the hook allows but release-please-config.json has no changelog section for
merges and then vanishes from the changelog; a section the hook refuses is dead.
Lifted from v1 (tests/test_claude_hooks.py::test_commit_types_are_release_pleases),
standard library only. The functions take the files' text, so the repository
template can run the same check on its own config. CI's `test` job runs it:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HOOK_ID = re.compile(r"^\s*-\s*id:\s*conventional-pre-commit\s*$")
NEXT_HOOK = re.compile(r"^\s*-\s*(id|repo):")
ARGS = re.compile(r"^(\s*)args:\s*(.*)$")


def hook_types(precommit: str) -> list[str]:
    """The conventional-pre-commit hook's `args`: the types it allows. Flow style
    (`[feat, fix]`, over several lines if need be) or block style (`- feat`)."""
    lines = precommit.splitlines()
    starts = [i for i, line in enumerate(lines) if HOOK_ID.match(line)]
    if len(starts) != 1:
        raise ValueError(f"expected one conventional-pre-commit hook, found {len(starts)}")
    for i in range(starts[0] + 1, len(lines)):
        if NEXT_HOOK.match(lines[i]):
            break
        m = ARGS.match(lines[i])
        if not m:
            continue
        rest = m.group(2).split("#", 1)[0].strip()
        if not rest and i + 1 < len(lines) and lines[i + 1].strip().startswith("["):
            i += 1
            rest = lines[i].split("#", 1)[0].strip()
        if rest.startswith("["):
            text, j = rest, i
            while "]" not in text:
                j += 1
                text += " " + lines[j].split("#", 1)[0].strip()
            items = text[text.index("[") + 1 : text.index("]")].split(",")
        else:
            items = []
            for line in lines[i + 1 :]:
                item = re.match(r"^\s*-\s+(?!id:|repo:)(.+)$", line)
                if not item:
                    break
                items.append(item.group(1).split("#", 1)[0])
        return [x.strip().strip("'\"") for x in items if x.strip()]
    raise ValueError("the conventional-pre-commit hook has no args")


def changelog_types(config: str) -> list[str]:
    """The types release-please-config.json has a changelog section for."""
    return [s["type"] for s in json.loads(config)["changelog-sections"]]


def mismatch(precommit: str, config: str) -> list[str]:
    """How the two lists differ, if they do."""
    hook, sections = set(hook_types(precommit)), set(changelog_types(config))
    out = [f"the hook allows {t!r}, which has no changelog section" for t in sorted(hook - sections)]
    out += [f"a changelog section for {t!r}, which the hook refuses" for t in sorted(sections - hook)]
    return out


CONFIG = json.dumps({"changelog-sections": [{"type": "feat"}, {"type": "fix"}]})


class CommitTypesAreReleasePleases(unittest.TestCase):
    def test_this_repository(self):
        precommit = (ROOT / ".pre-commit-config.yaml").read_text()
        config = (ROOT / "release-please-config.json").read_text()
        self.assertTrue(hook_types(precommit), "no types found")
        self.assertEqual(mismatch(precommit, config), [])

    def test_a_type_without_a_section_fails(self):
        hook = "  - id: conventional-pre-commit\n    args: [feat, fix, wip]\n"
        self.assertEqual(
            mismatch(hook, CONFIG), ["the hook allows 'wip', which has no changelog section"]
        )

    def test_a_section_the_hook_refuses_fails(self):
        hook = "  - id: conventional-pre-commit\n    args: [feat]\n"
        self.assertEqual(
            mismatch(hook, CONFIG), ["a changelog section for 'fix', which the hook refuses"]
        )

    def test_the_same_types_in_any_layout_pass(self):
        layouts = (
            "  - id: conventional-pre-commit\n    args: [fix, \"feat\"]\n",
            "  - id: conventional-pre-commit\n    stages: [commit-msg]\n    args:\n"
            "      [fix,\n       feat]  # comment\n",
            "  - id: conventional-pre-commit\n    args:\n      - fix\n      - feat\n",
        )
        for hook in layouts:
            with self.subTest(hook=hook):
                self.assertEqual(mismatch(hook, CONFIG), [])

    def test_another_hooks_args_are_not_read(self):
        text = (
            "  - id: conventional-pre-commit\n    stages: [commit-msg]\n"
            "  - id: other\n    args: [feat, fix]\n"
        )
        with self.assertRaises(ValueError):
            hook_types(text)


if __name__ == "__main__":
    unittest.main()
