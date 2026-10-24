# Trace relay

Lets the Insider Radar page trace a token without sending the visitor to GitHub. The page posts
the address here; the relay checks the daily limits, starts `trace.yml` and reports back until the
trace is saved, then hands the page the result.

- `POST /trace` with `{"mint": "<address>"}` starts a trace (or returns one under 2 hours old).
- `GET /trace?mint=<address>` gives `queued`, `running`, `done` (with the radar row), `failed`
  (with the reason), `limit`, `invalid` or `none`.

Limits: 3 traces a day per visitor, 40 a day in total. Visitor addresses are only used as a
salted, daily-changing hash in the run name.

## Setup (once, free)

1. Create a GitHub fine-grained token: Settings, Developer settings, Fine-grained tokens. Give it
   access to this repository only, with **Actions: Read and write** and **Contents: Read**.
2. In the Cloudflare dashboard, go to Workers & Pages, then Create, then Create Worker. Name it
   `sift-trace`, deploy, then choose Edit code and replace everything with `worker.js`. Deploy.
3. In the worker's Settings, under Variables and Secrets, add two secrets: `GH_TOKEN` (the token
   from step 1) and `SALT` (any long random text).
4. Put the worker's address (for example `https://sift-trace.<name>.workers.dev`) in `RELAY` in
   `radar.html`.

Or as a Cloudflare Pages project connected to this repository: `functions/trace.js` serves the
relay at `/trace`. Leave the build command empty, set the build output directory to `relay`, add
`functions/*` and `relay/*` as the only build watch paths (the hourly data commits then start no
builds), and add the two secrets under Variables and secrets.

Or from a terminal: `npx wrangler deploy`, then `npx wrangler secret put GH_TOKEN` and
`npx wrangler secret put SALT`.
