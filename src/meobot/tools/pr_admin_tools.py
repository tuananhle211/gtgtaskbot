"""Telegram tools for channels, review grants and publication facts.

Three groups that share one property: each is a thin call into a service that
already holds the rule.

**Channels.** Overlap between two assignment periods is decided by
:class:`~meobot.application.pr_channel_service.PrChannelService`, using the
closed-interval rule in :mod:`meobot.domain.pr.assignments`. Nothing here
compares two dates. A tool that did would eventually disagree with the service
about whether a handover may share a day, and only one of them would be right.

**Capability grants.** These exist because Step 1C.1 deploys secure-by-default:
after migration 0016 nobody holds any review right, so without a way to grant
one through Telegram a fresh deployment has no path to its first approval. The
grant itself is gated on ``user.role.manage`` inside
:class:`~meobot.application.pr_capability_service.PrCapabilityService` - the
existing owner-only permission. **No capability guards itself**, so nobody can
bootstrap themselves into reviewing by being granted the thing they are
granting.

**Publications.** ``register_publication`` records that something went out. It
calls no platform API and there is no code path to one; "đã đăng TikTok rồi" is
a person telling MeoBot a fact, not asking it to publish.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.pr_channel_service import CreateChannelCommand, UpdateChannelCommand
from meobot.application.pr_publication_service import RegisterPublicationCommand
from meobot.application.pr_services import PrServices
from meobot.core.errors import ToolExecutionError
from meobot.core.time import utcnow
from meobot.domain.identity.models import Actor
from meobot.domain.permissions.matrix import Permission
from meobot.domain.policy.models import RiskLevel
from meobot.domain.pr.errors import PrValidationError
from meobot.domain.pr.models import PrChannelAssignmentRole, PrChannelCategory, PrChannelStatus
from meobot.tools.base import ToolContext, ToolDefinition, ToolResult
from meobot.tools.pr_errors import pr_errors
from meobot.tools.pr_presenters import (
    format_assignments,
    format_capability_holders,
    format_capability_list,
    format_channel_list,
    format_channel_summary,
)
from meobot.tools.pr_support import (
    person_names,
    pr_services,
    resolve_capability,
    resolve_channel,
    resolve_content,
    resolve_day,
    resolve_person,
)

# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


class ListChannelsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, max_length=200, description="Lọc theo tên hoặc mã.")
    limit: int = Field(default=20, ge=1, le=50)


class ChannelReferenceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field(min_length=1, max_length=200, description="Mã hoặc tên kênh.")


class CreateChannelArgs(BaseModel):
    """Arguments for ``pr.channel.create``. No ``code``: it is generated."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    platform: str = Field(min_length=1, max_length=200, description="Mã hoặc tên nền tảng.")
    category: str = Field(default="TEST", description="SCALE, OPTIMIZE, TEST, MAINTAIN, STOP.")
    brand: str | None = Field(default=None, max_length=200)
    tier: int | None = Field(default=None, ge=1, le=3)
    url: str | None = Field(default=None, max_length=500)


class UpdateChannelArgs(BaseModel):
    """Arguments for ``pr.channel.update``. A channel code never changes."""

    model_config = ConfigDict(extra="forbid")

    channel: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None)
    tier: int | None = Field(default=None, ge=1, le=3)
    status: str | None = Field(default=None, description="ACTIVE, INACTIVE hoặc ARCHIVED.")
    url: str | None = Field(default=None, max_length=500)


class AssignChannelArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field(min_length=1, max_length=200)
    person: str = Field(min_length=1, max_length=200)
    role: str = Field(
        default="CONTENT_OWNER",
        description="CHANNEL_OWNER, CONTENT_OWNER, PRODUCTION_OWNER, SEEDING_OWNER, "
        "ANALYTICS_OWNER hoặc APPROVER.",
    )
    since: str | None = Field(default=None, description="Từ ngày, ví dụ 'hôm nay'.")
    until: str | None = Field(default=None, description="Đến ngày, nếu có hạn.")


class CloseAssignmentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field(min_length=1, max_length=200)
    person: str = Field(min_length=1, max_length=200)
    role: str = Field(default="CONTENT_OWNER")
    until: str | None = Field(default=None, description="Ngày kết thúc. Mặc định là hôm nay.")


async def _channel_list_handler(context: ToolContext, arguments: ListChannelsArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        if arguments.query:
            channels = list(
                await services.queries.search_channels(
                    actor=context.actor, text=arguments.query, limit=arguments.limit
                )
            )
        else:
            channels = list(
                await services.queries.list_channels(actor=context.actor, limit=arguments.limit)
            )
        return ToolResult(
            success=True,
            message=format_channel_list(channels, empty="Chưa có kênh nào khớp."),
            data={"count": len(channels), "codes": [c.code for c in channels]},
            entity_type="pr_channel",
        )


async def _channel_get_handler(context: ToolContext, arguments: ChannelReferenceArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        channel = await resolve_channel(services, actor=context.actor, reference=arguments.channel)
        detail = await services.queries.get_channel(actor=context.actor, channel_id=channel.id)
        names = await person_names(services, [a.user_id for a in detail.assignments])
        body = format_channel_summary(channel)
        assignments = format_assignments(
            detail.assignments, names=names, empty="Chưa có phân công nào."
        )
        return ToolResult(
            success=True,
            message=f"{body}\n\n{assignments}",
            data={"code": channel.code, "assignment_count": len(detail.assignments)},
            entity_type="pr_channel",
            entity_id=channel.code,
        )


async def _channel_assignments_handler(
    context: ToolContext, arguments: ChannelReferenceArgs
) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        channel = await resolve_channel(services, actor=context.actor, reference=arguments.channel)
        assignments = list(
            await services.queries.list_channel_assignments(
                actor=context.actor, channel_id=channel.id
            )
        )
        names = await person_names(services, [a.user_id for a in assignments])
        return ToolResult(
            success=True,
            message=format_assignments(assignments, names=names, empty="Chưa có phân công nào."),
            data={"code": channel.code, "count": len(assignments)},
            entity_type="pr_channel",
            entity_id=channel.code,
        )


async def _channel_create_handler(context: ToolContext, arguments: CreateChannelArgs) -> ToolResult:
    with pr_errors():
        from sqlalchemy import or_, select

        from meobot.db.models.pr import PrBrand, PrPlatform

        services = pr_services(context)
        needle = f"%{arguments.platform.strip()}%"
        platforms = list(
            (
                await services.session.execute(
                    select(PrPlatform).where(
                        or_(PrPlatform.code.ilike(needle), PrPlatform.name.ilike(needle))
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(platforms) != 1:
            raise ToolExecutionError(
                f'Mình chưa xác định được nền tảng "{arguments.platform}".',
                details={"reason": "platform_not_resolved", "matches": len(platforms)},
            )

        brand_id = None
        if arguments.brand:
            brand_needle = f"%{arguments.brand.strip()}%"
            brands = list(
                (
                    await services.session.execute(
                        select(PrBrand).where(
                            or_(PrBrand.code.ilike(brand_needle), PrBrand.name.ilike(brand_needle))
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(brands) != 1:
                raise ToolExecutionError(
                    f'Mình chưa xác định được thương hiệu "{arguments.brand}".',
                    details={"reason": "brand_not_resolved", "matches": len(brands)},
                )
            brand_id = brands[0].id

        channel = await services.channels.create_channel(
            actor=context.actor,
            request_id=context.request_id,
            command=CreateChannelCommand(
                name=arguments.name,
                platform_id=platforms[0].id,
                category=_category(arguments.category),
                brand_id=brand_id,
                tier=arguments.tier,
                url=arguments.url,
            ),
        )
        return ToolResult(
            success=True,
            message=f"✅ Đã tạo kênh.\n\n{format_channel_summary(channel)}",
            data={"code": channel.code},
            entity_type="pr_channel",
            entity_id=channel.code,
        )


def _category(raw: str) -> PrChannelCategory:
    try:
        return PrChannelCategory(raw.strip().upper())
    except ValueError:
        return PrChannelCategory.TEST


async def _channel_update_handler(context: ToolContext, arguments: UpdateChannelArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        channel = await resolve_channel(services, actor=context.actor, reference=arguments.channel)
        status = None
        if arguments.status:
            try:
                status = PrChannelStatus(arguments.status.strip().upper())
            except ValueError:
                raise ToolExecutionError(
                    f'Mình chưa hiểu trạng thái "{arguments.status}".',
                    details={"reason": "unknown_channel_status"},
                ) from None
        updated = await services.channels.update_channel(
            actor=context.actor,
            request_id=context.request_id,
            command=UpdateChannelCommand(
                channel_id=channel.id,
                name=arguments.name,
                category=_category(arguments.category) if arguments.category else None,
                tier=arguments.tier,
                status=status,
                url=arguments.url,
            ),
        )
        return ToolResult(
            success=True,
            message=f"✅ Đã cập nhật kênh.\n\n{format_channel_summary(updated)}",
            data={"code": updated.code},
            entity_type="pr_channel",
            entity_id=updated.code,
        )


def _assignment_role(raw: str) -> PrChannelAssignmentRole:
    try:
        return PrChannelAssignmentRole(raw.strip().upper())
    except ValueError:
        return PrChannelAssignmentRole.CONTENT_OWNER


async def _channel_assign_handler(context: ToolContext, arguments: AssignChannelArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        channel = await resolve_channel(services, actor=context.actor, reference=arguments.channel)
        user_id = await resolve_person(services, name=arguments.person)
        since = (
            await resolve_day(services, text=arguments.since, context=context) or utcnow().date()
        )
        until = await resolve_day(services, text=arguments.until, context=context)

        await services.channels.assign_user(
            actor=context.actor,
            request_id=context.request_id,
            channel_id=channel.id,
            user_id=user_id,
            assignment_role=_assignment_role(arguments.role),
            effective_from=since,
            effective_to=until,
            allocation_percent=Decimal("100"),
        )
        who = (await person_names(services, [user_id])).get(str(user_id), arguments.person)
        return ToolResult(
            success=True,
            message=(
                f"👥 Đã phân công {who} phụ trách <b>{channel.code}</b> "
                f"({arguments.role.upper()}) từ {since.strftime('%d/%m/%Y')}."
            ),
            data={"code": channel.code, "user_id": str(user_id), "from": since.isoformat()},
            entity_type="pr_channel",
            entity_id=channel.code,
        )


async def _channel_close_handler(
    context: ToolContext, arguments: CloseAssignmentArgs
) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        channel = await resolve_channel(services, actor=context.actor, reference=arguments.channel)
        user_id = await resolve_person(services, name=arguments.person)
        role = _assignment_role(arguments.role)
        until: date = (
            await resolve_day(services, text=arguments.until, context=context) or utcnow().date()
        )

        open_rows = list(
            await services.queries.list_channel_assignments(
                actor=context.actor,
                channel_id=channel.id,
                user_id=user_id,
                assignment_role=role,
                open_only=True,
            )
        )
        if not open_rows:
            raise ToolExecutionError(
                "Người này hiện không có phân công đang mở với vai trò đó trên kênh này.",
                details={"reason": "no_open_assignment"},
            )
        await services.channels.close_assignment(
            actor=context.actor,
            request_id=context.request_id,
            assignment_id=open_rows[0].id,
            effective_to=until,
        )
        who = (await person_names(services, [user_id])).get(str(user_id), arguments.person)
        return ToolResult(
            success=True,
            message=(
                f"👥 Đã kết thúc phân công của {who} trên <b>{channel.code}</b> "
                f"từ {until.strftime('%d/%m/%Y')}."
            ),
            data={"code": channel.code, "user_id": str(user_id), "until": until.isoformat()},
            entity_type="pr_channel",
            entity_id=channel.code,
        )


# ---------------------------------------------------------------------------
# Capability administration
# ---------------------------------------------------------------------------


class CapabilityForUserArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person: str | None = Field(
        default=None, max_length=200, description="Tên người. Bỏ trống để xem quyền của chính mình."
    )


class CapabilityHoldersArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str = Field(
        min_length=1, max_length=100, description="Ví dụ: 'Head Review', 'duyệt trưởng nhóm'."
    )


class GrantArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person: str = Field(min_length=1, max_length=200)
    capability: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=500)


async def _capabilities_for_user_handler(
    context: ToolContext, arguments: CapabilityForUserArgs
) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        if arguments.person:
            user_id = await resolve_person(services, name=arguments.person)
            capabilities = await services.queries.capabilities_for_user(
                actor=context.actor, user_id=user_id
            )
            who = (await person_names(services, [user_id])).get(str(user_id), arguments.person)
        else:
            capabilities = await services.queries.capabilities_for_actor(actor=context.actor)
            who = "bạn"
        return ToolResult(
            success=True,
            message=format_capability_list(sorted(capabilities), who=who),
            data={"capabilities": [item.value for item in sorted(capabilities)]},
            entity_type="pr_capability",
        )


async def _capability_holders_handler(
    context: ToolContext, arguments: CapabilityHoldersArgs
) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        capability = resolve_capability(arguments.capability)
        grants = list(
            await services.queries.users_allowed_to(actor=context.actor, capability=capability)
        )
        names = await person_names(services, [grant.user_id for grant in grants])
        return ToolResult(
            success=True,
            message=format_capability_holders(
                [names.get(str(grant.user_id), "—") for grant in grants], capability=capability
            ),
            data={
                "capability": capability.value,
                "user_ids": [str(grant.user_id) for grant in grants],
            },
            entity_type="pr_capability",
        )


async def _grant_handler(context: ToolContext, arguments: GrantArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        capability = resolve_capability(arguments.capability)
        user_id = await resolve_person(services, name=arguments.person)
        # Step 1F.2.7. A chat message cannot express a scope - which content
        # classifications, which channels - so this grant is issued in its
        # **pre-1F.2.7 form**: the whole workspace, and only in force where the
        # holder's role already carries the permission. That is exactly what
        # this tool has always meant.
        #
        # The alternative was to let a sentence in a group chat hand somebody
        # unrestricted approval rights over everything regardless of their role,
        # through the least deliberate channel in the product. Scoped, additive
        # grants are the permissions screen's job, and the reply says so.
        await services.capabilities.grant(
            actor=context.actor,
            request_id=context.request_id,
            user_id=user_id,
            capability=capability,
            requires_role_baseline=True,
            note=arguments.note,
        )
        who = (await person_names(services, [user_id])).get(str(user_id), arguments.person)
        from meobot.tools.pr_presenters import capability_label

        return ToolResult(
            success=True,
            message=(
                f"🔑 Đã cấp quyền {capability_label(capability)} cho {who} "
                "(toàn bộ nội dung, và chỉ có hiệu lực nếu vai trò của họ đã có quyền tương ứng).\n"
                "Muốn giới hạn theo phân loại nội dung và theo kênh thì cấp ở trang Phân quyền."
            ),
            data={
                "capability": capability.value,
                "user_id": str(user_id),
                "requires_role_baseline": True,
            },
            entity_type="pr_capability",
        )


async def _revoke_handler(context: ToolContext, arguments: GrantArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        capability = resolve_capability(arguments.capability)
        user_id = await resolve_person(services, name=arguments.person)
        # Every active grant of that gate, not one of them. Since Step 1F.2.7 a
        # person may hold several over different scopes, and "thu hồi quyền
        # Duyệt Trưởng phòng của Hảo" means all of them - revoking only removes
        # authority, so answering the plain sentence plainly is safe here.
        withdrawn = await services.capabilities.revoke_all(
            actor=context.actor,
            request_id=context.request_id,
            user_id=user_id,
            capability=capability,
        )
        who = (await person_names(services, [user_id])).get(str(user_id), arguments.person)
        from meobot.tools.pr_presenters import capability_label

        return ToolResult(
            success=True,
            message=(
                f"🔑 Đã thu hồi quyền {capability_label(capability)} của {who}"
                + (f" ({len(withdrawn)} quyền)." if len(withdrawn) > 1 else ".")
            ),
            data={
                "capability": capability.value,
                "user_id": str(user_id),
                "revoked": len(withdrawn),
            },
            entity_type="pr_capability",
        )


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


class RegisterPublicationArgs(BaseModel):
    """Arguments for ``pr.publication.register``. No ``code``: it is generated."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=300)
    channel: str = Field(min_length=1, max_length=200)
    #: Step 1F.2.3f. **Which produced file went out**, by its human label -
    #: *"Video final 60s"*, *"TikTok cut 25s"*. Never an id: nobody types a UUID
    #: into a chat.
    #:
    #: Optional, and what happens when it is omitted is the whole of the design.
    #: With exactly one produced output there is nothing to choose and it is
    #: used; with several the tool **refuses and lists them**, because picking
    #: "the newest" would attribute a posting to a file nobody named, and a
    #: wrong answer in the distribution record is worse than one more question.
    output: str | None = Field(
        default=None,
        max_length=200,
        description="Tên sản phẩm đã đăng, ví dụ 'Video final 60s' hoặc 'TikTok cut 25s'.",
    )
    published_at: str | None = Field(default=None, description="Ngày đã đăng. Mặc định là hôm nay.")
    platform_post_id: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=500)


@dataclass(frozen=True, slots=True)
class _Output:
    """One produced file a publication may name, as this tool sees it."""

    label: str
    submission_id: uuid.UUID | None = None
    derivative_id: uuid.UUID | None = None


async def _resolve_output(
    services: PrServices, *, actor: Actor, content_id: uuid.UUID, wanted: str | None
) -> _Output:
    """Which produced file this publication is about.

    Matched on the **label**, case-insensitively and after trimming, against the
    masters and the derivatives together - the same one list the web panel's
    picker is made of. An unlabelled master answers to *"Bản nộp #1"*, which is
    what the panel calls it too.

    Raises:
        PrValidationError: Nothing has been produced yet, the name matches
            nothing, the name matches more than one, or several outputs exist
            and none was named. Every one of them lists what there is to choose
            from, because a refusal a person cannot act on is not much better
            than a wrong answer.
    """
    submissions = await services.queries.content_submissions(actor=actor, content_id=content_id)
    derivatives = await services.queries.content_derivatives(actor=actor, content_id=content_id)
    options = [
        _Output(label=row.label or f"Bản nộp #{row.submission_no}", submission_id=row.id)
        for row in submissions
    ] + [_Output(label=row.label, derivative_id=row.id) for row in derivatives]

    if not options:
        raise PrValidationError(
            "This content has no produced output to publish",
            details={"field": "output", "reason": "no_output", "content_id": str(content_id)},
        )

    names = [option.label for option in options]
    if wanted is None:
        if len(options) == 1:
            return options[0]
        raise PrValidationError(
            "Say which produced output was published",
            details={"field": "output", "reason": "output_required", "options": names},
        )

    needle = wanted.strip().casefold()
    matched = [option for option in options if option.label.casefold() == needle]
    if not matched:
        matched = [option for option in options if needle in option.label.casefold()]
    if not matched:
        raise PrValidationError(
            "No produced output of this content is called that",
            details={"field": "output", "reason": "unknown_output", "options": names},
        )
    if len(matched) > 1:
        raise PrValidationError(
            "That name matches more than one produced output",
            details={
                "field": "output",
                "reason": "ambiguous_output",
                "options": [option.label for option in matched],
            },
        )
    return matched[0]


async def _register_publication_handler(
    context: ToolContext, arguments: RegisterPublicationArgs
) -> ToolResult:
    """Record that something went out. Never publishes anything."""
    with pr_errors():
        services = pr_services(context)
        content = await resolve_content(services, actor=context.actor, reference=arguments.content)
        channel = await resolve_channel(services, actor=context.actor, reference=arguments.channel)

        day = await resolve_day(services, text=arguments.published_at, context=context)
        from meobot.tools.pr_support import at_end_of_day, timezone_of

        published_at = at_end_of_day(day, tz=timezone_of(context)) if day is not None else utcnow()

        # Step 1F.2.3f: a publication names the produced file that went out.
        # Resolved by label here because this is a chat surface - see
        # ``_resolve_output`` on why an omitted name is sometimes fine and
        # sometimes a question.
        output = await _resolve_output(
            services, actor=context.actor, content_id=content.id, wanted=arguments.output
        )

        outcome = await services.publications.register_publication(
            actor=context.actor,
            request_id=context.request_id,
            command=RegisterPublicationCommand(
                content_id=content.id,
                channel_id=channel.id,
                published_at=published_at,
                production_submission_id=output.submission_id,
                derivative_id=output.derivative_id,
                platform_post_id=arguments.platform_post_id,
                url=arguments.url,
            ),
        )
        tail = f"\nNội dung đã chuyển sang: {outcome.new_stage.value}" if outcome.new_stage else ""
        return ToolResult(
            success=True,
            message=(
                f"📣 Đã ghi nhận <b>{content.code}</b> đăng trên <b>{channel.code}</b> "
                f"bằng <b>{output.label}</b> ({outcome.publication.code}).{tail}"
            ),
            data={
                "content_code": content.code,
                "channel_code": channel.code,
                "publication_code": outcome.publication.code,
                "output_label": output.label,
                "first_publication": outcome.was_first,
                "workflow_stage": outcome.new_stage.value if outcome.new_stage else None,
            },
            entity_type="pr_publication",
            entity_id=outcome.publication.code,
        )


def build_pr_admin_tools() -> list[ToolDefinition]:
    """Channel, capability and publication tools.

    Risk levels do the confirmation work: revoking a grant and closing an
    assignment are ``HIGH``, so the existing policy engine demands ``/confirm``
    before either runs. No PR-specific confirmation flow was added.
    """
    return [
        ToolDefinition(
            name="pr.channel.list",
            description="Liệt kê các kênh PR, có thể lọc theo tên hoặc nền tảng.",
            handler=_channel_list_handler,
            arguments_model=ListChannelsArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="pr.channel.get",
            description="Xem một kênh PR kèm danh sách phân công.",
            handler=_channel_get_handler,
            arguments_model=ChannelReferenceArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="pr.channel.assignments",
            description="Xem lịch sử phân công của một kênh PR.",
            handler=_channel_assignments_handler,
            arguments_model=ChannelReferenceArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="pr.channel.create",
            description="Tạo một kênh PR mới. Mã kênh (CH-…) do hệ thống tự sinh.",
            handler=_channel_create_handler,
            arguments_model=CreateChannelArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SETTINGS_WRITE,
            read_only=False,
        ),
        ToolDefinition(
            name="pr.channel.update",
            description="Cập nhật thông tin một kênh PR. Không đổi được mã kênh.",
            handler=_channel_update_handler,
            arguments_model=UpdateChannelArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SETTINGS_WRITE,
            read_only=False,
        ),
        ToolDefinition(
            name="pr.channel.assign",
            description="Phân công một người phụ trách một kênh PR từ một mốc thời gian.",
            handler=_channel_assign_handler,
            arguments_model=AssignChannelArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SETTINGS_WRITE,
            read_only=False,
        ),
        ToolDefinition(
            name="pr.channel.close_assignment",
            description="Kết thúc phân công của một người trên một kênh PR.",
            handler=_channel_close_handler,
            arguments_model=CloseAssignmentArgs,
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.SETTINGS_WRITE,
            read_only=False,
        ),
        ToolDefinition(
            name="pr.capability.list_for_user",
            description="Xem một người đang có những quyền PR nào.",
            handler=_capabilities_for_user_handler,
            arguments_model=CapabilityForUserArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="pr.capability.users_for",
            description="Xem ai đang có một quyền duyệt PR, ví dụ Head Review.",
            handler=_capability_holders_handler,
            arguments_model=CapabilityHoldersArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="pr.capability.grant",
            description=(
                "Cấp quyền duyệt PR cho một người: Duyệt Trưởng nhóm, Duyệt Trưởng phòng "
                "hoặc Duyệt nội bộ."
            ),
            handler=_grant_handler,
            arguments_model=GrantArgs,
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.USER_ROLE_MANAGE,
            read_only=False,
        ),
        ToolDefinition(
            name="pr.capability.revoke",
            description="Thu hồi một quyền duyệt PR của một người.",
            handler=_revoke_handler,
            arguments_model=GrantArgs,
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.USER_ROLE_MANAGE,
            # NOT ``destructive``: in this repository that flag means
            # "deletes data", and ``PolicyEngine`` refuses any destructive tool
            # outright (``deny_destructive=True``). Rejecting cancels content,
            # revoking closes a grant and cancelling a task is a status change -
            # none of them removes a row. ``RiskLevel.HIGH`` is what produces
            # the ``/confirm`` round trip, and that is the protection these need.
            read_only=False,
        ),
        ToolDefinition(
            name="pr.publication.register",
            description=(
                "Ghi nhận một nội dung PR đã được đăng lên một kênh, kèm tên sản "
                "phẩm đã đăng. Chỉ ghi nhận, không tự đăng bài lên mạng xã hội."
            ),
            handler=_register_publication_handler,
            arguments_model=RegisterPublicationArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.PUBLISH_SOCIAL,
            read_only=False,
        ),
    ]


__all__ = ["build_pr_admin_tools"]
