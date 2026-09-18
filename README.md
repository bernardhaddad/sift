# Sift

**A free hourly screener for Solana memecoins, with the two things the trading terminals do not show you and an honest public record of what a rules-based system actually earns.**

Live: **https://bernardhaddad.github.io/sift/** · Research: **https://bernardhaddad.github.io/sift/research.html**

## What it shows

- **Who is really buying.** Value-weighted flow over the last hours: net dollars in or out, distinct buyers against sellers, and a warning when one address is most of the buy volume.
- **Which top holders share a funder.** For every token the paper system considers, the top 20 holders are traced back through who funded them. Wallets that share a private funder are one cluster. Tokens where one cluster holds more than 10% of supply, or all clusters more than 20%, are refused and listed. Public rug-checkers pass most of these.
- **Bands that mean something.** Quiet dips, deep survivors, aged pools: each preset is a band that was measured on a 14-day panel of true forward returns, and the page tells you when a band is empty rather than filling it.
- **The paper record, unedited.** Every closed trade of the pre-registered strategies, the verdict rules, and the reviews that re-derive the book under honest fill conventions. As of September 2026 no strategy has shown an edge, and the site says so.

## How it is built

The screener is a static page served by GitHub Pages. An hourly GitHub Actions job runs the paper-trading cycle, rebuilds `data.json` and the research page, and commits them here. The trading code and the research documents live in a separate repository; this one holds the published site and the workflow that runs it.

## How it stays free

The trade buttons open the token in a trading terminal. When referral codes are configured, the terminal shares a part of its own fee with Sift on accounts that signed up through it; you pay the same fee either way. Sift does not run a bot, does not take a cut of your trades, and does not sell calls. The disclosure at the bottom of the page states exactly which links carry a referral.

## Research

The research page publishes what was measured: fill-convention re-readings of the book, the measured round-trip cost of trading each age band, the mining passes over the panel, the verified ledger of who actually makes money in memecoins, and the review that decided against funding the first strategy at its 60-trade gate.
