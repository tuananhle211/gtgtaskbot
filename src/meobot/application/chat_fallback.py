"""What MeoBot says when the model cannot answer.

This module is the whole point of the incident that produced it. The old
fallback was one sentence for every failure:

    "Mình chưa xử lý được câu này (trợ lý AI đang trục trặc). Bạn thử nhắn lại
    ngắn gọn hơn, hoặc dùng /help để xem các lệnh nhé."

Sent in reply to "Hello", that sentence is wrong twice over. It blames the
user's message when the provider was at fault, and there is no shorter way to
write "Hello", so the advice cannot be followed.

The replacement is built from the same data the real answer would have used -
the assistant profile, the actor profile, the live capability report - so a
deterministic reply is a *worse* answer than the model's, not a different kind
of thing. Only when even that is impossible does MeoBot admit the outage, and
it does so accurately: it names what still works and gives a reference id.

Nothing here reaches a provider, a database or a network.
"""

from __future__ import annotations

from dataclasses import dataclass

from meobot.application.capability_service import CapabilityReport
from meobot.domain.assistant.profile import AssistantProfile
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.profile import ActorProfile

#: Sent only when routing, generation, the retry and every canned reply have
#: all failed. Names what still works, and carries a correlation id so a log
#: line can be found from a screenshot.
PROVIDER_DOWN_TEMPLATE = (
    "Mình đang gặp lỗi kết nối với mô hình AI nên chưa thể trả lời đầy đủ. "
    "Các lệnh vận hành vẫn hoạt động. Mã tham chiếu lỗi: {reference}."
)


@dataclass(frozen=True, slots=True)
class DeterministicReplies:
    """Answers assembled from configuration rather than from a model.

    Args:
        assistant: MeoBot's own profile - the source of its self-description.
        profile: Who is being spoken to, including how to address them.
        report: The live capability report for this actor.
    """

    assistant: AssistantProfile
    profile: ActorProfile
    report: CapabilityReport

    # --- Openers ----------------------------------------------------------
    def greeting(self) -> str:
        """The answer to "Hello" when no model is available."""
        name = self.assistant.assistant_name
        return (
            f"Chào {self.profile.address}, mình là {name} — {self.assistant.identity} "
            f"Hôm nay {self.profile.address} muốn bắt đầu với công việc, kịch bản hay "
            "một ý tưởng mới?"
        )

    def identity(self) -> str:
        """The answer to "Bạn là ai?"."""
        lines = [
            f"Mình là {self.assistant.assistant_name}. {self.assistant.identity}",
            f"Nhiệm vụ của mình: {self.assistant.mission}",
        ]
        workspace = self.assistant.workspace_line()
        if workspace:
            lines.append(f"Mình làm việc cùng {workspace}.")
        if self.assistant.operating_domains:
            lines.append("Mảng mình bám: " + ", ".join(self.assistant.operating_domains) + ".")
        return "\n\n".join(lines)

    def actor(self) -> str:
        """The answer to "Bạn đang nói chuyện với ai?"."""
        lines = [f"Mình đang nói chuyện với {self.profile.short_name}."]
        details = [
            ("Chức danh", self.profile.job_title),
            ("Tổ chức", self.profile.organization),
            ("Phòng ban", self.profile.department),
        ]
        lines.extend(f"{label}: {value}" for label, value in details if value)
        lines.append(f"Vai trò trong hệ thống: {role_label(self.profile.role)}")
        if self.profile.responsibilities:
            lines.append("Phụ trách: " + ", ".join(self.profile.responsibilities) + ".")
        lines.append(
            f"Mình gọi {self.profile.address} là '{self.profile.address}'. "
            "Muốn đổi thì dùng /set_preferred_address."
        )
        return "\n".join(lines)

    # --- Capabilities -----------------------------------------------------
    def capabilities(self) -> str:
        """The answer to "Bạn làm được gì?", from the live registry.

        Four buckets, and the distinction between them is the answer: what can
        happen now, what needs a credential, what was never built, and what this
        person specifically is not allowed to ask for.
        """
        blocks: list[str] = [
            f"Đây là những gì mình làm được với quyền {role_label(self.profile.role)} của "
            f"{self.profile.address}:"
        ]
        sections = (
            ("✅ Có thể làm ngay", self.report.available_now),
            ("⚙️ Có thể làm sau khi cấu hình", self.report.needs_configuration),
            ("🚧 Chưa được xây dựng", self.report.not_implemented),
            (
                f"🔒 {self.profile.address.capitalize()} không có quyền sử dụng",
                self.report.not_permitted,
            ),
        )
        for title, items in sections:
            if not items:
                continue
            blocks.append(title + ":\n" + "\n".join(f"• {item}" for item in items))
        blocks.append("Muốn bắt đầu từ đâu?")
        return "\n\n".join(blocks)

    def limitations(self) -> str:
        """What MeoBot cannot do, and why not."""
        blocks: list[str] = []
        if self.report.needs_configuration:
            blocks.append(
                "Cần cấu hình thêm:\n"
                + "\n".join(f"• {item}" for item in self.report.needs_configuration)
            )
        if self.report.not_implemented:
            blocks.append(
                "Chưa được xây dựng:\n"
                + "\n".join(f"• {item}" for item in self.report.not_implemented)
            )
        return "\n\n".join(blocks) or "Hiện chưa có giới hạn nào được ghi nhận."

    # --- General ----------------------------------------------------------
    def conversational(self, message: str) -> str:
        """A useful reply to an ordinary message with no model available.

        Deliberately not an apology. It names what MeoBot understood, offers the
        next step, and leaves the user somewhere to go.
        """
        if _mentions_ideas(message):
            return (
                "Mình có thể cùng bạn tìm hướng. Đây là nội dung cho "
                f"{self.assistant.organization_name or 'thương hiệu'}, một kênh bác sĩ, "
                "hay thương hiệu cá nhân?"
            )
        return (
            f"Mình nghe rồi. Để bám đúng ý {self.profile.address}, cho mình biết đây là "
            "việc cho kênh nào và mục tiêu là gì — mình sẽ đi thẳng vào hướng triển khai."
        )

    def clarification(self, missing: str | None) -> str:
        """One question, when a real operation is missing its target."""
        target = missing or "đối tượng cụ thể"
        return (
            f"Mình chưa rõ {target}. Bạn nói cụ thể giúp mình nhé — mình không tự đoán "
            "để tránh thao tác nhầm vào dữ liệu thật."
        )

    @staticmethod
    def provider_down(reference: str) -> str:
        """The last resort. Honest about the failure, honest about the scope."""
        return PROVIDER_DOWN_TEMPLATE.format(reference=reference)


#: Words that mean "I am stuck for ideas" - the one ordinary message worth
#: answering specifically rather than generically.
_IDEA_WORDS: tuple[str, ...] = (
    "ý tưởng",
    "y tuong",
    "brainstorm",
    "bí",
    "bi y",
    "content",
    "hướng nội dung",
)


def _mentions_ideas(message: str) -> bool:
    lowered = message.lower()
    return any(word in lowered for word in _IDEA_WORDS)
