"""Tài chính đợt 3: hồ sơ chi, its route and the finance roles (user 12/11/2026,
meeting answers F1-F8, P1-P6, B1-B7, M1-M7; plan §16).

A **route** (one per budget code, from the Sổ tay, editable): the purchase
approvals (Trưởng phòng / GĐ trung tâm, Giám đốc khối, Phòng Tài chính = CFO,
PTGĐ / CEO), who carries the purchase out (an execution step that records the
quote / order / handover and the acceptance - F3, not an approval), the
payment approvals (Trưởng phòng, GĐ khối, the accountant in charge, Kế toán
trưởng, PTGĐ / CEO) and who hands the money out (Quỹ cơ sở / Thủ quỹ VP), plus
the papers it needs.

**Roles** are given to people in the module (P1): ``DEPT_HEAD`` and
``BLOCK_DIRECTOR`` come from the org chart (the requester's head / their
block's office head, as the HR requests read it); every other role is an
assignment, optionally for one pháp nhân and / or one cơ sở, with a stand-in
(F7: one approver per step, chosen by the request's pháp nhân and cơ sở).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from meobot.domain.finance.codebook import ROWS


class FinanceRole(StrEnum):
    DEPT_HEAD = "DEPT_HEAD"
    BLOCK_DIRECTOR = "BLOCK_DIRECTOR"
    CFO = "CFO"
    CHIEF_ACCOUNTANT = "CHIEF_ACCOUNTANT"
    CEO = "CEO"
    ACC_WAREHOUSE = "ACC_WAREHOUSE"
    ACC_PAYROLL = "ACC_PAYROLL"
    ACC_MKT = "ACC_MKT"
    ACC_RECON = "ACC_RECON"
    ACC_PAYMENT = "ACC_PAYMENT"
    ACC_GENERAL = "ACC_GENERAL"
    ACC_TAX = "ACC_TAX"
    ACC_ASSET = "ACC_ASSET"
    FUND_BRANCH = "FUND_BRANCH"
    CASHIER_VP = "CASHIER_VP"
    REQUESTER = "REQUESTER"
    EXEC_HCMS = "EXEC_HCMS"
    EXEC_HR = "EXEC_HR"
    EXEC_DRIVER = "EXEC_DRIVER"
    EXEC_SECRETARY = "EXEC_SECRETARY"


ROLE_LABELS: Mapping[FinanceRole, str] = MappingProxyType(
    {
        FinanceRole.DEPT_HEAD: "Trưởng phòng / GĐ trung tâm",
        FinanceRole.BLOCK_DIRECTOR: "Giám đốc khối",
        FinanceRole.CFO: "Phòng Tài chính (CFO)",
        FinanceRole.CHIEF_ACCOUNTANT: "Kế toán trưởng",
        FinanceRole.CEO: "PTGĐ / CEO",
        FinanceRole.ACC_WAREHOUSE: "Kế toán kho VP",
        FinanceRole.ACC_PAYROLL: "Kế toán tiền lương",
        FinanceRole.ACC_MKT: "Kế toán MKT",
        FinanceRole.ACC_RECON: "Kế toán đối soát",
        FinanceRole.ACC_PAYMENT: "Kế toán thanh toán",
        FinanceRole.ACC_GENERAL: "Kế toán tổng hợp",
        FinanceRole.ACC_TAX: "Kế toán thuế",
        FinanceRole.ACC_ASSET: "Kế toán TSCĐ",
        FinanceRole.FUND_BRANCH: "Quỹ cơ sở",
        FinanceRole.CASHIER_VP: "Thủ quỹ VP",
        FinanceRole.REQUESTER: "Phòng ban đề xuất (người lập)",
        FinanceRole.EXEC_HCMS: "Hành chính mua sắm (HCMS)",
        FinanceRole.EXEC_HR: "HCNS",
        FinanceRole.EXEC_DRIVER: "Lái xe / điều xe",
        FinanceRole.EXEC_SECRETARY: "Ban thư ký",
    }
)
#: Read from the org chart, never assigned.
ORG_ROLES = frozenset({FinanceRole.DEPT_HEAD, FinanceRole.BLOCK_DIRECTOR, FinanceRole.REQUESTER})
#: Roles assigned in the module ("Cấu hình > Vai trò tài chính").
ASSIGNABLE = tuple(role for role in FinanceRole if role not in ORG_ROLES)
#: F6: who steps in, in order, when the requester sits on a step.
ESCALATION: tuple[FinanceRole, ...] = (
    FinanceRole.DEPT_HEAD,
    FinanceRole.BLOCK_DIRECTOR,
    FinanceRole.CFO,
    FinanceRole.CHIEF_ACCOUNTANT,
    FinanceRole.CEO,
)
ACCOUNTANTS = frozenset(
    {
        FinanceRole.ACC_WAREHOUSE,
        FinanceRole.ACC_PAYROLL,
        FinanceRole.ACC_MKT,
        FinanceRole.ACC_RECON,
        FinanceRole.ACC_PAYMENT,
        FinanceRole.ACC_GENERAL,
        FinanceRole.ACC_TAX,
        FinanceRole.ACC_ASSET,
    }
)

_ACCOUNTANT_WORDS: Mapping[str, FinanceRole] = MappingProxyType(
    {
        "kế toán kho vp": FinanceRole.ACC_WAREHOUSE,
        "kế toán tiền lương": FinanceRole.ACC_PAYROLL,
        "kế toán mkt": FinanceRole.ACC_MKT,
        "kế toán đối soát": FinanceRole.ACC_RECON,
        "kế toán thanh toán": FinanceRole.ACC_PAYMENT,
        "kế toán thanh toán vp": FinanceRole.ACC_PAYMENT,
        "kế toán tổng hợp": FinanceRole.ACC_GENERAL,
        "kế toán thuế": FinanceRole.ACC_TAX,
        "kế toán tscđ": FinanceRole.ACC_ASSET,
    }
)
_EXECUTOR_WORDS: Mapping[str, FinanceRole] = MappingProxyType(
    {
        "kế toán kho vp": FinanceRole.ACC_WAREHOUSE,
        "hcns": FinanceRole.EXEC_HR,
        "phòng ban đề xuất": FinanceRole.REQUESTER,
        "cơ sở": FinanceRole.REQUESTER,
        "điều xe": FinanceRole.EXEC_DRIVER,
        "lái xe": FinanceRole.EXEC_DRIVER,
        "hành chính mua sắm": FinanceRole.EXEC_HCMS,
        "hcms": FinanceRole.EXEC_HCMS,
        "ban thư ký": FinanceRole.EXEC_SECRETARY,
    }
)


class DocumentKind(StrEnum):
    PURCHASE_REQUEST = "PURCHASE_REQUEST"
    QUOTES = "QUOTES"
    BOD_DECISION = "BOD_DECISION"
    CONTRACT = "CONTRACT"
    PAYMENT_REQUEST = "PAYMENT_REQUEST"
    HANDOVER = "HANDOVER"
    ACCEPTANCE = "ACCEPTANCE"
    INVOICE = "INVOICE"
    OTHER = "OTHER"
    #: The bank transfer / cash slip of "Đã chi" (M1).
    PAYMENT_PROOF = "PAYMENT_PROOF"


DOCUMENT_LABELS: Mapping[DocumentKind, str] = MappingProxyType(
    {
        DocumentKind.PURCHASE_REQUEST: "Giấy đề nghị mua sắm",
        DocumentKind.QUOTES: "Báo giá cạnh tranh 3 NCC",
        DocumentKind.BOD_DECISION: "Quyết định của BGĐ",
        DocumentKind.CONTRACT: "Hợp đồng",
        DocumentKind.PAYMENT_REQUEST: "Giấy đề nghị thanh toán",
        DocumentKind.HANDOVER: "Biên bản bàn giao / Phiếu xuất kho",
        DocumentKind.ACCEPTANCE: "Biên bản nghiệm thu khối lượng",
        DocumentKind.INVOICE: "Hoá đơn VAT / Hoá đơn bán hàng",
        DocumentKind.OTHER: "Hồ sơ liên quan khác",
        DocumentKind.PAYMENT_PROOF: "Chứng từ chi (UNC / phiếu chi)",
    }
)
#: The Sổ tay's eight document columns, in order.
SHEET_DOCUMENTS: tuple[DocumentKind, ...] = (
    DocumentKind.PURCHASE_REQUEST,
    DocumentKind.QUOTES,
    DocumentKind.BOD_DECISION,
    DocumentKind.CONTRACT,
    DocumentKind.PAYMENT_REQUEST,
    DocumentKind.HANDOVER,
    DocumentKind.ACCEPTANCE,
    DocumentKind.INVOICE,
)


class Phase(StrEnum):
    PURCHASE = "PURCHASE"
    EXECUTE = "EXECUTE"
    PAYMENT = "PAYMENT"
    CASH = "CASH"


PHASE_LABELS: Mapping[Phase, str] = MappingProxyType(
    {
        Phase.PURCHASE: "Duyệt đề nghị mua sắm",
        Phase.EXECUTE: "Thực hiện mua sắm & nghiệm thu",
        Phase.PAYMENT: "Duyệt thanh toán",
        Phase.CASH: "Chi tiền",
    }
)


class RequestStatus(StrEnum):
    PENDING = "PENDING"
    RETURNED = "RETURNED"
    PAID = "PAID"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


STATUS_LABELS: Mapping[RequestStatus, str] = MappingProxyType(
    {
        RequestStatus.PENDING: "Đang xử lý",
        RequestStatus.RETURNED: "Trả lại để sửa",
        RequestStatus.PAID: "Đã chi",
        RequestStatus.REJECTED: "Không duyệt",
        RequestStatus.CANCELLED: "Đã huỷ",
    }
)


class StepStatus(StrEnum):
    WAITING = "WAITING"
    PENDING = "PENDING"
    DONE = "DONE"
    REJECTED = "REJECTED"
    RETURNED = "RETURNED"
    SKIPPED = "SKIPPED"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class Route:
    """What a budget code's request goes through."""

    purchase: tuple[FinanceRole, ...] = ()
    executor: FinanceRole | None = None
    payment: tuple[FinanceRole, ...] = ()
    cash: tuple[FinanceRole, ...] = ()
    documents: tuple[DocumentKind, ...] = ()
    documents_note: str = ""
    #: Paid without approval (F4: only "Phí ngân hàng").
    no_approval: bool = False
    #: The sheet's "Bộ phận đề xuất" columns, shown only.
    proposer: str = ""

    @property
    def empty(self) -> bool:
        return not (self.purchase or self.executor or self.payment or self.cash)

    def as_value(self) -> dict[str, Any]:
        return {
            "purchase": [role.value for role in self.purchase],
            "executor": self.executor.value if self.executor else None,
            "payment": [role.value for role in self.payment],
            "cash": [role.value for role in self.cash],
            "documents": [kind.value for kind in self.documents],
            "documents_note": self.documents_note,
            "no_approval": self.no_approval,
            "proposer": self.proposer,
        }

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | None) -> Route:
        data = dict(value or {})
        executor = data.get("executor")
        return cls(
            purchase=tuple(FinanceRole(item) for item in data.get("purchase") or []),
            executor=FinanceRole(executor) if executor else None,
            payment=tuple(FinanceRole(item) for item in data.get("payment") or []),
            cash=tuple(FinanceRole(item) for item in data.get("cash") or []),
            documents=tuple(DocumentKind(item) for item in data.get("documents") or []),
            documents_note=str(data.get("documents_note") or ""),
            no_approval=bool(data.get("no_approval")),
            proposer=str(data.get("proposer") or ""),
        )


@dataclass(frozen=True, slots=True)
class CodeSeed:
    code: str
    name: str
    parent: str | None
    kind: str
    leaf: bool
    route: Route = field(default_factory=Route)


def _flags(text: str, roles: Sequence[FinanceRole | None]) -> tuple[FinanceRole, ...]:
    return tuple(
        role for flag, role in zip(text, roles, strict=False) if flag == "1" and role is not None
    )


def _accountant(label: str) -> FinanceRole | None:
    return _ACCOUNTANT_WORDS.get(label.strip().lower())


def executor_of(label: str) -> FinanceRole | None:
    return _EXECUTOR_WORDS.get(label.strip().lower()) if label.strip() else None


def parse_codebook(rows: str = ROWS) -> list[CodeSeed]:
    """The Sổ tay's codes (``domain/finance/codebook.py``), parents first."""
    seeds: list[CodeSeed] = []
    for line in rows.strip().splitlines():
        parts = [part.strip() for part in line.split("|")]
        code, name = parts[0], parts[1]
        parent = code.rsplit(".", 1)[0] if code.count(".") > 1 else None
        if len(parts) == 2:
            seeds.append(CodeSeed(code, name, parent, "", leaf=False))
            continue
        kind = parts[2] if len(parts) > 2 else ""
        if len(parts) < 13:
            seeds.append(CodeSeed(code, name, parent, kind, leaf=True))
            continue
        proposer, purchase, executor, _pay_by, pay, accountant, chief, cash, docs = parts[3:12]
        note = parts[12] if len(parts) > 12 else ""
        payment = (
            *_flags(pay, (FinanceRole.DEPT_HEAD, FinanceRole.BLOCK_DIRECTOR)),
            *((_accountant(accountant),) if _accountant(accountant) else ()),
            *_flags(chief, (FinanceRole.CHIEF_ACCOUNTANT, FinanceRole.CEO)),
        )
        route = Route(
            purchase=_flags(
                purchase,
                (
                    FinanceRole.DEPT_HEAD,
                    FinanceRole.BLOCK_DIRECTOR,
                    FinanceRole.CFO,
                    FinanceRole.CEO,
                ),
            ),
            executor=executor_of(executor),
            payment=tuple(role for role in payment if role is not None),
            cash=_flags(cash, (FinanceRole.FUND_BRANCH, FinanceRole.CASHIER_VP)),
            documents=_flags(docs, SHEET_DOCUMENTS),  # type: ignore[arg-type]
            documents_note=note,
            proposer=proposer,
        )
        no_route = not (route.purchase or route.executor or route.payment)
        if no_route and route.cash and code == "CO.06.06.02":
            route = Route(
                cash=route.cash,
                documents=route.documents,
                documents_note=route.documents_note,
                no_approval=True,
            )
        elif no_route:
            route = Route(documents=route.documents, documents_note=route.documents_note)
        seeds.append(CodeSeed(code, name, parent, kind, leaf=True, route=route))
    return seeds


def escalate(role: FinanceRole, remaining: Sequence[FinanceRole]) -> FinanceRole | None:
    """F6: the next level after ``role`` not already further on in the phase."""
    start = (
        ESCALATION.index(role) + 1
        if role in ESCALATION
        else (
            ESCALATION.index(FinanceRole.CHIEF_ACCOUNTANT)
            if role in ACCOUNTANTS
            else len(ESCALATION)
        )
    )
    for candidate in ESCALATION[start:]:
        if candidate not in remaining:
            return candidate
    return None


def cash_role(route: Route, apm_branch: str | None) -> FinanceRole | None:
    """Quỹ cơ sở pays a request of a cơ sở (CS_*), Thủ quỹ VP the office's."""
    if not route.cash:
        return None
    at_branch = bool(apm_branch and apm_branch.startswith("CS"))
    if at_branch and FinanceRole.FUND_BRANCH in route.cash:
        return FinanceRole.FUND_BRANCH
    if FinanceRole.CASHIER_VP in route.cash:
        return FinanceRole.CASHIER_VP
    return route.cash[0]


__all__ = [
    "ACCOUNTANTS",
    "ASSIGNABLE",
    "DOCUMENT_LABELS",
    "ESCALATION",
    "ORG_ROLES",
    "PHASE_LABELS",
    "ROLE_LABELS",
    "SHEET_DOCUMENTS",
    "STATUS_LABELS",
    "CodeSeed",
    "DocumentKind",
    "FinanceRole",
    "Phase",
    "RequestStatus",
    "Route",
    "StepStatus",
    "cash_role",
    "escalate",
    "executor_of",
    "parse_codebook",
]
