// Trace relay for the Insider Radar page. The page asks it to trace a token and then asks for the
// result; it starts trace.yml in this repository and reads the saved trace back, so a visitor never
// has to open GitHub. Runs as a Cloudflare Worker (relay/README.md).
//
// Secrets: GH_TOKEN, a fine-grained token for this repository only with Actions read and write and
// Contents read; SALT, any long random string (visitor addresses are only kept as salted hashes);
// TURNSTILE_SECRET: a trace starts only with a passed Cloudflare Turnstile check (variable TURNSTILE=off
// with no secret switches the check off).

const REPO = "bernardhaddad/sift";
const WORKFLOW = "trace.yml";
const ORIGIN = "https://bernardhaddad.github.io";
const HOST = "sift-trace.pages.dev";  // only the production address answers (RELAY_HOST to change it)
const DAILY_LIMIT = 40;      // traces a day, all visitors together (same as trace.yml)
const PER_VISITOR_LIMIT = 3; // traces a day per visitor
const FRESH_MS = 2 * 3600e3; // a trace this recent is returned instead of a new one
const RUN_WINDOW_MS = 30 * 60e3;
const MAX_ACTIVE = 2;        // relay traces queued or running at once; more are refused (the paper cycle shares the runners)
const CACHE_S = 5;           // GitHub answers reused this long, so polling cannot use up the token's API quota
const RECOUNT_MS = 2000;     // after a dispatch, the wait before the runs are counted again (RECOUNT_MS to change it)
const MAX_PAGES = 10;        // run list pages read (100 runs each); a longer list refuses rather than undercounts
const B58 = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

const LIMIT_TEXT = `The free daily limit is reached (${PER_VISITOR_LIMIT} traces a day per visitor, ${DAILY_LIMIT} in total). Please try again tomorrow.`;
const INVALID_TEXT = "That does not look like a Solana token address (base58, 32 to 44 characters).";
const BUSY_TEXT = "Other traces are running right now. Please try again in a few minutes.";
const CHECK_TEXT = "The check that you are a person did not pass. Please try again.";
const API = `https://api.github.com/repos/${REPO}/`;

export default {
  async fetch(req, env) {
    const cors = {
      "Access-Control-Allow-Origin": ORIGIN,
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Vary": "Origin",
    };
    const url = new URL(req.url);
    if (env.PRODUCTION_ONLY !== "off" && url.hostname !== (env.RELAY_HOST || HOST)) return reply({ error: "not found" }, 404, cors);
    if (req.method === "OPTIONS") return new Response(null, { headers: cors });
    // opened in a browser rather than called by the page: send the visitor to the radar
    if (req.method === "GET" && (url.pathname !== "/trace" || !url.searchParams.has("mint"))) {
      return Response.redirect(`${ORIGIN}/sift/radar.html`, 302);
    }
    if (url.pathname !== "/trace") return reply({ error: "not found" }, 404, cors);
    try {
      if (req.method === "GET") {
        return reply(await status(env, (url.searchParams.get("mint") || "").trim(), url.searchParams.get("since") || ""), 200, cors);
      }
      if (req.method === "POST") {
        let body = {};
        try { body = await req.json(); } catch { /* empty body: handled as an invalid address */ }
        const ip = req.headers.get("CF-Connecting-IP") || "";
        // checked before anything else, so automated requests cost no GitHub call
        if (!(await human(env, String(body.token || ""), ip))) {
          return reply({ status: "check", reason: CHECK_TEXT }, 200, cors);
        }
        return reply(await start(env, String(body.mint || "").trim(), ip), 200, cors);
      }
      return reply({ error: "method not allowed" }, 405, cors);
    } catch {
      return reply({ status: "error", reason: "The tracer is not reachable right now. Please try again in a few minutes." }, 502, cors);
    }
  },
};

// Cloudflare Turnstile: was this token issued to a person on the radar page, just now, once
async function human(env, token, ip) {
  if (env.TURNSTILE_SECRET === undefined && env.TURNSTILE === "off") return true;  // explicitly switched off
  if (!env.TURNSTILE_SECRET || !token || token.length > 2048) return false;  // a missing secret refuses, never waves through
  const form = new FormData();
  form.append("secret", env.TURNSTILE_SECRET);
  form.append("response", token);
  if (ip) form.append("remoteip", ip);
  try {
    const res = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", { method: "POST", body: form });
    const d = await res.json();
    return d.success === true && d.hostname === new URL(ORIGIN).hostname && d.action === "trace";
  } catch {
    return false;
  }
}

function reply(body, code, headers) {
  return new Response(JSON.stringify(body), { status: code, headers: { ...headers, "Content-Type": "application/json", "Cache-Control": "no-store" } });
}

function gh(env, path, init = {}) {
  const url = path.startsWith("https://") ? path : `https://api.github.com/repos/${REPO}${path}`;
  if (!url.startsWith(API)) throw new Error("not this repository");  // the token goes nowhere else
  return fetch(url, {
    ...init,
    headers: {
      "Authorization": `Bearer ${env.GH_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "sift-trace-relay",
      ...(init.headers || {}),
    },
  });
}

async function ok(res) {
  if (!res.ok) throw new Error(`github ${res.status}`);
  return res.json();
}

// a GitHub GET as JSON (null for 404), shared by all requests for CACHE_S seconds; `fresh` reads
// GitHub itself (and refreshes the shared copy)
async function cachedJson(env, path, accept, fresh = false) {
  const store = typeof caches !== "undefined" ? caches.default : null;
  const key = new Request(`https://relay.cache/${encodeURIComponent(path)}`);
  const hit = !fresh && store && await store.match(key);
  if (hit) return hit.json();
  const res = await gh(env, path, accept ? { headers: { Accept: accept } } : {});
  const body = res.status === 404 ? null : await ok(res);
  if (store) await store.put(key, new Response(JSON.stringify(body), { headers: { "Cache-Control": `max-age=${CACHE_S}` } }));
  return body;
}

// the saved trace of this address (radar-requests/<address>.json), or null
async function saved(env, mint) {
  try { return await cachedJson(env, `/contents/radar-requests/${mint}.json`, "application/vnd.github.raw+json"); } catch { return null; }
}

// the start of the 24-hour window, on the minute so the cache keys hold still
const daySince = () => new Date(Math.floor((Date.now() - 24 * 3600e3) / 60e3) * 60e3).toISOString().replace(/\.\d+Z$/, "Z");

// relay-started runs of trace.yml in the last 24 hours (newest first); each is named
// "trace <address> <visitor>". Every page is read: cancelled runs are listed too, and counted runs
// past the first 100 would otherwise drop out of the count. `partial`: more runs than were read.
async function runs(env, fresh = false) {
  const since = daySince(), seen = new Map();
  let total = 0;
  for (let page = 1; page <= MAX_PAGES; page++) {
    const q = `?event=workflow_dispatch&per_page=100&page=${page}&created=${encodeURIComponent(">=" + since)}`;
    const d = (await cachedJson(env, `/actions/workflows/${WORKFLOW}/runs${q}`, undefined, fresh)) || {};
    const batch = d.workflow_runs || [];
    total = Math.max(total, Number(d.total_count) || 0);
    for (const r of batch) seen.set(r.id, r);  // a run created between two pages shows up twice
    if (batch.length < 100) break;
  }
  const list = [...seen.values()].filter((r) => String(r.display_title || "").startsWith("trace "));
  return { list, partial: seen.size < total };
}

// trace requests opened as issues in the last 24 hours: they share the daily total with the relay.
// Best effort (0 when unreadable): trace.yml counts both again and refuses when it cannot.
async function issueTraces(env) {
  const since = daySince();
  let n = 0;
  try {
    for (let page = 1; page <= 3; page++) {
      const batch = (await cachedJson(env, `/issues?state=all&sort=created&since=${since}&per_page=100&page=${page}`)) || [];
      n += batch.filter((i) => !i.pull_request && String(i.created_at) >= since &&
        (String(i.title || "").startsWith("[trace]") || String(i.body || "").includes("### Token mint address"))).length;
      if (batch.length < 100) break;
    }
  } catch { /* counted again by trace.yml */ }
  return n;
}

// a Solana address: base58 that decodes to exactly 32 bytes (a made-up string of the right
// characters usually does not, and would cost a run)
function validMint(s) {
  if (!B58.test(s)) return false;
  let n = 0n;
  for (const c of s) n = n * 58n + BigInt(B58_ALPHABET.indexOf(c));
  const zeros = s.length - s.replace(/^1+/, "").length;  // each leading "1" is a zero byte
  const bytes = n === 0n ? 0 : Math.ceil(n.toString(16).length / 2);
  return zeros + bytes === 32;
}

const recent = (r) => Date.now() - Date.parse(r.created_at) < RUN_WINDOW_MS;

// the first four groups of an IPv6 address, with "::" expanded (2001:db8::1 -> 2001:db8:0:0)
function v6prefix(ip) {
  const [head, tail] = ip.split("::");
  const a = head ? head.split(":") : [], b = tail !== undefined && tail ? tail.split(":") : [];
  const full = tail === undefined ? a : [...a, ...Array(Math.max(0, 8 - a.length - b.length)).fill("0"), ...b];
  return full.slice(0, 4).map((g) => (parseInt(g, 16) || 0).toString(16)).join(":");
}

// a salted hash of the visitor's address; IPv6 by its /64, the block one connection usually holds
async function visitor(env, ip) {
  if (!env.SALT || env.SALT.length < 8) throw new Error("SALT is not set");  // never publish an unsalted hash
  // IPv4-mapped IPv6 (::ffff:1.2.3.4) is an IPv4 visitor; other IPv6 by its /64
  const key = ip.includes(".") ? ip.slice(ip.lastIndexOf(":") + 1) : ip.includes(":") ? v6prefix(ip) : ip;
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${env.SALT}|${key}`)));
  return [...hash.slice(0, 8)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

const fresh = (rec) => rec && Date.now() - Date.parse(rec.at) < FRESH_MS;
const done = (rec) => ({ status: "done", row: rec.row || null, at: rec.at });
const forMint = (list, mint) => list.filter((r) => r.display_title.startsWith(`trace ${mint} `));
const active = (r) => r.status !== "completed" && recent(r);

// why a new trace may not start, from the runs before it and the day's trace issues; null when it
// may. Cancelled runs traced nothing; a failed run (our side: the RPC node did not answer) spent
// the shared quota but does not use up the visitor's own three.
function refusal(day, issues, who, mint) {
  if (forMint(day, mint).some(active)) return { status: "running" };
  const counted = day.filter((r) => r.conclusion !== "cancelled");
  const mine = counted.filter((r) => r.conclusion !== "failure" && r.display_title.endsWith(` ${who}`));
  if (counted.length + issues >= DAILY_LIMIT || mine.length >= PER_VISITOR_LIMIT) return { status: "limit", reason: LIMIT_TEXT };
  if (day.filter(active).length >= MAX_ACTIVE) return { status: "busy", reason: BUSY_TEXT };
  return null;
}

async function start(env, mint, ip) {
  if (!validMint(mint)) return { status: "invalid", reason: INVALID_TEXT };
  const day = await runs(env);
  if (forMint(day.list, mint).some(active)) return { status: "running" };
  const rec = await saved(env, mint);
  if (fresh(rec)) return done(rec);
  const who = await visitor(env, ip);
  if (day.partial) return { status: "limit", reason: LIMIT_TEXT };  // more runs than can be counted
  const issues = await issueTraces(env);
  const no = refusal(day.list, issues, who, mint);
  if (no) return no;
  const since = new Date(Date.now() - 5e3).toISOString();  // the run this request starts is created after this
  const res = await gh(env, `/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main", inputs: { mint, requester: who }, return_run_details: true }),
  });
  if (res.status !== 200 && res.status !== 204) throw new Error(`dispatch ${res.status}`);
  const id = res.status === 200 ? Number((await res.json()).workflow_run_id) || 0 : 0;
  // Requests that arrive together all read the same list above and all pass. Count again now that
  // this run exists, against only the runs created before it (run ids rise with creation, so every
  // request ranks them the same way), and cancel this run if it is beyond a limit.
  if (id) {
    await new Promise((ok) => setTimeout(ok, Number(env.RECOUNT_MS ?? RECOUNT_MS)));
    const now = await runs(env, true);
    const before = now.list.filter((r) => r.id < id);
    const late = now.partial ? { status: "limit", reason: LIMIT_TEXT } : refusal(before, issues, who, mint);
    if (late) {
      try { await gh(env, `/actions/runs/${id}/cancel`, { method: "POST" }); } catch { /* trace.yml refuses it too */ }
      return late;
    }
  }
  return { status: "queued", since };
}

// why a finished run saved nothing: the reason trace.yml left as a "reply" annotation
async function reason(env, run) {
  try {
    const jobs = await cachedJson(env, `/actions/runs/${run.id}/jobs`) || {};
    for (const job of jobs.jobs || []) {
      const notes = await cachedJson(env, `${job.check_run_url}/annotations`) || [];
      const hit = notes.find((a) => a.title === "reply");
      if (hit) return hit.message.split("\n")[0];
    }
  } catch { /* no reason available */ }
  return null;
}

async function status(env, mint, since) {
  if (!validMint(mint)) return { status: "invalid", reason: INVALID_TEXT };
  // `since`: the page started its own run then, so an earlier finished run of this token is not its answer
  const after = Date.parse(since) || 0;
  // only addresses with a recent run are looked up further: polling made-up addresses costs nothing
  const run = forMint((await runs(env)).list, mint).filter((r) => recent(r) && Date.parse(r.created_at) >= after)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0];
  if (!run) return { status: "none" };
  if (run.status !== "completed") return { status: run.status === "in_progress" ? "running" : "queued" };
  const rec = await saved(env, mint);
  if (rec && Date.parse(rec.at) >= Date.parse(run.created_at) - 60e3) return done(rec);
  if (run.conclusion === "cancelled") return { status: "failed", reason: BUSY_TEXT };
  return { status: "failed", reason: (await reason(env, run)) || "The trace did not finish. Please try again in a few minutes." };
}
