"""Nothing in release-please.yml can merge a PR or arm auto-merge.

A release PR merged by a workflow's GITHUB_TOKEN starts no workflow, so the release
is never tagged (v1's 0.7.6); the maintainer merges it by hand. Each `run:` script is
read with the guard's own parser and rules (agent/guard.py), so a `gh pr merge` is
caught however it is written; the script's text is searched for the merge API and
auto-merge too, and no step may use an action that arms auto-merge.

The release App's token is minted and used only in the release-please job, in the
caller's `release-please` environment, scoped to the calling repository; every other
job keeps the GITHUB_TOKEN with its own permissions, and CI on the release PR is no
longer dispatched. Standard library only; CI's `test` job runs it:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from workflow_yaml import jobs

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
        others = [j for j in ("release-please", "digest", "auto-merge-off") if DISPATCH in job(text, j)]
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


CHECKOUT_OWN = "repository: ${{ job.workflow_repository }}"


def own_checkout(workflow: str) -> list[str]:
    """What is missing before the digest checks out this repository's script: a step
    that stops the job when job.workflow_repository or job.workflow_sha is empty."""
    block = job(workflow, "digest")
    if CHECKOUT_OWN not in block:
        return ["the digest doesn't check out its script from this repository"]
    before = block[: block.index(CHECKOUT_OWN)]
    checks = [s for s in run_scripts(before) if "WORKFLOW_REPOSITORY" in s and "WORKFLOW_SHA" in s]
    if not checks:
        return ["no step stops the job when job.workflow_repository or job.workflow_sha is empty"]
    return []


def run_check(script: str, repository: str, sha: str) -> int:
    env = {"PATH": os.environ.get("PATH", ""), "WORKFLOW_REPOSITORY": repository, "WORKFLOW_SHA": sha}
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True).returncode


class TheDigestFailsClosed(unittest.TestCase):
    def test_the_workflow(self):
        self.assertEqual(own_checkout(WORKFLOW.read_text()), [])

    def test_the_check_fails_on_an_empty_property(self):
        before = job(WORKFLOW.read_text(), "digest").split(CHECKOUT_OWN)[0]
        (script,) = [s for s in run_scripts(before) if "WORKFLOW_SHA" in s]
        self.assertEqual(run_check(script, "those-who-run/.github", "abc123"), 0)
        self.assertNotEqual(run_check(script, "", "abc123"), 0)
        self.assertNotEqual(run_check(script, "those-who-run/.github", ""), 0)

    def test_a_workflow_without_the_check_fails(self):
        text = WORKFLOW.read_text()
        start = text.index("      - name: The commit this workflow runs from")
        end = text.index("      - if:", start)
        self.assertEqual(
            own_checkout(text[:start] + text[end:]),
            ["no step stops the job when job.workflow_repository or job.workflow_sha is empty"],
        )


CALLER = ROOT / ".github" / "workflows" / "release-pr.yml"
APP_ACTION = re.compile(
    r"^        uses: actions/create-github-app-token@[0-9a-f]{40} # v\d+\.\d+\.\d+$", re.M
)
APP_TOKEN = "steps.app.outputs.token"
# What each job keeps from the GITHUB_TOKEN; the release-please job takes none of it.
PERMISSIONS = {
    "release-please": {},
    "digest": {"contents": "read", "pull-requests": "write"},
    "auto-merge-off": {"pull-requests": "write"},
    "release": {"actions": "write"},
}


def app_token_problems(workflow: str) -> list[str]:
    """Where the release App's token is minted and used, other than as it should be:
    minted and used only in the release-please job, in the `release-please`
    environment, scoped to the calling repository, and never written anywhere."""
    found = jobs(workflow)
    problems = []
    if set(found) != set(PERMISSIONS):
        problems.append(f"the jobs are {sorted(found)}, not {sorted(PERMISSIONS)}")
    rp = job(workflow, "release-please")
    if found.get("release-please", {}).get("keys", {}).get("environment") != "release-please":
        problems.append("the release-please job doesn't run in the release-please environment")
    if not APP_ACTION.search(rp):
        problems.append("the release-please job doesn't mint the token with a SHA-pinned create-github-app-token")
    for needed in ("client-id: ${{ vars.RELEASE_APP_CLIENT_ID }}",
                   "private-key: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}",
                   "token: ${{ steps.app.outputs.token }}"):
        if needed not in rp:
            problems.append(f"the release-please job lacks `{needed}`")
    # The App's Client ID, not the deprecated numeric `app-id` input.
    if re.search(r"^\s+app-id:", workflow, re.M) or "RELEASE_APP_ID" in workflow:
        problems.append("the token is minted with app-id, not client-id")
    # With neither set, the token reaches only the repository the run belongs to.
    if re.search(r"^          (owner|repositories):", rp, re.M):
        problems.append("the token is scoped beyond the calling repository")
    asked = set(re.findall(r"^          permission-([a-z-]+): (\w+)", rp, re.M))
    if asked != {("contents", "write"), ("pull-requests", "write"), ("issues", "write")}:
        problems.append(f"the token asks for {sorted(asked)}")
    # Used once, by release-please; never in a script, an output, the environment or a file.
    if workflow.count(APP_TOKEN) != 1:
        problems.append(f"the App's token is referenced {workflow.count(APP_TOKEN)} times, not once")
    if re.search(r"skip-token-revoke", rp):
        problems.append("the token outlives the job")
    for name in found:
        block = job(workflow, name)
        if name != "release-please":
            if re.search(r"create-github-app-token|secrets\.|vars\.RELEASE_APP|steps\.app\.", block):
                problems.append(f"{name} mints or uses the App's token or a secret")
            if re.findall(r"GH_TOKEN: (.+)", block) != ["${{ github.token }}"] * len(re.findall(r"GH_TOKEN:", block)):
                problems.append(f"{name} uses a token other than the GITHUB_TOKEN")
        if name in PERMISSIONS:
            have = found[name]["permissions"]
            if found[name]["keys"].get("permissions") == "{}":
                have = {}
            if have != PERMISSIONS[name]:
                problems.append(f"{name} has permissions {have}, not {PERMISSIONS[name]}")
    for script in run_scripts(workflow):
        if re.search(r"token|GITHUB_OUTPUT|GITHUB_ENV", script, re.I) and APP_TOKEN in script:
            problems.append("a script handles the App's token")
    return problems


class TheAppTokenStaysInTheReleasePleaseJob(unittest.TestCase):
    def test_the_workflow(self):
        self.assertEqual(app_token_problems(WORKFLOW.read_text()), [])

    def test_there_is_no_ci_dispatch_on_the_release_branch(self):
        text = WORKFLOW.read_text()
        self.assertNotIn("release-pr", jobs(text))
        self.assertNotIn("CI_WORKFLOW", text)
        self.assertNotRegex(text, r"gh workflow run \"\$CI_WORKFLOW\"|--ref \"\$branch\"")
        # The handoff on the tag is the only dispatch left.
        self.assertEqual(text.count("gh workflow run"), 1)

    def test_the_caller_passes_no_secrets(self):
        caller = CALLER.read_text()
        self.assertNotRegex(caller, r"(?m)^\s+secrets:")
        self.assertNotIn("ci-workflow", caller)
        self.assertNotIn("contents: write", caller)

    def test_the_token_in_another_job_is_caught(self):
        text = WORKFLOW.read_text()
        cases = {
            "digest mints or uses the App's token or a secret": text.replace(
                "  digest:\n", "  digest:\n    env:\n      KEY: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}\n", 1),
            "auto-merge-off uses a token other than the GITHUB_TOKEN": text.replace(
                "      GH_TOKEN: ${{ github.token }}\n    steps:\n      - name: Auto-merge off",
                "      GH_TOKEN: ${{ needs.release-please.outputs.token }}\n    steps:\n      - name: Auto-merge off", 1),
            "the App's token is referenced 2 times, not once": text.replace(
                "    outputs:\n", "    outputs:\n      token: ${{ steps.app.outputs.token }}\n", 1),
            "the token is scoped beyond the calling repository": text.replace(
                "          client-id:", "          owner: ${{ github.repository_owner }}\n          client-id:", 1),
            "the release-please job doesn't run in the release-please environment": text.replace(
                "    environment: release-please\n", "", 1),
            "the token is minted with app-id, not client-id": text.replace(
                "          client-id: ${{ vars.RELEASE_APP_CLIENT_ID }}",
                "          app-id: ${{ vars.RELEASE_APP_ID }}", 1),
            "the token outlives the job": text.replace(
                "          client-id:", "          skip-token-revoke: true\n          client-id:", 1),
        }
        for message, broken in cases.items():
            with self.subTest(message):
                self.assertNotEqual(broken, text)
                self.assertIn(message, app_token_problems(broken))

    def test_a_job_with_more_permissions_is_caught(self):
        text = WORKFLOW.read_text().replace(
            "    # Nothing from the GITHUB_TOKEN: release-please writes with the App's token.\n    permissions: {}\n",
            "    permissions:\n      contents: write\n", 1)
        self.assertIn("release-please has permissions {'contents': 'write'}, not {}", app_token_problems(text))


if __name__ == "__main__":
    unittest.main()
