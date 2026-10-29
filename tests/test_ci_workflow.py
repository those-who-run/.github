"""Checks on this repository's own .github/workflows/ci.yml.

ci.yml runs on pull requests, on pushes to main and in the merge queue
(`merge_group`), nothing else; a queue whose required checks never report never
merges, and only a superseded PR run is cancelled. release-please's PR is opened by
the organisation's release App, so it starts CI through `pull_request` like any
other PR, and there is no dispatch on the release branch to allow. These tests check
the triggers, and that the required jobs carry no `if:` or `needs:` that could leave
them skipped. pr-title.yml runs ci.yml's `checks` job again when a PR is edited, so a
fixed title clears the check without a push; these tests keep the two jobs equal.

Standard library only (tests/workflow_yaml.py). CI's `test` job runs it:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import re
import unittest

from workflow_yaml import WORKFLOWS, jobs

WORKFLOW = WORKFLOWS / "ci.yml"
TRIGGER = re.compile(r"^  ([a-z_]+):")


def triggers(text: str) -> set[str]:
    """The events in a workflow's top-level `on:`."""
    found, inside = set(), False
    for line in text.splitlines():
        if line.startswith("on:"):
            inside = True
            continue
        if inside and line and not line.startswith((" ", "#")):
            break
        if inside and (m := TRIGGER.match(line)):
            found.add(m.group(1))
    return found


def dispatch_problems(text: str) -> list[str]:
    """What is left of the release branch's dispatch: the trigger, or a step for it."""
    problems = []
    if "workflow_dispatch" in triggers(text):
        problems.append("ci.yml takes workflow_dispatch")
    if re.search(r"workflow_dispatch|release-please--", text):
        problems.append("ci.yml still handles a dispatch on a release-please branch")
    return problems


def job_lines(text: str, name: str) -> list[str]:
    """A job's lines as written, its `with:` inputs included, without comments or blank
    lines."""
    lines = text.split("\njobs:\n", 1)[1].splitlines()
    start = lines.index(f"  {name}:")
    found = []
    for line in lines[start + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith("    "):
            break
        found.append(line)
    return found


class CiWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text()
        self.jobs = jobs(self.text)

    def test_required_jobs_always_run(self) -> None:
        # `test` and `checks` are the required jobs; `go-fixture` and `go-check-cases`
        # test go.yml, `python-fixture` and `python-check-cases` python.yml. A new job
        # means changing this list.
        self.assertEqual(
            set(self.jobs),
            {"test", "checks", "go-fixture", "go-check-cases", "python-fixture", "python-check-cases"},
        )
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                self.assertNotIn("if", job["keys"])
                self.assertNotIn("needs", job["keys"])

    def test_it_runs_on_pull_requests_main_and_the_merge_queue_only(self) -> None:
        self.assertEqual(triggers(self.text), {"pull_request", "merge_group", "push"})
        self.assertIn("  merge_group:\n    types: [checks_requested]\n", self.text)
        self.assertIn("  push:\n    branches: [main]\n", self.text)

    def test_only_a_superseded_pr_run_is_cancelled(self) -> None:
        self.assertIn("  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n", self.text)

    def test_there_is_no_dispatch_on_a_release_branch(self) -> None:
        self.assertEqual(dispatch_problems(self.text), [])

    def test_a_dispatch_trigger_or_guard_is_caught(self) -> None:
        with_trigger = self.text.replace("  pull_request:\n", "  pull_request:\n  workflow_dispatch:\n", 1)
        self.assertIn("ci.yml takes workflow_dispatch", dispatch_problems(with_trigger))
        with_guard = self.text.replace(
            "    steps:\n",
            "    steps:\n      - if: github.event_name == 'workflow_dispatch'\n"
            "        run: case \"$GITHUB_REF\" in refs/heads/release-please--*) ;; esac\n",
            1,
        )
        self.assertIn(
            "ci.yml still handles a dispatch on a release-please branch", dispatch_problems(with_guard)
        )


class PrTitleWorkflow(unittest.TestCase):
    """pr-title.yml runs `checks / checks` again when a PR is edited
    (those-who-run/paddock#285), and nothing else, apart from CI's runs."""

    def setUp(self) -> None:
        self.text = (WORKFLOWS / "pr-title.yml").read_text()
        self.jobs = jobs(self.text)

    def test_it_runs_on_an_edit_only(self) -> None:
        self.assertEqual(triggers(self.text), {"pull_request"})
        self.assertIn("  pull_request:\n    types: [edited]\n", self.text)

    def test_it_runs_only_the_checks_job_and_always(self) -> None:
        # `test`, `go / check` or `image / build` here would run again on every edit.
        self.assertEqual(set(self.jobs), {"checks"})
        self.assertNotIn("if", self.jobs["checks"]["keys"])
        self.assertNotIn("needs", self.jobs["checks"]["keys"])

    def test_the_job_is_cis_checks_job(self) -> None:
        """The same reusable workflow with the same inputs and permissions, so the result
        it reports as `checks / checks` is the whole check, as CI's would be."""
        self.assertEqual(job_lines(self.text, "checks"), job_lines(WORKFLOW.read_text(), "checks"))
        self.assertIn("    uses: ./.github/workflows/checks.yml", job_lines(self.text, "checks"))

    def test_a_different_input_is_caught(self) -> None:
        changed = self.text + "    with:\n      dependency-review: false\n"
        self.assertNotEqual(job_lines(changed, "checks"), job_lines(WORKFLOW.read_text(), "checks"))

    def test_its_concurrency_group_is_its_own(self) -> None:
        # The workflow's name keeps it apart from CI's group, so an edit never cancels
        # a CI run on the head.
        self.assertIn("  group: ${{ github.workflow }}-${{ github.ref }}\n", self.text)
        self.assertIn("\nname: PR title\n", "\n" + self.text)

    def test_it_never_runs_with_the_base_branchs_privileges(self) -> None:
        self.assertNotIn("pull_request_target", self.text)
        self.assertIn("\npermissions: {}\n", self.text)


if __name__ == "__main__":
    unittest.main()
