// API latency under load through the BFF, with a realistic data volume. node tests-integration/perf.mjs
import { randomUUID as uuid } from "node:crypto";
const B = "http://localhost:3100"; const jar = new Map();
const call = async (p, o = {}) => { const r = await fetch(B + p, { redirect: "manual", ...o, headers: { cookie: [...jar].map(([k, v]) => `${k}=${v}`).join("; "), "x-bg-csrf": "1", "content-type": "application/json", ...o.headers } }); for (const c of r.headers.getSetCookie()) { const [kv] = c.split(";"); const i = kv.indexOf("="); jar.set(kv.slice(0, i), kv.slice(i + 1)); } return r; };
const cred = { email: "admin@qa.test", password: "Passw0rd!Passw0rd" };
let lr = await call("/auth/login", { method: "POST", body: JSON.stringify(cred) });
if (lr.status !== 200) { await call("/auth/register", { method: "POST", body: JSON.stringify({ ...cred, full_name: "Perf Admin", language: "en" }) }); await call("/bff/projects", { method: "POST", body: JSON.stringify({ name: "Perf Site" }) }); }
const pid = (await (await call("/bff/projects")).json()).data[0].id;
// seed: 400 batches, 3 tests each (via the offline-sync endpoint, 100 ops per request)
const have = (await (await call(`/bff/batches?project_id=${pid}&limit=100&offset=300`)).json()).data.length; let created = 0;
if (have === 0) for (let chunk = 0; chunk < 4; chunk++) { const ops = []; for (let i = 0; i < 25; i++) { const b = uuid(); ops.push({ op_id: "op-" + uuid(), type: "batch.create", data: { id: b, project_id: pid, grade: "M25", construction_type: "rcc", exposure: "moderate", quantity_m3: 10 + i, cement_type: "opc", supplier: "Supplier" + (i % 7) } }); for (const [t, v] of [["slump", { slump_mm: 100, placing_condition: "lightly_reinforced" }], ["fresh_temperature", { temperature_c: 30 }], ["cube_compressive_strength", { specimens_mpa: [31, 32, 33] }]]) ops.push({ op_id: "op-" + uuid(), type: "test.create", data: { id: uuid(), batch_id: b, test_type: t, ...(t.startsWith("cube") ? { age_days: 28 } : {}), values: v } }); } const r = await (await call("/bff/sync/push", { method: "POST", body: JSON.stringify({ device_id: "perf", ops }) })).json(); created += r.data.applied; }
console.log(`seeded ${created} new records (batches+tests)`);
const stat = (a) => { a.sort((x, y) => x - y); const p = (q) => a[Math.min(a.length - 1, Math.floor(q * a.length))]; return { n: a.length, p50: p(0.5).toFixed(0), p95: p(0.95).toFixed(0), p99: p(0.99).toFixed(0), max: a.at(-1).toFixed(0) }; };
async function bench(name, path, total, conc) { const lat = []; let err = 0, i = 0; const t0 = performance.now(); await Promise.all(Array.from({ length: conc }, async () => { while (i++ < total) { const s = performance.now(); const r = await call(path); await r.arrayBuffer(); lat.push(performance.now() - s); if (!r.ok) err++; } })); const secs = (performance.now() - t0) / 1000; console.log(`${name.padEnd(34)} conc=${String(conc).padStart(2)}  ${JSON.stringify(stat(lat))} ms  ${(total / secs).toFixed(0)} req/s  errors=${err}`); return { err, p95: +stat(lat).p95 }; }
const results = [];
results.push(await bench("GET /batches?limit=20", `/bff/batches?project_id=${pid}&limit=20`, 300, 1));
results.push(await bench("GET /batches?limit=20", `/bff/batches?project_id=${pid}&limit=20`, 600, 20));
results.push(await bench("GET /tests?limit=20", `/bff/tests?limit=20`, 400, 10));
results.push(await bench("GET /dashboard/summary", `/bff/dashboard/summary?project_id=${pid}`, 200, 10));
results.push(await bench("GET /auth/me (auth path only)", `/bff/auth/me`, 600, 20));
const bid = (await (await call(`/bff/batches?project_id=${pid}&limit=1`)).json()).data[0].id; results.push(await bench("GET /batches/{id} (detail)", `/bff/batches/${bid}`, 300, 10));
// direct backend vs via BFF overhead
const tok = (await (await fetch("http://127.0.0.1:8801/api/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email: "admin@qa.test", password: "Passw0rd!Passw0rd" }) })).json()).data.access_token;
const dl = []; for (let i = 0; i < 200; i++) { const s = performance.now(); const r = await fetch(`http://127.0.0.1:8801/api/batches?project_id=${pid}&limit=20`, { headers: { authorization: "Bearer " + tok } }); await r.arrayBuffer(); dl.push(performance.now() - s); } console.log(`direct backend GET /batches?limit=20   ${JSON.stringify(stat(dl))} ms  (BFF overhead = difference vs the conc=1 row)`);
// AI time-to-first-byte: through BFF vs direct (provider is a local simulator with a fixed 400 ms think time)
async function ttfb(url, headers) { const s = performance.now(); const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify({ message: "How many cube samples do I need?" }) }); const rd = r.body.getReader(); const first = await rd.read(); const t1 = performance.now() - s; let firstDelta = null, buf = new TextDecoder().decode(first.value); for (;;) { if (/event: delta/.test(buf)) { firstDelta = performance.now() - s; break; } const { value, done } = await rd.read(); if (done) break; buf += new TextDecoder().decode(value); } await rd.cancel().catch(() => {}); return { firstByte: t1.toFixed(0), firstToken: firstDelta?.toFixed(0) }; }
console.log("AI stream via BFF   :", JSON.stringify(await ttfb(B + "/bff/ai/chat/stream", { "x-bg-csrf": "1", cookie: [...jar].map(([k, v]) => `${k}=${v}`).join("; ") })), "ms");
console.log("AI stream direct API:", JSON.stringify(await ttfb("http://127.0.0.1:8801/api/ai/chat/stream", { authorization: "Bearer " + tok })), "ms  (simulated provider adds ~400 ms think time)");
process.exit(results.some((r) => r.err > 0) ? 1 : 0);
