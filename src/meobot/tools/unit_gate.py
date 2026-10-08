"""The unit wall, for Telegram tools.

The web routes are gated at include time (``main.py``); the Telegram tools are
gated here, by wrapping each PR tool's handler so that it first asks
:class:`~meobot.application.units.directory.UnitDirectoryService` whether the
caller is tagged into the unit. The handlers themselves are untouched, and a
caller outside the unit reads the same sentence the web shows: nothing about
what is behind the wall.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any

from meobot.application.units.directory import NOT_VISIBLE, UnitDirectoryService
from meobot.core.errors import ToolExecutionError
from meobot.domain.units.errors import UnitNotFoundError
from meobot.domain.units.models import UnitCode
from meobot.tools.base import ToolContext, ToolDefinition, ToolHandler, ToolResult


def _gated(handler: ToolHandler, code: UnitCode) -> ToolHandler:
    async def run(context: ToolContext, arguments: Any) -> ToolResult:
        directory = UnitDirectoryService(context.require_session())
        try:
            await directory.require(context.actor, code)
        except UnitNotFoundError as error:
            raise ToolExecutionError(
                NOT_VISIBLE, details={"reason": "unit_not_visible", "unit": code.value}
            ) from error
        return await handler(context, arguments)

    run.__name__ = getattr(handler, "__name__", "handler")
    run.__qualname__ = getattr(handler, "__qualname__", run.__name__)
    # So a test can tell a gated handler from a bare one.
    run.__wrapped_by_unit__ = code.value  # type: ignore[attr-defined]
    return run


def gate_tools_by_unit(tools: Sequence[ToolDefinition], code: UnitCode) -> list[ToolDefinition]:
    """Every tool in ``tools``, refusing callers outside ``code``."""
    return [dataclasses.replace(tool, handler=_gated(tool.handler, code)) for tool in tools]


__all__ = ["gate_tools_by_unit"]
