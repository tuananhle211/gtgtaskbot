"""Stored vocabulary for the PR and Communications module.

Every enum here is a :class:`~enum.StrEnum` whose member name and value are the
same uppercase code. That is not decoration: these values are written to the
database through :func:`~meobot.db.base.value_enum`, which stores the *value*,
so name and value being identical means the string in the column, the string in
the migration's ``CHECK`` constraint and the string in Python are one string.
A rename is therefore never silent - it fails the enum-parity test rather than
writing an unrecognised code.

Step 1A stores and constrains these codes. It does not interpret them: nothing
here knows that ``APPROVED`` follows ``HEAD_REVIEW``, or that a ``STOP`` channel
should stop receiving content. Those rules are Step 1B's, and they belong in a
service that can be tested against a transition table, not in a column.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class PrEntityStatus(StrEnum):
    """Whether a reference row may still be chosen.

    Shared by brands, platforms, content formats and content pillars, because
    for a reference table the only question worth asking is "is this still in
    use". A row is never deleted - archived work points at it - so retiring one
    means setting it ``INACTIVE``.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class PrChannelCategory(StrEnum):
    """What the team has decided to do with a channel.

    An editorial judgement, recorded so that a channel's budget of attention is
    explicit rather than implied by how busy somebody happened to be.
    """

    SCALE = "SCALE"
    OPTIMIZE = "OPTIMIZE"
    TEST = "TEST"
    MAINTAIN = "MAINTAIN"
    STOP = "STOP"


class PrChannelStatus(StrEnum):
    """Whether a channel is operating.

    ``ARCHIVED`` is distinct from ``INACTIVE``: an inactive channel is paused
    and expected back, an archived one is finished with. Neither is a deletion.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class PrChannelAssignmentRole(StrEnum):
    """What a person is responsible for on one channel.

    Separate roles rather than one "owner" field, because these responsibilities
    genuinely sit with different people: whoever answers for the channel is
    rarely the person who edits the footage.
    """

    CHANNEL_OWNER = "CHANNEL_OWNER"
    CONTENT_OWNER = "CONTENT_OWNER"
    PRODUCTION_OWNER = "PRODUCTION_OWNER"
    SEEDING_OWNER = "SEEDING_OWNER"
    ANALYTICS_OWNER = "ANALYTICS_OWNER"
    APPROVER = "APPROVER"


class PrPriority(StrEnum):
    """How urgent a piece of content or a task is.

    Step 1F.2.3d reshaped this vocabulary, and both halves of that were
    deliberate.

    ``CRITICAL`` was **added** because the top of the scale was doing two jobs.
    *Gấp* had to mean both "move this up the queue" and "everything else waits",
    and a triage word that covers both stops sorting anything - which is the
    state a work queue ends up in when every pressing item carries the same
    badge.

    ``LOW`` was **removed** because it never meant anything here. Nothing in the
    application, the panel or the Telegram tools ever wrote it or offered it, so
    every row in both tables was already ``NORMAL`` or above; it was a value the
    vocabulary claimed to support and the product had no word for. ``NORMAL`` is
    the baseline, and the four levels above it are all urgency. Revision 0023
    rewrites the ``CHECK`` on ``pr_content_items`` and ``pr_tasks`` together,
    because one enum backs both columns.

    Declaration order is **ascending urgency** and is the only place that order
    is written down. :data:`~meobot.domain.pr.priority.PRIORITY_RANK` derives
    the sort key from it rather than restating it, so adding a level between two
    others is one edit here.
    """

    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"
    CRITICAL = "CRITICAL"


class PrContentType(StrEnum):
    """What *kind of thing* a piece of content is.

    Step 1F.2.3e. Six values, closed: this is the vocabulary the team writes in,
    and adding to it is a product decision rather than a data-entry one.

    Not the platform, and never derived from it
    -------------------------------------------

    This is the distinction the whole enum exists to hold. ``SHORT_VIDEO_SCRIPT``
    runs on TikTok, on Facebook Reels, on YouTube Shorts and on whatever the next
    short-video surface turns out to be; ``FACEBOOK_POST`` is a Facebook post
    wherever it is boosted to. **Content type describes the format; the channel
    describes distribution**, and the two vary independently - a fact the schema
    already reflects by putting channels in ``pr_content_targets``, one item to
    many of them. Inferring one from the other would collapse a many-to-many into
    a guess, and would be wrong the first time somebody cross-posts.

    Nothing anywhere derives a type from a platform, a channel, a title or a
    policy pack. It is stated by a person, once, when the content is created.

    Why an enum rather than ``pr_content_formats``
    ----------------------------------------------

    ``pr_content_formats`` exists - a normalised table of *"a shape content takes
    - a short video, a long video, an article"* - and ``PrContentItem.format_id``
    points at it. It is nonetheless **not** what this is, and it is left
    untouched: it has never been seeded, no route lists it, no client sends it
    and nothing reads it back.

    A closed vocabulary the product defines wants stable codes, not rows. The
    codes are what a filter carries in a URL, what the API returns and what the
    panel maps to Vietnamese - and a foreign key would make each of those a join
    or a UUID in a query string, for a table nobody administers. See
    ``docs/pr/STEP_1F23E_CONTENT_TYPES_AND_RESOURCES.md``.

    Declaration order is the order a person is offered them: shortest and most
    common first.
    """

    ULTRA_SHORT_SCRIPT = "ULTRA_SHORT_SCRIPT"
    SHORT_VIDEO_SCRIPT = "SHORT_VIDEO_SCRIPT"
    FACEBOOK_POST = "FACEBOOK_POST"
    LONG_YOUTUBE_SCRIPT = "LONG_YOUTUBE_SCRIPT"
    PRESS_ARTICLE = "PRESS_ARTICLE"
    CORPORATE_TVC = "CORPORATE_TVC"


class PrContentResourceType(StrEnum):
    """What kind of supporting material a review resource is.

    Step 1F.2.3e. Seven values, and the boundary that matters is what is **not**
    here: nothing in this enum describes a produced file.

    A resource is **input**. A brief, a moodboard, a reference video, a brand
    guideline - the things somebody consults while writing the script and while
    deciding whether to approve it. What comes *out* of production is a
    :class:`PrProductionArtifactType` on a ``pr_production_submissions`` row, and
    the two are deliberately different tables, different vocabularies and
    different sections of the screen.

    The test for whether something belongs here: *would a reviewer need it open
    in another tab while reading the draft?* A client brief, yes. The final
    edited video, no - that is the thing being judged, not the thing to judge it
    against.
    """

    #: A document to read alongside the draft. The default kind.
    REFERENCE = "REFERENCE"
    #: A still - a packshot, a photo, a moodboard frame.
    IMAGE = "IMAGE"
    #: A video to watch for reference. **Not** a produced cut.
    VIDEO = "VIDEO"
    #: A file, usually on Google Drive. Never fetched by MeoBot.
    DRIVE_FILE = "DRIVE_FILE"
    #: Where a claim came from - a study, an article, a press release. The one
    #: that matters most for a piece that makes medical claims.
    SOURCE = "SOURCE"
    #: Logos, guidelines, fonts, approved copy - what keeps a piece on-brand.
    BRAND_ASSET = "BRAND_ASSET"
    #: Anything else. Present so nobody has to mislabel something to save it.
    OTHER = "OTHER"


class PrContentDerivativeType(StrEnum):
    """What kind of re-cut a derivative production asset is.

    Step 1F.2.3f. A content item is a durable, reusable asset: the 60-second
    master cut goes to Facebook in August, and in October somebody wants the same
    piece on a TikTok channel that did not exist then. What they make is a
    **new produced file from the same content** - not a new idea, not a new
    script, and emphatically not a copy of the content row.

    Six values, and the vocabulary is deliberately about *how the file differs
    from the master*, because that is the question somebody scanning a list of
    five outputs is actually asking. It is not about **where it is going**: a
    "TikTok cut" is a ``CUTDOWN`` whether it ends up on TikTok, on Reels or
    nowhere, and a type that named the destination would be wrong the first time
    a file was reused - which is the whole point of the feature.

    Note what this enum is **not** beside:

    * :class:`PrContentType` is what the *content* is - a short-video script, a
      TVC. One per content item, and a derivative does not change it;
    * :class:`PrProductionArtifactType` is how a file is **addressed** - a Drive
      link, a NAS path. Orthogonal: a ``CUTDOWN`` may live at either;
    * :class:`PrContentResourceType` is *input* material - the brief, the study
      behind a claim. A derivative is output.

    There is no workflow behind any of these. A derivative is produced work
    somebody is recording, not a state machine - see
    ``docs/pr/STEP_1F23F_DERIVATIVES_AND_PUBLICATIONS.md``.
    """

    #: A re-edit that keeps the material and changes the arrangement.
    REMIX = "REMIX"
    #: The same edit, shorter. The commonest one by far: a 60s master becomes a
    #: 25s cut for a feed that will not hold sixty seconds.
    CUTDOWN = "CUTDOWN"
    #: Cut again from the source footage rather than trimmed from the master.
    RECUT = "RECUT"
    #: The same edit in another shape - vertical from horizontal, square from
    #: vertical, a still set from a video.
    REFORMAT = "REFORMAT"
    #: Same picture, different words: another subtitle track, another caption
    #: burn-in, another language.
    CAPTION_VARIANT = "CAPTION_VARIANT"
    #: Anything else. Present so nobody has to mislabel a file to record it.
    OTHER = "OTHER"


class PrWorkflowStage(StrEnum):
    """Where a content item has got to.

    Stored as a single column rather than a set of boolean flags so that "where
    is this" has exactly one answer. Step 1A accepts any of these values in any
    order; which transitions are legal is a Step 1B service rule.

    Member order is the canonical workflow order and is the only place that
    order is written down. It is deliberately **not** mirrored into a numeric
    column: nothing in this repository has ever ordered a workflow by an integer
    on the row, and a second copy of the sequence is a second thing to keep in
    step. ``AI_REVIEW`` sits between ``SCRIPTING`` and ``TEAM_LEAD_REVIEW``
    (Step 1A1); ``CANCELLED`` is a terminal alternative and belongs to no
    position in the sequence, which is why it is last rather than in order.
    """

    IDEA = "IDEA"
    BRIEFING = "BRIEFING"
    SCRIPTING = "SCRIPTING"
    #: Advisory, automated quality control. Never a human approval - see
    #: :class:`PrAiReviewResult` and ``docs/pr/STEP_1A1_AI_REVIEW_WORKFLOW.md``.
    AI_REVIEW = "AI_REVIEW"
    TEAM_LEAD_REVIEW = "TEAM_LEAD_REVIEW"
    HEAD_REVIEW = "HEAD_REVIEW"
    APPROVED = "APPROVED"
    PRODUCTION = "PRODUCTION"
    INTERNAL_REVIEW = "INTERNAL_REVIEW"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISHED = "PUBLISHED"
    MEASURED = "MEASURED"
    ARCHIVED = "ARCHIVED"
    CANCELLED = "CANCELLED"


class PrDistributionMode(StrEnum):
    """How one content target reaches its audience.

    Step 1F.1. Per *target*, not per content item: one piece may run organically
    on Facebook and as a paid ad on TikTok, and the policies that apply differ
    between those two. A single field on the content item could not say that.

    ``UNSPECIFIED`` is the honest default and the value every pre-1F.1 row
    migrates to. It is **not** a synonym for ``ORGANIC``: guessing would silently
    review paid advertising against community rules alone, which is the failure
    this enum exists to prevent. For a policy-grounded platform it blocks AI
    review until somebody says which it is.
    """

    UNSPECIFIED = "UNSPECIFIED"
    ORGANIC = "ORGANIC"
    PAID_AD = "PAID_AD"


class PrProductionHandoff(StrEnum):
    """Where a piece stands between "approved" and "in production".

    Step 1F.2.3b, and **derived rather than stored**: every value here is a
    reading of ``(workflow_stage, producer_user_id)``, two columns that already
    exist. See :func:`~meobot.domain.pr.production.handoff_state`.

    A stage was considered and rejected. ``APPROVED`` with nobody holding it and
    ``APPROVED`` with a producer are the same editorial fact - the script is
    signed off - and differ only in whether the handoff has happened. Encoding
    that as a fourteenth ``PrWorkflowStage`` would have put a second thing in
    the column that answers "how far has this got", added two edges to the
    matrix, and made every existing query about ``APPROVED`` subtly wrong.

    What it buys: a screen can say *"Chờ nhận sản xuất"* rather than *"Đã
    duyệt"* for a piece nobody has picked up, which is the difference between a
    board somebody acts on and a column work quietly rots in.
    """

    #: ``APPROVED`` and ``producer_user_id IS NULL``. *Chờ nhận sản xuất.*
    WAITING_FOR_PRODUCER = "WAITING_FOR_PRODUCER"
    #: ``APPROVED`` with a producer. *Sẵn sàng sản xuất* - somebody holds it and
    #: has not started yet, which is the only state ``START_PRODUCTION`` is
    #: offered from.
    READY_FOR_PRODUCTION = "READY_FOR_PRODUCTION"
    #: ``PRODUCTION``. *Đang sản xuất.*
    IN_PRODUCTION = "IN_PRODUCTION"
    #: ``INTERNAL_REVIEW``. *Chờ duyệt nội bộ* - the cut is in, somebody is
    #: watching it. Included so a client has one field to render across the
    #: whole production half rather than a stage check beside a state check.
    IN_INTERNAL_REVIEW = "IN_INTERNAL_REVIEW"


class PrProductionArtifactType(StrEnum):
    """What kind of thing a finished production file *is*, and therefore how it
    is addressed.

    Step 1F.2.3. The distinction that earns the enum is the last one: a Drive
    link and a NAS web link are both ``https`` URLs and are validated as URLs;
    a NAS path is ``/volume1/PR/2026/cnt-42.mp4`` and is **not a URL at all**.
    Storing that path in a column called "url", or wrapping it in ``file://`` to
    make it look like one, would produce a reference that every client would try
    to make clickable and no browser could open. So the type says which kind of
    address the location is, and
    :func:`~meobot.domain.pr.production.normalize_artifact` validates it as that
    kind.

    ``DRIVE_LINK`` is narrower than ``EXTERNAL_LINK`` on purpose: a mistyped
    Drive link is the common error, and a type that promises Google Drive and
    holds a Dropbox URL is a promise the record should not make.
    """

    #: A Google Drive / Google Docs URL. Host-checked.
    DRIVE_LINK = "DRIVE_LINK"
    #: An ``http(s)`` URL served by the NAS - File Station, a share link, a
    #: reverse-proxied path. Host-checked only for being present.
    NAS_LINK = "NAS_LINK"
    #: A location on the NAS filesystem, as a path rather than a URL: an
    #: absolute POSIX path or a UNC share. Never fetched by MeoBot.
    NAS_PATH = "NAS_PATH"
    #: Any other ``http(s)`` URL - a client's WeTransfer, an agency portal.
    EXTERNAL_LINK = "EXTERNAL_LINK"


class PrPolicyScope(StrEnum):
    """Which family of official policy a source or rule belongs to.

    Two, because two is what the ORGANIC/PAID_AD distinction needs. Organic
    content is judged against community standards; a paid ad is judged against
    those *and* the advertising standards on top.
    """

    COMMUNITY_STANDARDS = "COMMUNITY_STANDARDS"
    ADVERTISING_STANDARDS = "ADVERTISING_STANDARDS"


class PrPolicySourceRole(StrEnum):
    """What a registered official page is *for*.

    Step 1F.1. The distinction exists because a platform's top-level policy page
    is usually a table of contents: Meta's Community Standards index is an
    introduction, and TikTok's advertising index is a list of article names. Both
    fetch cleanly and both contain almost no rules.

    Registering them as ``POLICY_CONTENT`` would have built a production pack out
    of navigation - a pack that exists, activates, and grounds reviews in nothing.
    So an index contributes **no rules**; it only discovers sub-pages, which are
    then registered as content sources in their own right.
    """

    #: Substantive policy prose. Eligible to contribute rules to a pack.
    POLICY_CONTENT = "POLICY_CONTENT"
    #: A table of contents. May discover sub-pages; contributes no rules.
    DISCOVERY_INDEX = "DISCOVERY_INDEX"


class PrPolicyPackStatus(StrEnum):
    """Where one versioned policy pack has got to.

    ``ACTIVE`` is immutable: a pack that reviews can cite must not change under
    them, or a stored finding stops being explainable. A policy update is a new
    pack, and the old one is ``RETIRED`` rather than edited.
    """

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class PrPolicyIngestionMethod(StrEnum):
    """How a source's text reached MeoBot.

    Recorded per source and carried onto every snapshot, because "we fetched
    this from the official page" and "an operator supplied this file" are
    different provenance claims and an audit must be able to tell them apart.

    ``OPERATOR_IMPORT`` exists because some official policy pages render their
    text client-side: an HTTP fetch of ``transparency.meta.com`` returns ~40
    characters of prose. Rather than fall back to an unofficial mirror - which
    the ingestion rules forbid, correctly - the operator supplies the text and
    the snapshot says so. See ``docs/pr/STEP_1F1_PLATFORM_POLICY.md``.
    """

    FETCH = "FETCH"
    OPERATOR_IMPORT = "OPERATOR_IMPORT"


#: Platform codes Step 1F.1 grounds against official policy. Everything else
#: keeps the generic Step 1F review, which is a real review and simply has no
#: platform rules behind it. Matched against ``pr_platforms.code`` - the
#: canonical record - never against a channel's name.
POLICY_GROUNDED_PLATFORM_CODES: frozenset[str] = frozenset({"FACEBOOK", "TIKTOK"})


#: Which policy scopes compose a pack for each distribution mode. A paid ad is
#: held to the community standards *and* the advertising standards; organic
#: content only to the former. ``UNSPECIFIED`` composes nothing, which is why
#: it blocks rather than defaults.
PACK_SCOPES: Mapping[PrDistributionMode, tuple[PrPolicyScope, ...]] = MappingProxyType(
    {
        PrDistributionMode.ORGANIC: (PrPolicyScope.COMMUNITY_STANDARDS,),
        PrDistributionMode.PAID_AD: (
            PrPolicyScope.COMMUNITY_STANDARDS,
            PrPolicyScope.ADVERTISING_STANDARDS,
        ),
    }
)


class PrContentTargetStatus(StrEnum):
    """Where one content item's appearance on one channel has got to.

    Per-target rather than per-item, because a piece published on two channels
    and cancelled on a third has no honest single status.
    """

    PLANNED = "PLANNED"
    READY = "READY"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"


class PrTaskStatus(StrEnum):
    """Where a task has got to."""

    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    IN_REVIEW = "IN_REVIEW"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class PrTaskAssignmentRole(StrEnum):
    """Why a person is attached to a task."""

    OWNER = "OWNER"
    CONTRIBUTOR = "CONTRIBUTOR"
    REVIEWER = "REVIEWER"


class PrApprovalStage(StrEnum):
    """Which review gate a **human** approval decision was made at.

    A subset of :class:`PrWorkflowStage` on purpose - only the stages at which
    somebody decides something produce an approval event. ``AI_REVIEW`` is
    absent from it deliberately and permanently: an AI review is advisory
    quality control, it produces a ``pr_ai_reviews`` row, and there is no value
    here it could ever be recorded under. Adding one would make an automated
    check indistinguishable from a person signing something off.
    """

    TEAM_LEAD_REVIEW = "TEAM_LEAD_REVIEW"
    HEAD_REVIEW = "HEAD_REVIEW"
    INTERNAL_REVIEW = "INTERNAL_REVIEW"


class PrApprovalDecision(StrEnum):
    """What a reviewer decided.

    ``REVISION_REQUIRED`` is not a rejection: the work continues, and the same
    reviewer is expected to see it again at a higher version number.
    """

    APPROVED = "APPROVED"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    REJECTED = "REJECTED"


class PrAiReviewResult(StrEnum):
    """What an automated review concluded about one version of a script.

    Three values, and none of them is an approval. The distinction that matters
    is between ``PASS`` and ``PASS_WITH_WARNINGS``: both let the content move on
    to ``TEAM_LEAD_REVIEW``, but the second says there are findings a person is
    expected to read on the way. Collapsing them into one value would lose the
    only reason the warnings were recorded.

    ``REVISION_REQUIRED`` sends the work back to ``SCRIPTING``. It is spelled
    the same as :attr:`PrApprovalDecision.REVISION_REQUIRED` because it means
    the same thing to the writer, and different in one respect that is not in
    the vocabulary: a human said it there, a model said it here.
    """

    # ``noqa: S105`` - ruff's hardcoded-password heuristic fires on any name
    # containing "pass". These are review outcomes, not credentials.
    PASS = "PASS"  # noqa: S105
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"  # noqa: S105
    REVISION_REQUIRED = "REVISION_REQUIRED"


class PrAiReviewRunStatus(StrEnum):
    """Where one *execution* of an AI review has got to.

    Step 1F. Deliberately a separate vocabulary from
    :class:`PrAiReviewResult`: a result is what the model said about a draft
    and lives forever in ``pr_ai_reviews``; a status is what happened to the
    job, and it changes. Putting the second on the first would have made an
    append-only table mutable, which is the one thing it must not be.
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    #: The review finished, but the draft or the stage moved while it ran, so
    #: its answer is about something that is no longer in front of anybody.
    #: Kept, never acted on - see ``docs/pr/STEP_1F_AI_REVIEW_EXECUTION.md``.
    SUPERSEDED = "SUPERSEDED"


#: Statuses nothing moves out of. A run in one of these is finished with.
TERMINAL_RUN_STATUSES: frozenset[PrAiReviewRunStatus] = frozenset(
    {
        PrAiReviewRunStatus.SUCCEEDED,
        PrAiReviewRunStatus.FAILED,
        PrAiReviewRunStatus.SUPERSEDED,
    }
)

#: Statuses that occupy the one active slot per (content, version, type).
#: Enforced by a partial unique index, not only by application code.
ACTIVE_RUN_STATUSES: frozenset[PrAiReviewRunStatus] = frozenset(
    {PrAiReviewRunStatus.QUEUED, PrAiReviewRunStatus.RUNNING}
)


class PrAiReviewTrigger(StrEnum):
    """Why an execution exists.

    ``AUTO`` is the workflow entering ``AI_REVIEW``; ``MANUAL_RETRY`` is a
    person asking again after a failure. Recorded because "we reviewed this
    twice" and "we reviewed this twice because the provider was down" are
    different facts.
    """

    AUTO = "AUTO"
    MANUAL_RETRY = "MANUAL_RETRY"


class PrAiReviewType(StrEnum):
    """Which question an automated review was asked.

    Recorded per review rather than inferred from the findings, because "the
    policy check passed" and "nothing in this review looked at policy" are
    different facts and a report has to be able to tell them apart. One content
    version may carry several reviews of different types, and several attempts
    at the same type.
    """

    SCRIPT_QUALITY = "SCRIPT_QUALITY"
    POLICY_COMPLIANCE = "POLICY_COMPLIANCE"
    BRAND_TONE = "BRAND_TONE"
    FULL_REVIEW = "FULL_REVIEW"
