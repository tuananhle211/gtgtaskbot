"""Step 1F.2.3e.1: references supplied while the content item is being created.

Step 1F.2.3e could only attach review material to a piece that already existed,
which meant somebody holding the brief, the packshot and two reference videos
had to create the item first and attach four things to it afterwards. This step
lets the create request carry them.

What is actually worth testing here is not "the field is accepted" - it is the
**aggregate**. One request, one transaction, and a refusal on the third
reference that leaves no content item, no resource row and no audit row behind.
The alternative shape, a committed create followed by N resource posts from the
client, fails by leaving a piece of content in the database holding half its
references and nobody aware of it; sections 78 and 79 are what would fail if the
implementation ever drifted back to that.

The rest is regression. Section 80 is the authorization question this step
raises and answers - creating a resource as part of a create is gated on the
create permission and nothing more, while everything about editing an
*existing* item's resources is untouched. Sections 81 and 82 are the fields and
the delete that had to keep working.

The harness is Step 1F.2.3e's own ``World``, imported rather than rebuilt: these
tests and the ones about post-create CRUD have to be talking about the same
brand, the same channel and the same four people with different authority, or
"the same validator" and "unchanged authorization" are claims about two
different worlds.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from meobot.application.pr_content_resource_support import ContentResourceSpec
from meobot.application.pr_content_service import ContentTargetSpec, CreateContentCommand
from meobot.application.pr_services import build_pr_services
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrContentItem
from meobot.db.models.pr_content_resource import PrContentResource
from meobot.db.models.user_notification import UserNotification
from meobot.domain.pr.errors import PrNotFoundError, PrValidationError
from meobot.domain.pr.models import PrContentResourceType, PrDistributionMode
from tests.unit.test_pr_content_types_and_resources import DRIVE, World, world

__all__ = ["world"]  # re-exported fixture, imported for its own sake

pytestmark = pytest.mark.asyncio

REFERENCE = {
    "resource_type": "REFERENCE",
    "label": "Brief khách hàng",
    "location": DRIVE,
}
IMAGE = {
    "resource_type": "IMAGE",
    "label": "Ảnh packshot sản phẩm",
    "location": "/volume1/PR/2026/packshot.jpg",
    "note": "Dùng packshot số 3",
}
VIDEO = {
    "resource_type": "VIDEO",
    "label": "Video tham khảo",
    "location": "https://example.com/ref.mp4",
    "required_for_review": True,
}


def _create(world: World, **overrides: object):  # type: ignore[no-untyped-def]
    """POST a valid create body, with whatever this test wants changed."""
    body: dict[str, object] = {
        "title": "Bài mới",
        "brand_id": str(world.brand.id),
        "owner_user_id": str(world.owner.id),
        "content_type": "SHORT_VIDEO_SCRIPT",
        "targets": [{"channel_id": str(world.channel.id), "distribution_mode": "ORGANIC"}],
    }
    body.update(overrides)
    return world.client.post("/api/pr/contents", json=body)


async def _count(world: World, model: type, *where: object) -> int:
    total = await world.session.execute(select(func.count()).select_from(model).where(*where))  # type: ignore[arg-type]
    return int(total.scalar_one())


async def _audit(world: World, action: str) -> list[AuditLog]:
    rows = await world.session.execute(
        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at.asc())
    )
    return list(rows.scalars().all())


def _resources_of(world: World, response) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    """The created item's resources, read back the way the detail page reads them."""
    assert response.status_code == 201, response.text
    return world.resources(uuid.UUID(response.json()["content"]["id"]))


# =============================================================================
# 77. Zero to many, and every field of each
# =============================================================================


async def test_77a_content_is_created_with_no_resources_at_all(world: World) -> None:
    """The ordinary case, and the field is not even sent.

    Resources are optional and this is the shape most creates have: somebody has
    a title, a channel and a format, and the brief will arrive later. A create
    flow that had started *requiring* material would have made the feature a
    tax on every piece of content.
    """
    world.act_as(world.manager)
    response = _create(world)
    assert response.status_code == 201, response.text
    assert _resources_of(world, response) == []


async def test_77b_an_explicit_empty_list_is_the_same_thing(world: World) -> None:
    """A form that renders no drafts sends ``[]``; that is not an error."""
    world.act_as(world.manager)
    response = _create(world, initial_resources=[])
    assert response.status_code == 201, response.text
    assert _resources_of(world, response) == []


async def test_77c_one_reference_is_attached_by_the_create_request(world: World) -> None:
    world.act_as(world.manager)
    response = _create(world, initial_resources=[REFERENCE])

    resources = _resources_of(world, response)
    assert len(resources) == 1
    assert resources[0]["resource_type"] == "REFERENCE"
    assert resources[0]["label"] == "Brief khách hàng"
    assert resources[0]["location"] == DRIVE
    # Computed on the server, as it is for a resource attached later.
    assert resources[0]["is_link"] is True
    # Attributed to whoever filled the form, not to the item's owner.
    assert resources[0]["added_by_user_id"] == str(world.manager.id)


async def test_77d_several_resources_arrive_together(world: World) -> None:
    """Three in one request - the case the whole step exists for."""
    world.act_as(world.manager)
    response = _create(world, initial_resources=[REFERENCE, IMAGE, VIDEO])

    resources = _resources_of(world, response)
    assert len(resources) == 3
    assert {row["label"] for row in resources} == {
        "Brief khách hàng",
        "Ảnh packshot sản phẩm",
        "Video tham khảo",
    }
    # A NAS path stays a path: nothing rewrites it, and the server says it is
    # not a link so the browser renders it as text to copy.
    packshot = next(row for row in resources if row["resource_type"] == "IMAGE")
    assert packshot["location"] == "/volume1/PR/2026/packshot.jpg"
    assert packshot["is_link"] is False


@pytest.mark.parametrize("resource_type", list(PrContentResourceType))
async def test_77e_every_resource_type_is_accepted_at_create(
    world: World, resource_type: PrContentResourceType
) -> None:
    """All seven, through ``initial_resources`` - not a subset of the vocabulary."""
    world.act_as(world.manager)
    location = (
        DRIVE if resource_type is PrContentResourceType.DRIVE_FILE else "https://example.com/x"
    )
    response = _create(
        world,
        initial_resources=[
            {"resource_type": resource_type.value, "label": "Tài liệu", "location": location}
        ],
    )

    resources = _resources_of(world, response)
    assert [row["resource_type"] for row in resources] == [resource_type.value]


async def test_77f_required_for_review_persists_and_sorts_first(world: World) -> None:
    """The flag survives the create, and the list is the server's usual order.

    Ordering is asserted here rather than "the order they were sent": a client
    draft index is a fact about a form, and the list a reviewer reads is the
    server's - required material first, whenever it was attached.
    """
    world.act_as(world.manager)
    response = _create(world, initial_resources=[REFERENCE, VIDEO])

    resources = _resources_of(world, response)
    assert [row["required_for_review"] for row in resources] == [True, False]
    assert resources[0]["label"] == "Video tham khảo"


async def test_77g_the_note_persists(world: World) -> None:
    world.act_as(world.manager)
    response = _create(world, initial_resources=[IMAGE])
    assert _resources_of(world, response)[0]["note"] == "Dùng packshot số 3"


async def test_77h_priority_and_content_type_still_arrive_with_them(world: World) -> None:
    """Step 1F.2.3d and 1F.2.3e are not disturbed by carrying references too."""
    world.act_as(world.manager)
    response = _create(world, priority="URGENT", initial_resources=[REFERENCE])

    assert response.status_code == 201, response.text
    body = response.json()["content"]
    assert body["priority"] == "URGENT"
    assert body["content_type"] == "SHORT_VIDEO_SCRIPT"
    assert len(_resources_of(world, response)) == 1


# =============================================================================
# 78. One transaction: all of it, or none of it
# =============================================================================


@pytest.mark.parametrize(
    "location",
    ["javascript:alert(1)", "data:text/html;base64,PHNjcmlwdD4=", "drive.google.com/file/d/1"],
)
async def test_78a_an_unacceptable_location_refuses_the_whole_create(
    world: World, location: str
) -> None:
    """The security boundary holds at creation, exactly as it does afterwards.

    A stored location is rendered as an ``href``. If the create path had its own
    laxer check, ``javascript:`` would enter through the form that is used most.
    """
    world.act_as(world.manager)
    response = _create(
        world,
        title="Bài không được tạo",
        initial_resources=[{**REFERENCE, "location": location}],
    )

    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] in {"unsafe_scheme", "unsupported_scheme", "missing_scheme"}
    assert details["initial_resource_index"] == 0
    assert await _count(world, PrContentItem, PrContentItem.title == "Bài không được tạo") == 0


async def test_78b_an_invalid_second_resource_takes_the_first_and_the_content(
    world: World,
) -> None:
    """Requirement: no partial aggregate.

    The first resource is perfectly valid. What must not happen is that it
    survives - either on its own, or attached to a content item created from a
    form its author was told had failed.
    """
    world.act_as(world.manager)
    contents_before = await _count(world, PrContentItem)
    resources_before = await _count(world, PrContentResource)

    response = _create(
        world,
        title="Bài không được tạo",
        initial_resources=[REFERENCE, {**IMAGE, "location": "javascript:alert(1)"}, VIDEO],
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["initial_resource_index"] == 1
    assert await _count(world, PrContentItem) == contents_before
    assert await _count(world, PrContentResource) == resources_before


async def test_78c_a_refused_create_writes_no_audit_at_all(world: World) -> None:
    """Not the content event, not the version event, not one resource event.

    An audit row describing a change that did not happen is worse than no row:
    it is a record somebody would act on.
    """
    world.act_as(world.manager)
    response = _create(
        world,
        title="Bài không được tạo",
        initial_resources=[REFERENCE, {**VIDEO, "location": "javascript:alert(1)"}],
    )
    assert response.status_code == 422, response.text

    assert await _audit(world, "pr.content.resource_added") == []
    assert await _audit(world, "pr.content.created") == []
    assert await _audit(world, "pr.content.version_created") == []


async def test_78d_the_refusal_is_the_resource_validator_s_own(world: World) -> None:
    """Same rules, same ``reason``, same wording - one validator, two callers.

    Asserted by comparing the two refusals rather than by reading the code: the
    claim is that a location refused on the detail page is refused identically in
    the create form, and a second implementation would show up here as two
    different ``details``.
    """
    world.act_as(world.manager)
    existing = await world.content()

    later = world.add(existing.id, location="javascript:alert(1)")
    at_create = _create(world, initial_resources=[{**REFERENCE, "location": "javascript:alert(1)"}])

    assert later.status_code == at_create.status_code == 422
    after = later.json()["error"]["details"]
    during = at_create.json()["error"]["details"]
    # The index is the only difference, and it is additive: everything the
    # existing clients already render is untouched.
    assert during == {**after, "initial_resource_index": 0}


async def test_78e_a_blank_label_names_the_field_and_the_draft(world: World) -> None:
    """A form holding five drafts must be told *which* one it is about."""
    world.act_as(world.manager)
    response = _create(world, initial_resources=[REFERENCE, {**IMAGE, "label": "   "}])

    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["field"] == "label"
    assert details["reason"] == "empty"
    assert details["initial_resource_index"] == 1


async def test_78f_an_unknown_resource_type_is_refused_with_its_index(world: World) -> None:
    """The vocabulary is closed at creation too, and the refusal still locates itself."""
    world.act_as(world.manager)
    response = _create(
        world,
        title="Bài không được tạo",
        initial_resources=[REFERENCE, {**IMAGE, "resource_type": "FINAL_VIDEO"}],
    )

    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["field"] == "resource_type"
    assert details["initial_resource_index"] == 1
    assert await _count(world, PrContentItem, PrContentItem.title == "Bài không được tạo") == 0


async def test_78g_the_service_writes_nothing_when_the_unit_of_work_rolls_back(
    world: World,
) -> None:
    """The failure that happens *after* rows are written, not before.

    A bad location is caught during validation, before a code is allocated - so
    on its own it proves the pre-check rather than the transaction. This drives
    the service directly with valid resources and an unknown channel, which
    fails once the content row and its version have already been flushed. What
    must survive that is nothing.
    """
    services = build_pr_services(world.session, world.settings)
    contents_before = await _count(world, PrContentItem)
    resources_before = await _count(world, PrContentResource)

    with pytest.raises(PrNotFoundError):
        async with world.session.begin_nested():
            await services.content.create_content(
                actor=world.actor(world.manager),
                request_id=uuid.uuid4(),
                command=CreateContentCommand(
                    title="Bài không được tạo",
                    brand_id=world.brand.id,
                    owner_user_id=world.owner.id,
                    targets=(
                        ContentTargetSpec(
                            channel_id=uuid.uuid4(),
                            distribution_mode=PrDistributionMode.ORGANIC,
                        ),
                    ),
                    initial_resources=(
                        ContentResourceSpec(
                            resource_type=PrContentResourceType.REFERENCE,
                            label="Brief khách hàng",
                            location=DRIVE,
                        ),
                    ),
                ),
            )

    assert await _count(world, PrContentItem) == contents_before
    assert await _count(world, PrContentResource) == resources_before
    assert await _audit(world, "pr.content.resource_added") == []


async def test_78h_the_service_refuses_the_bad_draft_before_it_writes_anything(
    world: World,
) -> None:
    """Validation first, so a bad paste burns no ``CNT-…`` number.

    The same ordering the content-type refusal has, and for the same reason.
    """
    services = build_pr_services(world.session, world.settings)

    with pytest.raises(PrValidationError) as refused:
        await services.content.create_content(
            actor=world.actor(world.manager),
            request_id=uuid.uuid4(),
            command=CreateContentCommand(
                title="Bài không được tạo",
                brand_id=world.brand.id,
                owner_user_id=world.owner.id,
                initial_resources=(
                    ContentResourceSpec(
                        resource_type=PrContentResourceType.REFERENCE,
                        label="Brief",
                        location="javascript:alert(1)",
                    ),
                ),
            ),
        )

    assert refused.value.details["initial_resource_index"] == 0
    assert await _count(world, PrContentItem, PrContentItem.title == "Bài không được tạo") == 0


# =============================================================================
# 79. Audited, and audited the same way
# =============================================================================


async def test_79a_each_initial_resource_writes_resource_added(world: World) -> None:
    """One event per resource, and the **existing** action name.

    A ``pr.content.initial_resource_added`` vocabulary would have meant every
    reader of the trail needed to know two actions to answer one question: what
    material was attached to this piece, and by whom.
    """
    world.act_as(world.manager)
    response = _create(world, initial_resources=[REFERENCE, IMAGE, VIDEO])
    assert response.status_code == 201, response.text

    events = await _audit(world, "pr.content.resource_added")
    assert len(events) == 3
    code = response.json()["content"]["code"]
    for event in events:
        assert event.entity_type == "pr_content_resource"
        assert event.actor_user_id == world.manager.id
        assert event.after_data["content_code"] == code  # type: ignore[index]
    assert {event.after_data["label"] for event in events} == {  # type: ignore[index]
        "Brief khách hàng",
        "Ảnh packshot sản phẩm",
        "Video tham khảo",
    }


async def test_79b_the_payload_is_the_one_a_later_resource_gets(world: World) -> None:
    """Identical shape, so the trail reads the same however the brief arrived."""
    world.act_as(world.manager)
    created = _create(world, initial_resources=[REFERENCE])
    assert created.status_code == 201, created.text
    content_id = uuid.UUID(created.json()["content"]["id"])
    world.add(content_id, label="Brief khách hàng")

    at_create, later = await _audit(world, "pr.content.resource_added")
    assert at_create.after_data is not None and later.after_data is not None
    assert at_create.after_data.keys() == later.after_data.keys()
    assert at_create.after_data["label"] == later.after_data["label"]
    assert at_create.after_data["content_code"] == later.after_data["content_code"]


async def test_79c_the_audit_still_never_copies_the_note(world: World) -> None:
    world.act_as(world.manager)
    _create(world, initial_resources=[{**IMAGE, "note": "Một ghi chú rất dài về cách dùng"}])

    payload = str((await _audit(world, "pr.content.resource_added"))[0].after_data)
    assert "rất dài" not in payload


async def test_79d_attaching_material_notifies_nobody(world: World) -> None:
    """Audit-only, like every other resource mutation.

    Creating a piece with four references would otherwise produce four inbox
    rows during a briefing session, which is how a notification centre stops
    being read.
    """
    world.act_as(world.manager)
    assert _create(world, initial_resources=[REFERENCE, IMAGE, VIDEO]).status_code == 201
    assert await _count(world, UserNotification) == 0


# =============================================================================
# 80. Authorization: the create permission, and nothing new
# =============================================================================


async def test_80a_no_second_permission_is_required_for_initial_resources(
    world: World,
) -> None:
    """The decisive case, and the reason this is not simply "reuse add_resource".

    ``stranger`` may create content and is **not** responsible for the item they
    are creating - the owner is somebody else, and creation is deliberately not a
    responsibility relationship. Running the existing resource-edit check against
    the item would refuse them, which would mean a person allowed to create a
    piece of content was not allowed to create it with the brief attached.

    The resources are part of the aggregate being created, so the create
    permission is the whole question.
    """
    world.act_as(world.stranger)
    response = _create(world, initial_resources=[REFERENCE, IMAGE])

    assert response.status_code == 201, response.text
    assert len(_resources_of(world, response)) == 2


async def test_80b_and_the_after_the_fact_rule_is_unchanged(world: World) -> None:
    """Same person, same item, one minute later: refused.

    Step 1F.2.3e's rule is that changing an existing item's material needs
    responsibility for it. Creating with resources does not weaken that - the
    stranger who just created this piece for somebody else may not attach a
    second brief to it afterwards.
    """
    world.act_as(world.stranger)
    created = _create(world, initial_resources=[REFERENCE])
    assert created.status_code == 201, created.text
    content_id = uuid.UUID(created.json()["content"]["id"])

    refused = world.add(content_id, label="Brief thứ hai")
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["details"]["reason"] == "not_responsible"
    # And nothing was attached by the attempt.
    assert len(world.resources(content_id)) == 1


# =============================================================================
# 81. What had to keep working
# =============================================================================


async def test_81a_a_content_type_is_still_required(world: World) -> None:
    """Carrying references does not excuse the item from having a format."""
    world.act_as(world.manager)
    response = _create(
        world, title="Bài không loại", content_type=None, initial_resources=[REFERENCE]
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"field": "content_type", "reason": "required"}
    # Refused before anything was written, resources included.
    assert await _count(world, PrContentResource) == 0


async def test_81b_resources_can_still_be_added_edited_and_deleted_afterwards(
    world: World,
) -> None:
    """Step 1F.2.3e's CRUD, on an item created with material already attached."""
    world.act_as(world.manager)
    created = _create(world, initial_resources=[REFERENCE])
    assert created.status_code == 201, created.text
    content_id = uuid.UUID(created.json()["content"]["id"])

    added = world.add(content_id, label="Brief thứ hai")
    assert added.status_code == 201, added.text
    assert len(world.resources(content_id)) == 2

    initial_id = next(
        row["id"] for row in world.resources(content_id) if row["label"] == "Brief khách hàng"
    )
    updated = world.client.patch(
        f"/api/pr/contents/{content_id}/resources/{initial_id}",
        json={"label": "Brief khách hàng (bản 2)"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["label"] == "Brief khách hàng (bản 2)"

    deleted = world.client.delete(f"/api/pr/contents/{content_id}/resources/{initial_id}")
    assert deleted.status_code == 204, deleted.text
    assert [row["label"] for row in world.resources(content_id)] == ["Brief thứ hai"]


# =============================================================================
# 82. And the aggregate delete still takes them
# =============================================================================


async def test_82a_permanent_delete_removes_initial_resources_too(world: World) -> None:
    """A resource created this way is an ordinary row, including when it is destroyed.

    ``pr_content_resources.content_id`` is ``RESTRICT``, so an aggregate delete
    that did not know about a resource would fail on PostgreSQL rather than
    orphan it - which is why this is worth asserting for a row that arrived by a
    new route.
    """
    world.act_as(world.manager)
    created = _create(world, initial_resources=[REFERENCE, IMAGE])
    assert created.status_code == 201, created.text
    content_id = uuid.UUID(created.json()["content"]["id"])
    assert await _count(world, PrContentResource, PrContentResource.content_id == content_id) == 2

    services = build_pr_services(world.session, world.settings)
    await services.lifecycle.delete_content(
        actor=world.actor(world.manager), request_id=uuid.uuid4(), content_id=content_id
    )

    assert await _count(world, PrContentResource, PrContentResource.content_id == content_id) == 0
    assert await _count(world, PrContentItem, PrContentItem.id == content_id) == 0
