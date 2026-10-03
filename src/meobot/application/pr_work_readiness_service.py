"""What will happen to this work after it is counted - said before it is created.

Milestone **M4A**, and a **read-only diagnostic**. It decides nothing, blocks
nothing and writes nothing. It exists because of a gap a manager cannot see
from the assignment form:

    A real job can be created, done, validated and counted, and still be worth
    nothing on anybody's performance report.

Two independent things have to be configured for counted work to reach a
performance figure, and neither is the assigning manager's to fix in the moment:

* **an approved KPI quota** for that person, that work type, that month. Without
  one, M2 allocates the counted contribution as ``NO_QUOTA`` - it is real
  counted work, it appears in the ledger and in every operational view, and
  M6's eligible amount for it is zero;
* **an approved M6 workload rule** for that work type. Without one, M6 reports
  ``NO_SCORING_RULE`` for the month rather than inventing a rate.

Why this is a diagnostic and not a rule
----------------------------------------

**Operational work must not wait for configuration.** "Kháng page David" is a
real job whether or not anybody has written a scoring rule for page recovery,
and a form that refused it would be a form that taught people to file real work
under whichever type happened to be configured - which is the exact
mismeasurement the Work Ledger exists to prevent. So the answer is a sentence on
the screen, not a refusal, and the sentence names the person who can fix it.

**And it must not be fixed by writing a quota.** Creating a quota because
somebody assigned work would let any manager with ``PR_WORK_MANAGE`` write KPI
capacity for anybody, which is M2's approval gate defeated by a side effect.
Whether manager-authorised ad-hoc work should carry its own eligibility is a
real product question and an **M2 policy decision**; M4 states the fact and
stops.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_performance_config_service import PrWorkScoringRuleService
from meobot.application.pr_work_period_service import PrWorkPeriodService
from meobot.application.pr_work_quota_service import PrWorkQuotaEligibilityService
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr_work import PrWorkType
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import PrNotFoundError
from meobot.domain.pr.policy import PrCapability

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AssigneeReadiness:
    """Whether one person's counted work of this kind would reach a KPI quota."""

    user_id: uuid.UUID
    full_name: str
    #: ``False`` means M2 will allocate this person's counted contribution as
    #: ``NO_QUOTA``. The work is still real and still counted.
    has_quota: bool


@dataclass(frozen=True, slots=True)
class WorkReadiness:
    """The two configuration facts a creation screen should say out loud.

    Deliberately carries **no verdict field**. There is no "ready" and "not
    ready", because there is nothing to be ready for: every combination of
    these flags creates work perfectly well. The screen phrases them; it does
    not gate on them.
    """

    work_type_id: uuid.UUID
    work_type_name: str
    #: Whether an approved M6 workload rule prices this kind of work today.
    #: ``False`` is a **configuration** fact about the type, identical for
    #: everybody, and the person who can fix it holds ``PR_WORK_CONFIGURE``.
    has_scoring_rule: bool
    #: ``None`` when no reporting period covers today - a month nobody opened.
    #: Distinct from "a period exists and this person has no quota in it",
    #: because the two need different sentences and different people to act.
    period_id: uuid.UUID | None
    period_label: str | None
    assignees: tuple[AssigneeReadiness, ...] = ()

    @property
    def assignees_without_quota(self) -> tuple[AssigneeReadiness, ...]:
        return tuple(one for one in self.assignees if not one.has_quota)


class PrWorkReadinessService:
    """Answers "what will this work be worth" without creating any.

    Args:
        session: Unit of work.
        capabilities: Gates the read at ``PR_WORK_EXECUTE`` - anybody who may
            file work may be told what will become of it.
        periods: The one place a date becomes a reporting period.
        eligibility: M2's evaluator, asked only its public read.
        scoring_rules: M6's rate book, asked only whether a rate exists.
    """

    def __init__(
        self,
        session: AsyncSession,
        capabilities: PrCapabilityService,
        periods: PrWorkPeriodService,
        eligibility: PrWorkQuotaEligibilityService,
        scoring_rules: PrWorkScoringRuleService,
    ) -> None:
        self._session = session
        self._capabilities = capabilities
        self._periods = periods
        self._eligibility = eligibility
        self._scoring_rules = scoring_rules

    async def readiness(
        self,
        *,
        actor: Actor,
        work_type_id: uuid.UUID,
        user_ids: Sequence[uuid.UUID] = (),
        now: datetime | None = None,
    ) -> WorkReadiness:
        """The quota and scoring-rule position for this work type, right now.

        ``user_ids`` is whoever the form currently names. Empty is legitimate -
        a proposal form has no assignee list yet - and yields the work type's
        half of the answer alone.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_EXECUTE)
        moment = now or utcnow()
        work_type = await self._session.get(PrWorkType, work_type_id)
        if work_type is None:
            raise PrNotFoundError(
                "Không tìm thấy loại công việc.",
                details={"work_type_id": str(work_type_id)},
            )

        # The rate in force *today*, which is the right question for work being
        # created now. A recalculation of September still prices September - see
        # ``rule_for``, which takes the contribution's own instant.
        rule = await self._scoring_rules.rule_for(work_type_id, on=moment.date())
        period = await self._periods.period_for(moment)

        assignees: list[AssigneeReadiness] = []
        if user_ids:
            names = await self._names(user_ids)
            for user_id in dict.fromkeys(user_ids):
                has_quota = False
                if period is not None:
                    has_quota = (
                        await self._eligibility.approved_quota_for(
                            user_id=user_id,
                            period_id=period.id,
                            work_type_id=work_type_id,
                        )
                    ) is not None
                assignees.append(
                    AssigneeReadiness(
                        user_id=user_id,
                        full_name=names.get(user_id, "—"),
                        has_quota=has_quota,
                    )
                )

        return WorkReadiness(
            work_type_id=work_type_id,
            work_type_name=work_type.name,
            has_scoring_rule=rule is not None,
            period_id=period.id if period is not None else None,
            period_label=period.code if period is not None else None,
            assignees=tuple(assignees),
        )

    async def _names(self, user_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Display names in one query, so no client is handed a bare UUID."""
        unique = list(dict.fromkeys(user_ids))
        if not unique:
            return {}
        result = await self._session.execute(
            select(User.id, User.full_name).where(User.id.in_(unique))
        )
        return {row[0]: row[1] for row in result.all()}


__all__: list[str] = [
    "AssigneeReadiness",
    "PrWorkReadinessService",
    "WorkReadiness",
]
