#!/usr/bin/env node
// Cross-platform task runner (Windows / macOS / Linux). Only Node is required to start it.
//   node scripts/tasks.mjs <bootstrap|check|dev|start|db|test|verify> [args]     (the npm scripts in package.json call this)
import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const WEB = path.join(ROOT, "frontend");
const isWin = process.platform === "win32";
const venvPy = path.join(ROOT, ".venv", isWin ? "Scripts" : "bin", isWin ? "python.exe" : "python");
const sysPy = () => (isWin ? "python" : "python3");
const py = () => (existsSync(venvPy) ? venvPy : sysPy());
const C = { g: "\x1b[32m", r: "\x1b[31m", y: "\x1b[33m", c: "\x1b[36m", d: "\x1b[90m", b: "\x1b[1m", x: "\x1b[0m" };
const ok = (m) => console.log(`${C.g}✓${C.x} ${m}`), bad = (m) => console.log(`${C.r}✗${C.x} ${m}`), warn = (m) => console.log(`${C.y}!${C.x} ${m}`);

function loadEnv() {
  const f = path.join(ROOT, ".env.local"); const env = {};
  if (!existsSync(f)) return env;
  for (const line of readFileSync(f, "utf8").split(/\r?\n/)) {
    const m = /^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$/.exec(line); if (!m) continue;
    let v = m[2].trim(); if (/^["']/.test(v)) v = v.replace(/^(["'])(.*?)\1.*$/, "$2"); else v = v.replace(/\s+#.*$/, "");
    env[m[1]] = v;
  }
  return env;
}
function run(cmd, args, opts = {}) { return spawnSync(cmd, args, { stdio: "inherit", cwd: opts.cwd ?? ROOT, env: opts.cleanEnv ?? { ...process.env, ...(opts.env ?? {}) }, shell: opts.shell ?? false }); }
/** Environment for automated tests: the user's shell + .env.local values (database, Redis, AI keys) are removed so tests cannot touch real services. */
function scrubbedEnv() { const e = { ...process.env }; for (const k of Object.keys(loadEnv())) delete e[k]; for (const k of Object.keys(e)) if (/^(AI_|TEST_DATABASE_URL$)/.test(k)) delete e[k]; return e; }
function capture(cmd, args, opts = {}) { const r = spawnSync(cmd, args, { encoding: "utf8", cwd: ROOT, shell: opts.shell ?? false }); return { code: r.status, out: `${r.stdout ?? ""}${r.stderr ?? ""}`.trim() }; }
const tcp = (host, port, ms = 1200) => new Promise((res) => { const s = net.connect({ host, port }); const done = (v) => { s.destroy(); res(v); }; s.setTimeout(ms, () => done(false)); s.on("connect", () => done(true)); s.on("error", () => done(false)); });
function hostPort(u, def) { try { const x = new URL(u); return [x.hostname === "localhost" ? "127.0.0.1" : x.hostname, Number(x.port) || def]; } catch { return null; } }
const ver = (s) => (/(\d+)\.(\d+)/.exec(s) ?? []).slice(1).map(Number);

async function check({ needEnv = true } = {}) {
  const env = { ...loadEnv() }; let fatal = 0;
  const pv = capture(py(), ["--version"]); const [pM, pm] = ver(pv.out);
  if (pv.code === 0 && (pM > 3 || (pM === 3 && pm >= 11))) ok(`Python ${pM}.${pm}${existsSync(venvPy) ? " (project .venv)" : ""}`); else { bad(`Python 3.11+ not found (got: ${pv.out || "nothing"}). Install Python 3.12 from python.org.`); fatal++; }
  const [nM] = ver(process.version.slice(1)); if (nM >= 20) ok(`Node ${process.version}`); else { bad(`Node 20+ required (you have ${process.version}).`); fatal++; }
  const pn = capture("pnpm", ["--version"], { shell: isWin }); if (pn.code === 0) ok(`pnpm ${pn.out.split("\n")[0]}`); else { bad("pnpm not found. Install it:  npm install -g pnpm@12"); fatal++; }
  const deps = capture(py(), ["-c", "import fastapi, sqlalchemy, alembic, psycopg, redis, uvicorn"]); if (deps.code === 0) ok("Python packages installed"); else { bad("Python packages missing → run:  npm run bootstrap"); fatal++; }
  if (existsSync(path.join(WEB, "node_modules"))) ok("Frontend packages installed"); else { bad("Frontend packages missing → run:  npm run bootstrap"); fatal++; }
  if (needEnv) {
    if (!existsSync(path.join(ROOT, ".env.local"))) { bad(".env.local missing → run:  npm run bootstrap"); fatal++; }
    else if (Object.values(env).some((v) => v === "CHANGE_ME" || /:CHANGE_ME@/.test(v))) { bad(".env.local still contains CHANGE_ME placeholders → delete it and run  npm run bootstrap"); fatal++; }
    else ok(".env.local present, secrets generated");
    const db = env.DATABASE_URL ?? "";
    if (db && !db.startsWith("sqlite")) { const hp = hostPort(db.replace(/^postgresql(\+\w+)?:/, "http:"), 5432); if (hp && await tcp(...hp)) ok(`PostgreSQL reachable at ${hp[0]}:${hp[1]}`); else { bad(`PostgreSQL is NOT reachable${hp ? ` at ${hp[0]}:${hp[1]}` : ""} → start it:  npm run db:up   (Docker)  or start your local PostgreSQL service`); fatal++; } }
    else if (db) ok("Database: SQLite file (no server needed)");
    if (env.REDIS_URL) { const hp = hostPort(env.REDIS_URL.replace(/^rediss?:/, "http:"), 6379); if (hp && await tcp(...hp)) ok(`Redis reachable at ${hp[0]}:${hp[1]}`); else warn(`Redis is NOT reachable at ${hp?.join(":")}: the app still runs (in-memory limits); start it with  npm run db:up`); }
    else warn("REDIS_URL empty: using in-memory limits/caches (fine for one person)");
    const ai = (env.AI_PROVIDER ?? "none"); if (ai === "none") warn("AI_PROVIDER=none: the assistant answers from verified IS-code notes only (see README to enable a model)"); else if (ai !== "local" && !env.AI_API_KEY) warn(`AI_PROVIDER=${ai} but AI_API_KEY is empty: the assistant will report that AI is not configured`); else ok(`AI provider: ${ai}`);
    for (const [name, port] of [["API_PORT", env.API_PORT ?? 8000], ["WEB_PORT", env.WEB_PORT ?? 3000]]) if (await tcp("127.0.0.1", Number(port), 400)) warn(`Port ${port} (${name}) is already in use: something else is running there (a previous run?)`);
  }
  return fatal;
}

// ── process supervision with prefixed logs ──
const children = [];
function start(tag, color, cmd, args, opts = {}) {
  const p = spawn(cmd, args, { cwd: opts.cwd ?? ROOT, env: { ...process.env, ...opts.env }, shell: opts.shell ?? false, detached: !isWin, stdio: ["ignore", "pipe", "pipe"] });
  const pipe = (s, out) => { let buf = ""; s.on("data", (d) => { buf += d; let i; while ((i = buf.indexOf("\n")) >= 0) { out.write(`${color}[${tag}]${C.x} ${buf.slice(0, i)}\n`); buf = buf.slice(i + 1); } }); };
  pipe(p.stdout, process.stdout); pipe(p.stderr, process.stderr);
  p.on("exit", (code, sig) => { if (!stopping) { console.log(`${C.r}[${tag}] exited (${sig ?? code}). Stopping everything.${C.x}`); stop(code || 1); } });
  children.push(p); return p;
}
let stopping = false;
function stop(code = 0) {
  if (stopping) return; stopping = true;
  for (const p of children) { try { if (isWin) spawnSync("taskkill", ["/pid", String(p.pid), "/T", "/F"]); else process.kill(-p.pid, "SIGTERM"); } catch { /* already gone */ } }
  setTimeout(() => process.exit(code), 400);
}
process.on("SIGINT", () => { console.log("\nStopping…"); stop(0); }); process.on("SIGTERM", () => stop(0));

function appEnv() {
  const e = loadEnv(); const api = e.API_PORT ?? "8000", web = e.WEB_PORT ?? "3000";
  return { api, web, env: { ...e, PORT: web, BACKEND_URL: e.BACKEND_URL ?? `http://127.0.0.1:${api}`, COOKIE_SECURE: e.COOKIE_SECURE ?? "true", PYTHONUNBUFFERED: "1" } };
}
const apiArgs = (port, reload) => ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port), "--log-level", "warning", "--no-access-log", ...(reload ? ["--reload", "--reload-dir", "app"] : [])];

async function main() {
  const [task, ...rest] = process.argv.slice(2);
  switch (task) {
    case "bootstrap": {
      console.log(`${C.b}Setting up BUILDGUARD (installs packages, creates .env.local)…${C.x}\n`);
      if (!existsSync(venvPy)) { const r = run(sysPy(), ["-m", "venv", ".venv"]); if (r.status !== 0) { bad("Could not create the Python virtual environment. Install Python 3.12 (python.org) and re-run."); process.exit(1); } }
      let r = run(venvPy, ["-m", "pip", "install", "--quiet", "--upgrade", "pip"]); r = run(venvPy, ["-m", "pip", "install", "--quiet", "-r", "requirements.lock", "-r", "requirements-dev.txt"]); if (r.status !== 0) { bad("pip install failed: see the message above."); process.exit(1); }
      ok("Python packages installed into .venv");
      r = run("pnpm", ["install", "--frozen-lockfile"], { cwd: WEB, shell: isWin }); if (r.status !== 0) { bad("pnpm install failed. Is pnpm installed?  npm install -g pnpm@12"); process.exit(1); }
      ok("Frontend packages installed");
      r = run(venvPy, ["scripts/init_env.py", ...rest]); if (r.status !== 0) process.exit(1);
      console.log(`\n${C.b}Next:${C.x}  1) npm run db:up        (starts PostgreSQL + Redis in Docker; skip if you installed them yourself)\n       2) npm run db:create    (only needed for a native PostgreSQL install)\n       3) npm run dev          (migrates the database, starts backend + frontend)\n       4) open http://localhost:${loadEnv().WEB_PORT ?? 3000}`);
      break;
    }
    case "check": { const f = await check(); console.log(f ? `\n${C.r}${f} problem(s) to fix.${C.x}` : `\n${C.g}All required checks passed.${C.x}`); process.exit(f ? 1 : 0); break; }
    case "dev": case "start": {
      const fatal = await check(); if (fatal) { console.log(`\n${C.r}Fix the ✗ items above, then run this again.${C.x}`); process.exit(1); }
      const { api, web, env } = appEnv();
      let r = run(py(), ["scripts/db.py", "migrate", "--quiet"], { env }); if (r.status !== 0) process.exit(r.status ?? 1);
      if (task === "start" && (rest.includes("--build") || !existsSync(path.join(WEB, ".next", "standalone", "server.js")))) { console.log(`${C.c}Building the frontend (production build)…${C.x}`); r = run("pnpm", ["build"], { cwd: WEB, shell: isWin, env }); if (r.status !== 0) process.exit(1); }
      console.log(`\n${C.b}Starting ${task === "dev" ? "development" : "production-like local"} servers.${C.x}  Open ${C.b}http://localhost:${web}${C.x}   (API on http://127.0.0.1:${api}${env.ENV === "production" ? "" : `/docs`} · Ctrl+C stops both)\n`);
      start("api", C.c, py(), apiArgs(api, task === "dev"), { env });
      if (task === "dev") start("web", C.g, "pnpm", ["dev"], { cwd: WEB, shell: isWin, env });
      else start("web", C.g, process.execPath, [path.join(".next", "standalone", "server.js")], { cwd: WEB, env: { ...env, NODE_ENV: "production", HOSTNAME: "127.0.0.1" } });
      break;
    }
    case "db": { const r = run(py(), ["scripts/db.py", ...rest], { env: appEnv().env }); process.exit(r.status ?? 1); break; }
    case "test": {
      let r = run(py(), ["-m", "pytest", "-q", "-p", "no:cacheprovider"], { cleanEnv: scrubbedEnv() }); if (r.status !== 0) process.exit(r.status ?? 1);
      r = run("pnpm", ["test"], { cwd: WEB, shell: isWin }); process.exit(r.status ?? 1); break;
    }
    case "verify": {   // everything a change must pass
      const steps = [[py(), ["-m", "ruff", "check", "app", "tests", "scripts"]], [py(), ["-m", "mypy", "app"]], [py(), ["-m", "pytest", "-q", "-p", "no:cacheprovider"]]];
      for (const [c, a] of steps) { console.log(`${C.d}$ ${c} ${a.join(" ")}${C.x}`); if (run(c, a, { cleanEnv: scrubbedEnv() }).status !== 0) process.exit(1); }
      for (const a of [["typecheck"], ["lint"], ["test"], ["build"]]) { console.log(`${C.d}$ pnpm ${a.join(" ")}${C.x}`); if (run("pnpm", a, { cwd: WEB, shell: isWin }).status !== 0) process.exit(1); }
      ok("Everything passed."); break;
    }
    default: console.log("Usage: node scripts/tasks.mjs <bootstrap|check|dev|start|db <status|create|migrate|seed|reset>|test|verify>"); process.exit(task ? 1 : 0);
  }
}
main();
