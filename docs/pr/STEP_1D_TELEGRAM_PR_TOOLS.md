# Step 1D — Telegram PR tools

**No Alembic revision.** Step 1D adds no table, no column and no constraint; the
head stays at `0016`. It is a transport layer over services that already exist.

Steps 1A–1C.1 built the PR module and its rules. This step lets a person reach
them by typing a sentence.

## Telegram architecture reused

MeoBot already had every mechanism this needed. Nothing new was invented.

```
Telegram message
  → ConversationService.handle          (existing)
  → LLM plans an ActionPlan             (existing, tool summaries from the registry)
  → PolicyEngine.evaluate               (existing: permission, risk, confirmation)
  → ConfirmationService  /confirm       (existing, for RiskLevel.HIGH)
  → ConversationService._run_tool       (existing: opens ONE transaction)
  → ToolDefinition.execute              (existing: pydantic-validated arguments)
  → pr_* tool handler                   ← Step 1D
  → Pr* application service             (Steps 1C / 1C.1)
  → PostgreSQL
```

| Concern | Existing mechanism reused |
| --- | --- |
| Tool registry & schemas | `ToolRegistry`, `ToolDefinition`, pydantic args with `extra="forbid"` |
| Tool execution | `ConversationService._run_tool` |
| Transaction | `Database.transaction()`, opened by `_run_tool`, session passed in `ToolContext` |
| Coarse permission | `ToolDefinition.required_permission` + `PolicyEngine` |
| Confirmation | `RiskLevel.HIGH` → `ConfirmationService` → `/confirm <token>` |
| Actor | `IdentityService.resolve_actor`, already built at the transport edge |
| Error surfacing | tools raise `MeoBotError`; the conversation handler renders `⛔ {message}` |
| Long messages | `meobot.bot.formatting.split_message` |
| Date phrases | `meobot.domain.hr.schedule.resolve_date` |
| Audit | `AuditService`, written by the PR services |

**No second command framework, no PR-only chatbot, no new confirmation state
machine.**

## Intent → service → authority

No row's authority is Telegram.

| Telegram action | Application service | Business authority |
| --- | --- | --- |
| "Tạo nội dung" | `PrContentService.create_content` | `PrContentService` + `PrCodeService` |
| "Xem CNT-…" | `PrQueryService` | `PrQueryService` |
| "Sửa nội dung" | `PrContentService.revise_content` | version policy in `PrContentService` |
| "Đưa sang AI Review" | `PrContentWorkflowService.request_transition` | workflow policy (`MANUAL` edges) |
| "Duyệt" | `PrApprovalService.record_decision` | `PrApprovalService` + PR capability policy |
| "Yêu cầu sửa" / "Từ chối" | `PrApprovalService.record_decision` | same |
| "Chờ tôi duyệt" | `PrCapabilityService` + `PrQueryService` | capability grants |
| "Giao task" | `PrTaskService` | task workflow matrix |
| "Task quá hạn" | `PrQueryService.list_overdue_tasks` | `PrQueryService` |
| "Phân công kênh" | `PrChannelService.assign_user` | assignment-overlap policy |
| "Cho quyền Team Lead" | `PrCapabilityService.grant` | PR capability policy (`user.role.manage`) |
| "Đã đăng TikTok rồi" | `PrPublicationService.register_publication` | publication policy |

## Tools implemented — 34

**Content (6)** — `pr.content.create`, `pr.content.get`, `pr.content.list`,
`pr.content.revise`, `pr.content.transition`, `pr.ai_review.submit`

**Review (5)** — `pr.review.pending`, `pr.review.context`, `pr.review.approve`,
`pr.review.request_revision`, `pr.review.reject`

**Tasks (10)** — `pr.task.create`, `.get`, `.list`, `.overdue`, `.assign`,
`.start`, `.block`, `.submit_review`, `.request_revision`, `.complete`, `.cancel`

**Channels (7)** — `pr.channel.list`, `.get`, `.assignments`, `.create`,
`.update`, `.assign`, `.close_assignment`

**Capabilities (4)** — `pr.capability.list_for_user`, `.users_for`, `.grant`,
`.revoke`

**Publication (1)** — `pr.publication.register`

### Content commands

`pr.content.create` takes a brand, a title and optional topic/hook/brief/script/
priority/planned date/channels/owner. **There is no `code` argument** — the
schema forbids it and `PrCodeService` allocates `CNT-YYYY-nnnnnn` inside the same
transaction.

`pr.content.transition` exposes **manual edges only**. Asking it for
`AI_REVIEW → TEAM_LEAD_REVIEW`, `TEAM_LEAD_REVIEW → HEAD_REVIEW` or
`HEAD_REVIEW → APPROVED` is refused — by the workflow policy, not by the tool,
so the refusal holds for every client.

`pr.content.revise` requires a version. When the caller does not state one, the
adapter reads the current version and passes it as `expected_version`. **It never
retries after `PrStaleVersionError`**; the user is told both version numbers and
decides.

### Review commands

`pr.review.pending` asks `PrCapabilityService.capabilities_for_actor` which gates
this person may act at, then lists content standing at exactly those stages.
**A `TEAM_LEAD` role with no grant sees nothing.** Each row carries code, title,
stage, version, the AI result for that version and a warning count.

`pr.review.context` renders `PrQueryService.get_content_review_context` in
pieces — result, score, summary, issues, suggestions, policy flags, approval
history:

```
🤖 AI Review: ⚠️ PASS WITH WARNINGS
Điểm: 84/100
Hai điểm cần lưu ý.

Cảnh báo:
1. [WARNING] Hook hơi nhạt.
```

`PASS_WITH_WARNINGS` has its own label and icon. There is no branch anywhere
that turns it into "AI đã duyệt".

`pr.review.approve` **derives** `approval_stage` from where the content stands
(`STAGE_APPROVAL_GATES`) — the caller cannot supply a mismatched one. Team Lead,
Head and Internal capability gates and version safety are all enforced inside
`PrApprovalService`. Reviewer separation was too, until Step 1F.2.2 removed it -
one person holding both grants may now approve at both gates, and the tool walks
them one call at a time as it always did.

`pr.review.request_revision` requires a comment (`min_length=3`): "sửa lại đi"
with no reason gives the author nothing to act on.

## AI-review handoff — the limitation

`pr.ai_review.submit` moves `SCRIPTING → AI_REVIEW` through
`PrContentWorkflowService` and **does nothing else** (and since Step 1D.1 it
asks for confirmation first):

* it calls no LLM;
* it writes no `pr_ai_reviews` row;
* its arguments contain no `result`, `score`, `summary` or `model_name` — a
  verdict cannot be supplied through it;
* it says *"đã được chuyển sang bước AI Review và đang chờ được review"* and
  never *"AI đang phân tích"*, because no worker is running.

Actual AI review execution is **Step 1F**. The bot's own conversational LLM is
not the PR reviewer.

## Task commands

One tool per transition (`start`, `block`, `submit_review`,
`request_revision`, `complete`, `cancel`) so the router picks a verb rather than
an enum value. Each hands its target status to `PrTaskService.change_status`;
**the transition matrix is not copied into the tools** and an illegal move
surfaces the service's error naming what was possible.

Task codes come from `PrCodeService`. Deadlines accept "thứ Sáu", "hôm nay",
"31/8" or ISO, resolved by the existing `resolve_date`.

## Channel commands

`pr.channel.assign` and `.close_assignment` hand dates to `PrChannelService`,
which owns the closed-interval overlap rule. **The Telegram layer never compares
two dates.**

## Capability administration

Required because 0016 deploys secure-by-default: after migration nobody holds a
review right, so without these a fresh deployment has no path to its first
approval.

Users say *"Cho Linh quyền Team Lead Review"*. A fixed phrase table maps
`"team lead review"`, `"duyệt trưởng nhóm"`, `"head review"`,
`"trưởng phòng duyệt"`, `"duyệt nội bộ"` onto the three enum members; the enum
is what crosses into the service. An unrecognised phrase is refused, not guessed.

Granting is gated on `Permission.USER_ROLE_MANAGE` (owner-only) **inside
`PrCapabilityService`** — no capability guards itself, so nobody can bootstrap
themselves into reviewing. Only the three grant-backed review capabilities can
be granted; asking for `PR_CONTENT_CREATE` is refused as ungrantable.

## Publication registration

`pr.publication.register` records a fact. It resolves content and channel,
collects `published_at` / `platform_post_id` / `url`, and calls
`PrPublicationService`. **No platform API is called and no code path to one
exists.** An untargeted channel or content that is not ready surfaces the
service's error.

## Actor and user resolution

The `Actor` is built by the existing identity mechanism at the transport edge,
from `users`. Every PR authorization decision uses `users.id`, `Actor.role` and
capability grants.

**Telegram identity is never an authorization identifier.** A source sweep
(`test_no_pr_tool_authorizes_by_a_telegram_identity`) asserts that no PR tool
module even mentions `telegram_user_id`, `telegram_username` or
`telegram_chat_id`. An actor with no `users` row is refused on every write.

Names are resolved by `PrPeopleResolver` (application layer, so the future web
client reuses it): active users only, accent-folded, three tiers — whole name,
whole word, substring — stopping at the first tier that hits. **It never
creates anybody**, and a missing name is reported.

## Ambiguity handling

| Reference | Behaviour |
| --- | --- |
| `CNT-2026-000123` | exact resolve |
| a description | search; one hit resolves, several ask, none reports |
| a person's name | one match resolves, several ask, none reports |
| a channel or brand | same |
| an unparseable date | asks rather than assuming |

Nothing guesses. Ambiguity comes back as a `ToolExecutionError` carrying a
numbered list and the candidate codes in `details`.

## Confirmation behaviour

Reuses `PolicyEngine` + `ConfirmationService`: `RiskLevel.HIGH` produces a
`/confirm <token>` round trip. **No PR-specific confirmation flow was written.**

High-risk (confirmed): `pr.review.approve`, `pr.review.request_revision`,
`pr.review.reject`, `pr.capability.grant`, `pr.capability.revoke`,
`pr.task.cancel`, `pr.channel.close_assignment`, and — since **Step 1D.1** —
`pr.ai_review.submit`.

> `pr.ai_review.submit` was `MEDIUM` in Step 1D. Step 1D.1's routing corpus
> showed it is one plausible misroute away from "AI review của bài này thế
> nào?", and entering `AI_REVIEW` takes the draft out of `EDITABLE_STAGES` — the
> author cannot revise it again without a reviewer sending it back. An
> irreversible effect one wrong word away needed the confirmation step. See
> [`STEP_1D1_TELEGRAM_ROUTING_VALIDATION.md`](STEP_1D1_TELEGRAM_ROUTING_VALIDATION.md).

Read tools are `RiskLevel.LOW` and confirm nothing.

**None of them is `destructive`.** In this repository that flag means "deletes
data", and `PolicyEngine` is built with `deny_destructive=True` — a destructive
tool is refused outright rather than confirmed. Rejecting cancels content,
revoking closes a grant and cancelling a task is a status change; none removes a
row, so `RiskLevel.HIGH` is the correct and sufficient protection.

## Error mapping

`meobot.tools.pr_errors` is the **one** place a PR error becomes Vietnamese.
Translation is keyed on `MeoBotError.code`, never on message text; an unknown
code produces a neutral refusal rather than leaking English internals. The
original code stays in `details["pr_error_code"]` for the audit trail.

| Code | What the user reads |
| --- | --- |
| `pr_stale_version` | "Nội dung này vừa được cập nhật. Phiên bản hiện tại là v5; thao tác của bạn dựa trên v4." |
| `pr_reviewer_separation` | "Bạn không thể đồng thời duyệt Trưởng nhóm và Trưởng phòng cho cùng một phiên bản nội dung." |
| `pr_forbidden` (`missing_grant`) | "Bạn chưa được cấp quyền duyệt ở bước này." |
| `pr_invalid_transition` | names the current stage and what *is* reachable |
| `pr_assignment_overlap` | quotes the overlapping period |
| `pr_ai_review_required` | "Phiên bản v5 chưa có kết quả AI review nên chưa thể duyệt." |

The services stay language-neutral.

## Transaction behaviour

`ConversationService._run_tool` opens **one** `Database.transaction()` per tool
call and puts the session in `ToolContext`. `pr_services(context)` builds every
PR service on that session, so code allocation, entity creation, workflow
transition and audit all commit together or not at all.

No PR tool calls `commit`, `rollback` or `Database.transaction` — asserted by
`test_no_pr_tool_opens_or_commits_a_transaction`.

## Audit

The PR services already audit every business action (`pr.content.created`,
`pr.approval.recorded`, …). Step 1D adds **no duplicate PR audit rows**; the
existing `tool.executed` telemetry that `_run_tool` writes for every tool
continues unchanged.

## Buttons and callbacks

Not implemented. MeoBot's tool architecture routes natural language through the
`ConversationService`; inline keyboards exist for dispatch and announcements but
not as a tool-execution surface, and wiring a parallel callback path into PR
approvals would have created exactly the second implementation this step was
told to avoid. When a callback surface is added it must call
`PrApprovalService` through the same handlers.

## Deferred

* **Notifications** — no proactive PR messages, no "notify Head when Team Lead
  approves", no scheduled reminders. The outbox was not extended.
* **AI review execution** — Step 1F.
* **Web UI / HTTP API** — client #2, later. `test_no_web_or_http_surface_was_added`
  asserts none appeared.
* **Reporting, Excel, metric collectors, Google Sheets, auto-publishing.**
* **Inline approval buttons** — see above.

## Running the tests

```bash
uv run pytest tests/unit/test_pr_telegram_tools.py \
              tests/unit/test_pr_telegram_architecture.py
```
