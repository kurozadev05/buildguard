import os from "node:os"; const OUT = process.env.OUT_DIR ?? os.tmpdir();
import { BASE, launch, newPage, login, axeRun, sleep } from "./lib.mjs";
const pages = ["/", "/batches", "/batches/new", "/tests", "/tests/new", "/investigations", "/alerts", "/insights", "/risk", "/projects", "/evidence", "/assistant", "/advisor", "/audit", "/admin", "/account", "/sync"];
for (const [name, w, h, mob] of [["desktop", 1280, 800, false], ["phone", 390, 844, true], ["tablet", 820, 1180, true]]) {
  const b = await launch(); const p = await newPage(b, w, h, mob); await login(p); console.log(`\n== ${name} ${w}x${h}: signed in via the real form`);
  for (const path of pages) {
    p.issues.length = 0; await p.goto(BASE + path, { waitUntil: "networkidle0" }); await sleep(400);
    const m = await p.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: innerWidth, h1: document.querySelector("h1")?.textContent, err: !!document.querySelector("[role=alert] h2") }));
    const ax = await axeRun(p); const bad = ax.filter((v) => ["serious", "critical"].includes(v.impact));
    console.log(`${m.sw <= m.iw ? "ok  " : "OVERFLOW"} ${path.padEnd(16)} h1="${m.h1}" ${m.err ? "ERRORSTATE " : ""}issues=${p.issues.length} axe=${ax.length}(serious+${bad.length})`);
    for (const i of p.issues.slice(0, 3)) console.log("      ", i); for (const v of ax.slice(0, 4)) console.log(`       axe ${v.impact} ${v.id} x${v.n}: ${v.target} :: ${v.sample}`);
    if (path === "/" || path === "/batches" || path === "/assistant") await p.screenshot({ path: `${OUT}/${name}${path === "/" ? "-dash" : path === "/batches" ? "-batches" : "-assistant"}.png` });
  }
  await b.close();
}
