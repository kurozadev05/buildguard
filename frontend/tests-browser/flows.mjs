// End-to-end user journeys in a real browser against the production-mode stack (scripts/qa_stack.sh up).
import { BASE, go, launch, loginUI, registerUI, reporter, shot, watch } from "./harness.mjs";
const R = reporter("flows"); const email = `admin@qa.test`;
const browser = await launch(); const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } }); const page = await ctx.newPage();
const w = watch(page); const quiet = (label) => { R.ok(w.errors.length === 0 && w.failed.length === 0, `${label}: no console errors / failed requests`, JSON.stringify({ e: w.errors, f: w.failed })); w.clear(); };

// ── Authentication ──
await go(page, BASE + "/batches"); R.ok(page.url().includes("/login?next=%2Fbatches"), "unauthenticated deep link redirects to /login with next=", page.url());
await registerUI(page, email); await shot(page, "01-dashboard-empty");
await page.getByRole("heading", { name: "No projects yet" }).waitFor({ timeout: 8000 }); R.ok(true, "new account lands on dashboard with a meaningful empty state");
const storage = await page.evaluate(() => ({ cookie: document.cookie, ls: JSON.stringify(localStorage), ss: JSON.stringify(sessionStorage) }));
R.ok(!/bg_at|bg_rt/.test(storage.cookie), "auth cookies are invisible to JavaScript (HttpOnly)", storage.cookie);
R.ok(!/eyJ|token|bearer/i.test(storage.ls + storage.ss), "no token-like data in localStorage/sessionStorage", storage.ls + storage.ss);
const cookies = await ctx.cookies(); const at = cookies.find((c) => c.name === "bg_at");
R.ok(at?.httpOnly && at?.secure && at?.sameSite === "Strict", "bg_at cookie: HttpOnly + Secure + SameSite=Strict", JSON.stringify(at));
quiet("registration + dashboard");
const role = await page.evaluate(() => fetch("/bff/auth/me", { headers: { "x-bg-csrf": "1" } }).then((r) => r.json()));
R.ok(role.data.role === "admin", "ADMIN_EMAIL account received the admin role", JSON.stringify(role));

// ── Project structure ──
await page.getByRole("link", { name: "Projects" }).first().click(); await page.waitForURL("**/projects");
await page.getByRole("button", { name: "New project" }).first().click();
await page.getByLabel("Project name").fill("QA Tower"); await page.getByLabel("Location").fill("Jaipur");
await page.getByRole("dialog").getByRole("button", { name: "Create" }).click(); await page.getByRole("link", { name: "QA Tower" }).waitFor();
R.ok(true, "project created via UI"); await page.getByRole("link", { name: "QA Tower" }).click(); await page.waitForURL("**/projects/*");
await page.getByRole("button", { name: "Add building" }).click(); await page.getByRole("dialog").getByLabel("Name").fill("Block A"); await page.getByRole("dialog").getByRole("button", { name: "Add", exact: true }).click();
await page.getByText("Block A").first().waitFor(); await page.getByRole("button", { name: "Add floor" }).click(); await page.getByRole("dialog").getByLabel("Name").fill("Ground"); await page.getByRole("dialog").getByRole("button", { name: "Add", exact: true }).click();
await page.getByText("Ground").first().waitFor(); await page.getByRole("button", { name: "Add element" }).click(); await page.getByRole("dialog").getByLabel("Name").fill("Slab S1"); await page.getByRole("dialog").getByRole("button", { name: "Add", exact: true }).click();
await page.getByText("Slab S1").first().waitFor(); R.ok(true, "building → floor → element created via UI"); await shot(page, "02-project-structure"); quiet("project structure");

// ── Batch registration (incl. XSS payload in a text field) ──
await go(page, BASE + "/batches/new");
const xss = `<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>`;
await page.getByLabel("Quantity (m³)").fill("12"); await page.getByLabel("Supplier").fill(xss); await page.getByLabel("Cement content").fill("320"); await page.getByLabel("Water-cement ratio").fill("0.5");
await page.getByLabel("Notes").fill(xss);
await page.getByRole("button", { name: "Register batch" }).last().click(); await page.waitForURL(/\/batches\/[0-9a-f-]{36}$/, { timeout: 15000 });
const batchUrl = page.url(); const code = (await page.getByRole("heading", { level: 1 }).innerText()).trim();
R.ok(/^CON-M25-\d{3}$/.test(code), `batch registered via UI, code ${code}`, code);
R.ok(await page.getByText("Registration check (IS 456)").isVisible(), "registration check rendered"); await shot(page, "03-batch-detail");
R.ok((await page.evaluate(() => window.__xss)) === undefined, "XSS payload in supplier/notes did NOT execute on the detail page");
await page.reload(); await page.getByRole("heading", { level: 1, name: code }).waitFor({ timeout: 15000 }); R.ok(true, "refresh keeps the batch (server persistence + session)");
await go(page, BASE + "/batches"); await page.getByRole("link", { name: code }).waitFor();
R.ok((await page.evaluate(() => window.__xss)) === undefined, "XSS payload did NOT execute on the batch list");
R.ok((await page.locator("table.data tbody").innerText()).includes("<script>"), "payload is shown as literal text (escaped), not markup"); quiet("batch registration + list");

// ── Debounced search: typing "UltraMix" must not fire one request per keystroke ──
const reqs = []; const onReq = (r) => { if (r.url().includes("/bff/batches?") && r.url().includes("supplier=")) reqs.push(r.url()); }; page.on("request", onReq);
await page.getByLabel("Search supplier or batch code").pressSequentially("Ultra", { delay: 60 }); await page.waitForTimeout(1200); page.off("request", onReq);
R.ok(reqs.length <= 1, `typing 5 characters fired ${reqs.length} search request(s) (debounced)`, reqs.join(" | "));
R.ok(page.url().includes("q=Ultra"), "search text is kept in the URL (shareable, survives refresh)"); await page.getByRole("button", { name: "Clear" }).first().click();

// ── Sample + test recording → validation ──
await go(page, batchUrl); await page.getByRole("tab", { name: "Samples" }).click(); await page.getByRole("button", { name: "Add sample" }).first().click();
await page.getByRole("dialog").getByRole("button", { name: "Add sample" }).click(); await page.getByText(/-S1$/).first().waitFor({ timeout: 10000 }); R.ok(true, "sample added via UI");
await page.getByRole("link", { name: "Record test" }).first().click(); await page.waitForURL(/tests\/new/);
await page.getByLabel("Sample").selectOption({ index: 1 }); await page.getByLabel("Age at test").selectOption("7");
await page.getByLabel("Specimen 1").fill("17"); await page.getByLabel("Specimen 2").fill("16.5"); await page.getByLabel("Specimen 3").fill("17.4");
await page.getByRole("button", { name: "Record result" }).click(); await page.waitForURL(/\/tests\/[0-9a-f-]{36}$/, { timeout: 15000 });
await page.getByText("IS-code checks").waitFor({ timeout: 8000 }); R.ok(true, "test recorded; IS-code validation outcomes displayed"); await shot(page, "04-test-detail");
await page.getByRole("button", { name: "Verify seal" }).click(); await page.getByText(/matches its seal/i).waitFor({ timeout: 8000 }); R.ok(true, "record-hash seal verification works from the UI");
await page.getByRole("button", { name: "Explain in plain words" }).click(); await page.getByText(/Written from the rule results|AI wording/).waitFor({ timeout: 15000 }); R.ok(true, "plain-language explanation shown with provenance note");
// invalid form: client-side validation, no request sent
await go(page, BASE + "/tests/new?batch=" + batchUrl.split("/").pop()); await page.getByRole("button", { name: "Record result" }).click();
R.ok(await page.getByText("Enter all three specimen strengths").isVisible(), "empty cube form shows a field-level error (not a server round-trip)"); quiet("sample + test");

// ── AI assistant: streaming, stop, retry, conversation continuity ──
await go(page, BASE + "/assistant"); const box = page.getByLabel(/Ask about IS codes/); await box.fill("How many cube samples do I need per cubic metre?");
const lens = []; const poll = setInterval(async () => { try { lens.push(await page.locator(".msg.ai .bubble").last().innerText().then((t) => t.length)); } catch {} }, 120);
await page.getByRole("button", { name: "Send" }).click(); await page.getByRole("button", { name: "Stop" }).waitFor({ timeout: 5000 });
R.ok(true, "Stop button appears while generating (UI not frozen)");
await page.getByRole("button", { name: "Send" }).waitFor({ timeout: 20000 }); clearInterval(poll);
const distinct = [...new Set(lens)].filter((n) => n > 0); R.ok(distinct.length >= 5, `answer rendered incrementally (${distinct.length} distinct lengths: ${distinct.slice(0, 6).join(",")}…)`);
await shot(page, "05-assistant-answer");
const ans = await page.locator(".msg.ai .bubble").last().innerText(); R.ok(/sample/i.test(ans), "assistant answer content present", ans.slice(0, 80));
R.ok(await page.locator(".msg.ai .chip").count() > 0 || (await page.getByText(/source/).count()) > 0, "source citation chip / sources list shown");
// cancel mid-stream
await box.fill("Explain curing requirements in detail please"); await page.getByRole("button", { name: "Send" }).click(); await page.getByRole("button", { name: "Stop" }).waitFor();
await page.waitForFunction(() => document.querySelectorAll(".msg.ai .bubble").length >= 2 && (() => { const x = document.querySelectorAll(".msg.ai .bubble")[1].innerText; return x.length > 20 && !x.includes("Thinking"); })()); await page.getByRole("button", { name: "Stop" }).click();
await page.getByText(/This is a partial answer/).waitFor({ timeout: 5000 }); R.ok(true, "Stop cancels generation and marks the answer as partial"); await page.getByRole("button", { name: "Send" }).waitFor();
// retry
await page.getByRole("button", { name: "Try again" }).last().click(); await page.getByRole("button", { name: "Stop" }).waitFor(); await page.getByRole("button", { name: "Send" }).waitFor({ timeout: 20000 }); R.ok(true, "Retry regenerates the answer");
// continue the conversation, then restore it after reload from history
await box.fill("And what about slump?"); await page.getByRole("button", { name: "Send" }).click(); await page.getByRole("button", { name: "Send" }).waitFor({ timeout: 20000 });
const n1 = await page.locator(".msg").count(); await page.reload(); await page.getByRole("button", { name: /Delete conversation/ }).first().waitFor({ timeout: 10000 });
await page.locator(".chat-side button[aria-current]").first().click(); await page.waitForFunction((n) => document.querySelectorAll(".msg").length >= n - 2, n1, { timeout: 8000 });
R.ok(true, `conversation restored from history after reload (${await page.locator(".msg").count()} messages)`); quiet("assistant");

// ── Logout & session end ──
await page.getByRole("button", { name: "Sign out" }).click(); await page.waitForURL("**/login"); R.ok(true, "sign-out returns to /login");
await go(page, BASE + "/batches"); R.ok(page.url().includes("/login"), "after sign-out protected pages redirect to login"); await page.goBack().catch(() => {});
const after = await ctx.cookies(); R.ok(!after.some((c) => c.name === "bg_at" || c.name === "bg_rt"), "session cookies cleared on sign-out");
await loginUI(page, email); R.ok(true, "sign-in with the registered account works");
// session expiry mid-use: cookies vanish → next API call sends the user to login with an explanation
await ctx.clearCookies(); await go(page, BASE + "/batches").catch(() => {}); await page.waitForURL(/\/login/, { timeout: 10000 }); R.ok(true, "expired/missing session → redirected to login");
await browser.close(); process.exit(R.done() ? 1 : 0);
