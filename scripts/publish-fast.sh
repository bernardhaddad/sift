#!/usr/bin/env bash
# Publish the minute books' snapshot (site/fast.json, written by scripts/fast.py in the
# private repository) to this site. The scanner calls this every 15 minutes and at the end
# of a run. A rejected push (the hourly paper job publishes to the same branch) is rebased
# onto origin/main and retried; the snapshot is regenerated whole, so "theirs" is always
# the right side of a conflict. Never fails the caller.
set -u
WS="${1:-${GITHUB_WORKSPACE:-.}}"
SRC="$WS/src/memecoin-trading/site/fast.json"
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
