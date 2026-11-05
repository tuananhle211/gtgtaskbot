"""Đội agent errors. Each carries ``details.reason`` = its code (the web client branches on it)."""

from __future__ import annotations

from meobot.core.errors import (
    AuthorizationError,
    ConflictError,
    MeoBotError,
    NotFoundError,
    ValidationError,
)


class AgentsForbiddenError(AuthorizationError):
    code = "agents_forbidden"

    def __init__(self, message: str = "Chỉ chủ sở hữu quản lý đội agent.") -> None:
        super().__init__(message, details={"reason": self.code})


class AgentNotFoundError(NotFoundError):
    """No such agent, an inactive one, or one this person may not chat with."""

    code = "agent_not_found"

    def __init__(self) -> None:
        super().__init__("Không tìm thấy agent.", details={"reason": self.code})


class ConversationNotFoundError(NotFoundError):
    """No such conversation - or somebody else's: the same answer."""

    code = "conversation_not_found"

    def __init__(self) -> None:
        super().__init__("Không tìm thấy cuộc trò chuyện.", details={"reason": self.code})


class AgentBusyError(ConflictError):
    code = "agent_busy"

    def __init__(self) -> None:
        super().__init__(
            "Agent đang trả lời câu trước. Chờ xong hoặc bấm Dừng.", details={"reason": self.code}
        )


class AgentMessageInvalidError(ValidationError):
    code = "agent_message_invalid"

    def __init__(self, message: str) -> None:
        super().__init__(message, details={"reason": self.code})


class AgentFileInvalidError(ValidationError):
    """An attachment APM cannot take or read (0092): the message says why."""

    code = "agent_file_invalid"

    def __init__(self, message: str) -> None:
        super().__init__(message, details={"reason": self.code})


class AttachmentNotFoundError(NotFoundError):
    """No such attachment - or one in somebody else's conversation."""

    code = "attachment_not_found"

    def __init__(self) -> None:
        super().__init__("Không tìm thấy tệp.", details={"reason": self.code})


class AgentAccessInvalidError(ValidationError):
    code = "agent_access_invalid"

    def __init__(self, message: str) -> None:
        super().__init__(message, details={"reason": self.code})


class AgentRateLimitedError(MeoBotError):
    """Too many messages in a short while. 429."""

    code = "agent_rate_limited"

    def __init__(self) -> None:
        super().__init__(
            "Bạn gửi hơi nhanh. Chờ vài phút rồi gửi tiếp.", details={"reason": self.code}
        )


class AgentsNotConfiguredError(MeoBotError):
    """The relay is not configured on this deployment. 503."""

    code = "agents_not_configured"

    def __init__(self) -> None:
        super().__init__(
            "Chưa kết nối tới máy chủ agent. Báo quản trị viên.", details={"reason": self.code}
        )


__all__ = [
    "AgentAccessInvalidError",
    "AgentBusyError",
    "AgentFileInvalidError",
    "AgentMessageInvalidError",
    "AgentNotFoundError",
    "AgentRateLimitedError",
    "AgentsForbiddenError",
    "AgentsNotConfiguredError",
    "AttachmentNotFoundError",
    "ConversationNotFoundError",
]
