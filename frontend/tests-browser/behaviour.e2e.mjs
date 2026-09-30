import { BASE, launch, newPage, login, sleep } from "./lib.mjs";
let pass = 0, fail = 0; const ok = (c, m, x = "") => { c ? pass++ : fail++; console.log(`${c ? "PASS" : "FAIL"}  ${m}${c ? "" : "  <<< " + x}`); };
const b = await launch(); const p = await newPage(b, 1280, 900); await login(p);
const api = async (path, method = "GET", body) => p.evaluate(async (path, method, body) => { const r = await fetch("/bff/" + path, { method, headers: { "x-bg-csrf": "1", "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); return { s: r.status, j: await r.json().catch(() => null) }; }, path, method, body);

// ---- 1. streaming chat
await p.goto(BASE + "/assistant", { waitUntil: "networkidle0" });
await p.type("#ask", "How many cube samples do I need per cubic metre of concrete?"); const t0 = Date.now(); await p.click("button[aria-label='Send']");
const samples = []; for (let i = 0; i < 40; i++) { await sleep(150); const t = await p.evaluate(() => document.querySelector(".msg.ai:last-of-type .bubble")?.textContent ?? ""); samples.push([Date.now() - t0, t.length]); if (i > 5 && samples.at(-1)[1] === samples.at(-2)[1] && samples.at(-1)[1] > 50) break; }
const growth = new Set(samples.map((s) => s[1])).size; ok(growth >= 5, `answer renders incrementally in the browser (${growth} distinct lengths, first text at ${samples.find((s) => s[1] > 0)?.[0]} ms, done at ${samples.at(-1)[0]} ms)`);
await p.waitForFunction(() => [...document.querySelectorAll("button")].some((x) => x.textContent.includes("Copy")), { timeout: 8000 }).catch(() => {});
const done = await p.evaluate(() => ({ text: document.querySelector(".msg.ai:last-of-type .bubble")?.textContent, chip: !!document.querySelector(".msg.ai .bubble .chip"), src: !!document.querySelector(".msg.ai details"), copy: [...document.querySelectorAll("button")].some((x) => x.textContent.includes("Copy")) }));
ok(done.text?.includes("cube sample") && done.chip && done.src && done.copy, "final answer has citation chip, sources list and copy action", JSON.stringify(done).slice(0, 200));
ok(await p.evaluate(() => (document.querySelector('[role=status][aria-live=polite].sr-only')?.textContent ?? "").includes("complete")), "screen-reader completion announcement");
// stop mid-stream
await p.evaluate(() => [...document.querySelectorAll("aside button")].find((x) => x.textContent.includes("New chat"))?.click()); await sleep(300); await p.type("#ask", "Explain the curing period and cancel me midway please"); await p.click("button[aria-label='Send']");
await p.waitForSelector("button[aria-label='Stop']", { timeout: 5000 }); await sleep(900); const before = await p.evaluate(() => document.querySelector(".msg.ai:last-of-type .bubble")?.textContent.length);
await p.click("button[aria-label='Stop']"); await sleep(1500); const after = await p.evaluate(() => ({ len: document.querySelector(".msg.ai:last-of-type .bubble")?.textContent.length, stopped: document.body.innerText.includes("partial answer"), send: !!document.querySelector("button[aria-label='Send']") }));
ok(after.stopped && after.send && after.len - before < 40, `Stop halts generation, keeps the partial text, restores Send (${before} -> ${after.len} chars)`, JSON.stringify(after));
// ---- 2. form validation (empty batch form)
await p.goto(BASE + "/batches/new", { waitUntil: "networkidle0" }); await p.click("button[type=submit]"); await sleep(300);
const errs = await p.evaluate(() => [...document.querySelectorAll(".err-text")].map((e) => e.textContent)); ok(errs.some((e) => /quantity/i.test(e)), "empty form shows a field error and does not submit", errs.join("|"));
ok(await p.evaluate(() => document.querySelector("[aria-invalid=true]") !== null), "invalid field is marked aria-invalid");
// ---- 3. XSS through stored data
const pj = (await api("projects")).j.data[0].id;
const xs = await api("batches", "POST", { project_id: pj, grade: "M25", quantity_m3: 5, supplier: "<img src=x onerror=window.__xss=1><script>window.__xss=2</script>Evil RMC" });
await p.goto(BASE + "/batches?q=Evil", { waitUntil: "networkidle0" }); await sleep(600);
const xr = await p.evaluate(() => ({ fired: window.__xss ?? null, hasImg: !!document.querySelector("td img, main img[src='x']"), shown: document.body.innerText.includes("<img src=x") }));
ok(xs.s === 201 && xr.fired === null && !xr.hasImg && xr.shown, "HTML in stored data is shown as text and never executes", JSON.stringify(xr));
// ---- 4. Hindi toggle
await p.goto(BASE + "/", { waitUntil: "networkidle0" }); await p.click("button[aria-label*='हिन्दी']"); await sleep(400);
ok(await p.evaluate(() => document.documentElement.lang === "hi" && document.querySelector("nav a")?.textContent.includes("डैशबोर्ड")), "Hindi toggle switches nav labels and <html lang>");
await p.click("button[aria-label*='English']"); await sleep(300);
// ---- 5. keyboard: skip link + focus visibility
await p.goto(BASE + "/batches", { waitUntil: "networkidle0" }); await p.keyboard.press("Tab"); const f1 = await p.evaluate(() => document.activeElement.className); ok(f1.includes("skip-link"), "first Tab stop is the skip link");
await p.keyboard.press("Enter"); await sleep(100); ok(await p.evaluate(() => document.activeElement.id === "main" || location.hash === "#main"), "skip link moves to main content");
// ---- 6. service worker + offline shell
await p.goto(BASE + "/batches/new", { waitUntil: "networkidle0" }); await p.goto(BASE + "/sync", { waitUntil: "networkidle0" });
const swState = await p.evaluate(async () => { const reg = await navigator.serviceWorker.getRegistration(); if (!reg) return "none"; await navigator.serviceWorker.ready; return reg.active?.state ?? "no-active"; });
ok(swState === "activated", `service worker registered and activated (${swState})`);
await p.reload({ waitUntil: "networkidle0" }); await sleep(500);
const caches_ = await p.evaluate(async () => { const out = {}; for (const k of await caches.keys()) out[k] = (await (await caches.open(k)).keys()).map((r) => new URL(r.url).pathname); return out; });
const all = Object.values(caches_).flat(); ok(!all.some((u) => u.startsWith("/bff") || u.startsWith("/auth") || u.startsWith("/p/")), `worker never cached API/auth/passport URLs (cached: ${all.length} entries e.g. ${all.slice(0, 4).join(", ")})`);
ok(all.includes("/offline") && all.some((u) => u.startsWith("/_next/static/")), "offline page and static assets are cached");
// ---- 7. offline queue end to end
await p.goto(BASE + "/batches/new", { waitUntil: "networkidle0" }); await sleep(800);
await p.setOfflineMode(true); await sleep(300);
ok(await p.evaluate(() => !!document.querySelector(".offline-bar")), "offline banner appears");
await p.select("select >> nth=0", "").catch(() => {}); const qty = await p.$$("input[type=number]"); await qty[0].type("7.5");
await p.evaluate(() => { const s = [...document.querySelectorAll("input")].find((i) => i.type === "text" && !i.value); s?.focus(); }); await p.keyboard.type("Offline Mix Co");
await p.click("button[type=submit]"); await sleep(1200);
const pend = await p.evaluate(() => ({ alert: document.body.innerText.includes("Pending sync"), toast: document.body.innerText.includes("Saved on this device"), claimedOk: /registered/i.test(document.querySelector(".toasts")?.textContent ?? "") }));
ok(pend.alert && !pend.claimedOk, "offline submit shows PENDING, never claims success", JSON.stringify(pend));
await p.setOfflineMode(false); await p.goto(BASE + "/sync", { waitUntil: "networkidle0" }); await sleep(500);
const srv1 = await api("batches?supplier=Offline%20Mix&limit=5"); 
await p.evaluate(() => window.dispatchEvent(new Event("online"))); 
for (let i = 0; i < 40; i++) { await sleep(500); const r = await api("batches?supplier=Offline%20Mix&limit=5"); if (r.j.data.length) break; }
const srv2 = await api("batches?supplier=Offline%20Mix&limit=5"); ok(srv2.j.data.length === 1, `after reconnect the queued batch was created on the server exactly once (${srv2.j.data.length})`, JSON.stringify(srv2.j).slice(0, 200));
await sleep(1000); await p.reload({ waitUntil: "networkidle0" }); ok(await p.evaluate(() => document.body.innerText.includes("Everything is synced")), "sync page shows queue emptied only after server confirmation");
// ---- 8. offline navigation
await p.setOfflineMode(true);
const okNav = await p.goto(BASE + "/batches/new", { waitUntil: "domcontentloaded" }).then((r) => r?.status()).catch((e) => "ERR " + e.message.slice(0, 40)); await sleep(800);
ok(okNav === 200 && (await p.evaluate(() => document.body.innerText)).includes("Register batch"), `previously visited page opens offline from the worker cache (${okNav})`);
const nav2 = await p.goto(BASE + "/risk", { waitUntil: "domcontentloaded" }).then((r) => r?.status()).catch((e) => "ERR " + e.message.slice(0, 50)); await sleep(500);
ok((await p.evaluate(() => document.body.innerText)).includes("offline"), `never-visited page falls back to the offline screen (${nav2})`);
await p.setOfflineMode(false);
// ---- 9. session expiry mid-use
await p.goto(BASE + "/batches", { waitUntil: "networkidle0" }); await p.deleteCookie(...(await p.cookies()).filter((c) => c.name === "bg_at"));
await p.evaluate(async () => { await fetch("/auth/logout", { method: "POST", headers: { "x-bg-csrf": "1" } }); });   // server ends the session
await p.click("button[aria-label='Batches'], nav a[href='/tests']").catch(() => {}); await sleep(1500);
await p.goto(BASE + "/batches", { waitUntil: "networkidle0" }); ok(p.url().includes("/login?next=%2Fbatches"), `signed-out user is redirected to login with return path (${p.url().replace(BASE, "")})`);
console.log(`\n${pass} passed, ${fail} failed`); await b.close(); process.exit(fail ? 1 : 0);
