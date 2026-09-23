#!/usr/bin/env bash
# Archive one scanner run's minute panel into this repository so the mining passes can
# read weeks of it. Run artifacts expire after 14 days and the cached logs rotate at
# three days; a gzipped slice of this run's rows (panel, leaderboard scan, liquidity
# pools) is about 2 MB and lives in panel/ forever. Rows are sliced by the run's start
# time so consecutive runs never duplicate each other. Never fails the caller.
#   usage: archive-run.sh <workspace> <run-started-at ISO> <run id>
set -u
WS="${1:-${GITHUB_WORKSPACE:-.}}"
STARTED="${2:-}"
RUN_ID="${3:-0}"
SRC="$WS/src/memecoin-trading/data/fast"
DEST="$WS/site/panel"
[ -d "$SRC" ] || { echo "archive: no scanner data"; exit 0; }
mkdir -p "$DEST"
STAMP=$(date -u -d "${STARTED:-now}" +%Y-%m-%dT%H%M 2>/dev/null || date -u +%Y-%m-%dT%H%M)
python3 - "$SRC" "$DEST" "$STARTED" "$STAMP-$RUN_ID" <<'PY'
import gzip, json, os, sys
src, dest, started, stem = sys.argv[1:5]
for name, tag in (("panel.jsonl", "panel"), ("scan_log.jsonl", "scan"), ("lp_log.jsonl", "lp")):
    path = os.path.join(src, name)
    if not os.path.exists(path):
        continue
    kept = 0
    out = os.path.join(dest, f"{stem}.{tag}.jsonl.gz")
    with open(path) as fh, gzip.open(out, "wt") as gz:
        for line in fh:
            try:
                if not started or json.loads(line)["at"] >= started:
                    gz.write(line)
                    kept += 1
            except (ValueError, KeyError, TypeError):
                continue
    if kept == 0:
        os.remove(out)
    print(f"archive: {tag} {kept} rows -> {os.path.basename(out) if kept else 'nothing'}")
PY
cd "$WS/site" || exit 0
git config user.name "sift-bot"
git config user.email "sift-bot@users.noreply.github.com"
git add panel
if git diff --cached --quiet; then echo "archive: nothing new"; exit 0; fi
git commit --quiet -m "Panel archive $STAMP (run $RUN_ID)"
for attempt in 1 2 3; do
  if git push --quiet origin HEAD:main; then echo "archive: published"; exit 0; fi
  git fetch --quiet origin main
  if ! git rebase --quiet -X theirs origin/main; then
    git rebase --abort || true
    git reset --quiet --hard origin/main
    echo "archive: rebase failed; not published this time"
    exit 0
  fi
  sleep 5
done
echo "archive: not published after 3 attempts"
exit 0
