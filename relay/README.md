# Trace relay

Lets the Insider Radar page trace a token without sending the visitor to GitHub. The page posts
the address here; the relay checks the daily limits, starts `trace.yml` and reports back until the
trace is saved, then hands the page the result.

- `POST /trace` with `{"mint": "<address>", "token": "<Turnstile token>"}` starts a trace (or returns
  one under 2 hours old). Answers `queued` (with `since`), `running`, `done`, `limit`, `busy`,
  `check` (Turnstile failed) or `invalid`.
- `GET /trace?mint=<address>&since=<time>` gives `queued`, `running`, `done` (with the radar row),
  `failed` (with the reason) or `none`.
- Any other visit is redirected to the radar page.

Limits: 3 traces a day per visitor (IPv6 by its /64), 40 a day in total, at most one trace running
and one waiting. Visitor addresses are only used as a salted hash in the run name.

Every trace needs a passed Cloudflare Turnstile check from the radar page (action `trace`, hostname
`bernardhaddad.github.io`). A missing secret refuses every request rather than letting them through.
Only the production address answers; preview builds and old deployments return 404.

## Setup (once, free)

1. Create a GitHub fine-grained token: Settings, Developer settings, Fine-grained tokens. Give it
   access to this repository only, with **Actions: Read and write** and **Contents: Read**.
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
check off.

Test without network: `node relay/test.mjs`.
