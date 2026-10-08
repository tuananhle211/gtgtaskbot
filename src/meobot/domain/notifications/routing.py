"""Whether a given message may go to a given destination.

Pure rules, no database and no Telegram, so the one question that matters -
*could this leak?* - is answerable by reading a small file and provable by a
test.

The rule that does the work: **a Telegram group may only receive
``PUBLIC_OPERATIONAL`` or ``TEAM_OPERATIONAL``.** Everything else is refused
before an outbox row exists, which is the point. A check performed at delivery
time would already have the private text sitting in a durable table addressed
to a group; a check performed here means the row is never written.
"""

from __future__ import annotations

from dataclasses import dataclass

from meobot.domain.notifications.models import (
    ChatPurpose,
    PrivacyClassification,
    RecipientType,
)


@dataclass(frozen=True, slots=True)
class RouteVerdict:
    """Whether one (classification, destination) pair is allowed."""

    allowed: bool
    #: Machine-readable reason, for logs and audit. Never shown to a user.
    reason: str = ""
    #: What the person who tried should be told. Vietnamese, actionable.
    message: str = ""


REFUSAL_PRIVATE_TO_GROUP = (
    "Nội dung này là thông tin cá nhân nên TasksBot không gửi vào group. "
    "Mình chỉ gửi riêng cho người có thẩm quyền."
)

REFUSAL_SECRET = "Nội dung này không được phép gửi qua Telegram."  # noqa: S105

REFUSAL_DISABLED = (
    "Nơi nhận này đang tắt nên TasksBot chưa gửi được. "
    "Bạn bật lại hoặc chọn nơi nhận khác giúp mình nhé."
)

REFUSAL_CANNOT_SEND = (
    "TasksBot hiện không có quyền gửi tin trong group này.\n"
    "Quản trị viên cần kiểm tra lại quyền của bot."
)

REFUSAL_NOT_REGISTERED = "Nơi nhận này chưa được đăng ký với TasksBot nên mình chưa gửi được."


def may_route(
    *,
    classification: PrivacyClassification,
    recipient_type: RecipientType,
    destination_allows_automated: bool = True,
    destination_is_active: bool = True,
    destination_can_send: bool = True,
) -> RouteVerdict:
    """Decide whether this message may reach this destination.

    Args:
        classification: How private the payload is.
        recipient_type: A person's private chat, or a registered group.
        destination_allows_automated: The group opted in to automated delivery.
        destination_is_active: The group registration has not been turned off.
        destination_can_send: MeoBot is present and permitted to post.

    Returns:
        A verdict carrying both a log reason and something to tell the user.
    """
    if classification is PrivacyClassification.SECRET:
        # Nothing in this category has a legitimate Telegram destination. It is
        # listed at all so that an accidental attempt is a refusal with an
        # audit trail rather than a message.
        return RouteVerdict(allowed=False, reason="secret_never_routed", message=REFUSAL_SECRET)

    if recipient_type is RecipientType.USER_PRIVATE:
        # A person's own chat may receive anything about them, including the
        # detail a group must never see.
        return RouteVerdict(allowed=True)

    if not classification.may_reach_a_group:
        return RouteVerdict(
            allowed=False,
            reason=f"privacy_{classification.value.lower()}_to_group",
            message=REFUSAL_PRIVATE_TO_GROUP,
        )
    if not destination_is_active:
        return RouteVerdict(allowed=False, reason="destination_inactive", message=REFUSAL_DISABLED)
    if not destination_allows_automated:
        return RouteVerdict(allowed=False, reason="destination_opted_out", message=REFUSAL_DISABLED)
    if not destination_can_send:
        return RouteVerdict(allowed=False, reason="bot_cannot_send", message=REFUSAL_CANNOT_SEND)
    return RouteVerdict(allowed=True)


def broadcast_purposes_for(
    *, is_owner: bool, managed: frozenset[ChatPurpose]
) -> frozenset[ChatPurpose]:
    """Which group purposes this person may address.

    The owner may address any registered group. Anybody else may address only
    the team purposes explicitly assigned to them - which is empty unless a
    deployment has configured it, so a Trưởng nhóm gains nothing by default and
    never gains the department-wide group.
    """
    if is_owner:
        return frozenset(ChatPurpose)
    return frozenset(managed)
