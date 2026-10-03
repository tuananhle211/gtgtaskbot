"""Every card the multi-group dispatch flow draws, in one place.

Kept out of the handler for the reason the copy registry exists: wording that
lives next to the code that sends it drifts, and a rule like "a person never
sees a numeric chat id" cannot be *checked* when the text is scattered.

Three cards do the work:

* the **registry list**, which is also a picker;
* the **multi-select keyboard**, whose buttons carry their own state so the
  screen is the source of truth about what is ticked;
* the **preview**, which is the last thing anybody sees before an outbox row
  exists, and therefore has to name every destination in full.

Nothing here touches a database or Telegram. It turns rows into text and
:class:`~meobot.application.member_interaction_service.ButtonSpec` rows, which
is what makes the wording testable without a Dispatcher.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from meobot.application.chat_registry_service import aliases_of, tags_of
from meobot.application.member_interaction_service import ButtonSpec
from meobot.bot import formatting
from meobot.db.models.dispatch import MessageDispatchDraftRecipient
from meobot.db.models.notifications import TelegramChat
from meobot.domain.dispatch.models import (
    DispatchRecipientStatus,
    SelectionSource,
    dispatch_recipient_status_label,
)
from meobot.domain.member import copy
from meobot.domain.notifications.models import DestinationHealth, health_label

#: How many groups fit on one page of the registry list. A phone screen, not a
#: database limit.
PAGE_SIZE = 5

NOTHING_REGISTERED = (
    "Chưa có group nào được đăng ký.\n"
    "Bạn vào group cần dùng và nhắn “đăng ký group này” giúp mình nhé."
)

NOTHING_VISIBLE = (
    "Hiện chưa có group nào bạn được phép gửi.\n"
    "Bạn nhờ Trưởng phòng gán quyền quản lý group giúp bạn nhé."
)

NO_SELECTION = "Bạn chưa chọn group nào. Bạn chọn ít nhất một group giúp mình nhé."

ASK_WHERE = "Bạn muốn gửi tới đâu?"

DRAFT_EXPIRED = (
    "Thông báo bạn soạn trước đó đã quá hạn nên MeoBot không gửi nữa.\n"
    "Bạn nhắn lại nội dung và nơi nhận giúp mình nhé."
)

STILL_CHOOSING = (
    "Bạn chọn group nhận thông báo bằng các nút phía trên, "
    "hoặc nhắn “tất cả”, “hai group đầu”, “bỏ <tên group>” giúp mình nhé."
)

SELECTION_MOVED = (
    "Danh sách nơi nhận đã thay đổi sau khi thẻ này hiện ra, "
    "nên MeoBot chưa gửi. Bạn xem lại và xác nhận giúp mình nhé."
)

AWAITING_CONFIRM = (
    "Bạn nhắn “xác nhận” hoặc bấm nút phía trên để MeoBot gửi thông báo này, "
    "hoặc nhắn “huỷ” nếu không gửi nữa."
)

PRIVATE_CONTENT = "Thông báo này có thể chứa thông tin cá nhân và không phù hợp để gửi vào group."


@dataclass(frozen=True, slots=True)
class Card:
    """One message MeoBot is about to draw: its text and its buttons."""

    text: str
    buttons: list[list[ButtonSpec]]


def _health_line(row: TelegramChat) -> str:
    """One destination's state, as a sentence rather than a status name."""
    if not row.is_active:
        return "Đang tạm dừng"
    if not row.allow_automated_delivery:
        return "Không nhận thông báo tự động"
    if not row.bot_can_send:
        return "MeoBot hiện không có quyền gửi tin trong group này"
    return health_label(row.health_status)


def _names_line(row: TelegramChat) -> str:
    """The other names this group answers to, so people know what they may say."""
    names = [name for name in (row.telegram_title, *aliases_of(row)) if name]
    unique: list[str] = []
    for name in names:
        if name != row.display_name and name not in unique:
            unique.append(name)
    return ", ".join(unique)


def registry_list(
    rows: Sequence[TelegramChat], *, page: int = 1, page_size: int = PAGE_SIZE
) -> Card:
    """ "Xem các group đã đăng ký" - and an offer to pick from it.

    Never shows a numeric Telegram chat id. The number in front of each group
    is its position on *this card*, which is what "ba group trên" counts off;
    it is not an identifier and it is not stable between cards.
    """
    if not rows:
        return Card(text=formatting.escape(NOTHING_REGISTERED), buttons=[])

    pages = max(1, (len(rows) + page_size - 1) // page_size)
    current = min(max(page, 1), pages)
    start = (current - 1) * page_size
    window = list(rows)[start : start + page_size]

    lines = ["👥 " + formatting.bold("CÁC GROUP ĐÃ ĐĂNG KÝ"), ""]
    for offset, row in enumerate(window, start=start + 1):
        lines.append(formatting.escape(f"{offset}. {row.display_name}"))
        lines.append(formatting.escape(f"   Trạng thái: {_health_line(row)}"))
        other = _names_line(row)
        if other:
            lines.append(formatting.escape(f"   Tên gọi: {other}"))
        labels = ", ".join(tags_of(row))
        if labels:
            lines.append(formatting.escape(f"   Nhãn: {labels}"))
        lines.append("")
    if pages > 1:
        lines.append(formatting.escape(f"Trang {current}/{pages}"))
        lines.append("")
    lines.append(formatting.escape(ASK_WHERE))

    buttons: list[list[ButtonSpec]] = [
        [ButtonSpec("✅ Chọn tất cả", "disp.list.all")],
        [ButtonSpec("☑️ Chọn từng group", "disp.list.each")],
        [ButtonSpec("🟢 Chỉ group hoạt động", "disp.list.healthy")],
    ]
    if pages > 1:
        paging: list[ButtonSpec] = []
        if current > 1:
            paging.append(ButtonSpec("⬅️ Trang trước", "disp.page"))
        if current < pages:
            paging.append(ButtonSpec("➡️ Trang sau", "disp.page"))
        buttons.append(paging)
    buttons.append([ButtonSpec(copy.Button.DISCARD.value, "disp.cancel")])
    return Card(text="\n".join(lines).rstrip(), buttons=buttons)


def selection_keyboard(
    recipients: Sequence[MessageDispatchDraftRecipient],
) -> list[list[ButtonSpec]]:
    """The multi-select keyboard, with each button carrying its own state.

    A ticked group's button asks to untick it and an unticked one's asks to
    tick it. That is what makes a double tap idempotent: the second press of
    the same physical button is a no-op rather than an undo.
    """
    rows: list[list[ButtonSpec]] = []
    for row in recipients:
        if not row.permitted:
            rows.append([ButtonSpec(f"🔒 {row.display_name}", "disp.detail", str(row.id))])
            continue
        mark = "☑️" if row.selected else "⬜"
        action = "disp.deselect" if row.selected else "disp.select"
        rows.append([ButtonSpec(f"{mark} {row.display_name}", action, str(row.id))])
    rows.append([ButtonSpec("✅ Xong", "disp.done")])
    rows.append(
        [ButtonSpec("☑️ Chọn tất cả", "disp.all"), ButtonSpec("⬜ Bỏ chọn tất cả", "disp.none")]
    )
    rows.append([ButtonSpec(copy.Button.DISCARD.value, "disp.cancel")])
    return rows


def selection_card(
    recipients: Sequence[MessageDispatchDraftRecipient], *, heading: str | None = None
) -> Card:
    """ "Chọn các group nhận thông báo" - the picker itself."""
    chosen = sum(1 for row in recipients if row.selected)
    lines = [formatting.escape(heading or "Chọn các group nhận thông báo:")]
    if chosen:
        lines.append("")
        lines.append(formatting.escape(f"Đang chọn {chosen} group."))
    locked = [row for row in recipients if not row.permitted]
    if locked:
        lines.append("")
        lines.append(
            formatting.escape(
                f"{len(locked)} group bạn chưa được phép gửi nên MeoBot không cho chọn."
            )
        )
    return Card(text="\n".join(lines), buttons=selection_keyboard(recipients))


def inferred_card(phrase: str, recipients: Sequence[MessageDispatchDraftRecipient]) -> Card:
    """ "MeoBot hiểu ‘các group Content’ là" - the reading, offered for checking.

    Shown whenever the destinations were *inferred* rather than named. A person
    who typed a description has not seen the list it expands to, and confirming
    something you have not read is not confirming.
    """
    lines = [formatting.escape(f"MeoBot hiểu “{phrase}” là:"), ""]
    lines.extend(formatting.escape(f"• {row.display_name}") for row in recipients)
    buttons = [
        [ButtonSpec(f"✅ Dùng {len(recipients)} group này", "disp.done")],
        [ButtonSpec("☑️ Chọn lại", "disp.reselect")],
        [ButtonSpec(copy.Button.DISCARD.value, "disp.cancel")],
    ]
    return Card(text="\n".join(lines), buttons=buttons)


def scope_card(*, usable: int, paused: int, unreachable: int, recipients: Sequence[object]) -> Card:
    """What "tất cả group" turned out to mean, with the exclusions counted.

    The counts are the point. "Bạn đang chọn 12 group" on its own hides that
    two were skipped, and somebody who wanted all fourteen would never find
    out.
    """
    lines = ["MeoBot tìm thấy:", "", formatting.escape(f"• {usable} group có thể gửi")]
    if paused:
        lines.append(formatting.escape(f"• {paused} group đang tạm dừng"))
    if unreachable:
        lines.append(formatting.escape(f"• {unreachable} group không có quyền gửi"))
    lines.append("")
    lines.append(formatting.escape(f"Bạn đang chọn {len(recipients)} group hoạt động."))
    buttons = [
        [ButtonSpec(f"✅ Tiếp tục với {len(recipients)} group", "disp.done")],
        [ButtonSpec("🔍 Xem danh sách", "disp.detail")],
        [ButtonSpec("☑️ Chọn lại", "disp.reselect")],
        [ButtonSpec(copy.Button.DISCARD.value, "disp.cancel")],
    ]
    return Card(text="\n".join(lines), buttons=buttons)


def preview(
    *,
    content: str,
    recipients: Sequence[MessageDispatchDraftRecipient],
    sender_label: str,
    health_lines: Sequence[str] = (),
    excluded: Sequence[str] = (),
    unresolved: Sequence[str] = (),
    part_count: int = 1,
    draft_id: str = "",
) -> Card:
    """The mandatory look-before-you-send card, for one destination or twelve.

    Every destination is named in full. A count on its own - "gửi tới 3 group" -
    reads as confirmation without being one, because the person cannot check a
    number against what they meant.
    """
    lines = [
        "📢 " + formatting.bold("THÔNG BÁO"),
        "",
        formatting.bold("Nội dung:"),
        "",
        formatting.escape(content),
        "",
        formatting.bold(f"Nơi nhận — {len(recipients)} group:"),
    ]
    lines.extend(formatting.escape(f"• {row.display_name}") for row in recipients)
    lines.extend(["", formatting.bold("Người gửi:"), formatting.escape(f"• {sender_label}")])
    if health_lines:
        lines.extend(["", formatting.bold("Trạng thái nơi nhận:")])
        lines.extend(formatting.escape(f"• {line}") for line in health_lines)
    if part_count > 1:
        lines.extend(
            [
                "",
                formatting.escape(
                    f"Thông báo dài nên MeoBot sẽ gửi thành {part_count} tin liên tiếp, "
                    "theo đúng thứ tự."
                ),
            ]
        )
    if excluded:
        lines.extend(["", formatting.bold("Không gửi vào:")])
        lines.extend(formatting.escape(f"• {name}") for name in excluded)
    if unresolved:
        lines.extend(["", formatting.bold("MeoBot chưa tìm thấy:")])
        lines.extend(formatting.escape(f"• {name}") for name in unresolved)

    buttons = [
        [ButtonSpec(f"✅ Gửi tới {len(recipients)} group", "disp.send", draft_id)],
        [ButtonSpec("✏️ Sửa nội dung", "disp.edit", draft_id)],
        [ButtonSpec("☑️ Chọn lại group", "disp.reselect", draft_id)],
        [ButtonSpec("🔍 Xem chi tiết", "disp.detail", draft_id)],
        [ButtonSpec(copy.Button.DISCARD.value, "disp.cancel", draft_id)],
    ]
    return Card(text="\n".join(lines), buttons=buttons)


def queued(count: int) -> str:
    """What the source chat is told the moment the outbox rows exist.

    "Xếp hàng gửi", never "đã gửi". An outbox row is a durable intention;
    Telegram settles it afterwards and the result card reports what actually
    happened.
    """
    return f"Thông báo đã được xếp hàng gửi tới {count} group."


def result_card(
    outcomes: Sequence[tuple[str, DispatchRecipientStatus, str]], *, dispatch_id: str
) -> Card:
    """ "📬 KẾT QUẢ GỬI THÔNG BÁO" - per destination, once everything has settled."""
    delivered = [item for item in outcomes if item[1] is DispatchRecipientStatus.DELIVERED]
    failed = [item for item in outcomes if item[1] is not DispatchRecipientStatus.DELIVERED]

    lines = [
        "📬 " + formatting.bold("KẾT QUẢ GỬI THÔNG BÁO"),
        "",
        formatting.escape(f"Đã gửi thành công: {len(delivered)}/{len(outcomes)}"),
    ]
    if delivered:
        lines.append("")
        lines.extend(formatting.escape(f"✅ {name}") for name, _, _ in delivered)
    if failed:
        lines.extend(["", formatting.bold("Chưa gửi được:")])
        for name, status, reason in failed:
            lines.append(formatting.escape(f"⚠️ {name} — {dispatch_recipient_status_label(status)}"))
            if reason:
                lines.append(formatting.escape(reason))

    buttons: list[list[ButtonSpec]] = []
    if failed:
        buttons.append([ButtonSpec("🔄 Thử lại nơi lỗi", "disp.retry", dispatch_id)])
        buttons.append([ButtonSpec("👥 Đổi nơi nhận", "disp.reselect", dispatch_id)])
    buttons.append([ButtonSpec("📄 Xem chi tiết", "disp.summary", dispatch_id)])
    return Card(text="\n".join(lines), buttons=buttons)


def source_label(source: SelectionSource) -> str:
    """How a destination came to be on the list, for the detail card."""
    return {
        SelectionSource.NAMED: "Bạn gọi tên",
        SelectionSource.INFERRED: "MeoBot suy ra từ nhãn của group",
        SelectionSource.ALL_REGISTERED: "Thuộc nhóm “tất cả group”",
        SelectionSource.BUTTON: "Bạn chọn bằng nút",
        SelectionSource.LIST_REFERENCE: "Bạn đếm theo danh sách vừa hiện",
    }[source]


def health_summary(rows: Sequence[TelegramChat]) -> list[str]:
    """One line per distinct destination state, for the preview."""
    healthy = sum(1 for row in rows if row.bot_can_send and row.health_status.is_healthy)
    unknown = sum(
        1 for row in rows if row.bot_can_send and row.health_status is DestinationHealth.UNKNOWN
    )
    broken = len(rows) - healthy - unknown
    lines: list[str] = []
    if healthy:
        lines.append(f"{healthy} group hoạt động bình thường")
    if unknown:
        lines.append(f"{unknown} group chưa được kiểm tra")
    if broken:
        lines.append(f"{broken} group có thể không nhận được")
    return lines
