"""scripts/protect-repo.sh and its list, scripts/repos.json, run against a stub `gh`
that keeps a pretend GitHub's settings in a JSON file, so nothing reaches GitHub.

The stub answers the reads the script makes in the shapes GitHub's REST API returns
them, applies its writes, and logs every call. That is enough to see what a run would
send, that a second run changes nothing, and that --check reports a difference
without writing anything. Lists come back in pages of PAGE, as GitHub's do (30 to a
page there), so a read that doesn't paginate misses what is on a later page. A write
can be made to fail with GitHub's error body (state["fail"]), to see that the run
carries on past it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "protect-repo.sh"
LIST = ROOT / "scripts" / "repos.json"
MAINTAINER_ID = 1001
# The release App's ID in the lists these tests write (repos.json holds the real one).
APP_ID = 424242
PAGE = 2

STUB = r'''
import base64, json, os, re, sys

PAGE = %d

state_path, log_path = os.environ["GH_STATE"], os.environ["GH_CALLS"]
state = json.load(open(state_path))
args = sys.argv[1:]
call = {"argv": args, "method": "GET", "path": None, "fields": {}, "input": None, "query": ""}
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
    elif a in ("-f", "-F"):
        k, v = args[i + 1].split("=", 1)
        if a == "-F":
            v = {"true": True, "false": False}.get(v, int(v) if v.isdigit() else v)
        call["fields"][k] = v; i += 2
    else:
        call["path"], _, call["query"] = a.partition("?"); i += 1

# A write GitHub refuses: its error body on stdout and gh's summary on stderr, as gh
# prints them.
for rule in state.get("fail", []):
    if call["method"] == rule["method"] and re.search(rule["path"], call["path"]):
        call["failed"] = True
        with open(log_path, "a") as log:
            log.write(json.dumps(call) + "\n")
        print(json.dumps({"message": rule["message"], "errors": rule.get("errors", []), "status": "422"}))
        print(f"gh: {rule['message']} (HTTP 422)", file=sys.stderr)
        sys.exit(1)

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

method, path, fields, body = call["method"], call["path"], call["fields"], call["input"]
m = re.fullmatch(r"users/([^/]+)", path)
if m:
    uid = state["users"].get(m[1])
    done({"login": m[1], "id": uid} if uid else None, 0 if uid else 404)
m = re.fullmatch(r"repos/([^/]+/[^/]+)(/.*)?", path)
if not m or m[1] not in state["repos"]:
    done(status=404)
r, rest = state["repos"][m[1]], m[2] or ""

def done_pages(items, page_body=lambda page: page):
    # gh --paginate prints each page's JSON one after another; without it, only the first.
    pages = [items[n:n + PAGE] for n in range(0, len(items), PAGE)] or [[]]
    if not call.get("paginate"):
        pages = pages[:1]
    with open(log_path, "a") as log:
        log.write(json.dumps(call) + "\n")
    json.dump(state, open(state_path, "w"))
    for page in pages:
        print(json.dumps(page_body(page)))
    sys.exit(0)

def next_id():
    state["next_id"] += 1
    return state["next_id"]

if rest == "":
    if method == "GET":
        done({k: v for k, v in r.items() if not k.startswith("_")})
    for k, v in fields.items():
        keys = re.findall(r"[^\[\]]+", k)
        target = r
        for key in keys[:-1]:
            target = target.setdefault(key, {})
        target[keys[-1]] = v
    done({})
if rest == "/rulesets":
    if method == "GET":
        # GitHub adds the organisation's rulesets unless includes_parents=false.
        own_only = "includes_parents=false" in call["query"].split("&")
        listed = r["_rulesets"] + ([] if own_only else state.get("org_rulesets", []))
        done_pages([{"id": x["id"], "name": x["name"], "target": x["target"],
                     "enforcement": x["enforcement"],
                     "source_type": x.get("source_type", "Repository")} for x in listed])
    r["_rulesets"].append(dict(body, id=next_id()))
    done({})
m = re.fullmatch(r"/rulesets/(\d+)", rest)
if m:
    found = [x for x in r["_rulesets"] if x["id"] == int(m[1])]
    if not found:
        done(status=404)
    if method == "GET":
        # GitHub adds fields of its own and needn't keep the order it was sent.
        shown = json.loads(json.dumps(found[0]))
        shown.update(source="x/y", source_type="Repository")
        shown["rules"] = list(reversed(shown["rules"]))
        for rule in shown["rules"]:
            if rule["type"] == "pull_request":
                rule["parameters"]["required_reviewers"] = []
        # GitHub shows the bypass list only to someone who may edit the ruleset.
        if not r["permissions"].get("admin"):
            del shown["bypass_actors"]
        done(shown)
    r["_rulesets"] = [x for x in r["_rulesets"] if x["id"] != int(m[1])] + [dict(body, id=int(m[1]))]
    done({})
if rest == "/vulnerability-alerts":
    if method == "GET":
        done(None, 0 if r["_alerts"] else 404)
    r["_alerts"] = True; done()
if rest == "/private-vulnerability-reporting":
    if method == "GET":
        done({"enabled": r["_pvr"]})
    r["_pvr"] = True; done()
simple = {
    "/actions/permissions": "_actions",
    "/actions/permissions/selected-actions": "_selected",
    "/actions/permissions/workflow": "_workflow",
    "/actions/permissions/fork-pr-contributor-approval": "_fork",
}
if rest in simple:
    if method == "GET":
        done(r[simple[rest]])
    r[simple[rest]].update(body if body is not None else fields)
    done({})
if rest == "/contents/.github/CODEOWNERS":
    text = r["_codeowners"]
    if text is True:
        text = "* @ciaranRoche\n"
    if not text:
        done(status=404)
    # GitHub sends the file base64-encoded, wrapped at 60 characters.
    encoded = base64.b64encode(text.encode()).decode()
    done({"name": "CODEOWNERS", "path": ".github/CODEOWNERS", "encoding": "base64",
          "content": "\n".join(encoded[n:n + 60] for n in range(0, len(encoded), 60)) + "\n"})
if rest == "/environments/release":
    if method == "GET":
        env = r["_env"]
        if env is None:
            done(status=404)
        logins = {v: k for k, v in state["users"].items()}
        done({"name": "release", "can_admins_bypass": env["can_admins_bypass"],
              "deployment_branch_policy": env["deployment_branch_policy"],
              "protection_rules": [
                  {"id": 1, "type": "required_reviewers", "prevent_self_review": env["prevent_self_review"],
                   "reviewers": [{"type": x["type"], "reviewer": {"login": logins[x["id"]], "id": x["id"]}}
                                 for x in env["reviewers"]]},
                  {"id": 2, "type": "branch_policy"}]})
    r["_env"] = body; done({})
if rest == "/environments/release/deployment-branch-policies":
    if r["_env"] is None:
        done(status=404)
    if method == "GET":
        done_pages(r["_policies"], lambda page: {"total_count": len(r["_policies"]), "branch_policies": page})
    r["_policies"].append(dict(body, id=next_id())); done({})
m = re.fullmatch(r"/environments/release/deployment-branch-policies/(\d+)", rest)
if m and method == "DELETE":
    r["_policies"] = [p for p in r["_policies"] if p["id"] != int(m[1])]; done()
if rest == "/code-scanning/default-setup":
    if method == "GET":
        done(r["_codeql"])
    r["_codeql"] = {"state": body["state"], "languages": body.get("languages", []), "query_suite": "default"}
    done({})
call["unhandled"] = True
done(status=404)
''' % PAGE


def fresh_repo(**overrides):
    """A repository as GitHub creates it, before this script has touched it."""
    repo = {
        "private": False, "has_issues": True, "permissions": {"admin": True},
        "allow_squash_merge": True, "allow_merge_commit": True, "allow_rebase_merge": True,
        "squash_merge_commit_title": "COMMIT_OR_PR_TITLE", "delete_branch_on_merge": False,
        "allow_auto_merge": False,
        "security_and_analysis": {"secret_scanning": {"status": "disabled"},
                                  "secret_scanning_push_protection": {"status": "disabled"}},
        "_rulesets": [], "_alerts": False, "_pvr": False, "_codeowners": True,
        "_actions": {"enabled": True, "allowed_actions": "all", "sha_pinning_required": False},
        "_selected": {}, "_workflow": {"default_workflow_permissions": "write",
                                       "can_approve_pull_request_reviews": True},
        "_fork": {"approval_policy": "first_time_contributors"},
        "_env": None, "_policies": [],
        "_codeql": {"state": "not-configured", "languages": [], "query_suite": "default"},
    }
    repo.update(overrides)
    return repo


def entry(**overrides):
    value = {"checks": ["test"], "actions": True, "approvals": 1, "code_owners": True,
             "actions_prs": False, "issues": False, "publishes": False, "tags": ["v*"],
             "codeql": []}
    value.update(overrides)
    return value


class Run:
    def __init__(self, result, calls, state):
        self.code, self.out, self.err = result.returncode, result.stdout, result.stderr
        self.calls, self.state = calls, state

    @property
    def writes(self):
        return [c for c in self.calls if c["method"] != "GET"]

    @property
    def written(self):
        """The writes GitHub accepted."""
        return [c for c in self.writes if not c.get("failed")]

    def sent(self, path_end):
        return [c for c in self.writes if c["path"].endswith(path_end)]


class ProtectRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        stub = tmp / "gh"
        stub.write_text(f"#!{sys.executable}\n{STUB}")
        stub.chmod(0o755)
        self.state_path = tmp / "state.json"
        self.calls_path = tmp / "calls.jsonl"
        self.list_path = tmp / "repos.json"
        self.state = {"repos": {}, "users": {"ciaranRoche": MAINTAINER_ID}, "next_id": 100}
        # The list in the repository with the tests' App ID, for one-off runs, so
        # their rulesets don't depend on the real ID.
        self.list_with_app = tmp / "repos-with-app.json"
        listed = json.loads(LIST.read_text())
        listed["release_app_id"] = APP_ID
        self.list_with_app.write_text(json.dumps(listed))
        self.env = dict(os.environ, PATH=f"{tmp}{os.pathsep}{os.environ['PATH']}",
                        GH_STATE=str(self.state_path), GH_CALLS=str(self.calls_path))

    def write_list(self, repositories, maintainer="ciaranRoche", release_app_id=APP_ID):
        self.list_path.write_text(json.dumps(
            {"maintainer": maintainer, "release_app_id": release_app_id, "repositories": repositories}))

    def run_script(self, *args, list_file=None):
        self.state_path.write_text(json.dumps(self.state))
        self.calls_path.write_text("")
        if list_file is None:
            list_file = self.list_path if self.list_path.exists() else self.list_with_app
        result = subprocess.run(["bash", str(SCRIPT), *args, "--list", str(list_file)],
                                env=self.env, capture_output=True, text=True, check=False)
        calls = [json.loads(line) for line in self.calls_path.read_text().splitlines()]
        self.state = json.loads(self.state_path.read_text())
        self.assertFalse([c for c in calls if c.get("unhandled")], "the stub didn't know a call")
        return Run(result, calls, self.state)

    def ruleset(self, repo, name):
        found = [r for r in self.state["repos"][repo]["_rulesets"] if r["name"] == name]
        self.assertEqual(len(found), 1, name)
        return found[0]

    # One-off runs, with options, for a repository not in the list.

    def test_actions_prs_without_code_owners_changes_nothing(self):
        self.state["repos"]["example/repo"] = fresh_repo()
        run = self.run_script("example/repo", "--approvals", "1", "--actions-prs")
        self.assertEqual(run.code, 2)
        self.assertIn("--actions-prs needs --code-owners", run.err)
        self.assertEqual(run.calls, [])

    def test_actions_prs_with_no_actions_changes_nothing(self):
        self.state["repos"]["example/repo"] = fresh_repo()
        run = self.run_script("example/repo", "--code-owners", "--actions-prs", "--no-actions")
        self.assertEqual(run.code, 2)
        self.assertEqual(run.calls, [])

    def test_actions_prs_with_code_owners_lets_actions_open_prs(self):
        self.state["repos"]["example/repo"] = fresh_repo()
        run = self.run_script("example/repo", "--approvals", "1", "--code-owners", "--actions-prs")
        self.assertEqual(run.code, 0, run.err)
        self.assertIs(self.state["repos"]["example/repo"]["_workflow"]["can_approve_pull_request_reviews"], True)
        main = self.ruleset("example/repo", "main")
        pr = [r for r in main["rules"] if r["type"] == "pull_request"][0]["parameters"]
        self.assertIs(pr["require_code_owner_review"], True)

    def test_actions_prs_without_a_codeowners_file_changes_nothing(self):
        self.state["repos"]["example/repo"] = fresh_repo(_codeowners=False)
        run = self.run_script("example/repo", "--code-owners", "--actions-prs")
        self.assertEqual(run.code, 2)
        self.assertIn("CODEOWNERS", run.err)
        self.assertEqual(run.writes, [])

    def test_by_default_actions_cannot_approve(self):
        for args in ([], ["--code-owners"]):
            with self.subTest(args=args):
                self.state["repos"]["example/repo"] = fresh_repo()
                run = self.run_script("example/repo", "--approvals", "1", *args)
                self.assertEqual(run.code, 0, run.err)
                self.assertIs(self.state["repos"]["example/repo"]["_workflow"]["can_approve_pull_request_reviews"], False)

    def test_check_names_with_spaces_and_slashes_reach_github_whole(self):
        self.state["repos"]["example/repo"] = fresh_repo()
        run = self.run_script("example/repo", "--checks", "test lint",
                              "--check-name", "checks / checks", "--check-name", "image / build")
        self.assertEqual(run.code, 0, run.err)
        sent = json.dumps(run.sent("/rulesets")[0]["input"])
        for name in ("test", "lint", "checks / checks", "image / build"):
            self.assertIn(json.dumps({"context": name, "integration_id": 15368}), sent)

    def test_one_off_leaves_issues_and_codeql_alone(self):
        self.state["repos"]["example/repo"] = fresh_repo()
        run = self.run_script("example/repo")
        self.assertEqual(run.code, 0, run.err)
        self.assertIs(self.state["repos"]["example/repo"]["has_issues"], True)
        self.assertFalse(run.sent("/code-scanning/default-setup"))

    def test_bad_arguments_change_nothing(self):
        self.state["repos"]["example/repo"] = fresh_repo()
        for args in (["--all", "example/repo"], ["--all", "--approvals", "1"], [],
                     ["example/repo", "--approvals", "one"], ["example/repo", "--checks"],
                     ["example/repo", "other/repo"], ["example/repo", "--frobnicate"]):
            with self.subTest(args=args):
                run = self.run_script(*args)
                self.assertEqual(run.code, 2)
                self.assertEqual(run.calls, [])

    # The list.

    def test_the_list_in_the_repository_is_valid_and_applies(self):
        listed = json.loads(LIST.read_text())["repositories"]
        for name in listed:
            self.state["repos"][name] = fresh_repo()
        run = self.run_script("--all", list_file=LIST)
        self.assertEqual(run.code, 0, run.err)
        self.assertNotIn("refused", run.err)
        self.assertEqual(self.run_script("--check", "--all", list_file=LIST).code, 0)
        self.assertNotIn("those-who-run/those-who-run", listed)
        self.assertEqual(listed["those-who-run/.github"]["checks"][:2], ["test", "checks / checks"])

    def test_the_list_in_the_repository_names_the_app(self):
        listed = json.loads(LIST.read_text())
        self.assertIs(type(listed["release_app_id"]), int)
        self.assertGreater(listed["release_app_id"], 0)
        for name in listed["repositories"]:
            self.state["repos"][name] = fresh_repo()
        self.assertEqual(self.run_script("--all", list_file=LIST).code, 0)
        for name in listed["repositories"]:
            self.assertEqual(self.ruleset(name, "release tag creation")["bypass_actors"],
                             [{"actor_id": listed["release_app_id"], "actor_type": "Integration",
                               "bypass_mode": "always"}])

    def test_the_list_in_the_repository_with_a_placeholder(self):
        # A new organisation or a fork starts with a placeholder.
        listed = json.loads(LIST.read_text())
        listed["release_app_id"] = "PLACEHOLDER: the App's ID"
        self.list_path.write_text(json.dumps(listed))
        for name in listed["repositories"]:
            self.state["repos"][name] = fresh_repo()
        # Everything but the creation ruleset applies; the run exits 1.
        run = self.run_script("--all", list_file=self.list_path)
        self.assertEqual(run.code, 1, run.err)
        for name in listed["repositories"]:
            self.assertIn(f"{name}: refused: ruleset 'release tag creation': release_app_id", run.err)
        self.assertEqual(run.err.count("refused:"), len(listed["repositories"]))
        names = {r["name"] for r in self.state["repos"]["those-who-run/.github"]["_rulesets"]}
        self.assertEqual(names, {"main", "release tags"})

    def test_github_needs_no_actions_prs_and_runs_codeql_on_go(self):
        dotgithub = json.loads(LIST.read_text())["repositories"]["those-who-run/.github"]
        # release-please opens its PR as the App, so Actions needn't open PRs.
        self.assertIs(dotgithub["actions_prs"], False)
        # The Go fixture that tests go.yml is Go code CodeQL reads.
        self.assertIn("go", dotgithub["codeql"])
        self.state["repos"]["those-who-run/.github"] = fresh_repo()
        run = self.run_script("those-who-run/.github", list_file=LIST)
        self.assertEqual(run.code, 0, run.err)
        repo = self.state["repos"]["those-who-run/.github"]
        self.assertEqual(sorted(repo["_codeql"]["languages"]), ["actions", "go", "python"])
        self.assertIs(repo["_workflow"]["can_approve_pull_request_reviews"], False)

    def test_listed_check_names_reach_github_whole(self):
        self.write_list({"example/repo": entry(checks=["test", "checks / checks", "image / build"])})
        self.state["repos"]["example/repo"] = fresh_repo()
        run = self.run_script("example/repo")
        self.assertEqual(run.code, 0, run.err)
        sent = json.dumps(run.sent("/rulesets")[0]["input"])
        self.assertIn('"context": "checks / checks"', sent)
        self.assertIn('"context": "image / build"', sent)
        self.assertNotIn('"context": "checks"', sent)

    def status_checks(self, repo):
        main = self.ruleset(repo, "main")
        found = [r for r in main["rules"] if r["type"] == "required_status_checks"]
        self.assertEqual(len(found), 1)
        return found[0]["parameters"]

    def test_every_listed_repository_merges_only_up_to_date_branches(self):
        # Two PRs, each green on its own head, broke `main` once both were merged:
        # strict checks make a branch catch up with `main` and pass again first.
        repositories = json.loads(LIST.read_text())["repositories"]
        for name in repositories:
            self.state["repos"][name] = fresh_repo()
        run = self.run_script("--all", list_file=self.list_with_app)
        self.assertEqual(run.code, 0, run.err)
        for name, listed in repositories.items():
            with self.subTest(repo=name):
                self.assertTrue(listed["checks"], "a listed repository with no checks has no strict policy")
                self.assertIs(self.status_checks(name)["strict_required_status_checks_policy"], True)

    def test_a_one_off_with_checks_merges_only_up_to_date_branches(self):
        self.state["repos"]["example/repo"] = fresh_repo()
        run = self.run_script("example/repo", "--checks", "test")
        self.assertEqual(run.code, 0, run.err)
        sent = [r for r in run.sent("/rulesets")[0]["input"]["rules"] if r["type"] == "required_status_checks"]
        self.assertIs(sent[0]["parameters"]["strict_required_status_checks_policy"], True)
        self.assertIs(self.status_checks("example/repo")["strict_required_status_checks_policy"], True)

    def test_a_bad_list_changes_nothing(self):
        cases = {
            "missing field tags": {"example/repo": {k: v for k, v in entry().items() if k != "tags"}},
            "unknown field colour": {"example/repo": entry(colour="red")},
            "actions_prs needs code_owners": {"example/repo": entry(actions_prs=True, code_owners=False)},
            "actions_prs and actions off": {"example/repo": entry(actions_prs=True, actions=False, checks=[])},
            "publishes needs actions": {"example/repo": entry(publishes=True, actions=False, checks=[])},
            "checks need actions": {"example/repo": entry(actions=False)},
            "codeql needs actions": {"example/repo": entry(actions=False, checks=[], codeql=["go"])},
            "approvals must be a whole number": {"example/repo": entry(approvals=-1)},
            "approvals must be a whole number,": {"example/repo": entry(approvals=1.5)},
            "approvals must be a whole number, 0": {"example/repo": entry(approvals="1")},
            "tags must list at least one pattern": {"example/repo": entry(tags=[])},
            "without refs/tags/": {"example/repo": entry(tags=["refs/tags/v*"])},
            "codeql languages are": {"example/repo": entry(codeql=["golang"])},
            # Not in what the default-setup PATCH accepts, so it fails here, not mid-run.
            "codeql languages are actions,": {"example/repo": entry(codeql=["rust"])},
            "prevent_self_review must be true or false": {"example/repo": entry(publishes=True, prevent_self_review="no")},
            "prevent_self_review needs publishes": {"example/repo": entry(prevent_self_review=True)},
            "merge_queue must be true or false": {"example/repo": entry(merge_queue="yes")},
            "merge_queue needs checks": {"example/repo": entry(checks=[], merge_queue=True)},
            "checks must be a list of names": {"example/repo": entry(checks="test lint")},
            "checks has a name twice": {"example/repo": entry(checks=["test", "test"])},
            "issues must be true or false": {"example/repo": entry(issues=None)},
            "not an owner/repo name": {"example repo": entry()},
            "listed twice": {"example/repo": entry(), "Example/Repo": entry()},
        }
        app_cases = {
            "release_app_id must be": None,
            "release_app_id must be the": "424242",
            "release_app_id must be the release": 0,
            "release_app_id must be the release App's": 4.5,
            "release_app_id must be the release App's App ID": "TODO",
        }
        self.state["repos"]["example/repo"] = fresh_repo()
        for message, value in app_cases.items():
            with self.subTest(message):
                self.write_list({"example/repo": entry()}, release_app_id=value)
                for args in (["--all"], ["--check", "--all"], ["example/repo"]):
                    run = self.run_script(*args)
                    self.assertEqual(run.code, 2, run.out)
                    self.assertIn(message, run.err)
                    self.assertEqual(run.calls, [])
        for message, repositories in cases.items():
            with self.subTest(message):
                self.write_list(repositories)
                for args in (["--all"], ["--check", "--all"]):
                    run = self.run_script(*args)
                    self.assertEqual(run.code, 2, run.out)
                    self.assertIn(message, run.err)
                    self.assertEqual(run.calls, [])
        self.write_list({"example/repo": entry()}, maintainer="")
        self.assertIn("maintainer", self.run_script("--all").err)
        self.list_path.write_text("{not json")
        self.assertEqual(self.run_script("--all").code, 2)

    def test_a_listed_repository_takes_no_options(self):
        self.write_list({"example/repo": entry()})
        self.state["repos"]["example/repo"] = fresh_repo()
        run = self.run_script("example/repo", "--approvals", "0")
        self.assertEqual(run.code, 2)
        self.assertIn("change it there", run.err)
        self.assertEqual(run.calls, [])

    def test_a_listed_repository_is_found_whatever_its_case(self):
        self.write_list({"example/repo": entry(approvals=1)})
        self.state["repos"]["example/repo"] = fresh_repo()
        self.assertEqual(self.run_script("Example/Repo", "--approvals", "0").code, 2)
        run = self.run_script("Example/Repo")
        self.assertEqual(run.code, 0, run.err)
        pr = [r for r in self.ruleset("example/repo", "main")["rules"] if r["type"] == "pull_request"][0]
        self.assertEqual(pr["parameters"]["required_approving_review_count"], 1)

    def test_apply_needs_an_admin_and_check_does_not(self):
        self.write_list({"example/repo": entry()})
        self.state["repos"]["example/repo"] = fresh_repo(permissions={"admin": False})
        run = self.run_script("--all")
        self.assertEqual(run.code, 2)
        self.assertEqual(run.writes, [])
        run = self.run_script("--check", "--all")
        self.assertEqual(run.writes, [])
        self.assertIn("example/repo: Actions: not checked", run.out)
        self.assertIn("example/repo: drift: issues: has_issues: want false, have true", run.out)

    def test_a_refused_repository_stops_the_whole_run_before_any_change(self):
        self.write_list({"example/a": entry(),
                         "example/b": entry(actions_prs=True)})
        self.state["repos"]["example/a"] = fresh_repo()
        self.state["repos"]["example/b"] = fresh_repo(_codeowners=False)
        run = self.run_script("--all")
        self.assertEqual(run.code, 2)
        self.assertEqual(run.writes, [])

    # What an apply sets.

    def test_release_tags_never_move_and_only_the_app_creates_them(self):
        self.write_list({"example/repo": entry(tags=["v*", "gen/go/v*"])})
        self.state["repos"]["example/repo"] = fresh_repo()
        run = self.run_script("--all")
        self.assertEqual(run.code, 0, run.err)
        include = ["refs/tags/v*", "refs/tags/gen/go/v*"]
        tags = self.ruleset("example/repo", "release tags")
        self.assertEqual(tags["target"], "tag")
        self.assertEqual(tags["conditions"]["ref_name"]["include"], include)
        self.assertEqual(sorted(r["type"] for r in tags["rules"]), ["deletion", "non_fast_forward", "update"])
        self.assertEqual(tags["bypass_actors"], [])
        creation = self.ruleset("example/repo", "release tag creation")
        self.assertEqual(creation["conditions"]["ref_name"]["include"], include)
        self.assertEqual(creation["rules"], [{"type": "creation"}])
        # The App, by the ID in the list, is the only bypass actor.
        self.assertEqual(creation["bypass_actors"],
                         [{"actor_id": APP_ID, "actor_type": "Integration", "bypass_mode": "always"}])
        self.write_list({"example/repo": entry()}, release_app_id=7)
        self.assertEqual(self.run_script("--check", "--all").code, 1)
        self.assertEqual(self.run_script("--all").code, 0)
        self.assertEqual(self.ruleset("example/repo", "release tag creation")["bypass_actors"],
                         [{"actor_id": 7, "actor_type": "Integration", "bypass_mode": "always"}])

    def test_a_one_off_takes_the_app_from_the_list(self):
        self.state["repos"]["example/repo"] = fresh_repo()
        run = self.run_script("example/repo", "--approvals", "1")
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(self.ruleset("example/repo", "release tag creation")["bypass_actors"],
                         [{"actor_id": APP_ID, "actor_type": "Integration", "bypass_mode": "always"}])

    # The App's ID still a placeholder.

    def test_a_placeholder_app_id_refuses_the_creation_ruleset(self):
        placeholder = "PLACEHOLDER: the App's ID"
        self.write_list({"example/a": entry(), "example/b": entry()}, release_app_id=placeholder)
        for args in (["--all"], ["example/a"], ["--check", "--all"], ["--check", "example/a"]):
            with self.subTest(args=args):
                self.state["repos"]["example/a"] = fresh_repo()
                self.state["repos"]["example/b"] = fresh_repo()
                run = self.run_script(*args)
                self.assertEqual(run.code, 1, run.out + run.err)
                self.assertIn("release_app_id in", run.err)
                self.assertIn("is still a placeholder", run.err)
                self.assertIn("example/a: refused: ruleset 'release tag creation'", run.err)
                self.assertNotIn("drift: ruleset 'release tag creation'", run.out)
                # The ruleset is never read, compared or written.
                creation = [c for c in run.calls if "release tag creation" in json.dumps(c)]
                self.assertEqual(creation, [])
                for repo in ("example/a", "example/b"):
                    names = {r["name"] for r in self.state["repos"][repo]["_rulesets"]}
                    self.assertNotIn("release tag creation", names)
                if "--check" in args:
                    self.assertEqual(run.writes, [])
                    self.assertNotIn("no drift", run.out)
                else:
                    # Everything else still applies.
                    self.assertEqual({r["name"] for r in self.state["repos"]["example/a"]["_rulesets"]},
                                     {"main", "release tags"})
                    self.assertTrue(run.sent("/actions/permissions/workflow"))
                    self.assertNotIn("example/a: no change", run.out)

    def test_a_placeholder_app_id_refuses_a_one_off(self):
        listed = json.loads(LIST.read_text())
        listed["release_app_id"] = "PLACEHOLDER"
        self.list_path.write_text(json.dumps(listed))
        self.state["repos"]["example/repo"] = fresh_repo()
        run = self.run_script("example/repo")
        self.assertEqual(run.code, 1)
        self.assertIn("example/repo: refused: ruleset 'release tag creation'", run.err)
        self.assertEqual({r["name"] for r in self.state["repos"]["example/repo"]["_rulesets"]},
                         {"main", "release tags"})

    # A write GitHub refuses.

    def test_a_failed_write_doesnt_stop_the_run(self):
        self.write_list({"example/a": entry(codeql=["go"]), "example/b": entry(codeql=["go"])})
        self.state["repos"]["example/a"] = fresh_repo()
        self.state["repos"]["example/b"] = fresh_repo()
        self.state["fail"] = [
            {"method": "POST", "path": r"^repos/example/a/rulesets$",
             "message": "Validation Failed", "errors": ["Actor is not a valid bypass actor"]},
            {"method": "PUT", "path": r"^repos/example/a/vulnerability-alerts$",
             "message": "Resource not accessible by integration"},
        ]
        run = self.run_script("--all")
        self.assertEqual(run.code, 1, run.out + run.err)
        # GitHub's message for each failure, with its field errors.
        self.assertIn("example/a: error: gh api -X POST repos/example/a/rulesets: "
                      "Validation Failed; Actor is not a valid bypass actor", run.err)
        self.assertIn("example/a: error: gh api -X PUT repos/example/a/vulnerability-alerts: "
                      "Resource not accessible by integration", run.err)
        self.assertIn("example/a: ruleset 'main': not changed: GitHub refused a write", run.err)
        self.assertIn("example/a: security: not changed", run.err)
        self.assertIn("failed:", run.err)
        # Every ruleset was tried, and the settings after them were still written.
        self.assertEqual(len([c for c in run.sent("/rulesets") if c["path"] == "repos/example/a/rulesets"]), 3)
        a = self.state["repos"]["example/a"]
        self.assertEqual(a["_workflow"]["default_workflow_permissions"], "read")
        self.assertEqual(a["_codeql"]["state"], "configured")
        # The security writes after the failed one went ahead too.
        self.assertIs(a["_pvr"], True)
        self.assertEqual(a["security_and_analysis"]["secret_scanning"]["status"], "enabled")
        # The next repository was applied in full.
        b = self.state["repos"]["example/b"]
        self.assertEqual({r["name"] for r in b["_rulesets"]}, {"main", "release tags", "release tag creation"})
        self.assertIs(b["_alerts"], True)
        self.assertEqual(b["_codeql"]["state"], "configured")
        # Once GitHub accepts the writes, a run completes and writes again only the
        # settings that failed (security as a whole, since it is one setting).
        self.state["fail"] = []
        again = self.run_script("--all")
        self.assertEqual(again.code, 0, again.err)
        self.assertIn("example/b: no change", again.out)
        self.assertIn("example/a: ruleset 'main': changed", again.out)
        self.assertIn("example/a: security: changed", again.out)
        self.assertEqual({c["path"] for c in again.written},
                         {"repos/example/a/rulesets", "repos/example/a",
                          "repos/example/a/vulnerability-alerts",
                          "repos/example/a/private-vulnerability-reporting"})
        self.assertEqual(self.run_script("--all").writes, [])

    def test_a_failed_ruleset_read_doesnt_stop_the_run(self):
        self.write_list({"example/a": entry(codeql=["go"]), "example/b": entry(codeql=["go"])})
        self.state["repos"]["example/a"] = fresh_repo()
        self.state["repos"]["example/b"] = fresh_repo()
        self.state["fail"] = [{"method": "GET", "path": r"^repos/example/a/rulesets$",
                               "message": "Server Error"}]
        for args, outcome in ((["--check", "--all"], "not checked"), (["--all"], "not changed")):
            with self.subTest(args=args):
                run = self.run_script(*args)
                self.assertEqual(run.code, 1, run.out + run.err)
                self.assertIn("example/a: error: gh api repos/example/a/rulesets: Server Error", run.err)
                for name in ("main", "release tags", "release tag creation"):
                    self.assertIn(f"example/a: ruleset '{name}': {outcome}: GitHub refused a read", run.err)
                # No ruleset is written blind, and the rest goes on.
                self.assertFalse([c for c in run.writes if "/rulesets" in c["path"] and "example/a" in c["path"]])
                self.assertIn("failed:", run.err)
        a, b = self.state["repos"]["example/a"], self.state["repos"]["example/b"]
        self.assertEqual(a["_rulesets"], [])
        self.assertEqual(a["_codeql"]["state"], "configured")
        self.assertEqual(len(b["_rulesets"]), 3)
        # A refused read of one ruleset, once they exist.
        self.state["fail"] = []
        self.assertEqual(self.run_script("--all").code, 0)
        self.state["fail"] = [{"method": "GET", "path": r"^repos/example/a/rulesets/\d+$",
                               "message": "Server Error"}]
        run = self.run_script("--check", "--all")
        self.assertEqual(run.code, 1)
        self.assertIn("example/a: ruleset 'main': not checked: GitHub refused a read", run.err)
        self.assertNotIn("example/b: ruleset", run.err)
        self.assertNotIn("no drift", run.out)

    def test_a_failed_environment_write_is_reported_and_the_run_goes_on(self):
        self.write_list({"example/a": entry(publishes=True), "example/b": entry(publishes=True)})
        self.state["repos"]["example/a"] = fresh_repo()
        self.state["repos"]["example/b"] = fresh_repo()
        self.state["fail"] = [{"method": "PUT", "path": r"^repos/example/a/environments/release$",
                               "message": "Validation Failed", "errors": [{"message": "reviewers is invalid"}]}]
        run = self.run_script("--all")
        self.assertEqual(run.code, 1)
        self.assertIn("Validation Failed; reviewers is invalid", run.err)
        self.assertIn("example/a: release environment: not changed", run.err)
        # The branch policies wait for the environment; the rest goes on.
        self.assertFalse(run.sent("repos/example/a/environments/release/deployment-branch-policies"))
        self.assertEqual(self.state["repos"]["example/a"]["_codeql"]["state"], "not-configured")
        self.assertTrue(run.sent("repos/example/b/environments/release/deployment-branch-policies"))
        self.assertIn("example/b: release environment: changed", run.out)

    def test_issues_follow_the_list(self):
        self.write_list({"example/on": entry(issues=True), "example/off": entry(issues=False)})
        self.state["repos"]["example/on"] = fresh_repo(has_issues=False)
        self.state["repos"]["example/off"] = fresh_repo(has_issues=True)
        self.assertEqual(self.run_script("--all").code, 0)
        self.assertIs(self.state["repos"]["example/on"]["has_issues"], True)
        self.assertIs(self.state["repos"]["example/off"]["has_issues"], False)

    def test_the_actions_allowlist(self):
        self.write_list({"example/repo": entry()})
        self.state["repos"]["example/repo"] = fresh_repo()
        self.assertEqual(self.run_script("--all").code, 0)
        repo = self.state["repos"]["example/repo"]
        self.assertEqual(repo["_actions"], {"enabled": True, "allowed_actions": "selected",
                                            "sha_pinning_required": True})
        self.assertIs(repo["_selected"]["verified_allowed"], False)
        self.assertIn("ossf/scorecard-action@*", repo["_selected"]["patterns_allowed"])
        self.assertNotIn("golangci/*", repo["_selected"]["patterns_allowed"])
        self.assertEqual(repo["_workflow"]["default_workflow_permissions"], "read")
        self.assertEqual(repo["_fork"]["approval_policy"], "all_external_contributors")

    def test_a_publishing_repository_gets_a_release_environment(self):
        self.write_list({"example/repo": entry(publishes=True, tags=["v*"])})
        self.state["repos"]["example/repo"] = fresh_repo(
            _env={"prevent_self_review": False, "reviewers": [], "can_admins_bypass": True,
                  "deployment_branch_policy": None},
            _policies=[{"id": 7, "name": "dev", "type": "branch"}])
        run = self.run_script("--all")
        self.assertEqual(run.code, 0, run.err)
        env = self.state["repos"]["example/repo"]["_env"]
        self.assertEqual(env["reviewers"], [{"type": "User", "id": MAINTAINER_ID}])
        # Off unless the list turns it on: with one reviewer it would block every release.
        self.assertIs(env["prevent_self_review"], False)
        self.assertIs(env["can_admins_bypass"], False)
        self.assertEqual(env["deployment_branch_policy"],
                         {"protected_branches": False, "custom_branch_policies": True})
        self.assertNotIn("secrets", json.dumps(run.writes))
        policies = sorted((p["type"], p["name"]) for p in self.state["repos"]["example/repo"]["_policies"])
        self.assertEqual(policies, [("branch", "main"), ("tag", "v*")])
        self.assertIn("release environment: unchanged", self.run_script("--all").out)

    def test_prevent_self_review_follows_the_list(self):
        for listed, want in (({}, False), ({"prevent_self_review": False}, False),
                             ({"prevent_self_review": True}, True)):
            with self.subTest(listed=listed):
                self.write_list({"example/repo": entry(publishes=True, **listed)})
                self.state["repos"]["example/repo"] = fresh_repo()
                self.assertEqual(self.run_script("--all").code, 0)
                self.assertIs(self.state["repos"]["example/repo"]["_env"]["prevent_self_review"], want)
                again = self.run_script("--all")
                self.assertEqual(again.writes, [])
                self.assertIn("release environment: unchanged", again.out)
                self.state["repos"]["example/repo"]["_env"]["prevent_self_review"] = not want
                check = self.run_script("--check", "--all")
                self.assertEqual(check.code, 1)
                self.assertIn(f"drift: release environment: prevent_self_review: want {json.dumps(want)}", check.out)

    def test_lists_are_read_past_their_first_page(self):
        self.write_list({"example/repo": entry(publishes=True, tags=["v*", "gen/go/v*", "gen/ts/v*"])})
        self.state["repos"]["example/repo"] = fresh_repo()
        self.assertEqual(self.run_script("--all").code, 0)
        repo = self.state["repos"]["example/repo"]
        # Push every ruleset this script made past the first page.
        repo["_rulesets"] = [{"id": n, "name": f"other {n}", "target": "branch", "enforcement": "active",
                              "conditions": {}, "bypass_actors": [], "rules": []}
                             for n in range(1, 2 * PAGE + 1)] + repo["_rulesets"]
        self.assertGreater(len(repo["_policies"]), PAGE)
        run = self.run_script("--all")
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(run.writes, [])
        self.assertTrue(all(c.get("paginate") for c in run.calls
                            if c["path"].endswith(("/rulesets", "/deployment-branch-policies"))))
        self.assertEqual(self.run_script("--check", "--all").code, 0)

    def test_codeql_default_setup(self):
        self.write_list({"example/go": entry(codeql=["go", "actions"]), "example/none": entry(codeql=[])})
        self.state["repos"]["example/go"] = fresh_repo()
        self.state["repos"]["example/none"] = fresh_repo(
            _codeql={"state": "configured", "languages": ["python"], "query_suite": "default"})
        self.assertEqual(self.run_script("--all").code, 0)
        self.assertEqual(self.state["repos"]["example/go"]["_codeql"]["state"], "configured")
        self.assertEqual(sorted(self.state["repos"]["example/go"]["_codeql"]["languages"]), ["actions", "go"])
        self.assertEqual(self.state["repos"]["example/none"]["_codeql"]["state"], "not-configured")

    # Running it again, and --check.

    def everything_listed(self):
        self.write_list({
            "example/tool": entry(checks=["test", "checks / checks"], actions_prs=True, codeql=["python"]),
            "example/home": entry(checks=[], issues=True),
            "example/image": entry(checks=["go / test", "image / build"], publishes=True,
                                   tags=["v*", "gen/go/v*"], codeql=["go"]),
            "example/data": entry(checks=[], actions=False, approvals=0, code_owners=False),
        })
        for name in ("example/tool", "example/home", "example/image", "example/data"):
            self.state["repos"][name] = fresh_repo()

    def test_a_second_apply_changes_nothing(self):
        self.everything_listed()
        first = self.run_script("--all")
        self.assertEqual(first.code, 0, first.err)
        self.assertTrue(first.writes)
        second = self.run_script("--all")
        self.assertEqual(second.code, 0, second.err)
        self.assertEqual(second.writes, [])
        for name in ("example/tool", "example/home", "example/image", "example/data"):
            self.assertIn(f"{name}: no change", second.out)
        self.assertNotIn(": changed", second.out)

    def test_check_reports_drift_before_an_apply_and_none_after(self):
        self.everything_listed()
        before = self.run_script("--check", "--all")
        self.assertEqual(before.code, 1)
        self.assertEqual(before.writes, [])
        self.assertIn("example/tool: drift: ruleset 'main'", before.out)
        self.assertIn("example/image: drift: release environment", before.out)
        self.run_script("--all")
        after = self.run_script("--check", "--all")
        self.assertEqual(after.code, 0, after.out)
        self.assertEqual(after.writes, [])
        self.assertNotIn("drift:", after.out)

    def test_check_reports_a_setting_changed_by_hand(self):
        self.everything_listed()
        self.run_script("--all")
        tool = self.state["repos"]["example/tool"]
        tool["has_issues"] = True
        tool["_workflow"]["can_approve_pull_request_reviews"] = False
        tool["_selected"]["patterns_allowed"].remove("ossf/scorecard-action@*")
        main = [r for r in tool["_rulesets"] if r["name"] == "main"][0]
        [r for r in main["rules"] if r["type"] == "required_status_checks"][0]["parameters"][
            "required_status_checks"].pop()
        creation = [r for r in tool["_rulesets"] if r["name"] == "release tag creation"][0]
        creation["bypass_actors"].append({"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"})
        run = self.run_script("--check", "example/tool")
        self.assertEqual(run.code, 1)
        self.assertEqual(run.writes, [])
        for line in (
            "example/tool: drift: issues: has_issues: want false, have true",
            "example/tool: drift: Actions: can_approve_pull_request_reviews: want true, have false",
            "example/tool: drift: Actions: patterns_allowed:",
            "example/tool: drift: ruleset 'main': rules.required_status_checks.required_status_checks:",
            "example/tool: drift: ruleset 'release tag creation': bypass:",
        ):
            self.assertIn(line, run.out)

    def test_check_reports_branches_not_required_up_to_date(self):
        self.everything_listed()
        self.run_script("--all")
        self.status_checks("example/tool")["strict_required_status_checks_policy"] = False
        run = self.run_script("--check", "--all")
        self.assertEqual(run.code, 1)
        self.assertEqual(run.writes, [])
        self.assertIn("example/tool: drift: ruleset 'main': "
                      "rules.required_status_checks.strict_required_status_checks_policy: want true, have false",
                      run.out)
        self.assertNotIn("example/image: drift:", run.out)
        fixed = self.run_script("--all")
        self.assertEqual(fixed.code, 0, fixed.err)
        self.assertIn("example/tool: ruleset 'main': changed", fixed.out)
        self.assertEqual(len(fixed.writes), 1)
        self.assertIs(self.status_checks("example/tool")["strict_required_status_checks_policy"], True)
        self.assertEqual(self.run_script("--check", "--all").code, 0)

    # The merge queue (those-who-run/paddock#277): a rule in `main`'s ruleset, only
    # where the list asks for it.

    QUEUE = {"merge_method": "SQUASH", "grouping_strategy": "ALLGREEN",
             "check_response_timeout_minutes": 60, "max_entries_to_build": 5,
             "max_entries_to_merge": 5, "min_entries_to_merge": 1,
             "min_entries_to_merge_wait_minutes": 5}

    def queue_rules(self, repo):
        return [r for r in self.ruleset(repo, "main")["rules"] if r["type"] == "merge_queue"]

    def queued_and_not(self):
        self.write_list({
            "example/queued": entry(checks=["test", "checks / checks"], merge_queue=True),
            "example/plain": entry(checks=["test", "checks / checks"], merge_queue=False),
            "example/unset": entry(checks=["test"]),
        })
        for name in ("example/queued", "example/plain", "example/unset"):
            self.state["repos"][name] = fresh_repo()

    def test_the_merge_queue_only_where_the_list_asks_for_it(self):
        self.queued_and_not()
        run = self.run_script("--all")
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual([r["parameters"] for r in self.queue_rules("example/queued")], [self.QUEUE])
        for name in ("example/plain", "example/unset"):
            with self.subTest(repo=name):
                self.assertEqual(self.queue_rules(name), [])
        # Nothing sent for a repository without it, not even an empty rule.
        sent = {c["path"].split("/")[2]: json.dumps(c["input"]) for c in run.sent("/rulesets")
                if c["input"]["name"] == "main"}
        self.assertIn('"merge_queue"', sent["queued"])
        self.assertNotIn("merge_queue", sent["plain"])
        self.assertNotIn("merge_queue", sent["unset"])
        # The strict policy stays, with the queue and without it.
        for name in ("example/queued", "example/plain", "example/unset"):
            self.assertIs(self.status_checks(name)["strict_required_status_checks_policy"], True)

    def test_no_listed_repository_has_the_merge_queue_yet(self):
        # Each repository turns it on by pull request once its CI caller runs on
        # merge_group and a queue run has been seen green.
        repositories = json.loads(LIST.read_text())["repositories"]
        for name, listed in repositories.items():
            self.assertIsNot(listed.get("merge_queue", False), True, name)
            self.state["repos"][name] = fresh_repo()
        run = self.run_script("--all", list_file=self.list_with_app)
        self.assertEqual(run.code, 0, run.err)
        for name in repositories:
            self.assertEqual(self.queue_rules(name), [], name)

    def test_a_one_off_has_no_merge_queue(self):
        self.state["repos"]["example/repo"] = fresh_repo()
        run = self.run_script("example/repo", "--checks", "test")
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(self.queue_rules("example/repo"), [])

    def test_a_second_apply_with_the_merge_queue_changes_nothing(self):
        self.queued_and_not()
        self.assertEqual(self.run_script("--all").code, 0)
        second = self.run_script("--all")
        self.assertEqual(second.code, 0, second.err)
        self.assertEqual(second.writes, [])
        self.assertEqual(self.run_script("--check", "--all").code, 0)

    def test_check_reports_merge_queue_drift(self):
        self.queued_and_not()
        self.run_script("--all")
        queued = self.queue_rules("example/queued")[0]["parameters"]
        queued["grouping_strategy"] = "HEADGREEN"
        queued["check_response_timeout_minutes"] = 5
        # A queue turned on by hand where the list has none.
        plain = self.ruleset("example/plain", "main")
        plain["rules"].append({"type": "merge_queue", "parameters": dict(self.QUEUE)})
        run = self.run_script("--check", "--all")
        self.assertEqual(run.code, 1)
        self.assertEqual(run.writes, [])
        self.assertIn("example/queued: drift: ruleset 'main': "
                      'rules.merge_queue.grouping_strategy: want "ALLGREEN", have "HEADGREEN"', run.out)
        self.assertIn("example/queued: drift: ruleset 'main': "
                      "rules.merge_queue.check_response_timeout_minutes: want 60, have 5", run.out)
        self.assertIn("example/plain: drift: ruleset 'main': rules.merge_queue: want null, have {}", run.out)
        self.assertNotIn("example/unset: drift", run.out)
        fixed = self.run_script("--all")
        self.assertEqual(fixed.code, 0, fixed.err)
        self.assertEqual(len(fixed.writes), 2)
        self.assertEqual([r["parameters"] for r in self.queue_rules("example/queued")], [self.QUEUE])
        self.assertEqual(self.queue_rules("example/plain"), [])
        self.assertEqual(self.run_script("--check", "--all").code, 0)

    def test_check_reports_a_merge_queue_that_is_missing(self):
        self.queued_and_not()
        self.run_script("--all")
        main = self.ruleset("example/queued", "main")
        main["rules"] = [r for r in main["rules"] if r["type"] != "merge_queue"]
        run = self.run_script("--check", "--all")
        self.assertEqual(run.code, 1)
        self.assertIn("example/queued: drift: ruleset 'main': rules.merge_queue.merge_method: "
                      'want "SQUASH", have null', run.out)
        self.assertEqual(self.run_script("--all").code, 0)
        self.assertEqual(self.run_script("--check", "--all").code, 0)

    def test_check_reports_a_ruleset_that_is_not_enforced(self):
        # A disabled `main` once went unnoticed; so would one the list doesn't manage.
        self.everything_listed()
        self.run_script("--all")
        tool = self.state["repos"]["example/tool"]
        [r for r in tool["_rulesets"] if r["name"] == "main"][0]["enforcement"] = "disabled"
        tool["_rulesets"].append({"id": 999, "name": "by hand", "target": "branch",
                                  "enforcement": "evaluate", "conditions": {}, "rules": []})
        run = self.run_script("--check", "--all")
        self.assertEqual(run.code, 1)
        self.assertEqual(run.writes, [])
        self.assertIn("example/tool: drift: ruleset 'main' is not enforced: "
                      "enforcement is disabled, want active", run.out)
        self.assertIn("example/tool: drift: ruleset 'by hand' is not enforced: "
                      "enforcement is evaluate, want active", run.out)
        self.assertNotIn("example/home: drift", run.out)
        # An apply makes `main` active again and leaves the one it doesn't manage, with
        # a warning; the check after it still reports that one.
        applied = self.run_script("--all")
        self.assertEqual(applied.code, 0, applied.err)
        self.assertNotIn("ruleset 'main' is not enforced", applied.out)
        self.assertIn("example/tool: warning: ruleset 'by hand' is not enforced: enforcement is evaluate",
                      applied.out)
        self.assertEqual(self.ruleset("example/tool", "main")["enforcement"], "active")
        after = self.run_script("--check", "--all")
        self.assertEqual(after.code, 1)
        self.assertNotIn("ruleset 'main' is not enforced", after.out)
        self.assertIn("example/tool: drift: ruleset 'by hand' is not enforced", after.out)

    def test_the_organisations_rulesets_are_not_this_repositorys_drift(self):
        # An organisation ruleset in evaluate is no drift an apply here could fix.
        self.everything_listed()
        self.state["org_rulesets"] = [{"id": 5000, "name": "org trial", "target": "branch",
                                       "enforcement": "evaluate", "source_type": "Organization"}]
        self.run_script("--all")
        run = self.run_script("--check", "--all")
        self.assertEqual(run.code, 0, run.out)
        self.assertNotIn("org trial", run.out + run.err)
        lists = [c for c in run.calls if c["method"] == "GET" and c["path"].endswith("/rulesets")]
        self.assertTrue(lists)
        for c in lists:
            self.assertIn("includes_parents=false", c["query"].split("&"))

    def test_check_reports_a_missing_codeowners_file(self):
        self.everything_listed()
        self.run_script("--all")
        self.state["repos"]["example/home"]["_codeowners"] = False
        run = self.run_script("--check", "--all")
        self.assertEqual(run.code, 1)
        self.assertIn("example/home: drift: code owners: no .github/CODEOWNERS", run.out)
        applied = self.run_script("--all")
        self.assertIn("example/home: warning: no .github/CODEOWNERS", applied.out)

    def test_check_without_admin_rights_leaves_bypass_lists_unchecked(self):
        self.write_list({"example/repo": entry()})
        self.state["repos"]["example/repo"] = fresh_repo()
        self.assertEqual(self.run_script("--all").code, 0)
        self.state["repos"]["example/repo"]["permissions"] = {"admin": False}
        run = self.run_script("--check", "--all")
        self.assertEqual(run.code, 0, run.out)
        self.assertEqual(run.writes, [])
        self.assertNotIn("drift:", run.out)
        for name in ("main", "release tags", "release tag creation"):
            self.assertIn(f"example/repo: ruleset '{name}' bypass: not checked", run.out)
        self.assertIn("--check is only complete with an admin login", run.out)
        self.assertIn("no drift in what could be read", run.out)
        # The rest of each ruleset is still compared.
        main = self.ruleset("example/repo", "main")
        main["rules"] = [r for r in main["rules"] if r["type"] != "deletion"]
        self.assertIn("example/repo: drift: ruleset 'main': rules.deletion:", self.run_script("--check", "--all").out)

    def test_check_with_admin_rights_has_no_warning(self):
        self.write_list({"example/repo": entry()})
        self.state["repos"]["example/repo"] = fresh_repo()
        self.run_script("--all")
        run = self.run_script("--check", "--all")
        self.assertEqual(run.code, 0, run.out)
        self.assertNotIn("not checked", run.out)
        self.assertNotIn("warning", run.out)
        self.assertIn("no drift\n", run.out)

    def test_check_wants_a_catch_all_codeowners_rule_naming_the_maintainer(self):
        cases = {
            "* @ciaranRoche\n": None,
            "# owners\n/docs/ @someone\n*   @Someone  @CIARANROCHE  # everything else\n": None,
            False: "no .github/CODEOWNERS on the default branch",
            "/scripts/ @ciaranRoche\n# * @ciaranRoche\n": "no catch-all * rule in .github/CODEOWNERS",
            "* @someone-else\n": "the catch-all * rule in .github/CODEOWNERS does not name @ciaranRoche",
            # The last matching rule wins, so a later `*` line replaces the maintainer.
            "* @ciaranRoche\n* @someone-else\n": "the catch-all * rule in .github/CODEOWNERS does not name @ciaranRoche",
        }
        self.write_list({"example/repo": entry()})
        self.state["repos"]["example/repo"] = fresh_repo()
        self.run_script("--all")
        for text, problem in cases.items():
            with self.subTest(text=text):
                self.state["repos"]["example/repo"]["_codeowners"] = text
                run = self.run_script("--check", "--all")
                self.assertEqual(run.writes, [])
                if problem is None:
                    self.assertEqual(run.code, 0, run.out)
                    self.assertNotIn("code owners", run.out)
                else:
                    self.assertEqual(run.code, 1, run.out)
                    self.assertIn(f"example/repo: drift: code owners: {problem}", run.out)
                    self.assertIn(f"example/repo: warning: {problem}", self.run_script("--all").out)

    def test_the_header_says_who_runs_it(self):
        header = SCRIPT.read_text().split("set -euo pipefail")[0]
        self.assertIn("Run by the maintainer", header)
        self.assertIn("Never by\n# an agent, and never from CI with write rights", header)


if __name__ == "__main__":
    unittest.main()
