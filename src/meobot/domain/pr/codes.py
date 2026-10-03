"""What a PR code looks like, and which counter it comes from.

Pure formatting and vocabulary. The allocation - the part that has to be safe
under concurrency - is
:class:`~meobot.application.pr_code_service.PrCodeService`; keeping the shape
of a code here means the format can be tested without a database, and means
there is exactly one place that knows ``CNT-2026-000001`` has six digits.

Codes are what people say to each other and what a report prints. They are
**not** keys: every relationship in the PR module is a UUID, so a code is a
label on a row and nothing depends on its structure. That is what makes it
safe for them to be human-shaped.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class PrCodeNamespace(StrEnum):
    """One counter's name. Stored in ``pr_code_counters.namespace``."""

    CHANNEL = "CHANNEL"
    CONTENT = "CONTENT"
    TASK = "TASK"
    PUBLICATION = "PUBLICATION"
    ISSUE = "ISSUE"
    #: M1. One counter for the Work Ledger. Year-scoped like content and tasks:
    #: ``WRK-2026-000001`` is the first job of 2026, which is how the department
    #: refers to a period's work.
    WORK = "WORK"


@dataclass(frozen=True, slots=True)
class PrCodeFormat:
    """How one namespace turns a number into a code.

    ``yearly`` decides two things at once, and they are the same decision:
    whether the counter resets each January, and whether the year appears in
    the code. A channel is a standing asset - ``CH-0007`` is the seventh
    channel the team ever registered, not the seventh this year - so it has
    neither.
    """

    prefix: str
    digits: int
    yearly: bool

    def render(self, value: int, *, year: int | None) -> str:
        """Format one allocated number.

        Raises:
            ValueError: The number is below 1, or ``year`` disagrees with
                :attr:`yearly`. Both are programming errors rather than user
                input, which is why they are ``ValueError`` and not a domain
                error - no caller can provoke them from outside.
        """
        if value < 1:
            raise ValueError(f"code number must be at least 1, got {value}")
        if self.yearly:
            if year is None:
                raise ValueError(f"{self.prefix} codes are year-scoped and need a year")
            return f"{self.prefix}-{year:04d}-{value:0{self.digits}d}"
        if year is not None:
            raise ValueError(f"{self.prefix} codes are not year-scoped; got year={year}")
        return f"{self.prefix}-{value:0{self.digits}d}"


#: The five formats this step supports.
#:
#: ``CH-0001`` · ``CNT-2026-000001`` · ``TSK-2026-000001`` ·
#: ``PUB-2026-000001`` · ``ISS-2026-000001``
#:
#: Four digits for channels and six for the rest, because a team registers
#: channels in the dozens and creates content in the thousands. Neither is a
#: hard limit: :meth:`PrCodeFormat.render` zero-pads to *at least* that width
#: and an overflowing counter simply produces a longer code rather than
#: wrapping or failing. ``pr_*.code`` is ``VARCHAR(64)``, which is far more
#: room than either will ever need.
CODE_FORMATS: Mapping[PrCodeNamespace, PrCodeFormat] = MappingProxyType(
    {
        PrCodeNamespace.CHANNEL: PrCodeFormat(prefix="CH", digits=4, yearly=False),
        PrCodeNamespace.CONTENT: PrCodeFormat(prefix="CNT", digits=6, yearly=True),
        PrCodeNamespace.TASK: PrCodeFormat(prefix="TSK", digits=6, yearly=True),
        PrCodeNamespace.PUBLICATION: PrCodeFormat(prefix="PUB", digits=6, yearly=True),
        PrCodeNamespace.ISSUE: PrCodeFormat(prefix="ISS", digits=6, yearly=True),
        PrCodeNamespace.WORK: PrCodeFormat(prefix="WRK", digits=6, yearly=True),
    }
)

#: What a rendered code looks like, for a test or a client that wants to
#: recognise one. Never used to parse a code back into a number: the code is a
#: label, and reading meaning out of its digits would make the format load
#: bearing.
CODE_PATTERN: re.Pattern[str] = re.compile(r"^(CH-\d{4,}|(?:CNT|TSK|PUB|ISS|WRK)-\d{4}-\d{6,})$")


def format_code(namespace: PrCodeNamespace, value: int, *, year: int | None = None) -> str:
    """Render one allocated number in its namespace's format."""
    return CODE_FORMATS[namespace].render(value, year=year)


def is_year_scoped(namespace: PrCodeNamespace) -> bool:
    """True when this namespace's counter restarts each calendar year."""
    return CODE_FORMATS[namespace].yearly


__all__: list[str] = [
    "CODE_FORMATS",
    "CODE_PATTERN",
    "PrCodeFormat",
    "PrCodeNamespace",
    "format_code",
    "is_year_scoped",
]
