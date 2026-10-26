#!/usr/bin/env bash
# copy cached panel rows that aren't in panel/ yet into a gzipped file there.
# best effort, always exits 0.
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


def archived_since(tag, oldest):
    """Every row timestamp already archived for this tag at or after `oldest`."""
    seen = set()
    for f in sorted(glob.glob(os.path.join(dest, f"*.{tag}.jsonl.gz")), reverse=True):
        newest_in_file = None
        try:
            with gzip.open(f, "rt") as gz:
                for line in gz:
                    try:
                        at = json.loads(line)["at"]
                    except (ValueError, KeyError, TypeError):
                        continue
                    newest_in_file = at if newest_in_file is None or at > newest_in_file else newest_in_file
                    if at >= oldest:
                        seen.add(at)
        except (OSError, EOFError):
            continue
        if newest_in_file is not None and newest_in_file < oldest:
            break  # older files are outside the cache window
    return seen


for name, tag in (("panel.jsonl", "panel"), ("scan_log.jsonl", "scan"), ("lp_log.jsonl", "lp")):
    path = os.path.join(src, name)
    if not os.path.exists(path):
        continue
    rows = []
    with open(path) as fh:
        for line in fh:
            try:
                rows.append((json.loads(line)["at"], line))
            except (ValueError, KeyError, TypeError):
                continue
    if not rows:
        continue
    seen = archived_since(tag, min(at for at, _ in rows))
    any_archived = bool(glob.glob(os.path.join(dest, f"*.{tag}.jsonl.gz")))
    kept = 0
    out = os.path.join(dest, f"{stem}.{tag}.jsonl.gz")
    with gzip.open(out, "wt") as gz:
        for at, line in rows:
            if at in seen:
                continue
            if not any_archived and started and at < started:
                continue
            gz.write(line)
            kept += 1
    if kept == 0:
        os.remove(out)
    print(f"archive: {tag} {kept} new of {len(rows)} cached rows -> {os.path.basename(out) if kept else 'nothing'}")
PY
cd "$WS/site" || exit 0
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add panel
if git diff --cached --quiet; then echo "archive: nothing new"; exit 0; fi
git commit --quiet -m "Panel archive $STAMP (run $RUN_ID)"
bash scripts/push-snapshot.sh 3 || echo "archive: not published this time"
exit 0
