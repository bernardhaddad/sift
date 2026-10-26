#!/usr/bin/env bash
# push the commit at HEAD to main. When another job pushed first, start again from the new main and
# re-apply exactly the files HEAD changed, by name: this job's version of each wins, and a file it
# deleted stays deleted. No rebase, so git never guesses renames between look-alike files (the
# share pages in t/ are near-identical and a rename guess can swap two tokens' pages).
# usage: push-snapshot.sh [attempts]   (run inside the site checkout; exit 0 = published)
attempts=${1:-3}
snap=$(git rev-parse HEAD)
msg=$(git log -1 --format=%B "$snap")
for attempt in $(seq 1 "$attempts"); do
  if git push --quiet origin HEAD:main; then echo "published"; exit 0; fi
  echo "push rejected (attempt $attempt); re-applying this run's files onto origin/main"
  sleep $((attempt * 3))
  git fetch --quiet origin main || continue
  git reset --quiet --hard origin/main
  git diff --name-only --no-renames -z "$snap~1" "$snap" | while IFS= read -r -d '' f; do
    if git cat-file -e "$snap:$f" 2>/dev/null; then
      git checkout --quiet "$snap" -- "$f"
    else
      git rm --quiet -f --ignore-unmatch -- "$f"
    fi
  done
  git add -A
  if git diff --cached --quiet; then echo "nothing left to publish"; exit 0; fi
  git commit --quiet -m "$msg"
done
echo "not published after $attempts attempts"
exit 1
