# Sift

Hourly Solana memecoin screener. It shows net buy flow and which top holders were funded by the same wallet, plus the paper-trading record.

- Site: https://bernardhaddad.github.io/sift/
- Insider Radar: https://bernardhaddad.github.io/sift/radar.html
- Research notes: https://bernardhaddad.github.io/sift/research.html

## What's on it

- Buy flow over the last few hours: net dollars in or out, buyers against sellers, and a warning when one address is most of the buying.
- Holder tracing. The top 20 holders of each token the paper system looks at are traced back to whoever funded them. If one funder's wallets hold more than 10% of supply (or all such groups more than 20%), the token is refused and listed. Public rug-checkers pass most of these. The [Insider Radar](https://bernardhaddad.github.io/sift/radar.html) shows every trace with the transactions behind it. To get any token traced, open an issue with the trace template.
- The presets (quiet dips, deep survivors, aged pools) are bands measured on a 14-day panel of forward returns.
- The paper record: every closed trade of the pre-registered strategies, the kill and promote rules, and the reviews. No strategy has passed its promotion gate so far.
- `proofs/`: a daily copy of the radar's track record and data with their SHA-256 hashes, timestamped in Bitcoin with OpenTimestamps. Check a day with `sha256sum -c manifest.txt` and `ots verify manifest.txt.ots`.

## How it runs

Static site on GitHub Pages. An hourly Actions job runs the paper-trading cycle, rebuilds `data.json`, the radar and the research page, and commits them here. The trading code and the research notes are in a separate private repo; this one has the site and the workflows.

A second workflow runs the minute books: a scanner that quotes its pools once a minute, runs the minute-scale paper books, measures the Jupiter round-trip cost at every entry and writes the book to `fast.json` every 15 minutes. Each run lasts about 5.5 hours and starts the next one; a half-hourly schedule restarts it if a hand-over is missed.

## Money

Trade buttons open the token in a trading terminal. If referral codes are set, the terminal shares part of its own fee with Sift; you pay the same either way, and the page says which links carry a code. Sift takes no cut.

[Privacy](https://bernardhaddad.github.io/sift/privacy.html) | [Terms](https://bernardhaddad.github.io/sift/terms.html)
