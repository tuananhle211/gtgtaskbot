# Step 1E — PR Web Admin API and UI

> **Superseded in places by [Step 1E.1](STEP_1E1_WEB_SECURITY_HARDENING.md).**
> Three statements below were true when written and are no longer: CORS is no
> longer derived from `WEB_BASE_URL` (it is off unless an origin is named); the
> milestone-1 `/api/v1` routers are no longer served by default; and login-token
> redemption now takes a row lock, because without one a raced link minted **five**
> sessions out of eight attempts. Known gap 6 below is what 1E.1 fixed, and the
> reality was worse than stated there: 24 unauthenticated OWNER *writes*,
> including a self-promotion to OWNER.

**Alembic head is now `0017`, not `0016`.** That is a deviation from this step's
brief, which expected no migration and said to stop and report if one was
genuinely needed. One was; it was reported; the magic-link design was chosen.
[The reasoning is below](#why-a-migration-was-unavoidable) and it is the first
thing to read.

## What was built

```
Browser
  └─ Next.js (frontend/)                      ← renders, never decides
       └─ /api/pr/…  (src/meobot/api/routers/pr.py)      ← translates
            └─ Pr* services (src/meobot/application/)     ← ALL authority
                 └─ PostgreSQL
                      ↑
Telegram tools (src/meobot/tools/pr_*.py) ─────┘  same services, same rules
```

| Layer | Files | Lines of business logic |
| --- | --- | --- |
| Migration | `alembic/versions/0017_web_sessions.py` | — |
| Model | `db/models/web_session.py` | — |
| Auth service | `application/web_auth_service.py` | sessions only |
| Shared wiring | `application/pr_services.py` | — |
| HTTP | `api/routers/pr.py`, `api/routers/web_auth.py`, `api/schemas/pr.py`, `api/deps.py` | **none** |
| Bot | `bot/handlers/web.py` (`/web`) | — |
| Frontend | `frontend/` (10 routes) | **none** |

34 new HTTP routes. 27 backend tests, 19 frontend tests, all passing.

## Why a migration was unavoidable

The brief assumed authentication existed. It did not — not partially, not in a
form that needed extending. Before this step:

```python
# src/meobot/api/deps.py, before Step 1E
def get_current_system_actor(settings) -> Actor:
    """Development-only actor for the internal API. …
    TODO(milestone-2): replace with a real mechanism … and stop granting OWNER
    unconditionally."""
    return Actor(user_id=None, role=Role.OWNER, is_bootstrap_owner=True, …)
```

**Every** HTTP request was a synthetic `OWNER`. No session store, no token table,
no signing secret, no auth middleware. And `users` carries no email, no password
hash and no external identity — only Telegram columns.

Building the panel on that stub would have meant: anybody who reached port 8810
could grant themselves Head Review and approve with it. The `cloudflared` service
in `docker-compose.yml` can expose that port.

Every design that answers "which *person* is this request" needs somewhere to
keep state:

| Design | Schema needed | Why not |
| --- | --- | --- |
| Shared service token | none | Cannot say **which person**. Reviewer separation, grant attribution and approval history all need a `users.id`. |
| Proxy header identity (Cloudflare Access) | `users.email` | Adds a column to `users`, and ties the panel to one hosting choice. |
| Username + password | password hash, reset flow | The brief forbade inventing this, correctly. |
| **Telegram magic link + session table** | `web_sessions` | Chosen. |

So the choice was between a migration and a design that cannot answer the
question the PR module is built around. The migration was reported rather than
created silently, and the option that creates it was selected.

## How authentication works

```
1.  Person, in a Telegram DM:  /web
2.  Bot mints a one-time token, stores sha256(token), DMs the link
3.  Browser opens https://…/auth/login?t=<token>
4.  FastAPI redeems it (single-use), mints a session token,
    sets HttpOnly; Secure; SameSite=Strict cookie, redirects to /pr
5.  Every later request: cookie → sha256 → web_sessions row → users row → Actor
```

**Telegram is the enrolment channel, not the credential.** A Telegram id proves
nothing — ids are public, and one in a header would be a bearer token anybody
could copy. What makes step 2 safe is that Telegram will only deliver a private
message to somebody who started a conversation with the bot, and this deployment
already knows which `users` row that account belongs to.

There is deliberately **no HTTP endpoint that issues a login link.** One would
hand a credential to whoever asked. `/web` is the only way in, and it refuses in
a group chat, refuses for an actor with no `users` row, and refuses when
`WEB_BASE_URL` is unset.

### What is stored

Only `sha256(token)`. A dump of `web_sessions` contains nothing replayable —
which is why it hashes rather than encrypts: there is no key to lose. No salt and
no stretching, deliberately: these are 256-bit random strings, not passwords, so
bcrypt would buy nothing and would cost latency on every authenticated request.

### The actor is rebuilt every request

Not cached in the session. Somebody demoted at 09:00 stops being a `TEAM_LEAD`
at 09:00, and a deactivated account stops resolving on its next click rather
than whenever its cookie happens to expire.

### CSRF, and why there is no token

`SameSite=Strict` is the control. The browser will not attach the cookie to a
request another site caused, so a form on an attacker's page posting to
`/api/pr/contents/…/reviews` arrives with no session and gets a 401.

That only works if the browser sees **one origin**, which is why Next proxies
`/api/*` and `/auth/login` to FastAPI server-side (`frontend/next.config.mjs`)
rather than the page calling a separate API host. Loosening the cookie to
`SameSite=Lax` to allow a cross-origin call would have given up the defence in
order to work around it.

CORS middleware is installed only when `WEB_EXTRA_ALLOWED_ORIGINS` is set — **not**
from `WEB_BASE_URL`, which Step 1E.1 corrected: deriving an allowed origin from the
panel's own address opened a credentialed cross-origin path the same-origin
topology never uses. Never `*`; a wildcard origin with credentials is a CSRF gift,
and browsers reject the combination anyway.

## The business-logic boundary

No route and no React component decides anything. Enforced, not asserted:

| Rule | Where it lives | Test |
| --- | --- | --- |
| Which transitions are legal | `domain/pr/workflow.py` matrices | backend 11, 21, 23; frontend 1 |
| Who may review at which gate | `PrCapabilityService` + grants | backend 17, 18; frontend 2, 14 |
| Code allocation | `PrCodeService` counters | backend 10, 21; frontend 3 |
| Assignment interval overlap | `domain/pr/assignments.py` | backend 20; frontend 4 |
| Reviewer separation | `PrApprovalService` | backend 15; frontend 5, 11 |
| Version binding | `PrContentService` / `PrApprovalService` | backend 12; frontend 11, 12 |

Three specific things worth naming:

**The reviewer is always the session.** `ApprovalDecisionRequest` has no
`reviewer_user_id` field, and `extra="forbid"` turns an attempt to send one into a
422 rather than a silently ignored field. If a caller could name the reviewer,
the Team-Lead/Head separation rule would be checking a value the caller chose.

**The approval gate is derived, not accepted.** `_stage_gate_for` looks the stage
up in `STAGE_APPROVAL_GATES`, so nobody can aim a Head approval at something
waiting for a Team Lead. An earlier draft of that function hand-wrote the pairs
and named two stages that do not exist (`SCHEDULED`, `FINAL_CHECK`) — caught by
mypy, and now pinned by backend test 23. That is exactly how a duplicated matrix
fails: quietly, for the case nobody tested.

**Every stage is offered in the UI and the server refuses the illegal ones.** No
transition table in the browser. A frontend copy would be wrong the first time
the Python changed, and would make legitimate refusals look like bugs. There is
no drag-and-drop on the board for the same reason — a card that snapped back
after a refusal reads worse than a button that explains itself.

## The AI review boundary holds

`POST /api/pr/contents/{id}/submit-ai-review` **records** a verdict produced
elsewhere. Nothing in this API calls a model. `model_name` and `prompt_version`
are mandatory, because an unattributable verdict cannot be audited later.

The review screen renders `ai_review: null` as **"Chưa có AI review cho phiên bản
này"** — never a blank verdict card, which would read as "checked, all clear".
Backend test 14 proves entering `AI_REVIEW` leaves `pr_ai_reviews` empty and
writes no approval event; frontend test 6 proves no result label appears.

## Error mapping

`api/main.py`'s existing `_STATUS_MAP` already handled most of the PR vocabulary
by inheritance. Step 1E added two entries:

* `WorkflowStateError → 409` — covers `PrWorkflowTransitionError`,
  `PrAiReviewRequiredError` and `PrApprovalStageMismatchError`, all of which
  previously fell through to a bare 400. They are conflicts with the record's
  current state, and 409 tells a client to re-read and reconsider.
* `PrImmutableFieldError → 409`, placed **ahead** of its `ValidationError` base.
  The request is well-formed; the target cannot be changed. 422 would send
  somebody hunting a typo in a body that has none.

| Error | Status |
| --- | --- |
| `PrNotFoundError` | 404 |
| `PrValidationError` | 422 |
| `PrPermissionDeniedError` | 403 |
| `PrWorkflowTransitionError`, `PrStaleVersionError`, `PrReviewVersionMismatchError`, `PrAiReviewRequiredError`, `PrApprovalStageMismatchError`, `PrAssignmentOverlapError`, `PrReviewerSeparationError` †, `PrImmutableFieldError`, `PrConflictError` | 409 |

† Removed by Step 1F.2.2 along with the rule it named. Everything else in the row is unchanged.
| No session | 401 |

PR and auth paths use `{"error": {code, message, details}}`. The milestone-1
routers keep their existing flat body — changing it would break callers for
cosmetic consistency, and both shapes carry the same three fields. Backend test
19 proves no traceback, statement text or driver name reaches a response.

## A wiring bug found and fixed

`get_app_settings` returned `get_settings()` — the process cache — so
`create_app(settings)` was a lie: the app held one set of settings while every
route read another. Invisible until a setting changes visible behaviour, and Step
1E has two (the cookie's `Secure` flag, the session TTL). A test passing
`web_cookie_secure=False` was getting a `Secure` cookie anyway. It now reads
`app.state.settings` and falls back to the cache.

## Guards that were deliberately flipped

Each had a docstring anticipating this step. Each was rewritten to assert the new
boundary rather than deleted.

| Test | Was | Now |
| --- | --- | --- |
| `test_no_web_or_http_surface_was_added` (1D req. 45) | no `api` module mentions `pr_` | renamed `test_the_web_surface_reuses_the_services_rather_than_reimplementing_them`; asserts the router uses the shared bundle and no PR module imports `meobot.tools` |
| `test_no_module_outside_the_models_package_touches_a_reporting_table` | 4 allowed files | +`api/schemas/pr.py`, which serialises `PrPublication` |
| `test_no_telegram_surface_mentions_the_reporting_module` | `api` absolute | `api/pr.py` allowed; `bot` and `integrations` still absolute |
| `test_routers_are_registered` | 15 bot routers | +`web` |
| `test_the_readme_names_the_real_alembic_head` | `0016` | `0017` |
| `test_the_chain_reaches_0016_with_both_new_tables` | `head == "0016"` | asserts 0016 is reached and its tables exist — a test about 0016 should not assert what `head` is |
| `test_downgrading_0016_removes_only_the_two_new_tables` | baselined at `head` | baselines at `0016`, so it measures 0016's own downgrade |

## Verification

```
uv run ruff format --check .        ✅
uv run ruff check .                 ✅
uv run mypy src                     ✅  297 files
uv run pytest tests/unit -q         ✅  2323/2349; 26 pre-existing failures (below)
uv run pytest tests/integration -q  ✅  230 passed against real PostgreSQL 16
cd frontend && npx tsc --noEmit     ✅
cd frontend && npx vitest run       ✅  19/19
cd frontend && npx next build       ✅  10 routes
docker build ./frontend             ✅  image serves /auth/failed, security headers present
uv run alembic upgrade head         ✅  0017
uv run alembic downgrade 0016       ✅  round trip clean
```

**26 pre-existing unit failures**, in `test_hr_requests.py` and
`test_notification_routing.py`, all `ValidationError: PAST_DATE` from
`hr_request_service.py:111`. Reproduced on clean `HEAD` (`6e6085e`) in a
throwaway git worktree: **26 there too, identical set.** They are date-dependent
tests whose fixed dates have fallen into the past, and they are unrelated to this
step. They will keep growing until somebody makes those fixtures relative.

The migration was verified against a **disposable** PostgreSQL container on
`127.0.0.1:55440`, created and destroyed for the purpose. The `content-factory-*`
containers on this machine belong to another project and were not touched — the
only command aimed at one was a read-only `SELECT 1` that failed on
authentication, which is how I learned it was not MeoBot's.

## Configuration

```bash
WEB_BASE_URL=                       # empty ⇒ /web refuses to issue links
WEB_LOGIN_TOKEN_TTL_SECONDS=600     # single-use, arrives instantly
WEB_SESSION_TTL_SECONDS=43200       # one working day
WEB_COOKIE_SECURE=true              # off only for http://localhost
WEB_EXTRA_ALLOWED_ORIGINS=          # not needed with the Next proxy
```

`WEB_BASE_URL` has no safe default, so it has none. An empty value refuses to
mint a link rather than emitting one pointing at an unknown host.

## Running it

```bash
docker compose --profile web up -d web    # published on 127.0.0.1:8811
# then, in a Telegram DM to the bot:
/web
```

The `web` service sits behind a profile because it is optional — the bot is the
primary interface and works without it — and because it is only useful once
`WEB_BASE_URL` is set and reachable, which is a deployment decision. It publishes
on localhost only, like the API: exposing it is a separate, deliberate step.

## Known gaps

1. **Brand is a UUID text field on the create-content form.** There is no brand
   endpoint yet, and scraping ids off the content list would be guesswork. One
   `/api/pr/brands` route would fix it.
2. **No platform or channel-creation UI.** The API supports creating channels;
   the panel only lists and updates them, because creating one needs a
   `platform_id` and there is no platform endpoint either.
3. **Reports page shows live stage counts and nothing else.** The Step 1B
   reporting tables exist and **nothing writes them** — no aggregation service,
   no scheduled job, no metrics ingestion. The page says so instead of drawing a
   chart from numbers that do not exist.
4. **No pagination controls.** List routes cap at 200 and the UI requests one
   page. Fine at current volume; a board with a thousand drafts would silently
   show 200 of them. The cap is server-side and explicit rather than a hidden
   truncation, but the UI does not yet say "showing 200 of N".
5. **No "sessions I have open" screen.** `revoke_all_for_user` exists in the
   service and no route exposes it. Somebody who loses a laptop currently needs a
   DB query.
6. ~~**`get_current_system_actor` still serves the milestone-1 routers.**~~
   **Fixed in [Step 1E.1](STEP_1E1_WEB_SECURITY_HARDENING.md).** The audit there
   found 24 unauthenticated OWNER *writes* — including
   `PATCH /api/v1/users/{id}/role`, a self-promotion to OWNER that unlocks the
   whole PR module. Those routers are now **not mounted** unless
   `API_INTERNAL_ROUTERS_ENABLED=true`, and `get_current_system_actor` refuses to
   produce an actor when they are not.
7. **The frontend has no end-to-end test against a live server.** The 19 tests
   stub `fetch`. What is unproven is the proxy rewrite and the real cookie
   round-trip in a browser — which is what
   [`STEP_1E_WEB_SMOKE_CHECKLIST.md`](STEP_1E_WEB_SMOKE_CHECKLIST.md) is for.

## Exit criteria

| # | Criterion | Status |
| --- | --- | --- |
| 1 | Web authentication resolves to a real `users.id` | **Met** |
| 2 | Telegram identity is never a web credential | **Met** — backend test 3 |
| 3 | No invented username/password auth | **Met** |
| 4 | Authorization checked inside `Pr*` services | **Met** — backend 17, 18 |
| 5 | No workflow/capability/overlap/code logic in the API or UI | **Met** — backend 21–24, frontend 1–5 |
| 6 | No raw ORM serialization | **Met** — hand-written schemas |
| 7 | No SQL errors or tracebacks exposed | **Met** — backend 19 |
| 8 | Hidden buttons are not authorization | **Met** — backend 17; capabilities are a rendering hint only |
| 9 | AI review recorded, never executed | **Met** — backend 14, frontend 6, 7 |
| 10 | Full `/api/pr/...` surface | **Met** — 34 routes |
| 11 | All seven pages exist with loading/error/empty states | **Met** |
| 12 | Reports page | **Met, as a placeholder that says so** |
| 13 | No migration | **Not met — `0017`, approved.** See the top of this document. |
| 14 | Unrelated projects untouched | **Met** |
