"""Deciding, without a model, whether text is too personal to post in a group.

The rule this enforces is short: **``PERSONAL_PRIVATE`` and ``SECRET`` content
never reaches a registered group.** Everything else a person deliberately wrote
as an announcement may go, because an announcement is by definition something
they want a room full of colleagues to read.

**Deliberately conservative.** A false positive here blocks a legitimate
announcement and teaches somebody that MeoBot argues with them; a false
negative posts a salary into a group of twenty people. The two are not
symmetric, so the markers are specific phrases rather than single common words:
"lương" alone appears in "lịch trả lương tháng này đã có" - which is exactly the
sort of thing a department announcement says - while "bảng lương của" does not.

Nothing here removes or rewrites content. When a marker matches, the sender is
told what was noticed and offered the three honest choices: edit it, send it
privately instead, or drop it. Silently stripping a sentence out of somebody's
message would be worse than either sending or refusing.
"""

from __future__ import annotations

from dataclasses import dataclass

from meobot.domain.dispatch.phrases import fold
from meobot.domain.notifications.models import PrivacyClassification

#: Credentials and things that behave like them. Any hit is ``SECRET``: there
#: is no announcement that needs to carry one, so the false-positive cost is
#: somebody rephrasing one sentence.
SECRET_MARKERS: tuple[str, ...] = (
    "mat khau",
    "password",
    "api key",
    "api_key",
    "access token",
    "refresh token",
    "ma otp",
    "ma xac thuc",
    "khoa bi mat",
    "private key",
    "secret key",
)

#: One person's own business. Phrases rather than words, so a schedule
#: announcement about payday does not read as a payslip.
PERSONAL_MARKERS: tuple[str, ...] = (
    "bang luong cua",
    "muc luong cua",
    "luong cua ban",
    "luong cua anh",
    "luong cua chi",
    "luong thang nay cua",
    "so tai khoan",
    "so tk",
    "so the ngan hang",
    "can cuoc cong dan",
    "chung minh nhan dan",
    "so cccd",
    "so cmnd",
    "benh an",
    "ho so benh",
    "ky luat ca nhan",
    "quyet dinh sa thai",
    "danh gia ca nhan cua",
    "so dien thoai ca nhan",
    "dia chi nha rieng",
)


@dataclass(frozen=True, slots=True)
class PrivacyVerdict:
    """What a piece of announcement text was classified as, and why.

    ``marker`` is the phrase that matched, kept so the refusal can be specific
    without quoting the sender's whole message back at them.
    """

    classification: PrivacyClassification
    marker: str = ""

    @property
    def may_reach_a_group(self) -> bool:
        return self.classification.may_reach_a_group


def classify_announcement(content: str) -> PrivacyVerdict:
    """Classify text a person wants sent to one or more groups.

    Returns ``PUBLIC_OPERATIONAL`` for anything that matches no marker, which
    is the overwhelming majority: somebody writing a department announcement is
    doing a public-operational thing, and treating that as suspect by default
    would make the feature unusable.
    """
    folded = fold(content)
    if not folded:
        return PrivacyVerdict(classification=PrivacyClassification.PUBLIC_OPERATIONAL)

    for marker in SECRET_MARKERS:
        if marker in folded:
            return PrivacyVerdict(classification=PrivacyClassification.SECRET, marker=marker)
    for marker in PERSONAL_MARKERS:
        if marker in folded:
            return PrivacyVerdict(
                classification=PrivacyClassification.PERSONAL_PRIVATE, marker=marker
            )
    return PrivacyVerdict(classification=PrivacyClassification.PUBLIC_OPERATIONAL)
