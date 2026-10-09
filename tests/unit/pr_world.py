"""The PR world every scoped-approval suite is argued against.

Extracted in Step 1F.2.8 from ``test_pr_scoped_approval_grants``, which built
it, and shared with ``test_pr_bulk_approval``. The extraction is the point
rather than a tidy-up: bulk approval has to be the *same* rule as single
approval, so it is asserted against the same brand, the same three channels,
the same three people and the same helpers. Two lookalike fixtures would let
the two paths drift and both suites stay green.

Registered as a fixture through ``tests/unit/conftest.py``, so no test module
imports it - which is also what keeps a shared fixture from being flagged as an
unused import wherever it is used.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import get_current_web_actor, get_session
from meobot.api.main import create_app
from meobot.application.audit_service import AuditService
from meobot.application.pr_ai_review_service import RecordAiReviewCommand
from meobot.application.pr_approval_service import RecordApprovalCommand
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_content_service import ContentTargetSpec, CreateContentCommand
from meobot.application.pr_services import build_pr_services
from meobot.core.config import Settings
from meobot.db.models.pr import PrBrand, PrChannel, PrContentItem, PrPlatform
from meobot.db.models.pr_platform_policy import PrPlatformPolicyPack
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.grants import GrantScope, PrGrantScopeMode
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewType,
    PrApprovalDecision,
    PrApprovalStage,
    PrChannelCategory,
    PrContentType,
    PrDistributionMode,
    PrPolicyPackStatus,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from tests.unit.streams import tag_pr

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
TODAY = date(2026, 6, 1)

SELECTED = PrGrantScopeMode.SELECTED
ALL = PrGrantScopeMode.ALL


def selected(
    *,
    content_types: frozenset[PrContentType] = frozenset(),
    channels: frozenset[uuid.UUID] = frozenset(),
    unclassified: bool = False,
    unassigned: bool = False,
) -> GrantScope:
    """A ``SELECTED``/``SELECTED`` scope. The shape the panel sends."""
    return GrantScope(
        content_type_scope=SELECTED,
        content_types=content_types,
        include_unclassified_content=unclassified,
        channel_scope=SELECTED,
        channel_ids=channels,
        include_unassigned_channel=unassigned,
    )


@dataclass
class World:
    """One brand, three channels, three people, every service wired up."""

    session: AsyncSession
    settings: Settings
    client: TestClient
    capabilities: PrCapabilityService
    #: ``OWNER``. Holds ``user.role.manage``, so the only one who may grant.
    owner: User
    #: ``TEAM_LEAD``. Holds ``script.review`` and ``script.approve`` by role.
    lead: User
    #: ``EMPLOYEE``. Holds neither, and is the person the step exists for.
    member: User
    brand: PrBrand
    tiktok: PrChannel
    facebook: PrChannel
    youtube: PrChannel

    @property
    def request_id(self) -> uuid.UUID:
        return uuid.uuid4()

    def actor(self, user: User) -> Actor:
        return Actor(user_id=user.id, full_name=user.full_name, role=user.role)

    def act_as(self, user: User) -> None:
        self.client.app.dependency_overrides[get_current_web_actor] = lambda: self.actor(user)  # type: ignore[attr-defined]

    async def content(
        self,
        *,
        content_type: PrContentType | None = PrContentType.SHORT_VIDEO_SCRIPT,
        channels: tuple[PrChannel, ...] = (),
    ) -> PrContentItem:
        services = build_pr_services(self.session, self.settings)
        snapshot = await services.content.create_content(
            actor=self.actor(self.owner),
            request_id=self.request_id,
            command=CreateContentCommand(
                title="Một bài",
                brand_id=self.brand.id,
                owner_user_id=self.owner.id,
                content_type=content_type,
                script_text="Nội dung.",
                targets=tuple(
                    # Facebook and TikTok are policy-grounded platforms, so a
                    # target on them has to say Organic or Paid before AI review
                    # will run. Irrelevant to authorization and required to get
                    # an item as far as the gate this suite is about.
                    ContentTargetSpec(
                        channel_id=item.id, distribution_mode=PrDistributionMode.ORGANIC
                    )
                    for item in channels
                ),
            ),
        )
        # Deliberately *not* refreshed. ``create_content`` leaves every column
        # populated from the INSERT, and re-reading the row here would put it
        # back in a state whose ``updated_at`` a later request has to lazy-load -
        # which, in the ``TestClient``'s thread, is outside the greenlet
        # SQLAlchemy's async layer needs.
        return snapshot.content

    async def content_id(
        self,
        *,
        content_type: PrContentType | None = PrContentType.SHORT_VIDEO_SCRIPT,
        channels: tuple[PrChannel, ...] = (),
    ) -> uuid.UUID:
        """The same item, as an id, keeping **no** reference to the ORM row.

        For the tests that go through the ``TestClient``. SQLAlchemy's identity
        map holds weak references, so a row this test still points at stays in
        it - and an attribute the app's own ``UPDATE`` expires is then lazily
        reloaded inside the request thread, outside the greenlet the async layer
        needs. Dropping the reference lets the request read the row for itself.
        A test that never crosses that boundary may hold the object safely, and
        :meth:`content` is for those.
        """
        return (await self.content(content_type=content_type, channels=channels)).id

    async def to_team_lead_review(self, content_id: uuid.UUID) -> None:
        """Walk an item to the first human gate, the way the workflow intends.

        Manual edges to ``AI_REVIEW`` and then a gating ``FULL_REVIEW`` verdict,
        which is the only edge into the human gate. Takes an id rather than a
        row for the reason :meth:`content_id` gives.
        """
        services = build_pr_services(self.session, self.settings)
        for stage in (
            PrWorkflowStage.BRIEFING,
            PrWorkflowStage.SCRIPTING,
            PrWorkflowStage.AI_REVIEW,
        ):
            await services.workflow.request_transition(
                actor=self.actor(self.owner),
                request_id=self.request_id,
                content_id=content_id,
                target=stage,
            )
        await services.ai_reviews.record_review(
            actor=self.actor(self.owner),
            request_id=self.request_id,
            command=RecordAiReviewCommand(
                content_id=content_id,
                reviewed_version=1,
                review_type=PrAiReviewType.FULL_REVIEW,
                result=PrAiReviewResult.PASS,
                model_name="claude-opus-5",
                prompt_version="p@1",
                reviewed_at=NOW,
            ),
        )

    async def grant(
        self,
        user: User,
        capability: PrCapability,
        scope: GrantScope,
        **kwargs: object,
    ) -> uuid.UUID:
        row = await self.capabilities.grant(
            actor=self.actor(self.owner),
            request_id=self.request_id,
            user_id=user.id,
            capability=capability,
            scope=scope,
            **kwargs,  # type: ignore[arg-type]
        )
        return row.id

    async def approve(self, user: User, content_id: uuid.UUID, stage: PrApprovalStage) -> None:
        services = build_pr_services(self.session, self.settings)
        await services.approvals.record_decision(
            actor=self.actor(user),
            request_id=self.request_id,
            command=RecordApprovalCommand(
                content_id=content_id,
                reviewer_user_id=user.id,
                approval_stage=stage,
                decision=PrApprovalDecision.APPROVED,
                version_reviewed=1,
                decided_at=NOW,
            ),
        )


@pytest_asyncio.fixture
async def world(session: AsyncSession) -> AsyncIterator[World]:
    owner = User(full_name="Chủ sở hữu", role=Role.OWNER)
    lead = User(full_name="Lê Trưởng Nhóm", role=Role.TEAM_LEAD)
    member = User(full_name="Hảo", role=Role.EMPLOYEE)
    brand = PrBrand(code="BRND-A", name="Apexmed")
    tiktok_platform = PrPlatform(code="TIKTOK", name="TikTok")
    facebook_platform = PrPlatform(code="FACEBOOK", name="Facebook")
    youtube_platform = PrPlatform(code="YOUTUBE", name="YouTube")
    session.add_all(
        [owner, lead, member, brand, tiktok_platform, facebook_platform, youtube_platform]
    )
    await session.flush()
    # Untagged sees no stream: the team lead and the member work in PR. The
    # OWNER sees every stream without a tag.
    await tag_pr(session, [lead, member])

    settings = Settings(web_base_url="https://pr.example.com", web_cookie_secure=False)
    audit = AuditService(session)
    capabilities = PrCapabilityService(session, audit)
    services = build_pr_services(session, settings)
    granter = Actor(user_id=owner.id, full_name=owner.full_name, role=Role.OWNER)

    async def channel(name: str, platform: PrPlatform) -> PrChannel:
        return await services.channels.create_channel(
            actor=granter,
            request_id=uuid.uuid4(),
            command=CreateChannelCommand(
                name=name,
                platform_id=platform.id,
                brand_id=brand.id,
                category=PrChannelCategory.SCALE,
            ),
        )

    tiktok = await channel("TikTok BS Tiến", tiktok_platform)
    facebook = await channel("Facebook BS Tiến", facebook_platform)
    youtube = await channel("YouTube Apexmed", youtube_platform)

    # Facebook and TikTok are policy-grounded platforms, so an organic target on
    # either needs an ACTIVE pack before the item may enter AI review - Step
    # 1F.1's rule, and a precondition of reaching the gate this suite is about
    # rather than anything to do with who may approve.
    session.add_all(
        [
            PrPlatformPolicyPack(
                platform_code=code,
                distribution_mode=PrDistributionMode.ORGANIC.value,
                version=1,
                label=f"{code}/ORGANIC v1",
                status=PrPolicyPackStatus.ACTIVE,
                manifest_hash="a" * 64,
            )
            for code in ("FACEBOOK", "TIKTOK")
        ]
    )
    await session.flush()

    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        built = World(
            session=session,
            settings=settings,
            client=client,
            capabilities=capabilities,
            owner=owner,
            lead=lead,
            member=member,
            brand=brand,
            tiktok=tiktok,
            facebook=facebook,
            youtube=youtube,
        )
        built.act_as(owner)
        yield built
    app.dependency_overrides.clear()
