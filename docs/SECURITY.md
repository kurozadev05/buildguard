# Security

## Model
* **Sessions:** access + refresh tokens exist only in `HttpOnly; SameSite=Strict` cookies (`Secure` by default). Refresh tokens rotate; re-use of an old one revokes the whole token family; password change/reset invalidates all sessions.
* **CSRF:** SameSite=Strict + required `x-bg-csrf` header + Origin check on every non-GET.
* **Authorisation:** enforced in the backend on every call (roles: admin, site_engineer, qa, lab, client, auditor + per-project membership). Hiding buttons in the UI is cosmetic only.
* **Browser exposure:** CSP with per-request nonce, `connect-src 'self'`, `frame-ancestors 'none'`, nosniff, referrer policy. The bundle contains no backend URL, keys or provider hosts.
* **AI:** output is rendered as plain text (no HTML, links or images). Tools run with the user's permissions and cannot cross projects. Project documents and caches are tenant-isolated. Per-user rate limit and daily token budget. Ungrounded claims are replaced.
* **Uploads:** server-side content sniffing (JPEG/PNG/WebP/PDF only), size cap, sanitised filenames, SHA-256 seal, safe download headers.
* **Local ≠ lax:** the same protections are on in local mode. `ALLOW_SELF_REGISTER=true` lets anyone who can reach your machine's port create an account that sees nothing until added to a project; the ports are bound to localhost by default.

## Audit results (run against the real stack: PostgreSQL 16 + Redis + production-mode backend)
| Check | Result |
|---|---|
| 49 cross-user (IDOR) attempts across every resource type | all refused / empty |
| Offline-sync endpoint used to write into another user's project | rejected |
| Mass assignment (role, status, batch code, token, verdict, version) | ignored |
| SQL/template/path-traversal strings in 30 query parameters | no 5xx, no extra rows |
| NUL byte in text (was HTTP 500 on PostgreSQL) | **fixed → HTTP 422** |
| 6 abusive uploads (renamed text, HTML, SVG+script, .php, empty, 11 MB) | all rejected (415/413) |
| Tampered token, `alg:none` token, Authorization header from browser | refused / ignored |
| Login brute force, registration flood | throttled with `Retry-After` |
| CORS | explicit origins only, never `*` |
| Secrets scan (181 files), dependency audit (pip-audit, pnpm audit) | clean |
| Client bundle scan | no keys, backend URL or provider hosts |

## Not covered
No third-party penetration test. Rate-limit and lockout behaviour is per process. Offline snapshots in IndexedDB are not encrypted (they are wiped on sign-out). Report issues privately to the project owner.
