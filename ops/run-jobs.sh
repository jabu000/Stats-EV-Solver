#!/usr/bin/env bash
# Scheduled work for Stats EV Solver: record the current slate, or grade yesterday's.
#
# Usage: run-jobs.sh snapshot|grade|both
#
# Invoked by launchd (macOS) or a systemd timer (Linux). Safe to run by hand, and safe
# to run repeatedly -- snapshots are idempotent and grading skips settled picks.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python"
ACTION="${1:-both}"

cd "$REPO_ROOT" || exit 1

if [ ! -x "$PY" ]; then
  echo "No virtualenv at $PY -- run 'make setup' first." >&2
  exit 1
fi

export PYTHONPATH="$REPO_ROOT/backend"
mkdir -p "$REPO_ROOT/data/logs"

status=0
case "$ACTION" in
  snapshot) "$PY" -m app.cli snapshot || status=$? ;;
  grade)    "$PY" -m app.cli grade    || status=$? ;;
  both)
    "$PY" -m app.cli snapshot || status=$?
    "$PY" -m app.cli grade    || status=$?
    ;;
  *)
    echo "Usage: run-jobs.sh snapshot|grade|both" >&2
    exit 2
    ;;
esac

exit $status
