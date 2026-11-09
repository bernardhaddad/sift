#!/usr/bin/env python3
"""On-demand funding-cluster trace of one token for the Sift Insider Radar.

Run by the public sift repository's trace.yml when someone opens a trace request issue. The
mint arrives through an environment variable, never through the shell, and is validated as a
base58 Solana address before any network call.

Usage: TRACE_MINT=<mint> python3 scripts/trace_token.py <out_json> <out_markdown>
Exit 0 with both files written; exit 2 for an invalid mint or a failed trace (the markdown then
explains why, so the workflow can still reply on the issue).
"""

import json
import os
import pathlib
import re
import sys
from datetime import UTC, datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memetrader import cluster, data, filters, lpburn, radar  # noqa: E402

DEADLINE_S = 240.0
SYMBOL_RE = re.compile(r"^[A-Za-z0-9 ._$-]{1,20}$")


def token_name(mint: str) -> str | None:
    """Best-effort symbol from GeckoTerminal; the trace never depends on it."""
    try:
        url = f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}"
        d = data.Http().get_json(url, retries=1)
        sym = (d.get("data") or {}).get("attributes", {}).get("symbol")
    except Exception:  # noqa: BLE001 - a missing name is cosmetic
        return None
    # the symbol is whatever the token's creator wrote; publish only a plain short ticker
    return sym if isinstance(sym, str) and SYMBOL_RE.match(sym) else None


def known_public() -> frozenset:
    """The linkers the hourly cycle treats as shared infrastructure (site/radar-known.json, passed in
    KNOWN_PUBLIC_FILE), so a requested trace grades a token exactly as the hourly one would."""
    try:
        known = json.loads(pathlib.Path(os.environ.get("KNOWN_PUBLIC_FILE", "")).read_text())
    except (OSError, ValueError):
        return frozenset()
    return frozenset(a for a in known if isinstance(a, str)) if isinstance(known, list) else frozenset()


TOKEN_PROGRAMS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
}


def pool_base_token(address: str) -> str | None:
    """The base token of a GeckoTerminal pool address, or None when it is not a known pool."""
    try:
        d = data.Http().get_json(
            f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{address}", retries=1
        )
        base = d["data"]["relationships"]["base_token"]["data"]["id"]
    except Exception:  # noqa: BLE001 - not a pool, or the lookup failed: handled as "not a token"
        return None
    mint = base.split("_", 1)[1] if "_" in base else ""
    return mint if radar.valid_mint(mint) else None


def what_is(rpc, address: str) -> tuple[str, str | None]:
    """What the pasted address is: ("token", mint), ("pool", its base token mint), ("wallet",
    None), ("other", None) or ("missing", None). ("unknown", None) when the node cannot say."""
    try:
        info = rpc.call("getAccountInfo", [address, {"encoding": "jsonParsed"}])["value"]
    except (cluster.RpcError, KeyError, TypeError):
        return "unknown", None
    if info is None:
        return "missing", None
    owner = info.get("owner")
    parsed = (info.get("data") or {}).get("parsed") if isinstance(info.get("data"), dict) else None
    if owner in TOKEN_PROGRAMS and (parsed or {}).get("type") == "mint":
        return "token", address
    base = pool_base_token(address)
    if base:
        return "pool", base
    if owner == cluster.SYSTEM_PROGRAM and not info.get("executable"):
        return "wallet", None
    return "other", None


NOT_A_TOKEN = {
    "wallet": "`{a}` is a wallet address, not a token. Open a new request with the token's mint "
    "address (its contract address, shown on Solscan, DEXScreener or Birdeye).",
    "missing": "`{a}` does not exist on Solana mainnet. Check the address and open a new request "
    "with the token's mint address.",
    "other": "`{a}` is not a token mint or a trading pool. Open a new request with the token's mint "
    "address (its contract address, shown on Solscan, DEXScreener or Birdeye).",
}


def main(argv: list[str]) -> int:
    out_json, out_md = pathlib.Path(argv[1]), pathlib.Path(argv[2])
    mint = (os.environ.get("TRACE_MINT") or "").strip()
    if not radar.valid_mint(mint) or cluster._b58decode(mint) is None:
        out_md.write_text(
            "That does not look like a Solana token address (base58, 32 to 44 characters). "
            "Open a new request with the mint address of the token."
        )
        return 2
    rpc = cluster.Rpc.from_env(budget_s=DEADLINE_S + 60)
    if rpc is None:
        out_md.write_text("The tracer has no RPC endpoint configured right now. Please try again later.")
        return 2
    kind, resolved = what_is(rpc, mint)
    if kind in NOT_A_TOKEN:
        out_md.write_text(NOT_A_TOKEN[kind].format(a=mint))
        return 2
    pasted = mint
    if kind == "pool":
        mint = resolved  # a trading pool's address: trace the token it trades
    if mint in filters.NON_MEME_MINTS:
        out_md.write_text(
            f"`{mint}` is a stablecoin, wrapped or staked coin, tokenized asset or large protocol token. "
            "The radar covers memecoins; the linked holders of these are usually the issuer's own wallets."
        )
        return 2
    result = cluster.funding_cluster(rpc, mint, deadline_s=DEADLINE_S, known_public=known_public())
    if result is None:
        out_md.write_text(
            f"Could not read the holders of `{mint}` right now: the RPC node did not answer in time. "
            "Please try again later."
        )
        return 2
    now = datetime.now(UTC).isoformat(timespec="seconds")
    rec = {"mint": mint, "name": token_name(mint), "at": now, "result": result}
    e = radar.entry(mint, rec["name"], result, now, "request")
    auth = radar.authorities(rpc, [mint], now)
    if mint in auth:
        e["authority"] = auth[mint]
    pool = lpburn.main_pool(data.Http(), mint) or {}  # how long it has traded; skipped if not answered
    if pool.get("first_pool_at"):
        e["trading_since"] = pool["first_pool_at"]
    rec["row"] = e  # the radar row, so the site can show the result before the next hourly build
    out_json.write_text(json.dumps(rec))
    report = radar.report_markdown(e)
    if pasted != mint:
        report = f"`{pasted}` is a trading pool; this is the trace of the token it trades.\n\n" + report
    out_md.write_text(report)
    print(f"[trace] {mint}: {cluster.summary(result)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
