"""Parse GeckoTerminal pool payloads and apply the pre-registered H1 filter.

All thresholds are frozen in STRATEGY.md; do not edit without an amendment.
"""

import math
from datetime import UTC, datetime

H1 = {
    "min_age_h": 24.0,
    "max_age_h": 14 * 24.0,
    "min_liq_usd": 30_000.0,
    "min_vol24_usd": 100_000.0,
    "vol_liq_min": 0.5,
    "vol_liq_max": 20.0,
    "max_fdv_usd": 50_000_000.0,
}


def _f(value, default=None):
    """A finite float, else `default`. NaN and infinity are malformed quotes, not numbers: a NaN
    price closed positions at r = NaN and switched off the total-R KILL (fixed 2026-10-26)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def parse_pool(pool: dict, now: datetime | None = None) -> dict | None:
    """Flatten one API pool object; None if it lacks the fields we need."""
    now = now or datetime.now(UTC)
    attrs = pool.get("attributes") or {}
    address = attrs.get("address")
    created_raw = attrs.get("pool_created_at")
    price = _f(attrs.get("base_token_price_usd"))
    liq = _f(attrs.get("reserve_in_usd"))
    if not address or not created_raw or not price or price < 0 or liq is None:
        return None
    try:
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    tx24 = (attrs.get("transactions") or {}).get("h24") or {}
    base_token = ((pool.get("relationships") or {}).get("base_token") or {}).get("data") or {}
    token_id = base_token.get("id") or ""
    return {
        "address": address,
        "name": attrs.get("name") or address[:8],
        "mint": token_id.split("_", 1)[1] if "_" in token_id else None,
        "price": price,
        "liq": liq,
        "vol24": _f((attrs.get("volume_usd") or {}).get("h24"), 0.0),
        "vol1": _f((attrs.get("volume_usd") or {}).get("h1"), 0.0),
        "buys24": int(tx24.get("buys") or 0),
        "sells24": int(tx24.get("sells") or 0),
        "buyers24": int(tx24.get("buyers") or 0),  # unique buying addresses (telemetry)
        "sellers24": int(tx24.get("sellers") or 0),
        "chg24": _f((attrs.get("price_change_percentage") or {}).get("h24"), 0.0),
        "fdv": _f(attrs.get("fdv_usd")),
        "age_h": (now - created).total_seconds() / 3600.0,
    }


def h1_check(c: dict, cfg: dict = H1) -> tuple[bool, str]:
    """(passes, reason-if-not) for one parsed pool."""
    if c["age_h"] < cfg["min_age_h"]:
        return False, "too_young"
    if c["age_h"] > cfg["max_age_h"]:
        return False, "too_old"
    if c["liq"] < cfg["min_liq_usd"]:
        return False, "low_liquidity"
    if c["vol24"] < cfg["min_vol24_usd"]:
        return False, "low_volume"
    ratio = c["vol24"] / c["liq"] if c["liq"] else 0.0
    if not (cfg["vol_liq_min"] <= ratio <= cfg["vol_liq_max"]):
        return False, "vol_liq_ratio"
    if c["buys24"] < c["sells24"]:
        return False, "net_selling"
    if c["chg24"] <= 0:
        return False, "no_momentum"
    if c["fdv"] is not None and c["fdv"] > cfg["max_fdv_usd"]:
        return False, "fdv_too_high"
    return True, "ok"


def candidates(pools: list, now: datetime | None = None) -> list[dict]:
    """Parsed pools passing H1, ranked by 24h volume descending (pre-registered)."""
    out = []
    for pool in pools:
        parsed = parse_pool(pool, now=now)
        if parsed and meme_pair_check(parsed)[0] and h1_check(parsed)[0]:
            out.append(parsed)
    out.sort(key=lambda c: c["vol24"], reverse=True)
    return out


# Universe defect fix (STRATEGY.md §5 Amendment 1, 2026-08-09): tokenized
# RWAs/stocks/gold and stable/LST pairs passed the hygiene bands (observed
# live: PAXG/USDC, NVDA/SOL held by M1). Memecoins trade against SOL, and
# known non-meme bases are excluded.
NON_MEME_BASES = {
    "PAXG", "XAUT", "WBTC", "CBBTC", "WETH", "NVDA", "TSLA", "AAPL", "MSTR",
    "SPY", "QQQ", "COIN", "HOOD", "GOOGL", "AMZN", "META", "CRCL", "USD1",
    "EURC", "USDS", "USDY", "JITOSOL", "MSOL", "BNSOL", "JUPSOL", "INF",
    # Amendment 10 (2026-10-15): the same classes, seen live under "memecoins" on the screener
    "USDC", "USDT", "USDG", "PYUSD",  # stablecoins
    "NVDAX", "SPCX", "SPCXX",  # tokenized stocks
    "WNEAR", "ZEC",  # wrapped or bridged coins
    "JUP", "RAY", "ORCA", "JLP", "MET", "HNT", "GRASS", "ORE",  # protocol tokens
}


# The radar's scope (fixed 23 Oct 2026): exact mint addresses of non-memecoin assets the trending
# feed brings in. Listed by address, never by symbol: a token merely calling itself "USDG" is the
# kind the radar must still trace. Stablecoins and tokenized assets keep issuer-held authorities and
# treasury wallets that look like a linked group without being one.
NON_MEME_MINTS = frozenset({
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH",  # USDG
    "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo",  # PYUSD
    "CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfcnZMVPCASH",  # CASH (USD stablecoin, added 2026-11-08)
    "HzwqbKZw8HxMN6bF2yFZNrht3c2iXXzpKcFu7uBEDKtr",  # EURC
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",  # WETH (Wormhole)
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",  # WBTC (Wormhole)
    "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",  # cbBTC
    "A7bdiYdS5GjqGFtxf17ppRHtDKPkkRqbKtR27dxvQXaS",  # ZEC (bridged)
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",  # mSOL
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",  # JitoSOL
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
    "27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4",  # JLP
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",  # RAY
    "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",  # ORCA
    "oreoU2P8bN6jkk3jbaiVxYnG1dCXcYxwhwyK9jSybcp",  # ORE
    "hntyVP6YFm1Hg25TN1ShBFhAvvSa8B6KQrBmjDQh3Ge",  # HNT
    "SPCXxcqXj6e5dJDVNovHN8744zkbhM2bYudU45BimGb",  # SPCX (tokenized stock)
})


def meme_pair_check(c: dict) -> tuple[bool, str]:
    parts = [p.strip().upper() for p in (c.get("name") or "").split("/")]
    if len(parts) != 2 or parts[1] != "SOL":
        return False, "non_sol_quote"
    if parts[0] in NON_MEME_BASES:
        return False, "non_meme_base"
    return True, "ok"


# ---- Experiment M2 additions (STRATEGY.md §7, frozen) ----

M2 = {
    "accel_mult": 1.5,
    "max_rug_score": 30.0,
    "max_top10_pct": 30.0,
    "max_insiders": 2,
    "min_lp_locked_pct": 80.0,
    "max_cluster_pct": 10.0,  # Amendment 6: largest funding cluster, % of supply
    "max_clustered_pct": 20.0,  # Amendment 6: all funding clusters combined
}


def attention_check(c: dict, trending_addresses: set, cfg: dict = M2) -> tuple[bool, str]:
    """Attention must be accelerating NOW: trending list or h1 volume pace."""
    if c["address"] in trending_addresses:
        return True, "trending"
    hourly_pace = c["vol24"] / 24.0
    if hourly_pace > 0 and c["vol1"] > cfg["accel_mult"] * hourly_pace:
        return True, "vol_accel"
    return False, "no_attention_accel"


def lifecycle(candles: list) -> dict | None:
    """Price-path features from oldest-first hourly candles [ts,o,h,l,c,vol].

    Needs >= 12 candles. Returns:
      ath           highest high over the pool's life
      from_ath      fractional drawdown of last close vs ath (0 = at ATH)
      higher_low    last-12h low above the prior-12h low (base building)
      vol_ratio     last-6h volume vs prior-6h volume (decay < 1 < growth)
      hours         candle count (~pool age in trading hours)
    """
    if len(candles) < 12:
        return None
    highs = [c[2] for c in candles]
    ath = max(highs)
    last_close = candles[-1][4]
    recent_low = min(c[3] for c in candles[-12:])
    prior_low = min(c[3] for c in candles[-24:-12]) if len(candles) >= 24 else None
    vol_recent = sum(c[5] for c in candles[-6:])
    vol_prior = sum(c[5] for c in candles[-12:-6])
    return {
        "ath": ath,
        "from_ath": 1.0 - (last_close / ath) if ath else 0.0,
        "higher_low": (prior_low is not None and recent_low > prior_low),
        "vol_ratio": (vol_recent / vol_prior) if vol_prior else None,
        "hours": len(candles),
    }


def rug_gate(report: dict | None, cfg: dict = M2) -> tuple[bool, str]:
    """Insider/concentration screen on a RugCheck report. No data = no entry."""
    if not report:
        return False, "no_rugcheck_data"
    score = report.get("score_normalised")
    if score is None or float(score) > cfg["max_rug_score"]:
        return False, "rug_score"
    holders = report.get("topHolders") or []
    top10 = sum(sorted((float(h.get("pct") or 0) for h in holders), reverse=True)[:10])
    if top10 > cfg["max_top10_pct"]:
        return False, "holder_concentration"
    if sum(1 for h in holders if h.get("insider")) > cfg["max_insiders"]:
        return False, "insiders_flagged"
    if "max_insider_pct" in cfg:
        insider_pct = sum(float(h.get("pct") or 0) for h in holders if h.get("insider"))
        if insider_pct > cfg["max_insider_pct"]:
            return False, "insider_supply"
    lp_locked = report.get("lpLockedPct")
    if lp_locked is not None and float(lp_locked) < cfg["min_lp_locked_pct"]:
        return False, "lp_not_locked"
    return True, "ok"


def cluster_gate(result: dict | None, cfg: dict = M2, authority: bool = True) -> tuple[bool, str]:
    """Amendment 6: refuse a token whose top holders form funding clusters -
    the largest over `max_cluster_pct` or all combined over
    `max_clustered_pct` of supply (the fragmented-bundle pattern RugCheck's
    per-wallet flags missed on XST). No data = no entry: a truncated check,
    or an unknown share large enough to hide a cap breach, is no data."""
    if not result:
        return False, "no_cluster_data"
    largest = float(result.get("cluster_pct") or 0.0)
    total = float(result.get("clustered_pct") or 0.0)
    unknown = float(result.get("unknown_pct") or 0.0)
    if not all(math.isfinite(v) for v in (largest, total, unknown)):
        return False, "no_cluster_data"  # NaN fails every comparison below: it must not admit
    if largest > cfg["max_cluster_pct"] or total > cfg["max_clustered_pct"]:
        return False, "funding_cluster"
    if (result.get("truncated") or largest + unknown > cfg["max_cluster_pct"]
            or total + unknown > cfg["max_clustered_pct"]):
        return False, "no_cluster_data"
    # Amendment 11: a key someone holds can still mint supply or freeze holders. A trace without
    # the field (older traces, or the node did not return the mint account) is not refused for it.
    auth = result.get("authority") or {}
    if authority and "wallet" in (auth.get("mint_by"), auth.get("freeze_by")):
        return False, "wallet_authority"
    return True, "ok"


# ---- Experiment M3 "Catcher" (STRATEGY.md §9, frozen) ----

M3_RUG = {**M2, "max_insider_pct": 10.0}

M3 = {
    "min_from_ath": 0.25,
    "max_from_ath": 0.60,
    "min_vol_ratio": 1.5,
    "min_holders": 300,
    "max_holders": 20_000,
    "holder_growth": 1.02,
    "min_sample_gap_h": 6.0,
    "watch_expiry_h": 7 * 24.0,
}


def h3_check(c: dict, cfg: dict = H1) -> tuple[bool, str]:
    """M3 universe: H1 hygiene bands WITHOUT the momentum/net-buying gates."""
    if c["age_h"] < cfg["min_age_h"]:
        return False, "too_young"
    if c["age_h"] > cfg["max_age_h"]:
        return False, "too_old"
    if c["liq"] < cfg["min_liq_usd"]:
        return False, "low_liquidity"
    if c["vol24"] < cfg["min_vol24_usd"]:
        return False, "low_volume"
    ratio = c["vol24"] / c["liq"] if c["liq"] else 0.0
    if not (cfg["vol_liq_min"] <= ratio <= cfg["vol_liq_max"]):
        return False, "vol_liq_ratio"
    if c["fdv"] is not None and c["fdv"] > cfg["max_fdv_usd"]:
        return False, "fdv_too_high"
    return True, "ok"


def h3_candidates(pools: list, now: datetime | None = None) -> list[dict]:
    out = []
    for pool in pools:
        parsed = parse_pool(pool, now=now)
        if parsed and meme_pair_check(parsed)[0] and h3_check(parsed)[0]:
            out.append(parsed)
    out.sort(key=lambda c: c["vol24"], reverse=True)
    return out


def m3_price_gate(lc: dict | None, cfg: dict = M3) -> tuple[bool, str]:
    """Pullback-zone + base-building + volume-second-burst gates on lifecycle()."""
    if not lc:
        return False, "no_price_history"
    if not (cfg["min_from_ath"] <= lc["from_ath"] <= cfg["max_from_ath"]):
        return False, "outside_pullback_zone"
    if not lc["higher_low"]:
        return False, "no_higher_low"
    if lc["vol_ratio"] is None or lc["vol_ratio"] < cfg["min_vol_ratio"]:
        return False, "no_volume_burst"
    return True, "ok"


# ---- Experiment M4 "Quiet Survivor" (STRATEGY.md §11, frozen) ----
# Every band cites a measured panel-mining result (docs/mining-report.md).

H4 = {
    "min_age_h": 7 * 24.0,
    "max_age_h": 30 * 24.0,
    "min_liq_usd": 250_000.0,
    "min_vol24_usd": 100_000.0,
    "max_vol24_usd": 1_700_000.0,
    "vol_liq_min": 0.5,
    "vol_liq_max": 10.0,
    "max_fdv_usd": 50_000_000.0,
}

M4_PRICE = {"min_from_ath": 0.25, "max_from_ath": 0.60, "min_vol_ratio": 1.0}
M4_CALM = {"chg24_min": -15.0, "chg24_max": -2.0, "buy_ratio_min": 0.45, "buy_ratio_max": 0.62}
M4_HOLDERS = {"min_holders": 1_000, "max_holders": 100_000}


def h4_check(c: dict, cfg: dict = H4) -> tuple[bool, str]:
    """M4 universe: older, deeper, capped-volume pools."""
    if c["age_h"] < cfg["min_age_h"]:
        return False, "too_young"
    if c["age_h"] > cfg["max_age_h"]:
        return False, "too_old"
    if c["liq"] < cfg["min_liq_usd"]:
        return False, "low_liquidity"
    if not (cfg["min_vol24_usd"] <= c["vol24"] <= cfg["max_vol24_usd"]):
        return False, "volume_band"
    ratio = c["vol24"] / c["liq"] if c["liq"] else 0.0
    if not (cfg["vol_liq_min"] <= ratio <= cfg["vol_liq_max"]):
        return False, "vol_liq_ratio"
    if c["fdv"] is not None and c["fdv"] > cfg["max_fdv_usd"]:
        return False, "fdv_too_high"
    return True, "ok"


def h4_candidates(pools: list, now: datetime | None = None) -> list[dict]:
    """M4 universe, ranked by LIQUIDITY descending (mining: never rank by volume)."""
    out = []
    for pool in pools:
        parsed = parse_pool(pool, now=now)
        if parsed and meme_pair_check(parsed)[0] and h4_check(parsed)[0]:
            out.append(parsed)
    out.sort(key=lambda c: c["liq"], reverse=True)
    return out


def m4_calm_check(c: dict, cfg: dict = M4_CALM) -> tuple[bool, str]:
    """Dip-zone momentum + non-dump flow (Amendment 4: re-mined on M4's own
    deep universe - modest 24h dips recover; flat/up and heavily sold don't)."""
    chg = c.get("chg24")
    if chg is None or not (cfg["chg24_min"] <= chg <= cfg["chg24_max"]):
        return False, "not_calm"
    total = (c.get("buys24") or 0) + (c.get("sells24") or 0)
    if not total:
        return False, "no_flow"
    ratio = (c.get("buys24") or 0) / total
    if not (cfg["buy_ratio_min"] <= ratio <= cfg["buy_ratio_max"]):
        return False, "flow_imbalance"
    return True, "ok"
