# Step 1C — PR application services and workflow engine

Alembic revision **`0015_pr_content_versions`** (`0014` → `0015`), plus eight
application services and the domain policy they enforce.

Steps 1A, 1A1 and 1B built tables. This step is the first that *does* anything:
it is where a content item is created, revised, reviewed, approved, published
and measured. Nothing above it may write a PR table directly.

Read [`STEP_1A_PR_CORE_FOUNDATION.md`](STEP_1A_PR_CORE_FOUNDATION.md) and
[`STEP_1A1_AI_REVIEW_WORKFLOW.md`](STEP_1A1_AI_REVIEW_WORKFLOW.md) first — every
schema convention below is theirs.

> **Superseded in two places.** Step 1C.1
> ([`STEP_1C1_AUTHORIZATION_AND_CODES.md`](STEP_1C1_AUTHORIZATION_AND_CODES.md),
> revision `0016`) replaced this step's capability vocabulary and removed the
> caller-supplied `code` from every creation command. The sections *Authorization
> boundary* and *Deferred code generation* below are rewritten to match;
> everything else here is current.

## Multi-client boundary

These services are **transport-agnostic shared business logic**. They will be
called by the Telegram bot, a future web admin UI, CLI/admin scripts and
background jobs, and all of them must get the same rules.

| Rule | How it is kept |
| --- | --- |
| No Telegram-formatted strings leave a PR service | Errors carry English messages and a structured `details` mapping; results are dataclasses of domain objects |
| No Telegram identifier is a business identifier | Every person reference is `users.id`; `Actor.telegram_user_id` is never used for authorization or lookup |
| No HTTP or web concern enters the domain | Nothing in `meobot.domain.pr` or the PR services imports a web framework |
| Validation, authorization, workflow and transactions live in the service layer | Handlers pass a command in and render the result out; a test greps the service package to prove no other module writes `workflow_stage` |
| One set of methods serves every client | The bot and a future web API call the same eight classes |

**This is a deliberate departure from `HrRequestService`**, which embeds the
exact Vietnamese sentence a person reads. That was right for a Telegram-only
feature and is wrong here: the wording belongs to whichever client is talking
to the human.

## Service boundaries

| Service | Owns | Never does |
| --- | --- | --- |
| `PrContentService` | content items, `pr_content_versions`, content targets at creation | change `workflow_stage` |
| `PrContentWorkflowService` | **every** write to `pr_content_items.workflow_stage` | write reviews or approvals |
| `PrAiReviewService` | `pr_ai_reviews` | write `pr_approval_events`; move content for a non-gating review |
| `PrApprovalService` | `pr_approval_events` | update a past decision; refuse a human on the AI's say-so |
| `PrTaskService` | `pr_tasks`, `pr_task_assignments` | derive task status from the content workflow |
| `PrChannelService` | channels, brands, `pr_channel_assignments` | change a brand code; allow overlapping assignments |
| `PrPublicationService` | `pr_publications`, target status | call a platform API |
| `PrQueryService` | reads only | write anything; return rendered strings |

All eight follow the constructor convention every MeoBot service uses:
`(session, audit, …)`. **The caller owns the transaction boundary**, exactly as
`HrRequestService` and `ScriptService` do — services `flush()` and never
`commit()`.

## Transaction ownership

**One application command = one transaction**, opened by the caller with
`Database.transaction()`:

```python
async with database.transaction() as session:
    audit = AuditService(session)
    capabilities = PrCapabilityService(session, audit)          # Step 1C.1
    codes = PrCodeService(session, settings)                    # Step 1C.1
    content = PrContentService(session, audit, capabilities, codes)
    workflow = PrContentWorkflowService(session, audit, capabilities)
    ai = PrAiReviewService(session, audit, content, workflow)
    await ai.record_review(actor=actor, request_id=request_id, command=command)
# commits here, once
```

A gating `FULL_REVIEW` therefore commits three things together or none of them:
the `pr_ai_reviews` row, the stage change, and the audit events. No service
issues a second `commit()` inside a business command, and a failure anywhere
leaves nothing behind — asserted on real PostgreSQL by
`test_a_failed_command_leaves_no_partial_state`.

## Content versioning

`pr_content_versions` is append-only: `id`, `content_id`, `version_no`, `title`,
`topic`, `hook`, `brief`, `script_text`, `change_note`, `created_by_user_id`,
`created_at`. No `updated_at`. `UNIQUE (content_id, version_no)`,
`version_no >= 1`, non-empty title, both foreign keys `ON DELETE RESTRICT`.

**The mutable columns on `pr_content_items` stay and remain the current
projection.** No column was removed. `PrContentService` writes the projection
and the new version in the same transaction, so they cannot disagree.

`script_text` lives **only** on the version row and gets no projection column:
the script is what a reviewer reads in full, and a second mutable copy would
create a second answer to "what does this draft say".

| Operation | Behaviour |
| --- | --- |
| `create_content` | code + item + version 1 + requested targets, atomically; stage `IDEA` |
| `revise_content` | locks the content row, requires `expected_version`, writes N+1, moves the projection |
| stale `expected_version` | `PrStaleVersionError` — never a silent merge |
| editable stages | `IDEA`, `BRIEFING`, `SCRIPTING` only |
| old versions | never updated, by any code path |

Revision stops at `AI_REVIEW` because from there on somebody is forming a
judgement about a specific draft. Content that needs changing later is *sent
back* by a reviewer, which returns it to `SCRIPTING` and leaves a record of why.

## Complete workflow transition matrix

Every edge, and which kind of driver may take it. `M` = manual (a person, via
`PrContentWorkflowService.request_transition`), `A` = an AI verdict, `H` = a
human decision. Anything not listed is refused.

| From | To | Driver |
| --- | --- | --- |
| `IDEA` | `BRIEFING` | M |
| `BRIEFING` | `SCRIPTING` | M |
| `SCRIPTING` | `AI_REVIEW` | M |
| `AI_REVIEW` | `TEAM_LEAD_REVIEW` | A (`PASS`, `PASS_WITH_WARNINGS`) |
| `AI_REVIEW` | `SCRIPTING` | A (`REVISION_REQUIRED`) |
| `TEAM_LEAD_REVIEW` | `HEAD_REVIEW` | H (`APPROVED`) |
| `TEAM_LEAD_REVIEW` | `SCRIPTING` | H (`REVISION_REQUIRED`) |
| `HEAD_REVIEW` | `APPROVED` | H (`APPROVED`) |
| `HEAD_REVIEW` | `SCRIPTING` | H (`REVISION_REQUIRED`) |
| `APPROVED` | `PRODUCTION` | M |
| `PRODUCTION` | `INTERNAL_REVIEW` | M |
| `INTERNAL_REVIEW` | `READY_TO_PUBLISH` | H (`APPROVED`) |
| `INTERNAL_REVIEW` | `PRODUCTION` | H (`REVISION_REQUIRED`) |
| `READY_TO_PUBLISH` | `PUBLISHED` | M (also driven by the first publication) |
| `PUBLISHED` | `ARCHIVED` | M |
| `IDEA`, `BRIEFING`, `SCRIPTING`, `AI_REVIEW`, `TEAM_LEAD_REVIEW`, `HEAD_REVIEW`, `APPROVED`, `PRODUCTION`, `INTERNAL_REVIEW`, `READY_TO_PUBLISH` | `CANCELLED` | M, and H at the three gates (`REJECTED`) |

`PUBLISHED`, `ARCHIVED` and `CANCELLED` **cannot** be cancelled.
`ARCHIVED` and `CANCELLED` are terminal — nothing moves out of them. There are
no self-edges.

**Why the driver matters.** `TEAM_LEAD_REVIEW → APPROVED` does not exist at all;
`HEAD_REVIEW → APPROVED` exists but only as `H`. Asking for it through
`request_transition` is refused, so content cannot reach `APPROVED` without a
`pr_approval_events` row being written in the same transaction. That is the
enforcement, not a convention.

Edges carry a **set** of drivers because some are genuinely reachable two ways:
a reviewer rejecting work and somebody abandoning it both reach `CANCELLED`.

## AI gate semantics

`PrAiReviewService.record_review` requires, in order: the capability, non-blank
provenance, a score within 0–100 if present, the content at stage `AI_REVIEW`,
and `reviewed_version == the latest immutable version`.

| Review type | Gating | Effect |
| --- | --- | --- |
| `SCRIPT_QUALITY` | no | stored, visible to the reviewer, moves nothing |
| `POLICY_COMPLIANCE` | no | stored, visible to the reviewer, moves nothing |
| `BRAND_TONE` | no | stored, visible to the reviewer, moves nothing |
| `FULL_REVIEW` | **yes** | `PASS` → `TEAM_LEAD_REVIEW`; `PASS_WITH_WARNINGS` → `TEAM_LEAD_REVIEW`; `REVISION_REQUIRED` → `SCRIPTING` |

AI review **never** creates a `pr_approval_events` row, never reaches
`APPROVED` (no such edge exists for any driver), and never names a person —
`pr_ai_reviews` has no `reviewer_user_id` and no foreign key to `users`.

### Version safety

> Version 4 → `FULL_REVIEW` `PASS` → `TEAM_LEAD_REVIEW` → `REVISION_REQUIRED` →
> `SCRIPTING` → version 5.
>
> **Version 5 must receive a new `FULL_REVIEW` before it can reach
> `TEAM_LEAD_REVIEW` again.**

Three independent mechanisms make that true, and the end-to-end walk is
`test_a_pass_for_version_four_cannot_authorise_version_five`:

1. `record_review` refuses any `reviewed_version` that is not the latest, so
   version 4's PASS cannot be re-filed against version 5.
2. `AI_REVIEW → TEAM_LEAD_REVIEW` is the **only** edge into that stage, and only
   a gating review drives it.
3. `PrApprovalService` re-checks at the point of the write that the current
   version has a `FULL_REVIEW` on file (`PrAiReviewRequiredError`).

Version 4's PASS is not deleted. It stays on file, still says `PASS`, and is
still true — *about version 4*.

## Human review semantics

`PrApprovalService.record_decision` checks: the capability for that gate, the
gate matches the current stage, the reviewer exists, `version_reviewed` is
current, and (at `TEAM_LEAD_REVIEW`) a gating AI verdict exists for that draft.

| Content stage | Required `approval_stage` | `APPROVED` | `REVISION_REQUIRED` | `REJECTED` |
| --- | --- | --- | --- | --- |
| `TEAM_LEAD_REVIEW` | `TEAM_LEAD_REVIEW` | `HEAD_REVIEW` | `SCRIPTING` | `CANCELLED` |
| `HEAD_REVIEW` | `HEAD_REVIEW` | `APPROVED` | `SCRIPTING` | `CANCELLED` |
| `INTERNAL_REVIEW` | `INTERNAL_REVIEW` | `READY_TO_PUBLISH` | `PRODUCTION` | `CANCELLED` |

Every decision **inserts** a row. Nothing updates one. A reviewer who changes
their mind adds a second event, and both stay true at the version each was made
against.

**A Team Lead may reject work the AI passed, and approve work it wanted
revised.** The AI verdict is evidence in front of a person; a service that
refused a human decision on a model's opinion would invert the responsibility
this module exists to keep. Team Lead and Head review remain mandatory: there
is no path from `AI_REVIEW` to `APPROVED`.

## Task transition matrix

| From | To |
| --- | --- |
| `TODO` | `IN_PROGRESS`, `CANCELLED` |
| `IN_PROGRESS` | `BLOCKED`, `IN_REVIEW`, `DONE`, `CANCELLED` |
| `BLOCKED` | `IN_PROGRESS`, `CANCELLED` |
| `IN_REVIEW` | `REVISION_REQUIRED`, `DONE`, `CANCELLED` |
| `REVISION_REQUIRED` | `IN_PROGRESS`, `CANCELLED` |
| `DONE` | *(terminal)* |
| `CANCELLED` | *(terminal)* |

Every other ordered pair is refused — including `TODO → DONE`, which would skip
the record that anybody did the work, and `DONE → IN_PROGRESS`, which would make
"done" mean "done for now". Reopening is a new task. The test enumerates all
49 pairs.

**Task status is never derived from the content workflow.** Approving a script
does not finish the task of writing it. `completed_at` is stamped on `DONE` and
never cleared, because nothing leaves `DONE`.

Unassigning removes somebody who has **not** completed their part. A completed
assignment is history and stays.

## Assignment-overlap semantics

**Closed intervals: `[effective_from, effective_to]`, where `effective_to` is
the last day in force.** `NULL` means open-ended.

Chosen rather than half-open because the schema already means this: the `CHECK`
permits `effective_from == effective_to` and Step 1A calls that row "a cover for
somebody on leave" — one day. Half-open `[from, to)` would silently reinterpret
that same row as covering nothing.

Consequence, stated plainly: **a handover cannot share a day.** `[Jan 1, Jun 30]`
and `[Jun 30, Dec 31]` overlap on the 30th and are refused; the outgoing row must
end on the 29th. On the 30th, under closed reading, two people would both be the
channel owner.

| Case | Database | Service |
| --- | --- | --- |
| two **open** rows, same (channel, user, role) | refused (partial unique index) | refused |
| two **closed** rows overlapping | accepted | **refused** |
| closed row overlapping an open one | accepted | **refused** |
| consecutive ranges (`…Jun 30`, `Jul 1…`) | accepted | accepted |
| two different roles over the same dates | accepted | accepted |

Overlap is only ever evaluated within one `(channel_id, user_id,
assignment_role)` triple. Two *people* holding the same role over the same dates
is a deliberate arrangement, not a clash.

**Brand codes are immutable through the service**: `update_brand` raises
`PrImmutableFieldError`, and `UpdateChannelCommand` has no `code` field at all.

## Publication semantics

`register_publication` records a **fact**, not an action. No platform API is
called; there is no code path to one.

* the content must be `READY_TO_PUBLISH` or already `PUBLISHED`;
* the channel must already be a `pr_content_targets` row — publishing to an
  unplanned channel is refused, because the target row is what every
  channel-level report joins through;
* it inserts `pr_publications` and sets that target to `PUBLISHED`;
* **the first** publication moves `READY_TO_PUBLISH → PUBLISHED`;
* additional targets publish while the content is already `PUBLISHED` and change
  no stage — a piece that goes to three channels becomes published once.

`url` is stored for a person to click and is never a key. `platform_post_id` is
what a future collector matches on.

## `MEASURED` is retired

**Step 1F.2.3f.5.** The lifecycle tail used to be `PUBLISHED → MEASURED →
ARCHIVED`, with `PUBLISHED → MEASURED` refused until at least one
`pr_post_metric_snapshot` existed for the content. Both edges are gone.

Two things were wrong with it. Archiving was reachable **only through a
measurement claim**, so a piece nobody had measured could never be put away. And
the stage asserted an editorial judgement — *somebody has looked at how this
performed* — that the schema could only approximate as *a number exists*.

Publication is now the terminal normal state, and `PUBLISHED → ARCHIVED` is one
deliberate step from it.

**Analytics are untouched.** `pr_post_metric_snapshots`, the channel sync and
everything that reads them are unchanged: recording numbers never required the
stage, and it still does not. The stage was a claim *about* measuring, not the
measuring.

`PrWorkflowStage.MEASURED` **remains a member of the enum**, deliberately.
`pr_content_transition_events` stores stages by value, so a history row naming it
must still deserialise, and a stale client's request must parse far enough to be
refused by name. What retires it is `RETIRED_STAGES`: no edge of the matrix, no
group, no lane, no filter. A request for it fails with
`reason = "measured_stage_retired"` and is **never** silently remapped.

No migration: `workflow_stage` is `varchar(30)` with no CHECK constraint and no
native enum, verified against a migrated database.

## Concurrency strategy

`SELECT … FOR UPDATE` on the aggregate root row, taken by the command that is
about to write, following the dialect check `ReminderService.due_batch` already
uses. `meobot.application.pr_support.lock_row` degrades to a plain `get` on
SQLite, which has no such clause and serialises writers anyway.

**No `skip_locked`**, unlike the outbox. A worker claiming messages wants to step
over a locked row; a workflow command wants the opposite — to wait, then re-read
the stage it is validating against. Skipping would let two commands each validate
against a stage that was true when they started and false when they wrote.

**No distributed locking**, and none is needed: every command's contention is on
one row in one database.

Proved on real PostgreSQL, not asserted:

* `test_two_concurrent_transitions_do_not_both_succeed` — two callers move the
  same content from `SCRIPTING`; exactly one succeeds and the other is refused
  by the matrix after re-reading.
* `test_two_concurrent_revisions_do_not_produce_two_version_twos` — exactly one
  version 2 exists afterwards.

## Emitted events

PR domain events are appended to the **existing audit trail** through
`AuditService`, on the same session as the change, using `AuditAction` codes:

`pr.content.created` · `pr.content.version_created` · `pr.content.stage_changed`
· `pr.ai_review.recorded` · `pr.approval.recorded` · `pr.task.created` ·
`pr.task.assigned` · `pr.task.unassigned` · `pr.task.status_changed` ·
`pr.channel.created` · `pr.channel.updated` · `pr.channel.assigned` ·
`pr.channel.assignment_closed` · `pr.publication.registered`

### Why not the transactional outbox

MeoBot's outbox (`outbound_messages`) is a **notification** outbox, not a
general event bus. Every row requires a `telegram_chat_id`, a `template_key` and
a `template_version`, and `NotificationEvent` is a closed set of things that get
*delivered to somebody*.

Step 1C sends nothing to anybody. There is no destination to name and no
template to render, so enqueuing rows there would either produce messages nobody
asked for or leave undeliverable rows forever. No second event framework was
created either — the audit trail is what this repository already has for "this
happened, durably, in the same transaction", and its `entity.verb` action codes
are exactly the shape the requested event names take.

When PR notifications are specified, they enqueue from the same transaction
alongside these rows. Nothing here has to change for that to work.
`test_no_pr_service_sends_a_telegram_message` asserts the outbox stays empty
today.

## Authorization boundary

Reuses MeoBot's identity and permission model. **No PR staff identity exists**,
no `Permission` member was added, and no role-to-permission set was modified.
Every human reference is `users.id`; Telegram usernames are never consulted.

Since **Step 1C.1**, authorization is a conjunction of two conditions:

1. the actor's role carries the capability's **baseline permission**, from the
   existing matrix;
2. for the three review gates only, an **active grant** exists in
   `pr_user_capabilities` for that person.

A grant narrows and never widens — both must hold — so nobody gains an ability
their role did not carry. The ten capabilities are
`PR_CONTENT_CREATE`, `PR_CONTENT_EDIT`, `PR_TEAM_LEAD_REVIEW`,
`PR_HEAD_REVIEW`, `PR_INTERNAL_REVIEW`, `PR_TASK_MANAGE`, `PR_CHANNEL_MANAGE`,
`PR_PUBLICATION_REGISTER`, `PR_CONTENT_CANCEL`, `PR_CONTENT_TRANSITION`. Reads
and AI-review recording are gated on `script.read` and `script.review`
directly. The full mapping table is in Step 1C.1's document.

### The ambiguity Step 1C reported is resolved

Step 1C reported that the four roles could not distinguish a Team Lead from a
Head, because `TEAM_LEAD` holds both `script.review` and `script.approve`.
`pr_user_capabilities` resolves it: the two gates require two different grants.

Step 1C.1 additionally forbade one person from recording the successful
`APPROVED` at both gates for one version. **Step 1F.2.2 removed that**; see
`STEP_1F22_CONTENT_VIEWS_AND_REVIEW_ROLES.md`. The two-grant requirement, which is
what actually resolves the ambiguity Step 1C reported, is unchanged.

There is **no admin override**, and none was added; see Step 1C.1.

## Generated codes

**Superseded by Step 1C.1.** Codes are now generated server-side and the `code`
field was removed from `CreateContentCommand`, `CreateTaskCommand`,
`CreateChannelCommand` and `RegisterPublicationCommand` — a caller cannot supply
one.

`PrCodeService` allocates `CH-0001`, `CNT-YYYY-000001`, `TSK-YYYY-000001`,
`PUB-YYYY-000001` and `ISS-YYYY-000001` from `pr_code_counters` with a single
`INSERT … ON CONFLICT … DO UPDATE … RETURNING`, in the same transaction as the
entity it names. No `MAX(code) + 1` exists anywhere, and a test sweeps the
package to keep it that way. Codes remain immutable once written.

## Service errors

All subclass the existing `meobot.core.errors` hierarchy, so transports that
have never heard of the PR module still map them correctly. All carry a
structured `details` mapping.

| Condition | Error | Base | `code` |
| --- | --- | --- | --- |
| PR entity not found | `PrNotFoundError` | `NotFoundError` | `pr_not_found` |
| validation failure | `PrValidationError` | `ValidationError` | `pr_validation_error` |
| invalid workflow transition | `PrWorkflowTransitionError` | `WorkflowStateError` | `pr_invalid_transition` |
| stale content version | `PrStaleVersionError` | `ConflictError` | `pr_stale_version` |
| review version mismatch | `PrReviewVersionMismatchError` | `ConflictError` | `pr_review_version_mismatch` |
| required AI review missing | `PrAiReviewRequiredError` | `WorkflowStateError` | `pr_ai_review_required` |
| approval stage mismatch | `PrApprovalStageMismatchError` | `WorkflowStateError` | `pr_approval_stage_mismatch` |
| permission denied | `PrPermissionDeniedError` | `AuthorizationError` | `pr_forbidden` |
| channel assignment overlap | `PrAssignmentOverlapError` | `ConflictError` | `pr_assignment_overlap` |
| immutable field modification | `PrImmutableFieldError` | `ValidationError` | `pr_immutable_field` |
| conflict / duplicate operation | `PrConflictError` | `ConflictError` | `pr_conflict` |

## Read side

`PrQueryService` returns dataclasses of domain objects. Nothing formats a
message.

`get_content_review_context` returns the AI verdict **in pieces** — `ai_result`,
`ai_score`, `ai_summary`, `ai_issues`, `ai_suggestions`, `ai_policy_flags`,
`ai_has_warnings` — alongside the current version, targets, tasks, every AI
review of that draft, and the full human approval history.

**There is no `ai_passed` boolean, and there must not be.** `PASS` and
`PASS_WITH_WARNINGS` both let content reach a human, which is exactly why
flattening them would be the most damaging simplification available: the
warnings are the reason the second value exists, and a reviewer who never sees
them is reviewing with less information than the machine had.

The gating review returned is the newest `FULL_REVIEW` **for the current
version**. After a rewrite there is none, and this reports `None` rather than
the previous draft's verdict.

## Out of scope

Not implemented and not started: LLM/OpenAI/Anthropic calls, AI prompt
templates, automatic AI review jobs, Telegram handlers or tools, Telegram
notifications, schedulers, Excel generation, periodic reports,
Facebook/TikTok/YouTube collectors, Google Sheets, legacy Excel imports, staff
scoring, automatic publication to social platforms, and the web UI or HTTP
endpoints. Docker and deployment files are untouched.

`test_no_pr_service_imports_an_llm_or_telegram_client` asserts none of the
corresponding imports arrived with this step.

## Running the tests

Offline:

```bash
uv run pytest tests/unit/test_pr_workflow_policy.py tests/unit/test_pr_application_services.py
```

Against a PostgreSQL you are willing to have scratch databases created in and
dropped from:

```bash
export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/meobot_test
uv run pytest tests/integration/test_pr_application_migrations.py -m integration
```

Each fixture creates its own uniquely-named `meobot_pr1c_*` database and drops
that one afterwards. Nothing is ever run against NAS or production.
