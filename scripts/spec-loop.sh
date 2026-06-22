#!/bin/bash
# spec-loop: loop Claude headless, implementing ONE tracer-bullet slice from the
# implementation plan per iteration, tracking progress inside the plan file itself.
#
# Usage: scripts/spec-loop.sh [plan-file] [iterations]
#   [plan-file]   the implementation plan to drive
#                 (default: specs/0.2/IMPLEMENTATION-PLAN.md)
#   [iterations]  max loop count (default 10). Each iteration = one slice.
#
# Branch: NONE. Wikify works directly on `main` — no feature branches (see CLAUDE.md →
# Git). The loop never creates or switches branches; it commits each slice on the current
# branch (expected: main). It does NOT push — push only when asked.
#
# Stops early when the model emits <promise>COMPLETE</promise> (all slices done per the
# plan's Slice map).
set -e

SPEC="${1:-specs/0.2/IMPLEMENTATION-PLAN.md}"
ITERATIONS="${2:-10}"

if [ ! -f "$SPEC" ]; then
  echo "Plan file not found: $SPEC"
  echo "Usage: $0 [plan-file] [iterations]"
  exit 1
fi

# Wikify works on `main`. Never create/switch branches; just warn if we're elsewhere.
branch=$(git branch --show-current)
if [ "$branch" != "main" ]; then
  echo "WARNING: not on 'main' (on '$branch'). Wikify convention is to work on main."
  echo "Continuing on '$branch' — the loop will NOT create or switch branches."
fi
echo "Working on branch: $branch  (driving: $SPEC)"

# jq filter to stream assistant text to the terminal as it arrives
stream_text='select(.type == "assistant").message.content[]? | select(.type == "text").text // empty | gsub("\n"; "\r\n") | . + "\r\n\n"'

# jq filter to extract the final result of an iteration
final_result='select(.type == "result").result // empty'

for ((i=1; i<=ITERATIONS; i++)); do
  echo "===== spec-loop iteration $i / $ITERATIONS — $SPEC ====="
  tmpfile=$(mktemp)
  trap "rm -f $tmpfile" EXIT

  recent_commits=$(git log --oneline -n 15 2>/dev/null || echo "none")

  claude --dangerously-skip-permissions \
    --verbose \
    --print \
    --output-format stream-json \
    "Plan to work on: $SPEC
Current branch (do NOT create or switch — Wikify works on main): $branch
Recent commits: $recent_commits
@scripts/prompt.md" \
  | grep --line-buffered '^{' \
  | tee "$tmpfile" \
  | jq --unbuffered -rj "$stream_text"

  result=$(jq -r "$final_result" "$tmpfile")

  if [[ "$result" == *"<promise>COMPLETE</promise>"* ]]; then
    echo "spec-loop complete after $i iterations — all tracer-bullet slices implemented."
    exit 0
  fi
done

echo "spec-loop stopped after $ITERATIONS iterations (not COMPLETE)."
