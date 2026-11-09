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

import json
import pathlib
import re
from datetime import UTC, datetime, timedelta

from memetrader import cluster, filters

MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # base58, the length range of a Solana address
SCHEMA = 1  # radar.json format: raised when a field is renamed or removed, never for additions
MAX_WALLETS_SHOWN = 20
MAX_FUNDERS_SHOWN = 8

GRADES = {  # code -> (label, order on the page, one-line meaning)
    "high": (
        "High risk",
        0,
        "Top holders funded by the same private wallet hold over 10% of supply in one group, or over "
        "20% across groups. Sift's own paper books refuse to trade above that line.",
    ),
    "watch": (
        "Watch",
        1,
        "Linked groups hold 5% of supply or more but stay under that line. Also used when the only "
        "links over the line run through a high-volume wallet (possibly an exchange or a bot) or through "
        "a wallet seen behind holders of several tokens.",
    ),
    "low": (
        "Low risk",
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
    """The date a token has traded since (its oldest pool seen), when that was more than
    ESTABLISHED_DAYS before the trace; else empty. Linked top holders of an older token are often
    the project's own wallets (treasury, vesting, market making) rather than launch insiders: the
    links stay true, the grade is unchanged, and the page says so beside it."""
    try:
        since = datetime.fromisoformat(str(t["trading_since"]).replace("Z", "+00:00"))
        traced = datetime.fromisoformat(str(t["traced_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return ""
    return since.date().isoformat() if traced - since > timedelta(days=ESTABLISHED_DAYS) else ""


ESTABLISHED_NOTE = "on a token this old, the linked holders may be the project's own wallets"


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
    """The reply posted on a trace request issue."""
    lines = [
        f"## {e['label']}: `{e['mint']}`",
        "",
        GRADES[e["grade"]][2],
        "",
        f"- Largest linked group: {e['cluster_pct'] or 0:.1f}% of supply",
        f"- All linked groups: {e['clustered_pct'] or 0:.1f}% of supply",
        f"- Could not be traced: {e['unknown_pct'] or 0:.1f}% of supply",
        f"- Top 20 accounts hold {e['top_pct'] or 0:.1f}% of supply",
    ]
    if established(e):
        lines.append(f"- Trading since {established(e)}: {ESTABLISHED_NOTE}")
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


def feed_xml(doc: dict, ledger: dict | None, registry: dict | None) -> str:
    """RSS 2.0 alerts, newest first: tokens that entered the track record as High or Watch (the
    verdict they were published with), exit warnings on those tokens (ledger.exit_warnings), and
    funder wallets the moment they are seen behind a second token. Built from the same published
    files, so it never says more than the page does."""
    rows = {t["mint"]: t for t in doc.get("tokens") or []}
    items = []
    entries = (ledger or {}).get("entries")
    for e in entries.values() if isinstance(entries, dict) else []:
        if not isinstance(e, dict) or not e.get("mint"):
            continue
        if e.get("grade") not in ("high", "watch") or e.get("out_of_scope"):
            continue
        t = rows.get(e["mint"]) or {}
        label = GRADES[e["grade"]][0]
        # the title keeps the verdict it entered with; the numbers are today's, so a re-grade is said
        regraded = t.get("grade") != e["grade"]
        since = f"since re-graded {GRADES.get(t.get('grade'), ('?',))[0]}; " if t and regraded else ""
        why = f" Graded Watch because {weak_link(t)}." if t and not regraded and weak_link(t) else ""
        detail = (
            f"{since}largest linked group {'now ' if since else ''}{float(t['cluster_pct'] or 0):.1f}% "
            f"of supply, {float(t['clustered_pct'] or 0):.1f}% across all groups.{why}"
            if t
            else "no longer on the radar page."
        )
        items.append(
            (
                e.get("entered_at") or "",
                f"{label}: {e.get('name') or e['mint'][:6]}",
                f"{SITE}radar.html#{e['mint']}",
                f"{e.get('name')} ({e['mint']}): {detail}",
                f"verdict-{e['mint']}",
            )
        )
        w = e.get("exit_warning")
        if isinstance(w, dict) and w.get("at") and w.get("first_pct"):
            held, first = float(w.get("held_pct") or 0), float(w["first_pct"])
            drop = (1 - held / first) * 100 if first else 0
            items.append(
                (
                    w["at"],
                    f"Exit warning: {e.get('name') or e['mint'][:6]}",
                    f"{SITE}radar.html#{e['mint']}",
                    f"{e.get('name')} ({e['mint']}): the linked wallets among its 20 largest holders "
                    f"now hold {held:.1f}% of supply, against {first:.1f}% when the verdict was "
                    f"published ({drop:.0f}% less). A wallet that left the 20 largest may still hold "
                    "a little.",
                    f"exit-{e['mint']}",
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
        names = ", ".join(r.get("name") or m[:6] for m, r in toks)
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


def share_page(t: dict, image: bool = False) -> str:
    """A tiny page per token whose only job is a correct link preview (title, verdict, key numbers)
    when the link is pasted into X, Telegram or Discord; it forwards readers to the radar row. With
    `image`, it points the preview at the card t/<mint>.png (memetrader.cards) published beside it."""
    mint, name = t["mint"], t.get("name") or t["mint"][:6]
    groups = t.get("clusters") or []
    held = sum(int(c.get("holders") or 0) for c in groups)
    why = f" ({weak_link(t)})" if weak_link(t) else ""
    if groups and established(t):
        why += f" (trading since {established(t)}, so the linked wallets may be the project's own)"
    desc = (
        f"{t['label']}{why}. Largest linked group {float(t.get('cluster_pct') or 0):.1f}% of supply, "
        f"all linked groups {float(t.get('clustered_pct') or 0):.1f}% ({held} top holders linked). "
        "Wallets and proof txs are on the radar page. Not financial advice."
        if groups
        else f"{t['label']}: no two top holders lead back to the same private wallet. "
        "Not financial advice."
    )
    title = f"{name}: {t['label']} | Sift Insider Radar"
    target = f"../radar.html#{mint}"
    url = f"{SITE}t/{mint}.html"
    card = (
        f'<meta property="og:image" content="{_xml(f"{SITE}t/{mint}.png")}">'
        '<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">'
        if image
        else ""
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
        f"{card}"
        f'<meta http-equiv="refresh" content="0; url={_xml(target)}">'
        "<style>body{font:16px/1.6 -apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:50px auto;"
        "max-width:640px;padding:0 20px;color:#1c1e21;background:#fff}a{color:#1f5bb8}</style>"
        f"</head><body><p>{_xml(desc)}</p>"
        f'<p><a href="{_xml(target)}">Open the evidence on Sift Insider Radar</a></p>'
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
