"""The reusable workflows a ruleset requires report on `merge_group` too.

With GitHub's merge queue on (scripts/protect-repo.sh, `merge_queue` in
scripts/repos.json), each required check runs again on the merge group, from the
caller's `merge_group` trigger. A merge group is not a pull request: the event has no
`pull_request` in its payload, so a step that reads the PR's title, description,
number or base would get nothing, and a required job that is skipped or never starts
on it stalls the queue until the check times out (those-who-run/paddock#277).

So, in every workflow a caller's required check comes from:

- the job has no `if:` or `needs:`, so it runs and reports on any event;
- every step that reads the pull request runs on `pull_request` only, by an `if:`
  that starts with `github.event_name == 'pull_request'`; on `merge_group` it is
  skipped and the job still succeeds under the same check name. Each of these ran on
  the PR itself, against the head the queue takes;
- a step that reads such a step's outcome or outputs is gated the same way, so a
  check never goes quiet on `merge_group` because the step it waits for was skipped.

Standard library only (tests/workflow_yaml.py). CI's `test` job runs it:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import re
import unittest

from workflow_yaml import WORKFLOWS, jobs

# The reusable workflows a caller's required checks come from, with each job's
# steps that read the pull request, by name. A step added that reads it is found by
# what it reads (PR_CONTEXT) whether it is listed here or not.
REQUIRED = {
    "checks.yml": {
        "checks": {"PR title is a conventional commit", "Dependency review",
                   "No secrets in the PR's commits"},
    },
    "go.yml": {
        "check": {"Test guard", "Weakened tests need a reason"},
    },
    "image.yml": {
        "build": set(),
    },
}

# What only a pull_request event carries.
PR_CONTEXT = re.compile(r"github\.event\.(pull_request|number)\b|github\.(head_ref|base_ref)\b")
PR_ONLY = re.compile(r"^github\.event_name == 'pull_request'( && .+)?$")
STEP_START = re.compile(r"^      - ")
STEP_IF = re.compile(r"^        if:\s*(.+?)\s*$")
STEP_ID = re.compile(r"^        id:\s*(\S+)\s*$")
STEP_NAME = re.compile(r"^      - name:\s*(.+?)\s*$")


def steps(job: dict) -> list[dict]:
    """Each step's name (or its `uses:`), id, `if:` and full text."""
    found: list[dict] = []
    for line in job["steps"]:
        if STEP_START.match(line):
            found.append({"name": None, "id": None, "if": None, "text": ""})
            if m := STEP_NAME.match(line):
                found[-1]["name"] = m.group(1)
            elif "uses:" in line:
                found[-1]["name"] = line.split("uses:", 1)[1].split("#")[0].strip()
        if not found:
            continue
        found[-1]["text"] += line + "\n"
        if m := STEP_IF.match(line):
            condition = m.group(1)
            if condition.startswith("${{") and condition.endswith("}}"):
                condition = condition[3:-2].strip()
            found[-1]["if"] = condition
        elif m := STEP_ID.match(line):
            found[-1]["id"] = m.group(1)
    return found


def problems(text: str, job_name: str, pr_steps: set[str]) -> list[str]:
    """What would leave the job's required check missing or wrong on merge_group."""
    job = jobs(text)[job_name]
    found = []
    for key in ("if", "needs"):
        if key in job["keys"]:
            found.append(f"{job_name}: a job-level {key}: can leave the required check unreported")
    pr_only_ids = set()
    named = set()
    for s in steps(job):
        named.add(s["name"])
        pr_only = s["if"] is not None and PR_ONLY.match(s["if"]) is not None
        if pr_only and s["id"]:
            pr_only_ids.add(s["id"])
        if PR_CONTEXT.search(s["text"]) and not pr_only:
            found.append(f"{s['name']}: reads the pull request but runs on every event")
        if s["name"] in pr_steps and not pr_only:
            found.append(f"{s['name']}: reads the pull request but runs on every event")
        waits_for = {i for i in pr_only_ids if re.search(rf"\bsteps\.{re.escape(i)}\.", s["if"] or "")}
        if waits_for and not pr_only:
            found.append(f"{s['name']}: waits for {sorted(waits_for)}, which never run on merge_group")
    for name in pr_steps - named:
        found.append(f"{name}: no such step")
    return found


class MergeGroup(unittest.TestCase):
    def test_every_required_check_reports_on_merge_group(self):
        for workflow, wanted in REQUIRED.items():
            text = (WORKFLOWS / workflow).read_text()
            self.assertEqual(set(jobs(text)), set(wanted), workflow)
            for job_name, pr_steps in wanted.items():
                with self.subTest(workflow=workflow, job=job_name):
                    self.assertEqual(problems(text, job_name, pr_steps), [])

    def test_the_workflows_are_called_on_any_event(self):
        # A reusable workflow runs on the caller's event; one with its own event
        # filter would not run where the caller added merge_group.
        for workflow in REQUIRED:
            with self.subTest(workflow=workflow):
                after = (WORKFLOWS / workflow).read_text().split("\non:\n", 1)[1].splitlines()
                block = []
                for line in after:
                    if line and not line.startswith(" "):
                        break
                    block.append(line)
                events = [line.strip() for line in block if re.match(r"^  [a-z_]+:", line)]
                self.assertEqual(events, ["workflow_call:"])

    def test_a_step_that_reads_the_pr_on_every_event_is_found(self):
        # Checks that see nothing would pass the test above.
        text = (WORKFLOWS / "checks.yml").read_text()
        ungated = text.replace("        if: github.event_name == 'pull_request'\n        env:\n"
                               "          PR_TITLE:", "        env:\n          PR_TITLE:", 1)
        self.assertNotEqual(ungated, text)
        self.assertIn("PR title is a conventional commit: reads the pull request but runs on every event",
                      problems(ungated, "checks", REQUIRED["checks.yml"]["checks"]))
        # A listed step that reads nothing from the event, such as gitleaks over
        # HEAD^1..HEAD, a PR's merge commit, is found by name.
        gitleaks = text.replace("      - name: No secrets in the PR's commits\n"
                                "        if: github.event_name == 'pull_request'\n",
                                "      - name: No secrets in the PR's commits\n", 1)
        self.assertNotEqual(gitleaks, text)
        self.assertIn("No secrets in the PR's commits: reads the pull request but runs on every event",
                      problems(gitleaks, "checks", REQUIRED["checks.yml"]["checks"]))
        job_if = text.replace("  checks:\n    runs-on: ubuntu-latest\n",
                              "  checks:\n    runs-on: ubuntu-latest\n    if: github.event_name == 'pull_request'\n", 1)
        self.assertIn("checks: a job-level if: can leave the required check unreported",
                      problems(job_if, "checks", set()))

    def test_a_step_waiting_for_a_pr_only_step_is_found(self):
        text = (WORKFLOWS / "go.yml").read_text()
        waiting = text.replace("        if: github.event_name == 'pull_request' && steps.guard.outcome == 'success'\n",
                               "        if: steps.guard.outcome == 'success'\n", 1)
        self.assertNotEqual(waiting, text)
        found = problems(waiting, "check", set())
        self.assertIn("Weakened tests need a reason: waits for ['guard'], which never run on merge_group", found)
        # It reads the PR's number too.
        self.assertIn("Weakened tests need a reason: reads the pull request but runs on every event", found)


if __name__ == "__main__":
    unittest.main()
