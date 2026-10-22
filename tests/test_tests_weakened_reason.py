"""scripts/tests_weakened_reason.py: a weakened test needs a `Tests weakened:` reason
in the PR's description, and the Go test guard's finding feeds it, as in go.yml's job.

Standard library and git only:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("tests_weakened_reason")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_go_test_guard import GIT_ENV, LAP_TEST, CleanGit, Repo, guard  # noqa: E402

# Not running in Actions, with no summary file: the cases leave nothing behind.
QUIET = {k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "GIT_"))}


class Reason(unittest.TestCase):
    def test_present(self):
        body = "Fixes a bug.\n\nTests weakened: the bounds were flaky on CI (#348)\n"
        self.assertEqual(check.reason(body), "the bounds were flaky on CI (#348)")

    def test_missing(self):
        self.assertIsNone(check.reason("Fixes a bug.\n"))
        self.assertIsNone(check.reason(""))

    def test_empty(self):
        self.assertIsNone(check.reason("Tests weakened:\n"))

    def test_too_short_after_trimming(self):
        # Nine characters of reason, padded past ten with spaces and tabs.
        self.assertIsNone(check.reason("Tests weakened:    too short \t  \n"))
        self.assertIsNone(check.reason("Tests weakened: demo"))

    def test_ten_characters_after_trimming(self):
        self.assertEqual(check.reason("Tests weakened:   1234567890   "), "1234567890")

    def test_crlf(self):
        body = "Fixes a bug.\r\n\r\nTests weakened: the file tested a removed feature\r\n"
        self.assertEqual(check.reason(body), "the file tested a removed feature")

    def test_crlf_does_not_count_towards_the_length(self):
        self.assertIsNone(check.reason("Tests weakened: too short\r\nMore text.\r\n"))

    def test_indented_line(self):
        self.assertEqual(
            check.reason("  Tests weakened: a removed feature's test"), "a removed feature's test"
        )

    def test_mid_line_does_not_count(self):
        self.assertIsNone(check.reason("No `Tests weakened: a long enough reason` here."))


class Main(unittest.TestCase):
    def run_main(self, *args: str, env: dict[str, str] | None = None) -> int:
        with (
            unittest.mock.patch.dict(os.environ, {**QUIET, **(env or {})}, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return check.main(list(args))

    def body_file(self, text: str) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "body")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return path

    def test_not_weakened_needs_no_reason(self):
        self.assertEqual(self.run_main("--weakened", "false"), 0)

    def test_weakened_with_a_reason_passes(self):
        path = self.body_file("Tests weakened: the test covered a removed flag\r\n")
        self.assertEqual(self.run_main("--weakened", "true", "--body-file", path), 0)

    def test_weakened_without_a_reason_fails(self):
        path = self.body_file("Fixes a bug.\n")
        self.assertEqual(self.run_main("--weakened", "true", "--body-file", path), 1)

    def test_the_description_comes_from_the_api_for_the_events_pr(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        event = os.path.join(tmp.name, "event.json")
        Path(event).write_text(json.dumps({"pull_request": {"number": 9}}))
        seen = []

        def fetch(pr: int) -> str:
            seen.append(pr)
            return "Tests weakened: the test covered a removed flag"

        env = {"GITHUB_EVENT_PATH": event}
        with unittest.mock.patch.object(check, "fetch_body", fetch):
            self.assertEqual(self.run_main("--weakened", "true", env=env), 0)
        self.assertEqual(seen, [9])

    def test_the_request_names_the_repository_and_the_token(self):
        requests = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(request, timeout):
            requests.append(request)
            return Response(b'{"body": null}')

        env = {**QUIET, "GITHUB_REPOSITORY": "those-who-run/lap", "GH_TOKEN": "t0ken"}
        with (
            unittest.mock.patch.dict(os.environ, env, clear=True),
            unittest.mock.patch.object(check.urllib.request, "urlopen", urlopen),
        ):
            self.assertEqual(check.fetch_body(9), "")
        self.assertEqual(requests[0].full_url, "https://api.github.com/repos/those-who-run/lap/pulls/9")
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer t0ken")


class GuardThenReason(CleanGit):
    """go.yml's two steps in a row: the guard's `weakened=` output, then the reason
    check on it."""

    def weakened(self, weaken: bool) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Repo(os.path.join(tmp.name, "repo"))
        repo.write("lap_test.go", LAP_TEST)
        repo.commit("base")
        repo.git("checkout", "-q", "-b", "pr")
        repo.write("lap_test.go", LAP_TEST.replace("\tt.Log", "\tt.Skip()\n\tt.Log") if weaken else LAP_TEST + "\n")
        repo.commit("change")
        out = os.path.join(tmp.name, "output")
        cwd = os.getcwd()
        os.chdir(repo.path)
        self.addCleanup(os.chdir, cwd)
        with (
            unittest.mock.patch.dict(os.environ, {"GITHUB_OUTPUT": out}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(guard.main(["main", "pr"]), 0)
        return Path(out).read_text().strip().removeprefix("weakened=")

    def reason_check(self, weakened: str, body: str) -> int:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "body")
        Path(path).write_text(body)
        env = {k: v for k, v in GIT_ENV.items() if not k.startswith("GITHUB_")}
        with (
            unittest.mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return check.main(["--weakened", weakened, "--body-file", path])

    def test_weakened_with_a_reason_passes(self):
        weakened = self.weakened(True)
        self.assertEqual(weakened, "true")
        self.assertEqual(self.reason_check(weakened, "Tests weakened: TestBest is flaky (#12)\n"), 0)

    def test_weakened_without_a_reason_fails(self):
        weakened = self.weakened(True)
        self.assertEqual(weakened, "true")
        self.assertEqual(self.reason_check(weakened, "Speeds up the lap counter.\n"), 1)

    def test_not_weakened_passes_without_a_reason(self):
        weakened = self.weakened(False)
        self.assertEqual(weakened, "false")
        self.assertEqual(self.reason_check(weakened, "Speeds up the lap counter.\n"), 0)


if __name__ == "__main__":
    unittest.main()
