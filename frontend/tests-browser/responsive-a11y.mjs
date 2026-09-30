// Responsive layout + accessibility in a real browser (axe incl. colour contrast), 6 viewports x all screens.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { BASE, go, launch, loginUI, reporter, shot } from "./harness.mjs";
const R = reporter("responsive-a11y"); const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const VIEWPORTS = [["small-phone", 360, 640], ["large-phone", 412, 915], ["tablet", 768, 1024], ["laptop", 1280, 800], ["desktop", 1440, 900], ["large-monitor", 2560, 1440]];
const browser = await launch(); const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } }); const page = await ctx.newPage();
await loginUI(page, "admin@qa.test");
const ids = await page.evaluate(async () => { const j = (p) => fetch("/bff/" + p, { headers: { "x-bg-csrf": "1" } }).then((r) => r.json()).then((x) => x.data); const projects = await j("projects"); const batches = await j("batches?limit=1"); const tests = await j("tests?limit=1"); return { p: projects[0].id, b: batches[0].id, t: tests[0]?.id }; });
const PAGES = ["/", "/batches", `/batches/${ids.b}`, "/batches/new", "/tests", "/tests/new", `/tests/${ids.t}`, "/projects", `/projects/${ids.p}`, "/risk", "/evidence", "/assistant", "/advisor", "/alerts", "/investigations", "/insights", "/audit", "/admin", "/account", "/sync"];

async function overflow(page) {
  return page.evaluate(() => {
    const w = window.innerWidth; const bad = [];
    for (const e of document.querySelectorAll("body *")) { const r = e.getBoundingClientRect(); if (r.width === 0 || r.right <= w + 1) continue; if (e.closest(".table-wrap, pre, .drawer, .toasts, [aria-hidden=true]")) continue; let sc = false; for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) { const ox = getComputedStyle(a).overflowX; if (ox === "auto" || ox === "scroll") { sc = true; break; } } if (sc) continue; /* inside an intentional horizontal scroller (tab strip) */ const cs = getComputedStyle(e); if (cs.position === "fixed" || cs.visibility === "hidden" || cs.display === "none") continue; bad.push(`${e.tagName.toLowerCase()}.${String(e.className).slice(0, 30)} right=${Math.round(r.right)}`); if (bad.length > 4) break; }
    return { scrollW: document.documentElement.scrollWidth, w, bad };
  });
}
const summary = {}; const axeAll = new Map(); const small = new Map();
for (const [name, W, H] of VIEWPORTS) {
  await page.setViewportSize({ width: W, height: H }); let overflowPages = [];
  for (const path of PAGES) {
    await go(page, BASE + path); await page.locator("main#main h1, main#main .state").first().waitFor({ timeout: 15000 }).catch(() => {}); await page.waitForTimeout(400);
    const o = await overflow(page); if (o.scrollW > o.w + 1 || o.bad.length) overflowPages.push(`${path} (scrollW ${o.scrollW} > ${o.w}; ${o.bad.slice(0, 2).join("; ")})`);
    if (W === 412 || W === 1280) {   // axe on a phone and a desktop width (content differs: tab bar vs sidebar)
      await page.evaluate(AXE); const res = await page.evaluate(() => axe.run(document, { resultTypes: ["violations"] }));
      for (const v of res.violations) { const k = `${v.id} [${v.impact}]`; if (!axeAll.has(k)) axeAll.set(k, { help: v.help, where: new Set(), sample: v.nodes[0]?.html.slice(0, 110) }); axeAll.get(k).where.add(path + "@" + W); }
    }
    if (W <= 412) { const tiny = await page.evaluate(() => [...document.querySelectorAll("a[href],button,input:not([type=hidden]),select,textarea")].filter((e) => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e); return r.width > 0 && cs.visibility !== "hidden" && (r.height < 36 || r.width < 36) && !e.closest("p,li,.small,.hint,td,dd") && !e.matches(".sr-only,[type=checkbox],[type=radio],[type=file]"); }).map((e) => `${e.tagName.toLowerCase()}:${(e.innerText || e.getAttribute("aria-label") || "").slice(0, 18)} ${Math.round(e.getBoundingClientRect().width)}x${Math.round(e.getBoundingClientRect().height)}`).slice(0, 4)); for (const t of tiny) small.set(t.replace(/ \d+x\d+$/, "") + " " + t.match(/\d+x\d+$/)?.[0], path); }
    if (["/", `/batches/${ids.b}`, "/assistant"].includes(path) && ["small-phone", "tablet", "laptop"].includes(name)) await shot(page, `r-${name}-${path === "/" ? "dashboard" : path.split("/")[1]}`);
  }
  summary[name] = overflowPages; R.ok(overflowPages.length === 0, `${name} ${W}x${H}: no horizontal overflow on ${PAGES.length} screens`, overflowPages.join(" || "));
}
const critical = [...axeAll.entries()]; for (const [k, v] of critical) R.info(`axe ${k}: ${v.help} | ${[...v.where].slice(0, 4).join(", ")} | e.g. ${v.sample}`);
R.ok(critical.filter(([k]) => /critical|serious/.test(k)).length === 0, `axe (real browser, incl. colour contrast): ${critical.length} rule(s) violated across ${PAGES.length} screens x 2 widths; critical/serious: ${critical.filter(([k]) => /critical|serious/.test(k)).length}`);
R.ok(small.size === 0, `touch targets on phones: ${small.size} element type(s) smaller than 36px` + (small.size ? " -> " + [...small.entries()].slice(0, 8).map(([k, v]) => k + " @" + v).join(" | ") : ""));

// ── keyboard + focus + dialogs ──
await page.setViewportSize({ width: 1280, height: 800 }); await go(page, BASE + "/projects");
await page.locator(".skip-link").waitFor({ state: "attached", timeout: 10000 }); await page.keyboard.press("Tab"); const first = await page.evaluate(() => document.activeElement?.className + "|" + document.activeElement?.textContent?.slice(0, 20)); R.ok(/skip-link/.test(first), "first Tab stop is the 'Skip to content' link", first);
const trigger = page.getByRole("button", { name: "New project" }).first(); await trigger.focus();
const ring = await trigger.evaluate((e) => { const cs = getComputedStyle(e); return cs.boxShadow + "|" + cs.outlineStyle; }); R.ok(/rgba\(245, 158, 11/.test(ring) || /solid/.test(ring.split("|")[1]), "focused button shows a visible focus ring", ring);
await trigger.press("Enter"); const dlg = page.getByRole("dialog"); await dlg.waitFor(); const inDlg = await page.evaluate(() => !!document.activeElement?.closest("dialog")); R.ok(inDlg, "opening a dialog moves keyboard focus inside it");
for (let i = 0; i < 12; i++) await page.keyboard.press("Tab"); R.ok(await page.evaluate(() => !!document.activeElement?.closest("dialog")), "Tab cycles inside the open dialog (focus trap)");
await page.keyboard.press("Escape"); await dlg.waitFor({ state: "hidden" }); R.ok(await trigger.evaluate((e) => e === document.activeElement), "Escape closes the dialog and returns focus to the trigger");
// keyboard-only: nav tabs on batch page (arrow keys)
await go(page, BASE + `/batches/${ids.b}`); await page.getByRole("tab", { name: "Overview" }).focus(); await page.keyboard.press("ArrowRight"); R.ok(await page.getByRole("tab", { name: "Tests" }).getAttribute("aria-selected") === "true", "ARIA tabs respond to arrow keys");
// status is never colour-only
const badges = await page.evaluate(() => [...document.querySelectorAll(".badge")].map((b) => b.textContent.trim().length > 0)); R.ok(badges.length > 0 && badges.every(Boolean), `all ${badges.length} status badges carry text (not colour alone)`);
// reduced motion
const rm = await browser.newContext({ viewport: { width: 1280, height: 800 }, reducedMotion: "reduce" }); const p2 = await rm.newPage(); await loginUI(p2, "admin@qa.test"); await go(p2, BASE + "/");
const anim = await p2.evaluate(() => { const d = document.createElement("div"); d.className = "skeleton"; document.body.appendChild(d); const a = getComputedStyle(d).animationName; d.remove(); return a; }); R.ok(anim === "none", "prefers-reduced-motion disables skeleton shimmer", anim);
// live regions for loading / errors
await go(page, BASE + "/assistant"); await page.locator("[role=log]").waitFor({ timeout: 10000 }); R.ok(await page.locator('[role=log]').count() === 1 && await page.locator('[aria-live]').count() >= 2, "assistant has log + live regions for announcements");
await browser.close(); process.exit(R.done() ? 1 : 0);
