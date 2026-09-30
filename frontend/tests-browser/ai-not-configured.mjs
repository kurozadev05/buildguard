// With AI_PROVIDER=none the app must stay fully usable and say plainly that AI is not configured. Start the app with AI_PROVIDER=none, then: BASE=http://localhost:3000 node tests-browser/ai-not-configured.mjs
import { BASE, go, launch, loginUI, reporter, watch } from "./harness.mjs";
const R = reporter("ai-not-configured"); const b = await launch(); const page = await (await b.newContext({ viewport: { width: 1280, height: 800 } })).newPage(); const w = watch(page);
await loginUI(page, "admin@qa.test"); await go(page, BASE + "/assistant");
await page.getByText(/AI is not configured/).waitFor({ timeout: 10000 }); R.ok(true, "assistant page says plainly that AI is not configured and how to fix it");
await page.getByLabel(/Ask about IS codes/).fill("How many cube samples do I need per cubic metre?"); await page.getByRole("button", { name: "Send" }).click(); await page.getByRole("button", { name: "Send" }).waitFor({ timeout: 30000 });
const a = await page.locator(".msg.ai").last().innerText(); R.ok(a.length > 30 && /sample/i.test(a), "still answers from verified IS-code notes", a.slice(0, 100)); R.ok(!/error|failed|went wrong/i.test(a.slice(0, 60)), "no error shown to the user");
const s = await page.evaluate(() => fetch("/bff/ai/status", { headers: { "x-bg-csrf": "1" } }).then((r) => r.json())); R.ok(s.data.enabled === false, "API reports the model as not enabled", JSON.stringify(s.data.enabled));
await go(page, BASE + "/batches"); await page.getByRole("link", { name: /CON-M25/ }).first().waitFor({ timeout: 10000 }); R.ok(true, "the rest of the app is unaffected");
R.ok(w.errors.length === 0, "no console errors", JSON.stringify(w.errors)); await b.close(); process.exit(R.done() ? 1 : 0);
