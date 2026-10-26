#!/usr/bin/env bash
# start the hourly cycle when GitHub's schedule has skipped it: no paper run queued or running,
# and the newest one started over 62 minutes ago. Needs GH_TOKEN with actions: write.
set -u
api="https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/workflows/paper.yml"
runs=$(curl -sS -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" "$api/runs?per_page=5") || exit 0
late=$(printf '%s' "$runs" | python3 -c '
import json, sys
from datetime import UTC, datetime
runs = json.load(sys.stdin).get("workflow_runs") or []
if not runs or any(r.get("status") != "completed" for r in runs):
    print("no"); sys.exit()
newest = max(datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) for r in runs)
print("yes" if (datetime.now(UTC) - newest).total_seconds() > 62 * 60 else "no")' 2>/dev/null) || exit 0
if [ "$late" = "yes" ]; then
  curl -sS -X POST -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "$api/dispatches" -d '{"ref":"main"}' -o /dev/null -w "[heartbeat] hourly cycle was late, started it: http %{http_code}\n"
fi
