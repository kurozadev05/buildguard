# BUILDGUARD Web (Step 3)

Next.js 15 (App Router) · React 19 · TypeScript (strict) · TanStack Query · plain CSS design tokens · pnpm.
Code: `frontend/`. The backend and AI layer are unchanged.

## Architecture
```
Browser  ──same-origin──▶  Next.js server (BFF)  ──private network──▶  FastAPI  ──▶  AI provider
 no tokens, no backend URL     httpOnly cookies bg_at / bg_rt            Bearer JWT      (server-side key only)
```
* `src/app/auth/[action]` login / register / logout: the backend's tokens are put into **httpOnly, SameSite=Strict** cookies and **never returned in a response body**.
* `src/app/bff/[...path]` proxies `/bff/x` → `BACKEND/api/x`: attaches the Bearer token, refreshes it transparently (single-flight, because refresh tokens rotate and re-use revokes the family), retries once on 401, streams bodies (SSE, uploads, downloads, QR PNGs), propagates client aborts upstream, applies timeouts (30 s; 90 s AI/OCR; 5 min stream), forwards `X-Forwarded-For` and a request id, forces `Cache-Control: no-store`.
* Token-issuing endpoints (`auth/login|register|refresh|token|logout`) are **not reachable** through the proxy. A successful password change clears the browser session.
* CSRF: SameSite=Strict + required `x-bg-csrf: 1` header + Origin check on every non-GET.
* `src/middleware.ts`: per-request nonce CSP (`script-src 'self' 'nonce-…' 'strict-dynamic'`, `connect-src 'self'`, `frame-ancestors 'none'`), redirect to `/login?next=` when no session cookie (cosmetic: the backend decides).
* `src/lib/api.ts` central client: envelope unwrap, error normalisation (`ApiError.kind`), request ids, timeout, cancellation, GET-only retry with backoff, upload with progress/cancel (XHR).
* State: server state = TanStack Query (dedupe, cancel, cache); UI state = component state; forms = local state + backend validation; session/project/language = small contexts; offline = IndexedDB queue + snapshots.

## Pages
`/login /register /` (dashboard) `/batches /batches/new /batches/[id]` `/tests /tests/new /tests/[id]` `/investigations /investigations/[id]` `/alerts /insights /risk /projects /projects/[id] /evidence /assistant /advisor /audit /admin /account /sync /offline`. Every Step 1/2 capability is reachable (see the contract table).

## Offline & PWA
* **Queued (idempotent via `/sync/push`, client ids, op_id):** `batch.create`, `sample.create`, `test.create`, `usage.create`, `observation.create`. Shown as *Pending* until the server confirms; rejected items stay visible on `/sync` with the server's reason. Timeouts are **not** queued (outcome unknown).
* **Never queued:** logins, password changes, reviews, amendments, investigation actions, deletions, admin actions, uploads, AI calls.
* **Read snapshots** (IndexedDB, per user, allow-list `projects, batches, batch, tests, test, dashboard, tree, investigations, alerts, risk`), labelled "saved data from …". AI conversations, audit, users are never stored. Everything is wiped on sign-out / session expiry / "Clear offline data". Snapshots are **not encrypted** at rest.
* Service worker (`public/sw.js`, production only): cache-first for `/_next/static`, icons, manifest; network-first page shells with `/offline` fallback. **Never** caches `/bff/*`, `/auth/*`, `/p/*` or non-GET. Update prompt via `SKIP_WAITING`; page cache cleared on sign-out.

## Frontend API contract
Auth column: **S** = any signed-in user, **W** write roles (admin, site_engineer, qa), **T** test roles (W + lab), **R** review roles (admin, qa, site_engineer), **A** audit roles (admin, auditor, qa), **X** admin. The backend enforces; the UI only hides. Envelope: success `{success,data,meta}`, error `{success:false,error:{code,message,details[{field,message}],request_id}}`. All calls go via `/bff/<path>`. "GET retry" = 2 retries on network/timeout/502-504. Skeleton/spinner shown on every call; errors render `ErrorState` (page) or `FormError` (form) with the request id.

| Area | Endpoint | Method | Auth | Request | Response used | Offline |
|---|---|---|---|---|---|---|
| Session | `/auth/login` `/auth/register` (BFF route) | POST | – | `{email,password}` / `+full_name,language` | `{user}` (tokens → cookies) | needs network |
| | `/auth/logout` (BFF) | POST | S | – | – | needs network |
| | `auth/me` | GET | S | – | `User` | cached profile allows offline start |
| | `auth/change-password` | POST | S | `{current_password,new_password}` | ends session | needs network |
| | `auth/users`, `auth/users/{id}` PATCH, `…/reset-password` | GET/PATCH/POST | X | `{role?,is_active?}` | `User[]`, `{temporary_password}` | needs network |
| Projects | `projects` GET/POST; `projects/{id}` | GET/POST | S / W | `{name,location?,client_name?}` | `Project[]` | snapshot (GET) |
| | `projects/{id}/tree`; `…/buildings`; `buildings/{id}/floors`; `floors/{id}/elements` | GET/POST | S / W | `{name}` `{name,level}` `{name,element_type}` | `Tree` | tree snapshot |
| | `projects/{id}/members` | GET/POST | S / X | `{user_id}` | members | needs network |
| | `projects/{id}/handover-passport` | GET | S | – | passport (print view) | needs network |
| Batches | `batches` GET list (`project_id,status,grade,supplier,limit,offset,lang`) | GET | S | – | `Batch[]` | first page snapshot |
| | `batches` POST | POST | W | `BatchIn` (+client `id`) | `Batch` | **queued** `batch.create` |
| | `batches/{id}` `batches/by-code/{code}` | GET | S | – | `Batch` | snapshot |
| | `batches/{id}/samples` `…/impact` `…/locations` | GET | S | – | lists | needs network |
| | `batches/{id}/qr.png`, `samples/{id}/qr.png` | GET | S | – | PNG | needs network |
| | `samples` POST | POST | W | `{id?,batch_id,cast_date,cubes_cast,curing_method?,notes?}` | `Sample` | **queued** `sample.create` |
| | `usage` POST | POST | W | `{id?,batch_id,element_id,volume_m3?,notes?}` | usage | **queued** `usage.create` |
| Tests | `tests` GET (`batch_id,status,current_only,lang`) `tests/{id}` | GET | S | – | `TestRec` | snapshot |
| | `tests` POST | POST | T | `{id?,batch_id,sample_id?,test_type,age_days?,values,lab_name?,tested_at?,investigation_action_id?}` | `TestRec` + rule outcomes | **queued** `test.create` |
| | `tests/{id}/review` `…/amend` `…/verify` | POST/POST/GET | R / T / S | `{decision,comment}` `{values,age_days?,reason}` | – | needs network |
| Evidence | `documents` POST (multipart `file,kind,project_id,batch_id?,sample_id?,test_id?`) | POST | T | XHR w/ progress + cancel | `Doc` | needs network |
| | `documents` GET, `documents/{id}/download` `…/verify` `…/attach` | GET/GET/GET/POST | S / T | `{test_id}` | `Doc[]`, `{ok}` | needs network |
| | `ocr/extract` (multipart) `ocr/drafts` `ocr/drafts/{id}/confirm|reject` `ocr/parse-text` | POST/GET | T | `OcrConfirmIn` | draft → human confirm | needs network |
| Investigations | `investigations` GET/POST `investigations/{id}` `…/actions/{id}` PATCH `…/close` | – | S / R | `{batch_id,reason}` `{status,note?}` `{closure_note}` | `Investigation` | list snapshot |
| Risk | `durability/projects/{id}/risk-map`, `…/elements/{id}/history`, `…/assess` POST, `elements/{id}/observations|batches` | – | S / W | – | risk rows | risk-map snapshot |
| | `observations` POST | POST | W | `{id?,element_id,kind,value,notes?}` | obs | **queued** `observation.create` |
| Insight | `alerts` GET, `alerts/{id}/ack`; `dashboard/summary` `dashboard/suppliers`; `predict/*`; `integrity/*` | – | S / W / screen roles | – | see types.ts | alerts + dashboard snapshot |
| Advisor | `advisor/checklist` POST, `advisor/knowledge` GET, `rules` GET | – | S | `ChecklistIn` | checklist (+`explanation`) | needs network |
| Audit | `audit`, `audit/verify` | GET | A | filters | entries, `{ok}` | never stored |
| AI | `ai/status` `ai/conversations` (+`{id}` GET/DELETE) `ai/ask` `ai/search` `ai/documents` (GET/POST/DELETE) `ai/tests/{id}/explain` `ai/read-display` `ai/usage/me|summary` `ai/metrics` | – | S (some X) | see AI_API.md | – | never cached |
| | `ai/chat/stream` (SSE `meta,delta,tool,replace,done,error`) | POST | S | `{message,conversation_id?,project_id?}` | incremental text; Stop = abort → backend logs `CLIENT_DISCONNECT` | needs network |
| Sync | `sync/push` | POST | S | `{device_id,ops[{op_id,type,client_ts,data}]}` | per-op `applied|duplicate|rejected|conflict` | this *is* the offline path |

## Running
Use the repository README (`npm run dev` / `npm start`). Notes for the web server: run **one** instance (the refresh-token single-flight cache is in memory); `BACKEND_URL` is server-only; the AI stream is exempt from gzip via `Cache-Control: no-transform`.

## Verification commands
```
cd frontend && pnpm typecheck && pnpm lint && pnpm test && pnpm build
```
