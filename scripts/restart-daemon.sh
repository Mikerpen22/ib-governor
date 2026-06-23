#!/usr/bin/env bash
#
# restart-daemon.sh — bounce the ib-governor circuit-breaker daemon (launchd).
#
# The daemon runs as a launchd agent (com.ib-governor.daemon) on your Mac. After
# pulling new code OR editing config/rules.yaml you must restart it for the
# change to take effect. This wraps "pull + restart" into one step and reports
# the safety mode (SAFE vs ARMED) the daemon came back in.
#
# Usage:
#   scripts/restart-daemon.sh            # pull (if clean) + restart
#   scripts/restart-daemon.sh --no-pull  # just restart (e.g. after a config edit)
#   scripts/restart-daemon.sh --logs     # restart, then follow the daemon log
#   make restart-daemon                  # same as the default
#
# It NEVER arms the brake — it only restarts. config/rules.yaml is untouched, so
# the daemon comes back in whatever mode that file already declares (ships SAFE:
# dry_run: true + readonly: true). The banner tells you which.
set -euo pipefail

LABEL="com.ib-governor.daemon"
GOVERNOR_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG="$GOVERNOR_HOME/logs/governor.err.log"

usage() { sed -n '3,18p' "${BASH_SOURCE[0]}" | sed 's/^#\{0,1\} \{0,1\}//'; }

PULL=1
TAIL=0
for arg in "$@"; do
  case "$arg" in
    --no-pull)        PULL=0 ;;
    --logs|--follow)  TAIL=1 ;;
    -h|--help)        usage; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

cd "$GOVERNOR_HOME"

# launchctl is macOS-only; bail clearly elsewhere with the manual invocation.
if ! command -v launchctl >/dev/null 2>&1; then
  echo "✗ launchctl not found — the daemon runs under launchd on macOS." >&2
  echo "  Run it directly instead:  .venv/bin/python -m governor.live.daemon" >&2
  exit 1
fi

# 1. Optionally fast-forward the current branch. Only when the tree is clean, so
#    we never clobber local edits; a non-ff / no-upstream pull is non-fatal —
#    we still restart on the current code.
if [ "$PULL" -eq 1 ]; then
  if [ -n "$(git status --porcelain)" ]; then
    echo "• local changes present — skipping pull, restarting on current code."
  else
    echo "• pulling latest (fast-forward only)…"
    git pull --ff-only || echo "• pull skipped (no upstream / not fast-forward) — restarting on current code."
  fi
fi

# 2. Restart. kickstart -k bounces an already-loaded service; if it isn't loaded
#    yet, load the plist (it must have been installed per HANDBOOK §5).
if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
  echo "• restarting ${LABEL}…"
  launchctl kickstart -k "gui/$(id -u)/${LABEL}"
elif [ -f "$PLIST" ]; then
  echo "• ${LABEL} not loaded — loading ${PLIST}…"
  launchctl load "$PLIST"
else
  echo "✗ ${LABEL} is not loaded and ${PLIST} is missing." >&2
  echo "  Install the launchd agent first — see docs/HANDBOOK.md §5 (Keep it running)." >&2
  exit 1
fi

# 3. Report the safety mode it came back in, read straight from config. Both
#    locks shipping ON (or either ON) means no orders can be placed.
dry="$(grep -E '^[[:space:]]*dry_run:'  config/rules.yaml | grep -oE 'true|false' | head -1 || true)"
ro="$( grep -E '^[[:space:]]*readonly:' config/rules.yaml | grep -oE 'true|false' | head -1 || true)"
if [ "$dry" != "false" ] || [ "$ro" != "false" ]; then
  echo "✓ restart issued. 🔒 SAFE — dry_run=${dry:-?} readonly=${ro:-?} (no orders will be placed)."
else
  echo "✓ restart issued. ⚠️ ARMED — dry_run=false AND readonly=false: the daemon CAN act on confirmed orders."
fi
echo "  logs: $LOG"

# 4. Optionally follow the log so you watch it come up ('brake daemon up: …').
if [ "$TAIL" -eq 1 ]; then
  echo "• tailing $LOG (Ctrl-C to stop)…"
  exec tail -n 20 -f "$LOG"
fi
