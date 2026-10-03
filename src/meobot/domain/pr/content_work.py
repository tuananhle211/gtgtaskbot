"""What the content workflow means in the Work Ledger's vocabulary. M3.

This module is the whole of M3's *rules*: which content milestones are real
work, whose work they are, what a semantic source key looks like, and what a
projection run can conclude. It knows nothing about SQL, HTTP or Celery, which
is what lets the mapping be argued about in one place and tested without a
database - the same split :mod:`meobot.domain.pr.work` already makes for M1.

The one idea this module exists to protect
-------------------------------------------

**If MeoChat already knows somebody did the work, they should not have to type
it in again.** A writer whose script was approved, a producer whose cut was
accepted, a person who posted the video - the content workflow recorded all
three, and asking them to file a second Work item afterwards is asking them to
tell the system something it already knows.

What that must **not** cost is the boundary M1 is built on:

    CREATED != ACCEPTED != COMPLETED != APPROVED != COUNTED

So projection produces *completed work*, and the fifth step still needs somebody
who did not do it. See :class:`PrContentWorkKind` on the self-approval case,
which is the whole reason this module has a notion of *independent* validation
at all.

What M3 does not decide
------------------------

**Eligibility.** Not one line here reads a quota, a plan, an allocation or a
``quota_status``. M3 answers *"has this content workflow produced trustworthy
COUNTED work"* and stops; M2 answers what that work is worth against a cap, and
a missing allocation row means three different things that M3 must not try to
tell apart - see ``docs/pr/WORK_QUOTA_M2.md`` §6b.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from meobot.domain.pr.labels import content_type_label
from meobot.domain.pr.models import PrApprovalStage, PrContentType, PrWorkflowStage
from meobot.domain.pr.work import PrWorkCategory, PrWorkSourceType, PrWorkUnit


class PrContentWorkKind(StrEnum):
    """One kind of real job the content workflow produces.

    A **semantic** vocabulary, deliberately independent of what any screen calls
    things. "Kịch bản" is a label; ``CONTENT_CREATION`` is the fact that
    somebody wrote a deliverable and somebody else accepted it, and the second
    is what a KPI may be built on.

    Three, and each earns its place by having a **canonical milestone, a
    canonical contributor and a canonical reversal signal** in tables that
    already exist. A kind without all three would be a guess dressed as a
    record.

    There is deliberately **no ``REVIEW``.** Reviewing is real effort, and
    whether it is countable workload is a product decision nobody has taken;
    inventing it here would also put the reviewer of a piece into the same
    ledger as its writer, which is the one pairing the anti-gaming boundary
    exists to keep apart.
    """

    #: Somebody wrote the deliverable and a reviewer accepted it. The milestone
    #: is the **final** content acceptance - ``HEAD_REVIEW -> APPROVED`` - and
    #: not any step before it: a draft handed in is a claim, and an intermediate
    #: approval is a step on the way rather than the accepted deliverable.
    CONTENT_CREATION = "CONTENT_CREATION"
    #: Somebody produced the file and an internal reviewer accepted it. The
    #: milestone is ``INTERNAL_REVIEW -> READY_TO_PUBLISH``: an assignment is not
    #: work, an upload is not acceptance, and a cut that was sent back to be
    #: re-done is one job that took two attempts rather than two jobs.
    PRODUCTION = "PRODUCTION"
    #: Somebody posted the piece to a channel. **One per publication row**, not
    #: one per content item: a fan-out to three channels is three real postings.
    #:
    #: **No longer projected automatically. M3.1.** The value stays in the enum
    #: and every rule below still understands it, because work M3 already created
    #: under it must keep resolving its kind, its source key and its label - but
    #: :data:`AUTOMATIC_KINDS` excludes it and no new publication work is
    #: created. The business decision is that V1 records workload at two content
    #: milestones only; the technical objection was already written down here and
    #: survives as the reason it was the first to go: recording a publication
    #: takes only the *view* right, the platform id and URL are nullable, and
    #: nothing verifies the post exists, so the person who records it is
    #: routinely the person it credits with no independent evidence.
    PUBLICATION = "PUBLICATION"


#: **The V1 rule: the only two content milestones that create work.** M3.1.
#:
#: The department contributes automatic workload at exactly two business steps -
#: *Duyệt trưởng phòng* and *Gửi bản dựng* - and nothing else. Receiving a brief,
#: research, an intermediate review, the reviewing itself, shooting, thumbnails,
#: publication and distribution are all real effort and none of them is projected:
#: each would need its own canonical milestone, contributor and reversal signal,
#: and inventing any of the three is how a ledger fills with rows nobody can
#: defend.
#:
#: ``PUBLICATION`` is **deliberately absent**, and M3.1 removed it rather than
#: never having had it - see the class docstring. Work already projected under it
#: is kept and keeps resolving; no new publication work is created.
AUTOMATIC_KINDS: frozenset[PrContentWorkKind] = frozenset(
    {PrContentWorkKind.CONTENT_CREATION, PrContentWorkKind.PRODUCTION}
)

#: Kinds whose source milestone carries **no independent validator at all**.
#:
#: A publication is written by whoever may read the piece, against no evidence
#: the platform ever saw it - so *"the publisher recorded it"* is not somebody
#: else confirming the work happened, and treating it as one would make the
#: cheapest row in the module the easiest KPI to write for yourself.
#:
#: Work of these kinds is therefore projected as **completed and uncounted**,
#: always, and reaches ``COUNTED`` only when a human who did not do it validates
#: it through the Work module. That is not a punishment for a simple operation;
#: it is the same rule every other kind gets, applied where the source cannot
#: supply the second person itself.
SELF_RECORDED_KINDS: frozenset[PrContentWorkKind] = frozenset({PrContentWorkKind.PUBLICATION})


#: The **milestone name inside the source key**, per kind.
#:
#: Separate from the enum's own value only so that renaming a kind for a screen
#: could never renumber the identity of work already projected. Today the two
#: are the same string, and a test asserts it - which is the cheap way to keep
#: the pair from drifting silently.
KIND_MILESTONES: Mapping[PrContentWorkKind, str] = MappingProxyType(
    {
        PrContentWorkKind.CONTENT_CREATION: "CONTENT_CREATION",
        PrContentWorkKind.PRODUCTION: "PRODUCTION",
        PrContentWorkKind.PUBLICATION: "PUBLICATION",
    }
)


#: The content-stage edge that **is** each kind's qualifying milestone.
#:
#: ``PUBLICATION`` is absent on purpose: its milestone is a *row*, not an edge.
#: A piece already at ``PUBLISHED`` takes no further stage change when the
#: second and third channels are recorded, so keying on the edge would credit
#: the first posting and silently lose the rest.
KIND_STAGE_MILESTONES: Mapping[
    PrContentWorkKind, tuple[PrWorkflowStage, PrWorkflowStage, PrApprovalStage]
] = MappingProxyType(
    {
        PrContentWorkKind.CONTENT_CREATION: (
            PrWorkflowStage.HEAD_REVIEW,
            PrWorkflowStage.APPROVED,
            PrApprovalStage.HEAD_REVIEW,
        ),
        PrContentWorkKind.PRODUCTION: (
            PrWorkflowStage.INTERNAL_REVIEW,
            PrWorkflowStage.READY_TO_PUBLISH,
            PrApprovalStage.INTERNAL_REVIEW,
        ),
    }
)


class PrContentWorkOutcome(StrEnum):
    """What one projection run concluded about one semantic piece of work.

    A **diagnostic** vocabulary, and the distinction that matters most is at the
    bottom: three of these mean *"the projector worked and this is the answer"*
    and three mean *"the projector could not answer"*. Collapsing the two groups
    is how a reconciliation report comes to look clean while quietly doing
    nothing.

    **None of these is a quota status.** ``UNRESOLVED_CONTRIBUTOR`` in
    particular is a projection problem - the module cannot say *whose* work this
    was - and has nothing to do with whether a quota exists. M2 owns that
    vocabulary and M3 never writes it.
    """

    #: A new work item, or a change to one, was written.
    PROJECTED = "PROJECTED"
    #: The Work Ledger already said exactly this. The common case on a re-run,
    #: and what makes a repeated reconcile cheap as well as safe.
    UNCHANGED = "UNCHANGED"
    #: Projected, and left **completed but uncounted**: the source milestone had
    #: no validator other than the contributor. Somebody else must validate it
    #: through the Work module. See :data:`SELF_RECORDED_KINDS` and
    #: ``docs/pr/CONTENT_WORK_PROJECTION_M3.md``.
    PENDING_VALIDATION = "PENDING_VALIDATION"
    #: The source milestone is no longer in force - an approval was undone, a
    #: publication reversed - and the work was reconciled back out of the count.
    REVERSED = "REVERSED"
    #: The source qualifies, and the result exists, and a **validator rejected
    #: it** (``0041``). The projector reached an answer - *leave it* - because a
    #: person's reviewed decision outranks the source's say-so; *Xem xét lại* is
    #: the only release. Also what a pre-``0041`` exclusion with no recorded
    #: author gets, for the same reason.
    HELD_BY_VALIDATOR = "HELD_BY_VALIDATOR"
    #: There is no qualifying milestone yet. Not an error: most content is here
    #: most of the time.
    NOT_QUALIFIED = "NOT_QUALIFIED"
    #: No work type could be named for this content and kind, **and none could
    #: be provisioned**. Since ``0040`` an unmapped content type is bound to a
    #: work type the projector creates for it, so this is no longer what a new
    #: content type produces; it is what remains: content with no
    #: ``content_type`` to bind on, a kind the projector does not provision, or
    #: a mapping an administrator deactivated - a decision, honoured rather than
    #: worked around. **Guessing a work type would file somebody's work under a
    #: heading nobody chose**, which is the same mistake the content-type
    #: backfill refused to make.
    NO_MAPPING = "NO_MAPPING"
    #: The source says work happened and **cannot say whose**. The draft that
    #: was approved has no author on record, or a publication has no publisher.
    #: Deliberately not resolved by falling back to today's owner: crediting the
    #: wrong person is worse than crediting nobody, and it looks identical to a
    #: correct row afterwards.
    UNRESOLVED_CONTRIBUTOR = "UNRESOLVED_CONTRIBUTOR"
    #: The work belongs to a reporting period that is ``CLOSED`` or ``LOCKED``,
    #: and the source has since changed. **Historical performance is not
    #: rewritten**, silently or otherwise - the discrepancy is reported and the
    #: numbers stay as they were agreed. There is no force flag.
    BLOCKED_BY_PERIOD = "BLOCKED_BY_PERIOD"


#: Outcomes that mean the projector reached an answer. The rest mean it could
#: not, and a reconciliation report has to show them separately or it is
#: reporting silence as success.
SETTLED_OUTCOMES: frozenset[PrContentWorkOutcome] = frozenset(
    {
        PrContentWorkOutcome.PROJECTED,
        PrContentWorkOutcome.UNCHANGED,
        PrContentWorkOutcome.PENDING_VALIDATION,
        PrContentWorkOutcome.REVERSED,
        PrContentWorkOutcome.HELD_BY_VALIDATOR,
        PrContentWorkOutcome.NOT_QUALIFIED,
    }
)


class PrContentWorkProjectionStatus(StrEnum):
    """Where one content item's projection request has got to.

    The queue is **one row per content item, for ever** rather than one row per
    request: projection is convergent, so two outstanding requests for the same
    piece are the same request, and a row that survives settlement is the
    diagnostic a reconciliation report reads.
    """

    #: Something about this content changed and the projector has not looked yet.
    PENDING = "PENDING"
    #: A worker has claimed it. Recovered by the sweeper if the worker dies.
    RUNNING = "RUNNING"
    #: The projector ran and the Work Ledger matches the source.
    SETTLED = "SETTLED"
    #: The projector ran and could not finish. ``last_outcome`` says why, and
    #: the row stays visible rather than the failure being a log line nobody
    #: reads.
    FAILED = "FAILED"


def content_work_source_key(kind: PrContentWorkKind, entity_id: uuid.UUID) -> str:
    """The semantic source key for one piece of content-derived work.

    ``content:{entity-uuid}:{MILESTONE}``, composed through M1's own
    :func:`~meobot.domain.pr.work.work_source_key` so that the shape is
    validated by the module that owns it rather than by string concatenation
    here.

    **The entity is not always the content item.** For ``CONTENT_CREATION`` and
    ``PRODUCTION`` it is, because one content item has one accepted script and
    one accepted cut. For ``PUBLICATION`` it is the **publication row**: a piece
    that goes out on three channels is three postings, and keying all three on
    the content id would credit one and lose two.

    **The transition event id is deliberately not in the key**, and that is the
    whole of the idempotency contract. An approval that is undone and re-made is
    *the same script being approved*, not a second one; a worker retry is not a
    second job; a redo is not a second job. Keying on the attempt would produce
    a second work item for every one of those and double-count the writer.
    """
    from meobot.domain.pr.work import PrWorkSourceType, work_source_key

    return work_source_key(PrWorkSourceType.CONTENT, entity_id, KIND_MILESTONES[kind])


def content_work_source_entity(source_key: str | None) -> uuid.UUID | None:
    """The entity a content source key names, or ``None`` when it is not one.

    The one sanctioned read of a key's inside, for the **read model**: a result
    row carries ``content:{uuid}:{MILESTONE}`` and a screen offering *"Đồng bộ
    lại từ Nội dung"* needs the content id to ask for, not the key. Services
    still compare keys for equality and never branch on their parts.
    """
    if not source_key:
        return None
    parts = source_key.split(":")
    if len(parts) != 3 or parts[0] != PrWorkSourceType.CONTENT.value.lower():
        return None
    try:
        return uuid.UUID(parts[1])
    except ValueError:
        return None


class ContentWorkProvenance(Protocol):
    """The two columns that say what grain a content-derived work row is."""

    @property
    def source_type(self) -> PrWorkSourceType: ...

    @property
    def reporting_period_id(self) -> uuid.UUID | None: ...


def is_legacy_content_work_item(item: ContentWorkProvenance) -> bool:
    """Whether a work row is an **old-version, item-grain** content projection.

    The canonical predicate, and the only one. Before the period-container
    patch (``0039``) the projector wrote *one work item per content milestone*:
    ``source_type = CONTENT``, a ``content:{uuid}:{MILESTONE}`` source key, one
    ``PRIMARY`` contribution, no reporting period. Since ``0039`` a milestone is
    one :class:`~meobot.db.models.pr_work_result.PrWorkResult` inside the
    contributor's monthly container, and the container itself is opened with
    ``source_type = MANUAL`` (see ``PrWorkResultService.ensure_container``).

    So the legacy shape is exactly *content provenance on a row that is not a
    container*, read from the two columns that carry those facts. What it is
    **not** read from: the title (``"Nội dung: …"`` is a display string the
    projector happened to choose), the content code, the work type's name, or
    anything decoded out of the source key. Manual work is ``MANUAL``, recurring
    work is ``RECURRING`` and a modern content result is a row in another table,
    so each of those is refused by the same two comparisons.
    """
    return item.source_type is PrWorkSourceType.CONTENT and item.reporting_period_id is None


# ===========================================================================
# Auto-provisioned work types
# ===========================================================================
#
# **No preconfiguration is required for content work.** A content type that
# nobody has mapped yet must not lose its first accepted deliverable, so the
# projector provisions the missing work type and its binding itself and counts
# that deliverable in the same run. Everything below is the *identity* half of
# that rule: which code the type gets, what it is called and how it is measured.
# The writes live in the services; the race handling lives on the database's
# unique indexes.

#: The reserved code namespace of every work type the projector provisions.
#:
#: A **namespace and not a label**: the code is the stable identifier the row is
#: found by on every later run, and the prefix is what keeps it clear of the
#: bootstrap taxonomy - the content type ``SHORT_VIDEO_SCRIPT`` and the bootstrap
#: work type ``SHORT_VIDEO_SCRIPT`` are different things that happen to share a
#: word, and an unprefixed code would have silently merged them. Manual creation
#: refuses codes in this namespace so that the prefix stays a reliable statement
#: of provenance: a type whose code starts with it was provisioned from content,
#: and no other type ever was.
AUTO_WORK_TYPE_CODE_PREFIX = "CONTENT_AUTO_"

#: The heading an auto-provisioned type is filed under, per kind. The two
#: automatic kinds have obvious homes; the retired ``PUBLICATION`` kind is never
#: provisioned - see :data:`AUTOMATIC_KINDS`.
AUTO_WORK_TYPE_CATEGORIES: Mapping[PrContentWorkKind, PrWorkCategory] = MappingProxyType(
    {
        PrContentWorkKind.CONTENT_CREATION: PrWorkCategory.CONTENT,
        PrContentWorkKind.PRODUCTION: PrWorkCategory.PRODUCTION,
    }
)

#: **One accepted deliverable is one unit.** The measurement an auto-provisioned
#: type gets, and the only one that could be right without a decision nobody has
#: taken: a script is a script, a cut is a cut, and neither has a quantity the
#: source could supply.
AUTO_WORK_TYPE_UNIT = PrWorkUnit.ITEM


def auto_work_type_code(kind: PrContentWorkKind, content_type: PrContentType) -> str:
    """The deterministic code of the work type provisioned for one binding.

    ``CONTENT_AUTO_{kind}_{content_type}``, from the two **stable enum values**
    and never from a display name: the same content type and kind resolve to
    the same code on every run, on every worker, and after any rename. The kind
    is part of the identity because one content type produces two kinds of work
    - a script that is written and a cut that is made - and they are not the
    same job.

    Fits ``pr_work_types.code`` (64) for every pair of values that exists.
    """
    if kind not in AUTO_WORK_TYPE_CATEGORIES:
        raise ValueError(f"{kind.value} is not a kind the projector provisions")
    return f"{AUTO_WORK_TYPE_CODE_PREFIX}{kind.value}_{content_type.value}"


def is_auto_work_type_code(code: str) -> bool:
    """Whether a work type code lies in the reserved auto-provisioned namespace."""
    return code.startswith(AUTO_WORK_TYPE_CODE_PREFIX)


def auto_work_type_name(kind: PrContentWorkKind, content_type: PrContentType) -> str:
    """What the provisioned type is called on a screen, **at creation**.

    Derived from the content type's display label so a manager recognises it -
    "Kịch bản video ngắn" for the script, "Sản xuất: Kịch bản video ngắn" for
    the cut, the same prefix a projected work item's title already uses. It is
    a starting name and nothing more: ``name`` is an editable label on every
    work type, identity is the code and the binding, and a later change to the
    content type's label does not rename a type somebody may already have
    renamed themselves.
    """
    label = content_type_label(content_type)
    if kind is PrContentWorkKind.PRODUCTION:
        return f"Sản xuất: {label}"[:200]
    return label[:200]


__all__: list[str] = [
    "AUTOMATIC_KINDS",
    "AUTO_WORK_TYPE_CATEGORIES",
    "AUTO_WORK_TYPE_CODE_PREFIX",
    "AUTO_WORK_TYPE_UNIT",
    "KIND_MILESTONES",
    "KIND_STAGE_MILESTONES",
    "SELF_RECORDED_KINDS",
    "SETTLED_OUTCOMES",
    "ContentWorkProvenance",
    "PrContentWorkKind",
    "PrContentWorkOutcome",
    "PrContentWorkProjectionStatus",
    "auto_work_type_code",
    "auto_work_type_name",
    "content_work_source_entity",
    "content_work_source_key",
    "is_auto_work_type_code",
    "is_legacy_content_work_item",
]
