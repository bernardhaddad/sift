"""GeckoTerminal free API client (keyless, ~30 req/min rate limit)."""

import http.client
import json
import math
import time
import urllib.error
import urllib.request
from datetime import datetime

BASE = "https://api.geckoterminal.com/api/v2"
RETRY_AFTER_CAP_S = 90.0
# Everything a GeckoTerminal call can raise short of a bug: HTTP errors, a dropped connection or
# short body (http.client.HTTPException, OSError), and a body that is not JSON (ValueError). Each
# wrapper below degrades to "no data" on any of them, so a bad answer never aborts a cycle that
# has already applied exits (fixed 2026-10-26: only HTTPError/URLError/TimeoutError were caught).
FETCH_ERRORS = (urllib.error.URLError, TimeoutError, http.client.HTTPException, OSError, ValueError)


def _retry_delay(header, attempt: int) -> float:
    """Seconds to wait after a 429: a numeric Retry-After, capped; anything else (an HTTP date,
    no header) falls back to exponential backoff. A date used to raise and an uncapped value
    could outlast the job (fixed 2026-10-26)."""
    try:
        v = float(header)
    except (TypeError, ValueError):
        v = None
    if v is None or not math.isfinite(v):
        return min(RETRY_AFTER_CAP_S, 8.0 * 2**attempt)
    return min(RETRY_AFTER_CAP_S, max(0.0, v))


def _rows(d) -> list:
    """The `data` list of a JSON:API answer; [] for anything else (a list, null, a string)."""
    rows = d.get("data") if isinstance(d, dict) else None
    return rows if isinstance(rows, list) else []


class Http:
    def __init__(self, min_interval: float = 3.0):
        self.min_interval = min_interval
        self._last = 0.0

    def _request(self, url: str, retries: int):
        req = urllib.request.Request(
            url, headers={"accept": "*/*", "user-agent": "memetrader/0.1"}
        )
        for attempt in range(retries + 1):
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    self._last = time.monotonic()
                    return resp.read()
            except urllib.error.HTTPError as err:
                self._last = time.monotonic()
                if err.code != 429 or attempt == retries:
                    raise
                time.sleep(_retry_delay(err.headers.get("Retry-After"), attempt))

    def get_json(self, url: str, retries: int = 4):
        return json.loads(self._request(url, retries))

    def get_text(self, url: str, retries: int = 2) -> str:
        return self._request(url, retries).decode("utf-8", errors="replace")


def top_pools(http, network: str = "solana", pages: int = 5) -> list:
    """Top pools by 24h volume, `pages` x 20 pools.

    Degrades to partial results if a page keeps failing after retries -
    a shorter scan means fewer candidates this cycle, never a crash.
    """
    pools = []
    for page in range(1, pages + 1):
        try:
            data = http.get_json(
                f"{BASE}/networks/{network}/pools?page={page}&sort=h24_volume_usd_desc"
            )
        except FETCH_ERRORS as err:
            print(f"[data] scan stopped at page {page}/{pages}: {err}")
            break
        pools.extend(p for p in _rows(data) if isinstance(p, dict))
    return pools


DISCOVERY_DEXES = ["pumpswap", "raydium", "meteora", "orca"]


def discovery_pools(http, network: str = "solana", pages_network: int = 6,
                    pages_per_dex: int = 3) -> list:
    """Network-wide top pools PLUS per-DEX top pools, deduped by address.

    The network-wide volume leaderboard is dominated by sub-24h launches
    (the sniper zone we refuse to trade), so the 1-14d universe mostly sits
    below it. Scanning each major DEX's own leaderboard reaches ~400 more
    pools where aged survivors actually rank. Fail-soft per source.

    Budget note: 6 network + 4x3 dex pages = 18 requests fits the per-IP
    rate budget that a 30-request scan consistently blew through (observed:
    truncation at ~page 5-6 nearly every cycle, making universe coverage
    fluctuate run to run). Fewer requests that always complete beat more
    that randomly truncate; network pages 7-10 are mostly sub-24h sniper
    zone anyway.
    """
    pools, seen = [], set()
    for p in top_pools(http, network, pages_network):
        addr = (p.get("attributes") or {}).get("address") if isinstance(p.get("attributes"), dict) else None
        if addr and addr not in seen:  # pagination shift can repeat a pool
            seen.add(addr)
            pools.append(p)
    for dex in DISCOVERY_DEXES:
        for page in range(1, pages_per_dex + 1):
            try:
                d = http.get_json(
                    f"{BASE}/networks/{network}/dexes/{dex}/pools"
                    f"?page={page}&sort=h24_volume_usd_desc"
                )
            except FETCH_ERRORS as err:
                print(f"[data] {dex} scan stopped at page {page}/{pages_per_dex}: {err}")
                break
            for p in _rows(d):
                attrs = p.get("attributes") if isinstance(p, dict) else None
                addr = attrs.get("address") if isinstance(attrs, dict) else None
                if addr and addr not in seen:
                    seen.add(addr)
                    pools.append(p)
    return pools


def trending_pools(http, network: str = "solana", page: int = 1, duration: str | None = None) -> list:
    """GeckoTerminal trending pools (attention signal), 20 a page; `duration` is the trending window
    ("5m", "1h", "6h" or "24h"; GeckoTerminal's default when None). Empty list on failure."""
    query = [f"page={page}"] if page > 1 else []
    if duration:
        query.append(f"duration={duration}")
    try:
        url = f"{BASE}/networks/{network}/trending_pools" + ("?" + "&".join(query) if query else "")
        data = http.get_json(url)
        return [p for p in _rows(data) if isinstance(p, dict)]
    except FETCH_ERRORS as err:
        print(f"[data] trending fetch failed: {err}")
        return []


def pool_ohlcv(http, address: str, network: str = "solana", limit: int = 336) -> list:
    """Hourly candles [ts, o, h, l, c, vol] since pool creation (newest first
    from the API; returned oldest-first here). [] on failure."""
    try:
        d = http.get_json(f"{BASE}/networks/{network}/pools/{address}/ohlcv/hour?limit={limit}")
        candles = d["data"]["attributes"]["ohlcv_list"] or []
    except (*FETCH_ERRORS, KeyError, TypeError) as err:
        print(f"[data] ohlcv failed for {address[:8]}…: {err}")
        return []
    return sorted(candles, key=lambda c: c[0])


def pool_trades(http, address: str, network: str = "solana") -> list:
    """The pool's most recent trades (≤ 300, ≤ 24h; newest first): kind
    buy/sell, volume_in_usd, tx_from_address, block_timestamp. [] on failure -
    flow telemetry is logged, never gated on, so a miss costs nothing."""
    try:
        d = http.get_json(f"{BASE}/networks/{network}/pools/{address}/trades"
                          "?trade_volume_in_usd_greater_than=0")
        return _rows(d)
    except (*FETCH_ERRORS, AttributeError) as err:
        print(f"[data] trades failed for {address[:8]}…: {err}")
        return []


def pool_ohlcv_5m(http, address: str, network: str = "solana", limit: int = 100) -> list:
    """Five-minute candles [ts, o, h, l, c, vol], oldest-first. [] on failure."""
    try:
        d = http.get_json(f"{BASE}/networks/{network}/pools/{address}/ohlcv/minute"
                          f"?aggregate=5&limit={limit}")
        candles = d["data"]["attributes"]["ohlcv_list"] or []
    except (*FETCH_ERRORS, KeyError, TypeError) as err:
        print(f"  [data] 5m ohlcv failed for {address[:8]}…: {err}")
        return []
    return sorted(candles, key=lambda c: c[0])


def pool_ohlcv_minute(http, address: str, network: str = "solana", limit: int = 300) -> list:
    """One-minute candles [ts, o, h, l, c, vol], oldest-first (limit ≤ 1000). [] on failure.
    Used by the minute scanner to seed a pool's price series after a restart."""
    try:
        d = http.get_json(f"{BASE}/networks/{network}/pools/{address}/ohlcv/minute"
                          f"?aggregate=1&limit={limit}")
        candles = d["data"]["attributes"]["ohlcv_list"] or []
    except (*FETCH_ERRORS, KeyError, TypeError) as err:
        print(f"  [data] 1m ohlcv failed for {address[:8]}…: {err}")
        return []
    return sorted(candles, key=lambda c: c[0])


def pool_low_since(http, address: str, since_ts: float, network: str = "solana") -> float | None:
    """Lowest low in the five-minute bars that START at or after `since_ts`. None if unknown.

    The cycle sees a pool's price once a cycle, so a dip that reverses between cycles
    is invisible to it - but a live resting stop-loss would have been filled.
    Amendment 7: exits check this low, so a stop that was breached between cycles
    fires. None means no data, and the caller must then fall back to the quoted
    price rather than assume the stop was safe.

    Amendment 7 correction (2026-09-15): the first version read HOURLY candles and
    admitted the one containing `since_ts`, whose low can predate the moment the
    position was opened. Two live trades (biketyson, STONK10 on 2026-09-14) were
    stopped on lows printed before the book bought. Five-minute bars starting at or
    after `since_ts` bound the pre-entry contamination to under five minutes on the
    first check and to zero once `checked_at` advances.
    """
    return pool_low_window(http, address, since_ts, network=network)[0]


def pool_low_window(http, address: str, since_ts: float, network: str = "solana") -> tuple:
    """(lowest low, start time of the newest bar read) over the five-minute bars that START at
    or after `since_ts`; (None, None) if unknown.

    The caller persists the newest bar's start, not the time of the check, as the next window
    start (fixed 2026-10-26): the bar in progress at the check is read again next time, so a
    dip in its remaining minutes is never skipped. All of that bar is after `since_ts`."""
    candles = pool_ohlcv_5m(http, address, network=network)
    bars = [c for c in candles if len(c) > 3 and c[0] >= since_ts and c[3]]
    if not bars:
        return None, None
    return min(c[3] for c in bars), max(c[0] for c in bars)


def token_first_pool_at(http, mint: str, network: str = "solana") -> str | None:
    """When the token first traded: the earliest `pool_created_at` among the pools
    GeckoTerminal lists for the mint (ISO text), None on any failure. STRATEGY.md §14.3 bands
    M6 on token age, which a new pool of an old token does not reset (Amendment 13)."""
    try:
        d = http.get_json(f"{BASE}/networks/{network}/tokens/{mint}/pools?page=1", retries=1)
    except FETCH_ERRORS as err:
        print(f"[data] token pools failed for {mint[:8]}…: {err}")
        return None
    born = []
    for p in _rows(d):
        attrs = p.get("attributes") if isinstance(p, dict) else None
        raw = attrs.get("pool_created_at") if isinstance(attrs, dict) else None
        try:
            born.append(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
        except ValueError:
            continue
    return min(born).isoformat(timespec="seconds") if born else None


def rugcheck_report(http, mint: str) -> dict | None:
    """RugCheck free full report: risk score, top holders w/ insider flags, LP lock.

    None on any failure - callers must treat missing data as a FAILED gate
    (no data means no entry, never a free pass).
    """
    try:
        report = http.get_json(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report")
    except FETCH_ERRORS as err:
        print(f"[data] rugcheck failed for {mint[:8]}…: {err}")
        return None
    return report if isinstance(report, dict) else None


JITO_TIP_FLOOR = "https://bundles.jito.wtf/api/v1/bundles/tip_floor"


def jito_tip_floor(http) -> dict:
    """The network's landed Jito tip percentiles (SOL) when asked: what bundles were paying, not a
    cost a paper trade paid. {"jito_tip_p50_sol", "jito_tip_p75_sol", "jito_tip_at"}, all None when
    the endpoint does not answer; never raises, never blocks an entry."""
    out = {"jito_tip_p50_sol": None, "jito_tip_p75_sol": None, "jito_tip_at": None}
    try:
        row = http.get_json(JITO_TIP_FLOOR, retries=0)[0]
        out.update(
            jito_tip_p50_sol=float(row["landed_tips_50th_percentile"]),
            jito_tip_p75_sol=float(row["landed_tips_75th_percentile"]),
            jito_tip_at=str(row.get("time") or "") or None,
        )
    except Exception as err:  # noqa: BLE001 - telemetry only: any failure is logged as null
        print(f"[data] jito tip floor unavailable: {type(err).__name__}")
    return out


def pools_multi(http, addresses: list, network: str = "solana") -> list:
    """Current data for specific pool addresses (chunks of 30).

    A failed chunk is skipped: positions with no fresh quote are left
    untouched by paper.manage(), never force-exited on missing data.
    """
    out = []
    for i in range(0, len(addresses), 30):
        chunk = ",".join(addresses[i : i + 30])
        try:
            data = http.get_json(f"{BASE}/networks/{network}/pools/multi/{chunk}")
        except FETCH_ERRORS as err:
            print(f"[data] quote chunk failed, skipping: {err}")
            continue
        out.extend(p for p in _rows(data) if isinstance(p, dict))
    return out


def token_prices(http, mints: list, network: str = "solana") -> dict:
    """mint -> USD price (see token_prices_answered)."""
    return token_prices_answered(http, mints, network)[0]


RETRY_PAUSE_S = 30.0  # before asking again for the batches the service refused in the first pass


def token_prices_answered(http, mints: list, network: str = "solana", pause=time.sleep) -> tuple[dict, set]:
    """(mint -> USD price, mints the service answered for), 30 per call (GeckoTerminal
    tokens/multi), in the order given: put the most urgent mints first. A batch the service
    refused is asked once more after a pause; an empty `data` list counts as refused. A mint in an
    answered batch without a price has no price; a mint in a batch refused twice was never asked,
    which the track record must not read as "no price"."""
    out: dict = {}
    answered: set = set()
    batches = [b for b in ([m for m in mints[i : i + 30] if m] for i in range(0, len(mints), 30)) if b]
    for attempt in (1, 2):
        failed = []
        for batch in batches:
            try:
                d = http.get_json(f"{BASE}/networks/{network}/tokens/multi/{','.join(batch)}", retries=2)
            except Exception as err:  # noqa: BLE001 - a missing price only delays the ledger by an hour
                print(f"[prices] batch of {len(batch)} unavailable (pass {attempt}): {err}")
                failed.append(batch)
                continue
            if not isinstance(d, dict) or not isinstance(d.get("data"), list) or not d["data"]:
                # an empty list for a batch of mints is not an answer: a degraded service returns
                # it for whole batches, and it must never be read as "no price" (fixed 2026-10-26)
                failed.append(batch)
                continue
            answered.update(batch)
            _read_prices(d, out)
        if not failed:
            break
        batches = failed
        if attempt == 1:
            pause(RETRY_PAUSE_S)
    return out, answered


def _read_prices(d: dict, out: dict) -> None:
    for row in d["data"]:
        a = row.get("attributes") if isinstance(row, dict) else None
        if not isinstance(a, dict):
            continue  # a null or malformed row is no price, never an error (fixed 2026-10-26)
        try:
            price = float(a.get("price_usd"))
        except (TypeError, ValueError):
            continue
        if a.get("address") and math.isfinite(price) and price > 0:
            out[a["address"]] = price
