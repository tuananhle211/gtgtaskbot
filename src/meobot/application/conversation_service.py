"""The conversational pipeline.

Every free-text Telegram message takes this path:

    message
      -> deterministic route (patterns)      # greeting, operation, ambiguity
      -> LLM route  (small schema)           # only for the messages in between
      -> chat        : plain-text generation -> retry -> deterministic reply
         clarify     : one question          -> deterministic question
         tool        : plan -> policy -> confirmation -> tool -> audit

**What changed in 0.4.0 and why.** Before it, one provider call answered every
question at once: route, reply, clarification and action plan in a single
structured-output request. That made three unrelated things fail together. When
the configured model refused a *request parameter*, the only call that could
produce conversation returned an error, and the pipeline had exactly one thing
to say - so "Hello" was unanswerable while every slash command still worked.

The fork is now staged, and each stage has its own fallback:

* a deterministic pattern settles the obvious messages with no provider at all,
  so greetings and capability questions work even with the model unreachable;
* a routing failure defaults to **chat**, never to tool - a guessed action is
  the one outcome a provider outage must not be able to produce;
* a chat-generation failure retries once with a smaller prompt, then answers
  from the assistant profile, the actor profile and the live capability report;
* only the last resort admits an outage, and it says which reference id to
  quote and that operational commands still work.

The guarantees that make the chat branch safe are unchanged:

* a ``chat`` decision carries no ``ActionPlan`` and the chat generation call is
  never given a tool catalogue, so it cannot execute anything;
* the chat branch opens no write transaction against business tables, calls no
  Google API, and touches nothing but the conversation-memory tables;
* there is no code path from a user message to a tool that skips
  :class:`~meobot.domain.policy.engine.PolicyEngine`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.actor_profile_service import ActorProfileService
from meobot.application.assistant_context_service import AssistantContextService
from meobot.application.audit_service import AuditService
from meobot.application.capability_service import CapabilityService
from meobot.application.chat_fallback import DeterministicReplies
from meobot.application.chat_memory_service import ChatMemoryService
from meobot.application.confirmation_service import ConfirmationService
from meobot.application.meobot_context_service import (
    ContextRef,
    MeoBotAssistantContextService,
)
from meobot.application.pr_services import build_pr_services
from meobot.application.prompt_context_service import (
    PromptContext,
    PromptContextService,
    TurnContext,
)
from meobot.core.config import Settings
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.db.session import Database
from meobot.domain.access.models import GuestPrincipal
from meobot.domain.assistant.profile import AssistantProfile
from meobot.domain.assistant.work_context import MeoBotAssistantContext
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.conversations.decision import ConversationDecision, ConversationMode
from meobot.domain.conversations.patterns import deterministic_route, fallback_route
from meobot.domain.conversations.references import RecentReference
from meobot.domain.identity.models import Actor
from meobot.domain.identity.profile import ActorProfile
from meobot.domain.policy.engine import PolicyEngine
from meobot.domain.policy.models import ActionPlan, DecisionCode, PolicyContext, PolicyDecision
from meobot.integrations.llm.base import (
    ChatRequest,
    ChatTurn,
    ClarificationRequest,
    LLMProvider,
    MessageRoute,
    PlanningRequest,
    RouteMode,
    RouteRequest,
)
from meobot.tools.base import ToolContext, ToolRegistry, ToolResult

logger = get_logger(__name__)

UNKNOWN_REPLY = (
    "Mình chưa rõ bạn muốn làm gì với yêu cầu này. Bạn mô tả cụ thể hơn một chút "
    "được không? Hoặc gõ /help để xem những việc mình làm được."
)

CHAT_DISABLED_REPLY = (
    "Chế độ trò chuyện đang tắt trên hệ thống này. Bạn vẫn dùng được các lệnh - "
    "gõ /help để xem danh sách."
)

#: What a Guest is told when they ask for something only a member can have.
#: Deterministic and free: producing it costs no provider call, so it must not
#: cost the Guest one of their ten questions either.
GUEST_TOOL_DENIED = (
    "Guest chỉ có thể trò chuyện với TasksBot trong group này.\n"
    "Bạn không có quyền sử dụng công cụ hoặc truy cập dữ liệu nội bộ."
)

GUEST_FALLBACK_REPLY = (
    "Mình chưa nghĩ ra câu trả lời tốt cho ý này. Bạn thử diễn đạt lại giúp mình nhé."
)


def _reference_context(references: tuple[RecentReference, ...]) -> ContextRef | None:
    """The PR content item this thread was last working on, if any.

    Step 1F.2.3h, and the reason no frontend work was needed to answer *"which
    page is this about"*: the conversation already records what MeoBot last
    operated on, because every PR tool returns ``entity_type="pr_content"`` with
    the item's id and
    :meth:`ConversationService._remember` turns that into a
    :class:`~meobot.domain.conversations.references.RecentReference`.

    So a member who looks a piece up and then asks *"ai thêm bản cắt này?"* gets
    an answer grounded in that piece, with no client having said anything about
    it. Newest wins - the pointer list is ordered oldest-first and the last
    thing discussed is what "cái này" means, which is the same rule
    :func:`~meobot.domain.conversations.references.render_references` renders by.

    **This is a pointer, not a claim.** The id goes to
    :class:`~meobot.application.meobot_context_service.MeoBotAssistantContextService`,
    which loads the row under this actor's own read authorization - a pointer
    left over from a thread where somebody else had access resolves to nothing.
    """
    for reference in reversed(references):
        if reference.entity_type == ContextRef.PR_CONTENT:
            return ContextRef(kind=ContextRef.PR_CONTENT, id=reference.entity_id)
    return None


def guest_prompt_context(guest: GuestPrincipal, assistant: AssistantProfile) -> str:
    """The entire prompt context a Guest's turn is allowed to carry.

    Compare with :class:`~meobot.application.prompt_context_service.PromptContext`,
    which a registered user gets: that one names the organisation, the
    department, the live integrations, the tool catalogue and the caller's
    permission set. None of that is a Guest's to see, so this builds a much
    smaller block by hand rather than filtering the large one - a filter can be
    forgotten, a smaller builder cannot leak what it never reads.
    """
    return "\n".join(
        [
            "[ASSISTANT IDENTITY]",
            f"Tên: {assistant.assistant_name}",
            "",
            "[CURRENT USER]",
            f"Tên: {guest.display_name}",
            "Quan hệ: khách mời tạm thời trong một group Telegram. Không phải thành viên hệ thống.",
            "",
            "[GUEST RULES]",
            "- Chỉ trò chuyện. Không chạy công cụ, không truy cập Sheet, Drive, "
            "kịch bản, báo cáo hay dữ liệu nội bộ.",
            "- Không mô tả cấu trúc nội bộ, danh sách công cụ, tên bảng dữ liệu "
            "hay quyền hạn của người khác.",
            "- Nếu khách hỏi về dữ liệu nội bộ, nói rõ là bạn không thể hỗ trợ "
            "phần đó và đề nghị họ liên hệ Trưởng phòng.",
            "- Trả lời ngắn gọn, lịch sự, bằng tiếng Việt.",
        ]
    )


class ConversationTarget(BaseModel):
    """Where a conversation is happening, so memory can be scoped to it."""

    model_config = ConfigDict(frozen=True)

    bot_id: int
    chat_id: int
    telegram_user_id: int


class ConversationReply(BaseModel):
    """What the transport layer should send back to the user."""

    model_config = ConfigDict(frozen=True)

    text: str
    mode: ConversationMode | None = None
    plan: ActionPlan | None = None
    decision: PolicyDecision | None = None
    tool_result: ToolResult | None = None
    confirmation_token: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def executed_a_tool(self) -> bool:
        """True only when a tool actually ran and returned a result."""
        return self.tool_result is not None


@dataclass(slots=True)
class _TurnState:
    """Everything loaded for one turn: memory plus identity context."""

    assistant: AssistantProfile
    profile: ActorProfile
    history: tuple[ChatTurn, ...] = ()
    rolling_summary: str | None = None
    references: tuple[RecentReference, ...] = ()
    is_new_thread: bool = True
    workflow: dict[str, str] = field(default_factory=dict)


class ConversationService:
    """Turns a natural-language message into a safe, audited action or an answer.

    Args:
        llm: Provider that routes, writes and proposes. Never executes anything.
        registry: Catalogue of executable tools.
        policy: Engine that authorises plans.
        database: Source of the unit of work used for tools and audit.
        settings: Application settings (TTLs, environment, identity).
    """

    def __init__(
        self,
        *,
        llm: LLMProvider,
        registry: ToolRegistry,
        policy: PolicyEngine,
        database: Database,
        settings: Settings,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._policy = policy
        self._database = database
        self._settings = settings
        self._capabilities = CapabilityService(registry, settings)
        self._prompts = PromptContextService(self._capabilities, settings)

    async def handle_message(
        self,
        *,
        actor: Actor,
        message: str,
        request_id: uuid.UUID,
        context: PolicyContext | None = None,
        target: ConversationTarget | None = None,
        context_ref: ContextRef | None = None,
    ) -> ConversationReply:
        """Decide what a message means, then chat, ask, or execute.

        Args:
            target: Where the conversation is happening. When omitted (the API
                path, and tests) no conversation memory is loaded or written -
                the turn is handled statelessly, with identity context still
                built from configuration.
            context_ref: Step 1F.2.3h. Which internal object this turn is about,
                as a **kind and an id and nothing else**. A caller that knows -
                a panel with a content page open, or a Telegram thread whose
                last PR tool ran against one - names it; the record itself is
                resolved and authorized here. When omitted, the thread's own
                most recent PR pointer is used, so a follow-up question about
                "cái này" grounds on the piece the conversation was already
                about.
        """
        state = await self._load_state(actor, target)
        resolved_ref = context_ref or _reference_context(state.references)
        meobot = await self._meobot_context(actor, message, resolved_ref)
        prompt_context = self._prompts.build(
            TurnContext(
                message=message,
                actor=actor,
                assistant=state.assistant,
                profile=state.profile,
                history=state.history,
                rolling_summary=state.rolling_summary,
                references=state.references,
                workflow=state.workflow,
                is_new_thread=state.is_new_thread,
                meobot=meobot,
            )
        )
        route, canned_kind = await self._route(actor, message, prompt_context)

        logger.info(
            "conversation_routed",
            extra={
                "mode": route.mode.value,
                "confidence": route.confidence,
                "possible_tool_name": route.possible_tool_name,
                "provider": self._llm.name,
                # Diagnostic label only; never shown to the user.
                "reason_label": (route.short_reason_label or "")[:80],
                "canned": canned_kind,
                "request_id": str(request_id),
            },
        )

        if route.mode is RouteMode.TOOL:
            return await self._handle_tool(
                actor=actor,
                message=message,
                route=route,
                request_id=request_id,
                context=context,
                target=target,
                state=state,
                prompt_context=prompt_context,
            )
        if route.mode is RouteMode.CLARIFY:
            return await self._handle_clarify(
                actor=actor,
                message=message,
                route=route,
                target=target,
                state=state,
                prompt_context=prompt_context,
            )
        return await self._handle_chat(
            actor=actor,
            message=message,
            request_id=request_id,
            target=target,
            state=state,
            prompt_context=prompt_context,
            canned_kind=canned_kind,
        )

    async def handle_guest_message(
        self,
        *,
        guest: GuestPrincipal,
        message: str,
        request_id: uuid.UUID,
    ) -> ConversationReply:
        """Answer a Guest. Chat generation only, and structurally nothing else.

        This is a separate entry point rather than a flag on
        :meth:`handle_message`, and that is the point. There is no
        :class:`~meobot.domain.identity.models.Actor` in scope here, so there is
        no role to look a permission up against; the router is never consulted,
        so no ``tool`` mode can be produced; the tool registry is never touched,
        so no catalogue can reach the prompt. A Guest cannot execute a tool for
        the same reason a function without a database handle cannot write to the
        database.

        The prompt is built from a Guest-only context - no capability report, no
        workspace, no permissions summary, no recent references - so nothing
        about the team's internal setup is described to somebody who has not
        been added to it.

        Memory is scoped to (this group, this Guest) by the caller's thread key
        and never merges with a registered user's history.
        """
        if not self._settings.chat_enabled:
            return ConversationReply(text=CHAT_DISABLED_REPLY, mode=ConversationMode.CLARIFY)

        target = ConversationTarget(
            bot_id=0,
            chat_id=guest.telegram_chat_id,
            telegram_user_id=guest.telegram_user_id,
        )
        state = await self._load_guest_state(guest, target)

        request = ChatRequest(
            message=message,
            prompt_context=guest_prompt_context(guest, state.assistant),
            history=list(state.history),
            max_output_tokens=self._settings.chat_response_max_tokens,
        )
        try:
            text = (await self._llm.generate_chat_reply(request)).text
        except Exception as exc:
            logger.warning(
                "guest_chat_generation_failed",
                extra={
                    "provider": self._llm.name,
                    "error": type(exc).__name__,
                    "request_id": str(request_id),
                },
            )
            raise

        final = text.strip() or GUEST_FALLBACK_REPLY
        await self._remember(target, user_message=message, assistant_message=final)
        return ConversationReply(text=final, mode=ConversationMode.CHAT)

    async def _load_guest_state(
        self, guest: GuestPrincipal, target: ConversationTarget
    ) -> _TurnState:
        """Load only this Guest's own history in this one group.

        Deliberately does not build an :class:`ActorProfile`: a Guest has no
        profile, no role and no preferred form of address to look up, and
        reaching for one would be the first step towards treating them as a
        member.
        """
        assistant = AssistantContextService(self._settings).defaults()
        try:
            async with self._database.session() as session:
                assistant = await AssistantContextService(self._settings, session).load()
                memory = ChatMemoryService(session, self._settings)
                chat = await memory.load_context(
                    bot_id=target.bot_id,
                    chat_id=target.chat_id,
                    telegram_user_id=target.telegram_user_id,
                )
                return _TurnState(
                    assistant=assistant,
                    profile=ActorProfile(display_name=guest.display_name),
                    history=chat.history,
                    rolling_summary=chat.rolling_summary,
                    is_new_thread=chat.is_new,
                )
        except Exception:
            logger.exception("guest_context_load_failed")
            return _TurnState(
                assistant=assistant, profile=ActorProfile(display_name=guest.display_name)
            )

    async def plan_only(
        self,
        *,
        actor: Actor,
        message: str,
        request_id: uuid.UUID,
        context: PolicyContext | None = None,
    ) -> ConversationReply:
        """Plan-and-execute without the conversational fork.

        Kept for callers that must never receive a chat answer: the internal
        API, and anything scripted.
        """
        plan = await self._llm.plan_tool_action(
            PlanningRequest(
                message=message,
                actor_role=actor.role,
                available_tools=self._registry.summaries_for(actor.role),
            )
        )
        logger.info(
            "plan_produced",
            extra={"intent": plan.intent, "tool_name": plan.tool_name, "provider": self._llm.name},
        )
        return await self.execute_plan(
            actor=actor, plan=plan, request_id=request_id, context=context
        )

    # --- Routing ----------------------------------------------------------
    async def _route(
        self,
        actor: Actor,
        message: str,
        prompt_context: PromptContext,
    ) -> tuple[MessageRoute, str | None]:
        """Decide what kind of message this is, chat-first.

        Returns the route and, when the reply itself can be produced without a
        model, the kind of canned answer to use.
        """
        settled = deterministic_route(message)
        if settled is not None:
            route, canned = settled.route, settled.canned_reply_kind
        else:
            try:
                route = await self._llm.route_message(
                    RouteRequest(
                        message=message,
                        prompt_context=prompt_context.render(),
                        actor_role=actor.role,
                        tool_names=sorted(
                            tool.name for tool in self._registry.summaries_for(actor.role)
                        ),
                    )
                )
                canned = None
            except Exception as exc:
                # Chat-first: a router that cannot answer must not be able to
                # produce a tool call, and must not stop an ordinary conversation.
                logger.warning(
                    "conversation_route_failed",
                    extra={"provider": self._llm.name, "error": type(exc).__name__},
                )
                route, canned = fallback_route(message), None

        # ``CHAT_ENABLED=false`` turns off *chat*, not the bot: an operational
        # message still routes to a tool, and an ambiguous one still asks. Only
        # the conversational outcome is refused, and refusing it after routing
        # is what keeps that true.
        if route.mode is RouteMode.CHAT and not self._settings.chat_enabled:
            return MessageRoute.chat(confidence=1.0, label="chat_disabled"), "chat_disabled"
        return route, canned

    # --- Branches ---------------------------------------------------------
    async def _handle_chat(
        self,
        *,
        actor: Actor,
        message: str,
        request_id: uuid.UUID,
        target: ConversationTarget | None,
        state: _TurnState,
        prompt_context: PromptContext,
        canned_kind: str | None,
    ) -> ConversationReply:
        """Reply conversationally. Executes nothing, changes no business state."""
        replies = DeterministicReplies(
            assistant=state.assistant,
            profile=state.profile,
            report=self._capabilities.report_for(actor),
        )

        if canned_kind == "chat_disabled":
            return await self._reply(CHAT_DISABLED_REPLY, ConversationMode.CLARIFY, message, target)

        text = await self._chat_text(
            message=message,
            prompt_context=prompt_context,
            state=state,
            replies=replies,
            canned_kind=canned_kind,
            request_id=request_id,
        )
        return await self._reply(text, ConversationMode.CHAT, message, target)

    async def _chat_text(
        self,
        *,
        message: str,
        prompt_context: PromptContext,
        state: _TurnState,
        replies: DeterministicReplies,
        canned_kind: str | None,
        request_id: uuid.UUID,
    ) -> str:
        """Produce conversational text, degrading rather than failing.

        Order: the deterministic answer when there is a good one (capabilities
        and identity are *facts*, and a model paraphrasing them can only lose
        accuracy), then generation, then generation with a smaller prompt, then
        the deterministic answer anyway.
        """
        deterministic = self._canned(replies, canned_kind, message)

        if canned_kind in {"capabilities", "actor"}:
            # Answered from the registry and the profile. Nothing a model adds
            # here is worth the risk of it inventing a capability.
            return deterministic or replies.conversational(message)

        request = ChatRequest(
            message=message,
            prompt_context=prompt_context.render(),
            history=list(state.history),
            max_output_tokens=self._settings.chat_response_max_tokens,
        )
        try:
            return (await self._llm.generate_chat_reply(request)).text
        except Exception as exc:
            logger.warning(
                "chat_generation_failed",
                extra={
                    "provider": self._llm.name,
                    "error": type(exc).__name__,
                    "attempt": 1,
                    "request_id": str(request_id),
                },
            )

        try:
            retry = request.model_copy(update={"minimal": True, "history": []})
            return (await self._llm.generate_chat_reply(retry)).text
        except Exception as exc:
            logger.warning(
                "chat_generation_failed",
                extra={
                    "provider": self._llm.name,
                    "error": type(exc).__name__,
                    "attempt": 2,
                    "request_id": str(request_id),
                },
            )

        if deterministic:
            return deterministic
        return replies.conversational(message)

    @staticmethod
    def _canned(replies: DeterministicReplies, kind: str | None, message: str) -> str | None:
        """The deterministic answer for a recognised opener, if there is one."""
        if kind == "greeting":
            return replies.greeting()
        if kind == "identity":
            return replies.identity()
        if kind == "actor":
            return replies.actor()
        if kind == "capabilities":
            return replies.capabilities()
        return None

    async def _handle_clarify(
        self,
        *,
        actor: Actor,
        message: str,
        route: MessageRoute,
        target: ConversationTarget | None,
        state: _TurnState,
        prompt_context: PromptContext,
    ) -> ConversationReply:
        """Ask exactly one question, and remember the context needed to continue."""
        try:
            reply = await self._llm.generate_clarification(
                ClarificationRequest(
                    message=message,
                    prompt_context=prompt_context.render(),
                    missing_information=route.missing_information,
                )
            )
            question = reply.question
            referenced = dict(reply.referenced_context)
        except Exception as exc:
            logger.warning(
                "clarification_generation_failed",
                extra={"provider": self._llm.name, "error": type(exc).__name__},
            )
            replies = DeterministicReplies(
                assistant=state.assistant,
                profile=state.profile,
                report=self._capabilities.report_for(actor),
            )
            question = replies.clarification(route.missing_information)
            referenced = {"missing_information": route.missing_information or ""}

        return await self._reply(
            question,
            ConversationMode.CLARIFY,
            message,
            target,
            data=referenced,
        )

    async def _handle_tool(
        self,
        *,
        actor: Actor,
        message: str,
        route: MessageRoute,
        request_id: uuid.UUID,
        context: PolicyContext | None,
        target: ConversationTarget | None,
        state: _TurnState,
        prompt_context: PromptContext,
    ) -> ConversationReply:
        """Plan the action, then run the unchanged policy -> tool -> audit path."""
        plan = await self._llm.plan_tool_action(
            PlanningRequest(
                message=message,
                actor_role=actor.role,
                available_tools=self._registry.summaries_for(actor.role),
                conversation_hint=prompt_context.active_work or None,
                suggested_tool_name=route.possible_tool_name,
            )
        )
        if plan.is_unknown:
            # Routing said "operation" but planning could not name one. Ask -
            # a guessed plan is exactly what must not reach the policy engine.
            logger.info("tool_plan_unknown", extra={"provider": self._llm.name})
            return await self._handle_clarify(
                actor=actor,
                message=message,
                route=MessageRoute.clarify(
                    missing="thao tác và đối tượng cụ thể", label="plan_unknown"
                ),
                target=target,
                state=state,
                prompt_context=prompt_context,
            )

        reply = await self.execute_plan(
            actor=actor, plan=plan, request_id=request_id, context=context
        )
        await self._remember(
            target,
            user_message=message,
            assistant_message=reply.text,
            tool_name=plan.tool_name if reply.executed_a_tool else None,
            entity_type=reply.tool_result.entity_type if reply.tool_result else None,
            entity_id=reply.tool_result.entity_id if reply.tool_result else None,
        )
        return reply.model_copy(update={"mode": ConversationMode.TOOL})

    async def _reply(
        self,
        text: str,
        mode: ConversationMode,
        message: str,
        target: ConversationTarget | None,
        *,
        data: dict[str, Any] | None = None,
    ) -> ConversationReply:
        """Store the exchange and package the answer."""
        final = text.strip() or UNKNOWN_REPLY
        await self._remember(target, user_message=message, assistant_message=final)
        return ConversationReply(text=final, mode=mode, data=data or {})

    # --- Context loading --------------------------------------------------
    async def _meobot_context(
        self, actor: Actor, message: str, context_ref: ContextRef | None
    ) -> MeoBotAssistantContext | None:
        """The grounded MeoBot context for this turn, or ``None``.

        Opens its **own read-only session** and closes it before the provider is
        called: nothing here writes, and holding a connection across a network
        round-trip to a model is how a pool runs out under load.

        Never raises. Grounding is an enhancement to an answer - a database
        hiccup must degrade the answer, exactly as a lost profile or a lost
        memory does one method below, and must not turn an ordinary chat turn
        into an error. What it must never do is *silently widen*: a failure
        produces no context at all, never a partial one that skipped an
        authorization check.
        """
        try:
            async with self._database.session() as session:
                services = build_pr_services(session, self._settings)
                built = await MeoBotAssistantContextService(services, session).build(
                    actor=actor, message=message, context_ref=context_ref
                )
        except Exception:
            logger.exception("assistant_context_build_failed")
            return None
        # Safe metadata only: ids, names, counts and flags. Never a title, never
        # a comment body - see ``MeoBotAssistantContext.diagnostics``.
        logger.info("assistant_context_built", extra=built.diagnostics())
        return built

    async def _load_state(self, actor: Actor, target: ConversationTarget | None) -> _TurnState:
        """Load memory and identity context for one turn.

        Never raises. Identity context always resolves - from configuration if
        storage is unavailable - because losing it changes *who is speaking*.
        """
        assistant = AssistantContextService(self._settings).defaults()
        profile = ActorProfile(
            telegram_user_id=actor.telegram_user_id,
            display_name=actor.full_name,
            role=actor.role,
        )

        try:
            async with self._database.session() as session:
                assistant = await AssistantContextService(self._settings, session).load()
                profile = await ActorProfileService(session, self._settings).profile_for(actor)
                if target is None:
                    return _TurnState(assistant=assistant, profile=profile)

                memory = ChatMemoryService(session, self._settings)
                chat = await memory.load_context(
                    bot_id=target.bot_id,
                    chat_id=target.chat_id,
                    telegram_user_id=target.telegram_user_id,
                )
                return _TurnState(
                    assistant=assistant,
                    profile=profile,
                    history=chat.history,
                    rolling_summary=chat.rolling_summary,
                    references=tuple(chat.references),
                    is_new_thread=chat.is_new,
                )
        except Exception:
            # Memory and profiles are enhancements. Losing them degrades the
            # answer; they must not stop the answer.
            logger.exception("conversation_context_load_failed")
            return _TurnState(assistant=assistant, profile=profile)

    async def _remember(
        self,
        target: ConversationTarget | None,
        *,
        user_message: str,
        assistant_message: str,
        tool_name: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ) -> None:
        """Store the exchange, redacted, and summarise when it is due.

        Never raises: a memory failure must not swallow a reply the user is
        waiting for.
        """
        if target is None:
            return
        try:
            async with self._database.transaction() as session:
                memory = ChatMemoryService(session, self._settings)
                thread = await memory.get_or_create_thread(
                    bot_id=target.bot_id,
                    chat_id=target.chat_id,
                    telegram_user_id=target.telegram_user_id,
                )
                await memory.record_message(thread=thread, role="user", content=user_message)
                await memory.record_message(
                    thread=thread,
                    role="tool" if tool_name else "assistant",
                    content=assistant_message,
                    related_tool_name=tool_name,
                    related_entity_type=entity_type,
                    related_entity_id=entity_id,
                )
                if entity_type and entity_id:
                    memory.remember_reference(
                        thread,
                        RecentReference(
                            entity_type=entity_type,
                            entity_id=entity_id,
                            display_name=entity_id,
                            confidence=1.0,
                        ),
                    )
                thread_id = thread.id
            async with self._database.transaction() as session:
                await ChatMemoryService(session, self._settings).summarize_if_needed(
                    thread_id, self._llm
                )
        except Exception:
            logger.exception("conversation_memory_write_failed")

    # --- Execution (unchanged path) ---------------------------------------
    async def execute_plan(
        self,
        *,
        actor: Actor,
        plan: ActionPlan,
        request_id: uuid.UUID,
        context: PolicyContext | None = None,
    ) -> ConversationReply:
        """Authorise a plan and act on the verdict."""
        decision = self._policy.evaluate(plan, actor, context)

        if decision.code is DecisionCode.UNKNOWN_INTENT:
            return ConversationReply(text=UNKNOWN_REPLY, plan=plan, decision=decision)

        if not decision.allowed:
            await self._audit_denial(actor, plan, decision, request_id)
            return ConversationReply(text=f"⛔ {decision.reason}", plan=plan, decision=decision)

        if decision.requires_confirmation:
            return await self._request_confirmation(actor, plan, decision, request_id)

        return await self._run_tool(actor, plan, decision, request_id)

    async def confirm(
        self,
        *,
        actor: Actor,
        token: str,
        request_id: uuid.UUID,
        context: PolicyContext | None = None,
    ) -> ConversationReply:
        """Redeem a confirmation token and execute the plan it stored.

        The plan is re-evaluated with ``confirmed=True``: permissions and
        workflow state are re-checked at execution time, not only when the
        confirmation was created.
        """
        async with self._database.transaction() as session:
            audit = AuditService(session)
            confirmations = ConfirmationService(session, self._settings, audit)
            try:
                _, plan = await confirmations.redeem(
                    actor=actor, request_id=request_id, token=token
                )
            except MeoBotError as exc:
                return ConversationReply(text=f"⛔ {exc.message}")

        confirmed_context = (context or PolicyContext()).model_copy(update={"confirmed": True})
        return await self.execute_plan(
            actor=actor, plan=plan, request_id=request_id, context=confirmed_context
        )

    async def cancel_confirmation(
        self,
        *,
        actor: Actor,
        token: str,
        request_id: uuid.UUID,
    ) -> ConversationReply:
        """Reject a pending confirmation so its token can never be redeemed."""
        async with self._database.transaction() as session:
            audit = AuditService(session)
            confirmations = ConfirmationService(session, self._settings, audit)
            await confirmations.reject(actor=actor, request_id=request_id, token=token)
        return ConversationReply(text="Đã huỷ hành động đang chờ xác nhận.")

    async def _request_confirmation(
        self,
        actor: Actor,
        plan: ActionPlan,
        decision: PolicyDecision,
        request_id: uuid.UUID,
    ) -> ConversationReply:
        async with self._database.transaction() as session:
            audit = AuditService(session)
            confirmations = ConfirmationService(session, self._settings, audit)
            row = await confirmations.create(actor=actor, request_id=request_id, plan=plan)
            token = row.confirmation_token

        minutes = self._settings.confirmation_ttl_seconds // 60
        text = (
            f"⚠️ Hành động rủi ro cao: {plan.tool_name}\n"
            f"Tham số: {plan.arguments}\n\n"
            f"Gõ /confirm {token} để xác nhận (hết hạn sau {minutes} phút), "
            f"hoặc /cancel {token} để huỷ."
        )
        return ConversationReply(text=text, plan=plan, decision=decision, confirmation_token=token)

    async def _run_tool(
        self,
        actor: Actor,
        plan: ActionPlan,
        decision: PolicyDecision,
        request_id: uuid.UUID,
    ) -> ConversationReply:
        assert plan.tool_name is not None  # guaranteed by the policy decision
        tool = self._registry.get(plan.tool_name)

        async with self._database.transaction() as session:
            audit = AuditService(session)
            tool_context = ToolContext(
                actor=actor,
                request_id=request_id,
                settings=self._settings,
                session=session,
            )
            try:
                result = await tool.execute(tool_context, plan.arguments)
            except MeoBotError as exc:
                # Audit the failure in its own transaction: the tool's writes
                # must roll back, the audit trail must not.
                logger.warning(
                    "tool_failed", extra={"tool_name": tool.name, "error_code": exc.code}
                )
                await self._audit_failure(actor, plan, request_id, exc)
                raise

            await audit.record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.TOOL_EXECUTED.value,
                result=AuditResult.SUCCESS if result.success else AuditResult.FAILED,
                entity_type=result.entity_type or "tool",
                entity_id=result.entity_id or tool.name,
                after_data={"tool_name": tool.name, "arguments": dict(plan.arguments)},
            )

        return ConversationReply(
            text=result.message,
            plan=plan,
            decision=decision,
            tool_result=result,
            data=result.data,
        )

    async def _audit_denial(
        self,
        actor: Actor,
        plan: ActionPlan,
        decision: PolicyDecision,
        request_id: uuid.UUID,
    ) -> None:
        async with self._database.transaction() as session:
            await AuditService(session).record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.TOOL_DENIED.value,
                result=AuditResult.DENIED,
                entity_type="tool",
                entity_id=plan.tool_name,
                after_data={"decision_code": decision.code.value, "intent": plan.intent},
                error_message=decision.reason,
            )

    async def _audit_failure(
        self,
        actor: Actor,
        plan: ActionPlan,
        request_id: uuid.UUID,
        error: MeoBotError,
    ) -> None:
        async with self._database.transaction() as session:
            await AuditService(session).record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.TOOL_EXECUTED.value,
                result=AuditResult.FAILED,
                entity_type="tool",
                entity_id=plan.tool_name,
                after_data={"error_code": error.code},
                error_message=error.message,
            )

    # --- Compatibility ----------------------------------------------------
    async def decide_only(self, *, actor: Actor, message: str) -> ConversationDecision:
        """One-shot decision, for the internal API.

        Uses the provider's composed ``decide``; the Telegram path does not,
        because it needs the stages separated.
        """
        from meobot.integrations.llm.base import DecisionRequest

        return await self._llm.decide(
            DecisionRequest(
                message=message,
                actor_role=actor.role,
                actor_name=actor.full_name,
                available_tools=self._registry.summaries_for(actor.role),
                capability_brief=self._capabilities.brief_for(actor),
                chat_enabled=self._settings.chat_enabled,
                max_output_tokens=self._settings.chat_response_max_tokens,
            )
        )
