# Architecture (local)

## Components
| Component | Tech | Port | Role |
|---|---|---|---|
| Web app + BFF | Next.js 15 (App Router), React 19, TypeScript | 3000 | UI, PWA, and a same-origin proxy that holds the session |
| Backend API | FastAPI, SQLAlchemy 2, Alembic | 8000 | business rules, RBAC, audit chain, AI orchestration |
| Database | PostgreSQL 16 (SQLite works for tests/quick trials) | 5432 | all business data |
| Redis | Redis 7 (optional) | 6379 | AI rate limits, token budgets, caches |
| AI provider | online API or local model | – | text generation, optional embeddings |

## Request flow
1. Browser → `http://localhost:3000/bff/<path>` (same origin, cookies attached automatically).
2. The web server (`src/server/proxy.ts`) checks CSRF (custom header + Origin), reads the `bg_at`/`bg_rt` HttpOnly cookies, refreshes the access token when needed (single-flight, because refresh tokens rotate), and forwards to `BACKEND_URL/api/<path>` with `Authorization: Bearer …`.
3. The backend authenticates, authorises (role + project membership), validates, runs business rules, writes PostgreSQL, appends to the audit chain, and answers with a uniform envelope `{success, data, meta}` / `{success:false, error:{code,message,details,request_id}}`.
4. Streaming AI (`/bff/ai/chat/stream`) is relayed as Server-Sent Events without buffering; closing the browser request cancels the provider call.
The browser never learns the backend URL, tokens or AI credentials.

## AI layer (`app/ai`)
Provider adapters (Anthropic, OpenAI-compatible incl. Gemini/Ollama/vLLM) behind one interface → resilient wrapper (timeouts, bounded retries, fallback provider) → RAG over verified IS notes + project documents (tenant-isolated) → tool calls executed *as the signed-in user* → output validation (ungrounded claims are replaced by verified text) → usage accounting. With `AI_PROVIDER=none` everything except free-text model answers still works.

## Offline model (browser)
IndexedDB holds (a) per-user read snapshots of recently viewed lists and (b) a queue of safe writes (`batch/sample/test/usage/observation.create`) sent through the idempotent `/api/sync/push`. The service worker caches only static assets and page shells (never `/bff`, `/auth`, `/p`).

## Data and state
Schema history: `migrations/` (Alembic 0001–0003). Files: `UPLOAD_DIR` (default `./uploads`, content-checked, SHA-256 sealed). Audit: hash-chained table, verifiable from the Audit screen.
