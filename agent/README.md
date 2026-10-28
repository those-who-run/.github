# The agent guard

The one copy of the Claude Code guard and project settings that every repository in
the organisation takes. Each repository copies these files unchanged; what differs
between repositories lives in its own `.claude/guard.json`.

| File | Goes to | What it is |
| --- | --- | --- |
| `guard.py` | `.claude/hooks/guard.py` | The `PreToolUse` hook that checks every Bash command an agent sends. |
| `settings.json` | `.claude/settings.json` | The project settings: secrets denied to the Read and Edit tools, an ask before editing the settings, the guard or its config, bypass mode off, and the hook that runs the guard. |
| `guard.example.json` | `.claude/guard.json` | The repository's values. Copy it and change what differs. |
| `tests/` | stays here | The guard's tests. |

The guard started in v1 as those-who-run/those-who-run#352. Its docstring says what it
blocks, what it asks about and what it can't see; the tables in `guard.py` are the
policy.

## What the guard does

- **Blocks** (the agent is told why and to ask the maintainer): skipping the commit
  hooks (including `pre-commit uninstall`), pushing to the protected branch,
  force-pushing, deleting remote branches or tags (`--delete`, `--prune`, a `:ref`
  refspec), pushing any tag by name or with `--tags`, creating a release tag,
  `gh pr merge --admin`, creating or changing releases, running or switching
  workflows, changing the repository's settings, secrets, variables and rulesets,
  printing the `gh` token or changing its login (`token`, `login`, `logout` and
  `refresh` under `gh auth`), writes to a cluster or a registry, reading a secret
  from Bash, and the maintainer's own jobs in `those-who-run/.github`: the make
  targets `board`, `board-check`, `protect`, `protect-check`, `envs`, `envs-check`
  and `new-repo-check`, and the scripts behind them (`protect-repo.sh`,
  `board-sync.sh`, `release-envs.sh`, `new-repo-check.sh`), whether run by path,
  through a shell or with `source`. Reading or linting those scripts passes. Two dry
  runs pass, and only in exactly these forms: `bash -n <script>` (or `sh -n`, `dash
  -n`, `zsh -n`) and `make -n <target>`. A shell given one of the scripts anywhere in
  its arguments is otherwise blocked, whatever its options, since an option that takes
  a value (`-eo pipefail`, `+xo noexec`) moves where the script really is; so is any
  other make command naming a target. Longer dry runs such as `bash -nx <script>`,
  `make -nk <target>` or `make --dry-run <target>` are blocked too. Every repository takes
  the same guard, so a target of one of those names is blocked in any repository;
  name a repository's own targets differently. Known gaps, as for any command the
  guard can't see into: a script fed to a shell on stdin (`bash < script`,
  `cat script | bash`), a target name that reaches make at run time (`echo board |
  xargs make`, `t=board; make $t`), make running a command of its own
  (`make -E '$(shell ...)'`, `make --eval ...`), and a program that runs make or a
  script itself. `make -n` still runs a recipe line that starts with `+` or uses
  `$(MAKE)`, and every `$(shell ...)` the Makefile expands; the maintainer targets
  use none of these today, so their dry run runs nothing. `gh project item-edit`,
  the write `make board` makes, isn't blocked. That is safe only because agents run
  as the bot account (the identity hook enforces it) and the bot's token can't write
  to the organisation's projects; an agent with the maintainer's login could. The
  tables in `guard.py` have the full list.
- **Asks** the maintainer first: posting in public (`gh issue` and `gh pr` writes),
  any `gh pr merge` (`--auto` and `--disable-auto` included), re-running a workflow
  run, acting on a repository outside the organisation, and any command it can't
  follow that names `git`, `gh`, `kubectl`, `oc`, `helm`, `docker`, `podman` or
  `pre-commit`.
- **Asks before auto-merge and the merge queue** (rule `gh-auto-merge`): a
  `gh api graphql` call whose query uses `enablePullRequestAutoMerge`,
  `disablePullRequestAutoMerge`, `enqueuePullRequest` or `dequeuePullRequest`, as
  `gh pr merge` does. Merging is the maintainer's decision, and once auto-merge is on
  or a PR is in the queue it merges without a further ask. The guard reads a query
  given inline (`-f query=...`, `-F query=...`); a GraphQL request it reads from a
  file or stdin (`--input`, `-F query=@file`, `-F query=@-`) asks whatever it holds.
- **Reads `gh api` endpoints the way gh does**, before any rule on them: a full URL to
  `api.github.com` (in any case), or to a GitHub Enterprise Server's `/api/v3/` or
  `/api/graphql`, meets the same rules as its path, as does a path with a leading `/`,
  a `?query` or a `#fragment`. A write to any other full URL asks; a GraphQL mutation
  the guard blocks still blocks there.
- **Asks about values it can't read before they run**: a `gh api` endpoint, or any
  field of a `gh api graphql` call, holding a shell expansion that the guard doesn't
  resolve. It resolves only a plain `$NAME` or `${NAME}` whose variable the command
  line set with a plain `NAME=value` (or `export NAME=value`) it can read
  (`q='...'; gh api graphql -f query="$q"`); a `${...}` with an operator, `$(...)`,
  backquotes, `$'...'`, a glob or a brace expansion is never resolved, nor is a
  variable named in a prefix assignment of the same command, or one set by `read`,
  `for`, `declare`, `printf -v` and the like. `$(cat <<'EOF' ... EOF)` is read as the
  here-document it prints. What the guard can still see decides first: a GraphQL
  mutation, and the REST rules, which it checks on the endpoint as written, a part
  it can't read standing for any one path segment, so
  `gh api -X PUT "repos/$O/$R/pulls/$N/merge"` blocks as the literal path does. Text
  in single quotes (a GraphQL `$owner`) and gh's own placeholders (`{owner}`,
  `{repo}`) are literal. A `gh api` write with no endpoint the guard can see asks,
  as does `xargs` or `parallel` running a `gh api` write.
- **Fails closed**: if the guard errors, or `.claude/guard.json` is missing or can't be
  read, the command is blocked. Without a readable config every Bash command is
  blocked, with the reason, until the file is fixed; the Read and Edit tools still
  reach it, so nothing needs Bash to fix it.

Blocks and asks are logged, with tokens and passwords redacted, to
`~/.claude/twr-guard.log`. `TWR_GUARD_LOG` moves the log; set it empty to turn it off.

## Taking the guard into a repository

1. Copy the files:

   ```sh
   mkdir -p .claude/hooks
   base=https://raw.githubusercontent.com/those-who-run/.github/main/agent
   curl -fsSL "$base/guard.py" -o .claude/hooks/guard.py
   curl -fsSL "$base/settings.json" -o .claude/settings.json
   curl -fsSL "$base/guard.example.json" -o .claude/guard.json
   ```

2. Edit `.claude/guard.json`. Every key is optional and defaults to the value shown in
   `guard.example.json`; an unknown key, or a key given twice, makes the config
   unreadable, so a misspelt or repeated key blocks rather than being ignored.

   | Key | Default | Meaning |
   | --- | --- | --- |
   | `branch` | `"main"` | The branch that takes changes only through a pull request, by its short name (`main`, not `refs/heads/main`), made of `A-Z a-z 0-9 . _ / -`. Pushes are matched without regard to case. |
   | `release_tags` | `["v*"]` | The tags a release starts from. Each pattern is a prefix followed by one `*`, which stands for a version and so must start with a digit: `v*` covers `v1.2.0` but not `very-old`. List every pattern the repository releases under, such as `["v*", "gen/go/v*"]`. |
   | `release_workflow` | `"release.yml"` | The workflow that publishes a release, named in the reasons the guard gives. |
   | `owner` | `"those-who-run"` | The organisation. `gh` commands on a repository outside it ask first. |

3. Keep the files tracked and the rest of `.claude/` out of git:

   ```gitignore
   .claude/*
   !.claude/settings.json
   !.claude/guard.json
   !.claude/hooks/
   ```

4. Check that it runs (needs `python3` 3.9 or later, nothing else):

   ```sh
   echo '{"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}' \
       | python3 .claude/hooks/guard.py; echo "exit $?"
   ```

   It prints the reason and `exit 2`.

A repository that needs a rule the guard doesn't have raises it here, so that the
canonical copy carries it, rather than changing its own copy.

## Checking a copy against the canonical one

`guard.py` and `settings.json` should be byte for byte the same as the files here.
From the repository's root:

```sh
base=https://raw.githubusercontent.com/those-who-run/.github/main/agent
curl -fsSL "$base/guard.py" | diff - .claude/hooks/guard.py
curl -fsSL "$base/settings.json" | diff - .claude/settings.json
```

No output means the copy is current. With a checkout of this repository beside it,
`sha256sum ../.github/agent/guard.py .claude/hooks/guard.py` gives two equal sums.

## Running the tests

From the root of this repository, with no packages to install:

```sh
python3 -m unittest discover agent/tests
```
