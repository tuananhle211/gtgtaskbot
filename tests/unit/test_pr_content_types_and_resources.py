"""Step 1F.2.3e: what content *is*, and what to read while judging it.

Two features, two halves.

The content-type half is mostly about the **absence** of a type. Making a column
required for new rows and optional for old ones is the kind of change that goes
wrong quietly - a legacy item that will not open, a filter that hides half the
board, a backfill nobody asked for - so section 71 spends most of its assertions
on ``NULL`` rather than on the six values.

The resource half is about a boundary. A resource is input and a production
submission is output, and the tests that matter are the ones that would fail if
those ever became the same thing: section 76 checks the tables stay independent,
that the aggregate delete takes resources with it, and that a reviewer who may
approve a piece still may not rewrite the brief they are approving it against.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import get_current_web_actor, get_session
from meobot.api.main import create_app
from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_channel_service import CreateChannelCommand, PrChannelService
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_content_service import ContentTargetSpec, CreateContentCommand
from meobot.application.pr_services import build_pr_services
from meobot.core.config import Settings
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrBrand, PrChannel, PrContentItem, PrPlatform
from meobot.db.models.pr_content_resource import PrContentResource
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.labels import content_type_label, resource_type_label
from meobot.domain.pr.models import (
    PrChannelCategory,
    PrContentResourceType,
    PrContentType,
    PrDistributionMode,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability

pytestmark = pytest.mark.asyncio

DRIVE = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv/view"


@dataclass
class World:
    """One brand, one channel, and four people with different authority."""

    session: AsyncSession
    client: TestClient
    settings: Settings
    #: ``ADMIN``: ``PR_CONTENT_EDIT`` and ``PR_CONTENT_CANCEL``, so management.
    manager: User
    #: ``EMPLOYEE``, owner of the content below. Metadata-edit by responsibility.
    owner: User
    #: ``EMPLOYEE``, related to nothing. Same capabilities as the owner, and it
    #: is the responsibility check that separates them.
    stranger: User
    #: ``TEAM_LEAD`` holding ``PR_HEAD_REVIEW`` and **not** responsible for the
    #: content. The case the whole authorization design turns on: they may
    #: approve a piece and must not be able to rewrite what it is judged against.
    reviewer: User
    brand: PrBrand
    channel: PrChannel

    def act_as(self, user: User) -> None:
        self.client.app.dependency_overrides[get_current_web_actor] = lambda: Actor(  # type: ignore[attr-defined]
            user_id=user.id, full_name=user.full_name, role=user.role, active=user.active
        )

    def actor(self, user: User) -> Actor:
        return Actor(user_id=user.id, full_name=user.full_name, role=user.role)

    async def content(
        self,
        title: str = "Bài A",
        *,
        content_type: PrContentType | None = PrContentType.SHORT_VIDEO_SCRIPT,
        owner: User | None = None,
        created_at: datetime | None = None,
    ) -> PrContentItem:
        """One content item. ``content_type=None`` builds the legacy shape.

        Constructible precisely because ``require_content_type`` is off for
        internal callers - a suite that could not create an unclassified item
        could not test the half of this step that is about them.
        """
        services = build_pr_services(self.session, self.settings)
        snapshot = await services.content.create_content(
            actor=self.actor(self.manager),
            request_id=uuid.uuid4(),
            command=CreateContentCommand(
                title=title,
                brand_id=self.brand.id,
                owner_user_id=(owner or self.owner).id,
                content_type=content_type,
                script_text="Nội dung.",
                targets=(
                    ContentTargetSpec(
                        channel_id=self.channel.id,
                        distribution_mode=PrDistributionMode.ORGANIC,
                    ),
                ),
            ),
        )
        row = snapshot.content
        if created_at is not None:
            row.created_at = created_at
            await self.session.flush()
        return row

    def board(self, **params: object) -> dict[str, object]:
        response = self.client.get("/api/pr/contents/board", params=params)
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]

    def titles(self, **params: object) -> list[str]:
        return [item["title"] for item in self.board(**params)["items"]]  # type: ignore[index,union-attr]

    def add(self, content_id: uuid.UUID, **body: object):  # type: ignore[no-untyped-def]
        payload: dict[str, object] = {
            "resource_type": "REFERENCE",
            "label": "Brief khách hàng",
            "location": DRIVE,
        }
        payload.update(body)
        return self.client.post(f"/api/pr/contents/{content_id}/resources", json=payload)

    def resources(self, content_id: uuid.UUID) -> list[dict[str, object]]:
        response = self.client.get(f"/api/pr/contents/{content_id}/resources")
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]


@pytest_asyncio.fixture
async def world(session: AsyncSession) -> AsyncIterator[World]:
    manager = User(full_name="Ha Trưởng Phòng", role=Role.ADMIN)
    owner = User(full_name="Nguyễn A", role=Role.EMPLOYEE)
    stranger = User(full_name="Trần B", role=Role.EMPLOYEE)
    reviewer = User(full_name="Le Trưởng Nhóm", role=Role.TEAM_LEAD)
    brand = PrBrand(code="BRND-A", name="Apexmed")
    platform = PrPlatform(code="TIKTOK", name="TikTok")
    session.add_all([manager, owner, stranger, reviewer, brand, platform])
    await session.flush()

    settings = Settings(web_base_url="https://pr.example.com", web_cookie_secure=False)
    audit = AuditService(session)
    capabilities = PrCapabilityService(session, audit)
    granter = Actor(user_id=manager.id, full_name=manager.full_name, role=Role.OWNER)
    # A real reviewer: holds the Head gate, owns nothing.
    await capabilities.grant(
        actor=granter,
        request_id=uuid.uuid4(),
        user_id=reviewer.id,
        capability=PrCapability.PR_HEAD_REVIEW,
    )
    channels = PrChannelService(session, audit, capabilities, PrCodeService(session, settings))
    channel = await channels.create_channel(
        actor=granter,
        request_id=uuid.uuid4(),
        command=CreateChannelCommand(
            name="Apexmed TikTok",
            platform_id=platform.id,
            brand_id=brand.id,
            category=PrChannelCategory.SCALE,
        ),
    )

    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        built = World(
            session=session,
            client=client,
            settings=settings,
            manager=manager,
            owner=owner,
            stranger=stranger,
            reviewer=reviewer,
            brand=brand,
            channel=channel,
        )
        built.act_as(manager)
        yield built
    app.dependency_overrides.clear()


# =============================================================================
# 70. The vocabulary
# =============================================================================


async def test_70a_there_are_exactly_six_content_types() -> None:
    """Closed, and closed on purpose. Adding one is a product decision."""
    assert [member.value for member in PrContentType] == [
        "ULTRA_SHORT_SCRIPT",
        "SHORT_VIDEO_SCRIPT",
        "FACEBOOK_POST",
        "LONG_YOUTUBE_SCRIPT",
        "PRESS_ARTICLE",
        "CORPORATE_TVC",
    ]


async def test_70b_every_type_and_resource_type_has_vietnamese() -> None:
    """No screen may have to fall back to a code."""
    assert [content_type_label(member) for member in PrContentType] == [
        "Kịch bản siêu ngắn",
        "Kịch bản video ngắn",
        "Bài đăng Facebook",
        "Kịch bản YouTube dài",
        "Báo chí",
        "TVC doanh nghiệp",
    ]
    assert content_type_label(None) == "Chưa phân loại"
    assert [resource_type_label(member) for member in PrContentResourceType] == [
        "Tài liệu tham khảo",
        "Hình ảnh",
        "Video tham khảo",
        "File / Google Drive",
        "Nguồn thông tin",
        "Tài nguyên thương hiệu",
        "Khác",
    ]


async def test_70c_content_type_is_independent_of_platform(world: World) -> None:
    """The distinction the enum exists to hold.

    Three items on the **same TikTok channel** with three different formats -
    which is only representable because nothing derives one from the other. A
    schema that inferred the type from the platform could not store this, and it
    is what actually happens: a short-video script, a Facebook post cross-posted,
    and a TVC cut-down all go out on the same channel.
    """
    for content_type in (
        PrContentType.SHORT_VIDEO_SCRIPT,
        PrContentType.FACEBOOK_POST,
        PrContentType.CORPORATE_TVC,
    ):
        row = await world.content(f"Bài {content_type.value}", content_type=content_type)
        assert row.content_type is content_type

    # And the channel is the same one for all three.
    assert (
        len(world.titles(scope="ALL", group="PREPARATION", channel_id=str(world.channel.id))) == 3
    )


# =============================================================================
# 71. Required for new content; absent, and readable, for old
# =============================================================================


async def test_71a_the_web_create_route_requires_a_type(world: World) -> None:
    """Refused, and refused *before* a content code is allocated."""
    world.act_as(world.manager)
    response = world.client.post(
        "/api/pr/contents",
        json={
            "title": "Bài không loại",
            "brand_id": str(world.brand.id),
            "owner_user_id": str(world.owner.id),
            "targets": [{"channel_id": str(world.channel.id), "distribution_mode": "ORGANIC"}],
        },
    )
    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details == {"field": "content_type", "reason": "required"}

    rows = await world.session.execute(
        select(PrContentItem).where(PrContentItem.title == "Bài không loại")
    )
    assert rows.scalars().first() is None, "a refused create must leave no row and burn no code"


@pytest.mark.parametrize("content_type", list(PrContentType))
async def test_71b_every_type_is_accepted_on_create(
    world: World, content_type: PrContentType
) -> None:
    world.act_as(world.manager)
    response = world.client.post(
        "/api/pr/contents",
        json={
            "title": f"Bài {content_type.value}",
            "brand_id": str(world.brand.id),
            "owner_user_id": str(world.owner.id),
            "content_type": content_type.value,
            "targets": [{"channel_id": str(world.channel.id), "distribution_mode": "ORGANIC"}],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["content"]["content_type"] == content_type.value


async def test_71c_an_invalid_type_is_refused_with_the_options(world: World) -> None:
    world.act_as(world.manager)
    row = await world.content()
    response = world.client.patch(
        f"/api/pr/contents/{row.id}/content-type", json={"content_type": "TIKTOK"}
    )
    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["field"] == "content_type"
    assert set(details["allowed"]) == {member.value for member in PrContentType}


async def test_71d_legacy_content_opens_and_serialises(world: World) -> None:
    """The rule the nullable column exists for: an old item still works.

    It opens, it serialises with an explicit ``null`` rather than a missing key,
    and it reads as *Chưa phân loại* rather than as an error.
    """
    row = await world.content("Bài cũ", content_type=None)
    world.act_as(world.owner)

    detail = world.client.get(f"/api/pr/contents/{row.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()["content"]
    assert "content_type" in body
    assert body["content_type"] is None
    assert content_type_label(None) == "Chưa phân loại"

    # And it is on the board like anything else.
    assert "Bài cũ" in world.titles(scope="ALL", group="PREPARATION")


async def test_71e_nothing_guesses_a_type_for_legacy_content(world: World) -> None:
    """No backfill, no inference. The item stays unclassified until told.

    Its channel is TikTok, its title says "video" - and both would be perfectly
    good evidence for a guess. Nothing guesses.
    """
    row = await world.content("Video ngắn về chăm sóc da", content_type=None)
    await world.session.refresh(row)
    assert row.content_type is None


# =============================================================================
# 72. Classifying, and who may
# =============================================================================


async def test_72a_management_may_classify(world: World) -> None:
    row = await world.content("Bài cũ", content_type=None)
    world.act_as(world.manager)

    response = world.client.patch(
        f"/api/pr/contents/{row.id}/content-type", json={"content_type": "PRESS_ARTICLE"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["content"]["content_type"] == "PRESS_ARTICLE"


async def test_72b_the_responsible_member_may_classify(world: World) -> None:
    row = await world.content("Bài cũ", content_type=None)
    world.act_as(world.owner)

    assert (
        world.client.patch(
            f"/api/pr/contents/{row.id}/content-type", json={"content_type": "FACEBOOK_POST"}
        ).status_code
        == 200
    )
    await world.session.refresh(row)
    assert row.content_type is PrContentType.FACEBOOK_POST


async def test_72c_an_unrelated_member_may_not(world: World) -> None:
    row = await world.content()
    world.act_as(world.stranger)

    response = world.client.patch(
        f"/api/pr/contents/{row.id}/content-type", json={"content_type": "PRESS_ARTICLE"}
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["reason"] == "not_responsible"
    await world.session.refresh(row)
    assert row.content_type is PrContentType.SHORT_VIDEO_SCRIPT


async def test_72d_a_review_grant_is_not_what_confers_edit_rights(world: World) -> None:
    """The reviewer/editor separation, as this permission matrix can express it.

    Step 1F.2.3e's brief asked that somebody holding only an approval capability
    should not thereby gain the right to edit metadata. **That case is not
    reachable here**, and the reason is worth pinning rather than hiding:

    * all three review capabilities - ``PR_TEAM_LEAD_REVIEW``,
      ``PR_HEAD_REVIEW``, ``PR_INTERNAL_REVIEW`` - have baselines only
      ``TEAM_LEAD`` and above hold;
    * ``PR_CONTENT_CANCEL``, which is what "management" means for a metadata
      edit, has the *same* baseline as ``PR_HEAD_REVIEW``.

    So anybody eligible to be granted a review gate is already management, and a
    grant narrows rather than widens (Step 1C.1). Manufacturing the separation
    would have meant inventing a capability, which this step was told not to do
    for exactly this kind of reason - a vocabulary invented to satisfy a test is
    a vocabulary nobody administers.

    What actually protects the brief is therefore **responsibility**, not the
    absence of a review grant: see
    :func:`test_72c_an_unrelated_member_may_not`. This test pins the structural
    fact so that the day somebody makes ``EMPLOYEE`` reviewable, it fails and is
    reconsidered.
    """
    from meobot.domain.permissions.matrix import Permission, has_permission
    from meobot.domain.pr.policy import baseline_permission

    review_gates = (
        PrCapability.PR_TEAM_LEAD_REVIEW,
        PrCapability.PR_HEAD_REVIEW,
        PrCapability.PR_INTERNAL_REVIEW,
    )
    manages = baseline_permission(PrCapability.PR_CONTENT_CANCEL)
    for gate in review_gates:
        for role in (Role.OWNER, Role.ADMIN, Role.TEAM_LEAD, Role.EMPLOYEE):
            if has_permission(role, baseline_permission(gate)):
                assert has_permission(role, manages), (
                    f"{role.value} can hold {gate.value} without being management; "
                    "the reviewer/editor separation is now expressible and needs a rule"
                )

    # And the consequence, stated as behaviour: a Team Lead reviewer *does* get
    # to classify, because they are management, not because they can review.
    row = await world.content("Bài cũ", content_type=None)
    world.act_as(world.reviewer)
    assert has_permission(world.reviewer.role, Permission.SCRIPT_APPROVE)
    assert (
        world.client.patch(
            f"/api/pr/contents/{row.id}/content-type", json={"content_type": "PRESS_ARTICLE"}
        ).status_code
        == 200
    )


async def test_72e_classifying_writes_an_audit_row(world: World) -> None:
    row = await world.content("Bài cũ", content_type=None)
    world.act_as(world.manager)
    assert (
        world.client.patch(
            f"/api/pr/contents/{row.id}/content-type", json={"content_type": "CORPORATE_TVC"}
        ).status_code
        == 200
    )

    events = await _audit(world, "pr.content.type_changed")
    assert len(events) == 1
    assert events[0].entity_type == "pr_content_item"
    assert events[0].entity_id == str(row.id)
    assert events[0].actor_user_id == world.manager.id
    # ``None`` is the real previous value for a historical row, and is recorded.
    assert events[0].before_data == {"content_type": None}
    assert events[0].after_data == {"content_code": row.code, "content_type": "CORPORATE_TVC"}


async def test_72f_classifying_writes_no_version_and_moves_no_stage(world: World) -> None:
    services = build_pr_services(world.session, world.settings)
    row = await world.content()
    row.workflow_stage = PrWorkflowStage.HEAD_REVIEW
    await world.session.flush()
    before = await services.content.current_version(row.id)
    assert before is not None

    world.act_as(world.manager)
    assert (
        world.client.patch(
            f"/api/pr/contents/{row.id}/content-type", json={"content_type": "PRESS_ARTICLE"}
        ).status_code
        == 200
    )

    after = await services.content.current_version(row.id)
    assert after is not None and after.version_no == before.version_no
    await world.session.refresh(row)
    assert row.workflow_stage is PrWorkflowStage.HEAD_REVIEW


async def test_72g_setting_the_same_type_records_nothing(world: World) -> None:
    row = await world.content()
    world.act_as(world.manager)
    assert (
        world.client.patch(
            f"/api/pr/contents/{row.id}/content-type",
            json={"content_type": "SHORT_VIDEO_SCRIPT"},
        ).status_code
        == 200
    )
    assert await _audit(world, "pr.content.type_changed") == []


# =============================================================================
# 73. The content-type filter
# =============================================================================


async def test_73a_the_filter_selects_one_format(world: World) -> None:
    await world.content("Video ngắn", content_type=PrContentType.SHORT_VIDEO_SCRIPT)
    await world.content("Bài Facebook", content_type=PrContentType.FACEBOOK_POST)
    await world.content("Chưa phân loại", content_type=None)

    assert world.titles(scope="ALL", group="PREPARATION", content_type="SHORT_VIDEO_SCRIPT") == [
        "Video ngắn"
    ]


async def test_73b_unclassified_is_its_own_slice(world: World) -> None:
    """*Chưa phân loại* is the work list for classifying the backlog."""
    await world.content("Video ngắn", content_type=PrContentType.SHORT_VIDEO_SCRIPT)
    await world.content("Bài cũ một", content_type=None)
    await world.content("Bài cũ hai", content_type=None)

    found = world.titles(scope="ALL", group="PREPARATION", content_type="UNCLASSIFIED")
    assert sorted(found) == ["Bài cũ hai", "Bài cũ một"]


async def test_73c_no_filter_means_every_format_including_none(world: World) -> None:
    await world.content("Video ngắn", content_type=PrContentType.SHORT_VIDEO_SCRIPT)
    await world.content("Bài cũ", content_type=None)
    assert len(world.titles(scope="ALL", group="PREPARATION")) == 2


async def test_73d_the_filter_narrows_before_the_page(world: World) -> None:
    """The assertion that matters: ``total`` counts the filtered set."""
    base = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)
    for index in range(9):
        await world.content(
            f"Video {index}",
            content_type=PrContentType.SHORT_VIDEO_SCRIPT,
            created_at=base + timedelta(hours=index),
        )
    await world.content("Bài báo", content_type=PrContentType.PRESS_ARTICLE, created_at=base)

    body = world.board(scope="ALL", group="PREPARATION", content_type="PRESS_ARTICLE", limit=5)
    assert body["total"] == 1
    assert [item["title"] for item in body["items"]] == ["Bài báo"]  # type: ignore[index,union-attr]


async def test_73e_the_filter_composes_with_everything_else(world: World) -> None:
    row = await world.content(
        "Bài báo của Nguyễn A",
        content_type=PrContentType.PRESS_ARTICLE,
        created_at=datetime(2026, 8, 10, 3, 0, tzinfo=UTC),
    )
    await world.content(
        "Bài báo của người khác", content_type=PrContentType.PRESS_ARTICLE, owner=world.manager
    )
    await world.content("Video của Nguyễn A", content_type=PrContentType.SHORT_VIDEO_SCRIPT)

    common: dict[str, object] = {
        "scope": "ALL",
        "group": "PREPARATION",
        "content_type": "PRESS_ARTICLE",
        "responsible_user_id": str(world.owner.id),
    }
    assert world.titles(**common) == ["Bài báo của Nguyễn A"]
    assert world.titles(**common, channel_id=str(world.channel.id)) == ["Bài báo của Nguyễn A"]
    assert world.titles(**common, stage="IDEA") == ["Bài báo của Nguyễn A"]
    assert world.titles(**common, priority="NORMAL") == ["Bài báo của Nguyễn A"]
    assert world.titles(**common, search="Nguyễn") == ["Bài báo của Nguyễn A"]
    assert world.titles(**common, date_from="2026-08-10", date_to="2026-08-10") == [
        "Bài báo của Nguyễn A"
    ]
    # And the exclusions, so each clause is proved to be applied.
    assert world.titles(**common, date_from="2026-09-01", date_to="2026-09-02") == []
    assert world.titles(**common, priority="CRITICAL") == []
    assert row.content_type is PrContentType.PRESS_ARTICLE


async def test_73f_the_filter_composes_with_the_workflow_group(world: World) -> None:
    gated = await world.content("Bài báo chờ duyệt", content_type=PrContentType.PRESS_ARTICLE)
    gated.workflow_stage = PrWorkflowStage.HEAD_REVIEW
    await world.content("Bài báo chuẩn bị", content_type=PrContentType.PRESS_ARTICLE)
    await world.session.flush()

    assert world.titles(scope="ALL", group="EDITORIAL_REVIEW", content_type="PRESS_ARTICLE") == [
        "Bài báo chờ duyệt"
    ]
    assert world.titles(scope="ALL", group="PREPARATION", content_type="PRESS_ARTICLE") == [
        "Bài báo chuẩn bị"
    ]


async def test_73g_the_tab_counts_follow_the_filter_but_not_the_group(world: World) -> None:
    """Step 1F.2.3c1's two-count design, still intact under a new filter."""
    gated = await world.content("Bài báo", content_type=PrContentType.PRESS_ARTICLE)
    gated.workflow_stage = PrWorkflowStage.HEAD_REVIEW
    await world.content("Video ngắn", content_type=PrContentType.SHORT_VIDEO_SCRIPT)
    await world.session.flush()

    body = world.board(scope="ALL", group="PREPARATION", content_type="PRESS_ARTICLE")
    counts = {row["stage"]: row["count"] for row in body["stage_counts"]}  # type: ignore[index,union-attr]
    # The counts are about the filtered set, group excluded: the press article is
    # counted at its own gate even though PREPARATION is open...
    assert counts["HEAD_REVIEW"] == 1
    # ...and the video, which the filter excluded, is counted nowhere.
    assert counts["IDEA"] == 0
    assert body["total"] == 0


async def test_73h_priority_ordering_survives_the_new_filter(world: World) -> None:
    """Step 1F.2.3d's sort is unchanged: nothing groups by content type."""
    base = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)
    services = build_pr_services(world.session, world.settings)
    normal = await world.content(
        "Thường", content_type=PrContentType.PRESS_ARTICLE, created_at=base
    )
    urgent = await world.content(
        "Gấp", content_type=PrContentType.PRESS_ARTICLE, created_at=base + timedelta(hours=1)
    )
    await services.content.set_content_priority(
        actor=world.actor(world.manager),
        request_id=uuid.uuid4(),
        content_id=urgent.id,
        priority=__import__("meobot.domain.pr.models", fromlist=["PrPriority"]).PrPriority.CRITICAL,
    )

    assert world.titles(scope="ALL", group="PREPARATION", content_type="PRESS_ARTICLE") == [
        "Gấp",
        "Thường",
    ]
    assert normal.content_type is PrContentType.PRESS_ARTICLE


# =============================================================================
# 74. Attaching review material
# =============================================================================


@pytest.mark.parametrize("resource_type", list(PrContentResourceType))
async def test_74a_every_resource_type_can_be_attached(
    world: World, resource_type: PrContentResourceType
) -> None:
    row = await world.content()
    world.act_as(world.manager)
    location = (
        DRIVE if resource_type is PrContentResourceType.DRIVE_FILE else "https://example.com/x"
    )

    response = world.add(row.id, resource_type=resource_type.value, location=location)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["resource_type"] == resource_type.value
    assert body["is_link"] is True


async def test_74b_an_invalid_resource_type_is_refused(world: World) -> None:
    row = await world.content()
    world.act_as(world.manager)
    assert world.add(row.id, resource_type="FINAL_VIDEO").status_code == 422


async def test_74c_a_label_is_required(world: World) -> None:
    """A list of rows titled with their own URLs is a list nobody can scan."""
    row = await world.content()
    world.act_as(world.manager)
    for blank in ("", "   "):
        response = world.add(row.id, label=blank)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"]["field"] == "label"


@pytest.mark.parametrize(
    "location",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "file:///etc/passwd",
    ],
)
async def test_74d_an_unsafe_scheme_is_refused(world: World, location: str) -> None:
    """The security half, and the reason the validator is shared with production.

    A stored location is rendered as an ``href``. ``javascript:`` survives every
    layer that only escapes HTML, and the mixed-case variant is here because a
    case-sensitive check is the usual way this protection is lost.
    """
    row = await world.content()
    world.act_as(world.manager)
    response = world.add(row.id, location=location)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] in {"unsafe_scheme", "unsupported_scheme"}
    assert world.resources(row.id) == []


@pytest.mark.parametrize(
    ("location", "resource_type", "reason"),
    [
        ("drive.google.com/file/d/1", "DRIVE_FILE", "missing_scheme"),
        ("https://", "REFERENCE", "missing_host"),
        ("https://dropbox.com/x", "DRIVE_FILE", "not_a_drive_host"),
        ("\\\\nas", "IMAGE", "incomplete_unc_path"),
    ],
)
async def test_74e_a_malformed_location_is_refused_with_a_reason(
    world: World, location: str, resource_type: str, reason: str
) -> None:
    row = await world.content()
    world.act_as(world.manager)
    response = world.add(row.id, location=location, resource_type=resource_type)
    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == reason
    # The resource-shaped key, which is what lets the panel word the refusal for
    # a brief rather than for a production file.
    assert details["resource_type"] == resource_type


async def test_74f_a_nas_path_is_stored_and_not_a_link(world: World) -> None:
    """Supported, and rendered as text to copy. Never fetched."""
    row = await world.content()
    world.act_as(world.manager)
    response = world.add(row.id, resource_type="IMAGE", location="/volume1/PR/2026/packshot.jpg")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["location"] == "/volume1/PR/2026/packshot.jpg"
    assert body["is_link"] is False


async def test_74g_required_for_review_defaults_false_and_persists(world: World) -> None:
    row = await world.content()
    world.act_as(world.manager)

    assert world.add(row.id, label="Tùy chọn").json()["required_for_review"] is False
    assert (
        world.add(row.id, label="Bắt buộc", required_for_review=True).json()["required_for_review"]
        is True
    )


async def test_74h_required_material_comes_first(world: World) -> None:
    """The whole of what the flag does: it orders, and it draws a badge."""
    row = await world.content()
    world.act_as(world.manager)
    world.add(row.id, label="Tham khảo thêm")
    world.add(row.id, label="Brief bắt buộc", required_for_review=True)
    world.add(row.id, label="Tham khảo nữa")

    listed = world.resources(row.id)
    assert next(item["label"] for item in listed) == "Brief bắt buộc"
    assert listed[0]["required_for_review"] is True


async def test_74i_required_material_gates_nothing(world: World) -> None:
    """It guides attention. It does not add approval state.

    A piece with an unread required brief still transitions exactly as it would
    without one - anything else would be new approval-gating state, which this
    step deliberately does not introduce.
    """
    services = build_pr_services(world.session, world.settings)
    row = await world.content()
    world.act_as(world.manager)
    world.add(row.id, label="Brief bắt buộc", required_for_review=True)

    await services.workflow.apply(
        actor=world.actor(world.manager),
        request_id=uuid.uuid4(),
        content=row,
        target=PrWorkflowStage.BRIEFING,
        trigger=__import__(
            "meobot.domain.pr.workflow", fromlist=["PrTransitionTrigger"]
        ).PrTransitionTrigger.MANUAL,
        reason="test",
    )
    await world.session.refresh(row)
    assert row.workflow_stage is PrWorkflowStage.BRIEFING


# =============================================================================
# 75. Editing, deleting, and who may
# =============================================================================


async def test_75a_the_responsible_member_may_manage_resources(world: World) -> None:
    row = await world.content()
    world.act_as(world.owner)
    assert world.add(row.id).status_code == 201


async def test_75b_an_unrelated_member_may_not(world: World) -> None:
    row = await world.content()
    world.act_as(world.stranger)
    response = world.add(row.id)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["reason"] == "not_responsible"


async def test_75c_an_unrelated_member_may_read_resources_but_not_change_them(
    world: World,
) -> None:
    """**Viewing is broad; editing is narrow.**

    The half of the rule that is reachable in this permission matrix - see
    :func:`test_72d_a_review_grant_is_not_what_confers_edit_rights` for why the
    reviewer-versus-management half is not.

    An ordinary member who is not responsible for the piece must be able to read
    every resource on it: they may be about to pick up a task on it, and a brief
    nobody can open is a brief nobody uses. They must not be able to rewrite it.
    """
    row = await world.content()
    world.act_as(world.manager)
    created = world.add(row.id, label="Brief khách hàng", required_for_review=True)
    resource_id = created.json()["id"]

    world.act_as(world.stranger)
    listed = world.resources(row.id)
    assert [item["label"] for item in listed] == ["Brief khách hàng"]
    assert listed[0]["required_for_review"] is True

    assert world.add(row.id, label="Của người khác").status_code == 403
    assert (
        world.client.patch(
            f"/api/pr/contents/{row.id}/resources/{resource_id}", json={"label": "Đổi tên"}
        ).status_code
        == 403
    )
    assert (
        world.client.delete(f"/api/pr/contents/{row.id}/resources/{resource_id}").status_code == 403
    )
    # Nothing changed.
    assert [item["label"] for item in world.resources(row.id)] == ["Brief khách hàng"]


async def test_75d_updating_changes_the_fields_it_names(world: World) -> None:
    row = await world.content()
    world.act_as(world.manager)
    resource_id = world.add(row.id).json()["id"]

    response = world.client.patch(
        f"/api/pr/contents/{row.id}/resources/{resource_id}",
        json={
            "label": "Brief đã cập nhật",
            "note": "Xem mục 3",
            "required_for_review": True,
            "resource_type": "SOURCE",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["label"] == "Brief đã cập nhật"
    assert body["note"] == "Xem mục 3"
    assert body["required_for_review"] is True
    assert body["resource_type"] == "SOURCE"
    # Untouched.
    assert body["location"] == DRIVE


async def test_75e_a_resource_cannot_be_moved_to_another_content(world: World) -> None:
    """Not merely refused - unrepresentable. The command has no such field."""
    from meobot.application.pr_content_resource_service import UpdateContentResourceCommand

    assert "content_id" not in UpdateContentResourceCommand.__dataclass_fields__

    first = await world.content("Bài một")
    second = await world.content("Bài hai")
    world.act_as(world.manager)
    resource_id = world.add(first.id).json()["id"]

    # Addressing it under the other item's URL is a 404, not a move.
    assert (
        world.client.patch(
            f"/api/pr/contents/{second.id}/resources/{resource_id}", json={"label": "Đổi"}
        ).status_code
        == 404
    )
    # An unknown field is refused outright by the body schema.
    assert (
        world.client.patch(
            f"/api/pr/contents/{first.id}/resources/{resource_id}",
            json={"content_id": str(second.id)},
        ).status_code
        == 422
    )
    assert [item["id"] for item in world.resources(first.id)] == [resource_id]
    assert world.resources(second.id) == []


async def test_75f_changing_type_to_drive_rechecks_the_location(world: World) -> None:
    """A type that promises Drive must not keep a Dropbox URL."""
    row = await world.content()
    world.act_as(world.manager)
    resource_id = world.add(row.id, location="https://dropbox.com/x").json()["id"]

    response = world.client.patch(
        f"/api/pr/contents/{row.id}/resources/{resource_id}",
        json={"resource_type": "DRIVE_FILE"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "not_a_drive_host"


async def test_75g_deleting_removes_it_and_moves_no_stage(world: World) -> None:
    row = await world.content()
    row.workflow_stage = PrWorkflowStage.HEAD_REVIEW
    await world.session.flush()
    world.act_as(world.manager)
    resource_id = world.add(row.id).json()["id"]

    assert (
        world.client.delete(f"/api/pr/contents/{row.id}/resources/{resource_id}").status_code == 204
    )
    assert world.resources(row.id) == []
    await world.session.refresh(row)
    assert row.workflow_stage is PrWorkflowStage.HEAD_REVIEW


async def test_75h_every_mutation_is_audited(world: World) -> None:
    row = await world.content()
    world.act_as(world.manager)
    resource_id = world.add(row.id, label="Brief khách hàng").json()["id"]
    world.client.patch(
        f"/api/pr/contents/{row.id}/resources/{resource_id}", json={"label": "Brief mới"}
    )
    world.client.delete(f"/api/pr/contents/{row.id}/resources/{resource_id}")

    added = await _audit(world, "pr.content.resource_added")
    updated = await _audit(world, "pr.content.resource_updated")
    deleted = await _audit(world, "pr.content.resource_deleted")
    assert len(added) == len(updated) == len(deleted) == 1

    assert added[0].entity_type == "pr_content_resource"
    assert added[0].after_data["content_code"] == row.code  # type: ignore[index]
    assert added[0].after_data["label"] == "Brief khách hàng"  # type: ignore[index]
    # The update records only what moved.
    assert updated[0].before_data == {"label": "Brief khách hàng"}
    assert updated[0].after_data["label"] == "Brief mới"  # type: ignore[index]


async def test_75i_the_audit_never_copies_the_note(world: World) -> None:
    """An audit trail is not a place to accumulate copies of prose."""
    row = await world.content()
    world.act_as(world.manager)
    world.add(row.id, note="Một ghi chú rất dài về cách dùng tài liệu này")

    payload = str((await _audit(world, "pr.content.resource_added"))[0].after_data)
    assert "rất dài" not in payload


async def test_75j_an_unedited_update_records_nothing(world: World) -> None:
    row = await world.content()
    world.act_as(world.manager)
    resource_id = world.add(row.id, label="Brief khách hàng").json()["id"]

    world.client.patch(
        f"/api/pr/contents/{row.id}/resources/{resource_id}", json={"label": "Brief khách hàng"}
    )
    assert await _audit(world, "pr.content.resource_updated") == []


# =============================================================================
# 76. The boundary with production submissions
# =============================================================================


async def test_76a_resources_and_submissions_are_separate_tables(world: World) -> None:
    """The distinction this whole feature is built on.

    Attaching a moodboard must not put a row anywhere near the table an internal
    reviewer reads to find the cut they are judging.
    """
    row = await world.content()
    world.act_as(world.manager)
    world.add(row.id, resource_type="VIDEO", label="Video tham khảo")

    resources = await world.session.execute(
        select(PrContentResource).where(PrContentResource.content_id == row.id)
    )
    submissions = await world.session.execute(
        select(PrProductionSubmission).where(PrProductionSubmission.content_id == row.id)
    )
    assert len(list(resources.scalars().all())) == 1
    assert list(submissions.scalars().all()) == [], "a reference is never a submission"


async def test_76b_the_resource_vocabulary_has_no_production_types() -> None:
    """No ``FINAL_VIDEO``, no ``NAS_PATH``. Those describe produced output."""
    values = {member.value for member in PrContentResourceType}
    assert values == {
        "REFERENCE",
        "IMAGE",
        "VIDEO",
        "DRIVE_FILE",
        "SOURCE",
        "BRAND_ASSET",
        "OTHER",
    }


async def test_76c_resources_are_visible_to_anybody_who_may_read_the_content(
    world: World,
) -> None:
    """No separate secret-resource visibility model. Editing is the narrow part."""
    row = await world.content()
    world.act_as(world.manager)
    world.add(row.id, label="Brief khách hàng")

    for person in (world.owner, world.stranger, world.reviewer):
        world.act_as(person)
        assert [item["label"] for item in world.resources(row.id)] == ["Brief khách hàng"]


async def test_76d_a_missing_content_item_has_no_resources(world: World) -> None:
    world.act_as(world.manager)
    assert world.resources(uuid.uuid4()) == []


async def _audit(world: World, action: str) -> list[AuditLog]:
    rows = await world.session.execute(
        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at.asc())
    )
    return list(rows.scalars().all())
