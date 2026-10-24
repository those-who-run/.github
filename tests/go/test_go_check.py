"""scripts/go_check.py against tests/go/fixture: the module passes every check, and
the same module broken one way at a time fails the matching check, for the reason
that check gives.

It needs Go (the version in fixture/go.mod) and the network, for the tools in
fixture/tools/go.mod and govulncheck's database, so it lives outside the tests that
`python3 -m unittest discover tests` finds. CI's `go-check-cases` job runs it:

    python3 -m unittest discover -s tests/go -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "go" / "fixture"
GO_CHECK = ROOT / "scripts" / "go_check.py"

# A broken case must not leave error annotations or a summary on the CI run that
# tests it: those are for a caller's real failures. Nor may the go command see a
# commit hook's GIT_DIR and the like.
ENV = {
    k: v
    for k, v in os.environ.items()
    if k not in ("GITHUB_ACTIONS", "GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT") and not k.startswith("GIT_")
}


class GoCheck(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.module = Path(tmp.name) / "fixture"
        shutil.copytree(FIXTURE, self.module)

    def write(self, name: str, text: str) -> None:
        path = self.module / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def check(self, *args: str) -> tuple[int, str]:
        proc = subprocess.run(
            [sys.executable, str(GO_CHECK), *args],
            cwd=self.module,
            env=ENV,
            capture_output=True,
            text=True,
        )
        return proc.returncode, proc.stdout + proc.stderr

    def assertFails(self, check: str, reason: str, *args: str) -> None:
        code, out = self.check(check, *args)
        self.assertEqual(code, 1, f"{check} passed:\n{out}")
        self.assertIn(reason, out)

    def test_the_fixture_passes_every_check(self):
        code, out = self.check("all")
        self.assertEqual(code, 0, out)
        self.assertIn("Coverage: 100.0% of statements (the floor is 90%).", out)

    def test_an_unformatted_file(self):
        self.write("pit.go", "package fixture\nfunc  pit( ) int { return 1 }\n")
        self.assertFails("fmt", "not formatted: run gofmt -w on it")

    def test_an_untidy_go_mod(self):
        # A local module, so the case needs no network: required, never imported.
        self.write("unused/go.mod", "module example.com/unused\n\ngo 1.27.0\n")
        self.write("unused/unused.go", "package unused\n")
        with (self.module / "go.mod").open("a") as f:
            f.write(
                "\nrequire example.com/unused v0.0.0\n\n"
                "replace example.com/unused => ./unused\n"
            )
        self.assertFails("tidy", "go.mod or go.sum is not tidy")

    def test_a_vet_finding(self):
        self.write(
            "vet.go",
            'package fixture\n\nimport "fmt"\n\n'
            '// Lapf formats a lap.\nfunc Lapf() string { return fmt.Sprintf("%d", "one") }\n',
        )
        self.assertFails("vet", "Sprintf format %d has arg")

    def test_a_lint_finding(self):
        self.write(
            "lint.go",
            'package fixture\n\nimport "os"\n\n'
            '// Clean removes the scratch file.\nfunc Clean() { os.Remove("scratch") }\n',
        )
        self.assertFails("lint", "errcheck")

    def test_a_data_race(self):
        self.write(
            "race_test.go",
            "package fixture\n\nimport \"testing\"\n\n"
            "func TestRace(t *testing.T) {\n"
            "\tn := 0\n\tdone := make(chan bool)\n"
            "\tgo func() { n++; done <- true }()\n"
            "\tn++\n\t<-done\n\t_ = n\n}\n",
        )
        self.assertFails("test", "WARNING: DATA RACE")

    def test_a_skipped_test(self):
        self.write(
            "skip_test.go",
            'package fixture\n\nimport "testing"\n\n'
            'func TestPitStop(t *testing.T) { t.Skip("later") }\n',
        )
        self.assertFails("test", "test skipped: example.com/fixture.TestPitStop")

    def test_coverage_below_the_floor(self):
        # Four statements nothing calls: 11 of 15 covered, 73.3%.
        self.write(
            "fuel.go",
            "package fixture\n\n// Fuel is the fuel left, in litres.\n"
            "func Fuel(start, used float64) float64 {\n"
            "\tleft := start - used\n\tif left < 0 {\n\t\treturn 0\n\t}\n\treturn left\n}\n",
        )
        self.assertFails("test", "is below the floor of 90%")
        code, out = self.check("test", "--floor", "73")
        self.assertEqual(code, 0, out)

    def test_a_package_without_tests_counts_as_uncovered(self):
        self.write(
            "pits/pits.go",
            "package pits\n\n// Count is the pit stops made.\n"
            "func Count(stops []int) int {\n\tn := 0\n\tfor range stops {\n\t\tn++\n\t}\n"
            "\treturn n\n}\n",
        )
        self.assertFails("test", "is below the floor of 90%")

    def test_generated_files_are_left_out_of_coverage(self):
        # A package of generated code with no tests: without the header it would
        # pull coverage under the floor, as the case above shows.
        body = (
            "package pb\n\n// Laps is the laps run.\n"
            "func Laps(stints []int) int {\n\tn := 0\n\tfor _, s := range stints {\n"
            "\t\tn += s\n\t}\n\treturn n\n}\n"
        )
        header = "// Code generated by protoc-gen-go. DO NOT EDIT.\n// source: laps.proto\n\n"
        self.write("pb/laps.pb.go", header + body)
        code, out = self.check("test")
        self.assertEqual(code, 0, out)
        self.assertIn("Coverage: 100.0% of statements", out)
        self.assertIn("leaves out 1 generated file(s): pb/laps.pb.go", out)
        # The line must be Go's exact form, before the package clause.
        self.write("pb/laps.pb.go", "// Code generated by hand, edit freely.\n\n" + body)
        self.assertFails("test", "is below the floor of 90%")
        self.write("pb/laps.pb.go", body + "\n// Code generated by protoc-gen-go. DO NOT EDIT.\n")
        self.assertFails("test", "is below the floor of 90%")

    def test_a_missing_tools_module(self):
        shutil.rmtree(self.module / "tools")
        self.assertFails("lint", "tools/go.mod is missing")

    def edit_tools(self, old: str, new: str) -> None:
        path = self.module / "tools" / "go.mod"
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1))

    def test_an_untidy_tools_module(self):
        # govulncheck taken out of the tool block, its requirements left behind.
        self.edit_tools("\tgolang.org/x/vuln/cmd/govulncheck\n", "")
        self.assertFails("tidy", "tools/go.mod or its go.sum is not tidy")

    def test_a_replace_in_the_tools_module(self):
        self.write("vuln/go.mod", "module golang.org/x/vuln\n\ngo 1.27.0\n")
        with (self.module / "tools" / "go.mod").open("a") as f:
            f.write("\nreplace golang.org/x/vuln => ../vuln\n")
        for check in ("lint", "vuln"):
            self.assertFails(check, "has a replace directive, for golang.org/x/vuln: remove it")

    def test_a_tools_module_without_golangci_lint(self):
        self.edit_tools("\tgithub.com/golangci/golangci-lint/v2 v2.14.0 // indirect\n", "")
        self.assertFails("lint", "does not require github.com/golangci/golangci-lint/v2")

    def test_a_tools_module_with_another_golangci_lint(self):
        self.edit_tools(
            "\tgithub.com/golangci/golangci-lint/v2/cmd/golangci-lint\n",
            "\texample.com/fork/golangci-lint\n",
        )
        self.assertFails(
            "lint", "tool block does not list github.com/golangci/golangci-lint/v2/cmd/golangci-lint"
        )

    def test_a_tools_module_without_govulncheck(self):
        self.edit_tools("\tgolang.org/x/vuln v1.9.0 // indirect\n", "")
        self.assertFails("vuln", "does not require golang.org/x/vuln")

    def test_a_build_failure(self):
        self.write("broken.go", 'package fixture\n\n// Broken does not compile.\nfunc Broken() int { return "one" }\n')
        self.assertFails("build", "cannot use")

    def test_a_reachable_vulnerability(self):
        # golang.org/x/text before v0.3.7 reads out of bounds in language.Parse
        # (GO-2021-0113), and Tag calls it.
        self.write(
            "lang.go",
            'package fixture\n\nimport "golang.org/x/text/language"\n\n'
            "// Tag parses a language tag.\n"
            "func Tag(s string) (language.Tag, error) { return language.Parse(s) }\n",
        )
        subprocess.run(
            ["go", "get", "golang.org/x/text@v0.3.5"],
            cwd=self.module,
            env=ENV,
            check=True,
            capture_output=True,
        )
        self.assertFails("vuln", "GO-2021-0113")


if __name__ == "__main__":
    unittest.main()
