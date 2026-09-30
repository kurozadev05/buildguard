# BUILDGUARD AI API

Everything is under `/api`, needs `Authorization: Bearer <access_token>`, and (except the stream) returns the standard envelope
`{"success":true,"data":...}` or `{"success":false,"error":{"code","message","details","request_id"}}`.
The frontend never needs to know which model/provider is behind it. `Accept-Language: hi` (or `?lang=hi`) switches replies to Hindi.

## Architecture
```
Frontend -> routers/ai.py -> ai/service.py -> ai/resilient.py -> ai/anthropic.py | ai/openai_compat.py -> provider
                                 |-> ai/rag.py (chunks, embeddings, hybrid search, tenant filter)
                                 |-> ai/tools.py (allow-listed read-only tools, run as the calling user)
                                 |-> ai/kv.py (memory or Redis: limits, budgets, cache)
                                 |-> ai/store.py (conversations, usage)      ai/grounding.py (output validation)
```
Business code never touches a vendor SDK. `ai/openai_compat.py` also serves Gemini (its OpenAI-compatible endpoint), Azure/OpenRouter, Ollama, vLLM.
Prompts live only in `ai/prompts.py` (`PROMPT_VERSION`, stored with every usage row).

## Endpoints
| Method & path | Roles | Purpose |
|---|---|---|
| `GET /ai/status` | any | `enabled`, `mode` (`llm+retrieval`/`retrieval-only`), features, limits. Admin also sees provider/model/Redis. |
| `POST /ai/ask` | any | One-shot grounded Q&A. Body `{question, batch_id?, project_id?}` |
| `POST /ai/chat` | any | Conversation turn. Body `{message, conversation_id?, project_id?}` |
| `POST /ai/chat/stream` | any | Same, as Server-Sent Events |
| `GET /ai/conversations`, `GET/DELETE /ai/conversations/{id}` | owner only | History (404 for anyone else, admins included) |
| `POST /ai/search` | any | `{query, project_id?, top_k?}` semantic + keyword search, no LLM |
| `POST/GET /ai/documents`, `DELETE /ai/documents/{id}` | write roles (admin, site_engineer, qa) | Index project documents for retrieval. `{project_id,title,text}` |
| `GET /ai/tests/{id}/explain` | project members | Plain-language explanation of a stored result |
| `POST /ai/read-display` | test roles | multipart `file, project_id, cube_size_mm(100/150), batch_id?, sample_id?`. Draft strength from a machine-display photo |
| `POST /documents/{id}/attach` | test roles | `{test_id}` link an uploaded photo to a test |
| `POST /ocr/extract` | test roles | Report photo/PDF -> DRAFT fields (validated) |
| `POST /advisor/checklist` | any | `explain:true` adds an AI summary of the verified checklist |
| `GET /ai/usage/me` | any | Your requests/tokens/estimated cost (24 h, 30 d) and today's budget use |
| `GET /ai/usage/summary?days=7`, `GET /ai/metrics` | admin | Top users, status counts; latency p50/p95, time-to-first-token, error/timeout/cache rates |
| `GET /integrity/projects/{id}`, `GET /integrity/batches/{id}` | admin, qa, site_engineer, auditor | Result-integrity screening (indicative only) |
| `GET /predict/projects/{id}[?status=]`, `GET /predict/batches/{id}` | members | 7-day -> 28-day estimate with range (`ON_TRACK`/`WATCH`/`AT_RISK`) |

### `POST /ai/chat` response
```json
{"conversation_id":"...","message_id":"...","text":"Take 4 cube samples for 40 m3 [K1].","generated_by":"llm","validated":true,"degraded":false,
 "sources":[{"label":"K1","title":"Sampling frequency","reference":"IS 456:2000 Cl. 15.2","source":"knowledge"}],
 "tools":["get_batch"],"usage":{"input_tokens":812,"output_tokens":41}}
```
`generated_by`: `llm` or `retrieval` (verified source text shown instead: AI off, down, or its answer failed validation).
`validated:false` means the model's text was replaced. `degraded:true` means the AI service was unavailable. Source labels `K#` are shared IS notes, `D#` are the project's documents.

### `POST /ai/chat/stream` (SSE, `text/event-stream`)
Checks (auth, rate limit, budget, conversation ownership, project access) happen **before** the stream opens, so those failures are ordinary HTTP errors. Then:
| event | data |
|---|---|
| `meta` | `{conversation_id}` (first event) |
| `delta` | `{text}` append to the message |
| `tool` | `{name, ok}` a tool ran (show "checking batch..." if you like) |
| `replace` | `{text}` **replace everything shown so far** (answer failed validation, or AI failed and verified text is shown) |
| `done` | same fields as the non-stream response (final) |
| `error` | `{code, message}` mid-stream failure (`AI_STREAM_INTERRUPTED`, `AI_UNAVAILABLE`...). Partial text is kept in history. |

Closing the connection cancels the upstream request. The web server relays the stream unbuffered (`X-Accel-Buffering: no`, `Cache-Control: no-transform`). Browser `EventSource` cannot POST or send headers: use `fetch()` and read `response.body`.

## Error codes
`AI_RATE_LIMITED` 429 (`Retry-After` set) | `AI_BUDGET_EXCEEDED` 429 | `FORBIDDEN` 403 | `NOT_FOUND` 404 | `VALIDATION_ERROR` 422 |
`AI_UNAVAILABLE` 503 | `AI_BUSY` 503 | `AI_TIMEOUT` 504 | `AI_MISCONFIGURED` 503 (bad key/model; details only in server logs) |
`AI_INPUT_TOO_LARGE` 413 | `AI_BAD_RESPONSE` 502 | `AI_STREAM_INTERRUPTED` (stream only). When verified sources exist, an outage returns 200 with `degraded:true` instead of an error.

## Limits
Per user: `AI_MAX_REQUESTS_PER_MINUTE` (default 20, fixed one-minute window) and `AI_DAILY_TOKEN_BUDGET` (150 000). Per IP: `RATE_LIMIT_AI_PER_MIN` on all `/api/ai/*`.
Message 2000 chars, document 200 KB, history and sources trimmed to `AI_MAX_CONTEXT_TOKENS`, at most 3 tool rounds / 6 tool calls per turn, 200 messages and 200 conversations per user.

## Tools the model may request (read-only, run as the caller)
`get_batch`, `batch_impact`, `list_batches`, `project_risk`, `supplier_scorecards`, `search_knowledge`. Arguments are validated (unknown fields rejected), access is checked exactly as in the REST API,
results are size-capped and sanitised and fenced as data. There is no SQL, shell, file, HTTP or write tool.

## Security summary
User text, documents, tool results and history are untrusted data (fenced; system rules say never to obey them). Model output is shown only if citations are real, present when sources exist, and every number appears in the sources/tool results/user text. Provider keys live in env only and travel only in the auth header; errors are mapped to generic codes. Project documents are filtered by the caller's memberships in SQL before ranking; answers derived from tenant data are never cached or shared. Usage rows contain no prompts, answers or keys.

## Operating notes
* Local: leave `AI_PROVIDER=none` to develop the UI (retrieval-only mode). To test with a model, set `AI_PROVIDER`, `AI_API_KEY`, `AI_MODEL`.
* Local models: `AI_PROVIDER=local`, `AI_BASE_URL=http://localhost:11434/v1`, `AI_MODEL=<ollama model>`. Tool calling and vision need a model that supports them.
* Multiple workers/instances: set `REDIS_URL` (limits, budgets, cache shared; if Redis is down the API keeps working with per-process counters and logs a warning).
* Troubleshooting: `AI_MISCONFIGURED` = wrong key/model/base URL (check server log line `provider_errors.auth/model_unavailable`); `AI_TIMEOUT` = raise `AI_TIMEOUT_MS`; answers always "retrieval" = provider off, failing, or validation rejecting (see `/ai/metrics` `validation_rejected`); Hindi search finds nothing = set `AI_EMBEDDING_PROVIDER`.
* Retrieval scales to a few thousand chunks (embeddings stored as JSON, compared in Python). Beyond ~10k chunks move to PostgreSQL + pgvector (`ai_chunks` already stores model, dimensions and chunking config).
