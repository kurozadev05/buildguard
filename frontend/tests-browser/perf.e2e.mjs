import os from "node:os"; const OUT = process.env.OUT_DIR ?? os.tmpdir();
import { BASE, launch, newPage, login, sleep } from "./lib.mjs";
const b = await launch(); const p = await newPage(b, 390, 844, true); await login(p);
const cdp = await p.createCDPSession();
// --- PWA installability as Chrome reports it
await p.goto(BASE + "/", { waitUntil: "networkidle0" });
const inst = await cdp.send("Page.getInstallabilityErrors").catch((e) => ({ err: e.message })); console.log("installability errors:", JSON.stringify(inst.installabilityErrors ?? inst));
const man = await cdp.send("Page.getAppManifest").catch((e) => ({ err: e.message })); console.log("manifest url:", man.url, "| parse errors:", JSON.stringify(man.errors ?? []), "| icons:", (JSON.parse(man.data || "{}").icons || []).length);
// --- debounce: type 8 chars with 70 ms gaps, count list requests
await p.goto(BASE + "/batches", { waitUntil: "networkidle0" }); let reqs = []; p.on("request", (r) => { if (/\/bff\/batches\?/.test(r.url())) reqs.push(r.url().replace(BASE, "")); });
await p.type("input[type=search]", "UltraMix", { delay: 70 }); await sleep(1500); console.log(`debounce: typed 8 characters -> ${reqs.length} list request(s): ${reqs.join(" , ").slice(0, 200)}`);
// --- perf on throttled mobile (Slow 4G + 4x CPU)
await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 150, downloadThroughput: 1.6 * 1024 * 1024 / 8, uploadThroughput: 750 * 1024 / 8 }); await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
for (const path of ["/", "/batches", "/assistant"]) {
  const q = await b.newPage(); await q.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 }); await q.setCookie(...(await p.cookies()));
  const c2 = await q.createCDPSession(); await c2.send("Network.emulateNetworkConditions", { offline: false, latency: 150, downloadThroughput: 1.6 * 1024 * 1024 / 8, uploadThroughput: 750 * 1024 / 8 }); await c2.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  let n = 0, bytes = 0, api = 0; q.on("response", async (r) => { n++; const l = Number(r.headers()["content-length"] || 0); bytes += l; if (r.url().includes("/bff/")) api++; });
  await q.evaluateOnNewDocument(() => { window.__lcp = 0; new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__lcp = e.startTime; }).observe({ type: "largest-contentful-paint", buffered: true }); window.__cls = 0; new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; }).observe({ type: "layout-shift", buffered: true }); });
  const t0 = Date.now(); await q.goto(BASE + path, { waitUntil: "networkidle0" }); const dt = Date.now() - t0;
  const m = await q.evaluate(() => { const nav = performance.getEntriesByType("navigation")[0]; const fcp = performance.getEntriesByName("first-contentful-paint")[0]?.startTime; const res = performance.getEntriesByType("resource"); return { fcp: Math.round(fcp), lcp: Math.round(window.__lcp), cls: +window.__cls.toFixed(3), dcl: Math.round(nav.domContentLoadedEventEnd), transfer: res.reduce((a, r) => a + (r.transferSize || 0), 0) + nav.transferSize, js: res.filter((r) => r.name.endsWith(".js")).length }; });
  console.log(`slow-4G/4xCPU ${path.padEnd(11)} FCP ${m.fcp} ms  LCP ${m.lcp} ms  CLS ${m.cls}  DCL ${m.dcl} ms  idle ${dt} ms  transferred ${(m.transfer / 1024).toFixed(0)} KB  requests ${n} (api ${api})`);
  await q.close();
}
// --- warm dashboard: request count / dedupe
await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 }); await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 });
const seen = {}; p.on("request", (r) => { if (r.url().includes("/bff/")) { const k = r.url().replace(BASE, "").split("?")[0]; seen[k] = (seen[k] || 0) + 1; } });
await p.goto(BASE + "/", { waitUntil: "networkidle0" }); await sleep(500); console.log("dashboard API calls (dedupe check):", JSON.stringify(seen));
// --- screenshots of denser screens
const api = (path) => p.evaluate(async (path) => (await (await fetch("/bff/" + path)).json()).data, path);
const tests = await api("tests?limit=5"); const inv = await api("investigations?limit=1"); const bat = await api("batches?limit=2");
await p.setViewport({ width: 1280, height: 900 });
for (const [n, u] of [["test", "/tests/" + tests[0].id], ["invest", "/investigations/" + inv[0].id], ["batchd", "/batches/" + bat[0].id], ["risk", "/risk"], ["insights", "/insights"]]) { await p.goto(BASE + u, { waitUntil: "networkidle0" }); await sleep(400); await p.screenshot({ path: `${OUT}/desk-${n}.png` }); }
await p.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 }); await p.goto(BASE + "/tests/" + tests[0].id, { waitUntil: "networkidle0" }); await sleep(400); await p.screenshot({ path: `${OUT}/phone-test.png` });
await b.close();
