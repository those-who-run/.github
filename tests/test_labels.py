"""scripts/labels.sh and its list, scripts/labels.json, run through make as the
maintainer runs them (`make labels-check`, `make labels`), against a stub `gh` that
keeps a pretend GitHub's labels in a JSON file, so nothing reaches GitHub.

The stub answers the reads the script makes in the shapes GitHub's REST API returns
them (the label list in pages of PAGE, as GitHub's come 30 to a page), applies its
writes and logs every call. A call can be made to fail with GitHub's error body
(state["fail"]), to see what the run does without a login or past a refused write.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "labels.sh"
LIST = ROOT / "scripts" / "repos.json"
LABELS = ROOT / "scripts" / "labels.json"
ORG = "those-who-run"
PAGE = 2

STUB = r'''
import json, os, re, sys

PAGE = __PAGE__

state_path, log_path = os.environ["GH_STATE"], os.environ["GH_CALLS"]
state = json.load(open(state_path))
args = sys.argv[1:]
call = {"argv": args, "method": "GET", "path": None, "input": None, "query": ""}
assert args[0] == "api", args
i = 1
while i < len(args):
    a = args[i]
    if a == "-X":
        call["method"] = args[i + 1]; i += 2
    elif a == "--paginate":
        call["paginate"] = True; i += 1
    elif a == "--input":
        call["input"] = json.loads(sys.stdin.read()); i += 2
    else:
        call["path"], _, call["query"] = a.partition("?"); i += 1

def done(body=None, status=0, message="Not Found"):
    with open(log_path, "a") as log:
        log.write(json.dumps(call) + "\n")
    json.dump(state, open(state_path, "w"))
    if status:
        print(json.dumps({"message": message}))
        print(f"gh: {message} (HTTP {status})", file=sys.stderr)
        sys.exit(1)
    if body is not None:
        print(json.dumps(body))
    sys.exit(0)

# A call GitHub refuses, by a pattern over its arguments.
for rule in state.get("fail", []):
    if re.search(rule["match"], " ".join(args)):
        call["failed"] = rule["status"]
        done(status=rule["status"], message=rule.get("message", "Forbidden"))

m = re.fullmatch(r"repos/([^/]+/[^/]+)(?:/(.*))?", call["path"])
assert m, call["path"]
repo, rest = m[1], m[2]
entry = state["repos"].get(repo)
if entry is None:
    done(status=404)
if rest is None:
    done({"full_name": repo, "permissions": {"admin": entry.get("push", True),
                                             "push": entry.get("push", True), "pull": True}})
labels = entry["labels"]
if rest == "labels" and call["method"] == "GET":
    assert call.get("paginate"), "the label list is paged: read every page"
    pages = [labels[n:n + PAGE] for n in range(0, len(labels), PAGE)] or [[]]
    with open(log_path, "a") as log:
        log.write(json.dumps(call) + "\n")
    for page in pages:
        print(json.dumps(page))
    sys.exit(0)
if rest == "labels" and call["method"] == "POST":
    body = call["input"]
    assert set(body) <= {"name", "color", "description"}, body
    if any(l["name"].lower() == body["name"].lower() for l in labels):
        done(status=422, message="Validation Failed; already_exists")
    labels.append({"name": body["name"], "color": body["color"].lower(),
                   "description": body.get("description")})
    call["created"] = body["name"]
    done(labels[-1])
m = re.fullmatch(r"labels/(.+)", rest or "")
if m:
    name = m[1].replace("%20", " ")
    # GitHub finds a label by its name in any case.
    found = [l for l in labels if l["name"].lower() == name.lower()]
    if not found:
        done(status=404)
    label = found[0]
    if call["method"] == "GET":
        done(label)
    if call["method"] == "PATCH":
        body = call["input"]
        assert set(body) <= {"new_name", "color", "description"}, body
        if "new_name" in body:
            call["renamed"] = [label["name"], body["new_name"]]
            label["name"] = body["new_name"]
        if "color" in body:
            label["color"] = body["color"].lower()
        if "description" in body:
            label["description"] = body["description"]
        call["patched"] = label["name"]
        done(label)
    if call["method"] == "DELETE":
        call["deleted"] = label["name"]
        labels.remove(label)
        done()
call["unhandled"] = True
done(status=404)
'''.replace("__PAGE__", str(PAGE))


def label(name, color, description):
    return {"name": name, "color": color, "description": description}


def wanted():
    """The labels the repository's own list asks for, by name."""
    return {l["name"]: l for l in json.loads(LABELS.read_text())["labels"]}


def applies(entry, repo):
    return repo.lower() in [r.lower() for r in entry.get("only", [repo])]


class Run:
    def __init__(self, result, calls, state):
        self.out, self.err = result.stdout, result.stderr
        self.calls, self.state = calls, state
        # make exits 2 whenever a recipe fails; the recipe's own status is in its message.
        self.code = result.returncode
        m = re.search(r"\] Error (\d+)$", self.err, re.M)
        if self.code and m:
            self.code = int(m[1])

    @property
    def writes(self):
        return [c for c in self.calls if c["method"] != "GET"]

    @property
    def created(self):
        return sorted((c["path"].split("/", 1)[1].rsplit("/", 1)[0], c["created"])
                      for c in self.calls if "created" in c)

    @property
    def patched(self):
        return sorted((c["path"].split("/", 1)[1].rsplit("/labels/", 1)[0], c["patched"])
                      for c in self.calls if "patched" in c)

    @property
    def read(self):
        return sorted({c["path"].split("/", 1)[1] for c in self.calls if c["method"] == "GET" and "/" in c["path"]
                       and c["path"].count("/") == 2})


class Labels(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        stub = self.tmp / "gh"
        stub.write_text(f"#!{sys.executable}\n{STUB}")
        stub.chmod(0o755)
        self.state_path = self.tmp / "state.json"
        self.calls_path = self.tmp / "calls.jsonl"
        self.repos = list(json.loads(LIST.read_text())["repositories"])
        # Every listed repository exists, with the labels GitHub gives a new one and,
        # where the maintainer made them by hand, the two the ladder reads today.
        self.state = {"repos": {r: {"labels": [label("bug", "d73a4a", "Something isn't working"),
                                               label("documentation", "0075ca", "Improvements or additions to documentation")]}
                                for r in self.repos}}
        self.state["repos"][f"{ORG}/paddock"]["labels"] += [
            label("needs triage", "fbca04", "Not yet accepted as a spec; agents don't work on it"),
            label("component: radio", "c5def5", "Voice: callouts to speech to Discord (Python)"),
            label("hold", "d93f0b", "Paused by the maintainer: agents stop, PRs don't merge"),
        ]
        self.state["repos"][f"{ORG}/.github"]["labels"].append(
            label("re-review", "0e8a16", "The maintainer asks the ladder to review this head again"))
        self.state["repos"][f"{ORG}/chassis"]["labels"].append(
            label("re-review", "0e8a16", "The maintainer asks the ladder to review this head again; the publisher removes it"))
        # Only what the script needs: no gh config, no token, the stub first on PATH.
        self.env = {"PATH": f"{self.tmp}{os.pathsep}{os.environ['PATH']}", "HOME": str(self.tmp),
                    "GH_STATE": str(self.state_path), "GH_CALLS": str(self.calls_path)}

    def run_(self, argv):
        self.state_path.write_text(json.dumps(self.state))
        self.calls_path.write_text("")
        result = subprocess.run(argv, env=self.env, capture_output=True, text=True, check=False)
        calls = [json.loads(line) for line in self.calls_path.read_text().splitlines()]
        self.state = json.loads(self.state_path.read_text())
        self.assertFalse([c for c in calls if c.get("unhandled")], "the stub didn't know a call")
        return Run(result, calls, self.state)

    def make(self, target):
        return self.run_(["make", "--no-print-directory", "-C", str(ROOT), target])

    def script(self, *args, labels=None, repos=None):
        argv = ["bash", str(SCRIPT), *args]
        if labels is not None:
            path = self.tmp / "labels.json"
            path.write_text(json.dumps(labels))
            argv += ["--labels", str(path)]
        if repos is not None:
            path = self.tmp / "repos.json"
            path.write_text(json.dumps(repos))
            argv += ["--list", str(path)]
        return self.run_(argv)

    def labels_of(self, repo):
        return {l["name"]: l for l in self.state["repos"][repo]["labels"]}

    def assert_every_label_right(self):
        for repo in self.repos:
            have = self.labels_of(repo)
            for name, want in wanted().items():
                if applies(want, repo):
                    self.assertEqual(have[name], label(name, want["color"], want["description"]), (repo, name))
                else:
                    self.assertNotIn(name, have, (repo, name))

    def test_the_list_in_the_repository(self):
        lists = wanted()
        self.assertEqual(set(lists), {"hold", "re-review", "agent-ready"})
        for name, entry in lists.items():
            self.assertRegex(entry["color"], r"^[0-9a-f]{6}$", name)
            self.assertTrue(entry["description"], name)
        self.assertNotIn("only", lists["hold"])
        self.assertNotIn("only", lists["re-review"])
        self.assertEqual(lists["agent-ready"]["only"], [f"{ORG}/paddock"])
        # The ladder's reviewer skips a held PR and reviews a re-review head again.
        self.assertEqual(lists["hold"]["color"], "d93f0b")
        self.assertEqual(lists["re-review"]["color"], "0e8a16")

    def test_check_reports_missing_differing_and_present_labels(self):
        paddock = self.state["repos"][f"{ORG}/paddock"]["labels"]
        paddock.append(label("agent-ready", "1d76db", "A spec for an unattended agent to build; only the maintainer applies it"))
        self.state["repos"][f"{ORG}/radio"]["labels"].append(
            label("hold", "d93f0b", "Paused"))
        run = self.make("labels-check")
        self.assertEqual(run.code, 1, run.out + run.err)
        self.assertEqual(run.writes, [])
        self.assertIn(f"{ORG}/paddock: label 'hold': ok", run.out)
        self.assertIn(f"{ORG}/chassis: label 're-review': ok", run.out)
        self.assertIn(f"{ORG}/paddock: drift: label 're-review': missing", run.out)
        # The description made by hand lacks the list's last clause.
        self.assertIn(f"{ORG}/.github: drift: label 're-review': description want "
                      f"\"The maintainer asks the ladder to review this head again; the publisher removes it\", "
                      f"have \"The maintainer asks the ladder to review this head again\"", run.out)
        self.assertIn(f"{ORG}/.github: drift: label 'hold': missing", run.out)
        self.assertIn(f"{ORG}/paddock: drift: label 'agent-ready': color want 006b75, have \"1d76db\"", run.out)
        self.assertIn(f"{ORG}/radio: drift: label 'hold': description want "
                      f"\"Paused by the maintainer: agents stop, PRs don't merge\", have \"Paused\"", run.out)
        self.assertNotIn(f"{ORG}/radio: drift: label 'hold': color", run.out)
        self.assertNotIn("no drift", run.out)
        # agent-ready belongs to paddock alone: no other repository is asked for it.
        self.assertNotIn(f"{ORG}/radio: drift: label 'agent-ready'", run.out)
        self.assertNotIn(f"{ORG}/.github: drift: label 'agent-ready'", run.out)

    def test_check_reads_every_listed_repository_and_only_those(self):
        self.state["repos"][f"{ORG}/outings"] = {"labels": []}
        run = self.make("labels-check")
        self.assertEqual(run.read, sorted(self.repos))
        self.assertEqual(sorted(c["path"] for c in run.calls if c["path"].endswith("/labels")),
                         sorted(f"repos/{r}/labels" for r in self.repos))

    def test_check_passes_when_every_label_is_right(self):
        for repo in self.repos:
            labels = self.state["repos"][repo]["labels"]
            labels[:] = [l for l in labels if l["name"] not in wanted()]
            for name, want in wanted().items():
                if applies(want, repo):
                    labels.append(label(name, want["color"], want["description"]))
        run = self.make("labels-check")
        self.assertEqual(run.code, 0, run.out + run.err)
        self.assertEqual(run.writes, [])
        self.assertNotIn("drift:", run.out)
        self.assertIn("no drift", run.out)

    def test_apply_creates_the_missing_patches_the_differing_and_leaves_the_rest(self):
        paddock = self.state["repos"][f"{ORG}/paddock"]["labels"]
        paddock.append(label("agent-ready", "1d76db", "The maintainer releases this issue to the builder"))
        before = {r: json.loads(json.dumps(e["labels"])) for r, e in self.state["repos"].items()}
        run = self.make("labels")
        self.assertEqual(run.code, 0, run.out + run.err)
        self.assert_every_label_right()
        # Created where missing, patched where different, and nothing else.
        expected_created = sorted((r, n) for r in self.repos for n, w in wanted().items()
                                  if applies(w, r) and n not in {l["name"] for l in before[r]})
        self.assertEqual(run.created, expected_created)
        self.assertEqual(run.patched, [(f"{ORG}/.github", "re-review"), (f"{ORG}/paddock", "agent-ready")])
        self.assertFalse([c for c in run.calls if "deleted" in c or "renamed" in c])
        self.assertEqual(len(run.writes), len(expected_created) + 2)
        self.assertIn(f"{ORG}/paddock: label 'hold': unchanged", run.out)
        self.assertIn(f"{ORG}/chassis: label 're-review': unchanged", run.out)
        self.assertIn(f"{ORG}/paddock: label 'agent-ready': changed", run.out)
        self.assertIn(f"{ORG}/.github: label 're-review': changed", run.out)
        self.assertIn(f"{ORG}/.github: label 'hold': created", run.out)
        self.assertIn(f"{ORG}/radio: label 're-review': created", run.out)
        # The labels the list doesn't name are as they were.
        for repo, labels in before.items():
            kept = [l for l in labels if l["name"] not in wanted()]
            self.assertEqual([l for l in self.state["repos"][repo]["labels"] if l["name"] not in wanted()], kept)
        # Read back and checked after the writes.
        self.assertIn("read back after the writes:", run.out)
        self.assertIn(f"{ORG}/paddock: label 'agent-ready': ok", run.out)
        self.assertIn("no drift", run.out)

    def test_a_second_apply_changes_nothing(self):
        first = self.make("labels")
        self.assertEqual(first.code, 0, first.out + first.err)
        again = self.make("labels")
        self.assertEqual(again.code, 0, again.out + again.err)
        self.assertEqual(again.writes, [])
        self.assertIn("no drift", again.out)
        check = self.make("labels-check")
        self.assertEqual(check.code, 0, check.out + check.err)

    def test_a_label_with_only_goes_to_that_repository_alone(self):
        run = self.make("labels")
        self.assertEqual(run.code, 0, run.out + run.err)
        self.assertIn("agent-ready", self.labels_of(f"{ORG}/paddock"))
        for repo in self.repos:
            if repo != f"{ORG}/paddock":
                self.assertNotIn("agent-ready", self.labels_of(repo), repo)
        self.assertEqual([c for c in run.created if c[1] == "agent-ready"], [(f"{ORG}/paddock", "agent-ready")])

    def test_a_label_in_another_case_is_updated_and_never_renamed(self):
        self.state["repos"][f"{ORG}/radio"]["labels"].append(label("Hold", "ffffff", "paused"))
        check = self.make("labels-check")
        self.assertEqual(check.code, 1)
        self.assertIn(f"{ORG}/radio: drift: label 'hold': named 'Hold' on GitHub", check.out)
        self.assertIn(f"{ORG}/radio: drift: label 'hold': color want d93f0b, have \"ffffff\"", check.out)
        run = self.make("labels")
        self.assertEqual(run.code, 1, run.out + run.err)
        self.assertIn(f"{ORG}/radio: label 'hold': named 'Hold' on GitHub, left as it is", run.out)
        self.assertIn((f"{ORG}/radio", "Hold"), run.patched)
        self.assertFalse([c for c in run.calls if "renamed" in c])
        # Patched under the name GitHub has, by its encoded path.
        self.assertTrue([c for c in run.calls if c["path"] == f"repos/{ORG}/radio/labels/Hold"])
        self.assertEqual(self.labels_of(f"{ORG}/radio")["Hold"],
                         label("Hold", "d93f0b", "Paused by the maintainer: agents stop, PRs don't merge"))
        # The case still differs after the apply, which the read-back reports.
        self.assertIn(f"{ORG}/radio: drift: label 'hold': named 'Hold' on GitHub", run.out)

    def test_a_name_with_a_space_is_encoded_in_the_path(self):
        labels = {"labels": [label("needs triage", "fbca04", "Not yet accepted")]}
        repos = {"repositories": {f"{ORG}/paddock": {}}}
        run = self.script(labels=labels, repos=repos)
        self.assertEqual(run.code, 0, run.out + run.err)
        self.assertEqual(run.patched, [(f"{ORG}/paddock", "needs triage")])
        self.assertTrue([c for c in run.calls if c["path"] == f"repos/{ORG}/paddock/labels/needs%20triage"])
        self.assertEqual(self.labels_of(f"{ORG}/paddock")["needs triage"]["description"], "Not yet accepted")

    def test_without_a_login_nothing_is_written(self):
        self.state["fail"] = [{"match": ".", "status": 401, "message": "Bad credentials"}]
        for target in ("labels-check", "labels"):
            with self.subTest(target=target):
                run = self.make(target)
                self.assertEqual(run.code, 2, run.out + run.err)
                self.assertEqual(run.writes, [])
                self.assertIn("can't read the repository", run.err)
                self.assertIn("gh auth status", run.err)
                self.assertNotIn("created", run.out)

    def test_a_repository_that_cannot_be_read_stops_the_run_before_any_write(self):
        del self.state["repos"][f"{ORG}/radio"]
        for target in ("labels-check", "labels"):
            with self.subTest(target=target):
                run = self.make(target)
                self.assertEqual(run.code, 2, run.out + run.err)
                self.assertEqual(run.writes, [])
                self.assertIn(f"{ORG}/radio: can't read the repository", run.err)
                self.assertFalse([c for c in run.calls if c["path"].endswith("/labels")])

    def test_apply_needs_push_access_and_check_does_not(self):
        self.state["repos"][f"{ORG}/radio"]["push"] = False
        run = self.make("labels")
        self.assertEqual(run.code, 2, run.out + run.err)
        self.assertEqual(run.writes, [])
        self.assertIn(f"{ORG}/radio: applying needs push access", run.err)
        check = self.make("labels-check")
        self.assertEqual(check.code, 1, check.out + check.err)
        self.assertIn(f"{ORG}/radio: drift: label 'hold': missing", check.out)

    def test_a_refused_write_does_not_stop_the_run(self):
        self.state["fail"] = [{"match": rf"^api -X POST repos/{ORG}/radio/labels", "status": 403,
                               "message": "Resource not accessible by integration"}]
        run = self.make("labels")
        self.assertEqual(run.code, 1, run.out + run.err)
        self.assertIn(f"{ORG}/radio: error: gh api -X POST repos/{ORG}/radio/labels: Resource not accessible", run.err)
        self.assertIn(f"{ORG}/radio: label 'hold': not created: GitHub refused a write", run.err)
        self.assertNotIn("hold", self.labels_of(f"{ORG}/radio"))
        # The other repositories were done in full, and the read-back names what's left.
        self.assertIn("hold", self.labels_of(f"{ORG}/.github"))
        self.assertIn("re-review", self.labels_of(f"{ORG}/paddock"))
        self.assertIn(f"{ORG}/radio: drift: label 'hold': missing", run.out)
        self.assertIn("failed: GitHub refused", run.err)

    def test_a_refused_read_of_the_labels_is_reported(self):
        self.state["fail"] = [{"match": rf"^api --paginate repos/{ORG}/radio/labels", "status": 403}]
        run = self.make("labels-check")
        self.assertEqual(run.code, 1, run.out + run.err)
        self.assertIn(f"{ORG}/radio: labels: not checked: GitHub refused a read", run.err)
        self.assertIn(f"{ORG}/.github: drift: label 'hold': missing", run.out)

    def test_a_bad_label_list_changes_nothing(self):
        good = label("hold", "d93f0b", "Paused")
        cases = {
            "must be a JSON object": [good],
            "unknown top-level field": {"labels": [good], "extra": 1},
            "must list at least one label": {"labels": []},
            "missing field name": {"labels": [{"color": "d93f0b", "description": "x"}]},
            "missing field color": {"labels": [{"name": "hold", "description": "x"}]},
            "missing field description": {"labels": [{"name": "hold", "color": "d93f0b"}]},
            "color must be six lowercase hex digits": {"labels": [label("hold", "#d93f0b", "x")]},
            "color must be six lowercase hex digits without": {"labels": [label("hold", "D93F0B", "x")]},
            "color must be six": {"labels": [label("hold", "d93f0", "x")]},
            "description must be 1 to 100": {"labels": [label("hold", "d93f0b", "")]},
            "description must be 1 to 100 characters": {"labels": [label("hold", "d93f0b", "x" * 101)]},
            "name must be letters": {"labels": [label("a/b", "d93f0b", "x")]},
            "unknown field colour": {"labels": [{**good, "colour": "d93f0b"}]},
            "only must list at least one repository": {"labels": [{**good, "only": []}]},
            "which is not in the repository list": {"labels": [{**good, "only": [f"{ORG}/outings"]}]},
            "listed twice": {"labels": [good, label("Hold", "ffffff", "y")]},
        }
        for message, labels in cases.items():
            with self.subTest(message=message):
                run = self.script(labels=labels)
                self.assertEqual(run.code, 2, run.out + run.err)
                self.assertIn(message, run.err)
                self.assertEqual(run.calls, [])

    def test_only_matches_a_repository_in_any_case(self):
        labels = {"labels": [{**label("agent-ready", "006b75", "x"), "only": [f"{ORG}/Paddock"]}]}
        run = self.script(labels=labels)
        self.assertEqual(run.code, 0, run.out + run.err)
        self.assertEqual(run.created, [(f"{ORG}/paddock", "agent-ready")])

    def test_bad_arguments_change_nothing(self):
        for args in (["--what"], ["--list"], ["--labels"], [f"{ORG}/radio"]):
            with self.subTest(args=args):
                run = self.script(*args)
                self.assertEqual(run.code, 2, run.out + run.err)
                self.assertEqual(run.calls, [])

    def test_the_script_says_who_runs_it(self):
        header = SCRIPT.read_text().split("set -euo pipefail")[0]
        self.assertIn("Run by the maintainer", header)
        self.assertIn("Never by an agent", header.replace("\n# ", " "))


if __name__ == "__main__":
    unittest.main()
