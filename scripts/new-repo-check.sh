#!/usr/bin/env bash
# Check that a new repository in the organisation has what every v2 repository needs.
# It only reads, and prints a pass or fail line for each check.
#
# Run by the maintainer, with his own gh login (`make new-repo-check REPO=<name>`).
# Never by an agent: some of what it reads (the team's access, the environment's
# secret names) needs an admin login, and the agents leave repository settings to the
# maintainer.
#
#   scripts/new-repo-check.sh <repo> [--owner ORG] [--list FILE]
#
# The checks:
#   - `main` exists and has a LICENSE;
#   - the team pit-crew has push access;
#   - the repository is in scripts/repos.json (then run `make protect`);
#   - its release-please environment is set up (scripts/release-envs.sh --check);
#   - paddock, where every issue lives, has the label `component: <repo>`;
#   - .github/CODEOWNERS exists on `main`.
#
# It exits 1 if any check fails, 2 on bad arguments.
#
# Needs: gh and jq.
set -euo pipefail

usage() {
  echo "usage: new-repo-check.sh <repo> [--owner ORG] [--list FILE]" >&2
  exit 2
}
here="$(cd "$(dirname "$0")" && pwd)"
owner=those-who-run
list="$here/repos.json"
name=""
while [ $# -gt 0 ]; do
  case "$1" in
    --owner) [ $# -ge 2 ] || usage; owner=$2; shift 2 ;;
    --list) [ $# -ge 2 ] || usage; list=$2; shift 2 ;;
    -h|--help) usage ;;
    -*) echo "unknown option: $1" >&2; usage ;;
    *) [ -z "$name" ] || usage; name=$1; shift ;;
  esac
done
# REPO may be given with the organisation or without it.
name=${name#"$owner"/}
[[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "give the repository's name, such as REPO=radio" >&2; usage; }
repo="$owner/$name"
TEAM=pit-crew
ISSUES_REPO="$owner/paddock"
LABEL="component: $name"

failures=0
pass() { echo "pass: $repo: $*"; }
failed() { echo "fail: $repo: $*"; failures=$((failures + 1)); }
exists() { gh api "$1" >/dev/null 2>&1; }

# on_main PATH WHAT: whether the file is on main, unless main itself is missing.
main=false
on_main() {
  if [ "$main" = false ]; then
    failed "no $2: there is no main branch"
  elif exists "repos/$repo/contents/$1?ref=main"; then
    pass "$2 on main"
  else
    failed "no $2 on main"
  fi
}

if exists "repos/$repo/branches/main"; then
  main=true
  pass "main exists"
else
  failed "no main branch"
fi
on_main LICENSE LICENSE

# With this media type GitHub returns the repository with the team's permissions;
# without access it is a 404.
if access=$(gh api -H "Accept: application/vnd.github.v3.repository+json" \
     "orgs/$owner/teams/$TEAM/repos/$repo" 2>/dev/null) &&
   [ "$(jq -r '.permissions.push // false' <<<"$access")" = true ]; then
  pass "team $TEAM has push access"
else
  failed "team $TEAM has no push access"
fi

if jq -e --arg r "$repo" '.repositories | keys | map(ascii_downcase) | index($r | ascii_downcase)' \
     "$list" >/dev/null 2>&1; then
  pass "in scripts/repos.json"
else
  failed "not in scripts/repos.json: add it by pull request, then run make protect"
fi

if envs=$(bash "$here/release-envs.sh" --check "$repo" --list "$list" 2>&1); then
  pass "release-please environment set up"
else
  failed "release-please environment not set up:"
  sed -n 's/^.*: wrong: /    /p' <<<"$envs"
fi

# A label's name goes in the path, so its space is encoded.
if exists "repos/$ISSUES_REPO/labels/${LABEL// /%20}"; then
  pass "$ISSUES_REPO has the label '$LABEL'"
else
  failed "$ISSUES_REPO has no label '$LABEL'"
fi

on_main .github/CODEOWNERS .github/CODEOWNERS

if [ "$failures" -gt 0 ]; then
  echo "$failures check(s) failed for $repo"
  exit 1
fi
echo "every check passed for $repo"
