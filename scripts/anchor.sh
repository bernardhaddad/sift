#!/usr/bin/env bash
# once a day: copy the track record and radar data into proofs/<date>/, list their sha256 in
# manifest.txt and timestamp it with OpenTimestamps (anchored in Bitcoin). each run also upgrades
# earlier pending stamps. best effort, always exits 0.
#   usage: anchor.sh <site dir>
#   check: sha256sum -c manifest.txt && ots verify manifest.txt.ots (needs a bitcoin node;
#   without one, ots --no-bitcoin verify prints the block and merkle root to look up)
set -u
site="$1"
day=$(date -u +%F)
dir="$site/proofs/$day"

if ! command -v ots >/dev/null; then
  python3 -m pip install --quiet opentimestamps-client >/dev/null 2>&1 || { echo "anchor: ots not installed"; exit 0; }
fi

# a fresh stamp waits on Bitcoin for a few hours; upgrade it once the block is in
for f in "$site"/proofs/*/manifest.txt.ots; do
  [ -f "$f" ] || continue
  if ! ots info "$f" 2>/dev/null | grep -q BitcoinBlockHeaderAttestation; then
    timeout 60 ots upgrade "$f" >/dev/null 2>&1 && echo "anchor: upgraded $f"
  fi
done
rm -f "$site"/proofs/*/manifest.txt.ots.bak

if [ ! -f "$dir/manifest.txt.ots" ] && [ -f "$site/radar-ledger.json" ] && [ -f "$site/radar.json" ]; then
  mkdir -p "$dir"
  gzip -9 -n -c "$site/radar-ledger.json" > "$dir/radar-ledger.json.gz"
  gzip -9 -n -c "$site/radar.json" > "$dir/radar.json.gz"
  (cd "$dir" && sha256sum radar-ledger.json.gz radar.json.gz > manifest.txt)
  if timeout 120 ots stamp "$dir/manifest.txt" >/dev/null 2>&1; then
    echo "anchor: stamped $day"
  else
    echo "anchor: stamp failed, retried next run"
    rm -f "$dir/manifest.txt.ots"
  fi
fi
exit 0
