// Real-browser web-vitals on an emulated mid-range phone (4x CPU slowdown, ~Fast-4G network). node tests-browser/perf.mjs
import { BASE, go, launch, loginUI, reporter } from "./harness.mjs";
const R = reporter("perf"); const browser = await launch();
async function measure(ctx, page, path, label, { throttle = true } = {}) {
  const cdp = await ctx.newCDPSession(page); await cdp.send("Network.enable");
  if (throttle) { await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 }); await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 150, downloadThroughput: (1.6 * 1024 * 1024) / 8, uploadThroughput: (750 * 1024) / 8 }); }
  await page.addInitScript(() => { window.__m = { lcp: 0, cls: 0, longTasks: 0, tbt: 0 }; new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__m.lcp = e.startTime; }).observe({ type: "largest-contentful-paint", buffered: true }); new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__m.cls += e.value; }).observe({ type: "layout-shift", buffered: true }); new PerformanceObserver((l) => { for (const e of l.getEntries()) { window.__m.longTasks++; window.__m.tbt += Math.max(0, e.duration - 50); } }).observe({ type: "longtask", buffered: true }); });
  let reqs = 0, bytes = 0, api = 0; const onR = (r) => { reqs++; if (r.url().includes("/bff/")) api++; }; const onF = async (r) => { try { const b = await r.body(); bytes += b.length; } catch {} }; page.on("request", onR); page.on("response", onF);
  await page.bringToFront(); const t0 = Date.now(); await page.goto(BASE + path); await page.waitForSelector('html[data-hydrated="1"]', { timeout: 30000 }); await page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {}); await page.waitForTimeout(600); const wall = Date.now() - t0;
  const m = await page.evaluate(() => ({ ...window.__m, fcp: (performance.getEntriesByType("paint").find((e) => e.name === "first-contentful-paint")?.startTime) ?? 0, heap: performance.memory ? performance.memory.usedJSHeapSize / 1048576 : 0, js: performance.getEntriesByType("resource").filter((e) => e.name.endsWith(".js") || e.name.includes(".js?")).reduce((a, e) => a + (e.transferSize || e.encodedBodySize || 0), 0) / 1024 }));
  page.off("request", onR); page.off("response", onF); await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 }); await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  R.info(`${label.padEnd(34)} FCP ${m.fcp.toFixed(0)}ms  LCP ${m.lcp.toFixed(0)}ms  CLS ${m.cls.toFixed(3)}  TBT ${m.tbt.toFixed(0)}ms  ready ${wall}ms  requests ${reqs} (api ${api})  JS ${m.js.toFixed(0)}KB  body ${(bytes / 1024).toFixed(0)}KB  heap ${m.heap.toFixed(0)}MB`); return { ...m, wall, reqs, api };
}
const ctx = await browser.newContext({ viewport: { width: 412, height: 915 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, serviceWorkers: "allow" }); const page = await ctx.newPage();
const login = await measure(ctx, page, "/login", "login (cold, no cache)");
R.ok(login.lcp < 4000 && login.cls < 0.1, `login page LCP ${login.lcp.toFixed(0)} ms (<4 s) and CLS ${login.cls.toFixed(3)} (<0.1) on a throttled phone`);
await loginUI(page, "admin@qa.test"); await page.waitForTimeout(1500);
const dash = await measure(ctx, page, "/", "dashboard (after sign-in)");
R.ok(dash.lcp > 0 && dash.lcp < 4000 && dash.cls < 0.1, `dashboard LCP ${dash.lcp.toFixed(0)} ms and CLS ${dash.cls.toFixed(3)}`);
R.ok(dash.api <= 8, `dashboard makes ${dash.api} API request(s) (deduplicated)`);
const bat = await measure(ctx, page, "/batches", "batches list (104+ rows, paged)"); R.ok(bat.lcp > 0 && bat.lcp < 4000, `batch list LCP ${bat.lcp.toFixed(0)} ms`);
const warm = await measure(ctx, page, "/batches", "batches list (2nd visit, SW cache)"); R.ok(warm.lcp > 0 && warm.lcp < 3000 && warm.cls < 0.1, `repeat visit (service-worker cache): LCP ${warm.lcp.toFixed(0)} ms, CLS ${warm.cls.toFixed(3)}`);
const rows = await page.locator("table.data tbody tr").count(); R.ok(rows <= 20, `list renders ${rows} rows at once (paged, not thousands of DOM nodes)`);
const nodes = await page.evaluate(() => document.querySelectorAll("*").length); R.ok(nodes < 1500, `DOM size ${nodes} elements`);
await browser.close(); process.exit(R.done() ? 1 : 0);
