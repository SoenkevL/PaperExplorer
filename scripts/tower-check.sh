#!/usr/bin/env bash
#
# Checks the checked-out branch on the tower and writes everything to tower-check.log, which is
# committed back to the branch so it can be read from the Mac (Claude Code cannot reach the tower).
#
# Steps:
#   1. the gate (ruff, pyright, pytest);
#   2. GROBID from compose.yaml;
#   3. the tower-only tests;
#   4. the Seed through `paperexplorer parse`;
#   5. the pipeline image with a live GROBID and OCR of the scan fixture;
#   6. the Seed's Stages (fetch, parse, chunk, extract_citations) on the host and in the pipeline
#      image; chunk logs its counts of Chunks, short and oversized_sentence;
#   7. the API and web containers, and the reader: every Citation of the Seed is a link that
#      opens a panel with its Reference.
#
# Every step runs even when an earlier one fails.
#
# Run on the tower, from the repo:
#   git fetch && git switch <branch> && git pull --ff-only && scripts/tower-check.sh

set -uo pipefail

LOG="tower-check.log"
SEED_ATTACHMENT="YTS2XCEG"  # the Seed's PDF in Zotero storage
SEED="YAAC9XCX"             # the Seed's Zotero item
STAGES=(fetch parse chunk extract_citations)
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

context() {
  echo "date:    $(date -Iseconds)"
  echo "host:    $(hostname) · $(uname -srm)"
  echo "branch:  $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
  echo "dirty:   $(git status --porcelain | grep -cv " $LOG\$") files"
  echo "uv:      $(uv --version)"
  echo "docker:  $(docker version --format '{{.Server.Version}}' 2>&1)"
  echo "cpus:    $(nproc 2>/dev/null || sysctl -n hw.ncpu)"
}

main() {
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
  for stage in "${STAGES[@]}"; do
    step "pipeline: run $stage" "${run[@]}" pipeline run "$stage" "$SEED"
  done
  QUIET=1 step "api + web up (compose)" docker compose up -d --build --wait api web
  step "reader: the Seed" reader_check

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
