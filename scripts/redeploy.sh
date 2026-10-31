#!/usr/bin/env bash
#
# Redeploys the stack from this checkout's commit: db up, pending migrations applied, then the
# api and web images rebuilt and restarted, waiting until both are healthy. Appends everything
# to data/redeploy.log.
#
# On the tower the post-merge hook runs it after a pull moves `dev` in the deploy checkout (see
# scripts/setup-deploy.sh). Run it by hand to redeploy without a pull; never source it.

# Sourced, its `set` and `exit` would act on the caller's shell, and could close an SSH session.
(return 0 2>/dev/null) && { echo "Run ${BASH_SOURCE[0]}, don't source it." >&2; return 1; }

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# A non-interactive SSH session lacks ~/.local/bin, where docker compose plugins may live.
export PATH="$HOME/.local/bin:$PATH"

mkdir -p data
LOG="data/redeploy.log"
# The containers have no git: the release on their traces is the commit they were built from.
PE_LANGFUSE_RELEASE=$(git rev-parse --short HEAD)
export PE_LANGFUSE_RELEASE

stamp() { date '+%Y-%m-%dT%H:%M:%S%z'; }

{
  echo "── $(stamp) redeploying $PE_LANGFUSE_RELEASE ($(git log -1 --format=%s))"
  docker compose up -d --quiet-pull --wait db
  docker compose run --rm --build pipeline migrate
  docker compose up -d --build --wait api web
  echo "── $(stamp) deployed $PE_LANGFUSE_RELEASE"
} 2>&1 | tee -a "$LOG"
