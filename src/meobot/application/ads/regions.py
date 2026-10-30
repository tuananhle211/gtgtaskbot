"""Doanh thu Marketing / Cơ sở theo miền và hiệu suất (2026-10-30).

The same revenue counted two ways, both adding up to the total:

* **Marketing** - by the team that brought the customer (the CRM account's
  region): HN = HN + HN chuyển SG, SG = SG + SG chuyển HN;
* **Cơ sở** - by the branch that served them: HN = HN + SG chuyển HN,
  SG = SG + HN chuyển SG.

The cost is split by the ad account's cơ sở (its BM's site, else the typed-in
account's: "Hà Nội" -> HN, "Sài Gòn" -> SG). Hiệu suất of a region = its
marketing cost (charges, else spend) ÷ its Marketing revenue without VAT
(``commission_revenue``) - "Chi phí Marketing HN / Doanh thu HN + HN chuyển SG".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from meobot.application.ads.revenue_service import RevenueRow
from meobot.domain.ads.crm import CrmRegion, branch_site
from meobot.domain.ads.models import Period, commission_revenue, ratio

#: Marketing (team) and Cơ sở (branch) per region, from the four flows.
MARKETING_FLOWS = {CrmRegion.HN: ("hn_hn", "hn_hcm"), CrmRegion.HCM: ("hcm_hcm", "hcm_hn")}
SITE_FLOWS = {CrmRegion.HN: ("hn_hn", "hcm_hn"), CrmRegion.HCM: ("hcm_hcm", "hn_hcm")}


def place_region(site: str | None) -> CrmRegion | None:
    """An ad account's cơ sở ("Hà Nội", "Sài Gòn") -> its region."""
    return branch_site(site)


@dataclass(slots=True)
class RegionFigures:
    #: Doanh thu Marketing (by team) and Cơ sở (by branch), VAT included.
    marketing: float = 0.0
    site: float = 0.0
    #: The Marketing revenue without VAT, what hiệu suất divides.
    marketing_net: float = 0.0
    spend: float = 0.0
    invoice: float = 0.0

    @property
    def charged(self) -> float:
        return self.invoice or self.spend

    @property
    def ads_ratio(self) -> float | None:
        """Hiệu suất: marketing cost ÷ Marketing revenue (hoa hồng)."""
        return ratio(self.charged, self.marketing_net) if self.marketing_net else None


@dataclass(slots=True)
class RegionSplit:
    hn: RegionFigures = field(default_factory=RegionFigures)
    hcm: RegionFigures = field(default_factory=RegionFigures)
    #: Cost on ad accounts whose cơ sở is unknown.
    other_spend: float = 0.0
    other_invoice: float = 0.0
    #: The four flows (``FLOWS``) over the period.
    flows: dict[str, float] = field(default_factory=dict)

    def of(self, region: CrmRegion | None) -> RegionFigures | None:
        if region is CrmRegion.HN:
            return self.hn
        if region is CrmRegion.HCM:
            return self.hcm
        return None


def region_split(
    revenue_rows: Iterable[RevenueRow],
    costs: Iterable[tuple[date, CrmRegion | None, float, float]],
    period: Period,
) -> RegionSplit:
    """``costs`` = (day, the account's region, spend, invoice) per row."""
    split = RegionSplit(flows=dict.fromkeys(("hcm_hcm", "hn_hn", "hn_hcm", "hcm_hn"), 0.0))
    for row in revenue_rows:
        if not period.start <= row.day <= period.end:
            continue
        flows = row.flows()
        for flow, value in flows.items():
            split.flows[flow] += float(value)
        for team, figures in ((CrmRegion.HN, split.hn), (CrmRegion.HCM, split.hcm)):
            brought = sum((flows[flow] for flow in MARKETING_FLOWS[team]), Decimal(0))
            figures.marketing += float(brought)
            figures.marketing_net += float(commission_revenue(brought, row.day))
            figures.site += float(sum((flows[flow] for flow in SITE_FLOWS[team]), Decimal(0)))
    for day, place, spend, invoice in costs:
        if not period.start <= day <= period.end:
            continue
        target = split.of(place)
        if target is None:
            split.other_spend += spend
            split.other_invoice += invoice
        else:
            target.spend += spend
            target.invoice += invoice
    return split


__all__ = [
    "MARKETING_FLOWS",
    "SITE_FLOWS",
    "RegionFigures",
    "RegionSplit",
    "place_region",
    "region_split",
]
