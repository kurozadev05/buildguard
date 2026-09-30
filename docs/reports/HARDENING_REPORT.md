# Backend hardening report

Scope: backend only. Stack kept (FastAPI + SQLAlchemy 2 + SQLite/Postgres). No rewrite; one Redis/queue/microservice was deliberately **not** added (see "Not done, on purpose").

## Audit findings that were real (and fixed)
| # | Finding | Fix |
|---|---|---|
| 1 | `async def` upload/OCR endpoints did blocking file, DB and LLM work **on the event loop** (one slow LLM call could stall every request) | Now plain `def` endpoints (run in the threadpool); image structure verified with Pillow |
| 2 | Public QR link used the **sequential batch code** (`CON-M25-001`), so anyone could enumerate every batch, supplier and result | Unguessable per-batch `public_token`; code-based public lookup removed |
| 3 | First registered user auto-became admin (takeover on a fresh deploy) | `ADMIN_EMAIL` bootstrap; production refuses first-user-admin without it |
| 4 | Any write role could pass `as_of` and **forge risk-assessment history** | `as_of` is admin-only; future-dated readings rejected |
| 5 | Sync idempotency ledger keyed by `op_id` only: another user replaying an id could read/collide with someone else's stored result | Ledger keyed by `(user_id, op_id)` |
| 6 | `list_documents` / `ocr/drafts` filtered tenants in Python, per row | Tenant filter in SQL |
| 7 | JWT: no issuer/audience/jti, no logout, 12 h life, role changes not effective until expiry | iss/aud/jti required, 2 h life, denylist logout, `token_version` (password change, role change, deactivation kill old tokens) |
| 8 | Login: no brute-force control, user-enumeration by timing/message | Per-account lockout (5 fails / 15 min), per-IP login bucket (10/min), dummy hash for unknown users, identical 401 |
| 9 | PBKDF2 200k iterations | 600k, transparent rehash on next login |
| 10 | Rate limiter: one global limit, spoofable `X-Forwarded-For`, unbounded key growth | Per-endpoint buckets (login/register/upload/AI/public/default), configurable, rightmost-XFF only when `TRUST_PROXY`, pruned + capped memory |
| 11 | CORS `*` default; 500s could leak internals | Explicit origins (production refuses `*`), generic 500 with request id, pydantic `input` stripped from 422 (no echoed passwords) |
| 12 | Unvalidated sizes: unbounded text, `values` blobs, future timestamps, naive/aware datetime mix | Length caps, 8 KB `values` cap, UUID-format client ids, all datetimes normalised to UTC |
| 13 | (superseded by the Step 2 AI layer) LLM: single call, no retries/limits, unvalidated OCR output | Provider adapter, timeout, 2 bounded retries with backoff+jitter (retryable statuses only), circuit breaker, prompt/output caps, whitelist validation of extracted fields, safe explanation cache (keyed by public rule inputs only) |
| 14 | No migrations, no readiness probe, no graceful pool close | Alembic (initial migration + drift test), `/health` + `/ready`, pool disposed on shutdown |

## Performance (measured, same synthetic dataset, real HTTP, single uvicorn worker, one laptop-class sandbox)
Data: 300 batches, 1,200 tests, 1,500 usages, 3,000 observations, 200 elements, 5,000 audit rows.

| Endpoint | before p50 / p95 (ms) | after p50 / p95 (ms) |
|---|---|---|
| investigations list | 85.7 / 108.0 | **11.9 / 12.5** (N+1 removed) |
| audit verify (5k rows) | 181.2 / 203.5 | **107.5 / 138.7** (streamed, columns only) |
| dashboard summary | 64.6 / 122.3 | **41.8 / 117.1** |
| batch passport / impact | 7.2 / 7.8, 6.5 / 7.3 | 5.9 / 7.1, 4.7 / 7.3 |
| batches list (100) | 12.3 / 21.3 | 14.3 / 22.3 (≈ unchanged) |
| project tree | 45.3 / 94.6 | 53.1 / 116.4 (slightly slower) |
| tests list | 43.8 / 50.5 | 50.6 / 72.3 (slightly slower) |
| 16 threads × 25 mixed requests | 28.7 req/s, p95 1,409 ms | **37.5 req/s, p95 867 ms**, 0 errors |

Honest notes: the three slightly slower rows are large JSON payloads; gzip (added for bandwidth) costs CPU on loopback where bandwidth is free, so they only pay off over a real network. Absolute throughput is still modest because this is SQLite + Python on one worker; the dashboard still loads its rows into Python. Batch benchmark test-status values for `PENDING` batches differ slightly between the two runs (a new DB constraint rejects `PENDING` on a test row), which does not affect the timings materially. Numbers are from one sandbox run and will vary.

Indexes added (each tied to a query above): tests `(project_id,is_current)`, observations `(element_id,kind,observed_at)`, batches `(project_id,created_at)`, alerts `(project_id,created_at)`, change feed `(project_id,seq)`, risk `(element_id,computed_at)`. Also CHECK constraints on batch/test status.
Pooling: `DB_POOL_SIZE/MAX_OVERFLOW/TIMEOUT/RECYCLE` configurable; PostgreSQL `statement_timeout` set.

## Verification actually run
* `pytest`: **41 passed** (12 rule tests, 2 end-to-end flow, 3 sync/seed/security, 24 hardening/security/resilience) - also re-run in a **fresh venv installed from `requirements.lock`** from the final ZIP.
* `ruff check` (rules E,F,W,B,S,UP): clean.
* `pip-audit -r requirements.lock`: no known vulnerabilities (advisory DB as of the run).
* Alembic: upgrade on an empty DB reproduces the models exactly (no drift), tested.
* Live server smoke (demo mode): health/ready, login, envelope, dashboard, audit chain intact, security headers present, CORS preflight from an unlisted origin gets no `Access-Control-Allow-Origin`, SIGTERM runs the shutdown path (`shutdown complete` logged; exit 143 = normal signal exit).
* Security tests cover: tenant isolation across 19 read and 9 write endpoints + all list endpoints (IDOR), forged/expired/wrong-audience/`alg:none` JWTs, lockout, logout/password-change revocation, upload disguises, oversize body, public-token gating, rate-limit buckets, LLM failure/retry/breaker/injection-output handling, production config refusal.

## Could NOT be verified (do not skip these)
* **Docker image not built**: no Docker in the sandbox. Dockerfile/healthcheck are untested.
* **PostgreSQL not tested at all**: pool settings, `statement_timeout`, the advisory-lock path for the audit chain and Alembic on Postgres are written but never executed.
* **`mypy` is not clean**: 40 errors remain (mostly SQLAlchemy `Optional` typing noise: 16 union-attr, 12 arg-type). Not fixed and not suppressed.
* No load test beyond 16 threads; no memory-leak or long-soak test; no external pen-test. CSRF n/a (bearer tokens, no cookies). SSRF/command injection: no code path fetches user URLs or shells out (reviewed, not fuzzed).
* Real LLM calls were never made (no key). The AI layer is tested over real sockets against a simulated provider server that speaks the Anthropic and OpenAI wire formats; real vendors may differ in details (model names, tool-call edge cases, vision limits).
* Request-body cap uses the declared `Content-Length`; a chunked upload without it is bounded only by the upload size check after reading.
* HSTS is only sent over HTTPS/`X-Forwarded-Proto: https`; not exercised here.

## Not done, on purpose
* **Redis**: optional (`REDIS_URL`); needed only for >1 worker/instance so per-user AI limits, budgets and caches are shared. Tested against a real redis-server, including outage fallback.
* **Job queue**: the only slow operation is optional OCR/AI, already time-boxed, bounded-retry, strictly rate limited and off the event loop. A queue would add a worker and a failure mode for no measured benefit yet.
* **Response caching**: business hot paths are per-tenant and change on every write, so they are not cached. AI answers are cached only when built purely from shared public knowledge; anything derived from tenant data is never cached or shared.
* **Register-time 409** still reveals that an email is registered (inherent to open self-registration). Turn off `ALLOW_SELF_REGISTER` for invite-only.
* Demo accounts (`SEED_DEMO`) have a public password by design: demo only, refused in production.

Verdict: hardened and measurably faster on the worst endpoints, tests and audits above pass, but **not declared production-ready** until Docker + Postgres paths are exercised and the items above are reviewed.


## Step 2 addendum: AI layer

Two defects found only by load-testing the new endpoints (fixed, with regression tests that fail without the fix, on SQLite and PostgreSQL):
1. **Pool deadlock**: the auth dependency kept a pooled DB connection for the whole (slow) provider call; 20 concurrent AI requests exhausted the pool (5+5) and hung. Fix: `user_release_db` detaches the user and ends the transaction before awaiting the provider.
2. **SQLite `database is locked`** under concurrent AI writes: fixed by serialising AI-layer writes in-process on SQLite (PostgreSQL needs no lock).

Measured (simulated provider with fixed 300 ms latency, 1 uvicorn worker, SQLite, local machine; see `scripts/bench_ai.py`):
| Measurement | Result |
|---|---|
| Chat overhead over provider latency | p50 ~22 ms, p95 ~100 ms |
| Streaming first-token relay overhead | p50 ~35 ms |
| `/ai/ask` cache hit vs miss | ~7 ms vs ~313 ms |
| 20 / 50 / 100 concurrent chats | 20/20 in 0.61 s, 50/50 in 1.18 s, 100/100 in 2.52 s (serial: 6 / 15 / 30 s) |
| 100 simultaneous open streams | 100/100 completed, 3.98 s wall, 0 errors |
| Redis INCR / GET round trip (local) | ~0.4 ms / ~0.2 ms |

These figures characterise our layer only. Real provider latency, network and model speed will dominate in production.
