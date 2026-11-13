// node relay/test.mjs: the relay against a simulated GitHub API (no network)
import w from "./worker.js";
const MINT = "2fk5eGwAch1wGhsK3XhRUZpopXFGyRbFUn6XL4Yxpump", OTHER = "8RpybikpGLbKsYJ4FXz12ExDz89R7VXAUZ3JXNcKpump";
// auto: a dispatch adds its run to the list, as GitHub does a moment later
let state = { runs: [], file: null, dispatched: [], notes: [], urls: [], issues: [], cancelled: [], auto: false, nextId: 1000 };
globalThis.fetch = async (url, init = {}) => {
  url = String(url); state.urls.push(url);
  const J = (b, s = 200) => new Response(JSON.stringify(b), { status: s });
  const page = (list) => { const q = new URL(url).searchParams, n = +q.get("per_page") || 30, p = +q.get("page") || 1; return list.slice((p - 1) * n, p * n); };
  if (url.includes("/contents/radar-requests/")) return state.file ? J(state.file) : J({}, 404);
  if (url.includes("/runs?")) return J({ total_count: state.runs.length, workflow_runs: page(state.runs) });
  if (url.includes("/issues?")) return J(page(state.issues));
  if (url.endsWith("/cancel")) {
    const id = +url.split("/").at(-2); state.cancelled.push(id);
    state.runs.filter((x) => x.id === id).forEach((x) => { x.status = "completed"; x.conclusion = "cancelled"; });
    return new Response(null, { status: 202 });
  }
  if (url.endsWith("/dispatches")) {
    const b = JSON.parse(init.body), id = state.nextId++; state.dispatched.push(b);
    if (state.auto) state.runs.unshift({ id, display_title: `trace ${b.inputs.mint} ${b.inputs.requester}`, status: "queued", conclusion: null, created_at: new Date().toISOString() });
    return b.return_run_details ? J({ workflow_run_id: id }) : new Response(null, { status: 204 });
  }
  if (url.includes("/jobs")) return J({ jobs: [{ check_run_url: "https://api.github.com/repos/bernardhaddad/sift/check-runs/1" }] });
  if (url.endsWith("/annotations")) return J(state.notes);
  if (url.includes("siteverify")) { const t = init.body.get("response"); return J({ success: t !== "bad", hostname: t === "offsite" ? "evil.github.io" : "bernardhaddad.github.io", action: t === "otheraction" ? "login" : "trace" }); }
  throw new Error("unexpected " + url);
};
const env = { GH_TOKEN: "t", SALT: "a-long-salt", PRODUCTION_ONLY: "off", TURNSTILE: "off", RECOUNT_MS: "0" };
const call = async (m, body, q = "", ip = "1.2.3.4", e = env) => (await w.fetch(new Request("https://r.dev/trace" + q, m === "POST" ? { method: "POST", body: JSON.stringify(body), headers: { "CF-Connecting-IP": ip } } : {}), e)).json();
const eq = (a, b, l) => { if (JSON.stringify(a) !== JSON.stringify(b)) { console.log("FAIL", l, JSON.stringify(a)); process.exitCode = 1; } else console.log("ok", l); };
const ago = (ms) => new Date(Date.now() - ms).toISOString();
eq((await call("POST", { mint: "bad" })).status, "invalid", "invalid");
let r = await call("POST", { mint: MINT }); eq(r.status, "queued", "queued"); eq(state.dispatched[0].inputs.mint, MINT, "dispatch mint");
const since = r.since; eq(typeof since, "string", "since returned");
const who = state.dispatched[0].inputs.requester;
state.runs = [{ id: 1, display_title: `trace ${MINT} ${who}`, status: "queued", conclusion: null, created_at: ago(0) }];
eq((await call("GET", null, `?mint=${MINT}&since=${since}`)).status, "queued", "poll queued");
eq((await call("POST", { mint: MINT })).status, "running", "no double dispatch");
state.runs[0].status = "in_progress"; eq((await call("GET", null, `?mint=${MINT}`)).status, "running", "poll running");
state.runs[0].status = "completed"; state.runs[0].conclusion = "success"; state.notes = [{ title: "reply", message: "`X` is a wallet address, not a token.\nmore" }];
r = await call("GET", null, `?mint=${MINT}`); eq([r.status, r.reason], ["failed", "`X` is a wallet address, not a token."], "failed reason");
r = await call("GET", null, `?mint=${MINT}&since=${new Date(Date.now() + 60e3).toISOString()}`); eq(r.status, "none", "older run is not this request's answer");
state.file = { mint: MINT, at: new Date(Date.now() + 1000).toISOString(), row: { mint: MINT, grade: "high" } };
r = await call("GET", null, `?mint=${MINT}`); eq([r.status, r.row.grade], ["done", "high"], "done");
eq((await call("POST", { mint: MINT })).status, "done", "fresh cached");
state.file = null; state.urls = [];
eq((await call("GET", null, `?mint=${OTHER}`)).status, "none", "unknown mint");
eq(state.urls.filter((u) => u.includes("/contents/")).length, 0, "no file lookup for a mint without a run");
state.runs = [{ id: 9, display_title: `trace ${OTHER} ${who}`, status: "completed", conclusion: "cancelled", created_at: ago(0) }];
eq((await call("GET", null, `?mint=${OTHER}`)).status, "failed", "cancelled run reported");
state.runs = [1, 2, 3].map((i) => ({ id: i, display_title: `trace M${i} ${who}`, status: "completed", conclusion: "success", created_at: ago(0) }));
eq((await call("POST", { mint: OTHER })).status, "limit", "per visitor limit");
state.runs.forEach((x) => x.conclusion = "cancelled");
eq((await call("POST", { mint: OTHER })).status, "queued", "cancelled runs not counted");
state.runs = Array.from({ length: 40 }, (_, i) => ({ id: i, display_title: `trace M${i} other`, status: "completed", conclusion: "success", created_at: ago(0) }));
eq((await call("POST", { mint: OTHER })).status, "limit", "daily limit");
state.runs = [1, 2].map((i) => ({ id: i, display_title: `trace M${i} x`, status: "in_progress", conclusion: null, created_at: ago(0) }));
eq((await call("POST", { mint: OTHER })).status, "busy", "busy when two are active");
state.runs = [];
state.dispatched = [];
await call("POST", { mint: OTHER }, "", "2001:db8:1:2:aaaa::1"); await call("POST", { mint: MINT }, "", "2001:db8:1:2:bbbb::2");
eq(state.dispatched[0].inputs.requester === state.dispatched[1].inputs.requester, true, "IPv6 keyed by /64");
state.runs = []; state.dispatched = [];
await call("POST", { mint: OTHER }, "", "2001:db8::aaaa:1"); await call("POST", { mint: MINT }, "", "2001:db8:0:0:bbbb::9");
eq(state.dispatched[0].inputs.requester === state.dispatched[1].inputs.requester, true, "compressed IPv6 keyed by the same /64");
state.runs = []; state.dispatched = [];
await call("POST", { mint: OTHER }, "", "::ffff:1.2.3.4"); await call("POST", { mint: MINT }, "", "::ffff:5.6.7.8"); await call("POST", { mint: MINT }, "", "1.2.3.4");
const [m1, m2, m3] = state.dispatched.map((d) => d.inputs.requester);
eq([m1 !== m2, m1 === m3], [true, true], "IPv4-mapped visitors kept apart and matched to their IPv4");
eq((await call("POST", { mint: OTHER }, "", "1.2.3.4", { GH_TOKEN: "t", PRODUCTION_ONLY: "off", TURNSTILE: "off" })).status, "error", "no SALT fails closed");
const strict = { GH_TOKEN: "t", SALT: "a-long-salt", PRODUCTION_ONLY: "off", TURNSTILE_SECRET: "s", RECOUNT_MS: "0" };
state.runs = []; state.dispatched = [];
eq((await call("POST", { mint: OTHER }, "", "9.9.9.9", strict)).status, "check", "no Turnstile token refused");
eq((await call("POST", { mint: OTHER, token: "bad" }, "", "9.9.9.9", strict)).status, "check", "failed Turnstile token refused");
eq(state.dispatched.length, 0, "nothing dispatched without the check");
eq((await call("POST", { mint: OTHER, token: "offsite" }, "", "9.9.9.9", strict)).status, "check", "token from another site refused");
eq((await call("POST", { mint: OTHER, token: "otheraction" }, "", "9.9.9.9", strict)).status, "check", "token for another action refused");
eq((await call("POST", { mint: OTHER, token: "good" }, "", "9.9.9.9", { GH_TOKEN: "t", SALT: "a-long-salt", PRODUCTION_ONLY: "off" })).status, "check", "missing secret refuses");
eq((await call("POST", { mint: OTHER, token: "good" }, "", "9.9.9.9", strict)).status, "queued", "passed Turnstile token accepted");
const prod = { GH_TOKEN: "t", SALT: "a-long-salt", TURNSTILE: "off" };
eq((await w.fetch(new Request("https://8ccf2c00.sift-trace.pages.dev/trace?mint=" + MINT), prod)).status, 404, "old or preview build address refused");
eq((await w.fetch(new Request("https://sift-trace.pages.dev/trace?mint=bad"), prod)).status, 200, "production address answers");
const pre = await w.fetch(new Request("https://r.dev/trace", { method: "OPTIONS" }), env);
eq(pre.headers.get("Access-Control-Allow-Origin"), "https://bernardhaddad.github.io", "cors");
const red = await w.fetch(new Request("https://r.dev/"), env); eq([red.status, red.headers.get("location")], [302, "https://bernardhaddad.github.io/sift/radar.html"], "browser visit redirected");
eq((await w.fetch(new Request("https://sift-trace.x.workers.dev/trace?mint=bad"), { ...prod, RELAY_HOST: "sift-trace.x.workers.dev" })).status, 200, "RELAY_HOST moves the allowed address");

// a burst of POSTs reads one run list and passes the checks together; each request re-counts after
// its dispatch and cancels its own run if it is beyond a limit (finding 17)
const mints = Array.from({ length: 10 }, (_, i) => MINT.slice(0, 40) + "abc" + "123456789A"[i]);
state = { ...state, runs: [], dispatched: [], cancelled: [], auto: true };
let rs = await Promise.all(mints.map((m) => call("POST", { mint: m }, "", "4.4.4.4")));
const live = state.runs.filter((x) => x.conclusion !== "cancelled");
eq([state.dispatched.length, live.length, state.cancelled.length], [10, 2, 8], "burst: surplus runs cancelled, MAX_ACTIVE kept");
eq(rs.filter((x) => x.status === "queued").length, 2, "burst: two queued, the rest refused");
eq(state.dispatched.every((d) => d.return_run_details === true), true, "dispatch asks for the run id");
state.runs = []; state.cancelled = []; state.dispatched = [];
state.runs = [1, 2].map((i) => ({ id: i, display_title: `trace M${i} z`, status: "completed", conclusion: "success", created_at: ago(0) }));
rs = await Promise.all(mints.slice(0, 5).map((m) => call("POST", { mint: m }, "", "4.4.4.5")));
eq([rs.filter((x) => x.status === "queued").length, state.runs.filter((x) => x.status !== "completed").length], [2, 2], "burst: two active at most");
state.runs.forEach((x) => { x.status = "completed"; x.conclusion = x.conclusion || "success"; });
rs = await Promise.all(mints.slice(5).map((m) => call("POST", { mint: m }, "", "4.4.4.5")));
eq(state.runs.filter((x) => x.display_title.endsWith(` ${state.dispatched[0].inputs.requester}`) && x.conclusion !== "cancelled").length, 3, "burst: per-visitor limit kept");
eq(rs.some((x) => x.status === "limit"), true, "burst: surplus over the visitor limit told so");
state.auto = false;

// more than 100 runs in a day: older counted runs are on later pages (finding 18)
state.runs = [...Array.from({ length: 100 }, (_, i) => ({ id: 1000 + i, display_title: `trace M${i} c${i}`, status: "completed", conclusion: "cancelled", created_at: ago(0) })),
  ...Array.from({ length: 40 }, (_, i) => ({ id: i, display_title: `trace M${i} y`, status: "completed", conclusion: "success", created_at: ago(3600e3) }))];
state.dispatched = [];
eq((await call("POST", { mint: OTHER }, "", "7.7.7.7")).status, "limit", "daily limit counts runs past the first page");
eq(state.dispatched.length, 0, "nothing dispatched past the daily limit");

// trace issues and relay traces share one daily total (finding 39)
const issue = (i) => ({ number: i, title: `[trace] M${i}`, body: "", created_at: ago(60e3) });
state.runs = Array.from({ length: 20 }, (_, i) => ({ id: i, display_title: `trace M${i} y`, status: "completed", conclusion: "success", created_at: ago(0) }));
state.issues = Array.from({ length: 20 }, (_, i) => issue(i));
eq((await call("POST", { mint: OTHER }, "", "7.7.7.8")).status, "limit", "issue traces count toward the daily total");
state.issues = Array.from({ length: 19 }, (_, i) => issue(i)).concat([{ number: 99, title: "a bug", body: "", created_at: ago(0) }, { ...issue(98), pull_request: {} }]);
eq((await call("POST", { mint: OTHER }, "", "7.7.7.8")).status, "queued", "other issues and pull requests not counted");
state.issues = [];

// only real 32-byte base58 keys are dispatched (finding 40)
state.runs = []; state.dispatched = [];
eq((await call("POST", { mint: "z".repeat(44) })).status, "invalid", "base58 text that is not a 32-byte key refused");
eq((await call("POST", { mint: "2".repeat(32) })).status, "invalid", "too short a key refused");
eq((await call("GET", null, `?mint=${"z".repeat(44)}`)).status, "invalid", "status checks the key too");
eq(state.dispatched.length, 0, "no dispatch for a made-up address");
eq((await call("POST", { mint: "11111111111111111111111111111111" })).status, "queued", "a 32-byte key with leading zeros accepted");

// a trace that failed on our side does not use up the visitor's three (finding 41)
state.runs = []; state.dispatched = [];
await call("POST", { mint: OTHER }, "", "6.6.6.6"); const v6 = state.dispatched[0].inputs.requester;
state.runs = [1, 2, 3].map((i) => ({ id: i, display_title: `trace M${i} ${v6}`, status: "completed", conclusion: "failure", created_at: ago(0) }));
eq((await call("POST", { mint: OTHER }, "", "6.6.6.6")).status, "queued", "failed traces not counted against the visitor");
state.runs = Array.from({ length: 40 }, (_, i) => ({ id: i, display_title: `trace M${i} q`, status: "completed", conclusion: "failure", created_at: ago(0) }));
eq((await call("POST", { mint: OTHER }, "", "6.6.6.7")).status, "limit", "failed traces still count toward the daily total");

// a dispatch whose run id is beyond a limit at the re-count is cancelled
state.runs = [1, 2].map((i) => ({ id: i, display_title: `trace M${i} x`, status: "in_progress", conclusion: null, created_at: ago(0) }));
state.cancelled = []; state.nextId = 5;
const race = { ...env };
let first = true;
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, init) => {  // the first read misses the two running runs (list lag)
  if (first && String(url).includes("/runs?")) { first = false; return new Response(JSON.stringify({ total_count: 0, workflow_runs: [] })); }
  return realFetch(url, init);
};
r = await call("POST", { mint: OTHER }, "", "3.3.3.3", race);
globalThis.fetch = realFetch;
eq([r.status, state.cancelled], ["busy", [5]], "late-seen runs: the new run is cancelled");

// ---- the visit counter: POST /hit and GET /stats, against a D1 stand-in (no network) ----
const SITE = "https://bernardhaddad.github.io", KEY = "a-long-random-stats-key-for-the-tests-0123";
const TOKEN_PAGE = `/sift/t/${MINT}.html`;
const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = (n) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);
// keeps every statement and value it is given (`seen`) and runs the counter's statements on a Map: the
// INSERT, and each SELECT as SQL would (SUM(n) over the days from ? to ?, grouped by the named columns)
function d1() {
  const db = { seen: [], rows: new Map(), fail: false, selects: 0 };
  const sums = (sql, [from, to]) => {
    const m = /^SELECT ([\w, ]+), SUM\(n\) AS n FROM hits WHERE day >= \? AND day <= \? GROUP BY ([\w, ]+)$/.exec(sql);
    if (!m || m[1] !== m[2]) throw new Error(`the stand-in does not run: ${sql}`);
    const cols = m[2].split(", "), out = new Map();
    for (const [k, n] of db.rows) {
      const [day, page, src, ref, country] = k.split("|"), row = { day, page, src, ref, country };
      if (day < from || day > to) continue;
      const key = cols.map((c) => row[c]).join("|");
      const sum = out.get(key) || { ...Object.fromEntries(cols.map((c) => [c, row[c]])), n: 0 };
      sum.n += n;
      out.set(key, sum);
    }
    return [...out.values()];
  };
  const run = async (sql, args) => {
    if (db.fail) throw new Error("D1_ERROR: no such table: hits at 10.0.0.7");
    db.seen.push(sql, ...args);
    if (sql.startsWith("INSERT INTO hits ")) db.rows.set(args.join("|"), (db.rows.get(args.join("|")) || 0) + 1);
    if (sql.startsWith("SELECT ")) db.selects++;
    return { success: true, results: sql.startsWith("SELECT ") ? sums(sql, args) : [] };
  };
  const stmt = (sql, args = []) => ({ sql, args, bind: (...a) => stmt(sql, a), run: () => run(sql, args), all: () => run(sql, args) });
  db.prepare = (sql) => stmt(sql);
  db.batch = async (list) => { const out = []; for (const s of list) out.push(await run(s.sql, s.args)); return out; };
  return db;
}
const keys = (db, i) => [...db.rows.keys()].map((k) => k.split("|")[i]);
const total = (db) => [...db.rows.values()].reduce((a, b) => a + b, 0);
// a page's beacon: a text/plain POST with the site's Origin (origin: null sends none, cf: null no request.cf)
const hit = (body, { e, origin = SITE, headers = {}, cf = { country: "DE" }, ctx, host = "https://r.dev" } = {}) => {
  const req = new Request(`${host}/hit`, { method: "POST", body: typeof body === "string" ? body : JSON.stringify(body),
    headers: { "Content-Type": "text/plain;charset=UTF-8", ...(origin ? { Origin: origin } : {}), ...headers } });
  if (cf) Object.defineProperty(req, "cf", { value: cf });
  return w.fetch(req, e, ctx);
};
const statsOf = (q, auth, e, host = "https://r.dev") =>
  w.fetch(new Request(`${host}/stats${q}`, auth === undefined ? {} : { headers: { Authorization: auth } }), e);
// records which request headers are read, and whether the headers are listed
function spyHeaders() {
  const P = Headers.prototype, saved = new Map(), out = { read: [], listed: false };
  for (const k of ["get", "has"]) { saved.set(k, P[k]); P[k] = function (n) { out.read.push(String(n).toLowerCase()); return saved.get(k).call(this, n); }; }
  for (const k of ["entries", "keys", "values", "forEach", Symbol.iterator]) { saved.set(k, P[k]); P[k] = function (...a) { out.listed = true; return saved.get(k).apply(this, a); }; }
  out.stop = () => saved.forEach((f, k) => { P[k] = f; });
  return out;
}

let db = d1();
const cenv = { ...env, STATS_DB: db, STATS_KEY: KEY };
state.urls = [];
// a visit carrying every identifying header Cloudflare and the browser send: only the five fields reach D1
const IP = "203.0.113.9", UA = "Mozilla/5.0 (X11; Linux x86_64) TestAgent/1.0", cfRead = [];
const cf = new Proxy({ country: "DE", city: "Berlin", postalCode: "10115", asn: 3320, latitude: "52.52", longitude: "13.40", clientTcpRtt: 12 },
  { get: (t, k) => { cfRead.push(String(k)); return t[k]; } });
const spy = spyHeaders();
let res = await hit({ p: "/sift/radar.html", s: "tg", r: "t.me" }, { e: cenv, cf, headers: { "CF-Connecting-IP": IP, "X-Forwarded-For": "198.51.100.4",
  "X-Real-IP": "198.51.100.5", "True-Client-IP": "198.51.100.6", "User-Agent": UA, Cookie: "sid=abc123", Referer: "https://t.me/s/abc123" } });
spy.stop();
eq([res.status, await res.text()], [204, ""], "hit: 204 with no body");
eq([...db.rows], [[`${today()}|radar|tg|t.me|DE`, 1]], "hit: one count for the day, page, source, referrer and country");
const sent = JSON.stringify(db.seen);
eq([IP, "198.51.100", "TestAgent", "abc123", "Berlin", "10115", "3320", "52.52"].filter((x) => sent.includes(x)), [], "hit: no IP address, user agent, cookie or other request detail reaches D1");
eq(spy.read.filter((h) => !["origin", "content-length"].includes(h)), [], "hit: reads no header but Origin and Content-Length");
eq(spy.listed, false, "hit: never lists the request headers");
eq(cfRead, ["country"], "hit: reads only the country from request.cf");
eq(db.seen[0].startsWith("CREATE TABLE IF NOT EXISTS hits ") && db.seen[0].includes("PRIMARY KEY (day, page, src, ref, country)"), true, "hit: the table is created when first needed");

// every site page by its name and every token page as t; a repeat visit adds one to the same total
db = cenv.STATS_DB = d1();
for (const p of ["/sift/", "/sift/index.html", "/sift/radar.html", "/sift/radar", "/sift/research.html", "/sift/privacy.html", "/sift/terms.html",
  "/sift/404.html", TOKEN_PAGE, `/sift/t/${OTHER}.html`]) await hit({ p, s: "", r: "" }, { e: cenv });
const pages = {};
db.rows.forEach((n, k) => { const pg = k.split("|")[1]; pages[pg] = (pages[pg] || 0) + n; });
eq(Object.entries(pages).sort(), [["404", 1], ["index", 2], ["privacy", 1], ["radar", 2], ["research", 1], ["t", 2], ["terms", 1]], "hit: site paths counted by page, token pages as t");
for (const s of ["x", "tg", "rss", "reddit", "gh", "hn", "mail"]) await hit({ p: "/sift/", s, r: "" }, { e: cenv });
eq(keys(db, 2).filter(Boolean).sort(), ["gh", "hn", "mail", "reddit", "rss", "tg", "x"], "hit: every source tag counted");
db = cenv.STATS_DB = d1();
await hit({ p: "/sift/" }, { e: cenv });
eq([...db.rows.keys()], [`${today()}|index|||DE`], "hit: a missing source and referrer count as none");

// a wrong origin, an unknown path or source, or a body that is not one small JSON object counts nothing
db = cenv.STATS_DB = d1();
const ok = { p: "/sift/radar.html", s: "", r: "" };
for (const [body, o, label] of [
  [ok, { origin: null }, "no Origin"], [ok, { origin: "https://evil.example" }, "another site"],
  [ok, { origin: "https://bernardhaddad.github.io.evil.example" }, "a look-alike origin"],
  [ok, { origin: "http://bernardhaddad.github.io" }, "the site over http"], [ok, { origin: "null" }, "an opaque origin"],
  [{ ...ok, p: "/sift/radar.json" }, {}, "a data file"], [{ ...ok, p: "/other/radar.html" }, {}, "a path outside /sift/"],
  [{ ...ok, p: "/sift/t/abc.html" }, {}, "a token page with no address"], [{ ...ok, p: `${SITE}/sift/radar.html` }, {}, "a full address"],
  [{ ...ok, p: "constructor" }, {}, "an object key as the path"], [{ ...ok, p: ["/sift/radar.html"] }, {}, "a path that is not text"],
  [{ ...ok, s: "facebook" }, {}, "an unknown source"], [{ ...ok, s: "X" }, {}, "a source tag in capitals"],
  [{ ...ok, s: "toString" }, {}, "an object key as the source"], [{ ...ok, s: 1 }, {}, "a source that is not text"],
  [{ ...ok, r: { host: "t.me" } }, {}, "a referrer that is not text"],
  ["not json", {}, "a body that is not JSON"], ["[]", {}, "a JSON array"], ["", {}, "an empty body"],
  [{ ...ok, pad: "x".repeat(600) }, {}, "a body over 512 bytes"],
  [ok, { headers: { "Content-Length": "4096" } }, "a declared length over 512 bytes"],
]) {
  res = await hit(body, { e: cenv, ...o });
  eq([res.status, db.seen.length], [204, 0], `hit: ${label} counts nothing`);
}
res = await hit({ ...ok, pad: "x".repeat(400) }, { e: cenv });
eq([res.status, total(db)], [204, 1], "hit: a body under 512 bytes counts, extra fields ignored");
// sent in pieces with no declared length (valid JSON once whole): read no further than 512 bytes
let pulled = 0, stopped = false;
const space = new TextEncoder().encode(" ".repeat(300)), tail = new TextEncoder().encode(JSON.stringify(ok));
const pieces = new ReadableStream({ pull(c) { pulled++; if (pulled <= 50) c.enqueue(space); else { c.enqueue(tail); c.close(); } }, cancel() { stopped = true; } });
res = await w.fetch(new Request("https://r.dev/hit", { method: "POST", body: pieces, duplex: "half", headers: { Origin: SITE } }), cenv);
eq([res.status, total(db), pulled <= 3, stopped], [204, 1, true, true], "hit: a streamed body is read no further than 512 bytes, and counts nothing");

// the referring host is kept only from a short list (a leading www. dropped); any other is "other"
for (const [r, want] of [["t.me", "t.me"], ["x.com", "x.com"], ["twitter.com", "twitter.com"], ["t.co", "t.co"],
  ["www.reddit.com", "reddit.com"], ["reddit.com", "reddit.com"], ["old.reddit.com", "old.reddit.com"],
  ["news.ycombinator.com", "news.ycombinator.com"], ["github.com", "github.com"], ["www.google.com", "google"],
  ["www.google.co.uk", "google"], ["google.de", "google"], ["www.google.com.br", "google"], ["www.bing.com", "bing.com"],
  ["duckduckgo.com", "duckduckgo.com"], ["bernardhaddad.github.io", "bernardhaddad.github.io"], ["T.ME", "t.me"], ["", ""],
  ["evil.example", "other"], ["google.evil.example", "other"], ["t.me.evil.example", "other"], ["mail.google.com", "other"],
  ["https://t.me/somechannel", "other"], ["192.0.2.1", "other"], ["www.", "other"], ["x".repeat(300), "other"]]) {
  db = cenv.STATS_DB = d1();
  await hit({ ...ok, r }, { e: cenv });
  eq(keys(db, 3), [want], `hit: referrer ${JSON.stringify(r.slice(0, 40))} counted as ${JSON.stringify(want)}`);
}
// every Google search domain is one referrer, so a forged beacon cannot add a row per made-up domain
// (google.zz, google.co.qq, ...): one client in one country makes at most 7 x 8 x 14 rows a day
db = cenv.STATS_DB = d1();
const letters = "abcdefghijklmnopqrstuvwxyz";
const madeUp = [...letters].flatMap((a) => [...letters].map((b) => a + b)).flatMap((cc) => [`google.${cc}`, `google.co.${cc}`, `google.com.${cc}`]);
for (const r of ["google.com", ...madeUp]) await hit({ ...ok, r }, { e: cenv });
eq([db.rows.size, keys(db, 3).slice(0, 3), total(db)], [1, ["google"], 1 + madeUp.length], `hit: ${1 + madeUp.length} Google domains, real or made up, make one row`);
db = cenv.STATS_DB = d1();
for (const p of ["/sift/", "/sift/radar.html", "/sift/research.html", "/sift/privacy.html", "/sift/terms.html", "/sift/404.html", TOKEN_PAGE]) {
  for (const s of ["", "x", "tg", "rss", "reddit", "gh", "hn", "mail"]) {
    for (const r of ["", "t.me", "x.com", "twitter.com", "t.co", "reddit.com", "old.reddit.com", "news.ycombinator.com", "github.com",
      "google.com", "google.zz", "bing.com", "duckduckgo.com", "bernardhaddad.github.io", "elsewhere.example", "google.evil.example"]) await hit({ p, s, r }, { e: cenv });
  }
}
eq(db.rows.size, 7 * 8 * 14, "hit: every page, tag and referrer from one country make at most 784 rows a day");
// the country as Cloudflare reports it, XX when it is missing or not a country code
for (const [c, want] of [[null, "XX"], [{}, "XX"], [{ country: "T1" }, "T1"], [{ country: "de" }, "XX"], [{ country: "<b>" }, "XX"], [{ country: 49 }, "XX"]]) {
  db = cenv.STATS_DB = d1();
  await hit(ok, { e: cenv, cf: c });
  eq(keys(db, 4), [want], `hit: country ${JSON.stringify(c)} counted as ${want}`);
}

// switched off, unbound or failing: still 204, nothing counted and nothing said
db = d1();
res = await hit(ok, { e: { ...cenv, STATS_DB: db, COUNTER: "off" } });
eq([res.status, db.seen.length], [204, 0], "hit: COUNTER=off counts nothing");
const unbound = { ...cenv };
delete unbound.STATS_DB;
res = await hit(ok, { e: unbound });
eq([res.status, await res.text()], [204, ""], "hit: without STATS_DB nothing is counted and nothing fails");
db = d1();
db.fail = true;
res = await hit(ok, { e: { ...cenv, STATS_DB: db } });
eq([res.status, await res.text()], [204, ""], "hit: a D1 error still answers 204, and says nothing");
// as a Worker (with waitUntil) the answer does not wait for the write
db = d1();
const later = [];
res = await hit(ok, { e: { ...cenv, STATS_DB: db }, ctx: { waitUntil: (p) => later.push(p) } });
eq([res.status, later.length], [204, 1], "hit: with waitUntil the answer does not wait for D1");
await Promise.all(later);
eq(total(db), 1, "hit: and the count lands after it");

// the production-address rule covers the counter; other methods on its paths answer as before
db = d1();
const prodEnv = { GH_TOKEN: "t", SALT: "a-long-salt", TURNSTILE: "off", STATS_DB: db, STATS_KEY: KEY };
eq((await hit(ok, { e: prodEnv, host: "https://8ccf2c00.sift-trace.pages.dev" })).status, 404, "hit: a preview build address answers 404");
eq(db.seen.length, 0, "hit: and counts nothing");
eq([(await hit(ok, { e: prodEnv, host: "https://sift-trace.pages.dev" })).status, total(db)], [204, 1], "hit: the production address counts");
eq((await statsOf("", `Bearer ${KEY}`, prodEnv, "https://8ccf2c00.sift-trace.pages.dev")).status, 404, "stats: a preview build address answers 404");
const visit = await w.fetch(new Request("https://r.dev/hit"), cenv);
eq([visit.status, visit.headers.get("location")], [302, `${SITE}/sift/radar.html`], "GET /hit is a browser visit: redirected to the radar as before");
eq((await w.fetch(new Request("https://r.dev/stats", { method: "POST", body: "{}" }), cenv)).status, 404, "POST /stats is not found, as before");
eq((await w.fetch(new Request("https://r.dev/hit", { method: "OPTIONS" }), cenv)).headers.get("Access-Control-Allow-Origin"), SITE, "OPTIONS /hit answers as before");

// GET /stats: only with the owner's key; totals by day, page, source, referrer and country
db = cenv.STATS_DB = d1();
await hit({ p: "/sift/radar.html", s: "x", r: "t.co" }, { e: cenv });
await hit({ p: "/sift/radar.html", s: "x", r: "t.co" }, { e: cenv, cf: { country: "US" } });
await hit({ p: TOKEN_PAGE, s: "", r: "" }, { e: cenv });
db.rows.set(`${daysAgo(20)}|index|hn|news.ycombinator.com|FR`, 5);  // older than the default window
const before = db.seen.length;
const keyless = { ...cenv };
delete keyless.STATS_KEY;
for (const [auth, e, label] of [[undefined, cenv, "no Authorization header"], ["Bearer wrong-key-of-a-good-length-0000000000", cenv, "a wrong key"],
  [KEY, cenv, "the key without Bearer"], [`Bearer ${KEY}x`, cenv, "a longer key"], [`Bearer ${KEY.slice(0, -1)}`, cenv, "a shorter key"],
  [`Bearer ${KEY.slice(0, -1)}4`, cenv, "a wrong key of the same length"],
  [`Basic ${KEY}`, cenv, "another scheme"], ["Bearer undefined", keyless, "no STATS_KEY set"], ["Bearer ", { ...cenv, STATS_KEY: "" }, "an empty STATS_KEY"],
  ["Bearer short", { ...cenv, STATS_KEY: "short" }, "a STATS_KEY under 16 characters"]]) {
  const refused = await statsOf("", auth, e);
  eq([refused.status, (await refused.json()).error], [401, "unauthorized"], `stats: ${label} gives 401`);
}
eq(db.seen.length, before, "stats: a refused request reads nothing");
res = await statsOf("", `Bearer ${KEY}`, cenv);
const st = await res.json();
eq([res.status, st.days, st.from, st.to, st.total], [200, 14, daysAgo(13), today(), 3], "stats: the right key reads the last 14 days");
eq([st.by_day[today()], Object.keys(st.by_day).length, st.by_page, st.by_src, st.by_ref, st.by_country],
  [3, 14, { radar: 2, t: 1 }, { x: 2, "": 1 }, { "t.co": 2, "": 1 }, { DE: 2, US: 1 }], "stats: totals by day, page, source, referrer and country");
eq([st.by_day_page[today()], st.by_day_src[today()], st.by_day_ref[today()], st.by_day_page[daysAgo(13)], Object.keys(st.by_day_src).length],
  [{ radar: 2, t: 1 }, { x: 2, "": 1 }, { "t.co": 2, "": 1 }, {}, 14], "stats: and per day by page, source and referrer");
eq("rows" in st, false, "stats: the totals are summed in D1, so the answer holds no row list");
eq([res.headers.get("Access-Control-Allow-Origin"), res.headers.get("Cache-Control")], [null, "no-store"], "stats: not readable by a web page, never cached");
eq((await statsOf("?days=1", `bearer ${KEY}`, cenv)).status, 200, "stats: the scheme name in any case");
for (const [q, n] of [["?days=1", 1], ["?days=30", 30], ["?days=90", 90], ["?days=500", 90], ["?days=abc", 14], ["?days=0", 14], ["?days=-3", 14], ["?days=2.5", 14]]) {
  const x = await (await statsOf(q, `Bearer ${KEY}`, cenv)).json();
  eq([x.days, x.from, Object.keys(x.by_day).length], [n, daysAgo(n - 1), n], `stats: ${q} reads ${n} day(s)`);
}
const month = await (await statsOf("?days=30", `Bearer ${KEY}`, cenv)).json();
eq([month.total, month.by_src.hn], [8, 5], "stats: a longer window includes older days");
eq([month.by_day_src[daysAgo(20)], month.by_day_page[daysAgo(20)]], [{ hn: 5 }, { index: 5 }], "stats: an older day keeps its own breakdown");
// a busy quarter (or forged beacons from many countries) is summed in D1: the answer stays small and its
// work does not grow with the number of rows (the free plan allows 10 ms of CPU a request)
const busy = cenv.STATS_DB = d1();
const codes = [...letters.toUpperCase()].flatMap((a) => [...letters.toUpperCase()].slice(0, 10).map((b) => a + b));
for (let d = 0; d < 90; d++) for (const c of codes) for (const r of ["t.co", "t.me", ""]) busy.rows.set(`${daysAgo(d)}|radar|x|${r}|${c}`, 1);
const bigText = await (await statsOf("?days=90", `Bearer ${KEY}`, cenv)).text(), big = JSON.parse(bigText);
eq([busy.rows.size, big.total, big.by_day[today()], Object.keys(big.by_country).length, big.by_day_ref[today()]],
  [90 * codes.length * 3, 90 * codes.length * 3, codes.length * 3, codes.length, { "": codes.length, "t.co": codes.length, "t.me": codes.length }],
  `stats: ${busy.rows.size} rows read as sums`);
eq([bigText.length < 40000, busy.selects], [true, 4], "stats: four sums, and an answer under 40 KB");
cenv.STATS_DB = db;
db.fail = true;
res = await statsOf("", `Bearer ${KEY}`, cenv);
eq([res.status, /D1_ERROR|10\.0\.0\.7|no such table/.test(await res.text())], [502, false], "stats: a D1 error says nothing more");
db.fail = false;
eq((await statsOf("", `Bearer ${KEY}`, unbound)).status, 503, "stats: no STATS_DB bound gives 503");
eq(state.urls.length, 0, "the counter calls no outside service");

// the Pages project serves the relay through functions/_middleware.js, which hands it waitUntil
const { onRequest } = await import("../functions/_middleware.js");
db = d1();
const held = [];
res = await onRequest({ request: new Request("https://r.dev/hit", { method: "POST", body: JSON.stringify(ok), headers: { Origin: SITE } }),
  env: { ...cenv, STATS_DB: db }, waitUntil: (p) => held.push(p) });
eq([res.status, held.length], [204, 1], "middleware: the visit is written after the answer, through the Pages context");
await Promise.all(held);
eq(total(db), 1, "middleware: and counted");
eq((await (await onRequest({ request: new Request("https://r.dev/trace?mint=bad"), env, waitUntil: () => {} })).json()).status, "invalid",
  "middleware: trace requests answer as before");

// the same statements on a real SQLite database (built into Node 22.5 and later; skipped elsewhere)
const warn = process.emitWarning;
process.emitWarning = (m, ...a) => (String(m).includes("SQLite") ? undefined : warn.call(process, m, ...a));
const sqlite = await import("node:sqlite").catch(() => null);
process.emitWarning = warn;
if (!sqlite) console.log("skip SQLite: node:sqlite is not available");
else {
  const mem = new sqlite.DatabaseSync(":memory:");
  const exec = (s) => ({ success: true, results: s.sql.startsWith("SELECT ") ? mem.prepare(s.sql).all(...s.args) : (mem.prepare(s.sql).run(...s.args), []) });
  const stmt = (sql, args = []) => ({ sql, args, bind: (...a) => stmt(sql, a), run: async () => exec({ sql, args }), all: async () => exec({ sql, args }) });
  const real = { prepare: (sql) => stmt(sql), batch: async (list) => {
    mem.exec("BEGIN");
    try { const out = list.map(exec); mem.exec("COMMIT"); return out; } catch (err) { mem.exec("ROLLBACK"); throw err; }
  } };
  const onSqlite = { ...cenv, STATS_DB: real };
  for (const b of [{ p: "/sift/radar.html", s: "x", r: "t.co" }, { p: "/sift/radar.html", s: "x", r: "t.co" }, { p: TOKEN_PAGE, s: "", r: "" }]) await hit(b, { e: onSqlite });
  const x = await (await statsOf("", `Bearer ${KEY}`, onSqlite)).json();
  eq([x.total, x.by_page, x.by_day_src[today()], x.by_country, mem.prepare("SELECT n FROM hits ORDER BY n DESC").all().map((row) => row.n)],
    [3, { radar: 2, t: 1 }, { x: 2, "": 1 }, { DE: 3 }, [2, 1]], "SQLite: a repeat visit adds one to its row, /stats reads the sums");
  eq(Object.keys(mem.prepare("SELECT * FROM hits").get()), ["day", "page", "src", "ref", "country", "n"], "SQLite: the table holds the five fields and the count, nothing else");
}
