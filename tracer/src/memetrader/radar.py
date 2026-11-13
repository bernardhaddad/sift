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
CARD_VERSION = 2  # preview card look (cards.py); 2: grey badge, renamed labels, safe names


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
    if _LINK_LIKE.search(text) or _HANDLE.search(text):
        return fallback
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


def feed_xml(doc: dict, book: dict | None, registry: dict | None) -> str:
    """RSS 2.0 alerts, newest first: tokens that entered the track record (`book`, the ledger) as
    high or watch (titled with the label of the verdict they were published with), exit warnings
    on those tokens (ledger.exit_warnings), titled EXIT_HEADLINE, and funder wallets the moment
    they are seen behind a second token. Built from the same published files, so it never says
    more than the page does. Names are safe_name()s. The items of a token over 30 days old at its
    verdict (older_token, its age read from the ledger entry, or from its radar row when the entry
    has none) carry OLDER_TOKEN and no grade word; so does a re-grade traced after that age."""
    rows = {t["mint"]: t for t in doc.get("tokens") or []}
    items = []
    entries = (book or {}).get("entries")
    for e in entries.values() if isinstance(entries, dict) else []:
        if not isinstance(e, dict) or not e.get("mint"):
            continue
        if e.get("grade") not in ("high", "watch") or e.get("out_of_scope"):
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
        link = f"{SITE}radar.html#{mint}"
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
        toks = sorted(
            ((m, r) for m, r in (recs if isinstance(recs, dict) else {}).items() if isinstance(r, dict)),
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
# on the site's own address. A token page sends it before it forwards to the token's radar row, and that
# row then sends none: a token link counts once, as "t", with the site that linked to it. site/404.html
# sends its own path instead of the address it is shown at (tests/test_counter_beacon.py).
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


def _js(text) -> str:
    """A JavaScript string literal that cannot end the <script> element it sits in."""
    return json.dumps(str(text)).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def share_page(t: dict, image: bool = False) -> str:
    """A tiny page per token whose only job is a correct link preview (title, verdict, key numbers)
    when the link is pasted into X, Telegram or Discord. It forwards readers to the radar row and
    keeps the link's query string, so a source tag (?s=x) reaches the radar page; without
    JavaScript, a refresh inside <noscript> forwards them without it. With `image`, it points the
    preview at the card t/<mint>.png (memetrader.cards) published beside it, with the card version
    in the address, so a preview service that keeps images by address fetches a redrawn card once.
    The name is safe_name(). A token over 30 days old at the trace with a linked group (older_note)
    is previewed with OLDER_TOKEN in place of its grade, and no grade word. Before it forwards, it
    counts the visit (BEACON), as page "t" with the site that linked to it."""
    mint = t["mint"]
    name = safe_name(t.get("name"), mint)
    groups = t.get("clusters") or []
    held = sum(int(c.get("holders") or 0) for c in groups)
    reason = weak_link(t)
    if groups:
        found = (
            f"Largest linked group {float(t.get('cluster_pct') or 0):.1f}% of supply, "
            f"all linked groups {float(t.get('clustered_pct') or 0):.1f}% ({held} top holders linked). "
            "Wallets and proof txs are on the radar page."
        )
    elif grade_code(t) == "incomplete":  # no group found, but not every holder could be traced
        found = GRADES["incomplete"][2]
    else:
        found = "No two top holders lead back to the same private wallet."
    if older_note(t):
        head = f"{OLDER_TOKEN}. Trading since {established(t)}" + (f"; {reason}" if reason else "")
        desc = f"{head}. {found} Not financial advice."
        title = f"{name} | {OLDER_TOKEN} | Sift Insider Radar"
    else:
        label = grade_label(t)
        if groups:
            desc = f"{label}{f' ({reason})' if reason else ''}. {found} Not financial advice."
        else:
            desc = f"{label}: {found[0].lower()}{found[1:]} Not financial advice."
        title = f"{name}: {label} | Sift Insider Radar"
    target, fragment = "../radar.html", f"#{mint}"
    url = f"{SITE}t/{mint}.html"
    card = ""
    if image:
        card = (
            f'<meta property="og:image" content="{_xml(f"{SITE}t/{mint}.png?v={CARD_VERSION}")}">'
            '<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">'
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_xml(title)}</title>"
        f'<meta name="description" content="{_xml(desc)}">'
        f'<link rel="canonical" href="{_xml(url)}">'
        '<meta property="og:type" content="website"><meta property="og:site_name" content="Sift">'
        f'<meta property="og:title" content="{_xml(title)}">'
        f'<meta property="og:description" content="{_xml(desc)}">'
        f'<meta property="og:url" content="{_xml(url)}">'
        f'<meta name="twitter:card" content="{"summary_large_image" if image else "summary"}">'
        f'<meta name="twitter:title" content="{_xml(title)}">'
        f'<meta name="twitter:description" content="{_xml(desc)}">'
        f"{card}{BEACON}"
        f"<script>location.replace({_js(target)}+location.search+{_js(fragment)})</script>"
        f'<noscript><meta http-equiv="refresh" content="0; url={_xml(target + fragment)}"></noscript>'
        "<style>body{font:16px/1.6 -apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:50px auto;"
        "max-width:640px;padding:0 20px;color:#1c1e21;background:#fff}a{color:#1f5bb8}</style>"
        f"</head><body><p>{_xml(desc)}</p>"
        f'<p><a href="{_xml(target + fragment)}">Open the evidence on Sift Insider Radar</a></p>'
        "</body></html>"
    )


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
