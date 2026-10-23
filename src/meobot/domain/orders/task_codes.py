"""The "mã task công việc" of each production phase and how they score (0074).

Every production node of an order carries a list of task codes from its
phase's catalogue - Biên kịch N1-N5, Design T1-T5, Dựng D1-D7, plus one
"Việc khác" per phase whose points the person types in. A code's points are
multiplied by the chosen form ("hình thức": Làm mới 100%, Adapt 50%,
Resize 20%, Diễn hoạt 125%, After Effects 150%) and the quantity.

The list on the order when it is placed is only a **suggestion**. What the
worker hands in is the **actual** list, and the one their points come from.
An actual list that differs from the suggestion waits for the phase's
Trưởng phòng (who sees the difference, :func:`diff_items`) unless that
Trưởng phòng did the work themselves. No I/O here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from meobot.domain.orders.design_codes import DesignItem, describe_item, total_points
from meobot.domain.orders.models import OrderNodeType

#: The phases that carry task codes, in pipeline order.
TASK_PHASES: tuple[OrderNodeType, ...] = (
    OrderNodeType.BIEN_TAP,
    OrderNodeType.THIET_KE,
    OrderNodeType.DUNG,
)

#: Biên kịch and Dựng take exactly one code per node, quantity 1; Design takes
#: several, each with a quantity.
SINGLE_CODE_PHASES: frozenset[OrderNodeType] = frozenset(
    {OrderNodeType.BIEN_TAP, OrderNodeType.DUNG}
)

PHASE_LABELS: dict[OrderNodeType, str] = {
    OrderNodeType.BIEN_TAP: "Biên kịch",
    OrderNodeType.THIET_KE: "Design",
    OrderNodeType.DUNG: "Dựng",
}

#: The internal code of each phase's "Việc khác" row.
FREE_CODES: dict[OrderNodeType, str] = {
    OrderNodeType.BIEN_TAP: "N0",
    OrderNodeType.THIET_KE: "T0",
    OrderNodeType.DUNG: "D0",
}
FREE_NAME = "Việc khác"

#: The form every line has unless another is chosen. It is never switched off.
DEFAULT_FORM = "NEW"


def is_single(phase: OrderNodeType) -> bool:
    return phase in SINGLE_CODE_PHASES


def phase_label(phase: OrderNodeType) -> str:
    return PHASE_LABELS.get(phase, phase.value)


@dataclass(frozen=True, slots=True)
class TaskFormSpec:
    """One form ("hình thức") and the phases it may be chosen in."""

    key: str
    label: str
    pct: int
    note: str
    phases: tuple[OrderNodeType, ...]
    active: bool = True


#: The forms of the department's sheet ("HÌNH THỨC", 2026-10-22).
DEFAULT_TASK_FORMS: tuple[TaskFormSpec, ...] = (
    TaskFormSpec("NEW", "Làm mới", 100, "Mặc định cho mọi việc. Để trống tính 100%.", TASK_PHASES),
    TaskFormSpec(
        "ADAPT",
        "Adapt / sửa từ mẫu có sẵn",
        50,
        "Thiết kế, video dùng lại khung có sẵn.",
        (OrderNodeType.THIET_KE, OrderNodeType.DUNG),
    ),
    TaskFormSpec(
        "RESIZE",
        "Resize / xuất thêm kích thước",
        20,
        "Chỉ đổi kích thước, chỉnh bố cục nhẹ.",
        (OrderNodeType.THIET_KE, OrderNodeType.DUNG),
    ),
    TaskFormSpec(
        "ANIMATED",
        "Video có diễn hoạt",
        125,
        "Chỉ dùng cho D1-D7.",
        (OrderNodeType.DUNG,),
    ),
    TaskFormSpec(
        "AFTER_EFFECTS",
        "Video có After Effects",
        150,
        "Chỉ dùng cho D1-D7. Đã gồm diễn hoạt, không chọn cả hai.",
        (OrderNodeType.DUNG,),
    ),
)


def forms_for(forms: Iterable[TaskFormSpec], phase: OrderNodeType) -> tuple[TaskFormSpec, ...]:
    """The active forms a line of ``phase`` may take, "Làm mới" first."""
    chosen = [form for form in forms if form.active and phase in form.phases]
    if not any(form.key == DEFAULT_FORM for form in chosen):
        chosen.insert(0, DEFAULT_TASK_FORMS[0])
    return tuple(chosen)


@dataclass(frozen=True, slots=True)
class SeedTaskCode:
    phase: OrderNodeType
    code: str
    name: str | None
    examples: str
    unit_label: str | None
    points: int
    note: str | None = None
    free: bool = False


#: The department's catalogue (sheet "Danh mục điểm", 2026-10-22). Design has
#: no names: its picker shows the code and the examples.
SEED_TASK_CODES: tuple[SeedTaskCode, ...] = (
    SeedTaskCode(
        OrderNodeType.BIEN_TAP,
        "N1",
        "Kịch bản ngắn / nội dung ngắn",
        "Kịch bản video trend, đổi hook, caption, linetext, content thư mời, brief order thiết kế",
        "bản",
        15,
    ),
    SeedTaskCode(
        OrderNodeType.BIEN_TAP,
        "N2",
        "Nội dung ngắn",
        "Caption, đổi hook, linetext, content thư mời, tin nhắn/email gửi khách, "
        "brief order thiết kế",
        "bản",
        60,
    ),
    SeedTaskCode(
        OrderNodeType.BIEN_TAP,
        "N3",
        "Kịch bản video đến 3 phút",
        "Kịch bản ads, talk bác sĩ, feedback/review, video gửi khách",
        "kịch bản",
        120,
    ),
    SeedTaskCode(
        OrderNodeType.BIEN_TAP,
        "N4",
        "Bài viết dài / kịch bản trên 3 phút",
        "Bài SEO, bài dịch vụ, trang thông tin website, kịch bản video tuyển dụng/đào tạo",
        "bài",
        120,
    ),
    SeedTaskCode(
        OrderNodeType.BIEN_TAP,
        "N5",
        "Kế hoạch / kịch bản tổng",
        "Kế hoạch nội dung dự án hoặc tháng, kịch bản KOL/KOC, chương trình sale, sự kiện lớn, YEP",
        "bản",
        480,
    ),
    SeedTaskCode(
        OrderNodeType.THIET_KE,
        "T1",
        None,
        "Ảnh post, ảnh ads 1 khung, banner, thumbnail, avatar, popup, barname, thiệp sinh nhật",
        "mẫu",
        90,
        "Adapt = 50%, Resize = 20% (chọn ở Hình thức).",
    ),
    SeedTaskCode(
        OrderNodeType.THIET_KE,
        "T2",
        None,
        "Cover page, standee, poster, flyer, backdrop, billboard, trang bìa brochure, "
        "bộ ảnh ads nhiều khung",
        "mẫu",
        360,
        "Adapt = 50%, Resize = 20% (chọn ở Hình thức).",
    ),
    SeedTaskCode(
        OrderNodeType.THIET_KE,
        "T3",
        None,
        "Key visual chương trình, logo, concept nhận diện",
        "phương án",
        480,
        "Adapt = 50%, Resize = 20% (chọn ở Hình thức).",
    ),
    SeedTaskCode(
        OrderNodeType.THIET_KE,
        "T4",
        None,
        "Trang trong brochure, slide thuyết trình, catalogue",
        "trang",
        90,
        "Adapt = 50%, Resize = 20% (chọn ở Hình thức).",
    ),
    SeedTaskCode(
        OrderNodeType.THIET_KE,
        "T5",
        None,
        "Thiết kế trang con, 1 màn mobile, dựng Ladipage/Webcake",
        "trang",
        480,
        "Adapt = 50%, Resize = 20% (chọn ở Hình thức).",
    ),
    SeedTaskCode(
        OrderNodeType.DUNG,
        "D1",
        "Edit video trend",
        "Video bắt trend làm theo format/âm thanh có sẵn, đến 60 giây",
        "video",
        45,
    ),
    SeedTaskCode(
        OrderNodeType.DUNG,
        "D2",
        "Video đến 90 giây",
        "Video ads, video social, video sinh nhật, thư mời",
        "video",
        120,
        "Đã gồm: cắt ghép, nhạc, text cơ bản, phụ đề, thumbnail lấy từ khung hình, "
        "chỉnh màu cơ bản.",
    ),
    SeedTaskCode(
        OrderNodeType.DUNG,
        "D3",
        "Video trên 90 giây đến 3 phút",
        "Video hỏi đáp bác sĩ, trailer sự kiện nhỏ, video tuyển dụng",
        "video",
        240,
        "Có diễn hoạt: chọn hình thức 'Video có diễn hoạt' (+25%). "
        "Có After Effects: chọn 'Video có After Effects' (+50%).",
    ),
    SeedTaskCode(
        OrderNodeType.DUNG,
        "D4",
        "Video trên 3 đến 5 phút",
        "Video quy trình, trailer sự kiện lớn, video cơ sở",
        "video",
        480,
    ),
    SeedTaskCode(
        OrderNodeType.DUNG,
        "D5",
        "Video trên 5 đến 15 phút",
        "Talk bác sĩ dài, video đào tạo",
        "video",
        480,
    ),
    SeedTaskCode(
        OrderNodeType.DUNG,
        "D6",
        "Video trên 15 phút",
        "Ghi hình hội thảo, đào tạo trọn buổi",
        "video",
        480,
    ),
    SeedTaskCode(
        OrderNodeType.DUNG,
        "D7",
        "Bản phái sinh từ video đã dựng",
        "Cắt bản ngắn, đổi tỷ lệ khung hình, đổi hook, ghép lại intro/outro",
        "bản",
        30,
    ),
    *(
        SeedTaskCode(
            phase,
            FREE_CODES[phase],
            FREE_NAME,
            "Việc ngoài danh mục: ghi rõ làm gì và tự điền số điểm (1 giờ công = 60 điểm).",
            None,
            0,
            free=True,
        )
        for phase in TASK_PHASES
    ),
)

#: The department's conversion of the 23 old design codes (2026-10-22): only
#: the code changes, the form stays "Làm mới".
LEGACY_DESIGN_CODES: Mapping[str, str] = {
    "RBTS": "T1",
    "TKBTS": "T1",
    "ABTS": "T1",
    "TAPE": "T2",
    "TKC": "T2",
    "CA": "T1",
    "CAD": "T1",
    "ATV": "T1",
    "TKTV": "T1",
    "TKKV": "T1",
    "AKV": "T1",
    "BVB": "T2",
    "BVA": "T3",
    "TKV": "T3",
    "TKBGL": "T4",
    "TKVO-TST": "T4",
    "AVO-TST": "T4",
    "TKLG": "T3",
    "TKLGDG": "T1",
    "O-SPB": "T2",
    "O-TF": "T2",
    "O-TB": "T3",
    "O-TD": "T3",
}


# --- suggestion vs actual -------------------------------------------------------------

DiffKind = Literal["ADDED", "REMOVED", "CHANGED"]


@dataclass(frozen=True, slots=True)
class DiffLine:
    kind: DiffKind
    label: str
    before: int
    after: int
    points_before: int
    points_after: int

    @property
    def points_delta(self) -> int:
        return self.points_after - self.points_before


@dataclass(frozen=True, slots=True)
class TaskDiff:
    """What changed between the suggestion and the actual list."""

    lines: tuple[DiffLine, ...]
    suggested_points: int
    actual_points: int

    @property
    def points_delta(self) -> int:
        return self.actual_points - self.suggested_points

    @property
    def changed(self) -> bool:
        return bool(self.lines)


def _label(item: DesignItem) -> str:
    if item.free:
        return f"{FREE_NAME} ({item.unit_points}đ)" + (f": {item.note}" if item.note else "")
    text = item.code if not item.name else f"{item.code} {item.name}"
    return text if item.form == DEFAULT_FORM else f"{text} · {item.form_label}"


def diff_items(suggested: Sequence[DesignItem], actual: Sequence[DesignItem]) -> TaskDiff:
    """Line by line: codes added, dropped, or with a different quantity. A
    different form or "Việc khác" points is a different line (one dropped,
    one added)."""
    before: dict[tuple[object, ...], DesignItem] = {}
    after: dict[tuple[object, ...], DesignItem] = {}
    for source, into in ((suggested, before), (actual, after)):
        for item in source:
            seen = into.get(item.key)
            into[item.key] = (
                item if seen is None else replace(seen, quantity=seen.quantity + item.quantity)
            )
    lines: list[DiffLine] = []
    for key, old in before.items():
        new = after.get(key)
        if new is None:
            lines.append(DiffLine("REMOVED", _label(old), old.quantity, 0, old.points, 0))
        elif new.quantity != old.quantity:
            lines.append(
                DiffLine("CHANGED", _label(new), old.quantity, new.quantity, old.points, new.points)
            )
    for key, new in after.items():
        if key not in before:
            lines.append(DiffLine("ADDED", _label(new), 0, new.quantity, 0, new.points))
    return TaskDiff(
        lines=tuple(lines),
        suggested_points=total_points(suggested),
        actual_points=total_points(actual),
    )


def describe_list(items: Sequence[DesignItem]) -> str:
    """``D4 Video 3-5 phút · After Effects (720)`` - a list with its points."""
    if not items:
        return "(trống)"
    parts = (
        _label(item) if item.quantity == 1 else f"{_label(item)} x{item.quantity}" for item in items
    )
    return ", ".join(parts) + f" ({total_points(items)}đ)"


def describe_change(suggested: Sequence[DesignItem], actual: Sequence[DesignItem]) -> str:
    """The lines the Trưởng phòng reads (history, Telegram, the approval box):

    ``Đề xuất: D3 … (240đ)`` / ``Nộp: D4 … · After Effects (720đ)`` /
    ``Chênh: +480 điểm``."""
    diff = diff_items(suggested, actual)
    sign = "+" if diff.points_delta >= 0 else ""
    return "\n".join(
        (
            f"Đề xuất: {describe_list(suggested)}",
            f"Nộp: {describe_list(actual)}",
            f"Chênh: {sign}{diff.points_delta} điểm",
        )
    )


__all__ = [
    "DEFAULT_FORM",
    "DEFAULT_TASK_FORMS",
    "FREE_CODES",
    "FREE_NAME",
    "LEGACY_DESIGN_CODES",
    "PHASE_LABELS",
    "SEED_TASK_CODES",
    "SINGLE_CODE_PHASES",
    "TASK_PHASES",
    "DiffLine",
    "SeedTaskCode",
    "TaskDiff",
    "TaskFormSpec",
    "describe_change",
    "describe_item",
    "describe_list",
    "diff_items",
    "form_specs",
    "forms_for",
    "is_single",
    "phase_label",
]


class _StoredForm(Protocol):
    @property
    def key(self) -> str: ...
    @property
    def label(self) -> str: ...
    @property
    def pct(self) -> int: ...
    @property
    def note(self) -> str: ...
    @property
    def phases(self) -> tuple[str, ...]: ...
    @property
    def active(self) -> bool: ...


def form_specs(raw: Iterable[_StoredForm]) -> tuple[TaskFormSpec, ...]:
    """The unit's stored forms (``UnitSettings.task_forms``) as specs. Unknown
    phases are dropped; an empty list falls back to the defaults."""
    specs: list[TaskFormSpec] = []
    for entry in raw:
        phases = tuple(
            OrderNodeType(value) for value in entry.phases if value in OrderNodeType.__members__
        )
        specs.append(
            TaskFormSpec(
                key=entry.key,
                label=entry.label,
                pct=entry.pct,
                note=entry.note or "",
                phases=phases,
                active=entry.active,
            )
        )
    return tuple(specs) or DEFAULT_TASK_FORMS
