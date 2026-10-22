#!/usr/bin/env bash
# Apply the org's standard protection to one repository. The org is on GitHub
# Free, where rulesets can only be set per repository, so this script does by hand
# what an organisation ruleset would do on GitHub Team. Safe to run again: it
# updates the rulesets it made before instead of adding new ones.
#
#   scripts/protect-repo.sh those-who-run/<repo> [--checks "test image"] [--approvals N] [--code-owners] [--no-actions]
#
# --checks   status checks `main` must pass (from GitHub Actions only). Leave it
#            out for a repo with no CI; `main` still needs a pull request.
# --approvals   approving reviews a pull request needs before it merges (default
#               0). Use 1 where an agent's bot account opens PRs, so they wait
#               for a person; admins can still merge their own PRs through the
#               bypass. A push after the approval needs a new one. Leave it at 0
#               where Dependabot or release-please PRs merge without a person.
# --code-owners require a code owner's approval (.github/CODEOWNERS, read from the
#               base branch) on top of --approvals (default off). Use it where
#               "Allow GitHub Actions to create and approve pull requests" is to be
#               turned on, for release-please: an approval by github-actions[bot] or
#               an agent's bot account then counts for nothing, since neither is a
#               code owner. This script never turns that setting on, and turns it off
#               each time it runs; the maintainer turns it on by hand afterwards.
# --no-actions  turn Actions off (repos with no workflows, like outings).
#
# Needs: gh, logged in as an admin of the repository.
set -euo pipefail

repo=${1:?usage: protect-repo.sh <owner/repo> [--checks "a b"] [--approvals N] [--code-owners] [--no-actions]}
shift
checks=""
approvals=0
code_owners=false
actions=on
while [ $# -gt 0 ]; do
  case "$1" in
    --checks) checks=$2; shift 2 ;;
    --approvals) approvals=$2; shift 2 ;;
    --code-owners) code_owners=true; shift ;;
    --no-actions) actions=off; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

case "$approvals" in
  ''|*[!0-9]*) echo "--approvals takes a number: $approvals" >&2; exit 2 ;;
esac
# With reviews required, an approval is dropped by a later push and must come after it.
fresh_review=$([ "$approvals" -gt 0 ] && echo true || echo false)

say() { printf '%s: %s\n' "$repo" "$*"; }

# Create or update the ruleset called $1 from the JSON on stdin.
ruleset() {
  local name=$1 body id
  body=$(cat)
  id=$(gh api "repos/$repo/rulesets" --jq ".[] | select(.name == \"$name\") | .id" | head -n1)
  if [ -n "$id" ]; then
    gh api -X PUT "repos/$repo/rulesets/$id" --input - <<<"$body" >/dev/null
    say "ruleset '$name' updated"
  else
    gh api -X POST "repos/$repo/rulesets" --input - <<<"$body" >/dev/null
    say "ruleset '$name' created"
  fi
}

# 15368 is the GitHub Actions app: only its runs can satisfy a required check.
check_json=$(for c in $checks; do printf '{"context":"%s","integration_id":15368},' "$c"; done)
check_json="[${check_json%,}]"
status_rule=""
if [ "$check_json" != "[]" ]; then
  status_rule=",{\"type\":\"required_status_checks\",\"parameters\":{\"strict_required_status_checks_policy\":false,\"do_not_enforce_on_create\":false,\"required_status_checks\":$check_json}}"
fi

# main: changes arrive by squashed pull request; no force-push, no deletion.
# Admins may bypass only through a pull request (release-please's PRs get no CI),
# which also lets them merge their own PRs when --approvals asks for reviews.
ruleset main <<EOF2
{"name":"main","target":"branch","enforcement":"active",
 "conditions":{"ref_name":{"include":["~DEFAULT_BRANCH"],"exclude":[]}},
 "bypass_actors":[{"actor_id":5,"actor_type":"RepositoryRole","bypass_mode":"pull_request"}],
 "rules":[{"type":"deletion"},{"type":"non_fast_forward"},{"type":"required_linear_history"},
  {"type":"pull_request","parameters":{"required_approving_review_count":$approvals,"dismiss_stale_reviews_on_push":$fresh_review,"require_code_owner_review":$code_owners,"require_last_push_approval":$fresh_review,"required_review_thread_resolution":false,"allowed_merge_methods":["squash"]}}
  $status_rule]}
EOF2

# Release tags never move or disappear.
ruleset "release tags" <<'EOF2'
{"name":"release tags","target":"tag","enforcement":"active",
 "conditions":{"ref_name":{"include":["refs/tags/v*"],"exclude":[]}},
 "bypass_actors":[],"rules":[{"type":"deletion"},{"type":"update"},{"type":"non_fast_forward"}]}
EOF2

# Merging: squash only, the PR title as the commit, branches deleted, auto-merge allowed.
gh api -X PATCH "repos/$repo" -F allow_squash_merge=true -F allow_merge_commit=false \
  -F allow_rebase_merge=false -f squash_merge_commit_title=PR_TITLE \
  -F delete_branch_on_merge=true -F allow_auto_merge=true >/dev/null
say "merge settings: squash only, auto-merge allowed"

# Security: secret scanning with push protection, Dependabot alerts, private reports.
gh api -X PATCH "repos/$repo" \
  -f 'security_and_analysis[secret_scanning][status]=enabled' \
  -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled' >/dev/null
gh api -X PUT "repos/$repo/vulnerability-alerts" >/dev/null
gh api -X PUT "repos/$repo/private-vulnerability-reporting" >/dev/null
say "secret scanning, push protection, Dependabot alerts, private vulnerability reporting on"

# Actions: GitHub's actions plus the ones we use, pinned by SHA; a read-only token
# that can't approve PRs; outside contributors' PRs wait for approval.
if [ "$actions" = off ]; then
  gh api -X PUT "repos/$repo/actions/permissions" -F enabled=false >/dev/null
  say "Actions off"
else
  gh api -X PUT "repos/$repo/actions/permissions" -F enabled=true -f allowed_actions=selected -F sha_pinning_required=true >/dev/null
  gh api -X PUT "repos/$repo/actions/permissions/selected-actions" --input - >/dev/null <<'EOF2'
{"github_owned_allowed":true,"verified_allowed":false,"patterns_allowed":["astral-sh/setup-uv@*","docker/*","dependabot/fetch-metadata@*","googleapis/release-please-action@*"]}
EOF2
  gh api -X PUT "repos/$repo/actions/permissions/workflow" -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false >/dev/null
  if [ "$(gh api "repos/$repo" --jq .private)" = false ]; then
    gh api -X PUT "repos/$repo/actions/permissions/fork-pr-contributor-approval" -f approval_policy=all_external_contributors >/dev/null
  fi
  say "Actions: selected actions, SHA pinning, read-only token, fork PRs need approval"
fi
