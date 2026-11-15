#!/usr/bin/env bash
# referral guard, run on the site checkout right before the hourly commit. Every file git reports as
# changed or new under <site dir> among *.html, *.xml, *.json and *.js is searched for a referral
# link pattern: a GMGN code (gmgn.ai/sol/token/<code>_), a ref parameter, an Axiom, Photon or Trojan
# invite, or Jupiter's referrer and fee parameters (the private code's referral scan uses the same
# list, and its tests check this copy against it). On a hit, a file that is already published goes
# back to its published copy and a new one is left out of the commit, with a warning for each.
# config.js is not searched: it holds the empty link templates. The record files are searched but
# never changed, only warned about: the ledger and the other files the next run reads back carry
# token names as the chain gives them, and an hour without the ledger's sample is lost for good
# (6 hours without one void every open entry). Best effort, always exits 0.
#   usage: publish-guard.sh <site dir>
set -u
export LC_ALL=C
export GIT_LITERAL_PATHSPECS=1  # file names are names, never patterns

# the same patterns as the private code's referral scan, as extended regular expressions (grep -Ei)
names=("GMGN code" "ref parameter" "Axiom invite" "Photon invite" "Trojan invite" "Jupiter referrer" "Jupiter fee")
patterns=(
  "gmgn\.ai/sol/token/[^[:space:]/_?#\"'<>]+_"
  "[?&](amp;)?ref="
  "axiom\.trade/@"
  "photon-sol\.tinyastro\.io/@"
  "start=r-"
  "referrer="
  "feeBps="
)
any=()
for p in "${patterns[@]}"; do any+=(-e "$p"); done

# relative to the site folder: the track record and the files the next run reads back
is_record() {
  case "$1" in
    radar-ledger.json | radar.json | radar-wallets.json | radar-lp.json | radar-audit.json) return 0 ;;
    data.json | fast.json | cluster_verdicts.json | paper-seed/* | radar-auto/* | radar-requests/*) return 0 ;;
  esac
  return 1
}

shown() { printf '%s' "${1//[^A-Za-z0-9._\/ -]/?}"; }  # a path as the log may show it

found() {  # the names of the patterns in file $1, comma-separated
  local i out=""
  for i in "${!patterns[@]}"; do
    grep -Eiq -e "${patterns[$i]}" -- "$1" 2>/dev/null && out="${out:+$out, }${names[$i]}"
  done
  printf '%s' "$out"
}

site="${1:-}"
if [ -z "$site" ] || ! cd -- "$site" 2>/dev/null; then
  echo "::warning::publish guard: no site folder ($(shown "$site")); nothing searched"
  exit 0
fi
if ! top=$(git rev-parse --show-toplevel 2>/dev/null) || ! prefix=$(git rev-parse --show-prefix 2>/dev/null); then
  echo "::warning::publish guard: $(shown "$site") is not a git checkout; nothing searched"
  exit 0
fi
cd -- "$top" || exit 0
list=$(mktemp 2>/dev/null) || { echo "::warning::publish guard: no temporary file; nothing searched"; exit 0; }
trap 'rm -f "$list"' EXIT
if ! git status --porcelain -z --untracked-files=all -- "${prefix:-.}" >"$list" 2>/dev/null; then
  echo "::warning::publish guard: git status failed; nothing searched"
  exit 0
fi

searched=0 restored=0 left=0 records=0
while IFS= read -r -d '' entry; do
  xy=${entry:0:2}
  path=${entry:3}
  case "$xy" in *R* | *C*) IFS= read -r -d '' _from ;; esac  # a rename or copy: its source follows
  rel=${path#"$prefix"}
  case "$rel" in *.html | *.xml | *.json | *.js) ;; *) continue ;; esac
  [ "$rel" = config.js ] && continue
  [ -f "$path" ] || continue  # deleted
  searched=$((searched + 1))
  grep -Eiq "${any[@]}" -- "$path" 2>/dev/null || continue
  what="$(shown "$rel") has a referral pattern ($(found "$path"))"
  if is_record "$rel"; then
    records=$((records + 1))
    echo "::warning::publish guard: $what; a record file, published as built"
  elif git cat-file -e "HEAD:$path" 2>/dev/null; then
    if git checkout -q HEAD -- "$path" 2>/dev/null; then
      restored=$((restored + 1))
      echo "::warning::publish guard: $what; the published copy is kept"
    else
      echo "::warning::publish guard: $what; the published copy could not be restored"
    fi
  else
    git rm -q --cached --ignore-unmatch -- "$path" >/dev/null 2>&1
    rm -f -- "$path"
    left=$((left + 1))
    echo "::warning::publish guard: $what; a new file, left out"
  fi
done <"$list"
echo "publish guard: $searched changed files searched, $restored restored to the published copy, $left new left out, $records record files warned"
exit 0
