#!/usr/bin/env bash
# push site/fast.json; on a rejected push re-apply it onto the new main (push-snapshot.sh). Then, if
# main now pins other private code (SOURCE_REF), stop the scanner so a fresh run takes over.
# always exits 0.
set -u
WS="${1:-${GITHUB_WORKSPACE:-.}}"
SRC="$WS/src/memecoin-trading/site/fast.json"

# the late hourly cycle is started by paper-heartbeat.sh alone (every 10 minutes from scanner.yml):
# a second dispatcher here could start the same late cycle twice

publish() {
  cd "$WS/site" || { echo "no site checkout"; return 0; }
  if [ ! -f "$SRC" ]; then echo "no snapshot to publish"; return 0; fi
  cp "$SRC" fast.json
  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  git add fast.json
  if git diff --cached --quiet; then echo "fast.json unchanged"; return 0; fi
  git commit --quiet -m "Minute books $(date -u +%Y-%m-%dT%H:%MZ)"
  bash scripts/push-snapshot.sh 3 || echo "snapshot not published this time"
}

# SOURCE_REF changed on main: this run would keep trading the minute books under the old rules for
# hours. fast.py has saved its state and waits for this script, so stopping it here loses nothing;
# scanner.yml then saves the cache and dispatches a run on main's code (FAST_PID_FILE is set there).
handover() {
  [ -n "${SOURCE_REF:-}" ] && [ -n "${FAST_PID_FILE:-}" ] && [ -s "$FAST_PID_FILE" ] || return 0
  git -C "$WS/site" fetch --quiet origin main 2>/dev/null || return 0
  local main_ref
  main_ref=$(git -C "$WS/site" show origin/main:.github/workflows/scanner.yml 2>/dev/null \
    | sed -nE 's/^ *SOURCE_REF: *([0-9a-f]{40}).*/\1/p' | head -1)
  if [ -z "$main_ref" ] || [ "$main_ref" = "$SOURCE_REF" ]; then return 0; fi
  echo "handover: main pins private code ${main_ref:0:8}, this run ${SOURCE_REF:0:8}"
  touch "${FAST_PID_FILE%.pid}.handover"
  kill -TERM "$(cat "$FAST_PID_FILE")" 2>/dev/null || true
}

publish
handover
exit 0
