"""Nothing in release-please.yml can merge a PR or arm auto-merge.

A release PR merged by a workflow's GITHUB_TOKEN starts no workflow, so the release
is never tagged (v1's 0.7.6); the maintainer merges it by hand. Each `run:` script is
read with the guard's own parser and rules (agent/guard.py), so a `gh pr merge` is
caught however it is written; the script's text is searched for the merge API and
auto-merge too, and no step may use an action that arms auto-merge. Standard library only; CI's `test` job runs it:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "release-please.yml"

_spec = importlib.util.spec_from_file_location("guard", ROOT / "agent" / "guard.py")
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
sys.modules["guard"] = guard  # dataclasses look their module up
_spec.loader.exec_module(guard)

RUN = re.compile(r"^(\s*)(-\s+)?run:\s*(.*)$")
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)")
# The guard's rules for a merge: `gh pr merge` (with or without --admin), and the
# REST merge endpoint or GraphQL mutation behind it.
MERGE_RULES = {"gh-pr-merge", "gh-admin-merge"}
MERGE_API = re.compile(r"pulls/[^/ ]+/merge\b|mergePullRequest")
# Arming auto-merge: the gh flag or the GraphQL mutation in a script, or an action
# for it. disablePullRequestAutoMerge, which auto-merge-off uses, only disarms it.
AUTO_MERGE = re.compile(r"--auto\b|enablePullRequestAutoMerge|\bauto_merge\b")
AUTO_MERGE_ACTION = re.compile(r"auto[-_]?merge", re.I)


def run_scripts(workflow: str) -> list[str]:
    """Every `run:` script in a workflow, block scalars included."""
    lines, out, i = workflow.splitlines(), [], 0
    while i < len(lines):
        m = RUN.match(lines[i])
        i += 1
        if not m:
            continue
        indent = len(m.group(1)) + len(m.group(2) or "")
        value = m.group(3).strip()
        if value[:1] not in ("|", ">"):
            out.append(value)
            continue
        body = []
        while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
            body.append(lines[i])
            i += 1
        out.append("\n".join(body))
    return out


def _verdicts(script: str, depth: int = 0) -> list:
    out = []
    for cmd in guard.parse(script):
        for sub in cmd.subs:
            out += _verdicts(sub, depth + 1)
        out.append(guard.check_command(cmd, {}, script, depth))
    return out


def merges(workflow: str) -> list[str]:
    """What in a workflow could merge a PR or arm auto-merge."""
    found = []
    for script in run_scripts(workflow):
        for v in _verdicts(script):
            if v.rule in MERGE_RULES or MERGE_API.search(v.reason):
                found.append(f"{v.rule}: {v.reason}")
        if MERGE_API.search(script):
            found.append(f"a merge through the API: {MERGE_API.search(script).group(0)}")
        if AUTO_MERGE.search(script):
            found.append(f"auto-merge in a run script: {AUTO_MERGE.search(script).group(0)}")
    for line in workflow.splitlines():
        m = USES.match(line)
        if m and AUTO_MERGE_ACTION.search(m.group(1)):
            found.append(f"an auto-merge action: {m.group(1)}")
    return found


def step(script: str) -> str:
    body = "".join(f"          {line}\n" for line in script.splitlines())
    return f"jobs:\n  x:\n    steps:\n      - run: |\n{body}"


class NothingMerges(unittest.TestCase):
    def test_the_workflow(self):
        text = WORKFLOW.read_text()
        self.assertTrue(run_scripts(text), "no run scripts found")
        self.assertEqual(merges(text), [])

    def test_auto_merge_is_disarmed_on_every_run(self):
        block = job(WORKFLOW.read_text(), "auto-merge-off")
        self.assertIn("disablePullRequestAutoMerge", block)
        self.assertNotRegex(block, r"(?m)^    if:")

    def test_each_way_to_merge_is_caught(self):
        cases = (
            'gh pr merge "$PR" --squash',
            'number=1\ngh pr merge "$number" --repo "$GITHUB_REPOSITORY" --admin',
            "gh pr merge 1 --auto --squash",
            'gh api -X PUT "repos/$GITHUB_REPOSITORY/pulls/1/merge"',
            "gh api graphql -f query='mutation { mergePullRequest(input: {}) { clientMutationId } }'",
            "gh api graphql -f query='mutation { enablePullRequestAutoMerge(input: {}) { x } }'",
            'x=$(gh pr merge 1)',
        )
        for script in cases:
            with self.subTest(script=script):
                self.assertTrue(merges(step(script)))

    def test_a_one_line_run_is_read(self):
        self.assertTrue(merges("jobs:\n  x:\n    steps:\n      - run: gh pr merge 1\n"))

    def test_an_auto_merge_action_is_caught(self):
        text = "jobs:\n  x:\n    steps:\n      - uses: peter-evans/enable-pull-request-automerge@abc\n"
        self.assertTrue(merges(text))

    def test_near_misses(self):
        for script in (
            'gh pr list --state open --label "autorelease: pending"',
            'gh pr view 1 --json mergeable',
            'gh workflow run ci.yml --ref "$branch"',
            'gh api "repos/$GITHUB_REPOSITORY/pulls/1"',
            'echo "#1 waits for the maintainer to merge it."',
            # Disarming auto-merge is not merging.
            "gh api graphql -F id=\"$id\" -f query='mutation($id: ID!) {"
            " disablePullRequestAutoMerge(input: {pullRequestId: $id}) { clientMutationId } }'",
        ):
            with self.subTest(script=script):
                self.assertEqual(merges(step(script)), [])


def job(workflow: str, name: str) -> str:
    """One job's block from a workflow's `jobs:`, by its name."""
    lines, out, inside = workflow.splitlines(), [], False
    for line in lines:
        if re.match(r"^  [\w-]+:\s*$", line):
            inside = line.strip() == f"{name}:"
        elif line and not line.startswith(" "):
            inside = False
        if inside:
            out.append(line)
    return "\n".join(out)


# The release job's condition: the run released, and the caller has a release workflow.
RELEASED = re.compile(
    r"^    if: needs\.release-please\.outputs\.release_created == 'true'"
    r" && inputs\.release-workflow != ''$",
    re.M,
)
DISPATCH = 'gh workflow run "$RELEASE_WORKFLOW" --repo "$GITHUB_REPOSITORY" --ref "$TAG"'
TAG = re.compile(r"^\s+TAG: \$\{\{ needs\.release-please\.outputs\.tag_name \}\}$", re.M)


def release_dispatch(workflow: str) -> list[str]:
    """What is missing from the handoff to publishing: the caller's release workflow,
    dispatched on the new tag, only when a release was created."""
    block = job(workflow, "release")
    problems = []
    if not block:
        return ["no `release` job"]
    if not RELEASED.search(block):
        problems.append("the release job doesn't run only when a release was created")
    if DISPATCH not in "\n".join(run_scripts(block)):
        problems.append("the release job doesn't dispatch the release workflow on the tag")
    if not TAG.search(block):
        problems.append("the tag doesn't come from release-please's tag_name")
    if "actions: write" not in block or "uses:" in block:
        problems.append("the release job must dispatch with actions: write, not call a workflow")
    return problems


class ReleaseIsDispatchedOnTheTag(unittest.TestCase):
    def test_the_workflow(self):
        self.assertEqual(release_dispatch(WORKFLOW.read_text()), [])

    def test_the_dispatch_is_the_only_job_with_it_on_a_release(self):
        text = WORKFLOW.read_text()
        others = [j for j in ("release-please", "digest", "release-pr") if DISPATCH in job(text, j)]
        self.assertEqual(others, [])

    def test_an_unconditional_dispatch_fails(self):
        text = WORKFLOW.read_text()
        broken = RELEASED.sub("    if: always()", text)
        self.assertIn(
            "the release job doesn't run only when a release was created", release_dispatch(broken)
        )

    def test_a_dispatch_on_main_fails(self):
        broken = WORKFLOW.read_text().replace('--ref "$TAG"', "--ref main")
        self.assertIn(
            "the release job doesn't dispatch the release workflow on the tag",
            release_dispatch(broken),
        )

    def test_a_direct_call_fails(self):
        text = (
            "jobs:\n  release:\n    needs: release-please\n"
            "    if: needs.release-please.outputs.release_created == 'true'"
            " && inputs.release-workflow != ''\n"
            "    uses: ./.github/workflows/release.yml\n"
        )
        self.assertTrue(release_dispatch(text))


if __name__ == "__main__":
    unittest.main()
