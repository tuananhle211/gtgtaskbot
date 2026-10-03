"""Request and response bodies for the PR web admin API.

Step 1E. Every one of these is a hand-written Pydantic model, and that is the
point: **no ORM row is ever serialised directly.** A response built from
``PrContentItem.__dict__`` would leak whatever column somebody adds next - and
in this schema that includes ``script_text``, approval comments and grant notes.
An explicit model means a new column appears in the API only when somebody
decides it should.

Reading direction
-----------------

``*Request`` bodies come in, ``*Response`` bodies go out, and the ``from_*``
classmethods are the only place a domain object turns into either. They are
deliberately dumb - field copies and enum-to-string - because anything cleverer
would be a business rule living in the transport layer.

Enums cross as their **values** (``"AI_REVIEW"``, ``"PASS_WITH_WARNINGS"``),
matching what the database stores and what the Telegram layer says. The
frontend renders Vietnamese labels from those codes; it never invents a stage.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.pr_action_service import PrAvailableAction
from meobot.application.pr_bulk_approval_service import BulkApprovalOutcome
from meobot.application.pr_bulk_archive_service import BulkArchiveOutcome
from meobot.application.pr_capability_service import PrCapabilityGrant
from meobot.application.pr_channel_connection_service import AccountChoices, ConnectionOutcome
from meobot.application.pr_channel_metrics_service import (
    ChannelMetricsSummary,
    ChannelMetricsView,
    SnapshotView,
)
from meobot.application.pr_content_asset_service import DerivativeView
from meobot.application.pr_content_comment_service import CommentView, ContentCommentPage
from meobot.application.pr_production_service import ProductionState
from meobot.application.pr_query_service import (
    ApprovableSelection,
    ArchiveCandidates,
    ChannelDetail,
    ContentDetail,
    ContentPage,
    ContentReviewContext,
)
from meobot.application.pr_tiktok_account_service import TikTokAccountView
from meobot.db.models.pr import (
    PrApprovalEvent,
    PrBrand,
    PrChannel,
    PrChannelAssignment,
    PrContentItem,
    PrContentTarget,
    PrPlatform,
    PrTask,
    PrTaskAssignment,
)
from meobot.db.models.pr_ai_review import PrAiReview
from meobot.db.models.pr_ai_review_run import PrAiReviewRun
from meobot.db.models.pr_authorization import PrUserCapability
from meobot.db.models.pr_channel_connection import PrChannelConnection
from meobot.db.models.pr_content_asset import PrContentDerivative, PrContentDestination
from meobot.db.models.pr_content_resource import PrContentResource
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_reporting import PrChannelMetricSnapshot, PrPublication
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.domain.identity.models import Actor
from meobot.domain.pr.assets import is_link_location as is_link_asset_location
from meobot.domain.pr.channel_analytics import (
    ChannelAnalytics,
    MetricCapabilities,
    MetricChange,
    TopPost,
    UnavailableMetric,
)
from meobot.domain.pr.channel_metrics import (
    MANUAL_METRIC_FIELDS,
    ChannelFollowerTrend,
    DiscoveredProviderAccount,
    PrChannelMetricsStatus,
    platform_from_code,
)
from meobot.domain.pr.grants import GrantScope, PrGrantScopeMode
from meobot.domain.pr.labels import (
    channel_platform_label,
    connection_state_label,
    field_availability_label,
    metric_source_label,
    metrics_status_label,
    sync_status_label,
    tiktok_scope_description,
    tiktok_scope_label,
)
from meobot.domain.pr.models import POLICY_GROUNDED_PLATFORM_CODES, PrDistributionMode
from meobot.domain.pr.policy import BULK_APPROVAL_MAX_ITEMS, BULK_ARCHIVE_MAX_ITEMS
from meobot.domain.pr.production import handoff_state, is_url_artifact
from meobot.domain.pr.reporting import is_active_publication
from meobot.domain.pr.resources import is_link_location
from meobot.integrations.tiktok.constants import TIKTOK_SCOPES
from meobot.integrations.tiktok.provider import TikTokVideoSummary

# --- Shared bits ------------------------------------------------------------


class _Body(BaseModel):
    """Base for incoming bodies.

    ``extra="forbid"`` on purpose, exactly as the Telegram tool argument models
    do it: a typo'd field name is a bug the caller should hear about, and a
    silently ignored ``"approve": true`` is the kind of bug that ships.
    """

    model_config = ConfigDict(extra="forbid")


class ErrorBody(BaseModel):
    """The ``{"error": {...}}`` envelope every failure uses."""

    code: str = Field(description="Stable machine code, e.g. `pr.workflow_transition`.")
    message: str = Field(description="Vietnamese sentence safe to show a person.")
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    """Wrapper so the frontend can branch on one shape for every failure."""

    error: ErrorBody


class ActorResponse(BaseModel):
    """Who the session says you are.

    ``capabilities`` is here so the UI can hide buttons nobody can use. It is
    **not** an authorization decision - the server re-checks every write inside
    the ``Pr*`` services, so a caller who edits this response, or posts straight
    to the endpoint, is refused just the same. Hiding a button is courtesy;
    refusing the request is the control.
    """

    user_id: uuid.UUID | None
    full_name: str
    role: str
    active: bool
    capabilities: list[str] = Field(default_factory=list)

    @classmethod
    def from_actor(cls, actor: Actor, capabilities: frozenset[Any]) -> ActorResponse:
        return cls(
            user_id=actor.user_id,
            full_name=actor.full_name,
            role=actor.role.value,
            active=actor.active,
            capabilities=sorted(capability.value for capability in capabilities),
        )


class PersonResponse(BaseModel):
    """A user, reduced to what a PR screen needs to show."""

    user_id: uuid.UUID
    full_name: str
    role: str


class BrandResponse(BaseModel):
    """A brand, reduced to what a picker and a card need.

    Step 1E.2.1. Three fields and no more: ``status`` is not here because the
    list route already decides which brands may be chosen, and a client that
    received the status would be one refactor away from filtering on it - which
    is the decision this response exists to keep on the server.

    The ``id`` is what a create request sends back. A person sees "Apexmed";
    the UUID travels underneath and is never something anybody types.
    """

    id: uuid.UUID
    code: str
    name: str

    @classmethod
    def from_row(cls, row: PrBrand) -> BrandResponse:
        return cls(id=row.id, code=row.code, name=row.name)


# --- Content ----------------------------------------------------------------


class ContentVersionResponse(BaseModel):
    """One immutable draft.

    ``script_text`` is included: this is the review screen's whole purpose. It
    is why the route behind it requires a session and a read permission rather
    than being open on the localhost port.
    """

    id: uuid.UUID
    version_no: int
    title: str
    topic: str | None
    hook: str | None
    brief: str | None
    script_text: str | None
    change_note: str | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: PrContentVersion) -> ContentVersionResponse:
        return cls(
            id=row.id,
            version_no=row.version_no,
            title=row.title,
            topic=row.topic,
            hook=row.hook,
            brief=row.brief,
            script_text=row.script_text,
            change_note=row.change_note,
            created_by_user_id=row.created_by_user_id,
            created_at=row.created_at,
        )


class ContentTargetResponse(BaseModel):
    """A channel this item is planned for.

    ``channel_code`` and ``channel_name`` are filled when the caller loaded the
    channels - the detail route does, in one ``IN`` query. They are ``None``
    elsewhere rather than absent, so a client renders the name it has and the
    channel id it always has, and never a UUID where a name belongs.
    """

    id: uuid.UUID
    channel_id: uuid.UUID
    target_publish_at: datetime | None
    status: str
    adaptation_note: str | None
    channel_code: str | None = None
    channel_name: str | None = None
    #: Step 1F.1. ``UNSPECIFIED`` on a Facebook or TikTok target blocks AI
    #: review until somebody says which it is.
    distribution_mode: str = PrDistributionMode.UNSPECIFIED.value
    #: Whether this target's platform is policy-grounded, so the panel knows
    #: whether to ask for a mode at all. The server decides; the browser does
    #: not match on platform codes.
    policy_grounded_platform: bool = False
    platform_code: str | None = None

    @classmethod
    def from_row(
        cls,
        row: PrContentTarget,
        channel: PrChannel | None = None,
        platform_code: str | None = None,
    ) -> ContentTargetResponse:
        return cls(
            id=row.id,
            channel_id=row.channel_id,
            target_publish_at=row.target_publish_at,
            status=row.status.value,
            adaptation_note=row.adaptation_note,
            channel_code=channel.code if channel else None,
            channel_name=channel.name if channel else None,
            distribution_mode=row.distribution_mode.value,
            policy_grounded_platform=platform_code in POLICY_GROUNDED_PLATFORM_CODES,
            platform_code=platform_code,
        )


class ContentSummaryResponse(BaseModel):
    """A card in the Kanban board. No script text - a board is not a reader.

    No version number either. ``pr_content_items`` does not carry one: the
    current draft is the newest row in ``pr_content_versions``, and reporting a
    number here would mean a per-card query or a denormalised column that could
    fall out of step with the versions table. The detail response has the real
    version, read from the row itself.
    """

    id: uuid.UUID
    code: str
    title: str
    workflow_stage: str
    priority: str
    #: Step 1F.2.3e. ``None`` for content that predates the column - the panel
    #: renders that as *Chưa phân loại* rather than as a blank.
    content_type: str | None
    brand_id: uuid.UUID
    owner_user_id: uuid.UUID
    planned_publish_at: datetime | None
    updated_at: datetime | None
    #: Step 1F.2.3. Who is cutting this, or ``null`` for "chưa có người nhận" -
    #: a real state at ``APPROVED`` and at ``PRODUCTION``, not a missing value.
    #: Separate from ``owner_user_id`` throughout: the owner answers for the
    #: piece, the producer for one file.
    producer_user_id: uuid.UUID | None = None
    #: Step 1F.2.3b. ``WAITING_FOR_PRODUCER``, ``READY_FOR_PRODUCTION``,
    #: ``IN_PRODUCTION``, ``IN_INTERNAL_REVIEW`` or ``null`` outside the
    #: production half. **Derived on the server** from the stage and the
    #: producer, so a board does not have to combine two fields and invent the
    #: rule - "đã duyệt" and "chờ nhận sản xuất" are the same stage and
    #: different things to do.
    production_state: str | None = None
    #: Step 1F.2.8. Whether **this session** could record an approval for this
    #: item right now: it is standing at a review gate and the actor holds a
    #: grant whose scope covers it. The server's answer, from the same predicate
    #: the write enforces - a client must not derive it from a capability list,
    #: because "may review somewhere" is not "may review this".
    #:
    #: ``False`` on every list that does not ask the question, which is every one
    #: except the board. A card that is not offered a checkbox is the honest
    #: default; a card wrongly offered one is a refusal at the end of a batch.
    approvable_by_me: bool = False
    #: Step 1F.2.3f.4. **When this piece went out** - the earliest instant among
    #: its active publications, resolved server-side. ``None`` for anything not
    #: published, and for a published row whose publications were all reversed.
    #:
    #: On the card this is *"Thực tế đăng"*, and it is deliberately not any of
    #: the three date filters: a piece created in August, planned for 31 August
    #: and actually posted on 2 September belongs to September, and only this
    #: field says so.
    published_at: datetime | None = None

    @classmethod
    def from_row(
        cls,
        row: PrContentItem,
        *,
        approvable_by_me: bool = False,
        published_at: datetime | None = None,
    ) -> ContentSummaryResponse:
        return cls(
            id=row.id,
            code=row.code,
            title=row.title,
            workflow_stage=row.workflow_stage.value,
            priority=row.priority.value,
            content_type=row.content_type.value if row.content_type else None,
            brand_id=row.brand_id,
            owner_user_id=row.owner_user_id,
            planned_publish_at=row.planned_publish_at,
            updated_at=row.updated_at,
            producer_user_id=row.producer_user_id,
            production_state=(
                state.value
                if (state := handoff_state(row.workflow_stage, row.producer_user_id))
                else None
            ),
            approvable_by_me=approvable_by_me,
            published_at=published_at,
        )


class ContentDetailResponse(BaseModel):
    """One item, its current draft, its planned channels and its brand.

    ``brand`` is the whole reason a detail header can read "Apexmed · Facebook".
    It is nullable because ``pr_brands`` rows are never deleted but a row can
    always be missing from a restored database, and a header that omits the
    brand is better than one that shows a UUID.
    """

    content: ContentSummaryResponse
    current_version: ContentVersionResponse | None
    targets: list[ContentTargetResponse]
    brand: BrandResponse | None = None

    @classmethod
    def from_detail(cls, detail: ContentDetail) -> ContentDetailResponse:
        channels = {channel.id: channel for channel in detail.target_channels}
        platforms = detail.platform_codes
        return cls(
            content=ContentSummaryResponse.from_row(detail.content),
            current_version=(
                ContentVersionResponse.from_row(detail.current_version)
                if detail.current_version is not None
                else None
            ),
            targets=[
                ContentTargetResponse.from_row(
                    row,
                    channel := channels.get(row.channel_id),
                    platforms.get(channel.platform_id) if channel else None,
                )
                for row in detail.targets
            ],
            brand=BrandResponse.from_row(detail.brand) if detail.brand is not None else None,
        )


class ContentTargetRequest(_Body):
    """One planned channel, as the create form submits it.

    ``distribution_mode`` is the caller's answer to organic-or-paid. There is
    deliberately **no platform field**: the platform is derived from the
    channel, so a client cannot decide which official rulebook its content is
    judged against.
    """

    channel_id: uuid.UUID
    target_publish_at: datetime | None = None
    adaptation_note: str | None = None
    distribution_mode: str = Field(
        default=PrDistributionMode.UNSPECIFIED.value,
        description="`ORGANIC` or `PAID_AD`. Required for Facebook and TikTok.",
    )


class ContentResourceRequest(_Body):
    """Material to attach to a content item, or the changes to one.

    ``location`` is validated in the domain - see
    :mod:`meobot.domain.pr.resources` - and deliberately **not** here beyond
    being a string, for the reason
    :class:`SubmitProductionRequest` gives: a Pydantic ``AnyUrl`` would refuse a
    NAS path and put a second, differently-shaped rule in front of the real one.

    Declared here, above :class:`CreateContentRequest`, because since Step
    1F.2.3e.1 that request embeds it: one shape for a resource whether it is
    attached at creation or afterwards, so the two cannot drift into needing
    different keys for the same field.
    """

    resource_type: str = Field(
        description=(
            "`REFERENCE`, `IMAGE`, `VIDEO`, `DRIVE_FILE`, `SOURCE`, `BRAND_ASSET` or `OTHER`."
        )
    )
    label: str = Field(description="What to call it on screen. Required.")
    location: str = Field(description="An `http(s)` URL, or an absolute NAS path.")
    note: str | None = None
    #: A reviewer-attention signal. It orders the list and draws a badge; it
    #: gates no approval.
    required_for_review: bool = False


class CreateContentRequest(_Body):
    """New content item. The **code is not accepted** - the server allocates it.

    A caller-supplied code would be either a collision or a way to skip the
    counter, and ``PrCodeService`` is the only thing allowed to number anything.
    """

    title: str = Field(min_length=1, max_length=500)
    brand_id: uuid.UUID
    owner_user_id: uuid.UUID
    format_id: uuid.UUID | None = None
    pillar_id: uuid.UUID | None = None
    topic: str | None = None
    hook: str | None = None
    brief: str | None = None
    script_text: str | None = None
    priority: str | None = None
    #: Step 1F.2.3e. **Required by this route** - the service refuses a create
    #: without one. Typed optional so the refusal is the domain's typed error
    #: naming the field, rather than a Pydantic message about a missing key.
    content_type: str | None = None
    planned_publish_at: datetime | None = None
    targets: list[ContentTargetRequest] = Field(default_factory=list)
    #: Step 1F.2.3e.1. Review material to attach as part of this creation.
    #:
    #: **Optional, and empty is the ordinary case.** Named ``initial_resources``
    #: rather than ``resources`` because that is all they are: the resources this
    #: item is *born with*. It is not a mirror of the item's resource list and
    #: never becomes one - after creation the list is
    #: ``/contents/{id}/resources``, which is what adds, corrects and removes
    #: them.
    #:
    #: They are created in the same transaction as the content item, so one
    #: unacceptable location refuses the whole request rather than leaving a
    #: piece of content that is missing the brief somebody attached to it.
    initial_resources: list[ContentResourceRequest] = Field(default_factory=list)


class ReviseContentRequest(_Body):
    """A new draft.

    ``expected_version`` is mandatory and is the optimistic-concurrency check:
    two people editing the same draft means the second one is told to reload,
    not that one silently overwrites the other. The frontend fills it from the
    version it rendered.
    """

    expected_version: int = Field(ge=1)
    title: str | None = None
    topic: str | None = None
    hook: str | None = None
    brief: str | None = None
    script_text: str | None = None
    change_note: str | None = None
    priority: str | None = None


class TransitionRequest(_Body):
    """Ask for a stage change. The **matrix decides**, not the caller.

    There is no "force" flag and no way to name a trigger: this always arrives
    at ``PrContentWorkflowService.request_transition`` as ``MANUAL``, so an edge
    reserved for an approval cannot be walked from a browser.

    ``TEAM_LEAD_REVIEW`` as a target is the direct submission of Step 1F.2.10 -
    a finished script handed to the Team Lead with the AI review skipped. It is
    accepted from ``SCRIPTING`` only, needs the same right and the same draft
    completeness as ``AI_REVIEW``, and records no AI verdict of any kind.
    """

    target_stage: str
    note: str | None = None


# --- Reviews ----------------------------------------------------------------


class AiReviewResponse(BaseModel):
    """One advisory AI verdict. Never an approval."""

    id: uuid.UUID
    reviewed_version: int
    review_type: str
    result: str
    score: Decimal | None
    summary: str | None
    issues: list[Any]
    suggestions: list[Any]
    policy_flags: list[Any]
    model_name: str
    model_version: str | None
    prompt_version: str
    reviewed_at: datetime
    created_at: datetime

    @classmethod
    def from_row(cls, row: PrAiReview) -> AiReviewResponse:
        return cls(
            id=row.id,
            reviewed_version=row.reviewed_version,
            review_type=row.review_type.value,
            result=row.result.value,
            score=row.score,
            summary=row.summary,
            issues=list(row.issues or []),
            suggestions=list(row.suggestions or []),
            policy_flags=list(row.policy_flags or []),
            model_name=row.model_name,
            model_version=row.model_version,
            prompt_version=row.prompt_version,
            reviewed_at=row.reviewed_at,
            created_at=row.created_at,
        )


class ApprovalEventResponse(BaseModel):
    """One human decision, append-only."""

    id: uuid.UUID
    approval_stage: str
    decision: str
    version_reviewed: int
    reviewer_user_id: uuid.UUID
    comment: str | None
    decided_at: datetime
    #: Step 1F.2.3. Which production submission was judged - set for
    #: ``INTERNAL_REVIEW`` rows and ``null`` for the two script gates, which
    #: judge ``version_reviewed``. A screen pairs the two lists on this id
    #: rather than on timestamps, which is the only way to say which decision
    #: was about which cut once a piece has been re-cut.
    production_submission_id: uuid.UUID | None = None

    @classmethod
    def from_row(cls, row: PrApprovalEvent) -> ApprovalEventResponse:
        return cls(
            id=row.id,
            approval_stage=row.approval_stage.value,
            decision=row.decision.value,
            version_reviewed=row.version_reviewed,
            reviewer_user_id=row.reviewer_user_id,
            comment=row.comment,
            decided_at=row.decided_at,
            production_submission_id=row.production_submission_id,
        )


class TaskSummaryResponse(BaseModel):
    """A task card."""

    id: uuid.UUID
    code: str
    task_type: str
    title: str
    status: str
    priority: str
    content_id: uuid.UUID | None
    deadline: datetime | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: PrTask) -> TaskSummaryResponse:
        return cls(
            id=row.id,
            code=row.code,
            task_type=row.task_type,
            title=row.title,
            status=row.status.value,
            priority=row.priority.value,
            content_id=row.content_id,
            deadline=row.deadline,
            created_at=row.created_at,
        )


class ReviewContextResponse(BaseModel):
    """Everything a reviewer needs on one screen.

    The ``ai_review`` field is the load-bearing one and can be ``null``. The UI
    must render "chưa có" for that case rather than an empty verdict card - a
    blank card reads as "reviewed, nothing found", which is the opposite of the
    truth.
    """

    content: ContentSummaryResponse
    current_version: ContentVersionResponse
    targets: list[ContentTargetResponse]
    tasks: list[TaskSummaryResponse]
    ai_review: AiReviewResponse | None
    ai_reviews_for_version: list[AiReviewResponse]
    approvals: list[ApprovalEventResponse]

    @classmethod
    def from_context(cls, context: ContentReviewContext) -> ReviewContextResponse:
        return cls(
            content=ContentSummaryResponse.from_row(context.content),
            current_version=ContentVersionResponse.from_row(context.current_version),
            targets=[ContentTargetResponse.from_row(row) for row in context.targets],
            tasks=[TaskSummaryResponse.from_row(row) for row in context.tasks],
            ai_review=(
                AiReviewResponse.from_row(context.ai_review)
                if context.ai_review is not None
                else None
            ),
            ai_reviews_for_version=[
                AiReviewResponse.from_row(row) for row in context.ai_reviews_for_version
            ],
            approvals=[ApprovalEventResponse.from_row(row) for row in context.approvals],
        )


class AssignProducerRequest(_Body):
    """Who produces this piece. ``null`` un-assigns.

    One field and one endpoint for assigning, reassigning and clearing, because
    all three are the same decision - *who is producing this* - and the rules
    that govern them are identical. Three endpoints would be three places to
    forget the same check.
    """

    producer_user_id: uuid.UUID | None = Field(
        default=None, description="A user id, or `null` to leave it unassigned."
    )


class SubmitProductionRequest(_Body):
    """The finished file, handed over for internal review.

    ``location`` is required and validated in the domain - see
    :mod:`meobot.domain.pr.production`. It is deliberately **not** validated
    here beyond being a string: a Pydantic ``AnyUrl`` would refuse a NAS path,
    accept ``javascript:`` on some versions, and put a second, differently-shaped
    rule in front of the real one.
    """

    artifact_type: str = Field(
        description="`DRIVE_LINK`, `NAS_LINK`, `NAS_PATH` or `EXTERNAL_LINK`."
    )
    location: str = Field(description="The URL, or the NAS path for `NAS_PATH`.")
    label: str | None = Field(default=None, max_length=200)
    note: str | None = None


class ProductionSubmissionResponse(BaseModel):
    """One production file that was handed over. Immutable."""

    id: uuid.UUID
    submission_no: int
    artifact_type: str
    location: str
    #: Whether a client should render ``location`` as a link. Decided by the
    #: server from the type rather than by the browser from the string's shape -
    #: a NAS path starting ``//`` looks like a protocol-relative URL to anything
    #: matching on characters.
    is_link: bool
    label: str | None
    note: str | None
    producer_user_id: uuid.UUID
    submitted_by_user_id: uuid.UUID
    content_version_id: uuid.UUID
    created_at: datetime
    #: Step 1F.2.3f.2. Whether **this session** may fix where this file lives.
    #:
    #: Per row, because both halves of the rule are per row: who handed *this*
    #: one in, and whether *this* one has ever been published. A client that
    #: worked it out itself would need the publication list and the submitter id
    #: and the capability set, which is three chances to get it wrong.
    can_correct: bool = False

    @classmethod
    def from_row(
        cls, row: PrProductionSubmission, *, can_correct: bool = False
    ) -> ProductionSubmissionResponse:
        return cls(
            id=row.id,
            submission_no=row.submission_no,
            artifact_type=row.artifact_type.value,
            location=row.location,
            is_link=is_url_artifact(row.artifact_type),
            label=row.label,
            note=row.note,
            producer_user_id=row.producer_user_id,
            submitted_by_user_id=row.submitted_by_user_id,
            content_version_id=row.content_version_id,
            created_at=row.created_at,
            can_correct=can_correct,
        )


class CorrectProductionOutputRequest(_Body):
    """A correction to where a handed-in production file lives. Step 1F.2.3f.2.

    **No ``submission_no``, no ``content_version_id``, no producer and no
    submitter.** Those are the row's identity and its place in the append-only
    sequence, so there is no field to send them in - which makes changing one
    unrepresentable rather than merely refused.

    ``location`` is validated in the domain against ``artifact_type``, and
    accepts the four storage shapes as well as an ``http(s)`` URL - see
    :mod:`meobot.domain.pr.production`. Nothing fetches it.
    """

    artifact_type: str | None = Field(
        default=None, description="`DRIVE_LINK`, `NAS_LINK`, `NAS_PATH` or `EXTERNAL_LINK`."
    )
    location: str | None = Field(
        default=None,
        description="A link, or a path: `/volume1/…`, `\\\\NAS\\…`, `M:\\…`, `shared/…`.",
    )
    note: str | None = None


class ProductionStateResponse(BaseModel):
    """The production half of a content item: who, and what they handed in.

    Every submission, newest first - not just the latest. An internal reviewer
    looking at the second cut needs to see that there was a first one, and the
    approval events carry ``production_submission_id`` so a screen can say what
    was decided about each.
    """

    content_id: uuid.UUID
    workflow_stage: str
    producer_user_id: uuid.UUID | None
    submissions: list[ProductionSubmissionResponse]

    @classmethod
    def from_state(cls, state: ProductionState) -> ProductionStateResponse:
        return cls(
            content_id=state.content.id,
            workflow_stage=state.content.workflow_stage.value,
            producer_user_id=state.producer_user_id,
            submissions=[ProductionSubmissionResponse.from_row(row) for row in state.submissions],
        )


class DeleteContentRequest(_Body):
    """Why this is being removed. Optional, and never parsed.

    Kept optional deliberately. A required reason on a destructive action reads
    as a safeguard and is not one - people type "x" - and the confirmation dialog
    plus the surviving audit row are what actually make the deletion accountable.
    """

    reason: str | None = None


class AvailableActionResponse(BaseModel):
    """One thing the caller may currently do to one content item.

    Step 1E.2. Codes cross the wire, not sentences: ``action`` is the write
    path, ``target_stage`` and ``decision`` are the domain enums, and the
    client renders the Vietnamese label from its own table - the same rule
    every other field in this module follows.

    ``emphasis`` is presentation metadata and nothing more. It lets a screen
    keep "Hủy nội dung" out of the forward path without pattern-matching on
    stage names in the browser; it grants nothing and forbids nothing, and the
    write route re-checks a ``DANGER`` action exactly as it checks a
    ``PRIMARY`` one.
    """

    action: str = Field(
        description=(
            "`TRANSITION`, `APPROVAL`, `EDIT_CONTENT`, `DELETE_CONTENT`, "
            "`ASSIGN_PRODUCER`, `CLAIM_PRODUCTION`, `START_PRODUCTION`, "
            "`SUBMIT_PRODUCTION` or `UNDO_LAST_ACTION`."
        )
    )
    target_stage: str | None = Field(default=None, description="Set for `TRANSITION`.")
    decision: str | None = Field(default=None, description="Set for `APPROVAL`.")
    emphasis: str = Field(description="`PRIMARY`, `SECONDARY` or `DANGER`.")
    #: Step 1F.2.3b. Set for `UNDO_LAST_ACTION`: which decision would be taken
    #: back. With `target_stage` - where the content would land - it is
    #: everything a client needs to say "Hoàn tác duyệt Trưởng phòng" and "nội
    #: dung sẽ quay lại bước Chờ Trưởng phòng duyệt" without reading history.
    undo_kind: str | None = Field(default=None, description="Set for `UNDO_LAST_ACTION`.")

    @classmethod
    def from_action(cls, action: PrAvailableAction) -> AvailableActionResponse:
        return cls(
            action=action.kind.value,
            target_stage=action.target_stage.value if action.target_stage else None,
            decision=action.decision.value if action.decision else None,
            emphasis=action.emphasis.value,
            undo_kind=action.undo_kind,
        )


class TransitionEventResponse(BaseModel):
    """One recorded stage change, and its reversal link if it has one.

    Step 1F.2.3b. What the history tab renders: an undo does not remove the move
    it takes back, so both rows come across and the two ids pair them up. A
    client shows the original with "đã hoàn tác" beside it rather than deleting
    it from the list, which is the whole difference between an undo and an edit.
    """

    id: uuid.UUID
    from_stage: str
    to_stage: str
    trigger: str
    actor_user_id: uuid.UUID | None
    approval_event_id: uuid.UUID | None
    production_submission_id: uuid.UUID | None
    #: Set on an undo row: the transition it took back.
    reverses_event_id: uuid.UUID | None
    #: Set on a transition that was taken back: the undo that did it.
    reversed_by_event_id: uuid.UUID | None
    note: str | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: PrContentTransitionEvent) -> TransitionEventResponse:
        return cls(
            id=row.id,
            from_stage=row.from_stage.value,
            to_stage=row.to_stage.value,
            trigger=row.trigger.value,
            actor_user_id=row.actor_user_id,
            approval_event_id=row.approval_event_id,
            production_submission_id=row.production_submission_id,
            reverses_event_id=row.reverses_event_id,
            reversed_by_event_id=row.reversed_by_event_id,
            note=row.note,
            created_at=row.created_at,
        )


class AvailableActionsResponse(BaseModel):
    """The answer to "what do I do next with this?".

    An empty list is a real answer - a cancelled or archived item offers
    nothing, and so does one whose reader holds no write capability. A client
    must render that as "không có thao tác nào", never as a missing panel.
    """

    content_id: uuid.UUID
    workflow_stage: str
    available_actions: list[AvailableActionResponse]


class AiReviewRunResponse(BaseModel):
    """One AI review *execution*, as a screen sees it.

    Step 1F. Carefully not the whole row: there is no prompt text, no payload,
    no provider response and no message of any kind. ``error_code`` is the
    stable machine string the run stored (``llm_error``, ``timed_out``) and the
    client renders its own sentence from it - a provider's error text is not
    something a browser should ever display.
    """

    id: uuid.UUID
    status: str = Field(description="`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `SUPERSEDED`.")
    trigger: str
    #: The exact immutable draft this execution read.
    content_version_id: uuid.UUID
    attempt_count: int
    #: The derived result, present on a finished run - including a superseded
    #: one, which concluded something about a draft that has since moved on.
    outcome: str | None
    model_name: str | None
    prompt_version: str
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_row(cls, row: PrAiReviewRun) -> AiReviewRunResponse:
        return cls(
            id=row.id,
            status=row.status.value,
            trigger=row.trigger.value,
            content_version_id=row.content_version_id,
            attempt_count=row.attempt_count,
            outcome=row.outcome.value if row.outcome else None,
            model_name=row.model_name,
            prompt_version=row.prompt_version,
            error_code=row.error_code,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )


class PolicyPackRefResponse(BaseModel):
    """One pinned pack, as the AI review panel names it.

    Step 1F.1. The label is what a person reads; the ids are what makes a
    finding explainable years later. No rules travel here - the panel shows the
    handful a finding cited, not the whole pack.
    """

    platform_code: str
    distribution_mode: str
    pack_label: str
    pack_version: int


class PolicyRuleCitationResponse(BaseModel):
    """One cited rule, with the official source link a person may follow.

    The URL comes from ``pr_platform_policy_rules.source_url`` - stored
    provenance - so the client never builds a source address of its own.
    """

    rule_id: str
    title: str
    source_url: str
    section_path: str | None = None


class AiReviewStateResponse(BaseModel):
    """Everything the detail page's AI review panel renders, and polls.

    ``run`` is the execution and ``review`` is its answer. Both are nullable and
    they are nullable independently: a queued run has no review yet, and a
    review recorded before Step 1F existed has no run. A client must not infer
    one from the other.

    ``can_retry`` is the **server's** answer to "may this person ask again",
    decided from the capability, the stage and whether anything is already
    running. The browser renders the button or does not; it never works this
    out, and pressing it anyway is refused by the route.

    ``active`` is what the poller watches. It exists so a client does not have
    to know which statuses are terminal - that list is the server's.
    """

    run: AiReviewRunResponse | None
    review: AiReviewResponse | None
    active: bool = Field(description="True while a run is QUEUED or RUNNING. Poll while true.")
    can_retry: bool
    #: Step 1F.1. Which policy packs this run pinned. **Empty means ungrounded**
    #: - a legacy pre-1F.1 run, or an unsupported platform - and a client must
    #: render that as "no policy grounding" rather than inventing one.
    policy_packs: list[PolicyPackRefResponse] = Field(default_factory=list)
    #: Rules any finding cited, resolved from the pinned packs.
    policy_citations: list[PolicyRuleCitationResponse] = Field(default_factory=list)


class UpdateTargetRequest(_Body):
    """Set one target's distribution mode.

    One field, deliberately. Everything else about a target is set at creation;
    this is the value Step 1F.1 added and the only one a person changes after
    the fact.
    """

    distribution_mode: str = Field(description="`ORGANIC` or `PAID_AD`.")


class UpdateContentTypeRequest(_Body):
    """Classify a content item, or correct its format.

    One field, and no way to send ``null``: *Chưa phân loại* is where a
    historical row starts, not somewhere a classified one can be returned to.
    """

    content_type: str = Field(
        description=(
            "`ULTRA_SHORT_SCRIPT`, `SHORT_VIDEO_SCRIPT`, `FACEBOOK_POST`, "
            "`LONG_YOUTUBE_SCRIPT`, `PRESS_ARTICLE` or `CORPORATE_TVC`."
        )
    )


class UpdateContentResourceRequest(_Body):
    """The fields of a resource that may change. Omitted means unchanged.

    **No ``content_id``.** A resource belongs to the item it was attached to;
    moving one elsewhere is a delete and an add, with the audit trail to match.
    """

    resource_type: str | None = None
    label: str | None = None
    location: str | None = None
    note: str | None = None
    required_for_review: bool | None = None


class ContentResourceResponse(BaseModel):
    """One piece of review material.

    ``is_link`` is computed on the server - see
    :func:`~meobot.domain.pr.resources.is_link_location` - so no client decides
    whether a location is clickable by reading its first characters. A NAS path
    beginning ``//`` looks like a protocol-relative URL to anything that does.
    """

    id: uuid.UUID
    content_id: uuid.UUID
    resource_type: str
    label: str
    location: str
    note: str | None
    required_for_review: bool
    #: ``True`` for an ``http(s)`` URL, ``False`` for a NAS path the browser
    #: should render as text to copy.
    is_link: bool
    added_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: PrContentResource) -> ContentResourceResponse:
        return cls(
            id=row.id,
            content_id=row.content_id,
            resource_type=row.resource_type.value,
            label=row.label,
            location=row.location,
            note=row.note,
            required_for_review=row.required_for_review,
            is_link=is_link_location(row.location),
            added_by_user_id=row.added_by_user_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class ContentDerivativeResponse(BaseModel):
    """One produced re-cut of a content item. Step 1F.2.3f.

    ``is_link`` is computed on the server - see
    :func:`~meobot.domain.pr.assets.is_link_location` - so no client decides
    whether a location is clickable by reading its first characters. A NAS path
    beginning ``//`` looks like a protocol-relative URL to anything that does.

    ``source_submission_id`` is the master this was cut from, when it was cut
    from a tracked one. The panel resolves it against the submissions it already
    loaded rather than being sent a copy of that row: two representations of the
    same file on one response is two things to keep in step.
    """

    id: uuid.UUID
    content_id: uuid.UUID
    derivative_type: str
    label: str
    location: str
    #: ``True`` for an ``http(s)`` URL, ``False`` for a NAS path the browser
    #: should render as text to copy.
    is_link: bool
    source_submission_id: uuid.UUID | None
    note: str | None
    created_by_user_id: uuid.UUID
    #: Step 1F.2.3g. Who recorded it, **by name**. Joined server-side rather
    #: than resolved by the client against ``/people``, which lists active users
    #: only: now that anybody who may view a piece may add a cut to it, the
    #: recorder is often somebody outside the production team, and the case a
    #: client-side lookup renders as a blank - a colleague who has since left -
    #: is the ordinary one. ``None`` when the ``users`` row has gone; a client
    #: renders that as an absence and never as the id.
    created_by_name: str | None = None
    #: Step 1F.2.3g. Whether **this session** may correct this row: its recorder,
    #: or production management. Per row rather than per content, because since
    #: this step the answer genuinely differs down the list.
    #:
    #: It does not promise every field is writable - see
    #: ``is_published_output``.
    can_edit: bool = False
    #: Step 1F.2.3g. Whether this session may remove it. ``False`` for everybody
    #: on a published output, recorder included.
    can_delete: bool = False
    #: Step 1F.2.3g. Whether any publication - a reversed one included - names
    #: this file, which is what freezes ``location``, ``derivative_type`` and
    #: ``source_submission_id`` for everybody. Sent so a form can disable those
    #: three fields instead of offering them and rendering a 409.
    is_published_output: bool = False
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: PrContentDerivative) -> ContentDerivativeResponse:
        """The row alone, with every authority flag ``False``.

        Used by the write routes, which answer with the thing that was just
        written. A ``POST`` response is not a list and is not what the controls
        are drawn from; the list route sends :meth:`from_view`.
        """
        return cls(
            id=row.id,
            content_id=row.content_id,
            derivative_type=row.derivative_type.value,
            label=row.label,
            location=row.location,
            is_link=is_link_asset_location(row.location),
            source_submission_id=row.source_submission_id,
            note=row.note,
            created_by_user_id=row.created_by_user_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @classmethod
    def from_view(cls, view: DerivativeView) -> ContentDerivativeResponse:
        """The row with the server's answers about this session attached."""
        return cls(
            **cls.from_row(view.derivative).model_dump(
                exclude={"created_by_name", "can_edit", "can_delete", "is_published_output"}
            ),
            created_by_name=view.created_by_name,
            can_edit=view.can_edit,
            can_delete=view.can_delete,
            is_published_output=view.is_published_output,
        )


class ContentDerivativeRequest(_Body):
    """A re-cut to record against a content item.

    ``location`` is validated in the domain - see
    :mod:`meobot.domain.pr.assets` - and deliberately **not** here beyond being
    a string, for the reason :class:`SubmitProductionRequest` gives: a Pydantic
    ``AnyUrl`` would refuse a NAS path and put a second, differently-shaped rule
    in front of the real one.
    """

    derivative_type: str = Field(
        description=("`REMIX`, `CUTDOWN`, `RECUT`, `REFORMAT`, `CAPTION_VARIANT` or `OTHER`.")
    )
    label: str = Field(description="What to call it on screen - 'TikTok cut 25s'. Required.")
    location: str = Field(description="An `http(s)` URL, or an absolute NAS path.")
    #: The original production submission this was cut from, when it was cut
    #: from a tracked one. Optional: a file re-cut from raw footage came from no
    #: submission, and requiring a link there would mean storing a guess.
    source_submission_id: uuid.UUID | None = None
    note: str | None = None


class UpdateContentDerivativeRequest(_Body):
    """Fields a derivative may have changed. Absent means "leave alone".

    **No ``content_id``.** A derivative belongs to the item it was cut from;
    moving one is a delete and an add.

    Once a publication points at this row, ``derivative_type``, ``location`` and
    ``source_submission_id`` are refused with a 409 and ``label``/``note`` are
    not - see
    :meth:`~meobot.application.pr_content_asset_service.PrContentAssetService.update_derivative`.
    """

    derivative_type: str | None = None
    label: str | None = None
    location: str | None = None
    source_submission_id: uuid.UUID | None = None
    note: str | None = None


class ContentDestinationResponse(BaseModel):
    """One product or landing page this content sends people to. Step 1F.2.3f."""

    id: uuid.UUID
    content_id: uuid.UUID
    label: str
    url: str
    note: str | None
    added_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: PrContentDestination) -> ContentDestinationResponse:
        return cls(
            id=row.id,
            content_id=row.content_id,
            label=row.label,
            url=row.url,
            note=row.note,
            added_by_user_id=row.added_by_user_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class ContentDestinationRequest(_Body):
    """A commercial page to attach to a content item.

    ``url`` must be an ``http(s)`` URL with a host - never a path. This field's
    whole job is to be clickable, and the rule lives in
    :func:`~meobot.domain.pr.assets.normalize_destination_url`.
    """

    label: str = Field(description="What to call it - 'Landing page dịch vụ'. Required.")
    url: str = Field(description="An `http(s)` URL with a host.")
    note: str | None = None


class UpdateContentDestinationRequest(_Body):
    """Fields a destination may have changed. Absent means "leave alone"."""

    label: str | None = None
    url: str | None = None
    note: str | None = None


# --- Comments, Step 1F.2.3g -------------------------------------------------


class ContentCommentResponse(BaseModel):
    """One comment, or the gap where one used to be.

    Built from
    :class:`~meobot.application.pr_content_comment_service.CommentView` rather
    than from the row, because three of the fields below are not columns: the
    author's **name**, and whether *this* session may edit or delete it. A client
    comparing ``author_user_id`` against its session id would be re-deriving an
    authorization rule the server owns, and would get the moderator half wrong.

    **A tombstone sends no words and no name.** ``body``, ``author_user_id``,
    ``author_name`` and ``edited_at`` are all ``None`` once ``is_deleted``, and
    ``id``, ``created_at`` and ``replies`` are not - because what a deleted
    comment says is *"there was a comment here"*, and the answers underneath it
    are still somebody else's. Sending the text with a flag beside it would leave
    every client one bug away from rendering it.
    """

    id: uuid.UUID
    content_id: uuid.UUID
    #: ``None`` for a root, the root's id for a reply. Threading is one level.
    parent_comment_id: uuid.UUID | None
    author_user_id: uuid.UUID | None
    author_name: str | None
    #: Plain text, exactly as typed with the ends trimmed. **Never markup** -
    #: nothing on either side parses it, and a client puts it in a text node.
    body: str | None
    created_at: datetime
    #: When the author last reworded it. ``None`` if they never did, which is
    #: what a client tells *"đã sửa"* from.
    edited_at: datetime | None
    is_deleted: bool
    can_edit: bool
    can_delete: bool
    #: Empty on a reply, always. A reply may not be replied to.
    replies: list[ContentCommentResponse] = Field(default_factory=list)

    @classmethod
    def from_view(cls, view: CommentView) -> ContentCommentResponse:
        return cls(
            id=view.id,
            content_id=view.content_id,
            parent_comment_id=view.parent_comment_id,
            author_user_id=view.author_user_id,
            author_name=view.author_name,
            body=view.body,
            created_at=view.created_at,
            edited_at=view.edited_at,
            is_deleted=view.is_deleted,
            can_edit=view.can_edit,
            can_delete=view.can_delete,
            replies=[cls.from_view(reply) for reply in view.replies],
        )


class ContentCommentPageResponse(BaseModel):
    """One page of root threads, with their replies nested one level.

    A page rather than a bare array, unlike this API's other child collections.
    Resources, derivatives and destinations are a handful of rows by their
    nature; a comment thread on a piece that ran for three months is not, and an
    endpoint that answers with all of it is one somebody eventually opens on a
    phone.

    ``total`` counts **roots**, not comments - it is what *"còn 12 chủ đề nữa"*
    is drawn from, and counting replies into it would make the number disagree
    with what paging through actually produces.
    """

    content_id: uuid.UUID
    items: list[ContentCommentResponse]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_page(
        cls, content_id: uuid.UUID, page: ContentCommentPage
    ) -> ContentCommentPageResponse:
        return cls(
            content_id=content_id,
            items=[ContentCommentResponse.from_view(thread) for thread in page.threads],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )


class ContentCommentRequest(_Body):
    """Something to say about a content item.

    ``parent_comment_id`` makes it a reply, and must name a **root** of this same
    item - the server checks both rather than trusting either. Absent means a new
    thread.

    There is deliberately no ``author_user_id``: the author is the session, for
    the reason :class:`ApprovalDecisionRequest` gives about reviewers. A field
    here would let anybody write a sentence in somebody else's name, which is
    worse on a comment than on almost anything else in this API, because a
    comment is displayed as speech.
    """

    body: str = Field(description="Plain text. Required, trimmed, max 2000 characters.")
    parent_comment_id: uuid.UUID | None = None


class UpdateContentCommentRequest(_Body):
    """New wording for one's own comment.

    One field. There is no ``parent_comment_id``: moving a comment to a different
    thread is not an edit - it changes what somebody was answering - and leaving
    the field out is what makes that unrepresentable rather than merely refused,
    exactly as :class:`UpdateContentDerivativeRequest` does for ``content_id``.
    """

    body: str = Field(description="Plain text. Required, trimmed, max 2000 characters.")


class UpdateContentPriorityRequest(_Body):
    """Retriage one content item.

    One field, and no ``expected_version``. Step 1F.2.3d: a priority change is
    not a draft, so there is no version for it to be stale against - two people
    marking the same piece urgent is not a conflict, it is agreement, and
    refusing the second with a 409 would be inventing one.
    """

    priority: str = Field(description="`CRITICAL`, `URGENT`, `HIGH` or `NORMAL`.")


class ApprovalDecisionRequest(_Body):
    """Approve, request revision, or reject.

    ``version_reviewed`` is required and is not a formality: it is what binds a
    decision to the draft that was on screen. Without it, approving a piece
    somebody revised while you were reading would attach your approval to text
    you never saw.

    ``reviewer_user_id`` is **absent by design.** The reviewer is the session,
    always - accepting one here would let anybody file a decision in somebody
    else's name. Since Step 1F.2.2 one person may hold both review grants and
    decide at both gates, which makes the actor recorded on each event the only
    thing that distinguishes those two signatures from each other.
    """

    decision: str = Field(description="APPROVED | REVISION_REQUESTED | REJECTED")
    version_reviewed: int = Field(ge=1)
    comment: str | None = None
    task_id: uuid.UUID | None = None


class BulkApproveRequest(_Body):
    """Approve every named item at one gate, or approve none of them.

    ``gate`` is required and is not a convenience. On the single-item route the
    gate is *derived* from the stage the item is standing at, because there is
    one item and one right answer; here there are up to
    :data:`~meobot.domain.pr.policy.BULK_APPROVAL_MAX_ITEMS`, and the whole
    same-step rule is that they must agree. Sending it makes the caller state
    which step they believe they are approving, and the server refuses the batch
    - rather than silently splitting it - when any item is somewhere else.

    ``content_ids`` is the frozen batch. A "select all at this step" is resolved
    to ids by ``GET /reviews/approvable`` *before* the confirmation dialog
    opens, so nothing created afterwards can join it.

    ``version_reviewed`` is deliberately **absent**, unlike
    :class:`ApprovalDecisionRequest`. It binds a decision to the draft on screen,
    and a bulk approval has no draft on screen; the version each event records is
    read on the server inside the row lock. That is safe for exactly one reason,
    and it is a property of the workflow rather than of this schema: content
    standing at a review gate cannot be revised - ``revise_content`` refuses
    outside ``EDITABLE_STAGES`` - so the text under an item cannot change without
    the item first leaving the gate, which the batch's gate check then catches.

    ``reviewer_user_id`` is absent for the reason it is absent on the single-item
    request: the reviewer is the session.
    """

    gate: str = Field(description="TEAM_LEAD_REVIEW | HEAD_REVIEW | INTERNAL_REVIEW")
    content_ids: list[uuid.UUID] = Field(
        min_length=1,
        max_length=BULK_APPROVAL_MAX_ITEMS,
        description=(
            "The frozen batch. Duplicates are de-duplicated server-side, keeping "
            "the first occurrence; the response reports how many were dropped."
        ),
    )
    #: One comment for the batch, recorded on every event in it.
    comment: str | None = None


class BulkApprovedItemResponse(BaseModel):
    """One item a batch approved, and where its approval sent it."""

    content_id: uuid.UUID
    code: str
    title: str
    new_stage: str


class BulkApproveResponse(BaseModel):
    """What a batch did. Only ever returned when **all** of it succeeded.

    There is no ``failed`` list and no per-item status, and the absence is the
    contract: a partial result is not a shape this operation can produce, so a
    panel cannot accidentally report "100 approved" over a response that meant
    something else. Every refusal is an error body with ``details.affected``
    naming the items that caused it and ``details.approved`` equal to ``0``.
    """

    batch_id: uuid.UUID
    gate: str
    approved_count: int
    approved: list[BulkApprovedItemResponse]
    #: How many ids the request carried, before de-duplication.
    requested_count: int
    #: How many of them were repeats. Reported rather than refused - see
    #: :meth:`~meobot.application.pr_bulk_approval_service.PrBulkApprovalService.approve`.
    duplicates_removed: int

    @classmethod
    def from_outcome(cls, outcome: BulkApprovalOutcome) -> BulkApproveResponse:
        return cls(
            batch_id=outcome.batch_id,
            gate=outcome.gate.value,
            approved_count=len(outcome.approved),
            approved=[
                BulkApprovedItemResponse(
                    content_id=item.content_id,
                    code=item.code,
                    title=item.title,
                    new_stage=item.new_stage.value,
                )
                for item in outcome.approved
            ],
            requested_count=outcome.requested,
            duplicates_removed=outcome.duplicates_removed,
        )


class ApprovableSelectionResponse(BaseModel):
    """Everything at one gate, under one filter, that this session may approve.

    ``total`` is the whole eligible queue and ``content_ids`` is at most
    ``limit`` of it, in the board's reading order. They are two statements over
    one ``WHERE``, so the number a panel shows and the batch it would submit
    cannot describe different sets.

    ``truncated`` is ``total > len(content_ids)`` and a panel **must** say so
    rather than rounding it away: "Duyệt 200 nội dung" over a queue of 340 is
    true, and "Duyệt 340 nội dung" would not be.
    """

    gate: str
    total: int
    content_ids: list[uuid.UUID]
    limit: int
    truncated: bool

    @classmethod
    def from_selection(cls, selection: ApprovableSelection) -> ApprovableSelectionResponse:
        return cls(
            gate=selection.gate.value,
            total=selection.total,
            content_ids=list(selection.content_ids),
            limit=selection.limit,
            truncated=selection.truncated,
        )


class ArchiveCandidatesResponse(BaseModel):
    """Everything still ``PUBLISHED`` whose publication month is ``period``.

    Step 1F.2.3f.6, the read half of *"Lưu trữ nội dung kỳ trước"*. ``total``
    and ``content_ids`` are two statements over one ``WHERE`` - the same two
    clauses that draw *Đã đăng* for that month - so the count in the
    confirmation is the column's header and the batch is the column.
    ``truncated`` says when the month holds more than one batch may take.

    ``may_archive`` is whether this session holds the capability the transition
    needs. Resolved on the server so the panel offers the button to the people
    the write would accept it from.
    """

    period: str
    total: int
    content_ids: list[uuid.UUID]
    limit: int
    truncated: bool
    may_archive: bool

    @classmethod
    def from_candidates(cls, found: ArchiveCandidates) -> ArchiveCandidatesResponse:
        return cls(
            period=found.period.isoformat()[:7],
            total=found.total,
            content_ids=list(found.content_ids),
            limit=found.limit,
            truncated=found.truncated,
            may_archive=found.may_archive,
        )


class BulkArchiveRequest(_Body):
    """Archive every named item published in ``period``, or archive none of them.

    ``period`` is ``YYYY-MM`` and is checked, not trusted: an id whose canonical
    publication instant is not in that month refuses the whole batch. It may not
    be the current month or a later one.

    ``content_ids`` is the frozen batch, resolved by
    ``GET /contents/archive-candidates`` before the confirmation opened.
    """

    period: str = Field(description="The closed month whose published output to archive, YYYY-MM")
    content_ids: list[uuid.UUID] = Field(
        min_length=1,
        max_length=BULK_ARCHIVE_MAX_ITEMS,
        description=(
            "The frozen batch. Duplicates are de-duplicated server-side, keeping "
            "the first occurrence; the response reports how many were dropped."
        ),
    )
    #: One note for the batch, recorded on every transition in it.
    note: str | None = None


class BulkArchivedItemResponse(BaseModel):
    content_id: uuid.UUID
    code: str
    title: str


class BulkArchiveResponse(BaseModel):
    """What a period archive did. Only ever returned when **all** of it succeeded.

    No ``failed`` list, for the reason :class:`BulkApproveResponse` has none: a
    partial result is not a shape this operation can produce. Every refusal is
    an error body with ``details.affected`` naming the items that caused it and
    ``details.archived`` equal to ``0``.
    """

    batch_id: uuid.UUID
    period: str
    archived_count: int
    archived: list[BulkArchivedItemResponse]
    requested_count: int
    duplicates_removed: int

    @classmethod
    def from_outcome(cls, outcome: BulkArchiveOutcome) -> BulkArchiveResponse:
        return cls(
            batch_id=outcome.batch_id,
            period=outcome.period.isoformat()[:7],
            archived_count=outcome.archived_count,
            archived=[
                BulkArchivedItemResponse(
                    content_id=item.content_id, code=item.code, title=item.title
                )
                for item in outcome.archived
            ],
            requested_count=outcome.requested,
            duplicates_removed=outcome.duplicates_removed,
        )


class SubmitAiReviewRequest(_Body):
    """Record an AI verdict produced **outside** this API.

    No route here calls a model. Provenance (``model_name``, ``prompt_version``)
    is mandatory because an unattributable verdict is worse than none: nobody
    can tell later whether it came from a model, a script, or a person typing
    into curl.
    """

    reviewed_version: int = Field(ge=1)
    review_type: str
    result: str
    model_name: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    model_version: str | None = None
    score: Decimal | None = None
    summary: str | None = None
    issues: list[Any] | None = None
    suggestions: list[Any] | None = None
    policy_flags: list[Any] | None = None
    task_id: uuid.UUID | None = None


# --- Tasks ------------------------------------------------------------------


class CreateTaskRequest(_Body):
    """New task. Code allocated server-side, as with content."""

    task_type: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    content_id: uuid.UUID | None = None
    description: str | None = None
    priority: str | None = None
    deadline: datetime | None = None


class AssignTaskRequest(_Body):
    """Put a person on a task.

    ``user_id``, never a name: a typed name is ambiguous, and resolving one
    silently is how work lands on the wrong Linh. The UI resolves names through
    ``/api/pr/people`` and sends an id.
    """

    user_id: uuid.UUID
    assignment_role: str


class TaskStatusRequest(_Body):
    """Move a task. Legality is the task matrix's call."""

    status: str
    note: str | None = None


class TaskAssignmentResponse(BaseModel):
    """One person on one task."""

    id: uuid.UUID
    user_id: uuid.UUID
    assignment_role: str
    assigned_at: datetime

    @classmethod
    def from_row(cls, row: PrTaskAssignment) -> TaskAssignmentResponse:
        return cls(
            id=row.id,
            user_id=row.user_id,
            assignment_role=row.assignment_role.value,
            assigned_at=row.assigned_at,
        )


class TaskDetailResponse(BaseModel):
    """A task and who is on it."""

    task: TaskSummaryResponse
    assignments: list[TaskAssignmentResponse]


# --- Platforms --------------------------------------------------------------


class PlatformResponse(BaseModel):
    """A platform, reduced to what a picker and an admin list need.

    ``policy_grounded`` is the server telling the client whether Step 1F.1 will
    hold content on this platform to official policy. The client is not asked to
    compare codes - that comparison is a policy decision and it stays here.
    """

    id: uuid.UUID
    code: str
    name: str
    status: str
    policy_grounded: bool = False

    @classmethod
    def from_row(cls, row: PrPlatform) -> PlatformResponse:
        return cls(
            id=row.id,
            code=row.code,
            name=row.name,
            status=row.status.value,
            policy_grounded=row.code in POLICY_GROUNDED_PLATFORM_CODES,
        )


class CreatePlatformRequest(_Body):
    """New platform.

    ``code`` is required and never derived from ``name``: it is the token
    Step 1F.1 matches on, so somebody has to mean it. The service normalizes
    case and refuses anything that is not a canonical identifier.
    """

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    api_available: bool = False
    api_note: str | None = None


# --- Channels ---------------------------------------------------------------


class ChannelResponse(BaseModel):
    """One channel, with the identity a person recognises it by.

    ``platform_code`` and ``policy_grounded_platform`` are the server's answer
    to "does a target on this channel need an organic-or-paid mode". A client
    that worked it out would be matching platform codes in the browser, which is
    the decision Step 1F.1 put on the server - and matching a channel's *name*
    would be worse still.

    Step 1F.2.4a added the rest of the identity. ``platform`` is the **canonical
    network** the code maps onto and is ``None`` for a platform outside the six -
    at which point ``platform_label`` is *Chưa xác định* and ``platform_name``
    still carries what the platform was registered as, so a manager can see what
    they are looking at and go and fix it.

    ``metrics_status``, ``latest_captured_at``, ``followers`` and
    ``days_since_capture`` are the badge on a channel card. They come from the
    **latest snapshot only**, resolved for a whole page in one query - a card
    never carries history, and the list never asks per channel. All four are
    absent for a channel nobody has recorded anything for, and absent is not
    zero: a card for such a channel says *Chưa có dữ liệu*, never ``0``.
    """

    id: uuid.UUID
    code: str
    name: str
    category: str
    status: str
    brand_id: uuid.UUID | None
    platform_id: uuid.UUID
    tier: int | None
    url: str | None
    platform_code: str | None = None
    policy_grounded_platform: bool = False

    #: Step 1F.2.4a. The canonical network, or ``None`` when the registered
    #: platform code is not one of the six. Never guessed from a name or a URL.
    platform: str | None = None
    #: Vietnamese for :attr:`platform`, or *Chưa xác định*. Sent by the server so
    #: the browser holds no second copy of this table.
    platform_label: str = ""
    #: What the platform was registered as, for the codes outside the six.
    platform_name: str | None = None
    handle: str | None = None
    #: The platform's own account id, for a future connector to match on.
    external_id: str | None = None
    metrics_status: str = PrChannelMetricsStatus.DISCONNECTED.value
    metrics_status_label: str = ""
    latest_captured_at: datetime | None = None
    followers: int | None = None
    days_since_capture: int | None = None

    @classmethod
    def from_row(
        cls,
        row: PrChannel,
        platform_code: str | None = None,
        platform_name: str | None = None,
        summary: ChannelMetricsSummary | None = None,
    ) -> ChannelResponse:
        platform = platform_from_code(platform_code)
        status = summary.status if summary is not None else PrChannelMetricsStatus.DISCONNECTED
        return cls(
            id=row.id,
            code=row.code,
            name=row.name,
            category=row.category.value,
            status=row.status.value,
            brand_id=row.brand_id,
            platform_id=row.platform_id,
            tier=row.tier,
            url=row.url,
            platform_code=platform_code,
            policy_grounded_platform=platform_code in POLICY_GROUNDED_PLATFORM_CODES,
            platform=platform.value if platform is not None else None,
            platform_label=channel_platform_label(platform),
            platform_name=platform_name,
            handle=row.handle,
            external_id=row.external_id,
            metrics_status=status.value,
            metrics_status_label=metrics_status_label(status),
            latest_captured_at=summary.latest_captured_at if summary is not None else None,
            followers=summary.followers if summary is not None else None,
            days_since_capture=summary.days_since_capture if summary is not None else None,
        )


class ChannelAssignmentResponse(BaseModel):
    """Who runs a channel, and for which closed interval.

    ``effective_to`` is the **last day in force**, not the day after. The
    overlap rule in ``meobot.domain.pr.assignments`` reads it that way, and a
    frontend that treated it as exclusive would draw a one-day gap that is not
    there.
    """

    id: uuid.UUID
    channel_id: uuid.UUID
    user_id: uuid.UUID
    assignment_role: str
    effective_from: date
    effective_to: date | None
    is_primary: bool
    allocation_percent: Decimal

    @classmethod
    def from_row(cls, row: PrChannelAssignment) -> ChannelAssignmentResponse:
        return cls(
            id=row.id,
            channel_id=row.channel_id,
            user_id=row.user_id,
            assignment_role=row.assignment_role.value,
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            is_primary=row.is_primary,
            allocation_percent=row.allocation_percent,
        )


class ChannelDetailResponse(BaseModel):
    """A channel, its assignments, and what this person may do here.

    The three flags are the server's decision, taken from the same capability
    the writes require. They exist so a browser never reasons ``role ===
    "OWNER"``: a grant-holder who is not an owner may manage channels, an owner
    whose grant has lapsed may not, and neither of those is visible from a role
    string.
    """

    channel: ChannelResponse
    assignments: list[ChannelAssignmentResponse]
    can_edit_channel: bool = False
    can_record_metrics: bool = False
    can_manage_assignments: bool = False

    @classmethod
    def from_detail(
        cls, detail: ChannelDetail, summary: ChannelMetricsSummary | None = None
    ) -> ChannelDetailResponse:
        return cls(
            channel=ChannelResponse.from_row(
                detail.channel, detail.platform_code, detail.platform_name, summary
            ),
            assignments=[ChannelAssignmentResponse.from_row(row) for row in detail.assignments],
            can_edit_channel=detail.can_edit_channel,
            can_record_metrics=detail.can_record_metrics,
            can_manage_assignments=detail.can_manage_assignments,
        )


class CreateChannelRequest(_Body):
    """New channel.

    ``platform_id`` is required and always has been - a channel exists on a
    platform. Step 1F.2.4a added ``handle`` and left every other field where it
    was; in particular it added **no metrics**, because a channel that has never
    been measured is a perfectly ordinary channel and a create form that
    demanded a follower count would collect a guess.
    """

    name: str = Field(min_length=1, max_length=300)
    platform_id: uuid.UUID
    category: str
    brand_id: uuid.UUID | None = None
    tier: int | None = None
    external_id: str | None = None
    url: str | None = None
    handle: str | None = Field(default=None, max_length=200)
    started_at: date | None = None


class UpdateChannelRequest(_Body):
    """Change a channel. Omitted fields are left alone.

    ``code`` is absent: an allocated code is the channel's identity and
    renumbering one would orphan every reference anybody has written down.

    ``platform_id`` **is** here, added by Step 1F.2.4a: a channel registered on
    the wrong platform used to be unfixable, and that mattered little while a
    platform was a label and matters a great deal now. Changing it moves no
    metric history - the readings stay on the channel that took them, and the
    panel warns first.
    """

    name: str | None = None
    category: str | None = None
    brand_id: uuid.UUID | None = None
    platform_id: uuid.UUID | None = None
    tier: int | None = None
    external_id: str | None = None
    url: str | None = None
    handle: str | None = Field(default=None, max_length=200)
    status: str | None = None


class AssignChannelRequest(_Body):
    """Put somebody on a channel for a date interval."""

    user_id: uuid.UUID
    assignment_role: str
    effective_from: date
    effective_to: date | None = None
    is_primary: bool = False
    allocation_percent: Decimal = Decimal("100")


class CloseAssignmentRequest(_Body):
    """End an assignment on a given day, inclusive."""

    effective_to: date


# --- Channel metrics --------------------------------------------------------


class ChannelMetricSnapshotResponse(BaseModel):
    """One stored reading of a channel's numbers.

    Every metric is ``int | None`` and ``None`` is sent as ``null`` rather than
    ``0``, because they are different facts: a platform that does not report
    reach leaves it absent, and a channel that got no shares last month reports
    zero. A response that flattened the first into the second would put a number
    on a screen that nobody measured.

    ``captured_at`` is the API's name for the column Step 1B called
    ``observed_at``. The column keeps its name; the field is spelled the way the
    step that exposed it speaks, and both mean *the instant this reading
    describes*, which is not when the row was written.
    """

    id: uuid.UUID
    channel_id: uuid.UUID
    captured_at: datetime
    source: str
    source_label: str
    recorded_by_user_id: uuid.UUID | None = None
    #: Resolved server-side by a join. A client must never have to ask
    #: ``/people`` per row to render a history table.
    recorded_by_name: str | None = None

    followers: int | None = None
    following: int | None = None
    posts_count: int | None = None
    views_7d: int | None = None
    views_30d: int | None = None
    reach_7d: int | None = None
    reach_30d: int | None = None
    impressions_7d: int | None = None
    impressions_30d: int | None = None
    engagements_7d: int | None = None
    engagements_30d: int | None = None
    likes_30d: int | None = None
    comments_30d: int | None = None
    shares_30d: int | None = None
    # --- Step 1F.2.4d -----------------------------------------------------
    #: Page likes. Never a copy of ``followers``: on a Facebook Page the two
    #: have been different numbers ever since Meta split them.
    fans: int | None = None
    posts_count_7d: int | None = None
    posts_count_30d: int | None = None
    reactions_30d: int | None = None
    video_views_7d: int | None = None
    video_views_30d: int | None = None
    #: Platform-specific numbers with no canonical column. Sent for completeness
    #: and deliberately not what any summary card reads.
    extra_metrics: dict[str, Any] | None = None

    @classmethod
    def from_view(cls, view: SnapshotView) -> ChannelMetricSnapshotResponse:
        row: PrChannelMetricSnapshot = view.snapshot
        return cls(
            id=row.id,
            channel_id=row.channel_id,
            captured_at=row.observed_at,
            source=row.source.value,
            source_label=metric_source_label(row.source),
            recorded_by_user_id=row.recorded_by_user_id,
            recorded_by_name=view.recorded_by_name,
            extra_metrics=row.extra_metrics,
            **{name: getattr(row, name) for name in MANUAL_METRIC_FIELDS},
        )


class ChannelFollowerTrendResponse(BaseModel):
    """The change in followers between the two most recent readings.

    ``delta_pct`` is ``null`` when the previous reading was zero followers -
    the percentage change from nothing is not a number, and inventing one would
    put a growth rate on a screen that no arithmetic supports.

    The whole object is absent when there is only one reading. There is no
    "trend of zero" for a channel measured once.
    """

    delta: int
    delta_pct: float | None = None

    @classmethod
    def from_trend(cls, trend: ChannelFollowerTrend) -> ChannelFollowerTrendResponse:
        return cls(delta=trend.delta, delta_pct=trend.delta_pct)


class MetricChangeResponse(BaseModel):
    """One metric, then and now, and how far apart "then" really was.

    ``baseline_age_days`` is the field that keeps the card honest. ``window_days``
    is what the panel *asked* for; ``baseline_age_days`` is what it actually
    compared against, and they differ routinely because snapshots land whenever a
    sync ran. A client must print the second, not the first - see the label the
    channel screen renders beside the delta.

    ``delta_pct`` is ``null`` when the baseline was zero. The percentage change
    from nothing is not a number, and the absolute delta is still true.
    """

    metric: str
    window_days: int
    latest: int
    baseline: int
    delta: int
    #: ``UP`` | ``DOWN`` | ``FLAT``. ``FLAT`` means genuinely unchanged; not
    #: knowing is the whole object being absent.
    direction: str
    delta_pct: float | None = None
    baseline_captured_at: datetime | None = None
    baseline_age_days: int | None = None

    @classmethod
    def from_change(cls, change: MetricChange) -> MetricChangeResponse:
        return cls(
            metric=change.metric,
            window_days=change.window_days,
            latest=change.latest,
            baseline=change.baseline,
            delta=change.delta,
            direction=change.direction.value,
            delta_pct=change.delta_pct,
            baseline_captured_at=change.baseline_observed_at,
            baseline_age_days=change.baseline_age_days,
        )


class TopPostResponse(BaseModel):
    """The window's best-performing post, as the connector recorded it.

    Sent so a manager can open the post that worked. ``excerpt`` is a truncated
    copy of the post's own text and nothing else - MeoBot stores no comments and
    sends none.
    """

    post_id: str
    engagements: int
    permalink_url: str | None = None
    created_at: datetime | None = None
    excerpt: str | None = None
    reactions: int | None = None
    comments: int | None = None
    shares: int | None = None

    @classmethod
    def from_domain(cls, post: TopPost) -> TopPostResponse:
        return cls(
            post_id=post.post_id,
            engagements=post.engagements,
            permalink_url=post.permalink_url,
            created_at=post.created_time,
            excerpt=post.excerpt,
            reactions=post.reactions,
            comments=post.comments,
            shares=post.shares,
        )


class UnavailableMetricResponse(BaseModel):
    """One card that will not fill, and why not.

    Sent so a client can group these somewhere other than the main grid. A
    "Reach — " card beside six live numbers reads as a broken sync to every
    manager who sees it, and they are right to read it that way: a blank where a
    number belongs is a defect unless something says otherwise.

    Both the short chip and the full sentence are **composed by the server**. A
    browser that decided which platforms retired reach, or which permissions a
    grant is missing, would be holding platform knowledge one layer too high.
    """

    metric: str
    #: The Vietnamese heading this metric would have carried.
    label: str
    #: ``AVAILABLE`` | ``NOT_RECORDED`` | ``NOT_PERMITTED`` | ``UNSUPPORTED``.
    #: Only the last two ever appear here - the other two fix themselves.
    availability: str
    #: The short chip: "Chưa có quyền đọc", "Không còn được Meta cung cấp".
    availability_label: str
    #: The full sentence, for a tooltip or an information area.
    note: str

    @classmethod
    def from_domain(cls, entry: UnavailableMetric) -> UnavailableMetricResponse:
        return cls(
            metric=entry.metric,
            label=entry.label,
            availability=entry.availability.value,
            availability_label=entry.availability_label,
            note=entry.note,
        )


class MetricCapabilitiesResponse(BaseModel):
    """What this reading's platform and this connection's grant could answer.

    The half of the analytics response that explains the other half. ``null``
    says a card is blank; this says whether waiting, reauthorizing, or nothing
    at all is the fix - which is the difference between a manager filing a bug
    and a manager reading a dashboard.

    Every flag defaults to ``false``. A reading with no capability metadata -
    every snapshot written before this milestone - therefore says "we cannot
    show that this was available", which is the safe direction: the client still
    renders any number that is there and explains nothing it cannot justify.
    """

    reach_available: bool = False
    impressions_available: bool = False
    reactions_available: bool = False
    comments_available: bool = False
    shares_available: bool = False
    video_views_available: bool = False
    page_views_available: bool = False
    #: Whether "bài tốt nhất" is a claim the data supports. ``false`` when the
    #: interaction summaries could not be read: the connector's ranking is then
    #: on shares alone, and ``top_post_30d`` is withheld rather than relabelled.
    top_post_rankable: bool = False
    #: The connector's own per-field words - ``available`` | ``empty`` |
    #: ``not_permitted`` | ``absent`` | ``not_read``. Passed through for an
    #: operator reading the panel with a capability probe open beside it; a
    #: client should branch on the booleans above, never on these strings.
    post_fields: dict[str, str] = Field(default_factory=dict)
    #: Which Page Insights metrics answered on the sync that wrote this reading.
    insight_metrics_available: list[str] = Field(default_factory=list)
    #: The settled 30-day window's last day, ``YYYY-MM-DD``, or ``null``. Meta
    #: Insights settles up to about 48 hours behind, so a monthly figure beside
    #: a sync timestamp from this morning does not reach the current moment.
    window_30d_end: str | None = None
    unavailable: list[UnavailableMetricResponse] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, capabilities: MetricCapabilities) -> MetricCapabilitiesResponse:
        return cls(
            reach_available=capabilities.reach_available,
            impressions_available=capabilities.impressions_available,
            reactions_available=capabilities.reactions_available,
            comments_available=capabilities.comments_available,
            shares_available=capabilities.shares_available,
            video_views_available=capabilities.video_views_available,
            page_views_available=capabilities.page_views_available,
            top_post_rankable=capabilities.top_post_rankable,
            post_fields=dict(capabilities.post_fields),
            insight_metrics_available=list(capabilities.insight_metrics_available),
            window_30d_end=capabilities.window_30d_end,
            unavailable=[
                UnavailableMetricResponse.from_domain(entry) for entry in capabilities.unavailable
            ],
        )


class ChannelAnalyticsResponse(BaseModel):
    """The management view of a channel: current counts, and what they did.

    **Every field may be ``null``, and ``null`` is never ``0``.** A Page whose
    Graph version does not serve video plays has ``video_views_30d = null``; a
    Page that posted no video last month has ``0``. The client renders the first
    as "—" and the second as "0", and the whole point of this response shape is
    that it can tell them apart.

    The derived half is computed **from MeoBot's own stored snapshots**, never
    fetched. That is what makes it survive a Graph version bump, and what makes
    it work identically for a channel somebody records by hand.
    """

    captured_at: datetime

    followers: int | None = None
    fans: int | None = None
    following: int | None = None
    posts_count: int | None = None
    engagements_7d: int | None = None
    engagements_30d: int | None = None
    posts_count_7d: int | None = None
    posts_count_30d: int | None = None
    reactions_30d: int | None = None
    likes_30d: int | None = None
    comments_30d: int | None = None
    shares_30d: int | None = None
    video_views_7d: int | None = None
    video_views_30d: int | None = None
    #: Reported by the platforms that have them and ``null`` on a Facebook
    #: Page, where Graph v23 retired reach and impressions and the account-level
    #: view metric counts profile views. The client draws no card for a ``null``
    #: here rather than a permanently blank one.
    views_7d: int | None = None
    views_30d: int | None = None
    reach_7d: int | None = None
    reach_30d: int | None = None
    impressions_7d: int | None = None
    impressions_30d: int | None = None

    #: Views of the Page's own profile, projected out of ``extra_metrics`` so no
    #: client has to know a connector's JSON key. Under its own name and never
    #: in ``views_*``: a profile view is not what "Views" means on a YouTube card
    #: in the same channel list, and one field meaning two things is how a
    #: dashboard starts lying quietly.
    page_views_7d: int | None = None
    page_views_30d: int | None = None

    follower_growth_7d: MetricChangeResponse | None = None
    follower_growth_30d: MetricChangeResponse | None = None
    engagement_change_7d: MetricChangeResponse | None = None
    engagement_change_30d: MetricChangeResponse | None = None

    #: The follower change as a **percentage** - ``4.8`` for +4,8%. The same
    #: number as ``follower_growth_*.delta_pct``, lifted to the top level because
    #: a client showing a growth rate should not have to reach through a nullable
    #: object for it. ``null`` both when there is no baseline near enough and
    #: when the baseline was zero: the percentage change from nothing is not a
    #: number.
    follower_growth_rate_7d: float | None = None
    follower_growth_rate_30d: float | None = None

    #: Engagements over the month per follower, as a **ratio** - ``0.0247`` for
    #: 2,47%. A ratio rather than a rendered percentage so that one place decides
    #: how many decimals a screen shows.
    engagement_per_follower_30d: float | None = None
    #: Interactions per post over the month, from the post-level sums. Not
    #: ``engagements_30d / posts_count_30d``: those count different populations.
    average_engagement_per_post_30d: float | None = None

    top_post_30d: TopPostResponse | None = None

    #: One Vietnamese sentence about metrics this platform has stopped
    #: reporting, or ``null``. Sent rather than composed in the browser: a
    #: screen that decided which platforms report reach would be holding
    #: platform knowledge the server is the authority on.
    limitation_note: str | None = None

    #: What this platform and this grant could answer, and what they could not.
    #: Always present, so a client never has to decide what an absent
    #: capabilities object would have meant.
    capabilities: MetricCapabilitiesResponse = Field(default_factory=MetricCapabilitiesResponse)

    @classmethod
    def from_domain(cls, analytics: ChannelAnalytics) -> ChannelAnalyticsResponse:
        def change(value: MetricChange | None) -> MetricChangeResponse | None:
            return None if value is None else MetricChangeResponse.from_change(value)

        return cls(
            captured_at=analytics.observed_at,
            followers=analytics.followers,
            fans=analytics.fans,
            following=analytics.following,
            posts_count=analytics.posts_count,
            engagements_7d=analytics.engagements_7d,
            engagements_30d=analytics.engagements_30d,
            posts_count_7d=analytics.posts_count_7d,
            posts_count_30d=analytics.posts_count_30d,
            reactions_30d=analytics.reactions_30d,
            likes_30d=analytics.likes_30d,
            comments_30d=analytics.comments_30d,
            shares_30d=analytics.shares_30d,
            video_views_7d=analytics.video_views_7d,
            video_views_30d=analytics.video_views_30d,
            views_7d=analytics.views_7d,
            views_30d=analytics.views_30d,
            reach_7d=analytics.reach_7d,
            reach_30d=analytics.reach_30d,
            impressions_7d=analytics.impressions_7d,
            impressions_30d=analytics.impressions_30d,
            page_views_7d=analytics.page_views_7d,
            page_views_30d=analytics.page_views_30d,
            follower_growth_7d=change(analytics.follower_growth_7d),
            follower_growth_30d=change(analytics.follower_growth_30d),
            engagement_change_7d=change(analytics.engagement_change_7d),
            engagement_change_30d=change(analytics.engagement_change_30d),
            follower_growth_rate_7d=analytics.follower_growth_rate_7d,
            follower_growth_rate_30d=analytics.follower_growth_rate_30d,
            engagement_per_follower_30d=analytics.engagement_per_follower_30d,
            average_engagement_per_post_30d=analytics.average_engagement_per_post_30d,
            top_post_30d=(
                TopPostResponse.from_domain(analytics.top_post_30d)
                if analytics.top_post_30d is not None
                else None
            ),
            limitation_note=analytics.limitation_note,
            capabilities=MetricCapabilitiesResponse.from_domain(analytics.capabilities),
        )


class ChannelMetricsResponse(BaseModel):
    """The whole metrics panel, in one answer.

    One request, because the alternative is three - latest, previous, history -
    that a client would have to keep consistent with each other. ``latest`` and
    ``previous`` are the first two rows of the same ordering ``history`` uses,
    so the cards and the top of the table can never describe different readings.

    ``status`` is derived, never stored: a snapshot exists, therefore *Nhập thủ
    công*; none does, therefore *Chưa có dữ liệu*. It cannot say ``CONNECTED_API``
    because Step 1F.2.4a built no connector.
    """

    channel_id: uuid.UUID
    #: ``DISCONNECTED`` | ``MANUAL`` | ``CONNECTED_API``.
    status: str
    status_label: str
    latest: ChannelMetricSnapshotResponse | None = None
    previous: ChannelMetricSnapshotResponse | None = None
    trend: ChannelFollowerTrendResponse | None = None
    history: list[ChannelMetricSnapshotResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0
    #: Whole days since the latest reading, computed here so no browser does
    #: date arithmetic on a timestamp.
    days_since_capture: int | None = None
    #: Step 1F.2.4d. The management view - current counts plus everything
    #: derived from this channel's own history. ``null`` only when the channel
    #: has never been measured at all; a channel with one reading gets an object
    #: whose derived half is full of ``null``, which is a different statement.
    analytics: ChannelAnalyticsResponse | None = None
    can_record_metrics: bool = False
    #: Whether this channel already has readings, which is what makes changing
    #: its platform worth a warning rather than a silent edit.
    has_history: bool = False

    @classmethod
    def from_view(cls, view: ChannelMetricsView) -> ChannelMetricsResponse:
        return cls(
            channel_id=view.channel.id,
            status=view.status.value,
            status_label=metrics_status_label(view.status),
            latest=(
                ChannelMetricSnapshotResponse.from_view(view.latest)
                if view.latest is not None
                else None
            ),
            previous=(
                ChannelMetricSnapshotResponse.from_view(view.previous)
                if view.previous is not None
                else None
            ),
            trend=(
                ChannelFollowerTrendResponse.from_trend(view.trend)
                if view.trend is not None
                else None
            ),
            history=[ChannelMetricSnapshotResponse.from_view(row) for row in view.history],
            total=view.total,
            limit=view.limit,
            offset=view.offset,
            days_since_capture=view.days_since_capture,
            analytics=(
                ChannelAnalyticsResponse.from_domain(view.analytics)
                if view.analytics is not None
                else None
            ),
            can_record_metrics=view.can_record_metrics,
            has_history=view.has_history,
        )


class FinishConnectionRequest(_Body):
    """Complete an authorization the browser already carried out.

    Both fields come from Google's redirect, and neither is trusted: ``state``
    is looked up as a hash and must be unexpired, unconsumed and owned by the
    session presenting it, and ``code`` is passed straight to the token exchange
    without ever being logged.

    **There is no channel field.** The channel is read from the state row MeoBot
    wrote when it started the flow - accepting one here would be exactly the
    injection the state exists to prevent.
    """

    state: str = Field(min_length=1, max_length=512)
    code: str = Field(min_length=1, max_length=2048)


class ChannelConnectionResponse(BaseModel):
    """A channel's link to a platform account, with **no credential in sight**.

    Every field here is identity, state or health. There is no token field, no
    scope *value*, no client id and nothing that could be replayed - and that is
    structural rather than careful: the response is built from an explicit field
    list, so a column added to ``pr_channel_connections`` appears here only when
    somebody decides it should.

    ``state`` and ``sync_status`` are separate because they answer different
    questions and come apart constantly. A connection whose last five syncs hit
    a quota limit is ``CONNECTED`` with ``sync_status = FAILED``: the credential
    is fine and the data is stale, and a single "connected" badge would have to
    hide one of those.
    """

    id: uuid.UUID
    channel_id: uuid.UUID
    provider: str
    #: ``CONNECTED`` | ``ACTION_REQUIRED`` | ``DISCONNECTED``.
    state: str
    state_label: str
    #: The platform's own id for the bound account - a public identifier, and
    #: what a manager checks when they suspect they authorized the wrong one.
    provider_account_id: str
    provider_account_name: str | None = None
    provider_account_handle: str | None = None
    #: ``NEVER_SYNCED`` | ``SYNCING`` | ``SUCCESS`` | ``FAILED``.
    sync_status: str
    sync_status_label: str
    last_sync_succeeded_at: datetime | None = None
    last_sync_failed_at: datetime | None = None
    #: The safe error class, never a provider message.
    last_sync_error_code: str | None = None
    #: A short Vietnamese sentence MeoBot wrote. Never provider prose.
    last_sync_error_message: str | None = None
    #: Whole days since the last **successful** sync, computed server-side.
    days_since_success: int | None = None
    auto_sync_enabled: bool = True
    connected_at: datetime | None = None

    @classmethod
    def from_row(
        cls, row: PrChannelConnection, *, days_since_success: int | None = None
    ) -> ChannelConnectionResponse:
        return cls(
            id=row.id,
            channel_id=row.channel_id,
            provider=row.provider.value,
            state=row.status.value,
            state_label=connection_state_label(row.status),
            provider_account_id=row.provider_account_id,
            provider_account_name=row.provider_account_name,
            provider_account_handle=row.provider_account_handle,
            sync_status=row.sync_status.value,
            sync_status_label=sync_status_label(row.sync_status),
            last_sync_succeeded_at=row.last_sync_succeeded_at,
            last_sync_failed_at=row.last_sync_failed_at,
            last_sync_error_code=(
                row.last_sync_error_code.value if row.last_sync_error_code else None
            ),
            last_sync_error_message=row.last_sync_error_message,
            days_since_success=days_since_success,
            auto_sync_enabled=row.auto_sync_enabled,
            connected_at=row.connected_at,
        )


class ChannelConnectionStateResponse(BaseModel):
    """What the connection panel draws, connected or not.

    Returned even when there is no connection, because "this platform has a
    connector and this channel has not used it" and "this platform has no
    connector at all" are different screens and the browser must not decide
    which by matching on a platform string.
    """

    channel_id: uuid.UUID
    #: Whether MeoBot has a connector for this channel's platform at all.
    supported: bool
    #: Whether this deployment has the connector configured. False here with
    #: ``supported`` true means an operator has env vars to set, not that the
    #: feature is missing.
    configured: bool
    provider: str | None = None
    #: Vietnamese for :attr:`provider`, sent by the server so the panel holds no
    #: second copy of the platform-name table.
    provider_label: str | None = None
    connection: ChannelConnectionResponse | None = None
    #: Server-decided, from the same capability every connection write requires.
    can_manage_connection: bool = False
    #: The cadence, in words, when auto-sync is on. Global configuration rather
    #: than a per-channel schedule - shown, never offered as a selector.
    auto_sync_label: str | None = None


class ConnectionAuthorizationResponse(BaseModel):
    """Where to send the browser for consent.

    The URL is built server-side and points at Google. It carries an opaque
    single-use state; the client's only job is to navigate to it.
    """

    authorization_url: str
    expires_at: datetime


class DiscoveredAccountResponse(BaseModel):
    """One account a channel could be bound to.

    **No credential field, and there never can be one**: this is built from
    :class:`~meobot.domain.pr.channel_metrics.DiscoveredProviderAccount`, which
    has nowhere to put a token. The Page access tokens Meta returns beside these
    stay inside the provider.
    """

    account_id: str
    name: str
    handle: str | None = None
    #: How the account was reached - the Facebook Page an Instagram account
    #: hangs off - so somebody managing several Pages can tell two similarly
    #: named accounts apart.
    via: str | None = None

    @classmethod
    def from_domain(cls, account: DiscoveredProviderAccount) -> DiscoveredAccountResponse:
        return cls(
            account_id=account.account_id,
            name=account.name,
            handle=account.handle,
            via=account.via,
        )


class AccountChoicesResponse(BaseModel):
    """The account picker's data, for a connection waiting on a choice."""

    channel_id: uuid.UUID
    provider: str
    accounts: list[DiscoveredAccountResponse] = Field(default_factory=list)

    @classmethod
    def from_choices(cls, choices: AccountChoices) -> AccountChoicesResponse:
        return cls(
            channel_id=choices.channel_id,
            provider=choices.provider.value,
            accounts=[DiscoveredAccountResponse.from_domain(a) for a in choices.accounts],
        )


class SelectAccountRequest(_Body):
    """Bind one discovered account.

    The id is **not** trusted. The server recomputes the discovery from the
    stored credential and refuses anything that is not in the result, so posting
    an arbitrary Page id gets a refusal rather than a binding.
    """

    account_id: str = Field(min_length=1, max_length=200)


class ConnectionOutcomeResponse(BaseModel):
    """What a finished authorization actually bound.

    ``rebound_from_account_id`` and ``external_id_conflict`` exist so nothing is
    silent: a reconnect that landed on a different YouTube account, or an
    account id that disagrees with what somebody typed into the channel, are
    both things a manager has to see rather than discover in a month.
    """

    connection: ChannelConnectionResponse
    is_reconnect: bool
    rebound_from_account_id: str | None = None
    external_id_conflict: str | None = None
    #: Step 1F.2.4c. Non-empty when consent succeeded and nothing is bound yet -
    #: the person manages several Pages or Instagram accounts and must pick one.
    #: The connection is ``PENDING_SELECTION`` and cannot sync until they do.
    pending_accounts: list[DiscoveredAccountResponse] = Field(default_factory=list)

    @classmethod
    def from_outcome(cls, outcome: ConnectionOutcome) -> ConnectionOutcomeResponse:
        return cls(
            connection=ChannelConnectionResponse.from_row(outcome.connection),
            is_reconnect=outcome.is_reconnect,
            rebound_from_account_id=outcome.rebound_from_account_id,
            external_id_conflict=outcome.external_id_conflict,
            pending_accounts=[
                DiscoveredAccountResponse.from_domain(a) for a in outcome.pending_accounts
            ],
        )


# --- The TikTok account panel (Step 1F.2.9) ---------------------------------
#
# Every model below is an **explicit field list** built from
# :class:`~meobot.application.pr_tiktok_account_service.TikTokAccountView`, which
# itself holds no credential. A token cannot appear here by omission, by a new
# column on ``pr_channel_connections``, or by somebody adding a field to a
# provider dataclass: it would have to be written into one of these classes on
# purpose. ``test_pr_tiktok_app_review`` renders a full panel over a fake TikTok
# and asserts the access token, the refresh token and the client secret appear
# nowhere in the serialised response.


class TikTokAccountResponse(BaseModel):
    """Who the connected TikTok account is.

    Identity and profile together, because that is how a person checks them: the
    question this half of the panel answers is *"is this the account I meant to
    authorize?"*, and an avatar beside a handle answers it in a glance where an
    ``open_id`` alone does not.

    Every field except ``open_id`` is nullable, and the nulls are meaningful
    rather than defensive. ``username``, ``profile_url``, ``bio`` and
    ``is_verified`` all come from ``user.info.profile``; an app not yet approved
    for it gets ``null`` for all four, and the panel says *"chưa được cấp
    quyền"* rather than drawing an unnamed, unverified account.
    """

    #: TikTok's stable per-app identifier, and what the connection binds to.
    #: A public identifier, not a secret - it is what somebody compares against
    #: TikTok's own developer console when a connection looks wrong.
    open_id: str
    display_name: str | None = None
    username: str | None = None
    #: ``@username``, composed once here so no browser has to know that TikTok
    #: handles carry an ``@``.
    handle: str | None = None
    avatar_url: str | None = None
    #: TikTok's own deep link, never one MeoBot composed from a username - a
    #: composed URL would be wrong the moment somebody renamed themselves.
    profile_url: str | None = None
    bio: str | None = None
    #: ``None`` when ``user.info.profile`` was not granted. Deliberately not
    #: ``False``: an unverified account and an unreadable one are different
    #: statements.
    is_verified: bool | None = None


class TikTokStatsResponse(BaseModel):
    """The four lifetime counters ``user.info.stats`` serves.

    **All four are lifetime totals with no reporting window**, which is the one
    thing about this block that must not be mis-read. In particular
    ``likes_count`` is every like the account has ever received across every
    video - not a week's and not a month's - and it is thousands of times larger
    than a windowed figure. It has no canonical metric column for exactly that
    reason; see
    :data:`~meobot.integrations.tiktok.provider.TIKTOK_TOTAL_LIKES_KEY`.

    ``availability`` says why the block is blank when it is blank, in the same
    vocabulary the connector and the probe use.
    """

    follower_count: int | None = None
    following_count: int | None = None
    #: Lifetime. See the class docstring.
    likes_count: int | None = None
    video_count: int | None = None
    #: ``available`` | ``empty`` | ``not_permitted`` | ``unsupported`` | ``not_read``.
    availability: str = "available"
    availability_label: str = ""


class TikTokVideoResponse(BaseModel):
    """One recent public video, as much of it as TikTok served.

    Nullable counters throughout, and that is the correctness property: a video
    with genuinely no comments and a video whose ``comment_count`` this app is
    not served must not both render ``0``. The browser prints "—" for the second
    and ``0`` for the first because the server told it which is which.

    ``cover_image_url`` is a TikTok CDN link that **expires within hours**. It is
    passed through to the browser looking at the panel now and stored nowhere -
    a saved copy would be a broken image by tomorrow.
    """

    video_id: str
    title: str | None = None
    description: str | None = None
    #: TikTok's ``create_time``, an epoch second, as an instant. Formatted by
    #: the browser in the reader's own locale, like every other timestamp here.
    created_at: datetime | None = None
    duration_seconds: int | None = None
    cover_image_url: str | None = None
    #: Where "Xem trên TikTok" goes. TikTok's own canonical link to the video.
    share_url: str | None = None
    embed_link: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    share_count: int | None = None

    @classmethod
    def from_domain(cls, video: TikTokVideoSummary) -> TikTokVideoResponse:
        return cls(
            video_id=video.video_id,
            title=video.title,
            description=video.description,
            created_at=video.created_at,
            duration_seconds=video.duration_seconds,
            cover_image_url=video.cover_image_url,
            share_url=video.share_url,
            embed_link=video.embed_link,
            view_count=video.view_count,
            like_count=video.like_count,
            comment_count=video.comment_count,
            share_count=video.share_count,
        )


class TikTokScopeResponse(BaseModel):
    """One permission this connection asked for, and whether TikTok granted it.

    Sent for **every** scope the connector requests, granted or not, rather than
    only the granted ones. A list that silently omitted a refused scope would
    make an app awaiting review look identical to one fully approved, and the
    difference is the whole reason the panel draws this block.

    A scope name is not a secret: it is the line a person read on TikTok's own
    consent screen. A token is - and there is nowhere here to put one.
    """

    scope: str
    label: str
    description: str
    granted: bool


class TikTokOverviewResponse(BaseModel):
    """The whole TikTok account panel, in one answer.

    One request rather than four - identity, stats, videos, permissions - because
    they are one live look at one account and a client that fetched them
    separately could draw a screen where the halves disagree about which account
    they describe.
    """

    channel_id: uuid.UUID
    provider: str
    #: The connection's state, from the same row and the same label table the
    #: connection panel uses. Present so the account panel and the status panel
    #: can never disagree about whether this channel is connected.
    state: str
    state_label: str
    account: TikTokAccountResponse
    stats: TikTokStatsResponse
    #: What happened to the ``user.info.profile`` group - the one that carries
    #: the username, the deep link, the bio and the verification flag.
    profile_availability: str
    profile_availability_label: str
    videos: list[TikTokVideoResponse] = Field(default_factory=list)
    #: ``available`` | ``empty`` | ``not_permitted`` | ``unsupported``.
    videos_availability: str = "available"
    videos_availability_label: str = ""
    #: What happened to the four per-video counters, which can be missing while
    #: the videos themselves are perfectly readable.
    video_counters_availability: str = "available"
    video_counters_availability_label: str = ""
    #: TikTok's own opaque continuation token. Passed back verbatim on "Xem
    #: thêm" and never interpreted - see
    #: :class:`~meobot.integrations.tiktok.client.VideoPage`.
    videos_cursor: int | None = None
    #: TikTok's own flag, and the only thing that decides whether another page
    #: exists.
    videos_has_more: bool = False
    #: How many pages a client may ask for in total. The connector's ceiling,
    #: sent so the bound on "Xem thêm" is the server's rather than a number the
    #: browser picked.
    max_video_pages: int = 0
    granted_scopes: list[str] = Field(default_factory=list)
    scopes: list[TikTokScopeResponse] = Field(default_factory=list)
    #: When MeoBot asked TikTok. Not when the numbers became true - the Display
    #: API has no reporting window at all, so there is no second time to
    #: conflate this with.
    fetched_at: datetime
    #: From the connection row: when a *snapshot* sync last succeeded. A
    #: different fact from ``fetched_at`` and shown as one.
    last_sync_succeeded_at: datetime | None = None
    sync_status: str = ""
    sync_status_label: str = ""
    #: True when this call also handed the connection to the existing sync
    #: infrastructure. False on a plain read, and false on a refresh that found
    #: a sync already in flight - which is not a failure.
    sync_requested: bool = False
    can_manage_connection: bool = False

    @classmethod
    def from_view(cls, view: TikTokAccountView) -> TikTokOverviewResponse:
        account = view.account
        availability = dict(account.availability)
        # The username travels in its own group - TikTok added it to
        # ``user.info.profile`` later than the rest, so an app approved before
        # that gets the group refused for asking. The panel reports the profile
        # group's word, which is the one that explains a missing bio and a
        # missing verification flag; a missing handle alone is a smaller loss
        # and shows as "—".
        profile_word = availability.get("profile", "not_read")
        stats_word = availability.get("stats", "not_read")
        connection = view.connection
        granted = set(view.granted_scopes)
        return cls(
            channel_id=view.channel.id,
            provider=connection.provider.value,
            state=connection.status.value,
            state_label=connection_state_label(connection.status),
            account=TikTokAccountResponse(
                open_id=account.open_id,
                display_name=account.display_name,
                username=account.username,
                handle=f"@{account.username}" if account.username else None,
                avatar_url=account.avatar_url,
                profile_url=account.profile_deep_link,
                bio=account.bio_description,
                is_verified=account.is_verified,
            ),
            stats=TikTokStatsResponse(
                follower_count=account.follower_count,
                following_count=account.following_count,
                likes_count=account.likes_count,
                video_count=account.video_count,
                availability=stats_word,
                availability_label=field_availability_label(stats_word),
            ),
            profile_availability=profile_word,
            profile_availability_label=field_availability_label(profile_word),
            videos=[TikTokVideoResponse.from_domain(row) for row in view.videos.videos],
            videos_availability=view.videos.availability,
            videos_availability_label=field_availability_label(view.videos.availability),
            video_counters_availability=view.videos.counters,
            video_counters_availability_label=field_availability_label(view.videos.counters),
            videos_cursor=view.videos.cursor,
            videos_has_more=view.videos.has_more,
            max_video_pages=view.max_video_pages,
            granted_scopes=list(view.granted_scopes),
            # Every requested scope, in the connector's own order, each marked
            # with whether the token actually carries it.
            scopes=[
                TikTokScopeResponse(
                    scope=scope,
                    label=tiktok_scope_label(scope),
                    description=tiktok_scope_description(scope),
                    granted=scope in granted,
                )
                for scope in TIKTOK_SCOPES
            ],
            fetched_at=view.fetched_at,
            last_sync_succeeded_at=connection.last_sync_succeeded_at,
            sync_status=connection.sync_status.value,
            sync_status_label=sync_status_label(connection.sync_status),
            sync_requested=view.sync_requested,
            can_manage_connection=view.can_manage_connection,
        )


class RecordChannelMetricsRequest(_Body):
    """One hand-entered reading.

    **No ``source`` and no ``recorded_by_user_id``**, and ``extra="forbid"``
    means a body carrying either is refused rather than ignored. This endpoint
    records what a person typed: the source is ``MANUAL`` because the service
    writes ``MANUAL``, and the recorder is the authenticated session because the
    service reads the actor. Neither is a field a client can supply, which is
    what stops a browser from labelling its own typing as an API reading or
    attributing it to a colleague.

    ``ge=0`` on every metric is the transport's half of a rule the service
    enforces again and the database constrains a third time. Zero is valid
    everywhere; leaving a field out means *not recorded*.
    """

    captured_at: datetime
    followers: int | None = Field(default=None, ge=0)
    following: int | None = Field(default=None, ge=0)
    posts_count: int | None = Field(default=None, ge=0)
    views_7d: int | None = Field(default=None, ge=0)
    views_30d: int | None = Field(default=None, ge=0)
    reach_7d: int | None = Field(default=None, ge=0)
    reach_30d: int | None = Field(default=None, ge=0)
    impressions_7d: int | None = Field(default=None, ge=0)
    impressions_30d: int | None = Field(default=None, ge=0)
    engagements_7d: int | None = Field(default=None, ge=0)
    engagements_30d: int | None = Field(default=None, ge=0)
    likes_30d: int | None = Field(default=None, ge=0)
    comments_30d: int | None = Field(default=None, ge=0)
    shares_30d: int | None = Field(default=None, ge=0)
    fans: int | None = Field(default=None, ge=0)
    posts_count_7d: int | None = Field(default=None, ge=0)
    posts_count_30d: int | None = Field(default=None, ge=0)
    reactions_30d: int | None = Field(default=None, ge=0)
    video_views_7d: int | None = Field(default=None, ge=0)
    video_views_30d: int | None = Field(default=None, ge=0)
    extra_metrics: dict[str, Any] | None = None


# --- Capabilities -----------------------------------------------------------


class GrantScopeBody(BaseModel):
    """Where one grant applies, on the wire.

    Two axes, each a mode plus - when the mode is ``SELECTED`` - the values it
    selected. Canonical codes and ids only: ``content_types`` are
    :class:`~meobot.domain.pr.models.PrContentType` members and ``channel_ids``
    are ``pr_channels.id``. Nothing here is a display label, so renaming a
    channel cannot change who may approve.

    The two ``include_*`` flags are how a ``SELECTED`` scope reaches the states
    an item can be *missing*: no classification, no channel. Both default to
    ``false`` - absent is not a wildcard - and both are implied by ``ALL``.
    """

    content_type_scope: str = PrGrantScopeMode.SELECTED.value
    content_types: list[str] = Field(default_factory=list)
    include_unclassified_content: bool = False
    channel_scope: str = PrGrantScopeMode.SELECTED.value
    channel_ids: list[uuid.UUID] = Field(default_factory=list)
    include_unassigned_channel: bool = False

    @classmethod
    def from_domain(cls, scope: GrantScope) -> GrantScopeBody:
        return cls(
            content_type_scope=scope.content_type_scope.value,
            content_types=sorted(value.value for value in scope.content_types),
            include_unclassified_content=scope.include_unclassified_content,
            channel_scope=scope.channel_scope.value,
            channel_ids=sorted(scope.channel_ids, key=str),
            include_unassigned_channel=scope.include_unassigned_channel,
        )


class CapabilityGrantResponse(BaseModel):
    """One grant, with its whole scope. ``note`` is deliberately not exposed.

    A grant note is written for the audit trail, sometimes about a person's
    circumstances, and a permissions screen is a page many people can open.

    ``id`` is what a revocation names. Step 1F.2.7 made one person able to hold
    several grants of one gate over different scopes, so ``(user, capability)``
    stopped identifying a grant and the client has to say which one.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    capability: str
    scope: GrantScopeBody
    effective_from: date | None
    effective_to: date | None
    #: ``true`` when the holder's role must *also* carry the capability's
    #: permission. Only rows that predate Step 1F.2.7 - see revision 0031.
    requires_role_baseline: bool
    granted_by_user_id: uuid.UUID | None

    @classmethod
    def from_grant(cls, grant: PrCapabilityGrant) -> CapabilityGrantResponse:
        return cls(
            id=grant.id,
            user_id=grant.user_id,
            capability=grant.capability.value,
            scope=GrantScopeBody.from_domain(grant.scope),
            effective_from=grant.effective_from,
            effective_to=grant.effective_to,
            requires_role_baseline=grant.requires_role_baseline,
            granted_by_user_id=grant.granted_by_user_id,
        )

    @classmethod
    def from_row(cls, row: PrUserCapability) -> CapabilityGrantResponse:
        return cls.from_grant(PrCapabilityGrant.from_row(row))


class GrantCapabilityRequest(_Body):
    """Grant a review capability to somebody, over a scope.

    ``scope`` is required. There is no "unscoped grant" to fall back on: the
    whole point of the step is that a grant says where it applies, and a body
    that omitted it would be asking the server to guess how much authority to
    hand out.
    """

    user_id: uuid.UUID
    capability: str
    scope: GrantScopeBody
    effective_from: date | None = None
    effective_to: date | None = None
    note: str | None = None


class RevokeCapabilityRequest(_Body):
    """Withdraw one grant, by id.

    ``(user_id, capability)`` is still accepted for a caller that predates
    scoped grants, and resolves only when the person holds exactly one active
    grant of that gate - with two, the server refuses rather than guessing which
    right to take away.
    """

    grant_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    capability: str | None = None
    effective_to: date | None = None


# --- Publications -----------------------------------------------------------


class PublicationResponse(BaseModel):
    """One recorded publication.

    Recorded, not performed: nothing in this API posts to TikTok or Facebook.

    Step 1F.2.3f added the output reference. Exactly one of
    ``production_submission_id`` and ``derivative_id`` is set on anything
    recorded since; **both are ``None`` on rows written before it**, which is a
    real and permanent state - inventing lineage for them would have been
    fabricating production history. A client renders that as *"không rõ sản
    phẩm"* rather than as a blank, because the two are different claims.

    The output's own **location is deliberately not copied here.** It lives on
    the submission or derivative this points at, and the panel resolves it from
    the lists it already loaded for the same page - one representation of one
    file. ``url`` is a different thing entirely: the live public post.
    """

    id: uuid.UUID
    code: str
    content_id: uuid.UUID
    channel_id: uuid.UUID
    #: The master that was published, or ``None``.
    production_submission_id: uuid.UUID | None
    #: The derivative that was published, or ``None``.
    derivative_id: uuid.UUID | None
    published_at: datetime
    #: The live public post. Never the output's storage location.
    url: str | None
    note: str | None
    platform_post_id: str | None
    #: Who recorded it, when anybody knows. Nullable for the same reason the
    #: column is: an imported back catalogue has no publisher.
    publisher_user_id: uuid.UUID | None
    #: Step 1F.2.3f.1. ``PUBLISHED``, ``REMOVED``, ``UNAVAILABLE`` or
    #: ``REVERSED``. Carried as the code because it is the machine-readable
    #: truth; the words a person reads are the client's label table, exactly as
    #: for a stage or a priority.
    status: str
    #: Whether this row still asserts that something went out - the server's
    #: answer, from
    #: :func:`~meobot.domain.pr.reporting.is_active_publication`, so no client
    #: decides it by comparing a string. A reversed publication stays in the
    #: history and stops counting.
    is_active: bool
    #: Step 1F.2.3f.2. Whether **this session** may correct this row.
    #:
    #: Per row rather than per content, because since this step the answer
    #: genuinely differs down the list: a contributor may fix the link on the
    #: posting they recorded and not on the one beside it. A content-level
    #: boolean could only have been wrong for half the rows, and a client
    #: comparing ``publisher_user_id`` to a session id would be re-deriving an
    #: authorization rule the server already owns.
    can_edit: bool = False
    #: Step 1F.2.3f.2. Whether this session may take this row back. Management
    #: only, and unchanged by the contributor capability - so it is usually
    #: ``False`` where ``can_edit`` is ``True``.
    can_reverse: bool = False

    @classmethod
    def from_row(
        cls, row: PrPublication, *, can_edit: bool = False, can_reverse: bool = False
    ) -> PublicationResponse:
        return cls(
            id=row.id,
            code=row.code,
            content_id=row.content_id,
            channel_id=row.channel_id,
            production_submission_id=row.production_submission_id,
            derivative_id=row.derivative_id,
            published_at=row.published_at,
            url=row.url,
            note=row.note,
            platform_post_id=row.platform_post_id,
            publisher_user_id=row.publisher_user_id,
            status=row.status.value,
            is_active=is_active_publication(row.status),
            can_edit=can_edit,
            can_reverse=can_reverse,
        )


class UpdatePublicationRequest(_Body):
    """A correction to a publication already on record. Step 1F.2.3f.1.

    Three fields, and **deliberately no ``channel_id`` and no output
    reference**: those define what the row means, and a wrong one is fixed by
    reversing the publication and recording a new one, which leaves both facts
    visible. See
    :class:`~meobot.application.pr_publication_service.UpdatePublicationCommand`.
    """

    url: str | None = None
    published_at: datetime | None = None
    note: str | None = None


class ReversePublicationResponse(BaseModel):
    """What a reversal did, and what it deliberately did not. Step 1F.2.3f.1.

    The publication is always marked; the content's stage follows only when it is
    safe, and ``reason`` names what stopped it when it did not - so a client can
    explain the outcome rather than show a control that appeared to do nothing.
    """

    publication: PublicationResponse
    #: ``True`` when the content went back to ``READY_TO_PUBLISH``.
    stage_reverted: bool
    #: ``other_active_publications``, ``has_metrics`` or
    #: ``no_publication_transition``. ``None`` when the stage did move.
    reason: str | None
    #: Where the content stands now, so a client need not refetch to re-render
    #: the header.
    workflow_stage: str


class RegisterPublicationRequest(_Body):
    """Record that something went out.

    **Exactly one output reference is required** - see
    :meth:`~meobot.application.pr_publication_service.PrPublicationService.register_publication`.
    Neither is refused with a 422 rather than stored, because a publication that
    cannot say which cut it was is the incomplete record this step exists to stop
    producing; both is refused by the service *and* by a database ``CHECK``.

    ``channel_id`` need **not** be one of the content's planned targets. That is
    the point of the reuse case: a channel created after the plan was written is
    exactly where a re-cut goes.
    """

    channel_id: uuid.UUID
    published_at: datetime
    production_submission_id: uuid.UUID | None = None
    derivative_id: uuid.UUID | None = None
    platform_post_id: str | None = None
    #: The live post URL. Validated as ``http(s)`` with a host - see
    #: :func:`~meobot.domain.pr.assets.normalize_publication_url` - and never
    #: fetched.
    url: str | None = None
    note: str | None = None
    publisher_user_id: uuid.UUID | None = None


# --- Dashboard --------------------------------------------------------------


class StageCountResponse(BaseModel):
    """How many items stand at one stage."""

    stage: str
    count: int


class ProductionStateCountResponse(BaseModel):
    """How many items stand at one derived production state.

    Step 1F.2.3c, and it exists because the production half of the board is four
    columns over three stages: ``APPROVED`` with nobody on it is *chờ nhận sản
    xuất* and ``APPROVED`` with a producer is *sẵn sàng sản xuất*, and a count
    per stage cannot tell a client how many of each there are. Derived on the
    server for the same reason ``ContentSummaryResponse.production_state`` is -
    combining the stage and the producer is a rule, not a rendering.
    """

    production_state: str
    count: int


class ContentBoardResponse(BaseModel):
    """One filtered page of content, with the counts that describe the filter.

    Step 1F.2.2, and the reason it is an envelope rather than a bare list: the
    board's cards used to come from ``/contents`` and its tiles from
    ``/dashboard``, which shared no filter, so a scoped view of seven items could
    be captioned "Chờ duyệt: 120". One response, one filter, one set of numbers.

    ``total`` and ``stage_counts`` are about the **whole** filtered set and
    ``items`` is one page of it, which is what lets a client say "7 / 213"
    honestly. Deriving either from ``len(items)`` after a ``LIMIT`` is the mistake
    this shape exists to prevent.

    With a ``group`` in the request the two describe deliberately different sets,
    and a client must not mix them: ``items`` and ``total`` are that group's - so
    a five-item group is one page of five - while ``stage_counts`` and
    ``production_state_counts`` stay over the filters *without* the group,
    because they label the tabs that lead out of it. Step 1F.2.3c1.

    With a ``lane`` as well, this is one column of the board: ``items`` is that
    lane's page, ``total`` is that lane's whole queue, and the two count tables
    are **empty** because a lane request did not ask about the board. Step
    1F.2.3c2 - four such requests draw a four-column board, each column paging
    on its own, and the figures come from one further request that names no lane
    (``limit=0``, which returns the counts and no rows at all).

    ``scope`` is echoed back because a client may send none and let the server
    pick - see :func:`~meobot.domain.pr.content_views.default_scope`. Without the
    echo, a tab strip would have to guess which view it is showing, which is the
    browser deciding the default all over again.

    Declared here rather than beside ``ContentSummaryResponse`` because it needs
    ``StageCountResponse``, and a Pydantic model cannot be built against a name
    that does not exist yet.
    """

    items: list[ContentSummaryResponse]
    total: int
    #: The scope actually applied - the one requested, or this actor's default.
    scope: str
    #: Every stage, zeros included, over the current filter.
    stage_counts: list[StageCountResponse]
    #: Every production state, zeros included, over the **same** filter. Step
    #: 1F.2.3c: the board's production columns are labelled from these rather
    #: than from the cards on screen, so a lane says 47 on the page that shows
    #: 20 of them.
    production_state_counts: list[ProductionStateCountResponse]
    #: Step 1F.2.3f.6. **The reporting month actually applied**, ``YYYY-MM``, or
    #: ``null`` for the cumulative board. Echoed for the reason ``scope`` is: a
    #: client may send ``period=CURRENT`` and the server says which month that
    #: was, in the business calendar - so the selector never guesses.
    period: str | None
    #: Step 1F.2.3f.6a. Whether ``period`` actually narrowed this response.
    #: ``false`` under ``MY_ACTIONS``: *Cần tôi xử lý* is an action queue and is
    #: read month-free, carry-over included, while the month is still echoed so
    #: the selector keeps its value. The server says so; a client must not
    #: infer it from the scope it *asked* for.
    period_applied: bool
    #: Step 1F.2.3f.6b. The current business month, ``YYYY-MM``, whatever
    #: ``period`` was selected. The upper anchor of the month selector: options
    #: run from here backwards, so the current month is always one click away.
    #: ``null`` only for the ``limit=0``-less flat callers that skip the page
    #: resolution, which no board request is.
    current_period: str | None
    #: Step 1F.2.3f.6c. ``ACTIVE`` or ``ARCHIVE`` - which board this is. The
    #: board route never answers without one.
    view: str | None
    limit: int
    offset: int

    @classmethod
    def from_page(cls, page: ContentPage, *, limit: int, offset: int) -> ContentBoardResponse:
        return cls(
            items=[
                ContentSummaryResponse.from_row(
                    row,
                    approvable_by_me=row.id in page.approvable_ids,
                    published_at=page.published_at.get(row.id),
                )
                for row in page.items
            ],
            total=page.total,
            scope=page.scope.value,
            period=page.period.isoformat()[:7] if page.period is not None else None,
            period_applied=page.period_applied,
            view=page.view.value if page.view is not None else None,
            current_period=(
                page.current_period.isoformat()[:7] if page.current_period is not None else None
            ),
            stage_counts=[
                StageCountResponse(stage=stage.value, count=count)
                for stage, count in page.stage_counts.items()
            ],
            production_state_counts=[
                ProductionStateCountResponse(production_state=state.value, count=count)
                for state, count in page.production_state_counts.items()
            ],
            limit=limit,
            offset=offset,
        )


class DashboardResponse(BaseModel):
    """The landing page, assembled from read-only queries.

    Everything here is counted from live tables. No figure is estimated,
    extrapolated or carried over from a previous run - a dashboard that
    guesses is worse than one that says zero.
    """

    stage_counts: list[StageCountResponse]
    awaiting_my_review: list[ContentSummaryResponse]
    overdue_tasks: list[TaskSummaryResponse]
    my_capabilities: list[str]
    recent_content: list[ContentSummaryResponse]


__all__: list[str] = [
    "AccountChoicesResponse",
    "ActorResponse",
    "AiReviewResponse",
    "AiReviewRunResponse",
    "AiReviewStateResponse",
    "ApprovableSelectionResponse",
    "ApprovalDecisionRequest",
    "ApprovalEventResponse",
    "AssignChannelRequest",
    "AssignTaskRequest",
    "AvailableActionResponse",
    "AvailableActionsResponse",
    "BrandResponse",
    "BulkApproveRequest",
    "BulkApproveResponse",
    "BulkApprovedItemResponse",
    "CapabilityGrantResponse",
    "ChannelAnalyticsResponse",
    "ChannelAssignmentResponse",
    "ChannelConnectionResponse",
    "ChannelConnectionStateResponse",
    "ChannelDetailResponse",
    "ChannelFollowerTrendResponse",
    "ChannelMetricSnapshotResponse",
    "ChannelMetricsResponse",
    "ChannelResponse",
    "CloseAssignmentRequest",
    "ConnectionAuthorizationResponse",
    "ConnectionOutcomeResponse",
    "ContentBoardResponse",
    "ContentDerivativeRequest",
    "ContentDerivativeResponse",
    "ContentDestinationRequest",
    "ContentDestinationResponse",
    "ContentDetailResponse",
    "ContentSummaryResponse",
    "ContentTargetRequest",
    "ContentTargetResponse",
    "ContentVersionResponse",
    "CorrectProductionOutputRequest",
    "CreateChannelRequest",
    "CreateContentRequest",
    "CreatePlatformRequest",
    "CreateTaskRequest",
    "DashboardResponse",
    "DiscoveredAccountResponse",
    "ErrorBody",
    "ErrorEnvelope",
    "FinishConnectionRequest",
    "GrantCapabilityRequest",
    "GrantScopeBody",
    "MetricCapabilitiesResponse",
    "MetricChangeResponse",
    "PersonResponse",
    "PlatformResponse",
    "PolicyPackRefResponse",
    "PolicyRuleCitationResponse",
    "ProductionStateCountResponse",
    "ProductionSubmissionResponse",
    "PublicationResponse",
    "RecordChannelMetricsRequest",
    "RegisterPublicationRequest",
    "ReversePublicationResponse",
    "ReviewContextResponse",
    "ReviseContentRequest",
    "RevokeCapabilityRequest",
    "SelectAccountRequest",
    "StageCountResponse",
    "SubmitAiReviewRequest",
    "TaskAssignmentResponse",
    "TaskDetailResponse",
    "TaskStatusRequest",
    "TaskSummaryResponse",
    "TopPostResponse",
    "TransitionRequest",
    "UnavailableMetricResponse",
    "UpdateChannelRequest",
    "UpdateContentDerivativeRequest",
    "UpdateContentDestinationRequest",
    "UpdatePublicationRequest",
    "UpdateTargetRequest",
]
