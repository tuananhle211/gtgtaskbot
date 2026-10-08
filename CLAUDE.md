# MeoChat / MeoBot — Creative Ops (PR + Ads)

FastAPI + SQLAlchemy 2 async + Alembic + Celery + aiogram (Telegram) backend in `src/meobot/`;
Next.js 15 (app router, React Query, Tailwind v4) frontend in `frontend/`. UI text is Vietnamese.

## Hard rules
- **PR logic is frozen.** Do not change PR backend behaviour or PR routes. Ads is a separate engine.
- **Never push.** Push to `master` auto-deploys to the production VPS (alembic is NOT run on deploy). Commit only when asked.
- **Never read or print `.env`** or anything in `secrets/`, `handover/` (prod dump + encryption key). Both are gitignored.
- Local-only work: Docker stack at http://127.0.0.1:8811 (web), 127.0.0.1:8810 (api), Postgres on localhost:5432.

## Where things live
- Units = **streams** ("Luồng"): PR = "Luồng PR", ADS = "Luồng Order (ORD)" (chip `short_label` PR/ORD, `unit_short_label`). Codes stay `PR`/`ADS` everywhere technical; only visible text says ORD. `domain/units/`, `application/units/{directory,admin}.py`, `api/routers/units.py`.
  - Rule (0048): **untagged = no stream** (`entries=()`, `/api/units/me` units=[] + `is_untagged`, PR routes 404). Invite redeem creates no tag. OWNER **and ADMIN** see every stream (`UnitMembership.sees_all`, "Tất cả", `can_admin` all; ADMIN gets the matrix's Admin column in ADS even untagged).
  - Tagging (`tag/update/untag_member`, `UnitAdminService.tags_in`): OWNER/ADMIN anyone anywhere; a TEAM_LEAD tagged in X only in X, never OWNER/ADMIN accounts nor themselves; else 403 `unit_tag_forbidden` (an outsider who tags nowhere still 404). `/api/units/me` `can_tag`; `GET /api/units/untagged`. Unit admin (settings/matrix/video kinds) = OWNER/ADMIN.
  - Deactivate/reactivate: `POST /api/account/members/{id}/deactivate|reactivate` (OWNER/ADMIN, not self/OWNER → 403 `account_status_forbidden`); `include_inactive=true` on the members list. Web invites `GET/POST /api/invites`, `/{id}/disable` (TEAM_LEAD+, else 403 `invite_forbidden`; `/create_invite` is TEAM_LEAD+ too). ORD function tag `function_tag` BT/TK/D.
  - PR routers are gated at include time in `api/main.py` with `require_unit(UnitCode.PR)` (outsiders get 404). Telegram PR tools: `tools/unit_gate.py`. Test worlds tag their PR people (`tests/unit/streams.py`, `pr_world`).
  - Board `order=todo_first` (rows awaiting the viewer first, paged; PR/ADS/ALL) and `TaskRow.awaiting_me` ("Cần làm").
- Ads orders: `domain/orders/` (pipeline, NODE_PLAN for the 7 process codes B/T/D/BT/BD/TD/BTD), `application/orders/` (command/query/scope/code/notifications/work_recorder), `api/routers/orders.py`.
  - Order code `MEMBERCODE-TYPE-yymmdd-nn`; missing member code is derived from the name (`domain/units/member_code.py`) and saved on the tag.
  - Optimistic `version` → 409 `order_stale_version`. KPI recorded via `PrWorkResultService.record_source_result(source_type=ORDER)`.
- Shared board (dashboard + task table for both units): `domain/board/`, `application/board/{sources,task_board_service,dashboard_service}.py`, `api/routers/board.py`.
- **One task entity** (0043): table `tasks`, 1-1 with `pr_content_items` or `orders` (`source_type`). Kept in sync by a global `before_flush` hook (`application/tasks/sync.py`, installed from `db/models/__init__.py`) — never write `tasks` by hand. Detail + actions for both units: `application/tasks/{detail_service,action_service}.py`, `GET /api/tasks/{ref}` and `POST /api/tasks/{ref}/actions` (opaque keys `ads:...` / `pr:...`).
- Migration head: **0048** (`alembic/versions/0048_tag_untagged_users_pr.py`: data only, tags every active untagged user PR MEMBER with uuid5 ids; downgrade deletes exactly those). 0047 = table `user_avatars`. 0046 = password reset (`users.password_temporary/password_reset_at`). 0045 = password login (`users.password_hash/password_changed_at/failed_login_count/locked_until`, `web_sessions.auth_method`). 0044 = flexible process codes, `unit_video_kinds` + 11 seeded ADS kinds, `orders.video_kind_*` snapshot.
- **Password login** (0045): username = Telegram id, default password `Settings.web_default_password` (env `MEOBOT_WEB_DEFAULT_PASSWORD`), scrypt hashes (`application/account/passwords.py`), `POST /api/auth/password-login` (`PasswordService`). A PASSWORD session on the default password gets 403 `password_change_required` everywhere except `/api/auth/*`, `/api/account/me`, `/api/account/password` — enforced once in `deps.get_current_web_actor`; Telegram-link sessions are never gated. 5 failures → `locked_until` +15 min (429 `login_locked`). New-password rules: only not empty/whitespace, ≤1024 chars, not the default (`password_empty`/`password_too_long`/`password_is_default`).
- **Password reset** (0046, `application/account/password_reset_service.py`): `POST /api/auth/password-reset {username}` always 202 `{"message": ...}` (no enumeration, 1 per 5 min per account via `users.password_reset_at`); admin `POST /api/account/members/{id}/reset-password` uses the same flow (409 `password_reset_undeliverable` without a private chat). Random 10-char temp password (scrypt-hashed, `password_temporary=true` ⇒ forced change like the default), lockout cleared, all web sessions revoked, sent via the outbox (`NotificationRouter` → template `account.temporary_password`, HTML `parse_mode`, `secret_fields` blanked by `OutboxService` once settled). Change clears the flag. Account screen: `api/routers/account.py`, `application/account/{account_service,stats_service}.py` (stats = grouped queries, Vietnamese month).
- **Avatars** (0047, `application/account/avatar_service.py`, model `db/models/user_avatar.py`): `PUT /api/account/avatar {content_type, data(base64)}` → `{"avatar_url": "/api/account/avatar/<id>?v=<version>"}`; `DELETE` → 204; `GET /api/account/avatar/{user_id}` = raw bytes (any signed-in user; `Cache-Control: private, max-age=31536000, immutable`, nosniff, inline, CSP sandbox; 404 `avatar_not_found`). Checks: webp/jpeg/png only, strict base64, ≤300 KB decoded (base64 >409600 chars refused undecoded), magic bytes → 422 `avatar_too_large` / `avatar_invalid_image`. Version bumps per upload. `avatar_url` on `/api/auth/session`, `/api/account/me`, member rows. The GET is the only avatar route open to a must-change session. Audit `user.avatar.updated/removed` without bytes.
- **Ads permissions** (role-based, like PR's capabilities but no per-person grants): `domain/orders/permissions.py` — `AdsPermission` × roles HEAD/ADMIN/LEAD/STAFF/ORDERER → NONE/OWN/ALL, stored in `org_units.settings["permissions"]` (defaults in `DEFAULT_MATRIX`), edited on `/admin/units`. OWNER = everything. The policy (`pipeline.available_actions`), visibility (`OrderScope`), awaiting-me and the named holder (`board/holders.py`) all read `ctx.permissions` — never check roles directly.
- Ads positions: HEAD = "Trưởng phòng ORD"; a function role (BIEN_TAP/THIET_KE/DUNG) with `is_lead` = "Trưởng phòng Biên kịch/Design/Dựng" (`LEAD_ROLE_LABELS`, `unit_role_label(role, is_lead)`). The picker sends role + is_lead (frontend key `ROLE:LEAD`); `is_lead` is forced false for non-function roles.
- A node activated with nobody chosen is routed to its Leader (else the head, else anyone with `NODE_ASSIGN`) — `AdsApprovers.assigner` in `board/holders.py`, used by `_activate`; shown as "Chờ X phân công". Only if nobody may assign does it stay `CHUA_GIAO` ("Chờ giao").
- **Process ("Quy trình")**: `orders.video_type` holds the process code = letters of the ticked production nodes in pipeline order (B=BIEN_TAP, T=THIET_KE, D=DUNG). Create takes `video_type` or `process: [node types]` (422 `process_empty` / `invalid_process` / `process_mismatch`). D without T → `design_link` required. Label "Biên kịch › Design › Dựng" (`labels.PROCESS_SEPARATOR`). The link (GAN_LINK) goes to the assignee of the last production node; `btd_link_attacher` only applies to BTD (`pipeline.link_attacher_node(settings, video_type)`); GAN_LINK permissions are the attaching function's (`function_node`). Script-lead video review applies when the plan has B.
- **Final review is the orderer's** (`order.owner_user_id`): holder/status "Chờ <orderer> duyệt final", notifications and awaiting-me go to them; `AdsPermission.FINAL_REVIEW` = stand-in (may decide, never named).
- **Video kinds** ("Loại video"): `unit_video_kinds` (no delete, deactivate), `GET/POST/PATCH /api/units/{code}/video-kinds` (read: members; include_inactive + writes: unit admin). Order create needs `video_kind_id` while the unit has an active kind (422 `video_kind_required` / `video_kind_invalid`); name/points snapshotted on the order. Board `kind_label` = kind name else process label; filter `video_kind_id`.
- Ads node review is a unit setting (`review_bien_tap`/`review_thiet_ke` off, `review_dung` on): without review, hand-in completes the node and starts the next.
- Frontend screens: `/dashboard`, `/tasks` (shared task table), `/tasks/[ref]` (ONE detail page for both units; PR-only tabs reuse `components/pr-content-detail/*`), `/orders/new` (create for ADS **and** PR — PR form is `components/pr-create-content.tsx`), `/admin/units` (its team panel is `components/unit-panel.tsx`, also `/pr/permissions` → Thành viên / Quyền duyệt cấp thêm, each split into Phòng PR / Phòng Ads via `?team=ads`; the PR roster shows PR/ADS tags). `/orders/:ref` and `/pr/content/:id` redirect to `/tasks/...`. PR module pages under `/pr/*` (`/pr/content` board still exists but is not in the nav). Nav: `components/shell.tsx`. API client: `lib/api.ts`.

## Commands
```bash
# backend (pyproject sets -q, so override addopts to get a summary)
MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meotest:test@localhost:5432/meobot_test \
  uv run pytest -o addopts="--strict-markers --strict-config" tests/unit/test_order_commands.py
uv run ruff check src tests && uv run ruff format src tests && uv run mypy src

# frontend (run from frontend/)
npm run typecheck && npx vitest run [tests/file.test.tsx]   # no lint script exists
# no prettier config: running prettier reformats whole files, keep it to new files

# local stack
docker compose build api web && docker compose up -d api web bot worker beat
```
Full backend suite takes ~18 min; prefer running the relevant files.
Demo Ads data (local only): `docker compose exec -T api python - < scripts/seed_ads_demo.py`. Migrate local DB: `docker compose exec -T api alembic upgrade head`.

## Known gotchas
- Pre-existing failures to ignore: `frontend/tests/security.test.ts` #37 (x-forwarded-proto); `tests/unit/test_provider_compatibility.py` 2 tests flaky only in full runs.
- Several frontend tests **scan source files** (e.g. `confirmation.test.tsx` #173 requires every `useMutation` file to import `@/components/confirm` or be in its exempt list; `ux.test.tsx` checks the nav href list and file contents). Moving code between files means updating those tests.
- Work/KPI tests rely on the `frozen_work_clock` fixture (`tests/unit/work_clock.py`, pinned 2026-09-15 04:00 UTC).
- Append-only tables use Python `default=utcnow` (ordering ties with DB `now()`).
- Frontend source-scanning tests read PR detail code through `readPrContentDetailSource()` in `frontend/tests/helpers.tsx`.
- `tests/unit/test_tasks.py` is the Celery eager-mode suite; unified task tests are `tests/unit/test_unified_tasks.py`.
- `/api/units/{code}/members` returns `{"members": [...], "assignable_roles": [...]}` (no `data` envelope).

## Status
Branch `feature/order-video`, nothing committed yet. 0044 (flexible process + video kinds, final review by the orderer), 0045 (password login), 0046 (password reset), 0047 (avatars) and 0048 (streams: tag untagged users PR) applied to the test DB only; the local docker DB still needs `alembic upgrade head`. Deferred: AI review for Ads.
