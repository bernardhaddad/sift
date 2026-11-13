# Sift tracer

The code behind the [Insider Radar](https://bernardhaddad.github.io/sift/radar.html): for a Solana
token's 20 largest holders, it finds who paid for each holder's first transaction (two hops back)
and who sent it its first tokens. Holders that lead to the same private wallet form a linked group.
Exchanges, pools and programs link nobody. Every link carries the transaction that proves it.

This is the same code the radar runs (copied from its source on 2026-11-13, revision `296fe76`).
Python 3.11 or newer, standard library only.

## Run a trace

    python3 trace.py <token mint or pool address> [result.json]

It prints the report the radar page shows (verdict, linked groups, each link with its proof) and,
with a second argument, writes the full result as JSON.

The free public Solana node often refuses the heavy calls a trace needs (`getTokenLargestAccounts`)
and rate-limits the rest, so a trace there can fail with "the RPC node did not answer in time". A
free [Helius](https://www.helius.dev) key works well:

    HELIUS_API_KEY=your-key python3 trace.py <mint>

or any other RPC endpoint with `SOLANA_RPC_URL=https://...`. A trace takes one to four minutes.

## Check a published link yourself

[verify.py](../verify.py) re-checks every link the radar published for a token against a public
node, with no key: `python3 verify.py <mint>`.

## What the code is

- `src/memetrader/cluster.py`: the trace (holders, first transactions, first tokens, linked groups).
- `src/memetrader/radar.py`: verdicts (High linked share, Linked 5%+, Under 5% linked, Incomplete)
  and the report.
- `src/memetrader/ledger.py`: the track record (every verdict frozen when published, then its
  prices for 7 days) and the base rates by token age.
- `src/memetrader/filters.py`, `data.py`, `lpburn.py`: scope lists, price lookups, LP burn checks.
- `scripts/trace_token.py`: the on-demand trace the site runs when someone requests a token.

## Limits

Only the 20 largest accounts are traced, and a holder wallet with over 1,000 transactions is not
traced back. A linked group suggests one actor holds several wallets; it says nothing about what
they will do, and a clean result is not a safety guarantee. Not financial advice.

## License

MIT, see [LICENSE](LICENSE).
