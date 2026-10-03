"""Deterministic offline provider.

``FakeLLMProvider`` is what runs when ``LLM_PROVIDER=fake`` - the default. It
lets the whole conversational path (message -> route -> chat | clarify | tool ->
policy -> reply) be exercised in tests and on the NAS without any AI credential.

It is intentionally dumb: keyword matching over Vietnamese/English phrases,
accent-folded. What changed in 0.4.0 is how *useful* it is expected to be. The
old version appended "MeoBot đang chạy ở chế độ ngoại tuyến" to every sentence,
which made every offline transcript read like an error message and made it
impossible to tell a real conversation bug from the notice. Now:

* the openers a person actually types are answered properly;
* the notice appears once per thread, on the first reply, not on every one;
* simple multi-turn context works - the channel and audience mentioned two
  turns ago are carried into the next answer - so the memory wiring can be
  tested offline;
* the capability answer is assembled from the caller's live capability report,
  never from a hardcoded promise.

It still never guesses an operational target: the ambiguous-scope phrases route
to ``clarify``, exactly as the real provider is instructed to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from meobot.core.logging import get_logger
from meobot.domain.conversations.decision import ConversationDecision
from meobot.domain.conversations.patterns import fold
from meobot.domain.policy.models import ActionPlan, RiskLevel
from meobot.domain.scripts.models import ReviewVerdict
from meobot.domain.sheets.mapping import propose_mapping
from meobot.integrations.llm.base import (
    ChatReply,
    ChatRequest,
    ClarificationReply,
    ClarificationRequest,
    ConversationSummaryResult,
    DecisionRequest,
    MessageRoute,
    PlanningRequest,
    RouteMode,
    RouteRequest,
    StructuredRequest,
    StructuredResponse,
    SummaryRequest,
)
from meobot.integrations.llm.diagnostics import ProviderDiagnostics
from meobot.integrations.llm.structured_tasks import StructuredTaskMixin

logger = get_logger(__name__)

#: Score below which the fake reviewer asks for a revision. Chosen so a short
#: hookless script fails and a complete one passes - enough to exercise both
#: branches of the workflow without an API key.
FAKE_PASS_SCORE = 70


def strip_accents(text: str) -> str:
    """Fold Vietnamese diacritics so 'sức khoẻ' matches 'suc khoe'.

    Delegates to :func:`meobot.domain.conversations.patterns.fold` so the
    offline provider and the deterministic router agree on what a word is -
    including ``đ``, which Unicode normalisation does not fold and which every
    trigger phrase here depends on ("đồng bộ", "đã tạo", "được sử dụng").
    """
    return fold(text)


#: ``tool name -> trigger phrases`` (accent-folded, lower-case).
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "system.health",
        (
            "health",
            "suc khoe",
            "tinh trang he thong",
            "he thong the nao",
            "kiem tra he thong",
            "status",
            "ping",
        ),
    ),
    (
        "script_type.list",
        (
            "script type",
            "the loai",
            "loai kich ban",
            "danh sach the loai",
            "kich ban co nhung loai",
        ),
    ),
    (
        "sheet_profile.list",
        (
            "sheet profile",
            "danh sach sheet",
            "cac sheet",
            "google sheet nao",
            "list sheet",
        ),
    ),
    (
        "script.list_pending",
        (
            "kich ban cho duyet",
            "cho duyet",
            "pending script",
            "kich ban dang cho",
            "can duyet",
            "cho review",
        ),
    ),
    (
        "drive.folder.list",
        (
            "thu muc drive",
            "thu muc",
            "drive folder",
            "folder nao",
            "duoc su dung",
        ),
    ),
    (
        "sheet_template.list",
        (
            "mau sheet",
            "sheet template",
            "template nao",
            "cac mau",
        ),
    ),
    (
        "spreadsheet.list_created",
        (
            "sheet da tao",
            "da tao",
            "sheet meobot tao",
            "created sheet",
        ),
    ),
)

#: Phrases whose target is genuinely ambiguous. Guessing here would approve the
#: wrong script or write into the wrong Sheet, so the fake provider asks -
#: exactly as the real one is instructed to.
_CLARIFY_RULES: tuple[tuple[str, str], ...] = (
    (
        "duyet het",
        "Bạn muốn duyệt những kịch bản nào? Gõ /pending_scripts để xem danh "
        "sách trước, rồi cho mình mã kịch bản cụ thể nhé.",
    ),
    (
        "duyet tat ca",
        "Bạn muốn duyệt những kịch bản nào? Gõ /pending_scripts để xem danh "
        "sách trước, rồi cho mình mã kịch bản cụ thể nhé.",
    ),
    (
        "duyet luon",
        "Bạn muốn duyệt kịch bản nào? Cho mình mã kịch bản cụ thể nhé.",
    ),
    (
        "xoa cai do",
        "MeoBot không có công cụ xoá. Bạn muốn tạm dừng đồng bộ một Sheet, hay "
        "yêu cầu sửa lại một kịch bản?",
    ),
    (
        "sua no",
        "Bạn muốn sửa cái gì — một kịch bản, hay mapping cột của một Sheet?",
    ),
)

_GREETING_TRIGGERS: tuple[str, ...] = (
    "xin chao",
    "chao ban",
    "chao meobot",
    "hello",
    "hi meobot",
    "chao buoi",
    "alo",
)

_IDENTITY_TRIGGERS: tuple[str, ...] = (
    "ban la ai",
    "em la ai",
    "gioi thieu ve ban",
    "meobot la gi",
    "ban ten gi",
)

_WHO_AM_I_TRIGGERS: tuple[str, ...] = (
    "dang noi chuyen voi ai",
    "toi la ai",
    "biet toi la ai",
    "ho so cua toi",
)

_CAPABILITY_TRIGGERS: tuple[str, ...] = (
    "lam duoc gi",
    "giup duoc gi",
    "co the lam gi",
    "chuc nang gi",
    "what can you do",
    "kha nang cua ban",
)

_THANKS_TRIGGERS: tuple[str, ...] = ("cam on", "thanks", "thank you", "cam on ban")

_BYE_TRIGGERS: tuple[str, ...] = ("tam biet", "bye", "chao tam biet")

_IDEA_TRIGGERS: tuple[str, ...] = (
    "bi y tuong",
    "het y tuong",
    "can y tuong",
    "brainstorm",
    "goi y noi dung",
    "y tuong content",
    "y tuong tiktok",
    "khong nghi ra",
)

_REWRITE_TRIGGERS: tuple[str, ...] = (
    "viet lai",
    "sua cau",
    "dien dat lai",
    "rewrite",
    "viet gon lai",
    "lam cho hay hon",
)

_LIMITATION_TRIGGERS: tuple[str, ...] = (
    "chua lam duoc gi",
    "gioi han",
    "han che",
    "khong lam duoc gi",
    "limitation",
)

_HELP_TRIGGERS: tuple[str, ...] = ("giup toi", "ho tro", "help me", "giup minh")

#: Shown once per thread rather than on every sentence. An operator must not
#: mistake the offline provider for a real model; a user must not have to read
#: the disclaimer forty times.
FAKE_CHAT_NOTICE = (
    "\n\n(Đang chạy ngoại tuyến: phần trò chuyện trả lời theo mẫu có sẵn. "
    "Đặt LLM_PROVIDER=openai để trò chuyện đầy đủ.)"
)

#: Facts the offline provider picks out of history so a follow-up turn does not
#: ask again. ``label -> (accent-folded patterns, how to phrase it back)``.
_CHANNEL_PATTERN = re.compile(r"kenh\s+([a-z0-9\s]{2,40})")
_AUDIENCE_PATTERN = re.compile(
    r"(phu nu|nam gioi|dan ong|khach hang|me bim|nguoi|doi tuong)[a-z0-9\s]{0,40}"
)
_GOAL_PATTERN = re.compile(
    r"(noi dau|pain point|muc tieu|chuyen doi|nhan dien|tuong tac)[a-z\s]{0,30}"
)


@dataclass(frozen=True, slots=True)
class _Threads:
    """Facts recovered from recent turns, so a follow-up is not a fresh start."""

    channel: str = ""
    audience: str = ""
    goal: str = ""

    @property
    def has_subject(self) -> bool:
        return bool(self.channel or self.audience)

    def describe(self) -> str:
        parts = [part for part in (self.channel, self.audience, self.goal) if part]
        return ", ".join(parts)


@dataclass
class FakeLLMProvider(StructuredTaskMixin):
    """Keyword-based :class:`~meobot.integrations.llm.base.LLMProvider`.

    Args:
        forced_plan: When set, every plan call returns this plan. Useful in
            tests that need to drive the policy engine with a specific proposal.
        forced_decision: When set, :meth:`decide` returns it unchanged.
        forced_route: When set, :meth:`route_message` returns it unchanged.
        fail_route: When true, :meth:`route_message` raises, so the caller's
            chat-first fallback can be exercised.
        fail_chat: When true, :meth:`generate_chat_reply` raises.
    """

    forced_plan: ActionPlan | None = None
    forced_payload: dict[str, Any] | None = None
    forced_decision: ConversationDecision | None = None
    forced_route: MessageRoute | None = None
    fail_route: bool = False
    fail_chat: bool = False
    seen_messages: list[str] = field(default_factory=list)
    seen_tasks: list[str] = field(default_factory=list)
    seen_decisions: list[str] = field(default_factory=list)
    seen_routes: list[str] = field(default_factory=list)
    seen_chats: list[ChatRequest] = field(default_factory=list)
    _diagnostics: ProviderDiagnostics = field(default_factory=ProviderDiagnostics)

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-deterministic-1"

    @property
    def diagnostics(self) -> ProviderDiagnostics:
        return self._diagnostics

    # --- Routing ----------------------------------------------------------
    async def route_message(self, request: RouteRequest) -> MessageRoute:
        """Route deterministically: ambiguous first, then tools, then chat.

        Order matters and is chosen for safety: an ambiguous-scope phrase
        becomes a clarification before it can become a tool, and an operational
        phrase becomes a tool before it can be answered as chat.
        """
        self.seen_routes.append(request.message)
        if self.fail_route:
            from meobot.core.errors import LLMError

            self._diagnostics.record_error(task="route_message", category="parse_failed")
            raise LLMError("FakeLLMProvider was configured to fail routing.")
        if self.forced_route is not None:
            return self.forced_route
        # A test that forces a plan or a decision is describing what kind of
        # message this is, so routing has to agree with it - otherwise the
        # forced value would be routed past and never reached.
        if self.forced_decision is not None:
            return MessageRoute(
                mode=RouteMode(self.forced_decision.mode.value),
                confidence=self.forced_decision.confidence or 0.9,
                possible_tool_name=(
                    self.forced_decision.action_plan.tool_name
                    if self.forced_decision.action_plan is not None
                    else None
                ),
                short_reason_label="fake:forced_decision",
            )
        if self.forced_plan is not None:
            return MessageRoute.tool(
                tool_name=self.forced_plan.tool_name,
                confidence=0.9,
                label="fake:forced_plan",
            )

        needle = strip_accents(request.message)
        for trigger, _ in _CLARIFY_RULES:
            if trigger in needle:
                return MessageRoute.clarify(
                    missing="đối tượng cụ thể", confidence=0.9, label=f"fake:{trigger}"
                )

        tool_name = self._match_tool(needle, set(request.tool_names))
        if tool_name is not None:
            return MessageRoute.tool(tool_name=tool_name, confidence=0.7, label=f"fake:{tool_name}")

        self._diagnostics.record_success(task="route_message", latency_ms=0)
        return MessageRoute.chat(confidence=0.6, label="fake:chat")

    @staticmethod
    def _match_tool(needle: str, available: set[str]) -> str | None:
        """First tool whose trigger appears, restricted to the actor's catalogue."""
        for tool_name, triggers in _RULES:
            if tool_name not in available:
                continue
            if any(trigger in needle for trigger in triggers):
                return tool_name
        return None

    # --- Chat generation ---------------------------------------------------
    async def generate_chat_reply(self, request: ChatRequest) -> ChatReply:
        """Answer a conversational opener from the deterministic script."""
        self.seen_chats.append(request)
        if self.forced_decision is not None and self.forced_decision.reply_text:
            return ChatReply(text=self.forced_decision.reply_text, confidence=0.9)
        if self.fail_chat:
            from meobot.core.errors import LLMError

            self._diagnostics.record_error(task="generate_chat_reply", category="empty_response")
            raise LLMError("FakeLLMProvider was configured to fail chat generation.")

        needle = strip_accents(request.message)
        threads = self._threads_from(request)
        text = self._compose(needle, request, threads)
        self._diagnostics.record_success(task="generate_chat_reply", latency_ms=0)
        return ChatReply(text=self._with_notice(text, request), confidence=0.6)

    @staticmethod
    def _with_notice(text: str, request: ChatRequest) -> str:
        """Append the offline notice on the first turn of a thread only.

        An operator must not mistake this provider for a real model. A user
        must not have to read the same disclaimer forty times - which is what
        the previous version did, and it made every offline transcript look
        like an error report.
        """
        if request.history:
            return text
        return text + FAKE_CHAT_NOTICE

    def _compose(self, needle: str, request: ChatRequest, threads: _Threads) -> str:
        """Pick the deterministic answer for one message."""
        if any(trigger in needle for trigger in _CAPABILITY_TRIGGERS):
            return self._capabilities_reply(request)

        if any(trigger in needle for trigger in _IDENTITY_TRIGGERS):
            return (
                "Mình là MeoBot — trợ lý điều hành và sáng tạo nội dung của bạn. "
                "Mình giúp bạn brainstorm hướng nội dung, phản biện ý tưởng, review "
                "kịch bản, quản lý các Sheet kịch bản và chạy các thao tác vận hành "
                "có kiểm duyệt quyền hạn."
            )

        if any(trigger in needle for trigger in _WHO_AM_I_TRIGGERS):
            return self._actor_reply(request)

        if any(trigger in needle for trigger in _LIMITATION_TRIGGERS):
            return self._limitations_reply(request)

        if any(trigger in needle for trigger in _GREETING_TRIGGERS):
            return (
                "Chào bạn, mình là MeoBot — trợ lý điều hành và sáng tạo nội dung "
                "của bạn. Hôm nay bạn muốn bắt đầu với công việc, kịch bản hay một "
                "ý tưởng mới?"
            )

        if any(trigger in needle for trigger in _THANKS_TRIGGERS):
            return "Không có gì. Cần gì thêm bạn cứ nhắn mình nhé."

        if any(trigger in needle for trigger in _BYE_TRIGGERS):
            return "Chào bạn nhé. Khi nào cần thì gọi mình."

        if any(trigger in needle for trigger in _REWRITE_TRIGGERS):
            return (
                "Bạn gửi mình đoạn cần viết lại nhé. Cho mình biết luôn bạn muốn "
                "giọng nào — gọn và thẳng, hay mềm và gần gũi — để mình chỉnh đúng ý."
            )

        if any(trigger in needle for trigger in _IDEA_TRIGGERS):
            if threads.has_subject:
                return (
                    f"Tiếp tục với {threads.describe()} nhé. Bạn muốn mình đi theo "
                    "hướng chạm vào nỗi đau có thật, hay hướng đưa giải pháp trước "
                    "rồi mới nói vấn đề?"
                )
            return (
                "Mình có thể cùng bạn tìm hướng. Đây là nội dung cho thương hiệu, "
                "một kênh bác sĩ, hay thương hiệu cá nhân?"
            )

        if any(trigger in needle for trigger in _HELP_TRIGGERS):
            return (
                "Mình giúp được ngay. Bạn cần gì — xem Sheet kịch bản, đồng bộ nội "
                "dung, review một kịch bản, hay brainstorm một hướng nội dung mới?"
            )

        if threads.has_subject:
            return (
                f"Mình đang bám theo {threads.describe()}. Bạn muốn mình triển khai "
                "tiếp phần nào — hook, dàn ý, hay danh sách hướng nội dung?"
            )

        return (
            "Mình chưa nắm rõ ý bạn. Bạn nói thêm một chút về mục tiêu hoặc kênh "
            "đang làm, mình sẽ bám theo đó."
        )

    @staticmethod
    def _threads_from(request: ChatRequest) -> _Threads:
        """Recover channel, audience and objective from recent turns.

        This is what makes offline multi-turn testable: "Cho kênh bác sĩ Tiến"
        followed by "Đối tượng là phụ nữ trên 35" must not reset the subject.
        """
        haystack = " ".join(
            strip_accents(turn.content) for turn in request.history if turn.role == "user"
        )
        haystack = f"{haystack} {strip_accents(request.message)}"

        channel = ""
        channel_match = _CHANNEL_PATTERN.search(haystack)
        if channel_match:
            channel = "kênh " + channel_match.group(1).strip()[:40]

        audience = ""
        audience_match = _AUDIENCE_PATTERN.search(haystack)
        if audience_match:
            audience = audience_match.group(0).strip()[:60]

        goal = ""
        goal_match = _GOAL_PATTERN.search(haystack)
        if goal_match:
            goal = goal_match.group(0).strip()[:40]

        return _Threads(channel=channel, audience=audience, goal=goal)

    @staticmethod
    def _capabilities_reply(request: ChatRequest) -> str:
        """Describe what MeoBot can do, from the caller's capability context."""
        block = _section_of(request.prompt_context, "[AVAILABLE CAPABILITIES]")
        if block:
            return "Đây là những gì mình làm được với quyền của bạn:\n\n" + block
        return (
            "Mình quản lý các Sheet kịch bản, đồng bộ nội dung, review kịch bản, "
            "theo dõi các phiên bản sửa, tạo mã mời cho nhân viên và tạo Sheet mới "
            "trên Google Drive. Mình chưa duyệt video và chưa đăng Facebook/TikTok."
        )

    @staticmethod
    def _actor_reply(request: ChatRequest) -> str:
        """Say who MeoBot thinks it is talking to, from the prompt context."""
        block = _section_of(request.prompt_context, "[CURRENT USER]")
        if block:
            return "Đây là những gì mình biết về bạn:\n\n" + block
        # Deliberately not "chưa có hồ sơ": in this codebase "hồ sơ" is the
        # optional ActorProfile, and telling somebody to go fill one in was the
        # 0.6.0a2.1 wording bug. What is missing here is only context.
        return "Lượt này mình chưa nhận được thông tin về bạn. Bạn gõ /whoami để xem nhé."

    @staticmethod
    def _limitations_reply(request: ChatRequest) -> str:
        block = _section_of(request.prompt_context, "[CURRENT LIMITATIONS]")
        if block:
            return "Những việc mình chưa làm được:\n\n" + block
        return (
            "Mình chưa duyệt video, chưa đăng Facebook/TikTok tự động, chưa làm báo "
            "cáo mạng xã hội và không có công cụ xoá file trên Drive."
        )

    async def generate_clarification(self, request: ClarificationRequest) -> ClarificationReply:
        """Ask the scripted question for an ambiguous phrase."""
        if self.forced_decision is not None and self.forced_decision.clarification_question:
            return ClarificationReply(question=self.forced_decision.clarification_question)
        needle = strip_accents(request.message)
        for trigger, question in _CLARIFY_RULES:
            if trigger in needle:
                return ClarificationReply(
                    question=question,
                    referenced_context={"trigger": trigger},
                )
        missing = request.missing_information or "đối tượng cụ thể"
        return ClarificationReply(
            question=f"Bạn cho mình biết {missing} nhé — mình không muốn đoán nhầm.",
            referenced_context={"missing_information": missing},
        )

    # --- Tool planning -----------------------------------------------------
    async def plan_tool_action(self, request: PlanningRequest) -> ActionPlan:
        """Match the message against the rule table; default to 'unknown'."""
        self.seen_messages.append(request.message)
        if self.forced_plan is not None:
            return self.forced_plan
        if self.forced_decision is not None and self.forced_decision.action_plan is not None:
            return self.forced_decision.action_plan

        tool_name = self._match_tool(strip_accents(request.message), request.tool_names())
        if tool_name is None:
            return ActionPlan.unknown(reasoning="FakeLLMProvider found no matching rule")

        logger.info("fake_llm_matched", extra={"tool_name": tool_name, "provider": self.name})
        return ActionPlan(
            intent=tool_name.replace(".", "_"),
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            tool_name=tool_name,
            arguments={},
            reasoning=f"FakeLLMProvider keyword match for {tool_name}",
            confidence=0.6,
        )

    async def plan(self, request: PlanningRequest) -> ActionPlan:
        """Alias kept for the internal API."""
        return await self.plan_tool_action(request)

    # --- Compatibility: one-shot decision ---------------------------------
    async def decide(self, request: DecisionRequest) -> ConversationDecision:
        """Route and answer in one call, for the internal API and its tests."""
        self.seen_decisions.append(request.message)
        if self.forced_decision is not None:
            return self.forced_decision

        needle = strip_accents(request.message)

        # 1. Ambiguous scope: ask, never guess.
        for trigger, question in _CLARIFY_RULES:
            if trigger in needle:
                return ConversationDecision.clarify(
                    question, confidence=0.5, internal_summary=f"fake:clarify:{trigger}"
                )

        # 2. Operational request: a real tool, through the real policy engine.
        plan = await self.plan_tool_action(
            PlanningRequest(
                message=request.message,
                actor_role=request.actor_role,
                available_tools=request.available_tools,
            )
        )
        if not plan.is_unknown:
            return ConversationDecision.tool(
                plan, confidence=plan.confidence, internal_summary=f"fake:tool:{plan.tool_name}"
            )

        # 3. Conversation, if it is switched on at all.
        if not request.chat_enabled:
            return ConversationDecision.clarify(
                "Chế độ trò chuyện đang tắt. Bạn dùng /help để xem các lệnh nhé.",
                internal_summary="fake:chat_disabled",
            )

        reply = await self.generate_chat_reply(
            ChatRequest(
                message=request.message,
                prompt_context=(
                    f"[AVAILABLE CAPABILITIES]\n{request.capability_brief}"
                    if request.capability_brief
                    else ""
                ),
                history=list(request.history),
                max_output_tokens=request.max_output_tokens,
            )
        )
        return ConversationDecision.chat(
            reply.text, confidence=reply.confidence, internal_summary="fake:chat"
        )

    # --- Summarisation ----------------------------------------------------
    async def summarize_conversation(self, request: SummaryRequest) -> ConversationSummaryResult:
        """Keep the facts, drop the pleasantries. Deterministic, no network."""
        if not request.turns:
            return ConversationSummaryResult(summary=request.previous_summary or "")

        facts = [
            turn.content.replace("\n", " ")[:120]
            for turn in request.turns
            if not _is_pleasantry(turn.content)
        ]
        head = f"{request.previous_summary} " if request.previous_summary else ""
        if not facts:
            return ConversationSummaryResult(summary=(request.previous_summary or "")[:4000])
        body = "Dữ kiện đã trao đổi: " + " | ".join(facts[-6:])
        return ConversationSummaryResult(summary=(head + body)[:2000])

    async def summarize(self, request: SummaryRequest) -> str:
        """Alias returning bare text."""
        return (await self.summarize_conversation(request)).summary

    # --- Structured tasks --------------------------------------------------
    async def complete_structured(self, request: StructuredRequest) -> StructuredResponse:
        """Answer a structured task deterministically, without any network call.

        The answers are real enough to drive the workflow end to end: the
        mapping task reuses the same alias table the deterministic mapper uses,
        and the review task scores a script from observable facts (length, hook
        presence) so both the pass and the revision branch are reachable.
        """
        self.seen_tasks.append(request.task)
        payload = self.forced_payload or self._answer(request)
        self._diagnostics.record_success(task=request.task, latency_ms=0)
        return StructuredResponse(
            payload=payload,
            provider=self.name,
            model=self.model,
            usage={},
        )

    def _answer(self, request: StructuredRequest) -> dict[str, Any]:
        if request.task == "sheet_mapping":
            headers = [str(header) for header in request.context.get("headers", [])]
            proposal = propose_mapping(headers)
            return proposal.model_dump(mode="json")
        if request.task == "script_review":
            return self._review(request.context)
        if request.task == "pr_full_review":
            return self._pr_full_review(request.context)
        return {}

    @staticmethod
    def _pr_full_review(context: dict[str, Any]) -> dict[str, Any]:
        """Findings from the draft's shape. Deterministic, never random.

        Reaches all three outcomes so the whole gate is exercisable offline:
        an empty or very short script blocks, a script with no hook warns, and
        anything of reasonable length with a hook passes clean. The severities
        are what the caller derives the outcome from - this method never says
        which outcome it produced, because nothing is allowed to.
        """
        body = str(context.get("script_text") or "")
        hook = str(context.get("hook") or "")
        findings: list[dict[str, Any]] = []

        if len(body.strip()) < 80:
            findings.append(
                {
                    "category": "CONTENT_QUALITY",
                    "severity": "BLOCKER",
                    "message": "Nội dung quá ngắn để đánh giá hoặc sản xuất.",
                    "suggestion": "Viết đầy đủ phần thân kịch bản trước khi gửi review.",
                }
            )
        elif not hook.strip():
            findings.append(
                {
                    "category": "STRUCTURE",
                    "severity": "WARNING",
                    "message": "Chưa có hook mở đầu rõ ràng.",
                    "suggestion": "Thêm một câu mở đầu thu hút ở đầu kịch bản.",
                }
            )
        else:
            findings.append(
                {
                    "category": "CTA",
                    "severity": "SUGGESTION",
                    "message": "Có thể thêm lời kêu gọi hành động ở cuối.",
                    "suggestion": None,
                }
            )
        return {"summary": "Đánh giá tự động ngoại tuyến.", "findings": findings}

    @staticmethod
    def _review(context: dict[str, Any]) -> dict[str, Any]:
        """Score a script from its shape. Deterministic, never random."""
        body = str(context.get("script_body", ""))
        hook = str(context.get("hook", "") or "")
        title = str(context.get("title", "") or "kịch bản")

        score = 55
        if hook.strip():
            score += 20
        if len(body) >= 400:
            score += 15
        elif len(body) >= 150:
            score += 8
        if str(context.get("production_notes", "") or "").strip():
            score += 5
        score = min(score, 95)

        issues: list[str] = []
        if not hook.strip():
            issues.append("Chưa có hook mở đầu - 3 giây đầu quyết định lượt xem.")
        if len(body) < 150:
            issues.append("Nội dung quá ngắn so với một video short hoàn chỉnh.")

        verdict = (
            ReviewVerdict.APPROVE
            if score >= FAKE_PASS_SCORE and not issues
            else ReviewVerdict.MINOR_REVISION
            if score >= FAKE_PASS_SCORE
            else ReviewVerdict.MAJOR_REVISION
        )
        return {
            "overall_score": score,
            "verdict": verdict.value,
            "short_summary_for_telegram": (
                f"[FAKE] {title[:60]} — {score}/100. "
                + ("Đủ điều kiện sản xuất." if not issues else issues[0])
            ),
            "strengths": ["Chủ đề rõ ràng."] + (["Có hook mở đầu."] if hook.strip() else []),
            "critical_issues": issues,
            "improvement_recommendations": [
                "Thêm call-to-action ở cuối video.",
                "Rút ngắn các câu dài trong phần thân.",
            ],
            "hook_analysis": "Đánh giá offline: " + ("có hook." if hook.strip() else "thiếu hook."),
            "structure_analysis": f"Độ dài thân kịch bản: {len(body)} ký tự.",
            "audience_fit": "Không đánh giá được khi chạy FakeLLMProvider.",
            "brand_safety_notes": "Không phát hiện vấn đề rõ ràng (đánh giá offline).",
            "factual_risk_notes": "Cần người thật kiểm chứng số liệu.",
            "medical_risk_notes": "Cần chuyên môn y tế kiểm chứng nếu có nội dung sức khoẻ.",
            "criterion_scores": {},
            "revised_hook_suggestion": (
                None if hook.strip() else f"Bạn có biết {title.lower()} ảnh hưởng tới bạn mỗi ngày?"
            ),
            "revised_script_suggestion": None,
        }


def _is_pleasantry(content: str) -> bool:
    """True for greetings and thanks - the turns a summary should drop."""
    needle = strip_accents(content).strip()
    if len(needle) > 40:
        return False
    return any(
        trigger in needle for trigger in _GREETING_TRIGGERS + _THANKS_TRIGGERS + _BYE_TRIGGERS
    )


def _section_of(context: str, heading: str) -> str:
    """Body of one ``[SECTION]`` of a rendered prompt context, if present."""
    if heading not in context:
        return ""
    after = context.split(heading, 1)[1]
    body = after.split("\n[", 1)[0]
    return body.strip()


#: Used by tests and by ``/chat_status`` to explain the offline mode.
__all__ = ["FAKE_CHAT_NOTICE", "FAKE_PASS_SCORE", "FakeLLMProvider", "strip_accents"]
