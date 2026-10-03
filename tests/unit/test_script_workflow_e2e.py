"""The milestone-2 workflow, end to end, against real SQL and fake integrations.

Covered here, in the order the Owner experiences it:

sheet -> import -> version -> review -> approve / request revision -> re-import.

These are the rules that would be expensive to get wrong: an unchanged row must
not create anything, a changed row must create a new version and invalidate the
review and the approval that belonged to the old one, and an approval must name
the exact version a human looked at.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.invite_service import InviteService
from meobot.application.script_review_service import ScriptReviewService
from meobot.application.script_service import ScriptService
from meobot.application.script_sync_service import ScriptSyncService
from meobot.application.sheet_profile_service import SheetProfileService
from meobot.application.sheet_writeback_service import SheetWriteBackService
from meobot.core.errors import (
    AuthorizationError,
    ConflictError,
    ValidationError,
    WorkflowStateError,
)
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.script import Script, ScriptVersion
from meobot.db.models.sheet_profile import SheetProfile
from meobot.db.models.user import User
from meobot.domain.identity.invites import generate_code
from meobot.domain.identity.models import Actor, Role
from meobot.domain.scripts.models import ApprovalAction, ReviewVerdict, compute_source_hash
from meobot.domain.scripts.workflow import ScriptStatus
from meobot.domain.sheets.mapping import propose_mapping
from meobot.integrations.google.sheets import FakeSheetsClient
from meobot.integrations.llm.fake import FakeLLMProvider

SPREADSHEET = "spreadsheet-test-1"
TAB = "Kịch bản"
HEADERS = ["ID", "Tiêu đề", "Hook", "Nội dung", "Ghi chú", "Người viết", "Deadline", "Trạng thái"]

ROW_1 = [
    "KB-001",
    "5 dấu hiệu thiếu ngủ",
    "Bạn ngủ 8 tiếng vẫn mệt?",
    "Cảnh 1: bác sĩ ngồi trước camera. " + "Nội dung chi tiết. " * 30,
    "Quay tại phòng khám",
    "Ngọc",
    "30/07/2026",
    "Chờ duyệt",
]
ROW_2 = [
    "KB-002",
    "Uống đủ nước mỗi ngày",
    "",
    "Cảnh 1: ly nước. " + "Thân bài. " * 25,
    "",
    "Minh",
    "01/08/2026",
    "Nháp",
]


# --- Fixtures ---------------------------------------------------------------
@pytest.fixture
def sheets() -> FakeSheetsClient:
    client = FakeSheetsClient()
    client.load(SPREADSHEET, TAB, list(HEADERS), [list(ROW_1), list(ROW_2)])
    return client


@pytest.fixture
def owner() -> Actor:
    return Actor(
        user_id=None,
        telegram_user_id=777000111,
        telegram_username="owner",
        full_name="Owner",
        role=Role.OWNER,
        active=True,
        is_bootstrap_owner=True,
    )


@pytest.fixture
def employee() -> Actor:
    return Actor(
        user_id=uuid.uuid4(),
        telegram_user_id=42,
        full_name="Nhân viên",
        role=Role.EMPLOYEE,
        active=True,
    )


async def make_profile(
    session: AsyncSession,
    owner: Actor,
    *,
    write_back: dict[str, str] | None = None,
) -> SheetProfile:
    """Create a profile from the deterministic mapping proposal."""
    service = SheetProfileService(session, AuditService(session))
    return await service.create_profile(
        actor=owner,
        request_id=uuid.uuid4(),
        name="TikTok tháng 7",
        spreadsheet_id=SPREADSHEET,
        sheet_name=TAB,
        field_mapping=dict(propose_mapping(HEADERS).mapping),
        headers=list(HEADERS),
        write_back_mapping=write_back,
    )


async def run_sync(
    session: AsyncSession,
    sheets: FakeSheetsClient,
    owner: Actor,
    profile: SheetProfile,
) -> Any:
    audit = AuditService(session)
    profiles = SheetProfileService(session, audit)
    return await ScriptSyncService(session, audit, sheets, profiles).sync_profile(
        actor=owner, request_id=uuid.uuid4(), profile=profile
    )


async def review(session: AsyncSession, owner: Actor, script_id: uuid.UUID) -> Any:
    audit = AuditService(session)
    return await ScriptReviewService(session, audit, FakeLLMProvider()).review_script(
        actor=owner, request_id=uuid.uuid4(), script_id=script_id
    )


# --- Import & idempotency ---------------------------------------------------
async def test_first_sync_imports_every_row(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    profile = await make_profile(session, owner)
    report = await run_sync(session, sheets, owner, profile)

    assert report.created == 2
    assert report.new_versions == 0
    scripts = (await session.execute(select(Script))).scalars().all()
    assert {script.external_script_id for script in scripts} == {"KB-001", "KB-002"}
    assert all(script.status is ScriptStatus.IMPORTED for script in scripts)
    assert all(script.current_version_id is not None for script in scripts)


async def test_resyncing_an_unchanged_sheet_creates_nothing(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    """The idempotency guarantee: same rows in, no new rows out."""
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)
    second = await run_sync(session, sheets, owner, profile)

    assert second.created == 0
    assert second.new_versions == 0
    assert second.unchanged == 2
    assert await _count(session, Script) == 2
    assert await _count(session, ScriptVersion) == 2


async def test_a_cosmetic_edit_does_not_create_a_version(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    """Re-wrapped whitespace is not a content change."""
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)

    padded = list(ROW_1)
    padded[3] = f"  {padded[3].replace('. ', '.  ')}  "
    sheets.load(SPREADSHEET, TAB, list(HEADERS), [padded, list(ROW_2)])
    report = await run_sync(session, sheets, owner, profile)

    assert report.new_versions == 0
    assert await _count(session, ScriptVersion) == 2


async def test_changed_content_creates_a_new_version_and_keeps_the_old(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)

    edited = list(ROW_1)
    edited[3] = "Cảnh 1 hoàn toàn mới. " * 20
    sheets.load(SPREADSHEET, TAB, list(HEADERS), [edited, list(ROW_2)])
    report = await run_sync(session, sheets, owner, profile)

    assert report.new_versions == 1
    script = await _script(session, "KB-001")
    versions = (
        (await session.execute(select(ScriptVersion).where(ScriptVersion.script_id == script.id)))
        .scalars()
        .all()
    )
    numbers = sorted(version.version_number for version in versions)
    assert numbers == [1, 2]
    assert script.current_version_id == max(versions, key=lambda v: v.version_number).id
    assert script.status is ScriptStatus.SUBMITTED_FOR_REVIEW


async def test_metadata_only_changes_do_not_create_a_version(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    """A new deadline is not a reason to re-review a script."""
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)

    edited = list(ROW_1)
    edited[6] = "15/08/2026"
    sheets.load(SPREADSHEET, TAB, list(HEADERS), [edited, list(ROW_2)])
    report = await run_sync(session, sheets, owner, profile)

    assert report.new_versions == 0
    script = await _script(session, "KB-001")
    assert script.deadline is not None
    assert script.deadline.isoformat() == "2026-08-15"


async def test_duplicate_external_ids_are_reported_not_merged(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    duplicate = list(ROW_2)
    duplicate[0] = "KB-001"
    sheets.load(SPREADSHEET, TAB, list(HEADERS), [list(ROW_1), duplicate])

    profile = await make_profile(session, owner)
    report = await run_sync(session, sheets, owner, profile)

    assert report.created == 1
    assert report.duplicates == ["KB-001"]
    # The first row won; the duplicate did not overwrite its content.
    script = await _script(session, "KB-001")
    assert script.current_version is not None
    assert script.current_version.title == ROW_1[1]


async def test_rows_without_an_id_get_a_stable_row_identity(
    session: AsyncSession, owner: Actor
) -> None:
    """A sheet with no id column is still importable, and still idempotent."""
    headers = ["Tiêu đề", "Nội dung"]
    sheets = FakeSheetsClient()
    sheets.load(SPREADSHEET, TAB, headers, [["Tiêu đề A", "Thân bài A " * 20]])

    profiles = SheetProfileService(session, AuditService(session))
    profile = await profiles.create_profile(
        actor=owner,
        request_id=uuid.uuid4(),
        name="Sheet không có ID",
        spreadsheet_id=SPREADSHEET,
        sheet_name=TAB,
        field_mapping={"title": "Tiêu đề", "script_body": "Nội dung"},
        headers=headers,
    )

    first = await run_sync(session, sheets, owner, profile)
    second = await run_sync(session, sheets, owner, profile)

    assert first.created == 1
    assert second.created == 0
    assert second.unchanged == 1
    script = (await session.execute(select(Script))).scalars().one()
    assert script.external_script_id == f"row:{TAB}:2"


async def test_blank_rows_are_skipped_silently(session: AsyncSession, owner: Actor) -> None:
    sheets = FakeSheetsClient()
    sheets.load(SPREADSHEET, TAB, list(HEADERS), [list(ROW_1), ["", "", "", "", "", "", "", ""]])
    profile = await make_profile(session, owner)

    report = await run_sync(session, sheets, owner, profile)

    assert report.created == 1
    assert report.row_errors == []


async def test_a_removed_column_stops_the_import_instead_of_corrupting_it(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)

    # The team renamed the body column.
    renamed = [header if header != "Nội dung" else "Nội dung kịch bản" for header in HEADERS]
    sheets.load(SPREADSHEET, TAB, renamed, [list(ROW_1)])
    report = await run_sync(session, sheets, owner, profile)

    assert report.needs_remap is True
    assert report.created == 0
    await session.refresh(profile)
    assert profile.state.value == "schema_changed"
    # The old mapping is preserved so a human can compare, not discarded.
    assert profile.field_mapping["script_body"] == ["Nội dung"]


# --- Review binding ---------------------------------------------------------
async def test_review_is_stored_against_the_exact_version(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)
    script = await _script(session, "KB-001")

    stored = await review(session, owner, script.id)

    assert stored.script_version_id == script.current_version_id
    assert stored.provider == "fake"
    assert stored.model == "fake-deterministic-1"
    assert 0 <= stored.overall_score <= 100
    assert stored.rubric_snapshot["criteria"]
    await session.refresh(script)
    assert script.status is ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL


async def test_a_new_version_makes_the_old_review_stale(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)
    script = await _script(session, "KB-001")
    await review(session, owner, script.id)

    edited = list(ROW_1)
    edited[3] = "Bản viết lại hoàn toàn. " * 20
    sheets.load(SPREADSHEET, TAB, list(HEADERS), [edited, list(ROW_2)])
    await run_sync(session, sheets, owner, profile)

    detail = await ScriptService(session, AuditService(session)).detail(script.id)
    assert detail.review is not None
    assert detail.review_matches_version is False


async def test_reviewing_twice_keeps_both_reviews(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    """History is append-only: an old review is never mutated."""
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)
    script = await _script(session, "KB-001")

    first = await review(session, owner, script.id)
    second = await review(session, owner, script.id)

    assert first.id != second.id
    reviews = await ScriptReviewService(session, AuditService(session)).list_reviews(script.id)
    assert len(reviews) == 2


async def test_the_fake_reviewer_flags_a_missing_hook(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    """KB-002 has no hook, so the offline reviewer must not wave it through."""
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)
    script = await _script(session, "KB-002")

    stored = await review(session, owner, script.id)

    assert stored.verdict is not ReviewVerdict.APPROVE
    assert any("hook" in issue.lower() for issue in stored.critical_issues)
    assert stored.revised_hook_suggestion


# --- Approval ---------------------------------------------------------------
async def test_owner_approves_the_current_version_for_production(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)
    script = await _script(session, "KB-001")
    await review(session, owner, script.id)

    approval = await ScriptService(session, AuditService(session)).approve_for_production(
        actor=owner,
        request_id=uuid.uuid4(),
        script_id=script.id,
        expected_version_id=script.current_version_id,
    )

    assert approval.action is ApprovalAction.APPROVE_FOR_PRODUCTION
    assert approval.script_version_id == script.current_version_id
    assert approval.status_after is ScriptStatus.APPROVED_FOR_PRODUCTION
    assert approval.actor_telegram_id == owner.telegram_user_id
    await session.refresh(script)
    assert script.status is ScriptStatus.APPROVED_FOR_PRODUCTION


async def test_approval_never_grants_publishing(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    """ADR-002 in a test: production approval creates no publish permission.

    Nothing in the approval path may produce a video, a publish job, or an
    ``approved_for_publish`` state - the video workflow is a separate decision
    on a separate entity, and it does not exist yet.
    """
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)
    script = await _script(session, "KB-001")
    await review(session, owner, script.id)

    approval = await ScriptService(session, AuditService(session)).approve_for_production(
        actor=owner, request_id=uuid.uuid4(), script_id=script.id
    )

    assert {action.value for action in ApprovalAction} == {
        "approve_for_production",
        "request_revision",
    }
    assert approval.status_after.value != "approved_for_publish"
    assert approval.status_after is ScriptStatus.APPROVED_FOR_PRODUCTION

    audit = await _audit_action(session, "script.approved_for_production")
    assert audit is not None
    assert audit.after_data is not None
    assert audit.after_data["grants_publishing"] is False


async def test_an_employee_may_not_approve(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor, employee: Actor
) -> None:
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)
    script = await _script(session, "KB-001")
    await review(session, owner, script.id)

    with pytest.raises(AuthorizationError, match=r"script\.approve"):
        await ScriptService(session, AuditService(session)).approve_for_production(
            actor=employee, request_id=uuid.uuid4(), script_id=script.id
        )
    await session.refresh(script)
    assert script.status is ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL


async def test_approving_a_stale_version_is_refused(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    """The sheet changed between the button being drawn and being pressed."""
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)
    script = await _script(session, "KB-001")
    await review(session, owner, script.id)
    stale_version_id = script.current_version_id

    edited = list(ROW_1)
    edited[3] = "Nội dung đã sửa sau khi gửi duyệt. " * 20
    sheets.load(SPREADSHEET, TAB, list(HEADERS), [edited, list(ROW_2)])
    await run_sync(session, sheets, owner, profile)

    with pytest.raises(ConflictError, match="đã thay đổi"):
        await ScriptService(session, AuditService(session)).approve_for_production(
            actor=owner,
            request_id=uuid.uuid4(),
            script_id=script.id,
            expected_version_id=stale_version_id,
        )


async def test_an_unreviewed_version_cannot_be_approved(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)
    script = await _script(session, "KB-001")
    await review(session, owner, script.id)

    edited = list(ROW_1)
    edited[3] = "Nội dung mới chưa được chấm. " * 20
    sheets.load(SPREADSHEET, TAB, list(HEADERS), [edited, list(ROW_2)])
    await run_sync(session, sheets, owner, profile)

    with pytest.raises(WorkflowStateError):
        await ScriptService(session, AuditService(session)).approve_for_production(
            actor=owner, request_id=uuid.uuid4(), script_id=script.id
        )


async def test_editing_an_approved_script_invalidates_the_approval(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    """The old approval stays as history; the new version starts unapproved."""
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)
    script = await _script(session, "KB-001")
    await review(session, owner, script.id)
    approval = await ScriptService(session, AuditService(session)).approve_for_production(
        actor=owner, request_id=uuid.uuid4(), script_id=script.id
    )
    approved_version_id = approval.script_version_id

    edited = list(ROW_1)
    edited[3] = "Nội dung sửa sau khi đã duyệt. " * 20
    sheets.load(SPREADSHEET, TAB, list(HEADERS), [edited, list(ROW_2)])
    report = await run_sync(session, sheets, owner, profile)

    await session.refresh(script)
    assert script.id in report.invalidated_approvals
    assert script.status is ScriptStatus.SUBMITTED_FOR_REVIEW
    assert script.current_version_id != approved_version_id

    # The historical approval is untouched and still names the old version.
    stored = await ScriptService(session, AuditService(session)).list_approvals(script.id)
    assert len(stored) == 1
    assert stored[0].script_version_id == approved_version_id
    assert await _audit_action(session, "script.approval_invalidated") is not None


async def test_request_revision_records_the_comment(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)
    script = await _script(session, "KB-001")
    await review(session, owner, script.id)

    record = await ScriptService(session, AuditService(session)).request_revision(
        actor=owner,
        request_id=uuid.uuid4(),
        script_id=script.id,
        comment="Hook chưa đủ mạnh, viết lại 3 giây đầu.",
    )

    assert record.action is ApprovalAction.REQUEST_REVISION
    assert record.status_after is ScriptStatus.REVISION_REQUIRED
    assert record.comment is not None
    await session.refresh(script)
    assert script.status is ScriptStatus.REVISION_REQUIRED


# --- Write-back -------------------------------------------------------------
async def test_write_back_touches_only_the_mapped_columns(
    session: AsyncSession, owner: Actor
) -> None:
    headers = [*HEADERS, "MeoBot Status", "Điểm AI", "Nhận xét AI"]
    sheets = FakeSheetsClient()
    sheets.load(SPREADSHEET, TAB, headers, [[*ROW_1, "", "", ""]])

    profiles = SheetProfileService(session, AuditService(session))
    profile = await profiles.create_profile(
        actor=owner,
        request_id=uuid.uuid4(),
        name="Có cột ghi ngược",
        spreadsheet_id=SPREADSHEET,
        sheet_name=TAB,
        field_mapping=dict(propose_mapping(headers).mapping),
        headers=headers,
        write_back_mapping={
            "meobot_status": "MeoBot Status",
            "review_score": "Điểm AI",
            "review_summary": "Nhận xét AI",
        },
    )
    await run_sync(session, sheets, owner, profile)
    script = await _script(session, "KB-001")
    stored = await review(session, owner, script.id)

    audit = AuditService(session)
    written = await SheetWriteBackService(session, audit, sheets).push(
        actor=owner,
        request_id=uuid.uuid4(),
        profile=profile,
        script=script,
        version=script.current_version,  # type: ignore[arg-type]
        review=stored,
    )

    assert written == 3
    columns = {update.column_index for update in sheets.writes}
    # Columns I, J, K - the eight source columns are never in the write set.
    assert columns == {8, 9, 10}
    assert all(update.row_number == 2 for update in sheets.writes)
    assert str(stored.overall_score) in {update.value for update in sheets.writes}


async def test_write_back_is_a_no_op_when_nothing_is_configured(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    """The whole workflow must run against a read-only sheet."""
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)
    script = await _script(session, "KB-001")

    written = await SheetWriteBackService(session, AuditService(session), sheets).push(
        actor=owner,
        request_id=uuid.uuid4(),
        profile=profile,
        script=script,
        version=script.current_version,  # type: ignore[arg-type]
    )

    assert written == 0
    assert sheets.writes == []


# --- Audit ------------------------------------------------------------------
async def test_every_state_change_leaves_an_audit_trail(
    session: AsyncSession, sheets: FakeSheetsClient, owner: Actor
) -> None:
    profile = await make_profile(session, owner)
    await run_sync(session, sheets, owner, profile)
    script = await _script(session, "KB-001")
    await review(session, owner, script.id)
    await ScriptService(session, AuditService(session)).approve_for_production(
        actor=owner, request_id=uuid.uuid4(), script_id=script.id
    )

    actions = {row.action for row in (await session.execute(select(AuditLog))).scalars().all()}
    assert {
        "sheet_profile.created",
        "script.imported",
        "sheet.synced",
        "script.reviewed",
        "script.approved_for_production",
    } <= actions


# --- Invites ----------------------------------------------------------------
async def test_an_employee_registers_with_a_valid_code(session: AsyncSession, owner: Actor) -> None:
    service = InviteService(session, AuditService(session))
    invite, code = await service.create(actor=owner, request_id=uuid.uuid4(), role=Role.EMPLOYEE)

    user = await service.redeem(
        request_id=uuid.uuid4(),
        code=code,
        telegram_user_id=555001,
        telegram_username="nhanvien",
        full_name="Nhân Viên Mới",
    )

    assert user.role is Role.EMPLOYEE
    assert user.telegram_user_id == 555001
    await session.refresh(invite)
    assert invite.use_count == 1
    assert invite.active is False  # single-use code is spent
    assert await _audit_action(session, "invite.redeemed") is not None


async def test_a_code_cannot_be_used_twice(session: AsyncSession, owner: Actor) -> None:
    service = InviteService(session, AuditService(session))
    _, code = await service.create(actor=owner, request_id=uuid.uuid4(), role=Role.EMPLOYEE)
    await service.redeem(request_id=uuid.uuid4(), code=code, telegram_user_id=555002)

    with pytest.raises(ValidationError):
        await service.redeem(request_id=uuid.uuid4(), code=code, telegram_user_id=555003)

    assert await _count(session, User) == 1


async def test_an_unknown_code_is_refused_and_audited(session: AsyncSession, owner: Actor) -> None:
    service = InviteService(session, AuditService(session))
    with pytest.raises(ValidationError, match="không hợp lệ"):
        await service.redeem(request_id=uuid.uuid4(), code=generate_code(), telegram_user_id=555004)
    assert await _audit_action(session, "invite.rejected") is not None
    assert await _count(session, User) == 0


async def test_an_employee_cannot_mint_invites(session: AsyncSession, employee: Actor) -> None:
    service = InviteService(session, AuditService(session))
    with pytest.raises(AuthorizationError):
        await service.create(actor=employee, request_id=uuid.uuid4(), role=Role.EMPLOYEE)


async def test_a_multi_use_code_survives_the_first_redemption(
    session: AsyncSession, owner: Actor
) -> None:
    service = InviteService(session, AuditService(session))
    invite, code = await service.create(
        actor=owner, request_id=uuid.uuid4(), role=Role.EMPLOYEE, max_uses=3
    )

    await service.redeem(request_id=uuid.uuid4(), code=code, telegram_user_id=555005)
    await service.redeem(request_id=uuid.uuid4(), code=code, telegram_user_id=555006)

    await session.refresh(invite)
    assert invite.use_count == 2
    assert invite.active is True


async def test_a_disabled_code_is_refused(session: AsyncSession, owner: Actor) -> None:
    service = InviteService(session, AuditService(session))
    invite, code = await service.create(actor=owner, request_id=uuid.uuid4())
    await service.disable(actor=owner, request_id=uuid.uuid4(), invite_id=invite.id)

    with pytest.raises(ValidationError, match="vô hiệu hoá"):
        await service.redeem(request_id=uuid.uuid4(), code=code, telegram_user_id=555007)


# --- Content hashing --------------------------------------------------------
def test_source_hash_ignores_whitespace_but_not_wording() -> None:
    base = compute_source_hash(
        title="Tiêu đề", hook="Hook", script_body="Thân  bài", production_notes=None
    )
    assert base == compute_source_hash(
        title=" Tiêu đề ", hook="Hook", script_body="Thân bài", production_notes=None
    )
    assert base != compute_source_hash(
        title="Tiêu đề", hook="Hook khác", script_body="Thân bài", production_notes=None
    )


# --- Helpers ----------------------------------------------------------------
async def _script(session: AsyncSession, external_id: str) -> Script:
    result = await session.execute(select(Script).where(Script.external_script_id == external_id))
    return result.scalars().one()


async def _count(session: AsyncSession, model: type[Any]) -> int:
    result = await session.execute(select(func.count()).select_from(model))
    return int(result.scalar_one())


async def _audit_action(session: AsyncSession, action: str) -> AuditLog | None:
    result = await session.execute(select(AuditLog).where(AuditLog.action == action))
    return result.scalars().first()
