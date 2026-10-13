"""The Design team's library ("Thư viện"): the named links in their menu.

A short list - a Figma file, the brand guideline, a template folder - that the
Design Leader sets up (the OWNER and an ADMIN may too) and everybody in the
Design team sees in the sidebar. It is stored with the ORD unit's settings
(``design_library``) and saved whole: the form sends the list as it should be,
in menu order.

Only ``http(s)`` links are kept: the entries are opened from the menu, so a
``javascript:`` or ``data:`` address is refused rather than stored.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.orders.design_code_service import in_design_team, manages_design_codes
from meobot.application.units.directory import UnitDirectoryService
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.orders.errors import OrderActionNotAllowedError, OrderValidationError
from meobot.domain.units.models import LibraryLink, UnitCode, UnitMembership, UnitSettings

#: How many entries the menu holds.
MAX_LINKS = 30


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    """One entry as the form sends it. ``id`` is kept for an entry that was
    already there, absent for a new one."""

    name: str
    url: str
    icon: str | None = None
    id: str | None = None


def sees_library(membership: UnitMembership) -> bool:
    """The Design team, and whoever keeps the library."""
    return membership.has(UnitCode.ADS) and (
        in_design_team(membership) or manages_design_codes(membership)
    )


def manages_library(membership: UnitMembership) -> bool:
    """The Design Leader, the OWNER, an ADMIN."""
    return membership.has(UnitCode.ADS) and manages_design_codes(membership)


class DesignLibraryService:
    def __init__(
        self, session: AsyncSession, *, directory: UnitDirectoryService, audit: AuditService
    ) -> None:
        self._session = session
        self._directory = directory
        self._audit = audit

    async def links(self, *, actor: Actor) -> tuple[LibraryLink, ...]:
        membership = await self._directory.require(actor, UnitCode.ADS)
        if not sees_library(membership):
            raise _forbidden()
        return (await self._directory.settings(UnitCode.ADS)).design_library

    async def replace(
        self, *, actor: Actor, request_id: uuid.UUID, entries: Sequence[LibraryEntry]
    ) -> tuple[LibraryLink, ...]:
        """Save the library as ``entries`` says, in that order."""
        membership = await self._directory.require(actor, UnitCode.ADS)
        if not manages_library(membership):
            raise _forbidden()
        if len(entries) > MAX_LINKS:
            raise OrderValidationError(
                f"Thư viện tối đa {MAX_LINKS} mục.",
                details={"reason": "library_too_many", "field": "links"},
            )
        seen: set[str] = set()
        links: list[LibraryLink] = []
        for index, entry in enumerate(entries):
            ident = (entry.id or "").strip()[:64]
            if not ident or ident in seen:
                ident = uuid.uuid4().hex
            seen.add(ident)
            links.append(
                LibraryLink(
                    id=ident,
                    name=_name(entry.name, index),
                    url=_url(entry.url, index),
                    icon=_icon(entry.icon),
                )
            )
        unit = await self._directory.unit(UnitCode.ADS)
        current = UnitSettings.model_validate(unit.settings or {})
        updated = current.model_copy(update={"design_library": tuple(links)})
        before = [link.model_dump(mode="json") for link in current.design_library]
        unit.settings = updated.model_dump(mode="json")
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.UNIT_LIBRARY_UPDATED.value,
            result=AuditResult.SUCCESS,
            entity_type="org_unit",
            entity_id=str(unit.id),
            before_data={"design_library": before},
            after_data={"design_library": [link.model_dump(mode="json") for link in links]},
        )
        return tuple(links)


def _forbidden() -> OrderActionNotAllowedError:
    return OrderActionNotAllowedError(
        "Thư viện là của ban Design; chỉ Trưởng phòng Design mới sửa được.",
        details={"reason": "library_forbidden"},
    )


def _name(value: str, index: int) -> str:
    cleaned = " ".join((value or "").split())
    if not cleaned:
        raise OrderValidationError(
            "Cần nhập tên cho mục thư viện.",
            details={"reason": "library_name_missing", "field": "name", "index": index},
        )
    if len(cleaned) > 80:
        raise OrderValidationError(
            "Tên mục thư viện tối đa 80 ký tự.",
            details={"reason": "library_name_missing", "field": "name", "index": index},
        )
    return cleaned


def _url(value: str, index: int) -> str:
    cleaned = (value or "").strip()
    parts = urlsplit(cleaned)
    if (
        parts.scheme.lower() not in ("http", "https")
        or not parts.netloc
        or len(cleaned) > 2000
        or any(character.isspace() for character in cleaned)
    ):
        raise OrderValidationError(
            "Link phải bắt đầu bằng http:// hoặc https://.",
            details={"reason": "library_url_invalid", "field": "url", "index": index},
        )
    return cleaned


def _icon(value: str | None) -> str | None:
    cleaned = "".join((value or "").split())
    return cleaned[:8] or None


__all__ = [
    "MAX_LINKS",
    "DesignLibraryService",
    "LibraryEntry",
    "manages_library",
    "sees_library",
]
