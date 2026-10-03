"""Work-type taxonomy rules. M2.5.

M1 gave the department a table to put its taxonomy in and M2 taught quotas to
read it. Neither gave anybody a way to *own* it: ``pr_work_types`` shipped
empty, and the only route into it was one API call nobody in production had
reason to make. This module holds the two decisions that turn the table into a
business taxonomy - which fields stop being editable once history exists, and
what a department starts with.

Why a structural lock rather than a warning
--------------------------------------------

A work type's ``code`` and ``default_quota_basis`` are what past rows *mean*.
``SEEDING_COMMENT`` measured by ``QUANTITY`` in ``COMMENT`` says a filed row of
``120`` is a hundred and twenty comments; the same type switched to
``ITEM_COUNT`` would say it is one job, and every historical row would quietly
change what it claims - including rows inside months somebody has already
reported and been assessed on.

So the rule is not "warn and proceed". Once a type has been used, those two
fields are refused, and the answer to a genuine change of measurement model is a
**new type** beside the old one, with the old one deactivated. That keeps both
meanings intact and dated, which is the only version of this that survives being
asked about a year later.

``name``, ``description`` and ``category`` stay editable for the same reason,
read the other way round: nothing authorises, counts or groups on them at
decision time. Renaming "Comment seeding" to "Seeding bình luận" changes what a
screen says and nothing about what a number means.
"""

from __future__ import annotations

from dataclasses import dataclass

from meobot.domain.pr.work import PrWorkCategory, PrWorkUnit
from meobot.domain.pr.work_quota import PrWorkQuotaBasis

#: The fields a used work type refuses. See the module docstring - each one is
#: something a historical row's meaning is read out of, not a label on it.
#:
#: ``category`` is deliberately **absent**: M1 kept it immutable alongside these
#: three, and M2.5 moves it to the safe side on purpose. A category is how a
#: report *groups* rows, not what a row measured, and a department that decides
#: research belongs under its own heading is re-grouping a screen rather than
#: rewriting what was counted.
#:
#: ``default_unit`` left this set with the period-container patch. The unit is
#: what a result is **called** - "khách hàng", "kịch bản" - and every row that
#: stores an amount stores its own copy of the unit it was written with, so
#: renaming the type's unit rewrites no historical figure. What it changes is
#: how open streams and draft plans read from now on, and that is exactly what
#: an administrator correcting "sản phẩm" to "khách hàng" is asking for. The
#: measurement model - ``default_quota_basis`` - stays locked, because it is
#: what a historical number *means*, not what it is called.
WORK_TYPE_STRUCTURAL_FIELDS: frozenset[str] = frozenset({"code", "default_quota_basis"})

#: The reason code a refused structural edit carries, and the string the
#: frontend maps to Vietnamese. Named once so the router, the service and the
#: tests cannot drift.
WORK_TYPE_STRUCTURE_LOCKED = "work_type_structure_locked"


@dataclass(frozen=True, slots=True)
class BootstrapWorkType:
    """One row of the starting taxonomy.

    A plain value, not a model: this is the *proposal* a bootstrap run makes,
    and what lands in the table is an ordinary row the owner may then rename,
    recategorise or deactivate like any other.
    """

    code: str
    name: str
    category: PrWorkCategory
    default_quota_basis: PrWorkQuotaBasis = PrWorkQuotaBasis.ITEM_COUNT
    default_unit: PrWorkUnit = PrWorkUnit.ITEM
    display_order: int = 0


#: The conservative V1 taxonomy, ordered as a person reads it.
#:
#: **Bootstrap data, not source-code truth.** Nothing in the codebase branches on
#: these codes; they are a first draft the department edits. In particular
#: ``OTHER_OPERATIONAL`` is a *fallback somebody chooses*, never a default the
#: code falls back to - work landing there in bulk is the signal that a type is
#: missing, and a default would hide exactly that signal.
BOOTSTRAP_WORK_TYPES: tuple[BootstrapWorkType, ...] = (
    # --- Nội dung ---------------------------------------------------------
    BootstrapWorkType(
        code="SHORT_VIDEO_SCRIPT",
        name="Kịch bản video ngắn",
        category=PrWorkCategory.CONTENT,
        display_order=10,
    ),
    BootstrapWorkType(
        code="CONTENT_SCRIPT",
        name="Kịch bản nội dung",
        category=PrWorkCategory.CONTENT,
        display_order=20,
    ),
    BootstrapWorkType(
        code="CONTENT_RESEARCH",
        name="Research nội dung",
        category=PrWorkCategory.CONTENT,
        display_order=30,
    ),
    # --- Sản xuất ---------------------------------------------------------
    BootstrapWorkType(
        code="VIDEO_EDIT",
        name="Dựng video",
        category=PrWorkCategory.PRODUCTION,
        display_order=10,
    ),
    BootstrapWorkType(
        code="TREND_VIDEO_EDIT",
        name="Dựng video trend",
        category=PrWorkCategory.PRODUCTION,
        display_order=20,
    ),
    BootstrapWorkType(
        code="HALF_DAY_SHOOT",
        name="Quay nửa ngày",
        category=PrWorkCategory.PRODUCTION,
        display_order=30,
    ),
    BootstrapWorkType(
        code="FULL_DAY_SHOOT",
        name="Quay cả ngày",
        category=PrWorkCategory.PRODUCTION,
        display_order=40,
    ),
    BootstrapWorkType(
        code="THUMBNAIL_DESIGN",
        name="Thiết kế thumbnail",
        category=PrWorkCategory.PRODUCTION,
        display_order=50,
    ),
    # --- Xuất bản ---------------------------------------------------------
    BootstrapWorkType(
        code="POST_PUBLISH",
        name="Đăng bài / xuất bản",
        category=PrWorkCategory.DISTRIBUTION,
        display_order=10,
    ),
    # --- Seeding ----------------------------------------------------------
    # The two QUANTITY types, and the reason the basis is not guessed from the
    # unit: "100 comments" is one job and a hundred quota units, and filing it
    # as a hundred rows would be the same work counted the shape that pays best.
    BootstrapWorkType(
        code="SEEDING_COMMENT",
        name="Comment seeding",
        category=PrWorkCategory.COMMUNITY,
        default_quota_basis=PrWorkQuotaBasis.QUANTITY,
        default_unit=PrWorkUnit.COMMENT,
        display_order=10,
    ),
    BootstrapWorkType(
        code="ACCOUNT_CARE",
        name="Nuôi / chăm sóc tài khoản",
        category=PrWorkCategory.COMMUNITY,
        default_quota_basis=PrWorkQuotaBasis.QUANTITY,
        default_unit=PrWorkUnit.ACCOUNT,
        display_order=20,
    ),
    # --- PR / Sự kiện -----------------------------------------------------
    BootstrapWorkType(
        code="PR_EVENT",
        name="PR / Sự kiện",
        category=PrWorkCategory.PR_EVENT,
        display_order=10,
    ),
    # --- Vận hành ---------------------------------------------------------
    BootstrapWorkType(
        code="OTHER_OPERATIONAL",
        name="Công việc vận hành khác",
        category=PrWorkCategory.OPERATIONS,
        display_order=90,
    ),
)
