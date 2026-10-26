#!/usr/bin/env bash
# Set up, or check, the `release-please` environment of every repository that
# releases: the environment release-please's job runs in, holding the release App's
# Client ID and private key (.github/workflows/README.md, "The repository's settings").
#
# Run by the maintainer, with his own gh login. Never by an agent: it changes
# repository settings and stores the release App's private key. Agents never handle
# the key.
#
#   scripts/release-envs.sh --check [<owner/repo>]...
#   CLIENT_ID=<Client ID> PEM=<key file> scripts/release-envs.sh [<owner/repo>]...
#
# Which repositories release: those in scripts/repos.json with a
# release-please-config.json on their default branch. release-please.yml, the reusable
# workflow every repository calls to release, reads that file, so a repository that
# calls it has one, and one without it can't release. Naming repositories limits the
# run to them, and takes them whether or not they are in the list or have the file
# yet (a new repository). Only a 404 for the file means "doesn't release": any other
# failed read stops the run with exit 2, and so does a list in which nothing releases.
#
# Without --check, a repository's setup stops at the first write GitHub refuses, and
# the variable and the secret are written only after the environment has been read
# back with custom_branch_policies true and main as its only branch policy.
#
# What a repository needs:
#   - an environment named release-please, deployments from custom branch policies
#     (custom_branch_policies true), with one branch policy: main;
#   - the environment variable RELEASE_APP_CLIENT_ID;
#   - the environment secret RELEASE_APP_PRIVATE_KEY, and no repository secret of that
#     name: a repository-level copy reaches every workflow, not only the job that
#     names the environment.
#
# --check prints, for each repository, custom_branch_policies, the branch policy names,
# the environment's secret and variable names and the repository's secret names, then
# a line for each problem; it exits 1 on any problem. It never prints a value.
#
# Without --check it creates or updates the environment, its branch policy, the
# variable and the secret in each repository, then checks them. The Client ID and the
# key file's path come from the environment variables CLIENT_ID and PEM. `make envs
# CLIENT_ID=... PEM=...` puts both on make's own command line (and in MAKEFLAGS),
# where ps and the shell's history see them; neither is a secret, the path being only
# a path. Inside this script they reach gh only on stdin, never in its arguments, and
# no message prints either. The key file's contents are never held in a variable.
#
# A repository-level copy of the key is reported, not deleted: delete it with
# `gh secret delete RELEASE_APP_PRIVATE_KEY --repo <owner/repo>`. Every releasing
# repository shares one key of the App (.github/workflows/README.md, "The repository's
# settings", says why and how to rotate it); naming one repository sets up that one
# alone, such as a new repository.
#
# Needs: gh and jq.
set -euo pipefail

usage() {
  echo "usage: release-envs.sh [--check] [<owner/repo>]... [--list FILE]" >&2
  exit 2
}
fail() { echo "$*" >&2; exit 2; }

ENV_NAME=release-please
VARIABLE=RELEASE_APP_CLIENT_ID
SECRET=RELEASE_APP_PRIVATE_KEY
list="$(cd "$(dirname "$0")" && pwd)/repos.json"
mode=apply
named=()
while [ $# -gt 0 ]; do
  case "$1" in
    --check) mode=check; shift ;;
    --list) [ $# -ge 2 ] || usage; list=$2; shift 2 ;;
    -h|--help) usage ;;
    -*) echo "unknown option: $1" >&2; usage ;;
    *) named+=("$1"); shift ;;
  esac
done

if [ "$mode" = apply ]; then
  # Refused before anything is read. The messages name neither value.
  [ -n "${CLIENT_ID:-}" ] || fail "CLIENT_ID is not set: give the release App's Client ID (make envs CLIENT_ID=... PEM=...)"
  [ -n "${PEM:-}" ] || fail "PEM is not set: give the path of the release App's private key file (make envs CLIENT_ID=... PEM=...)"
  [ -f "$PEM" ] && [ -r "$PEM" ] || fail "PEM does not name a readable file"
  grep -q -- '-----BEGIN [A-Z ]*PRIVATE KEY-----' "$PEM" || fail "PEM does not name a PEM private key file"
fi

[ -f "$list" ] || fail "no list at $list"
listed=$(jq -r '.repositories | keys[]' "$list") || fail "$list is not a list of repositories"

# The repositories to look at: the named ones, as they are (a new repository may not
# be in the list or have its release-please-config.json yet), or those in the list
# that release, which have a release-please-config.json on the default branch.
repos=()
candidates=()
if [ ${#named[@]} -gt 0 ]; then
  for r in "${named[@]}"; do
    [[ "$r" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] || fail "$r: not an owner/repo name"
    repos+=("$r")
  done
else
  mapfile -t candidates <<<"$listed"
fi
# Only GitHub's 404 means the file isn't there. Any other failure (no access, an expired
# login, a rate limit, the network) stops the run: taking it for "doesn't release"
# would skip the repository and report success having looked at nothing.
for r in ${candidates[@]+"${candidates[@]}"}; do
  if err=$(gh api "repos/$r/contents/release-please-config.json" 2>&1 >/dev/null); then
    repos+=("$r")
  elif grep -q '(HTTP 404)' <<<"$err"; then
    echo "$r: no release-please-config.json on the default branch, so it doesn't release; skipped"
  else
    fail "$r: can't tell whether it releases: reading release-please-config.json failed (${err//$'\n'/ }); check the gh login (gh auth status) and try again"
  fi
done
[ ${#repos[@]} -gt 0 ] || fail "no repository to look at: none in $list has a release-please-config.json"

# Names only: every list is reduced to its names before anything is printed.
names() { jq -r "[$1] | sort | if length == 0 then \"none\" else join(\", \") end"; }

problems=0
problem() { echo "$repo: wrong: $*"; problems=$((problems + 1)); }
# all PATH KEY: every page of a list GitHub returns as {KEY: [...]}, joined.
all() { gh api --paginate "$1" 2>/dev/null | jq -cs --arg k "$2" 'map(.[$k] // []) | add // []'; }
check_repo() {
  local env pol vars secrets repo_secrets custom
  if ! env=$(gh api "repos/$repo/environments/$ENV_NAME" 2>/dev/null); then
    problem "no $ENV_NAME environment"
    return 0
  fi
  custom=$(jq -r '.deployment_branch_policy.custom_branch_policies // false' <<<"$env")
  echo "$repo: custom_branch_policies: $custom"
  [ "$custom" = true ] || problem "custom_branch_policies is $custom, want true (deployments from main only)"
  if pol=$(all "repos/$repo/environments/$ENV_NAME/deployment-branch-policies" branch_policies); then
    echo "$repo: branch policies: $(names '.[] | "\(.name) (\(.type // "branch"))"' <<<"$pol")"
    jq -e 'map("\(.name):\(.type // "branch")") == ["main:branch"]' >/dev/null <<<"$pol" ||
      problem "the branch policies must be main alone"
  else
    problem "can't read the branch policies"
  fi
  if vars=$(all "repos/$repo/environments/$ENV_NAME/variables" variables); then
    echo "$repo: environment variables: $(names '.[].name' <<<"$vars")"
    jq -e --arg v "$VARIABLE" 'any(.[]; .name == $v)' >/dev/null <<<"$vars" ||
      problem "no environment variable $VARIABLE"
  else
    problem "can't read the environment's variables"
  fi
  if secrets=$(all "repos/$repo/environments/$ENV_NAME/secrets" secrets); then
    echo "$repo: environment secrets: $(names '.[].name' <<<"$secrets")"
    jq -e --arg s "$SECRET" 'any(.[]; .name == $s)' >/dev/null <<<"$secrets" ||
      problem "no environment secret $SECRET"
  else
    problem "can't read the environment's secret names"
  fi
  if repo_secrets=$(all "repos/$repo/actions/secrets" secrets); then
    echo "$repo: repository secrets: $(names '.[].name' <<<"$repo_secrets")"
    if jq -e --arg s "$SECRET" 'any(.[]; .name == $s)' >/dev/null <<<"$repo_secrets"; then
      problem "$SECRET is a repository secret, which every workflow can read: keep it in the $ENV_NAME environment only (gh secret delete $SECRET --repo $repo)"
    fi
  else
    problem "can't read the repository's secret names"
  fi
}

refused=0
setup_repo() {
  local pol env
  # Any failure stops the setup of this repository where it is: nothing after it,
  # least of all the key, is written.
  step() {
    echo "$repo: not set up: GitHub refused $1 (its message is above); the variable and the secret were not written" >&2
    refused=$((refused + 1))
  }
  unsafe() {
    echo "$repo: not set up: read back after the writes, the environment is not safe for the key: $1; the variable and the secret were not written" >&2
    refused=$((refused + 1))
  }
  # No reviewers and no wait: release-please runs on every push to main.
  jq -n '{wait_timer: 0, reviewers: [], deployment_branch_policy: {protected_branches: false, custom_branch_policies: true}}' |
    gh api -X PUT "repos/$repo/environments/$ENV_NAME" --input - >/dev/null || { step "the environment"; return 0; }
  pol=$(gh api --paginate "repos/$repo/environments/$ENV_NAME/deployment-branch-policies" |
    jq -cs 'map(.branch_policies // []) | add // []') || { step "a read of the branch policies"; return 0; }
  if ! jq -e 'any(.[]; .name == "main" and (.type // "branch") == "branch")' >/dev/null <<<"$pol"; then
    gh api -X POST "repos/$repo/environments/$ENV_NAME/deployment-branch-policies" \
      -f name=main -f type=branch >/dev/null || { step "the main branch policy"; return 0; }
  fi
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    gh api -X DELETE "repos/$repo/environments/$ENV_NAME/deployment-branch-policies/$id" >/dev/null ||
      { step "the removal of a branch policy other than main"; return 0; }
  done < <(jq -r '.[] | select((.name == "main" and (.type // "branch") == "branch") | not) | .id' <<<"$pol")
  # The key goes only into an environment that deploys from main alone: read it back
  # and stop for this repository, before either value is written, unless it does.
  if ! env=$(gh api "repos/$repo/environments/$ENV_NAME"); then
    step "a read of the environment"
    return 0
  fi
  if [ "$(jq -r '.deployment_branch_policy.custom_branch_policies // false' <<<"$env")" != true ]; then
    unsafe "custom_branch_policies isn't true"
    return 0
  fi
  if ! pol=$(gh api --paginate "repos/$repo/environments/$ENV_NAME/deployment-branch-policies" |
       jq -cs 'map(.branch_policies // []) | add // []'); then
    step "a read of the branch policies"
    return 0
  fi
  if ! jq -e 'map("\(.name):\(.type // "branch")") == ["main:branch"]' >/dev/null <<<"$pol"; then
    unsafe "its branch policies aren't main alone"
    return 0
  fi
  # Inside this script the values reach gh only on stdin, never in its arguments or
  # its output.
  if ! printf '%s' "$CLIENT_ID" | gh variable set "$VARIABLE" --env "$ENV_NAME" --repo "$repo" >/dev/null; then
    step "the variable $VARIABLE"
    return 0
  fi
  if ! gh secret set "$SECRET" --env "$ENV_NAME" --repo "$repo" <"$PEM" >/dev/null; then
    echo "$repo: not set up: GitHub refused the secret $SECRET (its message is above)" >&2
    refused=$((refused + 1))
    return 0
  fi
  echo "$repo: $ENV_NAME environment set up"
}

for repo in "${repos[@]}"; do
  [ "$mode" = check ] || setup_repo
  check_repo
done
if [ "$refused" -gt 0 ]; then
  echo "failed: the setup stopped in $refused repository(s); the messages are above" >&2
  exit 1
fi
if [ "$problems" -gt 0 ]; then
  echo "$problems problem(s) in the $ENV_NAME environments"
  exit 1
fi
echo "every $ENV_NAME environment is set up"
