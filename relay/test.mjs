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
