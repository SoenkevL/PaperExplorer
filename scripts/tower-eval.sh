#!/usr/bin/env bash
#
# Runs the eval of asking the Seed on the tower (docs/design-asking.md, Eval), then offers to
# commit its summary so it can be read from the Mac.
#
# It assumes what tower-check and setup-tower set up: the Seed fetched, parsed, chunked and
# embedded with bge-m3 in the compose database, and Ollama serving bge-m3 and paperexplorer-chat.
#
# Every Question of evals/seed-questions.yaml is asked of the Seed twice, each in a fresh
# Conversation: as the reader asks it (retrieval: the forced search, then the model's tools) and
# with the whole Seed in context (the baseline, no tools). Each turn is a `question` trace in
# Langfuse's `eval` environment, tagged `run:<run id>`. 22 Questions in two modes take a while,
# and the tower has no tmux: start a full run detached, so a dropped SSH connection does not
# stop it (detached, it does not offer the commit).
#
# Writes:
#   data/eval/<run id>/            the full report: report.json and answers.md hold the Answers
#                                  and the Seed's words (its licence is unknown: never commit
#                                  them), summary.json and summary.md, console.log
#   evals/runs/<run id>.{json,md}  the summary, without the Seed's or the Answers' words: these
#                                  are offered for commit and push to the checked-out branch
#
# Run it on the tower, from the repo; never source it:
#   git fetch && git switch <branch> && git pull --ff-only && scripts/tower-eval.sh
#   setsid nohup scripts/tower-eval.sh < /dev/null > data/eval/run.out 2>&1 &   # detached
# Arguments go to `paperexplorer eval`, e.g. one Question as the reader asks it:
#   scripts/tower-eval.sh --only uncovered-heart-rate --no-baseline
# The last lines print the command that copies the full report to the Mac.

# Sourced, its `set -e` and `exit` would close the caller's shell, and an SSH session with it.
(return 0 2>/dev/null) && { echo "Run ${BASH_SOURCE[0]}, don't source it." >&2; return 1; }

set -euo pipefail

SEED="YAAC9XCX"  # the Seed's Zotero item
MODELS=(bge-m3 paperexplorer-chat)

cd "$(git rev-parse --show-toplevel)"
export PE_LANGFUSE_RELEASE="${PE_LANGFUSE_RELEASE:-$(git rev-parse --short HEAD)}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$PE_LANGFUSE_RELEASE"
RUN_DIR="data/eval/$RUN_ID"
mkdir -p "$RUN_DIR"

{
  echo "== eval $RUN_ID: $(git rev-parse --abbrev-ref HEAD) @ $PE_LANGFUSE_RELEASE, $(date -Iseconds)"
  uv sync --quiet
  docker compose up -d --quiet-pull --wait db
  uv run paperexplorer migrate
  for model in "${MODELS[@]}"; do
    ollama show "$model" >/dev/null 2>&1 \
      || { echo "Ollama has no $model: create it from the Mac with scripts/setup-tower.sh" >&2; exit 1; }
  done
  uv run paperexplorer eval "$SEED" --run-id "$RUN_ID" "$@"
} 2>&1 | tee "$RUN_DIR/console.log"

summary=("evals/runs/$RUN_ID.json" "evals/runs/$RUN_ID.md")
branch=$(git rev-parse --abbrev-ref HEAD)
if [[ -t 0 ]]; then
  read -rp "Commit and push ${summary[*]} to $branch? [y/N] " reply
  if [[ "$reply" =~ ^[Yy] ]]; then
    git add "${summary[@]}" && git commit -q -m "chore: eval run $RUN_ID" && git push -q \
      && echo "pushed"
  fi
fi

echo
echo "On the Mac, copy the full report into the main checkout (never commit it):"
echo "  mkdir -p ~/Projects/PaperExplorer/data/eval && rsync -a tower:${PWD#"$HOME"/}/$RUN_DIR ~/Projects/PaperExplorer/data/eval/"
