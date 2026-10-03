# Step 1F.2.3 — Content lifecycle controls, production ownership, internal review

> **Superseded in part by Step 1F.2.3a.** Section A below describes a *soft*
> delete that shipped and was withdrawn one step later. Deletion is now permanent
> aggregate removal, and the authoritative description is
> [`STEP_1F23A_PERMANENT_DELETE.md`](STEP_1F23A_PERMANENT_DELETE.md). Everything
> else in this document - production ownership, submissions, internal review -
> is current.

Three changes, one theme: the half of the workflow **after** the script is
approved was vocabulary without machinery. `PRODUCTION`, `INTERNAL_REVIEW` and
`PR_INTERNAL_REVIEW` all existed in the enums and the transition matrix since
Step 1A/1C.1; nothing recorded who was producing a piece, nothing recorded the
file they produced, and the third review gate had no way to say which cut it had
judged. And there was no way to remove a piece of content at all.

1. **Deleting content**, with a rule that distinguishes a member removing their
   own untouched draft from a lead ending somebody else's work.
2. **Production ownership** — a nullable producer, management assignment, member
   self-claim, and a concurrency rule that produces exactly one winner.
3. **Production submissions and internal review** — an append-only record of each
   cut handed over, and an internal-review decision that names the cut it judged.

Nothing in the canonical workflow moved. No stage was added, removed or skipped,
and `CANCELLED` remains what it was.

---

## A. What "delete" means, exactly

**A soft delete, plus a cancellation where the stage allows one.** The button
says *Xóa nội dung*; the row survives.

`PrContentLifecycleService.delete_content`, in one transaction:

1. sets `deleted_at`, `deleted_by_user_id`, `deleted_reason` on
   `pr_content_items`;
2. if the current stage is in `CANCELLABLE_STAGES`, moves the item to
   `CANCELLED` **through `PrContentWorkflowService.apply`** — the same matrix,
   the same lock, the same `pr.content.stage_changed` event as any other
   transition;
3. writes `pr.content.deleted` to the audit trail with the actor, the reason and
   the stage it was at.

### Why not a hard delete

* **It cannot be done.** Every PR foreign key is `ON DELETE RESTRICT` by Step
  1A's design, so a `DELETE` fails against the item's versions, AI verdicts,
  approvals, tasks, targets, publications and production submissions.
* **It should not be.** Making it succeed means `CASCADE`, and then deleting one
  content item destroys approval events people signed —
  `pr_approval_events` has no `updated_at` and nothing updates it precisely
  because "who decided what, when, against which version" may not be edited
  afterwards. A cascade would edit it in the most complete way available.

Everything listed in the specification's §5 survives a delete: content versions,
AI reviews, policy-pack associations, human approvals, workflow history in the
audit trail, tasks, production submissions, internal review history, audit
events.

### What deletion changes

| | Before | After |
| --- | --- | --- |
| Lists (`/contents`, `/contents/board`, dashboard counts) | present | **absent** — `deleted_at IS NULL` is part of `content_conditions`, which all four statements share |
| Detail read by id | 200 | 200, with `deleted_at` set, so a screen can say *"nội dung đã bị xóa"* |
| Available actions | the stage's actions | **empty** |
| Every write | as normal | `PrContentDeletedError` (`pr_content_deleted`, HTTP 409) |

"Every write" is transitions (`PrContentWorkflowService.request_transition` and
`apply`), revisions (`PrContentService.revise_content`), review decisions
(`PrApprovalService.record_decision`) and all three production commands.

**Deleted ≠ cancelled.** Every deleted pre-publication item is also cancelled,
but a cancelled item is not deleted: somebody who presses *Hủy nội dung* still
sees the piece in the `Đã hủy` tab, unchanged from before this step. A deleted
`PUBLISHED` item is *not* moved to `CANCELLED` — that stage is unreachable from
there and would be a lie about something that went out.

---

## B. Who may delete

Two capabilities, no role strings, no super-admin bypass.

| Capability | Baseline permission | Held by | Meaning here |
| --- | --- | --- | --- |
| `PR_CONTENT_DELETE` | `script.submit` | every writer (`EMPLOYEE`+) | may ask to delete at all |
| `PR_CONTENT_CANCEL` | `script.approve` | `TEAM_LEAD`+ | **management** deletion — pre-existing, reused |

The rule is one pure function,
`meobot.domain.pr.lifecycle.delete_permitted(may_delete, manages_content, responsible, reached_production)`:

* no `PR_CONTENT_DELETE` → refused;
* holds `PR_CONTENT_CANCEL` → allowed, at any stage;
* otherwise → allowed only if **responsible** *and* **never produced**.

`PR_CONTENT_CANCEL` was reused rather than inventing a `PR_CONTENT_DELETE_ANY`:
it already means "may end this piece of work", it is already permission-backed,
and delete *is* a cancellation with the item hidden.

### How "responsible" is determined

`responsible_for(user_id)` from `application/pr_content_query.py` — the **same**
predicate behind the `MY_CONTENT` scope and the "Người phụ trách" filter:
`owner_user_id == me` **OR** an unfinished `pr_task_assignments` row of theirs on
one of the content's tasks. Creator identity is **not** used: Step 1A defines
`created_by_user_id` as "who started it" and it never changes hands.

Reusing the expression means what a member may delete is exactly what the panel
already tells them is theirs.

### How "has ever reached production" is determined

`pr_content_items.production_started_at` — a timestamp stamped **once**, by
`PrContentWorkflowService.apply`, the first time the item enters `PRODUCTION`,
and never cleared. Same shape as `archived_at`, which the module has used since
Step 1A.

`has_reached_production` answers from **the stamp OR the current stage** being
`PRODUCTION` or later (excluding `CANCELLED`, which is last in the enum because
it belongs to no position in the sequence):

* the stamp is what makes the answer survive an internal reviewer sending a cut
  back to `PRODUCTION` — a rule reading only the current stage would hand the
  member their delete button back at precisely the moment the piece has most
  work in it;
* the stage is the floor for rows that predate migration 0020, which was
  deliberately **not** backfilled: writing `now()` or `updated_at` into that
  column would have invented a date indistinguishable from a measured one.

There is no durable stage-history table in this module — the audit trail is the
history — and the specification forbids deriving durable business state from
audit text. A single-purpose stamped column is the durable fact instead.

---

## C. Production ownership

### The model

`pr_content_items.producer_user_id`, nullable, FK to `users`, `RESTRICT`.

Not `owner_user_id` (that answers for the piece from brief to measurement), not
`created_by_user_id` (historical), not a `pr_task_assignments` row (a task is
optional and most content has none — production would then depend on somebody
first creating an EDIT task, and "in production, nobody assigned" would be
indistinguishable from "nobody made the task yet").

**Nullable is the normal case.** `APPROVED → PRODUCTION` requires no producer and
never will: the script is approved when it is approved, and who edits it is a
separate decision. `PRODUCTION` + `producer_user_id IS NULL` renders as *"Chưa có
người nhận"*.

### The three commands

| Command | Capability | Extra conditions |
| --- | --- | --- |
| `assign_producer(producer_user_id \| None)` | `PR_PRODUCTION_ASSIGN` (`video.approve`, `TEAM_LEAD`+) | stage is `PRODUCTION`; the named person is active and eligible |
| `claim_production()` | `PR_PRODUCTION_EXECUTE` (`video.submit`, `EMPLOYEE`+) | stage is `PRODUCTION`; producer is `NULL` |
| `submit_production(artifact)` | `PR_PRODUCTION_EXECUTE` | stage is `PRODUCTION`; producer set; actor **is** the producer, or holds `PR_PRODUCTION_ASSIGN` |

Assignment, reassignment and un-assignment are one command because they are one
decision. A member cannot reach that path at all, which is what stops one
producer taking another's work; claiming refuses when the producer is set, which
is the other door.

### Concurrency

```sql
UPDATE pr_content_items SET producer_user_id = :me
 WHERE id = :id AND producer_user_id IS NULL
```

The winner is whoever the database says changed a row. `rowcount == 0` → the
loser gets `PrProductionClaimConflictError` (`pr_production_claimed`, HTTP 409),
carrying the producer actually on the row after a refresh; the panel and the
Telegram tool both render *"Nội dung đã được người khác nhận sản xuất."*

The row lock is taken as well and is **not** what makes this safe: on PostgreSQL
it serialises the two transactions, and on the offline SQLite suite `lock_row` is
a plain `get`. The conditional update is correct on both. No unique index is
involved — "may be set once from null" is not expressible as one without a second
table.

### Eligibility, and what it is not

Eligibility to hold a production is `PR_PRODUCTION_EXECUTE` and nothing else.
Every one of the four roles holds `video.submit`, so `/people` (active users)
*is* the eligible list — which is why the assign picker reuses it rather than
gaining a filter.

The channel-assignment relation is **not** eligibility. It decides whose *queue*
an unclaimed item appears in (see D), which Step 1F.2.2 established is a separate
concern: somebody sent a link to a piece outside their usual channels may still
claim it; they simply were not shown it unasked.

---

## D. Content views

Only two branches were added to `MY_ACTIONS`, and the rest of Step 1F.2.2 —
tab order, default scope, capability-based review queues, filters, URL state,
paging, counters — is untouched.

| New branch | Condition |
| --- | --- |
| my production | `stage = PRODUCTION AND producer_user_id = me` — no capability asked; being the producer *is* the assignment |
| unclaimed production | `stage = PRODUCTION AND producer_user_id IS NULL` **and** the actor holds `PR_PRODUCTION_EXECUTE` **and** an in-force `pr_channel_assignments` row for one of the content's channels |

The second is narrowed deliberately. Every `EMPLOYEE` may claim any unclaimed
edit; putting every unclaimed piece into every member's *Cần tôi xử lý* would
push their own work off the screen with other teams' backlogs, which is the
failure Step 1F.2.2 existed to fix. The narrowing is display, not authorization.

`INTERNAL_REVIEW` needed **no change at all**: it was already in
`STAGE_APPROVAL_GATES` and `APPROVAL_CAPABILITIES`, so `gate_stages_for` already
put it in the queue of whoever holds `PR_INTERNAL_REVIEW`.

### Người phụ trách vs Người sản xuất

Kept distinct, deliberately. `responsible_for` — and therefore `MY_CONTENT` and
the "Người phụ trách" filter — does **not** include `producer_user_id`. Folding
it in would silently redefine a filter people already use: a lead filtering
"Nguyễn A" would start seeing pieces Nguyễn A is only editing, with no way to ask
the narrower question again. The producer reaches their work through
`MY_ACTIONS`, and the detail page shows the two on separate lines.

---

## E. The production submission

`pr_production_submissions`, append-only, one row per handover:

```
id, content_id, content_version_id, submission_no,
producer_user_id, submitted_by_user_id,
artifact_type, location, label, note, created_at
```

A `production_url` column on the content item would have been one migration and
one field, and would have destroyed its own history on the second submission —
the ordinary sequence here is *submit v1 → yêu cầu sửa → submit v2 → duyệt*, and
an `UPDATE` erases the file the first decision was about.

* `submission_no` is per content, allocated under the content row's lock exactly
  as `version_no` is; `uq_pr_production_submissions_content_no` fails the loser
  of any race rather than letting two rows claim one number;
* `producer_user_id` is whose work it is (copied at submit time, so a later
  reassignment does not rewrite history) and `submitted_by_user_id` is who
  pressed the button — the same choice `pr_approval_events` makes with
  `reviewer_user_id`;
* no `updated_at`, and nothing in the module updates a row.

### Accepted references

`meobot.domain.pr.production.normalize_artifact` validates; it never fetches.
**No NAS credentials are used or needed** — the reference is a pointer a person
follows with their own access.

| `artifact_type` | Rule |
| --- | --- |
| `DRIVE_LINK` | `http(s)`, non-empty host, host in `drive.google.com` / `docs.google.com` |
| `NAS_LINK` | `http(s)`, non-empty host |
| `EXTERNAL_LINK` | `http(s)`, non-empty host |
| `NAS_PATH` | **not a URL**: absolute POSIX (`/volume1/…`) or UNC (`\\host\share\…`) |

Refused for every URL type: any scheme other than `http`/`https` — `javascript:`,
`data:`, `file:`, `vbscript:`, `blob:` are named individually so the refusal can
say which it recognised — a missing scheme, and a missing host. The check is
case-insensitive; `JavaScript:` is refused.

`NAS_PATH` is a distinct type rather than a `file://` URL because a path is not
an address any browser can open. The response carries `is_link`, computed by the
server, so the panel renders paths as text to copy and never guesses from the
string (`//nas/share/x` looks protocol-relative to anything matching characters).

Refusals are `PrValidationError` with `details.reason` — `empty`,
`missing_scheme`, `unsafe_scheme`, `unsupported_scheme`, `missing_host`,
`not_a_drive_host`, `not_a_path`, `not_absolute`, `incomplete_unc_path`,
`too_long`. Both clients word those themselves: `tools/pr_errors.py` for
Telegram, `errorMessage` in `lib/labels.ts` for the panel.

### The submit transaction

One transaction, in this order, and the order is the rule:

1. capability;
2. lock the content row; refuse if deleted; refuse if not at `PRODUCTION`;
3. refuse if no producer; refuse if the actor is neither the producer nor a
   production manager;
4. **validate the artifact** — before anything is written, so a bad paste costs
   nothing;
5. insert the submission (`content_version_id` from the current draft);
6. `PrContentWorkflowService.apply(PRODUCTION → INTERNAL_REVIEW, MANUAL)`;
7. audit `pr.production.submitted`.

Nothing commits in between; the caller owns the transaction. So
`INTERNAL_REVIEW` with no submission behind it is not a state this module can
produce, which is what lets the reviewer's screen assume there is a file.

---

## F. Internal review

`PR_INTERNAL_REVIEW` already existed: grant-backed, baseline `video.approve`,
mapped to `PrApprovalStage.INTERNAL_REVIEW` in `APPROVAL_CAPABILITIES`. It is
separate from `PR_TEAM_LEAD_REVIEW`, `PR_HEAD_REVIEW`, `PR_CONTENT_EDIT` and
`PR_CONTENT_DELETE`, and holding any of those implies nothing about it.

Decisions go through the same `PrApprovalService.record_decision` as the other
two gates, with outcomes from `APPROVAL_OUTCOMES`:

| Decision | Target |
| --- | --- |
| `APPROVED` | `READY_TO_PUBLISH` |
| `REVISION_REQUIRED` | `PRODUCTION` — never `SCRIPTING`; the reviewer is looking at a cut, and sending a bad edit to the scriptwriter would ask the wrong person to fix the wrong thing |
| `REJECTED` | `CANCELLED` (pre-existing, unchanged) |

### Which cut was judged

`pr_approval_events.production_submission_id`, set for `INTERNAL_REVIEW` rows and
null for the two script gates. It is read from the item's **latest** submission
by the service — never accepted from the request, for the same reason
`approval_stage` is derived from the stage.

Without it, a piece re-cut once has two internal-review rows with the same
content, the same stage and the same `version_reviewed`, saying opposite things,
with nothing to distinguish them. `version_reviewed` identifies a *script*, and a
re-cut does not change the script.

### One person, three gates

Unchanged from Step 1F.2.2 and now extended to the third gate: an actor holding
`PR_TEAM_LEAD_REVIEW`, `PR_HEAD_REVIEW` and `PR_INTERNAL_REVIEW` independently
may decide at all three for one piece. **Three separate append-only events** are
still written, each with its own stage, decision, version and timestamp, and none
is read as evidence for another — the Head approval still requires a team-lead
`APPROVED` for the same draft on file, whoever signed it. Nothing was collapsed
and no reviewer-separation rule was reintroduced.

---

## G. Available actions

`PrActionKind` gained four: `DELETE_CONTENT` (`DANGER`), `ASSIGN_PRODUCER`
(`SECONDARY`), `CLAIM_PRODUCTION` (`PRIMARY`), `SUBMIT_PRODUCTION` (`PRIMARY`).
Each is offered only when the service that performs it says it would work —
`PrProductionService.may_claim` / `may_assign` / `may_submit` and
`PrContentLifecycleService.may_delete`, all of which are the write's own
conditions read without a lock, in the pattern `PrCapabilityService.allows`
established.

Approving and requesting revision at the internal gate are **not** new kinds:
they are `APPROVAL` with a decision, exactly like the script gates, because the
gate is derived from the stage and the client renders the wording. The panel says
*Duyệt nội bộ* at `INTERNAL_REVIEW` and *Duyệt* elsewhere, from
`decisionLabelAt(stage, decision)` in `lib/labels.ts`.

A deleted item returns an empty action list.

---

## H. Web surface

| Route | Change |
| --- | --- |
| `DELETE /api/pr/contents/{id}` | **New.** Optional `{reason}`. Returns the item, with `deleted_at` set |
| `GET /api/pr/contents/{id}/production` | **New.** `{content_id, workflow_stage, producer_user_id, submissions[]}` |
| `POST /api/pr/contents/{id}/producer` | **New.** `{producer_user_id \| null}` |
| `POST /api/pr/contents/{id}/producer/claim` | **New.** No body — the claimant is the session |
| `POST /api/pr/contents/{id}/production-submissions` | **New.** `{artifact_type, location, label?, note?}` → 201, returns the whole production state |
| `GET /api/pr/contents…` | `ContentSummaryResponse` gains `producer_user_id` and `deleted_at`; `ApprovalEventResponse` gains `production_submission_id` |

The panel's production card renders at `PRODUCTION` and `INTERNAL_REVIEW`, shows
*Người sản xuất* (name, or *Chưa có người nhận*), the buttons the server offered,
the file form, and every submission with the internal-review decision paired to
it by `production_submission_id`. No UUID is displayed anywhere.

---

## I. Migration

**Migration `0020_pr_production_and_lifecycle` — required.** One new table, six
added columns, nothing altered, nothing backfilled.

| Object | Note |
| --- | --- |
| `pr_production_submissions` | new table, append-only |
| `pr_content_items.producer_user_id` | nullable FK → `users`, `RESTRICT`, indexed |
| `pr_content_items.production_started_at` | nullable timestamptz, stamped once |
| `pr_content_items.deleted_at` | nullable timestamptz, indexed |
| `pr_content_items.deleted_by_user_id` | nullable FK → `users`, `RESTRICT` |
| `pr_content_items.deleted_reason` | nullable text |
| `pr_approval_events.production_submission_id` | nullable FK → the new table |
| `ck_pr_content_items_deletion_is_attributed` | `(deleted_at IS NULL) = (deleted_by_user_id IS NULL)` |

Three indexes, each answering a query this step issues: producer (the new
`MY_ACTIONS` branch), `deleted_at` (every list filters it), and the unique
`(content_id, submission_no)` which is also the index behind "the latest
submission".

`pr_user_capabilities`' frozen `CHECK` list from 0016 is **not** widened: the
three new capabilities are role-decided, `PrCapabilityService.grant` refuses to
insert a non-grant-backed capability before it writes, and
`tests/unit/test_pr_authorization_schema_parity.py` now asserts exactly that
invariant rather than set equality.

Downgrade drops the table and the six columns. What is lost: every production
file reference, every producer assignment, and the record of which items were
deleted — after which **deleted content reappears in every list**.

---

## J. Tests

| Suite | Count | Covers |
| --- | --- | --- |
| `tests/unit/test_pr_production_lifecycle.py` | 50 new | Requirements 1-39: delete authority and its audit, inertness, the producer, concurrency, artifacts and schemes, the submit transaction, the three gates and the submission link, plus the HTTP surface |
| `tests/unit/test_pr_content_views.py` | 3 new (11f-11h) | Internal-review queue is capability-based; assigned production is in `MY_ACTIONS`; unclaimed production follows the team relation |
| `tests/unit/test_pr_authorization_and_codes.py` | 1 updated | The capability vocabulary is thirteen |
| `tests/unit/test_pr_core_schema_parity.py` | 1 updated | The two new person columns point at `users` |
| `tests/unit/test_pr_ai_review_schema_parity.py` | 1 updated | `pr_approval_events` gained one nullable column and still has no `updated_at` |
| `tests/unit/test_pr_authorization_schema_parity.py` | 1 rewritten | Everything grantable is in 0016's list, and everything missing from it is un-grantable |
| `frontend/tests/production.test.tsx` | 15 new | Requirements 50-61: delete from the action list only, confirmation, the production card, names not UUIDs, the required artifact field, readable refusals, the internal-review screen and its history |

---

## K. Deployment

* run `alembic upgrade head` → **0020**;
* rebuild **api** and **web**;
* recreate **api** and **web**;
* recreate **worker**, **bot** and **beat** — they import `pr_services`,
  `pr_approval_service` and `pr_workflow_service`, all of which changed;
* **no env changes**, and **no NAS configuration**: MeoBot stores NAS references
  as text and never opens them. No credential, mount, share or path prefix is
  read by this feature. If the team wants NAS links to be clickable from the
  panel, that is a matter of them pasting a File Station URL rather than a path —
  `NAS_LINK` exists for exactly that, and needs nothing from the server.

Order matters only in the usual way: the API must not run 0020's code before the
migration, since it reads columns that would not exist. The reverse - the
migration ahead of the code - is safe, because every added column is nullable and
the previous version simply never writes them.
