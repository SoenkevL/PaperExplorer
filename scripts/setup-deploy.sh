#!/usr/bin/env bash
#
# Sets up the tower to redeploy whenever a pull brings new commits to `dev`:
#   1. a deploy checkout, a git worktree of this clone with `dev` checked out (default: this
#      checkout's path plus `-dev`), so branch work in this checkout never moves what is deployed;
#   2. the deploy checkout's .env and data/ as links to this checkout's, so both share one set
#      of secrets, Zotero storage and pipeline output;
#   3. scripts/hooks/post-merge as the clone's post-merge hook, which runs scripts/redeploy.sh
#      after a pull that changes `dev`.
#
# Safe to run again: each step skips what is already in place. It deploys nothing itself.
#
# Run it on the tower, from the main checkout; never source it:
#   scripts/setup-deploy.sh [deploy-dir]
# Then, to deploy what was merged into `dev`:
#   git -C <deploy-dir> pull --ff-only

# Sourced, its `set` and `exit` would act on the caller's shell, and could close an SSH session.
(return 0 2>/dev/null) && { echo "Run ${BASH_SOURCE[0]}, don't source it." >&2; return 1; }

set -euo pipefail

MAIN=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
cd "$MAIN"
DEPLOY="${1:-$MAIN-dev}"
DEPLOY="$(cd "$(dirname "$DEPLOY")" && pwd -P)/$(basename "$DEPLOY")"
HOOK="$(git rev-parse --git-path hooks)/post-merge"

ok()   { printf '  ✓ %s\n' "$1"; }
fail() { printf '  ✗ %s\n' "$1" >&2; exit 1; }

[[ "$(git rev-parse --git-dir)" == "$(git rev-parse --git-common-dir)" ]] \
  || fail "run it from the main checkout, not from a worktree"
[[ -f .env ]] || fail "no .env in $MAIN: scripts/setup-tower.sh writes it"

git fetch -q origin dev

# Where `dev` is checked out now, if anywhere.
dev_at=$(git worktree list --porcelain \
  | awk '/^worktree /{wt=substr($0,10)} $0=="branch refs/heads/dev"{print wt}')
if [[ -z "$dev_at" ]]; then
  git worktree add -q "$DEPLOY" dev
  ok "deploy checkout at $DEPLOY, on dev"
elif [[ "$dev_at" == "$DEPLOY" ]]; then
  ok "deploy checkout already at $DEPLOY"
elif [[ "$dev_at" == "$MAIN" ]]; then
  fail "this checkout is on dev; switch it to another branch first, so dev can live in $DEPLOY"
else
  fail "dev is checked out at $dev_at; pass that path, or remove that worktree"
fi

# A pull that rebases instead of merging skips the post-merge hook.
git config branch.dev.rebase false

ln -sfn "$MAIN/.env" "$DEPLOY/.env"
mkdir -p "$MAIN/data"
if [[ -L "$DEPLOY/data" || ! -e "$DEPLOY/data" ]]; then
  ln -sfn "$MAIN/data" "$DEPLOY/data"
else
  fail "$DEPLOY/data is a real directory; move what it holds into $MAIN/data and delete it"
fi
ok ".env and data/ linked to $MAIN"

if [[ -e "$HOOK" ]] && ! grep -q "scripts/setup-deploy.sh" "$HOOK"; then
  fail "$HOOK exists and is not ours; merge it with scripts/hooks/post-merge by hand"
fi
mkdir -p "$(dirname "$HOOK")"
install -m 755 scripts/hooks/post-merge "$HOOK"
ok "post-merge hook installed at $HOOK"

echo
echo "Deploy what is on dev now:  $DEPLOY/scripts/redeploy.sh"
echo "Later merges:               git -C $DEPLOY pull --ff-only"
