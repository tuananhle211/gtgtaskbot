# Step 1E.1 — Web security and deployment hardening

**No migration. Alembic head remains `0017`.**

Step 1E delivered the panel and reported one known risk: "some older HTTP routers
still use a synthetic OWNER". This step audited that and found it understated.

## The finding

The API serves **90 routes**, not the 34 Step 1E added.

| Auth mechanism | Routes | Writes |
| --- | --- | --- |
| Real web session | 32 | 16 |
| **Synthetic OWNER** | **24** | **24** |
| **No actor at all** | **31** | **5** |

Every synthetic-OWNER route is a write. Among them:

* **`PATCH /api/v1/users/{user_id}/role`** — an unauthenticated caller promotes
  themselves to `OWNER`. Because PR capability administration is gated on
  `user.role.manage`, **the entire PR module follows from that one request**:
  grant yourself Head Review, then approve with it.
* `POST /api/v1/users/{id}/suspend` / `/revoke` / `/enable` — disable anybody.
* `POST /api/v1/invites` — mint invite codes.
* `GET /api/v1/invites` — *read* live invite codes, with no actor at all.
* `POST /api/v1/scripts/{id}/approve-production`, drive-folder registration,
  spreadsheet creation, group-policy writes.

The 26 unauthenticated reads expose the user list with roles, group policies,
scripts and their approval history, and `/api/v1/system/info` — which publishes
the full tool registry, a map of everything the deployment can do.

`/docs` and `/openapi.json` were enabled by default, publishing the whole surface.

This was survivable at `127.0.0.1:8810` behind nothing. It is not survivable once
a reverse proxy is pointed anywhere near this app.

## HTTP surface inventory

Reproduce it at any time:

```bash
uv run pytest tests/unit/test_web_security.py::test_01_the_inventory_finds_the_synthetic_owner_where_it_lives -q
```

`route_inventory()` in `tests/unit/test_web_security.py` walks each route's real
dependency tree. It does **not** grep signatures: the first version of this audit
matched the string `"ActorDep"` and reported every PR route as synthetic-OWNER,
because `CurrentActorDep` contains it as a substring. That false positive would
have buried the real finding.

### Browser-facing surface — the only paths a browser may reach

| Method | Path | Auth | Writes |
| --- | --- | --- | --- |
| GET | `/` | none | no |
| GET | `/health/live`, `/health/ready` | none | no |
| GET | `/auth/login?t=…` | the login token itself | mints a session |
| GET | `/api/auth/session` | web session | no |
| POST | `/api/auth/logout` | cookie if present | revokes |
| GET/POST/PATCH/DELETE | `/api/pr/…` (32 routes) | **web session** | 16 writes |

34 routes. **Zero use the synthetic OWNER; every write requires a real session.**

### Internal surface — not served by default

`/api/v1/*`, 51 routes across `access`, `invites`, `scripts`, `script_types`,
`sheet_profiles`, `drive`, `conversations`, `system`.

## Synthetic OWNER audit and classification

Every synthetic-OWNER and no-actor endpoint, classified per the brief:

| Endpoint group | Class | Decision |
| --- | --- | --- |
| `/health/live`, `/health/ready` | **A** public read-only | Unchanged. Component up/down and a timestamp; no config, no versions of dependencies, no user data. `docker compose` and `scripts/nas.sh` poll them. |
| `/` service banner | **A** | Unchanged. Name, version, environment. |
| `/api/pr/*`, `/api/auth/*` | **C** authenticated user | Already correct — real session → `users.id` → `Actor`. |
| `/api/v1/users/*` (role, suspend, revoke, enable, quota) | **E** unsafe | **Excluded from the served surface.** |
| `/api/v1/invites/*` | **E** unsafe | Excluded. |
| `/api/v1/scripts/*` approve/review/request-revision | **D** legacy | Excluded. |
| `/api/v1/sheet-profiles/*`, `/api/v1/drive/*`, `/api/v1/spreadsheets/*`, `/api/v1/sheet-templates/*`, `/api/v1/script-types` | **D** legacy | Excluded. |
| `/api/v1/group-policies/*` | **D** legacy | Excluded. |
| `/api/v1/conversations/*` | **D** legacy | Excluded. |
| `/api/v1/system/info` | **B** internal | Excluded. It is a tool-surface map, which is reconnaissance rather than a secret — but there is no reason a browser needs it. |

### Why exclusion rather than adding auth to 51 routes

Retrofitting authentication onto five routers is a rewrite of their
authorization, which is a larger change than a hardening pass should make and
would need its own tests and its own review. It is also not what they are for:
**nothing in the bot, the workers, or `scripts/nas.sh` calls them.** They are
development and operations tooling. `scripts/nas.sh health` uses `/health/ready`,
and that still works.

The brief's own rule applies: *"Legacy endpoints that have no valid current use
should be excluded from the externally served router surface rather than silently
running as OWNER."*

Turning them back on is one variable, for a port nothing untrusted can reach:

```bash
API_INTERNAL_ROUTERS_ENABLED=true    # logs a warning at startup
```

### Defence in depth

`get_current_system_actor` now **raises `ConfigurationError` unless that flag is
set**. The primary control is that the routers are absent; this makes the failure
mode of getting that wrong a 500 with nothing written, rather than a silent grant
of OWNER. A router mistakenly added to `_include_web_facing_routers` fails closed.

## Security fixes implemented

| # | Fix | File |
| --- | --- | --- |
| 1 | Internal `/api/v1` routers off by default | `api/main.py`, `core/config.py` |
| 2 | `get_current_system_actor` fails closed | `api/deps.py` |
| 3 | **Login-token redemption made atomic** (`SELECT … FOR UPDATE`) | `application/web_auth_service.py` |
| 4 | Production refuses insecure cookie / non-HTTPS base URL | `core/config.py` |
| 5 | CORS no longer derived from `WEB_BASE_URL`; off by default | `api/main.py` |
| 6 | `/docs` and `/openapi.json` off in production | `api/main.py` |
| 7 | Nonce-based CSP, Permissions-Policy, conditional HSTS | `frontend/src/middleware.ts` |
| 8 | Web settings actually reach the containers | `docker-compose.yml` |
| 9 | Log redaction covers the web-auth vocabulary | `core/logging.py` |

### Fix 3 was a real vulnerability, and it is measured

`redeem_login_token` read the row, checked `redeemed_at`, then wrote it. Two
requests carrying the same link both read it unredeemed, both passed the check,
and **both minted a session**.

Not theoretical: a double-click in the Telegram desktop client does it, and so
does a link-preview fetcher followed a moment later by the human.

Measured against real PostgreSQL by deleting the lock and re-running
`tests/integration/test_web_auth_concurrency.py`:

```
without FOR UPDATE:  5 of 8 concurrent attempts minted a session
with    FOR UPDATE:  1 of 8
```

One link became five credentials. `expires_at > created_at` does not help, and
neither does the unique index on `token_hash` — each session gets a different
token.

## Magic-link security

| Property | Status | Evidence |
| --- | --- | --- |
| Cryptographically random | `secrets.token_urlsafe(32)` — 256 bits | source |
| Only hashed token stored | SHA-256, verified across every column | test 11/18 |
| One-time use | `redeemed_at` under a row lock | tests 13, 16, 17 |
| Atomic consumption | `SELECT … FOR UPDATE`, no `SKIP LOCKED` | integration, lock-removal proof |
| Concurrent double-consume | exactly one session from 8 attempts | integration test 17 |
| Short TTL | 600 s default | config |
| Replay rejected | second use → `/auth/failed`, mints nothing | test 13 |
| Expired rejected | checked against the stored row | test 14 |
| Unknown rejected, indistinguishably | one destination for all four failures | test 15 |
| Raw token never logged | issue → redeem → use, every record inspected | test 19 |
| Issuing a new link kills the old | prior unredeemed tokens revoked | integration 17b |

No hashing cost beyond SHA-256, and that is deliberate: these are 256-bit random
strings, not passwords. There is no dictionary to attack, so bcrypt would buy
nothing while adding latency to every request that presents a cookie.

## Session security

| Property | Status |
| --- | --- |
| Only the hash stored | ✅ test 23 |
| Revocable server-side | ✅ logout revokes the row, not just the cookie (test 25/27) |
| Expires | ✅ checked against the stored row, not the cookie's max-age (test 26) |
| User must exist | ✅ FK `RESTRICT`; orphan resolves to nobody (test 28/30) |
| User must be active | ✅ next request → 401 (test 5) |
| Actor rebuilt per request | ✅ from the `users` row every time |
| Role change without re-login | ✅ test 6 |
| Capability change without re-login | ✅ test 7, **with one caveat below** |
| A login token cannot be a session | ✅ `kind` filter (test 29) |

### The caveat: a same-day capability revoke is not immediate

`PrCapabilityService.revoke` defaults `effective_to` to **today**, and Step 1C.1
defines `effective_to` as the **last day in force**. So revoking Head Review from
somebody leaves them able to approve **until midnight**.

The session layer is not at fault — it re-reads grants on every request, proven by
closing a grant as of yesterday and seeing it vanish on the next click. What lags
is the grant's own end date, which is consistent interval arithmetic.

**Not changed here**, because altering that default would change PR interval
semantics, and a security pass has no business doing that quietly. It is a named
residual risk with two levers that *are* immediate:

* **deactivate the user** — `users.active = false` → 401 on the next request;
* **revoke their sessions** — `WebAuthService.revoke_all_for_user`.

Test 7 asserts the current behaviour explicitly and will fail with a pointer to
this section if somebody changes it.

## Cookie / HTTPS behaviour

```
Set-Cookie: meobot_web_session=…; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=43200
```

* **HttpOnly** — script cannot read it, so an XSS bug cannot exfiltrate it.
* **Secure** — from `WEB_COOKIE_SECURE`, never hardcoded.
* **SameSite=Strict** — the CSRF control (below).
* **Path=/** and **no `Domain`** — a `Domain` would widen the cookie to every
  subdomain.
* **Max-Age matches `WEB_SESSION_TTL_SECONDS`**, so browser and database agree
  about when the session ends.

`APP_ENV=production` **refuses to start** if `WEB_COOKIE_SECURE=false`, or if
`WEB_BASE_URL` is not `https://`. Raised as a settings error, so the process dies
at startup rather than after emitting a login link over plain HTTP.

That refusal exists because of a specific temptation: a browser drops a `Secure`
cookie over `http://`, so the fastest way to make a plain-HTTP deployment "work"
is to turn the flag off — putting the session on the wire in clear. Serve HTTPS.

`development`, `staging` and `test` are unaffected; local work over
`http://localhost` needs `WEB_COOKIE_SECURE=false`.

Production mode is read from `APP_ENV`, **never inferred from a hostname**.

## CSRF / SameSite rationale

**No CSRF token, and here is why that is correct rather than convenient.**

`SameSite=Strict` means the browser will not attach the cookie to a request
another site caused. A form on an attacker's page posting to
`/api/pr/contents/…/reviews` arrives with **no session** and gets a 401. There is
no cross-site credentialed request for a token to protect.

That holds only if the browser sees **one origin**, which is why Next proxies
`/api/*` and `/auth/login` to FastAPI server-side (`frontend/next.config.mjs`)
rather than the page calling a separate API host. The alternative — loosening the
cookie to `SameSite=Lax` or `None` so a cross-origin call works — would give up
the defence in order to work around it.

**The supported deployment is same-origin, and this is enforced by construction:**
CORS middleware is not installed unless an operator explicitly names an origin.
If a cross-origin deployment is ever needed, a real CSRF mechanism must be added
in the same change — that is not a hand-wave, it is a precondition.

Verified end to end by test 36 (cookie in, write performed) and by the
cross-origin form in step 18 of the smoke checklist.

## CORS

| Environment | Behaviour |
| --- | --- |
| Default (any env) | **No CORS middleware installed at all.** |
| `WEB_EXTRA_ALLOWED_ORIGINS` set | Explicit origin list, `allow_credentials=True`, methods and headers minimised. |
| Ever | **Never `*`.** A wildcard origin with credentials is a CSRF gift, and browsers reject the combination anyway. |

Step 1E derived an allowed origin from `WEB_BASE_URL`, which opened a credentialed
cross-origin path that the same-origin topology never uses. Removed.

## Trusted proxy behaviour

**The app trusts no forwarded header, because it is not configured to.**

`uvicorn` runs without `--proxy-headers`, so:

* `request.url` uses the `Host` header — and **nothing security-sensitive reads
  it**. The login link comes from `WEB_BASE_URL`, asserted by tests 20/21.
* `request.client.host` is the **direct peer**, which is the Next.js container or
  the reverse proxy. So `web_sessions.created_ip` records the proxy's address, not
  the browser's.

That last point is a real limitation, stated rather than papered over. `created_ip`
is diagnostic — "was this session created from the office or from a phone" — and it
currently answers "from the proxy". It is used for **no security decision**, which
is why turning on `--proxy-headers` was not done: doing that safely needs a trusted
hop list, and getting it wrong means accepting a client-controlled `X-Forwarded-For`
as truth. The honest boundary is: **the reverse proxy is the last trusted hop, and
nothing downstream of it makes a decision from a header it set.**

## Public base URL handling

`WEB_BASE_URL` is the configured external origin — the brief's
`WEB_PUBLIC_BASE_URL` concept under the name Step 1E already shipped, documented
and tested. A second name for the same value would be worse than reusing it.

* No default. Empty ⇒ `/web` refuses to issue a link.
* Production ⇒ must be `https://`.
* Trailing slashes stripped before the path is appended, so
  `https://x.test/` and `https://x.test` both produce `https://x.test/auth/login?t=…`.
* The token travels as a single URL-safe query parameter; nothing else is
  interpolated into the URL.
* **Never built from a `Host` header.** An attacker who could set
  `Host: evil.test` would otherwise receive a link pointing at their own server,
  which the victim would then open.

## Open redirect protection

`/auth/login` redirects to exactly two hardcoded local paths: `/pr` on success,
`/auth/failed` on failure. It accepts no `next`, `return_to`, `redirect` or
`continue` parameter — test 22 sends all of them and confirms the destination is
unchanged, and greps the router to confirm no such parameter is named.

A magic-link endpoint that honoured a redirect target would be an open redirect
*with a session cookie attached* — the most useful kind to an attacker.

## Logging and redaction

| Concern | Status |
| --- | --- |
| Raw magic token | Never logged. Test 19 issues, redeems and uses a token, then inspects every record and every field. |
| Full login URL | Never logged. `IssuedLogin.__str__` deliberately omits it, so an accidental interpolation cannot leak it. |
| Query string | Not logged. The access log records `request.url.path`, which excludes `?t=…`. Pinned by test 19. |
| uvicorn access log | Disabled (`--no-access-log`), so the request line never reaches a sink either. |
| Session cookie / `Set-Cookie` / `Authorization` | In `SENSITIVE_KEYS`. |
| Added this step | `session_token`, `login_token`, `token_hash`, `magic_link`, `login_url`, `set_cookie`, `session_cookie`. |

What *is* logged: request id, method, path, status, duration, `user_id`, action and
error code. Enough to answer "who did what and did it work".

## Rate limiting — status

**No application-level rate limiting exists in this repository**, and Step 1E.1
does not add one. Per the brief, the smallest honest position:

* **There is no HTTP endpoint that issues a login link.** Issuing one is proving
  identity, so only `/web` in the bot does it, tied to the already-resolved
  Telegram `Actor` and taking `actor.user_id`. Asserted by test 41. Telegram
  applies its own flood control to the command.
* Asking for a second link **revokes the first**, so links cannot be accumulated.
* `/auth/login` is unauthenticated and brute-forceable in principle. The token is
  256 bits with a 10-minute lifetime; guessing is not the threat model.
* `/api/pr/*` needs a valid session first, so there is no unauthenticated write to
  flood.

**Named as a production follow-up:** a reverse-proxy rate limit on `/auth/login`
and `/api/auth/*` would be cheap and belongs at the proxy, not in the app. Synology
reverse proxy does not offer it; a `nginx`/`caddy` hop would.

## Security headers

Set by Next.js, which is the layer a browser talks to. The API is only ever
reached through that proxy.

| Header | Where | Value |
| --- | --- | --- |
| `Content-Security-Policy` | `middleware.ts` | nonce-based, see below |
| `Permissions-Policy` | `middleware.ts` | `camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()` |
| `Strict-Transport-Security` | `middleware.ts`, only when `WEB_COOKIE_SECURE=true` | `max-age=31536000; includeSubDomains` |
| `X-Content-Type-Options` | `next.config.mjs` | `nosniff` |
| `Referrer-Policy` | `next.config.mjs` | `strict-origin-when-cross-origin` |
| `X-Frame-Options` | `next.config.mjs` | `DENY` |

```
default-src 'self';
script-src 'self' 'nonce-<per-request>' 'strict-dynamic';
style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:; font-src 'self' data:;
connect-src 'self';
frame-ancestors 'none'; frame-src 'none'; object-src 'none';
base-uri 'self'; form-action 'self'; upgrade-insecure-requests
```

### The CSP was made to work rather than weakened

The brief warns against adding a CSP that breaks Next.js and then relaxing it to
`unsafe-everything`. That trap was hit and climbed out of:

1. A nonce CSP was written. Served correctly — and **every script tag on every
   page had no nonce**, because the pages were statically prerendered and a
   build-time HTML cannot carry a per-request value. With `strict-dynamic`, a
   script without the nonce does not load, so the app would have rendered blank in
   a browser while looking perfectly fine to `curl`.
2. `export const dynamic = "force-dynamic"` in the layout was tried. It does
   **not** propagate to child pages in Next 15.5 — the routes stayed `○ (Static)`.
3. `await headers()` in the `/pr` and `/auth` layouts does force it. Verified by
   serving a production build:

```
/pr             scripts=15  without_nonce=0
/pr/tasks       scripts=15  without_nonce=0
/pr/permissions scripts=15  without_nonce=0
/pr/channels    scripts=15  without_nonce=0
/pr/reports     scripts=15  without_nonce=0
/pr/content     scripts=15  without_nonce=0
/auth/failed    scripts=13  without_nonce=0
```

Nothing was lost: every page is a client component fetching behind a session, so
there was no cacheable HTML to prerender.

**`style-src 'unsafe-inline'` is the one concession** — Next injects inline
`<style>` and there is no nonce path for it. An inline-style injection can restyle
a page; it cannot execute. Named as a limitation rather than left to be found.

HSTS placement: asserted by the app for defence in depth, and it **also belongs at
the reverse proxy** — see the deployment checklist. It is decided from
configuration, never from `X-Forwarded-Proto`, because a header a client can set
must not decide a behaviour a browser then caches irrevocably.

## Frontend security

| Check | Status |
| --- | --- |
| No secret in `NEXT_PUBLIC_*` | ✅ no such variable exists anywhere |
| API address stays server-side | ✅ `process.env.MEOBOT_API_URL`, read in the proxy |
| No secret in the built bundle | ✅ swept `.next/static` for the API URL, DSNs, tokens |
| Session token unreachable from JS | ✅ `HttpOnly`, and no `document.cookie` / `localStorage` / `sessionStorage` anywhere |
| No `dangerouslySetInnerHTML` | ✅ |
| Mutations use same-origin credentials | ✅ `credentials: "same-origin"` |
| 401 → login guidance | ✅ points at `/web` |
| 403 ≠ 401 | ✅ distinct branches; a forbidden action never triggers a login loop |
| No client-side role trust | ✅ no `hasPermission`, `ROLE_RANK`, `isAdmin` |
| No retry on 401/403/404/409 | ✅ retrying a 409 would re-send an approval |

## Deployment topology

```
Browser
  │ HTTPS  (Synology reverse proxy terminates TLS)
  ▼
Synology Reverse Proxy  ──────────  the last trusted hop
  │ http://127.0.0.1:8811
  ▼
web        (Next.js)  ← ONE origin the browser ever sees
  │ http://api:8000   (container network, same-origin proxy)
  ▼
api        (FastAPI)  ← /api/pr only; /api/v1 not served
  │
  ▼
postgres · redis       ← no host ports at all
```

`bot`, `worker` and `beat` sit on the same container network with no published
ports. The browser never needs to reach the API container.

## Port exposure

| Service | Host binding | Notes |
| --- | --- | --- |
| `web` | `127.0.0.1:8811` → 3000 | profile `web`, off by default |
| `api` | `127.0.0.1:8810` → 8000 | localhost only |
| `postgres` | **none** | container network only |
| `redis` | **none** | container network only |
| `bot`, `worker`, `beat`, `cloudflared` | **none** | |

Audited across every profile, including `web` and `tunnel`. No override file
broadens anything.

**`127.0.0.1` is sufficient for Synology reverse proxy**, which runs on the NAS
host and can reach loopback. Do not change these to `0.0.0.0` — that would put the
API and the panel on the LAN, bypassing TLS termination and the proxy's HSTS.

**If you enable the `cloudflared` profile, point the tunnel at `web:3000`, never
`api:8000`.** Pointing it at the API would put the PR endpoints on the internet as
a second origin, which breaks the same-origin premise the CSRF story rests on.

## Production environment variables

```bash
APP_ENV=production                       # enables the startup safety checks

# --- Web admin ---
WEB_BASE_URL=https://meobot.example.com  # REQUIRED; must be https in production
WEB_COOKIE_SECURE=true                   # production refuses false
WEB_SESSION_TTL_SECONDS=43200            # one working day
WEB_LOGIN_TOKEN_TTL_SECONDS=600
WEB_EXTRA_ALLOWED_ORIGINS=               # leave empty for same-origin deployment

# --- Surface ---
API_INTERNAL_ROUTERS_ENABLED=false       # leave false
API_DOCS_ENABLED=false                   # forced off in production anyway
```

Development:

```bash
APP_ENV=development
WEB_BASE_URL=http://localhost:3000
WEB_COOKIE_SECURE=false                  # browsers reject Secure over http://
```

## Manual security smoke checklist

Fifteen steps, in [`STEP_1E1_DEPLOYMENT_CHECKLIST.md`](STEP_1E1_DEPLOYMENT_CHECKLIST.md)
§"Security smoke". Summary of what it covers: no-session access, magic-link login,
link replay, logout, old-session reuse, forbidden action with a low-privilege user,
capability revoke mid-session, legacy endpoint unreachability, cookie flags,
HTTPS-only behaviour, and a log inspection for token leakage.

## Residual risks

1. **A same-day capability revoke lasts until midnight.** Documented above with
   two immediate alternatives. The strongest candidate for a follow-up.
2. **`created_ip` records the reverse proxy, not the browser.** Diagnostic only;
   no security decision reads it. Fixing it needs `--proxy-headers` plus a trusted
   hop list, which is a deployment change rather than a code change.
3. **No rate limiting on `/auth/login`.** Token entropy makes guessing
   irrelevant; a proxy-level limit is the right place and is a follow-up.
4. **`style-src 'unsafe-inline'`** — a styling-injection surface. Cannot execute.
5. **The internal `/api/v1` routers still exist in the codebase** with 24
   unauthenticated OWNER writes. They are not served, and the actor dependency
   fails closed, but the code is one configuration flag away. Retrofitting real
   auth onto them, or deleting them, is milestone-2 work.
6. **`API_INTERNAL_ROUTERS_ENABLED=true` is genuinely dangerous.** It logs a
   warning at startup; nothing stops an operator setting it and then pointing a
   proxy at 8810.
7. **No end-to-end browser test.** The CSP nonce, the cookie round-trip and the
   proxy rewrite were verified by hand against a production build; nothing in CI
   re-checks them. The deployment checklist is the standing procedure.
8. **The frontend bundle sweep only runs when `.next` exists.** It warns and skips
   otherwise, so a CI run that never builds would not catch a leak.

## Verification

```
uv run ruff format --check .              ✅
uv run ruff check .                       ✅
uv run mypy src                           ✅  298 files
uv run pytest tests/unit -q               ✅  2391 passed; 26 pre-existing failures
uv run pytest tests/integration -q        ✅  real PostgreSQL 16
uv run alembic heads                      ✅  0017
git diff --check                          ✅
cd frontend && npx tsc --noEmit           ✅
cd frontend && npx vitest run             ✅  32/32
cd frontend && npx next build             ✅  10 routes, all /pr dynamic
docker compose config                     ✅  valid; only api+web bind, both 127.0.0.1
production-like serve smoke               ✅  headers + nonce verified on a built server
```

The 26 unit failures are the pre-existing `PAST_DATE` fixtures in
`test_hr_requests.py` and `test_notification_routing.py`, reproduced on clean
`HEAD` and unrelated to this step.

## Deployment readiness

**Ready to deploy behind a reverse proxy with HTTPS**, with these conditions:

1. `WEB_BASE_URL` is `https://` and matches what the browser types.
2. `API_INTERNAL_ROUTERS_ENABLED` stays `false`.
3. The proxy points at `web:3000` / `127.0.0.1:8811`, **not** at the API.
4. `postgres` and `redis` keep no host ports.
5. Residual risk 1 is understood: to remove somebody's review authority
   immediately, deactivate the account rather than revoking the grant.

Not ready for direct internet exposure of the API container, and not intended to
be — the topology exists so that it never is.
