import chromium from "@sparticuz/chromium"; import puppeteer from "puppeteer-core"; import fs from "node:fs"; import os from "node:os";
export const BASE = "http://127.0.0.1:3100";
export async function launch() { return puppeteer.launch({ args: [...chromium.args, "--no-sandbox"], executablePath: await chromium.executablePath(), headless: "shell" }); }
export async function newPage(b, w = 1280, h = 800, mobile = false) {
  const p = await b.newPage(); await p.setViewport({ width: w, height: h, isMobile: mobile, hasTouch: mobile, deviceScaleFactor: mobile ? 2 : 1 });
  p.issues = []; p.on("console", (m) => { if (["error", "warning"].includes(m.type())) p.issues.push(`${m.type()}: ${m.text().slice(0, 240)}`); });
  p.on("pageerror", (e) => p.issues.push("pageerror: " + String(e).slice(0, 240)));
  p.on("requestfailed", (r) => { if (!r.failure()?.errorText.includes("ABORTED")) p.issues.push(`reqfail: ${r.url().replace(BASE, "")} ${r.failure()?.errorText}`); });
  return p;
}
export async function login(p, email = "admin@buildguard.demo", pw = "Demo@1234") {
  await p.goto(BASE + "/login", { waitUntil: "networkidle0" }); await p.type("input[type=email]", email); await p.type("input[type=password]", pw);
  await Promise.all([p.waitForFunction(() => location.pathname === "/", { timeout: 15000 }), p.click("button[type=submit]")]); await p.waitForSelector("main#main", { timeout: 15000 });
}
export const axeSrc = fs.readFileSync(new URL("../node_modules/axe-core/axe.min.js", import.meta.url), "utf8");
export async function axeRun(p) { await p.evaluate(axeSrc); return p.evaluate(async () => { const r = await window.axe.run(document, { resultTypes: ["violations"] }); return r.violations.map((v) => ({ id: v.id, impact: v.impact, n: v.nodes.length, sample: v.nodes[0].html.slice(0, 140), target: v.nodes[0].target.join(" ") })); }); }
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
