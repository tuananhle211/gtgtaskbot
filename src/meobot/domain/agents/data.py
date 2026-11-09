"""What APM data each Hermes agent may read (2026-11-09).

The agents on their VPS ask APM for figures through the brain-mcp tool
``apm_bao_cao``, which calls ``/api/agents/gateway`` with the agent's name. A
figure comes from the same reports the Telegram assistant gives the bosses
(:class:`meobot.application.assistant.reports.AssistantReportService`), so an
agent and a boss never read two different numbers for the same thing.

Access is decided **per agent** (the user's choice): an agent reads the areas
ticked for it, whoever chats with it - so who may chat with an agent ("Ai thấy
agent nào") is what keeps a figure inside the right department. The OWNER
changes the ticks on Quản trị > Đội agent; until then :data:`DEFAULT_DATA_AREAS`.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class DataArea(StrEnum):
    """A kind of APM report an agent may read."""

    COMPANY = "COMPANY"
    ADS_REVENUE = "ADS_REVENUE"
    ADS_KPI_BUDGET = "ADS_KPI_BUDGET"
    PERFORMANCE = "PERFORMANCE"
    ORDERS = "ORDERS"
    PR = "PR"
    RESOURCES = "RESOURCES"


#: (label, what it holds) for each area - shown on the admin grid and to the agent.
DATA_AREA_INFO: Mapping[DataArea, tuple[str, str]] = MappingProxyType(
    {
        DataArea.COMPANY: (
            "Tổng công ty",
            "Mọi chỉ số chính trong một báo cáo: doanh thu Ads, order, điểm hiệu suất, PR, "
            "tài nguyên.",
        ),
        DataArea.ADS_REVENUE: (
            "Doanh thu Ads",
            "Doanh thu (thực thu CRM), DT hoa hồng, chi phí quảng cáo, Ads/DT, SĐT, KPI team, "
            "Marketing / Cơ sở HN-SG; theo tháng, ngày, khoảng ngày hoặc một marketer.",
        ),
        DataArea.ADS_KPI_BUDGET: (
            "KPI & ngân sách Ads",
            "KPI doanh thu từng marketer và ngân sách tuần (chi tiêu so với ngân sách).",
        ),
        DataArea.PERFORMANCE: (
            "Điểm hiệu suất",
            "Điểm theo tháng: toàn công ty, một phòng ban hoặc một người; định mức, trễ hạn, "
            "bị trả lại.",
        ),
        DataArea.ORDERS: (
            "Order sản xuất",
            "Order theo trạng thái (chờ duyệt, đang làm, đang sửa…), order trễ hạn, order "
            "trong tháng; liệt kê order ở một trạng thái.",
        ),
        DataArea.PR: (
            "PR",
            "Task PR đang mở theo pha, nội dung tạo / hoàn thành, số bài đã đăng trong tháng.",
        ),
        DataArea.RESOURCES: (
            "Tài nguyên",
            "Via / Page / TKQC / BM: số lượng, trạng thái, cảnh báo, đề xuất mua đang mở.",
        ),
    }
)

_ALL = tuple(DataArea)
_ADS = (DataArea.ADS_REVENUE, DataArea.ADS_KPI_BUDGET)
_PRODUCTION = (DataArea.ORDERS, DataArea.PERFORMANCE)

#: Until the OWNER changes them: the board reads everything, each agent its
#: own work; the agents that talk to customers (telesale, CSKH, chat page) and
#: Pháp chế read nothing.
DEFAULT_DATA_AREAS: Mapping[str, tuple[DataArea, ...]] = MappingProxyType(
    {
        "chutich": _ALL,
        "ceo": _ALL,
        "cfo": (DataArea.COMPANY, *_ADS, DataArea.RESOURCES),
        "cco": (*_ADS, DataArea.ORDERS),
        "cmo": (*_ADS, DataArea.ORDERS, DataArea.PR, DataArea.PERFORMANCE),
        "mktads": (*_ADS, DataArea.RESOURCES),
        "mktnghiencuu": (DataArea.ADS_REVENUE,),
        "mktnoidung": (*_PRODUCTION, DataArea.PR),
        "mktthietke": _PRODUCTION,
        "mktdung": _PRODUCTION,
        "nhansu": (DataArea.PERFORMANCE,),
    }
)


def default_areas(slug: str) -> tuple[DataArea, ...]:
    return DEFAULT_DATA_AREAS.get(slug, ())


def clean_areas(values: object) -> tuple[DataArea, ...]:
    """Known areas in the catalogue's order, each once (anything else dropped)."""
    wanted = {str(value).upper() for value in values} if isinstance(values, list | tuple) else set()
    return tuple(area for area in DataArea if area.value in wanted)


__all__ = [
    "DATA_AREA_INFO",
    "DEFAULT_DATA_AREAS",
    "DataArea",
    "clean_areas",
    "default_areas",
]
