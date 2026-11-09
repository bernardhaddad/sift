"""How much of a token's main pool's LP supply has been burned, read from the chain.

Three pool types keep a fungible LP token and record how much of it was ever minted:
- Raydium AMM v4: base mint at byte 400, quote mint at 432, LP mint at 464, LP minted (lp_reserve,
  u64) at 720.
- Raydium CPMM: LP mint at byte 136, token 0 mint at 168, token 1 mint at 200, LP minted (lp_supply,
  u64) at 333.
- PumpSwap: base mint at byte 43, quote mint at 75, LP mint at 107, LP minted (lp_supply, u64) at 203.
Burned share = 1 - current LP mint supply / LP minted. Burning reduces the mint's supply but not
the pool's record, which is what makes the difference measurable.

Where the LP is not all burned, the largest LP accounts are read and the rest is split by who holds
it: a known lock (Raydium Burn & Earn's lock authority, from Raydium's SDK), an address only a
program can sign for (off the ed25519 curve: a program-controlled account, not verified as a lock),
or a wallet (on the curve: someone holds the key and can withdraw).

Pools that hold liquidity as positions (Raydium CLMM, Orca Whirlpools, Meteora DLMM and DAMM v2,
Byreal, DefiTuna) or still on a launch curve (Meteora DBC) have no LP token: nothing to burn, and
they are reported as such, by the program that owns the pool account. Any other pool type is
reported as not checked.
"""

import base64
import struct

from memetrader import cluster

RAYDIUM_AMM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
RAYDIUM_CPMM = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
LAYOUTS = {  # program -> (base mint, quote mint, LP mint, LP minted) byte offsets
    RAYDIUM_AMM: (400, 432, 464, 720),
    RAYDIUM_CPMM: (168, 200, 136, 333),
    PUMPSWAP: (43, 75, 107, 203),
}
DEX_NAMES = {RAYDIUM_AMM: "Raydium", RAYDIUM_CPMM: "Raydium CPMM", PUMPSWAP: "PumpSwap"}
POSITION_PROGRAMS = {  # program -> name: liquidity held as positions (or a launch curve), no LP token
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora DLMM",
    "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG": "Meteora DAMM v2",
    "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN": "Meteora DBC",
    "REALQqNEomY6cQGZJUGwywTBD2UmDT32rZcNnfxQ5N2": "Byreal",
    "fUSioN9YKKSa3CUC2YUc4tPkHJ5Y6XW1yz8y6F7qWz9": "DefiTuna",
}
NO_LP_TOKEN = {"raydium-clmm", "orca"}  # concentrated liquidity: positions, no LP token
READ_DEXES = {"pumpswap", "raydium"}  # GeckoTerminal ids of the pool types read above (AMM v4, CPMM)
GECKO_NAMES = {  # GeckoTerminal dex id -> the name shown on the page
    "pumpswap": "PumpSwap",
    "raydium": "Raydium",
    "raydium-clmm": "Raydium CLMM",
    "orca": "Orca",
    "meteora": "Meteora",
    "meteora-damm-v2": "Meteora DAMM v2",
    "meteora-dbc": "Meteora DBC",
    "pump-fun": "Pump.fun",
    "byreal": "Byreal",
    "defituna": "DefiTuna",
    "metadao": "MetaDAO",
}
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
KNOWN_LOCKS = {  # LP lock authorities: tokens they hold cannot be withdrawn
    "3f7GcQFG397GAaEnv51zR6tsTVihYRydnydDD1cXekxH": "Raydium Burn & Earn",  # LOCK_CPMM_AUTH, raydium-sdk-V2
}
_P = 2**255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P


def _unb58(text: str) -> bytes:
    n = 0
    for ch in text:
        n = n * 58 + _B58.index(ch)
    raw = n.to_bytes(32, "big") if n else b""
    return b"\0" * (len(text) - len(text.lstrip("1"))) + raw.lstrip(b"\0") if len(raw) != 32 else raw


def on_curve(address: str) -> bool:
    """Whether a 32-byte address is a point on ed25519, i.e. a key someone can hold. Program-derived
    addresses are made to be off the curve, so only a program can sign for them."""
    try:
        raw = _unb58(address)
    except ValueError:
        return False
    if len(raw) != 32:
        return False
    y = int.from_bytes(raw, "little") & ((1 << 255) - 1)
    if y >= _P:
        return False
    u, v = (y * y - 1) % _P, (_D * y * y + 1) % _P
    x2 = u * pow(v, _P - 2, _P) % _P
    if x2 == 0:
        return not (raw[31] >> 7)  # x = 0 is on the curve only with the sign bit clear
    return pow(x2, (_P - 1) // 2, _P) == 1  # Euler's criterion: x^2 has a square root


def _b58(raw: bytes) -> str:
    n, out = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def parse_pool(owner: str, raw: bytes) -> dict | None:
    """{"base", "quote", "lp_mint", "minted"} of a supported pool account, else None."""
    layout = LAYOUTS.get(owner)
    if not layout or len(raw) < layout[3] + 8:
        return None
    base, quote, lp, minted = layout
    return {
        "base": _b58(raw[base : base + 32]),
        "quote": _b58(raw[quote : quote + 32]),
        "lp_mint": _b58(raw[lp : lp + 32]),
        "minted": struct.unpack_from("<Q", raw, minted)[0],
    }


def burned(rpc, pools: dict, at: str) -> dict:
    """pools: {token mint: pool address}. Returns {token mint: {"pool", "dex", "burned_pct", "at"}}
    for pools of a supported type that trade that token and whose LP supply the node returned, and
    {token mint: {"pool", "dex", "no_lp_token": True}} for pools owned by a POSITION_PROGRAMS program.
    Anything the node did not answer, or that does not check out, is left out."""
    out, items = {}, list(pools.items())
    for i in range(0, len(items), 100):
        batch = items[i : i + 100]
        try:
            accs = rpc.call("getMultipleAccounts", [[p for _, p in batch], {"encoding": "base64"}])["value"]
        except (cluster.RpcError, KeyError, TypeError):
            continue
        parsed = {}
        for (mint, pool), acc in zip(batch, accs or [], strict=False):
            if isinstance(acc, dict) and acc.get("owner") in POSITION_PROGRAMS:
                out[mint] = {"pool": pool, "dex": POSITION_PROGRAMS[acc["owner"]], "no_lp_token": True}
                continue
            try:
                info = parse_pool(acc["owner"], base64.b64decode(acc["data"][0]))
            except (KeyError, TypeError, IndexError, ValueError):
                continue
            if info and mint in (info["base"], info["quote"]) and info["minted"] > 0:
                parsed[mint] = (pool, acc["owner"], info)
        if not parsed:
            continue
        lp_mints = [info["lp_mint"] for _, _, info in parsed.values()]
        try:
            mints = rpc.call("getMultipleAccounts", [lp_mints, {"encoding": "jsonParsed"}])["value"]
        except (cluster.RpcError, KeyError, TypeError):
            continue
        for (mint, (pool, owner, info)), acc in zip(parsed.items(), mints or [], strict=False):
            try:
                supply = int(acc["data"]["parsed"]["info"]["supply"])
            except (KeyError, TypeError, ValueError):
                continue
            share = max(0.0, min(1.0, 1 - supply / info["minted"]))
            out[mint] = {"pool": pool, "dex": DEX_NAMES[owner], "burned_pct": round(share * 100, 2), "at": at}
            if share < 0.99:
                held = lp_holders(rpc, info["lp_mint"], info["minted"])
                if held:
                    out[mint].update(held)
    return out


def lp_holders(rpc, lp_mint: str, minted: int) -> dict | None:
    """Who holds the unburned LP, as shares of all LP ever minted (largest 20 accounts):
    {"locked_pct", "program_pct", "wallet_pct", "top_wallet_pct", "locks", "unread", "unread_pct"}.
    Only accounts the node answered for are split; "unread" of them were not, holding "unread_pct"
    (by getTokenLargestAccounts' own amounts) that could be anyone's. top_wallet_pct is the largest
    single wallet, all its accounts together. None if the node did not answer (the line then shows
    the burn alone)."""
    try:
        largest = rpc.call("getTokenLargestAccounts", [lp_mint])["value"][:20]
        addresses = [a["address"] for a in largest]
        accs = []
        if addresses:
            accs = rpc.call("getMultipleAccounts", [addresses, {"encoding": "jsonParsed"}])["value"]
    except (cluster.RpcError, KeyError, TypeError):
        return None
    accs = list(accs or []) if isinstance(accs, list) else []
    locked = program = unread = missed = 0
    per_wallet: dict = {}
    locks = set()
    for i, row in enumerate(largest):
        try:
            info = accs[i]["data"]["parsed"]["info"]
            amount, holder = int(info["tokenAmount"]["amount"]), info["owner"]
        except (KeyError, TypeError, ValueError, IndexError):
            missed += 1  # null, unreadable or not returned: counted apart, never as nobody's
            try:
                unread += int(row.get("amount") or 0)
            except (AttributeError, TypeError, ValueError):
                pass
            continue
        if holder in KNOWN_LOCKS:
            locked += amount
            locks.add(KNOWN_LOCKS[holder])
        elif on_curve(holder):
            per_wallet[holder] = per_wallet.get(holder, 0) + amount
        else:
            program += amount
    pct = lambda n: round(n / minted * 100, 2)  # noqa: E731
    wallet, top = sum(per_wallet.values()), max(per_wallet.values(), default=0)
    return {"locked_pct": pct(locked), "program_pct": pct(program), "wallet_pct": pct(wallet),
            "top_wallet_pct": pct(top), "locks": sorted(locks), "unread": missed, "unread_pct": pct(unread)}


def dex_name(gecko_id) -> str | None:
    """A readable name for a GeckoTerminal dex id (main_pool's "dex"); an id not listed is
    title-cased, never shown raw."""
    if not gecko_id:
        return None
    return GECKO_NAMES.get(gecko_id) or str(gecko_id).replace("-", " ").replace("_", " ").title()


def main_pool(http, mint: str) -> dict | None:
    """The token's pool with the most liquidity on GeckoTerminal: {"pool", "dex", "first_pool_at"}
    (the creation time of the oldest pool listed, so the token has traded at least since then); {} when
    GeckoTerminal lists no pool for it (an answer, kept like any other); None when the lookup
    itself failed (asked again next build)."""
    try:
        pools = http.get_json(
            f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools?page=1", retries=2
        )["data"]
    except Exception as err:  # noqa: BLE001 - a failed lookup is retried, never guessed
        code = getattr(err, "code", "") or ""  # the HTTP status, when there is one (429 = rate limit)
        print(f"[lp] pool lookup failed for {mint[:8]}: {type(err).__name__} {code}")
        return None
    if not pools:
        return {}
    try:
        best = max(pools, key=lambda p: float(p["attributes"].get("reserve_in_usd") or 0))
        created = [p["attributes"].get("pool_created_at") for p in pools]
        return {
            "pool": best["attributes"]["address"],
            "dex": best["relationships"]["dex"]["data"]["id"],
            "first_pool_at": min((c for c in created if c), default=None),
        }
    except (KeyError, TypeError, ValueError):
        return None
