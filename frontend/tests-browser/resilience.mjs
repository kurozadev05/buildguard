// Failure injection against the real stack: Redis / AI provider / backend / PostgreSQL down, slow network. UI must stay understandable and recover.
import { execSync } from "node:child_process";
import { BASE, go, launch, loginUI, reporter, shot, until } from "./harness.mjs";
const R = reporter("resilience"); const stack = (cmd) => execSync(`cd .. && scripts/qa_stack.sh ${cmd}`, { stdio: "pipe" }).toString();
const ready = async () => { try { const r = await fetch("http://127.0.0.1:8801/ready"); return { code: r.status, body: await r.json() }; } catch { return { code: 0, body: null }; } };
const browser = await launch(); const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } }); const page = await ctx.newPage();
await loginUI(page, "admin@qa.test"); await go(page, BASE + "/batches"); await page.getByRole("link", { name: /CON-M25/ }).first().waitFor();
let r0 = await ready(); R.ok(r0.code === 200 && r0.body.status === "ready" && r0.body.checks.redis === "ok", "baseline /ready: all dependencies ok", JSON.stringify(r0));

// ── Redis down ──
stack("stop redis"); await new Promise((r) => setTimeout(r, 800)); let r1 = await ready();
R.ok(r1.code === 200 && r1.body.status === "degraded" && r1.body.checks.redis === "unavailable", "Redis down: /ready says 'degraded / redis: unavailable' (not silent, not a crash)", JSON.stringify(r1));
await go(page, BASE + "/assistant"); await page.getByLabel(/Ask about IS codes/).fill("What is the minimum cement content?"); await page.getByRole("button", { name: "Send" }).click(); await page.getByRole("button", { name: "Send" }).waitFor({ timeout: 25000 });
const ans1 = await page.locator(".msg.ai .bubble").last().innerText(); R.ok(ans1.length > 20 && !/error|failed/i.test(ans1.slice(0, 40)), "Redis down: AI chat still answers (falls back to in-memory limits)", ans1.slice(0, 80));
await go(page, BASE + "/batches"); await page.getByRole("link", { name: /CON-M25/ }).first().waitFor({ timeout: 8000 }); R.ok(true, "Redis down: normal pages and data still work");
stack("start redis"); await new Promise((r) => setTimeout(r, 1500)); let r2 = await ready(); R.ok(r2.body?.status === "ready", "Redis restored: /ready returns to 'ready' without restarting the API", JSON.stringify(r2));

// ── AI provider down ──
stack("stop llm"); await go(page, BASE + "/assistant"); await page.getByLabel(/Ask about IS codes/).fill("How many cube samples are needed?"); await page.getByRole("button", { name: "Send" }).click(); await page.getByRole("button", { name: "Send" }).waitFor({ timeout: 60000 });
const ans2 = await page.locator(".msg.ai .bubble").last().innerText(); const note = await page.locator(".msg.ai").last().innerText();
R.ok(ans2.length > 20, "AI provider down: user still gets an answer from verified notes", ans2.slice(0, 90)); R.ok(/verified notes only|unavailable|could not be completed/i.test(note), "AI provider down: the UI says the model was unavailable (no fake 'AI' claim)", note.slice(-160)); await shot(page, "20-ai-degraded");
stack("start llm");
// The AI layer's circuit breaker opens after 3 failures and allows a trial request after a 30 s cooldown: recovery must happen by itself within ~45 s.
let recovered = false, tries = 0; const t0 = Date.now();
while (Date.now() - t0 < 60000 && !recovered) { tries++; await page.getByLabel(/Ask about IS codes/).fill("And what about slump? " + tries); await page.getByRole("button", { name: "Send" }).click(); await page.getByRole("button", { name: "Send" }).waitFor({ timeout: 60000 }); recovered = !/verified notes only/i.test(await page.locator(".msg.ai").last().innerText()); if (!recovered) await page.waitForTimeout(4000); }
R.ok(recovered, `AI provider restored: the assistant returns to the model by itself (after ${((Date.now() - t0) / 1000).toFixed(0)} s, ${tries} attempt(s); breaker cooldown is 30 s)`);

// ── Backend down ──
await go(page, BASE + "/batches"); await page.getByRole("link", { name: /CON-M25/ }).first().waitFor(); stack("stop api");
await go(page, BASE + "/alerts"); await page.getByText("Something went wrong").first().waitFor({ timeout: 15000 }); const errText = await page.locator("main").innerText();
R.ok(/service|try again|reach/i.test(errText) && !/traceback|psycopg|sqlalchemy|127\.0\.0\.1|8801/i.test(errText), "Backend down: friendly error with Retry, no internals leaked", errText.slice(0, 160)); await shot(page, "21-backend-down");
R.ok(await page.getByRole("button", { name: "Try again" }).first().isVisible(), "Backend down: a Retry button is offered");
stack("start api"); await until(page, async () => (await fetch("/bff/auth/me", { headers: { "x-bg-csrf": "1" } })).status === 200 || null, null, { timeout: 30000 });
await page.getByRole("button", { name: "Try again" }).first().click(); await page.getByText("Something went wrong").waitFor({ state: "hidden", timeout: 15000 }); R.ok(true, "Backend restored: Retry recovers the same page with the session intact (no re-login)");
R.ok(!page.url().includes("/login"), "session survived a backend restart");

// ── PostgreSQL down ──
stack("stop pg"); await new Promise((r) => setTimeout(r, 800)); let r3 = await ready(); R.ok(r3.code === 503, "PostgreSQL down: /ready answers 503", JSON.stringify(r3));
await go(page, BASE + "/tests"); await page.getByText("Something went wrong").first().waitFor({ timeout: 20000 }); const dbErr = await page.locator("main").innerText();
R.ok(!/psycopg|postgres|sqlalchemy|connection refused|5544|traceback/i.test(dbErr), "PostgreSQL down: UI shows a friendly error and leaks no database details", dbErr.slice(0, 140));
stack("start pg"); await new Promise((r) => setTimeout(r, 2500)); let r4 = await until(page, async () => { const r = await fetch("/bff/auth/me", { headers: { "x-bg-csrf": "1" } }); return r.status === 200 ? "ok" : null; }, null, { timeout: 40000 }).catch(() => null);
R.ok(r4 === "ok", "PostgreSQL restored: the API reconnects on its own (pool recovers) and the session is still valid");
let r5 = await ready(); R.ok(r5.code === 200, "PostgreSQL restored: /ready is 200 again", JSON.stringify(r5));

// ── Slow network: skeletons / loading states must be visible, page must not jump to an error ──
const cdp = await ctx.newCDPSession(page); await cdp.send("Network.enable"); await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 900, downloadThroughput: 60 * 1024, uploadThroughput: 60 * 1024 });
await page.goto(BASE + "/batches"); const sawSkeleton = await page.locator('.skeleton, [aria-label="Loading"]').first().waitFor({ timeout: 15000 }).then(() => true).catch(() => false); R.ok(sawSkeleton, "slow network (900 ms latency, 60 KB/s): loading skeleton is shown");
await page.getByRole("link", { name: /CON-M25/ }).first().waitFor({ timeout: 60000 }); R.ok(!(await page.getByText("Something went wrong").isVisible()), "slow network: content arrives with no false error state");
await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
await browser.close(); process.exit(R.done() ? 1 : 0);
