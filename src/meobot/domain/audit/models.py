"""Audit trail value objects.

Every state-changing action writes exactly one :class:`AuditEntry`, including
denials and failures. Before/after payloads are redacted by
:mod:`meobot.core.logging` helpers before they are persisted.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditResult(StrEnum):
    """Outcome recorded for an audited action."""

    SUCCESS = "success"
    DENIED = "denied"
    FAILED = "failed"
    PENDING_CONFIRMATION = "pending_confirmation"


class AuditAction(StrEnum):
    """Canonical action names. Extend as capabilities land."""

    SYSTEM_HEALTH_CHECK = "system.health_check"
    SCRIPT_TYPE_CREATED = "script_type.created"
    SCRIPT_TYPE_VERSION_ADDED = "script_type.version_added"
    SCRIPT_TYPE_DEACTIVATED = "script_type.deactivated"
    SHEET_PROFILE_CREATED = "sheet_profile.created"
    SHEET_PROFILE_UPDATED = "sheet_profile.updated"
    SHEET_PROFILE_SCHEMA_CHANGED = "sheet_profile.schema_changed"
    SHEET_PROFILE_DEACTIVATED = "sheet_profile.deactivated"
    SHEET_SYNCED = "sheet.synced"
    SHEET_WRITE_BACK = "sheet.write_back"
    SCRIPT_IMPORTED = "script.imported"
    SCRIPT_VERSION_CREATED = "script.version_created"
    SCRIPT_REVIEWED = "script.reviewed"
    SCRIPT_REVIEW_FAILED = "script.review_failed"
    SCRIPT_APPROVED_FOR_PRODUCTION = "script.approved_for_production"
    SCRIPT_REVISION_REQUESTED = "script.revision_requested"
    SCRIPT_APPROVAL_INVALIDATED = "script.approval_invalidated"
    INVITE_CREATED = "invite.created"
    INVITE_REDEEMED = "invite.redeemed"
    INVITE_REJECTED = "invite.rejected"
    INVITE_DISABLED = "invite.disabled"
    CONFIRMATION_REQUESTED = "confirmation.requested"
    CONFIRMATION_CONFIRMED = "confirmation.confirmed"
    CONFIRMATION_REJECTED = "confirmation.rejected"
    TOOL_EXECUTED = "tool.executed"
    TOOL_DENIED = "tool.denied"
    USER_REGISTERED = "user.registered"
    USER_ROLE_CHANGED = "user.role_changed"
    USER_SUSPENDED = "user.suspended"
    USER_ENABLED = "user.enabled"
    USER_REVOKED = "user.revoked"
    GROUP_POLICY_SET = "group_member_policy.set"
    GROUP_POLICY_RESET = "group_member_policy.reset"
    GUEST_ACCESS_GRANTED = "guest_access.granted"
    GUEST_ACCESS_EXTENDED = "guest_access.extended"
    GUEST_ACCESS_REVOKED = "guest_access.revoked"
    GUEST_ANSWER_ONCE = "guest_access.answer_once"
    ACCESS_REQUEST_OPENED = "access_request.opened"
    ACCESS_REQUEST_RESOLVED = "access_request.resolved"
    QUOTA_REQUEST_OPENED = "quota.request_opened"
    QUOTA_OVERRIDDEN = "quota.overridden"
    HR_REQUEST_SUBMITTED = "hr_request.submitted"
    HR_REQUEST_APPROVED = "hr_request.approved"
    HR_REQUEST_REJECTED = "hr_request.rejected"
    HR_REQUEST_CHANGE_REQUESTED = "hr_request.change_requested"
    HR_REQUEST_WITHDRAWN = "hr_request.withdrawn"
    HR_REQUEST_AMENDED = "hr_request.amended"
    HR_SCHEDULE_CONFIGURED = "hr_schedule.configured"
    CHAT_REGISTERED = "telegram_chat.registered"
    CHAT_DISABLED = "telegram_chat.disabled"
    ANNOUNCEMENT_PUBLISHED = "announcement.published"
    NOTIFICATION_QUEUED = "notification.queued"
    NOTIFICATION_REFUSED = "notification.refused"
    NOTIFICATION_RETRIED = "notification.retried"
    # --- PR and Communications, Step 1C ------------------------------------
    #: These are the PR module's domain events. They are recorded here, in the
    #: append-only audit trail, and deliberately **not** in ``outbound_messages``:
    #: that table is the *notification* outbox - every row needs a
    #: ``telegram_chat_id``, a template and a template version, because its job
    #: is delivering a message. Step 1C sends nothing, so it has nothing to
    #: enqueue there. See ``docs/pr/STEP_1C_APPLICATION_SERVICES.md``.
    PR_CONTENT_CREATED = "pr.content.created"
    PR_CONTENT_VERSION_CREATED = "pr.content.version_created"
    PR_CONTENT_STAGE_CHANGED = "pr.content.stage_changed"
    #: Step 1F.2.3d. Somebody retriaged a piece. Its own action rather than a
    #: ``version_created`` with a different payload, because it is not a draft:
    #: no version row is written, the stage does not move, and "who marked this
    #: Rất gấp, and when" is a question about a person's judgement rather than
    #: about the script. The payload carries the two levels and the code - never
    #: the content body, which is what the version rows are for.
    PR_CONTENT_PRIORITY_CHANGED = "pr.content.priority_changed"
    #: Step 1F.2.3e. Somebody classified a piece, or corrected its format. Its
    #: own action for the reason the priority one is: no version row is written
    #: and the stage does not move, so it is not a draft. ``before`` carries
    #: ``null`` when the item was historical and had never been classified.
    PR_CONTENT_TYPE_CHANGED = "pr.content.type_changed"
    #: Step 1F.2.3e. Review material attached to, changed on, or removed from a
    #: content item. Three actions rather than one with a verb in the payload,
    #: because "who deleted the brief" is a question somebody asks directly.
    #: The payload names and locates the resource; it never copies the note or
    #: anything the location points at.
    PR_CONTENT_RESOURCE_ADDED = "pr.content.resource_added"
    PR_CONTENT_RESOURCE_UPDATED = "pr.content.resource_updated"
    PR_CONTENT_RESOURCE_DELETED = "pr.content.resource_deleted"
    #: Step 1F.2.3f. A derivative production output - a cutdown, a remix, a
    #: caption variant - recorded against a content item, corrected, or removed.
    #: Three actions for the same reason the resource trio is three: "who
    #: deleted the TikTok cut" is a question somebody asks directly, and a
    #: single event with a verb in the payload makes it a search rather than a
    #: filter.
    #:
    #: The payload names and locates the derivative and carries its lineage; it
    #: never copies a note or anything the location points at.
    PR_CONTENT_DERIVATIVE_ADDED = "pr.content.derivative_added"
    PR_CONTENT_DERIVATIVE_UPDATED = "pr.content.derivative_updated"
    PR_CONTENT_DERIVATIVE_DELETED = "pr.content.derivative_deleted"
    #: Step 1F.2.3f. A commercial destination link - a landing page, a booking
    #: page - attached to a content item, corrected, or removed. Concise by
    #: design: a label and a URL are the whole row.
    #: Step 1F.2.3g. A comment **taken down**, and the only comment event that
    #: is audited at all.
    #:
    #: Creating and editing a comment write no audit row on purpose. The row
    #: itself already carries ``author_user_id``, ``created_at`` and
    #: ``edited_at``, it is displayed back to the person who wrote it, and a
    #: trail entry per typed sentence would bury the events somebody actually
    #: searches this table for. Deletion is different in kind: the row stops
    #: showing what it said, and *"who removed whose comment"* - an author
    #: thinking better of it, or a lead moderating - is a question asked
    #: directly, exactly as "who deleted the brief" is.
    PR_CONTENT_COMMENT_DELETED = "pr.content.comment_deleted"
    PR_CONTENT_DESTINATION_ADDED = "pr.content.destination_added"
    PR_CONTENT_DESTINATION_UPDATED = "pr.content.destination_updated"
    PR_CONTENT_DESTINATION_DELETED = "pr.content.destination_deleted"
    #: Somebody asked for an automated review to run. Step 1F. Deliberately
    #: distinct from ``recorded``: a request is a person's action and names
    #: them, a recording is a model's answer and names no person.
    #: Step 1F.1. Somebody said a target is organic or a paid ad, which decides
    #: which official policy the AI review applies.
    PR_CONTENT_TARGET_UPDATED = "pr.content.target_updated"
    PR_AI_REVIEW_REQUESTED = "pr.ai_review.requested"
    PR_AI_REVIEW_RECORDED = "pr.ai_review.recorded"
    PR_APPROVAL_RECORDED = "pr.approval.recorded"
    #: Step 1F.2.8. One row per *bulk* approval, beside - never instead of -
    #: the one ``PR_APPROVAL_RECORDED`` row each item in it still gets. It
    #: carries the batch id, the gate and the ids decided together, so "what
    #: was this batch" is answerable without reassembling it from timestamps.
    PR_APPROVAL_BATCH_RECORDED = "pr.approval.batch_recorded"
    #: Step 1F.2.3f.6. One row per *"Lưu trữ nội dung kỳ trước"*, beside the
    #: ``PR_CONTENT_STAGE_CHANGED`` row every item in it still gets from the
    #: transition itself. Carries the batch id, the month and the ids archived
    #: together.
    PR_CONTENT_ARCHIVE_BATCH_RECORDED = "pr.content.archive_batch_recorded"
    PR_TASK_CREATED = "pr.task.created"
    PR_TASK_ASSIGNED = "pr.task.assigned"
    PR_TASK_UNASSIGNED = "pr.task.unassigned"
    PR_TASK_STATUS_CHANGED = "pr.task.status_changed"
    # --- M1: the Work Ledger ---------------------------------------------
    # One action per decision somebody could later be asked about. Deliberately
    # finer-grained than the work item's status ladder: "who moved this
    # deadline" and "who took this person off the job" are questions the ladder
    # cannot answer, and they are exactly the questions an audit trail exists
    # for. The user-facing timeline is a different table with different
    # content - see ``pr_work_history``.
    PR_WORK_TYPE_CREATED = "pr.work_type.created"
    PR_WORK_TYPE_UPDATED = "pr.work_type.updated"
    #: M2.5. Separate from ``PR_WORK_TYPE_UPDATED`` because deactivating a type
    #: is a decision about what the department may file next month, and reading
    #: it out of a generic update's changed-field list is exactly the kind of
    #: archaeology an audit trail exists to spare somebody.
    PR_WORK_TYPE_ACTIVATED = "pr.work_type.activated"
    PR_WORK_TYPE_DEACTIVATED = "pr.work_type.deactivated"
    #: One row per bootstrap run, naming only the codes it actually created. A
    #: repeat run creates nothing and says so, which is what makes "did somebody
    #: run this twice" answerable.
    PR_WORK_TYPES_BOOTSTRAPPED = "pr.work_type.bootstrapped"
    # --- M6: scoring, monthly review and the performance index -------------
    # Every action here either sets a rate, judges a person, or agrees a month -
    # so each is something somebody may later be asked to justify, and each gets
    # its own name rather than a shared "performance.updated". **No money:** M6
    # scores performance and allocates none.
    PR_WORK_SCORING_RULE_CREATED = "pr.work_scoring_rule.created"
    PR_WORK_SCORING_RULE_APPROVED = "pr.work_scoring_rule.approved"
    PR_WORK_SCORING_RULE_SUPERSEDED = "pr.work_scoring_rule.superseded"
    PR_PERFORMANCE_POLICY_CREATED = "pr.performance_policy.created"
    PR_PERFORMANCE_POLICY_APPROVED = "pr.performance_policy.approved"
    PR_PERFORMANCE_POLICY_SUPERSEDED = "pr.performance_policy.superseded"
    PR_PERFORMANCE_REVIEW_CREATED = "pr.performance_review.created"
    #: One action per dimension, because "who changed my timeliness from Đạt to
    #: Chưa đạt, and when" is a question asked about **one** dimension and a
    #: shared event would make answering it a diff of a JSON blob.
    PR_PERFORMANCE_QUALITY_RATED = "pr.performance_review.quality_rated"
    PR_PERFORMANCE_TIMELINESS_RATED = "pr.performance_review.timeliness_rated"
    PR_PERFORMANCE_CONTRIBUTION_RATED = "pr.performance_review.contribution_rated"
    PR_PERFORMANCE_TARGET_OVERRIDDEN = "pr.performance.target_overridden"
    PR_PERFORMANCE_RECALCULATED = "pr.performance.recalculated"
    PR_PERFORMANCE_FINALIZED = "pr.performance.finalized"
    PR_WORK_CREATED = "pr.work.created"
    PR_WORK_ACCEPTED = "pr.work.accepted"
    PR_WORK_REJECTED = "pr.work.rejected"
    PR_WORK_STARTED = "pr.work.started"
    PR_WORK_COMPLETED = "pr.work.completed"
    PR_WORK_REOPENED = "pr.work.reopened"
    #: The one that matters. Validation is what turns finished work into
    #: counted work, so who did it, for which item, is the row an argument
    #: about somebody's KPI is settled from.
    PR_WORK_APPROVED = "pr.work.approved"
    #: **M4A.** One batch validation as a single act, *in addition to* the
    #: ``pr.work.approved`` row each item still writes. Nothing is collapsed:
    #: the per-item rows remain the record of who counted whose work, and this
    #: one answers the different question of what a single sweep did - how many
    #: jobs, how many contributions, at whose hand. Correlating them needs no
    #: column, because both carry the batch id in their JSON payload.
    PR_WORK_VALIDATION_BATCH_RECORDED = "pr.work.validation_batch_recorded"
    PR_WORK_CANCELLED = "pr.work.cancelled"
    PR_WORK_CONTRIBUTOR_ADDED = "pr.work.contributor_added"
    PR_WORK_CONTRIBUTOR_REMOVED = "pr.work.contributor_removed"
    PR_WORK_DEADLINE_CHANGED = "pr.work.deadline_changed"
    PR_WORK_PRIORITY_CHANGED = "pr.work.priority_changed"
    PR_WORK_EVIDENCE_ADDED = "pr.work.evidence_added"
    PR_WORK_EVIDENCE_REMOVED = "pr.work.evidence_removed"
    # --- M4B: recurring work ---------------------------------------------
    # A template is configuration, and its lifecycle is audited like other PR
    # configuration. ``ACTIVATED`` is the row that matters and is deliberately
    # separate from ``UPDATED``: activation is the moment a manager's standing
    # authorization begins, and every job the template later generates is filed
    # under the person named in it. "Who asked for a year of this routine" is
    # not a question a generic "updated" event could answer.
    PR_WORK_RECURRING_TEMPLATE_CREATED = "pr.work_recurring_template.created"
    PR_WORK_RECURRING_TEMPLATE_UPDATED = "pr.work_recurring_template.updated"
    PR_WORK_RECURRING_TEMPLATE_ACTIVATED = "pr.work_recurring_template.activated"
    PR_WORK_RECURRING_TEMPLATE_PAUSED = "pr.work_recurring_template.paused"
    PR_WORK_RECURRING_TEMPLATE_RESUMED = "pr.work_recurring_template.resumed"
    PR_WORK_RECURRING_TEMPLATE_ENDED = "pr.work_recurring_template.ended"
    PR_WORK_RECURRING_TEMPLATE_DELETED = "pr.work_recurring_template.deleted"
    #: One occurrence became work. Carries the occurrence key, the template
    #: revision that produced it and the codes of the items - the provenance
    #: that answers "why does this job exist and who authorised it".
    PR_WORK_RECURRING_GENERATED = "pr.work_recurring.generated"
    #: An occurrence that fell in a closed or locked reporting period. Recorded
    #: rather than logged, because a routine that produced nothing in a month is
    #: exactly the thing somebody asks about afterwards.
    PR_WORK_RECURRING_SKIPPED = "pr.work_recurring.skipped"
    # --- M2: quota eligibility -------------------------------------------
    # One action per decision that changes **whose work is eligible for KPI**.
    # Finer-grained than the plan's status ladder for the same reason M1's are:
    # "who raised this cap in the middle of the month" is the question an
    # argument about somebody's KPI is settled from, and a single
    # ``pr.work_plan.updated`` could not answer it.
    #
    # There is no separate domain history table for plans. The version chain -
    # ``supersedes_plan_id``, ``approved_by_user_id``, ``approved_at`` - plus the
    # allocation's own ``work_plan_id`` / ``work_quota_id`` provenance already
    # answers "which version decided this and what replaced it", and a second
    # table repeating these payloads would be two records to keep in step.
    PR_WORK_PERIOD_CREATED = "pr.work_period.created"
    PR_WORK_PLAN_CREATED = "pr.work_plan.created"
    PR_WORK_PLAN_QUOTA_ADDED = "pr.work_plan.quota_added"
    PR_WORK_PLAN_QUOTA_UPDATED = "pr.work_plan.quota_updated"
    PR_WORK_PLAN_QUOTA_REMOVED = "pr.work_plan.quota_removed"
    #: The one that matters. Approving a plan is what turns a target somebody
    #: typed into the cap an employee's counted work is measured against, so who
    #: did it, for whom and for which month is the row that settles a dispute.
    PR_WORK_PLAN_APPROVED = "pr.work_plan.approved"
    PR_WORK_PLAN_REVISED = "pr.work_plan.revised"
    PR_WORK_PLAN_SUPERSEDED = "pr.work_plan.superseded"
    PR_WORK_PLAN_DISCARDED = "pr.work_plan.discarded"
    #: KPI self-service. The employee handed their draft to the manager, and
    #: the manager sent it back. Approval keeps ``PR_WORK_PLAN_APPROVED``.
    PR_WORK_PLAN_SUBMITTED = "pr.work_plan.submitted"
    PR_WORK_PLAN_RETURNED = "pr.work_plan.returned"
    #: An explicit recompute of an **open** period. Recorded because it can move
    #: a contribution between ``ELIGIBLE`` and ``OVER_QUOTA``, and somebody
    #: asking why their figure changed overnight needs to find the request that
    #: did it.
    PR_WORK_ELIGIBILITY_RECONCILED = "pr.work.eligibility_reconciled"
    # --- M3: content -> work projection ----------------------------------
    # The projector's own trail. Every one of these names the **source** as well
    # as the work, because the question somebody will actually ask is "why does
    # my KPI say I wrote this" and the answer is a content code, not a work id.
    PR_CONTENT_WORK_RULE_CREATED = "pr.content_work_rule.created"
    PR_CONTENT_WORK_RULE_UPDATED = "pr.content_work_rule.updated"
    #: A content milestone became a work item. Written once per semantic result,
    #: never per projector run: a re-run that changes nothing writes nothing.
    PR_CONTENT_WORK_PROJECTED = "pr.content_work.projected"
    #: The source validated it independently, so it counted. **The one that
    #: matters** - this is the row an argument about a KPI figure is settled
    #: from, and it names the content, the milestone and the human who approved
    #: it at source.
    PR_WORK_SOURCE_COUNTED = "pr.work.source_counted"
    #: Projected and deliberately left uncounted: the only person who validated
    #: the source milestone is the person it credits. Recorded rather than
    #: silent, because "why is this not counted" has to have an answer.
    PR_CONTENT_WORK_PENDING_VALIDATION = "pr.content_work.pending_validation"
    #: The source withdrew the milestone and the work left the count.
    PR_WORK_SOURCE_REVERSED = "pr.work.source_reversed"
    #: The source and the work disagree, and the reporting period is ``CLOSED``
    #: or ``LOCKED`` so the disagreement stands. Historical performance is not
    #: rewritten; it is **recorded that it was not**.
    PR_CONTENT_WORK_BLOCKED = "pr.content_work.blocked"
    #: Somebody asked for a bounded catch-up over content that already happened.
    PR_CONTENT_WORK_RECONCILED = "pr.content_work.reconciled"
    #: **Period containers and results.** ``0039``. A stream for one employee,
    #: one work type and one month was created - by a routine, by a report, or
    #: by the content projector needing somewhere to put a result.
    PR_WORK_CONTAINER_CREATED = "pr.work_container.created"
    #: One result declared into a stream. The payload names the quantity, the
    #: source and the reporter; it decides nothing about credit.
    PR_WORK_RESULT_REPORTED = "pr.work_result.reported"
    #: A validator who is not the subject counted results, or the source's own
    #: independent validator did. This is the act that moves the actual.
    PR_WORK_RESULT_COUNTED = "pr.work_result.counted"
    #: A result was taken out of the actual because its **source** withdrew the
    #: fact underneath it - the projector reversing an undone approval, or a
    #: rebuild moving a wrongly-typed result. The payload's ``exclusion_kind``
    #: says so.
    PR_WORK_RESULT_EXCLUDED = "pr.work_result.excluded"
    #: A validator reviewed a result and refused to count it. ``0041``. The
    #: decision the projector must not reverse; the payload carries the note.
    PR_WORK_RESULT_REJECTED = "pr.work_result.rejected"
    #: A validator released a rejected result back to ``PENDING``. The only act
    #: that lifts a rejection; the earlier rejection stays in the trail.
    PR_WORK_RESULT_RECONSIDERED = "pr.work_result.reconsidered"
    #: The reporter withdrew their own pending result before anybody counted it.
    PR_WORK_RESULT_WITHDRAWN = "pr.work_result.withdrawn"
    #: Work maintenance. **Administrative cleanup, ``PR_WORK_CONFIGURE`` only.**
    #: A "requested" row carries the scope and the preview counts; a "completed"
    #: row carries what was actually written. Counts and small samples, never
    #: row dumps.
    PR_WORK_CONTENT_SYNC_REQUESTED = "pr.work.content_sync_requested"
    PR_WORK_CONTENT_SYNC_COMPLETED = "pr.work.content_sync_completed"
    PR_WORK_CONTENT_REBUILD_REQUESTED = "pr.work.content_rebuild_requested"
    PR_WORK_CONTENT_REBUILD_COMPLETED = "pr.work.content_rebuild_completed"
    #: An administrator took a result - counted or not, from any source - out of
    #: the actual. Distinct from a validator's exclusion so the trail says who
    #: acted under which authority.
    PR_WORK_RESULT_ADMIN_REMOVED = "pr.work.result_admin_removed"
    #: An administrator removed an empty generated period container.
    PR_WORK_CONTAINER_ADMIN_REMOVED = "pr.work.container_admin_removed"
    #: An administrator deleted one **legacy, item-grain** content work item -
    #: the pre-``0039`` shape - outright. Written against the item's id after
    #: the row is gone, with a summary of what went with it, so the trail keeps
    #: what the ledger no longer holds. Never followed by a projection.
    PR_WORK_ITEM_ADMIN_DELETED = "pr.work.item_admin_deleted"
    #: A work type with no references left was deleted outright.
    PR_WORK_TYPE_DELETED = "pr.work_type.deleted"
    #: A draft quota was moved to another kind of work.
    PR_WORK_PLAN_QUOTA_WORK_TYPE_CHANGED = "pr.work_plan.quota_work_type_changed"
    PR_PLATFORM_CREATED = "pr.platform.created"
    PR_CHANNEL_CREATED = "pr.channel.created"
    PR_CHANNEL_UPDATED = "pr.channel.updated"
    PR_CHANNEL_ASSIGNED = "pr.channel.assigned"
    PR_CHANNEL_ASSIGNMENT_CLOSED = "pr.channel.assignment_closed"
    #: Step 1F.2.4a. One hand-entered reading of a channel's numbers, appended
    #: to the channel metric snapshot history. The payload names the channel,
    #: the snapshot and the instant it claims - not the numbers themselves and
    #: not the platform-specific extras: the snapshot row is append-only and is
    #: itself the record of what was entered, so copying the figures into the
    #: audit trail would store them twice and give somebody two places to read
    #: the same reading from.
    PR_CHANNEL_METRICS_RECORDED = "pr.channel.metrics.recorded"
    #: Step 1F.2.4b. A channel connector's lifecycle and its runs.
    #:
    #: Every payload on these six is deliberately identity and outcome only:
    #: which channel, which provider, which platform account, who asked, and how
    #: it went. **No token, no authorization code, no provider response body.**
    #: Scope *names* do appear on the connect events and are safe - they are what
    #: an operator consented to, and a missing one is how an insufficient-scope
    #: failure gets diagnosed months later.
    PR_CHANNEL_CONNECTION_CONNECTED = "pr.channel.connection.connected"
    PR_CHANNEL_CONNECTION_RECONNECTED = "pr.channel.connection.reconnected"
    PR_CHANNEL_CONNECTION_DISCONNECTED = "pr.channel.connection.disconnected"
    #: Somebody pressed "Đồng bộ ngay". Recorded separately from the outcome
    #: because "who asked for this" and "what came back" are different facts,
    #: and the metric row itself deliberately does not name a person - an API
    #: reading has no human author. See ``PrChannelSyncService``.
    PR_CHANNEL_SYNC_REQUESTED = "pr.channel.sync.requested"
    PR_CHANNEL_SYNC_SUCCEEDED = "pr.channel.sync.succeeded"
    PR_CHANNEL_SYNC_FAILED = "pr.channel.sync.failed"
    #: Step 1F.2.3f.2. Where a handed-in production file lives, corrected. The
    #: **one** event that writes to an otherwise append-only table, so it is
    #: named for what it is - a correction - rather than a generic update, and
    #: ``before``/``after`` carry only the fields that moved. Never
    #: ``submission_no``, ``content_version_id`` or either person: those cannot
    #: change, so they appear on neither side.
    PR_PRODUCTION_SUBMISSION_CORRECTED = "pr.production.submission_corrected"
    PR_PUBLICATION_REGISTERED = "pr.publication.registered"
    #: Step 1F.2.3f.1. A recorded publication corrected in place - the live URL,
    #: the instant it went out, or the note beside it. ``before`` carries only
    #: the fields that actually moved, so a reader sees the change rather than a
    #: copy of the row, and neither side ever carries content.
    #:
    #: The channel and the produced output are **not** correctable and therefore
    #: never appear here: they define what the row means, and fixing a wrong one
    #: is a reversal and a new publication rather than a rewrite of history.
    PR_PUBLICATION_UPDATED = "pr.publication.updated"
    #: Step 1F.2.3f.1. A publication taken back as entered in error. The row
    #: survives - this is a correction, not an erasure - and ``after`` says
    #: whether the content's stage went back with it and, when it did not, why.
    PR_PUBLICATION_REVERSED = "pr.publication.reversed"
    # --- PR production and lifecycle, Step 1F.2.3 --------------------------
    #: A content aggregate was destroyed. Step 1F.2.3a: the delete is permanent,
    #: so this row is the **only** thing left of the piece - which is why it
    #: carries the code, the title and the stage as plain text rather than
    #: pointing at a row that no longer exists. ``audit_logs.entity_id`` is a
    #: ``String`` with no foreign key, which is what makes that possible.
    PR_CONTENT_DELETED_PERMANENTLY = "pr.content.deleted_permanently"
    #: Management said who produces a piece, or took it off them. ``after``
    #: carries both the previous and the new producer, because "reassigned" and
    #: "assigned for the first time" are different facts about a team.
    PR_PRODUCTION_ASSIGNED = "pr.production.assigned"
    #: Somebody took an unclaimed production for themselves. Distinct from
    #: ``assigned`` on purpose: one is a manager's decision about somebody else,
    #: the other is a person volunteering, and a report that could not tell them
    #: apart would misread how work is actually distributed.
    PR_PRODUCTION_CLAIMED = "pr.production.claimed"
    #: A cut was handed over for internal review.
    PR_PRODUCTION_SUBMITTED = "pr.production.submitted"
    # --- PR workflow undo, Step 1F.2.3b ------------------------------------
    #: Somebody took back the decision they had just made. The original decision
    #: keeps its own event: this is a second line in the trail, not a correction
    #: of the first, which is the whole difference between an undo and an edit.
    PR_WORKFLOW_UNDONE = "pr.workflow.undone"
    # --- PR authorization, Step 1C.1 ---------------------------------------
    #: Who may review at which gate is a decision somebody makes, so it is
    #: recorded the same way every other PR decision is.
    PR_CAPABILITY_GRANTED = "pr.capability.granted"
    PR_CAPABILITY_REVOKED = "pr.capability.revoked"
    # --- Web admin sessions, Step 1E ---------------------------------------
    #: Who signed in to the browser panel, when, and from where. The PR actions
    #: above record *what* somebody decided; these record how they came to be
    #: the person making the decision - which is the question asked after an
    #: approval nobody remembers making.
    WEB_LOGIN_LINK_ISSUED = "web.login.link_issued"
    WEB_LOGIN_REDEEMED = "web.login.redeemed"
    WEB_LOGIN_REJECTED = "web.login.rejected"
    WEB_SESSION_REVOKED = "web.session.revoked"
    # --- Web password login (0045) ------------------------------------------
    #: Never with the password, the hash or the default in the payload.
    AUTH_PASSWORD_LOGIN_SUCCEEDED = "auth.password_login.succeeded"  # noqa: S105
    AUTH_PASSWORD_LOGIN_FAILED = "auth.password_login.failed"  # noqa: S105
    AUTH_PASSWORD_CHANGED = "auth.password.changed"  # noqa: S105
    AUTH_PASSWORD_CHANGE_FAILED = "auth.password.change_failed"  # noqa: S105
    AUTH_PASSWORD_RESET = "auth.password.reset"  # noqa: S105
    #: Self-service "Quên mật khẩu?" (0046). ``after_data.outcome`` says what
    #: happened (sent / unknown / rate limited / undeliverable); never the
    #: temporary password.
    AUTH_PASSWORD_RESET_REQUESTED = "auth.password.reset_requested"  # noqa: S105
    USER_PROFILE_UPDATED = "user.profile.updated"
    #: Profile picture (0047). Type, size and version only - never the image.
    USER_AVATAR_UPDATED = "user.avatar.updated"
    USER_AVATAR_REMOVED = "user.avatar.removed"

    # Units (PR / Ads) and the Ads order engine.
    UNIT_MEMBER_TAGGED = "unit.member.tagged"
    UNIT_MEMBER_UPDATED = "unit.member.updated"
    UNIT_MEMBER_UNTAGGED = "unit.member.untagged"
    UNIT_SETTINGS_UPDATED = "unit.settings.updated"
    UNIT_VIDEO_KIND_CREATED = "unit.video_kind.created"
    UNIT_VIDEO_KIND_UPDATED = "unit.video_kind.updated"
    ORDER_SUBMITTED = "order.submitted"
    ORDER_RESUBMITTED = "order.resubmitted"
    ORDER_APPROVED = "order.approved"
    ORDER_RETURNED = "order.returned"
    ORDER_NODE_ASSIGNED = "order.node.assigned"
    ORDER_NODE_ACCEPTED = "order.node.accepted"
    ORDER_NODE_SUBMITTED = "order.node.submitted"
    ORDER_NODE_APPROVED = "order.node.approved"
    ORDER_NODE_RETURNED = "order.node.returned"
    ORDER_LINK_ATTACHED = "order.link.attached"
    ORDER_VIDEO_APPROVED = "order.video.approved"
    ORDER_VIDEO_RETURNED = "order.video.returned"
    ORDER_FINAL_APPROVED = "order.final.approved"
    ORDER_FINAL_RETURNED = "order.final.returned"
    ORDER_PRIORITY_CHANGED = "order.priority.changed"
    ORDER_CANCELLED = "order.cancelled"


class AuditEntry(BaseModel):
    """One immutable line of the audit trail."""

    model_config = ConfigDict(frozen=True)

    request_id: uuid.UUID
    action: str = Field(min_length=1, max_length=100)
    result: AuditResult
    actor_user_id: uuid.UUID | None = None
    actor_telegram_id: int | None = None
    entity_type: str | None = Field(default=None, max_length=100)
    entity_id: str | None = Field(default=None, max_length=200)
    before_data: dict[str, Any] | None = None
    after_data: dict[str, Any] | None = None
    error_message: str | None = Field(default=None, max_length=2000)
