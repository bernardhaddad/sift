#!/usr/bin/env python3
"""Check the Insider Radar's published links against the Solana chain yourself.

Needs only Python 3.11+ and network access: no key, no account, no dependencies.

    python3 verify.py <token mint>       every published link of one token
    python3 verify.py all                every published link (slow on the public node)
    SOLANA_RPC_URL=<url> python3 verify.py <mint>    use your own node

It reads radar.json from the site and checks what each link claims:

- "funded" (A paid for B's first transaction): the proof transaction's fee payer is A, B takes
  part in it, and it is B's oldest transaction (when B has fewer than 1,000, so the oldest is
  reachable; otherwise only the fee payer is checked, and the result says so).
- "tokens" (A sent B its first tokens): the proof transaction moves tokens under A's authority and
  leaves B holding a token balance.

Each link is "verified", "failed" (the chain contradicts it) or "unreachable" (the node did not
answer). The site runs this same file every hour on a sample of links and publishes the result as
radar-audit.json:

    python3 verify.py --audit <radar.json> <audit_in_out.json> [max_links]
"""

import json
import os
import pathlib
import random
import sys
import time
import urllib.request
from datetime import UTC, datetime

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
RADAR_URL = "https://bernardhaddad.github.io/sift/radar.json"
MAX_LINKS = 12
HISTORY = 200
TX_OPTS = {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}


class Unreachable(Exception):
    pass


class Node:
    def __init__(self, url: str, pause: float = 0.6):
        self.url, self.pause = url, pause

    def call(self, method: str, params: list):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        for attempt in range(4):
            try:
                req = urllib.request.Request(
                    self.url, data=body, headers={"content-type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    reply = json.loads(resp.read())
                time.sleep(self.pause)
                if "result" in reply:
                    return reply["result"]
            except Exception:  # noqa: BLE001  # any transport problem is "could not ask"
                pass
            time.sleep(2.0 * (attempt + 1))
        raise Unreachable(method)


def _keys(tx) -> list:
    return [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]


def _instructions(tx) -> list:
    out = list(tx["transaction"]["message"].get("instructions") or [])
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        out += group.get("instructions") or []
    return out


def check_link(node: Node, link: dict) -> tuple[str, str]:
    """(status, reason) for one published link."""
    sig, src, dst, kind = link.get("tx"), link.get("from"), link.get("to"), link.get("kind")
    if not sig:
        return "skipped", "published without a proof transaction"
    tx = node.call("getTransaction", [sig, TX_OPTS])
    if tx is None:
        return "failed", "proof transaction not found"
    keys = _keys(tx)
    if kind == "funded":
        if keys[0] != src:
            return "failed", f"fee payer is {keys[0][:8]}, not {src[:8]}"
        if dst not in keys:
            return "failed", f"{dst[:8]} does not take part in the proof transaction"
        sigs = node.call("getSignaturesForAddress", [dst, {"limit": 1000}])
        if len(sigs) >= 1000:
            return "verified", "fee payer confirmed; 1,000+ transactions, first one not reachable"
        if sigs and sigs[-1]["signature"] != sig:
            return "failed", "the proof is not the wallet's first transaction"
        return "verified", "fee payer and first transaction confirmed"
    if kind == "tokens":
        moved = False
        for ins in _instructions(tx):
            p = ins.get("parsed")
            if not isinstance(p, dict):
                continue
            info = p.get("info") or {}
            who = info.get("authority") or info.get("multisigAuthority") or info.get("mintAuthority")
            if p.get("type") in ("transfer", "transferChecked", "mintTo", "mintToChecked") and who == src:
                moved = True
        if not moved:
            return "failed", f"no token transfer under {src[:8]}'s authority"
        owners = {b.get("owner") for b in (tx.get("meta") or {}).get("postTokenBalances") or []}
        if dst not in owners:
            return "failed", f"{dst[:8]} received no tokens in the proof transaction"
        return "verified", "token transfer under the sender's authority confirmed"
    return "skipped", f"unknown link kind {kind!r}"


def sample(doc: dict, max_links: int, seed: int) -> list:
    links = [
        {**k, "mint": t["mint"], "name": t.get("name")}
        for t in doc.get("tokens") or []
        for c in t.get("clusters") or []
        for k in c.get("links") or []
        if k.get("tx")
    ]
    random.Random(seed).shuffle(links)
    return links[:max_links]


def run(doc: dict, prior: dict | None, node: Node, now: datetime, max_links: int = MAX_LINKS) -> dict:
    # a new sample every run: two runs in one hour must not re-count the same links
    picked = sample(doc, max_links, seed=int(now.timestamp()))
    results = []
    for link in picked:
        try:
            status, reason = check_link(node, link)
        except Unreachable:
            status, reason = "unreachable", "the independent node did not answer"
        except (KeyError, TypeError, IndexError, ValueError) as err:
            status, reason = "unreachable", f"unreadable reply: {type(err).__name__}"
        results.append(
            {
                "mint": link["mint"],
                "name": link["name"],
                "from": link["from"],
                "to": link["to"],
                "kind": link["kind"],
                "tx": link["tx"],
                "status": status,
                "reason": reason,
            }
        )
    counts = {
        s: sum(1 for r in results if r["status"] == s)
        for s in ("verified", "failed", "unreachable", "skipped")
    }
    entry = {"at": now.isoformat(timespec="seconds"), "node": "public Solana RPC", **counts}
    history = ([entry] + list((prior or {}).get("history") or []))[:HISTORY]
    keys = ("verified", "failed", "unreachable")
    if isinstance((prior or {}).get("runs"), int):  # running totals since `since`, never trimmed
        old = prior.get("totals") or {}
        totals = {k: int(old.get(k) or 0) + entry[k] for k in keys}
        runs = prior["runs"] + 1
    else:  # older files kept only the last HISTORY runs; their sum is all they can give
        totals = {k: sum(h.get(k, 0) for h in history) for k in keys}
        runs = len(history)
    since = (prior or {}).get("since") or entry["at"]
    return {"since": since, "runs": runs, "last": {**entry, "links": results}, "totals": totals,
            "history": history}


def all_links(doc: dict, mint: str | None = None) -> list:
    return [
        {**k, "mint": t["mint"], "name": t.get("name")}
        for t in doc.get("tokens") or []
        if mint is None or t.get("mint") == mint
        for c in t.get("clusters") or []
        for k in c.get("links") or []
    ]


def check_all(node: Node, links: list) -> dict:
    counts = {"verified": 0, "failed": 0, "unreachable": 0, "skipped": 0}
    for link in links:
        try:
            status, reason = check_link(node, link)
        except Unreachable:
            status, reason = "unreachable", "the node did not answer"
        except (KeyError, TypeError, IndexError, ValueError) as err:
            status, reason = "unreachable", f"unreadable reply: {type(err).__name__}"
        counts[status] += 1
        print(f"{status:<11} {link.get('name') or link['mint'][:8]} {link.get('kind')} "
              f"{link.get('from')} -> {link.get('to')}\n            tx {link.get('tx')}: {reason}")
    return counts


def verify(argv: list[str]) -> int:
    target = argv[1]
    with urllib.request.urlopen(RADAR_URL, timeout=30) as resp:
        doc = json.loads(resp.read())
    links = all_links(doc, None if target == "all" else target)
    if not links:
        print(f"no published links for {target} (not on the radar, or no linked holders)")
        return 1
    print(f"radar.json of {doc.get('generated_at') or doc.get('updated')}: {len(links)} links to check")
    counts = check_all(Node(os.environ.get("SOLANA_RPC_URL") or PUBLIC_RPC), links)
    print(", ".join(f"{n} {s}" for s, n in counts.items() if n))
    return 1 if counts["failed"] else 0


def audit(argv: list[str]) -> int:
    doc = json.loads(pathlib.Path(argv[1]).read_text())
    path = pathlib.Path(argv[2])
    try:
        prior = json.loads(path.read_text()) if path.is_file() else None
    except (OSError, ValueError):
        print(f"[audit] {path} unreadable; not overwritten")
        return 1
    node = Node(os.environ.get("AUDIT_RPC_URL") or PUBLIC_RPC)
    out = run(doc, prior, node, datetime.now(UTC), int(argv[3]) if len(argv) > 3 else MAX_LINKS)
    path.write_text(json.dumps(out, separators=(",", ":")))
    last = out["last"]
    print(f"[audit] {last['verified']} verified, {last['failed']} failed, {last['unreachable']} unreachable")
    for r in last["links"]:
        if r["status"] == "failed":
            print(f"[audit] FAILED {r['name']} {r['kind']} {r['from']} -> {r['to']} {r['tx']}: {r['reason']}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[1] == "--audit":
        return audit(argv[1:])
    if len(argv) == 2 and not argv[1].startswith("-"):
        return verify(argv)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
