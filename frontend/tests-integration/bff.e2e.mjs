// Integration test of the BFF against a REAL running backend + Next server (scripts/dev_stack.sh start).  node tests-integration/bff.e2e.mjs
const B = process.env.BFF ?? "http://localhost:3100";
let pass = 0, fail = 0;
const ok = (c, m, extra = "") => { (c ? pass++ : fail++); console.log(`${c ? "PASS" : "FAIL"}  ${m}${c ? "" : "  <<< " + extra}`); };
const jar = new Map();
const store = (res) => { for (const c of res.headers.getSetCookie()) { const [kv] = c.split(";"); const i = kv.indexOf("="); const k = kv.slice(0, i), v = kv.slice(i + 1); v ? jar.set(k, v) : jar.delete(k); } };
const cookie = () => [...jar].map(([k, v]) => `${k}=${v}`).join("; ");
const call = async (path, o = {}) => { const res = await fetch(B + path, { redirect: "manual", ...o, headers: { cookie: cookie(), "x-bg-csrf": "1", ...(o.body && !(o.body instanceof FormData) ? { "content-type": "application/json" } : {}), ...o.headers } }); store(res); return res; };
const json = async (r) => { try { return await r.json(); } catch { return null; } };
const login = (pw) => call("/auth/login", { method: "POST", body: JSON.stringify({ email: "admin@qa.test", password: pw }) });

let r = await fetch(B + "/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
ok(r.status === 403, "login without CSRF header is refused", r.status);
r = await fetch(B + "/auth/login", { method: "POST", headers: { "content-type": "application/json", "x-bg-csrf": "1", origin: "https://evil.example" }, body: "{}" });
ok(r.status === 403, "login from a foreign Origin is refused", r.status);
r = await fetch(B + "/auth/login", { method: "POST", headers: { "content-type": "application/json", "x-bg-csrf": "1" }, body: JSON.stringify({ email: "admin@qa.test", password: "wrong-password" }) });
ok(r.status === 401, "wrong password -> 401", r.status);
r = await login("Passw0rd!Passw0rd"); const lb = await json(r); const sc = r.headers.getSetCookie().join("\n");
ok(r.status === 200 && lb.data.user.email === "admin@qa.test", "login ok, user returned");
ok(!/access_token|refresh_token|eyJ/.test(JSON.stringify(lb)), "no token appears in the login response body");
ok(/bg_at=[^;]+;.*HttpOnly/i.test(sc) && /SameSite=Strict/i.test(sc) && /bg_rt=[^;]+;.*HttpOnly/i.test(sc), "both cookies are HttpOnly + SameSite=Strict", sc);

r = await call("/bff/auth/me"); ok(r.status === 200 && (await json(r)).data.role === "admin", "authenticated call through the proxy");
for (const p of ["login", "register", "refresh", "token", "logout"]) { r = await call(`/bff/auth/${p}`, { method: "POST", body: "{}" }); ok(r.status === 404, `token-issuing endpoint auth/${p} is not reachable via proxy`, r.status); }
r = await call("/bff/%2e%2e/health"); ok(r.status === 404, "path traversal refused", r.status);
r = await call("/bff/dashboard/summary", { method: "POST", body: "{}", headers: { "x-bg-csrf": "" } }); ok(r.status === 403, "mutating proxy call without CSRF header refused", r.status);
r = await call("/bff/nope"); const nb = await json(r); ok(r.status === 404 && nb?.error?.request_id, "backend error envelope + request id pass through");
ok(r.headers.get("cache-control") === "no-store", "API responses are no-store");

const saved = new Map(jar); jar.delete("bg_at");
const many = await Promise.all(Array.from({ length: 8 }, () => call("/bff/projects")));
ok(many.every((x) => x.status === 200), "8 concurrent requests with only a refresh cookie all succeed", many.map((x) => x.status).join(","));
ok(jar.get("bg_at") && jar.get("bg_rt") !== saved.get("bg_rt"), "cookies were rotated");
const rotated = new Map(jar); jar.clear(); jar.set("bg_rt", saved.get("bg_rt"));
r = await call("/bff/projects"); ok(r.status === 200, "a request still carrying the previous refresh cookie is served (no false token-reuse revocation)", r.status);
jar.clear(); rotated.forEach((v, k) => jar.set(k, v));
r = await call("/bff/auth/me"); ok(r.status === 200, "session still valid after concurrent rotation (token family not revoked)", r.status);

const pid = (await json(await call("/bff/projects"))).data[0].id;
const bid = (await json(await call(`/bff/batches?project_id=${pid}&limit=1`))).data[0].id;
r = await call(`/bff/batches/${bid}/qr.png`); const buf = Buffer.from(await r.arrayBuffer());
ok(r.status === 200 && r.headers.get("content-type") === "image/png" && buf.subarray(1, 4).toString() === "PNG", "binary (QR png) passes through unchanged");
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");
const fd = new FormData(); fd.append("file", new Blob([png], { type: "image/png" }), "x.png"); fd.append("project_id", pid); fd.append("kind", "photo"); fd.append("batch_id", bid);
r = await call("/bff/documents", { method: "POST", body: fd }); const ub = await json(r); ok(r.status === 201, "multipart upload streams through the proxy", `${r.status} ${JSON.stringify(ub)?.slice(0, 200)}`);
r = await call(`/bff/documents/${ub?.data?.id}/download`); ok(r.status === 200 && /image\/png/.test(r.headers.get("content-type") ?? ""), "download passes content-type");

const opId = "op-" + Date.now() + Math.random().toString(36).slice(2, 8);
r = await call("/bff/sync/push", { method: "POST", body: JSON.stringify({ device_id: "e2e", ops: [{ op_id: opId, type: "usage.create", data: { batch_id: bid, element_id: "00000000-0000-0000-0000-000000000000" } }] }) });
console.log("INFO  sync push result:", JSON.stringify((await json(r))?.data).slice(0, 500));
r = await call("/bff/ocr/drafts"); console.log("INFO  ocr drafts shape:", JSON.stringify((await json(r))?.data).slice(0, 200));

async function sse(message, abortAfter) {
  const t0 = performance.now(), times = [], events = []; let text = "";
  const ac = new AbortController();
  const res = await call("/bff/ai/chat/stream", { method: "POST", body: JSON.stringify({ message }), signal: ac.signal });
  const reader = res.body.getReader(), dec = new TextDecoder(); let acc = "";
  try { for (;;) { const { value, done } = await reader.read(); if (done) break; acc += dec.decode(value, { stream: true }); let i;
    while ((i = acc.indexOf("\n\n")) >= 0) { const blk = acc.slice(0, i); acc = acc.slice(i + 2); const ev = /event: (\w+)/.exec(blk)?.[1]; const d = JSON.parse(/data: (.*)/.exec(blk)?.[1] ?? "{}"); events.push(ev);
      if (ev === "delta") { times.push(performance.now() - t0); text += d.text; if (abortAfter && times.length >= abortAfter) { ac.abort(); return { res, times, events, text, aborted: true }; } } } } } catch (e) { if (!ac.signal.aborted) throw e; }
  return { res, times, events, text };
}
let s = await sse("How many cube samples do I need per cubic metre?");
ok(s.res.status === 200 && s.res.headers.get("content-type").startsWith("text/event-stream"), "SSE content-type preserved");
ok(s.events[0] === "meta" && s.events.at(-1) === "done" && s.times.length > 6, `stream events arrive (${s.events.length} events)`, s.events.join());
ok(s.times.at(-1) - s.times[0] > 600 && s.times[0] < 1200, `deltas are relayed incrementally (first ${s.times[0]?.toFixed(0)} ms, last ${s.times.at(-1)?.toFixed(0)} ms)`);
s = await sse("cancel me while streaming about sampling frequency", 3); await new Promise((x) => setTimeout(x, 2500));
ok(s.aborted, "client aborted mid-stream (backend accounting checked separately)");

r = await call("/bff/auth/change-password", { method: "POST", body: JSON.stringify({ current_password: "Passw0rd!Passw0rd", new_password: "Passw0rd!Passw0rd-changed" }) });
ok(r.status === 200 && !jar.has("bg_at") && !jar.has("bg_rt"), "password change clears the browser session", `${r.status} ${[...jar.keys()]}`);
r = await call("/bff/auth/me"); ok(r.status === 401, "after that, protected calls are 401", r.status);
r = await login("Passw0rd!Passw0rd-changed"); ok(r.status === 200, "login with the new password", r.status);
r = await call("/auth/logout", { method: "POST", body: "{}" }); ok(r.status === 200 && !jar.has("bg_at") && !jar.has("bg_rt"), "logout clears cookies");

r = await fetch(B + "/batches", { redirect: "manual" }); ok(r.status === 307 && (r.headers.get("location") ?? "").includes("/login?next=%2Fbatches"), "protected page redirects to login with next=", `${r.status} ${r.headers.get("location")}`);
r = await fetch(B + "/login"); const csp = r.headers.get("content-security-policy") ?? "";
ok(/script-src 'self' 'nonce-[^']+' 'strict-dynamic'/.test(csp) && csp.includes("connect-src 'self'") && csp.includes("frame-ancestors 'none'"), "CSP: nonce scripts, same-origin connect only, no framing", csp);
ok(r.headers.get("x-content-type-options") === "nosniff" && r.headers.get("x-frame-options") === "DENY", "security headers present");
console.log(`\n${pass} passed, ${fail} failed`); process.exit(fail ? 1 : 0);
