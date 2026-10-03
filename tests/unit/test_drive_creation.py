"""Folder allow-listing, template creation, idempotency and permissions.

The two properties worth the most here:

**No duplicate files.** Drive has no idempotent create, so a retried Celery
task, a double-tapped inline button, or a worker that died after Google said
yes must all converge on one file. Several tests drive exactly those three
sequences.

**The right sheet becomes the right thing.** A script sheet registers a Sheet
Profile with the template's own mapping; a work sheet never does, because it
has no ``script_body`` column and importing it would produce nonsense.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.drive_folder_service import DriveFolderService
from meobot.application.sheet_template_service import SheetTemplateService
from meobot.application.spreadsheet_creation_service import SpreadsheetCreationService
from meobot.core.config import Settings
from meobot.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.drive import CreatedSpreadsheet
from meobot.db.models.sheet_profile import SheetProfile
from meobot.domain.drive.models import (
    CreationMethod,
    CreationStatus,
    FolderValidationStatus,
    SpreadsheetRequest,
    TemplateKind,
    extract_folder_id,
)
from meobot.domain.drive.templates import (
    SCRIPT_TEMPLATE,
    WORK_TASK_HEADERS,
    template_for_kind,
)
from meobot.domain.identity.models import Actor
from meobot.domain.sheets.models import CANONICAL_FIELDS, WRITE_BACK_FIELDS
from meobot.integrations.google.drive import FOLDER_MIME, DriveFile, FakeDriveClient
from meobot.integrations.google.sheets import FakeSheetsClient

ROOT_ID = "root-folder"
FOLDER_ID = "folder-tiktok"


@pytest.fixture
def drive() -> FakeDriveClient:
    """A Drive with a root folder and one registered destination inside it."""
    client = FakeDriveClient()
    client.add_folder(ROOT_ID, "MeoBot", drive_id="shared-1")
    client.add_folder(FOLDER_ID, "Kịch bản TikTok", parents=(ROOT_ID,), drive_id="shared-1")
    return client


@pytest.fixture
def sheets() -> FakeSheetsClient:
    return FakeSheetsClient()


@pytest.fixture
def rooted_settings() -> Settings:
    """Settings that confine MeoBot to one Drive subtree."""
    return Settings(google_drive_root_folder_id=ROOT_ID, google_shared_drive_id="shared-1")


def _folders(
    session: AsyncSession, drive: FakeDriveClient, settings: Settings
) -> DriveFolderService:
    return DriveFolderService(session, AuditService(session), drive, settings)


def _creation(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    settings: Settings,
) -> SpreadsheetCreationService:
    return SpreadsheetCreationService(session, AuditService(session), drive, sheets, settings)


async def _register_folder(
    session: AsyncSession,
    drive: FakeDriveClient,
    settings: Settings,
    actor: Actor,
    *,
    folder_id: str = FOLDER_ID,
    team_scope: str | None = None,
):  # type: ignore[no-untyped-def]
    return await _folders(session, drive, settings).register(
        actor=actor,
        request_id=uuid.uuid4(),
        drive_folder_id=folder_id,
        path_label="Marketing / Kịch bản",
        purpose="Sheet kịch bản TikTok",
        team_scope=team_scope,
    )


def _request(
    kind: TemplateKind,
    *,
    name: str = "Kịch bản TikTok tháng 8",
    confirmation: str = "confirm-1",
    actor_reference: str = "777000111",
) -> SpreadsheetRequest:
    spec = template_for_kind(kind)
    return SpreadsheetRequest(
        template_code=spec.code,
        template_version=spec.version,
        name=name,
        folder_id=FOLDER_ID,
        kind=kind,
        channel="TikTok bác sĩ Tiến" if kind is TemplateKind.SCRIPT_MANAGEMENT else None,
        team="Content" if kind is TemplateKind.WORK_MANAGEMENT else None,
        period="Tháng 8/2026",
        actor_reference=actor_reference,
        confirmation_reference=confirmation,
        register_profile=kind is TemplateKind.SCRIPT_MANAGEMENT,
    )


# --- Folder URLs ------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://drive.google.com/drive/folders/1AbC_def-GHI", "1AbC_def-GHI"),
        ("https://drive.google.com/drive/u/0/folders/1AbC_def-GHI?usp=sharing", "1AbC_def-GHI"),
        ("1AbC_def-GHI", "1AbC_def-GHI"),
        ("https://drive.google.com/open?id=1AbC_def-GHI", "1AbC_def-GHI"),
        ("chỗ nào đó", None),
        ("", None),
    ],
)
def test_folder_id_is_extracted_or_refused(value: str, expected: str | None) -> None:
    """An unrecognised reference asks the human again, it does not guess."""
    assert extract_folder_id(value) == expected


# --- Folder validation ------------------------------------------------------
async def test_owner_can_register_a_valid_folder(
    session: AsyncSession,
    drive: FakeDriveClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    folder = await _register_folder(session, drive, rooted_settings, owner_actor)

    assert folder.validation_status == FolderValidationStatus.VALID.value
    assert folder.shared_drive_id == "shared-1"
    assert folder.name == "Kịch bản TikTok"
    assert folder.created_by_telegram_id == owner_actor.telegram_user_id


async def test_registering_a_folder_is_audited(
    session: AsyncSession,
    drive: FakeDriveClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    await _register_folder(session, drive, rooted_settings, owner_actor)
    rows = (await session.execute(select(AuditLog))).scalars().all()
    assert any(row.entity_type == "drive_folder" for row in rows)


async def test_a_folder_outside_the_root_is_rejected(
    session: AsyncSession,
    drive: FakeDriveClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """``GOOGLE_DRIVE_ROOT_FOLDER_ID`` confines MeoBot to one subtree."""
    drive.add_folder("elsewhere", "Kế toán", parents=("some-other-root",))

    with pytest.raises(ValidationError) as caught:
        await _register_folder(session, drive, rooted_settings, owner_actor, folder_id="elsewhere")

    assert "ngoài thư mục gốc" in caught.value.message


async def test_a_folder_meobot_cannot_write_to_is_rejected(
    session: AsyncSession,
    drive: FakeDriveClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """Read access is not enough to be a destination."""
    drive.add_folder("read-only", "Chỉ đọc", parents=(ROOT_ID,), can_add_children=False)

    with pytest.raises(ValidationError) as caught:
        await _register_folder(session, drive, rooted_settings, owner_actor, folder_id="read-only")

    assert "không có quyền tạo file" in caught.value.message


async def test_a_missing_folder_is_reported_not_registered(
    session: AsyncSession,
    drive: FakeDriveClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    with pytest.raises(ValidationError):
        await _register_folder(session, drive, rooted_settings, owner_actor, folder_id="ghost")


async def test_a_file_is_not_a_folder(
    session: AsyncSession,
    drive: FakeDriveClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    drive.add_template("a-spreadsheet")
    with pytest.raises(ValidationError):
        await _register_folder(
            session, drive, rooted_settings, owner_actor, folder_id="a-spreadsheet"
        )


async def test_a_folder_cannot_be_registered_twice(
    session: AsyncSession,
    drive: FakeDriveClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    await _register_folder(session, drive, rooted_settings, owner_actor)
    with pytest.raises(ConflictError):
        await _register_folder(session, drive, rooted_settings, owner_actor)


async def test_an_unregistered_folder_is_never_a_destination(
    session: AsyncSession,
    drive: FakeDriveClient,
    rooted_settings: Settings,
) -> None:
    """A well-formed id that nobody registered is still refused."""
    with pytest.raises(NotFoundError) as caught:
        await _folders(session, drive, rooted_settings).resolve_destination("1AbCdefGHIjkl")
    assert "/add_drive_folder" in caught.value.message


async def test_no_root_configured_means_no_root_restriction(
    session: AsyncSession,
    drive: FakeDriveClient,
    settings: Settings,
    owner_actor: Actor,
) -> None:
    """Deployments without a root still work; registration is the control."""
    drive.add_folder("anywhere", "Bất kỳ", parents=("unknown-parent",))
    folder = await _register_folder(session, drive, settings, owner_actor, folder_id="anywhere")
    assert folder.validation_status == FolderValidationStatus.VALID.value


# --- Permissions ------------------------------------------------------------
async def test_employee_may_not_create_spreadsheets(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
    employee_actor: Actor,
) -> None:
    """Sheet creation is not an employee capability by default."""
    from meobot.domain.permissions.matrix import Permission, has_permission

    assert not has_permission(employee_actor.role, Permission.SPREADSHEET_CREATE)
    assert not has_permission(employee_actor.role, Permission.DRIVE_FOLDER_MANAGE)
    # ...and they may still see a Sheet somebody shared with them.
    assert has_permission(employee_actor.role, Permission.SPREADSHEET_READ)


async def test_admin_may_manage_folders_and_templates(admin_actor: Actor) -> None:
    from meobot.domain.permissions.matrix import Permission, has_permission

    for permission in (
        Permission.DRIVE_FOLDER_MANAGE,
        Permission.SHEET_TEMPLATE_MANAGE,
        Permission.SPREADSHEET_CREATE,
    ):
        assert has_permission(admin_actor.role, permission)


async def test_owner_holds_every_new_capability(owner_actor: Actor) -> None:
    from meobot.domain.permissions.matrix import Permission, has_permission

    for permission in (
        Permission.CONVERSATION_USE,
        Permission.DRIVE_FOLDER_READ,
        Permission.DRIVE_FOLDER_MANAGE,
        Permission.SHEET_TEMPLATE_READ,
        Permission.SHEET_TEMPLATE_MANAGE,
        Permission.SPREADSHEET_CREATE,
        Permission.SPREADSHEET_READ,
    ):
        assert has_permission(owner_actor.role, permission)


async def test_team_lead_may_not_use_a_team_scoped_folder(
    session: AsyncSession,
    drive: FakeDriveClient,
    rooted_settings: Settings,
    owner_actor: Actor,
    team_lead_actor: Actor,
    admin_actor: Actor,
) -> None:
    """MeoBot does not know who is on which team, so it does not pretend to.

    A team-scoped folder is restricted to ADMIN and above rather than being
    silently open to every team lead.
    """
    folder = await _register_folder(
        session, drive, rooted_settings, owner_actor, team_scope="Content"
    )
    service = _folders(session, drive, rooted_settings)

    with pytest.raises(AuthorizationError) as caught:
        service.assert_usable_by(folder, team_lead_actor)
    assert "Content" in caught.value.message

    service.assert_usable_by(folder, admin_actor)
    service.assert_usable_by(folder, owner_actor)


async def test_an_unvalidated_folder_is_refused_at_use_time(
    session: AsyncSession,
    drive: FakeDriveClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """Even a registered folder is re-checked before a file goes into it."""
    folder = await _register_folder(session, drive, rooted_settings, owner_actor)
    folder.validation_status = FolderValidationStatus.NO_ACCESS.value

    with pytest.raises(ValidationError):
        _folders(session, drive, rooted_settings).assert_usable_by(folder, owner_actor)


# --- Templates --------------------------------------------------------------
async def test_builtin_templates_are_seeded_from_code(
    session: AsyncSession, settings: Settings
) -> None:
    """The database row and the code definition cannot drift apart."""
    service = SheetTemplateService(session, AuditService(session), settings)
    templates = await service.ensure_all_builtin()

    assert {template.code for template in templates} == {"WORK_MANAGEMENT", "SCRIPT_MANAGEMENT"}
    script = next(t for t in templates if t.code == "SCRIPT_MANAGEMENT")
    assert script.expected_tabs["tabs"] == ["Scripts", "Lists", "Instructions"]
    assert script.default_worksheet_name == "Scripts"


async def test_seeding_is_idempotent(session: AsyncSession, settings: Settings) -> None:
    service = SheetTemplateService(session, AuditService(session), settings)
    first = await service.ensure_all_builtin()
    second = await service.ensure_all_builtin()
    assert [t.id for t in first] == [t.id for t in second]


async def test_configuring_a_template_id_switches_creation_to_copying(
    session: AsyncSession,
) -> None:
    """``GOOGLE_SCRIPT_SHEET_TEMPLATE_ID`` takes effect on the next startup."""
    configured = Settings(google_script_sheet_template_id="template-7")
    service = SheetTemplateService(session, AuditService(session), configured)
    template = await service.for_kind(TemplateKind.SCRIPT_MANAGEMENT)
    assert template.source_file_id == "template-7"


def test_the_work_template_has_the_documented_task_columns() -> None:
    assert WORK_TASK_HEADERS == (
        "task_id",
        "task_name",
        "description",
        "assignee",
        "team",
        "priority",
        "status",
        "start_date",
        "deadline",
        "progress_percent",
        "dependencies",
        "deliverable_url",
        "notes",
        "last_updated",
    )
    assert template_for_kind(TemplateKind.WORK_MANAGEMENT).expected_tabs == [
        "Tasks",
        "Team",
        "Lists",
        "Dashboard",
    ]


def test_the_script_template_maps_every_canonical_field() -> None:
    """The default mapping is complete, so no LLM guess is needed."""
    mapping = SCRIPT_TEMPLATE.default_field_mapping
    assert set(mapping) == set(CANONICAL_FIELDS)
    # Every mapped header actually exists on the sheet the template creates.
    for header in mapping.values():
        assert header in SCRIPT_TEMPLATE.primary_headers


def test_the_script_template_write_back_mapping_is_complete() -> None:
    mapping = SCRIPT_TEMPLATE.default_write_back_mapping
    assert set(mapping) == set(WRITE_BACK_FIELDS)
    for header in mapping.values():
        assert header in SCRIPT_TEMPLATE.primary_headers


def test_the_work_template_is_not_a_script_source() -> None:
    """No script mapping, and no ``script_body`` column to build one from."""
    work = template_for_kind(TemplateKind.WORK_MANAGEMENT)
    assert work.default_field_mapping == {}
    assert "script_body" not in work.primary_headers


# --- Creation ---------------------------------------------------------------
async def test_creating_a_script_sheet_registers_a_sheet_profile(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """The whole point of a script sheet: MeoBot can read it immediately."""
    await _register_folder(session, drive, rooted_settings, owner_actor)
    outcome = await _creation(session, drive, sheets, rooted_settings).create(
        actor=owner_actor,
        request_id=uuid.uuid4(),
        request=_request(TemplateKind.SCRIPT_MANAGEMENT),
    )

    assert outcome.succeeded
    assert outcome.created_now
    assert outcome.record.spreadsheet_url
    assert outcome.profile_id is not None

    profile = await session.get(SheetProfile, outcome.profile_id)
    assert profile is not None
    assert profile.sheet_name == "Scripts"
    assert profile.field_mapping["script_body"] == ["script_body"]
    assert profile.write_back_mapping["meobot_status"] == "meobot_status"
    assert profile.active


async def test_creating_a_work_sheet_registers_no_sheet_profile(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """A task board must never be imported as a source of scripts."""
    await _register_folder(session, drive, rooted_settings, owner_actor)
    outcome = await _creation(session, drive, sheets, rooted_settings).create(
        actor=owner_actor,
        request_id=uuid.uuid4(),
        request=_request(TemplateKind.WORK_MANAGEMENT),
    )

    assert outcome.succeeded
    assert outcome.profile_id is None
    assert outcome.record.sheet_profile_id is None
    profiles = (await session.execute(select(SheetProfile))).scalars().all()
    assert profiles == []


async def test_a_blank_sheet_gets_the_standard_tabs_and_headers(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """Without a template id, the generated file is still usable."""
    await _register_folder(session, drive, rooted_settings, owner_actor)
    outcome = await _creation(session, drive, sheets, rooted_settings).create(
        actor=owner_actor,
        request_id=uuid.uuid4(),
        request=_request(TemplateKind.SCRIPT_MANAGEMENT),
    )

    assert outcome.method is CreationMethod.BLANK_STANDARD
    spreadsheet_id = outcome.record.spreadsheet_id
    assert spreadsheet_id is not None
    headers, _ = sheets.sheets[(spreadsheet_id, "Scripts")]
    assert headers == list(SCRIPT_TEMPLATE.primary_headers)
    assert (spreadsheet_id, "Instructions") in sheets.sheets


async def test_a_configured_template_is_copied_rather_than_generated(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    owner_actor: Actor,
) -> None:
    """Copying keeps the formatting, formulas and dropdowns a human designed."""
    configured = Settings(
        google_drive_root_folder_id=ROOT_ID,
        google_script_sheet_template_id="template-7",
    )
    drive.add_template("template-7", "Mẫu kịch bản chuẩn")
    await _register_folder(session, drive, configured, owner_actor)

    outcome = await _creation(session, drive, sheets, configured).create(
        actor=owner_actor,
        request_id=uuid.uuid4(),
        request=_request(TemplateKind.SCRIPT_MANAGEMENT),
    )

    assert outcome.method is CreationMethod.TEMPLATE_COPY
    assert any(call.startswith("copy_file:template-7") for call in drive.calls)
    assert not any(call.startswith("create_spreadsheet") for call in drive.calls)


async def test_creation_is_audited(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    await _register_folder(session, drive, rooted_settings, owner_actor)
    await _creation(session, drive, sheets, rooted_settings).create(
        actor=owner_actor,
        request_id=uuid.uuid4(),
        request=_request(TemplateKind.WORK_MANAGEMENT),
    )

    rows = (await session.execute(select(AuditLog))).scalars().all()
    created = [row for row in rows if row.entity_type == "created_spreadsheet"]
    assert created
    assert created[-1].after_data is not None
    assert created[-1].after_data["creation_status"] == CreationStatus.SUCCEEDED.value


async def test_the_created_file_carries_meobots_own_markers(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """The app properties reconciliation depends on - and nothing sensitive."""
    await _register_folder(session, drive, rooted_settings, owner_actor)
    request = _request(TemplateKind.SCRIPT_MANAGEMENT)
    outcome = await _creation(session, drive, sheets, rooted_settings).create(
        actor=owner_actor, request_id=uuid.uuid4(), request=request
    )

    assert outcome.record.drive_file_id is not None
    created: DriveFile = drive.files[outcome.record.drive_file_id]
    assert created.app_properties["meobot_managed"] == "true"
    assert created.app_properties["meobot_template_code"] == "SCRIPT_MANAGEMENT"
    assert created.app_properties["meobot_idempotency_reference"] == request.idempotency_key()
    # No personal data and no secrets on a file anyone with access can read.
    assert "777000111" not in "".join(created.app_properties.values())


# --- Idempotency and reconciliation ----------------------------------------
async def test_the_same_confirmation_creates_exactly_one_file(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """A double-tapped button, or a Celery retry: one file either way."""
    await _register_folder(session, drive, rooted_settings, owner_actor)
    service = _creation(session, drive, sheets, rooted_settings)
    request = _request(TemplateKind.SCRIPT_MANAGEMENT)

    first = await service.create(actor=owner_actor, request_id=uuid.uuid4(), request=request)
    second = await service.create(actor=owner_actor, request_id=uuid.uuid4(), request=request)

    assert first.created_now is True
    assert second.created_now is False, "the second attempt created a new file"
    assert first.record.id == second.record.id
    assert first.record.drive_file_id == second.record.drive_file_id

    spreadsheets = [item for item in drive.files.values() if item.is_spreadsheet]
    assert len(spreadsheets) == 1

    rows = (await session.execute(select(CreatedSpreadsheet))).scalars().all()
    assert len(rows) == 1


async def test_a_genuinely_new_request_does_create_a_second_file(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """Deduplication must not become "you may only ever create one Sheet"."""
    await _register_folder(session, drive, rooted_settings, owner_actor)
    service = _creation(session, drive, sheets, rooted_settings)

    first = await service.create(
        actor=owner_actor,
        request_id=uuid.uuid4(),
        request=_request(TemplateKind.WORK_MANAGEMENT, name="Tháng 8", confirmation="c1"),
    )
    second = await service.create(
        actor=owner_actor,
        request_id=uuid.uuid4(),
        request=_request(TemplateKind.WORK_MANAGEMENT, name="Tháng 9", confirmation="c2"),
    )

    assert first.record.drive_file_id != second.record.drive_file_id
    assert second.created_now is True


async def test_a_retry_reconciles_a_file_google_already_created(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """The crash-between-Google-and-commit case.

    The row is left pending with no file id, but the file exists on Drive
    carrying the idempotency reference. The retry must find it, not make
    another one.
    """
    await _register_folder(session, drive, rooted_settings, owner_actor)
    service = _creation(session, drive, sheets, rooted_settings)
    request = _request(TemplateKind.WORK_MANAGEMENT)

    created = await service.create(actor=owner_actor, request_id=uuid.uuid4(), request=request)
    # Simulate the lost commit: the file is on Drive, the row is not settled.
    created.record.creation_status = CreationStatus.PENDING.value
    created.record.drive_file_id = None
    created.record.spreadsheet_id = None
    await session.flush()

    retried = await service.create(actor=owner_actor, request_id=uuid.uuid4(), request=request)

    assert retried.created_now is False
    assert retried.record.creation_status == CreationStatus.SUCCEEDED.value
    assert len([item for item in drive.files.values() if item.is_spreadsheet]) == 1


async def test_the_reconciler_settles_pending_rows(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """The scheduled sweep finds the file and closes the row."""
    await _register_folder(session, drive, rooted_settings, owner_actor)
    service = _creation(session, drive, sheets, rooted_settings)
    outcome = await service.create(
        actor=owner_actor, request_id=uuid.uuid4(), request=_request(TemplateKind.WORK_MANAGEMENT)
    )
    outcome.record.creation_status = CreationStatus.PENDING.value
    outcome.record.drive_file_id = None
    await session.flush()

    report = await service.reconcile_pending(actor=owner_actor, request_id=uuid.uuid4())

    assert report == {"pending": 1, "recovered": 1, "failed": 0}
    assert outcome.record.creation_status == CreationStatus.SUCCEEDED.value


async def test_the_reconciler_never_creates_a_file(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """A pending row with nothing behind it is failed, not fulfilled."""
    await _register_folder(session, drive, rooted_settings, owner_actor)
    service = _creation(session, drive, sheets, rooted_settings)
    session.add(
        CreatedSpreadsheet(
            name="Không bao giờ tạo được",
            kind=TemplateKind.WORK_MANAGEMENT.value,
            parent_folder_id=FOLDER_ID,
            idempotency_key="orphan-key",
            creation_status=CreationStatus.PENDING.value,
        )
    )
    await session.flush()
    before = len(drive.files)

    report = await service.reconcile_pending(actor=owner_actor, request_id=uuid.uuid4())

    assert report["failed"] == 1
    assert report["recovered"] == 0
    assert len(drive.files) == before, "reconciliation created a file"


def test_the_idempotency_key_covers_everything_that_identifies_a_request() -> None:
    """Same request -> same key; any real difference -> a different key."""
    base = _request(TemplateKind.SCRIPT_MANAGEMENT)
    assert base.idempotency_key() == _request(TemplateKind.SCRIPT_MANAGEMENT).idempotency_key()

    variations = [
        _request(TemplateKind.SCRIPT_MANAGEMENT, name="Tên khác"),
        _request(TemplateKind.SCRIPT_MANAGEMENT, confirmation="another-confirmation"),
        _request(TemplateKind.SCRIPT_MANAGEMENT, actor_reference="999"),
        _request(TemplateKind.WORK_MANAGEMENT),
        base.model_copy(update={"folder_id": "another-folder"}),
        base.model_copy(update={"template_version": 2}),
    ]
    keys = {variant.idempotency_key() for variant in variations}
    assert base.idempotency_key() not in keys
    assert len(keys) == len(variations)


def test_the_idempotency_key_ignores_case_and_padding_in_the_name() -> None:
    """ "Tháng 8" and " tháng 8 " are the same request typed twice."""
    first = _request(TemplateKind.WORK_MANAGEMENT, name="Tháng 8")
    second = _request(TemplateKind.WORK_MANAGEMENT, name="  tháng 8  ")
    assert first.idempotency_key() == second.idempotency_key()


# --- Failure handling -------------------------------------------------------
async def test_a_drive_failure_marks_the_row_failed_and_reports_it(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """A failure is recorded, not silently swallowed - and never claimed a
    success."""
    from meobot.core.errors import IntegrationAuthError

    await _register_folder(session, drive, rooted_settings, owner_actor)
    drive.next_error = IntegrationAuthError("Google từ chối", provider="google_drive")

    with pytest.raises(IntegrationAuthError):
        await _creation(session, drive, sheets, rooted_settings).create(
            actor=owner_actor,
            request_id=uuid.uuid4(),
            request=_request(TemplateKind.WORK_MANAGEMENT),
        )

    rows = (await session.execute(select(CreatedSpreadsheet))).scalars().all()
    assert len(rows) == 1
    assert rows[0].creation_status == CreationStatus.FAILED.value
    assert rows[0].creation_error


async def test_creating_into_an_unregistered_folder_is_refused(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """The allow-list is checked at creation time, not only at preview time."""
    with pytest.raises(NotFoundError):
        await _creation(session, drive, sheets, rooted_settings).create(
            actor=owner_actor,
            request_id=uuid.uuid4(),
            request=_request(TemplateKind.WORK_MANAGEMENT),
        )
    assert not [item for item in drive.files.values() if item.is_spreadsheet]


# --- Preview ----------------------------------------------------------------
async def test_the_preview_shows_what_will_actually_happen(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    """Everything the confirmation is supposed to display, resolved for real."""
    await _register_folder(session, drive, rooted_settings, owner_actor)
    preview = await _creation(session, drive, sheets, rooted_settings).preview(
        _request(TemplateKind.SCRIPT_MANAGEMENT)
    )

    assert preview["template_code"] == "SCRIPT_MANAGEMENT"
    assert preview["template_version"] == 1
    assert preview["creation_method"] == CreationMethod.BLANK_STANDARD.value
    assert preview["folder_name"] == "Kịch bản TikTok"
    assert preview["shared_drive"] is True
    assert preview["tabs"] == ["Scripts", "Lists", "Instructions"]
    assert preview["will_register_profile"] is True
    assert preview["period"] == "Tháng 8/2026"
    assert preview["idempotency_key"]


async def test_the_preview_says_when_a_work_sheet_will_not_be_registered(
    session: AsyncSession,
    drive: FakeDriveClient,
    sheets: FakeSheetsClient,
    rooted_settings: Settings,
    owner_actor: Actor,
) -> None:
    await _register_folder(session, drive, rooted_settings, owner_actor)
    preview = await _creation(session, drive, sheets, rooted_settings).preview(
        _request(TemplateKind.WORK_MANAGEMENT)
    )
    assert preview["will_register_profile"] is False
    assert preview["tabs"] == ["Tasks", "Team", "Lists", "Dashboard"]


# --- Tool surface -----------------------------------------------------------
def test_the_registry_exposes_the_documented_drive_tools() -> None:
    from meobot.tools.registry import build_default_registry
    from tests.fakes import StubHealthService

    registry = build_default_registry(health_service=StubHealthService())  # type: ignore[arg-type]
    for name in (
        "drive.folder.list",
        "drive.folder.inspect",
        "drive.folder.register",
        "sheet_template.list",
        "spreadsheet.create_work",
        "spreadsheet.create_script",
        "spreadsheet.list_created",
        "spreadsheet.get_created",
    ):
        assert name in registry, f"{name} is not registered"


def test_no_registered_tool_can_delete_anything() -> None:
    """Out of scope, enforced by the registry rather than by a policy note."""
    from meobot.tools.registry import build_default_registry
    from tests.fakes import StubHealthService

    registry = build_default_registry(health_service=StubHealthService())  # type: ignore[arg-type]
    for name in registry.names:
        assert not any(word in name for word in ("delete", "trash", "remove", "destroy")), name
    for tool in registry.policies().values():
        assert not tool.destructive, f"{tool.name} is flagged destructive"


def test_drive_tool_risk_levels_match_what_they_cost() -> None:
    """Reads are low, state changes are medium, template changes are high."""
    from meobot.domain.policy.models import RiskLevel
    from meobot.tools.registry import build_default_registry
    from tests.fakes import StubHealthService

    registry = build_default_registry(health_service=StubHealthService())  # type: ignore[arg-type]
    expected = {
        "drive.folder.list": RiskLevel.LOW,
        "drive.folder.inspect": RiskLevel.LOW,
        "sheet_template.list": RiskLevel.LOW,
        "spreadsheet.list_created": RiskLevel.LOW,
        "spreadsheet.get_created": RiskLevel.LOW,
        "drive.folder.register": RiskLevel.MEDIUM,
        "spreadsheet.create_work": RiskLevel.MEDIUM,
        "spreadsheet.create_script": RiskLevel.MEDIUM,
        "sheet_template.set_active": RiskLevel.HIGH,
        "sheet_template.set_source": RiskLevel.HIGH,
    }
    for name, risk in expected.items():
        assert registry.get(name).risk_level is risk, name


def test_high_risk_drive_tools_require_confirmation(owner_actor: Actor) -> None:
    """Changing what every future creation produces needs an explicit yes."""
    from meobot.domain.policy.engine import PolicyEngine
    from meobot.domain.policy.models import ActionPlan, RiskLevel
    from meobot.tools.registry import build_default_registry
    from tests.fakes import StubHealthService

    registry = build_default_registry(health_service=StubHealthService())  # type: ignore[arg-type]
    policy = PolicyEngine(registry.policies())

    decision = policy.evaluate(
        ActionPlan(
            intent="deactivate_template",
            tool_name="sheet_template.set_active",
            risk_level=RiskLevel.HIGH,
            arguments={"template_id": str(uuid.uuid4()), "active": False},
        ),
        owner_actor,
    )
    assert decision.allowed
    assert decision.requires_confirmation


def test_folder_mime_type_is_the_google_one() -> None:
    """A wrong constant here would silently make every folder look like a file."""
    assert FOLDER_MIME == "application/vnd.google-apps.folder"
