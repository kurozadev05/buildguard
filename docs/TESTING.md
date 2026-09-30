# Testing

## Run the tests yourself (no running services needed)
```bash
npm test          # backend (pytest, 118 tests) + web (vitest, 88 tests)
npm run verify    # ruff, mypy, pytest, TypeScript, ESLint, vitest, production build
```
The automated tests use a throw-away SQLite database and never read `.env` / `.env.local`, so they cannot touch your real database, Redis or AI key (guarded by `tests/test_hardening.py::test_suite_is_isolated_from_the_developers_real_configuration`). To run the backend suite on PostgreSQL: create an empty database and set `TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/testdb` (the suite creates its own tables).

## Suites and what they prove
| Suite | Where | Needs | Count |
|---|---|---|---|
| Backend unit + API | `tests/` | nothing | 118 |
| Web unit + component (incl. axe on shared components, palette contrast, XSS-safe AI renderer, offline queue, SSE parser, BFF path/CSRF/refresh logic) | `frontend/tests/` | nothing | 88 |
| End-to-end journeys (register → project → batch → test → AI stream/stop/retry → sign-out → session expiry) | `frontend/tests-browser/flows.mjs` | running app + Chromium | 38 |
| PWA + offline (installability, pre-warm, offline shell, queued writes + sync, update prompt, sign-out wipe) | `tests-browser/pwa-offline.mjs` | production-like app (`npm start`) | 31 |
| Responsive (6 viewports × 20 screens) + accessibility (axe with colour contrast, keyboard, focus trap, reduced motion) | `tests-browser/responsive-a11y.mjs` | running app | 17 |
| AI not configured (`AI_PROVIDER=none`): plain notice, verified-note answers, app unaffected | `tests-browser/ai-not-configured.mjs` | app started with `AI_PROVIDER=none` | 6 |
| Dev mode (stale service-worker cleanup, hot reload, no CSP errors) | `tests-browser/dev-mode.mjs` | backend running | 10 |
| Failure injection (Redis / AI provider / backend / PostgreSQL down, slow network) | `tests-browser/resilience.mjs` | QA stack | 18 |
| Web vitals on an emulated phone | `tests-browser/perf.mjs` | QA stack | 7 |
| BFF (cookies, CSRF, refresh, streaming, abort, upload) | `tests-integration/bff.e2e.mjs` | QA stack | 34 |
| API contract (every request body the UI builds, incl. offline-queue types) | `tests-integration/contract.e2e.mjs` | QA stack | 101 |
| Security audit (cross-user access, mass assignment, injection, uploads, tokens, CORS) | `tests-integration/security.e2e.mjs` | QA stack | 44 |
| Rate limits | `tests-integration/ratelimit.e2e.mjs` | QA stack, low limits | 4 |
| API latency under load | `tests-integration/perf.mjs` | QA stack | (measurements) |

### Running the browser and integration suites
They drive a real Chromium (Playwright) and, for the ones marked *QA stack*, control the services themselves. Maintainer tooling, Linux only:
```bash
cd frontend && pnpm build && cd ..
scripts/qa_stack.sh up          # PostgreSQL 16 :5544 + Redis :6390 + simulated LLM :8899 + backend (ENV=production) :8801 + web :3100
cd frontend
node tests-browser/flows.mjs            # then any suite above; set BASE=http://localhost:3000 to point a browser suite at `npm start`
scripts/qa_stack.sh down                # (from the repo root)
```
Chromium comes from `CHROME_PATH` or the bundled `@sparticuz/chromium` dev dependency. The simulated LLM (`scripts/fake_llm.py`) speaks the Anthropic/OpenAI wire formats so streaming, tool calls and outages can be exercised without spending money.

## Results recorded for this release (Linux, Chromium 153, PostgreSQL 16, Redis 7)
* All suites above passed on the final code. Full first-time setup (`bootstrap`, `check`, `db:create`, `db:migrate`, `dev`, `start`, `db:reset/seed` safety rails) was performed from a freshly extracted archive on default ports.
* API latency through the web server: p95 ≈ 26 ms (one user) and ≈ 460 ms with 20 concurrent users, 0 errors. Web vitals on a 4× CPU-throttled, ~Fast-4G phone: LCP 0.7–2.2 s, CLS ≤ 0.01, 2–3 API calls per screen.
* Dependency audits (`pip-audit`, `pnpm audit --prod`) and a secrets scan of all source files: clean.

## What was NOT verified
* Windows and macOS; Firefox, Safari, Edge and any real phone or tablet; screen readers (only automated axe + keyboard checks).
* `docker-compose.yml` (syntax-checked, never started: no Docker in the test environment).
* Any real AI provider or a real local model (Ollama): a protocol-accurate simulator was used. The Anthropic/OpenAI/local adapters are tested against it, not against live services.
* PWA installation through a real browser's install UI (Chromium reported zero installability errors and the service worker behaved correctly, but the install prompt itself was not clicked).
* Load beyond ~100 batches / 300 test records; horizontal scaling (the design is single-instance).
* IS-code threshold values (`app/rules/is_concrete.json`) were not validated by a qualified structural/materials engineer.
