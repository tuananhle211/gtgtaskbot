"""Whether one content item may enter ``AI_REVIEW`` yet, decided once.

Step 1F.1. Step 1E.2.1 established that ``/available-actions`` must list only
actions that actually work, and the way it did that was to ask the write path's
own checks rather than re-implement them. This service is the same idea for the
one prerequisite Step 1F.1 adds, and it is deliberately the *only* place the
rule exists:

```
PrContentWorkflowService.request_transition ─┐
                                             ├─→ PrPolicyReadinessService
PrAvailableActionService.for_content ────────┘
```

The rule
--------

For a target whose channel sits on a **policy-grounded platform** (Facebook or
TikTok, matched against ``pr_platforms.code`` - never a channel's name):

* ``distribution_mode == UNSPECIFIED`` → not ready. Nobody has said whether this
  is organic or a paid ad, and the two are judged against different rules. This
  is an application prerequisite, not something to hand a model and hope it
  notices;
* **no ACTIVE pack** for that platform and mode → not ready. Reviewing paid
  advertising against no advertising policy and calling the result compliant is
  the failure this refuses to commit.

Everything else is ready. A target on an unsupported platform, or an item with
no targets at all, keeps the generic Step 1F review - which is a real review that
simply has no platform rules behind it, and is not pretending otherwise.

Fail closed, and say why
------------------------

Both refusals produce a Vietnamese sentence a person can act on and a stable
machine ``reason``. Neither leaks an exception, a stack or a pack id a browser
has no use for.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.pr_policy_pack_service import PrPolicyPackService
from meobot.core.logging import get_logger
from meobot.db.models.pr import PrChannel, PrContentTarget, PrPlatform
from meobot.db.models.pr_platform_policy import PrPlatformPolicyPack
from meobot.domain.pr.errors import PrWorkflowTransitionError
from meobot.domain.pr.models import (
    POLICY_GROUNDED_PLATFORM_CODES,
    PrDistributionMode,
)

logger = get_logger(__name__)

#: Stable machine reasons. Carried in ``details`` so a client can branch, and
#: distinct so "nobody set the mode" and "nobody activated a pack" - an author
#: problem and an operator problem - never look the same.
REASON_MODE_REQUIRED = "policy_distribution_mode_required"
REASON_PACK_UNAVAILABLE = "policy_pack_unavailable"
#: Step 1F.2. No planned channel at all, so nothing says which policy applies.
#: Previously this read as "no supported target" and let the content through to
#: a generic review - which succeeded, and looked to the author exactly like a
#: platform-policy check that had passed.
REASON_TARGETS_REQUIRED = "content_targets_required"


@dataclass(frozen=True, slots=True)
class PolicyPin:
    """One pack a run must pin, resolved at queue time."""

    platform_code: str
    distribution_mode: PrDistributionMode
    pack: PrPlatformPolicyPack


@dataclass(frozen=True, slots=True)
class PolicyReadiness:
    """Whether a piece may enter ``AI_REVIEW``, and what it would pin."""

    ready: bool
    #: Deduplicated: two Facebook organic targets pin the Facebook organic pack
    #: once, because the rules do not differ per target.
    pins: tuple[PolicyPin, ...] = ()
    reason: str | None = None
    message: str | None = None
    #: The platform the refusal is about, for a message a person can act on.
    platform_code: str | None = None

    def raise_if_blocked(self, content_id: uuid.UUID) -> None:
        """Turn a refusal into the workflow's own error type.

        ``blocked_message`` repeats the sentence into ``details`` on purpose.
        This is a transition error, so Telegram renders it with the *stage*
        template - which reads the stage edge that a readiness refusal has no
        reason to carry, and says "đang ở bước None". Carrying the sentence lets
        that renderer use the wording this service already owns, instead of a
        second copy of it living in the tool layer.
        """
        if self.ready:
            return
        message = self.message or "Chưa thể gửi AI review cho nội dung này."
        raise PrWorkflowTransitionError(
            message,
            details={
                "content_id": str(content_id),
                "reason": self.reason,
                "platform_code": self.platform_code,
                "blocked_message": message,
            },
        )


class PrPolicyReadinessService:
    """The one implementation of "may this enter AI review".

    Args:
        session: Unit of work; nothing here writes.
        packs: Resolves the ACTIVE pack per platform and mode.
    """

    def __init__(self, session: AsyncSession, packs: PrPolicyPackService) -> None:
        self._session = session
        self._packs = packs

    async def evaluate(self, content_id: uuid.UUID) -> PolicyReadiness:
        """Ready, or the first reason it is not.

        Deterministic in which refusal it reports: targets are examined in a
        stable order, so two callers asking the same question get the same
        sentence rather than whichever race won.
        """
        contexts, blocked = await self._completeness(content_id, before="AI review")
        if blocked is not None:
            return blocked
        if not contexts:
            # Targets exist, none on a policy-grounded platform. Generic Step 1F
            # review - explicit domain behaviour, not missing data.
            return PolicyReadiness(ready=True)

        pins: dict[tuple[str, PrDistributionMode], PolicyPin] = {}
        for platform_code, mode in contexts:
            key = (platform_code, mode)
            if key in pins:
                continue
            pack = await self._packs.resolve_active_pack(platform_code, mode)
            if pack is None:
                return PolicyReadiness(
                    ready=False,
                    reason=REASON_PACK_UNAVAILABLE,
                    platform_code=platform_code,
                    message=(
                        "Chưa có bộ chính sách đang áp dụng cho kênh này, "
                        "nên chưa chạy được AI review."
                    ),
                )
            pins[key] = PolicyPin(platform_code=platform_code, distribution_mode=mode, pack=pack)

        return PolicyReadiness(ready=True, pins=tuple(pins.values()))

    async def is_ready(self, content_id: uuid.UUID) -> bool:
        """The same question as :meth:`evaluate`, as a boolean."""
        return (await self.evaluate(content_id)).ready

    async def evaluate_for_human_review(self, content_id: uuid.UUID) -> PolicyReadiness:
        """Whether a draft is complete enough to hand to a **person**. Step 1F.2.10.

        The direct submission to the Team Lead skips the AI review and nothing
        else, so it asks the same two questions about the *content* that
        :meth:`evaluate` asks - is there a planned channel, and has each grounded
        target said Organic or Paid - and refuses with the same reason codes.
        What it does not ask is the third question, whether an ``ACTIVE`` policy
        pack exists for that platform and mode: that is a precondition of
        *running a grounded AI review*, an operator's concern about the machine
        rather than a fact about the draft, and a reviewer reading a script does
        not need one. Nothing is pinned, because nothing will run.

        Its own method rather than a flag on :meth:`evaluate`, so the sentence a
        person reads says "trước khi gửi duyệt" rather than promising an AI
        review this path will never perform.
        """
        _, blocked = await self._completeness(content_id, before="gửi duyệt")
        return blocked if blocked is not None else PolicyReadiness(ready=True)

    async def is_ready_for_human_review(self, content_id: uuid.UUID) -> bool:
        """The same question as :meth:`evaluate_for_human_review`, as a boolean."""
        return (await self.evaluate_for_human_review(content_id)).ready

    async def _completeness(
        self, content_id: uuid.UUID, *, before: str
    ) -> tuple[Sequence[tuple[str, PrDistributionMode]], PolicyReadiness | None]:
        """The content-completeness half of readiness, shared by both submissions.

        Returns the grounded targets to resolve packs for, and the refusal if
        there is one. One implementation on purpose: the direct path must not be
        able to send a draft with no channel, or an undecided Organic/Paid, past
        a check the AI path enforces - see Step 1F.2.10.
        """
        if not await self._has_any_target(content_id):
            # Step 1F.2. Zero targets is not "an unsupported platform" - it is
            # missing information, and letting it through produced a generic
            # review that read as a passed policy check. Generic review remains
            # available for content that *has* targets on platforms with no
            # policy pack; it is no longer what happens when nobody said where
            # the piece is going.
            return (), PolicyReadiness(
                ready=False,
                reason=REASON_TARGETS_REQUIRED,
                message=f"Vui lòng chọn ít nhất một kênh dự kiến trước khi {before}.",
            )
        contexts = await self._grounded_targets(content_id)
        for platform_code, mode in contexts:
            if mode is PrDistributionMode.UNSPECIFIED:
                return contexts, PolicyReadiness(
                    ready=False,
                    reason=REASON_MODE_REQUIRED,
                    platform_code=platform_code,
                    message=(
                        "Nội dung này cần xác định Organic hay Quảng cáo trả phí "
                        f"trước khi {before}."
                    ),
                )
        return contexts, None

    async def _has_any_target(self, content_id: uuid.UUID) -> bool:
        """Whether anybody has said where this piece is going."""
        result = await self._session.execute(
            select(PrContentTarget.id).where(PrContentTarget.content_id == content_id).limit(1)
        )
        return result.scalars().first() is not None

    # --- Internals --------------------------------------------------------
    async def _grounded_targets(
        self, content_id: uuid.UUID
    ) -> Sequence[tuple[str, PrDistributionMode]]:
        """Platform code and mode for every target on a supported platform.

        Joined through ``pr_channels`` to ``pr_platforms`` so the platform is
        the canonical record's ``code``. A channel called "FB Apexmed" on a
        YouTube platform row is a YouTube target, and reading its name would
        have got that wrong.
        """
        result = await self._session.execute(
            select(PrPlatform.code, PrContentTarget.distribution_mode)
            .join(PrChannel, PrChannel.id == PrContentTarget.channel_id)
            .join(PrPlatform, PrPlatform.id == PrChannel.platform_id)
            .where(PrContentTarget.content_id == content_id)
            .order_by(PrPlatform.code, PrContentTarget.distribution_mode)
        )
        return [
            (code, mode) for code, mode in result.all() if code in POLICY_GROUNDED_PLATFORM_CODES
        ]


__all__ = [
    "REASON_MODE_REQUIRED",
    "REASON_PACK_UNAVAILABLE",
    "REASON_TARGETS_REQUIRED",
    "PolicyPin",
    "PolicyReadiness",
    "PrPolicyReadinessService",
]
