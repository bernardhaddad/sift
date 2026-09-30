#!/usr/bin/env bash
# Archive one scanner run's minute panel into this repository so the mining passes can
# read weeks of it. Run artifacts expire after 14 days and the cached logs rotate at
# three days; a gzipped slice of this run's rows (panel, leaderboard scan, liquidity
# pools) is about 2 MB and lives in panel/ forever. Each tag's rows are sliced from just
# after the last row already archived (falling back to the run's start time when nothing
# is archived yet), so consecutive runs never duplicate each other and a run whose push
# failed is caught up by the next one while the cached logs still hold it (three days).
# Never fails the caller.
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
import glob


def last_archived(tag):
    """Timestamp of the newest row already archived for this tag, or None."""
    for f in sorted(glob.glob(os.path.join(dest, f"*.{tag}.jsonl.gz")), reverse=True):
        last = None
        try:
            with gzip.open(f, "rt") as gz:
                for line in gz:
                    try:
                        last = json.loads(line)["at"]
                    except (ValueError, KeyError, TypeError):
                        continue
        except (OSError, EOFError):
            continue
        if last:
            return last
    return None


for name, tag in (("panel.jsonl", "panel"), ("scan_log.jsonl", "scan"), ("lp_log.jsonl", "lp")):
    path = os.path.join(src, name)
    if not os.path.exists(path):
        continue
    after = last_archived(tag)
    kept = 0
    out = os.path.join(dest, f"{stem}.{tag}.jsonl.gz")
    with open(path) as fh, gzip.open(out, "wt") as gz:
        for line in fh:
            try:
                at = json.loads(line)["at"]
            except (ValueError, KeyError, TypeError):
                continue
            if (after and at > after) or (not after and (not started or at >= started)):
                gz.write(line)
                kept += 1
    if kept == 0:
        os.remove(out)
    since = f"after {after}" if after else f"from {started or 'the start'}"
    print(f"archive: {tag} {kept} rows {since} -> {os.path.basename(out) if kept else 'nothing'}")
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
