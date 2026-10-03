"""The two spreadsheets MeoBot knows how to produce.

These are defined in code rather than seeded by a migration, so the column
lists, the tab names and the Sheet-Profile mapping cannot drift apart from each
other. :class:`~meobot.application.sheet_template_service.SheetTemplateService`
upserts them into ``sheet_templates`` on demand; the database row is what an
admin can then point at a real ``source_file_id``.

Two rules the rest of the system depends on:

* a **work-management** sheet is never registered as a script Sheet Profile -
  it has no ``script_body`` column and importing it would produce nonsense;
* a **script-management** sheet created from this template gets its mapping
  from here, not from the LLM. The structure is known. Asking a model to guess
  a layout we designed ourselves would be strictly worse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from meobot.domain.drive.models import TemplateKind

#: Bumped whenever the columns below change. The version is part of the
#: idempotency key and is recorded on every created file, so an old file can
#: always be traced back to the layout it was created from.
WORK_TEMPLATE_VERSION = 1
SCRIPT_TEMPLATE_VERSION = 1

WORK_TEMPLATE_CODE = "WORK_MANAGEMENT"
SCRIPT_TEMPLATE_CODE = "SCRIPT_MANAGEMENT"


@dataclass(frozen=True, slots=True)
class TabSpec:
    """One worksheet of a generated spreadsheet."""

    title: str
    headers: tuple[str, ...] = ()
    #: Rows written under the header - dropdown vocabularies, instructions.
    seed_rows: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    """A complete built-in template."""

    code: str
    name: str
    description: str
    kind: TemplateKind
    version: int
    default_worksheet_name: str
    tabs: tuple[TabSpec, ...]
    #: Canonical script field -> the header that feeds it. Empty for work
    #: sheets, which are not script sources.
    default_field_mapping: dict[str, str] = field(default_factory=dict)
    #: Canonical write-back field -> the header MeoBot writes into.
    default_write_back_mapping: dict[str, str] = field(default_factory=dict)

    @property
    def expected_tabs(self) -> list[str]:
        return [tab.title for tab in self.tabs]

    def tab(self, title: str) -> TabSpec | None:
        return next((tab for tab in self.tabs if tab.title == title), None)

    @property
    def primary_headers(self) -> tuple[str, ...]:
        """Headers of the tab a Sheet Profile would read."""
        primary = self.tab(self.default_worksheet_name)
        return primary.headers if primary else ()


# --- Work management -------------------------------------------------------
WORK_TASK_HEADERS: tuple[str, ...] = (
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

WORK_TEMPLATE = TemplateSpec(
    code=WORK_TEMPLATE_CODE,
    name="Sheet quản lý công việc",
    description=(
        "Theo dõi đầu việc theo người phụ trách, team, ưu tiên, tiến độ và hạn "
        "chót. Không dùng làm nguồn kịch bản."
    ),
    kind=TemplateKind.WORK_MANAGEMENT,
    version=WORK_TEMPLATE_VERSION,
    default_worksheet_name="Tasks",
    tabs=(
        TabSpec(title="Tasks", headers=WORK_TASK_HEADERS),
        TabSpec(
            title="Team",
            headers=("member", "role", "team", "telegram", "email", "active"),
        ),
        TabSpec(
            title="Lists",
            headers=("priority", "status", "team"),
            seed_rows=(
                ("Cao", "Chưa bắt đầu", "Content"),
                ("Trung bình", "Đang làm", "Production"),
                ("Thấp", "Chờ duyệt", "Media"),
                ("", "Hoàn thành", "Marketing"),
                ("", "Tạm dừng", ""),
            ),
        ),
        TabSpec(
            title="Dashboard",
            headers=("chỉ số", "giá trị", "ghi chú"),
            seed_rows=(
                ("Tổng số việc", "=COUNTA(Tasks!A2:A)", "Tự động đếm từ tab Tasks"),
                (
                    "Đang làm",
                    '=COUNTIF(Tasks!G2:G;"Đang làm")',
                    "Đổi công thức nếu bạn đổi tên trạng thái",
                ),
                ("Hoàn thành", '=COUNTIF(Tasks!G2:G;"Hoàn thành")', ""),
                ("Quá hạn", "", "Điền công thức theo lịch của team"),
            ),
        ),
    ),
)


# --- Script management -----------------------------------------------------
SCRIPT_HEADERS: tuple[str, ...] = (
    "external_script_id",
    "title",
    "hook",
    "script_body",
    "production_notes",
    "author",
    "deadline",
    "source_status",
    "script_type",
    "meobot_status",
    "review_score",
    "review_summary",
    "reviewed_at",
    "approved_by",
    "approved_at",
    "revision_comment",
)

#: Canonical field -> header. The left-hand names are
#: :data:`meobot.domain.sheets.models.CANONICAL_FIELDS`.
SCRIPT_FIELD_MAPPING: dict[str, str] = {
    "script_id": "external_script_id",
    "title": "title",
    "hook": "hook",
    "script_body": "script_body",
    "production_notes": "production_notes",
    "author": "author",
    "deadline": "deadline",
    "source_status": "source_status",
}

#: Canonical write-back field -> header. These are the only columns MeoBot ever
#: writes; everything the team keeps in the sheet is left alone.
SCRIPT_WRITE_BACK_MAPPING: dict[str, str] = {
    "meobot_status": "meobot_status",
    "review_score": "review_score",
    "review_summary": "review_summary",
    "reviewed_at": "reviewed_at",
    "approved_by": "approved_by",
    "approved_at": "approved_at",
    "revision_comment": "revision_comment",
}

SCRIPT_TEMPLATE = TemplateSpec(
    code=SCRIPT_TEMPLATE_CODE,
    name="Sheet quản lý kịch bản",
    description=(
        "Nguồn kịch bản cho MeoBot: nhập kịch bản, đồng bộ, review bằng AI, "
        "duyệt sản xuất và ghi kết quả ngược lại Sheet."
    ),
    kind=TemplateKind.SCRIPT_MANAGEMENT,
    version=SCRIPT_TEMPLATE_VERSION,
    default_worksheet_name="Scripts",
    tabs=(
        TabSpec(title="Scripts", headers=SCRIPT_HEADERS),
        TabSpec(
            title="Lists",
            headers=("source_status", "script_type", "meobot_status"),
            seed_rows=(
                ("Nháp", "tiktok_short", "imported"),
                ("Chờ review", "facebook_post", "ai_reviewed"),
                ("Chờ duyệt", "youtube_short", "waiting_for_script_approval"),
                ("Đã duyệt", "", "approved_for_production"),
                ("Cần sửa", "", "revision_required"),
            ),
        ),
        TabSpec(
            title="Instructions",
            headers=("hướng dẫn",),
            seed_rows=(
                ("Mỗi dòng trong tab Scripts là một kịch bản.",),
                ("Cột external_script_id là mã của team, phải duy nhất.",),
                ("Cột script_body là nội dung kịch bản — bắt buộc có.",),
                ("MeoBot chỉ ghi vào các cột meobot_status, review_*, approved_*.",),
                ("Đừng đổi tên cột: đổi tên sẽ làm MeoBot dừng đồng bộ để hỏi lại.",),
                ("Duyệt kịch bản = được phép sản xuất, chưa phải duyệt đăng bài.",),
            ),
        ),
    ),
    default_field_mapping=SCRIPT_FIELD_MAPPING,
    default_write_back_mapping=SCRIPT_WRITE_BACK_MAPPING,
)


BUILTIN_TEMPLATES: tuple[TemplateSpec, ...] = (WORK_TEMPLATE, SCRIPT_TEMPLATE)


def template_for_kind(kind: TemplateKind) -> TemplateSpec:
    """Return the built-in template for ``kind``."""
    for spec in BUILTIN_TEMPLATES:
        if spec.kind is kind:
            return spec
    raise KeyError(f"No built-in template for kind {kind!r}")  # pragma: no cover - enum-bound
