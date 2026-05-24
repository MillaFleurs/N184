#!/bin/bash
# export.sh — print, copy, or locate Honoré's pot still (the /sorrow distillate)
# so it can be read, shared, or backed up.
#
# The pot still lives at ./build/data/palace/potstill.md on the host
# (== ~/.n184/potstill.md inside the container). It's plain Markdown.
#
#   ./export.sh           # print the pot still to stdout (pipe/copy to share)
#   ./export.sh --file    # write a timestamped copy to the repo root, print its path
#   ./export.sh --to-git  # copy lessons to the tracked ./potstill.md and git-add it
#   ./export.sh --path    # just print the file path
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POT="$ROOT/build/data/palace/potstill.md"
LIFECYCLE="$ROOT/build/data/palace/lifecycle.json"

if [ ! -f "$POT" ]; then
  echo "No pot still yet — Honoré hasn't run /sorrow. (Expected at $POT)" >&2
  exit 1
fi

case "${1:-}" in
  --file|-f)
    OUT="$ROOT/potstill-$(date +%Y%m%d-%H%M%S).md"
    cp "$POT" "$OUT"
    echo "Pot still copied to: $OUT" >&2
    echo "$OUT"
    ;;
  --to-git|-g)
    # Copy the distilled lessons to a TRACKED file so they can be committed and
    # shared across deployments (the runtime pot still in ./build is git-ignored
    # and wiped on a reset; this is how lessons survive that).
    DEST="$ROOT/potstill.md"
    cp "$POT" "$DEST"
    ( cd "$ROOT" && git add potstill.md >/dev/null 2>&1 ) || true
    echo "Lessons copied to tracked ./potstill.md and staged. Commit to share:" >&2
    echo "    git commit -m 'Update pot still lessons'" >&2
    echo "$DEST"
    ;;
  --path|-p)
    echo "$POT"
    ;;
  *)
    cat "$POT"
    # Show the lifecycle ledger too, if present (lineage / last joy+sorrow).
    if [ -f "$LIFECYCLE" ]; then
      echo
      echo "--- lifecycle ledger ($LIFECYCLE) ---"
      cat "$LIFECYCLE"
    fi
    ;;
esac
