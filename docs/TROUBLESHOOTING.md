# Troubleshooting

First run **`npm run check`**. It tests most of the list below. Then **`http://localhost:3000/healthz`** tells you whether the web server or the backend is the problem, and `npm run db:status` shows database, migrations and Redis.

| Symptom | Likely cause | Fix |
|---|---|---|
| `npm run check` says PostgreSQL NOT reachable | database not running | `npm run db:up` (Docker running?) or start your PostgreSQL service |
| `password authentication failed` | `.env.local` password ≠ database's | with Docker: `docker compose down -v` then `npm run db:up` (⚠ erases the Docker database), or fix `DATABASE_URL` |
| `database "buildguard" does not exist` | native PostgreSQL | `npm run db:create` |
| Backend crashes with `bytes-like object` / odd text errors on PostgreSQL | database created with SQL_ASCII encoding | recreate it as UTF-8 (`createdb -E UTF8 -T template0 buildguard`) |
| Page says "Something went wrong / service unavailable" | backend not running or wrong `BACKEND_URL` | see the `[api]` lines in the terminal; `/healthz` |
| Sign-in "succeeds" but you land back on the login page, message about cookies | Safari (or a browser) refusing `Secure` cookies on http | use Chrome/Edge/Firefox, or `COOKIE_SECURE=false` in `.env.local` and restart |
| "Redis unavailable" in the terminal or `/ready` | Redis not running | `npm run db:up`. The app keeps working with in-memory limits |
| AI says "not configured" | `AI_PROVIDER=none` or key missing | see README → *Turn on the AI assistant* |
| AI answers "from verified notes only" although a key is set | provider unreachable / no internet / wrong model name | read the backend log line `ai … provider error`; check the key and `AI_MODEL`; the app never fakes a model answer |
| After fixing the AI provider/internet the assistant still says "verified notes only" | circuit breaker: opens after 3 failures, retries after 30 s | wait ~30 s and ask again: no restart needed |
| Port 3000/8000 already in use | earlier run still alive | close it, or change `WEB_PORT` / `API_PORT` (also update `CORS_ORIGINS`, `PUBLIC_BASE_URL`) |
| Old UI keeps appearing after code changes | stale service worker | see docs/PWA.md → *Developing without PWA surprises* |
| `pnpm: command not found` | pnpm not installed | `npm install -g pnpm@12` |
| Windows: `python` not found | Python not on PATH | reinstall Python and tick *Add python.exe to PATH* |
| Migrations "PENDING" | new version pulled | `npm run db:migrate` (safe) |
| Want a clean slate | | `npm run db:reset -- --yes --with-uploads` (⚠ erases all data) |
| Login locked for 15 min | 5 wrong passwords | wait, or `npm run db:reset` for a dev database |
| Phone can't install the app | http over LAN is not a secure context | see docs/PWA.md |
