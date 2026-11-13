# Trace relay

Lets the Insider Radar page trace a token without sending the visitor to GitHub. The page posts
the address here; the relay checks the daily limits, starts `trace.yml` and reports back until the
trace is saved, then hands the page the result. It also counts the site's page visits, without
cookies (see Visit counter below).

- `POST /trace` with `{"mint": "<address>", "token": "<Turnstile token>"}` starts a trace (or returns
  one under 2 hours old). Answers `queued` (with `since`), `running`, `done`, `limit`, `busy`,
  `check` (Turnstile failed) or `invalid`.
- `GET /trace?mint=<address>&since=<time>` gives `queued`, `running`, `done` (with the radar row),
  `failed` (with the reason) or `none`.
- Any other visit is redirected to the radar page.

Limits: 3 traces a day per visitor (IPv6 by its /64), 40 a day in total (trace requests opened as
issues count toward the same 40), at most two traces queued or running at once. A trace that fails
on our side (the RPC node did not answer) does not use up one of the visitor's three. Only an
address that decodes to a 32-byte Solana key starts a run. Requests that arrive together all pass
the first check, so after starting its run the relay counts again against the runs started before
it and cancels its own run if that one is over a limit. `trace.yml` repeats the same checks when the
run starts. Visitor addresses are only used as a salted hash in the run name.

Every trace needs a passed Cloudflare Turnstile check from the radar page (action `trace`, hostname
`bernardhaddad.github.io`). A missing secret refuses every request rather than letting them through.
Only the production address answers; preview builds and old deployments return 404.

## Setup (once, free)

1. Create a GitHub fine-grained token: Settings, Developer settings, Fine-grained tokens. Give it
   access to this repository only, with **Actions: Read and write**, **Contents: Read** and
   **Issues: Read** (to count trace issues toward the daily total; without it the relay leaves that
   count to `trace.yml`).
2. In Cloudflare, create a Turnstile widget for the hostname `bernardhaddad.github.io` and put its
   site key in `TURNSTILE` in `radar.html`.
3. Create a Cloudflare Pages project connected to this repository (`functions/_middleware.js` serves
   the relay). Leave the build command empty, set the build output directory to `relay`, and add
   `functions/*` and `relay/*` as the only build watch paths, so the hourly data commits start no
   builds. Put the project's address in `RELAY` in `radar.html`.
4. Under Variables and secrets add `GH_TOKEN` (step 1), `SALT` (any long random text) and
   `TURNSTILE_SECRET` (step 2), then redeploy. A changed secret only reaches a new deployment.
5. Delete old deployments that still hold a working token, or regenerate the token: an old build
   runs old code.

Settings: `RELAY_HOST` if the relay is served from another address than `sift-trace.pages.dev`
(for example a Worker on workers.dev); `TURNSTILE=off` with no `TURNSTILE_SECRET` switches the
check off; `RECOUNT_MS` (default 2000) is the wait after starting a run before the second count;
`COUNTER=off` stops the visit counter.

## Visit counter

Every page of the site (the screener, `radar.html`, `research.html`, `privacy.html`, `terms.html`,
`404.html` and each token page `t/<address>.html`) sends one beacon when it opens, from a short
inline script that does nothing on any other origin than `https://bernardhaddad.github.io` or in a
browser without `navigator.sendBeacon`. A token page sends it before it forwards to the token's
radar row, which then sends none, so a token link counts once, as page `t`, with the site that
linked to it. The 404 page sends `/sift/404.html` whatever address it is shown at. For each visit
the relay adds one to a daily total.
It reads nothing that tells visitors apart, so it cannot tell two visitors apart, or a second visit
from a first. Counts are approximate: a browser or blocker that stops the beacon is not counted, and
nothing removes repeat visits.

- `POST /hit` with a text/plain JSON body `{"p": location.pathname, "s": "<the link's ?s= tag>",
  "r": "<the referrer's host>"}`, as `navigator.sendBeacon` sends a string (no CORS preflight).
  It always answers 204 with no body, and the count is written after the answer.
- A visit is counted only when the `Origin` header is exactly `https://bernardhaddad.github.io`,
  the body is at most 512 bytes, the path is a site page (`/sift/`, or `index`, `radar`,
  `research`, `privacy`, `terms` or `404` under it, with or without `.html`; every
  `/sift/t/<address>.html` counts as page `t`) and the tag is `x`, `tg`, `rss`, `reddit`, `gh`,
  `hn`, `mail` or none. Anything else counts nothing.
- The referring site is kept only as a host from a short list: `t.me`, `x.com`, `twitter.com`,
  `t.co`, `reddit.com`, `old.reddit.com`, `news.ycombinator.com`, `github.com`, `bing.com`,
  `duckduckgo.com` and the site itself, with a leading `www.` dropped, and `google` for any of
  Google's search domains (`google.com`, `google.de`, `google.co.uk` and the like: one value, so a
  made-up domain adds no row). Any other host is kept as `other`, and no referrer as empty. A path
  or a full address is never kept. So one client in one country can make at most 7 x 8 x 14 rows a
  day, whatever it sends.
- The country is the one Cloudflare reports for the request (`XX` when it reports none).
- The counter reads the `Origin` and `Content-Length` headers, the body and that country, and
  nothing else: never the IP address, the user agent, cookies or any other header.
- Each count adds one to `n` in the D1 table `hits(day, page, src, ref, country, n)`, one row per
  day (UTC), page, tag, referring site and country. The table is created on the first count.
- `COUNTER=off`, or no `STATS_DB` binding, turns `/hit` into a no-op that still answers 204. A D1
  error is dropped the same way.
- `GET /stats?days=14` with `Authorization: Bearer <STATS_KEY>` gives the totals of the last N days,
  today included (N from 1 to 90, default 14): `total`, `by_day`, `by_page`, `by_src`, `by_ref`,
  `by_country`, and per day `by_day_page`, `by_day_src` and `by_day_ref` (every day of the window,
  `{}` for a day with no visit). D1 makes the sums, so the answer stays a few hundred numbers and
  its work does not grow with the number of rows. The key is compared in constant time. A missing or
  wrong key gives 401, as does a `STATS_KEY` that is unset or shorter than 16 characters; no
  `STATS_DB` binding gives 503. The answer is never cached and has no CORS header, so no web page
  can read it.

### Counter setup (once, free)

1. In Cloudflare, create a D1 database (Storage and databases, D1 SQL database). Any name will do,
   for example `sift-stats`.
2. In the Pages project from step 3 above, add a D1 database binding named `STATS_DB` for that
   database, in the production environment (Settings, Bindings).
3. Under Variables and secrets, add a secret `STATS_KEY`: a long random text, for example the output
   of `openssl rand -hex 32`. Keep it out of issues, commits and chats.
4. Redeploy. A changed binding or secret only reaches a new deployment.
5. Check: open the site once, then, with the key in the shell variable `STATS_KEY`, run
   `curl -sS -H "Authorization: Bearer $STATS_KEY" "https://sift-trace.pages.dev/stats?days=1"`.
   The visit shows in `total`.

Test without network: `node relay/test.mjs`. It also runs the counter's statements on a real SQLite
database when Node has `node:sqlite` (22.5 or later).
