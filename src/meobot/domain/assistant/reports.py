"""The report assistant on Telegram ("Trợ lý AI", 2026-10-31).

Plan: ``docs/ai-assistant/BOT_REPORT_ASSISTANT_PLAN.md``. Somebody the OWNER
ticks asks the bot for the company's reports - Ads revenue, KPI and budgets,
points, orders, resources, PR - and is answered **from the reports' own
numbers**, never from a model's guess:

* the permission is a module of its own (``ASSISTANT`` / ``CHAT``), held by
  the OWNER by role and granted per person by the OWNER alone - not an ADMIN,
  no department cell; who holds it sees the **whole company**;
* every figure comes from a read-only report tool whose message is written by
  the tool from the very numbers it returns; the model only picks the tool and
  its few arguments;
* a question that asks for figures but names no report is asked back, never
  answered from general knowledge;
* the assistant speaks to its holders as "em" to "sếp" (2026-10-31) - in the
  model's turns and in every sentence written here or by the report tools.

This module holds the vocabulary and the word-matching that keep the
assistant working when the model is not: which report a question is about,
whether it asks for figures at all, and which month it names. Pure, no I/O,
accent-folded like the other conversation patterns.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from types import MappingProxyType

from meobot.domain.member.normalization import strip_accents as fold

MODULE_ASSISTANT = "ASSISTANT"


#: How the assistant speaks to whoever holds it: it says "em", they are "sếp".
#: Overrides a stored ``/set_preferred_address`` while the permission is held.
ASSISTANT_SELF_ADDRESS = "em"
ASSISTANT_USER_ADDRESS = "sếp"


class AssistantPermission(StrEnum):
    #: Chat with the report assistant on Telegram - every report, company-wide.
    CHAT = "CHAT"


#: Held by the OWNER alone by role, and granted by the OWNER alone.
ASSISTANT_OWNER_ONLY: frozenset[str] = frozenset({AssistantPermission.CHAT.value})

ASSISTANT_PERMISSION_LABELS: Mapping[AssistantPermission, str] = MappingProxyType(
    {AssistantPermission.CHAT: "Chat trợ lý AI trên Telegram (xem báo cáo toàn công ty)"}
)


def assistant_allowed_scopes(_permission: AssistantPermission) -> tuple[str, ...]:
    """Ticked = the whole company: there is no "own" report assistant."""
    return ("NONE", "ALL")


# --- the report tools ------------------------------------------------------------


class ReportTool(StrEnum):
    COMPANY = "report.company"
    ADS_REVENUE = "report.ads_revenue"
    ADS_KPI_BUDGET = "report.ads_kpi_budget"
    PERFORMANCE = "report.performance"
    ORDERS = "report.orders"
    RESOURCES = "report.resources"
    PR = "report.pr"


REPORT_TOOLS: frozenset[str] = frozenset(item.value for item in ReportTool)

#: What each report covers, for the "which one?" question and the tool list.
REPORT_AREAS: Mapping[ReportTool, str] = MappingProxyType(
    {
        ReportTool.COMPANY: "tổng hợp toàn công ty",
        ReportTool.ADS_REVENUE: "doanh thu Ads, chi phí, Ads / Doanh thu",
        ReportTool.ADS_KPI_BUDGET: "KPI marketing và ngân sách tuần",
        ReportTool.PERFORMANCE: "điểm hiệu suất, định mức",
        ReportTool.ORDERS: "order (task ORD): chờ duyệt, trễ hạn, đang làm",
        ReportTool.RESOURCES: "tài nguyên: cảnh báo, đề xuất mua",
        ReportTool.PR: "task PR, bài đã đăng",
    }
)

#: Phrases per report, accent-folded; ``=word`` matches a whole word only.
#: ``strong`` phrases are reports by themselves ("doanh thu", "KPI", "trễ
#: hạn"); ``weak`` ones are everyday words too ("video", "task", "điểm") and
#: make a report question only beside a figures word ("bao nhiêu", "tình
#: hình"...) - "viết 3 ý tưởng video" stays a conversation. Strong phrases are
#: looked for first, then weak ones, each in this order: the first match wins,
#: so the narrower report comes before the wider one ("hiệu suất Ads" is Ads,
#: "điểm hiệu suất phòng PR" is points, "task PR" is PR's board, "task" alone
#: ORD's).
_TOPICS: tuple[tuple[ReportTool, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ReportTool.COMPANY,
        (
            "tong hop",
            "toan cong ty",
            "tinh hinh cong ty",
            "ca cong ty",
            "tat ca cac mang",
            "a-z",
            "a den z",
        ),
        (),
    ),
    (ReportTool.ADS_KPI_BUDGET, ("kpi", "ngan sach"), ("budget",)),
    (
        ReportTool.ADS_REVENUE,
        (
            "doanh thu",
            "doanh so",
            "thuc thu",
            "chi phi ads",
            "chi phi quang cao",
            "chi tieu ads",
            "hieu suat ads",
            "ads/dt",
            "ads / dt",
            "lap hoa don",
        ),
        ("chi tieu", "quang cao", "=ads", "=crm"),
    ),
    (
        ReportTool.PERFORMANCE,
        ("diem hieu suat", "dinh muc", "he so diem"),
        ("=diem", "hieu suat"),
    ),
    (
        ReportTool.RESOURCES,
        ("tai nguyen", "de xuat mua"),
        ("=tkqc", "=via"),
    ),
    (ReportTool.PR, ("bai da dang",), ("bai dang", "dang bai", "seeding", "=pr")),
    (
        ReportTool.ORDERS,
        ("tre han", "qua han", "cho phan cong"),
        ("order", "task", "cho duyet", "dang lam", "dang sua", "video"),
    ),
)

#: Words that ask for figures. A model must not answer one of these from the
#: chat branch, where there are no numbers: the question is asked back.
_FIGURE_WORDS: tuple[str, ...] = (
    "bao nhieu",
    "so lieu",
    "con so",
    "thong ke",
    "ty le",
    "tong cong",
)

#: What else makes an everyday word a report question ("tình hình order").
_REPORT_WORDS: tuple[str, ...] = (
    *_FIGURE_WORDS,
    "tinh hinh",
    "bao cao",
    "dat chua",
    "ra sao",
)

_TOKEN = re.compile(r"[a-z0-9]+")


def _matches(phrases: tuple[str, ...], folded: str, words: set[str]) -> bool:
    return any(
        phrase[1:] in words if phrase.startswith("=") else phrase in folded for phrase in phrases
    )


def report_topic(message: str) -> ReportTool | None:
    """The report a question is about, by its words; ``None`` when it is not
    a report question or is unsure."""
    folded = fold(message)
    words = set(_TOKEN.findall(folded))
    for tool, strong, _weak in _TOPICS:
        if _matches(strong, folded, words):
            return tool
    if not any(word in folded for word in _REPORT_WORDS):
        return None
    for tool, _strong, weak in _TOPICS:
        if _matches(weak, folded, words):
            return tool
    return None


def asks_for_figures(message: str) -> bool:
    folded = fold(message)
    return any(word in folded for word in _FIGURE_WORDS)


_ISO_MONTH = re.compile(r"\b(20\d{2})-(\d{1,2})\b")
_MONTH_YEAR = re.compile(r"\b(?:thang\s*)?(\d{1,2})\s*[/-]\s*(20\d{2})\b")
_MONTH = re.compile(r"\bthang\s*(\d{1,2})\b")


def month_in(message: str, today: date) -> str | None:
    """The month a question names, as ``YYYY-MM``; ``None`` = not named (the
    reports then take this month). "tháng này", "tháng trước", "tháng 9",
    "tháng 9/2026", "09/2026", "2026-09". A bare month after this one is last
    year's: in October, "tháng 12" is December just gone."""
    folded = fold(message)
    if "thang truoc" in folded:
        year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        return f"{year:04d}-{month:02d}"
    if "thang nay" in folded:
        return f"{today.year:04d}-{today.month:02d}"
    for pattern in (_ISO_MONTH, _MONTH_YEAR):
        found = pattern.search(folded)
        if found is None:
            continue
        first, second = int(found.group(1)), int(found.group(2))
        year, month = (first, second) if pattern is _ISO_MONTH else (second, first)
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}"
    found = _MONTH.search(folded)
    if found is not None:
        month = int(found.group(1))
        if 1 <= month <= 12:
            year = today.year if month <= today.month else today.year - 1
            return f"{year:04d}-{month:02d}"
    return None


def area_question() -> str:
    """What the assistant asks when a figures question names no report."""
    areas = "\n".join(f"- {label}" for label in REPORT_AREAS.values())
    return (
        "Dạ, em chỉ đưa số liệu lấy thẳng từ báo cáo của hệ thống, không ước đoán. "
        "Sếp muốn xem mảng nào ạ?\n"
        f"{areas}\n"
        'Ví dụ: "doanh thu tháng này", "order trễ hạn", "điểm hiệu suất tháng 9".'
    )


__all__ = [
    "ASSISTANT_OWNER_ONLY",
    "ASSISTANT_PERMISSION_LABELS",
    "ASSISTANT_SELF_ADDRESS",
    "ASSISTANT_USER_ADDRESS",
    "MODULE_ASSISTANT",
    "REPORT_AREAS",
    "REPORT_TOOLS",
    "AssistantPermission",
    "ReportTool",
    "area_question",
    "asks_for_figures",
    "assistant_allowed_scopes",
    "month_in",
    "report_topic",
]
