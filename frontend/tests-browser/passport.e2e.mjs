import { BASE, launch, newPage, login } from "./lib.mjs";
import os from "node:os"; const OUT = process.env.OUT_DIR ?? os.tmpdir();
let pass = 0, fail = 0; const ok = (c, m, x = "") => { c ? pass++ : fail++; console.log(`${c ? "PASS" : "FAIL"}  ${m}${c ? "" : "  <<< " + x}`); };
const b1 = await launch(); const a = await newPage(b1); await login(a);
const first = await a.evaluate(async () => (await (await fetch("/bff/batches?limit=1")).json()).data[0]); await b1.close();
const b2 = await launch(); const p = await newPage(b2, 390, 844, true);   // brand-new browser: no cookies at all
const r = await p.goto(`${BASE}/p/${first.public_token}`, { waitUntil: "networkidle0" }); const t = await p.evaluate(() => document.body.innerText);
ok(r.status() === 200 && t.includes(first.batch_code) && !p.url().includes("/login"), `anonymous visitor sees the passport for ${first.batch_code} without logging in`, `${r.status()} ${t.slice(0, 120)}`);
console.log("   visible text:", t.replace(/\n+/g, " | ").slice(0, 300)); console.log("   console issues:", p.issues.length, p.issues.slice(0, 2).join(" ; "));
const bad = await p.goto(`${BASE}/p/nonexistent-token-zzzz`, { waitUntil: "networkidle0" }); ok(bad.status() === 404, `unknown token -> ${bad.status()}`);
await p.goto(`${BASE}/p/${first.public_token}`, { waitUntil: "networkidle0" }); await p.screenshot({ path: `${OUT}/passport.png` });
await b2.close(); console.log(`\n${pass} passed, ${fail} failed`); process.exit(fail ? 1 : 0);
