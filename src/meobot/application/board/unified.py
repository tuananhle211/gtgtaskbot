"""One column layout for both units on the "Tất cả" tab.

PR keeps five phases and Ads three nodes plus its final review; side by side
in one table that is two different sets of columns under one header. The merged view rewrites every
row's cells into the same five:

    Order · Biên kịch · Thiết kế · Dựng / Sản xuất · Duyệt final

* **Order** - Ads: the order and its approval by the head. PR: the piece being
  written and its script review (Trưởng nhóm / Trưởng phòng), which is PR's
  equivalent of having the order approved.
* **Biên kịch**, **Thiết kế** - Ads nodes. PR has neither: "—".
* **Dựng / Sản xuất** - Ads "Dựng"; PR production.
* **Duyệt final** - Ads: the gates the product goes through once the last
  production node is done (the script lead's video review where it applies,
  then the orderer's final review); PR internal review and publishing.

Read-only reshaping of rows the sources already built: no rule is decided here.
"""

from __future__ import annotations

from dataclasses import replace

from meobot.domain.board.models import Phase, TaskCell, TaskRow
from meobot.domain.units.models import UnitCode

UNIFIED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("ORDER", "Order"),
    ("BIEN_TAP", "Biên kịch"),
    ("THIET_KE", "Thiết kế"),
    ("PRODUCTION", "Dựng / Sản xuất"),
    ("FINAL", "Duyệt final"),
)
_LABELS = dict(UNIFIED_COLUMNS)


def _relabel(cell: TaskCell | None, key: str) -> TaskCell:
    if cell is None:
        return _absent(key)
    return replace(cell, key=key, label=_LABELS[key])


def _absent(key: str) -> TaskCell:
    """A step this unit's process does not have."""
    return TaskCell(
        key=key,
        label=_LABELS[key],
        person_name=None,
        status="BO_QUA",
        status_label="—",
        is_current=False,
    )


def _ads_order(row: TaskRow) -> TaskCell:
    if row.status in ("ORDER_PENDING", "ORDER_RETURNED"):
        returned = row.status == "ORDER_RETURNED"
        return TaskCell(
            key="ORDER",
            label=_LABELS["ORDER"],
            person_name=row.current_person_name,
            status="DANG_SUA" if returned else "CHO_DUYET",
            status_label="Trả sửa" if returned else "Chờ duyệt order",
            is_current=True,
            since=row.stage_since,
        )
    if row.phase is Phase.CANCELLED and row.status == "CANCELLED":
        state, label = "BO_QUA", "Đã huỷ"
    else:
        state, label = "HOAN_THANH", "Đã duyệt"
    return TaskCell(
        key="ORDER",
        label=_LABELS["ORDER"],
        person_name=row.owner_name,
        status=state,
        status_label=label,
        is_current=False,
    )


def _pr_order(cells: dict[str, TaskCell], row: TaskRow) -> TaskCell:
    # While the script is under review the review cell is the live one.
    if row.phase is Phase.REVIEW and "REVIEW" in cells:
        return _relabel(cells["REVIEW"], "ORDER")
    return _relabel(cells.get("ORDER"), "ORDER")


def unify_row(row: TaskRow) -> TaskRow:
    """The row with its cells rewritten into :data:`UNIFIED_COLUMNS`."""
    cells = {cell.key: cell for cell in row.cells}
    if row.unit is UnitCode.ADS:
        unified = (
            _ads_order(row),
            _relabel(cells.get("BIEN_TAP"), "BIEN_TAP"),
            _relabel(cells.get("THIET_KE"), "THIET_KE"),
            _relabel(cells.get("DUNG"), "PRODUCTION"),
            _relabel(cells.get("FINAL"), "FINAL"),
        )
    else:
        unified = (
            _pr_order(cells, row),
            _absent("BIEN_TAP"),
            _absent("THIET_KE"),
            _relabel(cells.get("PRODUCTION"), "PRODUCTION"),
            _relabel(cells.get("FINAL_REVIEW"), "FINAL"),
        )
    return replace(row, cells=unified)


__all__ = ["UNIFIED_COLUMNS", "unify_row"]
