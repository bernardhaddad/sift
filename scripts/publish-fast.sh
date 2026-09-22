#!/usr/bin/env bash
# Publish the minute books' snapshot (site/fast.json, written by scripts/fast.py in the
# private repository) to this site. The scanner calls this every 15 minutes and at the end
# of a run. A rejected push (the hourly paper job publishes to the same branch) is rebased
# onto origin/main and retried; the snapshot is regenerated whole, so "theirs" is always
# the right side of a conflict. Never fails the caller.
set -u
WS="${1:-${GITHUB_WORKSPACE:-.}}"
SRC="$WS/src/memecoin-trading/site/fast.json"

# Watchdog for the hourly paper cycle. GitHub's cron skips most of paper.yml's slots;
# the scanner is the one job that is always running, so every time it publishes it
# checks when the last paper cycle started and dispatches one if that was over 65
# minutes ago and nothing is queued. paper.yml's concurrency group makes a duplicate
# dispatch harmless. Needs GH_TOKEN (the job's GITHUB_TOKEN) and actions: write.
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
if any(r["status"] in ("queued", "in_progress", "waiting", "pending") for r in runs):
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
git config user.name "sift-bot"
git config user.email "sift-bot@users.noreply.github.com"
git add fast.json
if git diff --cached --quiet; then echo "fast.json unchanged"; exit 0; fi
git commit --quiet -m "Minute books $(date -u +%Y-%m-%dT%H:%MZ)"
for attempt in 1 2 3; do
  if git push --quiet origin HEAD:main; then echo "published"; exit 0; fi
  echo "push rejected (attempt $attempt); rebasing onto origin/main"
  git fetch --quiet origin main
  if ! git rebase --quiet -X theirs origin/main; then
    git rebase --abort || true
    git reset --quiet --hard origin/main
    echo "rebase failed; snapshot not published this time"
    exit 0
  fi
  sleep 5
done
echo "not published after 3 attempts"
exit 0
