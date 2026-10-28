"""scripts/fan_out.py, which opens a PR in every repository moving it onto a release of
this repository, run against a stub `gh` and local git remotes, so nothing reaches
GitHub.

Each repository is a bare git repository under a temporary directory, which the
script clones and pushes to through FAN_OUT_GIT_BASE. The stub `gh` keeps the open
PRs in a JSON file, answers `gh pr list`, `gh pr create` and the `gh api` lookup of a
bot's ID, and logs every call. The release's files come from a local git repository
standing in for this one, tagged at the release.

Standard library only; CI's `test` job runs it:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workflow_yaml import WORKFLOWS, jobs

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "fan_out.py"
ORG = "those-who-run"

_spec = importlib.util.spec_from_file_location("fan_out", SCRIPT)
assert _spec and _spec.loader
fan_out = importlib.util.module_from_spec(_spec)
sys.modules["fan_out"] = fan_out  # dataclasses look their module up
_spec.loader.exec_module(fan_out)

OLD_SHA = "6328457b1a763ee250f4326023ee0a5db0ab78cb"
OLD = "v0.2.8"
NEW = "v0.3.0"
BRANCH = f"chore/github-{NEW}"
TITLE = f"ci: those-who-run/.github {NEW}"
GUARD = b"# the guard at the release\nprint('guard')\n"
SETTINGS = b'{"hooks": {"release": true}}\n'

STUB = r'''
import json, os, sys

state_path, log_path = os.environ["GH_STATE"], os.environ["GH_CALLS"]
state = json.load(open(state_path))
args = sys.argv[1:]
call = {"argv": args}

def opt(name):
    return args[args.index(name) + 1] if name in args else None

def done(out="", status=0):
    with open(log_path, "a") as log:
        log.write(json.dumps(call) + "\n")
    json.dump(state, open(state_path, "w"))
    if out:
        print(out)
    sys.exit(status)

if args[:2] == ["pr", "list"]:
    repo, head = opt("--repo"), opt("--head")
    if repo in state.get("refuse", []):
        print("gh: HTTP 403: Resource not accessible by integration", file=sys.stderr)
        done(status=1)
    assert opt("--state") == "open", args
    if repo in state.get("garbage", []):
        done("not json")
    prs = [{"number": p["number"], "url": p["url"], "headRefName": p["head"],
            "author": {"login": p.get("author", "someone")}} for p in state["prs"]
           if p["repo"] == repo and p["state"] == "open" and head in (None, p["head"])]
    fields = opt("--json").split(",")
    done(json.dumps([{k: v for k, v in pr.items() if k in fields} for pr in prs]))
if args[:2] == ["pr", "close"]:
    repo, number = opt("--repo"), int(args[2])
    call["comment"] = opt("--comment")
    [pr] = [p for p in state["prs"] if p["repo"] == repo and p["number"] == number]
    pr["state"], pr["comment"] = "closed", opt("--comment")
    done()
if args[:2] == ["pr", "create"]:
    repo = opt("--repo")
    call["body"] = sys.stdin.read()
    number = len(state["prs"]) + 1
    url = f"https://github.com/{repo}/pull/{number}"
    state["prs"].append({"repo": repo, "number": number, "url": url, "head": opt("--head"),
                         "base": opt("--base"), "title": opt("--title"), "state": "open",
                         "body": call["body"], "author": "app/twr-release"})
    done(url)
if args[:1] == ["api"] and args[1].startswith("users/"):
    assert args[2:] == ["--jq", ".id"], args
    done(str(state["users"][args[1][len("users/"):]]))
call["unhandled"] = True
done(status=1)
'''


# The tests' git: no global or system config (a signing setting there would break
# the commits), and a fixed identity. setUp fills it in.
GIT_ENV: dict[str, str] = {}

# The environment without git's own variables: a commit hook runs these tests with
# GIT_INDEX_FILE and the like set to the committing checkout's, and git in the tests'
# repositories would write that index instead of their own.
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


class Link:
    """A symbolic link in a repository, to target."""

    def __init__(self, target: str | Path):
        self.target = str(target)


BOT = "4242+twr-release[bot]@users.noreply.github.com"
BOT_ENV = {"GIT_AUTHOR_NAME": "twr-release[bot]", "GIT_AUTHOR_EMAIL": BOT,
           "GIT_COMMITTER_NAME": "twr-release[bot]", "GIT_COMMITTER_EMAIL": BOT}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**ENV, **GIT_ENV}).stdout


def pinned(name: str, sha: str = OLD_SHA, tag: str = OLD) -> str:
    return f"    uses: {ORG}/.github/.github/workflows/{name}@{sha} # {tag}\n"


CI = (
    "name: CI\n"
    "jobs:\n"
    "  checks:\n"
    + pinned("checks.yml")
    + "  go:\n"
    + pinned("go.yml")
    + "  other:\n"
    "    uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
)
RENOVATE = (
    '{\n  "$schema": "https://docs.renovatebot.com/renovate-schema.json",\n'
    f'  "extends": ["github>{ORG}/.github#{OLD}"]\n}}\n'
)


class FanOut(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        self.env = {
            "PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.tmp / "home"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "tester", "GIT_AUTHOR_EMAIL": "tester@example.com",
            "GIT_COMMITTER_NAME": "tester", "GIT_COMMITTER_EMAIL": "tester@example.com",
            "GH_STATE": str(self.tmp / "gh.json"),
            "GH_CALLS": str(self.tmp / "calls.jsonl"),
            "FAN_OUT_GIT_BASE": (self.tmp / "remotes").as_uri(),
            "GITHUB_STEP_SUMMARY": str(self.tmp / "summary.md"),
        }
        (self.tmp / "home").mkdir()
        GIT_ENV.clear()
        GIT_ENV.update(self.env)
        (self.tmp / "bin").mkdir()
        gh = self.tmp / "bin" / "gh"
        gh.write_text(f"#!{sys.executable}\n{STUB}")
        gh.chmod(0o755)
        self.state = {"prs": [], "users": {"twr-release%5Bbot%5D": 4242}}
        self.save()
        self.repos: list[str] = []
        # This repository, at the release.
        self.source = self.tmp / "source"
        (self.source / "agent").mkdir(parents=True)
        (self.source / "agent" / "guard.py").write_bytes(GUARD)
        (self.source / "agent" / "settings.json").write_bytes(SETTINGS)
        git(self.tmp, "init", "--quiet", "-b", "main", str(self.source))
        self.commit(self.source)
        git(self.source, "tag", NEW)
        self.sha = git(self.source, "rev-parse", "HEAD").strip()

    def commit(self, repo: Path):
        git(repo, "add", "-A")
        git(repo, "commit", "--quiet", "-m", "init")

    def save(self):
        Path(self.env["GH_STATE"]).write_text(json.dumps(self.state))

    def remote(self, name: str, files: dict[str, str | bytes]) -> str:
        """A repository on the pretend GitHub, with these files on main."""
        repo = f"{ORG}/{name}"
        work = self.tmp / "work" / name
        work.mkdir(parents=True)
        git(work, "init", "--quiet", "-b", "main")
        for path, content in files.items():
            (work / path).parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, Link):
                (work / path).symlink_to(content.target)
            elif isinstance(content, bytes):
                (work / path).write_bytes(content)
            else:
                (work / path).write_text(content)
        self.commit(work)
        bare = self.tmp / "remotes" / ORG / f"{name}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        git(self.tmp, "clone", "--quiet", "--bare", str(work), str(bare))
        self.repos.append(repo)
        return repo

    def push_branch(self, name: str, commits: list[dict[str, str]], branch: str = BRANCH):
        """Push branch to the remote, with one commit on main per entry, each by the
        identity it names (GIT_AUTHOR_* and GIT_COMMITTER_*)."""
        work = self.tmp / "branch-work" / name
        git(self.tmp, "clone", "--quiet", str(self.tmp / "remotes" / ORG / f"{name}.git"), str(work))
        git(work, "checkout", "--quiet", "-b", branch)
        for i, who in enumerate(commits):
            (work / f"note-{i}.txt").write_text(f"{i}\n")
            git(work, "add", "-A")
            subprocess.run(["git", "commit", "--quiet", "-m", f"commit {i}"], cwd=work, check=True,
                           env={**ENV, **GIT_ENV, **who})
        git(work, "push", "--quiet", "origin", branch)
        return git(work, "rev-parse", "HEAD").strip()

    def run_fan_out(self, *args: str) -> subprocess.CompletedProcess:
        listing = self.tmp / "repos.json"
        listing.write_text(json.dumps({"repositories": {r: {} for r in [f"{ORG}/.github", *self.repos]}}))
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--tag", NEW, "--source", str(self.source), "--list", str(listing), *args],
            env={**ENV, **self.env}, capture_output=True, text=True,
        )

    def calls(self) -> list[dict]:
        path = Path(self.env["GH_CALLS"])
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def prs(self) -> list[dict]:
        return json.loads(Path(self.env["GH_STATE"]).read_text())["prs"]

    def on_branch(self, name: str, path: str, branch: str = BRANCH) -> str:
        bare = self.tmp / "remotes" / ORG / f"{name}.git"
        return git(bare, "show", f"{branch}:{path}")

    def branches(self, name: str) -> list[str]:
        bare = self.tmp / "remotes" / ORG / f"{name}.git"
        return git(bare, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()

    # The rewrites.

    def test_pins_move_in_the_form_they_had(self):
        text = (
            pinned("checks.yml")
            + f"    uses: {ORG}/.github/.github/workflows/go.yml@{OLD}\n"
            + f"    uses: {ORG}/.github/.github/workflows/image.yml@{OLD_SHA}\n"
            + f"    uses: {ORG}/.github/.github/workflows/image-publish.yml@{OLD_SHA}  #  {OLD}\n"
            + f"    uses: {ORG}/paddock/.github/workflows/x.yml@{OLD_SHA} # {OLD}\n"
        )
        self.assertEqual(
            fan_out.rewrite_pins(text, self.sha, NEW),
            pinned("checks.yml", self.sha, NEW)
            + f"    uses: {ORG}/.github/.github/workflows/go.yml@{NEW}\n"
            + f"    uses: {ORG}/.github/.github/workflows/image.yml@{self.sha}\n"
            + f"    uses: {ORG}/.github/.github/workflows/image-publish.yml@{self.sha}  #  {NEW}\n"
            + f"    uses: {ORG}/paddock/.github/workflows/x.yml@{OLD_SHA} # {OLD}\n",
        )

    def test_a_pin_on_a_later_tag_is_not_a_near_miss(self):
        # v0.2.80 is not v0.2.8 with something after it.
        self.assertEqual(fan_out.referenced_versions(pinned("x.yml", tag="v0.2.80")), [(0, 2, 80)])

    def test_both_pin_forms_end_to_end(self):
        bare_tag = f"    uses: {ORG}/.github/.github/workflows/checks.yml@{OLD}\n"
        self.remote("ecu", {".github/workflows/ci.yml": CI, ".github/workflows/bare.yml": "jobs:\n  x:\n" + bare_tag})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        ci = self.on_branch("ecu", ".github/workflows/ci.yml")
        self.assertIn(pinned("checks.yml", self.sha, NEW), ci)
        self.assertIn(pinned("go.yml", self.sha, NEW), ci)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1", ci)
        self.assertNotIn(OLD_SHA, ci)
        self.assertIn(f"checks.yml@{NEW}\n", self.on_branch("ecu", ".github/workflows/bare.yml"))

    def test_line_endings_are_kept(self):
        self.remote("ecu", {"renovate.json": RENOVATE.replace("\n", "\r\n").encode()})
        self.assertEqual(self.run_fan_out().returncode, 0)
        bare = self.tmp / "remotes" / ORG / "ecu.git"
        raw = subprocess.run(["git", "show", f"{BRANCH}:renovate.json"], cwd=bare,
                             check=True, capture_output=True, env={**ENV, **GIT_ENV}).stdout
        self.assertEqual(raw, RENOVATE.replace(OLD, NEW).replace("\n", "\r\n").encode())

    def test_renovate_preset(self):
        self.remote("paddock", {"renovate.json": RENOVATE})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.on_branch("paddock", "renovate.json"), RENOVATE.replace(OLD, NEW))

    def test_guard_copy(self):
        self.remote("salsa", {".claude/hooks/guard.py": b"# an old guard\n", ".claude/settings.json": b"{}\n",
                              ".claude/guard.json": "{}\n"})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        bare = self.tmp / "remotes" / ORG / "salsa.git"
        raw = subprocess.run(["git", "show", f"{BRANCH}:.claude/hooks/guard.py"], cwd=bare,
                             check=True, capture_output=True, env={**ENV, **GIT_ENV}).stdout
        self.assertEqual(raw, GUARD)
        self.assertEqual(self.on_branch("salsa", ".claude/settings.json").encode(), SETTINGS)
        # The repository's own guard config isn't a copy.
        self.assertEqual(self.on_branch("salsa", ".claude/guard.json"), "{}\n")

    def test_a_guard_the_repository_doesnt_have_isnt_added(self):
        self.remote("radio", {"renovate.json": RENOVATE})
        self.assertEqual(self.run_fan_out().returncode, 0)
        bare = self.tmp / "remotes" / ORG / "radio.git"
        files = git(bare, "ls-tree", "-r", "--name-only", BRANCH).split()
        self.assertEqual(files, ["renovate.json"])

    def test_chassis_template_files(self):
        self.remote("chassis", {
            "copier.yml": "_subdirectory: template\n_templates_suffix: .jinja\n",
            ".github/workflows/ci.yml": CI,
            ".github/workflows/release-pr.yml.jinja": "[# a template #]\n" + pinned("release-please.yml"),
            "renovate.json": RENOVATE,
            "renovate.json.jinja": RENOVATE,
            ".claude/hooks/guard.py": b"# old\n",
            "template/.github/workflows/ci.yml.jinja": CI,
            "template/renovate.json.jinja": RENOVATE,
            "template/.claude/hooks/guard.py": b"# old\n",
            "template/.claude/settings.json": b"{}\n",
        })
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        for path in (".github/workflows/ci.yml", "template/.github/workflows/ci.yml.jinja"):
            with self.subTest(path):
                self.assertEqual(self.on_branch("chassis", path), CI.replace(OLD_SHA, self.sha).replace(OLD, NEW))
        self.assertIn(pinned("release-please.yml", self.sha, NEW),
                      self.on_branch("chassis", ".github/workflows/release-pr.yml.jinja"))
        for path in ("renovate.json", "renovate.json.jinja", "template/renovate.json.jinja"):
            with self.subTest(path):
                self.assertEqual(self.on_branch("chassis", path), RENOVATE.replace(OLD, NEW))
        for path in (".claude/hooks/guard.py", "template/.claude/hooks/guard.py"):
            with self.subTest(path):
                self.assertEqual(self.on_branch("chassis", path).encode(), GUARD)
        self.assertEqual(self.on_branch("chassis", "template/.claude/settings.json").encode(), SETTINGS)
        self.assertEqual(self.on_branch("chassis", "copier.yml"), "_subdirectory: template\n_templates_suffix: .jinja\n")

    # The PR.

    def test_the_pr(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI, "renovate.json": RENOVATE,
                            ".claude/hooks/guard.py": b"# old\n"})
        done = self.run_fan_out("--app-slug", "twr-release")
        self.assertEqual(done.returncode, 0, done.stderr)
        [pr] = self.prs()
        self.assertEqual((pr["repo"], pr["head"], pr["base"], pr["title"]), (f"{ORG}/ecu", BRANCH, "main", TITLE))
        self.assertIn(f"https://github.com/{ORG}/.github/releases/tag/{NEW}", pr["body"])
        for path in (".github/workflows/ci.yml", "renovate.json", ".claude/hooks/guard.py"):
            self.assertIn(f"`{path}`", pr["body"])
        bare = self.tmp / "remotes" / ORG / "ecu.git"
        log = git(bare, "log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%s", BRANCH).splitlines()
        bot = "twr-release[bot] <4242+twr-release[bot]@users.noreply.github.com>"
        self.assertEqual(log, [bot, bot, TITLE])
        # One commit on top of main.
        self.assertEqual(git(bare, "rev-list", "--count", f"main..{BRANCH}").strip(), "1")
        self.assertIn(f"{ORG}/ecu: opened https://github.com/{ORG}/ecu/pull/1", done.stdout)
        summary = Path(self.env["GITHUB_STEP_SUMMARY"]).read_text()
        self.assertIn(f"| {ORG}/ecu | opened | https://github.com/{ORG}/ecu/pull/1 |", summary)

    def test_this_repository_is_skipped(self):
        self.remote("ecu", {"renovate.json": RENOVATE})
        self.run_fan_out()
        repos = {c["argv"][c["argv"].index("--repo") + 1] for c in self.calls() if "--repo" in c["argv"]}
        self.assertEqual(repos, {f"{ORG}/ecu"})

    def test_nothing_merges_or_approves(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        self.run_fan_out()
        for call in self.calls():
            self.assertNotIn(call["argv"][:2], (["pr", "merge"], ["pr", "review"]))
            self.assertNotIn("--auto", call["argv"])
            self.assertFalse(call.get("unhandled"), call)
        text = SCRIPT.read_text()
        for word in ("merge", "--auto", "review", "--approve"):
            self.assertNotIn(f'"{word}"', text)

    # Idempotence.

    def test_a_repository_already_current_gets_no_pr(self):
        self.remote("ecu", {
            ".github/workflows/ci.yml": CI.replace(OLD_SHA, self.sha).replace(OLD, NEW),
            "renovate.json": RENOVATE.replace(OLD, NEW),
            ".claude/hooks/guard.py": GUARD,
        })
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.prs(), [])
        self.assertEqual(self.branches("ecu"), ["main"])
        self.assertIn(f"{ORG}/ecu: current", done.stdout)

    def test_a_repository_on_a_later_release_is_left_alone(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI.replace(OLD, "v0.4.0"), "renovate.json": RENOVATE})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.prs(), [])
        self.assertIn("already on v0.4.0, later than v0.3.0", done.stdout)

    def test_a_second_run_opens_no_second_pr(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        self.assertEqual(self.run_fan_out().returncode, 0)
        head = git(self.tmp / "remotes" / ORG / "ecu.git", "rev-parse", BRANCH)
        again = self.run_fan_out()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(self.prs()), 1)
        self.assertEqual(git(self.tmp / "remotes" / ORG / "ecu.git", "rev-parse", BRANCH), head)
        self.assertIn(f"{ORG}/ecu: PR open https://github.com/{ORG}/ecu/pull/1 (already open, left alone)", again.stdout)
        self.assertEqual([c for c in self.calls() if c["argv"][:2] == ["pr", "create"]].__len__(), 1)

    def test_an_open_pr_that_differs_is_left_alone(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        bare = self.tmp / "remotes" / ORG / "ecu.git"
        git(bare, "branch", BRANCH, "main")
        self.state["prs"].append({"repo": f"{ORG}/ecu", "number": 7, "url": f"https://github.com/{ORG}/ecu/pull/7",
                                  "head": BRANCH, "state": "open"})
        self.save()
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(self.prs()), 1)
        self.assertEqual(git(bare, "rev-parse", BRANCH), git(bare, "rev-parse", "main"))
        self.assertIn("left alone; it differs from what this run would write", done.stdout)

    def test_the_bots_branch_left_without_a_pr_is_replaced(self):
        # An earlier run pushed the branch, then failed before opening the PR.
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        self.push_branch("ecu", [BOT_ENV])
        done = self.run_fan_out("--app-slug", "twr-release")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(self.prs()), 1)
        self.assertIn(self.sha, self.on_branch("ecu", ".github/workflows/ci.yml"))

    def test_a_branch_with_someone_elses_work_is_left_alone(self):
        # The maintainer closed the PR on purpose and kept the branch with a commit
        # of his own on it, or the bot's commit has another on top: a plain push
        # --force would replace either, and this test would fail.
        person = {"GIT_AUTHOR_NAME": "maintainer", "GIT_AUTHOR_EMAIL": "maintainer@example.com",
                  "GIT_COMMITTER_NAME": "maintainer", "GIT_COMMITTER_EMAIL": "maintainer@example.com"}
        cases = {"his own commit": [person], "the bot's commit and his": [BOT_ENV, person],
                 "two of the bot's": [BOT_ENV, BOT_ENV]}
        for n, (case, commits) in enumerate(cases.items()):
            with self.subTest(case):
                name = f"repo{n}"
                self.remote(name, {".github/workflows/ci.yml": CI})
                head = self.push_branch(name, commits)
                done = self.run_fan_out("--app-slug", "twr-release", "--repo", f"{ORG}/{name}")
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(git(self.tmp / "remotes" / ORG / f"{name}.git", "rev-parse", BRANCH).strip(), head)
                self.assertEqual([p for p in self.prs() if p["repo"] == f"{ORG}/{name}"], [])
                self.assertIn(f"{ORG}/{name}: skipped", done.stdout)
                self.assertIn("isn't one commit by the App's bot", done.stdout)

    def test_a_push_to_the_branch_during_the_run_is_not_overwritten(self):
        # The bot's leftover branch passes the check, then someone pushes to it before
        # the run's own push. The push is leased against the commit checked, so it is
        # refused; a plain --force would overwrite theirs, and this test would fail.
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        self.push_branch("ecu", [BOT_ENV])
        bare = self.tmp / "remotes" / ORG / "ecu.git"
        theirs = git(bare, "rev-parse", "main").strip()
        real_git = shutil.which("git")
        wrapper = self.tmp / "bin" / "git"
        wrapper.write_text(
            f"#!{sys.executable}\nimport os, subprocess, sys\n"
            f"if 'push' in sys.argv and os.path.exists({str(self.tmp / 'race')!r}):\n"
            f"    os.remove({str(self.tmp / 'race')!r})\n"
            f"    subprocess.run([{real_git!r}, '-C', {str(bare)!r}, 'update-ref', 'refs/heads/{BRANCH}', {theirs!r}], check=True)\n"
            f"os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])\n")
        wrapper.chmod(0o755)
        (self.tmp / "race").write_text("")
        done = self.run_fan_out("--app-slug", "twr-release")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn(f"{ORG}/ecu: failed", done.stderr)
        self.assertEqual(git(bare, "rev-parse", BRANCH).strip(), theirs)
        self.assertEqual(self.prs(), [])

    def test_without_the_apps_identity_no_branch_is_replaced(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        head = self.push_branch("ecu", [BOT_ENV])
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(git(self.tmp / "remotes" / ORG / "ecu.git", "rev-parse", BRANCH).strip(), head)
        self.assertEqual(self.prs(), [])

    # Older fan-out PRs.

    def older_pr(self, repo: str, number: int, tag: str, author: str = "app/twr-release") -> dict:
        pr = {"repo": repo, "number": number, "url": f"https://github.com/{repo}/pull/{number}",
              "head": f"chore/github-{tag}", "state": "open", "author": author}
        self.state["prs"].append(pr)
        return pr

    def test_an_older_fan_out_pr_is_closed_and_its_branch_kept(self):
        repo = self.remote("ecu", {".github/workflows/ci.yml": CI})
        self.push_branch("ecu", [BOT_ENV], branch="chore/github-v0.2.8")
        self.older_pr(repo, 1, "v0.2.8")
        self.older_pr(repo, 2, "v0.2.7", author="maintainer")  # not the bot's
        self.older_pr(repo, 3, "v0.4.0")  # a later release
        self.state["prs"].append({"repo": repo, "number": 4, "url": "u", "head": "feature", "state": "open",
                                  "author": "app/twr-release"})
        self.save()
        done = self.run_fan_out("--app-slug", "twr-release")
        self.assertEqual(done.returncode, 0, done.stderr)
        prs = {p["number"]: p for p in self.prs()}
        self.assertEqual({n: p["state"] for n, p in prs.items()},
                         {1: "closed", 2: "open", 3: "open", 4: "open", 5: "open"})
        self.assertEqual(prs[5]["head"], BRANCH)
        self.assertIn(prs[5]["url"], prs[1]["comment"])
        self.assertIn("the branch is kept", prs[1]["comment"])
        [close] = [c for c in self.calls() if c["argv"][:2] == ["pr", "close"]]
        self.assertNotIn("--delete-branch", close["argv"])
        self.assertIn("chore/github-v0.2.8", self.branches("ecu"))
        self.assertIn("closed #1 as superseded", done.stdout)

    def test_a_dry_run_names_the_pr_it_would_close(self):
        repo = self.remote("ecu", {".github/workflows/ci.yml": CI})
        self.older_pr(repo, 1, "v0.2.8")
        self.save()
        done = self.run_fan_out("--app-slug", "twr-release", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("would close #1 as superseded", done.stdout)
        self.assertEqual(self.prs()[0]["state"], "open")

    def test_a_repository_with_no_workflow_references(self):
        self.remote("dash", {".github/workflows/ci.yml": "jobs:\n  x:\n    runs-on: ubuntu-latest\n",
                             "renovate.json": RENOVATE})
        self.remote("podium", {"README.md": "nothing to move\n"})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        [pr] = self.prs()
        self.assertEqual(pr["repo"], f"{ORG}/dash")
        self.assertNotIn(".github/workflows", pr["body"])
        self.assertEqual(self.on_branch("dash", ".github/workflows/ci.yml"), "jobs:\n  x:\n    runs-on: ubuntu-latest\n")
        self.assertIn(f"{ORG}/podium: current", done.stdout)

    # Symbolic links: nothing outside the clone is read or written.

    def outside(self) -> Path:
        out = self.tmp / "outside"
        out.mkdir(exist_ok=True)
        (out / "victim.py").write_bytes(b"precious\n")
        (out / "ci.yml").write_text(CI)
        return out

    def assert_untouched(self, out: Path):
        self.assertEqual((out / "victim.py").read_bytes(), b"precious\n")
        self.assertEqual((out / "ci.yml").read_text(), CI)

    def test_symbolic_links_are_refused(self):
        out = self.outside()
        cases = {
            "a guard copy": {".claude/hooks/guard.py": Link(out / "victim.py"), "renovate.json": RENOVATE},
            "a workflow file": {".github/workflows/ci.yml": Link(out / "ci.yml"), "renovate.json": RENOVATE},
            "the workflows directory": {".github/workflows": Link(out), "renovate.json": RENOVATE},
            "the .claude directory": {".claude": Link(out), "renovate.json": RENOVATE},
        }
        for n, (case, files) in enumerate(cases.items()):
            for dry in (False, True):
                with self.subTest(case, dry_run=dry):
                    name = f"link{n}{'d' if dry else ''}"
                    self.remote(name, files)
                    args = ["--repo", f"{ORG}/{name}"] + (["--dry-run"] if dry else [])
                    done = self.run_fan_out(*args)
                    self.assertEqual(done.returncode, 1, done.stdout)
                    self.assertIn(f"{ORG}/{name}: failed", done.stderr)
                    self.assertIn("refusing to follow it", done.stderr)
                    self.assert_untouched(out)
                    self.assertEqual(self.branches(name), ["main"])
                    # Nothing from the outside file reaches the diff either.
                    self.assertNotIn("precious", done.stdout)
        self.assertEqual(self.prs(), [])

    def test_each_guard_against_links_on_its_own(self):
        # Two checks, each enough alone: git's modes before anything is read, and the
        # path on disk before each read and write.
        for files in ({".github/workflows/ci.yml": "120000"}, {".github/workflows": "120000"},
                      {".claude/hooks/guard.py": "160000"}, {"template": "120000", "copier.yml": "100644"}):
            with self.subTest(files):
                clone = self.tmp / "clone-modes"
                clone.mkdir(exist_ok=True)
                (clone / "copier.yml").write_text("_subdirectory: template\n")
                with self.assertRaisesRegex(fan_out.Failure, "refusing to follow it"):
                    fan_out.candidates(clone, files)
        clone = self.tmp / "clone-disk"
        (clone / ".github").mkdir(parents=True)
        out = self.outside()
        (clone / ".github" / "workflows").symlink_to(out)
        (clone / "guard.py").symlink_to(out / "victim.py")
        for rel in (".github/workflows/ci.yml", "guard.py", "../outside/victim.py"):
            with self.subTest(rel):
                with self.assertRaises(fan_out.Failure):
                    fan_out.safe(clone, rel)

    def test_a_link_elsewhere_in_the_repository_is_no_matter(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI, "docs/link": Link("../README.md"), "README.md": "x\n"})
        self.assertEqual(self.run_fan_out().returncode, 0)
        self.assertEqual(len(self.prs()), 1)

    # Unexpected input.

    def test_a_file_that_isnt_utf8_is_skipped(self):
        self.remote("aaa", {".github/workflows/ci.yml": CI, ".github/workflows/latin.yml": b"# caf\xe9\n" + pinned("x.yml").encode()})
        self.remote("bbb", {".github/workflows/ci.yml": CI})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(sorted(p["repo"] for p in self.prs()), [f"{ORG}/aaa", f"{ORG}/bbb"])
        self.assertIn(".github/workflows/latin.yml is not UTF-8, so it was left as it is", done.stdout)
        bare = self.tmp / "remotes" / ORG / "aaa.git"
        self.assertIn(OLD_SHA.encode(), subprocess.run(
            ["git", "show", f"{BRANCH}:.github/workflows/latin.yml"], cwd=bare, check=True, capture_output=True,
            env={**ENV, **GIT_ENV}).stdout)

    def test_an_unexpected_error_fails_that_repository_only(self):
        self.remote("aaa", {".github/workflows/ci.yml": CI})
        self.remote("bbb", {".github/workflows/ci.yml": CI})
        self.state["garbage"] = [f"{ORG}/aaa"]  # gh prints something that isn't JSON
        self.save()
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 1)
        self.assertIn(f"{ORG}/aaa: failed (unexpected JSONDecodeError", done.stderr)
        self.assertEqual([p["repo"] for p in self.prs()], [f"{ORG}/bbb"])
        summary = Path(self.env["GITHUB_STEP_SUMMARY"]).read_text()
        self.assertIn(f"| {ORG}/aaa | failed |", summary)
        self.assertIn(f"| {ORG}/bbb | opened |", summary)

    def test_a_template_subdirectory_behind_a_linked_temporary_directory(self):
        real = self.tmp / "real-tmp"
        real.mkdir()
        (self.tmp / "linked-tmp").symlink_to(real)
        self.env["TMPDIR"] = str(self.tmp / "linked-tmp")
        self.remote("chassis", {"copier.yml": "_subdirectory: template\n",
                                "template/renovate.json.jinja": RENOVATE})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.on_branch("chassis", "template/renovate.json.jinja"), RENOVATE.replace(OLD, NEW))

    def test_a_subdirectory_that_climbs_out_is_ignored(self):
        for value in ("../x", "/etc", "[[ name ]]"):
            with self.subTest(value):
                self.assertIsNone(fan_out.subdirectory(value))
        self.assertEqual(fan_out.subdirectory("./template/"), "template")

    def test_a_longer_owner_name_is_not_this_repository(self):
        text = f"    uses: x{ORG}/.github/.github/workflows/a.yml@{OLD_SHA} # {OLD}\n"
        self.assertEqual(fan_out.rewrite_pins(text, self.sha, NEW), text)
        self.assertEqual(fan_out.referenced_versions(text), [])
        for before in ("my-", "a.", "_"):
            with self.subTest(before):
                other = text.replace(f"x{ORG}", f"{before}{ORG}")
                self.assertEqual(fan_out.rewrite_pins(other, self.sha, NEW), other)
        # Still found after a space, a quote or `uses:`.
        for line in (f"uses: {ORG}/.github/x.yml@{OLD}", f"uses: '{ORG}/.github/x.yml@{OLD}'"):
            with self.subTest(line):
                self.assertIn(NEW, fan_out.rewrite_pins(line, self.sha, NEW))

    def test_a_bare_sha_without_a_tag_is_left_alone(self):
        bare_sha = f"    uses: {ORG}/.github/.github/workflows/image.yml@{OLD_SHA}\n"
        self.remote("ecu", {".github/workflows/ci.yml": CI, ".github/workflows/image.yml": "jobs:\n  x:\n" + bare_sha})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.on_branch("ecu", ".github/workflows/image.yml"), "jobs:\n  x:\n" + bare_sha)
        self.assertIn(self.sha, self.on_branch("ecu", ".github/workflows/ci.yml"))
        summary = Path(self.env["GITHUB_STEP_SUMMARY"]).read_text()
        self.assertIn(".github/workflows/image.yml pins those-who-run/.github at a commit SHA with no tag comment", summary)
        # The release's own SHA without a comment is current, not unknown.
        self.assertFalse(fan_out.unversioned(bare_sha.replace(OLD_SHA, self.sha), self.sha))

    # Dry runs and failures.

    def test_dry_run_prints_the_diff_and_touches_nothing(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI, "renovate.json": RENOVATE})
        done = self.run_fan_out("--dry-run")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"-  \"extends\": [\"github>{ORG}/.github#{OLD}\"]", done.stdout)
        self.assertIn(f"+  \"extends\": [\"github>{ORG}/.github#{NEW}\"]", done.stdout)
        self.assertIn(f"+{pinned('checks.yml', self.sha, NEW)}", done.stdout)
        self.assertIn(f"{ORG}/ecu: dry run (would open a PR changing 2 files)", done.stdout)
        self.assertEqual(self.prs(), [])
        self.assertEqual(self.branches("ecu"), ["main"])
        self.assertEqual({tuple(c["argv"][:2]) for c in self.calls()}, {("pr", "list")})
        self.assertIn("(dry run)", Path(self.env["GITHUB_STEP_SUMMARY"]).read_text())

    def test_one_failure_doesnt_stop_the_others(self):
        self.remote("contracts", {".github/workflows/ci.yml": CI})
        self.repos.append(f"{ORG}/gone")  # no remote: the clone fails
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        self.remote("salsa", {".github/workflows/ci.yml": CI})
        self.state["refuse"] = [f"{ORG}/salsa"]
        self.save()
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(sorted(p["repo"] for p in self.prs()), [f"{ORG}/contracts", f"{ORG}/ecu"])
        self.assertIn(f"{ORG}/gone: failed", done.stderr)
        self.assertIn(f"{ORG}/salsa: failed", done.stderr)
        self.assertIn("2 of 4 repositories failed", done.stderr)
        summary = Path(self.env["GITHUB_STEP_SUMMARY"]).read_text()
        self.assertIn(f"| {ORG}/gone | failed |", summary)
        self.assertIn(f"| {ORG}/ecu | opened |", summary)

    def test_bad_arguments(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        listing = self.tmp / "repos.json"
        for args in (["--tag", "0.3.0"], ["--tag", "v0.3"], ["--tag", NEW, "--repo", f"{ORG}/.github"],
                     ["--tag", "v9.9.9"]):
            with self.subTest(args=args):
                done = subprocess.run(
                    [sys.executable, str(SCRIPT), "--source", str(self.source), "--list", str(listing), *args],
                    env={**ENV, **self.env}, capture_output=True, text=True)
                self.assertEqual(done.returncode, 2, done.stderr)
        self.assertEqual(self.calls(), [])

    def test_names_for_the_token(self):
        listing = self.tmp / "repos.json"
        listing.write_text(json.dumps({"repositories": {f"{ORG}/.github": {}, f"{ORG}/ecu": {}, f"{ORG}/dash": {}}}))
        done = subprocess.run([sys.executable, str(SCRIPT), "--names", "--list", str(listing)],
                              capture_output=True, text=True, check=True)
        self.assertEqual(done.stdout, "ecu,dash\n")

    def test_the_real_list_names_every_other_repository(self):
        names = fan_out.repositories(fan_out.LIST)
        self.assertNotIn("those-who-run/.github", names)
        self.assertIn("those-who-run/chassis", names)


class TheFanOutJob(unittest.TestCase):
    """release-pr.yml's fan-out job: after a release only, on main, in the
    release-please environment, as the release App with the three permissions the
    fan-out writes with, and nothing else."""

    @classmethod
    def setUpClass(cls):
        cls.text = (WORKFLOWS / "release-pr.yml").read_text()
        cls.job = jobs(cls.text)["fan-out"]
        cls.block = cls.text[cls.text.index("\n  fan-out:\n"):]

    def test_it_runs_after_a_release_on_main(self):
        keys = self.job["keys"]
        self.assertEqual(keys["needs"], "release")
        self.assertEqual(keys["if"], "needs.release.outputs.release_created == 'true' && github.ref == 'refs/heads/main'")
        self.assertEqual(keys["environment"], "release-please")
        self.assertEqual(self.job["permissions"], {"contents": "read"})

    def test_the_apps_token(self):
        self.assertRegex(self.block, r"uses: actions/create-github-app-token@[0-9a-f]{40} # v\d+\.\d+\.\d+\n")
        self.assertIn("client-id: ${{ vars.RELEASE_APP_CLIENT_ID }}", self.block)
        self.assertIn("private-key: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}", self.block)
        # Only the repositories it opens PRs in.
        self.assertIn("repositories: ${{ steps.repos.outputs.names }}", self.block)
        self.assertIn("run: echo \"names=$(python3 scripts/fan_out.py --names)\" >> \"$GITHUB_OUTPUT\"", self.block)
        asked = set(re.findall(r"(?m)^          permission-([a-z-]+): (\w+)", self.block))
        self.assertEqual(asked, {("contents", "write"), ("pull-requests", "write"), ("workflows", "write")})
        self.assertNotIn("skip-token-revoke", self.block)
        # Handed to the script in its step's environment only, once.
        self.assertEqual(self.block.count("steps.app.outputs.token"), 1)
        self.assertIn("          GH_TOKEN: ${{ steps.app.outputs.token }}\n", self.block)

    def test_a_token_that_cant_be_minted_says_where_to_look(self):
        self.assertIn("      - name: Why there is no token\n        if: failure() && steps.app.outcome == 'failure'\n",
                      self.block)
        hint = self.block[self.block.index("Why there is no token"):self.block.index("A PR in every repository")]
        for words in ("installation", "Workflows", "repository access", "GITHUB_STEP_SUMMARY"):
            self.assertIn(words, hint)
        # It prints no secret, variable or step output.
        self.assertNotIn("${{", hint)

    def test_it_checks_out_the_release_and_runs_the_script(self):
        self.assertIn("ref: ${{ needs.release.outputs.tag_name }}", self.block)
        self.assertIn("persist-credentials: false", self.block)
        self.assertIn('run: python3 scripts/fan_out.py --tag "$TAG" --app-slug "$APP_SLUG"', self.block)
        self.assertNotIn("--dry-run", self.block)
        # The tag reaches the script through the environment, never inline in run:.
        self.assertNotRegex(self.block, r"run:.*\$\{\{")


if __name__ == "__main__":
    unittest.main()
