// Rate limiting through the BFF. Run against an API started with:  QA_LOGIN_RPM=5 QA_REG_RPM=4 QA_AI_RPM=3   (see docs/TESTING.md)
const B = "http://localhost:3100"; let pass = 0, fail = 0; const ok = (c, m, x = "") => { c ? pass++ : fail++; console.log(`${c ? "PASS" : "FAIL"}  ${m}${c ? "" : "  <<< " + String(x).slice(0, 250)}`); };
const post = (p, body, cookie = "") => fetch(B + p, { method: "POST", headers: { "content-type": "application/json", "x-bg-csrf": "1", cookie }, body: JSON.stringify(body) });
// login brute force
let codes = [], retry = null; for (let i = 0; i < 9; i++) { const r = await post("/auth/login", { email: "nobody@rl.test", password: "wrong-password-" + i }); codes.push(r.status); if (r.status === 429) retry = r.headers.get("retry-after"); }
ok(codes.slice(0, 4).every((c) => c === 401) && codes.includes(429), `login brute force is throttled: ${codes.join(",")}`); ok(!!retry && Number(retry) > 0, `429 carries Retry-After (${retry}s)`);
const uiMsg = await (await post("/auth/login", { email: "nobody@rl.test", password: "x" })).json(); ok(uiMsg.error?.code === "RATE_LIMITED" && !/redis|ip|bucket/i.test(uiMsg.error.message), "throttle message is user-safe", JSON.stringify(uiMsg.error));
// registration flood
codes = []; for (let i = 0; i < 8; i++) { const r = await post("/auth/register", { email: `flood${i}${Date.now()}@rl.test`, full_name: "Flood", password: "Passw0rd!Passw0rd", language: "en" }); codes.push(r.status); }
ok(codes.includes(429), `registration flood is throttled: ${codes.join(",")}`);
console.log(`\n[ratelimit] ${pass} passed, ${fail} failed`); process.exit(fail ? 1 : 0);
