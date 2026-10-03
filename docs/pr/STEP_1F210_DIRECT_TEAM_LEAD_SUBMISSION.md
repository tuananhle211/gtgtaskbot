# Step 1F.2.10 — Gửi duyệt Trưởng nhóm, bỏ qua AI review

**Status:** implemented, verified offline and on PostgreSQL; **not deployed**,
and no production data has been read or repaired.
**Scope:** one additional way to submit a finished script. Beside *"Gửi đi AI
review"* the author may choose *"Gửi duyệt Trưởng nhóm"*, and the piece lands
on the same *Chờ duyệt Trưởng nhóm* column the AI path lands on.
**Explicitly out of scope:** the AI review itself, the human gates, production,
publication, the permission model, the board's grouping and every notification.
None of them changed.

---

## 1. The audit this started from

The script stage was one-way. `SCRIPTING -> AI_REVIEW` was the only manual edge
out of *Viết kịch bản*, and `AI_REVIEW -> TEAM_LEAD_REVIEW` existed only as the
consequence of a gating `FULL_REVIEW` verdict. Three facts about that shape
mattered for this step:

| Fact | Where |
| --- | --- |
| The matrix is derived once, with a trigger per edge; `request_transition` accepts `MANUAL` edges only | `domain/pr/workflow.py`, `pr_workflow_service.py` |
| Entering `AI_REVIEW` needs policy readiness - a planned channel, Organic/Paid decided on every grounded target, and an **active policy pack** - and queues a durable run for the current draft | `pr_policy_readiness_service.py`, `PrContentWorkflowService._queue_ai_review` |
| Deciding at `TEAM_LEAD_REVIEW` refuses a draft with no `FULL_REVIEW` on file - the "AI gate", per version | `PrApprovalService._require_ai_gate`, and the same predicate behind `/available-actions` |

The third is the one a naive bypass would have broken: content could have been
moved to the Team Lead and then found undecidable, because the gate would have
looked for a verdict that was never going to exist.

Two further facts shaped what was *not* built. Nothing is sent to anybody when
content enters `TEAM_LEAD_REVIEW` - on the AI path or any other - because the
module notifies people, not capability holders (`pr_notifications`). And every
role already holds `script.submit`, which is the baseline for
`PR_CONTENT_TRANSITION`: whoever may press *"Gửi đi AI review"* is exactly who
may press the new button, so no permission was needed and none was added.

## 2. What was built

### 2.1 One edge, reused destination

`SCRIPTING -> TEAM_LEAD_REVIEW` is now a `MANUAL` edge, named
`DIRECT_TEAM_LEAD_SUBMISSION` in the domain. It is the **only** manual edge into
any human gate, and the enumeration test says so. No stage was added, no
trigger value was added, no column was added.

What makes the move honest is that it is the only move of its shape. A history
row with `from_stage = SCRIPTING`, `to_stage = TEAM_LEAD_REVIEW`, `trigger =
MANUAL` can mean one thing, so:

* the history tab words it *"Bỏ qua AI review, gửi duyệt Trưởng nhóm"* from the
  row alone (`transitionHistoryLabel`), and words the AI handoff differently;
* the audit line `pr.content.stage_changed` additionally carries
  `action = submit_team_lead_review`, `ai_review_bypassed = true` and
  `content_version_no`, so a reader of the trail does not have to know the rule;
* the Team Lead gate accepts it - see §2.3.

An undo of a revision request takes the same edge with `trigger = UNDO` and is
deliberately not a direct submission.

### 2.2 The same readiness, minus the machine

`PrContentWorkflowService.submit_to_team_lead_review` is the named entry point,
beside `cancel`; the generic `POST /transition` with `TEAM_LEAD_REVIEW` routes to
it, and the Telegram `pr.content.transition` tool reaches the same
`request_transition`. Under the row lock it requires:

* the edge to exist from the current stage - refused from `IDEA`, `BRIEFING`,
  `AI_REVIEW`, every gate, production and every terminal stage with a
  structured `409 pr_invalid_transition`;
* a current draft (`reason = no_content_version` otherwise);
* the content-completeness half of AI readiness: a planned channel
  (`content_targets_required`) and Organic/Paid decided on every grounded
  target (`policy_distribution_mode_required`) - the same reason codes, from the
  same service, through `evaluate_for_human_review`.

It does **not** require an active policy pack. A pack is what a grounded AI
review runs against; no review will run, and a Team Lead reading a script does
not need one. This is the one readiness check the direct path does not inherit,
and it is a decision rather than an omission: it is exactly the case in which a
team most needs the human path to be open.

It writes no `pr_ai_reviews` row, queues no `pr_ai_review_runs` row, records no
`pr.ai_review.*` audit line and creates no approval event.

### 2.3 The gate learns the second route

`_require_ai_gate` is satisfied by either a gating verdict **for this version**
or a direct-submission transition **pinned to this version**. Both records are
per draft, so a direct submission of v1 does not vouch for v2, exactly as a v1
verdict never did. Everything else at the gate is untouched: the grant-backed
capability, the scoped-grant rule, the version check, the three decisions and
their outcomes, the Team Lead approval notification, undo.

### 2.4 The offer

`PrAvailableActionService` offers `TRANSITION → TEAM_LEAD_REVIEW` at
`SCRIPTING` with `SECONDARY` emphasis - AI review keeps `PRIMARY` as the
recommended default - and withholds it on the same predicate the write refuses.
The panel renders the list as it always has; it owns no rule about `SCRIPTING`,
no `skip` flag and no AI field. The button says *"Gửi duyệt Trưởng nhóm"*; the
confirmation - every workflow move confirms since Step 1F.2.8 - says the one
thing the button leaves out: *"Nội dung này sẽ bỏ qua bước AI review và chuyển
sang Chờ duyệt Trưởng nhóm."*

## 3. Board, notifications, return

The piece leaves the `SCRIPTING` lane and appears once in `TEAM_LEAD_REVIEW`
under *Chờ duyệt*; `AI_REVIEW` never holds it. Nothing about `GROUP_STAGES` or
the lanes changed.

No notification is sent on the handoff - the same as the AI path - and nothing
resembling "AI review completed" exists to be sent. The Team Lead's own decision
notifies exactly as before.

When the Team Lead returns the piece, the author revises, and both buttons are
offered again for the new draft. The earlier choice is not remembered for them.

## 4. Concurrency

Every path goes through the same `SELECT ... FOR UPDATE` on the content row.
`tests/integration/test_pr_direct_submission_race_pg.py` runs four genuine
two-connection races on PostgreSQL - AI vs direct, direct vs direct, direct vs
revision, direct vs cancel - and asserts one winner, one transition row, no
mixed state, no duplicate run, no notification and no unhandled database error.

## 5. What did not change

No migration: the Alembic head is `0041`, as before. No new permission, no new
capability, no new stage, no new trigger, no new audit action, no new endpoint.
The AI path - submit, pass, fail, return, retry - is byte-for-byte what it was,
and its suites say so.
