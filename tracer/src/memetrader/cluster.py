"""Funding-cluster screen on a token's top holders (STRATEGY.md §10 Amendment 6).

Why: RugCheck's per-wallet insider flags missed XST - ~74% of supply sat in
one fragmented cluster of small wallets, and it stopped M2 out within an
hour. The verified research ledger shows this is the dominant extraction
pattern, not an edge case: deployer-funded sniper wallets (Pine Analytics:
10,400 deployers → 4,600 wallets, 87% profitable), MELANIA ~90% and YZY
~94% of supply in one cluster at launch.

What the live XST holders look like three weeks after the rug (probe of
2026-09-04): 17 of the top-20 accounts were each funded by a distinct
throwaway wallet (4 signatures, 0 SOL), all 17 throwaways were funded by
ONE hub, and all 17 received their tokens by direct transfer from ONE
distributor wallet on the same day. A one-hop funding trace sees nothing;
either of the two links below sees the whole ring.

What it does, with plain Solana JSON-RPC (Helius free tier or any archival
endpoint):
  1. top 20 token accounts → owner addresses → share of supply per owner
     (raw integer amounts; the deprecated uiAmount is never used)
  2. owners whose account is program-owned (pool vaults, PDAs with state) are
     excluded; an account-less owner is a wallet only when its address is on
     the ed25519 curve (off-curve and account-less = a pool authority PDA)
  3. FUNDING LINK, hop 1: each fresh wallet (< FRESH_MAX_SIGS signatures) is
     linked to the fee payer of its oldest transaction - whoever paid for a
     wallet's first transaction funded or controls it
  4. TRANSFER LINK: each holder's token account is linked to the wallet that
     sent it its first tokens (an SPL transfer or mintTo); tokens that came
     from a pool vault or bonding curve (a program authority) were bought
     and create no link
  5. FUNDING LINK, hop 2: every private, fresh linker is itself linked to
     the fee payer of ITS oldest transaction, so hub → throwaway → holder
     rings collapse onto the hub (bounded at two hops)
  6. every linker is classified PUBLIC (≥ PUBLIC_MIN_SIGS signatures AND
     ≥ PUBLIC_MIN_SOL balance: an exchange hot wallet; or a program / PDA)
     or PRIVATE; holders joined through private linkers form connected
     components (union-find: chains and shared hubs merge, nothing is
     counted twice); a component with ≥ 2 top holders is a cluster

Fail-closed at the gate: no data = no entry, same rule as RugCheck. Every
holder whose links could not be established - a null or unreadable
transaction, a failed lookup, a linker that was never classified, a
truncated run - is reported as `unknown_pct`; the gate refuses when that
unknown share could hide a cap breach, and incomplete results are cached
only briefly so they are re-checked.

Known limits (documented in STRATEGY.md): only the 20 largest accounts are
sampled; wallets and token accounts with ≥ FRESH_MAX_SIGS signatures are
not traced; funding is traced two hops, transfers one; the endpoint must
serve full history (Helius and the public endpoint do).
"""

import http.client
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta

SYSTEM_PROGRAM = "11111111111111111111111111111111"
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
SIG_PAGE = 1000  # getSignaturesForAddress page limit
FRESH_MAX_SIGS = 1000  # a full first page = established, not traced
PUBLIC_MIN_SIGS = 3000  # three full pages AND ...
PUBLIC_MIN_SOL = 250.0  # ... a real treasury: exchange hot wallet, not a hub
TOP_N = 20  # getTokenLargestAccounts returns at most 20
# Trace method version, stored in every result. 2 = program addresses never link (Amendment 9),
# v1 transactions read, broad view and proofs recorded. Older results stay valid gate verdicts but
# are re-traced first and never published on the radar (outdated()).
METHOD = 2
LAMPORTS_PER_SOL = 1_000_000_000
# version 1 transactions are live on mainnet (~12% of a block on 14 Oct 2026); asking for at most 0
# made every holder whose first transaction was a v1 one "unknown"
TX_OPTS = {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}
SLICE0 = {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}


_KEY_RE = re.compile(r"(api-key=)[^&\s'\"]+")


class RpcError(Exception):
    """Messages are printed to public CI logs: any API key in them is redacted."""

    def __init__(self, message: str = ""):
        super().__init__(_KEY_RE.sub(r"\1***", str(message)))


def _retry_delay(header: str | None, attempt: int) -> float:
    """Seconds to wait after a 429. Numeric Retry-After honoured (capped);
    HTTP-date or missing header → exponential backoff."""
    try:
        return min(10.0, float(header))
    except (TypeError, ValueError):
        return 2.0 * (attempt + 1)


def _throttled(error) -> bool:
    """A JSON-RPC error that means "slow down" rather than "no": a 429 code, or Helius's
    "Request deprioritized ... Slow down requests" for tokens with very many holder accounts."""
    if not isinstance(error, dict):
        return False
    msg = str(error.get("message") or "").lower()
    return error.get("code") == 429 or "deprioritized" in msg or "slow down" in msg


class Rpc:
    """Minimal paced JSON-RPC client with a per-cycle circuit breaker and an
    optional cycle-wide deadline.

    After `max_failures` consecutive failed calls (429s exhausted, transport
    errors, JSON-RPC error replies) the client trips and every further call
    raises immediately; once `deadline` (a time.monotonic() value) passes it
    trips as well. Either way a slow or rate-limited endpoint costs one
    candidate a minute, never the whole cycle.
    """

    def __init__(self, url: str, min_interval: float = 0.12, max_failures: int = 3,
                 deadline: float | None = None):
        self.url = url
        self.min_interval = min_interval
        self.max_failures = max_failures
        self.deadline = deadline
        self._last = 0.0
        self._failures = 0
        self.tripped = False
        self.calls = 0

    @classmethod
    def from_env(cls, budget_s: float | None = None) -> "Rpc":
        deadline = time.monotonic() + budget_s if budget_s else None
        key = (os.environ.get("HELIUS_API_KEY") or "").strip()  # a stray newline breaks the URL
        if key:
            return cls(f"https://mainnet.helius-rpc.com/?api-key={key}", deadline=deadline)
        return cls(os.environ.get("SOLANA_RPC_URL") or PUBLIC_RPC, min_interval=0.5,
                   deadline=deadline)

    @property
    def kind(self) -> str:
        if "helius" in self.url:
            return "helius"
        return "public" if self.url == PUBLIC_RPC else "custom"

    def out_of_time(self) -> bool:
        if self.deadline is not None and time.monotonic() > self.deadline:
            self.tripped = True
        return self.tripped

    @property
    def failed(self) -> bool:
        """Tripped by the node failing, not by the clock: a fresh budget would not help."""
        return self._failures >= self.max_failures

    def call(self, method: str, params: list, retries: int = 2):
        if self.out_of_time():
            raise RpcError("circuit tripped")
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        for attempt in range(retries + 1):
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            req = urllib.request.Request(
                self.url, data=body,
                headers={"content-type": "application/json", "user-agent": "memetrader/0.1"},
            )
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    self._last = time.monotonic()
                    payload = json.loads(resp.read())
            except urllib.error.HTTPError as err:
                self._last = time.monotonic()
                if err.code == 429 and attempt < retries:
                    time.sleep(_retry_delay(err.headers.get("Retry-After"), attempt))
                    continue
                self._fail()
                raise RpcError(f"{method}: HTTP {err.code}") from err
            except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as err:
                # URLError covers DNS/connect failures; HTTPException covers
                # RemoteDisconnected/IncompleteRead/BadStatusLine raised by
                # getresponse()/read(); OSError covers resets and TLS errors.
                self._last = time.monotonic()
                self._fail()
                raise RpcError(f"{method}: {err}") from err
            if not isinstance(payload, dict):
                self._fail()
                raise RpcError(f"{method}: malformed reply")
            if "error" in payload:
                if _throttled(payload["error"]) and attempt < retries and not self.out_of_time():
                    time.sleep(2.0 * (attempt + 1))  # a JSON-RPC "slow down", same as a 429
                    continue
                self._fail()  # node-side failures count toward the breaker too
                raise RpcError(f"{method}: {payload['error']}")
            self.calls += 1
            self._failures = 0
            return payload.get("result")
        raise RpcError(f"{method}: retries exhausted")  # unreachable, for type checkers

    def _fail(self) -> None:
        self._failures += 1
        if self._failures >= self.max_failures:
            self.tripped = True


# ---- address arithmetic (no RPC) ----

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_P = 2**255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _b58decode(address: str) -> bytes | None:
    n = 0
    for ch in address:
        idx = _B58.find(ch)
        if idx < 0:
            return None
        n = n * 58 + idx
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(address) - len(address.lstrip("1"))
    return b"\x00" * pad + raw


def on_curve(address: str) -> bool:
    """True when the address is a valid ed25519 point, i.e. can belong to a
    keypair. Program-derived addresses (pool authorities, vault PDAs) are off
    the curve by construction, so a data-less, lamport-less owner that is
    off-curve is a PDA, not a wallet. Pure arithmetic, no RPC."""
    raw = _b58decode(address) if isinstance(address, str) else None
    if raw is None or len(raw) != 32:
        return False
    y = int.from_bytes(raw, "little") & ((1 << 255) - 1)
    sign = raw[31] >> 7
    if y >= _P:
        return False
    y2 = y * y % _P
    u, v = (y2 - 1) % _P, (_D * y2 + 1) % _P
    x = u * pow(v, 3, _P) % _P * pow(u * pow(v, 7, _P) % _P, (_P - 5) // 8, _P) % _P
    vx2 = v * x * x % _P
    if vx2 == (-u) % _P:
        x = x * _SQRT_M1 % _P
    elif vx2 != u:
        return False
    return not (x == 0 and sign == 1)


# ---- RPC helpers ----

def authority_holder(address) -> str:
    """Who holds a mint or freeze authority: "none" (revoked, or set to the System Program, which
    nobody can sign for), "program" (a program address: bridges, minting programs) or "wallet"
    (a key someone holds, the case that matters; Amendment 11)."""
    if not address or address == SYSTEM_PROGRAM:
        return "none"
    return "program" if is_pda(address) else "wallet"


def mint_authority(info) -> dict | None:
    """The authorities of a parsed mint account, or None when it is not one."""
    try:
        parsed = info["data"]["parsed"]
        if parsed.get("type") != "mint":
            return None
        m, f = parsed["info"].get("mintAuthority"), parsed["info"].get("freezeAuthority")
    except (KeyError, TypeError, AttributeError):
        return None
    return {"mint": m, "mint_by": authority_holder(m), "freeze": f, "freeze_by": authority_holder(f)}


def is_pda(address: str) -> bool:
    """A real 32-byte address that is off the ed25519 curve: a program-derived address (a pool or
    AMM authority, a vault). No keypair can sign for it, so it is never a person's wallet, whatever
    account state it has: Raydium's AMM authority is system-owned and holds SOL, yet only the
    program moves its funds (radar review, 2026-10-14)."""
    raw = _b58decode(address) if isinstance(address, str) else None
    return raw is not None and len(raw) == 32 and not on_curve(address)


def _history(rpc, address: str, max_sigs: int) -> tuple[int, str | None, bool]:
    """Signature count up to `max_sigs` (paged, newest first) and the oldest
    successful signature seen (failed transactions are skipped). Returns
    (count, oldest, capped); capped means the address has at least `max_sigs`
    signatures and `oldest` is NOT its first."""
    count, oldest, before = 0, None, None
    while count < max_sigs:
        opts = {"limit": min(SIG_PAGE, max_sigs - count)}
        if before:
            opts["before"] = before
        page = rpc.call("getSignaturesForAddress", [address, opts]) or []
        if not page:
            break
        count += len(page)
        before = page[-1]["signature"]
        ok = [p for p in page if p.get("err") is None]  # a failed transaction moves nothing and
        if ok:  # needs no signature from the accounts it names: never a funding proof
            oldest = ok[-1]["signature"]
        if len(page) < opts["limit"]:
            break
    return count, oldest, count >= max_sigs


def _first_page(rpc, address: str) -> tuple[list[str], bool]:
    """Signatures of `address`, newest first, one page. (sigs, capped)."""
    page = rpc.call("getSignaturesForAddress", [address, {"limit": SIG_PAGE}]) or []
    return [p["signature"] for p in page if p.get("err") is None], len(page) >= SIG_PAGE


def _funder_of(tx, owner: str) -> str | None:
    """Fee payer (accountKeys[0]) of `owner`'s oldest transaction when it is
    not `owner` itself: whoever paid for a wallet's first transaction funded
    or operates it, whether or not SOL was credited in the same transaction.

    Returns None only for a confirmed self-paid first transaction. Raises
    ValueError when the transaction is missing (JSON-RPC null: not found or
    pruned history) or unreadable - the caller must count that as unknown,
    never as clean."""
    try:
        keys = tx["transaction"]["message"]["accountKeys"]
        payer = keys[0]["pubkey"] if isinstance(keys[0], dict) else keys[0]
    except (TypeError, KeyError, IndexError) as err:
        raise ValueError("transaction missing or unreadable") from err
    if not isinstance(payer, str):
        raise ValueError("fee payer unreadable")
    return payer if payer != owner else None


def _instructions(tx) -> list:
    """Top-level plus inner parsed instructions of a jsonParsed transaction.
    Raises ValueError when the transaction is missing or unreadable."""
    try:
        instrs = list(tx["transaction"]["message"]["instructions"])  # always present when parsed
        for inner in (tx.get("meta") or {}).get("innerInstructions") or []:
            instrs.extend(inner.get("instructions") or [])
    except (TypeError, KeyError, AttributeError) as err:
        raise ValueError("transaction missing or unreadable") from err
    return instrs


def _sender_of(tx, token_account: str) -> str | None:
    """Authority that sent `token_account` its tokens in this transaction: the
    transfer authority of an SPL transfer/transferChecked into it, or the
    mint authority of a mintTo. None when the transaction holds no inbound
    token movement for the account. Raises ValueError when unreadable."""
    for ins in _instructions(tx):
        parsed = ins.get("parsed") if isinstance(ins, dict) else None
        if not isinstance(parsed, dict):
            continue
        info = parsed.get("info") or {}
        kind = parsed.get("type")
        if kind in ("transfer", "transferChecked") and info.get("destination") == token_account:
            who = info.get("authority") or info.get("multisigAuthority")
            return who if isinstance(who, str) else None
        if kind in ("mintTo", "mintToChecked") and info.get("account") == token_account:
            who = info.get("mintAuthority") or info.get("multisigMintAuthority")
            return who if isinstance(who, str) else None
    return None


def _components(edges: list[tuple[str, str]]) -> list[set[str]]:
    """Connected components over undirected (a, b) edges (union-find)."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups: dict[str, set[str]] = defaultdict(set)
    for node in list(parent):
        groups[find(node)].add(node)
    return list(groups.values())


# ---- the check ----

class _Trace:
    """Bookkeeping for one funding_cluster run."""

    def __init__(self, rpc, deadline_s: float, known_public=frozenset()):
        self.rpc = rpc
        self.started = time.monotonic()
        self.deadline_s = deadline_s
        self.known_public = set(known_public)
        self.edges: list[tuple[str, str]] = []  # (holder or linker, linker)
        self.linkers: set[str] = set()
        self.unknown: set[str] = set()  # holders whose links could not be established
        self.fresh: set[str] = set()
        self.transfer_edges: set[tuple[str, str]] = set()  # (holder, sender)
        self.kinds: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.sigs: dict[tuple[str, str, str], str] = {}
        self.busy: set[str] = set()  # private linkers with exchange-scale history (see _classify)
        self.hop2 = 0
        self.truncated = False

    def out_of_time(self) -> bool:
        if time.monotonic() - self.started > self.deadline_s:
            return True
        probe = getattr(self.rpc, "out_of_time", None)
        return bool(probe() if callable(probe) else getattr(self.rpc, "tripped", False))

    def link(self, node: str, linker: str, kind: str = "funded", sig: str | None = None) -> None:
        self.edges.append((node, linker))
        self.kinds[(node, linker)].add(kind)  # "funded" (paid the first transaction) or "tokens"
        if sig:
            self.sigs.setdefault((node, linker, kind), sig)  # the transaction that proves the link
        self.linkers.add(linker)


def _classify(trace: _Trace, addrs: list[str], shares: dict, hop: bool):
    """Classify linkers: returns (public, checked). With `hop`, private fresh
    linkers are traced one more funding hop (their grand-funders are added
    to trace.linkers for a later classification round without hops)."""
    rpc = trace.rpc
    public: set[str] = set(a for a in addrs if a in trace.known_public)
    checked: set[str] = set(public)
    addrs = [a for a in addrs if a not in public]
    states: dict[str, dict | None] = {}
    if addrs and not trace.out_of_time():
        try:
            fetched = rpc.call("getMultipleAccounts", [addrs, SLICE0])["value"]
            states = dict(zip(addrs, fetched, strict=False))
        except (RpcError, KeyError, TypeError, ValueError) as err:
            print(f"[cluster] linker states unavailable: {err}")
            return public, checked
    for addr in addrs:
        if trace.out_of_time():
            trace.truncated = True
            break
        if addr not in states:
            continue  # short reply: leave unchecked
        st = states[addr]
        is_program = (st is not None and (st.get("executable") or st.get("owner") != SYSTEM_PROGRAM))
        if is_program or (st is None and not on_curve(addr)) or is_pda(addr):
            checked.add(addr)
            public.add(addr)  # a program, pool authority or other PDA: never a hub
            continue
        try:
            count, oldest, capped = _history(rpc, addr, PUBLIC_MIN_SIGS)
        except (RpcError, KeyError, TypeError, ValueError) as err:
            print(f"[cluster] linker {addr[:8]}… unresolved: {err}")
            continue
        checked.add(addr)
        lamports = int((st or {}).get("lamports") or 0)
        if capped and lamports >= PUBLIC_MIN_SOL * LAMPORTS_PER_SOL:
            public.add(addr)
            continue
        if capped:
            trace.busy.add(addr)  # private for the gate, but possibly an exchange or bot wallet
        if hop and 0 < count < FRESH_MAX_SIGS and oldest and addr not in shares:
            try:
                tx = rpc.call("getTransaction", [oldest, TX_OPTS])
                grand = _funder_of(tx, addr)
            except (RpcError, KeyError, TypeError, ValueError) as err:
                print(f"[cluster] linker {addr[:8]}… second hop unresolved: {err}")
                checked.discard(addr)  # its own origin is unknown: not classified
                continue
            if grand:
                trace.hop2 += 1
                trace.link(addr, grand, "funded", oldest)
    return public, checked


def funding_cluster(rpc, mint: str, top_n: int = TOP_N, deadline_s: float = 90.0,
                    known_public=frozenset()) -> dict | None:
    """Cluster analysis of the top holders. None when the core data (supply,
    largest accounts, owners) cannot be fetched; otherwise a result whose
    `truncated` / `unknown_pct` fields say how complete it is.

    `known_public`: linker addresses to treat as public without lookup - the
    caller's memory of wallets seen linking holders of several unrelated
    tokens (bot fee payers, relayers), which are infrastructure, not hubs."""
    trace = _Trace(rpc, deadline_s, known_public)
    try:
        supply = int(rpc.call("getTokenSupply", [mint])["value"]["amount"])
        # Helius answers this with "Request deprioritized" for tokens with very many holder
        # accounts; it succeeds on a retry (Rpc.call), the public node refuses it outright
        largest = rpc.call("getTokenLargestAccounts", [mint])["value"][:top_n]
        if supply <= 0 or not largest:
            return None
        accounts = [{"account": a["address"], "amount": int(a["amount"])} for a in largest]
        # the mint account rides along in the same call: its authorities (Amendment 11)
        reply = rpc.call("getMultipleAccounts",
                         [[a["account"] for a in accounts] + [mint], {"encoding": "jsonParsed"}])["value"]
        infos = reply[: len(accounts)]
        authority = mint_authority(reply[len(accounts)]) if len(reply) > len(accounts) else None
    except (RpcError, KeyError, TypeError, ValueError, IndexError) as err:
        print(f"[cluster] unavailable for {mint[:8]}…: {err}")
        return None

    shares: dict[str, float] = defaultdict(float)
    unattributed = 0  # raw amount in accounts whose owner could not be read
    for acct, info in zip(accounts, infos, strict=False):  # tolerate a short reply
        try:
            owner = info["data"]["parsed"]["info"]["owner"]
            if not isinstance(owner, str):
                raise TypeError
        except (TypeError, KeyError):
            unattributed += acct["amount"]  # null entry or base64 fallback
            acct["owner"] = None
            continue
        acct["owner"] = owner
        shares[owner] += acct["amount"] / supply * 100.0
    if len(infos) < len(accounts):
        unattributed += sum(a["amount"] for a in accounts[len(infos):])
    owners = sorted(shares)
    if not owners:
        return None

    try:
        states = rpc.call("getMultipleAccounts", [owners, SLICE0])["value"]
    except (RpcError, KeyError, TypeError) as err:
        print(f"[cluster] unavailable for {mint[:8]}…: {err}")
        return None
    wallets = [o for o, st in zip(owners, states, strict=False)
               if (st is None and on_curve(o))
               or (st is not None and st.get("owner") == SYSTEM_PROGRAM and not st.get("executable")
                   and not is_pda(o))]
    wallet_set = set(wallets)
    visited: set[str] = set()

    # Funding link, hop 1.
    for owner in wallets:
        if trace.out_of_time():
            trace.truncated = True
            break
        visited.add(owner)
        try:
            count, oldest, capped = _history(rpc, owner, FRESH_MAX_SIGS)
            if capped:
                continue  # established wallet: not traced by design
            if not count or oldest is None:  # no visible (successful) history: endpoint gap
                trace.unknown.add(owner)
                continue
            tx = rpc.call("getTransaction", [oldest, TX_OPTS])
            funder = _funder_of(tx, owner)  # ValueError on null / unreadable
        except (RpcError, KeyError, TypeError, ValueError) as err:
            print(f"[cluster] wallet {owner[:8]}… unresolved: {err}")
            trace.unknown.add(owner)
            continue
        if funder:
            trace.fresh.add(owner)
            trace.link(owner, funder, "funded", oldest)
        # else: confirmed self-paid first transaction - no link, and clean

    # Transfer link: who sent each holder account its first tokens.
    for acct in accounts:
        owner = acct.get("owner")
        if owner not in wallet_set:
            continue
        if trace.out_of_time():
            trace.truncated = True
            break
        try:
            sigs, capped = _first_page(rpc, acct["account"])
            if capped:
                continue  # a very busy account: origin not traced by design
            if not sigs:
                trace.unknown.add(owner)
                continue
            sender, proof = None, None
            for sig in reversed(sigs[-2:]):  # oldest, then second oldest
                tx = rpc.call("getTransaction", [sig, TX_OPTS])
                sender = _sender_of(tx, acct["account"])  # ValueError on null / unreadable
                if sender:
                    proof = sig
                    break
        except (RpcError, KeyError, TypeError, ValueError) as err:
            print(f"[cluster] account {acct['account'][:8]}… origin unresolved: {err}")
            trace.unknown.add(owner)
            continue
        if sender and sender != owner:
            trace.transfer_edges.add((owner, sender))
            trace.link(owner, sender, "tokens", proof)

    # Classify linkers (with a second funding hop), then their grand-funders.
    round1 = sorted(trace.linkers)
    public, checked = _classify(trace, round1, shares, hop=True)
    round2 = sorted(trace.linkers - set(round1))
    if round2:
        pub2, chk2 = _classify(trace, round2, shares, hop=False)
        public |= pub2
        checked |= chk2

    # A holder whose chain contains an unclassified linker is unknown.
    downstream: dict[str, set[str]] = defaultdict(set)  # linker -> holders behind it
    for a, b in trace.edges:
        if a in shares:
            downstream[b].add(a)
    for a, b in trace.edges:
        if a not in shares:  # (linker, grand-linker): grand covers the linker's holders
            downstream[b] |= downstream.get(a, set())
    for linker, holders in downstream.items():
        if linker not in checked:
            trace.unknown |= holders
    if trace.truncated:
        trace.unknown.update(w for w in wallets if w not in visited)

    edges = [(a, b) for a, b in trace.edges if b in checked and b not in public]
    transfer_linked = {o for o, s in trace.transfer_edges if s in checked and s not in public}
    clusters = []
    for comp in _components(edges):
        members = sorted(m for m in comp if m in shares)
        if len(members) < 2:
            continue
        clusters.append({"wallets": members,
                         "linkers": sorted(m for m in comp if m not in shares),
                         "pct": round(sum(shares[m] for m in members), 3)})
    clusters.sort(key=lambda c: c["pct"], reverse=True)
    # The same clustering without busy linkers (>= PUBLIC_MIN_SIGS signatures but a small balance:
    # exchange withdrawal and bot wallets look like this). The trading gate ignores it and stays
    # strict; the public radar uses it so a holder who withdrew from an exchange is not reported as
    # an insider of the token.
    busy = trace.busy & checked
    strict = [round(sum(shares[m] for m in comp if m in shares), 3)
              for comp in _components([(a, b) for a, b in edges if b not in busy])
              if sum(1 for m in comp if m in shares) >= 2]
    # The radar's broad view: also keep links through linkers the caller's memory marked public
    # (seen behind holders of several tokens). For the gate they are infrastructure; on a public
    # page a serial deployer's hub must not vanish, so the radar shows these groups with a note.
    known = {a for a in trace.known_public & trace.linkers if not is_pda(a)}
    broad_edges = [(a, b) for a, b in trace.edges if b in checked and (b not in public or b in known)]
    broad = []
    for comp in _components(broad_edges):
        members = sorted(m for m in comp if m in shares)
        if len(members) >= 2:
            broad.append({"wallets": members, "linkers": sorted(m for m in comp if m not in shares),
                          "pct": round(sum(shares[m] for m in members), 3)})
    broad.sort(key=lambda c: c["pct"], reverse=True)
    in_broad = set().union(*(set(c["wallets"]) | set(c["linkers"]) for c in broad)) if broad else set()
    unknown_pct = sum(shares[o] for o in trace.unknown) + unattributed / supply * 100.0
    out = {
        "mint": mint,
        "method": METHOD,
        "top_pct": round(sum(shares.values()) + unattributed / supply * 100.0, 3),
        "wallet_pct": round(sum(shares[o] for o in wallets), 3),
        "fresh_pct": round(sum(shares[o] for o in trace.fresh), 3),
        "unknown_pct": round(unknown_pct, 3),
        "cluster_pct": clusters[0]["pct"] if clusters else 0.0,
        "clustered_pct": round(sum(c["pct"] for c in clusters), 3),
        "clusters": clusters,
        "holders": len(owners),
        "wallets": len(wallets),
        "fresh": len(trace.fresh),
        "transfer_linked": len(transfer_linked),  # holders supplied by a private wallet
        "hop2": trace.hop2,
        "linkers": len(trace.linkers),
        "checked": len(checked),
        "unknown": len(trace.unknown),
        "public_linkers": len(public),
        "private_linkers": sorted(a for a in checked if a not in public),
        "truncated": trace.truncated,
        "busy_linkers": sorted(busy),
        "known_linkers": sorted(known),
        "broad_clusters": broad,
        "broad_clustered_pct": round(sum(c["pct"] for c in broad), 3),
        # evidence for the public radar: every link inside a broad cluster, one per kind,
        # [holder or linker, linker, "funded" | "tokens", signature of the proving transaction]
        "edges": [[a, b, k, trace.sigs.get((a, b, k))] for a, b, k in sorted(
            {(a, b, k) for a, b in broad_edges if a in in_broad for k in trace.kinds[(a, b)]}
        )],
        "strict_cluster_pct": max(strict, default=0.0),
        "strict_clustered_pct": round(sum(strict), 3),
        # evidence for the public radar (site/radar.html): each top owner's share of supply
        "shares": {o: round(p, 3) for o, p in sorted(shares.items(), key=lambda kv: -kv[1])},
    }
    if authority:
        out["authority"] = authority
    return out


def complete(result: dict | None) -> bool:
    """True when every holder was classified: safe to cache for the full TTL."""
    return bool(result) and not result.get("truncated") and not result.get("unknown_pct")


def summary(result: dict) -> str:
    return (f"largest cluster {result['cluster_pct']:.1f}%, all clusters {result['clustered_pct']:.1f}%, "
            f"unknown {result['unknown_pct']:.1f}%, fresh {result['fresh_pct']:.1f}%, "
            f"{result['wallets']}/{result['holders']} wallets, {result['transfer_linked']} transfer-linked, "
            f"{result['checked']}/{result['linkers']} linkers classified"
            + (", truncated" if result.get("truncated") else ""))


class Cache:
    """Per-mint result cache persisted as one JSON file. Complete results
    live `ttl_h`; incomplete ones only `ttl_incomplete_h`, so a transient
    gap is re-checked soon without re-tracing the same mint every fire."""

    def __init__(self, path: pathlib.Path, ttl_h: float = 24.0, ttl_incomplete_h: float = 2.0):
        self.path = pathlib.Path(path)
        self.ttl = timedelta(hours=ttl_h)
        self.ttl_incomplete = timedelta(hours=ttl_incomplete_h)
        self._data: dict = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except (ValueError, OSError):
                self._data = {}
        if not isinstance(self._data, dict):
            self._data = {}

    def _ttl(self, entry) -> timedelta:
        return self.ttl if entry.get("complete", True) else self.ttl_incomplete

    def get(self, mint: str, now: datetime) -> dict | None:
        entry = self._data.get(mint)
        try:
            at = datetime.fromisoformat(entry["at"])
            result = entry["result"]
        except (KeyError, ValueError, TypeError):
            return None
        return result if now - at < self._ttl(entry) else None

    def put(self, mint: str, result: dict, now: datetime) -> None:
        kept = {}
        for m, e in self._data.items():
            at = _parse_at(e)
            if at and now - at < self._ttl(e):
                kept[m] = e
        kept[mint] = {"at": now.isoformat(timespec="seconds"), "result": result,
                      "complete": complete(result)}
        self._data = kept
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data))


def outdated(result) -> bool:
    """Traced before busy linkers and the broad view were recorded. Still a valid gate verdict
    (the gate never used them), but the hourly feed re-traces it first and the radar skips it."""
    return isinstance(result, dict) and int(result.get("method") or 0) < METHOD


def _parse_at(entry) -> datetime | None:
    try:
        return datetime.fromisoformat(entry["at"])
    except (KeyError, ValueError, TypeError):
        return None
