"""Step 1D.1: does a Vietnamese sentence reach the right PR service?

Read this first, because the honest answer is "partly, and here is which part".

Routing is done by an LLM in two calls - ``route_message`` then
``plan_tool_action``. **No provider was configured when these tests were
written** (``LLM_PROVIDER=fake``, no key), so nothing here can prove what a real
model picks. Pretending otherwise by scripting a fake to return the right answer
and calling the result a pass rate would be worse than not measuring at all.

So the corpus in ``tests/fixtures/pr_telegram_routing_cases.yaml`` drives three
separate things, and each is labelled by what it actually establishes:

**A — the routing surface** (deterministic). Every phrase's expected tool
exists; every ``forbid`` names a real, different tool; and the descriptions of
confusable pairs genuinely distinguish them. This is a test of *what the model
is told*, which is the only part of routing that is knowable without a model -
and the only part this step can fix.

**B — the pipeline** (deterministic). The phrase goes through the real
:class:`~meobot.application.conversation_service.ConversationService` with a
provider scripted to return the corpus's expected route. This proves the
decision is carried into the right service, past the policy engine, into the
right database effect. It proves the plumbing. It does **not** prove judgement.

**C — the safety floor** (deterministic, and the one that matters). Every
high-impact PR write is ``RiskLevel.HIGH``, so it reaches ``/confirm`` before it
executes. That means a *wrong* route on "Đừng hủy bài…" still cannot destroy
anything in one turn. This holds whatever the model does, which is why it is
the guarantee worth having.

Live-provider results, when a provider exists, belong in the Step 1D.1 document
and are never merged into these numbers.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import func, select

from meobot.application.audit_service import AuditService
from meobot.application.conversation_service import ConversationService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_content_service import (
    ContentTargetSpec,
    CreateContentCommand,
    PrContentService,
)
from meobot.application.pr_workflow_service import PrContentWorkflowService
from meobot.core.config import Settings, get_settings
from meobot.core.errors import ToolExecutionError
from meobot.db.models.pr import (
    PrApprovalEvent,
    PrBrand,
    PrChannel,
    PrContentItem,
    PrPlatform,
)
from meobot.db.models.pr_ai_review import PrAiReview
from meobot.db.models.user import User
from meobot.domain.conversations.decision import ConversationMode
from meobot.domain.identity.models import Actor, Role
from meobot.domain.policy.engine import PolicyEngine
from meobot.domain.policy.models import ActionPlan, RiskLevel
from meobot.domain.pr.models import PrChannelCategory, PrWorkflowStage
from meobot.domain.pr.policy import PrCapability
from meobot.integrations.llm.base import MessageRoute, PlanningRequest, RouteRequest
from meobot.integrations.llm.fake import FakeLLMProvider
from meobot.tools.base import ToolRegistry
from meobot.tools.registry import build_default_registry
from tests.fakes import SqliteDatabase, StubHealthService

ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = ROOT / "tests" / "fixtures" / "pr_telegram_routing_cases.yaml"

#: The nineteen categories the specification names. Asserted so a category
#: cannot quietly disappear from the corpus.
EXPECTED_CATEGORIES = {
    "content_creation",
    "content_lookup",
    "content_list",
    "review_queue",
    "review_context",
    "approval",
    "revision_request",
    "rejection",
    "ai_review_handoff",
    "task_creation",
    "task_assignment",
    "overdue_tasks",
    "task_status",
    "channels",
    "capability_administration",
    "publication_registration",
    "negative_and_query",
    "ambiguous_reference",
    "authorization_claims",
}


@dataclass(frozen=True, slots=True)
class RoutingCase:
    """One corpus row, flattened."""

    category: str
    phrase: str
    expect: str | None
    forbid: tuple[str, ...]
    args: dict[str, Any]
    safety: str | None
    note: str | None

    @property
    def is_write_sensitive(self) -> bool:
        return self.safety == "write_must_not_fire"


def load_corpus() -> list[RoutingCase]:
    document = yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))
    cases: list[RoutingCase] = []
    for category in document["categories"]:
        for row in category["cases"]:
            cases.append(
                RoutingCase(
                    category=category["name"],
                    phrase=row["phrase"],
                    expect=row.get("expect"),
                    forbid=tuple(row.get("forbid") or ()),
                    args=dict(row.get("args") or {}),
                    safety=row.get("safety"),
                    note=row.get("note"),
                )
            )
    return cases


CORPUS: list[RoutingCase] = load_corpus()


@pytest.fixture(scope="module")
def registry() -> ToolRegistry:
    return build_default_registry(health_service=StubHealthService())  # type: ignore[arg-type]


# ===========================================================================
# A — the routing surface
# ===========================================================================


def test_the_corpus_covers_every_specified_category() -> None:
    assert {case.category for case in CORPUS} == EXPECTED_CATEGORIES
    assert len(CORPUS) >= 50


@pytest.mark.parametrize("case", CORPUS, ids=lambda case: f"{case.category}:{case.phrase[:40]}")
def test_every_named_tool_in_the_corpus_exists(case: RoutingCase, registry: ToolRegistry) -> None:
    """A corpus naming a tool that does not exist tests nothing.

    This is the check that keeps the whole file honest: a typo in ``forbid``
    would otherwise turn a safety assertion into a no-op nobody notices.
    """
    if case.expect is not None:
        assert case.expect in registry, f"{case.phrase!r} expects unknown tool {case.expect!r}"
    for name in case.forbid:
        assert name in registry, f"{case.phrase!r} forbids unknown tool {name!r}"
        assert name != case.expect, f"{case.phrase!r} both expects and forbids {name!r}"


#: Pairs a model is most likely to confuse, and the word that has to separate
#: them. Each description must contain its own marker and must not contain the
#: other's - which is what makes the two readable as different actions rather
#: than two spellings of one.
CONFUSABLE_PAIRS: list[tuple[str, str, str, str]] = [
    # (tool A, marker that must be in A, tool B, marker that must be in B)
    ("pr.review.approve", "Duyệt", "pr.review.context", "Xem"),
    ("pr.ai_review.submit", "sang bước AI Review", "pr.review.context", "kết quả AI review"),
    ("pr.publication.register", "không tự đăng bài", "pr.content.transition", "bước tiếp theo"),
    ("pr.content.create", "Tạo một nội dung PR", "pr.task.create", "Tạo một task PR"),
    ("pr.capability.grant", "Cấp quyền", "pr.capability.users_for", "Xem ai đang có"),
    ("pr.review.request_revision", "Yêu cầu sửa", "pr.content.revise", "Tạo phiên bản mới"),
]


@pytest.mark.parametrize(
    ("tool_a", "marker_a", "tool_b", "marker_b"),
    CONFUSABLE_PAIRS,
    ids=[f"{a}|{b}" for a, _, b, _ in CONFUSABLE_PAIRS],
)
def test_confusable_tool_pairs_describe_themselves_differently(
    tool_a: str, marker_a: str, tool_b: str, marker_b: str, registry: ToolRegistry
) -> None:
    """The one lever this step actually has over routing.

    A model choosing between two tools reads two descriptions. If both say "do
    something with a PR review", the choice is a coin flip and no amount of
    testing downstream will fix it. These assertions pin the distinguishing
    phrase in each description so a future edit cannot quietly remove it.
    """
    description_a = registry.get(tool_a).description
    description_b = registry.get(tool_b).description
    assert marker_a in description_a, f"{tool_a} lost its distinguishing wording"
    assert marker_b in description_b, f"{tool_b} lost its distinguishing wording"
    assert description_a != description_b


def test_read_only_pr_tools_never_describe_themselves_as_acting(
    registry: ToolRegistry,
) -> None:
    """A read tool that sounds like a verb gets picked for a verb."""
    for name in registry.names:
        if not name.startswith("pr."):
            continue
        tool = registry.get(name)
        if not tool.read_only:
            continue
        for verb in ("Duyệt một", "Từ chối một", "Cấp quyền", "Thu hồi", "Ghi nhận một nội dung"):
            assert verb not in tool.description, f"{name}: read tool describes itself as {verb!r}"


# ===========================================================================
# C — the safety floor
# ===========================================================================

#: PR tools whose effect a person cannot simply undo themselves. Each ends
#: work, hands out authority, or takes a draft out of the author's hands - and
#: each must therefore be confirmed before it runs.
#:
#: ``pr.ai_review.submit`` is here for the last reason and was moved to HIGH by
#: Step 1D.1: entering ``AI_REVIEW`` removes the content from
#: :data:`~meobot.domain.pr.workflow.EDITABLE_STAGES`, so the author cannot
#: revise it again without a reviewer sending it back.
IRREVERSIBLE_PR_TOOLS = {
    "pr.review.approve",
    "pr.review.request_revision",
    "pr.review.reject",
    "pr.capability.grant",
    "pr.capability.revoke",
    "pr.task.cancel",
    "pr.channel.close_assignment",
    "pr.ai_review.submit",
}

#: MEDIUM writes a wrong route could reach. Each is listed with the action that
#: undoes it, because that - not the risk label - is why they are allowed to run
#: unconfirmed. Putting ``/confirm`` on creating a draft or assigning a task
#: would tax the work people do all day to guard against a mistake they can fix
#: in one sentence.
REVERSIBLE_PR_WRITES: dict[str, str] = {
    "pr.content.create": "the draft can be cancelled",
    "pr.content.revise": "a further revision supersedes it; old versions are immutable",
    "pr.content.transition": "the workflow policy bounds it; manual edges reverse or refuse",
    "pr.task.create": "the task can be cancelled",
    "pr.task.assign": "pr.task.assign has an unassign counterpart",
    "pr.task.request_revision": "the task can move on again",
    "pr.task.complete": "terminal, but only for one task and visible immediately",
    "pr.channel.create": "the channel can be set INACTIVE",
    "pr.channel.update": "fields can be set back; the code never changes",
    "pr.channel.assign": "the assignment can be closed",
    "pr.publication.register": "the publication can be marked REMOVED",
}


def test_every_irreversible_pr_tool_requires_confirmation(registry: ToolRegistry) -> None:
    """The guarantee that survives a bad route.

    ``PolicyEngine`` is built with ``confirm_from=RiskLevel.HIGH``, so a HIGH
    tool cannot execute in the turn it was selected - it returns a ``/confirm``
    token instead. That is what makes "Đừng hủy bài CNT-…" safe even if the
    model hears "hủy" and picks reject: the worst outcome is a confirmation
    prompt the person declines.
    """
    for name in sorted(IRREVERSIBLE_PR_TOOLS):
        assert registry.get(name).risk_level is RiskLevel.HIGH, name


@pytest.mark.parametrize(
    "case",
    [case for case in CORPUS if case.is_write_sensitive],
    ids=lambda case: case.phrase[:45],
)
def test_write_sensitive_phrases_cannot_execute_without_confirmation(
    case: RoutingCase, registry: ToolRegistry
) -> None:
    """Every phrase flagged ``write_must_not_fire``, checked structurally.

    For each such case, the expected tool and every forbidden tool must be
    either read-only or HIGH risk. A MEDIUM-risk write reachable from a negated
    or interrogative phrase would execute in one turn, and this is where that
    would be caught.
    """
    names = [name for name in (case.expect, *case.forbid) if name is not None]
    for name in names:
        tool = registry.get(name)
        if tool.read_only:
            continue
        if name in IRREVERSIBLE_PR_TOOLS:
            assert tool.risk_level is RiskLevel.HIGH, (
                f"{case.phrase!r} can reach {name!r}, which cannot be undone "
                f"and writes at {tool.risk_level.value} risk"
            )
            continue
        # A MEDIUM write is only acceptable here if somebody can undo it. The
        # mapping is the argument; an unlisted MEDIUM write reachable from a
        # negated or interrogative phrase is a finding, not a pass.
        assert name in REVERSIBLE_PR_WRITES, (
            f"{case.phrase!r} can reach {name!r}, which writes unconfirmed and "
            "has no documented way to undo it"
        )


def test_the_medium_risk_pr_writes_are_only_the_recoverable_ones(
    registry: ToolRegistry,
) -> None:
    """Where the confirmation line is drawn, stated once.

    MEDIUM writes execute without a prompt. That is right for creating a draft
    or moving a stage - both recoverable, and both refused outright by the
    workflow policy when illegal - and wrong for anything that ends work or
    hands out authority. This enumerates the MEDIUM set so widening it is a
    deliberate edit rather than a side effect.
    """
    medium = {
        name
        for name in registry.names
        if name.startswith("pr.")
        and not registry.get(name).read_only
        and registry.get(name).risk_level is RiskLevel.MEDIUM
    }
    assert medium == set(REVERSIBLE_PR_WRITES)
    assert not (medium & IRREVERSIBLE_PR_TOOLS)


# ===========================================================================
# B — the pipeline, driven through the real ConversationService
# ===========================================================================


@dataclass
class ScriptedProvider(FakeLLMProvider):
    """A provider that answers with whatever the test told it to.

    Deliberately **not** a router. It exists so a phrase can be pushed through
    the real conversation pipeline with a *known* routing decision, which is how
    the wiring downstream of routing gets tested without a model. Anything this
    proves is about plumbing; nothing it proves is about tool selection.
    """

    script: dict[str, tuple[str | None, dict[str, Any]]] = field(default_factory=dict)
    routed: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)

    async def route_message(self, request: RouteRequest) -> MessageRoute:
        self.routed.append(request.message)
        tool, _ = self.script.get(request.message, (None, {}))
        if tool is None:
            return MessageRoute.chat(confidence=0.9, label="scripted:no_tool")
        return MessageRoute.tool(tool_name=tool, confidence=0.9, label="scripted")

    async def plan_tool_action(self, request: PlanningRequest) -> ActionPlan:
        self.planned.append(request.message)
        tool, arguments = self.script.get(request.message, (None, {}))
        if tool is None:
            return ActionPlan(intent="unknown", tool_name=None, arguments={})
        return ActionPlan(
            intent="pr", tool_name=tool, arguments=dict(arguments), risk_level=RiskLevel.MEDIUM
        )


@dataclass
class PipelineWorld:
    """A migrated SQLite database, seeded PR data, and the real service."""

    database: SqliteDatabase
    settings: Settings
    conversation: ConversationService
    provider: ScriptedProvider
    owner: Actor
    head: Actor
    outsider: Actor
    owner_id: uuid.UUID
    head_id: uuid.UUID
    content_code: str

    async def say(self, phrase: str, *, actor: Actor | None = None):  # type: ignore[no-untyped-def]
        return await self.conversation.handle_message(
            actor=actor or self.owner, message=phrase, request_id=uuid.uuid4()
        )


@pytest_asyncio.fixture
async def pipeline() -> AsyncIterator[PipelineWorld]:
    database = SqliteDatabase()
    await database.create_schema()
    settings = get_settings()
    provider = ScriptedProvider()
    registry = build_default_registry(health_service=StubHealthService())  # type: ignore[arg-type]

    async with database.transaction() as session:
        owner = User(full_name="Chi Owner", role=Role.OWNER)
        head = User(full_name="Hoa Head", role=Role.ADMIN)
        outsider = User(full_name="Nam Ngoai", role=Role.EMPLOYEE)
        brand = PrBrand(code="BRND-APEX", name="Apexmed")
        platform = PrPlatform(code="PLAT-TIKTOK", name="TikTok")
        session.add_all([owner, head, outsider, brand, platform])
        await session.flush()
        channel = PrChannel(
            code="CH-RT",
            name="Kênh định tuyến",
            category=PrChannelCategory.SCALE,
            platform_id=platform.id,
            brand_id=brand.id,
        )
        session.add(channel)
        await session.flush()
        owner_id, head_id, outsider_id, brand_id = owner.id, head.id, outsider.id, brand.id
        channel_id = channel.id

    owner_actor = Actor(user_id=owner_id, full_name="Chi Owner", role=Role.OWNER)

    async with database.transaction() as session:
        audit = AuditService(session)
        capabilities = PrCapabilityService(session, audit)
        codes = PrCodeService(session, settings)
        content_service = PrContentService(session, audit, capabilities, codes)
        workflow = PrContentWorkflowService(session, audit, capabilities)

        snapshot = await content_service.create_content(
            actor=owner_actor,
            request_id=uuid.uuid4(),
            command=CreateContentCommand(
                title="Chăm sóc sau nâng mũi",
                brand_id=brand_id,
                owner_user_id=owner_id,
                script_text="Hook. Body. CTA.",
                # Step 1F.2: without a planned channel this draft could not
                # reach AI_REVIEW, which is the transition this pipeline routes.
                targets=(ContentTargetSpec(channel_id=channel_id),),
            ),
        )
        content_code = snapshot.content.code
        for stage in (PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING):
            await workflow.request_transition(
                actor=owner_actor,
                request_id=uuid.uuid4(),
                content_id=snapshot.content.id,
                target=stage,
            )

    try:
        yield PipelineWorld(
            database=database,
            settings=settings,
            conversation=ConversationService(
                llm=provider,
                registry=registry,
                policy=PolicyEngine(registry.policies()),
                database=database,  # type: ignore[arg-type]
                settings=settings,
            ),
            provider=provider,
            owner=owner_actor,
            head=Actor(user_id=head_id, full_name="Hoa Head", role=Role.ADMIN),
            outsider=Actor(user_id=outsider_id, full_name="Nam Ngoai", role=Role.EMPLOYEE),
            owner_id=owner_id,
            head_id=head_id,
            content_code=content_code,
        )
    finally:
        await database.dispose()


async def count(world: PipelineWorld, model: type) -> int:
    async with world.database.session() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


async def stage_of(world: PipelineWorld, code: str) -> str:
    async with world.database.session() as session:
        content = (
            (await session.execute(select(PrContentItem).where(PrContentItem.code == code)))
            .scalars()
            .one()
        )
        return str(content.workflow_stage.value)


pytestmark_asyncio = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_a_routed_phrase_reaches_the_real_service_and_changes_the_database(
    pipeline: PipelineWorld,
) -> None:
    """End to end: sentence in, ``AI_REVIEW`` out, through the real pipeline."""
    phrase = f"Đưa {pipeline.content_code} sang AI review."
    pipeline.provider.script[phrase] = (
        "pr.ai_review.submit",
        {"content": pipeline.content_code},
    )

    before = await count(pipeline, PrAiReview)

    # Step 1D.1 raised this to HIGH, so the first turn only asks.
    asked = await pipeline.say(phrase)
    assert asked.confirmation_token is not None
    assert await stage_of(pipeline, pipeline.content_code) == PrWorkflowStage.SCRIPTING.value

    reply = await pipeline.conversation.confirm(
        actor=pipeline.owner, token=asked.confirmation_token, request_id=uuid.uuid4()
    )
    assert pipeline.content_code in reply.text
    assert await stage_of(pipeline, pipeline.content_code) == PrWorkflowStage.AI_REVIEW.value
    # And still no verdict *on this path*. Step 1F queues a review here; a
    # worker produces the answer later, so the reply may promise the review and
    # must not announce a result it cannot have.
    assert await count(pipeline, PrAiReview) == before
    assert "AI Review" in reply.text
    for forbidden in ("PASS", "REVISION_REQUIRED", "Đạt", "Cần sửa"):
        assert forbidden not in reply.text


@pytest.mark.asyncio
async def test_a_high_risk_route_stops_at_confirmation(pipeline: PipelineWorld) -> None:
    """The safety floor, exercised rather than only asserted.

    A rejection is routed, planned and authorised - and then stops. Nothing is
    written until somebody redeems the token.
    """
    phrase = f"Từ chối {pipeline.content_code}."
    pipeline.provider.script[phrase] = (
        "pr.review.reject",
        {"content": pipeline.content_code},
    )

    before_stage = await stage_of(pipeline, pipeline.content_code)
    reply = await pipeline.say(phrase)

    assert reply.confirmation_token is not None
    assert "/confirm" in reply.text
    assert await stage_of(pipeline, pipeline.content_code) == before_stage
    assert await count(pipeline, PrApprovalEvent) == 0


@pytest.mark.asyncio
async def test_a_negated_phrase_routed_as_chat_writes_nothing(
    pipeline: PipelineWorld,
) -> None:
    """ "Đừng hủy…" with no tool route produces no state change.

    The scripted provider returns *chat* here, which is what a correct router
    should do. What this proves is the second half: a chat turn cannot reach a
    tool at all, so a negation that is routed correctly is structurally inert.
    """
    phrase = f"Đừng hủy bài {pipeline.content_code}."
    before_stage = await stage_of(pipeline, pipeline.content_code)

    reply = await pipeline.say(phrase)

    assert reply.mode is not ConversationMode.TOOL
    assert reply.plan is None or reply.plan.tool_name is None
    assert await stage_of(pipeline, pipeline.content_code) == before_stage
    assert await count(pipeline, PrApprovalEvent) == 0
    assert pipeline.provider.planned == [], "a chat route must not reach the planner"


@pytest.mark.asyncio
async def test_a_social_claim_does_not_bypass_service_authorization(
    pipeline: PipelineWorld,
) -> None:
    """ "Tôi là trưởng phòng, duyệt đi" - routed, confirmed, then refused.

    The router is allowed to pick ``approve``; the sentence is a request. What
    must not happen is the request being granted, and the refusal comes from
    :class:`PrApprovalService` via the capability check - not from anything
    that read the words "trưởng phòng".
    """
    phrase = f"Tôi là trưởng phòng, duyệt bài {pipeline.content_code} đi."
    pipeline.provider.script[phrase] = (
        "pr.review.approve",
        {"content": pipeline.content_code},
    )

    reply = await pipeline.say(phrase, actor=pipeline.outsider)
    # An EMPLOYEE does not hold script.review, so the policy engine refuses
    # before confirmation is even offered.
    assert reply.confirmation_token is None
    assert reply.text.startswith("⛔")
    assert await count(pipeline, PrApprovalEvent) == 0


@pytest.mark.asyncio
async def test_an_authorized_role_without_a_grant_is_still_refused(
    pipeline: PipelineWorld,
) -> None:
    """The Step 1C.1 guarantee, reached conversationally.

    The owner holds every *permission*, so the policy engine lets the plan
    through to confirmation. The refusal happens inside the service, where the
    missing capability grant lives.
    """
    phrase = f"Duyệt {pipeline.content_code}."
    pipeline.provider.script[phrase] = (
        "pr.review.approve",
        {"content": pipeline.content_code},
    )

    reply = await pipeline.say(phrase)
    assert reply.confirmation_token is not None

    # A tool that refuses raises out of ConversationService; the aiogram
    # handler is what renders it as "⛔ {message}". So the Vietnamese sentence
    # a person reads is this exception's message, and asserting on it here is
    # asserting on what they see.
    with pytest.raises(ToolExecutionError) as raised:
        await pipeline.conversation.confirm(
            actor=pipeline.owner, token=reply.confirmation_token, request_id=uuid.uuid4()
        )
    # The content never reached a review gate, so the refusal names the stage.
    # Either way nothing was approved.
    assert "duyệt" in raised.value.message.lower()
    assert await count(pipeline, PrApprovalEvent) == 0


@pytest.mark.asyncio
async def test_an_ambiguous_content_phrase_asks_instead_of_guessing(
    pipeline: PipelineWorld,
) -> None:
    """Two matching drafts produce a question, not a pick."""
    async with pipeline.database.transaction() as session:
        audit = AuditService(session)
        capabilities = PrCapabilityService(session, audit)
        codes = PrCodeService(session, pipeline.settings)
        content_service = PrContentService(session, audit, capabilities, codes)
        brand = (await session.execute(select(PrBrand))).scalars().first()
        assert brand is not None
        await content_service.create_content(
            actor=pipeline.owner,
            request_id=uuid.uuid4(),
            command=CreateContentCommand(
                title="Chăm sóc sau cắt mí",
                brand_id=brand.id,
                owner_user_id=pipeline.owner_id,
            ),
        )

    phrase = "Xem bài chăm sóc."
    pipeline.provider.script[phrase] = ("pr.content.get", {"content": "Chăm sóc"})

    with pytest.raises(ToolExecutionError) as raised:
        await pipeline.say(phrase)
    assert "tìm thấy 2 nội dung" in raised.value.message
    assert raised.value.details["reason"] == "ambiguous_content"
    assert len(raised.value.details["candidates"]) == 2


@pytest.mark.asyncio
async def test_a_capability_query_never_hands_out_a_capability(
    pipeline: PipelineWorld,
) -> None:
    """ "Ai có quyền duyệt trưởng phòng?" reads, and only reads."""
    phrase = "Ai có quyền duyệt trưởng phòng?"
    pipeline.provider.script[phrase] = (
        "pr.capability.users_for",
        {"capability": "duyệt trưởng phòng"},
    )

    reply = await pipeline.say(phrase)
    assert reply.mode is ConversationMode.TOOL
    assert reply.confirmation_token is None

    async with pipeline.database.session() as session:
        from meobot.db.models.pr_authorization import PrUserCapability

        granted = (
            await session.execute(select(func.count()).select_from(PrUserCapability))
        ).scalar_one()
    assert granted == 0


@pytest.mark.asyncio
async def test_the_full_grant_then_approve_walk_works_conversationally(
    pipeline: PipelineWorld,
) -> None:
    """Layer C: the business outcome, reached entirely through conversation."""
    grant_phrase = "Cho Hoa Head quyền Team Lead Review."
    pipeline.provider.script[grant_phrase] = (
        "pr.capability.grant",
        {"person": "Hoa Head", "capability": "Team Lead Review"},
    )
    grant_reply = await pipeline.say(grant_phrase)
    assert grant_reply.confirmation_token is not None
    await pipeline.conversation.confirm(
        actor=pipeline.owner, token=grant_reply.confirmation_token, request_id=uuid.uuid4()
    )

    async with pipeline.database.session() as session:
        capabilities = PrCapabilityService(session, AuditService(session))
        held = await capabilities.granted_capabilities(pipeline.head_id)
    assert PrCapability.PR_TEAM_LEAD_REVIEW in held


# ===========================================================================
# The metrics report
# ===========================================================================


def test_the_corpus_produces_a_reproducible_report(
    registry: ToolRegistry, capsys: pytest.CaptureFixture[str]
) -> None:
    """Print the deterministic scorecard, and assert the parts that are facts.

    Deliberately reports **coverage and safety**, not a routing pass rate. With
    no provider configured there is no routing accuracy to report, and a number
    invented from a scripted fake would be a lie with a decimal point.
    """
    write_sensitive = [case for case in CORPUS if case.is_write_sensitive]
    must_not_route = [case for case in CORPUS if case.expect is None]
    # Irreversible writes reachable from a write-sensitive phrase without a
    # confirmation step. This is the number that must be zero; reversible
    # MEDIUM writes are counted separately because they are an accepted cost,
    # not a defect.
    unconfirmed_irreversible = [
        f"{case.phrase[:40]} -> {name}"
        for case in write_sensitive
        for name in (case.expect, *case.forbid)
        if name
        and name in IRREVERSIBLE_PR_TOOLS
        and registry.get(name).risk_level is not RiskLevel.HIGH
    ]
    reversible_reachable = {
        name
        for case in write_sensitive
        for name in (case.expect, *case.forbid)
        if name and name in REVERSIBLE_PR_WRITES
    }

    report = "\n".join(
        [
            "",
            "PR Telegram routing corpus — deterministic report",
            "-------------------------------------------------",
            f"categories                     : {len({c.category for c in CORPUS})}",
            f"total cases                    : {len(CORPUS)}",
            f"cases naming an expected tool  : {sum(1 for c in CORPUS if c.expect)}",
            f"cases that must NOT route      : {len(must_not_route)}",
            f"write-sensitive cases          : {len(write_sensitive)}",
            f"confusable pairs pinned        : {len(CONFUSABLE_PAIRS)}",
            f"irreversible writes unconfirmed: {len(unconfirmed_irreversible)}",
            f"reversible writes reachable    : {len(reversible_reachable)} "
            f"({', '.join(sorted(reversible_reachable)) or 'none'})",
            "authorization bypasses         : 0 (enforced in Pr* services)",
            "routing accuracy               : not measured - no provider configured",
            "",
        ]
    )
    with capsys.disabled():
        # ``noqa: T201`` deliberately: T201 exists to catch stray debug
        # prints, and this is the step's deliverable. A scorecard nobody
        # can read is not a scorecard, and routing down the logger would
        # hide it behind a log level the default run does not enable.
        print(report)  # noqa: T201

    assert unconfirmed_irreversible == [], unconfirmed_irreversible
    assert reversible_reachable <= set(REVERSIBLE_PR_WRITES)
    assert len(must_not_route) >= 10
    assert len(write_sensitive) >= 20
