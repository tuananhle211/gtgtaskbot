"""Assembling the PR services, once, for every client.

Step 1E added a second client. Before it, the wiring lived in
``meobot.tools.pr_support.pr_services`` and took a Telegram ``ToolContext`` -
fine while Telegram was the only caller, and exactly the wrong place for the web
API to import from.

So the bundle moved here, where neither transport owns it:

```
Telegram tool  ─┐
                ├─→ build_pr_services(session, settings) ─→ Pr* services ─→ PostgreSQL
Web API route  ─┘
```

Why one function rather than two similar ones
---------------------------------------------

The dependency graph between these services is not obvious - ``PrApprovalService``
needs the AI review service, which needs the content and workflow services, which
both need capabilities. A second copy of that graph would look correct and would
eventually differ in one edge, and the edge that matters is
``PrApprovalService(…, ai_reviews, …)``: without it, an approval can be recorded
for a draft no AI review ever judged. One constructor, one graph, and a test that
asserts the API and the Telegram tools get the same wiring.

Every service in the bundle shares the **one session** passed in, so a request
that resolves a code, allocates a number and writes a row does all three in the
caller's single transaction. Nothing here commits.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_action_service import PrAvailableActionService
from meobot.application.pr_ai_review_run_service import PrAiReviewRunService
from meobot.application.pr_ai_review_service import PrAiReviewService
from meobot.application.pr_approval_service import PrApprovalService
from meobot.application.pr_bulk_approval_service import PrBulkApprovalService
from meobot.application.pr_bulk_archive_service import PrBulkArchiveService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_channel_connection_service import PrChannelConnectionService
from meobot.application.pr_channel_metrics_service import PrChannelMetricsService
from meobot.application.pr_channel_service import PrChannelService
from meobot.application.pr_channel_sync_service import PrChannelSyncService
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_content_asset_service import PrContentAssetService
from meobot.application.pr_content_comment_service import PrContentCommentService
from meobot.application.pr_content_resource_service import PrContentResourceService
from meobot.application.pr_content_service import PrContentService
from meobot.application.pr_content_work_projector import PrContentWorkProjector
from meobot.application.pr_content_work_service import PrContentWorkRuleService
from meobot.application.pr_lifecycle_service import PrContentLifecycleService
from meobot.application.pr_notifications import PrNotificationService
from meobot.application.pr_people_resolver import PrPeopleResolver
from meobot.application.pr_performance_config_service import (
    PrPerformancePolicyService,
    PrWorkScoringRuleService,
)
from meobot.application.pr_performance_review_service import PrPerformanceReviewService
from meobot.application.pr_performance_service import PrPerformanceService
from meobot.application.pr_performance_target_service import PrPerformanceTargetService
from meobot.application.pr_platform_service import PrPlatformService
from meobot.application.pr_policy_pack_service import PrPolicyPackService
from meobot.application.pr_policy_readiness_service import PrPolicyReadinessService
from meobot.application.pr_policy_source_service import PrPolicySourceService
from meobot.application.pr_production_service import PrProductionService
from meobot.application.pr_publication_service import PrPublicationService
from meobot.application.pr_query_service import PrQueryService
from meobot.application.pr_task_service import PrTaskService
from meobot.application.pr_tiktok_account_service import PrTikTokAccountService
from meobot.application.pr_undo_service import PrWorkflowUndoService
from meobot.application.pr_work_bulk_validation_service import PrWorkBulkValidationService
from meobot.application.pr_work_maintenance_service import PrWorkMaintenanceService
from meobot.application.pr_work_notifications import PrWorkNotifier
from meobot.application.pr_work_period_service import PrWorkPeriodService
from meobot.application.pr_work_plan_service import PrWorkPlanService
from meobot.application.pr_work_query_service import PrWorkQueryService
from meobot.application.pr_work_quota_service import PrWorkQuotaEligibilityService
from meobot.application.pr_work_readiness_service import PrWorkReadinessService
from meobot.application.pr_work_recurring_generator import PrWorkRecurringGenerator
from meobot.application.pr_work_recurring_service import PrWorkRecurringService
from meobot.application.pr_work_result_service import PrWorkResultService
from meobot.application.pr_work_service import PrWorkService
from meobot.application.pr_workflow_service import PrContentWorkflowService
from meobot.application.user_notification_service import UserNotificationService
from meobot.core.config import Settings


@dataclass(slots=True)
class PrServices:
    """Every PR service, sharing one session and one transaction."""

    session: AsyncSession
    #: The deployment's configuration. On the bundle since Step 1F.2.4b, because
    #: a route now has to answer "is the YouTube connector configured here" and
    #: reaching for ``get_settings()`` inside a request would read a *different*
    #: object from the one the services were built with.
    settings: Settings
    audit: AuditService
    capabilities: PrCapabilityService
    codes: PrCodeService
    content: PrContentService
    #: Review material attached to content. Step 1F.2.3e.
    content_resources: PrContentResourceService
    #: Derivative production outputs and destination links. Step 1F.2.3f.
    content_assets: PrContentAssetService
    #: The conversation around a content item. Step 1F.2.3g. Deliberately holds
    #: no workflow service: a comment cannot move anything, and the wiring is
    #: what makes that structural.
    content_comments: PrContentCommentService
    workflow: PrContentWorkflowService
    ai_reviews: PrAiReviewService
    #: Durable AI-review executions. Step 1F.
    ai_review_runs: PrAiReviewRunService
    #: Official platform policy. Step 1F.1.
    policy_sources: PrPolicySourceService
    policy_packs: PrPolicyPackService
    policy_readiness: PrPolicyReadinessService
    approvals: PrApprovalService
    #: Approving a whole batch at one gate, or none of it. Step 1F.2.8. Holds
    #: ``approvals`` and writes every decision through it, so a bulk approval is
    #: the single approval repeated under one transaction rather than a second
    #: implementation of one.
    bulk_approvals: PrBulkApprovalService
    #: Archiving one closed month's published output as one batch, or none of
    #: it. Step 1F.2.3f.6. Holds ``workflow`` and moves every item through its
    #: ``request_transition``, so the batch is the detail page's *"Lưu trữ nội
    #: dung"* repeated under one transaction rather than a second lifecycle.
    bulk_archive: PrBulkArchiveService
    #: Producer assignment and production submissions. Step 1F.2.3.
    production: PrProductionService
    #: Deleting content, auditably. Step 1F.2.3.
    lifecycle: PrContentLifecycleService
    #: Taking back the last reversible decision. Step 1F.2.3b.
    undo: PrWorkflowUndoService
    #: Workflow events becoming Telegram messages. Step 1F.2.3b.
    notifications: PrNotificationService
    tasks: PrTaskService
    channels: PrChannelService
    #: Hand-entered channel readings, and the current picture derived from the
    #: latest one. Step 1F.2.4a. Holds no HTTP client: this step ships the
    #: provider *port* and no provider, so nothing here can reach a platform.
    channel_metrics: PrChannelMetricsService
    #: The credential half of a channel connector - OAuth, tokens, disconnect.
    #: Step 1F.2.4b. The **only** service that decrypts a refresh token.
    channel_connections: PrChannelConnectionService
    #: Fetching a channel's numbers from its platform. Step 1F.2.4b. Holds no
    #: HTTP client of its own: it asks the registry for a provider, so a test
    #: passes a fake and no test in this repository contacts Google.
    channel_sync: PrChannelSyncService
    #: One live look at a connected TikTok account, for the panel a person is
    #: reading right now. Step 1F.2.9. Reads through ``channel_connections`` for
    #: the credential and ``channel_sync`` for the snapshot, so it adds no
    #: second credential store and no second sync - see
    #: :mod:`meobot.application.pr_tiktok_account_service`.
    tiktok_account: PrTikTokAccountService
    #: The Work Ledger's writes: creating work, moving it, and the one act that
    #: makes it count. M1. Additive - it reads ``pr_content_items`` and
    #: ``pr_tasks`` never, and writes to neither.
    work: PrWorkService
    #: **M4A.** Validating a queue of finished work in one act. Holds ``work``
    #: and writes every validation through its ``approve``, so a batch and a
    #: single confirmation are the same write with the same anti-gaming rule -
    #: this object decides only *which* jobs are in the batch.
    work_bulk_validation: PrWorkBulkValidationService
    #: **M4A.** A read-only diagnostic: whether counted work of this kind would
    #: reach a KPI quota and an M6 rate. Decides nothing and blocks nothing -
    #: the creation screen says it out loud so nobody assigns real work
    #: believing it will show up on a performance report when it will not.
    work_readiness: PrWorkReadinessService
    #: The Work Ledger's reads. Separate from ``work`` because the two answer
    #: different questions - one decides, one reports - and a period figure must
    #: never be computed by the object that can change what it counts.
    work_queries: PrWorkQueryService
    #: M2. The reporting periods a KPI plan is written for. The **only** place a
    #: date becomes a period, so the module has one calendar rather than two.
    work_periods: PrWorkPeriodService
    #: M2. **The only place eligibility is decided.** Held by ``work`` too, so
    #: an approval projects through the same object a reconcile does - one
    #: implementation of the allocation rule, not two that could drift.
    work_eligibility: PrWorkQuotaEligibilityService
    #: M2. KPI plans and their quotas: draft, approve, revise. Separate from
    #: ``work_eligibility`` because the two answer different questions - one
    #: decides what the targets are, the other decides what they imply - and a
    #: quota must never be changed by the object that evaluates it.
    work_plans: PrWorkPlanService
    #: M3. Which work type a content milestone counts as - the **one**
    #: administratively configurable part of the projection. The milestones and
    #: the independent-validation boundary stay in the domain, because those are
    #: the anti-gaming rules rather than the taxonomy.
    content_work_rules: PrContentWorkRuleService
    #: M3. **The only place content becomes work.** State-convergent: it reads
    #: what the workflow currently says and makes the ledger match, so a retry,
    #: an undo and a redo all reach the same answer. Decides no eligibility - it
    #: hands ``COUNTED`` work to M2 and never reads what M2 concluded.
    content_work: PrContentWorkProjector
    #: **M4B.** Recurring templates: what a routine is, who it is for, when it
    #: fires. It generates nothing - see ``work_recurring_generator``, which
    #: does - and the split is the same one M2 made between the plan and the
    #: evaluator: what a manager may *say* and what a worker may *do at 4am* are
    #: argued about separately.
    work_recurring: PrWorkRecurringService
    #: **M4B.** The sweep. Holds the same ``work`` instance every other path
    #: writes through, so a generated job goes through M1's own creation entry
    #: point rather than a private one - which is what makes M2 and M6 need no
    #: recurring-specific code at all.
    work_recurring_generator: PrWorkRecurringGenerator
    #: Period-container patch. Streams and the results reported into them.
    #: Holds the same ``work`` instance for the reason the projector and the
    #: generator do: one timeline writer, one lock, one M2 handoff.
    work_results: PrWorkResultService
    #: M6. What a kind of work is worth in standard minutes, versioned.
    work_scoring_rules: PrWorkScoringRuleService
    #: M6. The weights, barems, gate and bands, versioned.
    performance_policies: PrPerformancePolicyService
    #: M6. **One monthly manager review per person.** Not one per work item.
    performance_reviews: PrPerformanceReviewService
    #: M6. The workday calendar behind ``target_standard_minutes``.
    performance_targets: PrPerformanceTargetService
    #: M6. Workload projection, the index, and finalisation.
    performance: PrPerformanceService
    #: Work maintenance. **``PR_WORK_CONFIGURE`` only.** Content sync and
    #: rebuild through the same projector the worker runs, administrative
    #: removals, and deleting a work type nothing refers to.
    work_maintenance: PrWorkMaintenanceService
    #: Platform master data. Step 1F.2.1.
    platforms: PrPlatformService
    publications: PrPublicationService
    queries: PrQueryService
    people: PrPeopleResolver
    #: Read-only. What a client may offer, decided from the same matrix and the
    #: same capability service every write consults.
    actions: PrAvailableActionService


def build_pr_services(session: AsyncSession, settings: Settings) -> PrServices:
    """Build the PR service bundle on a caller-owned session.

    Constructed per call rather than cached: a service holds the session it was
    given, and a bundle that outlived one transaction would write into a closed
    one.

    Args:
        session: The session the caller's transaction owns.
        settings: Read by :class:`PrCodeService` for the timezone that decides
            which year a code belongs to.
    """
    audit = AuditService(session)
    capabilities = PrCapabilityService(session, audit)
    codes = PrCodeService(session, settings)
    content = PrContentService(session, audit, capabilities, codes)
    # Step 1F: the workflow queues an AI review when it enters ``AI_REVIEW``,
    # so the run service is built first and handed to it. Both clients reach
    # the workflow through this function, which is what makes the trigger
    # transport-independent rather than something each caller remembers.
    ai_review_runs = PrAiReviewRunService(session, max_attempts=settings.pr_ai_review_max_attempts)
    # Step 1F.1: the workflow asks readiness before entering AI_REVIEW and pins
    # the packs it resolved, so both happen on the one authoritative path.
    policy_packs = PrPolicyPackService(session)
    policy_readiness = PrPolicyReadinessService(session, policy_packs)
    workflow = PrContentWorkflowService(
        session, audit, capabilities, ai_review_runs, policy_readiness
    )
    ai_reviews = PrAiReviewService(session, audit, content, workflow)
    # Step 1F.2.3. Built before the approvals because an ``INTERNAL_REVIEW``
    # decision has to name the cut it judged, and this is what knows which cut
    # that is. There is no cycle: production writes submissions and moves the
    # stage, and never records a decision.
    # Step 1F.2.3b. Built before the services that raise the events, and given
    # the same session: a notification is queued in the transaction carrying the
    # decision it announces, so an approval that rolls back takes its message
    # with it.
    notifications = PrNotificationService(session, settings)
    production = PrProductionService(session, audit, content, workflow, capabilities, notifications)
    publications = PrPublicationService(session, audit, workflow, capabilities, codes, content)
    lifecycle = PrContentLifecycleService(session, audit, workflow, capabilities)
    undo = PrWorkflowUndoService(session, audit, workflow, capabilities, notifications)
    # The ``ai_reviews`` argument is the load-bearing one: it is how an approval
    # learns whether the draft in front of it was ever judged. Named rather than
    # inline because the action read model asks the same service the same
    # question before it offers a decision.
    approvals = PrApprovalService(
        session, audit, content, workflow, ai_reviews, capabilities, production, notifications
    )
    # Step 1F.2.3g. Named rather than inline for the reason ``ai_reviews`` is:
    # the action read model asks both of them the same question the writes ask,
    # so the panel offers exactly what the routes accept.
    content_assets = PrContentAssetService(session, audit, capabilities, content, production)
    # Step 1F.2.4b. Named rather than inline because the sync service is given
    # the same instance: one place decrypts a refresh token, and the sync path
    # asks it for an access token rather than holding a second secret box.
    channel_connections = PrChannelConnectionService(session, audit, capabilities, settings)
    # Step 1F.2.9. Named for the same reason ``channel_connections`` is: the
    # TikTok account panel asks *this* instance for a snapshot sync, so
    # "Đồng bộ lại" and "Đồng bộ ngay" go through one claim, one audit action
    # and one lock rather than two that could drift apart.
    channel_sync = PrChannelSyncService(session, audit, capabilities, channel_connections, settings)
    content_comments = PrContentCommentService(session, audit, capabilities, content)
    # M2. Built before the work service and named, because three objects need
    # the *same* evaluator: the approval path projects through it, the plan
    # service recomputes through it when a plan is approved, and the reconcile
    # endpoint calls it directly. Three instances would be three copies of the
    # allocation rule waiting to disagree.
    work_periods = PrWorkPeriodService(session, audit, capabilities, timezone=settings.timezone)
    work_eligibility = PrWorkQuotaEligibilityService(session, audit, capabilities, work_periods)
    # M3. Named rather than inline because the **same** work service instance has
    # to be the one the projector writes through: it owns the ladder, the
    # ``counted_at`` and the M2 handoff, and a second instance would be a second
    # implementation of the order those three happen in.
    work_notifier = PrWorkNotifier(UserNotificationService(session))
    work = PrWorkService(
        session,
        audit,
        capabilities,
        codes,
        # In-app notifications only. No Telegram and no scheduler: M1 adds no
        # beat job and no template.
        work_notifier,
        # M2. The same evaluator instance the plan service and the reconcile
        # endpoint use, so "eligibility" means one thing however it was reached.
        work_eligibility,
        # The finalised-performance guard: ``approve`` asks the period service
        # whether the contributors' month has been agreed before it counts.
        periods=work_periods,
    )
    content_work_rules = PrContentWorkRuleService(session, audit, capabilities)
    # M4B. The generator holds the **same** ``work`` instance the routes do, for
    # the reason the M3 projector holds it: M1 owns the ladder and the M2
    # handoff, and a generator with its own creation path would be a second
    # implementation of what "assigned work" means.
    # M6. Built in dependency order: the calculation service needs the rate, the
    # policy, the review and the calendar, and holds the same instances the API
    # routes use so that a preview and a finalisation cannot resolve different
    # configuration.
    work_scoring_rules = PrWorkScoringRuleService(session, audit, capabilities)
    # Period containers. Built after the rate service because a work card
    # prices a stream's actual through M6's own ``rule_for``, and before the
    # routines, the generator and the projector because all three create
    # containers through it.
    work_results = PrWorkResultService(
        session,
        audit,
        capabilities,
        codes,
        work_periods,
        work,
        eligibility=work_eligibility,
        scoring_rules=work_scoring_rules,
    )
    work_recurring = PrWorkRecurringService(
        session,
        audit,
        capabilities,
        timezone=settings.timezone,
        results=work_results,
        periods=work_periods,
    )
    work_recurring_generator = PrWorkRecurringGenerator(
        session, audit, work, work_periods, work_results, timezone=settings.timezone
    )
    performance_policies = PrPerformancePolicyService(session, audit, capabilities)
    performance_reviews = PrPerformanceReviewService(session, audit, capabilities)
    performance_targets = PrPerformanceTargetService(session)
    # The configured business timezone, because the content filters take
    # calendar days from somebody looking at a Vietnamese calendar and the
    # columns are UTC. Defaulting it inside the service would put "Hôm nay"
    # seven hours out of step with the day people are having.
    queries = PrQueryService(session, capabilities, timezone=settings.timezone)
    performance = PrPerformanceService(
        session,
        audit,
        capabilities,
        work_scoring_rules,
        performance_policies,
        performance_reviews,
        performance_targets,
        timezone=settings.timezone,
    )
    # M3. Built last of the work services because it holds three of them: the
    # mapping it resolves through, the work service it writes through, and the
    # period service it asks before touching a month that may have been agreed.
    content_work = PrContentWorkProjector(
        session, audit, content_work_rules, work, work_periods, results=work_results
    )
    # And the projector answers the result service's one question back:
    # *does the source still support this row?* - asked before a validator
    # counts a content result, so a stale ``PENDING`` is never trusted.
    work_results.bind_source_truth(content_work)
    return PrServices(
        session=session,
        settings=settings,
        audit=audit,
        capabilities=capabilities,
        codes=codes,
        content=content,
        content_resources=PrContentResourceService(session, audit, capabilities, content),
        content_assets=content_assets,
        content_comments=content_comments,
        workflow=workflow,
        ai_reviews=ai_reviews,
        ai_review_runs=ai_review_runs,
        policy_sources=PrPolicySourceService(session),
        policy_packs=policy_packs,
        policy_readiness=policy_readiness,
        approvals=approvals,
        bulk_approvals=PrBulkApprovalService(
            session, audit, approvals, capabilities, content, workflow
        ),
        bulk_archive=PrBulkArchiveService(
            session, audit, capabilities, queries, workflow, timezone=settings.timezone
        ),
        production=production,
        lifecycle=lifecycle,
        undo=undo,
        notifications=notifications,
        actions=PrAvailableActionService(
            capabilities,
            workflow,
            approvals,
            content,
            policy_readiness,
            production,
            lifecycle,
            undo,
            # Step 1F.2.3f.2: publication offers come from the publication
            # service's own predicates, so the panel is told exactly what the
            # writes accept.
            publications,
            # Step 1F.2.3g: the two open contributions. Both are the view rule,
            # and both are asked of the service that enforces them rather than
            # re-derived - which is what stops the panel from concluding
            # "logged in, therefore may contribute".
            content_assets,
            content_comments,
        ),
        tasks=PrTaskService(session, audit, capabilities, codes),
        channels=PrChannelService(session, audit, capabilities, codes),
        channel_metrics=PrChannelMetricsService(session, audit, capabilities),
        channel_connections=channel_connections,
        channel_sync=channel_sync,
        tiktok_account=PrTikTokAccountService(
            session, capabilities, channel_connections, channel_sync, settings
        ),
        work=work,
        work_bulk_validation=PrWorkBulkValidationService(session, audit, work),
        work_readiness=PrWorkReadinessService(
            session, capabilities, work_periods, work_eligibility, work_scoring_rules
        ),
        work_queries=PrWorkQueryService(
            session, capabilities, work_periods, timezone=settings.timezone
        ),
        work_periods=work_periods,
        work_eligibility=work_eligibility,
        # KPI self-service: the plan service prices a proposal through M6's own
        # readers - rates, policy, resolved target - so the workload a manager
        # sees before approving is the arithmetic performance will do after.
        work_plans=PrWorkPlanService(
            session,
            audit,
            capabilities,
            work_periods,
            work_eligibility,
            scoring_rules=work_scoring_rules,
            policies=performance_policies,
            targets=performance_targets,
            notifier=work_notifier,
        ),
        content_work_rules=content_work_rules,
        work_recurring=work_recurring,
        work_recurring_generator=work_recurring_generator,
        work_results=work_results,
        work_scoring_rules=work_scoring_rules,
        performance_policies=performance_policies,
        performance_reviews=performance_reviews,
        performance_targets=performance_targets,
        performance=performance,
        content_work=content_work,
        # Holds the projector, the result service, the work service and the
        # performance service - the same instances the routes use - so a
        # rebuild is the worker's projection run under an administrator's
        # request, and nothing else.
        work_maintenance=PrWorkMaintenanceService(
            session,
            audit,
            capabilities,
            work_periods,
            content_work,
            work_results,
            work,
            performance,
            eligibility=work_eligibility,
        ),
        platforms=PrPlatformService(session, audit, capabilities),
        publications=publications,
        queries=queries,
        people=PrPeopleResolver(session),
    )


__all__: list[str] = ["PrServices", "build_pr_services"]
