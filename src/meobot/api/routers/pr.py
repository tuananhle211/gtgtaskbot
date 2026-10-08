"""HTTP surface for the PR web admin.

Step 1E. Every route in this module is a translator. It parses a body, calls one
``Pr*`` service method, and shapes the result into a response model. That is the
whole job, and the constraint that makes the web client safe to add: Telegram and
the browser are siblings over one set of rules, not two implementations of the
same rules.

```
Browser ─→ Next.js ─→ these routes ─→ Pr* services ─→ PostgreSQL
                                          ↑
Telegram tools ───────────────────────────┘
```

What no route here does
-----------------------

* **decide whether a transition is legal** - the matrices in
  ``meobot.domain.pr.workflow`` do, inside ``PrContentWorkflowService``. No route
  writes ``workflow_stage``, and none accepts a trigger: a browser request is
  always ``MANUAL``, so an edge reserved for an approval cannot be walked from
  one;
* **decide whether somebody is authorized** - ``PrCapabilityService`` does, inside
  each service. There is no permission check in this file, on purpose: a check
  here would be a second authority, and two authorities eventually disagree;
* **allocate a code** - ``PrCodeService`` does. No request body accepts one;
* **run an AI review** - ``submit-ai-review`` records a verdict produced
  elsewhere, with mandatory provenance. Nothing in this API calls a model;
* **name its own actor** - the reviewer, the author and the granter are always
  the session. Bodies that could carry an identity (approvals, revisions) do not
  have the field.

Reviewer identity
-----------------

``RecordApprovalCommand`` needs a ``reviewer_user_id``, and it is filled from
``actor.user_id`` - never from the request. Step 1F.2.2 removed the rule that
made that load-bearing for authorization, and it stays anyway for a plainer
reason: ``pr_approval_events`` is the record of **who** decided, and a caller who
could name somebody else would be writing a signature in another person's name.

An actor with no ``users`` row (the milestone-1 bootstrap owner) therefore cannot
approve through the web at all. :func:`_reviewer_id` refuses rather than
substituting somebody, and the web dependency cannot produce such an actor
anyway - a session requires a row.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from meobot.api.deps import (
    CurrentActorDep,
    OptionalActorDep,
    PrServicesDep,
    RequestIdDep,
    SessionDep,
)
from meobot.api.schemas.pr import (
    AccountChoicesResponse,
    AiReviewResponse,
    AiReviewRunResponse,
    AiReviewStateResponse,
    ApprovableSelectionResponse,
    ApprovalDecisionRequest,
    ApprovalEventResponse,
    ArchiveCandidatesResponse,
    AssignChannelRequest,
    AssignProducerRequest,
    AssignTaskRequest,
    AvailableActionResponse,
    AvailableActionsResponse,
    BrandResponse,
    BulkApproveRequest,
    BulkApproveResponse,
    BulkArchiveRequest,
    BulkArchiveResponse,
    CapabilityGrantResponse,
    ChannelConnectionResponse,
    ChannelConnectionStateResponse,
    ChannelDetailResponse,
    ChannelMetricsResponse,
    ChannelResponse,
    CloseAssignmentRequest,
    ConnectionAuthorizationResponse,
    ConnectionOutcomeResponse,
    ContentBoardResponse,
    ContentCommentPageResponse,
    ContentCommentRequest,
    ContentCommentResponse,
    ContentDerivativeRequest,
    ContentDerivativeResponse,
    ContentDestinationRequest,
    ContentDestinationResponse,
    ContentDetailResponse,
    ContentResourceRequest,
    ContentResourceResponse,
    ContentSummaryResponse,
    ContentVersionResponse,
    CorrectProductionOutputRequest,
    CreateChannelRequest,
    CreateContentRequest,
    CreatePlatformRequest,
    CreateTaskRequest,
    DashboardResponse,
    DeleteContentRequest,
    FinishConnectionRequest,
    GrantCapabilityRequest,
    GrantScopeBody,
    PersonResponse,
    PlatformResponse,
    PolicyPackRefResponse,
    PolicyRuleCitationResponse,
    ProductionStateResponse,
    ProductionSubmissionResponse,
    PublicationResponse,
    RecordChannelMetricsRequest,
    RegisterPublicationRequest,
    ReversePublicationResponse,
    ReviewContextResponse,
    ReviseContentRequest,
    RevokeCapabilityRequest,
    SelectAccountRequest,
    StageCountResponse,
    SubmitAiReviewRequest,
    SubmitProductionRequest,
    TaskAssignmentResponse,
    TaskDetailResponse,
    TaskStatusRequest,
    TaskSummaryResponse,
    TikTokOverviewResponse,
    TransitionEventResponse,
    TransitionRequest,
    UpdateChannelRequest,
    UpdateContentCommentRequest,
    UpdateContentDerivativeRequest,
    UpdateContentDestinationRequest,
    UpdateContentPriorityRequest,
    UpdateContentResourceRequest,
    UpdateContentTypeRequest,
    UpdatePublicationRequest,
    UpdateTargetRequest,
)
from meobot.application.pr_ai_review_service import RecordAiReviewCommand
from meobot.application.pr_approval_service import RecordApprovalCommand
from meobot.application.pr_bulk_approval_service import BulkApproveCommand
from meobot.application.pr_bulk_archive_service import BulkArchiveCommand
from meobot.application.pr_channel_metrics_service import (
    DEFAULT_SNAPSHOT_PAGE,
    MAX_SNAPSHOT_PAGE,
    RecordChannelMetricsCommand,
)
from meobot.application.pr_channel_providers import (
    PrConnectorNotConfiguredError,
)
from meobot.application.pr_channel_providers import (
    supports as connector_supports,
)
from meobot.application.pr_channel_service import CreateChannelCommand, UpdateChannelCommand
from meobot.application.pr_content_asset_service import (
    AddDerivativeCommand,
    AddDestinationCommand,
    UpdateDerivativeCommand,
    UpdateDestinationCommand,
)
from meobot.application.pr_content_comment_service import AddCommentCommand, CommentView
from meobot.application.pr_content_query import ContentQuery
from meobot.application.pr_content_resource_service import (
    AddContentResourceCommand,
    UpdateContentResourceCommand,
)
from meobot.application.pr_content_resource_support import (
    ContentResourceSpec,
    resource_refusal_index,
)
from meobot.application.pr_content_service import (
    ContentTargetSpec,
    CreateContentCommand,
    ReviseContentCommand,
)
from meobot.application.pr_platform_service import CreatePlatformCommand
from meobot.application.pr_production_service import (
    CorrectSubmissionCommand,
    SubmitProductionCommand,
)
from meobot.application.pr_publication_service import (
    RegisterPublicationCommand,
    UpdatePublicationCommand,
)
from meobot.application.pr_support import record_pr_event
from meobot.application.pr_task_service import CreateTaskCommand
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.db.models.pr import PrContentItem, PrContentTarget, PrPlatform
from meobot.db.models.pr_content_comment import PrContentComment
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.channel_connections import PrChannelSyncTrigger
from meobot.domain.pr.channel_metrics import PrChannelPlatform, platform_from_code, stale_days
from meobot.domain.pr.comments import DEFAULT_COMMENT_PAGE, MAX_COMMENT_PAGE
from meobot.domain.pr.content_views import (
    PERIOD_CURRENT,
    UNCLASSIFIED_CONTENT_TYPE,
    PrContentBoardView,
    PrContentDateField,
    PrContentGroup,
    PrContentLane,
    PrContentViewScope,
)
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
    PrWorkflowTransitionError,
)
from meobot.domain.pr.grants import GrantScope, PrGrantScopeMode
from meobot.domain.pr.labels import channel_platform_label
from meobot.domain.pr.models import (
    ACTIVE_RUN_STATUSES,
    PrAiReviewResult,
    PrAiReviewTrigger,
    PrAiReviewType,
    PrApprovalDecision,
    PrApprovalStage,
    PrChannelAssignmentRole,
    PrChannelCategory,
    PrChannelStatus,
    PrContentDerivativeType,
    PrContentResourceType,
    PrContentType,
    PrDistributionMode,
    PrEntityStatus,
    PrPriority,
    PrProductionArtifactType,
    PrTaskAssignmentRole,
    PrTaskStatus,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.workflow import STAGE_APPROVAL_GATES
from meobot.integrations.tiktok.constants import RECENT_VIDEO_PAGE, VIDEO_PAGE_SIZE

router = APIRouter(prefix="/api/pr", tags=["pr"])

#: The three OAuth callbacks. Same prefix, same file, a second router object,
#: because ``main.py`` mounts ``router`` behind the PR unit gate and a callback
#: arrives by redirect from another site with no session to gate on - it
#: authenticates from its signed ``state`` instead (see
#: :func:`~meobot.api.deps.get_optional_web_actor`). Their bodies are untouched.
oauth_callback_router = APIRouter(prefix="/api/pr", tags=["pr"])

#: Applied to every list route. A browser asking for everything is usually a
#: page that will render everything, and neither the database nor the person
#: benefits from ten thousand rows.
_MAX_LIMIT = 200

#: How many cards the dashboard shows per section. Enough to be useful, few
#: enough that the landing page stays one screen.
_DASHBOARD_LIMIT = 10

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "No such record, or not visible to you."}
}
_WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "No usable session."},
    403: {"description": "Refused by the PR services - role or capability."},
    409: {"description": "Refused by a workflow, version or gate-prerequisite rule."},
    422: {"description": "The request itself is not valid."},
    **_NOT_FOUND,
}


# --- Parsing enums out of request bodies ------------------------------------
# One helper, so thirty routes cannot grow thirty slightly different failures.


def _enum[T](enum_type: type[T], raw: str, *, field: str) -> T:
    """Parse a string into a domain enum, or raise a 422 naming the options.

    Deliberately a ``PrValidationError`` rather than letting Pydantic coerce it:
    the message lists the accepted values, which is what somebody debugging a
    request needs. It also keeps the vocabulary in the domain enums rather than
    duplicated into the API schema, so adding a stage does not need an edit here.
    """
    try:
        return enum_type(raw)  # type: ignore[call-arg]
    except ValueError as exc:
        allowed = sorted(str(member.value) for member in enum_type)  # type: ignore[attr-defined]
        raise PrValidationError(
            f"Giá trị không hợp lệ cho '{field}'.",
            details={"field": field, "value": raw, "allowed": allowed},
        ) from exc


def _month(raw: str, *, field: str) -> date:
    """Parse ``YYYY-MM`` into that month's first day, or raise a 422.

    The wire form is the month a person reads - *"Kỳ 09/2026"* - and the query
    wants a date, so the conversion happens once here rather than in every
    caller. A day component is deliberately not accepted: the parameter selects a
    month, and taking ``2026-09-17`` would invite the belief that it selected a
    week.
    """
    try:
        year, month = raw.split("-")
        return date(int(year), int(month), 1)
    except (ValueError, TypeError) as exc:
        raise PrValidationError(
            f"Giá trị không hợp lệ cho '{field}'. Định dạng đúng là YYYY-MM.",
            details={"field": field, "value": raw, "expected": "YYYY-MM"},
        ) from exc


def _grant_scope(body: GrantScopeBody) -> GrantScope:
    """Turn a request body's scope into the domain object that decides with it.

    The one place a wire scope becomes a :class:`GrantScope`. Every code is
    parsed through :func:`_enum`, so an unknown content type is a 422 naming the
    six rather than a grant that silently covers nothing.
    """
    return GrantScope(
        content_type_scope=_enum(
            PrGrantScopeMode, body.content_type_scope, field="content_type_scope"
        ),
        content_types=frozenset(
            _enum(PrContentType, value, field="content_types") for value in body.content_types
        ),
        include_unclassified_content=body.include_unclassified_content,
        channel_scope=_enum(PrGrantScopeMode, body.channel_scope, field="channel_scope"),
        channel_ids=frozenset(body.channel_ids),
        include_unassigned_channel=body.include_unassigned_channel,
    )


def _reviewer_id(actor: Actor) -> uuid.UUID:
    """The session's ``users.id``, or a refusal.

    Never falls back to another id. ``pr_approval_events`` is the record of who
    decided, and an approval attributed to somebody who did not make it is worse
    than a refused request - the more so now that one person may legitimately
    appear at both gates, which makes the actor on each row the only thing
    distinguishing "she approved twice" from "he approved, then she did".
    """
    if actor.user_id is None:
        raise PrPermissionDeniedError(
            "Tài khoản này chưa được liên kết với một người dùng, không thể thực hiện."
        )
    return actor.user_id


def _capped(limit: int) -> int:
    return min(limit, _MAX_LIMIT)


# --- The content filter, parsed once ----------------------------------------


def content_query(
    scope: Annotated[str | None, Query(description="MY_ACTIONS | MY_CONTENT | TEAM | ALL")] = None,
    group: Annotated[
        str | None,
        Query(
            description=(
                "PREPARATION | EDITORIAL_REVIEW | PRODUCTION | COMPLETED | CANCELLED. "
                "Narrows before the page is cut, and intersects with 'stage'."
            )
        ),
    ] = None,
    lane: Annotated[
        str | None,
        Query(
            description=(
                "One board column: IDEA | BRIEFING | SCRIPTING | AI_REVIEW | "
                "TEAM_LEAD_REVIEW | HEAD_REVIEW | WAITING_FOR_PRODUCER | "
                "READY_FOR_PRODUCTION | IN_PRODUCTION | IN_INTERNAL_REVIEW | "
                "READY_TO_PUBLISH | PUBLISHED | ARCHIVED | CANCELLED. "
                "Narrows before the page is cut, and intersects with 'group' "
                "and 'stage'."
            )
        ),
    ] = None,
    view: Annotated[
        str | None,
        Query(
            description=(
                "ACTIVE | ARCHIVE. Which board: the operational one, which never "
                "holds or counts ARCHIVED content, or the archive alone. The board "
                "route defaults to ACTIVE; the flat list applies no view."
            )
        ),
    ] = None,
    stage: Annotated[str | None, Query()] = None,
    brand_id: Annotated[uuid.UUID | None, Query()] = None,
    owner_user_id: Annotated[uuid.UUID | None, Query()] = None,
    responsible_user_id: Annotated[uuid.UUID | None, Query()] = None,
    channel_id: Annotated[uuid.UUID | None, Query()] = None,
    platform_id: Annotated[uuid.UUID | None, Query()] = None,
    date_field: Annotated[
        str | None, Query(description="CREATED_AT | PLANNED_PUBLISH_AT | UPDATED_AT")
    ] = None,
    period: Annotated[
        str | None,
        Query(
            description=(
                "Kỳ báo cáo - an optional reporting month, YYYY-MM, or CURRENT for "
                "the current business month (the response echoes which). Each lane "
                "is read against its own business instant: stage entry, production "
                "start, actual publication, archive. Omitted - the board's default - "
                "means no month: every active row, no reporting expression built. "
                "Never mutates anything."
            )
        ),
    ] = None,
    date_from: Annotated[date | None, Query(description="Inclusive, business-timezone day")] = None,
    date_to: Annotated[date | None, Query(description="Inclusive, business-timezone day")] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    priority: Annotated[
        str | None,
        Query(description="CRITICAL | URGENT | HIGH | NORMAL. Narrows before the page is cut."),
    ] = None,
    content_type: Annotated[
        str | None,
        Query(
            description=(
                "One of the six content types, or UNCLASSIFIED for content that "
                "predates the field. Narrows before the page is cut."
            )
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=0,
            le=_MAX_LIMIT,
            description=(
                "0 asks for the figures alone - the counts with no rows, which "
                "is how the board fetches its tab and lane totals without being "
                "handed cards it would throw away."
            ),
        ),
    ] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ContentQuery:
    """Every content filter, parsed in one place.

    A dependency rather than thirteen parameters repeated on each route that
    filters, which is the duplication Step 1F.2.2 was warned about: two routes
    with their own copies of "how is ``date_from`` read" is two routes that
    eventually disagree about it, and the disagreement shows up as a count that
    does not match its list.

    ``group`` is a filter like the rest of them, which is the correction in Step
    1F.2.3c1: it reaches
    :func:`~meobot.application.pr_content_query.content_conditions` and is part
    of the ``WHERE`` the page is cut from, which is the whole of what makes a tab
    reading "Chuẩn bị 5" show five cards on one page. Which stages a group covers
    is not written here: this parses a string into
    :class:`~meobot.domain.pr.content_views.PrContentGroup` and stops, because a
    second copy of that table in the router is how the two would drift.

    Parses only. Which rows the filter selects is
    :func:`~meobot.application.pr_content_query.content_conditions`, and what a
    missing ``scope`` becomes is
    :func:`~meobot.domain.pr.content_views.default_scope` - deliberately *not*
    defaulted here, because the answer depends on the actor's capabilities and
    this function has no actor.
    """
    return ContentQuery(
        scope=_enum(PrContentViewScope, scope, field="scope") if scope else None,
        group=_enum(PrContentGroup, group, field="group") if group else None,
        # Step 1F.2.3c2. Parsed and passed on, exactly like the group: which rows
        # a lane selects is ``lane_conditions``, and a second copy of the
        # lane/stage table in the router is how the two would drift.
        lane=_enum(PrContentLane, lane, field="lane") if lane else None,
        stage=_enum(PrWorkflowStage, stage, field="stage") if stage else None,
        brand_id=brand_id,
        owner_user_id=owner_user_id,
        responsible_user_id=responsible_user_id,
        channel_id=channel_id,
        platform_id=platform_id,
        date_field=(
            _enum(PrContentDateField, date_field, field="date_field")
            if date_field
            else PrContentDateField.CREATED_AT
        ),
        date_from=date_from,
        date_to=date_to,
        # Step 1F.2.3f.6. Parsed to the month's first day and no further: which
        # rows the month selects, per lane, is ``reporting_instant``, and a
        # second copy of that classification here is how the two would drift.
        # ``CURRENT`` is a flag rather than a month, resolved by the service
        # against the business calendar it already reads ``today`` from.
        period_month=(
            _month(period, field="period") if period and period != PERIOD_CURRENT else None
        ),
        current_period=period == PERIOD_CURRENT,
        # Step 1F.2.3f.6c. Parsed and passed on; what a view excludes is
        # ``view_conditions``, and the board route decides the default.
        view=_enum(PrContentBoardView, view, field="view") if view else None,
        search=search,
        priority=_enum(PrPriority, priority, field="priority") if priority else None,
        # ``UNCLASSIFIED`` is a filter value, not a content type - see
        # ``ContentQuery.unclassified_content_type``. Parsed here so the enum
        # stays the six real formats.
        content_type=(
            _enum(PrContentType, content_type, field="content_type")
            if content_type and content_type != UNCLASSIFIED_CONTENT_TYPE
            else None
        ),
        unclassified_content_type=content_type == UNCLASSIFIED_CONTENT_TYPE,
        limit=_capped(limit),
        offset=offset,
    )


ContentQueryDep = Annotated[ContentQuery, Depends(content_query)]


# --- Dashboard --------------------------------------------------------------


@router.get("/dashboard", response_model=DashboardResponse, summary="Landing page figures")
async def dashboard(actor: CurrentActorDep, services: PrServicesDep) -> DashboardResponse:
    """Counts per stage, what is waiting for *you*, and what is late.

    "Waiting for you" is what this actor may **actually decide**: the grants that
    authorise them, each applied over its own classifications and channels - see
    ``PrQueryService.content_awaiting``. Somebody with no review grant sees an
    empty queue, including an OWNER: holding every ``Permission`` is not holding
    a grant, and showing them a queue they cannot act on would be a lie about
    their authority. Since Step 1F.2.7a the same is true one level down, of an
    item their grant does not cover.

    The list and the board's *Cần tôi xử lý* counters are therefore the same set,
    because both are ``approvable_by`` over the same grants.

    Every number is counted live. Nothing here is cached or estimated.

    Step 1F.2.2 replaced fourteen ``SELECT``s - one per stage, each fetching up to
    200 rows to call ``len`` on - with a single grouped count inside
    ``PrQueryService.content_page``. The figures are the same and the page is one
    query instead of fourteen; the old shape also silently capped every count at
    ``_MAX_LIMIT``, so a stage holding 300 items reported 200.
    """
    held = await services.capabilities.capabilities_for_actor(actor)
    # Step 1F.2.7a. No gate list is computed here any more: the queue is scoped,
    # and the only thing that knows a person's scopes is the capability service.
    # ``content_awaiting`` asks it, so this route cannot hand it a wider set than
    # the write path would honour.
    awaiting = await services.queries.content_awaiting(actor=actor, limit=_DASHBOARD_LIMIT)
    # ``ALL``: the landing page's figures are the department's, not this person's
    # queue - "what is waiting for you" is the section below them.
    overall = await services.queries.content_page(
        actor=actor,
        query=ContentQuery(scope=PrContentViewScope.ALL, limit=_DASHBOARD_LIMIT),
    )
    return DashboardResponse(
        stage_counts=[
            StageCountResponse(stage=stage.value, count=count)
            for stage, count in overall.stage_counts.items()
        ],
        awaiting_my_review=[ContentSummaryResponse.from_row(row) for row in awaiting],
        overdue_tasks=[
            TaskSummaryResponse.from_row(row)
            for row in await services.queries.list_overdue_tasks(
                actor=actor, limit=_DASHBOARD_LIMIT
            )
        ],
        my_capabilities=sorted(capability.value for capability in held),
        # The same page the counts came from - newest first, ``_DASHBOARD_LIMIT``
        # of them - rather than a second query asking the same thing.
        recent_content=[ContentSummaryResponse.from_row(row) for row in overall.items],
    )


# --- People -----------------------------------------------------------------


@router.get("/people", response_model=list[PersonResponse], summary="Active users")
async def list_people(
    actor: CurrentActorDep,
    session: SessionDep,
    services: PrServicesDep,
) -> list[PersonResponse]:
    """Active users, for the owner/assignee pickers.

    A picker rather than a text box, because every PR write takes a ``user_id``.
    That is the same rule the Telegram side enforces by asking a question when a
    name is ambiguous - here the ambiguity cannot arise, since the person clicks
    a row and the id travels.

    Guarded by the read permission through ``PrQueryService`` before the query
    runs, so this is not an unauthenticated staff directory.
    """
    await services.queries.list_contents(actor=actor, limit=1)
    rows = (
        (await session.execute(select(User).where(User.active.is_(True)).order_by(User.full_name)))
        .scalars()
        .all()
    )
    return [
        PersonResponse(user_id=row.id, full_name=row.full_name, role=row.role.value) for row in rows
    ]


@router.get("/brands", response_model=list[BrandResponse], summary="Brands you may choose")
async def list_brands(actor: CurrentActorDep, services: PrServicesDep) -> list[BrandResponse]:
    """Active brands, by name, for the create form's picker.

    Step 1E.2.1. Before it, creating content asked a person to type a brand
    UUID - which is not a thing anybody knows, and which made the one screen
    that starts every piece of work unusable without a database client.

    Active only, and that is ``PrQueryService.list_brands``' decision rather
    than this route's: a retired brand is retired, and offering it in a picker
    would undo that one new content item at a time. Guarded by the same read
    permission as every other list here, so this is not an open directory of
    who the department works for.
    """
    return [BrandResponse.from_row(row) for row in await services.queries.list_brands(actor=actor)]


# --- Content ----------------------------------------------------------------


@router.get("/contents", response_model=list[ContentSummaryResponse], summary="List content")
async def list_contents(
    actor: CurrentActorDep,
    services: PrServicesDep,
    query: ContentQueryDep,
) -> list[ContentSummaryResponse]:
    """A flat list of content, filtered.

    Every filter in :func:`content_query` applies here, and the response is a
    bare array - the shape this route has always had, kept because clients and
    scripts read it. A screen wanting the *counts* for the same filter asks
    ``/contents/board``, which answers both from one query rather than making
    this route grow a wrapper object underneath existing callers.

    ``search`` short-circuits: with a search string this delegates to
    ``PrQueryService.search_contents`` and the structured filters stand aside,
    which is the behaviour from Step 1E and is right for its purpose - somebody
    typing a code wants *that item*, not the intersection of their filters with
    it. The board route composes search with everything instead, because there a
    filter bar is visibly switched on and quietly ignoring it would be worse.
    """
    if query.search:
        rows: Sequence[object] = await services.queries.search_contents(
            actor=actor, text=query.search, limit=query.limit
        )
    else:
        page = await services.queries.content_page(actor=actor, query=query, with_counts=False)
        rows = list(page.items)
    return [ContentSummaryResponse.from_row(row) for row in rows]  # type: ignore[arg-type]


# Declared **before** ``/contents/{content_id}``. FastAPI matches in declaration
# order, and "board" is not a UUID, so the other way round this path would be
# parsed as a content id and refused with a 422 about the wrong thing.
@router.get(
    "/contents/board",
    response_model=ContentBoardResponse,
    summary="Filtered content with its counts",
)
async def content_board(
    actor: CurrentActorDep,
    services: PrServicesDep,
    query: ContentQueryDep,
) -> ContentBoardResponse:
    """The content workspace: one page of rows, and the counts describing them.

    Both from one filter, which is the point. Before Step 1F.2.2 this screen read
    its cards here and its figures from ``/dashboard``, and the two shared no
    notion of a filter - so narrowing to seven items left "Chờ duyệt: 120" on
    screen above them.

    ``scope`` may be omitted, in which case the server applies this actor's
    default and says which one it applied. That default is decided from the
    capabilities they hold, not from their role and not by the browser - see
    :func:`~meobot.domain.pr.content_views.default_scope`.

    ``group`` may be omitted too, and then means every group - there is no
    default to echo, because the server never picks one. What it does *not* mean
    is "narrow afterwards": with a group the rows and ``total`` are that group's,
    while ``stage_counts`` and ``production_state_counts`` stay over the filters
    without it, so the tab strip still says how much work each of the other four
    holds. Step 1F.2.3c1.

    ``lane`` is the same correction one column deeper, and Step 1F.2.3c2. A
    group is four independent queues stacked into one response, so paging the
    *group* let the 155 items waiting for a producer take every slot on the first
    pages and left the three items actually in production on page three - under a
    lane header that correctly read 3. With a ``lane`` the rows are that column's
    and ``total`` is that column's whole queue, so a board is drawn as one
    request per visible lane and each of them pages on its own.

    Two request shapes, then, and a client uses both:

    * ``?group=PRODUCTION&limit=0`` - the figures with no rows. ``total`` is the
      group's, and the two count tables label the tabs and the lane headers;
    * ``?group=PRODUCTION&lane=IN_PRODUCTION&limit=20&offset=0`` - one column's
      page. ``total`` is that lane's, and the count tables are empty because the
      request did not ask about the board.

    An impossible pair - ``group=PREPARATION`` with ``lane=IN_PRODUCTION`` - is
    an empty intersection and not a 422, which is the same answer ``stage``
    already gives to the same question. Nothing silently reinterprets one of the
    two into agreement with the other.

    None of this is an access rule. The read permission gates the whole route as
    it always has; a scope decides what is *shown first*, and every actor who
    reaches this endpoint may ask for ``ALL``.
    """
    # Step 1F.2.3f.6c. A board is the operational board unless the archive was
    # asked for. Decided here and not in the shared dependency, because the flat
    # list below has no view and must keep answering ``stage=ARCHIVED``.
    if query.view is None:
        query = replace(query, view=PrContentBoardView.ACTIVE)
    page = await services.queries.content_page(actor=actor, query=query)
    # Step 1F.2.3f.6. The month actually applied rides on the page and is echoed,
    # so the selector shows the month the counts and cards were read against.
    return ContentBoardResponse.from_page(page, limit=query.limit, offset=query.offset)


@router.get(
    "/contents/archive-candidates",
    response_model=ArchiveCandidatesResponse,
    summary="What 'Lưu trữ nội dung kỳ trước' would archive",
)
async def archive_candidates(
    actor: CurrentActorDep,
    services: PrServicesDep,
    period: Annotated[str, Query(description="The closed month, YYYY-MM")],
) -> ArchiveCandidatesResponse:
    """Resolve *"lưu trữ nội dung kỳ MM/YYYY"* into a frozen list of ids.

    Step 1F.2.3f.6. The predicate is the board's own *Đã đăng* for that month -
    ``workflow_stage = PUBLISHED`` and the canonical publication instant inside
    it - so ``total`` is the header the person saw over that column and
    ``content_ids`` are its rows, first :data:`BULK_ARCHIVE_MAX_ITEMS` of them
    in the board's order, with ``truncated`` saying when there are more.

    Deliberately takes **no** other filter: this is an end-of-period action over
    the department's output, not over one person's filtered view of it.
    """
    return ArchiveCandidatesResponse.from_candidates(
        await services.queries.archive_candidates(
            actor=actor, period=_month(period, field="period")
        )
    )


@router.post(
    "/contents/archive-batch",
    response_model=BulkArchiveResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Archive one closed month's published output, or none of it",
    responses=_WRITE_RESPONSES,
)
async def archive_batch(
    body: BulkArchiveRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> BulkArchiveResponse:
    """Move every item in the batch ``PUBLISHED -> ARCHIVED``, or none of them.

    **Not a shortcut past anything.** Every item is moved by the same
    ``PrContentWorkflowService.request_transition`` the detail page's *"Lưu trữ
    nội dung"* button calls, under the same capability, the same matrix check,
    the same ``archived_at`` stamp, audit row, transition event and work
    projection request - inside one transaction. Nothing is scheduled and
    nothing runs on its own: this is a person, a named month, a count and a
    confirmation.

    Three refusals, and all three archive nothing:

    * **422** - an empty batch, more than the server's limit, or a month that
      is not closed yet.
    * **403** - the actor may not archive at all.
    * **409** ``pr_bulk_archive_stale`` - an item is missing, already archived,
      no longer ``PUBLISHED``, or its publication is outside the month.
      ``details.affected`` lists which. A retry after success lands here with
      every item ``already_archived``.
    """
    outcome = await services.bulk_archive.archive(
        actor=actor,
        request_id=request_id,
        command=BulkArchiveCommand(
            period=_month(body.period, field="period"),
            content_ids=body.content_ids,
            note=body.note,
        ),
    )
    return BulkArchiveResponse.from_outcome(outcome)


@router.post(
    "/contents",
    response_model=ContentDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create content",
    responses=_WRITE_RESPONSES,
)
async def create_content(
    body: CreateContentRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDetailResponse:
    """Create an item at ``IDEA`` with version 1, and whatever it arrives with.

    The code is allocated by the service. The first version row is written by the
    service. This route builds a command and hands it over.

    ``initial_resources`` - Step 1F.2.3e.1 - is part of that command rather than
    a series of follow-up requests this route makes on the caller's behalf. The
    difference matters on failure: one command is one transaction, and a refused
    resource leaves no content item behind for somebody to find later and wonder
    about.
    """
    initial_resources = []
    for index, resource in enumerate(body.initial_resources):
        # The index travels with the refusal so the form can say which draft was
        # wrong; see ``resource_refusal_index``. An unknown ``resource_type``
        # deserves it as much as an unsafe location does.
        with resource_refusal_index(index):
            initial_resources.append(
                ContentResourceSpec(
                    resource_type=_enum(
                        PrContentResourceType, resource.resource_type, field="resource_type"
                    ),
                    label=resource.label,
                    location=resource.location,
                    note=resource.note,
                    required_for_review=resource.required_for_review,
                )
            )

    snapshot = await services.content.create_content(
        actor=actor,
        request_id=request_id,
        command=CreateContentCommand(
            title=body.title,
            brand_id=body.brand_id,
            owner_user_id=body.owner_user_id,
            format_id=body.format_id,
            pillar_id=body.pillar_id,
            topic=body.topic,
            hook=body.hook,
            brief=body.brief,
            script_text=body.script_text,
            content_type=(
                _enum(PrContentType, body.content_type, field="content_type")
                if body.content_type
                else None
            ),
            # Step 1F.2.3e: the web form is a human-facing create path, so a
            # format is required. The refusal is the service's, before a code is
            # allocated.
            require_content_type=True,
            priority=(
                _enum(PrPriority, body.priority, field="priority")
                if body.priority
                else PrPriority.NORMAL
            ),
            planned_publish_at=body.planned_publish_at,
            targets=tuple(
                ContentTargetSpec(
                    channel_id=target.channel_id,
                    target_publish_at=target.target_publish_at,
                    adaptation_note=target.adaptation_note,
                    distribution_mode=_enum(
                        PrDistributionMode,
                        target.distribution_mode,
                        field="distribution_mode",
                    ),
                )
                for target in body.targets
            ),
            # Step 1F.2. The web flow does not produce content without a
            # channel: a targetless item reaches AI review, pins no policy pack,
            # gets a generic review, and reads to its author exactly like a
            # platform-policy check that passed. Other callers - the Telegram
            # tool, internal scripts - keep the old behaviour.
            require_targets=True,
            initial_resources=tuple(initial_resources),
        ),
    )
    return await _content_detail(actor, services, snapshot.content.id)


@router.get(
    "/contents/{content_id}",
    response_model=ContentDetailResponse,
    summary="One content item",
    responses=_NOT_FOUND,
)
async def get_content(
    content_id: uuid.UUID, actor: CurrentActorDep, services: PrServicesDep
) -> ContentDetailResponse:
    """One item, its current draft and its planned channels."""
    return await _content_detail(actor, services, content_id)


@router.get(
    "/contents/{content_id}/versions",
    response_model=list[ContentVersionResponse],
    summary="Draft history",
    responses=_NOT_FOUND,
)
async def list_versions(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
) -> list[ContentVersionResponse]:
    """Every draft, newest first. Immutable - none of these can be edited."""
    rows = await services.queries.list_content_versions(
        actor=actor, content_id=content_id, limit=_capped(limit)
    )
    return [ContentVersionResponse.from_row(row) for row in rows]


@router.post(
    "/contents/{content_id}/versions",
    response_model=ContentDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Write a new draft",
    responses=_WRITE_RESPONSES,
)
async def revise_content(
    content_id: uuid.UUID,
    body: ReviseContentRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDetailResponse:
    """Append a new version. The previous one is never touched.

    ``expected_version`` mismatching the current draft is a 409, not a silent
    overwrite - two people editing the same script is the case this protects.
    """
    await services.content.revise_content(
        actor=actor,
        request_id=request_id,
        command=ReviseContentCommand(
            content_id=content_id,
            expected_version=body.expected_version,
            title=body.title,
            topic=body.topic,
            hook=body.hook,
            brief=body.brief,
            script_text=body.script_text,
            change_note=body.change_note,
            priority=(
                _enum(PrPriority, body.priority, field="priority") if body.priority else None
            ),
        ),
    )
    return await _content_detail(actor, services, content_id)


@router.post(
    "/contents/{content_id}/transition",
    response_model=ContentDetailResponse,
    summary="Move to another stage",
    responses=_WRITE_RESPONSES,
)
async def transition_content(
    content_id: uuid.UUID,
    body: TransitionRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDetailResponse:
    """Ask for a stage change, or cancel.

    The route does not consult the matrix and does not know which edges exist.
    ``CANCELLED`` is routed to :meth:`PrContentWorkflowService.cancel` because
    cancelling is its own operation with its own rule about which stages allow
    it - not a transition with a different destination.
    """
    target = _enum(PrWorkflowStage, body.target_stage, field="target_stage")
    if target is PrWorkflowStage.CANCELLED:
        await services.workflow.cancel(
            actor=actor, request_id=request_id, content_id=content_id, note=body.note
        )
    elif target is PrWorkflowStage.TEAM_LEAD_REVIEW:
        # Step 1F.2.10. The direct submission - a finished script handed to the
        # Team Lead with the AI review skipped. Routed to its named method for
        # the reason cancelling is: the intent is a business step, and the
        # service that owns it says what it requires and what it does not do.
        # It is still the ``MANUAL`` matrix edge underneath, still re-checked
        # from the current stage, and still refused from anywhere but
        # ``SCRIPTING``.
        await services.workflow.submit_to_team_lead_review(
            actor=actor, request_id=request_id, content_id=content_id, note=body.note
        )
    else:
        await services.workflow.request_transition(
            actor=actor,
            request_id=request_id,
            content_id=content_id,
            target=target,
            note=body.note,
        )
    return await _content_detail(actor, services, content_id)


@router.delete(
    "/contents/{content_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    # Both spelled out because FastAPI infers a response model from the return
    # annotation, and ``-> None`` infers ``NoneType`` rather than "no body" - it
    # then refuses to start, correctly, because a 204 may not describe one.
    response_model=None,
    response_class=Response,
    summary="Xóa vĩnh viễn nội dung",
    responses=_WRITE_RESPONSES,
)
async def delete_content(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
    body: DeleteContentRequest | None = None,
) -> None:
    """Permanently delete one content item and everything that belongs to it.

    Step 1F.2.3a. **This destroys data**: the item, its drafts, its AI reviews,
    its approval history, its tasks and its production submissions are removed in
    one transaction. Step 1F.2.3's soft delete is gone, and with it the hidden
    row it used to leave behind.

    ``204`` and no body, because there is nothing left to return. A tombstone
    object would be a shape the rest of this API does not use and an invitation
    to render a ghost; a client that needs to know what went is reading the audit
    trail, not this response.

    Who may is not decided here. ``PrContentLifecycleService`` asks two
    capabilities and the lifecycle: nobody deletes published work, management
    deletes anything below that, and a member deletes only their own
    never-produced content. The refusals are distinguishable -
    ``pr_published_content`` (409) is "nobody may", ``pr_forbidden`` (403) with a
    ``reason`` is "not you, or not any more".
    """
    await services.lifecycle.delete_content(
        actor=actor,
        request_id=request_id,
        content_id=content_id,
        reason=body.reason if body else None,
    )


# --- Production -------------------------------------------------------------


@router.get(
    "/contents/{content_id}/production",
    response_model=ProductionStateResponse,
    summary="Who is producing this, and what they handed in",
    responses=_NOT_FOUND,
)
async def production_state(
    content_id: uuid.UUID, actor: CurrentActorDep, services: PrServicesDep
) -> ProductionStateResponse:
    """The producer and every production submission, newest first.

    All of them, not the latest: the internal reviewer's screen shows the
    history, because a piece on its second cut was sent back for a reason
    somebody wrote down.
    """
    detail = await services.queries.get_content(actor=actor, content_id=content_id)
    return ProductionStateResponse.from_state(await services.production.state(detail.content))


@router.post(
    "/contents/{content_id}/producer",
    response_model=ContentDetailResponse,
    summary="Assign or change the producer",
    responses=_WRITE_RESPONSES,
)
async def assign_producer(
    content_id: uuid.UUID,
    body: AssignProducerRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDetailResponse:
    """Say who produces this piece, or clear it.

    Needs ``PR_PRODUCTION_ASSIGN``, which no ``EMPLOYEE`` holds - so this is the
    management path, and it is what stops one member taking work already assigned
    to another. A member with nothing assigned to them uses ``/producer/claim``
    below instead.
    """
    await services.production.assign_producer(
        actor=actor,
        request_id=request_id,
        content_id=content_id,
        producer_user_id=body.producer_user_id,
    )
    return await _content_detail(actor, services, content_id)


@router.post(
    "/contents/{content_id}/producer/claim",
    response_model=ContentDetailResponse,
    summary="Nhận sản xuất",
    responses=_WRITE_RESPONSES,
)
async def claim_production(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDetailResponse:
    """Take an unclaimed production for yourself.

    Takes no body: *who* is claiming is the session and could never be anything
    else, exactly as the reviewer on an approval is. Two people pressing at once
    produce one winner and one ``409`` naming who got it - the conditional update
    in ``PrProductionService.claim_production`` is what decides, not this route.
    """
    await services.production.claim_production(
        actor=actor, request_id=request_id, content_id=content_id
    )
    return await _content_detail(actor, services, content_id)


@router.post(
    "/contents/{content_id}/production/start",
    response_model=ContentDetailResponse,
    summary="Bắt đầu sản xuất",
    responses=_WRITE_RESPONSES,
)
async def start_production(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDetailResponse:
    """Move ``APPROVED -> PRODUCTION``, once somebody holds the piece.

    Step 1F.2.3b. Its own route rather than a body on ``/transition``, because
    it is not a generic move: it needs a producer, it is refused to everybody
    who is neither that producer nor a production manager, and it stamps
    ``production_started_at`` - the marker the permanent-delete rule reads and
    nothing ever clears.

    Takes no body. *Which* piece is the path and *who* is the session; there is
    nothing left to say.
    """
    await services.production.start_production(
        actor=actor, request_id=request_id, content_id=content_id
    )
    return await _content_detail(actor, services, content_id)


@router.post(
    "/contents/{content_id}/production-submissions",
    response_model=ProductionStateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Gửi duyệt nội bộ",
    responses=_WRITE_RESPONSES,
)
async def submit_production(
    content_id: uuid.UUID,
    body: SubmitProductionRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ProductionStateResponse:
    """Record a finished file and send the item to internal review.

    The transition is **not** available on ``/transition``'s side of the house
    for this stage in practice: the service writes the submission and moves
    ``PRODUCTION -> INTERNAL_REVIEW`` in one transaction, so the reviewer never
    opens a gate with no file behind it. A caller that tries the plain transition
    route instead gets an item at internal review with nothing to watch, which is
    why the panel offers ``SUBMIT_PRODUCTION`` and not the raw move.

    Returns the whole production state, so the screen that submitted sees its own
    submission at the top of the list without a second request.
    """
    await services.production.submit_production(
        actor=actor,
        request_id=request_id,
        command=SubmitProductionCommand(
            content_id=content_id,
            artifact_type=_enum(
                PrProductionArtifactType, body.artifact_type, field="artifact_type"
            ),
            location=body.location,
            label=body.label,
            note=body.note,
        ),
    )
    detail = await services.queries.get_content(actor=actor, content_id=content_id)
    return ProductionStateResponse.from_state(await services.production.state(detail.content))


@router.post(
    "/contents/{content_id}/undo",
    response_model=ContentDetailResponse,
    summary="Hoàn tác bước vừa rồi",
    responses=_WRITE_RESPONSES,
)
async def undo_last_action(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDetailResponse:
    """Take back the last reversible decision on this item.

    Step 1F.2.3b. **There is no destination parameter, and there will not be
    one.** A client that could name a stage could move content anywhere, and
    "where does this go back to" is not a question a browser is in a position to
    answer - the server reads the item's own transition history, finds the last
    reversible action, and reverses that.

    Nothing is erased. The original transition and the original approval stay
    exactly as they were, and the undo appends a reversal linked to them; what
    changes is that the reversed approval stops counting as authority, so a Head
    cannot approve on the strength of a team-lead sign-off somebody withdrew.

    Refusals are checked and specific: ``pr_undo_not_available`` (409) with a
    ``reason`` for "nothing to undo", "somebody moved it on" and each downstream
    dependency; ``pr_forbidden`` (403) when the action is not this actor's to
    take back.
    """
    await services.undo.undo_last(actor=actor, request_id=request_id, content_id=content_id)
    return await _content_detail(actor, services, content_id)


@router.get(
    "/contents/{content_id}/history",
    response_model=list[TransitionEventResponse],
    summary="Every stage change, and what was undone",
    responses=_NOT_FOUND,
)
async def content_history(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 100,
) -> list[TransitionEventResponse]:
    """The structured transition history, oldest first.

    Step 1F.2.3b. Read as a story, which is why it is not reversed: a person
    scanning it wants to see the piece move forward and then see the line where
    somebody took a step back. Reversal pairs carry each other's ids, so the
    client shows both rather than inferring which undo cancelled which move from
    the clock.
    """
    detail = await services.queries.get_content(actor=actor, content_id=content_id)
    rows = await services.undo.history(detail.content.id, limit=_capped(limit))
    return [TransitionEventResponse.from_row(row) for row in rows]


@router.get(
    "/contents/{content_id}/available-actions",
    response_model=AvailableActionsResponse,
    summary="What you may do next with this item",
    responses=_NOT_FOUND,
)
async def available_actions(
    content_id: uuid.UUID, actor: CurrentActorDep, services: PrServicesDep
) -> AvailableActionsResponse:
    """The next steps this session may actually take. **Reads only.**

    Step 1E.2. Before it, the panel drew a button for every stage in the enum
    and let the server refuse eleven of them; this route lets a screen offer the
    one or two that work.

    It computes nothing. ``PrAvailableActionService`` composes the transition
    matrix, the approval-gate table and the capability service - the same three
    the write routes go through - and this route shapes the result. There is no
    stage list here, no capability comparison, and nothing is written: the item
    stands at the same stage after this call as before it.

    An empty ``available_actions`` is a real answer, not an error: an archived
    item, or a reader with no write capability, has nothing to do next.
    """
    detail = await services.queries.get_content(actor=actor, content_id=content_id)
    return AvailableActionsResponse(
        content_id=detail.content.id,
        workflow_stage=detail.content.workflow_stage.value,
        available_actions=[
            AvailableActionResponse.from_action(action)
            for action in await services.actions.for_content(actor=actor, content=detail.content)
        ],
    )


@router.get(
    "/contents/{content_id}/review-context",
    response_model=ReviewContextResponse,
    summary="Everything needed to decide",
    responses=_NOT_FOUND,
)
async def review_context(
    content_id: uuid.UUID, actor: CurrentActorDep, services: PrServicesDep
) -> ReviewContextResponse:
    """The review screen's payload, bound to the draft currently on top.

    ``ai_review`` is ``null`` when nothing has judged this version. The UI must
    say so rather than render an empty verdict - and it especially must not
    render "PASS" for an absent review.
    """
    return ReviewContextResponse.from_context(
        await services.queries.get_content_review_context(actor=actor, content_id=content_id)
    )


async def _content_detail(
    actor: Actor, services: PrServicesDep, content_id: uuid.UUID
) -> ContentDetailResponse:
    """Re-read and shape one item.

    Every write route returns this, so the browser never has to guess what
    changed - including the stage a service may have advanced on its own, such as
    a publication completing an item.
    """
    return ContentDetailResponse.from_detail(
        await services.queries.get_content(actor=actor, content_id=content_id)
    )


# --- Human review -----------------------------------------------------------


@router.get(
    "/reviews/pending",
    response_model=list[ContentSummaryResponse],
    summary="Waiting for me",
)
async def pending_reviews(
    actor: CurrentActorDep,
    services: PrServicesDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
) -> list[ContentSummaryResponse]:
    """Content this actor may actually decide, right now.

    Empty for somebody with no grant, whatever their role. The honest answer:
    ``PrApprovalService`` would refuse each of these, so listing them would be an
    invitation to a refusal.

    Step 1F.2.7a extended "actually" to the **scope**. A grant over two Facebook
    channels used to put every item at that gate in this list; it now puts the
    Facebook ones on those two channels, which is the set
    ``PrCapabilityService.can_approve`` admits.
    """
    rows = await services.queries.content_awaiting(actor=actor, limit=_capped(limit))
    return [ContentSummaryResponse.from_row(row) for row in rows]


@router.get(
    "/reviews/approvable",
    response_model=ApprovableSelectionResponse,
    summary="Select all I can approve at one step",
)
async def approvable_selection(
    actor: CurrentActorDep,
    services: PrServicesDep,
    query: ContentQueryDep,
    gate: Annotated[str, Query(description="TEAM_LEAD_REVIEW | HEAD_REVIEW | INTERNAL_REVIEW")],
) -> ApprovableSelectionResponse:
    """Resolve "chọn tất cả nội dung ở bước này" into a frozen list of ids.

    It takes **the same filter query as the board** - deliberately, and it is the
    whole reason a select-all cannot reach outside what somebody is looking at:
    the search, the channel, the platform, the responsible person, the dates, the
    priority, the format and the scope tab all narrow this exactly as they narrow
    the cards on screen. The ``stage`` is the one filter this route overrides,
    with the gate's own, because a select-all *at a step* is what was asked for.

    The response's ``total`` and ``content_ids`` come from one ``WHERE``, so the
    figure the panel puts in the button and the batch it would submit are the
    same question asked with and without a ``LIMIT``. When the queue is longer
    than the limit the ids are the first page of it in the board's order and
    ``truncated`` says so - the panel then offers a bounded, truthful batch
    rather than a number it cannot honour.

    Nothing here is a capability flag the browser combines: eligibility is
    ``approvable_by`` in SQL, the same predicate ``PrCapabilityService`` enforces
    item by item at the write.
    """
    return ApprovableSelectionResponse.from_selection(
        await services.queries.approvable_selection(
            actor=actor,
            gate=_enum(PrApprovalStage, gate, field="gate"),
            query=query,
        )
    )


@router.post(
    "/reviews/bulk-approve",
    response_model=BulkApproveResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Approve several items at one step, or none of them",
    responses=_WRITE_RESPONSES,
)
async def bulk_approve(
    body: BulkApproveRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> BulkApproveResponse:
    """Record one ``APPROVED`` decision for every item in the batch, or none.

    **This route is not a shortcut past anything.** A direct caller gets exactly
    what the panel's user gets, because the panel is not where any of it happens:
    the unscoped capability check, the per-item scoped grant check, the row
    locks, the same-gate check and the staleness check are all inside
    ``PrBulkApprovalService``, and every approval it records is written by the
    same ``PrApprovalService.record_decision`` a single approval goes through -
    so each item keeps its own event, its own transition, its own audit row and
    its own notifications.

    Three refusals, and all three approve nothing:

    * **422** - an empty batch, or more than the server's limit.
    * **409** ``pr_bulk_approval_stale`` - an item has moved, vanished, or is at
      a different gate from the one named. ``details.affected`` lists which.
    * **403** ``pr_bulk_approval_forbidden`` - an item is outside this actor's
      grant scope. ``details.affected`` lists which.

    A 201 therefore means every item in ``content_ids`` was approved. There is no
    partial success to report and no shape in which one could be reported.
    """
    outcome = await services.bulk_approvals.approve(
        actor=actor,
        request_id=request_id,
        command=BulkApproveCommand(
            gate=_enum(PrApprovalStage, body.gate, field="gate"),
            content_ids=body.content_ids,
            reviewer_user_id=_reviewer_id(actor),
            comment=body.comment,
        ),
    )
    return BulkApproveResponse.from_outcome(outcome)


@router.post(
    "/contents/{content_id}/reviews",
    response_model=ContentDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Approve, request revision, or reject",
    responses=_WRITE_RESPONSES,
)
async def record_review_decision(
    content_id: uuid.UUID,
    body: ApprovalDecisionRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDetailResponse:
    """File one human decision.

    Three things this route does **not** decide, all of them inside
    ``PrApprovalService``:

    * **which gate you are at.** The stage the content stands at determines the
      approval stage; a body cannot name one, so nobody files a Head approval
      against something waiting for a Team Lead;
    * **whether you may.** The capability check is the service's, and it is per
      gate: since Step 1F.2.2 one person may decide at both, but only by holding
      ``PR_TEAM_LEAD_REVIEW`` *and* ``PR_HEAD_REVIEW`` in their own right. Each
      decision is its own event and its own check;
    * **whether the gate's prerequisites are met.** A Head approval needs the
      team-lead approval of this draft already on file, which the service reads.

    ``version_reviewed`` binds the decision to the text that was on screen. A
    revision landing in between makes this a 409.
    """
    stage = _stage_gate_for(await services.content.require_content(content_id))
    await services.approvals.record_decision(
        actor=actor,
        request_id=request_id,
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=_reviewer_id(actor),
            approval_stage=stage,
            decision=_enum(PrApprovalDecision, body.decision, field="decision"),
            version_reviewed=body.version_reviewed,
            comment=body.comment,
            task_id=body.task_id,
        ),
    )
    return await _content_detail(actor, services, content_id)


def _stage_gate_for(content: PrContentItem) -> PrApprovalStage:
    """Which gate an item standing at its current stage is waiting at.

    A lookup in ``STAGE_APPROVAL_GATES`` - the domain's own mapping, not a copy.
    An earlier draft of this route hand-wrote the pairs here, which is exactly the
    duplicated-matrix problem the module docstring forbids: it named stages that
    do not exist and would have drifted the moment a gate was added.

    Deriving the gate rather than accepting it in the body is what stops a caller
    from choosing which gate their approval counts for. ``PrApprovalService``
    re-checks the pairing and raises ``PrApprovalStageMismatchError`` if this ever
    disagreed, so this is a convenience, not the enforcement.
    """
    gate = STAGE_APPROVAL_GATES.get(content.workflow_stage)
    if gate is None:
        raise PrValidationError(
            "Nội dung này không đang ở bước chờ duyệt.",
            details={"workflow_stage": content.workflow_stage.value},
        )
    return gate


# --- AI review (recording only) ---------------------------------------------


@router.get(
    "/contents/{content_id}/ai-reviews",
    response_model=list[AiReviewResponse],
    summary="AI verdicts on record",
    responses=_NOT_FOUND,
)
async def list_ai_reviews(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    version_no: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
) -> list[AiReviewResponse]:
    """Recorded AI reviews, newest first. An empty list means none exist.

    It does not mean "not yet finished", because nothing here runs a review.
    """
    await services.queries.get_content(actor=actor, content_id=content_id)
    rows = await services.ai_reviews.list_reviews(
        content_id, version_no=version_no, limit=_capped(limit)
    )
    return [AiReviewResponse.from_row(row) for row in rows]


@router.post(
    "/contents/{content_id}/submit-ai-review",
    response_model=ContentDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a verdict produced elsewhere",
    responses=_WRITE_RESPONSES,
)
async def submit_ai_review(
    content_id: uuid.UUID,
    body: SubmitAiReviewRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDetailResponse:
    """Record an AI verdict. **This route does not call a model.**

    It exists so an external reviewer process has somewhere to report. The
    verdict is advisory: it can move an item off ``AI_REVIEW``, and it can never
    create an approval event or stand in for a person - ``PrAiReviewService``
    enforces both, and ``pr_ai_reviews`` has no reviewer column to abuse.

    ``model_name`` and ``prompt_version`` are required. An unattributable verdict
    cannot be audited later, and "the AI said so" with no record of which AI is
    not a reason anybody can act on.
    """
    await services.ai_reviews.record_review(
        actor=actor,
        request_id=request_id,
        command=RecordAiReviewCommand(
            content_id=content_id,
            reviewed_version=body.reviewed_version,
            review_type=_enum(PrAiReviewType, body.review_type, field="review_type"),
            result=_enum(PrAiReviewResult, body.result, field="result"),
            model_name=body.model_name,
            prompt_version=body.prompt_version,
            model_version=body.model_version,
            reviewed_at=utcnow(),
            score=body.score,
            summary=body.summary,
            issues=body.issues,
            suggestions=body.suggestions,
            policy_flags=body.policy_flags,
            task_id=body.task_id,
        ),
    )
    return await _content_detail(actor, services, content_id)


@router.patch(
    "/contents/{content_id}/targets/{target_id}",
    response_model=ContentDetailResponse,
    summary="Set a target's distribution mode",
    responses=_WRITE_RESPONSES,
)
async def set_target_distribution_mode(
    content_id: uuid.UUID,
    target_id: uuid.UUID,
    body: UpdateTargetRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDetailResponse:
    """Say whether this target is organic or a paid ad.

    Step 1F.1. Which official policy applies depends on it, so it is stated by a
    person rather than guessed from a channel name containing "ads" - that guess
    would decide, silently, which rulebook somebody's advertising is judged
    against.

    Guarded by ``PR_CONTENT_EDIT``: it is a change to what the content *is*, on
    the same footing as revising it, and no new capability was invented.
    """
    await services.capabilities.require(actor, PrCapability.PR_CONTENT_EDIT)
    mode = _enum(PrDistributionMode, body.distribution_mode, field="distribution_mode")

    target = await services.session.get(PrContentTarget, target_id)
    if target is None or target.content_id != content_id:
        raise PrNotFoundError(
            "Không tìm thấy kênh dự kiến này của nội dung.",
            details={"content_id": str(content_id), "target_id": str(target_id)},
        )
    before = target.distribution_mode
    target.distribution_mode = mode
    await services.session.flush()

    await record_pr_event(
        services.audit,
        request_id=request_id,
        actor=actor,
        action=AuditAction.PR_CONTENT_TARGET_UPDATED,
        entity_type="pr_content_target",
        entity_id=target.id,
        before={"distribution_mode": before.value},
        after={"content_id": str(content_id), "distribution_mode": mode.value},
    )
    return await _content_detail(actor, services, content_id)


@router.patch(
    "/contents/{content_id}/priority",
    response_model=ContentDetailResponse,
    summary="Set a content item's priority",
    responses=_WRITE_RESPONSES,
)
async def set_content_priority(
    content_id: uuid.UUID,
    body: UpdateContentPriorityRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDetailResponse:
    """Retriage a piece: *Rất gấp*, *Gấp*, *Ưu tiên* or *Bình thường*.

    Step 1F.2.3d. A ``PATCH`` of its own rather than a field on the revise route,
    because the two are different kinds of change: revising writes an immutable
    version of the script and is refused once the piece leaves the editable
    stages, and priority is queue metadata that matters most *after* that point.
    Folding it in would have meant either a version row nobody wrote or a
    priority nobody could change.

    The router parses and delegates. Which levels exist is
    :class:`~meobot.domain.pr.models.PrPriority`, and who may set one is
    :meth:`~meobot.application.pr_content_service.PrContentService.set_content_priority`
    - no capability check is written here, because a second copy of that rule in
    the transport layer is how the API and the Telegram tools come to disagree
    about who may act.
    """
    priority = _enum(PrPriority, body.priority, field="priority")
    await services.content.set_content_priority(
        actor=actor,
        request_id=request_id,
        content_id=content_id,
        priority=priority,
    )
    return await _content_detail(actor, services, content_id)


@router.patch(
    "/contents/{content_id}/content-type",
    response_model=ContentDetailResponse,
    summary="Classify a content item",
    responses=_WRITE_RESPONSES,
)
async def set_content_type(
    content_id: uuid.UUID,
    body: UpdateContentTypeRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDetailResponse:
    """Say which of the six formats this is, or correct it.

    Step 1F.2.3e. Its own route rather than a field on the revise call, for the
    reason the priority route is separate: a format is a fact *about* the work,
    not a draft of it, and it is most often set long after the piece has left the
    editable stages - classifying a historical item is the main use.
    """
    content_type = _enum(PrContentType, body.content_type, field="content_type")
    await services.content.set_content_type(
        actor=actor,
        request_id=request_id,
        content_id=content_id,
        content_type=content_type,
    )
    return await _content_detail(actor, services, content_id)


@router.get(
    "/contents/{content_id}/resources",
    response_model=list[ContentResourceResponse],
    summary="Review material attached to this content",
    responses=_NOT_FOUND,
)
async def list_content_resources(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> list[ContentResourceResponse]:
    """The briefs, references and assets a reviewer should consult.

    Step 1F.2.3e. Gated on the plain read permission and **no narrower**:
    resources are the context somebody judges the content against, so anybody who
    may read the item may read them. Editing is stricter - see the write routes
    below.

    Required-for-review items come first; the ordering is the server's, and the
    panel renders it rather than re-sorting.

    Its own endpoint rather than a field on the detail response: the board does
    not need it, and sixty cards each carrying their references is the payload
    this keeps off the list.
    """
    rows = await services.queries.content_resources(actor=actor, content_id=content_id)
    return [ContentResourceResponse.from_row(row) for row in rows]


@router.post(
    "/contents/{content_id}/resources",
    response_model=ContentResourceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Attach review material",
    responses=_WRITE_RESPONSES,
)
async def add_content_resource(
    content_id: uuid.UUID,
    body: ContentResourceRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentResourceResponse:
    """Attach a brief, a reference, an image or an asset to this content.

    The location is validated in the domain, not here - see
    :mod:`meobot.domain.pr.resources`. Nothing fetches it.
    """
    resource = await services.content_resources.add_resource(
        actor=actor,
        request_id=request_id,
        command=AddContentResourceCommand(
            content_id=content_id,
            resource_type=_enum(PrContentResourceType, body.resource_type, field="resource_type"),
            label=body.label,
            location=body.location,
            note=body.note,
            required_for_review=body.required_for_review,
        ),
    )
    return ContentResourceResponse.from_row(resource)


@router.patch(
    "/contents/{content_id}/resources/{resource_id}",
    response_model=ContentResourceResponse,
    summary="Correct attached review material",
    responses=_WRITE_RESPONSES,
)
async def update_content_resource(
    content_id: uuid.UUID,
    resource_id: uuid.UUID,
    body: UpdateContentResourceRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentResourceResponse:
    """Change a resource's type, label, location, note or required flag.

    ``content_id`` is in the path so the URL names the aggregate, and is checked
    against the resource's own - but it is **not** something this can change. A
    resource belongs to the item it was attached to; moving one is a delete and
    an add.
    """
    resource = await services.content_resources.update_resource(
        actor=actor,
        request_id=request_id,
        command=UpdateContentResourceCommand(
            resource_id=resource_id,
            resource_type=(
                _enum(PrContentResourceType, body.resource_type, field="resource_type")
                if body.resource_type
                else None
            ),
            label=body.label,
            location=body.location,
            note=body.note,
            required_for_review=body.required_for_review,
        ),
    )
    if resource.content_id != content_id:
        raise PrNotFoundError(
            "Không tìm thấy tài nguyên này của nội dung.",
            details={"content_id": str(content_id), "resource_id": str(resource_id)},
        )
    return ContentResourceResponse.from_row(resource)


@router.delete(
    "/contents/{content_id}/resources/{resource_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove attached review material",
    responses=_WRITE_RESPONSES,
)
async def delete_content_resource(
    content_id: uuid.UUID,
    resource_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> Response:
    """Detach one resource. The material it points at is untouched.

    Not the content delete flow: this removes a pointer, moves no stage and
    destroys nothing outside this table.
    """
    resource = await services.queries.content_resources(actor=actor, content_id=content_id)
    if not any(row.id == resource_id for row in resource):
        raise PrNotFoundError(
            "Không tìm thấy tài nguyên này của nội dung.",
            details={"content_id": str(content_id), "resource_id": str(resource_id)},
        )
    await services.content_resources.delete_resource(
        actor=actor, request_id=request_id, resource_id=resource_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Production outputs: masters and derivatives, Step 1F.2.3f --------------


@router.get(
    "/contents/{content_id}/production-outputs",
    response_model=list[ProductionSubmissionResponse],
    summary="Original production files handed over for this content",
    responses=_NOT_FOUND,
)
async def list_production_outputs(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> list[ProductionSubmissionResponse]:
    """The masters: every file submitted for internal review, in order.

    Step 1F.2.3f. Its own endpoint rather than a field on the detail response,
    for the reason the resources route gives: the board does not need it, and
    sixty cards each carrying their production files is the payload this keeps
    off the list.

    **Never hidden after publication.** A published piece is exactly when
    somebody wants to see which file was approved, and this is also half of what
    the publication form's output picker is made of - the other half is
    ``/derivatives``.
    """
    detail = await services.queries.get_content(actor=actor, content_id=content_id)
    rows = await services.queries.content_submissions(actor=actor, content_id=content_id)
    # Step 1F.2.3f.2. Per row: who handed *this* one in, and whether *this* one
    # has ever been published. Both halves are the write's own predicates.
    published = {
        row.production_submission_id
        for row in await services.publications.list_publications(content_id)
    }
    return [
        ProductionSubmissionResponse.from_row(
            row,
            can_correct=row.id not in published
            and await services.production.may_correct_submission(actor, detail.content, row),
        )
        for row in rows
    ]


@router.patch(
    "/contents/{content_id}/production-outputs/{submission_id}",
    response_model=ProductionSubmissionResponse,
    summary="Correct where a handed-in production file lives",
    responses=_WRITE_RESPONSES,
)
async def correct_production_output(
    content_id: uuid.UUID,
    submission_id: uuid.UUID,
    body: CorrectProductionOutputRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ProductionSubmissionResponse:
    """Fix a mistyped path or a moved link on a file already handed in.

    Step 1F.2.3f.2, and the **one** write to an otherwise append-only table. It
    changes where the file is and nothing about which file it is: the submission
    number, the draft it was cut from, the producer and the submitter have no
    field here and cannot move.

    Who: the person who handed it in, or whoever manages this piece's production.
    When: never once any publication references it - including one already
    reversed, because that row still records that this exact file was posted.
    """
    submission = await services.production.correct_submission(
        actor=actor,
        request_id=request_id,
        command=CorrectSubmissionCommand(
            submission_id=submission_id,
            artifact_type=(
                _enum(PrProductionArtifactType, body.artifact_type, field="artifact_type")
                if body.artifact_type
                else None
            ),
            location=body.location,
            note=body.note,
        ),
    )
    if submission.content_id != content_id:
        raise PrNotFoundError(
            "Không tìm thấy file sản xuất này của nội dung.",
            details={"content_id": str(content_id), "submission_id": str(submission_id)},
        )
    return ProductionSubmissionResponse.from_row(submission, can_correct=True)


@router.get(
    "/contents/{content_id}/derivatives",
    response_model=list[ContentDerivativeResponse],
    summary="Derivative production outputs",
    responses=_NOT_FOUND,
)
async def list_content_derivatives(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> list[ContentDerivativeResponse]:
    """The re-cuts made from this content: cutdowns, remixes, caption variants.

    Gated on the plain read permission and no narrower - what came out of a
    piece is part of reading it. Since Step 1F.2.3g **recording** one is the same
    rule, and correcting or removing one is not: each row carries the server's
    own ``can_edit`` and ``can_delete`` for this session, plus the name of
    whoever recorded it, in three bounded queries for the whole list.
    """
    views = await services.content_assets.describe_derivatives(actor=actor, content_id=content_id)
    return [ContentDerivativeResponse.from_view(view) for view in views]


@router.post(
    "/contents/{content_id}/derivatives",
    response_model=ContentDerivativeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a derivative production output",
    responses=_WRITE_RESPONSES,
)
async def add_content_derivative(
    content_id: uuid.UUID,
    body: ContentDerivativeRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDerivativeResponse:
    """Record a cutdown, remix or reformat made from this content.

    **Not a workflow transition.** The stage does not move, production does not
    reopen, no approval is required and the board does not notice - which is the
    whole point: reuse happens on the same content record rather than by cloning
    it.

    Step 1F.2.3g: **anybody who may view this item may record one**, producer or
    not. Who exactly is
    :meth:`~meobot.application.pr_content_asset_service.PrContentAssetService.add_derivative`,
    which checks it again on the way in - this route decides nothing - and the
    location is validated in the domain. Nothing fetches it.
    """
    derivative = await services.content_assets.add_derivative(
        actor=actor,
        request_id=request_id,
        command=AddDerivativeCommand(
            content_id=content_id,
            derivative_type=_enum(
                PrContentDerivativeType, body.derivative_type, field="derivative_type"
            ),
            label=body.label,
            location=body.location,
            source_submission_id=body.source_submission_id,
            note=body.note,
        ),
    )
    return ContentDerivativeResponse.from_row(derivative)


@router.patch(
    "/contents/{content_id}/derivatives/{derivative_id}",
    response_model=ContentDerivativeResponse,
    summary="Correct a derivative production output",
    responses=_WRITE_RESPONSES,
)
async def update_content_derivative(
    content_id: uuid.UUID,
    derivative_id: uuid.UUID,
    body: UpdateContentDerivativeRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDerivativeResponse:
    """Change a derivative's type, label, location, lineage or note.

    ``content_id`` is in the path so the URL names the aggregate, and is checked
    against the derivative's own - but it is **not** something this can change.

    Once a publication points at this row the identity-bearing fields are frozen
    and the human ones are not; the service refuses with a 409 naming which.
    """
    derivative = await services.content_assets.update_derivative(
        actor=actor,
        request_id=request_id,
        command=UpdateDerivativeCommand(
            derivative_id=derivative_id,
            derivative_type=(
                _enum(PrContentDerivativeType, body.derivative_type, field="derivative_type")
                if body.derivative_type
                else None
            ),
            label=body.label,
            location=body.location,
            source_submission_id=body.source_submission_id,
            note=body.note,
        ),
    )
    if derivative.content_id != content_id:
        raise PrNotFoundError(
            "Không tìm thấy sản phẩm phái sinh này của nội dung.",
            details={"content_id": str(content_id), "derivative_id": str(derivative_id)},
        )
    return ContentDerivativeResponse.from_row(derivative)


@router.delete(
    "/contents/{content_id}/derivatives/{derivative_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a derivative production output",
    responses=_WRITE_RESPONSES,
)
async def delete_content_derivative(
    content_id: uuid.UUID,
    derivative_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> Response:
    """Remove one derivative, unless something was published from it.

    A published output is refused with a 409 rather than deleted: the publication
    would be left naming a file that no longer exists. The file itself is
    untouched either way - this table holds a pointer.
    """
    rows = await services.queries.content_derivatives(actor=actor, content_id=content_id)
    if not any(row.id == derivative_id for row in rows):
        # The URL names an aggregate, so a derivative belonging to a different
        # item is *not found* here rather than refused - the caller is not
        # entitled to learn it exists. Whether they may remove **this** one is
        # the service's, below.
        raise PrNotFoundError(
            "Không tìm thấy sản phẩm phái sinh này của nội dung.",
            details={"content_id": str(content_id), "derivative_id": str(derivative_id)},
        )
    await services.content_assets.delete_derivative(
        actor=actor, request_id=request_id, derivative_id=derivative_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Comments, Step 1F.2.3g -------------------------------------------------
# The one collection on a content item that is **conversation** rather than
# record. Read and write take the same permission - the plain read - because a
# person who can see a discussion and not join it is a state nobody asked for.


@router.get(
    "/contents/{content_id}/comments",
    response_model=ContentCommentPageResponse,
    summary="The discussion on this content",
    responses=_NOT_FOUND,
)
async def list_content_comments(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    limit: int = Query(DEFAULT_COMMENT_PAGE, ge=1, le=MAX_COMMENT_PAGE),
    offset: int = Query(0, ge=0),
) -> ContentCommentPageResponse:
    """Root threads with their replies, oldest first.

    Paginated over **roots**, unlike this API's other child collections: a
    resource list is a handful of rows by its nature and a three-month
    conversation is not. Each comment carries the server's own ``can_edit`` and
    ``can_delete`` for this session, so the panel draws controls from answers
    rather than from a session-id comparison.

    Three bounded queries however long the thread is - see
    :meth:`~meobot.application.pr_content_comment_service.PrContentCommentService.list_comments`.
    The board does **not** call this and carries no comment count: sixty cards
    each fetching their discussion is the N+1 the work queue exists without.
    """
    page = await services.content_comments.list_comments(
        actor=actor, content_id=content_id, limit=limit, offset=offset
    )
    return ContentCommentPageResponse.from_page(content_id, page)


@router.post(
    "/contents/{content_id}/comments",
    response_model=ContentCommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Say something about this content",
    responses=_WRITE_RESPONSES,
)
async def add_content_comment(
    content_id: uuid.UUID,
    body: ContentCommentRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentCommentResponse:
    """Post a root comment, or a reply when ``parent_comment_id`` is given.

    **Changes nothing about the content.** No stage moves, no version is
    written, no approval is satisfied or invalidated, no priority or owner
    changes, and nobody is notified. A comment is discussion.

    One route for both verbs, because a reply is a comment with a parent. The
    parent must be a **root of this same item**; a reply to a reply is refused
    with a 422 rather than silently re-parented, which would move somebody's
    answer under a different question.
    """
    comment = await services.content_comments.add_comment(
        actor=actor,
        request_id=request_id,
        command=AddCommentCommand(
            content_id=content_id,
            body=body.body,
            parent_comment_id=body.parent_comment_id,
        ),
    )
    return ContentCommentResponse.from_view(_own_comment_view(comment, actor, can_delete=True))


@router.patch(
    "/contents/{content_id}/comments/{comment_id}",
    response_model=ContentCommentResponse,
    summary="Reword your own comment",
    responses=_WRITE_RESPONSES,
)
async def update_content_comment(
    content_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: UpdateContentCommentRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentCommentResponse:
    """Change the wording. The author's, and nobody else's.

    Not management's either: a lead rewording a member's comment produces a
    sentence in that member's name they never wrote.

    ``content_id`` is in the path so the URL names the aggregate, and the
    service checks the comment against it - a comment id from one item may not
    be edited through another item's URL.
    """
    comment = await services.content_comments.update_comment(
        actor=actor,
        request_id=request_id,
        content_id=content_id,
        comment_id=comment_id,
        body=body.body,
    )
    return ContentCommentResponse.from_view(_own_comment_view(comment, actor, can_delete=True))


@router.delete(
    "/contents/{content_id}/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Take a comment down",
    responses=_WRITE_RESPONSES,
)
async def delete_content_comment(
    content_id: uuid.UUID,
    comment_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> Response:
    """Tombstone one comment. Its author, or a moderator.

    **Nothing is removed.** The row stays with ``deleted_at`` set, stops sending
    its words and its author's name, and the replies underneath it remain
    readable - a deleted question that took its answers with it would destroy
    other people's words to honour one person's decision about their own.

    A ``204``, like every other delete here: the client refetches the thread,
    which is what it needs anyway to redraw the tombstone in place.
    """
    await services.content_comments.delete_comment(
        actor=actor,
        request_id=request_id,
        content_id=content_id,
        comment_id=comment_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _own_comment_view(comment: PrContentComment, actor: Actor, *, can_delete: bool) -> CommentView:
    """The comment just written, as its own author sees it.

    A write answers with the thing it wrote, and the author of a fresh comment
    is by construction the session - so the two flags are known without asking
    anything, and the name is the actor's own. The **list** route is where a
    client draws its controls from; this exists so a panel can render the new
    row optimistically without a second request.
    """
    return CommentView(
        id=comment.id,
        content_id=comment.content_id,
        parent_comment_id=comment.parent_comment_id,
        author_user_id=comment.author_user_id,
        author_name=actor.full_name,
        body=comment.body,
        created_at=comment.created_at,
        edited_at=comment.edited_at,
        is_deleted=False,
        can_edit=True,
        can_delete=can_delete,
    )


# --- Product and landing pages, Step 1F.2.3f --------------------------------


@router.get(
    "/contents/{content_id}/destinations",
    response_model=list[ContentDestinationResponse],
    summary="Product and landing pages this content points at",
    responses=_NOT_FOUND,
)
async def list_content_destinations(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> list[ContentDestinationResponse]:
    """Where this content sends people - a landing page, a booking page.

    Durable content metadata, visible at every stage. Deliberately none of the
    three things it sits near: not review material, not a produced file, and not
    a publication URL.
    """
    rows = await services.queries.content_destinations(actor=actor, content_id=content_id)
    return [ContentDestinationResponse.from_row(row) for row in rows]


@router.post(
    "/contents/{content_id}/destinations",
    response_model=ContentDestinationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Attach a product or landing page",
    responses=_WRITE_RESPONSES,
)
async def add_content_destination(
    content_id: uuid.UUID,
    body: ContentDestinationRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDestinationResponse:
    """Attach a commercial destination link to this content.

    Authorised as content metadata - the same rule behind priority, content type
    and resources - because where a piece sends a customer is a fact about the
    campaign rather than about a file. The URL is validated in the domain and
    never fetched.
    """
    destination = await services.content_assets.add_destination(
        actor=actor,
        request_id=request_id,
        command=AddDestinationCommand(
            content_id=content_id, label=body.label, url=body.url, note=body.note
        ),
    )
    return ContentDestinationResponse.from_row(destination)


@router.patch(
    "/contents/{content_id}/destinations/{destination_id}",
    response_model=ContentDestinationResponse,
    summary="Correct a product or landing page",
    responses=_WRITE_RESPONSES,
)
async def update_content_destination(
    content_id: uuid.UUID,
    destination_id: uuid.UUID,
    body: UpdateContentDestinationRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDestinationResponse:
    """Change a destination's label, URL or note.

    Nothing freezes here, unlike a published derivative: a destination is where a
    customer is sent *now*, so a moved landing page is a correction somebody must
    be able to make. The previous value lives in the audit trail.
    """
    destination = await services.content_assets.update_destination(
        actor=actor,
        request_id=request_id,
        command=UpdateDestinationCommand(
            destination_id=destination_id, label=body.label, url=body.url, note=body.note
        ),
    )
    if destination.content_id != content_id:
        raise PrNotFoundError(
            "Không tìm thấy link sản phẩm này của nội dung.",
            details={"content_id": str(content_id), "destination_id": str(destination_id)},
        )
    return ContentDestinationResponse.from_row(destination)


@router.delete(
    "/contents/{content_id}/destinations/{destination_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a product or landing page",
    responses=_WRITE_RESPONSES,
)
async def delete_content_destination(
    content_id: uuid.UUID,
    destination_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> Response:
    """Detach one destination link. The page it points at is untouched."""
    rows = await services.queries.content_destinations(actor=actor, content_id=content_id)
    if not any(row.id == destination_id for row in rows):
        raise PrNotFoundError(
            "Không tìm thấy link sản phẩm này của nội dung.",
            details={"content_id": str(content_id), "destination_id": str(destination_id)},
        )
    await services.content_assets.delete_destination(
        actor=actor, request_id=request_id, destination_id=destination_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/contents/{content_id}/ai-review",
    response_model=AiReviewStateResponse,
    summary="The current AI review execution and its result",
    responses=_NOT_FOUND,
)
async def ai_review_state(
    content_id: uuid.UUID, actor: CurrentActorDep, services: PrServicesDep
) -> AiReviewStateResponse:
    """What the AI review panel shows, and what it polls. **Reads only.**

    Step 1F. Scoped to the draft that is current *now*: a finished run about a
    draft that has since been rewritten is not the state of the new one, so it
    is not returned as such. That is the read-side half of "an AI review of
    version N is never an answer about version N+1".

    ``can_retry`` is decided here and not in the browser. Pressing a retry the
    server did not offer is refused by :func:`retry_ai_review` regardless.
    """
    detail = await services.queries.get_content(actor=actor, content_id=content_id)
    version = detail.current_version
    run = (
        await services.ai_review_runs.latest_for_content(content_id, content_version_id=version.id)
        if version is not None
        else None
    )
    review = (
        await services.ai_reviews.latest_gating_review(content_id, version_no=version.version_no)
        if version is not None
        else None
    )
    active = run is not None and run.status in ACTIVE_RUN_STATUSES
    packs, citations = await _policy_grounding(services, run, review)
    return AiReviewStateResponse(
        run=AiReviewRunResponse.from_row(run) if run is not None else None,
        review=AiReviewResponse.from_row(review) if review is not None else None,
        active=active,
        policy_packs=packs,
        policy_citations=citations,
        can_retry=(
            version is not None
            and not active
            and detail.content.workflow_stage is PrWorkflowStage.AI_REVIEW
            and await services.ai_reviews.may_request(actor)
        ),
    )


@router.post(
    "/contents/{content_id}/ai-review/retry",
    response_model=AiReviewStateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask for the AI review again",
    responses=_WRITE_RESPONSES,
)
async def retry_ai_review(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> AiReviewStateResponse:
    """Queue another attempt after a failure. **Nothing is reviewed here.**

    202, not 201: this route writes a queue row and returns. The provider call
    happens on the worker, and a browser that got a 201 would reasonably expect
    a result in the body.

    Four refusals, none of them this route's own invention:

    * the read permission and ``SCRIPT_REVIEW`` are checked by the services -
      recording an AI review has always taken that permission, and asking for
      one is the same authority. **No new capability was added**;
    * the content must still be at ``AI_REVIEW``, or there is nothing this
      review could gate;
    * there must be a draft to pin;
    * an active run means the answer is already coming, and the partial unique
      index refuses a second one anyway.
    """
    detail = await services.queries.get_content(actor=actor, content_id=content_id)
    services.ai_reviews.require_may_request(actor)

    if detail.content.workflow_stage is not PrWorkflowStage.AI_REVIEW:
        raise PrWorkflowTransitionError(
            "Nội dung này không ở bước AI review.",
            details={
                "content_id": str(content_id),
                "current": detail.content.workflow_stage.value,
                "required": PrWorkflowStage.AI_REVIEW.value,
            },
        )
    version = detail.current_version
    if version is None:
        raise PrValidationError(
            "Nội dung này chưa có bản nháp nào để review.",
            details={"content_id": str(content_id)},
        )

    queued = await services.ai_review_runs.enqueue(
        content_id=content_id,
        content_version_id=version.id,
        trigger=PrAiReviewTrigger.MANUAL_RETRY,
        requested_by_user_id=actor.user_id,
    )
    if queued is None:
        raise PrConflictError(
            "Đã có một lượt AI review đang chạy cho bản nháp này.",
            details={"content_id": str(content_id)},
        )
    await record_pr_event(
        services.audit,
        request_id=request_id,
        actor=actor,
        action=AuditAction.PR_AI_REVIEW_REQUESTED,
        entity_type="pr_ai_review_run",
        entity_id=queued.id,
        after={
            "content_id": str(content_id),
            "content_code": detail.content.code,
            "content_version_id": str(version.id),
            "trigger": queued.trigger.value,
        },
    )
    return await ai_review_state(content_id, actor, services)


async def _policy_grounding(
    services: PrServicesDep,
    run: object | None,
    review: object | None,
) -> tuple[list[PolicyPackRefResponse], list[PolicyRuleCitationResponse]]:
    """The packs one run pinned, and the rules its findings cited.

    An empty list is a real answer and means **ungrounded**: a legacy pre-1F.1
    run, or an unsupported platform. Nothing is invented for those - attaching
    today's pack to a review that never saw it would be fabricating history.

    Citations are resolved from the pinned packs, so the source link a person
    follows is stored provenance rather than an address the client assembled.
    """
    if run is None:
        return [], []
    pins = await services.ai_review_runs.pinned_packs(run.id)  # type: ignore[attr-defined]
    packs: list[PolicyPackRefResponse] = []
    rules_by_id: dict[str, PolicyRuleCitationResponse] = {}
    for pin in pins:
        pack = await services.policy_packs.get_pack(pin.policy_pack_id)
        if pack is None:
            continue
        packs.append(
            PolicyPackRefResponse(
                platform_code=pack.platform_code,
                distribution_mode=pack.distribution_mode.value,
                pack_label=pack.label,
                pack_version=pack.version,
            )
        )
        for rule in await services.policy_packs.rules_for(pack.id):
            rules_by_id[rule.rule_id] = PolicyRuleCitationResponse(
                rule_id=rule.rule_id,
                title=rule.title,
                source_url=rule.source_url,
                section_path=rule.section_path,
            )

    cited: list[PolicyRuleCitationResponse] = []
    for issue in getattr(review, "issues", None) or []:
        if isinstance(issue, dict):
            for rule_id in issue.get("policy_rule_ids") or []:
                found = rules_by_id.get(str(rule_id))
                if found is not None and found not in cited:
                    cited.append(found)
    return packs, cited


@router.get(
    "/contents/{content_id}/approvals",
    response_model=list[ApprovalEventResponse],
    summary="Decision history",
    responses=_NOT_FOUND,
)
async def approval_history(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 100,
) -> list[ApprovalEventResponse]:
    """Every human decision on this item. Append-only, so nothing is missing."""
    await services.queries.get_content(actor=actor, content_id=content_id)
    rows = await services.approvals.history(content_id, limit=_capped(limit))
    return [ApprovalEventResponse.from_row(row) for row in rows]


# --- Publications -----------------------------------------------------------


@router.get(
    "/contents/{content_id}/publications",
    response_model=list[PublicationResponse],
    summary="What went out",
    responses=_NOT_FOUND,
)
async def list_publications(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> list[PublicationResponse]:
    """Recorded publications for one item."""
    await services.queries.get_content(actor=actor, content_id=content_id)
    rows = await services.publications.list_publications(content_id)
    # Step 1F.2.3f.2. Answered per row, on the server. Ownership is a property of
    # the row - a contributor may fix the posting they recorded and not the one
    # beside it - so a content-level flag could only have been wrong for half the
    # list, and a client comparing ``publisher_user_id`` to its session id would
    # be re-deriving a rule the server already owns.
    may_reverse = await services.publications.may_reverse_publication(actor)
    return [
        PublicationResponse.from_row(
            row,
            can_edit=await services.publications.may_edit_publication(actor, row),
            can_reverse=may_reverse and row.status.value != "REVERSED",
        )
        for row in rows
    ]


@router.post(
    "/contents/{content_id}/publications",
    response_model=ContentDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a publication",
    responses=_WRITE_RESPONSES,
)
async def register_publication(
    content_id: uuid.UUID,
    body: RegisterPublicationRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentDetailResponse:
    """Record that something was published. **Nothing is posted anywhere.**

    No Meta, TikTok or Google call happens on this path. It writes what a person
    already did, and may advance the item's stage as a consequence - which is the
    service's decision, made from the workflow matrix.

    Step 1F.2.3f: the body names **which produced file** went out - exactly one
    of ``production_submission_id`` and ``derivative_id`` - and the channel need
    not be one of the content's planned targets. Both rules are the service's;
    the router parses and delegates.
    """
    await services.publications.register_publication(
        actor=actor,
        request_id=request_id,
        command=RegisterPublicationCommand(
            content_id=content_id,
            channel_id=body.channel_id,
            published_at=body.published_at,
            production_submission_id=body.production_submission_id,
            derivative_id=body.derivative_id,
            platform_post_id=body.platform_post_id,
            url=body.url,
            note=body.note,
            publisher_user_id=body.publisher_user_id or actor.user_id,
        ),
    )
    return await _content_detail(actor, services, content_id)


@router.patch(
    "/contents/{content_id}/publications/{publication_id}",
    response_model=PublicationResponse,
    summary="Correct a recorded publication",
    responses=_WRITE_RESPONSES,
)
async def update_publication(
    content_id: uuid.UUID,
    publication_id: uuid.UUID,
    body: UpdatePublicationRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> PublicationResponse:
    """Fix the live link, the instant or the note on a publication already on record.

    Step 1F.2.3f.1. **The channel and the produced output are not here**, and
    that is the design: those three columns are what the row *means*, and
    rewriting one silently turns a record of one event into a record of a
    different event nobody witnessed. A row entered against the wrong channel is
    reversed and re-entered, which leaves both facts visible.

    The URL is validated by the same domain function that validates it on the way
    in - see :mod:`meobot.domain.pr.assets`. Nothing is fetched.
    """
    publication = await services.publications.update_publication(
        actor=actor,
        request_id=request_id,
        command=UpdatePublicationCommand(
            publication_id=publication_id,
            url=body.url,
            published_at=body.published_at,
            note=body.note,
        ),
    )
    if publication.content_id != content_id:
        raise PrNotFoundError(
            "Không tìm thấy bài đăng này của nội dung.",
            details={"content_id": str(content_id), "publication_id": str(publication_id)},
        )
    return PublicationResponse.from_row(publication)


@router.post(
    "/contents/{content_id}/publications/{publication_id}/reverse",
    response_model=ReversePublicationResponse,
    summary="Take back a publication recorded in error",
    responses=_WRITE_RESPONSES,
)
async def reverse_publication(
    content_id: uuid.UUID,
    publication_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ReversePublicationResponse:
    """Mark one publication as entered in error. **Nothing is deleted.**

    Step 1F.2.3f.1. The row stays in the history with
    ``status = REVERSED`` - publication history is operational evidence, and a
    correction is not an erasure. Whether the content goes back to *Sẵn sàng
    đăng* is the **server's** decision, taken from five conditions the service
    documents, and the response says whether it did and why not.

    A ``POST`` to a sub-resource rather than a ``DELETE``, because nothing is
    being removed and a ``DELETE`` would say otherwise to every reader of the
    route table.
    """
    rows = await services.publications.list_publications(content_id)
    if not any(row.id == publication_id for row in rows):
        raise PrNotFoundError(
            "Không tìm thấy bài đăng này của nội dung.",
            details={"content_id": str(content_id), "publication_id": str(publication_id)},
        )
    outcome = await services.publications.reverse_publication(
        actor=actor, request_id=request_id, publication_id=publication_id
    )
    content = await services.content.require_content(content_id)
    return ReversePublicationResponse(
        publication=PublicationResponse.from_row(outcome.publication),
        stage_reverted=outcome.stage_reverted,
        reason=outcome.reason,
        workflow_stage=content.workflow_stage.value,
    )


# --- Tasks ------------------------------------------------------------------


@router.get("/tasks", response_model=list[TaskSummaryResponse], summary="List tasks")
async def list_tasks(
    actor: CurrentActorDep,
    services: PrServicesDep,
    content_id: Annotated[uuid.UUID | None, Query()] = None,
    task_status: Annotated[str | None, Query(alias="status")] = None,
    open_only: Annotated[bool, Query()] = False,
    overdue: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
) -> list[TaskSummaryResponse]:
    """Tasks, filtered. ``overdue=true`` is its own query, not a client-side sort.

    "Overdue" means an unfinished task past its deadline, and which statuses count
    as finished is the task matrix's business - so the service answers it. Same
    for ``open_only``, which excludes ``DONE`` and ``CANCELLED``.

    There is deliberately no ``assignee`` filter: ``PrQueryService.list_tasks``
    does not offer one, and adding a join here would be this layer inventing a
    query the application layer has not agreed to.
    """
    if overdue:
        rows: Sequence[object] = await services.queries.list_overdue_tasks(
            actor=actor, limit=_capped(limit)
        )
    else:
        rows = await services.queries.list_tasks(
            actor=actor,
            content_id=content_id,
            status=_enum(PrTaskStatus, task_status, field="status") if task_status else None,
            open_only=open_only,
            limit=_capped(limit),
        )
    return [TaskSummaryResponse.from_row(row) for row in rows]  # type: ignore[arg-type]


@router.post(
    "/tasks",
    response_model=TaskDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
    responses=_WRITE_RESPONSES,
)
async def create_task(
    body: CreateTaskRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> TaskDetailResponse:
    """Create a task at ``TODO``. Code allocated server-side."""
    task = await services.tasks.create_task(
        actor=actor,
        request_id=request_id,
        command=CreateTaskCommand(
            task_type=body.task_type,
            title=body.title,
            content_id=body.content_id,
            description=body.description,
            priority=(
                _enum(PrPriority, body.priority, field="priority")
                if body.priority
                else PrPriority.NORMAL
            ),
            deadline=body.deadline,
        ),
    )
    return await _task_detail(services, task.id)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskDetailResponse,
    summary="One task",
    responses=_NOT_FOUND,
)
async def get_task(
    task_id: uuid.UUID, actor: CurrentActorDep, services: PrServicesDep
) -> TaskDetailResponse:
    """A task and who is on it."""
    await services.queries.list_tasks(actor=actor, limit=1)
    return await _task_detail(services, task_id)


@router.post(
    "/tasks/{task_id}/assignments",
    response_model=TaskDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign somebody",
    responses=_WRITE_RESPONSES,
)
async def assign_task(
    task_id: uuid.UUID,
    body: AssignTaskRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> TaskDetailResponse:
    """Put a person on a task, by id."""
    await services.tasks.assign_user(
        actor=actor,
        request_id=request_id,
        task_id=task_id,
        user_id=body.user_id,
        assignment_role=_enum(PrTaskAssignmentRole, body.assignment_role, field="assignment_role"),
    )
    return await _task_detail(services, task_id)


@router.delete(
    "/tasks/{task_id}/assignments/{user_id}",
    response_model=TaskDetailResponse,
    summary="Take somebody off a task",
    responses=_WRITE_RESPONSES,
)
async def unassign_task(
    task_id: uuid.UUID,
    user_id: uuid.UUID,
    assignment_role: Annotated[str, Query()],
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> TaskDetailResponse:
    """Remove one assignment.

    The role is required because somebody can hold two on one task (doing it and
    reviewing it), and removing "the assignment" would be ambiguous.
    """
    await services.tasks.unassign_user(
        actor=actor,
        request_id=request_id,
        task_id=task_id,
        user_id=user_id,
        assignment_role=_enum(PrTaskAssignmentRole, assignment_role, field="assignment_role"),
    )
    return await _task_detail(services, task_id)


@router.post(
    "/tasks/{task_id}/status",
    response_model=TaskDetailResponse,
    summary="Move a task",
    responses=_WRITE_RESPONSES,
)
async def change_task_status(
    task_id: uuid.UUID,
    body: TaskStatusRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> TaskDetailResponse:
    """Change status. Legality is ``TASK_TRANSITIONS``' call, in the service."""
    await services.tasks.change_status(
        actor=actor,
        request_id=request_id,
        task_id=task_id,
        target=_enum(PrTaskStatus, body.status, field="status"),
        note=body.note,
    )
    return await _task_detail(services, task_id)


async def _task_detail(services: PrServicesDep, task_id: uuid.UUID) -> TaskDetailResponse:
    """Re-read one task and its assignments."""
    task = await services.tasks.require_task(task_id)
    return TaskDetailResponse(
        task=TaskSummaryResponse.from_row(task),
        assignments=[
            TaskAssignmentResponse.from_row(row)
            for row in await services.tasks.list_assignments(task_id)
        ],
    )


# --- Platforms --------------------------------------------------------------


@router.get("/platforms", response_model=list[PlatformResponse], summary="List platforms")
async def list_platforms(
    actor: CurrentActorDep,
    services: PrServicesDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> list[PlatformResponse]:
    """Platforms, by name, for the channel form's picker.

    Active only unless asked otherwise, and that is the service's decision
    rather than this route's - offering a retired platform in a picker would
    undo the retirement one channel at a time.
    """
    rows = await services.platforms.list_platforms(
        actor=actor, status=None if include_inactive else PrEntityStatus.ACTIVE
    )
    return [PlatformResponse.from_row(row) for row in rows]


@router.post(
    "/platforms",
    response_model=PlatformResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a platform",
    responses=_WRITE_RESPONSES,
)
async def create_platform(
    body: CreatePlatformRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> PlatformResponse:
    """Register a platform. ``PR_CHANNEL_MANAGE``, and the code is asked for.

    Unlike a channel, a platform's code is **not** allocated: it is the token
    Step 1F.1 compares against, so ``FACEBOOK`` has to be something somebody
    typed on purpose rather than something derived from a display name.
    """
    platform = await services.platforms.create_platform(
        actor=actor,
        request_id=request_id,
        command=CreatePlatformCommand(
            code=body.code,
            name=body.name,
            api_available=body.api_available,
            api_note=body.api_note,
        ),
    )
    return PlatformResponse.from_row(platform)


# --- Channels ---------------------------------------------------------------


@router.get("/channels", response_model=list[ChannelResponse], summary="List channels")
async def list_channels(
    actor: CurrentActorDep,
    services: PrServicesDep,
    brand_id: Annotated[uuid.UUID | None, Query()] = None,
    platform_id: Annotated[uuid.UUID | None, Query()] = None,
    channel_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ChannelResponse]:
    """Channels, filtered."""
    rows = await services.channels.list_channels(
        actor=actor,
        brand_id=brand_id,
        platform_id=platform_id,
        status=(_enum(PrChannelStatus, channel_status, field="status") if channel_status else None),
        limit=_capped(limit),
        offset=offset,
    )
    # Two queries for the whole page, and neither of them is per channel: one
    # for the platforms these channels sit on, so the picker can be told which
    # need a distribution mode, and one for the latest reading of each, so a
    # card can show its data badge. Step 1F.2.4a's rule is that a channel list
    # never touches metric history - a card carries the newest row and nothing
    # else.
    platforms = await _platform_identities(services, [row.platform_id for row in rows])
    summaries = await services.channel_metrics.summaries_for_channels([row.id for row in rows])
    responses: list[ChannelResponse] = []
    for row in rows:
        identity = platforms.get(row.platform_id)
        responses.append(
            ChannelResponse.from_row(
                row,
                identity[0] if identity is not None else None,
                identity[1] if identity is not None else None,
                summaries.get(row.id),
            )
        )
    return responses


async def _platform_identities(
    services: PrServicesDep, platform_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str]]:
    """``platform_id`` -> ``(code, name)``, in one query.

    The code is what Step 1F.1 matches policy on and what Step 1F.2.4a maps onto
    the canonical platform badge; the name is what a channel on a platform
    *outside* the canonical six is shown as, so "Chưa xác định" is never the
    only thing on screen.
    """
    unique = set(platform_ids)
    if not unique:
        return {}
    result = await services.session.execute(
        select(PrPlatform.id, PrPlatform.code, PrPlatform.name).where(PrPlatform.id.in_(unique))
    )
    return {row[0]: (row[1], row[2]) for row in result.all()}


@router.post(
    "/channels",
    response_model=ChannelDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a channel",
    responses=_WRITE_RESPONSES,
)
async def create_channel(
    body: CreateChannelRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ChannelDetailResponse:
    """Create a channel. ``CH-nnnn`` allocated server-side."""
    channel = await services.channels.create_channel(
        actor=actor,
        request_id=request_id,
        command=CreateChannelCommand(
            name=body.name,
            platform_id=body.platform_id,
            category=_enum(PrChannelCategory, body.category, field="category"),
            brand_id=body.brand_id,
            tier=body.tier,
            external_id=body.external_id,
            url=body.url,
            handle=body.handle,
            started_at=body.started_at,
        ),
    )
    return await _channel_detail(actor, services, channel.id)


@router.get(
    "/channels/{channel_id}",
    response_model=ChannelDetailResponse,
    summary="One channel and its assignments",
    responses=_NOT_FOUND,
)
async def get_channel(
    channel_id: uuid.UUID, actor: CurrentActorDep, services: PrServicesDep
) -> ChannelDetailResponse:
    """A channel with who runs it."""
    return await _channel_detail(actor, services, channel_id)


@router.patch(
    "/channels/{channel_id}",
    response_model=ChannelDetailResponse,
    summary="Update a channel",
    responses=_WRITE_RESPONSES,
)
async def update_channel(
    channel_id: uuid.UUID,
    body: UpdateChannelRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ChannelDetailResponse:
    """Change a channel. The code cannot be changed - it is the identity."""
    await services.channels.update_channel(
        actor=actor,
        request_id=request_id,
        command=UpdateChannelCommand(
            channel_id=channel_id,
            name=body.name,
            category=(
                _enum(PrChannelCategory, body.category, field="category") if body.category else None
            ),
            brand_id=body.brand_id,
            platform_id=body.platform_id,
            tier=body.tier,
            external_id=body.external_id,
            url=body.url,
            handle=body.handle,
            status=(_enum(PrChannelStatus, body.status, field="status") if body.status else None),
        ),
    )
    return await _channel_detail(actor, services, channel_id)


@router.post(
    "/channels/{channel_id}/assignments",
    response_model=ChannelDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Put somebody on a channel",
    responses=_WRITE_RESPONSES,
)
async def assign_channel(
    channel_id: uuid.UUID,
    body: AssignChannelRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ChannelDetailResponse:
    """Assign for a closed date interval.

    Overlap is refused by ``meobot.domain.pr.assignments``, in the service. This
    route does no interval arithmetic - a second implementation of it would
    eventually disagree about whether the last day counts, and it does.
    """
    await services.channels.assign_user(
        actor=actor,
        request_id=request_id,
        channel_id=channel_id,
        user_id=body.user_id,
        assignment_role=_enum(
            PrChannelAssignmentRole, body.assignment_role, field="assignment_role"
        ),
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        is_primary=body.is_primary,
        allocation_percent=body.allocation_percent,
    )
    return await _channel_detail(actor, services, channel_id)


@router.post(
    "/channels/{channel_id}/assignments/{assignment_id}/close",
    response_model=ChannelDetailResponse,
    summary="End an assignment",
    responses=_WRITE_RESPONSES,
)
async def close_channel_assignment(
    channel_id: uuid.UUID,
    assignment_id: uuid.UUID,
    body: CloseAssignmentRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ChannelDetailResponse:
    """Close an assignment on a day, inclusive. The row stays - it is history."""
    await services.channels.close_assignment(
        actor=actor,
        request_id=request_id,
        assignment_id=assignment_id,
        effective_to=body.effective_to,
    )
    return await _channel_detail(actor, services, channel_id)


async def _channel_detail(
    actor: Actor, services: PrServicesDep, channel_id: uuid.UUID
) -> ChannelDetailResponse:
    """Re-read one channel with its assignments and its data badge.

    The badge is the same one-query projection the list uses, asked for one
    channel. The *panel* - current figures, trend, history - is a separate
    request to ``/metrics``, because it is paginated and a channel edit has no
    reason to re-send thirty readings.
    """
    detail = await services.queries.get_channel(actor=actor, channel_id=channel_id)
    summaries = await services.channel_metrics.summaries_for_channels([channel_id])
    return ChannelDetailResponse.from_detail(detail, summaries.get(channel_id))


# --- Channel metrics --------------------------------------------------------


@router.get(
    "/channels/{channel_id}/metrics",
    response_model=ChannelMetricsResponse,
    summary="A channel's numbers, now and before",
    responses=_NOT_FOUND,
)
async def get_channel_metrics(
    channel_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    limit: Annotated[int, Query(ge=1, le=MAX_SNAPSHOT_PAGE)] = DEFAULT_SNAPSHOT_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ChannelMetricsResponse:
    """Latest, previous, trend and one bounded page of history, in one answer.

    There is no separate ``/metrics/latest``: the current figure is the first
    row of the same ordering the history uses, and a second endpoint returning
    it would be a second thing to keep in step.

    Reading is the ordinary PR read permission - whoever may see the channel may
    see what it measured. Writing is not; see the POST below.
    """
    return ChannelMetricsResponse.from_view(
        await services.channel_metrics.describe(
            actor=actor, channel_id=channel_id, limit=limit, offset=offset
        )
    )


@router.post(
    "/channels/{channel_id}/metrics",
    response_model=ChannelMetricsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Write down a channel's numbers",
    responses=_WRITE_RESPONSES,
)
async def record_channel_metrics(
    channel_id: uuid.UUID,
    body: RecordChannelMetricsRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ChannelMetricsResponse:
    """Append one hand-entered reading. ``PR_CHANNEL_MANAGE``.

    **Manual only, and manual is not a claim the caller makes.** The command
    built here carries no source and no recorder: the service writes
    ``MANUAL`` and the authenticated actor, so there is no request that can
    record a reading as if a platform API had produced it. The body model
    forbids the fields outright, and the service would ignore them anyway.

    Appends. It never updates the reading before it - a wrong number is
    corrected by recording the right one, and both stay.

    Returns the whole refreshed panel rather than the created row, because the
    thing that changed is *what the current figures are*, and the caller would
    have to fetch that immediately afterwards.
    """
    await services.channel_metrics.record_manual_snapshot(
        actor=actor,
        request_id=request_id,
        command=RecordChannelMetricsCommand(
            channel_id=channel_id,
            captured_at=body.captured_at,
            **{
                name: getattr(body, name)
                for name in RecordChannelMetricsCommand.__dataclass_fields__
                if name not in {"channel_id", "captured_at", "extra_metrics"}
            },
            extra_metrics=body.extra_metrics,
        ),
    )
    return ChannelMetricsResponse.from_view(
        await services.channel_metrics.describe(actor=actor, channel_id=channel_id)
    )


# --- Channel connections (Step 1F.2.4b) -------------------------------------


def _connection_redirect(settings: Settings, *, status: str, channel_id: str = "") -> str:
    """Where the OAuth callback sends the browser.

    **Built from configuration, never from the request.** There is no ``next``
    or ``return_to`` parameter anywhere in this flow, because an open redirect
    on an endpoint that has just completed an authorization is how a consent
    screen becomes a phishing step. The destination is always this deployment's
    own channel page, and the only thing the query string carries is a short
    status token the panel turns into a Vietnamese sentence.
    """
    base = settings.web_base_url.strip().rstrip("/") or ""
    query = urlencode({"connection": status, **({"channel": channel_id} if channel_id else {})})
    return f"{base}/pr/channels?{query}"


@router.get(
    "/channels/{channel_id}/connection",
    response_model=ChannelConnectionStateResponse,
    summary="A channel's connector state",
    responses=_NOT_FOUND,
)
async def get_channel_connection(
    channel_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> ChannelConnectionStateResponse:
    """What the connection panel draws, whether or not one exists.

    Answered for every channel, including those on platforms with no connector:
    "unsupported", "supported but not configured here" and "supported and
    unused" are three different screens, and a browser must not tell them apart
    by matching on a platform string.
    """
    detail = await services.queries.get_channel(actor=actor, channel_id=channel_id)
    platform = platform_from_code(detail.platform_code)
    connection = None
    if connector_supports(platform):
        assert platform is not None
        connection = await services.channel_connections.get_live_connection(
            channel_id, provider=platform
        )
    days = None
    if connection is not None:
        days = stale_days(connection.last_sync_succeeded_at, now=utcnow())
    return ChannelConnectionStateResponse(
        channel_id=channel_id,
        supported=connector_supports(platform),
        configured=_connector_configured(services.settings, platform),
        provider=platform.value if connector_supports(platform) and platform else None,
        provider_label=(channel_platform_label(platform) if connector_supports(platform) else None),
        connection=(
            ChannelConnectionResponse.from_row(connection, days_since_success=days)
            if connection is not None
            else None
        ),
        can_manage_connection=detail.can_edit_channel,
        auto_sync_label="Hàng ngày" if services.settings.pr_channel_sync_enabled else None,
    )


def _connector_configured(settings: Settings, platform: PrChannelPlatform | None) -> bool:
    """Whether *this deployment* has set the connector for ``platform`` up.

    Separate from "is it supported": a platform outside
    :data:`~meobot.domain.pr.channel_connections.CONNECTABLE_PLATFORMS` has no
    connector at all, while a Facebook channel on a deployment with no Meta app
    has one that an operator has not configured. Those need different sentences.

    Step 1F.2.9 added the TikTok branch, and its absence was a real bug rather
    than a gap: this function's ``return False`` tail meant a TikTok channel on a
    fully configured deployment was told *"Cấu hình TikTok chưa sẵn sàng"* and
    had its "Kết nối TikTok" button disabled - the panel disables the control on
    ``configured``. Step 1F.2.6 shipped the whole connector behind a button
    nobody could press.

    The tail stays ``False`` on purpose: it is what a platform with no connector
    gets, and it must not become ``True`` by default the next time somebody adds
    one.
    """
    if platform in {PrChannelPlatform.FACEBOOK, PrChannelPlatform.INSTAGRAM}:
        return settings.meta_connector_enabled
    if platform is PrChannelPlatform.YOUTUBE:
        return settings.youtube_connector_enabled
    if platform is PrChannelPlatform.TIKTOK:
        return settings.tiktok_connector_enabled
    return False


def _require_provider(provider: str) -> PrChannelPlatform:
    """The path's provider segment, as a platform that actually has a connector.

    ``facebook`` / ``instagram`` / ``youtube`` in a URL, refused here if it is
    anything else - so an unimplemented platform cannot be reached by typing its
    name into a path.
    """
    platform = platform_from_code(provider)
    if platform is None or not connector_supports(platform):
        raise PrValidationError(
            "Đồng bộ API tự động chưa được hỗ trợ cho nền tảng này.",
            details={"reason": "connector_unsupported", "provider": provider},
        )
    return platform


@router.post(
    "/channels/{channel_id}/connections/{provider}/authorize",
    response_model=ConnectionAuthorizationResponse,
    summary="Start connecting a channel to its platform",
    responses=_WRITE_RESPONSES,
)
async def authorize_connection(
    channel_id: uuid.UUID,
    provider: str,
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> ConnectionAuthorizationResponse:
    """Mint a single-use state and return the platform's consent URL.

    One route for every connector, because the flow is identical: capability
    check, platform check, state, URL. ``PR_CHANNEL_MANAGE``, and the channel's
    own platform must match the provider in the path - a TikTok channel is
    refused here on the server, and so is a Facebook channel pointed at
    Instagram's flow, not by a button that was not drawn.

    The same endpoint serves reconnect: an authorization over a live connection
    updates it in place, which is what keeps the channel's history attached.
    """
    start = await services.channel_connections.start_authorization(
        actor=actor, channel_id=channel_id, provider=_require_provider(provider)
    )
    return ConnectionAuthorizationResponse(
        authorization_url=start.authorization_url, expires_at=start.expires_at
    )


@router.get(
    "/channels/{channel_id}/connections/accounts",
    response_model=AccountChoicesResponse,
    summary="Accounts this authorization could bind",
    responses=_NOT_FOUND,
)
async def list_connection_accounts(
    channel_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> AccountChoicesResponse:
    """The Pages or Instagram accounts a parked connection can choose from.

    Recomputed from the platform on every call rather than replayed from
    anything the browser holds, so the list is what the authorization can reach
    *now*. Ids and names only - the access tokens that came back beside them
    never leave the server.
    """
    return AccountChoicesResponse.from_choices(
        await services.channel_connections.list_account_choices(actor=actor, channel_id=channel_id)
    )


@router.post(
    "/channels/{channel_id}/connections/select",
    response_model=ConnectionOutcomeResponse,
    summary="Bind the chosen account",
    responses=_WRITE_RESPONSES,
)
async def select_connection_account(
    channel_id: uuid.UUID,
    body: SelectAccountRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ConnectionOutcomeResponse:
    """Finish a Meta connection by binding one Page or Instagram account.

    The submitted id is verified against a discovery result computed **here**,
    from the stored credential, at this moment - not against a list the browser
    was handed earlier. An arbitrary Page id gets a refusal, and so does one the
    authorizing account lost access to in the meantime.
    """
    return ConnectionOutcomeResponse.from_outcome(
        await services.channel_connections.select_account(
            actor=actor,
            request_id=request_id,
            channel_id=channel_id,
            account_id=body.account_id,
        )
    )


@oauth_callback_router.get(
    "/channels/connections/meta/callback",
    summary="Where Meta sends the browser back",
    include_in_schema=False,
)
async def meta_connection_callback(
    session_actor: OptionalActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
    state: Annotated[str | None, Query()] = None,
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Meta's redirect target. Identical handling to Google's - see below.

    A separate path because Meta registers one Valid OAuth Redirect URI per app
    and it must match exactly; the **provider is still read from the state row**,
    never from this path, so a Facebook state cannot finalise an Instagram
    connection and neither can a crafted URL.
    """
    return await _finish_oauth_callback(
        session_actor=session_actor,
        services=services,
        request_id=request_id,
        state=state,
        code=code,
        error=error,
    )


@oauth_callback_router.get(
    "/channels/connections/tiktok/callback",
    summary="Where TikTok sends the browser back",
    include_in_schema=False,
)
async def tiktok_connection_callback(
    session_actor: OptionalActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
    state: Annotated[str | None, Query()] = None,
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """TikTok's redirect target. Identical handling to Google's - see below.

    A separate path because TikTok registers Redirect URIs per app and each must
    match exactly; the **provider is still read from the state row**, never from
    this path, so a crafted URL cannot finalise a connection for a platform it
    names.

    TikTok also sends ``error_description`` and ``scopes`` on some callbacks.
    Neither is read: the first is provider prose that would end up on a
    Vietnamese screen, and the second is not evidence - the authoritative
    granted-scope list is the one the **token response** carries, which arrives
    after the code has been exchanged server-side and cannot be edited by
    whoever composed this URL.
    """
    return await _finish_oauth_callback(
        session_actor=session_actor,
        services=services,
        request_id=request_id,
        state=state,
        code=code,
        error=error,
    )


@oauth_callback_router.get(
    "/channels/connections/youtube/callback",
    summary="Where Google sends the browser back",
    include_in_schema=False,
)
async def youtube_connection_callback(
    session_actor: OptionalActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
    state: Annotated[str | None, Query()] = None,
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Finish an authorization, then send the browser somewhere safe.

    **Nothing in this query string is trusted.** The channel is read from the
    state row TasksBot wrote when it started the flow, never from a parameter -
    which is what stops a crafted callback from binding an account to a channel
    of the caller's choosing. ``code`` is passed straight to the token exchange
    and is never logged: it is a bearer credential for a few seconds.

    Every outcome, success or refusal, ends in a redirect to this deployment's
    own channel page with a short status token. The browser is never shown a
    provider error payload, and no destination ever comes from the request.
    """
    return await _finish_oauth_callback(
        session_actor=session_actor,
        services=services,
        request_id=request_id,
        state=state,
        code=code,
        error=error,
    )


async def _finish_oauth_callback(
    *,
    session_actor: Actor | None,
    services: PrServicesDep,
    request_id: uuid.UUID,
    state: str | None,
    code: str | None,
    error: str | None,
) -> RedirectResponse:
    """The callback body every provider shares.

    One implementation, because the security properties must not differ by
    provider: the state decides the user, the channel *and* the provider, it is
    consumed before any network call, and every outcome lands on a redirect this
    deployment built.

    **No session is required to reach here.** The browser arrives by redirect
    from Google or Meta, so whether a TasksBot cookie is attached depends on
    cross-site cookie policy - and a flow already carrying a single-use,
    expiring, user-and-channel-bound state must not additionally depend on that.
    Requiring one produced a 401 on every valid Meta callback in production.

    A session that *does* arrive is passed down as a cross-check, and a mismatch
    is refused with the same generic failure as an unknown token.
    """
    settings = services.settings
    if error or not code or not state:
        # A denied consent is the ordinary case here, not an exception.
        return RedirectResponse(
            _connection_redirect(settings, status="denied"), status_code=status.HTTP_303_SEE_OTHER
        )
    try:
        outcome = await services.channel_connections.finish_authorization(
            request_id=request_id,
            state_token=state,
            code=code,
            session_actor=session_actor,
        )
    except (PrValidationError, PrPermissionDeniedError, PrNotFoundError):
        return RedirectResponse(
            _connection_redirect(settings, status="failed"), status_code=status.HTTP_303_SEE_OTHER
        )
    except PrConnectorNotConfiguredError:
        return RedirectResponse(
            _connection_redirect(settings, status="unconfigured"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if outcome.pending_accounts:
        # Consent worked and nothing is bound: the person manages several Pages
        # or Instagram accounts. The panel opens the chooser.
        return RedirectResponse(
            _connection_redirect(
                settings, status="choose", channel_id=str(outcome.connection.channel_id)
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        _connection_redirect(
            settings,
            status="rebound" if outcome.rebound_from_account_id else "connected",
            channel_id=str(outcome.connection.channel_id),
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/channels/{channel_id}/connections/finish",
    response_model=ConnectionOutcomeResponse,
    summary="Finish an authorization from the panel",
    responses=_WRITE_RESPONSES,
)
async def finish_connection(
    channel_id: uuid.UUID,
    body: FinishConnectionRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ConnectionOutcomeResponse:
    """The same exchange as the callback, for a client that handled the redirect.

    Exists so the panel can complete a flow and render the bound identity
    immediately - which is what makes a wrong-account authorization visible in
    the same breath as the success, rather than a discovery a week later. For
    Meta it returns the eligible accounts instead, and the panel opens a chooser.

    ``channel_id`` in the path is **not** what binds, and neither is any provider
    segment: the **state row** decides both the channel and the provider. A
    mismatch simply means the state wins, which is what stops a Facebook
    authorization from finalising an Instagram connection.
    """
    outcome = await services.channel_connections.finish_authorization(
        request_id=request_id,
        state_token=body.state,
        code=body.code,
        # First-party call from the panel, so the route requires a session and
        # passes it as the cross-check. The **state** still decides whose flow
        # this is, which is what keeps this route and the redirect callback
        # behaving identically.
        session_actor=actor,
    )
    return ConnectionOutcomeResponse.from_outcome(outcome)


@router.delete(
    "/channels/{channel_id}/connections/{provider}",
    response_model=ChannelConnectionResponse,
    summary="Disconnect a channel from its platform",
    responses=_WRITE_RESPONSES,
)
async def disconnect_connection(
    channel_id: uuid.UUID,
    provider: str,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ChannelConnectionResponse:
    """Revoke where the platform supports it, drop the stored secret, keep every
    reading.

    Metric history is untouched - manual and API alike. The connection row is
    kept too, marked disconnected, because "this was connected and then was not"
    is something somebody will want to read.
    """
    connection = await services.channel_connections.disconnect(
        actor=actor,
        request_id=request_id,
        channel_id=channel_id,
        provider=_require_provider(provider),
    )
    return ChannelConnectionResponse.from_row(connection)


@router.post(
    "/channels/{channel_id}/metrics/sync",
    response_model=ChannelConnectionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Sync this channel's numbers now",
    responses=_WRITE_RESPONSES,
)
async def sync_channel_metrics_now(
    channel_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ChannelConnectionResponse:
    """Enqueue one sync. ``PR_CHANNEL_MANAGE``, and ``202`` rather than ``200``.

    The Google round trip happens on the worker. Doing it inside this request
    would hold an HTTP connection open for seconds and time out on a bad day,
    for no benefit: the panel refetches the connection state either way, and the
    numbers land in the timeline whenever they land.

    The claim is taken **here**, before the task is dispatched and in this
    request's transaction, so a second press while the first sync is still
    running is refused rather than queued. Who pressed it is recorded in the
    audit trail - never on the metric row, which has no human author.
    """
    connection = await services.channel_sync.request_sync(
        actor=actor, request_id=request_id, channel_id=channel_id
    )
    claimed = await services.channel_sync.claim(connection.id)
    if not claimed:
        raise PrConflictError(
            "Kênh này đang được đồng bộ. Hãy đợi lần chạy hiện tại kết thúc.",
            details={"channel_id": str(channel_id)},
        )
    _enqueue_channel_sync(str(connection.id))
    return ChannelConnectionResponse.from_row(connection)


# --- The TikTok account panel (Step 1F.2.9) ---------------------------------


@router.get(
    "/channels/{channel_id}/connections/tiktok/overview",
    response_model=TikTokOverviewResponse,
    summary="A connected TikTok account, read live",
    responses=_NOT_FOUND,
)
async def tiktok_account_overview(
    channel_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    videos: Annotated[int, Query(ge=1, le=VIDEO_PAGE_SIZE)] = RECENT_VIDEO_PAGE,
    cursor: Annotated[int | None, Query(ge=0)] = None,
) -> TikTokOverviewResponse:
    """The account, its lifetime counters and one bounded page of its videos.

    A **live** call to TikTok, made in this request rather than on the worker,
    and that is the difference between this and ``/metrics/sync``. A metric
    snapshot is a durable reading nobody is waiting for; this is a picture
    somebody is looking at, it is worth two bounded round trips, and its cover
    image URLs expire within hours - there is nothing here to store.

    Precedent rather than a new pattern: ``/connections/accounts`` already makes
    a live provider call inside a request, for the same reason - a chooser has
    to show what the authorization can reach *now*.

    Reading is the ordinary PR read permission, the same one the metrics panel
    sits behind. ``videos`` and ``cursor`` are bounded by the connector: at most
    :data:`~meobot.integrations.tiktok.constants.VIDEO_PAGE_SIZE` rows per page,
    and the cursor is TikTok's own opaque token passed back verbatim.

    Carries **no credential**. See
    :class:`~meobot.api.schemas.pr.TikTokOverviewResponse`.
    """
    return TikTokOverviewResponse.from_view(
        await services.tiktok_account.describe(
            actor=actor, channel_id=channel_id, video_limit=videos, cursor=cursor
        )
    )


@router.post(
    "/channels/{channel_id}/connections/tiktok/refresh",
    response_model=TikTokOverviewResponse,
    summary="Re-read this TikTok account now",
    responses=_WRITE_RESPONSES,
)
async def refresh_tiktok_account(
    channel_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
    videos: Annotated[int, Query(ge=1, le=VIDEO_PAGE_SIZE)] = RECENT_VIDEO_PAGE,
) -> TikTokOverviewResponse:
    """ "Đồng bộ lại". ``PR_CHANNEL_MANAGE``, and ``200`` rather than ``202``.

    Two things behind one press, and the status code is the honest one for the
    part a person can see:

    * the **existing** sync infrastructure is asked for a metric snapshot -
      the same capability check, the same audit action, the same conditional
      UPDATE claim and the same Celery task ``/metrics/sync`` uses. No second
      sync system;
    * the account and its videos are re-read live and returned, which is what
      makes the press visibly do something inside one HTTP response.

    ``200`` because the returned body is complete and current. The snapshot the
    worker will append is the ``202`` half, and
    :attr:`~meobot.api.schemas.pr.TikTokOverviewResponse.sync_requested` reports
    whether it was actually handed over - it is ``false`` when a sync was
    already in flight, which is not an error and must not be raised as one: the
    picture on screen was still refreshed.

    **No ConfirmDialog.** Re-reading numbers changes no business state, destroys
    nothing and is not a decision anybody can regret, so a confirmation in front
    of it would be a click that means nothing - and would teach people to click
    through the dialogs that do mean something.
    """
    view = await services.tiktok_account.refresh(
        actor=actor, request_id=request_id, channel_id=channel_id, video_limit=videos
    )
    if view.sync_requested:
        # Claimed here, in this request's transaction, exactly as
        # ``sync_channel_metrics_now`` does - and dispatched from the route
        # rather than the service, because a PR service that imported a Celery
        # task is a PR service that could dispatch one. An architecture test
        # keeps them free of both.
        _enqueue_channel_sync(str(view.connection.id))
    return TikTokOverviewResponse.from_view(view)


def _enqueue_channel_sync(connection_id: str) -> None:
    """Hand one claimed connection to the worker.

    Imported inside the function so the API container does not import the Celery
    task module - and therefore the whole task graph - at startup. The same
    reason the AI review routes do it.
    """
    from meobot.tasks.pr_channel_sync import sync_channel_metrics

    sync_channel_metrics.apply_async(
        kwargs={"connection_id": connection_id, "trigger": PrChannelSyncTrigger.MANUAL.value}
    )


# --- Capabilities -----------------------------------------------------------


@router.get(
    "/capabilities",
    response_model=list[CapabilityGrantResponse],
    summary="Who may approve what, and over which content and channels",
)
async def list_capabilities(
    actor: CurrentActorDep,
    services: PrServicesDep,
    capability: Annotated[str | None, Query()] = None,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[CapabilityGrantResponse]:
    """Active grants, optionally for one gate or one person.

    Only the three grant-backed review capabilities ever appear here. The other
    ten come from the role, are not grants, and cannot be handed out - so
    listing them as if they could would misdescribe the model.

    One query for the whole page, scope included. Step 1F.2.7: a permissions
    screen that showed *who* and *which gate* but not *over what* would be
    describing a grant this module no longer issues.
    """
    return [
        CapabilityGrantResponse.from_grant(grant)
        for grant in await services.queries.approval_grants(
            actor=actor,
            capability=_enum(PrCapability, capability, field="capability") if capability else None,
            user_id=user_id,
        )
    ]


@router.post(
    "/capabilities/grant",
    response_model=CapabilityGrantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a scoped approval right",
    responses=_WRITE_RESPONSES,
)
async def grant_capability(
    body: GrantCapabilityRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> CapabilityGrantResponse:
    """Grant one of the three review capabilities, over a scope.

    Guarded by ``user.role.manage`` inside ``PrCapabilityService`` - and by the
    fact that no capability guards itself, so nobody can grant their way into
    being able to grant, and a person who holds a grant cannot widen their own.

    The grant is **additive**: within its scope it authorises regardless of the
    holder's role, and it changes no role and no permission. Outside its scope
    it does nothing at all - see :mod:`meobot.domain.pr.grants`.
    """
    row = await services.capabilities.grant(
        actor=actor,
        request_id=request_id,
        user_id=body.user_id,
        capability=_enum(PrCapability, body.capability, field="capability"),
        scope=_grant_scope(body.scope),
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        note=body.note,
    )
    return CapabilityGrantResponse.from_row(row)


@router.post(
    "/capabilities/revoke",
    response_model=CapabilityGrantResponse,
    summary="Withdraw a grant, effective immediately",
    responses=_WRITE_RESPONSES,
)
async def revoke_capability(
    body: RevokeCapabilityRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> CapabilityGrantResponse:
    """Stamp the grant revoked. The row is kept - somebody held this once.

    Takes effect on the next request: authorization reads ``revoked_at``, which
    is an instant rather than a date, so nobody keeps approving until midnight.
    """
    row = await services.capabilities.revoke(
        actor=actor,
        request_id=request_id,
        grant_id=body.grant_id,
        user_id=body.user_id,
        capability=_enum(PrCapability, body.capability, field="capability")
        if body.capability
        else None,
        effective_to=body.effective_to,
    )
    return CapabilityGrantResponse.from_row(row)


__all__: list[str] = ["router"]
