// Trace relay for the Insider Radar page. The page asks it to trace a token and then asks for the
// result; it starts trace.yml in this repository and reads the saved trace back, so a visitor never
// has to open GitHub. Runs as a Cloudflare Worker (relay/README.md).
//
// Secrets: GH_TOKEN, a fine-grained token for this repository only with Actions read and write and
// Contents read; SALT, any long random string (visitor addresses are only kept as salted hashes).

const REPO = "bernardhaddad/sift";
const WORKFLOW = "trace.yml";
const ORIGIN = "https://bernardhaddad.github.io";
const DAILY_LIMIT = 40;      // traces a day, all visitors together (same as trace.yml)
const PER_VISITOR_LIMIT = 3; // traces a day per visitor
const FRESH_MS = 2 * 3600e3; // a trace this recent is returned instead of a new one
const RUN_WINDOW_MS = 30 * 60e3;
const B58 = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

const LIMIT_TEXT = `The free daily limit is reached (${PER_VISITOR_LIMIT} traces a day per visitor, ${DAILY_LIMIT} in total). Please try again tomorrow.`;
const INVALID_TEXT = "That does not look like a Solana token address (base58, 32 to 44 characters).";

export default {
  async fetch(req, env) {
    const cors = {
      "Access-Control-Allow-Origin": ORIGIN,
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Vary": "Origin",
    };
    if (req.method === "OPTIONS") return new Response(null, { headers: cors });
    const url = new URL(req.url);
    if (url.pathname !== "/trace") return reply({ error: "not found" }, 404, cors);
    try {
      if (req.method === "GET") return reply(await status(env, (url.searchParams.get("mint") || "").trim()), 200, cors);
      if (req.method === "POST") {
        let body = {};
        try { body = await req.json(); } catch { /* empty body: handled as an invalid address */ }
        const ip = req.headers.get("CF-Connecting-IP") || "";
        return reply(await start(env, String(body.mint || "").trim(), ip), 200, cors);
      }
      return reply({ error: "method not allowed" }, 405, cors);
    } catch {
      return reply({ status: "error", reason: "The tracer is not reachable right now. Please try again in a few minutes." }, 502, cors);
    }
  },
};

function reply(body, code, headers) {
  return new Response(JSON.stringify(body), { status: code, headers: { ...headers, "Content-Type": "application/json", "Cache-Control": "no-store" } });
}

function gh(env, path, init = {}) {
  const url = path.startsWith("https://") ? path : `https://api.github.com/repos/${REPO}${path}`;
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

// the saved trace of this address (radar-requests/<address>.json), or null
async function saved(env, mint) {
  const res = await gh(env, `/contents/radar-requests/${mint}.json`, { headers: { Accept: "application/vnd.github.raw+json" } });
  if (res.status === 404) return null;
  try { return await ok(res); } catch { return null; }
}

// relay-started runs of trace.yml since a moment; each is named "trace <address> <visitor>"
async function runs(env, sinceMs) {
  const since = new Date(Date.now() - sinceMs).toISOString().replace(/\.\d+Z$/, "Z");
  const q = `?event=workflow_dispatch&per_page=100&created=${encodeURIComponent(">=" + since)}`;
  const d = await ok(await gh(env, `/actions/workflows/${WORKFLOW}/runs${q}`));
  return (d.workflow_runs || []).filter((r) => String(r.display_title || "").startsWith("trace "));
}

async function visitor(env, ip) {
  const day = new Date().toISOString().slice(0, 10);
  const bytes = new TextEncoder().encode(`${env.SALT || ""}|${day}|${ip}`);
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...hash.slice(0, 8)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

const fresh = (rec) => rec && Date.now() - Date.parse(rec.at) < FRESH_MS;
const done = (rec) => ({ status: "done", row: rec.row || null, at: rec.at });
const forMint = (list, mint) => list.filter((r) => r.display_title.startsWith(`trace ${mint} `));

async function start(env, mint, ip) {
  if (!B58.test(mint)) return { status: "invalid", reason: INVALID_TEXT };
  const rec = await saved(env, mint);
  if (fresh(rec)) return done(rec);
  const day = await runs(env, 24 * 3600e3);
  if (forMint(day, mint).some((r) => r.status !== "completed" && Date.now() - Date.parse(r.created_at) < RUN_WINDOW_MS)) {
    return { status: "running" };
  }
  const who = await visitor(env, ip);
  if (day.length >= DAILY_LIMIT || day.filter((r) => r.display_title.endsWith(` ${who}`)).length >= PER_VISITOR_LIMIT) {
    return { status: "limit", reason: LIMIT_TEXT };
  }
  const res = await gh(env, `/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main", inputs: { mint, requester: who } }),
  });
  if (res.status !== 204) throw new Error(`dispatch ${res.status}`);
  return { status: "queued" };
}

// why a finished run saved nothing: the reason trace.yml left as a "reply" annotation
async function reason(env, run) {
  try {
    const jobs = await ok(await gh(env, `/actions/runs/${run.id}/jobs`));
    for (const job of jobs.jobs || []) {
      const notes = await ok(await gh(env, `${job.check_run_url}/annotations`));
      const hit = notes.find((a) => a.title === "reply");
      if (hit) return hit.message.split("\n")[0];
    }
  } catch { /* no reason available */ }
  return null;
}

async function status(env, mint) {
  if (!B58.test(mint)) return { status: "invalid", reason: INVALID_TEXT };
  const run = forMint(await runs(env, RUN_WINDOW_MS), mint)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0];
  if (!run) {
    const rec = await saved(env, mint);
    return fresh(rec) ? done(rec) : { status: "none" };
  }
  if (run.status !== "completed") return { status: run.status === "in_progress" ? "running" : "queued" };
  const rec = await saved(env, mint);
  if (rec && Date.parse(rec.at) >= Date.parse(run.created_at) - 60e3) return done(rec);
  return { status: "failed", reason: (await reason(env, run)) || "The trace did not finish. Please try again in a few minutes." };
}
