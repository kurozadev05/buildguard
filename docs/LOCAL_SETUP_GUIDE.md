# Local setup guide (beginner friendly)

Everything runs on your own computer. Plan for about 15 minutes. Each step says **what** you do and **why**.
Commands go into a *terminal* (macOS: Terminal · Windows: PowerShell · Linux: your terminal app).

## 0. Install the prerequisites (once)
| Install | Get it from | Check it worked (type this) |
|---|---|---|
| Python 3.12 | python.org (Windows: tick **Add python.exe to PATH**) | `python --version` (Windows) / `python3 --version` |
| Node.js 20 or newer | nodejs.org (LTS) | `node --version` |
| pnpm 12 | after Node: `npm install -g pnpm@12` | `pnpm --version` |
| Docker Desktop | docker.com (easiest way to get the database) | `docker --version` |
| Chrome, Edge or Firefox | any | – |

*Why:* Python runs the backend, Node runs the web app and the helper commands, pnpm installs web packages, Docker runs PostgreSQL (the database) and Redis (a fast cache) without you installing them by hand.
No Docker? Install PostgreSQL 16 and Redis 7 yourself; see step 4b.

## 1. Extract the ZIP
Extract `buildguard-local.zip`. Open a terminal **inside** the extracted `buildguard` folder (it contains `README.md` and `package.json`).
Check: `ls` (macOS/Linux) or `dir` (Windows) lists `README.md`.

## 2. Install everything: `npm run bootstrap`
*What it does:* creates a private Python environment (`.venv`), installs the pinned Python and web packages, and writes **`.env.local`**, your configuration, with newly generated random secrets. It asks which email should be the administrator: type **your own email**.
*If it fails:* read the ✗ line; the usual cause is a missing prerequisite from step 0.

## 3. Start the database and Redis: `npm run db:up`
*What it does:* starts PostgreSQL and Redis in Docker, bound to your computer only. Data is kept between runs.
Check: `npm run check` shows ✓ for PostgreSQL and Redis.

## 4b. (Only if you skipped Docker)
Start your own PostgreSQL and Redis, then edit `.env.local` so `DATABASE_URL` and `REDIS_URL` match them, and run `npm run db:create`. The database must be UTF-8.

## 5. (Optional) AI
Skip this for now, the app works without it. When you want the assistant to use a model, edit `.env.local`:
* Online: `AI_PROVIDER=anthropic` (or `openai`/`gemini`), `AI_MODEL=<model name>`, `AI_API_KEY=<your key>`
* On your own computer with Ollama: `AI_PROVIDER=local`, `AI_BASE_URL=http://localhost:11434/v1`, `AI_MODEL=llama3.1`
Restart the app afterwards. **Never share `.env.local`.** It contains your key.

## 6. Start the app: `npm run dev`
*What it does:* checks everything, applies the database structure (safe, never deletes data), then starts the backend and the web app. You'll see lines tagged `[api]` and `[web]`. Wait for “Ready”. **Leave this terminal open.** Stop with **Ctrl + C**.

## 7. Open it
Go to **http://localhost:3000** in Chrome/Edge/Firefox.
Diagnose problems at **http://localhost:3000/healthz**: it says whether the web app, backend, database, Redis and AI are OK.

## 8. Create your account and test sign-in
Click *Create an account* and register with the **administrator email** from step 2 (you become admin). Then sign out and sign back in: your session survives a page refresh.
Want sample data instead? Stop the app, run `npm run db:seed`, start again and sign in as `admin@buildguard.demo` / `Demo@1234`.

## 9. Try the main features
1. **Projects** → *New project* → open it → add a building, floor and element.
2. **Batches** → *Register batch* (quantity, supplier…) → the batch page shows a QR passport and the IS 456 registration check.
3. On the batch: **Samples** → *Add sample*, then **Record test** → cube strengths → you get a verdict with reasons (Verified / Review required / Flagged) from the rules engine.
4. **Risk map**, **Alerts**, **Insights**, **Evidence** (upload a photo), **Test advisor**.

## 10. Try the AI assistant
Open **Assistant**, ask “How many cube samples do I need for 30 m³ of M25?”. Without a model configured it says so and answers from verified IS-code notes. With one, the answer streams in; press **Stop** to cancel and **Try again** to regenerate.
Test the failure case: turn off Wi-Fi and ask again. It says the model can't be reached and falls back to verified notes; turn Wi-Fi on and ask again (it can take up to ~30 s to switch back to the model).

## 11. Try the installable app and offline mode
Stop `npm run dev` (Ctrl + C) and run **`npm start`** (builds the fast production version, needs ~1 minute the first time). Open http://localhost:3000, sign in, click the install icon in the address bar (or Account → *Install app*).
Offline test: sign in, open *Batches*, then turn off Wi-Fi. Reload: the app still opens and shows your list marked *Saved data*. *Register batch* now: it is stored as **Pending**, and appears in the list after you turn Wi-Fi on. Nothing is shown as saved until the server confirms.
Developing later? `npm run dev` removes any old app cache automatically, so you always see your edits.

## 12. Run the automated tests: `npm test`
Runs 118 backend and 88 web tests in about a minute; they use a temporary database and never touch your data. `npm run verify` also runs linting, type checks and a production build.

## 13. Day-to-day
| I want to… | Command |
|---|---|
| start working | `npm run db:up` (if Docker was stopped) then `npm run dev` |
| stop | Ctrl + C, then optionally `npm run db:down` |
| see what's connected | `npm run check` or `npm run db:status` |
| update the database structure after pulling new code | `npm run db:migrate` |
| erase everything and start clean | `npm run db:reset -- --yes` (asks you to type the database name) |

## When something goes wrong
1. `npm run check` (it names the exact missing piece), 2. http://localhost:3000/healthz, 3. the `[api]` / `[web]` lines in the terminal, 4. `docs/TROUBLESHOOTING.md`.
