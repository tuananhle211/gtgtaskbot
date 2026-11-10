"""Hồ sơ nhân sự (0096, ``docs/hr/HR_MODULE_PLAN.md`` §5): read, list, edit.

What a reader is shown is decided per person by :class:`HrAccess`: the work
information to whoever may view the profile, the sensitive blocks (personal
details, pay, insurance & tax, documents) only to oneself and the
``SENSITIVE`` holders. A sensitive read of somebody else is audited.

The ID number, bank account and pay are sealed in ``hr_profiles.secret`` (one
JSON, AAD ``hr_profile:<user id>``); a contract's salary in
``hr_contracts.salary_secret``. Without an encryption key they cannot be saved
(422 ``hr_secret_not_configured``) and read back as hidden.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.account.member_code import write_member_code
from meobot.application.account.passwords import generate_temporary_password, hash_password
from meobot.application.audit_service import AuditService
from meobot.application.hr.access import HrAccess, HrForbiddenError, HrNotFoundError
from meobot.application.org.departments import seat_person, sync_stream_tags
from meobot.application.org.seats import load_seats
from meobot.application.resources.employment import apply_employment_status
from meobot.core.config import Settings
from meobot.core.errors import ValidationError
from meobot.core.secrets import SecretBox, build_secret_box
from meobot.db.models.department import Department
from meobot.db.models.hr_profile import (
    HrChangeRequest,
    HrContract,
    HrContractType,
    HrDocument,
    HrProfile,
)
from meobot.db.models.legal_entity import LegalEntity
from meobot.db.models.system_setting import SystemSetting
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.hr.profiles import (
    CONTRACT_STATE_LABELS,
    CONTRACT_TYPE_SEED,
    DEFAULT_REQUIRED_DOCUMENTS,
    DEFAULT_WARN_DAYS,
    DOCUMENT_KINDS,
    GENDERS,
    PAPER_DOCUMENTS,
    ContractState,
    RequestStatus,
    age,
    contract_end,
    contract_state,
    seniority,
)
from meobot.domain.identity.models import Actor, Role
from meobot.domain.org.models import BRANCHES, POSITION_LABELS, Position, Seat
from meobot.domain.resources.models import EMPLOYMENT_STATUS_LABELS, EmploymentStatus
from meobot.domain.units.member_code import normalise_member_code

SETTINGS_KEY = "hr"
#: How far ahead the overview lists contracts ending (the mock's 60 days).
EXPIRING_DAYS = 60


class HrValidationError(ValidationError):
    code = "hr_invalid"

    def __init__(self, reason: str, message: str, *, field: str | None = None) -> None:
        super().__init__(message, details={"reason": reason, **({"field": field} if field else {})})
        self.code = reason


# --- what may be written ------------------------------------------------------------------

#: Plain profile columns, by kind. The work ones are seen by any viewer.
WORK_TEXT = (
    "grade",
    "work_form",
    "work_email",
    "leave_reason",
    "note",
    "team",
    "fingerprint_id",
    "fingerprint_floor",
)
WORK_DATES = ("joined_on", "official_on", "left_on", "fingerprint_on")
PERSONAL_TEXT = (
    "birth_place",
    "nationality",
    "ethnicity",
    "religion",
    "marital_status",
    "id_issued_by",
    "phone",
    "personal_email",
    "drive_email",
    "permanent_address",
    "current_address",
    "bank_name",
    "bank_holder",
    "bank2_name",
    "bank2_holder",
    "social_insurance_no",
    "insurance_title",
    "health_insurance_no",
    "tax_code",
    "scan_link",
)
PERSONAL_DATES = ("birth_date", "id_issued_on", "social_insurance_from")
#: Sealed in ``secret``.
SECRET_TEXT = ("id_number", "bank_account", "bank_account_2", "salary_type", "salary_note")
SECRET_MONEY = ("base_salary", "allowance", "insurance_salary")
#: On ``users``.
ACCOUNT_FIELDS = ("full_name", "job_title", "branch", "legal_entity_id", "employment_status")
BULK_FIELDS = (
    "job_title",
    "branch",
    "legal_entity_id",
    "employment_status",
    "grade",
    "work_form",
)
TEXT_LIMITS = {
    "note": 2000,
    "leave_reason": 1000,
    "permanent_address": 500,
    "current_address": 500,
    "salary_note": 500,
    "scan_link": 1000,
}


def _clean(value: object, field: str) -> str | None:
    text = " ".join(str(value or "").split()) if field not in TEXT_LIMITS else str(value or "")
    text = text.strip()
    limit = TEXT_LIMITS.get(field, 200)
    if len(text) > limit:
        raise HrValidationError("hr_value_too_long", f"Tối đa {limit} ký tự.", field=field)
    return text or None


def _fold(text: str) -> str:
    """Lower case, no accents ("Đ" -> "d"): the search's comparison."""
    value = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(char for char in value if unicodedata.category(char) != "Mn").casefold()


def _last4(value: str | None) -> str | None:
    digits = re.sub(r"\s+", "", value or "")
    return digits[-4:] if digits else None


def _money(value: object, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise HrValidationError("hr_money_invalid", "Số tiền không hợp lệ.", field=field) from None
    if not 0 <= number <= 10_000_000_000:
        raise HrValidationError("hr_money_invalid", "Số tiền không hợp lệ.", field=field)
    return number


@dataclass(frozen=True, slots=True)
class HrSettings:
    contract_warn_days: int = DEFAULT_WARN_DAYS
    required_documents: tuple[str, ...] = DEFAULT_REQUIRED_DOCUMENTS

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_warn_days": self.contract_warn_days,
            "required_documents": list(self.required_documents),
        }


class HrProfileService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        audit: AuditService,
        *,
        secret_box: Callable[[], SecretBox] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._audit = audit
        self._secret_box = secret_box or (lambda: build_secret_box(settings))
        self._box: SecretBox | None = None

    # --- secrets ---------------------------------------------------------------------

    def _seal_box(self) -> SecretBox:
        if self._box is None:
            try:
                self._box = self._secret_box()
            except Exception as exc:
                raise HrValidationError(
                    "hr_secret_not_configured",
                    "Máy chủ chưa có khoá mã hoá: chưa lưu được CCCD, số tài khoản, lương.",
                ) from exc
        return self._box

    def _open_secret(self, profile: HrProfile | None) -> dict[str, Any] | None:
        """The sealed block, ``{}`` when empty, ``None`` when it cannot be read."""
        if profile is None or not profile.secret:
            return {}
        try:
            box = self._seal_box()
            data: dict[str, Any] = json.loads(
                box.decrypt(profile.secret, aad=f"hr_profile:{profile.user_id}")
            )
            return data
        except Exception:
            return None

    def _seal_secret(self, profile: HrProfile, data: dict[str, Any]) -> None:
        kept = {key: value for key, value in data.items() if value not in (None, "")}
        if not kept:
            profile.secret = None
            return
        profile.secret = self._seal_box().encrypt(
            json.dumps(kept, ensure_ascii=False), aad=f"hr_profile:{profile.user_id}"
        )

    def _contract_salary(self, row: HrContract) -> int | None:
        if not row.salary_secret:
            return None
        try:
            return int(self._seal_box().decrypt(row.salary_secret, aad=f"hr_contract:{row.id}"))
        except Exception:
            return None

    # --- loading ---------------------------------------------------------------------

    async def settings(self) -> HrSettings:
        row = await self._session.scalar(
            select(SystemSetting).where(SystemSetting.key == SETTINGS_KEY)
        )
        value = row.value if row is not None and isinstance(row.value, dict) else {}
        days = value.get("contract_warn_days")
        docs = value.get("required_documents")
        return HrSettings(
            contract_warn_days=days if isinstance(days, int) and 1 <= days <= 365 else 30,
            required_documents=tuple(d for d in docs if d in DOCUMENT_KINDS)
            if isinstance(docs, list)
            else DEFAULT_REQUIRED_DOCUMENTS,
        )

    async def save_settings(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        contract_warn_days: int,
        required_documents: Sequence[str],
    ) -> HrSettings:
        access.require_edit()
        if not 1 <= contract_warn_days <= 365:
            raise HrValidationError(
                "hr_settings_invalid", "Số ngày cảnh báo từ 1 đến 365.", field="contract_warn_days"
            )
        wanted = HrSettings(
            contract_warn_days=contract_warn_days,
            required_documents=tuple(
                dict.fromkeys(d for d in required_documents if d in DOCUMENT_KINDS)
            ),
        )
        row = await self._session.scalar(
            select(SystemSetting).where(SystemSetting.key == SETTINGS_KEY)
        )
        if row is None:
            self._session.add(
                SystemSetting(
                    key=SETTINGS_KEY,
                    value=wanted.as_dict(),
                    description="Hồ sơ nhân sự: cảnh báo hết hạn hợp đồng, tài liệu bắt buộc.",
                    updated_by=actor.user_id,
                )
            )
        else:
            row.value = wanted.as_dict()
            row.version = int(row.version) + 1
            row.updated_by = actor.user_id
        await self._session.flush()
        await self._record(
            actor, request_id, AuditAction.HR_SETTINGS_UPDATED, None, wanted.as_dict()
        )
        return wanted

    async def contract_types(self, *, include_inactive: bool = False) -> list[HrContractType]:
        if await self._session.scalar(select(func.count(HrContractType.id))) == 0:
            # A database built without 0096's seed (tests): the mock's six.
            for index, (code, name, months) in enumerate(CONTRACT_TYPE_SEED):
                self._session.add(
                    HrContractType(code=code, name=name, months=months, sort_order=index)
                )
            await self._session.flush()
        statement = select(HrContractType).order_by(HrContractType.sort_order, HrContractType.code)
        if not include_inactive:
            statement = statement.where(HrContractType.active.is_(True))
        return list((await self._session.scalars(statement)).all())

    async def save_contract_type(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        type_id: uuid.UUID | None,
        fields: dict[str, Any],
    ) -> HrContractType:
        access.require_edit()
        row = None if type_id is None else await self._session.get(HrContractType, type_id)
        if type_id is not None and row is None:
            raise HrNotFoundError("Không tìm thấy loại hợp đồng.")
        if row is None:
            code = re.sub(r"[^A-Z0-9]+", "", str(fields.get("code") or "").upper())[:20]
            if not code:
                raise HrValidationError("hr_contract_type_invalid", "Cần mã loại.", field="code")
            if await self._session.scalar(
                select(HrContractType.id).where(HrContractType.code == code)
            ):
                raise HrValidationError("hr_contract_type_taken", "Mã loại đã có.", field="code")
            orders = (await self._session.scalars(select(HrContractType.sort_order))).all()
            row = HrContractType(
                code=code, name="", months=0, sort_order=max(orders, default=-1) + 1
            )
            self._session.add(row)
        if "name" in fields or not row.name:
            name = _clean(fields.get("name"), "name")
            if not name:
                raise HrValidationError("hr_contract_type_invalid", "Cần tên loại.", field="name")
            row.name = name
        if "months" in fields:
            months = fields["months"]
            if not isinstance(months, int) or not 0 <= months <= 240:
                raise HrValidationError(
                    "hr_contract_type_invalid", "Thời hạn từ 0 đến 240 tháng.", field="months"
                )
            row.months = months
        if "active" in fields and fields["active"] is not None:
            row.active = bool(fields["active"])
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.HR_SETTINGS_UPDATED,
            None,
            {
                "contract_type": row.code,
                "name": row.name,
                "months": row.months,
                "active": row.active,
            },
        )
        return row

    async def _user(self, user_id: uuid.UUID) -> User:
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise HrNotFoundError()
        return user

    async def _profile(self, user_id: uuid.UUID, *, create: bool = False) -> HrProfile | None:
        profile = await self._session.get(HrProfile, user_id)
        if profile is None and create:
            profile = HrProfile(user_id=user_id)
            self._session.add(profile)
            await self._session.flush()
        return profile

    async def _staff(self) -> list[User]:
        """Everybody the module lists: living accounts carrying a member code."""
        return list(
            (
                await self._session.scalars(
                    select(User)
                    .where(User.deleted_at.is_(None), User.member_code.is_not(None))
                    .order_by(User.full_name, User.id)
                )
            ).all()
        )

    async def _entities(self) -> dict[uuid.UUID, LegalEntity]:
        return {row.id: row for row in (await self._session.scalars(select(LegalEntity))).all()}

    async def _contracts_of(
        self, user_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, list[HrContract]]:
        ids = list(user_ids)
        found: dict[uuid.UUID, list[HrContract]] = {}
        if not ids:
            return found
        rows = (
            await self._session.scalars(
                select(HrContract)
                .where(HrContract.user_id.in_(ids))
                .order_by(HrContract.start_on.desc().nullslast(), HrContract.created_at.desc())
            )
        ).all()
        for row in rows:
            found.setdefault(row.user_id, []).append(row)
        return found

    async def _document_kinds(self, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, set[str]]:
        ids = list(user_ids)
        found: dict[uuid.UUID, set[str]] = {}
        if not ids:
            return found
        for user_id, kind in (
            await self._session.execute(
                select(HrDocument.user_id, HrDocument.kind).where(HrDocument.user_id.in_(ids))
            )
        ).all():
            found.setdefault(user_id, set()).add(kind)
        return found

    # --- shaping ---------------------------------------------------------------------

    def _contract_view(
        self, row: HrContract, types: dict[str, HrContractType], today: date, *, salary: bool
    ) -> dict[str, Any]:
        state = contract_state(row.start_on, row.end_on, row.ended_on, today)
        days_left = (row.end_on - today).days if row.end_on is not None else None
        kind = types.get(row.type_code)
        return {
            "id": str(row.id),
            "number": row.number,
            "type_code": row.type_code,
            "type_name": kind.name if kind else row.type_code,
            "start_on": row.start_on,
            "end_on": row.end_on,
            "signed": row.signed,
            "signed_by": row.signed_by,
            "ended_on": row.ended_on,
            "state": state.value,
            "state_label": CONTRACT_STATE_LABELS[state],
            "days_left": days_left,
            "salary": self._contract_salary(row) if salary else None,
            "note": row.note,
        }

    @staticmethod
    def _current(contracts: list[dict[str, Any]]) -> dict[str, Any] | None:
        active = [item for item in contracts if item["state"] == ContractState.ACTIVE.value]
        return active[0] if active else (contracts[0] if contracts else None)

    def _row(
        self,
        user: User,
        profile: HrProfile | None,
        seats: Sequence[Seat],
        entities: dict[uuid.UUID, LegalEntity],
        contracts: list[dict[str, Any]],
        documents: set[str],
        settings: HrSettings,
        access: HrAccess,
        today: date,
    ) -> dict[str, Any]:
        """One line of "Quản lý hồ sơ"."""
        entity = entities.get(user.legal_entity_id) if user.legal_entity_id else None
        current = self._current(contracts)
        sensitive = access.sees_sensitive(user.id)
        row: dict[str, Any] = {
            "user_id": str(user.id),
            "member_code": user.member_code,
            "full_name": user.full_name,
            "job_title": user.job_title,
            "departments": [
                {
                    "department_id": str(seat.department_id),
                    "code": seat.code,
                    "name": seat.name,
                    "position": seat.position.value,
                }
                for seat in seats
            ],
            "branch": user.branch,
            "branch_label": BRANCHES.get(user.branch or ""),
            "legal_entity_id": None if entity is None else str(entity.id),
            "legal_entity_code": None if entity is None else entity.code,
            "employment_status": user.employment_status.value,
            "employment_status_label": EMPLOYMENT_STATUS_LABELS[user.employment_status],
            "active": bool(user.active) and user.employment_status is not EmploymentStatus.LEFT,
            "telegram_linked": user.telegram_user_id is not None,
            "gender": None if profile is None else profile.gender,
            "gender_label": GENDERS.get((profile.gender if profile else None) or ""),
            "grade": None if profile is None else profile.grade,
            "work_email": None if profile is None else profile.work_email,
            "joined_on": None if profile is None else profile.joined_on,
            "official_on": None if profile is None else profile.official_on,
            "seniority": seniority(profile.joined_on if profile else None, today),
            "contract": None
            if current is None
            else {
                key: current[key]
                for key in ("type_name", "number", "end_on", "state", "state_label", "days_left")
            },
            "contract_warning": bool(
                current
                and current["days_left"] is not None
                and 0 <= current["days_left"] <= settings.contract_warn_days
                and current["state"] == ContractState.ACTIVE.value
            ),
            "missing_documents": [
                kind
                for kind in settings.required_documents
                if kind not in documents
                and kind not in ((profile.paper_documents or []) if profile else [])
            ],
            "sensitive": sensitive,
        }
        if sensitive and profile is not None:
            row.update(
                {
                    "phone": profile.phone,
                    "birth_date": profile.birth_date,
                    "personal_email": profile.personal_email,
                    "id_masked": f"••••{profile.id_last4}" if profile.id_last4 else None,
                }
            )
        return row

    # --- reads -----------------------------------------------------------------------

    async def rows(self, *, access: HrAccess, today: date) -> list[dict[str, Any]]:
        """Every profile the reader may see (unfiltered)."""
        if not access.lists and access.user_id is None:
            raise HrForbiddenError()
        staff = [user for user in await self._staff() if access.may_view(user.id)]
        ids = [user.id for user in staff]
        profiles = (
            {
                row.user_id: row
                for row in (
                    await self._session.scalars(select(HrProfile).where(HrProfile.user_id.in_(ids)))
                ).all()
            }
            if ids
            else {}
        )
        seats = await load_seats(self._session, ids)
        entities = await self._entities()
        types = {row.code: row for row in await self.contract_types(include_inactive=True)}
        contracts = await self._contracts_of(ids)
        documents = await self._document_kinds(ids)
        settings = await self.settings()
        return [
            self._row(
                user,
                profiles.get(user.id),
                seats.get(user.id, ()),
                entities,
                [
                    self._contract_view(item, types, today, salary=False)
                    for item in contracts.get(user.id, [])
                ],
                documents.get(user.id, set()),
                settings,
                access,
                today,
            )
            for user in staff
        ]

    @staticmethod
    def filter_rows(
        rows: list[dict[str, Any]],
        *,
        q: str | None = None,
        department_id: str | None = None,
        branch: str | None = None,
        status: str | None = None,
        legal_entity_id: str | None = None,
        gender: str | None = None,
        contract: str | None = None,
    ) -> list[dict[str, Any]]:
        needle = _fold(q or "").strip()
        found: list[dict[str, Any]] = []
        for row in rows:
            if department_id and not any(
                seat["department_id"] == department_id for seat in row["departments"]
            ):
                continue
            if branch and row["branch"] != branch:
                continue
            if status and row["employment_status"] != status:
                continue
            if status is None and not row["active"]:
                # Leavers only when asked for.
                continue
            if legal_entity_id and row["legal_entity_id"] != legal_entity_id:
                continue
            if gender and row["gender"] != gender:
                continue
            if contract:
                current = row["contract"]
                if contract == "none" and current is not None:
                    continue
                if contract == "expiring" and not (
                    current
                    and current["state"] == ContractState.ACTIVE.value
                    and current["days_left"] is not None
                    and 0 <= current["days_left"] <= EXPIRING_DAYS
                ):
                    continue
                if contract == "expired" and not (
                    current and current["state"] == ContractState.EXPIRED.value
                ):
                    continue
            if needle:
                haystack = " ".join(
                    str(row.get(key) or "")
                    for key in (
                        "member_code",
                        "full_name",
                        "work_email",
                        "phone",
                        "personal_email",
                        "job_title",
                    )
                )
                if needle not in _fold(haystack):
                    continue
            found.append(row)
        return found

    async def profile(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        today: date,
    ) -> dict[str, Any]:
        access.require_view(user_id)
        user = await self._user(user_id)
        profile = await self._profile(user_id)
        seats = (await load_seats(self._session, [user_id])).get(user_id, ())
        entities = await self._entities()
        types = {row.code: row for row in await self.contract_types(include_inactive=True)}
        sensitive = access.sees_sensitive(user_id)
        contracts = [
            self._contract_view(item, types, today, salary=sensitive)
            for item in (await self._contracts_of([user_id])).get(user_id, [])
        ]
        settings = await self.settings()
        kinds = (await self._document_kinds([user_id])).get(user_id, set())
        view = self._row(user, profile, seats, entities, contracts, kinds, settings, access, today)
        managers = [seat.manager_user_id for seat in seats if seat.manager_user_id]
        manager_names = (
            {
                row.id: row.full_name
                for row in (
                    await self._session.scalars(select(User).where(User.id.in_(managers)))
                ).all()
            }
            if managers
            else {}
        )
        view["departments"] = [
            {
                "department_id": str(seat.department_id),
                "code": seat.code,
                "name": seat.name,
                "position": seat.position.value,
                "position_label": POSITION_LABELS[seat.position],
                "manager": manager_names.get(seat.manager_user_id)
                if seat.manager_user_id
                else None,
            }
            for seat in seats
        ]
        view["contracts"] = contracts
        view["work"] = {
            "grade": None if profile is None else profile.grade,
            "work_form": None if profile is None else profile.work_form,
            "joined_on": None if profile is None else profile.joined_on,
            "official_on": None if profile is None else profile.official_on,
            "seniority": view["seniority"],
            "left_on": None if profile is None else profile.left_on,
            "leave_reason": None if profile is None else profile.leave_reason,
            "work_email": None if profile is None else profile.work_email,
            "note": None if profile is None else profile.note,
            "team": None if profile is None else profile.team,
            "fingerprint_id": None if profile is None else profile.fingerprint_id,
            "fingerprint_on": None if profile is None else profile.fingerprint_on,
            "fingerprint_floor": None if profile is None else profile.fingerprint_floor,
        }
        view["can_edit"] = access.edit
        view["papers"] = None
        view["personal"] = view["pay"] = view["insurance"] = None
        view["documents"] = None
        if sensitive:
            secret = self._open_secret(profile)
            view["personal"] = {
                "gender": None if profile is None else profile.gender,
                "birth_date": None if profile is None else profile.birth_date,
                "age": age(profile.birth_date if profile else None, today),
                **{
                    name: None if profile is None else getattr(profile, name)
                    for name in (
                        "birth_place",
                        "nationality",
                        "ethnicity",
                        "religion",
                        "marital_status",
                        "id_issued_on",
                        "id_issued_by",
                        "phone",
                        "personal_email",
                        "drive_email",
                        "permanent_address",
                        "current_address",
                        "bank_name",
                        "bank_holder",
                        "bank2_name",
                        "bank2_holder",
                    )
                },
                "id_number": None if secret is None else secret.get("id_number"),
                "bank_account": None if secret is None else secret.get("bank_account"),
                "bank_account_2": None if secret is None else secret.get("bank_account_2"),
                "secret_unreadable": secret is None,
            }
            base = None if secret is None else secret.get("base_salary")
            allowance = None if secret is None else secret.get("allowance")
            view["pay"] = {
                "base_salary": base,
                "allowance": allowance,
                "total": None
                if base is None and allowance is None
                else (base or 0) + (allowance or 0),
                "salary_type": None if secret is None else secret.get("salary_type"),
                "salary_note": None if secret is None else secret.get("salary_note"),
            }
            view["insurance"] = {
                "social_insurance_no": None if profile is None else profile.social_insurance_no,
                "insurance_title": None if profile is None else profile.insurance_title,
                "social_insurance_from": None if profile is None else profile.social_insurance_from,
                "insurance_salary": None if secret is None else secret.get("insurance_salary"),
                "health_insurance_no": None if profile is None else profile.health_insurance_no,
                "tax_code": None if profile is None else profile.tax_code,
                "dependants": None if profile is None else profile.dependants,
            }
            handed = set((profile.paper_documents or []) if profile else [])
            view["papers"] = {
                "scan_link": None if profile is None else profile.scan_link,
                "items": [
                    {
                        "code": code,
                        "label": DOCUMENT_KINDS[code],
                        "handed": code in handed,
                        "uploaded": code in kinds,
                        "required": code in settings.required_documents,
                    }
                    for code in PAPER_DOCUMENTS
                ],
            }
            view["documents"] = [
                {
                    "id": str(row.id),
                    "kind": row.kind,
                    "kind_label": DOCUMENT_KINDS.get(row.kind, row.kind),
                    "name": row.name,
                    "content_type": row.content_type,
                    "size_bytes": row.size_bytes,
                    "created_at": row.created_at,
                }
                for row in (
                    await self._session.scalars(
                        select(HrDocument)
                        .where(HrDocument.user_id == user_id)
                        .order_by(HrDocument.created_at.desc())
                    )
                ).all()
            ]
            if user_id != actor.user_id:
                await self._record(
                    actor, request_id, AuditAction.HR_PROFILE_SENSITIVE_VIEWED, user_id, None
                )
        view["requests"] = [
            {
                "id": str(row.id),
                "field": row.field,
                "new_value": row.new_value if sensitive else None,
                "status": row.status,
                "created_at": row.created_at,
            }
            for row in (
                await self._session.scalars(
                    select(HrChangeRequest)
                    .where(
                        HrChangeRequest.user_id == user_id,
                        HrChangeRequest.status == RequestStatus.PENDING.value,
                    )
                    .order_by(HrChangeRequest.created_at.desc())
                )
            ).all()
        ]
        return view

    async def overview(self, *, access: HrAccess, today: date) -> dict[str, Any]:
        if not access.view_all:
            raise HrForbiddenError("Tổng quan nhân sự dành cho phòng Hành chính nhân sự.")
        rows = await self.rows(access=access, today=today)
        active = [row for row in rows if row["active"]]
        women = sum(1 for row in active if row["gender"] == "FEMALE")
        with_gender = sum(1 for row in active if row["gender"])
        by_department: dict[str, dict[str, Any]] = {}
        for row in active:
            for seat in row["departments"]:
                entry = by_department.setdefault(
                    seat["department_id"], {"name": seat["name"], "code": seat["code"], "people": 0}
                )
                entry["people"] += 1
        by_status: dict[str, dict[str, Any]] = {}
        for row in rows:
            entry = by_status.setdefault(
                row["employment_status"], {"label": row["employment_status_label"], "people": 0}
            )
            entry["people"] += 1
        expiring = sorted(
            (
                row
                for row in active
                if row["contract"]
                and row["contract"]["state"] == ContractState.ACTIVE.value
                and row["contract"]["days_left"] is not None
                and 0 <= row["contract"]["days_left"] <= EXPIRING_DAYS
            ),
            key=lambda row: row["contract"]["days_left"],
        )
        expired = [
            row
            for row in active
            if row["contract"] and row["contract"]["state"] == ContractState.EXPIRED.value
        ]
        newest = sorted(
            (row for row in active if row["joined_on"]),
            key=lambda row: row["joined_on"],
            reverse=True,
        )[:6]
        pending = int(
            await self._session.scalar(
                select(func.count(HrChangeRequest.id)).where(
                    HrChangeRequest.status == RequestStatus.PENDING.value
                )
            )
            or 0
        )
        result: dict[str, Any] = {
            "total": len(rows),
            "active": len(active),
            "left": len(rows) - len(active),
            "female_pct": round(women * 100 / with_gender) if with_gender else None,
            "by_department": sorted(by_department.values(), key=lambda item: -item["people"]),
            "by_status": list(by_status.values()),
            "expiring": [self._brief(row) for row in expiring[:8]],
            "expiring_count": len(expiring),
            "expired_count": len(expired),
            "newest": [self._brief(row) for row in newest],
            "pending_requests": pending,
            "missing_documents": sum(1 for row in active if row["missing_documents"]),
            "no_contract": sum(1 for row in active if row["contract"] is None),
            "sensitive": access.sensitive,
            "average_age": None,
            "birthdays": [],
            "payroll": None,
        }
        if access.sensitive:
            profiles = (
                {
                    row.user_id: row
                    for row in (
                        await self._session.scalars(
                            select(HrProfile).where(
                                HrProfile.user_id.in_([uuid.UUID(row["user_id"]) for row in active])
                            )
                        )
                    ).all()
                }
                if active
                else {}
            )
            ages = [age(item.birth_date, today) for item in profiles.values() if item.birth_date]
            result["average_age"] = round(sum(a for a in ages if a) / len(ages)) if ages else None
            names = {row["user_id"]: row for row in active}
            result["birthdays"] = [
                {**self._brief(names[str(item.user_id)]), "birth_date": item.birth_date}
                for item in sorted(
                    (
                        p
                        for p in profiles.values()
                        if p.birth_date and p.birth_date.month == today.month
                    ),
                    key=lambda p: p.birth_date.day if p.birth_date else 0,
                )
            ][:10]
            total = 0
            counted = False
            for item in profiles.values():
                secret = self._open_secret(item)
                if secret:
                    for key in ("base_salary", "allowance"):
                        if isinstance(secret.get(key), int):
                            total += secret[key]
                            counted = True
            result["payroll"] = total if counted else None
        return result

    @staticmethod
    def _brief(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": row["user_id"],
            "member_code": row["member_code"],
            "full_name": row["full_name"],
            "job_title": row["job_title"],
            "department": row["departments"][0]["name"] if row["departments"] else None,
            "joined_on": row["joined_on"],
            "contract": row["contract"],
        }

    async def org_chart(self) -> list[dict[str, Any]]:
        """Departments as a tree, each with its heads and headcount."""
        departments = list(
            (
                await self._session.scalars(
                    select(Department)
                    .where(Department.active.is_(True))
                    .order_by(Department.sort_order, Department.name)
                )
            ).all()
        )
        seats = await load_seats(self._session)
        living = {
            row.id: row
            for row in (
                await self._session.scalars(
                    select(User).where(User.deleted_at.is_(None), User.active.is_(True))
                )
            ).all()
        }
        heads: dict[uuid.UUID, list[dict[str, Any]]] = {}
        count: dict[uuid.UUID, int] = {}
        for user_id, held in seats.items():
            user = living.get(user_id)
            if user is None or user.employment_status is EmploymentStatus.LEFT:
                continue
            for seat in held:
                count[seat.department_id] = count.get(seat.department_id, 0) + 1
                if seat.position is Position.HEAD:
                    heads.setdefault(seat.department_id, []).append(
                        {
                            "user_id": str(user.id),
                            "full_name": user.full_name,
                            "job_title": user.job_title,
                        }
                    )
        return [
            {
                "id": str(row.id),
                "code": row.code,
                "name": row.name,
                "block": row.block,
                "parent_id": None if row.parent_id is None else str(row.parent_id),
                "heads": heads.get(row.id, []),
                "people": count.get(row.id, 0),
            }
            for row in departments
        ]

    # --- writes ----------------------------------------------------------------------

    async def update(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        fields: dict[str, Any],
    ) -> None:
        """Only the keys sent change; ``None`` clears."""
        access.require_edit()
        user = await self._user(user_id)
        if (
            any(
                name in fields
                for name in (
                    *PERSONAL_TEXT,
                    *PERSONAL_DATES,
                    *SECRET_TEXT,
                    *SECRET_MONEY,
                    "gender",
                    "dependants",
                    "paper_documents",
                )
            )
            and not access.sensitive
        ):
            raise HrForbiddenError("Chỉ người được xem dữ liệu nhạy cảm mới sửa được phần này.")
        changed = await self._apply(actor, request_id, user, fields)
        if changed:
            await self._record(
                actor,
                request_id,
                AuditAction.HR_PROFILE_UPDATED,
                user_id,
                {"fields": sorted(changed)},
            )

    async def _apply(
        self, actor: Actor, request_id: uuid.UUID, user: User, fields: dict[str, Any]
    ) -> set[str]:
        changed: set[str] = set()
        profile = await self._profile(user.id, create=True)
        assert profile is not None
        for name in (*WORK_TEXT, *PERSONAL_TEXT):
            if name in fields:
                value = _clean(fields[name], name)
                if getattr(profile, name) != value:
                    setattr(profile, name, value)
                    changed.add(name)
        for name in (*WORK_DATES, *PERSONAL_DATES):
            if name in fields:
                value = fields[name]
                if value is not None and not isinstance(value, date):
                    raise HrValidationError("hr_date_invalid", "Ngày không hợp lệ.", field=name)
                if getattr(profile, name) != value:
                    setattr(profile, name, value)
                    changed.add(name)
        if "gender" in fields:
            gender = fields["gender"]
            if gender is not None and gender not in GENDERS:
                raise HrValidationError(
                    "hr_gender_invalid", "Giới tính không hợp lệ.", field="gender"
                )
            if profile.gender != gender:
                profile.gender = gender
                changed.add("gender")
        if "paper_documents" in fields:
            raw = fields["paper_documents"] or []
            if not isinstance(raw, list) or any(
                not isinstance(code, str) or code not in DOCUMENT_KINDS for code in raw
            ):
                raise HrValidationError(
                    "hr_value_invalid", "Loại giấy tờ không hợp lệ.", field="paper_documents"
                )
            papers = [code for code in DOCUMENT_KINDS if code in set(raw)]
            if list(profile.paper_documents or []) != papers:
                profile.paper_documents = papers
                changed.add("paper_documents")
        if "dependants" in fields:
            dependants = fields["dependants"]
            if dependants is not None and (
                not isinstance(dependants, int) or not 0 <= dependants <= 30
            ):
                raise HrValidationError(
                    "hr_value_invalid", "Số người phụ thuộc 0-30.", field="dependants"
                )
            if profile.dependants != dependants:
                profile.dependants = dependants
                changed.add("dependants")
        sealed = [name for name in (*SECRET_TEXT, *SECRET_MONEY) if name in fields]
        if sealed:
            secret = self._open_secret(profile)
            if secret is None:
                raise HrValidationError(
                    "hr_secret_not_configured",
                    "Không đọc được phần dữ liệu đã mã hoá của hồ sơ này.",
                )
            for name in sealed:
                sealed_value: object = (
                    _money(fields[name], name)
                    if name in SECRET_MONEY
                    else _clean(fields[name], name)
                )
                if name == "id_number" and sealed_value is not None:
                    sealed_value = re.sub(r"\s+", "", str(sealed_value))
                if secret.get(name) != sealed_value:
                    secret[name] = sealed_value
                    changed.add(name)
            self._seal_secret(profile, secret)
            profile.id_last4 = _last4(secret.get("id_number"))
            profile.bank_last4 = _last4(secret.get("bank_account"))
            profile.bank2_last4 = _last4(secret.get("bank_account_2"))
        # the account
        if "full_name" in fields:
            name = " ".join(str(fields["full_name"] or "").split())
            if not name or len(name) > 200:
                raise HrValidationError(
                    "hr_value_invalid", "Họ tên 1-200 ký tự.", field="full_name"
                )
            if user.full_name != name:
                user.full_name = name
                changed.add("full_name")
        if "job_title" in fields:
            title = _clean(fields["job_title"], "job_title")
            if user.job_title != title:
                user.job_title = title
                changed.add("job_title")
        if "branch" in fields:
            branch = fields["branch"]
            if branch is not None and branch not in BRANCHES:
                raise HrValidationError("branch_invalid", "Chi nhánh không hợp lệ.", field="branch")
            if user.branch != branch:
                user.branch = branch
                changed.add("branch")
        if "legal_entity_id" in fields:
            entity_id = fields["legal_entity_id"]
            if entity_id is not None and await self._session.get(LegalEntity, entity_id) is None:
                raise HrValidationError(
                    "legal_entity_invalid", "Pháp nhân không hợp lệ.", field="legal_entity_id"
                )
            if user.legal_entity_id != entity_id:
                user.legal_entity_id = entity_id
                changed.add("legal_entity_id")
        if "employment_status" in fields and fields["employment_status"] is not None:
            status = EmploymentStatus(fields["employment_status"])
            if user.employment_status is not status:
                await apply_employment_status(
                    self._session,
                    self._audit,
                    actor=actor,
                    request_id=request_id,
                    target=user,
                    status=status,
                    settings=self._settings,
                )
                changed.add("employment_status")
        profile.updated_by_user_id = actor.user_id
        await self._session.flush()
        return changed

    async def bulk_update(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        user_ids: Sequence[uuid.UUID],
        fields: dict[str, Any],
    ) -> int:
        access.require_edit()
        unknown = set(fields) - set(BULK_FIELDS)
        if unknown or not fields:
            raise HrValidationError("hr_bulk_invalid", "Trường sửa hàng loạt không hợp lệ.")
        done = 0
        for user_id in dict.fromkeys(user_ids):
            user = await self._user(user_id)
            if await self._apply(actor, request_id, user, fields):
                done += 1
        await self._record(
            actor,
            request_id,
            AuditAction.HR_PROFILE_BULK_UPDATED,
            None,
            {"fields": sorted(fields), "people": len(set(user_ids)), "changed": done},
        )
        return done

    async def create_employee(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        member_code: str,
        full_name: str,
        department_id: uuid.UUID,
        position: Position,
        fields: dict[str, Any],
    ) -> tuple[User, str]:
        """A new account with its profile and seat; returns the temporary
        password (shown once)."""
        access.require_edit()
        code = normalise_member_code(member_code)
        if not code:
            raise HrValidationError("member_code_missing", "Cần mã nhân viên.", field="member_code")
        if await self._session.scalar(
            select(User.id).where(User.member_code == code, User.deleted_at.is_(None))
        ):
            raise HrValidationError("member_code_taken", "Mã nhân viên đã có.", field="member_code")
        name = " ".join(full_name.split())
        if not name:
            raise HrValidationError("hr_value_invalid", "Cần họ tên.", field="full_name")
        department = await self._session.get(Department, department_id)
        if department is None or not department.active:
            raise HrValidationError(
                "department_invalid", "Phòng ban không hợp lệ.", field="department_id"
            )
        password = generate_temporary_password()
        user = User(
            telegram_user_id=None,
            full_name=name,
            role=Role.EMPLOYEE,
            status=UserStatus.ACTIVE,
            active=True,
            employment_status=EmploymentStatus.PROBATION,
            password_hash=hash_password(password),
            password_temporary=True,
        )
        self._session.add(user)
        await self._session.flush()
        await write_member_code(self._session, user, code)
        await seat_person(self._session, department.id, user.id, position)
        await self._apply(actor, request_id, user, fields)
        await sync_stream_tags(self._session, [user.id])
        await self._record(
            actor,
            request_id,
            AuditAction.HR_PROFILE_CREATED,
            user.id,
            {"member_code": code, "department": department.code},
        )
        return user, password

    # --- contracts -------------------------------------------------------------------

    async def save_contract(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        contract_id: uuid.UUID | None,
        fields: dict[str, Any],
    ) -> HrContract:
        access.require_edit()
        await self._user(user_id)
        row = None if contract_id is None else await self._session.get(HrContract, contract_id)
        if contract_id is not None and (row is None or row.user_id != user_id):
            raise HrNotFoundError("Không tìm thấy hợp đồng.")
        types = {item.code: item for item in await self.contract_types(include_inactive=True)}
        if row is None:
            code = fields.get("type_code")
            if code not in types:
                raise HrValidationError(
                    "hr_contract_invalid", "Chọn loại hợp đồng.", field="type_code"
                )
            row = HrContract(user_id=user_id, type_code=code, created_by_user_id=actor.user_id)
            self._session.add(row)
            await self._session.flush()
        if "type_code" in fields:
            if fields["type_code"] not in types:
                raise HrValidationError(
                    "hr_contract_invalid", "Loại hợp đồng không hợp lệ.", field="type_code"
                )
            row.type_code = fields["type_code"]
        for name in ("start_on", "end_on", "ended_on"):
            if name in fields:
                value = fields[name]
                if value is not None and not isinstance(value, date):
                    raise HrValidationError("hr_date_invalid", "Ngày không hợp lệ.", field=name)
                setattr(row, name, value)
        if "end_on" not in fields and row.start_on is not None and row.end_on is None:
            # Auto: the type's length from the start (an indefinite one stays open).
            row.end_on = contract_end(row.start_on, types[row.type_code].months)
        if row.start_on and row.end_on and row.end_on < row.start_on:
            raise HrValidationError(
                "hr_contract_invalid", "Ngày kết thúc trước ngày bắt đầu.", field="end_on"
            )
        for name in ("number", "signed_by", "note"):
            if name in fields:
                setattr(row, name, _clean(fields[name], name))
        if "signed" in fields and fields["signed"] is not None:
            row.signed = bool(fields["signed"])
        if "salary" in fields:
            if not access.sensitive:
                raise HrForbiddenError("Chỉ người được xem dữ liệu nhạy cảm mới nhập lương.")
            salary = _money(fields["salary"], "salary")
            row.salary_secret = (
                None
                if salary is None
                else self._seal_box().encrypt(str(salary), aad=f"hr_contract:{row.id}")
            )
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.HR_CONTRACT_SAVED,
            user_id,
            {"contract_id": str(row.id), "type": row.type_code, "fields": sorted(fields)},
        )
        return row

    async def delete_contract(
        self, *, actor: Actor, access: HrAccess, request_id: uuid.UUID, contract_id: uuid.UUID
    ) -> None:
        access.require_edit()
        row = await self._session.get(HrContract, contract_id)
        if row is None:
            raise HrNotFoundError("Không tìm thấy hợp đồng.")
        await self._session.delete(row)
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.HR_CONTRACT_DELETED,
            row.user_id,
            {"contract_id": str(row.id)},
        )

    async def renew_contracts(
        self,
        *,
        actor: Actor,
        access: HrAccess,
        request_id: uuid.UUID,
        user_ids: Sequence[uuid.UUID],
        type_code: str,
        start_on: date | None,
        today: date,
        signed: bool = False,
    ) -> int:
        """ "Gia hạn / thêm HĐ" for several people: a new contract of
        ``type_code`` from ``start_on``, else the day after each person's
        current contract ends, else today."""
        access.require_edit()
        types = {item.code: item for item in await self.contract_types(include_inactive=True)}
        if type_code not in types:
            raise HrValidationError("hr_contract_invalid", "Chọn loại hợp đồng.", field="type_code")
        existing = await self._contracts_of(user_ids)
        made = 0
        for user_id in dict.fromkeys(user_ids):
            await self._user(user_id)
            start = start_on
            if start is None:
                ends = [
                    row.end_on
                    for row in existing.get(user_id, [])
                    if row.end_on and not row.ended_on
                ]
                latest = max(ends) if ends else None
                start = (
                    date.fromordinal(latest.toordinal() + 1)
                    if latest is not None and latest >= today
                    else today
                )
            self._session.add(
                HrContract(
                    user_id=user_id,
                    type_code=type_code,
                    start_on=start,
                    end_on=contract_end(start, types[type_code].months),
                    signed=signed,
                    created_by_user_id=actor.user_id,
                )
            )
            made += 1
        await self._session.flush()
        await self._record(
            actor,
            request_id,
            AuditAction.HR_CONTRACT_SAVED,
            None,
            {"bulk": True, "type": type_code, "people": made},
        )
        return made

    async def _record(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        user_id: uuid.UUID | None,
        after: dict[str, Any] | None,
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action.value,
            result=AuditResult.SUCCESS,
            entity_type="hr_profile",
            entity_id=None if user_id is None else str(user_id),
            after_data=after,
        )


__all__ = ["EXPIRING_DAYS", "HrProfileService", "HrSettings", "HrValidationError"]
