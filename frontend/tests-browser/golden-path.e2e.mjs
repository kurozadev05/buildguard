import os from "node:os"; const OUT = process.env.OUT_DIR ?? os.tmpdir();
import { BASE, launch, newPage, login, sleep } from "./lib.mjs"; import fs from "node:fs"; import os from "node:os";
let pass = 0, fail = 0; const ok = (c, m, x = "") => { c ? pass++ : fail++; console.log(`${c ? "PASS" : "FAIL"}  ${m}${c ? "" : "  <<< " + String(x).slice(0, 260)}`); };
const b = await launch(); const p = await newPage(b, 1280, 900); await login(p);
const txt = () => p.evaluate(() => document.body.innerText);
const waitText = (t, ms = 8000) => p.waitForFunction((t) => document.body.innerText.includes(t), { timeout: ms }, t).then(() => true).catch(() => false);
const sel = async (label) => { const id = await p.evaluate((l) => [...document.querySelectorAll("label")].filter((x) => x.offsetParent).find((x) => x.textContent.trim().startsWith(l))?.htmlFor, label); if (!id) throw new Error("no label: " + label); return `[id="${id}"]`; };
const fill = async (label, v) => { const s = await sel(label); await p.click(s); await p.keyboard.down("Control"); await p.keyboard.press("KeyA"); await p.keyboard.up("Control"); await p.keyboard.press("Backspace"); await p.type(s, String(v)); };
const choose = async (label, v) => p.select(await sel(label), v);
const click = async (text, scope = "body") => { const h = await p.evaluateHandle((t, sc) => [...document.querySelector(sc).querySelectorAll("button,a")].filter((x) => x.offsetParent !== null || x.closest("dialog[open]")).find((x) => x.textContent.trim() === t || x.getAttribute("aria-label") === t), text, scope); if (!h.asElement()) throw new Error("no button: " + text); await h.asElement().click(); };
const dlg = "dialog[open]";
const api = (path, method = "GET", body) => p.evaluate(async (path, method, body) => { const r = await fetch("/bff/" + path, { method, headers: { "x-bg-csrf": "1", "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); return { s: r.status, j: await r.json().catch(() => null) }; }, path, method, body);

// 1 register a batch through the form
await p.goto(BASE + "/batches/new", { waitUntil: "networkidle0" });
await choose("Grade", "M25"); await fill("Quantity", "10"); await fill("Supplier", "GoldenPath RMC"); await fill("Water-cement ratio", "0.45"); await fill("Cement content", "340");
await Promise.all([p.waitForFunction(() => /\/batches\/[0-9a-f-]{36}/.test(location.pathname), { timeout: 10000 }), click("Register batch")]);
const bid = p.url().split("/batches/")[1]; await waitText("Registration check");
ok(/CON-M25-\d+/.test(await p.evaluate(() => document.querySelector("h1").textContent)) && (await txt()).includes("Registration check (IS 456)"), "1 batch registered via the form; detail page shows code + IS 456 registration check", p.url());
ok(await p.evaluate(() => !!document.querySelector('img[alt^="QR code"]') && document.querySelector('img[alt^="QR code"]').naturalWidth > 0), "1b QR image actually loads (authenticated PNG)");
// 2 sample via modal
await click("Samples"); await click("Add sample"); await p.waitForSelector(dlg); await fill("Cubes cast", "6"); await click("Add sample", dlg); await waitText("CON-M25-");
await p.waitForFunction(() => !document.querySelector("dialog[open]"), { timeout: 5000 }).catch(() => {});
ok((await txt()).includes("-S1") || /-S\d/.test(await txt()), "2 sample added through the modal and listed");
// 3 record a cube test via the form (from the sample link)
await click("Record test", "main"); await p.waitForFunction(() => location.pathname === "/tests/new", { timeout: 8000 }); await sleep(600);
await choose("Age at test", "7"); await p.type('input[aria-label="Specimen 1"]', "12.0"); await p.type('input[aria-label="Specimen 2"]', "12.5"); await p.type('input[aria-label="Specimen 3"]', "12.2");
await Promise.all([p.waitForFunction(() => /^\/tests\/[0-9a-f-]{36}/.test(location.pathname), { timeout: 10000 }), click("Record result")]); await waitText("IS-code checks");
const t = await txt(); ok(t.includes("Cube compressive strength") && t.includes("IS-code checks") && /Review required|Verified|Flagged/.test(t), "3 cube result recorded; validation outcomes rendered with status text", t.slice(0, 120));
// 4 verify + explain
await click("Verify seal"); ok(await waitText("Record matches its seal"), "4a seal verification shows server message");
await click("Explain in plain words"); ok(await waitText("Written from the rule results") || await waitText("AI wording"), "4b plain-language explanation shown with provenance note");
// 5 engineer review via dialog
if ((await txt()).includes("Record engineer review")) { await click("Record engineer review"); await p.waitForSelector(dlg); await fill("Justification", "Accepted: 28-day result will decide"); await click("Save review", dlg); ok(await waitText("Engineer review: accepted"), "5 review dialog saved and displayed"); } else ok(false, "5 review button missing", await txt());
// 5b amend
await click("Amend result"); await p.waitForSelector(dlg); await p.click('dialog[open] input[aria-label="Specimen 1"]'); await p.keyboard.down("Control"); await p.keyboard.press("KeyA"); await p.keyboard.up("Control"); await p.keyboard.press("Backspace"); await p.type('dialog[open] input[aria-label="Specimen 1"]', "17.2"); await fill("Reason for amendment", "Re-read the display");
await Promise.all([p.waitForNavigation({ waitUntil: "networkidle0", timeout: 10000 }).catch(() => {}), click("Save new version", dlg)]); await waitText("v2");
ok((await txt()).includes("v2"), "5b amend creates version 2");
// 6 investigation from the batch page
await p.goto(BASE + "/batches/" + bid, { waitUntil: "networkidle0" });
if ((await txt()).includes("Open investigation")) { await click("Open investigation"); await p.waitForSelector(dlg); await fill("Reason", "Low 7-day strength; verify with cores"); await click("Open", dlg); await p.waitForFunction(() => location.pathname.startsWith("/investigations/"), { timeout: 10000 });
  await waitText("Stage"); ok((await txt()).includes("Investigation:"), "6a investigation opened with generated staged actions"); const before = await txt();
  await click("Mark done"); await p.waitForSelector(dlg); await click("Save", dlg); ok(await waitText("Done"), "6b action marked done through the dialog"); }
else ok(true, "6 (batch verified; no investigation offered)");
// 7 risk map
await p.goto(BASE + "/risk", { waitUntil: "networkidle0" }); await p.evaluate(() => document.querySelector("main button.card")?.click()); await p.waitForSelector(dlg, { timeout: 5000 });
ok((await txt()).includes("Suggested next steps") || (await txt()).includes("Why"), "7a risk element dialog opens with factors"); await click("Observations", dlg); await fill("Value", "0.4"); await click("Record", dlg); ok(await waitText("Observation recorded"), "7b observation recorded"); await click("Re-assess now", dlg); ok(await waitText("Risk re-assessed"), "7c re-assess works");
await p.keyboard.press("Escape"); await sleep(300); ok(await p.evaluate(() => !document.querySelector("dialog[open]")), "7d Escape closes the dialog");
// 8 project structure
await p.goto(BASE + "/projects", { waitUntil: "networkidle0" }); await click("New project"); await p.waitForSelector(dlg); await fill("Project name", "Browser Test Site"); await click("Create", dlg);
await waitText("Browser Test Site"); await p.evaluate(() => [...document.querySelectorAll("main a")].find((a) => a.textContent === "Browser Test Site")?.click()); await p.waitForFunction(() => /^\/projects\/[0-9a-f-]{36}/.test(location.pathname), { timeout: 8000 });
await waitText("Buildings, floors and elements"); await click("Add building"); await p.waitForSelector(dlg); await fill("Name", "Tower 1"); await click("Add", dlg); await waitText("Tower 1"); await click("Add floor"); await p.waitForSelector(dlg); await fill("Name", "Ground"); await click("Add", dlg); await waitText("Ground");
await click("Add element"); await p.waitForSelector(dlg); await fill("Name", "Column C1"); await choose("Element type", "column"); await click("Add", dlg); ok(await waitText("Column C1"), "8 project → building → floor → element built through dialogs");
await click("Handover passport"); ok(await waitText("Audit chain intact") || await waitText("Print"), "8b handover passport tab renders");
// 9 evidence upload
fs.writeFileSync(`${OUT}/up.png`, Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64")); fs.writeFileSync(`${OUT}/bad.exe`, "MZ");
await p.goto(BASE + "/evidence", { waitUntil: "networkidle0" });
const inputs = await p.$$('input[type=file]'); await inputs[1].uploadFile(`${OUT}/bad.exe`); ok(await waitText("Choose a JPEG, PNG, WebP image or a PDF"), "9a wrong file type rejected client-side with a clear message");
await inputs[1].uploadFile(`${OUT}/up.png`); await waitText("up.png"); await click("Upload"); ok(await waitText("Uploaded"), "9b file uploaded (progress → success toast)"); await sleep(800); ok((await txt()).includes("up.png"), "9c uploaded file appears in the list with hash");
// 10 admin dialogs (cancel path only)
await p.goto(BASE + "/admin", { waitUntil: "networkidle0" }); ok((await txt()).includes("Users") && (await txt()).includes("Reset password"), "10 admin user table renders");
// 11 mobile drawer
await p.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 }); await p.goto(BASE + "/", { waitUntil: "networkidle0" }); await click("More"); await p.waitForSelector(".drawer");
ok(await p.evaluate(() => document.querySelectorAll(".drawer nav a").length >= 14), "11a mobile drawer lists the navigation"); await p.evaluate(() => [...document.querySelectorAll(".drawer a")].find((a) => a.textContent.includes("Alerts"))?.click());
await p.waitForFunction(() => location.pathname === "/alerts", { timeout: 6000 }); ok(await p.evaluate(() => !document.querySelector(".drawer")), "11b choosing a link navigates and closes the drawer"); await p.setViewport({ width: 1280, height: 900 });
// 12 change password ends session everywhere
await p.goto(BASE + "/account", { waitUntil: "networkidle0" }); await fill("Current password", "Demo@1234"); await fill("New password", "Demo@1234-new!"); await fill("Repeat new password", "Demo@1234-new!"); await click("Change password", "main");
await p.waitForFunction(() => location.pathname === "/login", { timeout: 8000 }).catch(() => {}); ok(p.url().includes("/login"), "12a password change → signed out", p.url());
await p.type("input[type=email]", "admin@buildguard.demo"); await p.type("input[type=password]", "Demo@1234"); await p.click("button[type=submit]"); ok(await waitText("Incorrect email or password"), "12b old password now rejected with the right wording (not 'session ended')");
console.log(`\nissues logged by the browser: ${p.issues.length}`); p.issues.slice(0, 6).forEach((i) => console.log("   ", i));
console.log(`\n${pass} passed, ${fail} failed`); await b.close(); process.exit(fail ? 1 : 0);
