"""Evidence as one free text. **A UX simplification, not a schema change.**

The member types one thing - a description, a link, several lines, any mix -
and the row underneath is the same ``pr_work_evidence`` row it always was: the
text lives in ``note``, and the two columns the table still requires are
derived from it. Nothing about who may add evidence, what counts as evidence
for a work type that requires it, or how a legacy row reads has moved.

Numbered against the task's own list:

* 25: free-text persistence - the text, the derived label, the derived
  location (first link, else the sentinel) and the API shape;
* 26: a legacy label-and-link row still reads as one, beside a text row;
* 27: authorization is the existing rule - a colleague who is not on the job
  is refused, a manager is not;
* 28: adding text rows rewrites no legacy row;
* 29: a text row satisfies ``requires_evidence`` exactly as a link did, and
  nothing satisfies it before one exists;
* 30: the request schema accepts one shape or the other, never both or
  neither.

Nothing here contacts a network.
"""

from __future__ import annotations

# ruff: noqa: F811 - `world` is a fixture imported from the production suite
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from meobot.api.schemas.pr_work import AddEvidenceRequest, WorkEvidenceResponse
from meobot.db.models.pr_work import PrWorkEvidence
from meobot.domain.pr.errors import PrPermissionDeniedError, PrValidationError
from meobot.domain.pr.work import (
    EVIDENCE_TEXT_ONLY_LOCATION,
    PrWorkStatus,
    evidence_label_for,
    evidence_location_for,
    find_urls,
)
from tests.unit.test_pr_production_lifecycle import World, world  # noqa: F401
from tests.unit.test_pr_work_core import assigned, work_type

TEXT = (
    "Đã liên hệ 3 khách từ group cộng đồng.\n"
    "Danh sách: https://docs.google.com/spreadsheets/d/abc/edit.\n"
    "Khách thứ 2 phản hồi qua Zalo."
)


async def evidence_rows(world: World, item_id) -> list[PrWorkEvidence]:  # type: ignore[no-untyped-def]
    return list(
        (
            await world.session.execute(
                select(PrWorkEvidence)
                .where(PrWorkEvidence.work_item_id == item_id)
                .order_by(PrWorkEvidence.created_at)
            )
        ).scalars()
    )


# ===========================================================================
# The pure rules
# ===========================================================================


def test_urls_are_found_without_their_trailing_punctuation() -> None:
    assert find_urls(TEXT) == ["https://docs.google.com/spreadsheets/d/abc/edit"]
    assert find_urls("xem (https://a.b/c), rồi http://x.y/z?q=1!") == [
        "https://a.b/c",
        "http://x.y/z?q=1",
    ]
    assert find_urls("không có link nào, ftp://cũng không") == []


def test_the_label_is_the_first_line_and_the_location_the_first_link() -> None:
    assert evidence_label_for(TEXT) == "Đã liên hệ 3 khách từ group cộng đồng."
    assert evidence_label_for("\n\n  chỉ một dòng  ") == "chỉ một dòng"
    assert len(evidence_label_for("x" * 500)) == 200
    assert evidence_location_for(TEXT) == "https://docs.google.com/spreadsheets/d/abc/edit"
    assert evidence_location_for("chỉ chữ, không link") == EVIDENCE_TEXT_ONLY_LOCATION


# ===========================================================================
# 25-26: PERSISTENCE AND THE API SHAPE
# ===========================================================================


@pytest.mark.asyncio
async def test_25_free_text_evidence_is_stored_whole_and_reads_back_as_text(
    world: World,
) -> None:
    item = await assigned(world)
    row = await world.services.work.add_evidence_text(
        actor=world.actor(world.member),
        request_id=world.request_id,
        work_item_id=item.id,
        text=TEXT,
    )
    assert row.note == TEXT, "the text is stored whole, line breaks and all"
    assert row.label == "Đã liên hệ 3 khách từ group cộng đồng."
    assert row.location == "https://docs.google.com/spreadsheets/d/abc/edit"

    shape = WorkEvidenceResponse.from_row(row)
    assert shape.text == TEXT
    assert shape.location == "https://docs.google.com/spreadsheets/d/abc/edit"

    plain = await world.services.work.add_evidence_text(
        actor=world.actor(world.member),
        request_id=world.request_id,
        work_item_id=item.id,
        text="Gọi điện xác nhận với khách, không có link.",
    )
    assert plain.location == EVIDENCE_TEXT_ONLY_LOCATION, "no URL is required"
    plain_shape = WorkEvidenceResponse.from_row(plain)
    assert plain_shape.location is None, "the sentinel never leaves the server"
    assert plain_shape.text == "Gọi điện xác nhận với khách, không có link."
    assert plain_shape.label == "Gọi điện xác nhận với khách, không có link."


@pytest.mark.asyncio
async def test_26_a_legacy_row_still_reads_as_a_label_and_a_link(world: World) -> None:
    item = await assigned(world)
    legacy = await world.services.work.add_evidence(
        actor=world.actor(world.member),
        request_id=world.request_id,
        work_item_id=item.id,
        label="Bản dựng",
        location="https://drive.google.com/file/d/x/view",
    )
    shape = WorkEvidenceResponse.from_row(legacy)
    assert shape.label == "Bản dựng"
    assert shape.location == "https://drive.google.com/file/d/x/view"
    assert shape.text is None, "a legacy row is not dressed up as a text"


# ===========================================================================
# 27-28: AUTHORIZATION AND NO REWRITE
# ===========================================================================


@pytest.mark.asyncio
async def test_27_the_textarea_grants_nobody_anything(world: World) -> None:
    """Same rule as the link: a contributor or a manager, and nobody else."""
    item = await assigned(world)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work.add_evidence_text(
            actor=world.actor(world.other),
            request_id=world.request_id,
            work_item_id=item.id,
            text="Tôi không làm việc này",
        )
    assert await evidence_rows(world, item.id) == []

    by_manager = await world.services.work.add_evidence_text(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        work_item_id=item.id,
        text="Kiểm tra tại chỗ",
    )
    assert by_manager.added_by_user_id == world.lead.id

    with pytest.raises(PrValidationError) as caught:
        await world.services.work.add_evidence_text(
            actor=world.actor(world.member),
            request_id=world.request_id,
            work_item_id=item.id,
            text="   \n  ",
        )
    assert caught.value.details["reason"] == "empty"


@pytest.mark.asyncio
async def test_28_adding_text_rows_rewrites_no_legacy_row(world: World) -> None:
    item = await assigned(world)
    legacy = await world.services.work.add_evidence(
        actor=world.actor(world.member),
        request_id=world.request_id,
        work_item_id=item.id,
        label="Bản dựng",
        location="https://drive.google.com/file/d/x/view",
        note="Bản cuối",
    )
    before = (legacy.id, legacy.label, legacy.location, legacy.note)
    await world.services.work.add_evidence_text(
        actor=world.actor(world.member),
        request_id=world.request_id,
        work_item_id=item.id,
        text=TEXT,
    )
    rows = await evidence_rows(world, item.id)
    assert len(rows) == 2
    kept = next(one for one in rows if one.id == legacy.id)
    assert (kept.id, kept.label, kept.location, kept.note) == before


# ===========================================================================
# 29: THE REQUIREMENT IS THE WORK TYPE'S, AND A TEXT SATISFIES IT
# ===========================================================================


@pytest.mark.asyncio
async def test_29_a_text_row_satisfies_requires_evidence_and_nothing_less_does(
    world: World,
) -> None:
    row = await work_type(world, code="EDIT_VIDEO", name="Dựng video", requires_evidence=True)
    item = await assigned(world, type_row=row)
    with pytest.raises(PrValidationError) as caught:
        await world.services.work.complete(
            actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
        )
    assert caught.value.details["reason"] == "evidence_required"

    await world.services.work.add_evidence_text(
        actor=world.actor(world.member),
        request_id=world.request_id,
        work_item_id=item.id,
        text="Đã dựng xong, file trên NAS thư mục /dung/2026-09",
    )
    finished = await world.services.work.complete(
        actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
    )
    assert finished.status is PrWorkStatus.COMPLETED


# ===========================================================================
# 30: THE REQUEST SCHEMA
# ===========================================================================


def test_30_the_request_is_one_shape_or_the_other() -> None:
    assert AddEvidenceRequest(text="chỉ chữ").text == "chỉ chữ"
    legacy = AddEvidenceRequest(label="Bản dựng", location="https://x.y/z")
    assert legacy.text is None and legacy.label == "Bản dựng"
    with pytest.raises(ValidationError):
        AddEvidenceRequest()
    with pytest.raises(ValidationError):
        AddEvidenceRequest(label="Bản dựng")
    with pytest.raises(ValidationError):
        AddEvidenceRequest(text="chữ", label="Bản dựng", location="https://x.y/z")
    with pytest.raises(ValidationError):
        AddEvidenceRequest(text="", label=None, location=None)
