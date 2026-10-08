"""Tool definitions, execution context and registry."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.config import Settings
from meobot.core.errors import ToolArgumentError, ToolNotFoundError
from meobot.domain.identity.models import Actor, Role
from meobot.domain.permissions.matrix import Permission, has_permission
from meobot.domain.policy.models import RiskLevel, ToolPolicy
from meobot.integrations.llm.base import ToolSummary

ArgsT = TypeVar("ArgsT", bound=BaseModel)


class NoArguments(BaseModel):
    """Argument model for tools that take no input."""

    model_config = ConfigDict(extra="forbid")


class ToolResult(BaseModel):
    """What a tool returns to the caller (bot, API or task)."""

    model_config = ConfigDict(frozen=True)

    success: bool = True
    message: str = Field(description="Human-facing text, already in Vietnamese.")
    data: dict[str, Any] = Field(default_factory=dict)
    entity_type: str | None = None
    entity_id: str | None = None


@dataclass(slots=True)
class ToolContext:
    """Everything a tool handler is allowed to touch.

    A handler receives collaborators explicitly; it never reaches for a global
    session or a module-level client.
    """

    actor: Actor
    request_id: uuid.UUID
    settings: Settings
    session: AsyncSession | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def require_session(self) -> AsyncSession:
        """Return the session, or fail loudly if the caller did not supply one."""
        if self.session is None:
            raise RuntimeError("This tool requires a database session in its ToolContext")
        return self.session


ToolHandler = Callable[[ToolContext, Any], Awaitable[ToolResult]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A registered capability.

    Structurally satisfies :class:`~meobot.domain.policy.models.ToolPolicy`, so
    the policy engine can consume the registry without importing this module.
    """

    name: str
    description: str
    handler: ToolHandler
    arguments_model: type[BaseModel] = NoArguments
    risk_level: RiskLevel = RiskLevel.HIGH
    required_permission: Permission | None = None
    destructive: bool = False
    requires_ownership: bool = False
    min_role: Role | None = None
    read_only: bool = True

    def parse_arguments(self, raw: Mapping[str, Any]) -> BaseModel:
        """Validate LLM-supplied arguments against this tool's schema.

        Raises:
            ToolArgumentError: When the arguments do not fit the schema. The
                LLM never gets to pass unvalidated data to a handler.
        """
        try:
            return self.arguments_model.model_validate(dict(raw))
        except PydanticValidationError as exc:
            messages = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
            raise ToolArgumentError(
                f"Tham số không hợp lệ cho công cụ {self.name!r}: {messages}",
                details={"tool_name": self.name},
            ) from exc

    async def execute(self, context: ToolContext, raw_arguments: Mapping[str, Any]) -> ToolResult:
        """Validate arguments and run the handler."""
        arguments = self.parse_arguments(raw_arguments)
        return await self.handler(context, arguments)

    def to_summary(self) -> ToolSummary:
        """Describe this tool to an LLM (schema only, no handler)."""
        return ToolSummary(
            name=self.name,
            description=self.description,
            risk_level=self.risk_level,
            arguments_schema=self.arguments_model.model_json_schema(),
        )


class ToolRegistry:
    """Name -> :class:`ToolDefinition` lookup with role-aware listing."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> ToolDefinition:
        """Register ``tool``.

        Raises:
            ValueError: When a tool with the same name is already registered.
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name!r}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> ToolDefinition:
        """Return a tool by name.

        Raises:
            ToolNotFoundError: When the name is unknown.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(f"Không tìm thấy công cụ {name!r}", details={"tool_name": name})
        return tool

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[ToolDefinition]:
        """Every registered tool, in registration order."""
        return iter(self._tools.values())

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def policies(self) -> Mapping[str, ToolPolicy]:
        """Policy metadata for :class:`~meobot.domain.policy.engine.PolicyEngine`."""
        return dict(self._tools)

    def visible_to(self, role: Role) -> list[ToolDefinition]:
        """Tools whose permission and minimum role the given role satisfies."""
        visible: list[ToolDefinition] = []
        for tool in self._tools.values():
            if tool.min_role is not None and role.rank < tool.min_role.rank:
                continue
            if tool.required_permission is not None and not has_permission(
                role, tool.required_permission
            ):
                continue
            visible.append(tool)
        return sorted(visible, key=lambda item: item.name)

    def summaries_for(self, role: Role) -> list[ToolSummary]:
        """Tool catalogue handed to the LLM for a given role."""
        return [tool.to_summary() for tool in self.visible_to(role)]
