"""Step 1F.2.7a: the queue lists what the write would accept, and nothing else.

Step 1F.2.7 scoped the **write**. The read models kept a pre-scoping rule: *this
person holds Head Review, so every item at ``HEAD_REVIEW`` is theirs*. So a
member granted *Duyệt Trưởng nhóm* over two Facebook channels opened "Cần tôi xử
lý" and saw every item at that gate - press articles, YouTube scripts, other
teams' channels - each of which the write would refuse. A queue that behaves
like that trains people to ignore it.

Three things are under test here, and they fail differently:

* **equivalence.** :func:`~meobot.application.pr_grant_scope_sql.scope_matches`
  is a second expression of
  :meth:`~meobot.domain.pr.grants.GrantScope.covers` - it has to be, because a
  queue must narrow the ``WHERE`` rather than the page. Section 4 runs both over
  a matrix of scopes by items and asserts they never disagree;
* **the queue itself** - the board's *Cần tôi xử lý*, the dashboard's list, and
  ``/reviews/pending`` - contains exactly the approvable items;
* **the counters** that caption them agree with the rows, under pagination.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import get_current_web_actor, get_session
from meobot.api.main import create_app
from meobot.application.audit_service import AuditService
from meobot.application.pr_ai_review_service import RecordAiReviewCommand
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_content_query import ContentQuery
from meobot.application.pr_content_service import ContentTargetSpec, CreateContentCommand
from meobot.application.pr_services import build_pr_services
from meobot.core.config import Settings
from meobot.db.models.pr import PrBrand, PrChannel, PrContentTarget, PrPlatform
from meobot.db.models.pr_platform_policy import PrPlatformPolicyPack
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.content_views import PrContentViewScope
from meobot.domain.pr.grants import ContentScopeKey, GrantScope, PrGrantScopeMode
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewType,
    PrChannelCategory,
    PrContentType,
    PrDistributionMode,
    PrPolicyPackStatus,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from tests.unit.streams import tag_pr

pytestmark = pytest.mark.asyncio

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
    owner: User
    lead: User
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

    async def content_id(
        self,
        *,
        title: str = "Một bài",
        content_type: PrContentType | None = PrContentType.SHORT_VIDEO_SCRIPT,
        channels: tuple[PrChannel, ...] = (),
    ) -> uuid.UUID:
        """One content item, as an id.

        Never the ORM row: SQLAlchemy's identity map holds weak references, so a
        row this test still points at stays in it and an attribute the app's own
        ``UPDATE`` expires is then lazily reloaded inside the request thread,
        outside the greenlet the async layer needs.
        """
        services = build_pr_services(self.session, self.settings)
        snapshot = await services.content.create_content(
            actor=self.actor(self.owner),
            request_id=self.request_id,
            command=CreateContentCommand(
                title=title,
                brand_id=self.brand.id,
                owner_user_id=self.owner.id,
                content_type=content_type,
                script_text="Nội dung.",
                targets=tuple(
                    ContentTargetSpec(
                        channel_id=item.id, distribution_mode=PrDistributionMode.ORGANIC
                    )
                    for item in channels
                ),
            ),
        )
        return snapshot.content.id

    async def to_team_lead_review(self, content_id: uuid.UUID) -> None:
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

    async def strip_targets(self, content_id: uuid.UUID) -> None:
        """Take every planned channel off an item that already stands at a gate.

        Step 1F.2 refuses to send an item with no target into AI review, so an
        item cannot *arrive* at a script gate unassigned - but it can end up
        there, because plans change and a target can be taken off afterwards.
        That is the state ``include_unassigned_channel`` exists for, and this is
        how a test reaches it honestly rather than by forcing a stage.
        """
        await self.session.execute(
            delete(PrContentTarget).where(PrContentTarget.content_id == content_id)
        )
        await self.session.flush()

    async def gated(
        self,
        *,
        title: str = "Một bài",
        content_type: PrContentType | None = PrContentType.SHORT_VIDEO_SCRIPT,
        channels: tuple[PrChannel, ...] = (),
    ) -> uuid.UUID:
        content_id = await self.content_id(
            title=title, content_type=content_type, channels=channels
        )
        await self.to_team_lead_review(content_id)
        return content_id

    async def grant(
        self, user: User, capability: PrCapability, scope: GrantScope, **kwargs: object
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

    # --- The three read models, as titles -------------------------------
    async def my_actions(self, user: User, **query: object) -> list[str]:
        """The board's *Cần tôi xử lý*, through the service."""
        page = await build_pr_services(self.session, self.settings).queries.content_page(
            actor=self.actor(user),
            query=ContentQuery(scope=PrContentViewScope.MY_ACTIONS, **query),  # type: ignore[arg-type]
            on=TODAY,
        )
        return [row.title for row in page.items]

    async def my_actions_total(self, user: User, **query: object) -> int:
        page = await build_pr_services(self.session, self.settings).queries.content_page(
            actor=self.actor(user),
            query=ContentQuery(scope=PrContentViewScope.MY_ACTIONS, **query),  # type: ignore[arg-type]
            on=TODAY,
        )
        return page.total

    async def awaiting(self, user: User) -> list[str]:
        """``content_awaiting``, which the dashboard and the bot both read."""
        rows = await build_pr_services(self.session, self.settings).queries.content_awaiting(
            actor=self.actor(user), on=TODAY
        )
        return [row.title for row in rows]

    def dashboard(self, user: User) -> dict[str, object]:
        self.act_as(user)
        response = self.client.get("/api/pr/dashboard")
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]

    def pending(self, user: User) -> list[str]:
        self.act_as(user)
        response = self.client.get("/api/pr/reviews/pending")
        assert response.status_code == 200, response.text
        return [row["title"] for row in response.json()]


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
    await tag_pr(session, [owner, lead, member])  # untagged sees no stream

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

    # Facebook and TikTok are policy-grounded, so an organic target on either
    # needs an ACTIVE pack before the item may enter AI review - Step 1F.1's
    # rule, and a precondition of reaching the gate rather than anything to do
    # with who may approve.
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


# =============================================================================
# 1. A scoped grant puts the right items in the queue, and only those
# =============================================================================


async def test_1a_a_matching_scoped_grant_appears(world: World) -> None:
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    await world.gated(
        title="Bài Facebook",
        content_type=PrContentType.FACEBOOK_POST,
        channels=(world.facebook,),
    )
    assert await world.my_actions(world.member) == ["Bài Facebook"]
    assert await world.awaiting(world.member) == ["Bài Facebook"]
    assert world.pending(world.member) == ["Bài Facebook"]


async def test_1b_the_wrong_channel_does_not_appear(world: World) -> None:
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    await world.gated(
        title="Bài trên YouTube",
        content_type=PrContentType.FACEBOOK_POST,
        channels=(world.youtube,),
    )
    assert await world.my_actions(world.member) == []
    assert await world.awaiting(world.member) == []
    assert world.pending(world.member) == []


async def test_1c_the_wrong_content_type_does_not_appear(world: World) -> None:
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    await world.gated(
        title="Bài báo chí",
        content_type=PrContentType.PRESS_ARTICLE,
        channels=(world.facebook,),
    )
    assert await world.my_actions(world.member) == []
    assert await world.awaiting(world.member) == []


async def test_1d_a_wrong_gate_does_not_appear(world: World) -> None:
    """A Head grant does not make the Team Lead queue theirs, however wide."""
    await world.grant(world.member, PrCapability.PR_HEAD_REVIEW, GrantScope.everything())
    await world.gated(title="Chờ trưởng nhóm", channels=(world.tiktok,))
    assert await world.my_actions(world.member) == []
    assert await world.awaiting(world.member) == []


# =============================================================================
# 2. Multi-channel items: partial coverage is not coverage
# =============================================================================


async def test_2a_a_multi_channel_item_with_partial_coverage_does_not_appear(
    world: World,
) -> None:
    """The rule the whole subset test exists for.

    Approving an item that goes to TikTok *and* YouTube is what puts it on
    YouTube, so a grant naming only TikTok must not be shown it - the write
    would refuse, and the queue must agree.
    """
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    await world.gated(title="Hai kênh", channels=(world.tiktok, world.youtube))
    assert await world.my_actions(world.member) == []
    assert await world.awaiting(world.member) == []


async def test_2b_a_multi_channel_item_fully_covered_appears(world: World) -> None:
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id, world.facebook.id}),
        ),
    )
    await world.gated(title="Hai kênh đủ", channels=(world.tiktok, world.facebook))
    assert await world.my_actions(world.member) == ["Hai kênh đủ"]
    assert await world.awaiting(world.member) == ["Hai kênh đủ"]


async def test_2c_the_missing_cases_stay_denied_by_default(world: World) -> None:
    """Unclassified and unassigned are refused unless the grant says otherwise.

    The dangerous direction: a subset test over an empty target set is vacuously
    true, so without its own branch every scoped grant would silently reach every
    item nobody has given a channel.
    """
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    await world.gated(title="Chưa phân loại", content_type=None, channels=(world.tiktok,))
    assert await world.my_actions(world.member) == []
    assert await world.awaiting(world.member) == []


async def test_2d_the_missing_cases_appear_when_opted_into(world: World) -> None:
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
            unclassified=True,
        ),
    )
    await world.gated(title="Chưa phân loại", content_type=None, channels=(world.tiktok,))
    assert await world.my_actions(world.member) == ["Chưa phân loại"]


# =============================================================================
# 3. Time, revocation, roles and "all"
# =============================================================================


async def test_3a_an_expired_grant_counts_for_nothing(world: World) -> None:
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        GrantScope.everything(),
        effective_to=TODAY - timedelta(days=1),
    )
    await world.gated(title="Hết hạn rồi", channels=(world.tiktok,))
    assert await world.my_actions(world.member) == []
    assert await world.awaiting(world.member) == []
    assert world.dashboard(world.member)["awaiting_my_review"] == []


async def test_3b_a_revoked_grant_counts_for_nothing(world: World) -> None:
    grant_id = await world.grant(
        world.member, PrCapability.PR_TEAM_LEAD_REVIEW, GrantScope.everything()
    )
    await world.gated(title="Bị thu hồi", channels=(world.tiktok,))
    assert await world.my_actions(world.member) == ["Bị thu hồi"]

    await world.capabilities.revoke(
        actor=world.actor(world.owner), request_id=world.request_id, grant_id=grant_id
    )
    assert await world.my_actions(world.member) == []
    assert await world.awaiting(world.member) == []
    assert world.pending(world.member) == []


async def test_3c_a_role_backed_grant_still_sees_everything_it_did(world: World) -> None:
    """Requirement 8, and what revision 0031 migrates old grants to.

    A ``requires_role_baseline`` grant over ``ALL``/``ALL`` held by somebody
    whose role carries the permission is the pre-1F.2.7 reviewer. Their queue is
    unchanged: every item at their gate, whatever its classification or channel.
    """
    await world.capabilities.grant(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.lead.id,
        capability=PrCapability.PR_TEAM_LEAD_REVIEW,
        scope=GrantScope.everything(),
        requires_role_baseline=True,
    )
    await world.gated(title="TikTok", channels=(world.tiktok,))
    await world.gated(
        title="Báo chí", content_type=PrContentType.PRESS_ARTICLE, channels=(world.youtube,)
    )
    stripped = await world.gated(title="Không kênh", content_type=None, channels=(world.tiktok,))
    await world.strip_targets(stripped)
    assert sorted(await world.my_actions(world.lead)) == ["Báo chí", "Không kênh", "TikTok"]


async def test_3d_a_role_backed_grant_whose_role_lost_the_permission_sees_nothing(
    world: World,
) -> None:
    """The other half of requirement 8: the queue follows the write.

    An ``EMPLOYEE`` holding a migrated grant cannot approve - the grant asks for
    the role baseline and their role has none - so their queue is empty. The
    queue asks ``approval_grants_for``, which applies the same test.
    """
    await world.capabilities.grant(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        capability=PrCapability.PR_TEAM_LEAD_REVIEW,
        scope=GrantScope.everything(),
        requires_role_baseline=True,
    )
    await world.gated(title="Không thấy được", channels=(world.tiktok,))
    assert await world.my_actions(world.member) == []
    assert await world.awaiting(world.member) == []


async def test_3e_two_grants_union_their_scopes(world: World) -> None:
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
    )
    await world.gated(
        title="FB", content_type=PrContentType.FACEBOOK_POST, channels=(world.facebook,)
    )
    await world.gated(title="TT", channels=(world.tiktok,))
    await world.gated(
        title="Chéo", content_type=PrContentType.FACEBOOK_POST, channels=(world.tiktok,)
    )
    assert sorted(await world.my_actions(world.member)) == ["FB", "TT"]


# =============================================================================
# 4. The counters, the dashboard and pagination
# =============================================================================


async def test_4a_the_dashboard_list_and_the_board_agree(world: World) -> None:
    """Requirement 2: one eligibility rule, three surfaces."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    for index in range(3):
        await world.gated(
            title=f"Của tôi {index}",
            content_type=PrContentType.FACEBOOK_POST,
            channels=(world.facebook,),
        )
    for index in range(4):
        await world.gated(title=f"Của người khác {index}", channels=(world.youtube,))

    board = sorted(await world.my_actions(world.member))
    body = world.dashboard(world.member)
    dashboard_titles = sorted(row["title"] for row in body["awaiting_my_review"])
    assert board == dashboard_titles == ["Của tôi 0", "Của tôi 1", "Của tôi 2"]
    assert sorted(world.pending(world.member)) == board
    assert await world.my_actions_total(world.member) == 3


async def test_4b_the_stage_counters_count_only_approvable_items(world: World) -> None:
    """The badge is the same ``WHERE`` as the rows, so it cannot overcount."""
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    await world.gated(
        title="Đếm được", content_type=PrContentType.FACEBOOK_POST, channels=(world.facebook,)
    )
    for index in range(5):
        await world.gated(title=f"Ngoài phạm vi {index}", channels=(world.youtube,))

    page = await build_pr_services(world.session, world.settings).queries.content_page(
        actor=world.actor(world.member),
        query=ContentQuery(scope=PrContentViewScope.MY_ACTIONS),
        on=TODAY,
    )
    assert page.total == 1
    assert page.stage_counts.get(PrWorkflowStage.TEAM_LEAD_REVIEW, 0) == 1
    assert sum(page.stage_counts.values()) == 1


async def test_4c_pagination_never_reintroduces_an_unauthorized_row(world: World) -> None:
    """The filter is in the ``WHERE``, not in a pass over the page.

    Six out-of-scope items sit between the two in scope. A read model that
    filtered after ``LIMIT`` would return page 1 with one row and page 2 with
    none, and the pager caption would say eight.
    """
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.facebook.id}),
        ),
    )
    await world.gated(
        title="Trong phạm vi A",
        content_type=PrContentType.FACEBOOK_POST,
        channels=(world.facebook,),
    )
    for index in range(6):
        await world.gated(title=f"Ngoài {index}", channels=(world.youtube,))
    await world.gated(
        title="Trong phạm vi B",
        content_type=PrContentType.FACEBOOK_POST,
        channels=(world.facebook,),
    )

    assert await world.my_actions_total(world.member) == 2
    first = await world.my_actions(world.member, limit=1, offset=0)
    second = await world.my_actions(world.member, limit=1, offset=1)
    third = await world.my_actions(world.member, limit=1, offset=2)
    assert len(first) == 1 and len(second) == 1 and third == []
    assert sorted(first + second) == ["Trong phạm vi A", "Trong phạm vi B"]


async def test_4d_someone_with_no_grant_has_an_empty_queue_and_a_zero_badge(
    world: World,
) -> None:
    await world.gated(title="Không của ai", channels=(world.tiktok,))
    for person in (world.member, world.lead, world.owner):
        assert await world.my_actions(person) == []
        assert await world.my_actions_total(person) == 0
        assert await world.awaiting(person) == []


# =============================================================================
# 5. The two implementations of the rule agree, cell by cell
# =============================================================================


async def test_5a_the_sql_clause_and_the_domain_object_never_disagree(world: World) -> None:
    """The parity test the second implementation is allowed to exist because of.

    Every scope shape against every item shape. One side is
    :meth:`GrantScope.covers` walking a loaded object; the other is
    ``approvable_by`` narrowing a real query. A branch added to one and not the
    other fails here rather than in production, where it would look like a queue
    that is slightly wrong for one person.
    """
    from sqlalchemy import select

    from meobot.application.pr_grant_scope_sql import scope_matches
    from meobot.db.models.pr import PrContentItem, PrContentTarget

    items: list[tuple[str, uuid.UUID, ContentScopeKey]] = []

    async def add(
        name: str, content_type: PrContentType | None, channels: tuple[PrChannel, ...]
    ) -> None:
        content_id = await world.content_id(
            title=name, content_type=content_type, channels=channels
        )
        items.append(
            (
                name,
                content_id,
                ContentScopeKey(
                    content_type=content_type,
                    channel_ids=frozenset(channel.id for channel in channels),
                ),
            )
        )

    await add("tt-short", PrContentType.SHORT_VIDEO_SCRIPT, (world.tiktok,))
    await add("fb-post", PrContentType.FACEBOOK_POST, (world.facebook,))
    await add("yt-press", PrContentType.PRESS_ARTICLE, (world.youtube,))
    await add("two-channels", PrContentType.SHORT_VIDEO_SCRIPT, (world.tiktok, world.facebook))
    await add(
        "three-channels", PrContentType.FACEBOOK_POST, (world.tiktok, world.facebook, world.youtube)
    )
    await add("unclassified", None, (world.tiktok,))
    await add("no-channel", PrContentType.SHORT_VIDEO_SCRIPT, ())
    await add("neither", None, ())

    scopes: list[GrantScope] = [
        GrantScope(),
        GrantScope.everything(),
        GrantScope(
            content_type_scope=ALL, channel_scope=SELECTED, channel_ids=frozenset({world.tiktok.id})
        ),
        GrantScope(
            content_type_scope=SELECTED,
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channel_scope=ALL,
        ),
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
        ),
        selected(
            content_types=frozenset(
                {PrContentType.SHORT_VIDEO_SCRIPT, PrContentType.FACEBOOK_POST}
            ),
            channels=frozenset({world.tiktok.id, world.facebook.id}),
        ),
        selected(
            content_types=frozenset({PrContentType.FACEBOOK_POST}),
            channels=frozenset({world.tiktok.id, world.facebook.id, world.youtube.id}),
        ),
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
            unclassified=True,
        ),
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset({world.tiktok.id}),
            unassigned=True,
        ),
        selected(
            content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
            channels=frozenset(),
            unassigned=True,
        ),
        selected(
            content_types=frozenset(), channels=frozenset({world.tiktok.id}), unclassified=True
        ),
        GrantScope(
            content_type_scope=ALL,
            channel_scope=SELECTED,
            channel_ids=frozenset(),
            include_unassigned_channel=True,
        ),
    ]

    # Sanity: the fixture has to contain items on both sides of every branch, or
    # the parity assertion below would be satisfied by an empty intersection.
    assert len(items) == 8
    seen_true = seen_false = False

    for index, scope in enumerate(scopes):
        matched = set(
            (
                await world.session.execute(select(PrContentItem.title).where(scope_matches(scope)))
            ).scalars()
        )
        for name, _content_id, key in items:
            expected = scope.covers(key)
            seen_true = seen_true or expected
            seen_false = seen_false or not expected
            assert (name in matched) is expected, (index, name, expected)

    assert seen_true and seen_false

    # And the correlated subquery really is per item, not per table: an item
    # whose only target is TikTok must not be matched by a scope that names only
    # Facebook, even though a Facebook target exists somewhere in the table.
    facebook_only = selected(
        content_types=frozenset({PrContentType.SHORT_VIDEO_SCRIPT}),
        channels=frozenset({world.facebook.id}),
    )
    matched = set(
        (
            await world.session.execute(
                select(PrContentItem.title).where(scope_matches(facebook_only))
            )
        ).scalars()
    )
    assert "tt-short" not in matched
    assert (
        await world.session.scalar(
            select(PrContentTarget.id)
            .where(PrContentTarget.channel_id == world.facebook.id)
            .limit(1)
        )
    ) is not None


async def test_5b_the_queue_admits_exactly_what_can_approve_admits(world: World) -> None:
    """The end-to-end equivalence, item by item, through both services.

    Not a re-run of the parity test above: this one goes through the *whole* of
    each path - ``content_page`` on one side, ``can_approve`` on the other - so a
    gate mismatch, a stale grant or a missing role-baseline test would show up
    here even though the scope arithmetic agrees.
    """
    await world.grant(
        world.member,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        selected(
            content_types=frozenset(
                {PrContentType.SHORT_VIDEO_SCRIPT, PrContentType.FACEBOOK_POST}
            ),
            channels=frozenset({world.tiktok.id, world.facebook.id}),
        ),
    )
    plan: list[tuple[str, PrContentType | None, tuple[PrChannel, ...]]] = [
        ("a", PrContentType.SHORT_VIDEO_SCRIPT, (world.tiktok,)),
        ("b", PrContentType.FACEBOOK_POST, (world.facebook,)),
        ("c", PrContentType.SHORT_VIDEO_SCRIPT, (world.tiktok, world.facebook)),
        ("d", PrContentType.SHORT_VIDEO_SCRIPT, (world.tiktok, world.youtube)),
        ("e", PrContentType.PRESS_ARTICLE, (world.tiktok,)),
        ("f", None, (world.tiktok,)),
        # ``g`` is the unassigned case: it reaches the gate with a channel and
        # then loses it, which is the only way an item gets there without one.
        ("g", PrContentType.FACEBOOK_POST, (world.facebook,)),
    ]
    ids: dict[str, uuid.UUID] = {}
    for title, content_type, channels in plan:
        ids[title] = await world.gated(title=title, content_type=content_type, channels=channels)
    await world.strip_targets(ids["g"])

    from meobot.db.models.pr import PrContentItem
    from meobot.domain.pr.models import PrApprovalStage

    listed = set(await world.my_actions(world.member))
    for title, content_id in ids.items():
        content = await world.session.get(PrContentItem, content_id)
        assert content is not None
        writable = await world.capabilities.can_approve(
            world.actor(world.member), content, PrApprovalStage.TEAM_LEAD_REVIEW, on=TODAY
        )
        assert (title in listed) is writable, title
    assert listed == {"a", "b", "c"}
