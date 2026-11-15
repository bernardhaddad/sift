"""Sift Insider Radar: the funding-cluster traces this system already runs, published as evidence.

Every hour the paper cycle traces the top holders of the tokens the minute scanner watches
(cluster.funding_cluster, STRATEGY.md §10 Amendment 6) and caches the full result. This module
turns those results, plus the traces anyone can request through a GitHub issue
(scripts/trace_token.py, the sift repository's trace.yml), into site/radar.json: per token a
plain-language grade, the share of supply held by wallets that share a private funder, and the
evidence (which holders, which funding wallets). The grade uses the same thresholds as the
trading gate (filters.cluster_gate), so the radar says exactly what the books act on.

What a cluster is and is not: two or more top-20 holders linked, within two funding hops or by a
direct token transfer, to the same private wallet (not an exchange hot wallet or a program). It is
strong evidence of one actor behind several "independent" holders. It is not proof of intent,
and a clean grade is not a safety guarantee: only the 20 largest accounts are sampled, and busy
wallets are not traced (cluster.py, "Known limits").
"""

import html
import json
import math
import pathlib
import re
import unicodedata
from datetime import UTC, datetime, timedelta

from memetrader import cluster, filters, ledger

MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # base58, the length range of a Solana address
SCHEMA = 1  # radar.json format: raised when a field is renamed or removed, never for additions
MAX_WALLETS_SHOWN = 20
MAX_FUNDERS_SHOWN = 8

# code -> (label, order on the page, one-line meaning). The labels say what was measured, not what it
# means for anyone holding the token. They were renamed for display only (site/radar.html dates it):
# the codes, the thresholds and every frozen verdict are unchanged, and the definitions fixed in
# ledger.py keep the earlier names word for word (site/radar.html says so beside the grades).
GRADES = {
    "high": (
        "High linked share",
        0,
        "Top holders funded by the same private wallet hold over 10% of supply in one group, or over "
        "20% across groups. Sift's own paper books refuse to trade above that line.",
    ),
    "watch": (
        "Linked 5%+",
        1,
        "Linked groups hold 5% of supply or more but stay under that line. Also used when the only "
        "links over the line run through a high-volume wallet (possibly an exchange or a bot) or through "
        "a wallet seen behind holders of several tokens.",
    ),
    "low": (
        "Under 5% linked",
        2,
        "Linked groups hold under 5% of the supply that could be traced.",
    ),
    "incomplete": (
        "Incomplete",
        3,
        "Too many top holders could not be traced to say how much linked groups hold.",
    ),
}
WATCH_CLUSTERED_PCT = 5.0  # any linked groups at or above this share of supply are worth a look
REQUEST_TTL = timedelta(days=7)  # requested traces are shown for a week, with their date
MAX_EDGES_SHOWN = 120  # 20 holders x (funding hop, second hop, token transfer), all of them
# preview card look (cards.py); 2: grey badge, renamed labels, safe names; 3: drawn once more, from the
# evidence frozen at entry (the hourly build no longer redraws cards, and 88 of the 319 published on 13 Nov
# showed a later trace than their token page, 21 of them with another grade)
CARD_VERSION = 3


def valid_mint(text: str) -> bool:
    return bool(MINT_RE.match(text or ""))


def grade(result: dict | None) -> str:
    """Same thresholds as the trading gate: refused for a cluster = high; refused for missing data =
    incomplete; admitted but with linked groups holding 5% or more = watch.

    One difference: the gate counts a link through a busy wallet (exchange-scale history, small
    balance: an exchange withdrawal or bot wallet) as private, which is right for refusing a
    trade but not for calling someone an insider in public. A token the gate refuses only because
    of such links is graded watch, not high."""
    ok, reason = filters.cluster_gate(result, authority=False)  # the verdict is about holders only
    if reason == "funding_cluster":
        if "strict_cluster_pct" not in result:
            return "high"  # traced before busy wallets were recorded
        strict = {
            **result,
            "cluster_pct": result["strict_cluster_pct"],
            "clustered_pct": result["strict_clustered_pct"],
        }
        return "high" if filters.cluster_gate(strict, authority=False)[1] == "funding_cluster" else "watch"
    if not ok:
        return "incomplete"
    # (the share of wallets with little history is shown, not graded: most are ordinary wallets
    # first funded by an exchange, which says nothing about insiders)
    total = result.get("broad_clustered_pct", result.get("clustered_pct"))
    if float(total or 0) >= WATCH_CLUSTERED_PCT:
        return "watch"
    return "low"


ESTABLISHED_DAYS = 30


def established(t: dict) -> str:
    """The date a token has traded since (its oldest pool seen), when it was over ESTABLISHED_DAYS
    old at the verdict (older_token); else empty. Linked top holders of an older token are often
    the project's own wallets (treasury, vesting, market making) rather than launch insiders: the
    links stay true and the grade is unchanged, but what is shared shows OLDER_TOKEN instead."""
    try:
        since = datetime.fromisoformat(str(t["trading_since"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return ""
    return since.date().isoformat() if older_token(t) else ""


OLDER_TOKEN = "Older token: links may be the project's own wallets"


def older_token(t: dict) -> bool:
    """Over ESTABLISHED_DAYS old at the verdict: ledger.age_band is "over_30d", read at a ledger
    entry's entered_at, or at a radar row's traced_at. An unknown age is not older. The share page
    title, the card, the feed items and the trace reply of such a token carry OLDER_TOKEN and no
    grade word when it has linked holders (older_note); the grade itself, the ledger and the radar
    row are unchanged."""
    at = t.get("entered_at") or t.get("traced_at")
    return ledger.age_band({"trading_since": t.get("trading_since"), "entered_at": at}) == "over_30d"


def older_note(t: dict) -> bool:
    """Whether what is shared of a radar row (share page, card, trace reply) shows OLDER_TOKEN in
    place of its label: an older token (older_token) whose trace found a linked group, which the
    note is about. Every High and Watch grade rests on one. An older token with no linked group
    shows its label, as the radar page does: saying its links may be the project's own wallets
    would suggest links that were not found."""
    return older_token(t) and (grade_code(t) in ("high", "watch") or bool(t.get("clusters")))


def grade_code(t: dict) -> str:
    """A row's or entry's grade code; incomplete for anything unknown."""
    g = t.get("grade")
    return g if isinstance(g, str) and g in GRADES else "incomplete"


def grade_label(t: dict) -> str:
    """The label of a row's or entry's grade, read from its code (an unknown code reads Incomplete),
    so a row saved before the labels were renamed shows today's."""
    return GRADES[grade_code(t)][0]


def _num(v) -> float:
    """A number from published data, 0 for anything unreadable (never raises)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return v if v == v and abs(v) != float("inf") else 0.0


def weak_link(t: dict) -> str:
    """Why a Watch token with a large linked group is not High: its links run through a busy or a
    repeat wallet. Empty when that is not the case. The reason comes from the groups over the
    one-group line when there are any (the ones that would make it High), else from any group."""
    if t.get("grade") != "watch":
        return ""
    groups = t.get("clusters") or []
    over = [c for c in groups if _num(c.get("pct")) > filters.M2["max_cluster_pct"]]
    for scope in (over, groups):
        if any(c.get("busy") for c in scope):
            return "the link runs through a high-volume wallet, possibly an exchange"
        if any(c.get("known") for c in scope):
            return "the link runs through a wallet seen behind several tokens"
    return ""


authority_holder = cluster.authority_holder  # revoked / program / wallet


def authorities(rpc, mints: list, at: str) -> dict:
    """{mint: {"mint": address, "mint_by": holder, "freeze": address, "freeze_by": holder, "at": at}}
    for each mint the node answered, read with getMultipleAccounts (100 a call); holder is
    authority_holder(). Mints the node did not answer for are left out, never guessed."""
    out = {}
    for i in range(0, len(mints), 100):
        batch = mints[i : i + 100]
        try:
            res = rpc.call("getMultipleAccounts", [batch, {"encoding": "jsonParsed"}])
            values = res["value"]
        except (cluster.RpcError, KeyError, TypeError):
            continue
        for mint, acc in zip(batch, values or [], strict=False):
            auth = cluster.mint_authority(acc)
            if auth:
                out[mint] = {**auth, "at": at}
    return out


def retained(rpc, tokens: list, at: str) -> dict:
    """How much of each flagged token's linked supply its linked wallets still hold, read from the
    token's 20 largest accounts now. {mint: {"held_pct", "wallets", "out_of_top", "below_pct", "at"}}:
    held_pct counts the linked wallets still among the 20 largest; each of the out_of_top others
    holds less than below_pct (0 when the token has 20 accounts or fewer, so they hold nothing).
    Tokens the node did not answer for are left out, never guessed."""
    flagged = [t for t in tokens if t.get("grade") in ("high", "watch") and t.get("clusters")]
    supply, largest = {}, {}
    mints = [t["mint"] for t in flagged]
    for i in range(0, len(mints), 100):
        try:
            batch = mints[i : i + 100]
            values = rpc.call("getMultipleAccounts", [batch, {"encoding": "jsonParsed"}])["value"]
        except (cluster.RpcError, KeyError, TypeError):
            continue
        for mint, acc in zip(batch, values or [], strict=False):
            try:
                supply[mint] = int(acc["data"]["parsed"]["info"]["supply"])
            except (KeyError, TypeError, ValueError):
                pass
    for mint in mints:
        if not supply.get(mint):
            continue
        try:
            rows = rpc.call("getTokenLargestAccounts", [mint])["value"]
            largest[mint] = [(a["address"], int(a["amount"])) for a in rows]
        except (cluster.RpcError, KeyError, TypeError, ValueError):
            continue
    accounts = [a for rows in largest.values() for a, _ in rows]
    owner = {}
    for i in range(0, len(accounts), 100):
        batch = accounts[i : i + 100]
        try:
            values = rpc.call("getMultipleAccounts", [batch, {"encoding": "jsonParsed"}])["value"]
        except (cluster.RpcError, KeyError, TypeError):
            continue
        for a, acc in zip(batch, values or [], strict=False):
            try:
                owner[a] = acc["data"]["parsed"]["info"]["owner"]
            except (KeyError, TypeError):
                pass
    out = {}
    for t in flagged:
        rows = largest.get(t["mint"])
        if rows is None or any(a not in owner for a, _ in rows):
            continue  # an account whose owner is unknown could be a linked wallet: no answer
        linked = {w["address"] for c in t["clusters"] for w in c.get("wallets") or []}
        total = supply[t["mint"]]
        held = sum(n for a, n in rows if owner[a] in linked)
        present = {owner[a] for a, _ in rows} & linked
        floor = min((n for _, n in rows), default=0) if len(rows) >= 20 else 0
        out[t["mint"]] = {
            "held_pct": round(held / total * 100, 3),
            "wallets": len(linked),
            "out_of_top": len(linked - present),
            "below_pct": round(floor / total * 100, 3),
            "at": at,
        }
    return out


def current(result) -> bool:
    """A result from the current trace method (cluster.outdated). Older results are not published:
    their grade could count an exchange withdrawal as an insider link."""
    return isinstance(result, dict) and not cluster.outdated(result)


def display_name(name: str | None) -> str | None:
    """The token ticker: scanner names are pool names such as "IDLE / SOL"."""
    return name.split(" / ")[0].strip() or None if name else None


SAFE_NAME_MAX = 32
_NAME_SIGNS = frozenset("$._-")
_BLANK_LETTERS = frozenset(map(chr, (0x115F, 0x1160, 0x3164, 0xFFA0)))  # Hangul fillers: show nothing
_LINK_LIKE = re.compile(  # possessive and anchored to a word start, so a long name stays linear
    r"://|www\.|t\.me/|(?<![\w-])[\w-]++\.[^\W\d_]{2,}+(?![^\W_]|-)",  # the last: x.com/abc, site.io
    re.IGNORECASE,
)
_HANDLE = re.compile(r"@\w")
# letters that render as a dot or colon (NFKC keeps them): a name such as axiom<U+A4F8>trade must read as a
# dotted host to _LINK_LIKE, and none of them is ever kept in a name
_DOT_LIKE = {ord(c): "." for c in "\u02d0\u02d1\u1427\u16eb\u2e31\ua4f8\ua4f9\ua4fa\ua4fb\ua789\u0701\u0702"}


def safe_name(name: str | None, mint: str) -> str:
    """A token's name as public text may show it: Unicode letters, digits, spaces and $ . _ - only
    (after HTML entities are read, as some price services send S&amp;P500, and after NFKC, so
    fullwidth and styled letters read as plain ones), spaces collapsed, at most 32 characters.
    Whoever launches a token chooses its name, so a name that looks like a link (://, www., t.me/
    or a dotted host such as x.com/abc or site.io), an @handle, or one holding a line break or
    control character, is replaced by the first 6 characters of the mint, as is a name with
    nothing left. Expects the display name (display_name), not a pool name."""
    fallback = str(mint or "")[:6]
    if not isinstance(name, str):
        return fallback
    name = html.unescape(name)  # &#64;handle and &#10; are an @handle and a line break
    text = unicodedata.normalize("NFKC", name)
    if any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in name + text):
        return fallback
    if _LINK_LIKE.search(text.translate(_DOT_LIKE)) or _HANDLE.search(text):
        return fallback
    text = text.translate({k: None for k in _DOT_LIKE})
    kept = []
    for ch in text:
        kind = unicodedata.category(ch)
        if ch in _NAME_SIGNS or kind == "Nd" or (kind[0] == "L" and ch not in _BLANK_LETTERS):
            kept.append(ch)
        elif kind == "Zs":
            kept.append(" ")
    out = " ".join("".join(kept).split())[:SAFE_NAME_MAX].strip()
    return out if out and not _LINK_LIKE.search(out) else fallback  # x.c/om would leave x.com


def entry(mint: str, name: str | None, result: dict, at: str | None, source: str) -> dict:
    """One token's public record. Wallet and funder addresses are on-chain public data."""
    shares = result.get("shares") or {}
    busy = set(result.get("busy_linkers") or [])
    known = set(result.get("known_linkers") or [])
    edges = [
        (e[0], e[1], e[2], e[3] if len(e) > 3 else None)
        for e in result.get("edges") or []
        if isinstance(e, list | tuple) and len(e) >= 3
    ]
    clusters = []
    groups = result.get("broad_clusters")
    groups = groups if isinstance(groups, list) else result.get("clusters") or []
    for c in groups:
        wallets = sorted(c.get("wallets") or [], key=lambda w: -float(shares.get(w, 0)))
        members = set(wallets) | set(c.get("linkers") or [])
        clusters.append(
            {
                "pct": c.get("pct"),
                "holders": len(wallets),
                "wallets": [{"address": w, "pct": shares.get(w)} for w in wallets[:MAX_WALLETS_SHOWN]],
                "funders": (c.get("linkers") or [])[:MAX_FUNDERS_SHOWN],
                # linked through a busy wallet: possibly an exchange or bot, not one actor
                "busy": sorted(busy & members),
                # how the group is linked: `from` paid for `to`'s first transaction (funded) or sent
                # its token account its first tokens (tokens)
                "links": [
                    {"from": b, "to": a, "kind": k, "tx": tx}
                    for a, b, k, tx in edges
                    if a in members and b in members
                ][:MAX_EDGES_SHOWN],
                "links_total": sum(1 for a, b, _, _ in edges if a in members and b in members),
                # linked through a wallet seen behind holders of several tokens
                "known": sorted(known & members),
            }
        )
    g = grade(result)
    return {
        "mint": mint,
        "name": display_name(name) or mint[:6] + "…",
        "grade": g,
        "label": GRADES[g][0],
        # the groups shown: the broad view (the gate's groups plus links through repeat wallets)
        "cluster_pct": groups[0].get("pct") if groups else 0.0,
        "clustered_pct": round(sum(float(c.get("pct") or 0) for c in groups), 3),
        "fresh_pct": result.get("fresh_pct"),
        "unknown_pct": result.get("unknown_pct"),
        "top_pct": result.get("top_pct"),
        "holders": result.get("holders"),
        "wallets": result.get("wallets"),
        "fresh": result.get("fresh"),
        "transfer_linked": result.get("transfer_linked"),
        "truncated": bool(result.get("truncated")),
        "clusters": clusters,
        "traced_at": at,
        "source": source,
    }


def names_from_log(log_path: pathlib.Path) -> dict:
    """mint -> name from the cluster log (the cache stores results without names)."""
    names: dict = {}
    try:
        lines = log_path.read_text().splitlines()
    except OSError:
        return names
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("mint") and rec.get("name"):
            names[rec["mint"]] = rec["name"]
    return names


def _age(at, now: datetime) -> timedelta | None:
    try:
        return now - datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return None


def build(
    cache_data: dict,
    names: dict,
    requests: list,
    now: datetime,
    ttl: timedelta = timedelta(hours=24),
    ttl_incomplete: timedelta = timedelta(hours=2),
) -> dict:
    """The radar document. `cache_data` is cluster.Cache's raw dict (mint -> {at, result,
    complete}); `requests` are on-demand traces ({mint, name, at, result}); the newer trace of a
    mint wins. Nothing past its lifetime is published (the cache's own TTLs for scanner traces,
    REQUEST_TTL for requests): an hour with a failed RPC node must not republish old grades as new."""
    rows: dict = {}
    for mint, e in (cache_data or {}).items():
        if not (isinstance(e, dict) and current(e.get("result"))):
            continue
        age = _age(e.get("at"), now)
        if age is None or age >= (ttl if e.get("complete", True) else ttl_incomplete):
            continue
        rows[mint] = entry(mint, names.get(mint), e["result"], e.get("at"), "scanner")
    for r in requests:
        mint = r.get("mint")
        if not (mint and current(r.get("result"))):
            continue
        age = _age(r.get("at"), now)
        if age is None or age >= REQUEST_TTL:
            continue
        if mint in rows and (rows[mint]["traced_at"] or "") >= (r.get("at") or ""):
            continue
        source = "trending" if r.get("source") == "trending" else "request"
        rows[mint] = entry(mint, r.get("name") or names.get(mint), r["result"], r.get("at"), source)
    for mint in filters.NON_MEME_MINTS & set(rows):  # outside the radar's scope
        del rows[mint]
    tokens = sorted(rows.values(), key=lambda t: (GRADES[t["grade"]][1], -(t["clustered_pct"] or 0)))
    counts = {g: sum(1 for t in tokens if t["grade"] == g) for g in GRADES}
    return {
        "schema": SCHEMA,
        "generated_at": now.isoformat(timespec="seconds"),
        "counts": counts,
        "grades": {g: {"label": v[0], "meaning": v[2]} for g, v in GRADES.items()},
        "tokens": tokens,
        "repeat_wallets": repeat_wallets(tokens),
    }


def repeat_wallets(tokens: list, limit: int = 50) -> list:
    """Wallets that link top holders of two or more tokens on the radar: the cross-token view no
    single trace has. Flagged when any trace saw them as busy or as a known shared service."""
    seen: dict = {}
    flags: dict = {}
    names = {t["mint"]: t["name"] for t in tokens}
    for t in tokens:
        for c in t.get("clusters") or []:
            for k in c.get("links") or []:
                seen.setdefault(k["from"], set()).add(t["mint"])
            for b in c.get("busy") or []:
                flags.setdefault(b, set()).add("busy")
            for b in c.get("known") or []:
                flags.setdefault(b, set()).add("known")
    rows = [
        {
            "address": a,
            "tokens": [{"mint": m, "name": names[m]} for m in sorted(ms, key=lambda m: names[m])],
            "flags": sorted(flags.get(a, ())),
        }
        for a, ms in seen.items()
        if len(ms) >= 2
    ]
    rows.sort(key=lambda r: (-len(r["tokens"]), r["address"]))
    return rows[:limit]


def load_requests(directory: pathlib.Path | None) -> list:
    out = []
    if not directory or not pathlib.Path(directory).is_dir():
        return out
    for p in sorted(pathlib.Path(directory).glob("*.json")):
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and valid_mint(rec.get("mint") or ""):
            out.append(rec)
    return out


def report_markdown(e: dict) -> str:
    """The reply posted on a trace request issue. A token over 30 days old at the trace with a
    linked group (older_note) is headed OLDER_TOKEN instead of its grade, and carries no grade word."""
    if older_note(e):
        head = [
            f"## {OLDER_TOKEN}",
            "",
            f"`{e['mint']}` has traded since {established(e)}, more than "
            f"{ESTABLISHED_DAYS} days before this trace.",
        ]
    else:
        head = [f"## {grade_label(e)}: `{e['mint']}`", "", GRADES[grade_code(e)][2]]
    lines = [
        *head,
        "",
        f"- Largest linked group: {e['cluster_pct'] or 0:.1f}% of supply",
        f"- All linked groups: {e['clustered_pct'] or 0:.1f}% of supply",
        f"- Could not be traced: {e['unknown_pct'] or 0:.1f}% of supply",
        f"- Top 20 accounts hold {e['top_pct'] or 0:.1f}% of supply",
    ]
    auth = e.get("authority")
    if auth:
        for key, power in (("mint", "can mint new tokens"), ("freeze", "can freeze tokens")):
            a = auth.get(key)
            by = auth.get(f"{key}_by") or authority_holder(a)
            if by == "none":
                text = "revoked"
            elif by == "program":
                text = f"held by a program address `{a}` (bridges and minting programs work this way)"
            elif a is None:  # its holder's address was withdrawn on request; the power is still shown
                text = f"held by a wallet (address withdrawn on request), which {power}"
            else:
                text = f"held by wallet `{a}`, which {power}"
            lines.append(f"- {key.capitalize()} authority: {text}")
    short = lambda a: f"`{a}`"  # noqa: E731 - full addresses, so anyone can check them
    for i, c in enumerate(e["clusters"][:3], 1):
        lines += ["", f"**Group {i}: {c['holders']} holders, {c['pct']:.1f}% of supply**"]
        lines += [f"- `{w['address']}` {w['pct'] or 0:.2f}%" for w in c["wallets"][:8]]
        if len(c["wallets"]) > 8:
            lines.append(f"- and {len(c['wallets']) - 8} more")
        if c.get("links"):
            lines += ["", "How they are linked:"]
            lines += [
                f"- {short(k['from'])} "
                + (
                    "paid for the first transaction of "
                    if k["kind"] == "funded"
                    else "sent the first tokens to "
                )
                + short(k["to"])
                + (f" ([proof](https://solscan.io/tx/{k['tx']}))" if k.get("tx") else "")
                for k in c["links"][:10]
            ]
            total = c.get("links_total", len(c["links"]))
            if total > 10:
                lines.append(f"- and {total - 10} more links (on the radar page)")
        elif c["funders"]:
            lines += ["", "Linked through " + ", ".join(short(f) for f in c["funders"][:5])]
        if c.get("known"):
            lines += [
                "",
                "_Linked through a wallet seen behind top holders of several tokens ("
                + ", ".join(short(b) for b in c["known"][:3])
                + "): a shared service such as a bot, or one deployer behind several tokens._",
            ]
        if c["busy"]:
            lines += [
                "",
                "_Linked through a high-volume wallet ("
                + ", ".join(short(b) for b in c["busy"][:3])
                + "), possibly an exchange or a bot: weaker evidence of a single owner._",
            ]
    lines += [
        "",
        "_Method: top 20 holders, traced 2 funding hops back, plus who sent them their first tokens. "
        "Exchanges (3,000+ txs and 250+ SOL) and programs never count as links; busy or repeat wallets "
        "are flagged as weaker. Not financial advice._",
    ]
    return "\n".join(lines)


REGISTRY_PRUNE = timedelta(days=30)  # a wallet seen behind one token only is forgotten after this
REGISTRY_MAX = 20000


def _when(text) -> datetime | None:
    """A stored timestamp; one without a zone is read as UTC (every stamp here is written in UTC).
    None when it cannot be read."""
    try:
        at = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def update_registry(prior: dict | None, tokens: list, now: datetime) -> dict:
    """The radar's memory across time: every wallet that funded or supplied top holders of a token,
    with every token it was seen behind. Grows by itself each hour; the page lists wallets seen
    behind two or more tokens, including tokens no longer on the page."""
    kept = (prior or {}).get("wallets")
    wallets = {  # a damaged record is dropped, never allowed to stop the build
        a: {**w, "tokens": {m: r for m, r in w["tokens"].items() if isinstance(r, dict)}}
        if isinstance(w.get("tokens"), dict)
        else {**w, "tokens": {}}
        for a, w in (kept.items() if isinstance(kept, dict) else [])
        if isinstance(w, dict) and not cluster.is_pda(a)
    }
    for w in wallets.values():
        w["flags"] = w["flags"] if isinstance(w.get("flags"), list) else []
    stamp = now.isoformat(timespec="seconds")
    for t in tokens:
        for c in t.get("clusters") or []:
            flagged = {b: "busy" for b in c.get("busy") or []} | {b: "known" for b in c.get("known") or []}
            for k in c.get("links") or []:
                if cluster.is_pda(k["from"]):
                    continue  # a program address never stands for a person (cluster.is_pda)
                w = wallets.setdefault(k["from"], {"tokens": {}, "flags": []})
                rec = w["tokens"].setdefault(t["mint"], {"name": t["name"], "first_seen": stamp})
                rec.update({"last_seen": stamp, "grade": t["grade"]})
                if k["from"] in flagged and flagged[k["from"]] not in w["flags"]:
                    w["flags"] = sorted([*w["flags"], flagged[k["from"]]])
    for a in list(wallets):
        toks = wallets[a]["tokens"] = {
            m: r for m, r in wallets[a].get("tokens", {}).items() if m not in filters.NON_MEME_MINTS
        }  # outside the radar's scope (filters.py): never a link between memecoins
        if not toks:
            del wallets[a]
            continue
        seen = [_when(r.get("last_seen")) for r in toks.values()]
        last = max((s for s in seen if s), default=None)  # an unreadable date never prunes
        if len(toks) < 2 and last and now - last > REGISTRY_PRUNE:
            del wallets[a]
    if len(wallets) > REGISTRY_MAX:  # keep the most connected, then the most recently seen

        def last(a):
            return max(str(r.get("last_seen") or "") for r in wallets[a]["tokens"].values())

        keep = sorted(wallets, key=last, reverse=True)
        keep = sorted(keep, key=lambda a: -len(wallets[a]["tokens"]))[:REGISTRY_MAX]  # stable
        wallets = {a: wallets[a] for a in keep}
    return {"since": (prior or {}).get("since") or stamp, "updated_at": stamp, "wallets": wallets}


def repeat_from_registry(registry: dict, limit: int = 50) -> list:
    rows = [
        {
            "address": a,
            "tokens": [
                {"mint": m, "name": r.get("name"), "grade": r.get("grade"), "last_seen": r.get("last_seen")}
                for m, r in sorted(
                    w["tokens"].items(), key=lambda kv: kv[1].get("last_seen") or "", reverse=True
                )
            ],
            "flags": w.get("flags") or [],
        }
        for a, w in (registry.get("wallets") or {}).items()
        if len(w.get("tokens") or {}) >= 2
    ]
    rows.sort(key=lambda r: (-len(r["tokens"]), r["address"]))
    return rows[:limit]


SITE = "https://bernardhaddad.github.io/sift/"
FEED_ITEMS = 50


_XML_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # not allowed anywhere in XML 1.0


def _xml(text) -> str:
    return (
        _XML_CONTROL.sub("", str(text if text is not None else ""))
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _rfc822(stamp: str | None) -> str:
    from email.utils import format_datetime

    try:
        return format_datetime(datetime.fromisoformat(stamp))
    except (TypeError, ValueError):
        return ""


EXIT_HEADLINE = "Linked wallets sold half"  # an exit warning's headline, as in channel posts


def feed_xml(doc: dict, book: dict | None, registry: dict | None, suppressed: dict | None = None) -> str:
    """RSS 2.0 alerts, newest first: tokens that entered the track record (`book`, the ledger) as
    high or watch (titled with the label of the verdict they were published with), exit warnings
    on those tokens (ledger.exit_warnings), titled EXIT_HEADLINE, and funder wallets the moment
    they are seen behind a second token. Built from the same published files, so it never says
    more than the page does. Names are safe_name()s. The items of a token over 30 days old at its
    verdict (older_token, its age read from the ledger entry, or from its radar row when the entry
    has none) carry OLDER_TOKEN and no grade word; so does a re-grade traced after that age. A token's
    items link to its permanent page, tagged as a visit from the feed (t/<mint>.html?s=rss).

    Nothing withdrawn (`suppressed`, suppression()) is in it: a withdrawn token has no item and is
    left out of the tokens a wallet item names, and a withdrawn wallet has no item (an item about that
    wallet alone would say nothing once its address is gone); no other item names a wallet."""
    rows = {t["mint"]: t for t in doc.get("tokens") or []}
    gone_tokens, gone_wallets = withdrawn_tokens(suppressed), withdrawn_wallets(suppressed)
    items = []
    entries = (book or {}).get("entries")
    for e in entries.values() if isinstance(entries, dict) else []:
        if not isinstance(e, dict) or not e.get("mint"):
            continue
        if e.get("grade") not in ("high", "watch") or e.get("out_of_scope") or e["mint"] in gone_tokens:
            continue
        mint = e["mint"]
        t = rows.get(mint) or {}
        name = safe_name(e.get("name"), mint)
        aged = {**e, "trading_since": e.get("trading_since") or t.get("trading_since")}
        older = older_token(aged)
        # the title keeps the verdict it entered with; the numbers are today's, so a re-grade is said
        regraded = t.get("grade") != e["grade"]
        changed = "now " if regraded and not older else ""
        numbers = (
            f"largest linked group {changed}{_num(t.get('cluster_pct')):.1f}% of supply, "
            f"{_num(t.get('clustered_pct')):.1f}% across all groups."
            if t
            else "no longer on the radar page."
        )
        if older:  # no grade word: neither the one it entered with nor a re-grade since
            title = f"{name} | {OLDER_TOKEN}"
            aged_text = (
                f"{OLDER_TOKEN}. {name} ({mint}) had traded for over {ESTABLISHED_DAYS} days at the "
                f"verdict, since {established(aged)}."
            )
            detail = f"{aged_text} {numbers[0].upper()}{numbers[1:]}" if t else f"{aged_text} It is {numbers}"
        else:
            title = f"{grade_label(e)}: {name}"
            regrade = f"re-graded {grade_label(t)}" if not older_token(t) else "re-graded"  # traced older
            since = f"since {regrade}; " if t and regraded else ""
            reason = weak_link(t) if t and not regraded else ""
            why = f" {reason[0].upper()}{reason[1:]}." if reason else ""
            detail = f"{name} ({mint}): {since}{numbers}{why}"
        link = f"{SITE}t/{mint}.html?s=rss"
        items.append((e.get("entered_at") or "", title, link, detail, f"verdict-{mint}"))
        note = f"{OLDER_TOKEN}. " if older else ""
        w = e.get("exit_warning")
        if isinstance(w, dict) and w.get("at") and w.get("first_pct"):
            held, first = float(w.get("held_pct") or 0), float(w["first_pct"])
            drop = (1 - held / first) * 100 if first else 0
            items.append(
                (
                    w["at"],
                    f"{EXIT_HEADLINE}: {name}",
                    link,
                    f"{note}{name} ({mint}): the linked wallets among its 20 largest holders "
                    f"now hold {held:.1f}% of supply, against {first:.1f}% when the verdict was "
                    f"published ({drop:.0f}% less). A wallet that left the 20 largest may still hold "
                    "a little.",
                    f"exit-{mint}",
                )
            )
        w5 = e.get("exit_warning_5m")
        if isinstance(w5, dict) and w5.get("at") and w5.get("first_pct"):
            held, first = float(w5.get("held_pct") or 0), float(w5["first_pct"])
            items.append(
                (
                    w5["at"],
                    f"{EXIT_HEADLINE} (5-minute watch): {name}",
                    link,
                    f"{note}{name} ({mint}): on the 5-minute watch, the linked wallets now hold "
                    f"{held:.1f}% of supply, under half of the {first:.1f}% when the verdict was published.",
                    f"exit5-{mint}",
                )
            )
    wallets = (registry or {}).get("wallets")
    for a, w in wallets.items() if isinstance(wallets, dict) else []:
        recs = w.get("tokens") if isinstance(w, dict) else None
        if a in gone_wallets:
            continue
        toks = sorted(
            (
                (m, r)
                for m, r in (recs if isinstance(recs, dict) else {}).items()
                if isinstance(r, dict) and m not in gone_tokens
            ),
            key=lambda kv: kv[1].get("first_seen") or "",
        )
        if len(toks) < 2:
            continue
        names = ", ".join(safe_name(r.get("name"), m) for m, r in toks)
        shared = " Flagged as a possible shared service (bot, relayer or exchange)." if w.get("flags") else ""
        items.append(
            (
                toks[1][1].get("first_seen") or "",
                f"Wallet {a[:4]}…{a[-4:]} linked top holders of {len(toks)} tokens",
                f"https://solscan.io/account/{a}",
                f"{a} funded or supplied top holders of {names}.{shared}",
                f"wallet-{a}-{len(toks)}",
            )
        )
    items.sort(key=lambda i: i[0], reverse=True)
    body = "".join(
        f"<item><title>{_xml(t)}</title><link>{_xml(link)}</link><description>{_xml(d)}</description>"
        f'<guid isPermaLink="false">{_xml(g)}</guid><pubDate>{_rfc822(at)}</pubDate></item>'
        for at, t, link, d, g in items[:FEED_ITEMS]
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        "<title>Sift Insider Radar alerts</title>"
        f"<link>{SITE}radar.html</link>"
        "<description>Solana tokens whose top holders share a private funder, exit warnings when those "
        "linked wallets sell, and wallets seen behind several tokens. Evidence and proof transactions on "
        "the page. Not financial advice.</description>"
        f"<lastBuildDate>{_rfc822(doc.get('generated_at'))}</lastBuildDate>{body}</channel></rss>"
    )


# The visit counter's beacon (sift relay/worker.js, POST /hit; site/privacy.html, "Visit counts"), the
# same on every page: the page, the link's ?s= tag and the referring host, sent once when the page opens
# on the site's own address. A token page (token_page) sends it as page "t", with the site that linked to
# it; the token's radar row opened from it (its "Open it on the Insider Radar" link) sends none, the rule
# that kept a token link counted once when token pages still forwarded to that row. site/404.html sends
# its own path instead of the address it is shown at (tests/test_counter_beacon.py).
BEACON = """<script>
try {
  if (navigator.sendBeacon && location.origin === "https://bernardhaddad.github.io") {
    let r = "", forwarded = false;
    try {
      const u = new URL(document.referrer), token = "/sift/t/" + location.hash.slice(1);
      r = u.hostname;
      forwarded = u.origin === location.origin && location.hash.length > 1
        && [token + ".html", token].includes(u.pathname);
    } catch (e) {}
    const s = new URLSearchParams(location.search).get("s") || "";
    const visit = JSON.stringify({ p: location.pathname, s, r });
    if (!forwarded) navigator.sendBeacon("https://sift-trace.pages.dev/hit", visit);
  }
} catch (e) {}
</script>"""


# --- permanent token pages, the sitemap and the private suppression list (money plan rank 1, BUILD 8, 9) --
#
# A token page (t/<mint>.html) is built from what does not move: the token's ledger entry, of which it
# reads the milestones only, the evidence frozen at its entry (radar-evidence/<mint>.json, written once)
# and its hand review. So the hourly build leaves it byte for byte as it was until a milestone happens,
# and writes it to the site only then (scripts/build_radar.py).

WITHDRAWN = "withdrawn"  # what a withdrawn wallet reads on a page, with no link
EVIDENCE_NOT_SAVED = "Evidence not saved for this verdict."
NOT_A_CLAIM = "A linked group is not a claim that anyone broke a law."
OBJECTIONS = "privacy.html#objections"  # the objection notice (site/privacy.html)
REPLY_DAYS = 7
NOT_ADVICE = "Not advice."
OLDER_METHOD = "traced with an older method"  # the words of memetrader.evidence.OLDER_METHOD
HISTORY_URL = "https://github.com/bernardhaddad/sift/commits/main/radar-ledger.json"  # every hourly copy
VOID_NOTE = (
    "Void: the price readings for this entry were interrupted for over 6 hours, so it is left out of every "
    "score."
)
OUT_OF_SCOPE = (
    "Outside the radar's scope: stablecoins, wrapped and staking tokens, tokenized stocks and large "
    "protocol tokens are taken out of it by exact address (the list began on 23 Oct 2026 and grows as "
    "such tokens appear). The entry stays in the track record, left out of its scores."
)
MAIN_PAGES = (  # (path under SITE, changefreq): the site's own pages, first in the sitemap
    ("", "hourly"),
    ("radar.html", "hourly"),
    ("research.html", "weekly"),
    ("privacy.html", "weekly"),
    ("terms.html", "weekly"),
)
_TX = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{64,90}$")  # a transaction signature (radar.html's SIG)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _address(a) -> bool:
    return isinstance(a, str) and valid_mint(a)


def _instant(text) -> datetime | None:
    """A stored stamp or date as an aware UTC datetime; None when it cannot be read."""
    if not isinstance(text, str) or not text.strip():
        return None
    at = _when(text.strip().replace("Z", "+00:00"))
    return at.astimezone(UTC) if at else None


def _day_words(text) -> str:
    """'10 Nov 2026' for a stored stamp; '' when it cannot be read (fixed month names, no locale)."""
    at = _instant(text)
    return f"{at.day} {_MONTHS[at.month - 1]} {at.year}" if at else ""


def _utc(text) -> str:
    """'10 Nov 2026 08:00 UTC' for a stored stamp; '' when it cannot be read."""
    at = _instant(text)
    return f"{_day_words(text)} {at:%H:%M} UTC" if at else ""


def suppression(doc) -> dict:
    """The private suppression list (site/radar-suppress.json, never published) as the build applies it:
    {"tokens": {mint: day}, "wallets": {address: day}}, each day the UTC date (YYYY-MM-DD) of the row's
    "since". `doc` is the file's JSON, {"tokens": [{"mint", "since", "reason"}], "wallets": [{"address",
    "since"}]}, where a "reason" is the owner's own note and is never read here. None (no file) is an
    empty list; anything else that is not that shape raises ValueError, so a damaged list is never
    applied as a shorter one."""
    out: dict = {"tokens": {}, "wallets": {}}
    if doc is None:
        return out
    if not isinstance(doc, dict):
        raise ValueError("the suppression list is not an object")
    for kind, key in (("tokens", "mint"), ("wallets", "address")):
        rows = doc.get(kind, [])
        if not isinstance(rows, list):
            raise ValueError(f"{kind} is not a list")
        for i, r in enumerate(rows):
            address = r.get(key) if isinstance(r, dict) else None
            if not _address(address):
                raise ValueError(f"{kind}[{i}] has no valid {key}")
            at = _instant(r.get("since"))
            if at is None:
                raise ValueError(f"{kind}[{i}] has no readable since date")
            out[kind][address] = at.date().isoformat()
    return out


def withdrawn_tokens(suppressed) -> dict:
    """mint -> the day its page was withdrawn (suppression()); empty for None."""
    found = suppressed.get("tokens") if isinstance(suppressed, dict) else None
    return found if isinstance(found, dict) else {}


def withdrawn_wallets(suppressed) -> dict:
    """address -> the day it was withdrawn (suppression()); empty for None."""
    found = suppressed.get("wallets") if isinstance(suppressed, dict) else None
    return found if isinstance(found, dict) else {}


def _gone(a, wallets) -> bool:
    return isinstance(a, str) and a in wallets


def _named(t: dict) -> set:
    """Every address a radar row names: its groups' holders, links, funders, busy and known wallets, and
    the holders of its mint and freeze authority."""
    found: set = set()
    for c in t.get("clusters") or []:
        if not isinstance(c, dict):
            continue
        found.update(w.get("address") for w in c.get("wallets") or [] if isinstance(w, dict))
        for k in c.get("links") or []:
            if isinstance(k, dict):
                found.update((k.get("from"), k.get("to")))
        for key in ("funders", "busy", "known"):
            found.update(c.get(key) or [])
    auth = t.get("authority")
    if isinstance(auth, dict):
        found.update((auth.get("mint"), auth.get("freeze")))
    return {a for a in found if isinstance(a, str)}


def withdraw_row(t: dict, wallets) -> dict:
    """The radar row `t` (entry()) without the withdrawn wallets (addresses): they leave each group's
    holders, links, funders, busy and known lists, and an authority one of them holds takes the row's
    authority with it. The numbers stay as traced: a group's holders and share still count a withdrawn
    holder. `t` itself when it names none of them."""
    if not wallets or not (_named(t) & set(wallets)):
        return t
    groups = []
    for c in t.get("clusters") or []:
        if not isinstance(c, dict):
            groups.append(c)
            continue
        c = dict(c)
        if isinstance(c.get("wallets"), list):
            c["wallets"] = [
                w for w in c["wallets"] if not (isinstance(w, dict) and _gone(w.get("address"), wallets))
            ]
        if isinstance(c.get("links"), list):
            kept = [
                k
                for k in c["links"]
                if not (
                    isinstance(k, dict) and (_gone(k.get("from"), wallets) or _gone(k.get("to"), wallets))
                )
            ]
            if "links_total" in c:
                c["links_total"] = max(len(kept), int(_num(c["links_total"])) - (len(c["links"]) - len(kept)))
            c["links"] = kept
        for key in ("funders", "busy", "known"):
            if isinstance(c.get(key), list):
                c[key] = [a for a in c[key] if not _gone(a, wallets)]
        groups.append(c)
    out = {**t, "clusters": groups}
    auth = t.get("authority")
    if isinstance(auth, dict) and (_gone(auth.get("mint"), wallets) or _gone(auth.get("freeze"), wallets)):
        # the risk stays on the page ("can mint", "can freeze"): only the withdrawn address goes
        out["authority"] = {**auth, **{k: None for k in ("mint", "freeze") if _gone(auth.get(k), wallets)}}
    return out


def withdraw_rows(tokens: list, suppressed) -> list:
    """radar.json's token rows as published: withdrawn tokens left out, withdrawn wallets taken out of
    the others (withdraw_row). `tokens` itself when nothing is withdrawn."""
    mints, wallets = withdrawn_tokens(suppressed), withdrawn_wallets(suppressed)
    if not (mints or wallets):
        return tokens
    return [withdraw_row(t, wallets) for t in tokens if t.get("mint") not in mints]


def withdraw_registry(registry, suppressed):
    """radar-wallets.json as published (update_registry): a withdrawn wallet left out, a withdrawn token
    left out of every wallet's tokens, and a wallet left with no token dropped. `registry` itself when
    nothing is withdrawn."""
    mints, wallets = withdrawn_tokens(suppressed), withdrawn_wallets(suppressed)
    kept = registry.get("wallets") if isinstance(registry, dict) else None
    if not (mints or wallets) or not isinstance(kept, dict):
        return registry
    out = {}
    for a, w in kept.items():
        if a in wallets:
            continue
        if mints and isinstance(w, dict) and isinstance(w.get("tokens"), dict):
            toks = {m: r for m, r in w["tokens"].items() if m not in mints}
            if not toks:
                continue
            w = {**w, "tokens": toks}
        out[a] = w
    return {**registry, "wallets": out}


def without_withdrawn(items: list, suppressed) -> list:
    """The records of `items` (dicts with a "mint", such as the recent exit warnings) whose token is not
    withdrawn; `items` itself when none is."""
    mints = withdrawn_tokens(suppressed)
    if not mints:
        return items
    return [r for r in items if not (isinstance(r, dict) and r.get("mint") in mints)]


def frozen_groups(ev) -> list | None:
    """The linked groups of a token's evidence (memetrader.evidence) when it has a group section from the
    current trace method; None otherwise: no section, or an older method, whose groups are never shown."""
    if not isinstance(ev, dict) or not isinstance(ev.get("groups"), list):
        return None
    try:
        outdated = cluster.outdated({"method": ev.get("method")})
    except (TypeError, ValueError):
        outdated = True
    return None if outdated else [c for c in ev["groups"] if isinstance(c, dict)]


YOUNG_BANDS = ("under_1d", "1_7d", "7_30d")  # ledger.age_band's bands of 30 days or less


def token_indexable(entry, evidence, suppressed) -> bool:
    """Whether search engines may index a token's page (money plan rank 1, BUILD 8): only when its
    evidence exists with linked groups from the current method, its grade is High or Watch or it was
    warned on either clock, it is known to have traded for 30 days or less at entry (ledger.age_band in
    YOUNG_BANDS, from the entry's or the evidence's age as the page shows it; an unknown age is not
    indexed), it is in the radar's scope and it is not withdrawn. Every other page carries noindex."""
    if not (isinstance(entry, dict) and isinstance(evidence, dict)):
        return False
    mint = entry.get("mint")
    if not _address(mint) or evidence.get("mint") != mint or mint in withdrawn_tokens(suppressed):
        return False
    if entry.get("out_of_scope") or not frozen_groups(evidence):
        return False
    warned = bool(entry.get("exit_warning") or entry.get("exit_warning_5m"))
    if grade_code(entry) not in ("high", "watch") and not warned:
        return False
    since = entry.get("trading_since") or evidence.get("trading_since")  # the age token_page shows
    band = ledger.age_band({"trading_since": since, "entered_at": entry.get("entered_at")})
    return band in YOUNG_BANDS  # an unknown age is not known to be young


def page_lastmod(entry) -> str | None:
    """The sitemap date of a token page (YYYY-MM-DD, UTC): that of the entry's last milestone (its 24-hour
    and 7-day prices, its first hourly prices 50% and 80% down, its exit warnings, the hour it closed as
    vanished), or the day it entered when it has none. None when none of them can be read."""
    if not isinstance(entry, dict):
        return None
    stamps = [entry.get("entered_at")]
    for key in ("p24", "p7d", "exit_warning", "exit_warning_5m", "dd50", "dd80"):
        rec = entry.get(key)
        if isinstance(rec, dict):
            stamps.append(rec.get("at") or rec.get("before"))
    if entry.get("vanished"):
        stamps.append(entry.get("asked_at"))  # never moved again once the entry closed
    times = [at for at in map(_instant, stamps) if at is not None]
    return max(times).date().isoformat() if times else None


def sitemap_xml(pages: dict) -> str:
    """pub/sitemap.xml: the site's own pages (MAIN_PAGES), then every indexable token page by mint, with
    its lastmod (`pages`: mint -> page_lastmod, None for none)."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        *(
            f"  <url><loc>{SITE}{path}</loc><changefreq>{freq}</changefreq></url>"
            for path, freq in MAIN_PAGES
        ),
    ]
    for mint in sorted(pages):
        lastmod = f"<lastmod>{_xml(pages[mint])}</lastmod>" if pages[mint] else ""
        lines.append(f"  <url><loc>{_xml(f'{SITE}t/{mint}.html')}</loc>{lastmod}</url>")
    return "\n".join([*lines, "</urlset>"]) + "\n"


def _price_of(v) -> float | None:
    """A price as recorded: a finite number above zero, else None (never a guess)."""
    if isinstance(v, bool):
        return None
    try:
        p = float(v)
    except (TypeError, ValueError):
        return None
    return p if math.isfinite(p) and p > 0 else None


def _usd(p: float) -> str:
    """A USD price with 4 significant digits and no exponent: $0.00001234, $0.5, $12.35."""
    if p >= 1000:
        return f"${p:,.0f}"
    if p >= 1:
        return f"${p:.4g}"
    digits = min(20, 3 - math.floor(math.log10(p)))
    return "$" + f"{p:.{digits}f}".rstrip("0").rstrip(".")


def _against(price, p0) -> str | None:
    """A recorded price and how far it is from the verdict price p0, in whole percent (as the channel
    posts say it); None when either is missing. Never "100% below": a price still quoted is above zero."""
    now, then = _price_of(price), _price_of(p0)
    if now is None or then is None:
        return None
    change = now / then - 1
    n = int(abs(change) * 100 + 0.5)
    if n == 0:
        return f"{_usd(now)}, within 1% of the verdict price"
    if change < 0 and n >= 100:
        return f"{_usd(now)}, over 99% below the verdict price"
    return f"{_usd(now)}, {n}% {'below' if change < 0 else 'above'} the verdict price"


def _after(rec: dict, entered) -> str:
    """', 24.3 hours after the verdict', from the record's own hours, else from its time and entered_at."""
    hours = rec.get("hours")
    if isinstance(hours, bool) or not isinstance(hours, int | float) or not math.isfinite(hours):
        at, start = _instant(rec.get("at")), _instant(entered)
        hours = (at - start).total_seconds() / 3600 if at and start else None
    return f", {hours:.1f} hours after the verdict" if hours is not None else ""


def _pct(v) -> str:
    return f"{_num(v):.1f}%"


def _check_price(w: dict, p0) -> str:
    """The price at a warning's own check against p0. An hourly warning keeps the last price the ledger
    held (price, read at price_at): an earlier hour's when this hour's went unanswered, which is not
    this check's price, so it reads as not read."""
    at = w.get("price_at")
    found = _against(w.get("price"), p0) if at is None or at == w.get("at") else None
    return f"Price at this check: {found}." if found else "Price not read at this check."


def _left_top(w: dict) -> str:
    n = w.get("out_of_top")
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        return ""
    if n == 1:
        return " 1 linked wallet was no longer among the 20 largest, so what it still held is not counted."
    return f" {n} linked wallets were no longer among the 20 largest, so what they still held is not counted."


def _timeline(e: dict) -> list:
    """The entry's milestones as HTML, oldest first: the verdict price, the first hourly prices at or
    under half and a fifth of it, the 24-hour and 7-day prices, each exit warning with its price, and
    vanished. Nothing that moves every hour (last_price, min_price, the lowest price after a warning)
    is read."""
    p0, entered = e.get("p0"), e.get("entered_at")
    start = _price_of(p0)
    out = [
        (
            _instant(entered),
            f"<b>{_utc(entered)}</b>: entered the track record"
            + (
                f" at {_usd(start)}, the verdict price." if start else "; its verdict price was not recorded."
            ),
        )
    ]
    for key, words, line in (
        ("dd50", "half the verdict price (down 50% or more)", 50),
        ("dd80", "a fifth of the verdict price (down 80% or more)", 80),
    ):
        d = e.get(key)
        if not isinstance(d, dict):
            continue
        if _instant(d.get("at")):
            first = f"first hourly price at or below {words}."
            out.append((_instant(d["at"]), f"<b>{_utc(d['at'])}</b>{_after(d, entered)}: {first}"))
        elif _instant(d.get("before")):
            known = (
                f"its hourly price had already been down {line}% or more from the verdict price when these "
                "times were first recorded; the hour is not known."
            )
            out.append((_instant(d["before"]), f"<b>Before {_utc(d['before'])}</b>: {known}"))
    for key, words in (("p24", "24 hours"), ("p7d", "7 days")):
        p = e.get(key)
        if isinstance(p, dict) and _instant(p.get("at")):
            price = _against(p.get("price"), p0) or "not recorded"
            out.append(
                (
                    _instant(p["at"]),
                    f"<b>{_utc(p['at'])}</b>{_after(p, entered)}: price after {words} {price}.",
                )
            )
    w5 = e.get("exit_warning_5m")
    if isinstance(w5, dict) and _instant(w5.get("at")):
        out.append(
            (
                _instant(w5["at"]),
                f"<b>{_utc(w5['at'])}</b>{_after(w5, entered)}: exit warning on the 5-minute watch. "
                "Among the accounts that were its 20 largest at the last hourly check, the linked wallets "
                f"held {_pct(w5.get('held_pct'))} of supply, under half of the {_pct(w5.get('first_pct'))} "
                f"read when the verdict was published. {_check_price(w5, p0)}",
            )
        )
    w = e.get("exit_warning")
    if isinstance(w, dict) and _instant(w.get("at")):
        out.append(
            (
                _instant(w["at"]),
                f"<b>{_utc(w['at'])}</b>{_after(w, entered)}: exit warning on the hourly check. Among its 20 "
                f"largest accounts, the linked wallets held {_pct(w.get('held_pct'))} of supply, under half "
                f"of the {_pct(w.get('first_pct'))} read when the verdict was published.{_left_top(w)} "
                f"{_check_price(w, p0)}",
            )
        )
    if e.get("vanished"):
        scored = not (e.get("void") or e.get("out_of_scope"))
        gone = "no price was quoted for it for its last 24 hours within the 7 days" + (
            ", which counts as a collapse." if scored else "."
        )
        out.append((None, f"<b>Vanished</b>: {gone}"))
    last = datetime.max.replace(tzinfo=UTC)
    return [text for _, text in sorted(out, key=lambda item: (item[0] is None, item[0] or last))]


def _short(a: str) -> str:
    return f"{a[:4]}…{a[-4:]}"


def _acct(a, wallets) -> str:
    """A wallet in a page's text: shortened, linked to Solscan by its full address; WITHDRAWN, with no
    link, when it is withdrawn; escaped text with no link when it is not an address."""
    if _gone(a, wallets):
        return WITHDRAWN
    if not _address(a):
        return _xml(a) if isinstance(a, str) and a else "unknown"
    return f'<a class="addr" href="https://solscan.io/account/{a}">{_short(a)}</a>'


def _mask(text: str, wallets) -> str:
    for a in wallets:
        text = text.replace(a, WITHDRAWN)
    return text


def _group_html(i: int, c: dict, wallets) -> str:
    """One linked group as static text: its holders and their shares, every link with its proof
    transaction, and the notes on busy and repeat wallets (as radar.html's groupHtml shows them)."""
    holders = _num(c.get("holders")) or len(c.get("wallets") or [])
    parts = [f"<h3>Group {i}: {int(holders)} holders, {_num(c.get('pct')):.1f}% of supply</h3>"]
    shown = [w for w in c.get("wallets") or [] if isinstance(w, dict)]
    if shown:
        items = "".join(
            f"<li>{_acct(w.get('address'), wallets)}"
            + (
                ""
                if isinstance(w.get("pct"), bool) or not isinstance(w.get("pct"), int | float)
                else f" {_pct(w['pct'])}"
            )
            + "</li>"
            for w in shown
        )
        parts.append(f'<p class="sub">Holders</p><ul class="hold">{items}</ul>')
    links = [k for k in c.get("links") or [] if isinstance(k, dict)]
    items = []
    for k in links:
        a, b, tx = k.get("from"), k.get("to"), k.get("tx")
        verb = (
            "sent the first tokens to" if k.get("kind") == "tokens" else "paid for the first transaction of"
        )
        if _gone(a, wallets) or _gone(b, wallets):
            proof = ""  # its transaction would name the withdrawn wallet
        elif isinstance(tx, str) and _TX.match(tx):
            proof = f' <a class="proof" href="https://solscan.io/tx/{tx}">proof</a>'
        else:
            proof = " (no proof transaction saved)"
        items.append(f"<li>{_acct(a, wallets)} {verb} {_acct(b, wallets)}{proof}</li>")
    more = int(_num(c.get("links_total"))) - len(links)
    if more > 0:
        items.append(f"<li>and {more} more links</li>")
    funders = [a for a in c.get("funders") or [] if isinstance(a, str)]
    if not links and funders:
        items.append(f"<li>Linked through {', '.join(_acct(a, wallets) for a in funders)}</li>")
    if items:
        parts.append(f'<p class="sub">How they are linked</p><ul class="ev">{"".join(items)}</ul>')
    busy = [a for a in c.get("busy") or [] if isinstance(a, str)]
    if busy:
        via = ", ".join(_acct(a, wallets) for a in busy)
        parts.append(
            f'<p class="note">Linked through a high-volume wallet ({via}), possibly an exchange or a bot: '
            "weaker evidence of a single owner.</p>"
        )
    known = [a for a in c.get("known") or [] if isinstance(a, str)]
    if known:
        via = ", ".join(_acct(a, wallets) for a in known)
        parts.append(
            f'<p class="note">Linked through a wallet seen behind top holders of several tokens ({via}): a '
            "shared service, or one deployer behind several tokens.</p>"
        )
    return f'<div class="group">{"".join(parts)}</div>'


def _review_html(review: dict, wallets) -> str:
    """The token's hand review (a case of site/radar-review.json), as text."""
    kinds = {"young": "Young token", "older": "Older token"}
    found = []
    if isinstance(review.get("links"), str) and review["links"]:
        found.append(f"Links: {_xml(_mask(review['links'], wallets))}.")
    finding = review.get("finding")
    if isinstance(finding, str) and finding:
        found.append(f"{_xml(kinds.get(finding, finding))}.")
    note = review.get("note")
    text = f"<p>{' '.join(found)}</p>" if found else ""
    if isinstance(note, str) and note:
        text += f"<p>{_xml(_mask(note, wallets))}</p>"
    if not text:
        return ""
    return (
        '<h2>Hand review</h2><p class="muted">Checked a second time by hand, apart from the tracer: every '
        "link re-read on the chain, the token's age, and how active each linking wallet is.</p>" + text
    )


_ICON = (  # radar.html's own icon
    '<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' '
    "viewBox='0 0 16 16'%3E%3Crect width='16' height='16' fill='%2313294b'/%3E%3Crect x='3' y='11' "
    "width='10' height='2' fill='%23f2b632'/%3E%3C/svg%3E\">"
)
_STYLE = (  # the layout; ../sift.css, loaded after it, brings the site's fonts and colours
    "<style>body{margin:0;font:16px/1.6 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif;"
    "color:#17213a;background:#fff}a{color:#1f4e9c}.wrap{max-width:780px;margin:0 auto;padding:0 16px}"
    ".bar .wrap{display:flex;align-items:center;gap:22px;height:56px}.brand{font-weight:700;font-size:22px;"
    "text-decoration:none}.links{display:flex;gap:12px}.links a{text-decoration:none;font-size:15px}"
    "main.wrap{padding-top:28px;padding-bottom:8px}h1{font-size:30px;line-height:1.2;margin:0 0 6px;"
    "overflow-wrap:anywhere}h2{font-size:20px;margin:30px 0 8px}h3{font-size:16px;margin:0 0 8px}"
    ".mint{font:13px/1.5 monospace;color:#4f5869;overflow-wrap:anywhere;margin:0 0 16px}"
    ".verdict{display:inline-block;font-weight:600;padding:2px 9px;border-radius:4px;background:#eceef1;"
    "color:#373c43}.group{border:1px solid #dfe2e7;border-radius:4px;padding:14px 16px;margin:12px 0}"
    ".sub{font-size:13px;font-weight:600;color:#4f5869;margin:8px 0 2px}ul{padding-left:20px;margin:4px 0}"
    "li{margin:3px 0;overflow-wrap:anywhere}.addr{font:13.5px monospace}.note{background:#fbf2dc;"
    "color:#5f4510;border-radius:4px;padding:8px 10px}.muted{color:#4f5869;font-size:14.5px}"
    "code{font:14px monospace;overflow-wrap:anywhere}footer .wrap{text-align:center}</style>"
)
_NAV = (
    '<header class="bar"><div class="wrap"><a class="brand" href="../" aria-label="Sift home">'
    '<span>sift</span></a><nav class="links"><a href="../">Screener</a><a href="../radar.html">Insider '
    'Radar</a><a href="../research.html">Research</a></nav></div></header>'
)
_FOOT = (
    '<footer><div class="wrap"><a href="../">Screener</a> | <a href="../radar.html">Insider Radar</a> | '
    '<a href="../research.html">Research</a> | <a href="../privacy.html">Privacy</a> | '
    '<a href="../terms.html">Terms</a></div></footer>'
)


def _page(mint: str, title: str, desc: str, body: str, *, index: bool, image=None) -> str:
    """A whole page at t/<mint>.html: the link-preview tags (with the card t/<mint>.png?v=<image> when
    `image` is a card version), noindex unless `index`, the visit counter (BEACON) and `body`."""
    url = f"{SITE}t/{mint}.html"
    card = ""
    if image is not None:
        card = (
            f'<meta property="og:image" content="{_xml(f"{SITE}t/{mint}.png?v={image}")}">'
            '<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">'
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_xml(title)}</title>"
        f'<meta name="description" content="{_xml(desc)}">'
        + ("" if index else '<meta name="robots" content="noindex">')
        + f'<link rel="canonical" href="{_xml(url)}">'
        '<meta property="og:type" content="website"><meta property="og:site_name" content="Sift">'
        f'<meta property="og:title" content="{_xml(title)}">'
        f'<meta property="og:description" content="{_xml(desc)}">'
        f'<meta property="og:url" content="{_xml(url)}">'
        f'<meta name="twitter:card" content="{"summary" if image is None else "summary_large_image"}">'
        f'<meta name="twitter:title" content="{_xml(title)}">'
        f'<meta name="twitter:description" content="{_xml(desc)}">'
        f'{card}{_ICON}{_STYLE}<link rel="stylesheet" href="../sift.css">{BEACON}</head>'
        f'<body>{_NAV}<main class="wrap">{body}</main>{_FOOT}</body></html>'
    )


def withdrawn_page(mint: str, since) -> str:
    """The page of a withdrawn token: only a dated notice, noindex, with no name and no evidence."""
    day = _day_words(since)
    notice = "This page was withdrawn after a correction request" + (f" on {day}." if day else ".")
    body = f"<h1>Page withdrawn</h1><p>{notice}</p>"
    return _page(str(mint), "Page withdrawn | Sift Insider Radar", notice, body, index=False)


def token_page(entry, evidence, review, *, suppressed=None, row=None, image=None) -> str:
    """The permanent page of one token, t/<mint>.html (money plan rank 1, BUILD 8), from its ledger entry
    (None for a radar token without one yet), its evidence frozen at entry (memetrader.evidence; None when
    it has none), its hand review (a case of site/radar-review.json, or None), the private suppression list
    (suppression()) and its radar row now (`row`, read only when there is no evidence). `image` is the
    version of its published card t/<mint>.png (True: CARD_VERSION), None when it has none.

    It shows the factual label at entry (OLDER_TOKEN and no grade word instead, for a token over 30 days
    old at the verdict with a linked group: older_note), when the trace was taken, the linked groups with
    a Solscan link for every wallet and proof transaction, the ledger's milestones only (each exit warning
    with its UTC time and its price against the verdict price), the hand review, the verify.py command,
    NOT_A_CLAIM, the way to object or correct (OBJECTIONS, a reply within REPLY_DAYS days), NOT_ADVICE and
    the visit counter (BEACON). No trade link and no price that moves: two ledger states that differ only
    in what each hourly sample changes give the same page, byte for byte. Evidence without groups shows
    its no_groups_reason instead; with no evidence, the token's radar row now when it is on the radar,
    else EVIDENCE_NOT_SAVED. A withdrawn token's page is withdrawn_page(); a withdrawn wallet reads
    WITHDRAWN, with no link and no proof link. Only a token_indexable() page leaves out noindex. Names are
    safe_name()s and everything is escaped."""
    e = entry if isinstance(entry, dict) else None
    r = row if isinstance(row, dict) else None
    mint = str((e or r or (evidence if isinstance(evidence, dict) else None) or {}).get("mint") or "")
    if not mint:  # never a page with no address (a withdrawn token without an entry: withdrawn_page)
        raise ValueError("a token page needs its mint: an entry, a row or evidence")
    tokens = withdrawn_tokens(suppressed)
    if mint in tokens:
        return withdrawn_page(mint, tokens[mint])
    wallets = withdrawn_wallets(suppressed)
    ev = evidence if e is not None and isinstance(evidence, dict) and evidence.get("mint") == mint else None
    name = safe_name((e or {}).get("name") or (ev or {}).get("name") or (r or {}).get("name"), mint)
    g = grade_code(e if e is not None else (r or {}))
    if ev is not None:  # what the verdict rested on, frozen at entry
        groups = frozen_groups(ev)
        reason = ev.get("no_groups_reason") if isinstance(ev.get("no_groups_reason"), str) else None
        if groups is None and not reason:
            reason = OLDER_METHOD if isinstance(ev.get("groups"), list) else "no group section was saved"
        traced, numbers = ev.get("traced_at"), ev
    elif r is not None:  # the token's trace on the radar now
        groups = [c for c in r.get("clusters") or [] if isinstance(c, dict)]
        reason, traced, numbers = None, r.get("traced_at"), r
    else:
        groups, reason, traced, numbers = None, None, None, {}
    if e is not None:  # what the verdict knew: the live row would flip the page as it comes and goes
        since = e.get("trading_since") or (ev or {}).get("trading_since")
    else:
        since = (r or {}).get("trading_since")
    view = {
        "grade": g,
        "trading_since": since,
        "entered_at": (e or {}).get("entered_at"),
        "traced_at": traced if e is None else None,
        "clusters": groups or [],
    }
    older = older_note(view)
    weak = weak_link({"grade": g, "clusters": groups or []})
    label = grade_label({"grade": g})
    if groups:
        total = numbers.get("clustered_pct")
        total = _num(total) if isinstance(total, int | float) else sum(_num(c.get("pct")) for c in groups)
        held = sum(int(_num(c.get("holders"))) for c in groups)
        largest = max(_num(c.get("pct")) for c in groups)
        found = (
            f"Largest linked group {largest:.1f}% of supply, all linked groups {total:.1f}% ({held} top "
            "holders linked). Wallets and proof transactions on the page."
        )
    elif groups is not None:
        found = "No two top holders lead back to the same private wallet."
        found = GRADES["incomplete"][2] if g == "incomplete" else found
    else:
        found = f"The linked groups are not shown: {reason}." if reason else EVIDENCE_NOT_SAVED
    day = _day_words(since)
    if older:
        title = f"{name} | {OLDER_TOKEN} | Sift Insider Radar"
        desc = f"{OLDER_TOKEN}. Trading since {day}{f'; {weak}' if weak else ''}. {found} {NOT_ADVICE}"
    else:
        title = f"{name}: {label} | Sift Insider Radar"
        on = _day_words(e.get("entered_at") if e is not None else traced)
        when = ("at the verdict" if e is not None else "as traced") + (f" on {on}" if on else "")
        desc = f"{label} {when}{f' ({weak})' if weak else ''}. {found} {NOT_ADVICE}"
    m = _xml(mint)
    body = [
        f"<h1>{_xml(name)}</h1>",
        f'<p class="mint">{m} · <a href="https://solscan.io/token/{m}">Solscan</a></p>',
        f'<p><span class="verdict">{_xml(OLDER_TOKEN if older else label)}</span></p>',  # it may wrap
    ]
    aged = f" Trading since {day}." if day and not older else ""
    taken = _utc((ev or {}).get("traced_at"))
    trace = f"; trace taken {taken}" if taken else "; the trace it rested on was not saved"
    if e is not None:
        entered = f"Entered the track record {_utc(e.get('entered_at'))}{trace}."
        old = f"Trading since {day}, more than {ESTABLISHED_DAYS} days before the verdict. " if older else ""
        body.append(f"<p>{old}{entered}{aged}</p>")
    else:
        old = f"Trading since {day}, more than {ESTABLISHED_DAYS} days before this trace. " if older else ""
        body.append(f"<p>{old}Traced {_utc(traced)}. Not in the track record yet.{aged}</p>")
    if not older:
        body.append(f'<p class="muted">{_xml(GRADES[g][2])}</p>')
    if weak:
        body.append(f'<p class="muted">{_xml(weak[0].upper() + weak[1:])}.</p>')
    if e is not None and e.get("out_of_scope"):
        body.append(f'<p class="note">{OUT_OF_SCOPE}</p>')
    if e is not None and e.get("void"):
        body.append(f'<p class="note">{VOID_NOTE}</p>')
    body.append("<h2>Linked groups</h2>")
    if ev is not None and groups is not None and isinstance(numbers.get("clustered_pct"), int | float):
        top = numbers.get("top_pct")
        among = "among the holders that could be traced, " if g == "incomplete" else ""
        body.append(
            f"<p>As traced for the verdict, {among}linked groups held "
            f"{_pct(numbers['clustered_pct'])} of supply"
            + (
                f"; the 20 largest accounts held {_pct(top)}.</p>"
                if isinstance(top, int | float)
                else ".</p>"
            )
        )
    elif ev is None and e is not None and r is not None:
        body.append(
            f"<p>{EVIDENCE_NOT_SAVED} The groups below are from the latest trace, taken {_utc(traced)}.</p>"
        )
    if groups:
        body += [_group_html(i, c, wallets) for i, c in enumerate(groups, 1)]
    else:
        body.append(f"<p>{_xml(found)}</p>")
    if e is not None:
        items = "".join(f"<li>{text}</li>" for text in _timeline(e))
        body.append(
            f'<h2>Track record</h2><ul class="tl">{items}</ul><p class="muted">Milestones only, added as '
            "they happen. The latest hourly price and the lowest within 7 days are in "
            '<a href="../radar-ledger.json">radar-ledger.json</a>; every hourly copy of it is in the <a '
            f'href="{HISTORY_URL}">public history</a>.</p>'
        )
    if isinstance(review, dict) and review.get("mint") in (None, mint):
        body.append(_review_html(review, wallets))
    body.append(
        "<h2>Check it yourself</h2><p>Each proof link opens its transaction on Solscan. To re-check the "
        "links on the Solana chain, download "
        '<a href="../verify.py">verify.py</a> (Python 3.11 or later, no key or packages) and run '
        f"<code>python3 verify.py {m}</code>: it checks the links the radar publishes now, or, once the "
        f'token has left the radar, those frozen at the verdict. <a href="../radar.html#{m}">Open it on '
        "the Insider Radar</a>.</p>"
        f"<p>{NOT_A_CLAIM}</p>"
        f'<p>To object or correct: <a href="../{OBJECTIONS}">the privacy page says how</a>. Sift replies '
        f"within {REPLY_DAYS} days.</p><p>{NOT_ADVICE}</p>"
    )
    version = CARD_VERSION if image is True else (None if image is None or image is False else image)
    return _page(mint, title, desc, "".join(body), index=token_indexable(e, ev, suppressed), image=version)


def audit_summary(audit: dict | None) -> dict | None:
    """What the page shows of the self-audit (scripts/radar_audit.py)."""
    if not isinstance(audit, dict) or not isinstance(audit.get("totals"), dict):
        return None
    last = audit.get("last")
    return {
        "since": audit.get("since"),
        "last_at": last.get("at") if isinstance(last, dict) else None,
        "totals": audit["totals"],
    }
