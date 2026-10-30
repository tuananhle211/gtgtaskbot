"""Points earned outside the order flow (2026-10-30), with no I/O.

The points reports read two ledgers: ``order_node_points`` (ORD steps, 0074)
and ``work_points`` (everything else, 0085). A ``work_points`` row is written
by the flow that earns it and is keyed by ``source_key`` so it can be
rewritten, never doubled:

* ``PR_CONTENT`` - a PR piece's task codes, typed by whoever did the work once
  the phase is done and approved by a Trưởng phòng PR ("Nội dung" = the
  Biên kịch codes N…, "Sản xuất" = the Design T… and/or Dựng D… codes - the
  same catalogue as ORD);
* ``PR_PUBLISH`` - +10 for whoever records a publication with a link;
* ``SEEDING_PHONES`` - 200 per phone number a seeding member handed in and a
  Trưởng phòng PR approved (one row per person per day);
* ``SEEDING_OTHER`` - "Việc khác" a seeding member reported (work + points),
  once approved.

The PR workflow itself is untouched: these rows sit beside it.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from meobot.domain.orders.design_codes import DesignItem


class WorkPointSource(StrEnum):
    PR_CONTENT = "PR_CONTENT"
    PR_PUBLISH = "PR_PUBLISH"
    SEEDING_PHONES = "SEEDING_PHONES"
    SEEDING_OTHER = "SEEDING_OTHER"


#: Extra report phases beside BIEN_TAP / THIET_KE / DUNG.
PHASE_PUBLISH = "PUBLISH"
PHASE_SEEDING = "SEEDING"
PHASE_OTHER = "OTHER"

EXTRA_PHASE_LABELS: dict[str, str] = {
    PHASE_PUBLISH: "Đăng bài",
    PHASE_SEEDING: "Seeding",
    PHASE_OTHER: "Việc khác",
}

#: The report order of every phase.
PHASE_ORDER: tuple[str, ...] = (
    "BIEN_TAP",
    "THIET_KE",
    "DUNG",
    PHASE_PUBLISH,
    PHASE_SEEDING,
    PHASE_OTHER,
)

#: A PR piece's two stages and the catalogue phases each takes.
STAGE_CONTENT = "CONTENT"
STAGE_PRODUCTION = "PRODUCTION"
STAGE_PHASES: dict[str, tuple[str, ...]] = {
    STAGE_CONTENT: ("BIEN_TAP",),
    STAGE_PRODUCTION: ("THIET_KE", "DUNG"),
}
STAGE_LABELS: dict[str, str] = {STAGE_CONTENT: "Nội dung", STAGE_PRODUCTION: "Sản xuất"}
#: How a PR line's phase reads on the reports.
PR_PHASE_LABELS: dict[str, str] = {
    "BIEN_TAP": "Nội dung (PR)",
    "THIET_KE": "Thiết kế (PR)",
    "DUNG": "Dựng (PR)",
}

PUBLISH_POINTS = 10
PHONE_POINTS = 200

#: Stable ids for the synthetic "codes" of the new lines, so the code tables
#: group them like catalogue rows.
_NAMESPACE = uuid.UUID("4f1c2a6e-5b7d-4c8e-9a10-2b3c4d5e6f70")
PUBLISH_CODE_ID = uuid.uuid5(_NAMESPACE, "publish")
PHONE_CODE_ID = uuid.uuid5(_NAMESPACE, "seeding-phone")
OTHER_CODE_ID = uuid.uuid5(_NAMESPACE, "seeding-other")


def publish_item() -> DesignItem:
    return DesignItem(
        code_id=PUBLISH_CODE_ID,
        code="Đăng bài",
        name="Đăng bài có link",
        quantity=1,
        unit_points=PUBLISH_POINTS,
    )


def phone_item(quantity: int) -> DesignItem:
    return DesignItem(
        code_id=PHONE_CODE_ID,
        code="SĐT",
        name="Số điện thoại seeding",
        quantity=quantity,
        unit_points=PHONE_POINTS,
        unit_label="SĐT",
    )


def other_item(description: str, points: int) -> DesignItem:
    return DesignItem(
        code_id=OTHER_CODE_ID,
        code="Việc khác",
        name="Việc khác",
        quantity=1,
        unit_points=points,
        note=description,
        free=True,
    )


#: Sources that count as a finished task ("Task xong") on the reports.
STEP_SOURCES = frozenset({WorkPointSource.PR_CONTENT.value})


__all__ = [
    "EXTRA_PHASE_LABELS",
    "OTHER_CODE_ID",
    "PHASE_ORDER",
    "PHASE_OTHER",
    "PHASE_PUBLISH",
    "PHASE_SEEDING",
    "PHONE_CODE_ID",
    "PHONE_POINTS",
    "PR_PHASE_LABELS",
    "PUBLISH_CODE_ID",
    "PUBLISH_POINTS",
    "STAGE_CONTENT",
    "STAGE_LABELS",
    "STAGE_PHASES",
    "STAGE_PRODUCTION",
    "STEP_SOURCES",
    "WorkPointSource",
    "other_item",
    "phone_item",
    "publish_item",
]
