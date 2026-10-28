#!/usr/bin/env bash
#
# Checks the checked-out branch on the tower and writes everything to tower-check.log, which is
# committed back to the branch so it can be read from the Mac (Claude Code cannot reach the tower).
#
# Steps:
#   1. the gate (ruff, pyright, pytest);
#   2. GROBID from compose.yaml;
#   3. the tower-only tests (Ollama's bge-m3 among them);
#   4. the Seed through `paperexplorer parse`;
#   5. the pipeline image with a live GROBID and OCR of the scan fixture;
#   6. the Seed's Stages (fetch, parse, chunk, embed, extract_citations) on the host and in the
#      pipeline image, each with its time; chunk logs its counts of Chunks, short and
#      oversized_sentence, embed how many Chunks it embedded with Ollama's bge-m3 (traced to
#      Langfuse as stage.embed), and embed run again on the host embeds none;
#   7. five searches of the Seed (`paperexplorer search --no-text`): about its measurements, its
#      methods, its shortcomings, what it proposes next, and one topic it does not cover; each
#      lists the 8 best Chunks' RRF, vector and full-text ranks and scores, page and Heading Path,
#      never their text (each is traced to Langfuse as `search`);
#   8. three Questions asked of the Seed with the real chat model (`paperexplorer ask`): its
#      measurements, its shortcomings, and a topic it does not cover. The full output goes to
#      data/asking/; this log gets only each Answer's counts (sentences, Pointer coverage,
#      Support levels, exact and not-exact Quotes, unknown handles), the tools it ran, its model
#      requests and latency, and whether it says the Seed does not cover the Question, never its
#      text (each is traced to Langfuse as `question`);
#   9. the API and web containers, and the reader: every Citation of the Seed is a link that
#      opens a panel with its Reference;
#  10. a Conversation on the running API (`scripts/check_conversation.py`): one Question about the
#      Seed over HTTP, logging its events counted by type, the tools run, the time to the first
#      delta and to the Answer, and the Answer's counts, never its text (traced as `question`,
#      with this commit as its release).
#
# Every step runs even when an earlier one fails. Containers have no git, so the release on
# their traces is this commit, passed as PE_LANGFUSE_RELEASE.
#
# Run on the tower, from the repo:
#   git fetch && git switch <branch> && git pull --ff-only && scripts/tower-check.sh

set -uo pipefail

LOG="tower-check.log"
SEED_ATTACHMENT="YTS2XCEG"  # the Seed's PDF in Zotero storage
SEED="YAAC9XCX"             # the Seed's Zotero item
STAGES=(fetch parse chunk embed extract_citations)
# What the searches of the Seed ask: its measurements, methods, shortcomings, what it proposes
# next, and a topic it does not cover, whose scores should stay low.
SEARCHES=(
  "How was brain entropy measured?"
  "Which methods and analyses did the authors use?"
  "What are the limitations and shortcomings of this work?"
  "What future research do the authors propose?"
  "How does caffeine affect sleep in shift workers?"
)
# What the answering agent is asked of the Seed: its measurements, its shortcomings, and a
# topic it does not cover, whose Answer should say so.
QUESTIONS=(
  "How was brain entropy measured, and with which tools?"
  "What are the shortcomings of this work?"
  "How does caffeine affect sleep in shift workers?"
)
ASKED="data/asking"
RESULTS=()

# step NAME CMD...: runs CMD, logs its output, status and time, and carries on either way.
# With QUIET=1 the output is kept only when CMD fails (last 40 lines).
step() {
  local name="$1" start=$SECONDS status=0 out
  shift
  printf '\n=== %s\n$ %s\n' "$name" "$*"
  if [[ -n "${QUIET:-}" ]]; then
    out=$(mktemp)
    "$@" >"$out" 2>&1 || { status=$?; tail -n 40 "$out"; }
    rm -f "$out"
  else
    "$@" || status=$?
  fi
  local verdict="ok"
  [[ $status -eq 0 ]] || verdict="FAILED (exit $status)"
  RESULTS+=("$(printf '%-26s %-18s %4ss' "$name" "$verdict" $((SECONDS - start)))")
}

grobid_alive() {
  curl -fsS --retry 60 --retry-delay 3 --retry-all-errors localhost:8070/api/isalive && echo
  curl -fsS localhost:8070/api/version && echo
}

# reader_check: the running reader links every Citation of the Seed (scripts/check_reader.py);
# on failure the api and web containers' last log lines say why.
reader_check() {
  uv run python scripts/check_reader.py "$SEED" && return 0
  echo "--- api and web logs"
  docker compose logs --no-color --tail 40 api web
  return 1
}

# seed_searches: each of SEARCHES on the Seed. The Seed's licence is unknown and this log is
# public: ranks, scores, pages and Heading Paths only, no Chunk text.
seed_searches() {
  local query status=0
  for query in "${SEARCHES[@]}"; do
    uv run paperexplorer search --no-text "$SEED" "$query" || status=$?
    echo
  done
  return "$status"
}

# seed_questions: each of QUESTIONS asked of the Seed with the real chat model. The Seed's
# licence is unknown and this log is public: the full output goes to $ASKED/question-<n>.txt,
# and the log gets each Answer's summary line, the tools it ran (not their arguments, which may
# hold the Seed's words) and whether its Answer says the Seed does not cover the Question.
seed_questions() {
  local i=0 question out status=0 uncovered
  mkdir -p "$ASKED"
  for question in "${QUESTIONS[@]}"; do
    i=$((i + 1))
    out="$ASKED/question-$i.txt"
    printf 'question %d: %s\n' "$i" "$question"
    if uv run paperexplorer ask "$SEED" "$question" >"$out" 2>&1; then
      grep '^summary: ' "$out" || { echo "no summary line"; status=1; }
      uncovered=no
      sed -n '/^Answer$/,/^Pointers/p' "$out" \
        | grep -qiE "does not (say|cover|address|discuss|mention|report)|doesn't|not covered" \
        && uncovered=yes
      echo "says the Seed does not cover it: $uncovered"
    else
      status=1
      echo "ask failed (see $out on the tower)"
    fi
    echo "tools: $(grep '^→ ' "$out" | awk '{print $2}' | paste -sd ' ' -)"
    echo "full output: $out"
    echo
  done
  return "$status"
}

# conversation_check: one Question asked of the Seed over the running API's HTTP stream; on
# failure the api container's last log lines say why.
conversation_check() {
  uv run python scripts/check_conversation.py "$SEED" "${QUESTIONS[0]}" && return 0
  echo "--- api logs"
  docker compose logs --no-color --tail 40 api
  return 1
}

context() {
  echo "date:    $(date -Iseconds)"
  echo "host:    $(hostname) · $(uname -srm)"
  echo "branch:  $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
  echo "release: $PE_LANGFUSE_RELEASE (on every trace, the containers' too)"
  echo "dirty:   $(git status --porcelain | grep -cv " $LOG\$") files"
  echo "uv:      $(uv --version)"
  echo "docker:  $(docker version --format '{{.Server.Version}}' 2>&1)"
  echo "cpus:    $(nproc 2>/dev/null || sysctl -n hw.ncpu)"
}

main() {
  # compose.yaml passes it into the api and pipeline containers, which have no git.
  export PE_LANGFUSE_RELEASE="${PE_LANGFUSE_RELEASE:-$(git rev-parse --short HEAD)}"
  context

  QUIET=1 step "uv sync" uv sync
  step "ruff check" uv run ruff check
  step "ruff format --check" uv run ruff format --check
  step "pyright" uv run pyright
  # Includes the live GROBID check: testcontainers starts its own GROBID and Postgres.
  step "pytest" uv run pytest -q -rs

  QUIET=1 step "GROBID up (compose)" docker compose up -d --quiet-pull grobid
  step "GROBID alive" grobid_alive
  step "pytest -m tower" uv run pytest -m tower -v -rs

  local storage seed
  storage=$(sed -n 's/^PE_ZOTERO_STORAGE_DIR=//p' .env 2>/dev/null | tail -n 1)
  seed=$(find "${storage:-/nonexistent}/$SEED_ATTACHMENT" -name '*.pdf' 2>/dev/null | head -n 1)
  if [[ -z "$seed" ]]; then
    printf '\n=== parse the Seed\nno PDF in %s/%s (PE_ZOTERO_STORAGE_DIR in .env)\n' \
      "${storage:-<unset>}" "$SEED_ATTACHMENT"
    RESULTS+=("parse the Seed            SKIPPED: no Seed PDF")
  else
    # The Seed's licence is unknown and this log is public: counts only, no quoted sentences.
    step "parse the Seed" uv run paperexplorer parse --show 0 "$seed"
  fi

  QUIET=1 step "build pipeline image" docker compose build pipeline
  # A fresh data dir in the container: no cached GROBID output, so it calls grobid:8070.
  local run=(docker compose run --rm --no-deps -T -e PE_DATA_DIR=/tmp/pe)
  if [[ -n "$seed" ]]; then
    step "pipeline: the Seed" "${run[@]}" pipeline parse --show 0 \
      "/zotero/storage/$SEED_ATTACHMENT/$(basename "$seed")"
  fi
  step "pipeline: scan via OCR" "${run[@]}" -v "$PWD/tests/fixtures/pdfs:/fixtures:ro" \
    pipeline parse /fixtures/scan-villiger2024.pdf

  QUIET=1 step "db up (compose)" docker compose up -d --wait db
  step "migrate" uv run paperexplorer migrate
  local stage
  for stage in "${STAGES[@]}"; do
    step "run $stage (host)" uv run paperexplorer run "$stage" "$SEED"
  done
  # Every Chunk has its Vector now, so a second run embeds none (and makes extract_citations
  # pending, which the pipeline image runs again below).
  step "run embed again (host)" uv run paperexplorer run embed "$SEED"
  step "search the Seed (host)" seed_searches
  step "ask the Seed (host)" seed_questions
  for stage in "${STAGES[@]}"; do
    step "pipeline: run $stage" "${run[@]}" pipeline run "$stage" "$SEED"
  done
  QUIET=1 step "api + web up (compose)" docker compose up -d --build --wait api web
  step "reader: the Seed" reader_check
  step "conversation (api)" conversation_check

  printf '\n=== summary\n'
  printf '%s\n' "${RESULTS[@]}"
}

cd "$(git rev-parse --show-toplevel)" || exit 1
main 2>&1 | tee "$LOG"

branch=$(git rev-parse --abbrev-ref HEAD)
if [[ -t 0 ]]; then
  read -rp "Commit and push $LOG to $branch? [y/N] " reply
  if [[ "$reply" =~ ^[Yy] ]]; then
    git add "$LOG" && git commit -q -m "chore: tower check log" && git push -q && echo "pushed"
  fi
fi
