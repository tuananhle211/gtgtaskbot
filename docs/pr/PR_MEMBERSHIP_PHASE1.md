# Thành viên & Phân quyền — giai đoạn 1

Web member management, role management (read-only) and effective permissions
on `/pr/permissions`, on top of the membership backend the Telegram bot already
uses. **No migration.** Alembic head stays at `0038`.

## 1. Audit findings (what already existed)

| Question | Answer, with evidence |
| --- | --- |
| What is "membership"? | A row in `users`. There is no membership table, no team table, and no channel-derived team. `users.status` (`pending` / `active` / `suspended` / `revoked`, `domain/access/models.py`) says whether the membership is live; `users.active` is kept in step with it and is what every auth gate reads. |
| How is a base role stored? | One enum column `users.role` (`Role`: `OWNER` / `ADMIN` / `TEAM_LEAD` / `EMPLOYEE`, `domain/identity/models.py`). Single-role. Roles are code, not rows: no role table, no custom role, no per-role permission configuration in the database. |
| Where does the owner come from? | `MEOBOT_OWNER_TELEGRAM_ID` bootstrap (`IdentityService.resolve_actor`), and/or a `users` row with `role = OWNER`. `OWNER` is never invitable (`can_invite_role`, `INVITABLE_ROLES`) and never assignable by `change_role`; an `OWNER` target is protected from suspend/revoke/re-role (`UserService._guard_target`, `owner_protected`). |
| What is the canonical membership service? | `application/user_service.py: UserService` — `add_user`, `suspend`, `enable`, `revoke`, `change_role` (a `restore` method also exists on the service but no Telegram command, tool or web route calls it). Every Telegram path builds `UserService(session, AuditService(session))`: `/add_user` (`bot/handlers/invites.py`), the access-request `CONFIRM_MEMBER` button (`bot/handlers/access.py`), `/suspend_user` `/enable_user` `/revoke_user` `/change_user_role` (`bot/handlers/people.py`), `/join` (`InviteService.redeem`). |
| Is there already a web membership API? | `api/routers/access.py` exposes `/api/v1/users` — **localhost-only, unauthenticated, system actor**, mounted only when internal routers are enabled. Not usable from the browser and not extended. |
| How are permissions computed? | Role-backed PR capabilities: `domain/pr/policy.py` maps each `PrCapability` to a `Permission`, and `domain/permissions/matrix.py` says which roles hold it. Grant-backed capabilities (`PR_TEAM_LEAD_REVIEW`, `PR_HEAD_REVIEW`, `PR_INTERNAL_REVIEW`): active rows in `pr_user_capabilities` with a `GrantScope`, read by `PrCapabilityService`. `PrQueryService.approval_grants` and `PrCapabilityService.approval_grants_for` are the grant enumerations authorization itself uses. |
| Does a scoped grant change a role? | No. Granting writes a `pr_user_capabilities` row and never touches `users.role` (test 4e). |
| What happens to grants on suspension? | Nothing. The rows stay; the account is refused at the two doors — the web session (`WebAuthService.resolve_session` checks `user.active` on every request → 401 on the next click) and the Telegram middleware (`bot/middlewares.py`: `if actor is None or not actor.active`). The policy engine also refuses an inactive actor. On reactivation the same role and the same grants apply again. |
| Are historical references safe? | Nothing is ever deleted. `pr_content_items.owner_user_id`, `pr_work_contributions.user_id`, `pr_task_assignments.user_id`, `pr_work_plans.user_id`, `pr_approval_events.reviewer_user_id`, `pr_user_capabilities.user_id` and every `*_by_user_id` column keep pointing at the same row through suspension and revocation. |
| Is a migration needed? | No (case 3 in the request). Everything the web needs is already stored. |

### What the audit ruled out

- **Custom roles / multi-role**: no evidence anywhere in the code, the data model
  or the docs. Roles are hard-coded and the matrix is the single source; a role
  editor would have to invent a storage model this repository never had. Phase 1
  shows the roles tab read-only. See §7 for the phase 2 recommendation.
- **A Team**: no table, no concept; channels have assignments but nothing
  groups people into a team. Not created, not inferred.
- **Removing a user row**: the references above make a hard delete corrupt
  history. "Loại khỏi PR" is `revoke` (status `revoked`), **terminal in phase
  1** - see §8.

## 2. Backend changes

### 2.1 `UserService` (canonical, shared with Telegram)

Behaviour unchanged except for two additions, both of which Telegram inherits:

- Every refusal now carries `details["reason"]`, a stable code the web wordings
  key on: `member_add_forbidden`, `invalid_role`, `invalid_telegram_id`,
  `member_revoked`, `member_already_registered`, `member_manage_forbidden`,
  `owner_protected`, `self_change_forbidden`, `target_outranks_actor`,
  `role_change_forbidden`, `role_unchanged`, `member_not_found`. The Vietnamese
  sentences the bot prints are unchanged.
- `change_role` to the role the person already has is a `ConflictError`
  (`role_unchanged`) instead of a silent audit row.
- `_guard_target` refuses an actor acting on their own row
  (`self_change_forbidden`). With the current matrix only `OWNER` holds
  `user.status.manage`, and the owner is already protected as a *target*, so
  this guard is reached only if a future role gains the permission — it is
  defence in depth, not a behaviour change today.
- `_require` locks the target row (`SELECT … FOR UPDATE` on PostgreSQL) so two
  concurrent status changes serialize.

### 2.2 New read models — `application/pr_membership_service.py`

`PrMembershipService` writes nothing (asserted by test 7c). Reads, all behind
`Permission.USER_READ` (ADMIN and OWNER), self excepted for permissions:

- `list_members` — every `users` row with an active-grant count from one
  grouped query, counts per status, and the actor's own `may_add` /
  `may_change_status` / `may_change_role` flags (the same matrix checks the
  writes make).
- `effective_permissions(user_id, on)` — all 20 `PrCapability` values with a
  provenance: `ROLE` (matrix, via `domain/pr/membership.role_capabilities`),
  `SCOPED_GRANT` (active grants on `on`, from `PrCapabilityService.approval_grants_for`,
  each with its scope), or `NONE`. `is_active` is stated once; for a suspended
  account the rows are what returns on reactivation.
- `responsibilities(user_id)` — counts only: content owned in a non-terminal
  stage, open work items contributed to, unfinished task assignments, KPI
  drafts (and how many are awaiting review), active grants.
- `roles()` — the four roles, active member count, capabilities grouped by
  domain, `assignable` false for `OWNER`.

`domain/pr/membership.py` holds the vocabulary: capability domains and
Vietnamese labels for all 20 capabilities, `role_capabilities(role)`,
`ASSIGNABLE_ROLES = (EMPLOYEE, TEAM_LEAD, ADMIN)`.

### 2.3 Routes — `api/routers/pr_members.py`

Session-authenticated (`CurrentActorDep`), error envelope unchanged.

| Route | Calls |
| --- | --- |
| `GET /api/pr/members` | `PrMembershipService.list_members` |
| `POST /api/pr/members` `{telegram_user_id, role, full_name?, telegram_username?}` | `UserService.add_user` |
| `GET /api/pr/members/{id}` | `PrMembershipService.member` |
| `POST /api/pr/members/{id}/role` `{role}` | `UserService.change_role` |
| `POST /api/pr/members/{id}/deactivate` `{reason?}` | `UserService.suspend` |
| `POST /api/pr/members/{id}/reactivate` | `UserService.enable` |
| `POST /api/pr/members/{id}/revoke` `{reason?}` | `UserService.revoke` |
| `GET /api/pr/members/{id}/effective-permissions?on=` | `PrMembershipService.effective_permissions` |
| `GET /api/pr/members/{id}/responsibilities` | `PrMembershipService.responsibilities` |
| `GET /api/pr/roles` | `PrMembershipService.roles` |

The router holds no membership rule (test 7a): no `has_permission`, no
`user.role =`, no delete. `UserService` is built exactly as the bot builds it
(`UserService(session, AuditService(session))`).

Status mapping as everywhere else: `AuthorizationError` → 403,
`ConflictError` → 409, `ValidationError` → 422, `NotFoundError` → 404.
`enable` on a revoked account has always been a `ValidationError`
(`member_revoked`, 422) and stays so.

Audit: `USER_REGISTERED`, `USER_ROLE_CHANGED`, `USER_SUSPENDED`,
`USER_ENABLED`, `USER_REVOKED` — the rows `UserService` already wrote for
Telegram, now with a web actor.

## 3. Frontend

`/pr/permissions` → **Thành viên & Phân quyền** with `?tab=members|roles|grants`
(default `members`; nav label updated; the 403 hint in `ErrorBox` links to
`?tab=grants`).

- **Thành viên** (`members.tsx`): counts, search / role / status filters
  (client-side over the roster), member cards (mobile-first grid), *Thêm thành
  viên* form (Telegram ID + tên + vai trò; button names the role; explains
  `/start`), per-card actions gated by the server flags and the row's status:
  *Xem quyền hiện tại* (fetched on demand), *Đổi vai trò* (dialog naming both
  roles), *Vô hiệu hóa* (fetches responsibilities first; counts in the dialog;
  reason field), *Kích hoạt lại*, *Loại khỏi PR* (reason field, dialog),
  *Khôi phục*. The owner's card offers none of the three mutations.
- **Vai trò & quyền** (`roles.tsx`): read-only cards per role, capabilities
  grouped by domain, owner marked *Không gán được*, server note printed
  verbatim.
- **Quyền duyệt cấp thêm** (`grants.tsx`): the previous page, unchanged in what
  it sends, with the clarifying sentence "Quyền duyệt cấp thêm chỉ áp dụng trong
  đúng phạm vi đã chọn và không thay đổi vai trò nền của thành viên."
- `lib/labels.ts`: `MEMBER_REASONS` for every reason code above; `ROLES`
  fallback aligned with the server's `ROLE_LABELS` (see §8 for the canonical
  words); labels for the
  `PR_WORK_*`, `PR_PERFORMANCE_REVIEW`, `PR_PUBLICATION_CREATE` capabilities.
- `lib/confirmations.ts`: four member dialogs and six `ACTION_INVENTORY`
  entries. Wording is *Vô hiệu hóa thành viên* / *Loại khỏi PR* — never "team".

## 4. Tests

- `tests/unit/test_pr_membership_admin.py` (32): characterization of the
  Telegram path, roster and roles, add, role change, deactivate / reactivate /
  revoke as terminal, session rejection after deactivation, owner safety,
  effective permissions with provenance, expired grant absent, one-implementation
  source checks.
- `tests/unit/test_user_lifecycle.py` (23) unchanged and green — the Telegram
  behaviour is the same.
- `frontend/tests/membership.test.tsx` (18); `permission-scopes.test.tsx` and
  `pr-admin.test.tsx` updated for the tab structure.

## 5. Invariants kept

Membership ≠ base role ≠ scoped grant. Nothing deletes a user. Grants never
change roles; role changes never touch grants. `OWNER` cannot be assigned,
suspended, revoked or demoted; nobody can change their own role or status.
Content reporting-period logic, Work/KPI rules, M2/M6 formulas, and migration
`0038` are untouched.

## 6. Known limits

- Adding a member requires a Telegram ID (the only identity the system has).
  There is no email/invite-from-web path; the Telegram `/join` invite flow is
  unchanged.
- `PrCapabilityService` itself does not check `actor.active`; the two entry
  gates do. `effective_permissions` therefore reports a suspended member's
  grants (with `is_active: false`) on purpose.
- The self-change guard is unreachable with today's matrix (see §2.1).

## 8. Pre-deploy semantic cleanup

Two product semantics were corrected before release.

### 8.1 Canonical role labels

`domain/identity/labels.py: ROLE_LABELS` is the one source; the web receives
it as `role_label` on every member, role and permission response, and
`frontend/src/lib/labels.ts: ROLES` is a code-only fallback with the same words.

| Role | Label | Was |
| --- | --- | --- |
| `OWNER` | **Chủ sở hữu** | "Trưởng phòng" |
| `ADMIN` | Quản trị viên | unchanged |
| `TEAM_LEAD` | Trưởng nhóm | unchanged |
| `EMPLOYEE` | **Nhân viên** | "Member" |

`OWNER` is workspace ownership, not a job title. "Trưởng phòng" still names the
approval gate (`PR_HEAD_REVIEW`, "Duyệt Trưởng phòng"), the approval stage, and
the person addressed in notifications and access-gate copy - none of those is
the role. Every surface that printed the role label now prints the new word:
`/start`, `/whoami`, refusals, the HR preview's *Người duyệt* line, the audit
trail's `role_label`, the web header, the roster and the roles tab. "Trưởng
phòng" and "Member" remain accepted **input** aliases so old habits still
parse; neither is ever printed for a role. Permissions and capability mappings
are untouched.

### 8.2 Revoked is terminal in phase 1

`UserService.restore` exists as a service method, but no Telegram command,
tool or web surface calls it - it is not a canonical operation. The web route
`POST /members/{id}/restore`, the `Khôi phục` button, its dialog and its
inventory entry were removed. Final semantics:

| From | Action | To |
| --- | --- | --- |
| active | Vô hiệu hóa | suspended |
| suspended | Kích hoạt lại | active |
| active / suspended | Loại khỏi PR | revoked |
| revoked | *none* | terminal |

A revoked row stays in the roster as **"Đã loại khỏi PR"** (the canonical
`status_label`, shared with Telegram), read-only: its history and effective
permissions remain inspectable. `UserService.enable` on a revoked account keeps
its structured refusal (`member_revoked`, 422), now worded "Thành viên này đã
bị loại khỏi PR và không thể kích hoạt lại."

Adding a Telegram id that belongs to a revoked user is refused by
`UserService.add_user` (`member_revoked`, 409, "Thành viên này đã bị loại khỏi
PR. Không thể đăng ký lại tài khoản Telegram này.") - no duplicate row is
created. **Phase 1.1 candidate:** *Khôi phục thành viên đã loại*, built on a
canonical operation exposed to Telegram and web alike, with a precondition
(`revoked` only) that today's `restore` method lacks.

## 7. Phase 2 recommendation

Custom roles: **not warranted by the audit.** The four roles cover the
permission matrix; the only per-person variation the business has asked for is
approval authority in a scope, which scoped grants already model. If a real
need appears, the honest design is a `roles` table with a matrix snapshot and
a migration, not an extension of `users.role` — and it should start with a
requirements document listing the permissions a fifth role would hold that no
existing role does.
