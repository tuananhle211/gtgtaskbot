"""Phòng họp agent (0093): several agents talk in turns, people read and write.

A room has an owner, two to :data:`MAX_ROOM_AGENTS` agents in a set order, one
of them the chair ("chủ trì"), and people the owner invites. A **round** runs
when the owner writes (or presses "Họp tiếp"): the chair opens (sums up what
is asked, says what each member should bring), every other agent speaks once
in order, the chair closes (conclusion, actions, what a person must decide).
People's messages written meanwhile reach the agents who speak after them.

Each agent keeps its own Hermes transcript of the room, so a turn sends only
what was said since that agent last spoke, plus the instruction for its step.

Who may be in a room follows the rule of the Second Brain: what an agent reads
must not be wider than what every person talking to it may read - so a person
joins only if they may chat with every agent of the room, and an agent joins
only if every person of the room may chat with it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

MIN_ROOM_AGENTS = 2
MAX_ROOM_AGENTS = 6
MAX_ROOM_PEOPLE = 30
MAX_ROOM_TITLE = 120
#: Messages a person may write in rooms within the rate window.
ROOM_RATE_LIMIT = 20
#: The words of one agent turn the prompt asks for (the model may go over).
TURN_WORDS = 200


class RoomSpeaker(StrEnum):
    PERSON = "PERSON"
    AGENT = "AGENT"


class RoomStep(StrEnum):
    """Which part of a round an agent's message is."""

    #: The chair opens the round.
    OPEN = "OPEN"
    #: A member speaks.
    SPEAK = "SPEAK"
    #: The chair closes the round.
    CLOSE = "CLOSE"


STEP_LABELS: dict[RoomStep, str] = {
    RoomStep.OPEN: "Mở đầu",
    RoomStep.SPEAK: "Phát biểu",
    RoomStep.CLOSE: "Kết luận",
}


@dataclass(frozen=True, slots=True)
class TurnAgent:
    """One agent of a room as a turn needs it."""

    room_agent_id: str
    slug: str
    name: str
    title: str


@dataclass(frozen=True, slots=True)
class Said:
    """One message of the room, as another agent reads it."""

    speaker: str
    content: str


def round_plan(agents: Sequence[TurnAgent], chair_slug: str) -> list[tuple[TurnAgent, RoomStep]]:
    """Who speaks, in which step: the chair, every other agent in order, the chair."""
    chair = next((seat for seat in agents if seat.slug == chair_slug), None)
    if chair is None:
        return [(seat, RoomStep.SPEAK) for seat in agents]
    others = [seat for seat in agents if seat.slug != chair_slug]
    return [
        (chair, RoomStep.OPEN),
        *((seat, RoomStep.SPEAK) for seat in others),
        (chair, RoomStep.CLOSE),
    ]


def room_session_id(room_agent_id: object) -> str:
    """The Hermes transcript of one agent in one room."""
    return f"apm-room-{room_agent_id}"


def room_session_key(room_id: object, slug: str) -> str:
    """The Hermes memory scope of one agent in one room."""
    return f"apm:room:{room_id}:{slug}"


def room_briefing(
    *,
    title: str,
    seat: TurnAgent,
    chair: TurnAgent | None,
    agents: Sequence[TurnAgent],
    people: Sequence[str],
) -> str:
    """What an agent reads the first time it speaks in a room."""
    others = ", ".join(
        f"{a.name} ({a.title})" if a.title else a.name for a in agents if a.slug != seat.slug
    )
    chair_line = (
        "Bạn là chủ trì cuộc họp."
        if chair is not None and chair.slug == seat.slug
        else f"Chủ trì: {chair.name}."
        if chair is not None
        else "Cuộc họp không có chủ trì."
    )
    return (
        f'[PHÒNG HỌP APM: "{title}"]\n'
        f"Bạn là {seat.name}{f' ({seat.title})' if seat.title else ''}, đang họp cùng "
        f"các agent khác của Thẩm mỹ Apexmed qua APM Management. {chair_line}\n"
        f"Agent cùng họp: {others or 'không có'}.\n"
        f"Người dự họp: {', '.join(people) or 'không có'}.\n"
        "Luật họp:\n"
        "- Chỉ nói phần của mình; không nói thay, không đóng vai agent hay người khác.\n"
        f"- Ngắn gọn, khoảng {TURN_WORDS} chữ, đi thẳng vào chuyên môn của bạn.\n"
        "- Đáp lại ý của ai thì gọi tên người / agent đó; không lặp ý đã có.\n"
        "- Đây là thảo luận nội bộ: đề xuất, không tự làm việc cần người duyệt.\n"
        "- Thiếu số liệu thì nói thiếu, không đoán."
    )


def step_instruction(step: RoomStep, seat: TurnAgent) -> str:
    if step is RoomStep.OPEN:
        return (
            "ĐẾN LƯỢT BẠN - MỞ ĐẦU VÒNG HỌP. Tóm tắt điều cần bàn từ các tin mới nhất, rồi nêu rõ "
            "từng agent cần đóng góp gì (gọi tên). Không trả lời thay họ."
        )
    if step is RoomStep.CLOSE:
        return (
            "ĐẾN LƯỢT BẠN - KẾT LUẬN VÒNG HỌP. Tổng kết các ý đã nêu, chốt kết luận / đề xuất, "
            "liệt kê việc cần làm (ai - việc - hạn nếu có) và điểm nào còn cần người quyết."
        )
    role = f" ({seat.title})" if seat.title else ""
    return (
        f"ĐẾN LƯỢT BẠN{role}. Trả lời phần chủ trì giao cho bạn và các ý mới, "
        "từ góc nhìn chuyên môn của bạn."
    )


def turn_prompt(
    *, briefing: str | None, said: Sequence[Said], step: RoomStep, seat: TurnAgent
) -> str:
    """One turn: the briefing (first turn only), what was said since, the instruction."""
    parts: list[str] = []
    if briefing:
        parts.append(briefing)
    if said:
        lines = "\n\n".join(f"[{item.speaker}]: {item.content.strip()}" for item in said)
        parts.append(f"--- Diễn biến mới trong phòng ---\n{lines}")
    else:
        parts.append("--- Không có ý mới từ lần bạn nói trước ---")
    parts.append(step_instruction(step, seat))
    return "\n\n".join(parts)


__all__ = [
    "MAX_ROOM_AGENTS",
    "MAX_ROOM_PEOPLE",
    "MAX_ROOM_TITLE",
    "MIN_ROOM_AGENTS",
    "ROOM_RATE_LIMIT",
    "STEP_LABELS",
    "TURN_WORDS",
    "RoomSpeaker",
    "RoomStep",
    "Said",
    "TurnAgent",
    "room_briefing",
    "room_session_id",
    "room_session_key",
    "round_plan",
    "step_instruction",
    "turn_prompt",
]
