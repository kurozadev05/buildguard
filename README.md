# BUILDGUARD

Construction **material testing, traceability and durability** platform for concrete: QR material passports, IS-code (IS 456 / IS 516 / IS 1199) validation with a verdict per result (Verified / Review required / Flagged), batch-to-location traceability, investigations, durability-risk scoring, early-warning forecasts, a hash-chained audit log, an offline-capable installable web app (PWA), Hindi labels, and an AI assistant that is grounded in verified notes.

**It runs 100 % on your own computer.** No domain, server or cloud account is needed. The only optional outside service is an AI provider (or a local model).

```
                        YOUR COMPUTER
   Browser ──▶ Web server (Next.js, :3000) ──▶ Backend API (FastAPI, :8000) ──▶ PostgreSQL :5432
   (PWA)        │  httpOnly cookies only        │  rules engine, audit, RBAC   └▶ Redis :6379 (limits/cache)
                │  browser never sees tokens,   └▶ AI layer ──▶ AI provider (online API  or  local model)
                │  the backend URL or AI keys
```

## What you need installed

| Software | Version | Why |
|---|---|---|
| **Python** | 3.11+ (3.12 recommended) | backend |
| **Node.js** | 20+ (includes `npm`) | web app and the task runner |
| **pnpm** | 12 (10+ works) | `npm install -g pnpm@12` |
| **Docker Desktop** *(easiest)* | any recent | runs PostgreSQL 16 + Redis 7 for you |
| — or — PostgreSQL 16 + Redis 7 | | if you prefer to install them yourself |
| **Chrome / Edge / Firefox** | current | to use the app (Safari: see Troubleshooting) |

> **New to this?** Follow the click-by-click version in [`docs/LOCAL_SETUP_GUIDE.md`](docs/LOCAL_SETUP_GUIDE.md).

## Quick start (about 10 minutes)

```bash
npm run bootstrap     # creates .venv, installs Python + web packages, writes .env.local with fresh random secrets
npm run db:up         # starts PostgreSQL + Redis in Docker (skip if you installed them yourself)
npm run dev           # applies database migrations, starts backend + web app, prints http://localhost:3000
```

Open **http://localhost:3000**, click *Create an account*, and register with the email you set as `ADMIN_EMAIL` in `.env.local`: that account becomes the administrator. (Want sample data instead? `npm run db:seed`, then sign in as `admin@buildguard.demo` / `Demo@1234`.)

Stop everything with **Ctrl + C**. Your data stays in the Docker volume. `npm run db:down` stops PostgreSQL/Redis.

**No Docker?** Install PostgreSQL 16 and Redis 7, create the database with `npm run db:create`, put your own connection details in `.env.local` (`DATABASE_URL`, `REDIS_URL`), then continue with `npm run dev`. The database must use **UTF-8** encoding. Redis is optional: without it the app uses in-memory limits and says so.

Not sure something is set up right? `npm run check` lists every requirement with ✓ / ✗ and the exact fix.

## Commands

| Command | What it does |
|---|---|
| `npm run bootstrap` | one-time setup (safe to re-run; never overwrites `.env.local`) |
| `npm run check` | verifies Python, Node, pnpm, packages, `.env.local`, PostgreSQL, Redis, AI config, free ports |
| `npm run dev` | development: auto-reload for backend and web app (hot reload in the browser) |
| `npm start` | production-like local run: optimised build + no auto-reload (use it to test the PWA) |
| `npm run db:up` / `db:down` | start / stop PostgreSQL + Redis (Docker) |
| `npm run db:status` | which database, which migration, how much data, is Redis up |
| `npm run db:create` | create the PostgreSQL database (native installs) |
| `npm run db:migrate` | apply pending schema migrations: **never deletes data** |
| `npm run db:seed` | add demo accounts + demo project, **only into an empty database** |
| `npm run db:reset -- --yes` | ⚠ **erases everything** and rebuilds the schema. Refuses production and non-local hosts, and asks you to type the database name |
| `npm test` | backend + web unit tests (need no running services) |
| `npm run verify` | lint, types, all tests and a production build |

Normal startup **never** drops tables, deletes data or changes the schema on its own (`AUTO_MIGRATE=false`); `npm run dev` runs the safe migrate step explicitly.

## Configuration (`.env.local`)

`npm run bootstrap` creates it from `.env.example` (all variables are documented there). It is git-ignored: never share or commit it.
Most useful settings: `ADMIN_EMAIL`, `WEB_PORT` / `API_PORT`, `DATABASE_URL`, `REDIS_URL`, `COOKIE_SECURE`, `AI_*`.

## Turn on the AI assistant (optional)

Without AI the assistant still answers from verified IS-code notes and your project documents, and clearly says the model is not connected. To connect a model, edit `.env.local`, then restart `npm run dev`:

**Mode A: online provider** (your computer → internet → provider). Needs internet from this machine.
```
AI_PROVIDER=anthropic          # or openai / gemini
AI_MODEL=<model name from your provider's docs>
AI_API_KEY=<your key>
```
**Mode B: fully local model** (e.g. [Ollama](https://ollama.com): `ollama pull llama3.1`). No key, no internet, but slower on a laptop.
```
AI_PROVIDER=local
AI_BASE_URL=http://localhost:11434/v1
AI_MODEL=llama3.1
```
The key is read only by the backend. It is never sent to the browser or included in the web bundle. If the provider or the internet is unavailable, answers fall back to verified notes and the chat says so. It never pretends the model answered. Verdicts (Verified/Flagged) always come from the rules engine, not from AI. Details: `docs/AI_API.md`.

## Install as an app and use it offline

1. Run `npm start` (the service worker is only active in the production-like run, not in `npm run dev`).
2. Open http://localhost:3000 in Chrome/Edge, sign in, then use *Install app* (address-bar icon, or Account → *Install app*).
3. Core screens are prepared for offline use right after sign-in. Turn the network off: recently viewed lists still open (labelled *Saved data from…*), and new batches/samples/tests/observations are kept as **Pending** on your device and sent automatically when the connection returns. Nothing is shown as saved until the server confirms it.

Full details, and how to reset the cache while developing: `docs/PWA.md`.

## Testing

`npm test` runs the backend (118 tests) and web (88 tests) suites. `docs/TESTING.md` explains the browser and integration suites and what was verified.

## Project layout

```
app/            backend (FastAPI): routers, rules engine, AI layer (app/ai), models, services
migrations/     database schema history (Alembic)
frontend/       web app + BFF (Next.js): src/app, src/components, src/lib, src/server, public (PWA)
scripts/        tasks.mjs (task runner), db.py (database commands), init_env.py, fake_llm.py, qa_stack.sh
tests/          backend tests            frontend/tests · frontend/tests-browser · frontend/tests-integration
docs/           ARCHITECTURE · SECURITY · TESTING · PWA · TROUBLESHOOTING · AI_API · FRONTEND
docker-compose.yml   PostgreSQL + Redis for local use     .env.example   configuration template
```

## Security in one paragraph

Sign-in tokens live only in `HttpOnly`, `SameSite=Strict` cookies that JavaScript cannot read; the browser talks only to the web server, which forwards to the backend; every API call is authorised on the server (roles + project membership); AI output is treated as untrusted text (never HTML); uploads are content-checked on the server; CSP with per-request nonces; rate limits on sign-in, registration, uploads and AI. Local does not mean lax. See `docs/SECURITY.md` for the model and the audit results.

## Known limitations

See `docs/TESTING.md` → *What was NOT verified*. Highlights: verified on Linux + Chromium only (Windows, macOS, Firefox, Safari and real phones untested); Docker Compose file is syntax-checked but not run; no real AI provider was called (a protocol-accurate simulator was used); IS-code thresholds in `app/rules/is_concrete.json` need review by a qualified engineer before real-world reliance; Hindi coverage is partial; offline copies on the device are not encrypted.
