// Real-browser QA harness. Chromium comes from CHROME_PATH, else the bundled @sparticuz/chromium build.
import { chromium as pw } from "playwright-core";
export const BASE = process.env.BASE ?? "http://localhost:3100";
export async function launch() {
  let exe = process.env.CHROME_PATH;
  if (!exe) { const { default: c } = await import("@sparticuz/chromium"); exe = await c.executablePath(); }
  return pw.launch({ executablePath: exe, headless: true, args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--hide-scrollbars"] });
}
export function reporter(name) {
  let pass = 0, fail = 0; const t0 = Date.now();
  return {
    ok(c, m, extra = "") { c ? pass++ : fail++; console.log(`${c ? "PASS" : "FAIL"}  ${m}${c ? "" : "  <<< " + String(extra).slice(0, 400)}`); return c; },
    info(m) { console.log(`INFO  ${m}`); },
    done() { console.log(`\n[${name}] ${pass} passed, ${fail} failed in ${((Date.now() - t0) / 1000).toFixed(1)}s`); return fail; },
  };
}
/** Collects everything a user would never want to see: console errors (incl. CSP violations), uncaught exceptions, failed requests, unexpected 4xx/5xx. */
export function watch(page, allow = []) {
  const s = { errors: [], http: [], failed: [] };
  page.on("console", (m) => { if (m.type() === "error") s.errors.push(m.text().slice(0, 300)); });
  page.on("pageerror", (e) => s.errors.push("pageerror: " + String(e.message).slice(0, 300)));
  page.on("requestfailed", (r) => { const u = r.url(); if (r.failure()?.errorText === "net::ERR_ABORTED" && (u.includes("_rsc=") || u.includes("/bff/ai/chat/stream") || (r.method() === "GET" && u.includes("/bff/")))) return; /* prefetch cancels + headless DevTools reports finished SSE bodies as aborted; server ledger proves delivery (see docs/QA) */ /* cancelled prefetches / superseded GET queries (navigation, StrictMode double-mount in dev) are normal */ if (!allow.some((a) => u.includes(a))) s.failed.push(`${r.method()} ${u} ${r.failure()?.errorText}`); });
  page.on("response", (r) => { const u = r.url(); if (r.status() >= 400 && !allow.some((a) => u.includes(a))) s.http.push(`${r.status()} ${r.request().method()} ${u.replace(BASE, "")}`); });
  s.clear = () => { s.errors.length = 0; s.http.length = 0; s.failed.length = 0; };
  return s;
}
/** Navigate and wait until React has hydrated: typing earlier would be wiped when hydration reconciles controlled inputs. */
export async function go(page, url) { await page.goto(url); await page.waitForSelector('html[data-hydrated="1"]', { timeout: 15000 }).catch(() => {}); }
export const shot = (page, name) => page.screenshot({ path: `/tmp/shots/${name}.png`, fullPage: false });
export async function registerUI(page, email, name = "QA Admin", password = "Passw0rd!Passw0rd") {
  await go(page, BASE + "/register"); await page.getByLabel("Full name").fill(name); await page.getByLabel("Email").fill(email); await page.getByLabel("Password", { exact: false }).first().fill(password);
  await page.getByRole("button", { name: "Create an account" }).click(); await page.waitForURL(BASE + "/", { timeout: 15000 });
}
export async function loginUI(page, email, password = "Passw0rd!Passw0rd") {
  await go(page, BASE + "/login"); await page.getByLabel("Email").fill(email); await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click(); await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 15000 });
}

/** Poll an async page.evaluate until it returns truthy (page.waitForFunction does not await async predicates). */
export async function until(page, fn, arg, { timeout = 20000, every = 250 } = {}) {
  const t0 = Date.now(); let last;
  while (Date.now() - t0 < timeout) { last = await page.evaluate(fn, arg).catch(() => null); if (last) return last; await page.waitForTimeout(every); }
  throw new Error("until(): timed out; last value " + JSON.stringify(last));
}

/** Reload that tolerates the navigation being aborted while a service worker claims the page. */
export async function reload(page) { for (let i = 0; i < 4; i++) { try { await page.reload(); await page.waitForSelector('html[data-hydrated="1"]', { timeout: 15000 }); return; } catch { await page.waitForTimeout(500); } } throw new Error("reload failed"); }
