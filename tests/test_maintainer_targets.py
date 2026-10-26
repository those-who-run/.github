"""The maintainer's make targets and the scripts behind them: scripts/board-sync.sh,
scripts/release-envs.sh and scripts/new-repo-check.sh (protect-repo.sh has its own
tests in test_protect_repo.py). Each runs through make, as the maintainer runs it,
against a stub `gh` that keeps a pretend GitHub in a JSON file, so nothing reaches
GitHub.

The stub answers the `gh project`, `gh issue list`, `gh api`, `gh variable set` and
`gh secret set` calls the scripts make, in the shapes gh prints them, applies the
writes and logs every call with what it read on stdin.
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
LIST = ROOT / "scripts" / "repos.json"
ORG = "those-who-run"

STUB = r'''
import json, os, re, sys

state_path, log_path = os.environ["GH_STATE"], os.environ["GH_CALLS"]
state = json.load(open(state_path))
args = sys.argv[1:]
call = {"argv": args}

def done(body=None, status=0):
    with open(log_path, "a") as log:
        log.write(json.dumps(call) + "\n")
    json.dump(state, open(state_path, "w"))
    if status:
        print(json.dumps({"message": "Not Found"}))
        print(f"gh: Not Found (HTTP {status})", file=sys.stderr)
        sys.exit(1)
    if body is not None:
        print(json.dumps(body))
    sys.exit(0)

def opt(name):
    return args[args.index(name) + 1] if name in args else None

def unhandled():
    call["unhandled"] = True
    done(status=404)

# A call GitHub refuses, by a pattern over its arguments: no access (403), an expired
# login (401) and so on, with gh's summary on stderr as gh prints it.
for rule in state.get("fail", []):
    if re.search(rule["match"], " ".join(args)):
        if rule["status"] == 0:
            # Accepted but not applied: GitHub said yes and nothing changed.
            call["dropped"] = True
            with open(log_path, "a") as log:
                log.write(json.dumps(call) + "\n")
            print("{}")
            sys.exit(0)
        call["failed"] = rule["status"]
        if args[:2] in (["variable", "set"], ["secret", "set"]):
            sys.stdin.read()
        with open(log_path, "a") as log:
            log.write(json.dumps(call) + "\n")
        print(json.dumps({"message": rule.get("message", "Forbidden")}))
        print(f"gh: {rule.get('message', 'Forbidden')} (HTTP {rule['status']})", file=sys.stderr)
        sys.exit(1)

def camel(name):
    # gh names a field's value in item-list's JSON with its camelCase().
    return name[:1].lower() + name[1:]

if args[:1] == ["project"]:
    p = state["project"]
    if args[1] != "item-edit" and (args[2] != str(p["number"]) or opt("--owner") != p["owner"]):
        done(status=404)
    if args[1] == "view":
        done({"id": p["id"], "number": p["number"], "title": "Race Control"})
    if args[1] == "field-list":
        done({"fields": p["fields"], "totalCount": len(p["fields"])})
    if args[1] == "item-list":
        key = camel(p.get("status_field", "Status"))
        items = []
        for i in p["items"]:
            shown = {k: v for k, v in i.items() if k != "status"}
            if i.get("status") is not None:
                shown[key] = i["status"]
            items.append(shown)
        # gh stops at --limit; totalCount still counts every card.
        done({"items": items[:int(opt("--limit"))], "totalCount": len(items)})
    if args[1] == "item-edit":
        assert opt("--project-id") == p["id"], args
        field = [f for f in p["fields"] if f["id"] == opt("--field-id")][0]
        assert field["name"] == p.get("status_field", "Status"), field
        option = [o for o in field["options"] if o["id"] == opt("--single-select-option-id")][0]
        item = [i for i in p["items"] if i["id"] == opt("--id")][0]
        item["status"] = option["name"]
        call["moved"] = [item["id"], option["name"]]
        done({"id": item["id"]})
    unhandled()

if args[:2] == ["issue", "list"]:
    assert opt("--state") == "all" and opt("--json") == "number,state,labels", args
    done(state["issues"].get(opt("--repo"), []))

if args[:1] in (["variable"], ["secret"]) and args[1] == "set":
    repo, env, value = opt("--repo"), opt("--env"), sys.stdin.read()
    call["stdin"] = value
    store = state["envs"][repo]["variables" if args[0] == "variable" else "secrets"]
    store[args[2]] = value
    done()

assert args[0] == "api", args
method, path, headers, fields, body = "GET", None, [], {}, None
i = 1
while i < len(args):
    a = args[i]
    if a == "-X":
        method = args[i + 1]; i += 2
    elif a == "-H":
        headers.append(args[i + 1]); i += 2
    elif a == "--input":
        body = json.loads(sys.stdin.read()); i += 2
    elif a in ("-f", "-F"):
        k, v = args[i + 1].split("=", 1); fields[k] = v; i += 2
    elif a == "--paginate":
        i += 1
    else:
        path = a; i += 1
call.update(method=method, path=path)

m = re.fullmatch(r"orgs/([^/]+)/teams/([^/]+)/repos/([^/]+/[^/]+)", path)
if m:
    perms = state["teams"].get(m[2], {}).get(m[3])
    if perms is None:
        done(status=404)
    if "Accept: application/vnd.github.v3.repository+json" in headers:
        done({"full_name": m[3], "permissions": perms})
    done()
m = re.fullmatch(r"repos/([^/]+/[^/]+)/(.*)", path)
if not m:
    unhandled()
repo, rest = m[1], m[2]
rest, _, query = rest.partition("?")
if rest == "branches/main":
    done({"name": "main"} if "main" in state["branches"].get(repo, []) else None,
         0 if "main" in state["branches"].get(repo, []) else 404)
m = re.fullmatch(r"contents/(.+)", rest)
if m:
    ref = dict(q.split("=") for q in query.split("&") if q).get("ref", "main")
    files = state["files"].get(repo, {}).get(ref, [])
    done({"path": m[1]} if m[1] in files else None, 0 if m[1] in files else 404)
m = re.fullmatch(r"labels/(.+)", rest)
if m:
    name = m[1].replace("%20", " ")
    labels = state["labels"].get(repo, [])
    done({"name": name} if name in labels else None, 0 if name in labels else 404)
if rest == "actions/secrets":
    names = state["envs"].get(repo, {}).get("repo_secrets", [])
    done({"total_count": len(names), "secrets": [{"name": n} for n in names]})
m = re.fullmatch(r"environments/release-please(/.*)?", rest)
if m:
    e = state["envs"].get(repo)
    sub = m[1] or ""
    if sub == "":
        if method == "PUT":
            state["envs"].setdefault(repo, {"variables": {}, "secrets": {}, "repo_secrets": [], "policies": []})
            state["envs"][repo]["env"] = body
            done({})
        if not e or e.get("env") is None:
            done(status=404)
        done({"name": "release-please", "deployment_branch_policy": e["env"]["deployment_branch_policy"]})
    if not e or e.get("env") is None:
        done(status=404)
    if sub == "/deployment-branch-policies":
        if method == "POST":
            state["next_id"] += 1
            e["policies"].append({"id": state["next_id"], "name": fields["name"], "type": fields["type"]})
            done({})
        done({"total_count": len(e["policies"]), "branch_policies": e["policies"]})
    m = re.fullmatch(r"/deployment-branch-policies/(\d+)", sub)
    if m and method == "DELETE":
        e["policies"] = [p for p in e["policies"] if p["id"] != int(m[1])]
        done()
    if sub == "/variables":
        done({"total_count": len(e["variables"]),
              "variables": [{"name": k, "value": v} for k, v in e["variables"].items()]})
    if sub == "/secrets":
        done({"total_count": len(e["secrets"]), "secrets": [{"name": k} for k in e["secrets"]]})
unhandled()
'''

STATUS_OPTIONS = ["Needs triage", "Ready", "In progress", "In review", "Done"]


def status_field(ids=None, names=STATUS_OPTIONS, name="Status"):
    """The project's Status field, with option IDs that change when the options are
    recreated: the script must never assume them."""
    ids = ids or {o: f"opt-{n}-{o.lower().replace(' ', '-')}" for n, o in enumerate(names)}
    return {"id": "PVTSSF_status", "name": name, "type": "ProjectV2SingleSelectField",
            "options": [{"id": ids[o], "name": o} for o in names]}


def card(item_id, repo, number, status, kind="Issue"):
    return {"id": item_id, "status": status,
            "content": {"type": kind, "number": number, "repository": f"{ORG}/{repo}",
                        "title": f"{kind} {number}"}}


def issue(number, state="OPEN", labels=()):
    return {"number": number, "state": state, "labels": [{"name": n} for n in labels]}


def ready_env(**overrides):
    env = {"env": {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}},
           "policies": [{"id": 1, "name": "main", "type": "branch"}],
           "variables": {"RELEASE_APP_CLIENT_ID": "Iv23-the-client-id"},
           "secrets": {"RELEASE_APP_PRIVATE_KEY": "the key"},
           "repo_secrets": []}
    env.update(overrides)
    return env


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
    def moves(self):
        return [c["moved"] for c in self.calls if "moved" in c]

    @property
    def writes(self):
        """The writes the scripts sent, refused or not."""
        return [c for c in self.calls
                if c["argv"][:2] in (["project", "item-edit"], ["variable", "set"], ["secret", "set"])
                or c.get("method", "GET") != "GET"]

    def sent(self, *argv):
        return [c for c in self.calls if c["argv"][:len(argv)] == list(argv)]


class Targets(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        stub = self.tmp / "gh"
        stub.write_text(f"#!{sys.executable}\n{STUB}")
        stub.chmod(0o755)
        self.state_path = self.tmp / "state.json"
        self.calls_path = self.tmp / "calls.jsonl"
        self.state = {
            "project": {"owner": ORG, "number": 2, "id": "PVT_project", "items": [],
                        "fields": [{"id": "PVTF_title", "name": "Title", "type": "ProjectV2Field"},
                                   status_field()]},
            "issues": {}, "envs": {}, "files": {}, "branches": {}, "labels": {}, "teams": {},
            "next_id": 100,
        }
        # Only what the scripts need: no gh config, no token, the stub first on PATH.
        self.env = {"PATH": f"{self.tmp}{os.pathsep}{os.environ['PATH']}", "HOME": str(self.tmp),
                    "GH_STATE": str(self.state_path), "GH_CALLS": str(self.calls_path)}

    def make(self, *args, env=None):
        self.state_path.write_text(json.dumps(self.state))
        self.calls_path.write_text("")
        result = subprocess.run(["make", "--no-print-directory", "-C", str(ROOT), *args],
                                env=dict(self.env, **(env or {})), capture_output=True, text=True,
                                check=False)
        calls = [json.loads(line) for line in self.calls_path.read_text().splitlines()]
        self.state = json.loads(self.state_path.read_text())
        self.assertFalse([c for c in calls if c.get("unhandled")], "the stub didn't know a call")
        return Run(result, calls, self.state)

    def status(self, item_id):
        return [i for i in self.state["project"]["items"] if i["id"] == item_id][0]["status"]


class BoardSync(Targets):
    def setUp(self):
        super().setUp()
        self.state["project"]["items"] = [
            card("I_triage", "paddock", 1, None),
            card("I_ready", "paddock", 2, "Needs triage"),
            card("I_closed", "paddock", 3, "Ready"),
            card("I_right", "paddock", 4, "Ready"),
            card("I_working", "paddock", 5, "In progress"),
            card("I_review", "paddock", 6, "In review"),
            card("I_finished", "paddock", 7, "In progress"),
            card("I_radio", "radio", 1, "Ready"),
            card("I_pr", "paddock", 8, "In review", kind="PullRequest"),
            card("I_draft", "paddock", 0, None, kind="DraftIssue"),
        ]
        self.state["issues"] = {
            f"{ORG}/paddock": [issue(1, labels=["needs triage"]), issue(2, labels=["bug"]),
                               issue(3, "CLOSED"), issue(4), issue(5),
                               issue(6, labels=["needs triage"]), issue(7, "CLOSED")],
            f"{ORG}/radio": [issue(1, "CLOSED", labels=["needs triage"])],
        }

    def test_each_card_follows_its_issue(self):
        run = self.make("board")
        self.assertEqual(run.code, 0, run.out + run.err)
        expected = {"I_triage": "Needs triage", "I_ready": "Ready", "I_closed": "Done",
                    "I_right": "Ready", "I_radio": "Done"}
        for item_id, status in expected.items():
            self.assertEqual(self.status(item_id), status, item_id)
        self.assertIn(f"{ORG}/paddock#1: moved: no status -> Needs triage", run.out)
        self.assertIn(f"{ORG}/paddock#2: moved: Needs triage -> Ready", run.out)
        self.assertIn(f"{ORG}/paddock#3: moved: Ready -> Done", run.out)
        self.assertIn(f"{ORG}/radio#1: moved: Ready -> Done", run.out)
        self.assertIn("moved 5, already right 1, kept in progress, review or another column 2, "
                      "not found 0, refused 0; "
                      "2 cards that aren't issues left alone", run.out)

    def test_ids_are_looked_up_by_name(self):
        # Options recreated: new IDs, none of them the ones a script could have kept.
        ids = {name: f"recreated-{n}" for n, name in enumerate(reversed(STATUS_OPTIONS))}
        self.state["project"]["fields"] = [status_field(ids), {"id": "PVTF_title", "name": "Title"}]
        self.state["project"]["id"] = "PVT_another"
        run = self.make("board")
        self.assertEqual(run.code, 0, run.err)
        edits = [c["argv"] for c in run.calls if c["argv"][:2] == ["project", "item-edit"]]
        self.assertTrue(edits)
        for argv in edits:
            self.assertEqual(argv[argv.index("--project-id") + 1], "PVT_another")
            self.assertEqual(argv[argv.index("--field-id") + 1], "PVTSSF_status")
            self.assertIn(argv[argv.index("--single-select-option-id") + 1], ids.values())
        self.assertEqual(self.status("I_closed"), "Done")
        self.assertEqual(self.status("I_triage"), "Needs triage")

    def test_a_missing_option_moves_nothing(self):
        field = status_field()
        field["options"] = [o for o in field["options"] if o["name"] != "Ready"]
        self.state["project"]["fields"] = [field]
        run = self.make("board")
        self.assertEqual(run.code, 2)
        self.assertIn('Status has no option named "Ready"', run.err)
        self.assertEqual(run.writes, [])

    def test_in_progress_and_in_review_are_not_moved_back(self):
        run = self.make("board")
        self.assertEqual(run.code, 0, run.err)
        # Open issues: In progress stays, and so does In review, even with needs triage.
        self.assertEqual(self.status("I_working"), "In progress")
        self.assertEqual(self.status("I_review"), "In review")
        self.assertNotIn("paddock#5:", run.out)
        self.assertNotIn("paddock#6:", run.out)

    def test_a_closed_issue_moves_in_progress_to_done(self):
        run = self.make("board")
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(self.status("I_finished"), "Done")
        self.assertIn(f"{ORG}/paddock#7: moved: In progress -> Done", run.out)

    def test_cards_in_the_right_column_and_pull_requests_are_left_alone(self):
        run = self.make("board")
        moved = {item for item, _ in run.moves}
        self.assertNotIn("I_right", moved)
        self.assertNotIn("I_pr", moved)
        self.assertNotIn("I_draft", moved)
        self.assertEqual(self.status("I_pr"), "In review")
        # A second run finds nothing to move.
        again = self.make("board")
        self.assertEqual(again.code, 0, again.err)
        self.assertEqual(again.moves, [])
        self.assertIn("moved 0, already right 6", again.out)

    def test_issues_are_read_once_per_repository(self):
        run = self.make("board")
        lists = [c["argv"] for c in run.calls if c["argv"][:2] == ["issue", "list"]]
        self.assertEqual(sorted(a[a.index("--repo") + 1] for a in lists), [f"{ORG}/paddock", f"{ORG}/radio"])
        self.assertFalse([c for c in run.calls if c["argv"][:2] == ["issue", "view"]])

    def test_board_check_is_a_dry_run_that_changes_nothing(self):
        before = json.loads(json.dumps(self.state["project"]))
        run = self.make("board-check")
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(run.writes, [])
        self.assertEqual(self.state["project"], before)
        self.assertIn(f"{ORG}/paddock#3: would move: Ready -> Done", run.out)
        self.assertIn(f"{ORG}/paddock#7: would move: In progress -> Done", run.out)
        self.assertIn("would move 5, already right 1", run.out)

    def test_every_column_must_exist_by_name(self):
        # A renamed column, such as GitHub's default "In Progress", stops the run before
        # any move, rather than letting its cards be moved to Ready.
        for gone, renamed in (("In progress", "In Progress"), ("In review", "Review"),
                              ("Needs triage", "Triage")):
            with self.subTest(renamed=renamed):
                names = [renamed if n == gone else n for n in STATUS_OPTIONS]
                self.state["project"]["fields"] = [status_field(names=names)]
                for item in self.state["project"]["items"]:
                    if item["status"] == gone:
                        item["status"] = renamed
                before = json.loads(json.dumps(self.state["project"]))
                run = self.make("board")
                self.assertEqual(run.code, 2, run.out + run.err)
                self.assertIn(f'Status has no option named "{gone}"', run.err)
                self.assertEqual(run.writes, [])
                self.assertEqual(self.state["project"], before)
                self.setUp()

    def test_a_card_in_another_column_moves_only_to_done(self):
        # A column added later is never overwritten while its issue is open.
        self.state["project"]["fields"] = [status_field(names=[*STATUS_OPTIONS, "Blocked"])]
        self.state["project"]["items"] = [card("I_blocked", "paddock", 2, "Blocked"),
                                          card("I_blocked_triage", "paddock", 1, "Blocked"),
                                          card("I_blocked_closed", "paddock", 3, "Blocked")]
        run = self.make("board")
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(self.status("I_blocked"), "Blocked")
        self.assertEqual(self.status("I_blocked_triage"), "Blocked")
        self.assertEqual(self.status("I_blocked_closed"), "Done")
        self.assertIn(f"{ORG}/paddock#3: moved: Blocked -> Done", run.out)
        self.assertIn("moved 1, already right 0, kept in progress, review or another column 2", run.out)

    def test_a_field_with_another_name_is_read_under_ghs_key(self):
        # gh keys a field's value by its camelCase name: "Board Status" is "board Status".
        self.state["project"]["status_field"] = "Board Status"
        self.state["project"]["fields"] = [{"id": "PVTF_status", "name": "Status", "type": "ProjectV2Field"},
                                           status_field(name="Board Status")]
        self.state_path.write_text(json.dumps(self.state))
        self.calls_path.write_text("")
        result = subprocess.run(["bash", str(ROOT / "scripts" / "board-sync.sh"), "--field", "Board Status"],
                                env=self.env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.state = json.loads(self.state_path.read_text())
        # The cards with a status were read as having one: In progress and the card
        # already Ready weren't touched.
        self.assertEqual(self.status("I_working"), "In progress")
        self.assertEqual(self.status("I_closed"), "Done")
        self.assertIn("moved 5, already right 1, kept in progress, review or another column 2", result.stdout)

    def test_more_cards_than_the_limit_moves_nothing(self):
        run = self.make("board", env={"BOARD_ITEM_LIMIT": "4"})
        self.assertEqual(run.code, 2, run.out + run.err)
        self.assertIn("has 10 cards, more than the 4 read", run.err)
        self.assertEqual(run.writes, [])
        self.assertEqual(self.make("board", env={"BOARD_ITEM_LIMIT": "10"}).code, 0)

    def test_a_card_whose_issue_is_not_found_is_reported(self):
        self.state["project"]["items"].append(card("I_gone", "paddock", 99, "Ready"))
        run = self.make("board")
        self.assertEqual(run.code, 1)
        self.assertIn(f"{ORG}/paddock#99: not found", run.err)
        self.assertEqual(self.status("I_gone"), "Ready")
        self.assertEqual(self.status("I_closed"), "Done")


SECRETS = ("Iv23-the-client-id", "Iv23liVeRyNewClientId", "-----BEGIN", "the key")


class ReleaseEnvs(Targets):
    def setUp(self):
        super().setUp()
        self.releasing = [f"{ORG}/.github", f"{ORG}/radio"]
        for repo in self.releasing:
            self.state["files"][repo] = {"main": ["release-please-config.json"]}
        self.pem = self.tmp / "twr-release.private-key.pem"
        self.pem.write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIEnotarealkey\n-----END RSA PRIVATE KEY-----\n")

    def assert_no_values(self, run):
        for value in (*SECRETS, str(self.pem), "MIIEnotarealkey"):
            self.assertNotIn(value, run.out + run.err)

    def test_envs_refuses_a_missing_pem_or_client_id(self):
        cases = [
            {"CLIENT_ID": "Iv23liVeRyNewClientId", "PEM": str(self.tmp / "missing.pem")},
            {"CLIENT_ID": "Iv23liVeRyNewClientId"},
            {"PEM": str(self.pem)},
            {},
        ]
        for variables in cases:
            with self.subTest(variables=sorted(variables)):
                run = self.make("envs", *[f"{k}={v}" for k, v in variables.items()])
                self.assertEqual(run.code, 2, run.out + run.err)
                self.assertEqual(run.calls, [])
                self.assert_no_values(run)
        # The script refuses on its own too, for anyone who runs it without make.
        result = subprocess.run(["bash", str(ROOT / "scripts" / "release-envs.sh")],
                                env=dict(self.env, CLIENT_ID="Iv23liVeRyNewClientId",
                                         PEM=str(self.tmp / "missing.pem")),
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("PEM does not name a readable file", result.stderr)
        self.assertNotIn("missing.pem", result.stderr)
        self.assertEqual(self.calls_path.read_text(), "")

    def test_envs_sets_up_every_releasing_repository_without_printing_a_value(self):
        self.state["envs"][f"{ORG}/radio"] = ready_env(
            policies=[{"id": 7, "name": "release/*", "type": "branch"}], variables={}, secrets={})
        run = self.make("envs", "CLIENT_ID=Iv23liVeRyNewClientId", f"PEM={self.pem}")
        self.assertEqual(run.code, 0, run.out + run.err)
        self.assert_no_values(run)
        for repo in self.releasing:
            env = self.state["envs"][repo]
            self.assertEqual(env["env"]["deployment_branch_policy"],
                             {"protected_branches": False, "custom_branch_policies": True})
            self.assertEqual([(p["name"], p["type"]) for p in env["policies"]], [("main", "branch")])
            self.assertEqual(env["variables"], {"RELEASE_APP_CLIENT_ID": "Iv23liVeRyNewClientId"})
            self.assertEqual(env["secrets"], {"RELEASE_APP_PRIVATE_KEY": self.pem.read_text()})
            self.assertIn(f"{repo}: release-please environment set up", run.out)
        # A repository without release-please-config.json doesn't release.
        self.assertNotIn(f"{ORG}/paddock", self.state["envs"])
        self.assertIn(f"{ORG}/paddock: no release-please-config.json", run.out)
        # The values went to gh on stdin, never as an argument.
        for c in run.calls:
            for value in ("Iv23liVeRyNewClientId", str(self.pem)):
                self.assertNotIn(value, " ".join(c["argv"]))
        self.assertIn("every release-please environment is set up", run.out)

    def test_envs_check_reports_names_and_flags_a_repository_secret(self):
        self.state["envs"][f"{ORG}/.github"] = ready_env()
        self.state["envs"][f"{ORG}/radio"] = ready_env(repo_secrets=["RELEASE_APP_PRIVATE_KEY", "OTHER"])
        run = self.make("envs-check")
        self.assertEqual(run.code, 1, run.out + run.err)
        self.assertEqual(run.writes, [])
        self.assert_no_values(run)
        for repo in self.releasing:
            self.assertIn(f"{repo}: custom_branch_policies: true", run.out)
            self.assertIn(f"{repo}: branch policies: main (branch)", run.out)
            self.assertIn(f"{repo}: environment secrets: RELEASE_APP_PRIVATE_KEY", run.out)
            self.assertIn(f"{repo}: environment variables: RELEASE_APP_CLIENT_ID", run.out)
        self.assertIn(f"{ORG}/.github: repository secrets: none", run.out)
        self.assertIn(f"{ORG}/radio: repository secrets: OTHER, RELEASE_APP_PRIVATE_KEY", run.out)
        self.assertIn(f"{ORG}/radio: wrong: RELEASE_APP_PRIVATE_KEY is a repository secret", run.out)
        self.assertNotIn(f"{ORG}/.github: wrong", run.out)
        self.assertIn("1 problem(s)", run.out)

    def test_envs_check_flags_a_missing_or_open_environment(self):
        self.state["envs"][f"{ORG}/.github"] = ready_env(
            env={"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": False}},
            policies=[], secrets={})
        run = self.make("envs-check")
        self.assertEqual(run.code, 1)
        for line in (f"{ORG}/.github: custom_branch_policies: false",
                     f"{ORG}/.github: wrong: custom_branch_policies is false",
                     f"{ORG}/.github: wrong: the branch policies must be main alone",
                     f"{ORG}/.github: wrong: no environment secret RELEASE_APP_PRIVATE_KEY",
                     f"{ORG}/radio: wrong: no release-please environment"):
            self.assertIn(line, run.out)

    def test_a_refused_branch_policy_write_never_stores_the_key(self):
        # radio has a policy other than main, and no main: the setup must add main and
        # remove the other before the key goes in.
        for refused in (r"^api -X POST repos/those-who-run/radio/environments/release-please/deployment-branch-policies -f",
                        r"^api -X DELETE repos/those-who-run/radio/environments/release-please/deployment-branch-policies/7$",
                        r"^api -X PUT repos/those-who-run/radio/environments/release-please --input -$"):
            with self.subTest(refused=refused):
                self.state["envs"] = {f"{ORG}/radio": ready_env(
                    policies=[{"id": 7, "name": "*", "type": "branch"}], variables={}, secrets={})}
                self.state["fail"] = [{"match": refused, "status": 422, "message": "Validation Failed"}]
                run = self.make("envs", "CLIENT_ID=Iv23liVeRyNewClientId", f"PEM={self.pem}")
                self.assertEqual(run.code, 1, run.out + run.err)
                self.assertIn(f"{ORG}/radio: not set up: GitHub refused", run.err)
                self.assertEqual(run.sent("secret", "set", "RELEASE_APP_PRIVATE_KEY", "--env",
                                           "release-please", "--repo", f"{ORG}/radio"), [])
                self.assertEqual(run.sent("variable", "set", "RELEASE_APP_CLIENT_ID", "--env",
                                           "release-please", "--repo", f"{ORG}/radio"), [])
                self.assertEqual(self.state["envs"][f"{ORG}/radio"]["secrets"], {})
                # The other repository was set up in full.
                self.assertEqual(self.state["envs"][f"{ORG}/.github"]["secrets"],
                                 {"RELEASE_APP_PRIVATE_KEY": self.pem.read_text()})
                self.assert_no_values(run)

    def test_the_key_waits_for_the_policies_read_back(self):
        # GitHub accepts the writes but the environment still isn't main alone, or
        # still allows every branch: the read-back stops the setup before the key.
        for dropped, why in (
            (r"^api -X DELETE repos/those-who-run/radio/environments/release-please/deployment-branch-policies/7$",
             "its branch policies aren't main alone"),
            (r"^api -X PUT repos/those-who-run/radio/environments/release-please --input -$",
             "custom_branch_policies isn't true"),
        ):
            with self.subTest(dropped=dropped):
                self.state["envs"] = {f"{ORG}/radio": ready_env(
                    env={"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": False}},
                    policies=[{"id": 7, "name": "*", "type": "branch"}], variables={}, secrets={})}
                self.state["fail"] = [{"match": dropped, "status": 0}]
                run = self.make("envs", "CLIENT_ID=Iv23liVeRyNewClientId", f"PEM={self.pem}")
                self.assertEqual(run.code, 1, run.out + run.err)
                self.assertIn(f"{ORG}/radio: not set up: read back after the writes, "
                              f"the environment is not safe for the key: {why}", run.err)
                self.assertEqual(self.state["envs"][f"{ORG}/radio"]["secrets"], {})
                self.assertEqual(self.state["envs"][f"{ORG}/radio"]["variables"], {})

    def test_a_refused_read_of_the_config_is_not_doesnt_release(self):
        # A 403 or an expired login isn't a 404: the run stops, having written nothing.
        for status in (403, 401):
            for target in (["envs-check"], ["envs", "CLIENT_ID=Iv23liVeRyNewClientId", f"PEM={self.pem}"]):
                with self.subTest(status=status, target=target[0]):
                    self.state["fail"] = [{"match": r"^api repos/those-who-run/\.github/contents/release-please-config\.json$",
                                           "status": status, "message": "Forbidden"}]
                    run = self.make(*target)
                    self.assertEqual(run.code, 2, run.out + run.err)
                    self.assertIn(f"{ORG}/.github: can't tell whether it releases", run.err)
                    self.assertIn(f"HTTP {status}", run.err)
                    self.assertNotIn(f"{ORG}/.github: no release-please-config.json", run.out)
                    self.assertEqual(run.writes, [])
                    self.assert_no_values(run)

    def test_every_call_refused_fails(self):
        self.state["fail"] = [{"match": ".", "status": 401, "message": "Bad credentials"}]
        for target in (["envs-check"], ["envs", "CLIENT_ID=Iv23liVeRyNewClientId", f"PEM={self.pem}"]):
            with self.subTest(target=target[0]):
                run = self.make(*target)
                self.assertEqual(run.code, 2, run.out + run.err)
                self.assertIn("Bad credentials", run.err)
                self.assertNotIn("set up", run.out)
                self.assertEqual([c for c in run.writes if not c.get("failed")], [])

    def test_no_releasing_repository_fails(self):
        self.state["files"] = {}
        run = self.make("envs-check")
        self.assertEqual(run.code, 2, run.out + run.err)
        self.assertIn("no repository to look at", run.err)

    def test_a_pem_path_with_spaces(self):
        folder = self.tmp / "release app keys"
        folder.mkdir()
        pem = folder / "twr release key.pem"
        pem.write_text(self.pem.read_text())
        run = self.make("envs", "CLIENT_ID=Iv23liVeRyNewClientId", f"PEM={pem}")
        self.assertEqual(run.code, 0, run.out + run.err)
        for repo in self.releasing:
            self.assertEqual(self.state["envs"][repo]["secrets"], {"RELEASE_APP_PRIVATE_KEY": pem.read_text()})
        self.assertNotIn(str(pem), run.out + run.err)
        self.assertNotIn("release app keys", run.out + run.err)

    def test_envs_check_passes_when_every_environment_is_set_up(self):
        for repo in self.releasing:
            self.state["envs"][repo] = ready_env()
        run = self.make("envs-check")
        self.assertEqual(run.code, 0, run.out)
        self.assertNotIn("wrong", run.out)
        self.assert_no_values(run)


class NewRepoCheck(Targets):
    def complete(self, name):
        repo = f"{ORG}/{name}"
        self.state["branches"][repo] = ["main"]
        self.state["files"][repo] = {"main": ["LICENSE", ".github/CODEOWNERS", "release-please-config.json"]}
        self.state["teams"]["pit-crew"] = {repo: {"admin": False, "maintain": False, "push": True,
                                                  "triage": True, "pull": True}}
        self.state["envs"][repo] = ready_env()
        self.state["labels"][f"{ORG}/paddock"] = [f"component: {name}", "needs triage"]

    def test_a_repository_with_everything_passes(self):
        self.complete("radio")
        run = self.make("new-repo-check", "REPO=radio")
        self.assertEqual(run.code, 0, run.out + run.err)
        self.assertEqual(run.writes, [])
        for line in ("main exists", "LICENSE on main", "team pit-crew has push access",
                     "in scripts/repos.json", "release-please environment set up",
                     f"{ORG}/paddock has the label 'component: radio'", ".github/CODEOWNERS on main"):
            self.assertIn(f"pass: {ORG}/radio: {line}", run.out)
        self.assertNotIn("fail:", run.out)
        self.assertIn(f"every check passed for {ORG}/radio", run.out)

    def test_a_repository_missing_things_fails_each_one(self):
        name = "brand-new"
        self.assertNotIn(f"{ORG}/{name}", json.loads(LIST.read_text())["repositories"])
        self.complete(name)
        repo = f"{ORG}/{name}"
        self.state["files"][repo]["main"] = ["LICENSE"]
        self.state["teams"]["pit-crew"][repo]["push"] = False
        self.state["envs"][repo] = ready_env(repo_secrets=["RELEASE_APP_PRIVATE_KEY"])
        self.state["labels"][f"{ORG}/paddock"] = []
        run = self.make("new-repo-check", f"REPO={ORG}/{name}")
        self.assertEqual(run.code, 1, run.out + run.err)
        self.assertEqual(run.writes, [])
        for line in (f"pass: {repo}: main exists", f"pass: {repo}: LICENSE on main",
                     f"fail: {repo}: team pit-crew has no push access",
                     f"fail: {repo}: not in scripts/repos.json",
                     f"fail: {repo}: release-please environment not set up",
                     "    RELEASE_APP_PRIVATE_KEY is a repository secret",
                     f"fail: {repo}: {ORG}/paddock has no label 'component: {name}'",
                     f"fail: {repo}: no .github/CODEOWNERS on main"):
            self.assertIn(line, run.out)
        self.assertIn(f"5 check(s) failed for {repo}", run.out)

    def test_a_repository_without_main(self):
        run = self.make("new-repo-check", "REPO=nothing-yet")
        self.assertEqual(run.code, 1)
        self.assertIn(f"fail: {ORG}/nothing-yet: no main branch", run.out)
        self.assertIn(f"fail: {ORG}/nothing-yet: no LICENSE: there is no main branch", run.out)

    def test_it_needs_a_repository_name(self):
        for args in ([], ["REPO=../etc"], ["REPO=a b"]):
            with self.subTest(args=args):
                run = self.make("new-repo-check", *args)
                self.assertEqual(run.code, 2)
                self.assertEqual(run.calls, [])


class Makefile(unittest.TestCase):
    TARGETS = ["board-check", "board", "protect-check", "protect", "envs-check", "envs", "new-repo-check"]

    def test_help_lists_the_targets_under_the_maintainer_heading(self):
        out = subprocess.run(["make", "--no-print-directory", "-C", str(ROOT), "help"],
                             capture_output=True, text=True, check=True).stdout
        heading, _, after = out.partition("Maintainer only (your own gh login)")
        self.assertTrue(after, out)
        listed = [line.split(" ", 1)[0] for line in after.splitlines()[1:] if line]
        self.assertEqual(listed, self.TARGETS)
        for target in self.TARGETS:
            self.assertNotIn(f"\n{target} ", heading)

    def test_the_scripts_say_who_runs_them(self):
        for name in ("board-sync.sh", "release-envs.sh", "new-repo-check.sh"):
            header = (ROOT / "scripts" / name).read_text().split("set -euo pipefail")[0]
            with self.subTest(script=name):
                self.assertIn("Run by the maintainer", header)
                self.assertIn("Never by an agent", header.replace("\n# ", " "))


if __name__ == "__main__":
    unittest.main()
