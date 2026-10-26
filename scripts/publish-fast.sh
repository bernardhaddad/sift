#!/usr/bin/env bash
# push site/fast.json; on a rejected push re-apply it onto the new main (push-snapshot.sh). always exits 0.
set -u
WS="${1:-${GITHUB_WORKSPACE:-.}}"
SRC="$WS/src/memecoin-trading/site/fast.json"

# paper-cycle watchdog: dispatch paper.yml if its last run started over 65 min ago
paper_watchdog() {
  local repo="${GITHUB_REPOSITORY:-bernardhaddad/sift}"
  local runs
  runs=$(curl -sS -m 20 "https://api.github.com/repos/$repo/actions/workflows/paper.yml/runs?per_page=3") || return 0
  local verdict
  verdict=$(printf '%s' "$runs" | python3 -c '
import json, sys, datetime as dt
try:
    runs = json.load(sys.stdin)["workflow_runs"]
except Exception:
    print("skip"); raise SystemExit
now = dt.datetime.now(dt.timezone.utc)
def created(r):
    return dt.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
# a run queued for over 30 min is stuck, ignore it
if any(r["status"] == "in_progress" or (r["status"] in ("queued", "waiting", "pending")
                                        and (now - created(r)).total_seconds() < 30 * 60)
       for r in runs):
    print("running"); raise SystemExit
if not runs:
    print("dispatch"); raise SystemExit
newest = max(dt.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) for r in runs)
age = (now - newest).total_seconds() / 60
print("dispatch" if age > 65 else f"fresh {age:.0f}m")
' 2>/dev/null) || return 0
  if [ "$verdict" = "dispatch" ] && [ -n "${GH_TOKEN:-}" ]; then
    curl -sS -m 20 -X POST -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/$repo/actions/workflows/paper.yml/dispatches" -d '{"ref":"main"}' \
      -o /dev/null -w "paper watchdog: dispatched (http %{http_code})\n" || true
  else
    echo "paper watchdog: $verdict"
  fi
}
paper_watchdog

cd "$WS/site" || { echo "no site checkout"; exit 0; }
if [ ! -f "$SRC" ]; then echo "no snapshot to publish"; exit 0; fi
cp "$SRC" fast.json
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add fast.json
if git diff --cached --quiet; then echo "fast.json unchanged"; exit 0; fi
git commit --quiet -m "Minute books $(date -u +%Y-%m-%dT%H:%MZ)"
bash scripts/push-snapshot.sh 3 || echo "snapshot not published this time"
exit 0
