"""Versioned, approved configuration: what work is worth, and how it is weighed. M6.

Two services with one shape, because they are the same kind of object: **numbers
that decide how somebody's month is judged**. Both are versioned, both are approved by a person,
and both become immutable the moment they can affect a figure. A change is a new
version with its own effective date, so last September keeps being scored the way
September was agreed.

Why immutability is not fastidiousness here
--------------------------------------------

An editable rate is a rate that can be changed after the month it priced. The
change would be invisible in the result - the number would simply be different
the next time anybody looked - and the person whose month changed would have no way
to find out why. Approval freezes the row; revision writes a new one; the result
records which one it used.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_support import record_pr_event
from meobot.core.time import utcnow
from meobot.db.models.pr_performance import PrPerformancePolicy, PrWorkScoringRule
from meobot.db.models.pr_work import PrWorkType
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import PrConflictError, PrNotFoundError, PrValidationError
from meobot.domain.pr.performance import (
    DEFAULT_BUSINESS_CONTRIBUTION_SCORES,
    DEFAULT_DAILY_TARGET_MINUTES,
    DEFAULT_PERFORMANCE_BANDS,
    DEFAULT_QUALITY_GATE,
    DEFAULT_QUALITY_SCORES,
    DEFAULT_TIMELINESS_SCORES,
    PrScoringRuleStatus,
    PrWorkScoringMode,
)
from meobot.domain.pr.policy import PrCapability


class PrWorkScoringRuleService:
    """What one kind of work is worth in standard minutes, over time.

    ``PR_WORK_CONFIGURE`` throughout - the same capability that owns the work
    taxonomy itself, because a rate is master data of exactly that kind. An
    employee cannot write one, and neither can a Trưởng nhóm holding only
    ``PR_WORK_MANAGE``.
    """

    def __init__(
        self, session: AsyncSession, audit: AuditService, capabilities: PrCapabilityService
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities

    async def create_draft(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_type_id: uuid.UUID,
        mode: PrWorkScoringMode,
        effective_from: date,
        standard_minutes_per_unit: Decimal | None = None,
        note: str | None = None,
    ) -> PrWorkScoringRule:
        """Draft a rate. Not in force until somebody approves it.

        ``standard_minutes_per_unit`` is required for ``STANDARD_MINUTES`` and
        refused for ``EXCLUDED_FROM_PERFORMANCE`` - *excluded* and *worth zero
        per unit* are different statements, and letting one be written as the
        other would make a deliberate exclusion indistinguishable from a typo.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        work_type = await self._session.get(PrWorkType, work_type_id)
        if work_type is None:
            raise PrNotFoundError(
                "Không tìm thấy loại công việc.",
                details={"field": "work_type_id", "reason": "work_type_not_found"},
            )

        if mode is PrWorkScoringMode.STANDARD_MINUTES:
            if standard_minutes_per_unit is None or standard_minutes_per_unit < 0:
                raise PrValidationError(
                    "Quy tắc tính theo phút chuẩn phải có số phút cho mỗi đơn vị.",
                    details={
                        "field": "standard_minutes_per_unit",
                        "reason": "minutes_required_for_standard_mode",
                    },
                )
        elif standard_minutes_per_unit is not None:
            raise PrValidationError(
                "Loại công việc được loại trừ khỏi hiệu suất thì không nhận số phút chuẩn.",
                details={
                    "field": "standard_minutes_per_unit",
                    "reason": "minutes_not_allowed_for_excluded_mode",
                },
            )

        row = PrWorkScoringRule(
            work_type_id=work_type_id,
            version_no=await self._next_version(work_type_id),
            mode=mode,
            standard_minutes_per_unit=standard_minutes_per_unit,
            effective_from=effective_from,
            status=PrScoringRuleStatus.DRAFT,
            note=note,
            created_by_user_id=actor.user_id,
        )
        self._session.add(row)
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_SCORING_RULE_CREATED,
            entity_type="pr_work_scoring_rule",
            entity_id=row.id,
            after={
                "work_type_code": work_type.code,
                "version_no": row.version_no,
                "mode": mode.value,
                "standard_minutes_per_unit": (
                    str(standard_minutes_per_unit)
                    if standard_minutes_per_unit is not None
                    else None
                ),
                "effective_from": effective_from.isoformat(),
            },
        )
        return row

    async def approve(
        self, *, actor: Actor, request_id: uuid.UUID, rule_id: uuid.UUID
    ) -> PrWorkScoringRule:
        """Put a drafted rate in force, and close the one it replaces.

        The overlap rule, enforced here rather than by a database exclusion
        constraint so that the refusal is a sentence: **two approved rules for
        one work type may not both be in force on one day**. If they could, the
        engine would have to pick one, and whichever it picked would be a rate
        nobody chose.

        The predecessor is closed the day before this one opens rather than
        deleted, so a contribution counted last month still finds the rate that
        priced it.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        rule = await self._require_rule(rule_id)
        if rule.status is not PrScoringRuleStatus.DRAFT:
            raise PrConflictError(
                "Chỉ bản nháp mới được duyệt.",
                details={
                    "field": "status",
                    "reason": "rule_not_draft",
                    "status": rule.status.value,
                },
            )

        current = await self.rule_for(rule.work_type_id, on=rule.effective_from)
        if current is not None and current.id != rule.id:
            if current.effective_from == rule.effective_from:
                raise PrConflictError(
                    "Đã có quy tắc hiệu lực cùng ngày cho loại công việc này.",
                    details={
                        "field": "effective_from",
                        "reason": "overlapping_effective_range",
                        "conflicting_rule_id": str(current.id),
                    },
                )
            current.effective_to = rule.effective_from - timedelta(days=1)
            current.status = PrScoringRuleStatus.SUPERSEDED
            rule.supersedes_rule_id = current.id
            await self._session.flush()
            await record_pr_event(
                self._audit,
                request_id=request_id,
                actor=actor,
                action=AuditAction.PR_WORK_SCORING_RULE_SUPERSEDED,
                entity_type="pr_work_scoring_rule",
                entity_id=current.id,
                before={"status": PrScoringRuleStatus.APPROVED.value, "effective_to": None},
                after={
                    "status": PrScoringRuleStatus.SUPERSEDED.value,
                    "effective_to": current.effective_to.isoformat(),
                    "superseded_by_rule_id": str(rule.id),
                },
            )

        # A later approved rule already in force means this draft would open a
        # window inside somebody else's - refused rather than silently nested.
        later = await self._approved_after(rule.work_type_id, rule.effective_from)
        if later is not None:
            raise PrConflictError(
                "Đã có quy tắc hiệu lực sau ngày này, nên không thể duyệt bản nháp cũ hơn.",
                details={
                    "field": "effective_from",
                    "reason": "overlapping_effective_range",
                    "conflicting_rule_id": str(later.id),
                },
            )

        rule.status = PrScoringRuleStatus.APPROVED
        rule.approved_by_user_id = actor.user_id
        rule.approved_at = utcnow()
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_SCORING_RULE_APPROVED,
            entity_type="pr_work_scoring_rule",
            entity_id=rule.id,
            after={
                "version_no": rule.version_no,
                "effective_from": rule.effective_from.isoformat(),
                "mode": rule.mode.value,
            },
        )
        return rule

    async def rules_for(
        self, work_type_ids: Iterable[uuid.UUID], *, on: date
    ) -> dict[uuid.UUID, PrWorkScoringRule]:
        """The rule in force on ``on`` for each work type, in **one** query.

        The batched form of :meth:`rule_for`, and the only place the predicate
        lives - ``rule_for`` delegates here, so a plan priced forty quotas at
        a time and a contribution priced one at a time cannot disagree about
        which version applied. Types with no rule in force are absent from the
        result rather than mapped to ``None``, so ``dict.get`` reads naturally.
        """
        ids = list(set(work_type_ids))
        if not ids:
            return {}
        statement = (
            select(PrWorkScoringRule)
            .where(
                PrWorkScoringRule.work_type_id.in_(ids),
                PrWorkScoringRule.status.in_(
                    (PrScoringRuleStatus.APPROVED, PrScoringRuleStatus.SUPERSEDED)
                ),
                PrWorkScoringRule.effective_from <= on,
                (PrWorkScoringRule.effective_to.is_(None)) | (PrWorkScoringRule.effective_to >= on),
            )
            # Newest effective date first, so the first row seen per type wins -
            # the same choice ``LIMIT 1`` made for one type.
            .order_by(PrWorkScoringRule.effective_from.desc(), PrWorkScoringRule.version_no.desc())
        )
        found: dict[uuid.UUID, PrWorkScoringRule] = {}
        for row in (await self._session.execute(statement)).scalars().all():
            found.setdefault(row.work_type_id, row)
        return found

    async def rule_for(self, work_type_id: uuid.UUID, *, on: date) -> PrWorkScoringRule | None:
        """The approved rate in force for this work type on ``on``.

        Chosen by the **contribution's own instant**, never by today: a
        recalculation in December must price September at September's rate, and
        this is the query that makes that true.
        """
        return (await self.rules_for((work_type_id,), on=on)).get(work_type_id)

    async def list_rules(
        self, *, actor: Actor, work_type_id: uuid.UUID | None = None
    ) -> list[PrWorkScoringRule]:
        """Every version, newest first. Configuration reading, so ``PR_WORK_CONFIGURE``."""
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        statement = select(PrWorkScoringRule)
        if work_type_id is not None:
            statement = statement.where(PrWorkScoringRule.work_type_id == work_type_id)
        statement = statement.order_by(
            PrWorkScoringRule.work_type_id, PrWorkScoringRule.version_no.desc()
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def _require_rule(self, rule_id: uuid.UUID) -> PrWorkScoringRule:
        row = await self._session.get(PrWorkScoringRule, rule_id)
        if row is None:
            raise PrNotFoundError(
                "Không tìm thấy quy tắc workload.",
                details={"field": "rule_id", "reason": "scoring_rule_not_found"},
            )
        return row

    async def _next_version(self, work_type_id: uuid.UUID) -> int:
        highest = await self._session.scalar(
            select(PrWorkScoringRule.version_no)
            .where(PrWorkScoringRule.work_type_id == work_type_id)
            .order_by(PrWorkScoringRule.version_no.desc())
            .limit(1)
        )
        return (highest or 0) + 1

    async def _approved_after(
        self, work_type_id: uuid.UUID, moment: date
    ) -> PrWorkScoringRule | None:
        statement = (
            select(PrWorkScoringRule)
            .where(
                PrWorkScoringRule.work_type_id == work_type_id,
                PrWorkScoringRule.status == PrScoringRuleStatus.APPROVED,
                PrWorkScoringRule.effective_from > moment,
            )
            .limit(1)
        )
        return (await self._session.execute(statement)).scalars().one_or_none()


class PrPerformancePolicyService:
    """The weights, caps, barems, gate and bands one month is judged under.

    Versioned like the rates, and for the identical reason. The defaults it
    drafts from are M6's documented V1: 50/30/10/10, a 120 workload cap, a 1.20
    coefficient cap, *Đạt* at exactly 100 on all three barems, and a gate on
    quality only.
    """

    def __init__(
        self, session: AsyncSession, audit: AuditService, capabilities: PrCapabilityService
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities

    async def create_draft(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        effective_from: date,
        daily_target_minutes: int = DEFAULT_DAILY_TARGET_MINUTES,
        workload_weight: Decimal = Decimal("50"),
        quality_weight: Decimal = Decimal("30"),
        timeliness_weight: Decimal = Decimal("10"),
        business_contribution_weight: Decimal = Decimal("10"),
        workload_score_cap: Decimal = Decimal("120"),
        note: str | None = None,
    ) -> PrPerformancePolicy:
        """Draft a policy. **The weights must total exactly 100.**

        Checked here as well as by a database CHECK, because a policy summing to
        90 would deflate every index computed under it and the deflation would
        look like people getting worse rather than like a configuration mistake.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        total = workload_weight + quality_weight + timeliness_weight + business_contribution_weight
        if total != Decimal("100"):
            raise PrValidationError(
                "Tổng trọng số phải đúng bằng 100.",
                details={
                    "field": "weights",
                    "reason": "weights_must_total_100",
                    "total": str(total),
                },
            )
        if daily_target_minutes <= 0:
            raise PrValidationError(
                "Số phút chuẩn mỗi ngày phải lớn hơn 0.",
                details={
                    "field": "daily_target_minutes",
                    "reason": "daily_target_must_be_positive",
                },
            )

        row = PrPerformancePolicy(
            version_no=await self._next_version(),
            effective_from=effective_from,
            daily_target_minutes=daily_target_minutes,
            workload_weight=workload_weight,
            quality_weight=quality_weight,
            timeliness_weight=timeliness_weight,
            business_contribution_weight=business_contribution_weight,
            workload_score_cap=workload_score_cap,
            quality_scores={k.value: str(v) for k, v in DEFAULT_QUALITY_SCORES.items()},
            timeliness_scores={k.value: str(v) for k, v in DEFAULT_TIMELINESS_SCORES.items()},
            business_contribution_scores={
                k.value: str(v) for k, v in DEFAULT_BUSINESS_CONTRIBUTION_SCORES.items()
            },
            quality_gate=[
                [str(floor), None if cap is None else str(cap)]
                for floor, cap in DEFAULT_QUALITY_GATE
            ],
            performance_bands=[[str(floor), name] for floor, name in DEFAULT_PERFORMANCE_BANDS],
            status=PrScoringRuleStatus.DRAFT,
            note=note,
            created_by_user_id=actor.user_id,
        )
        self._session.add(row)
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_PERFORMANCE_POLICY_CREATED,
            entity_type="pr_performance_policy",
            entity_id=row.id,
            after={
                "version_no": row.version_no,
                "effective_from": effective_from.isoformat(),
                "weights": {
                    "workload": str(workload_weight),
                    "quality": str(quality_weight),
                    "timeliness": str(timeliness_weight),
                    "business_contribution": str(business_contribution_weight),
                },
            },
        )
        return row

    async def approve(
        self, *, actor: Actor, request_id: uuid.UUID, policy_id: uuid.UUID
    ) -> PrPerformancePolicy:
        """Put a policy in force, superseding the one before it."""
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        policy = await self._session.get(PrPerformancePolicy, policy_id)
        if policy is None:
            raise PrNotFoundError(
                "Không tìm thấy chính sách hiệu suất.",
                details={"field": "policy_id", "reason": "policy_not_found"},
            )
        if policy.status is not PrScoringRuleStatus.DRAFT:
            raise PrConflictError(
                "Chỉ bản nháp mới được duyệt.",
                details={"field": "status", "reason": "policy_not_draft"},
            )

        current = await self.policy_for(on=policy.effective_from)
        if current is not None and current.id != policy.id:
            current.status = PrScoringRuleStatus.SUPERSEDED
            await self._session.flush()
            await record_pr_event(
                self._audit,
                request_id=request_id,
                actor=actor,
                action=AuditAction.PR_PERFORMANCE_POLICY_SUPERSEDED,
                entity_type="pr_performance_policy",
                entity_id=current.id,
                after={"superseded_by_policy_id": str(policy.id)},
            )

        policy.status = PrScoringRuleStatus.APPROVED
        policy.approved_by_user_id = actor.user_id
        policy.approved_at = utcnow()
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_PERFORMANCE_POLICY_APPROVED,
            entity_type="pr_performance_policy",
            entity_id=policy.id,
            after={
                "version_no": policy.version_no,
                "effective_from": policy.effective_from.isoformat(),
            },
        )
        return policy

    async def policy_for(self, *, on: date) -> PrPerformancePolicy | None:
        """The policy in force on ``on``. Newest effective date wins."""
        statement = (
            select(PrPerformancePolicy)
            .where(
                PrPerformancePolicy.status.in_(
                    (PrScoringRuleStatus.APPROVED, PrScoringRuleStatus.SUPERSEDED)
                ),
                PrPerformancePolicy.effective_from <= on,
            )
            .order_by(PrPerformancePolicy.effective_from.desc())
            .limit(1)
        )
        return (await self._session.execute(statement)).scalars().one_or_none()

    async def list_policies(self, *, actor: Actor) -> list[PrPerformancePolicy]:
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        statement = select(PrPerformancePolicy).order_by(PrPerformancePolicy.version_no.desc())
        return list((await self._session.execute(statement)).scalars().all())

    async def _next_version(self) -> int:
        highest = await self._session.scalar(
            select(PrPerformancePolicy.version_no)
            .order_by(PrPerformancePolicy.version_no.desc())
            .limit(1)
        )
        return (highest or 0) + 1


__all__: list[str] = ["PrPerformancePolicyService", "PrWorkScoringRuleService"]
