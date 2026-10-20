"""``K7Q2M``: the order code, allocated once and kept for life (2026-10-20).

Five characters, capital letters and digits, picked at random and never
reused - short enough to say aloud and type, and it tells nobody how many
orders there are or whose this is. The alphabet leaves out the look-alikes
(0/O, 1/I/L), and a code always mixes letters and digits so it never reads
as a word or a number.

Orders placed before this keep their long codes (``TUAN-D-261003-01``): a
code is in links, messages and history. ``order_code_counters`` is no longer
written.

Unique by checking every order and unified task code before handing one
out; ``orders.code`` is unique, so a near-impossible race between two
requests ends in an error, never in two orders sharing a code.
"""

from __future__ import annotations

import secrets

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.order import Order
from meobot.db.models.task import Task
from meobot.domain.orders.errors import OrderValidationError

logger = get_logger(__name__)

#: Capital letters and digits without 0, O, 1, I, L.
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 5
#: Tries before giving up (each one is a ~1 in 28 million collision).
MAX_TRIES = 30


def random_order_code() -> str:
    """Five characters from :data:`CODE_ALPHABET`, letters and digits mixed."""
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if any(char.isdigit() for char in code) and any(char.isalpha() for char in code):
            return code


class OrderCodeService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def allocate(self) -> str:
        """A fresh code no order or task carries. Must run on the order's session."""
        for _ in range(MAX_TRIES):
            code = random_order_code()
            taken = await self._session.scalar(
                select(Order.id).where(Order.code == code).limit(1)
            ) or await self._session.scalar(
                select(Task.id).where(or_(Task.code == code, Task.code == code.lower())).limit(1)
            )
            if taken is None:
                logger.info("order_code_allocated", extra={"order_code": code})
                return code
        raise OrderValidationError(
            "Chưa sinh được mã order, vui lòng thử lại.",
            details={"reason": "order_code_unavailable"},
        )


__all__ = ["CODE_ALPHABET", "CODE_LENGTH", "OrderCodeService", "random_order_code"]
