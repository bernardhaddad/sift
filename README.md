# Sift

Sift's Insider Radar traces the top 20 holders of trending Solana tokens back to the wallets that funded them, grades each token by how much of its supply sits with linked wallets, and publishes the on-chain transaction behind every link. Each verdict is then followed for 7 days in a public track record, timestamped daily in Bitcoin, and a sample of the published links is re-checked on-chain every hour. The site also has a memecoin screener and a paper-trading record. Paper only: no real money is traded.

- Insider Radar: https://bernardhaddad.github.io/sift/radar.html
- Screener: https://bernardhaddad.github.io/sift/
- Research notes: https://bernardhaddad.github.io/sift/research.html

## What's on it

- Buy flow over the last few hours: net dollars in or out, buyers against sellers, and a warning when one address is most of the buying.
- Holder tracing. The top 20 holders of each token the paper system looks at are traced back to whoever funded them. If one funder's wallets hold more than 10% of supply (or all such groups more than 20%), the token is refused and listed. The [Insider Radar](https://bernardhaddad.github.io/sift/radar.html) shows every trace with the transactions behind it. To get any token traced, paste its address on the radar page and press Trace; the result shows on the page within a few minutes.
- The presets (quiet dips, deep survivors, aged 1–14 d) are bands measured on a 14-day panel of forward returns.
- The paper record: every closed trade of the pre-registered strategies, the kill and promote rules, and the reviews. No strategy has passed its promotion gate so far.
- `verify.py`: check the radar's published links for any token yourself, `python3 verify.py <mint>` (Python 3.11+, no key). The site runs the same file hourly on a sample.
- `proofs/`: a daily copy of the radar's track record and data with their SHA-256 hashes, timestamped in Bitcoin with OpenTimestamps. Check a day with `sha256sum -c manifest.txt`, then `ots verify manifest.txt.ots` if you run a Bitcoin node. Without one, `ots --no-bitcoin verify manifest.txt.ots` prints a block number and Merkle root to compare with any block explorer.

## How it runs

Static site on GitHub Pages. An hourly Actions job runs the paper-trading cycle, rebuilds `data.json`, the radar and the research page, and commits them here. The trading code and the research notes are in a separate private repo; this one has the site and the workflows.

A second workflow runs the minute books: a scanner that quotes its pools once a minute, runs the minute-scale paper books, measures the Jupiter round-trip cost at every entry and writes the book to `fast.json` every 15 minutes. Each run lasts about 5.5 hours and starts the next one; a half-hourly schedule restarts it if a hand-over is missed.

## Money

Trade buttons open the token in a trading terminal. No referral codes are set, so Sift earns nothing from them. If codes are ever set, the terminal shares part of its own fee with Sift, you pay the same, and the page says which links carry a code.

[Privacy](https://bernardhaddad.github.io/sift/privacy.html) | [Terms](https://bernardhaddad.github.io/sift/terms.html)
