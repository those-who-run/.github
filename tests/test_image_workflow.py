"""Checks on .github/workflows/image.yml and image-publish.yml that no linter makes.

zizmor checks that permissions are declared and actions pinned, but not which job
holds which write permission, or what runs next to it. These workflows publish
images for every repository, so the write permissions, and the `release`
environment that gates them, belong to one job, image-publish.yml's `publish`, and
that job builds nothing and runs no command from the calling repository: it checks
the digest image.yml recorded and pushes that image. A change that hands the
permissions to another job, lets a caller's command into `publish`, rebuilds there
or pushes before the digest check fails here.

Standard library only, so it reads the workflows line by line and relies on their
two-space indentation, which actionlint keeps valid. CI's `test` job runs it:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
BUILD = WORKFLOWS / "image.yml"
PUBLISH = WORKFLOWS / "image-publish.yml"

JOB = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
PERMISSION = re.compile(r"^      ([a-z-]+):\s*(read|write|none)\b")
KEY = re.compile(r"^    ([a-z-]+):(.*)$")
STEP = re.compile(r"^      - (.*)$")
STEP_KEY = re.compile(r"^(?:      - |        )([a-z-]+):\s?(.*)$")
PINNED = re.compile(r"^\s*(?:-\s*)?uses:\s*\S+@[0-9a-f]{40}\s+# v\d+(\.\d+)*\s*$")

PUBLISHING = {"packages", "id-token", "attestations"}


def jobs(text: str) -> dict[str, dict]:
    """Each job's permissions, other top-level keys and steps, from `jobs:`."""
    found: dict[str, dict] = {}
    job = None
    in_jobs = False
    in_permissions = False
    in_steps = False
    for line in text.splitlines():
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if m := JOB.match(line):
            job = m.group(1)
            found[job] = {"permissions": {}, "keys": {}, "steps": []}
            in_permissions = in_steps = False
            continue
        if job is None:
            continue
        if m := KEY.match(line):
            key, value = m.group(1), m.group(2).strip()
            found[job]["keys"][key] = value
            in_permissions = key == "permissions"
            in_steps = key == "steps"
            continue
        if in_permissions and (m := PERMISSION.match(line)):
            found[job]["permissions"][m.group(1)] = m.group(2)
        if in_steps:
            if STEP.match(line):
                found[job]["steps"].append({"text": [], "keys": {}, "script": []})
            if not found[job]["steps"] or line.lstrip().startswith("#"):
                continue  # YAML comments between steps belong to none of them
            step = found[job]["steps"][-1]
            step["text"].append(line)
            if m := STEP_KEY.match(line):
                step["current"] = m.group(1)
                step["keys"][m.group(1)] = m.group(2).strip()
            elif step.get("current") == "run" and line.startswith(" " * 10):
                step["script"].append(line.strip())
    return found


def steps(job: dict) -> list[dict]:
    """Each step's name, its `uses:` action and its `run:` script as one string."""
    return [
        {
            "name": s["keys"].get("name", ""),
            "uses": s["keys"].get("uses", ""),
            "run": "\n".join(s["script"]) if s["keys"].get("run") == "|" else s["keys"].get("run", ""),
            "text": "\n".join(s["text"]),
        }
        for s in job["steps"]
    ]


class BuildWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.text = BUILD.read_text()
        self.jobs = jobs(self.text)

    def test_stable_job_names(self) -> None:
        # Rulesets require `<caller job> / build`; renaming breaks every caller.
        self.assertEqual(set(self.jobs), {"build"})

    def test_build_only_reads(self) -> None:
        self.assertEqual(self.jobs["build"]["permissions"], {"contents": "read"})

    def test_digest_recorded_before_the_smoke_test(self) -> None:
        names = [s["name"] for s in steps(self.jobs["build"])]
        self.assertLess(names.index("Record the digest"), names.index("Smoke test"))
        self.assertLess(names.index("Smoke test"), names.index("Hand the image on"))
        self.assertIn("digest: ${{ steps.digest.outputs.digest }}", self.text)

    def test_one_build_with_provenance_and_sbom(self) -> None:
        builds = [s for s in steps(self.jobs["build"]) if "build-push-action" in s["uses"]]
        self.assertEqual(len(builds), 1)
        self.assertIn("provenance: mode=max", builds[0]["text"])
        self.assertIn("sbom: true", builds[0]["text"])
        self.assertIn("type=oci", builds[0]["text"])
        self.assertNotIn("push: true", builds[0]["text"])

    def test_version_build_argument_only_from_a_release_tag(self) -> None:
        # The one build argument is VERSION, from the version the inputs step took
        # from a vX.Y.Z tag, and none off a tag, so the Dockerfile's default holds.
        builds = [s for s in steps(self.jobs["build"]) if "build-push-action" in s["uses"]]
        self.assertIn(
            "build-args: ${{ steps.inputs.outputs.version != '' && "
            "format('VERSION={0}', steps.inputs.outputs.version) || '' }}",
            builds[0]["text"],
        )
        inputs = [s for s in steps(self.jobs["build"]) if s["name"] == "Check the inputs"][0]
        self.assertIn('version=""', inputs["run"])
        self.assertIn('echo "version=$version"', inputs["run"])


class PublishWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.text = PUBLISH.read_text()
        self.jobs = jobs(self.text)
        self.steps = steps(self.jobs["publish"])

    def test_stable_job_names(self) -> None:
        self.assertEqual(set(self.jobs), {"publish"})

    def test_publish_permissions_exactly(self) -> None:
        self.assertEqual(
            self.jobs["publish"]["permissions"],
            {"contents": "read", **{p: "write" for p in PUBLISHING}},
        )

    def test_publish_runs_in_release_one_at_a_time(self) -> None:
        keys = self.jobs["publish"]["keys"]
        self.assertEqual(keys.get("environment"), "release")
        self.assertIn("concurrency", keys)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_no_caller_command(self) -> None:
        # Every input reaches a script through env:, and none is run as a command.
        self.assertNotIn("inputs.smoke", self.text)
        self.assertNotRegex(self.text, r"(?m)^      smoke:")
        for step in self.steps:
            with self.subTest(step=step["name"]):
                self.assertNotIn("${{", step["run"])
                self.assertNotRegex(step["run"], r"(?m)(^|[;&|(]\s*)(eval|source|\.)\s|\b(ba|z)?sh\s+-c\b")

    def test_no_build(self) -> None:
        for step in self.steps:
            with self.subTest(step=step["name"]):
                self.assertNotIn("build-push-action", step["uses"])
                self.assertNotIn("setup-buildx-action", step["uses"])
                self.assertNotRegex(step["run"], r"\b(docker|podman|buildah)\b.*\bbuild\b")
                self.assertNotIn("buildctl", step["run"])

    def test_tag_check_comes_first(self) -> None:
        first = self.steps[0]["run"]
        self.assertIn('[ "$REF_TYPE" != tag ]', first)
        self.assertIn(r"^v([0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?)$", first)

    def test_digest_verified_before_login_and_push(self) -> None:
        names = [s["name"] for s in self.steps]
        verify = names.index("Verify the digest")
        login = next(i for i, s in enumerate(self.steps) if "login-action" in s["uses"])
        push = next(i for i, s in enumerate(self.steps) if "skopeo copy" in s["run"])
        self.assertLess(verify, login)
        self.assertLess(login, push)
        self.assertIn('!= "$DIGEST"', self.steps[verify]["run"])
        self.assertIn("sha256sum --check", self.steps[verify]["run"])
        # Nothing pushes in any other way or before the check.
        for i, step in enumerate(self.steps):
            if i != push:
                with self.subTest(step=step["name"]):
                    self.assertNotRegex(step["run"], r"\b(skopeo copy|docker push)\b")

    def test_push_keeps_the_digest(self) -> None:
        self.assertIn("--preserve-digests", self.steps[[s["name"] for s in self.steps].index("Push")]["run"])

    def test_attests_the_verified_digest(self) -> None:
        attests = [s for s in self.steps if "actions/attest@" in s["uses"]]
        self.assertEqual(len(attests), 2)
        for step in attests:
            self.assertIn("subject-digest: ${{ steps.check.outputs.digest }}", step["text"])


class BothWorkflows(unittest.TestCase):
    def test_nothing_granted_at_the_top(self) -> None:
        for path in (BUILD, PUBLISH):
            with self.subTest(workflow=path.name):
                self.assertRegex(path.read_text(), r"(?m)^permissions: \{\}\s*$")

    def test_every_job_declares_its_permissions(self) -> None:
        for path in (BUILD, PUBLISH):
            for name, job in jobs(path.read_text()).items():
                with self.subTest(workflow=path.name, job=name):
                    self.assertIn("permissions", job["keys"])

    def test_actions_pinned_by_sha(self) -> None:
        for path in (BUILD, PUBLISH):
            uses = [line for line in path.read_text().splitlines() if re.match(r"^\s*(-\s*)?uses:", line)]
            self.assertTrue(uses)
            for line in uses:
                with self.subTest(workflow=path.name, line=line.strip()):
                    self.assertRegex(line, PINNED)

    def test_the_parser_sees_a_misplaced_write(self) -> None:
        # A parser that saw nothing would pass the checks above.
        text = BUILD.read_text()
        moved = text.replace(
            "  build:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n",
            "  build:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n"
            "      id-token: write\n",
        )
        self.assertNotEqual(moved, text)
        self.assertEqual(jobs(moved)["build"]["permissions"].get("id-token"), "write")

    def test_the_parser_sees_a_caller_command(self) -> None:
        text = PUBLISH.read_text()
        found = steps(jobs(text)["publish"])
        self.assertTrue(any("skopeo copy" in s["run"] for s in found))
        planted = text.replace(
            "      - name: Push\n",
            "      - name: Planted\n        run: bash -c \"$SMOKE\"\n      - name: Push\n",
        )
        self.assertNotEqual(planted, text)
        self.assertIn('bash -c "$SMOKE"', [s["run"] for s in steps(jobs(planted)["publish"])])


if __name__ == "__main__":
    unittest.main()
