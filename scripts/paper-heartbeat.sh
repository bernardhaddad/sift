#!/usr/bin/env bash
# start the hourly cycle when GitHub's schedule has skipped it: no paper run queued or running,
# and the newest one started over 62 minutes ago. Needs GH_TOKEN with actions: write.
# The only paper dispatcher in the scanner job (publish-fast.sh no longer has one).
set -u
api="https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/workflows/paper.yml"
# -m: a hung request must not stop the heartbeat loop for the rest of the scanner run
runs=$(curl -sS -m 20 -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" "$api/runs?per_page=5") || exit 0
late=$(printf '%s' "$runs" | python3 -c '
import json, sys
from datetime import UTC, datetime
runs = json.load(sys.stdin).get("workflow_runs") or []
now = datetime.now(UTC)
def age(r):
    return (now - datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))).total_seconds()
# a run in progress, or waiting for under 30 minutes, is the cycle; one left queued longer is stuck
# (the same rule as the scanner watchdog in paper.yml)
if not runs or any(r.get("status") == "in_progress" or (r.get("status") != "completed" and age(r) < 30 * 60)
                   for r in runs):
    print("no"); sys.exit()
print("yes" if min(age(r) for r in runs) > 62 * 60 else "no")' 2>/dev/null) || exit 0
if [ "$late" = "yes" ]; then
  curl -sS -m 20 -X POST -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "$api/dispatches" -d '{"ref":"main"}' -o /dev/null -w "[heartbeat] hourly cycle was late, started it: http %{http_code}\n" || true
fi
