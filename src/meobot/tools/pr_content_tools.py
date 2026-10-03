"""Telegram tools over PR content.

Every handler here is a translator. It turns "Tạo một nội dung mới cho Apexmed
về chăm sóc sau nâng mũi" into a
:class:`~meobot.application.pr_content_service.CreateContentCommand`, hands it
to the service, and renders what comes back. It contains no workflow rule, no
version arithmetic and no permission decision - those are in the services,
where the future web client gets them too.

Two things this module is careful about:

**No code is ever asked for or accepted.** ``CNT-2026-000123`` comes from
:class:`~meobot.application.pr_code_service.PrCodeService` inside the same
transaction as the row it names. There is no argument a caller - or a model -
could fill to influence it.

**``pr.content.transition`` exposes manual edges only.** The tool passes the
requested stage to
:meth:`~meobot.application.pr_workflow_service.PrContentWorkflowService.request_transition`,
which accepts ``MANUAL`` edges and refuses everything else. Asking it for
``AI_REVIEW → TEAM_LEAD_REVIEW`` fails in the workflow policy, not here - so
the refusal holds for every client rather than for the one that remembered to
check.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from meobot.application.pr_content_service import (
    ContentTargetSpec,
    CreateContentCommand,
    ReviseContentCommand,
)
from meobot.core.errors import ToolExecutionError
from meobot.db.models.pr import PrBrand, PrChannel, PrContentTarget
from meobot.domain.identity.models import Actor
from meobot.domain.permissions.matrix import Permission
from meobot.domain.policy.models import RiskLevel
from meobot.domain.pr.models import PrContentType, PrPriority, PrWorkflowStage
from meobot.tools.base import ToolContext, ToolDefinition, ToolResult
from meobot.tools.pr_errors import pr_errors
from meobot.tools.pr_presenters import (
    ai_result_label,
    format_content_list,
    format_content_summary,
    stage_label,
)
from meobot.tools.pr_support import (
    PrServices,
    person_names,
    pr_services,
    resolve_channel,
    resolve_content,
    resolve_deadline,
    resolve_person,
    timezone_of,
)

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 25

#: The stages a person may drive by hand. Exactly the ``MANUAL`` edges of
#: :data:`~meobot.domain.pr.workflow.CONTENT_TRANSITIONS`, listed here only so
#: the tool schema can describe them to the model. **The list is not the
#: authority** - the workflow service re-checks every one, and a stage that
#: crept in here without a matching edge would still be refused.
MANUAL_TARGETS: tuple[str, ...] = (
    PrWorkflowStage.BRIEFING.value,
    PrWorkflowStage.SCRIPTING.value,
    PrWorkflowStage.AI_REVIEW.value,
    # Step 1F.2.10: the direct submission, ``SCRIPTING -> TEAM_LEAD_REVIEW``.
    PrWorkflowStage.TEAM_LEAD_REVIEW.value,
    PrWorkflowStage.PRODUCTION.value,
    PrWorkflowStage.INTERNAL_REVIEW.value,
    PrWorkflowStage.PUBLISHED.value,
    PrWorkflowStage.ARCHIVED.value,
    PrWorkflowStage.CANCELLED.value,
)


class CreateContentArgs(BaseModel):
    """Arguments for ``pr.content.create``.

    Note the absence of ``code``: it is generated. A model that invented one
    would have nowhere to put it.

    **And the absence of ``initial_resources``.** Step 1F.2.3e.1 lets the web
    form send review material along with a new item, and
    :class:`~meobot.application.pr_content_service.CreateContentCommand` accepts
    it from any caller - this tool deliberately does not offer it. A resource is
    a *label plus an exact location*, and the only place a location exists
    verbatim is on somebody's screen, pasted; asking a model to fill an array of
    URLs from a sentence is asking it to guess links, which is the one failure
    mode this feature must not have. Attaching material stays a paste, on the
    content detail page, where the person doing it can see what they pasted.

    Nothing is lost by that: resources are optional everywhere, so a piece
    created through the bot is complete without them.
    """

    model_config = ConfigDict(extra="forbid")

    brand: str = Field(min_length=1, max_length=200, description="Tên hoặc mã thương hiệu.")
    title: str = Field(min_length=1, max_length=300, description="Tiêu đề nội dung.")
    topic: str | None = Field(default=None, max_length=2000)
    hook: str | None = Field(default=None, max_length=2000)
    brief: str | None = Field(default=None, max_length=8000)
    script_text: str | None = Field(default=None, max_length=20000)
    priority: str | None = Field(
        default=None, description="NORMAL, HIGH, URGENT hoặc CRITICAL. Mặc định NORMAL."
    )
    content_type: str | None = Field(
        default=None,
        description=(
            "Bắt buộc. Loại nội dung: ULTRA_SHORT_SCRIPT (kịch bản siêu ngắn), "
            "SHORT_VIDEO_SCRIPT (kịch bản video ngắn), FACEBOOK_POST (bài đăng "
            "Facebook), LONG_YOUTUBE_SCRIPT (kịch bản YouTube dài), PRESS_ARTICLE "
            "(báo chí), CORPORATE_TVC (TVC doanh nghiệp)."
        ),
    )
    planned_publish: str | None = Field(
        default=None, description="Ngày dự kiến đăng, ví dụ 'thứ Sáu' hoặc '31/8'."
    )
    channels: list[str] = Field(
        default_factory=list, max_length=10, description="Tên hoặc mã kênh sẽ đăng."
    )
    owner: str | None = Field(
        default=None, description="Tên người phụ trách. Mặc định là người đang thao tác."
    )


class ContentReferenceArgs(BaseModel):
    """Arguments for tools addressing one content item."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(
        min_length=1, max_length=300, description="Mã nội dung (CNT-…) hoặc mô tả ngắn."
    )


class ListContentArgs(BaseModel):
    """Arguments for ``pr.content.list``."""

    model_config = ConfigDict(extra="forbid")

    stage: str | None = Field(default=None, description="Lọc theo bước, ví dụ SCRIPTING.")
    brand: str | None = Field(default=None, max_length=200)
    owner: str | None = Field(default=None, max_length=200, description="Tên người phụ trách.")
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0, le=1000)


class ReviseContentArgs(BaseModel):
    """Arguments for ``pr.content.revise``."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=300)
    title: str | None = Field(default=None, max_length=300)
    topic: str | None = Field(default=None, max_length=2000)
    hook: str | None = Field(default=None, max_length=2000)
    brief: str | None = Field(default=None, max_length=8000)
    script_text: str | None = Field(default=None, max_length=20000)
    change_note: str | None = Field(default=None, max_length=2000)
    expected_version: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Phiên bản người dùng đang xem. Bỏ trống nếu vừa xem bản mới nhất; "
            "hệ thống sẽ dùng phiên bản hiện tại."
        ),
    )


class TransitionArgs(BaseModel):
    """Arguments for ``pr.content.transition``."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=300)
    target: str = Field(description=f"Bước muốn chuyển tới. Hợp lệ: {', '.join(MANUAL_TARGETS)}.")
    note: str | None = Field(default=None, max_length=1000)


async def _brand_id(services: PrServices, *, actor: Actor, phrase: str) -> uuid.UUID:
    """One brand from a name or a code, or a question.

    Brands are few and their names are typed by people who know them, so a
    channel-style search is enough. Ambiguity still asks rather than picking.
    """
    needle = f"%{phrase.strip()}%"
    result = await services.session.execute(
        select(PrBrand).where(or_(PrBrand.code.ilike(needle), PrBrand.name.ilike(needle)))
    )
    brands = list(result.scalars().all())
    if len(brands) == 1:
        return brands[0].id
    if brands:
        listed = "\n".join(f"{index}. {b.code} — {b.name}" for index, b in enumerate(brands, 1))
        raise ToolExecutionError(
            f'Mình tìm thấy {len(brands)} thương hiệu khớp với "{phrase}":\n{listed}\n'
            "Bạn cho mình mã thương hiệu cụ thể nhé.",
            details={"reason": "ambiguous_brand"},
        )
    raise ToolExecutionError(
        f'Mình không tìm thấy thương hiệu nào khớp với "{phrase}".',
        details={"reason": "brand_not_found", "query": phrase},
    )


async def _create_handler(context: ToolContext, arguments: CreateContentArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        tz = timezone_of(context)

        brand_id = await _brand_id(services, actor=context.actor, phrase=arguments.brand)
        owner_id = (
            await resolve_person(services, name=arguments.owner)
            if arguments.owner
            else context.actor.user_id
        )
        if owner_id is None:
            raise ToolExecutionError(
                "Tài khoản của bạn chưa được đăng ký trong MeoBot nên chưa tạo nội dung được.",
                details={"reason": "actor_has_no_user_row"},
            )

        # A loop rather than a comprehension: each channel resolution may raise
        # an ambiguity question, and the first one that does should stop the
        # command rather than be buried inside a generator.
        targets: list[ContentTargetSpec] = []
        for name in arguments.channels:
            channel = await resolve_channel(services, actor=context.actor, reference=name)
            targets.append(ContentTargetSpec(channel_id=channel.id))

        snapshot = await services.content.create_content(
            actor=context.actor,
            request_id=context.request_id,
            command=CreateContentCommand(
                title=arguments.title,
                brand_id=brand_id,
                owner_user_id=owner_id,
                topic=arguments.topic,
                hook=arguments.hook,
                brief=arguments.brief,
                script_text=arguments.script_text,
                priority=_priority(arguments.priority),
                content_type=_content_type(arguments.content_type),
                # Step 1F.2.3e. A person creating content through the bot is a
                # human-facing create path like the web form, so a format is
                # required here too - and unlike ``_priority`` there is no
                # silent fallback, because guessing a format is exactly what
                # the nullable column exists to avoid.
                require_content_type=True,
                planned_publish_at=await resolve_deadline(
                    services, text=arguments.planned_publish, context=context
                ),
                targets=tuple(targets),
            ),
        )
        names = await person_names(services, [snapshot.content.owner_user_id])
        summary = format_content_summary(
            snapshot.content,
            version=snapshot.version,
            tz=tz,
            owner_name=names.get(str(snapshot.content.owner_user_id)),
            target_names=list(arguments.channels),
        )
        return ToolResult(
            success=True,
            message=f"✅ Đã tạo nội dung.\n\n{summary}",
            data={
                "content_id": str(snapshot.content.id),
                "code": snapshot.content.code,
                "workflow_stage": snapshot.content.workflow_stage.value,
                "version_no": snapshot.version_no,
                "target_count": len(targets),
            },
            entity_type="pr_content",
            entity_id=snapshot.content.code,
        )


def _content_type(raw: str | None) -> PrContentType | None:
    """Parse the format, or return ``None`` so the service refuses.

    Deliberately unlike :func:`_priority`, which falls back to ``NORMAL`` on an
    unrecognised string. There is no sensible default format, and picking one
    would write a classification nobody chose - the thing Step 1F.2.3e's
    nullable column exists to prevent. An unparseable value therefore reaches
    the service as "absent" and comes back as a refusal naming the field.
    """
    if not raw:
        return None
    try:
        return PrContentType(raw.strip().upper())
    except ValueError:
        return None


def _priority(raw: str | None) -> PrPriority:
    if not raw:
        return PrPriority.NORMAL
    try:
        return PrPriority(raw.strip().upper())
    except ValueError:
        return PrPriority.NORMAL


async def _get_handler(context: ToolContext, arguments: ContentReferenceArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        tz = timezone_of(context)
        content = await resolve_content(services, actor=context.actor, reference=arguments.content)
        detail = await services.queries.get_content(actor=context.actor, content_id=content.id)
        tasks = list(
            await services.queries.list_tasks(
                actor=context.actor, content_id=content.id, open_only=True, limit=5
            )
        )
        latest_ai = (
            await services.ai_reviews.latest_gating_review(
                content.id, version_no=detail.current_version.version_no
            )
            if detail.current_version
            else None
        )
        names = await person_names(services, [content.owner_user_id])
        channel_names = await _target_names(services, detail.targets)

        body = format_content_summary(
            content,
            version=detail.current_version,
            tz=tz,
            owner_name=names.get(str(content.owner_user_id)),
            target_names=channel_names,
        )
        extra: list[str] = []
        if tasks:
            extra.append("Task đang mở: " + ", ".join(task.code for task in tasks))
        if latest_ai is not None:
            extra.append(
                f"AI review (v{latest_ai.reviewed_version}): {ai_result_label(latest_ai.result)}"
            )
        message = body + ("\n" + "\n".join(extra) if extra else "")
        return ToolResult(
            success=True,
            message=message,
            data={
                "content_id": str(content.id),
                "code": content.code,
                "workflow_stage": content.workflow_stage.value,
                "version_no": detail.current_version_no,
                "open_task_codes": [task.code for task in tasks],
                "latest_ai_result": latest_ai.result.value if latest_ai else None,
            },
            entity_type="pr_content",
            entity_id=content.code,
        )


async def _target_names(services: PrServices, targets: Sequence[PrContentTarget]) -> list[str]:
    """Channel codes for the planned targets, for a one-line summary."""
    names: list[str] = []
    for target in targets:
        channel = await services.session.get(PrChannel, target.channel_id)
        if channel is not None:
            names.append(channel.code)
    return names


async def _list_handler(context: ToolContext, arguments: ListContentArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        stage = _stage(arguments.stage)
        brand_id = (
            await _brand_id(services, actor=context.actor, phrase=arguments.brand)
            if arguments.brand
            else None
        )
        owner_id = await resolve_person(services, name=arguments.owner) if arguments.owner else None
        contents = list(
            await services.queries.list_contents(
                actor=context.actor,
                brand_id=brand_id,
                stage=stage,
                owner_user_id=owner_id,
                limit=arguments.limit,
                offset=arguments.offset,
            )
        )
        rows = [(content, None) for content in contents]
        heading = "📄 <b>Nội dung</b>" + (f" · {stage_label(stage)}" if stage else "")
        return ToolResult(
            success=True,
            message=format_content_list(
                rows, heading=heading, empty="Không có nội dung nào khớp bộ lọc."
            ),
            data={
                "count": len(contents),
                "items": [
                    {
                        "code": content.code,
                        "title": content.title,
                        "workflow_stage": content.workflow_stage.value,
                    }
                    for content in contents
                ],
            },
            entity_type="pr_content",
        )


def _stage(raw: str | None) -> PrWorkflowStage | None:
    if not raw:
        return None
    try:
        return PrWorkflowStage(raw.strip().upper())
    except ValueError:
        raise ToolExecutionError(
            f'Mình chưa hiểu bước "{raw}".',
            details={"reason": "unknown_stage", "query": raw},
        ) from None


async def _revise_handler(context: ToolContext, arguments: ReviseContentArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        content = await resolve_content(services, actor=context.actor, reference=arguments.content)

        # When the caller did not state a version, use the one that is current
        # *right now*. That is the "initiated from a displayed context" case:
        # anything else would be guessing on the author's behalf. If somebody
        # else writes a version between this read and the write, the service's
        # own lock and check produce PrStaleVersionError - which the error
        # mapper turns into a sentence naming both versions. Nothing here
        # retries.
        expected = arguments.expected_version
        if expected is None:
            expected = (await services.content.require_current_version(content.id)).version_no

        snapshot = await services.content.revise_content(
            actor=context.actor,
            request_id=context.request_id,
            command=ReviseContentCommand(
                content_id=content.id,
                expected_version=expected,
                title=arguments.title,
                topic=arguments.topic,
                hook=arguments.hook,
                brief=arguments.brief,
                script_text=arguments.script_text,
                change_note=arguments.change_note,
            ),
        )
        return ToolResult(
            success=True,
            message=(
                f"✏️ Đã tạo phiên bản v{snapshot.version_no} cho <b>{content.code}</b>.\n"
                f"Bước hiện tại: {stage_label(snapshot.content.workflow_stage)}"
            ),
            data={
                "code": content.code,
                "version_no": snapshot.version_no,
                "workflow_stage": snapshot.content.workflow_stage.value,
            },
            entity_type="pr_content",
            entity_id=content.code,
        )


async def _transition_handler(context: ToolContext, arguments: TransitionArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        content = await resolve_content(services, actor=context.actor, reference=arguments.content)
        target = _stage(arguments.target)
        if target is None:
            raise ToolExecutionError(
                "Bạn cho mình biết muốn chuyển sang bước nào nhé.",
                details={"reason": "missing_target"},
            )
        updated = await services.workflow.request_transition(
            actor=context.actor,
            request_id=context.request_id,
            content_id=content.id,
            target=target,
            note=arguments.note,
        )
        return ToolResult(
            success=True,
            message=(
                f"➡️ <b>{content.code}</b> đã chuyển sang: {stage_label(updated.workflow_stage)}."
            ),
            data={"code": content.code, "workflow_stage": updated.workflow_stage.value},
            entity_type="pr_content",
            entity_id=content.code,
        )


async def _submit_ai_review_handler(
    context: ToolContext, arguments: ContentReferenceArgs
) -> ToolResult:
    """``SCRIPTING → AI_REVIEW``, which now starts a real review.

    Still nothing but a transition *here*: this handler calls no model and
    writes no ``pr_ai_reviews`` row. What changed in Step 1F is what the
    transition causes - ``PrContentWorkflowService.apply`` queues a durable
    ``FULL_REVIEW`` run when content enters ``AI_REVIEW``, so every caller gets
    one without each transport remembering to ask.

    The message can now honestly say a review is starting, because a worker
    exists to run it. It does not promise a result in Telegram: the findings are
    a screenful, and the web panel is where they are read.
    """
    with pr_errors():
        services = pr_services(context)
        content = await resolve_content(services, actor=context.actor, reference=arguments.content)
        updated = await services.workflow.request_transition(
            actor=context.actor,
            request_id=context.request_id,
            content_id=content.id,
            target=PrWorkflowStage.AI_REVIEW,
            note="submitted_for_ai_review",
        )
        return ToolResult(
            success=True,
            message=(
                f"🤖 <b>{content.code}</b> đã chuyển sang bước AI Review. "
                "MeoBot đang tự động phân tích nội dung — kết quả sẽ hiện trong "
                "PR Admin, và bài sẽ tự chuyển bước theo kết quả."
            ),
            data={"code": content.code, "workflow_stage": updated.workflow_stage.value},
            entity_type="pr_content",
            entity_id=content.code,
        )


def build_pr_content_tools() -> list[ToolDefinition]:
    """Content tools, plus the AI-review handoff that only moves a stage.

    ``required_permission`` is the coarse gate the tool *catalogue* uses to
    decide what to show a role. It is not the authorization: the PR capability
    check runs inside every service call, and a tool visible to somebody who
    lacks the capability still fails there.
    """
    return [
        ToolDefinition(
            name="pr.content.create",
            description=(
                "Tạo một nội dung PR mới cho một thương hiệu. "
                "Mã nội dung (CNT-…) do hệ thống tự sinh, không cần người dùng nhập."
            ),
            handler=_create_handler,
            arguments_model=CreateContentArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SCRIPT_SUBMIT,
            read_only=False,
        ),
        ToolDefinition(
            name="pr.content.get",
            description="Xem chi tiết một nội dung PR: bước, phiên bản, kênh, task, AI review.",
            handler=_get_handler,
            arguments_model=ContentReferenceArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="pr.content.list",
            description="Liệt kê nội dung PR theo bước, thương hiệu hoặc người phụ trách.",
            handler=_list_handler,
            arguments_model=ListContentArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="pr.content.revise",
            description=(
                "Tạo phiên bản mới cho một nội dung PR. "
                "Chỉ sửa được khi nội dung chưa vào vòng duyệt."
            ),
            handler=_revise_handler,
            arguments_model=ReviseContentArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SCRIPT_SUBMIT,
            read_only=False,
        ),
        ToolDefinition(
            name="pr.content.transition",
            description=(
                "Chuyển nội dung PR sang bước tiếp theo do người dùng chủ động. "
                "Không dùng để duyệt: các bước duyệt do AI review và người duyệt quyết định."
            ),
            handler=_transition_handler,
            arguments_model=TransitionArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SCRIPT_SUBMIT,
            read_only=False,
        ),
        ToolDefinition(
            name="pr.ai_review.submit",
            description=(
                "Đưa một nội dung PR đang viết kịch bản sang bước AI Review. "
                "MeoBot sẽ tự động chạy AI review và chuyển bước theo kết quả."
            ),
            handler=_submit_ai_review_handler,
            arguments_model=ContentReferenceArgs,
            # HIGH, and therefore confirmed, although it only moves a stage.
            # Entering AI_REVIEW takes the draft out of EDITABLE_STAGES: the
            # author can no longer revise it, and getting back means asking a
            # reviewer to send it back. Step 1D.1 found that "AI review của bài
            # này thế nào?" is one plausible misroute away from this, so the
            # cost of a wrong route is a lock the author cannot undo - which is
            # what ``/confirm`` exists for.
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.SCRIPT_SUBMIT,
            read_only=False,
        ),
    ]


__all__ = ["MANUAL_TARGETS", "build_pr_content_tools"]
