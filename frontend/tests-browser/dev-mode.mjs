// Development-mode behaviour: hot reload, no CSP/console errors, and stale production service workers are removed. node tests-browser/dev-mode.mjs
import { spawn } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { launch, reporter, until, watch } from "./harness.mjs";
const R = reporter("dev-mode"); const PORT = 3300, BASE = `http://localhost:${PORT}`;
const env = { ...process.env, PORT: String(PORT), BACKEND_URL: process.env.BACKEND_URL ?? "http://127.0.0.1:8000", COOKIE_SECURE: "true", NEXT_TELEMETRY_DISABLED: "1" };
const startProc = (cmd, args, e) => { const p = spawn(cmd, args, { env: e, stdio: ["ignore", "pipe", "pipe"] }); p.logs = ""; p.stdout.on("data", (d) => (p.logs += d)); p.stderr.on("data", (d) => (p.logs += d)); return p; };
const waitUp = async (path = "/login") => { for (let i = 0; i < 90; i++) { try { const r = await fetch(BASE + path); if (r.status < 500) return true; } catch {} await new Promise((r) => setTimeout(r, 500)); } return false; };
const kill = (p) => new Promise((r) => { p.once("exit", r); p.kill("SIGTERM"); setTimeout(() => { p.kill("SIGKILL"); r(); }, 3000); });

// 1) production build on this port registers its service worker
let prod = startProc(process.execPath, [".next/standalone/server.js"], { ...env, NODE_ENV: "production", HOSTNAME: "0.0.0.0" }); R.ok(await waitUp(), "production build started on the port");
const browser = await launch(); const ctx = await browser.newContext({ serviceWorkers: "allow" }); const page = await ctx.newPage();
await page.goto(BASE + "/login"); await until(page, async () => (await navigator.serviceWorker.ready).active?.state === "activated"); await page.reload(); await page.waitForSelector('html[data-hydrated="1"]');
const prodCaches = await page.evaluate(async () => (await caches.keys()).filter((k) => k.startsWith("bg-"))); R.ok(prodCaches.length > 0, `production run left a service worker + caches behind (${prodCaches.join(", ")})`);
await kill(prod);

// 2) developer now runs `next dev` on the SAME address
const dev = startProc("node_modules/.bin/next", ["dev", "-p", String(PORT)], env); R.ok(await waitUp(), "next dev started (" + (dev.logs.match(/Ready in [\d.]+m?s/)?.[0] ?? "ready") + ")");
const w = watch(page, ["favicon"]); await page.goto(BASE + "/login"); await page.waitForSelector('html[data-hydrated="1"]', { timeout: 60000 });
const cleaned = await until(page, async () => { const regs = await navigator.serviceWorker.getRegistrations(); const ks = (await caches.keys()).filter((k) => k.startsWith("bg-")); return regs.length === 0 && ks.length === 0 ? "clean" : null; }, null, { timeout: 15000 }).catch(() => null);
R.ok(cleaned === "clean", "dev mode removed the stale production service worker and its caches (your edits will not be hidden)");
R.ok(w.errors.filter((e) => !/Download the React DevTools|Fast Refresh|\[HMR\]/i.test(e)).length === 0, "dev mode: no CSP violations or console errors on the login page", JSON.stringify(w.errors));

// 3) hot reload: edit a source file, the open page updates by itself
const file = "src/lib/i18n.tsx"; const orig = readFileSync(file, "utf8");
try {
  writeFileSync(file, orig.replace('"login.title": "Sign in to BUILDGUARD"', '"login.title": "Sign in to BUILDGUARD HMR_TEST"'));
  await page.getByRole("heading", { name: /HMR_TEST/ }).waitFor({ timeout: 30000 }); R.ok(true, "hot reload: an edit appears in the open page without a manual refresh");
  await page.waitForSelector('html[data-hydrated="1"]', { timeout: 30000 }); await page.getByLabel("Email").fill("still@works.test"); R.ok((await page.getByLabel("Email").inputValue()) === "still@works.test", "page is interactive after the hot update (typing works)");
} finally { writeFileSync(file, orig); }
await page.getByRole("heading", { name: "Sign in to BUILDGUARD", exact: true }).waitFor({ timeout: 30000 }).catch(() => {});
// 4) dev-mode login works end-to-end (cookie flags, no service worker interference)
await page.goto(BASE + "/register"); await page.waitForSelector('html[data-hydrated="1"]'); const email = `dev${Date.now()}@dev.test`;
await page.getByLabel("Full name").fill("Dev User"); await page.getByLabel("Email").fill(email); await page.getByLabel("Password", { exact: false }).first().fill("Passw0rd!Passw0rd"); await page.getByRole("button", { name: "Create an account" }).click();
await page.waitForURL(BASE + "/", { timeout: 60000 }); R.ok(true, "register + sign-in works in dev mode"); await page.waitForTimeout(1500);
R.ok(w.errors.filter((e) => !/Download the React DevTools|Fast Refresh|\[HMR\]|ERR_ABORTED/i.test(e)).length === 0, "dev mode: still no console errors after sign-in", JSON.stringify(w.errors.slice(-3)));
R.ok(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length === 0), "dev mode registers no service worker");
await kill(dev); await browser.close(); process.exit(R.done() ? 1 : 0);
