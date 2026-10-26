#!/usr/bin/env python3
"""Is the paper book in DIR usable: readable, and not behind the published record?

    python3 scripts/book-check.py DIR PUBLISHED_DATA_JSON

Exit 0 when the book in DIR/state.json is readable and its cycle_meta seq and every book's closed
count are at least those of the published data.json; exit 1 (reason printed) otherwise. A missing
or unreadable published record is no evidence either way and does not block."""

import json
import sys


def problems(book_dir: str, published: str) -> list[str]:
    try:
        book = json.load(open(f"{book_dir}/state.json"))["experiments"]
        book.items()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return ["state.json missing or unreadable"]
    try:
        pub = json.load(open(published))
    except (OSError, ValueError):
        return []
    if not isinstance(pub, dict):
        return []
    try:
        meta = json.load(open(f"{book_dir}/cycle_meta.json"))
    except (OSError, ValueError):
        meta = {}
    out = []
    pub_seq, seq = pub.get("seq"), (meta.get("seq") if isinstance(meta, dict) else None)
    if isinstance(pub_seq, int) and (not isinstance(seq, int) or seq < pub_seq):
        out.append(f"seq {seq} is older than the published seq {pub_seq}")
    for b in pub.get("books") or []:
        if isinstance(b, dict) and isinstance(book.get(b.get("name")), dict) and isinstance(b.get("closed"), int):
            have = len(book[b["name"]].get("closed") or [])
            if have < b["closed"]:
                out.append(f"{b['name']} has {have} closed trades, the published record {b['closed']}")
    return out


if __name__ == "__main__":
    found = problems(sys.argv[1], sys.argv[2])
    print("; ".join(found) if found else "book is readable and not behind the published record")
    sys.exit(1 if found else 0)
