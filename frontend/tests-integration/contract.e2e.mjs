// Sends the exact request bodies the UI builds, through the BFF, to the real backend. node tests-integration/contract.e2e.mjs
import { randomUUID as uuid } from "node:crypto";
const B = process.env.BFF ?? "http://localhost:3100"; let pass = 0, fail = 0; const jar = new Map();
const ok = (c, m, x = "") => { c ? pass++ : fail++; console.log(`${c ? "PASS" : "FAIL"}  ${m}${c ? "" : "  <<< " + x}`); };
const call = async (path, o = {}) => { const r = await fetch(B + path, { redirect: "manual", ...o, headers: { cookie: [...jar].map(([k, v]) => `${k}=${v}`).join("; "), "x-bg-csrf": "1", ...(o.body && !(o.body instanceof FormData) ? { "content-type": "application/json" } : {}) } });
  for (const c of r.headers.getSetCookie()) { const [kv] = c.split(";"); const i = kv.indexOf("="); const v = kv.slice(i + 1); v ? jar.set(kv.slice(0, i), v) : jar.delete(kv.slice(0, i)); } return r; };
const j = (path, method = "GET", body) => call(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
async function step(name, p, want = [200, 201]) { const r = await p; const b = await r.json().catch(() => null); const good = want.includes(r.status); ok(good, name, `${r.status} ${JSON.stringify(b)?.slice(0, 260)}`); return b?.data; }

const cred = { email: "admin@qa.test", password: "Passw0rd!Passw0rd" };
if ((await call("/auth/login", { method: "POST", body: JSON.stringify(cred) })).status !== 200) await call("/auth/register", { method: "POST", body: JSON.stringify({ ...cred, full_name: "QA Admin", language: "en" }) });
const proj = await step("create project", j("/bff/projects", "POST", { name: "Contract Test Site", location: "Jaipur", client_name: "ACME" }));
const bld = await step("add building", j(`/bff/projects/${proj.id}/buildings`, "POST", { name: "Block A" }));
const flr = await step("add floor (level int)", j(`/bff/buildings/${bld.id}/floors`, "POST", { name: "Ground", level: 0 }));
const el = await step("add element", j(`/bff/floors/${flr.id}/elements`, "POST", { name: "Slab S1", element_type: "slab" }));
const tree = await step("project tree", j(`/bff/projects/${proj.id}/tree`)); ok(tree.buildings[0].floors[0].elements[0].id === el.id, "tree contains the new element");
await step("members list", j(`/bff/projects/${proj.id}/members`));

// what the New-batch form sends (client id + only filled fields)
const bid = uuid(); const batch = await step("register batch with client-generated id", j("/bff/batches", "POST", { id: bid, project_id: proj.id, grade: "M25", construction_type: "rcc", exposure: "moderate", quantity_m3: 12, cement_type: "opc", supplier: "UltraMix", cement_content_kg_m3: 320, wc_ratio: 0.5, delivery_date: "2026-09-28" }));
ok(batch.id === bid && batch.batch_code && batch.registration_check.outcomes.length > 0, "server kept the client id; registration_check present");
await step("get by code", j(`/bff/batches/by-code/${batch.batch_code}`));
const page1 = await step("batches page 1", j(`/bff/batches?project_id=${proj.id}&limit=1&offset=0`)); ok(page1.length === 1, "limit honoured");
await step("batches filter by supplier+status", j(`/bff/batches?project_id=${proj.id}&supplier=Ultra&status=PENDING`));
const sid = uuid(); const smp = await step("add sample (client id)", j("/bff/samples", "POST", { id: sid, batch_id: bid, cast_date: "2026-09-28", cubes_cast: 6, curing_method: "water" })); ok(smp.id === sid, "sample kept client id");
const qr = await call(`/bff/samples/${sid}/qr.png`); ok(qr.status === 200 && qr.headers.get("content-type") === "image/png", "sample QR label");

// tests exactly as buildValues() makes them
const t1 = await step("record cube test (7d)", j("/bff/tests", "POST", { id: uuid(), batch_id: bid, sample_id: sid, test_type: "cube_compressive_strength", age_days: 7, values: { specimens_mpa: [12, 12.5, 12.2] }, lab_name: "City Lab" }));
await step("record slump", j("/bff/tests", "POST", { id: uuid(), batch_id: bid, test_type: "slump", values: { slump_mm: 100, placing_condition: "lightly_reinforced" } }));
await step("record temperature", j("/bff/tests", "POST", { id: uuid(), batch_id: bid, test_type: "fresh_temperature", values: { temperature_c: 30 } }));
await step("record core", j("/bff/tests", "POST", { id: uuid(), batch_id: bid, test_type: "core_strength", values: { cores_equiv_cube_mpa: [24, 25, 26] } }));
await step("record upv", j("/bff/tests", "POST", { id: uuid(), batch_id: bid, test_type: "upv", values: { velocity_km_s: 4.2 } }));
await step("record rebound", j("/bff/tests", "POST", { id: uuid(), batch_id: bid, test_type: "rebound_hammer", values: { rebound_number_avg: 35, estimated_strength_mpa: 30 } }));
const t28 = await step("record failing 28d cube", j("/bff/tests", "POST", { id: uuid(), batch_id: bid, sample_id: sid, test_type: "cube_compressive_strength", age_days: 28, values: { specimens_mpa: [15, 16, 15.5] } }));
ok(["FLAGGED", "REVIEW_REQUIRED"].includes(t28.status), `28d low cubes are not Verified (${t28.status})`, t28.status);
const list = await step("tests list (filters)", j(`/bff/tests?batch_id=${bid}&current_only=true&limit=20&lang=hi`)); ok(list.length >= 6, "tests list");
await step("test detail", j(`/bff/tests/${t28.id}?lang=hi`));
const v = await step("verify seal", j(`/bff/tests/${t28.id}/verify`)); ok(v.seal_valid === true, "seal valid");
const ex = await step("AI explain (template or LLM)", j(`/bff/ai/tests/${t28.id}/explain?lang=en`)); ok(typeof ex.explanation === "string" && ex.generated_by, "explain shape");
await step("review (accepted)", j(`/bff/tests/${t28.id}/review`, "POST", { decision: "accepted", comment: "Engineer accepted with cores" }), [200, 201, 400, 409, 422]);
const am = await step("amend", j(`/bff/tests/${t1.id}/amend`, "POST", { values: { specimens_mpa: [12.1, 12.5, 12.2] }, age_days: 7, reason: "Transcription error" })); ok(am.version === 2, "amend creates v2");
// evidence
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");
const fd = new FormData(); fd.append("file", new Blob([png], { type: "image/png" }), "cube.png"); fd.append("kind", "photo"); fd.append("project_id", proj.id); fd.append("batch_id", bid);
const doc = await step("upload document (UploadBox fields)", call("/bff/documents", { method: "POST", body: fd }));
await step("attach document to test (test page / display flow)", j(`/bff/documents/${doc.id}/attach`, "POST", { test_id: t28.id }));
const dl = await step("documents by test_id", j(`/bff/documents?test_id=${t28.id}&limit=50`)); ok(dl.length === 1, "attached doc listed");
const dv = await step("document verify", j(`/bff/documents/${doc.id}/verify`)); console.log("INFO  document verify shape:", JSON.stringify(dv));
// traceability + risk
await step("record usage (client id)", j("/bff/usage", "POST", { id: uuid(), batch_id: bid, element_id: el.id, volume_m3: 5 }));
const imp = await step("impact", j(`/bff/batches/${bid}/impact`)); console.log("INFO  impact location:", JSON.stringify(imp.affected_locations[0]));
for (const [kind, value] of [["crack_width_mm", 0.3], ["moisture_pct", 6], ["upv_km_s", 3.4]]) await step(`observation ${kind}`, j("/bff/observations", "POST", { id: uuid(), element_id: el.id, kind, value }));
const as = await step("assess (persist)", j(`/bff/durability/elements/${el.id}/assess?persist=true&lang=en`, "POST")); ok(typeof as.score === "number", "assessment score");
await step("risk map", j(`/bff/durability/projects/${proj.id}/risk-map`)); await step("risk history", j(`/bff/durability/elements/${el.id}/history`)); await step("element observations", j(`/bff/elements/${el.id}/observations?limit=30`)); await step("element batches", j(`/bff/elements/${el.id}/batches`));
// investigation
const inv = await step("open investigation", j("/bff/investigations", "POST", { batch_id: bid, reason: "Low 28-day strength" }), [200, 201, 409]);
const invs = await step("list investigations", j("/bff/investigations?status=open&limit=20")); const mine = invs.find((x) => x.batch_id === bid) ?? inv;
if (mine?.id) { const d = await step("investigation detail", j(`/bff/investigations/${mine.id}`)); const a = d.actions?.[0];
  if (a) await step("patch action -> done", j(`/bff/investigations/actions/${a.id}`, "PATCH", { status: "done", note: "Done on site" }));
  await step("close investigation", j(`/bff/investigations/${mine.id}/close`, "POST", { closure_note: "Accepted after coring" }), [200, 201, 400, 409, 422]); }
// alerts/dashboard/insights/audit
const al = await step("alerts unacknowledged", j(`/bff/alerts?project_id=${proj.id}&unacknowledged=true&limit=20`)); if (al[0]) await step("ack alert", j(`/bff/alerts/${al[0].id}/ack`, "POST"));
await step("dashboard", j(`/bff/dashboard/summary?project_id=${proj.id}`)); await step("suppliers", j(`/bff/dashboard/suppliers?project_id=${proj.id}`));
await step("predict project", j(`/bff/predict/projects/${proj.id}`)); await step("predict batch", j(`/bff/predict/batches/${bid}`)); await step("integrity project", j(`/bff/integrity/projects/${proj.id}`)); await step("integrity batch", j(`/bff/integrity/batches/${bid}`));
await step("handover passport", j(`/bff/projects/${proj.id}/handover-passport`)); await step("audit filtered", j(`/bff/audit?project_id=${proj.id}&action=test.create&limit=30`)); const av = await step("audit verify", j("/bff/audit/verify")); ok(av.ok === true, "chain ok");
await step("rules", j("/bff/rules"));
// offline queue payloads via sync/push (one op per queue type, client ids, cross-referencing offline-created ids)
const nb = uuid(), ns = uuid(), nt = uuid(), ops = [
  { type: "batch.create", data: { id: nb, project_id: proj.id, grade: "M30", construction_type: "rcc", exposure: "moderate", quantity_m3: 8, cement_type: "opc" } },
  { type: "sample.create", data: { id: ns, batch_id: nb, cast_date: "2026-09-29", cubes_cast: 6 } },
  { type: "test.create", data: { id: nt, batch_id: nb, sample_id: ns, test_type: "slump", values: { slump_mm: 90, placing_condition: "lightly_reinforced" } } },
  { type: "usage.create", data: { id: uuid(), batch_id: nb, element_id: el.id, volume_m3: 2 } },
  { type: "observation.create", data: { id: uuid(), element_id: el.id, kind: "crack_width_mm", value: 0.1 } }].map((o) => ({ op_id: "op-" + uuid(), client_ts: new Date().toISOString(), ...o }));
const push = await step("sync push (5 queue types, dependent ids)", j("/bff/sync/push", "POST", { device_id: "web", ops })); ok(push.applied === 5, `all 5 queued operation types applied by the server`, JSON.stringify(push.results));
const again = await step("replay same op_ids (idempotency)", j("/bff/sync/push", "POST", { device_id: "web", ops })); ok(again.duplicates === 5 && again.applied === 0, "replay is de-duplicated (no double records)", JSON.stringify(again));
await step("offline-created batch exists", j(`/bff/batches/${nb}`)); await step("sync bootstrap", j(`/bff/sync/bootstrap?project_id=${proj.id}`));
// AI + advisor
const st = await step("ai status", j("/bff/ai/status")); const chat = await step("ai chat", j("/bff/ai/chat", "POST", { message: "How many cube samples do I need?", project_id: proj.id })); console.log("INFO  chat keys:", Object.keys(chat ?? {}).join(","));
const cid = chat.conversation_id ?? chat.conversation?.id; const cl = await step("conversations list", j("/bff/ai/conversations?limit=30")); ok(cl.length >= 1, "conversation stored");
const cv = await step("conversation detail", j(`/bff/ai/conversations/${cid}`)); ok(Array.isArray(cv.messages) && cv.messages.length >= 2, "messages restored", JSON.stringify(cv).slice(0, 200));
const ask = await step("ai ask", j("/bff/ai/ask", "POST", { question: "What is the slump range for pumped concrete?", project_id: proj.id })); console.log("INFO  ask keys:", Object.keys(ask ?? {}).join(","));
const se = await step("ai search", j("/bff/ai/search", "POST", { query: "curing period", project_id: proj.id, top_k: 5 })); ok(Array.isArray(se.results), "search results array");
const ad = await step("ai add project doc", j("/bff/ai/documents", "POST", { project_id: proj.id, title: "Method statement", text: "Pumped concrete shall be placed within 30 minutes of arrival on site in hot weather." })); const dls = await step("ai docs list", j(`/bff/ai/documents?project_id=${proj.id}`)); console.log("INFO  ai doc keys:", Object.keys(dls[0] ?? {}).join(","));
await step("ai doc delete", j(`/bff/ai/documents/${ad.id ?? dls[0].id}`, "DELETE"), [200, 204]); await step("conversation delete", j(`/bff/ai/conversations/${cid}`, "DELETE"), [200, 204]);
await step("ai usage me", j("/bff/ai/usage/me")); await step("ai usage summary", j("/bff/ai/usage/summary?days=7")); await step("ai metrics", j("/bff/ai/metrics"));
const cl2 = await step("advisor checklist (+explain)", j("/bff/advisor/checklist?lang=en", "POST", { construction_type: "rcc", element_type: "slab", grade: "M25", exposure: "moderate", quantity_m3: 10, reinforcement: "light", placing_method: "normal", weather: "normal", cement_type: "opc", explain: true })); console.log("INFO  checklist keys:", Object.keys(cl2).join(","));
await step("advisor knowledge", j("/bff/advisor/knowledge?q=curing&limit=5"));
const ocr = await step("ocr parse-text", j("/bff/ocr/parse-text", "POST", { text: "Cube 28 day 31.5 32.0 30.8 N/mm2" })); console.log("INFO  parse-text keys:", Object.keys(ocr ?? {}).join(","));
const fd2 = new FormData(); fd2.append("file", new Blob([png], { type: "image/png" }), "r.png"); fd2.append("project_id", proj.id); fd2.append("batch_id", bid);
const ex2 = await step("ocr extract -> draft", call("/bff/ocr/extract", { method: "POST", body: fd2 })); const dr = await step("ocr drafts list", j("/bff/ocr/drafts?status=pending&limit=20")); console.log("INFO  draft item keys:", Object.keys(dr[0] ?? {}).join(","), "| extract keys:", Object.keys(ex2 ?? {}).join(","));
if (dr[0]) await step("ocr reject draft", j(`/bff/ocr/drafts/${dr[0].id}/reject`, "POST"));
const fd3 = new FormData(); fd3.append("file", new Blob([png], { type: "image/png" }), "d.png"); fd3.append("project_id", proj.id); fd3.append("cube_size_mm", "150");
const rd = await step("ai read-display", call("/bff/ai/read-display", { method: "POST", body: fd3 })); ok("needs_confirmation" in rd, "read-display returns a confirmable draft");
// admin
const us = await step("users list", j("/bff/auth/users?limit=50")); const other = us.find((u) => u.email !== "admin@qa.test");
if (other) { await step("patch user role", j(`/bff/auth/users/${other.id}`, "PATCH", { role: other.role })); const rp = await step("reset password (other user)", j(`/bff/auth/users/${other.id}/reset-password`, "POST")); ok(typeof rp.temporary_password === "string", "temporary password returned"); }
// role enforcement is backend-authoritative
const lab = await fetch(B + "/auth/register", { method: "POST", headers: { "content-type": "application/json", "x-bg-csrf": "1" }, body: JSON.stringify({ email: `c${Date.now()}@t.io`, full_name: "Client Test", password: "Passw0rd!Passw0rd", language: "en" }) });
const cj = new Map(); for (const c of lab.headers.getSetCookie()) { const [kv] = c.split(";"); const i = kv.indexOf("="); cj.set(kv.slice(0, i), kv.slice(i + 1)); }
const asNew = (p, m = "GET", b) => fetch(B + p, { method: m, headers: { cookie: [...cj].map(([k, v]) => `${k}=${v}`).join("; "), "x-bg-csrf": "1", "content-type": "application/json" }, body: b ? JSON.stringify(b) : undefined });
const me2 = await (await asNew("/bff/auth/me")).json(); console.log("INFO  self-registered role:", me2.data?.role);
let r = await asNew("/bff/auth/users"); ok(r.status === 403, "non-admin cannot list users (backend enforces, UI hiding is only cosmetic)", r.status);
r = await asNew(`/bff/projects/${proj.id}/tree`); ok([403, 404].includes(r.status), "non-member cannot read a project", r.status);
console.log(`\n${pass} passed, ${fail} failed`); process.exit(fail ? 1 : 0);
