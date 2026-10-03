"""The transport boundary, asserted rather than trusted.

Step 1D's whole premise is that Telegram is a client and the ``Pr*`` services
are the authority. Behavioural tests show that is true *today*; these show it
stays true, because the failure mode is a future handler quietly acquiring
business logic of its own and no behavioural test of today's tools would
notice.

Every check here is a source sweep over ``src/meobot/tools/pr_*.py`` and the
bot package. They are blunt on purpose: a grep that occasionally needs a
deliberate exception is a guard people maintain, and a clever one that nobody
understands is a guard people delete.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

from meobot.domain.policy.models import RiskLevel
from meobot.tools.base import ToolRegistry
from meobot.tools.registry import build_default_registry
from tests.fakes import StubHealthService

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "meobot"
TOOLS = SRC / "tools"
BOT = SRC / "bot"

#: The Step 1D modules. Everything asserted below applies to all of them.
PR_TOOL_FILES: list[Path] = sorted(TOOLS.glob("pr_*.py"))

#: PR ORM models a Telegram module must never construct. Reading one is fine -
#: a presenter takes rows the services loaded - but ``PrApprovalEvent(...)`` in
#: the transport layer means an approval was written without the service that
#: enforces reviewer separation.
FORBIDDEN_CONSTRUCTIONS = (
    "PrApprovalEvent",
    "PrAiReview",
    "PrContentItem",
    "PrContentVersion",
    "PrTask",
    "PrTaskAssignment",
    "PrChannelAssignment",
    "PrPublication",
    "PrUserCapability",
    "PrCodeCounter",
)

#: How a code would be invented locally instead of allocated.
CODE_FABRICATION = re.compile(
    r"[\"']CNT-|[\"']TSK-\d|[\"']CH-\d|[\"']PUB-\d|[\"']ISS-\d|"
    r"f[\"']CNT-|f[\"']TSK-|f[\"']CH-|f[\"']PUB-|f[\"']ISS-"
)


@pytest.fixture(scope="module")
def registry() -> ToolRegistry:
    return build_default_registry(health_service=StubHealthService())  # type: ignore[arg-type]


def executable_source(path: Path) -> str:
    """Source with comments and string literals removed.

    A text sweep would otherwise flag the docstrings that *explain* the rule -
    which is the one place the forbidden phrases should appear.
    """
    text = path.read_text(encoding="utf-8")
    kept: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type in {tokenize.COMMENT, tokenize.STRING}:
            continue
        kept.append(token.string)
    return " ".join(kept)


def test_step_1d_actually_added_pr_tool_modules() -> None:
    """The sweeps below prove nothing if there is nothing to sweep."""
    assert len(PR_TOOL_FILES) >= 5
    names = {path.name for path in PR_TOOL_FILES}
    assert {"pr_content_tools.py", "pr_review_tools.py", "pr_admin_tools.py"} <= names


# --- The four rules the specification names --------------------------------


def test_no_pr_tool_writes_workflow_stage() -> None:
    """``PrContentWorkflowService`` is the only writer, still."""
    assignment = re.compile(r"\.workflow_stage\s*=(?!=)")
    offenders = [path.name for path in PR_TOOL_FILES if assignment.search(executable_source(path))]
    assert offenders == [], offenders

    # And the sanity check that the pattern can fire at all.
    owner = SRC / "application" / "pr_workflow_service.py"
    assert assignment.search(owner.read_text(encoding="utf-8")) is not None


def test_no_pr_tool_constructs_a_pr_orm_row() -> None:
    """Reading a row is fine; building one in the transport layer is not."""
    offenders: list[str] = []
    for path in PR_TOOL_FILES:
        source = executable_source(path)
        for model in FORBIDDEN_CONSTRUCTIONS:
            if re.search(rf"\b{model}\s*\(", source):
                offenders.append(f"{path.name}: {model}(...)")
    assert offenders == [], offenders


def test_no_pr_tool_fabricates_a_code() -> None:
    """``CNT-…`` is allocated by ``PrCodeService`` or it does not exist."""
    offenders = [
        path.name for path in PR_TOOL_FILES if CODE_FABRICATION.search(executable_source(path))
    ]
    assert offenders == [], offenders


def test_no_pr_tool_calls_an_llm_or_a_social_platform() -> None:
    """Step 1D routes intent; it does not review, and it does not publish."""
    forbidden = re.compile(
        r"\b(?:openai|anthropic|gemini|litellm|langchain|facebook|tiktok_api|graph_api"
        r"|httpx|requests|aiohttp|fastapi|starlette|flask)\b"
    )
    for path in PR_TOOL_FILES:
        imports = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith(("import ", "from "))
        )
        assert not forbidden.search(imports), path.name


# --- The transport boundary -------------------------------------------------


def test_no_pr_tool_opens_or_commits_a_transaction() -> None:
    """The caller owns the boundary.

    ``ConversationService._run_tool`` opens one transaction per tool call and
    commits it. A tool that opened its own would produce two commits per
    command, and a tool that committed would break the guarantee that a failure
    rolls the whole thing back.
    """
    for path in PR_TOOL_FILES:
        source = executable_source(path)
        assert ".commit(" not in source, path.name
        assert "database.transaction(" not in source, path.name
        assert ".rollback(" not in source, path.name


def test_every_pr_tool_takes_its_session_from_the_context() -> None:
    """A handler that built its own session would escape the transaction."""
    for path in PR_TOOL_FILES:
        source = path.read_text(encoding="utf-8")
        assert "async_sessionmaker" not in source, path.name
        assert "get_database(" not in source, path.name


def test_no_pr_tool_authorizes_by_a_telegram_identity() -> None:
    """Requirement 44. Authorization is ``users.id`` plus role plus grant."""
    offenders: list[str] = []
    for path in PR_TOOL_FILES:
        source = executable_source(path)
        for needle in ("telegram_username", "telegram_user_id", "telegram_chat_id"):
            if needle in source:
                offenders.append(f"{path.name}: {needle}")
    assert offenders == [], offenders


def test_no_pr_tool_reimplements_a_transition_matrix() -> None:
    """The matrices live in ``meobot.domain.pr.workflow`` and are not copied.

    A tool may *name* a stage - the schema has to describe what it accepts -
    but it must not decide which follows which. The give-away is importing the
    matrices themselves; ``STAGE_APPROVAL_GATES`` is allowed because deriving
    the gate from the stage is a lookup in the shared table, not a second copy
    of it.
    """
    banned = ("CONTENT_TRANSITIONS", "TASK_TRANSITIONS", "assert_content_transition")
    offenders: list[str] = []
    for path in PR_TOOL_FILES:
        source = executable_source(path)
        for symbol in banned:
            if symbol in source:
                offenders.append(f"{path.name}: {symbol}")
    assert offenders == [], offenders


def test_the_web_surface_reuses_the_services_rather_than_reimplementing_them() -> None:
    """Requirement 45, as **Step 1E redefined it.**

    Step 1D's version asserted that no HTTP module mentioned ``pr_`` at all, on
    the grounds that "the web client is later". Later has arrived: Step 1E added
    ``api/routers/pr.py``, ``api/schemas/pr.py`` and a session table, so the
    original assertion is now deliberately false and keeping it would only stop
    the suite from running.

    What requirement 45 was protecting is not the *absence* of a web client - it
    is that a second client must not become a second implementation. So the
    assertion moved to that:

    * the PR router imports the shared bundle builder, not the services one by
      one, so its dependency graph cannot drift from Telegram's;
    * no PR module under ``api`` imports a Telegram module. A PR route reaching
      into ``meobot.tools`` would drag ``ToolContext``, risk levels and the
      confirmation flow into HTTP, where none of them mean anything.
      ``api/main.py`` is exempt and was before Step 1E: it builds the tool
      registry for the pre-existing conversations router, which is Telegram's
      pipeline exposed for development and has nothing to do with PR.

    The deeper checks - no ``workflow_stage`` write, no code fabrication, no
    duplicated matrix - live in ``tests/unit/test_pr_web_admin.py`` beside the
    rest of the web tests.
    """
    assert not (SRC / "web").exists(), "The frontend belongs outside the Python package."
    router = (SRC / "api" / "routers" / "pr.py").read_text(encoding="utf-8")
    assert "PrServicesDep" in router
    pr_modules = [path for path in sorted((SRC / "api").rglob("*.py")) if path.name == "pr.py"]
    assert len(pr_modules) == 2, "Expected the PR router and the PR schema module."
    for path in [*pr_modules, SRC / "api" / "deps.py"]:
        source = path.read_text(encoding="utf-8")
        assert "meobot.tools" not in source, path.name
        assert "ToolContext" not in source, path.name


# --- The registry ------------------------------------------------------------


def test_every_pr_tool_is_registered_once_and_declares_a_permission(
    registry: ToolRegistry,
) -> None:
    pr_tools = [registry.get(name) for name in registry.names if name.startswith("pr.")]
    assert len(pr_tools) >= 30
    assert len({tool.name for tool in pr_tools}) == len(pr_tools)
    for tool in pr_tools:
        assert tool.required_permission is not None, tool.name
        assert tool.description.strip(), tool.name


def test_write_tools_are_not_marked_read_only(registry: ToolRegistry) -> None:
    """``read_only`` feeds the policy engine; a mislabelled write skips checks."""
    writes = {
        "pr.content.create",
        "pr.content.revise",
        "pr.content.transition",
        "pr.ai_review.submit",
        "pr.review.approve",
        "pr.review.request_revision",
        "pr.review.reject",
        "pr.task.create",
        "pr.task.assign",
        "pr.channel.create",
        "pr.channel.assign",
        "pr.capability.grant",
        "pr.capability.revoke",
        "pr.publication.register",
    }
    for name in writes:
        assert registry.get(name).read_only is False, name


def test_high_impact_pr_tools_inherit_the_existing_confirmation_flow(
    registry: ToolRegistry,
) -> None:
    """Confirmation is ``RiskLevel.HIGH`` plus the existing ``PolicyEngine``.

    No PR-specific confirmation state machine exists, and this is what says so:
    the actions the specification calls high-impact are exactly the ones marked
    ``HIGH``, which is the flag ``/confirm`` already keys on.
    """
    for name in (
        "pr.review.reject",
        "pr.capability.revoke",
        "pr.task.cancel",
        "pr.channel.close_assignment",
    ):
        assert registry.get(name).risk_level is RiskLevel.HIGH, name

    for name in ("pr.content.get", "pr.review.pending", "pr.task.overdue", "pr.channel.list"):
        tool = registry.get(name)
        assert tool.risk_level is RiskLevel.LOW, name
        assert tool.read_only is True, name


def test_no_pr_tool_description_promises_more_than_the_services_enforce(
    registry: ToolRegistry,
) -> None:
    """Tool text is read by a model, and a model reads promises as permission.

    Nothing may describe itself as publishing to a platform or as running an AI
    review, because neither happens - and a description that said so would
    make the router pick it for requests it cannot satisfy.
    """
    # Positive promises only. "Không chạy AI ở bước này" is the *disclaimer*
    # the handoff tool is required to carry, and a naive substring check would
    # flag the honest sentence along with the dishonest one.
    forbidden = (
        "tự động đăng",
        "đăng lên Facebook",
        "AI sẽ phân tích",
        "đang phân tích",
        "AI đã duyệt",
    )
    for name in registry.names:
        if not name.startswith("pr."):
            continue
        description = registry.get(name).description
        for phrase in forbidden:
            assert phrase not in description, f"{name}: {phrase}"


def test_the_ai_handoff_tool_says_what_it_actually_does(registry: ToolRegistry) -> None:
    """Requirement 46, as Step 1F changed the truth it asserts.

    The tool still only *moves a stage* - the handler calls no model and writes
    no ``pr_ai_reviews`` row, which the handler test proves. What changed is
    what the move causes: the workflow queues a real review, so the description
    that said "Không chạy AI ở bước này" would now be the misleading text this
    requirement exists to prevent.

    So the assertion is inverted rather than dropped: the description must
    promise the review, and must not still claim none happens.
    """
    tool = registry.get("pr.ai_review.submit")
    assert "Không chạy AI" not in tool.description
    assert "AI review" in tool.description
    assert tool.read_only is False
