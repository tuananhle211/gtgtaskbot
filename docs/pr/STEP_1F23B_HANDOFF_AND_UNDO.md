# Step 1F.2.3b — Approved-to-production handoff, and safe workflow undo

Two changes. The first corrects where a Head approval leaves a piece of content;
the second gives the team a way to take back a decision they have just made,
without letting anybody move content wherever they like.

---

# Part A — the handoff

## A1. What was wrong

A Head approval left content at `APPROVED`, and the only way onward was the
generic transition to `PRODUCTION` — available to anybody holding
`PR_CONTENT_TRANSITION`, with `producer_user_id` still null. So the board showed
work "đang sản xuất" that nobody had picked up, and the only way to give somebody
the job was to first pretend they had started it: producer assignment required
the item to be at `PRODUCTION` already.

## A2. The corrected sequence

```
HEAD_REVIEW --approve--> APPROVED            "Chờ nhận sản xuất"
                            |
              assign / claim v
                         APPROVED            "Sẵn sàng sản xuất"
                            |
            START_PRODUCTION v   ← stamps production_started_at
                       PRODUCTION            "Đang sản xuất"
                            |
             submit artifact v
                  INTERNAL_REVIEW            "Chờ duyệt nội bộ"
```

A Head approval still ends at `APPROVED` — that was already true and now has a
test. Nothing auto-enters production.

## A3. The derived state

`PrProductionHandoff`, computed by `handoff_state(stage, producer_user_id)`:

| Stage | Producer | State | Vietnamese |
| --- | --- | --- | --- |
| `APPROVED` | null | `WAITING_FOR_PRODUCER` | Chờ nhận sản xuất |
| `APPROVED` | set | `READY_FOR_PRODUCTION` | Sẵn sàng sản xuất |
| `PRODUCTION` | — | `IN_PRODUCTION` | Đang sản xuất |
| `INTERNAL_REVIEW` | — | `IN_INTERNAL_REVIEW` | Chờ duyệt nội bộ |
| anything else | — | `null` | — |

**No new workflow stage.** `APPROVED` with and without a producer are the same
editorial fact and differ only in whether the handoff has happened; a fourteenth
stage would have added matrix edges and made every existing query about
`APPROVED` subtly wrong. The value is computed on the server and sent as
`production_state` on every content summary, so the browser never combines two
fields and invents the rule.

## A4. Assignment and self-claim, at `APPROVED`

`HANDOFF_STAGES = {APPROVED, PRODUCTION}` — assignment and claiming work at
both. Assigning at `APPROVED` is the normal handoff; reassigning at `PRODUCTION`
is the exception that still has to work when somebody goes on leave.

Neither moves the stage. Neither stamps `production_started_at`. The claim keeps
its conditional-`UPDATE` concurrency rule from Step 1F.2.3 unchanged.

## A5. `START_PRODUCTION`

A distinct action and a distinct route (`POST /contents/{id}/production/start`),
allowed when:

* stage is `APPROVED`, **and**
* `producer_user_id` is not null, **and**
* the actor holds `PR_PRODUCTION_EXECUTE`, **and**
* the actor **is** the producer, or holds `PR_PRODUCTION_ASSIGN` (a manager may
  start work they could have assigned to themselves a second earlier; the
  transition event records who actually did it).

An unrelated member is refused with `reason: not_the_producer`.

The generic `TRANSITION` to `PRODUCTION` is **withheld from the action list** —
one move, one button, one set of rules — but the route still exists and now
enforces the producer requirement itself, so the Telegram tool and any script get
the same refusal (`reason: no_producer_assigned`, *"Bạn cần phân công hoặc nhận
người sản xuất trước khi bắt đầu sản xuất."*). Button visibility is not the rule.

The same shape was applied to the gate after it: entering `INTERNAL_REVIEW` by
the generic route now requires a production submission to exist
(`reason: no_production_submission`). `submit_production` writes the row and
calls `apply` directly, so the honest path is unaffected.

## A6. `production_started_at`

Set by exactly one thing: the successful `APPROVED → PRODUCTION` transition, in
`PrContentWorkflowService.apply`, once, never cleared. Assignment does not set
it; a claim does not set it. Step 1F.2.3a's permanent-delete rule reads it, so
this invariant is what keeps a member's delete right ending permanently at the
moment their work is produced.

## A7. Notifications

Three, all `PERSONAL_PRIVATE`, all queued through the existing
`NotificationRouter` in the transaction carrying the change:

| Event | To | Says |
| --- | --- | --- |
| `pr_content_approved` | the **responsible** user (`responsible_for` — owner, or unfinished task assignee) | approved; take the production or arrange somebody who will, plus the deep link |
| `pr_production_assigned` | the assigned producer (never on self-claim) | you have been given this production |
| `pr_workflow_undone` | the producer if there is one, else the responsible user | which decision was taken back, and which stage the content is at now |

An unreachable or inactive recipient is logged and skipped — it never fails the
decision. Nothing goes to a group.

## A8. MY_ACTIONS and the board

New `MY_ACTIONS` branches, on top of Step 1F.2.2's:

* producer == me at `APPROVED` **or** `PRODUCTION` (I can start it, or hand it in);
* `APPROVED`/`PRODUCTION` with no producer **and** (I am responsible for it, or I
  answer for one of its channels) — for `PR_PRODUCTION_EXECUTE` holders;
* `APPROVED`/`PRODUCTION` with no producer, for `PR_PRODUCTION_ASSIGN` holders,
  because arranging the handoff is management's own work.

An arbitrary member is *not* flooded with every unclaimed approved item — that
narrowing is display, not authorization, exactly as Step 1F.2.2 established.

The board distinguishes the four states through `production_state` on each card
and in the detail header, so `APPROVED` never reads as "in production" and
`INTERNAL_REVIEW` never mixes with either.

---

# Part B — Undo

## B1. What "Hoàn tác" is

**Reverse the most recent reversible workflow action, and keep it in history.**

Not a stage picker: there is no destination parameter in the route, the command
or the service. Not an eraser: the original transition, the original approval and
the original audit row all stay, and the undo *appends* a reversal linked to
them.

## B2. The durable model

`pr_content_transition_events` (migration 0022), written by
`PrContentWorkflowService.apply` — already the only writer of `workflow_stage` —
for **every** transition, in the same transaction as the audit row:

```
content_id, content_version_id, from_stage, to_stage, trigger,
actor_user_id, approval_event_id, production_submission_id,
reverses_event_id, reversed_by_event_id, note, created_at
```

The audit trail is unchanged and still records every move. This is the
*structured* half, because undo has to ask a business question in SQL and
`audit_logs.after_data` is free-form by design — parsing it for authority is the
failure this table replaces.

`reversed_by_event_id` is the one column ever updated, write-once, so "is this
transition still in force" is an index lookup.

## B3. Approval reversal, and why it matters

An undone approval **stays in `pr_approval_events`** — that table is append-only
and has no `updated_at`. What changes is that it stops being *authority*:

```sql
-- is_effective_approval()
NOT EXISTS (SELECT 1 FROM pr_content_transition_events t
             WHERE t.approval_event_id = pr_approval_events.id
               AND t.reversed_by_event_id IS NOT NULL)
```

`successful_team_lead_approval` applies it, so `_require_prior_team_lead_approval`
— and therefore the Head gate — will not accept a team-lead sign-off somebody
withdrew. Read as *history* the same rows come back unfiltered, because a
withdrawn approval genuinely happened.

## B4. What is undoable

The candidate is the **latest transition that is not already reversed, is not
itself an undo, and still describes where the content is**
(`event.to_stage == content.workflow_stage`). That last clause makes "only the
latest action" and "not after somebody moved on" true by construction.

| Decision | From | Back to | Also blocked when |
| --- | --- | --- | --- |
| Team Lead `APPROVED` | `HEAD_REVIEW` | `TEAM_LEAD_REVIEW` | a Head decision has been taken (it is then no longer the latest) |
| Head `APPROVED` | `APPROVED` | `HEAD_REVIEW` | a producer holds it, production started, or a cut exists |
| Internal `APPROVED` | `READY_TO_PUBLISH` | `INTERNAL_REVIEW` | anything has been published |
| `REVISION_REQUIRED` | `SCRIPTING` | the gate | a newer draft exists |
| `REVISION_REQUIRED` | `PRODUCTION` | `INTERNAL_REVIEW` | a newer cut exists |

Downstream checks compare **identity, not timestamps** — the transition records
which version was current and the approval records which cut it judged, so rows
written in one transaction cannot compare equal and a clock cannot change the
answer.

Undoing a Head approval **does not** clear the producer to make itself fit:
somebody else's work is not this button's to discard, and the refusal says
*"Không thể hoàn tác duyệt vì nội dung đã được bàn giao cho sản xuất."*

## B5. What is deliberately not undoable

| | Why |
| --- | --- |
| `START_PRODUCTION` | `production_started_at` is irreversible; the delete rule reads it. A "back to approved" correction, if ever needed, is its own action with its own name |
| Rejections (`REJECTED` → `CANCELLED`) | un-cancelling abandoned work is a decision, not an undo. No `UNDO` edge leaves `CANCELLED` |
| **AI review** | undoing `SCRIPTING → AI_REVIEW` means cancelling a queued or running job, and this repository has no run-cancellation state. Moving the stage and hoping the worker notices is how a stale `PASS` advances content nobody re-checked. **Omitted from this patch, deliberately, rather than faked** |
| Anything `PUBLISHED`/`MEASURED`/`ARCHIVED` | published history is not rewound, and no capability crosses it |
| Deletion | Step 1F.2.3a's delete is permanent; there is no row to undo |
| Producer assignment | not an undo — reassign or clear through `PR_PRODUCTION_ASSIGN`, which is the normal semantics |

## B6. Who may undo

```
holds the capability the original action needed
AND (performed it themselves OR holds PR_CONTENT_CANCEL)
```

So a Team Lead takes back their own approval with `PR_TEAM_LEAD_REVIEW`; taking
back somebody else's also needs `PR_CONTENT_CANCEL`, the existing "may end this
piece of work" right. No undo-anything capability was created, and nobody gains
authority they did not already have over the action itself.

## B7. The transaction

Lock the content → resolve the candidate from history → authorize → check
downstream work → append the reversal and link the original → move the stage
through `apply` with the `UNDO` trigger → audit `pr.workflow.undone` → notify.
One transaction, caller commits, nothing hidden.

The `UNDO` edges are **derived** from `APPROVAL_OUTCOMES` (every reversible
decision, backwards) rather than hand-written, and `request_transition` refuses
the trigger — so no route and no tool can drive a backward edge by naming a
stage.

## B8. Concurrency

Everything serialises on the content row lock. An undo racing an approval, a
producer assignment, a production start or a publication: whoever takes the row
first wins, and the loser finds that the action it was aiming at is no longer the
one that put the content where it is (`reason: superseded`) or that its target
now has downstream work. A second undo of the same decision finds it already
reversed. Errors are checked domain errors — `pr_undo_not_available` (409) with a
`reason`, or `pr_forbidden` (403) — and no `IntegrityError`, SQL or traceback
reaches a client.

---

## C. API and UI

| Route | Change |
| --- | --- |
| `POST /contents/{id}/production/start` | **New.** No body. `APPROVED → PRODUCTION` |
| `POST /contents/{id}/undo` | **New.** No body, no destination |
| `GET /contents/{id}/history` | **New.** Transition events, oldest first, with reversal links |
| `POST /contents/{id}/producer`, `/producer/claim` | now accepted at `APPROVED` as well as `PRODUCTION` |
| content responses | gain `production_state` |
| `available-actions` | gains `START_PRODUCTION` and `UNDO_LAST_ACTION` (with `undo_kind` and `target_stage`); the plain transitions to `PRODUCTION`/`INTERNAL_REVIEW` are withheld |

The panel renders the handoff state on the header and the production card,
"Nhận sản xuất" / "Phân công người sản xuất" at `APPROVED`, "Bắt đầu sản xuất"
only when the server offers it, and an undo button that names the decision —
*"Hoàn tác duyệt Trưởng phòng"* — with a confirmation naming the stage the
content returns to. The history tab lists every transition and marks a reversed
one "Đã hoàn tác" instead of removing it.

## D. Migration

**`0022_pr_transition_history`** (down_revision `0021`, head is now `0022`). One
table, four indexes, no change to any existing table, no backfill. Content that
moved before this revision has no history and is therefore not undoable — the
correct answer rather than a limitation, since reconstructing it from audit text
is the thing the table exists to avoid.

## E. Tests

| Suite | Count | Covers |
| --- | --- | --- |
| `tests/unit/test_pr_handoff_and_undo.py` | 37 new | Requirements 1-45: the handoff, the stamp, notifications, every undo case and refusal, history pairs, authority, concurrency outcomes |
| `tests/unit/test_pr_content_views.py` | 2 new | The approved handoff in `MY_ACTIONS`, and who is *not* shown it |
| `frontend/tests/production.test.tsx` | 10 new | Requirements 46-57: the derived state, the missing start button, the assignment picker, undo wording, confirmation, refusals, history pairs |
| existing PR suites | updated | The walk to `PRODUCTION` now assigns and starts, because that is the only way there |

## F. Deployment

* `alembic upgrade head` → **0022**;
* rebuild **api** and **web**; recreate **api**, **web**, **worker**, **bot**,
  **beat** — the worker sends the new notification templates and the bot's
  presenter table moved into the domain;
* **no env changes, nothing about the NAS.**

Deploy the migration **before or with** the code: `apply` writes a transition row
on every stage change, so the table has to exist first.
