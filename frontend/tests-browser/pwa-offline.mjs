// PWA + offline behaviour in a real browser: installability, service worker, offline shell, queued writes, sync, update, cleanup.
import { execSync } from "node:child_process";
import { BASE, go, launch, loginUI, reload, reporter, shot, until, watch } from "./harness.mjs";
const R = reporter("pwa-offline"); const email = "admin@qa.test"; const RUN = Date.now().toString(36);
const browser = await launch();
process.on("uncaughtException", async (e) => { console.log("FATAL " + String(e.message).slice(0, 300)); try { console.log("URL " + page.url()); console.log("BUSY " + await page.getByRole("button", { name: "Register batch" }).last().getAttribute("aria-busy").catch(() => "n/a") + " | onLine " + await page.evaluate(() => navigator.onLine)); console.log("QUEUE " + await page.evaluate(() => new Promise((res) => { const r = indexedDB.open("bg-offline"); r.onsuccess = () => { const q = r.result.transaction("queue").objectStore("queue").getAll(); q.onsuccess = () => res(JSON.stringify(q.result.map((o) => o.label + ":" + o.status))); }; r.onerror = () => res("idb error"); })).catch((e) => "eval failed " + e.message)); console.log("TRACE " + JSON.stringify(await page.evaluate(() => window.__log.slice(-14)))); console.log("ALERTS " + JSON.stringify(await page.evaluate(() => [...document.querySelectorAll(".err-text,.alert,.toast,[role=alert]")].map((e) => e.innerText.slice(0, 120))))); console.log("VALUES " + JSON.stringify(await page.evaluate(() => [...document.querySelectorAll("input,select")].filter((i) => i.value).map((i) => (i.labels?.[0]?.innerText || i.name || i.type).slice(0, 20) + "=" + i.value.slice(0, 20))))); console.log("CONSOLE " + JSON.stringify(w.errors.slice(-4)).slice(0, 900)); console.log("BODY " + (await page.locator("body").innerText()).replace(/\n+/g, " | ").slice(-900)); await shot(page, "pwa-fail"); } catch {} process.exit(1); });
 const ctx = await browser.newContext({ viewport: { width: 412, height: 915 }, isMobile: true, hasTouch: true, serviceWorkers: "allow" }); const page = await ctx.newPage(); const w = watch(page);

// ── Manifest & installability ──
await go(page, BASE + "/login");
const man = await (await ctx.request.get(BASE + "/manifest.webmanifest")).json();
R.ok(man.name && man.short_name && man.start_url === "/" && man.display === "standalone" && man.theme_color && man.background_color, "manifest has name, short_name, start_url, display, theme/background colours", JSON.stringify(man).slice(0, 200));
const sizes = []; for (const ic of man.icons) { const r = await ctx.request.get(BASE + ic.src); const buf = await r.body(); sizes.push({ src: ic.src, purpose: ic.purpose, status: r.status(), type: r.headers()["content-type"], w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) }); }
R.ok(sizes.every((s) => s.status === 200 && s.type === "image/png" && `${s.w}x${s.h}` === man.icons.find((i) => i.src === s.src).sizes), "every manifest icon loads with the declared dimensions", JSON.stringify(sizes));
R.ok(man.icons.some((i) => i.purpose === "maskable") && man.icons.some((i) => i.sizes === "192x192") && man.icons.some((i) => i.sizes === "512x512"), "192, 512 and maskable icons present");
const links = await page.evaluate(() => ({ manifest: document.querySelector('link[rel=manifest]')?.getAttribute("href"), apple: document.querySelector('link[rel=apple-touch-icon]')?.getAttribute("href"), theme: document.querySelector('meta[name=theme-color]')?.getAttribute("content") }));
R.ok(links.manifest === "/manifest.webmanifest" && links.apple && links.theme === "#111827", "<head> links manifest, apple-touch-icon, theme-color", JSON.stringify(links));

// Service worker registration and control
await until(page, async () => { const r = await navigator.serviceWorker.ready; return r.active?.state === "activated"; });
await reload(page);
const sw = await page.evaluate(async () => { const r = await navigator.serviceWorker.ready; return { scope: r.scope, state: r.active?.state, controlled: !!navigator.serviceWorker.controller }; });
R.ok(sw.state === "activated" && sw.controlled, `service worker activated and controlling the page (scope ${sw.scope})`, JSON.stringify(sw));
const cdp = await ctx.newCDPSession(page); const inst = await cdp.send("Page.getInstallabilityErrors").catch((e) => ({ err: String(e) }));
R.ok(Array.isArray(inst.installabilityErrors) && inst.installabilityErrors.length === 0, "Chromium reports NO installability errors", JSON.stringify(inst));
const appManifest = await cdp.send("Page.getAppManifest").catch(() => null); R.ok(!!appManifest && (appManifest.errors ?? []).length === 0, "Chromium parses the manifest with no errors", JSON.stringify(appManifest?.errors));

// ── Sign in, then warm the caches the way a user would ──
await loginUI(page, email); await page.getByRole("link", { name: "Batches" }).first().click().catch(async () => { await go(page, BASE + "/batches"); });
await go(page, BASE + "/batches"); await page.getByRole("link", { name: /CON-M25/ }).first().waitFor({ timeout: 10000 }); const listedCode = (await page.getByRole("link", { name: /CON-M25/ }).first().innerText()).trim();
// NOTE: /batches/new is deliberately NEVER opened online. The service worker must have pre-warmed it after sign-in.
const warmed = await until(page, async () => { const c = await caches.open((await caches.keys()).find((x) => x.startsWith("bg-pages-"))); const k = (await c.keys()).map((r) => new URL(r.url).pathname); return k.length >= 10 && k.includes("/batches/new") ? k : null; }); R.info("warmed pages: " + warmed.join(", ")); R.ok(true, "after sign-in the service worker pre-warms the core screens");
const caches1 = await page.evaluate(async () => { const out = {}; for (const k of await caches.keys()) out[k] = (await (await caches.open(k)).keys()).map((r) => new URL(r.url).pathname); return out; });
R.ok(Object.values(caches1).flat().every((p) => !p.startsWith("/bff/") && !p.startsWith("/auth/") && !p.startsWith("/p/")), "service-worker caches contain NO /bff, /auth or /p entries (no private API data)", JSON.stringify(caches1).slice(0, 400));
R.ok(Object.values(caches1).flat().some((p) => p.startsWith("/_next/static/")) && Object.values(caches1).flat().includes("/offline"), "static assets and /offline are cached");
const idb1 = await page.evaluate(() => new Promise((res) => { const r = indexedDB.open("bg-offline"); r.onsuccess = () => { const db = r.result; const tx = db.transaction("snap", "readonly").objectStore("snap").getAllKeys(); tx.onsuccess = () => res(tx.result.map(String)); }; r.onerror = () => res(["ERR"]); }));
R.ok(idb1.some((k) => k.includes("batches")) && !idb1.some((k) => /ai-|conversation|audit|users/.test(k)), "IndexedDB holds batch snapshots but never AI conversations / audit / users", idb1.join(" | ").slice(0, 300));

// ── An operation that cannot work offline (AI chat) must fail clearly, keep the user's text, and pretend nothing ──
await go(page, BASE + "/assistant"); await ctx.setOffline(true); await page.getByLabel(/Ask about IS codes/).fill("hello offline?"); await page.getByRole("button", { name: "Send" }).click();
await page.getByText(/couldn't reach the server|You are offline/i).first().waitFor({ timeout: 10000 }); R.ok((await page.getByLabel(/Ask about IS codes/).inputValue()) === "hello offline?", "AI request while offline: clear error shown and the user's question is kept for retry"); await ctx.setOffline(false);
// ── OFFLINE (A): the connection drops while the user is on the form ──
w.clear(); await go(page, BASE + "/batches/new"); await ctx.setOffline(true);
await page.getByLabel("Quantity (m³)").fill("5"); await page.getByLabel("Supplier").fill(`LiveDrop${RUN}`); await page.getByRole("button", { name: "Register batch" }).last().click();
await page.getByText("Pending sync").waitFor({ timeout: 10000 }); const live = await page.locator("main").innerText();
R.ok(/not yet confirmed|will be sent|saved on this device/i.test(live), "connection lost mid-form: the entry is kept as PENDING (not lost, not falsely confirmed)", live.slice(0, 200));
await page.locator(".offline-bar").waitFor({ timeout: 5000 }); R.ok(true, "the offline banner appears even though the browser never fired an 'offline' event (request failure detection)");
await ctx.setOffline(false); await go(page, BASE + "/sync"); await page.getByText("Everything is synced").waitFor({ timeout: 30000 }); R.ok(true, "reconnect: the live-drop entry synchronised automatically");
const one = await page.evaluate((r) => fetch(`/bff/batches?supplier=LiveDrop${r}`, { headers: { "x-bg-csrf": "1" } }).then((x) => x.json()), RUN); R.ok(one.data.length === 1, `exactly one server record for LiveDrop: ${one.data.length}`);

// ── OFFLINE (B): opening the app with no network ──
w.clear(); await go(page, BASE + "/batches"); await page.getByRole("link", { name: listedCode }).first().waitFor(); await ctx.setOffline(true);
await page.goto(BASE + "/batches", { waitUntil: "domcontentloaded" }); await page.waitForSelector('html[data-hydrated="1"]', { timeout: 15000 });
await page.locator(".offline-bar").waitFor({ timeout: 8000 }); R.ok(true, "app shell loads while OFFLINE (served by the service worker) with the offline banner");
await page.getByRole("link", { name: listedCode }).waitFor({ timeout: 8000 }); R.ok(true, "previously viewed batch list is available offline from the saved snapshot");
R.ok(await page.getByText(/Saved data from/).isVisible(), "the list clearly says it is showing SAVED data (not live)"); await shot(page, "10-offline-list");
await page.goto(BASE + "/audit", { waitUntil: "domcontentloaded" }).catch(() => {}); const t = await page.locator("body").innerText(); R.ok(/offline/i.test(t) && await page.getByRole("link", { name: "Try again" }).isVisible(), "a page that was never cached shows the friendly offline page (works without JavaScript)", t.slice(0, 120));
await page.goto(BASE + "/batches/new", { waitUntil: "domcontentloaded" }).catch(() => {}); await page.waitForSelector('html[data-hydrated="1"]', { timeout: 15000 }); R.ok(true, "a never-opened form (/batches/new) loads and is interactive offline (pre-warmed)");
await page.evaluate(() => { window.__log = []; const of = window.fetch; window.fetch = (...a) => { window.__log.push("fetch " + String(a[0]).slice(0, 40)); return of(...a).then((r) => { window.__log.push("ok " + r.status); return r; }, (e) => { window.__log.push("fail " + e.message); throw e; }); }; document.addEventListener("submit", () => window.__log.push("submit"), true); document.addEventListener("click", (e) => window.__log.push("click " + (e.target.closest("button,a")?.innerText || e.target.tagName).slice(0, 20)), true); });
await page.getByLabel("Quantity (m³)").fill("7"); await page.getByLabel("Supplier").fill(`OfflineMix${RUN}`); await page.getByRole("button", { name: "Register batch" }).last().click();
await page.getByText("Pending sync").waitFor({ timeout: 8000 }); const banner = await page.locator("main").innerText();
R.ok(/not yet confirmed|will be sent|saved on this device/i.test(banner), "offline creation is shown as PENDING (no false success message)", banner.slice(0, 220)); await shot(page, "11-offline-pending");
await page.goto(BASE + "/sync", { waitUntil: "domcontentloaded" }).catch(() => {}); await page.waitForSelector('html[data-hydrated="1"]'); await page.getByText("Waiting for server").first().waitFor({ timeout: 8000 }); R.ok(true, "pending change is listed on /sync as 'Waiting for server'");
// ── BACK ONLINE: automatic synchronisation ──
w.clear(); await ctx.setOffline(false); await page.goto(BASE + "/sync"); await page.waitForSelector('html[data-hydrated="1"]');
await page.getByText("Everything is synced").waitFor({ timeout: 25000 }); R.ok(true, "after reconnect the queued batch is sent and the queue empties (server-confirmed)");
await go(page, BASE + "/batches"); await page.getByText(`OfflineMix${RUN}`).first().waitFor({ timeout: 8000 }); R.ok(true, "the offline-created batch now exists on the server and in the live list");
const dup = await page.evaluate((r) => fetch(`/bff/batches?supplier=OfflineMix${r}`, { headers: { "x-bg-csrf": "1" } }).then((x) => x.json()), RUN); R.ok(dup.data.length === 1, `exactly ONE server record for the offline batch (no duplicates): ${dup.data.length}`);

// ── UPDATE FLOW: ship a new service worker and check the user is told, and can apply it ──
const swStandalone = ".next/standalone/public/sw.js";
const orig = execSync(`cat ${swStandalone}`).toString(); const oldVer = /const VERSION = "([^"]+)"/.exec(orig)[1]; R.ok(oldVer !== "__BUILD_ID__" && oldVer.length > 5, `service worker is stamped with the build id (${oldVer})`);
execSync(`sed -i 's/const VERSION = "${oldVer}"/const VERSION = "zz-update-test"/' ${swStandalone}`);
await page.evaluate(() => navigator.serviceWorker.getRegistration().then((r) => r.update())); await page.getByText("Update available").waitFor({ timeout: 15000 }); R.ok(true, "a new deployment is detected and the user sees 'Update available'"); await shot(page, "12-update-toast");
const cachesBefore = await page.evaluate(() => caches.keys()); await page.getByRole("button", { name: "Reload" }).click();
const keysAfter = await until(page, async (old) => { const k = await caches.keys(); return k.some((x) => x.endsWith("zz-update-test")) && !k.some((x) => x.endsWith(old)) && navigator.serviceWorker.controller ? k : null; }, oldVer, { timeout: 25000 });
await page.waitForSelector('html[data-hydrated="1"]', { timeout: 15000 }); R.ok(true, "applying the update activates the new version, takes control, and deletes the old caches: " + JSON.stringify({ before: cachesBefore, after: keysAfter }));
execSync(`printf '%s' '${orig.replace(/'/g, "'\\''")}' > ${swStandalone}`);

// ── Sign-out wipes local data ──
await go(page, BASE + "/batches"); await page.getByRole("button", { name: "Sign out" }).click(); await page.waitForURL("**/login");
const wiped = await page.evaluate(async () => { const db = await new Promise((res) => { const r = indexedDB.open("bg-offline"); r.onsuccess = () => res(r.result); }); const count = (s) => new Promise((res) => { const q = db.transaction(s).objectStore(s).count(); q.onsuccess = () => res(q.result); }); const pages = (await caches.keys()).filter((k) => k.includes("pages")); let pageEntries = 0; for (const k of pages) pageEntries += (await (await caches.open(k)).keys()).length; return { snap: await count("snap"), queue: await count("queue"), pageEntries, ls: JSON.stringify(localStorage) }; });
R.ok(wiped.snap === 0 && wiped.queue === 0 && wiped.pageEntries === 0, "sign-out clears offline snapshots, the write queue and cached pages", JSON.stringify(wiped));
R.ok(!/"bg.project"/.test(wiped.ls), "sign-out clears the stored project selection", wiped.ls);
await browser.close(); process.exit(R.done() ? 1 : 0);
