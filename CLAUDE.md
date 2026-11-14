# MeoChat / MeoBot — APM Management (Apexmed internal operations)

FastAPI + SQLAlchemy 2 async + Alembic + Celery + aiogram (Telegram) backend in `src/meobot/`;
Next.js 15 (app router, React Query, Tailwind v4) frontend in `frontend/`. UI text is Vietnamese.
Product name on screen: **APM Management** (package stays `meobot`; bot + `/terms` `/privacy` still say TasksBot on purpose).

## Hard rules
- **PR logic is frozen.** Do not change PR backend behaviour or PR routes. ORD (code `ADS`) is a separate engine.
- **Never push.** Push to `master` auto-deploys to the production VPS; push to `dev` to the dev VPS. Both deploy jobs back the DB up (`~/db-backups/before-deploy-*.dump`) and run `alembic upgrade head` before starting the new code (`.github/workflows/deploy.yml`). **The workflow runs no tests / lint** - run them yourself. Commit only when asked.
- **Never read or print `.env`** or anything in `secrets/`, `handover/` at the repo root (prod dump + encryption key). Both are gitignored. `docs/handover/` is ordinary docs.
- Local-only work: Docker stack at http://127.0.0.1:8811 (web), 127.0.0.1:8810 (api), Postgres on localhost:5432.
- **Keep the docs current**: a change to a module's behaviour updates its `docs/modules/<module>.md` in the same change - the rule in "Quy tắc nghiệp vụ hiện hành" (current behaviour only, with `path:function` + error code) and one line in "Mốc thay đổi" (migration + date + what). Do not grow this file with module detail; add a line here only for a cross-module rule.

## How to work — every code change, whoever drives
- **Load the skill `apm-change` before editing anything in `src/`, `frontend/`, `alembic/`, `tests/`.** It is the process: read the module doc → classify risk Xanh/Vàng/Đỏ and **stop for approval** on Vàng/Đỏ → ask instead of guessing a business rule → test first → update the module doc → `python3 scripts/check_changes.py` must print `ALL GREEN` → independent review by the `apm-reviewer` agent → hand-over report in the skill's format. Never say "xong" with a step skipped without saying which.
- Guardrails run as hooks (`.claude/settings.json`, `.claude/hooks/`): reading/printing secrets and `git push` are denied for everyone. Without the owner marker `.claude/owner.local` (gitignored, only on the owner's machine): commits on `master`/`dev`/`main` are denied, edits in red zones (frozen PR code, migrations, permissions, flush hooks, deploy/CI, dependencies, `.claude/`, this file) and destructive/remote commands ask a human, and a turn cannot end while changed code has not passed `check_changes.py`.
- Branches: `feat/<x>` or `fix/<x>` from `origin/dev` → PR into `dev` (template `.github/pull_request_template.md`, CI `.github/workflows/ci.yml`) → the owner merges → `dev` → `master`. Tests red on master before the gate: `.github/known-failures.txt` - only NEW failures fail; never add a line to pass. Human guide: `CONTRIBUTING.md`.

## Where things live — read the module doc before touching a module
Index + glossary: [`docs/modules/README.md`](docs/modules/README.md). Older core (PR workflow, Telegram, outbox, KPI M2/M6): `docs/handover/` (code map `07_CODEBASE_MAP.md`, local setup `09_LOCAL_DEVELOPMENT_SETUP.md`). Plans in `docs/<area>/*_PLAN.md` are design-time and may be stale; the module docs and the code win.

| Doc | Module | Backend entry points |
|---|---|---|
| `docs/modules/org.md` | Departments, seats, unified permissions (`MODULES`, `load_access`), streams/tags, invites, bot access, "đang phát triển" | `domain/org/`, `application/org/`, `application/units/`, `api/routers/{departments,permissions,units}.py` |
| `docs/modules/account.md` | Password / member-code login, reset, avatars, impersonation, member code, profiles, delete/restore | `application/account/`, `api/routers/account.py` |
| `docs/modules/board.md` | Shared task board (PR + ORD), one `tasks` table, task page | `domain/board/`, `application/board/`, `application/tasks/`, `api/routers/{board,tasks}.py` |
| `docs/modules/ord.md` | Order flow (ORD/`ADS`): process B/T/D, approval, cancel / "Trả order", task codes + points, Design ban, birthday, tokens & deadlines, AI script review | `domain/orders/`, `application/orders/`, `api/routers/orders.py` |
| `docs/modules/pr.md` | Phòng PR (frozen core), PR roster, PR code gate, Seeding | flat `application/pr_*.py`, `api/routers/pr*.py`, `application/seeding/` |
| `docs/modules/performance.md` | Points reports, quota, coefficients, Gantt, `work_points` ledger, PR points | `domain/performance/`, `application/performance/` |
| `docs/modules/ads.md` | Phòng Ads: Meta syncs, dashboards, CRM revenue, KPI + weekly budget per BM | `domain/ads/`, `application/ads/`, `integrations/{meta,crm}/`, `tasks/ads.py` |
| `docs/modules/finance.md` | Tài chính: shared CRM sync, revenue, BCLT ledger, debts, spend requests, budgets, MISA | `domain/finance/`, `application/{finance,crm}/`, `api/routers/finance*.py` |
| `docs/modules/hr.md` | Nhân sự: profiles, contracts, đơn từ, leave, shifts, timesheet | `domain/hr/`, `application/hr/`, `api/routers/hr*.py` |
| `docs/modules/resources.md` | Phòng Tài nguyên: inventory, hand-over, requests | `domain/resources/`, `application/resources/` |
| `docs/modules/cskh.md` | CSKH inbox: Pancake, POS, Zalo bridge | `domain/cskh/`, `application/cskh/`, `integrations/{pancake,zalo_bridge}/`, `deploy/zalo-bridge` |
| `docs/modules/agents.md` | Đội agent (Hermes relay), files, rooms, data gateway | `application/agents/`, `api/routers/agent*.py` |
| `docs/modules/assistant.md` | Telegram report assistant, AI connections + routing | `application/assistant/`, `tools/report_tools.py`, `application/ai/` |
| `docs/modules/platform.md` | Brand UI, dates, nav + pins, admin tabs, scaling, perf stats, cron monitor, prod network, deploy | `frontend/src/components/shell.tsx`, `application/{cron,ops}/` |

Migration head: **0109** (`alembic/versions/0109_finance_ledger_marks.py`). Each module doc's "Mốc thay đổi" lists its migrations.

## Cross-module rules (break these and something else breaks)
- **Codes vs labels**: units are `PR` / `ADS` everywhere technical; only visible text says ORD ("Luồng Order (ORD)"). Steps `BIEN_TAP`/`THIET_KE`/`DUNG` = Biên kịch / Design / Dựng. `HCM` shows as SG.
- **Permissions**: resolved once per request by `application/org/access.py` `load_access` → `Access` (role OWNER/ADMIN → department seat grants → personal overrides in `user_permissions`, which may narrow to `NONE`). Every module reads its own `*_access(...)` built on it; never test roles directly (ORD reads `ctx.permissions`). OWNER holds everything; ADMIN everything but `owner_only` permissions. New permission module = add a `ModuleSpec` to `domain/org/modules.py` `MODULES`. Detail: `org.md`.
- **Streams follow seats**: ORD/PR tags (`org_unit_members`) are derived by `sync_stream_tags` after every seat change - never write role / lead / manager there by hand.
- **One task entity**: table `tasks` mirrors `pr_content_items` / `orders` through the global `before_flush` hook (`application/tasks/sync.py`, installed from `db/models/__init__.py`) - never write `tasks` by hand. The `after_flush` hook `application/performance/publish_points.py` writes publication points the same way.
- **Per-request memo**: `application/org/access.request_memo(session, name)` caches in `session.info`, cleared on every flush/commit/rollback/bulk statement. Store only frozen values.
- **The API is one process** (`uvicorn --workers 1`) holding in-memory state (agent turns, rooms, AI script cache, Zalo QR owners, perf collector). Do not add per-process state without saying so in `platform.md`. Workbook build/read (`openpyxl`) in an `async def` goes through `asyncio.to_thread` - `tests/unit/test_event_loop_guard.py` scans for it (new heavy helper → its `HEAVY` set).
- **Queues**: long Meta / CRM syncs on `q_sync` (service `worker-sync` only); everything else on `worker` (`q_default,q_integrations,q_reports,q_notifications`).
- **Secrets at rest**: `SecretBox` (key `PR_SECRET_ENCRYPTION_KEY`) with a per-row AAD (`ai_connection:<id>`, `hr_profile:<user>`, `cskh_page:<id>`…); only a `••••abcd` hint leaves the server. Customer phones are never stored outside CSKH.
- **API shape**: web routes answer `{"data": ...}` (envelope prefixes in the API + a Next rewrite per prefix in `frontend/next.config.mjs`). Exception: `/api/units/{code}/members` returns `{"members": [...], "assignable_roles": [...]}`.
- **Dates**: one format `dd/mm/yyyy HH:mm`, 24 h, Vietnam time. Frontend `lib/datetime.ts` (`formatDateTime` / `formatDate`, board cells `formatShortDateTime`); never `toLocaleString("vi-VN")`. No native date boxes: `components/date-input.tsx` `DateInput` / `DateTimeInput` (tests type `dd/mm/yyyy HH:mm`). Backend user-visible instants `%d/%m/%Y %H:%M`.
- **UI**: load the project skill `apexmed-ui` before any UI work. Shared `Select` (`components/pr.tsx`) becomes searchable at ≥ 8 options. Every file using `useMutation` imports `@/components/confirm` (or is exempt in `confirmation.test.tsx`). Fonts are self-hosted in `frontend/src/fonts/` (keep them out of `src/app/`).
- **Units/me** (`GET /api/units/me`) is the frontend's boot payload: every module's access flags + menu badges live there; the shell refetches it every 60 s.

## Commands
```bash
# definition of done for a change (maps changed files -> module docs + tests, see the script)
python3 scripts/check_changes.py [--base origin/dev] [--full] [--docs-ok "why no doc change"]

# backend (pyproject sets -q, so override addopts to get a summary)
MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meotest:test@localhost:5432/meobot_test \
  uv run pytest -o addopts="--strict-markers --strict-config" tests/unit/test_order_commands.py
uv run ruff check src tests && uv run ruff format src tests && uv run mypy src

# frontend (run from frontend/)
npm run typecheck && npx vitest run [tests/file.test.tsx]   # no lint script exists
# no prettier config: running prettier reformats whole files, keep it to new files

# local stack
docker compose build api web && docker compose up -d api web bot worker worker-sync beat
docker compose exec -T api alembic upgrade head
```
Full backend suite takes ~18 min; prefer running the relevant files (each module doc lists its tests).
Demo Ads data (local only): `docker compose exec -T api python - < scripts/seed_ads_demo.py`.
Visual check of a UI change: `docker compose build web && up -d web`, then `.claude/skills/apexmed-ui/scripts/shoot.sh OUT /path…` (`next dev` cannot hydrate under the strict CSP).

## Known gotchas
- **Prod network**: ufw denies everything but SSH; Docker-published ports skip ufw, so every `ports:` entry must be `127.0.0.1:`. The site is reached only through the Cloudflare tunnel. Details: `platform.md`.
- Pre-existing failures: the exact list is `.github/known-failures.txt` (`scripts/junit_gate.py` ignores only those). Notable: `frontend/tests/security.test.ts` #37 (x-forwarded-proto); nav tests in `design-ban`/`design-library`/`token-deadline` and `performance-reports` "Hệ số điểm" (red on master 14/11/2026); `tests/unit/test_provider_compatibility.py` 2 tests flaky only in full runs.
- Several frontend tests **scan source files** (`confirmation.test.tsx` #173, `ux.test.tsx` nav hrefs, `shell-layouts.test.ts` treats every folder under `src/app/` as a route; PR detail code via `readPrContentDetailSource()` in `frontend/tests/helpers.tsx`). Moving code between files means updating those tests.
- Work/KPI tests rely on the `frozen_work_clock` fixture (`tests/unit/work_clock.py`, pinned 2026-09-15 04:00 UTC).
- Append-only tables use Python `default=utcnow` (ordering ties with DB `now()`).
- `tests/unit/test_tasks.py` is the Celery eager-mode suite; unified task tests are `tests/unit/test_unified_tasks.py`.
- The fake LLM flags the default script rules' literal words (`fake.ORDER_SCRIPT_FORBIDDEN`): test orders must not contain e.g. "cam kết".
- Known code/doc mismatches found on 2026-11-14 are listed in each module doc's "Cạm bẫy & chưa làm" (e.g. "Loại khỏi PR" does not block open PR work; manual Ads sync buttons enqueue on `q_integrations`).

## Status
Branch `master` (prod). Work is committed by the user; uncommitted changes in the tree may belong to another session - look before overwriting. Personal running notes live in the auto-memory (`MEMORY.md`).
