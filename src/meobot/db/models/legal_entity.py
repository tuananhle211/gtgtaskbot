"""Legal entities ("Pháp nhân", 0095): the companies people are employed by.

Each account may name one (``users.legal_entity_id``) - the company that pays
their social insurance in the HR sheet ("Pháp nhân đóng BHXH"). The list is
data: the OWNER / an ADMIN adds or renames entries; an entry is switched off,
never deleted, so the people who name it keep it.
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

LEGAL_ENTITIES = "legal_entities"


class LegalEntity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = LEGAL_ENTITIES
    __table_args__ = (
        CheckConstraint("length(trim(code)) > 0", name="code_not_empty"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_empty"),
    )

    #: Short: ``APEXMED``, ``GPI``, ``CMG``, ``APM``.
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Mã số thuế, when known.
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


__all__ = ["LEGAL_ENTITIES", "LegalEntity"]
