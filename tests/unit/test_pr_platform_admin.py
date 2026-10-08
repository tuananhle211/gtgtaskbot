"""Step 1F.2.1 - platform and channel master data through the admin surface.

Seventeen numbered tests. The thing being proven is narrow and concrete: after
Step 1F.2 made a target channel mandatory, a production database with zero
platforms had no way out that did not involve a psql prompt. These tests are
what says it now does.

The interesting half is not "a row appears". It is that ``FACEBOOK`` typed into
a form is the *same token* Step 1F.1 matches on - tests 14 and 15 - because
that is the join between master data somebody types and a policy system nobody
should be able to enter by accident.

Run against the real router over ``TestClient``, on one SQLite transaction, for
the reason ``test_pr_web_admin`` gives: what is unproven below the router is the
wiring, and a service test cannot see it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import get_current_web_actor, get_session
from meobot.api.main import create_app
from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_content_service import ContentTargetSpec, CreateContentCommand
from meobot.application.pr_platform_service import (
    CreatePlatformCommand,
    PrPlatformService,
)
from meobot.application.pr_policy_readiness_service import REASON_PACK_UNAVAILABLE
from meobot.application.pr_services import build_pr_services
from meobot.core.config import Settings, get_settings
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrBrand, PrChannel, PrPlatform
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import PrConflictError, PrValidationError
from meobot.domain.pr.models import (
    POLICY_GROUNDED_PLATFORM_CODES,
    PrChannelCategory,
    PrChannelStatus,
    PrDistributionMode,
    PrEntityStatus,
)
from tests.unit.streams import tag_pr

pytestmark = pytest.mark.asyncio


def _settings() -> Settings:
    return get_settings()


@dataclass
class World:
    """An app on one transaction, an admin, and somebody who is not one."""

    session: AsyncSession
    client: TestClient
    settings: Settings
    #: ADMIN - holds ``SETTINGS_WRITE``, so ``PR_CHANNEL_MANAGE`` resolves.
    admin: User
    #: TEAM_LEAD - full PR access, and no business registering platforms.
    lead: User
    brand_id: uuid.UUID

    def act_as(self, user: User) -> None:
        self.client.app.dependency_overrides[get_current_web_actor] = lambda: Actor(  # type: ignore[attr-defined]
            user_id=user.id, full_name=user.full_name, role=user.role, active=user.active
        )

    def actor(self, user: User) -> Actor:
        return Actor(user_id=user.id, full_name=user.full_name, role=user.role)


@pytest_asyncio.fixture
async def world(session: AsyncSession) -> AsyncIterator[World]:
    admin = User(full_name="Hoa Head", role=Role.ADMIN)
    lead = User(full_name="Le Trưởng Nhóm", role=Role.TEAM_LEAD)
    brand = PrBrand(code="BRND-APEX", name="Apexmed")
    session.add_all([admin, lead, brand])
    await session.flush()
    await tag_pr(session, [admin, lead])  # untagged sees no stream

    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield World(
            session=session,
            client=client,
            settings=settings,
            admin=admin,
            lead=lead,
            brand_id=brand.id,
        )
    app.dependency_overrides.clear()


async def _platform(world: World, *, code: str, name: str) -> PrPlatform:
    """Register a platform through the service, as a seeded fixture would."""
    services = build_pr_services(world.session, world.settings)
    return await services.platforms.create_platform(
        actor=world.actor(world.admin),
        request_id=uuid.uuid4(),
        command=CreatePlatformCommand(code=code, name=name),
    )


# --- 01-04: platforms -------------------------------------------------------


async def test_01_an_authorized_actor_creates_a_platform(world: World) -> None:
    """The whole point: FACEBOOK exists without anybody opening psql."""
    world.act_as(world.admin)
    response = world.client.post("/api/pr/platforms", json={"code": "FACEBOOK", "name": "Facebook"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["code"] == "FACEBOOK"
    assert body["name"] == "Facebook"
    assert body["status"] == PrEntityStatus.ACTIVE.value
    # The server says whether policy applies. A client comparing codes would be
    # making a policy decision in a browser.
    assert body["policy_grounded"] is True


async def test_02_an_unauthorized_actor_cannot_create_a_platform(world: World) -> None:
    """A TEAM_LEAD reads everything here and registers nothing.

    Not a UI concern: the refusal is the server's, and the row does not appear.
    """
    world.act_as(world.lead)
    response = world.client.post("/api/pr/platforms", json={"code": "TIKTOK", "name": "TikTok"})

    assert response.status_code == 403
    remaining = await world.session.execute(select(PrPlatform.code))
    assert remaining.scalars().all() == []


async def test_03_a_duplicate_platform_code_is_refused(world: World) -> None:
    """409, checked, rather than an IntegrityError that aborts the transaction.

    Case-insensitively: ``facebook`` and ``FACEBOOK`` are the same identifier
    written twice, and the normalization happens before the check for exactly
    that reason.
    """
    await _platform(world, code="FACEBOOK", name="Facebook")
    world.act_as(world.admin)

    response = world.client.post(
        "/api/pr/platforms", json={"code": "facebook", "name": "Facebook lần hai"}
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["message"] == "Đã có nền tảng với mã FACEBOOK."
    codes = await world.session.execute(select(PrPlatform.code))
    assert codes.scalars().all() == ["FACEBOOK"]


@pytest.mark.parametrize(
    ("code", "why"),
    [
        ("   ", "blank"),
        ("Facebook Việt Nam", "a display name, not an identifier"),
        ("FACE-BOOK", "punctuation"),
        ("1FACEBOOK", "does not open with a letter"),
    ],
)
async def test_04_a_non_canonical_platform_code_is_refused(
    world: World, code: str, why: str
) -> None:
    """The code is the token Step 1F.1 compares. It has to survive being typed.

    ``Facebook Việt Nam`` is the case that matters: accepting it and quietly
    turning it into ``FACEBOOK`` would infer a legal obligation from a label.
    """
    world.act_as(world.admin)
    response = world.client.post("/api/pr/platforms", json={"code": code, "name": "Facebook"})

    assert response.status_code == 422, f"{why}: {response.text}"
    codes = await world.session.execute(select(PrPlatform.code))
    assert codes.scalars().all() == []


async def test_05_a_lowercase_code_is_normalized_not_rejected(world: World) -> None:
    """``tiktok`` and ``TIKTOK`` are one identifier, so one of them is stored."""
    world.act_as(world.admin)
    response = world.client.post("/api/pr/platforms", json={"code": " tiktok ", "name": "TikTok"})

    assert response.status_code == 201, response.text
    assert response.json()["code"] == "TIKTOK"


# --- 06-11: channels --------------------------------------------------------


async def test_06_an_authorized_actor_creates_a_channel(world: World) -> None:
    """A channel, on a platform, under a brand - and a server-allocated code."""
    platform = await _platform(world, code="FACEBOOK", name="Facebook")
    world.act_as(world.admin)

    response = world.client.post(
        "/api/pr/channels",
        json={
            "name": "Apexmed Facebook",
            "platform_id": str(platform.id),
            "brand_id": str(world.brand_id),
            "category": "SCALE",
        },
    )

    assert response.status_code == 201, response.text
    channel = response.json()["channel"]
    assert channel["name"] == "Apexmed Facebook"
    assert channel["status"] == PrChannelStatus.ACTIVE.value
    assert channel["platform_code"] == "FACEBOOK"
    assert channel["policy_grounded_platform"] is True
    # Step 1C.1 allocates this. Nothing in the form invents an identifier.
    assert channel["code"].startswith("CH-")


async def test_07_an_unauthorized_actor_cannot_create_a_channel(world: World) -> None:
    """``PR_CHANNEL_MANAGE`` guards the channel route as it guards the platform one."""
    platform = await _platform(world, code="FACEBOOK", name="Facebook")
    world.act_as(world.lead)

    response = world.client.post(
        "/api/pr/channels",
        json={"name": "Kênh lén", "platform_id": str(platform.id), "category": "SCALE"},
    )

    assert response.status_code == 403
    rows = await world.session.execute(select(PrChannel.id))
    assert rows.scalars().all() == []


async def test_08_a_channel_needs_a_platform_that_exists(world: World) -> None:
    """An unknown platform id is a 404, not a dangling foreign key."""
    world.act_as(world.admin)
    response = world.client.post(
        "/api/pr/channels",
        json={"name": "Kênh mồ côi", "platform_id": str(uuid.uuid4()), "category": "SCALE"},
    )
    assert response.status_code == 404


async def test_09_a_channel_cannot_name_a_brand_that_does_not_exist(world: World) -> None:
    platform = await _platform(world, code="FACEBOOK", name="Facebook")
    world.act_as(world.admin)

    response = world.client.post(
        "/api/pr/channels",
        json={
            "name": "Kênh sai thương hiệu",
            "platform_id": str(platform.id),
            "brand_id": str(uuid.uuid4()),
            "category": "SCALE",
        },
    )
    assert response.status_code == 404


async def test_10_an_invalid_category_is_refused(world: World) -> None:
    """``PrChannelCategory`` decides, and a made-up value never reaches the row."""
    platform = await _platform(world, code="FACEBOOK", name="Facebook")
    world.act_as(world.admin)

    response = world.client.post(
        "/api/pr/channels",
        json={"name": "Kênh sai danh mục", "platform_id": str(platform.id), "category": "VIRAL"},
    )
    assert response.status_code == 422


async def test_11_a_retired_platform_stops_accepting_new_channels(world: World) -> None:
    """Retiring a platform has to mean something, so it refuses new channels."""
    platform = await _platform(world, code="ORKUT", name="Orkut")
    platform.status = PrEntityStatus.INACTIVE
    await world.session.flush()
    world.act_as(world.admin)

    response = world.client.post(
        "/api/pr/channels",
        json={
            "name": "Kênh trên nền tảng cũ",
            "platform_id": str(platform.id),
            "category": "SCALE",
        },
    )

    assert response.status_code == 422, response.text
    assert "ngừng hoạt động" in response.json()["error"]["message"]


# --- 12-13: the transaction -------------------------------------------------


async def test_12_creating_a_platform_writes_its_audit_in_the_same_transaction(
    world: World,
) -> None:
    """The row and the record of who made it are one write or neither."""
    platform = await _platform(world, code="FACEBOOK", name="Facebook")

    logged = await world.session.execute(
        select(AuditLog).where(AuditLog.entity_id == str(platform.id))
    )
    event = logged.scalars().one()
    assert event.action == "pr.platform.created"
    assert event.actor_user_id == world.admin.id
    assert event.entity_type == "pr_platform"
    assert event.after_data == {"code": "FACEBOOK", "name": "Facebook"}


async def test_13_the_service_does_not_commit(world: World) -> None:
    """Caller owns the transaction. A rollback has to actually undo this.

    A service that committed on its own would make every caller's error handling
    a lie, so the check is a rollback rather than a claim about the code.
    """
    async with world.session.begin_nested() as nested:
        await _platform(world, code="FACEBOOK", name="Facebook")
        await nested.rollback()

    codes = await world.session.execute(select(PrPlatform.code))
    assert codes.scalars().all() == []


# --- 14-15: the join with Step 1F.1 -----------------------------------------


async def _content_targeting(world: World, *, platform_code: str, mode: str) -> uuid.UUID:
    """A platform, a channel on it, and one content item planned there."""
    platform = await _platform(world, code=platform_code, name=platform_code.title())
    services = build_pr_services(world.session, world.settings)
    channel = await services.channels.create_channel(
        actor=world.actor(world.admin),
        request_id=uuid.uuid4(),
        command=CreateChannelCommand(
            name=f"Apexmed {platform_code.title()}",
            platform_id=platform.id,
            brand_id=world.brand_id,
            category=PrChannelCategory.SCALE,
        ),
    )
    snapshot = await services.content.create_content(
        actor=world.actor(world.admin),
        request_id=uuid.uuid4(),
        command=CreateContentCommand(
            title="Chăm sóc sau nâng mũi",
            brand_id=world.brand_id,
            owner_user_id=world.admin.id,
            script_text="Hook. Body. CTA.",
            targets=(
                ContentTargetSpec(
                    channel_id=channel.id, distribution_mode=PrDistributionMode(mode)
                ),
            ),
        ),
    )
    return snapshot.content.id


@pytest.mark.parametrize("code", sorted(POLICY_GROUNDED_PLATFORM_CODES))
async def test_14_a_canonical_code_is_recognized_by_policy_readiness(
    world: World, code: str
) -> None:
    """A platform typed into the form is the one Step 1F.1 grounds against.

    The assertion is deliberately *not* "readiness passes" - this database has
    no active pack, so it must not - but that readiness recognized the platform
    as one it grounds, refused for that reason, and named it. A platform whose
    code did not match would have sailed through to a generic review instead.
    """
    content_id = await _content_targeting(world, platform_code=code, mode="ORGANIC")
    services = build_pr_services(world.session, world.settings)

    readiness = await services.policy_readiness.evaluate(content_id)

    assert readiness.ready is False
    assert readiness.reason == REASON_PACK_UNAVAILABLE
    assert readiness.platform_code == code


async def test_15_a_platform_code_that_is_not_canonical_is_not_grounded(world: World) -> None:
    """``FB_VN`` is a platform. It is not Facebook, and nothing pretends it is.

    So it needs no Organic/Paid choice and no pack, and readiness lets it
    through to the generic Step 1F review - which is the honest answer, because
    no official Facebook policy was ever consulted for it.
    """
    platform = await _platform(world, code="FB_VN", name="Facebook Việt Nam")
    world.act_as(world.admin)

    listed = world.client.get("/api/pr/platforms")
    row = next(item for item in listed.json() if item["id"] == str(platform.id))
    assert row["policy_grounded"] is False

    content_id = await _content_targeting(world, platform_code="FB_VN_2", mode="UNSPECIFIED")
    services = build_pr_services(world.session, world.settings)
    readiness = await services.policy_readiness.evaluate(content_id)
    assert readiness.ready is True


# --- 16-17: what the target picker sees -------------------------------------


async def test_16_a_new_active_channel_appears_in_the_target_picker(world: World) -> None:
    """The acceptance criterion, end to end: create it, then it is choosable."""
    platform = await _platform(world, code="FACEBOOK", name="Facebook")
    world.act_as(world.admin)
    created = world.client.post(
        "/api/pr/channels",
        json={
            "name": "Apexmed Facebook",
            "platform_id": str(platform.id),
            "brand_id": str(world.brand_id),
            "category": "SCALE",
        },
    )
    assert created.status_code == 201, created.text

    listed = world.client.get("/api/pr/channels?status=ACTIVE")
    assert [row["name"] for row in listed.json()] == ["Apexmed Facebook"]
    assert listed.json()[0]["policy_grounded_platform"] is True


async def test_17_an_inactive_channel_is_not_offered_as_a_target(world: World) -> None:
    """A paused channel stays in the admin list and leaves the picker."""
    platform = await _platform(world, code="FACEBOOK", name="Facebook")
    world.act_as(world.admin)
    created = world.client.post(
        "/api/pr/channels",
        json={"name": "Kênh tạm dừng", "platform_id": str(platform.id), "category": "SCALE"},
    )
    channel_id = created.json()["channel"]["id"]

    paused = world.client.patch(f"/api/pr/channels/{channel_id}", json={"status": "INACTIVE"})
    assert paused.status_code == 200, paused.text

    assert world.client.get("/api/pr/channels?status=ACTIVE").json() == []
    assert len(world.client.get("/api/pr/channels").json()) == 1


# --- Service-level edges the HTTP surface cannot reach ----------------------


async def test_18_the_service_refuses_a_blank_name(world: World) -> None:
    """Pydantic catches this over HTTP; the service is the one that decides."""
    services = build_pr_services(world.session, world.settings)
    with pytest.raises(PrValidationError):
        await services.platforms.create_platform(
            actor=world.actor(world.admin),
            request_id=uuid.uuid4(),
            command=CreatePlatformCommand(code="FACEBOOK", name="   "),
        )


async def test_19_the_platform_picker_hides_retired_platforms(world: World) -> None:
    """Default ACTIVE, and the caller has to ask for anything else."""
    live = await _platform(world, code="FACEBOOK", name="Facebook")
    retired = await _platform(world, code="ORKUT", name="Orkut")
    retired.status = PrEntityStatus.INACTIVE
    await world.session.flush()

    service = PrPlatformService(
        world.session,
        AuditService(world.session),
        PrCapabilityService(world.session, AuditService(world.session)),
    )
    offered = await service.list_platforms(actor=world.actor(world.lead))
    assert [row.id for row in offered] == [live.id]

    everything = await service.list_platforms(actor=world.actor(world.lead), status=None)
    assert {row.id for row in everything} == {live.id, retired.id}


async def test_20_a_duplicate_is_refused_by_the_service_not_only_the_index(
    world: World,
) -> None:
    """The typed error is what lets a caller answer; the index is the backstop."""
    await _platform(world, code="FACEBOOK", name="Facebook")
    with pytest.raises(PrConflictError):
        await _platform(world, code="FACEBOOK", name="Facebook again")
