"""Assembly of the default tool registry.

Note what is still *absent*: nothing publishes a post, promotes a script into a
video, or deletes data - and as of 0.3.0, nothing deletes, trashes or moves a
Google Drive file either. Those tools do not exist, the Drive client has no
method that would implement them, and the policy engine refuses any plan naming
a tool that is not registered here.
"""

from __future__ import annotations

from meobot.application.health_service import HealthService
from meobot.core.logging import get_logger
from meobot.domain.units.models import UnitCode
from meobot.integrations.google.drive import DriveClient, NotConfiguredDriveClient
from meobot.integrations.google.sheets import NotConfiguredSheetsClient, SheetsClient
from meobot.integrations.llm.base import LLMProvider
from meobot.integrations.llm.fake import FakeLLMProvider
from meobot.tools.base import ToolRegistry
from meobot.tools.drive_tools import build_drive_tools
from meobot.tools.invite_tools import build_invite_tools
from meobot.tools.pr_admin_tools import build_pr_admin_tools
from meobot.tools.pr_content_tools import build_pr_content_tools
from meobot.tools.pr_review_tools import build_pr_review_tools
from meobot.tools.pr_task_tools import build_pr_task_tools
from meobot.tools.script_tools import build_script_tools
from meobot.tools.script_type_tools import build_script_type_tools
from meobot.tools.sheet_tools import build_sheet_tools
from meobot.tools.system_tools import build_system_tools
from meobot.tools.unit_gate import gate_tools_by_unit

logger = get_logger(__name__)


def build_default_registry(
    *,
    health_service: HealthService,
    sheets: SheetsClient | None = None,
    llm: LLMProvider | None = None,
    drive: DriveClient | None = None,
) -> ToolRegistry:
    """Build the registry used by the bot, the API and the tasks.

    Args:
        health_service: Concrete health checker injected into ``system.health``.
        sheets: Google Sheets client. Defaults to the not-configured stub, so a
            deployment without credentials still builds a full registry and
            fails only when a Google-backed tool is actually invoked.
        llm: Provider used by ``script.request_review``. Defaults to the fake
            provider, which keeps the registry usable offline.
        drive: Google Drive client. Same rule as ``sheets``: a missing
            credential produces a registry whose Drive tools fail loudly on
            use, not a process that refuses to start.
    """
    registry = ToolRegistry()
    resolved_sheets = sheets or NotConfiguredSheetsClient()
    for tool in (
        *build_system_tools(health_service),
        *build_script_type_tools(),
        *build_sheet_tools(sheets=resolved_sheets),
        *build_script_tools(llm=llm or FakeLLMProvider()),
        *build_invite_tools(),
        *build_drive_tools(
            drive=drive or NotConfiguredDriveClient(),
            sheets=resolved_sheets,
        ),
        # PR and Communications (Step 1D). Every one of these is a translator
        # onto the Pr* application services - the same services a future web
        # admin UI will call. None of them holds a workflow rule. Since units,
        # every one of them is also behind the PR unit wall, exactly as the
        # ``/api/pr/*`` routes are.
        *gate_tools_by_unit(
            [
                *build_pr_content_tools(),
                *build_pr_review_tools(),
                *build_pr_task_tools(),
                *build_pr_admin_tools(),
            ],
            UnitCode.PR,
        ),
    ):
        registry.register(tool)
    logger.info("tool_registry_built", extra={"tool_count": len(registry)})
    return registry
