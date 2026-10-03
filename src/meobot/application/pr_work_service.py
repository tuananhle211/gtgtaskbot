"""Every rule that decides whether work counts. M1.

One service, because the rules are one argument: who may file work, who may
accept it, who may say it is finished, and - the only one that matters for a
KPI - who may confirm that it was. Splitting them across two services would put
the anti-gaming boundary in a seam.

The boundary
------------

``CREATED != ACCEPTED != COMPLETED != APPROVED != COUNTED``, and the two places
that enforce it are :meth:`PrWorkService.accept` and
:meth:`PrWorkService.approve`. Both refuse an actor who is on the wrong side of
the work:

* a **proposer** may not accept their own proposal. Otherwise "propose" and
  "assign" are the same button with two names, and an employee writes their own
  workload;
* a **contributor** may not approve work they contributed to, whatever
  capability they hold. Otherwise the last gate before a number lands on
  somebody's performance record is a gate they stand on both sides of.

Neither is a UI rule. Both are checked here, on the server, against
``pr_work_contributions`` - so a direct API call gets the same refusal as a
hidden button, which is the only version of this that is worth anything.

Approval is one transaction
----------------------------

:meth:`approve` locks the item, re-reads its status, checks the capability,
checks that the actor contributed to nothing on it, writes ``approved_at``,
writes ``count_status`` and ``counted_at`` onto every eligible contribution,
appends the history and the audit rows, and returns. All of it or none of it -
there is no state in which an item is approved and its contributions are not,
and the ``counted_at_matches_status`` CHECK is the database saying the same
thing independently.

Two managers pressing the button at once therefore do not double-count: the
second waits on the row lock, re-reads ``APPROVED``, and is refused by the
transition matrix rather than writing a second set of ``counted_at``.

What this service does not do
------------------------------

It computes no score, decides no eligibility and knows no cap. ``COUNTED``
means *this person's contribution is valid completed work* and nothing more.
M6 will put a number on it, and it will not need this file to change what it
recorded.

M2 added exactly one line to :meth:`PrWorkService.approve`: after the
contributions are counted, the quota evaluator is asked to project their
eligibility. It is asked **behind a savepoint and with its result ignored on
failure** - see :meth:`PrWorkService._project_eligibility`. That is the shape
the requirement demands in both directions: a valid contribution must be able
to become ``COUNTED`` even when its quota status will be ``NO_QUOTA``, so
validation never depends on a quota existing; and a projection that fails must
not take a correct approval down with it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import exists, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.application.pr_work_notifications import PrWorkNotifier
from meobot.application.pr_work_period_service import PrWorkPeriodService
from meobot.application.pr_work_quota_service import PrWorkQuotaEligibilityService
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr import PrChannel
from meobot.db.models.pr_content_work import PrContentWorkRule
from meobot.db.models.pr_work import (
    PrWorkContribution,
    PrWorkEvidence,
    PrWorkHistory,
    PrWorkItem,
    PrWorkType,
)
from meobot.db.models.pr_work_quota import PrWorkQuota, PrWorkQuotaAllocation
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.content_work import (
    AUTO_WORK_TYPE_CODE_PREFIX,
    is_auto_work_type_code,
)
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.models import PrPriority
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.work import (
    DEFAULT_CREDIT_WEIGHT,
    MAX_CREDIT_WEIGHT,
    MAX_WORK_QUANTITY,
    MIN_CREDIT_WEIGHT,
    PrWorkAssignmentMode,
    PrWorkCategory,
    PrWorkContributionRole,
    PrWorkCountStatus,
    PrWorkEventType,
    PrWorkSourceType,
    PrWorkStatus,
    PrWorkUnit,
    assert_source_key,
    assert_work_transition,
    can_transition_work,
    evidence_label_for,
    evidence_location_for,
)
from meobot.domain.pr.work_labels import work_unit_label
from meobot.domain.pr.work_quota import PrWorkQuotaBasis
from meobot.domain.pr.work_results import (
    PERFORMANCE_FINALIZED,
    PERIOD_CONTAINER_LOCKED,
    PrWorkCountOrigin,
)
from meobot.domain.pr.work_types import (
    BOOTSTRAP_WORK_TYPES,
    WORK_TYPE_STRUCTURE_LOCKED,
)

logger = get_logger(__name__)

#: The longest title, description and note this service accepts. Matches the
#: columns; validated here so the refusal is a sentence rather than a database
#: error travelling up through five layers.
MAX_TITLE = 300
MAX_LABEL = 200
MAX_TEXT = 4000
MAX_LOCATION = 2000
#: How many contributors one job may have. Twenty is more than the department
#: has people; the bound exists so a request body cannot ask for ten thousand
#: inserts in one transaction.
MAX_CONTRIBUTORS = 20


@dataclass(frozen=True, slots=True)
class CreateWorkCommand:
    """One piece of work somebody is filing.

    Deliberately carries **no status and no source**. Which of the two entry
    points was used decides both - see :meth:`PrWorkService.propose_work` and
    :meth:`PrWorkService.assign_work` - so there is no request body anywhere
    that can file work as already accepted, and none that can claim to be
    derived from a content item.
    """

    work_type_id: uuid.UUID
    title: str
    description: str | None = None
    priority: PrPriority = PrPriority.NORMAL
    quantity: Decimal | None = None
    due_at: datetime | None = None
    channel_id: uuid.UUID | None = None
    #: Who did or will do the work. Empty on a proposal means "me".
    contributor_user_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkItemDetail:
    """One work item with everything a detail screen draws."""

    item: PrWorkItem
    work_type: PrWorkType
    contributions: tuple[PrWorkContribution, ...]
    evidence: tuple[PrWorkEvidence, ...]
    #: M3. ``CNT-2026-000042``, when this work was projected from content. What
    #: a detail panel prints and links to, so nobody is shown a UUID and nobody
    #: has to look up which piece their work came from.
    content_code: str | None = None
    #: Server-decided, from the same checks the writes make. A screen drawn a
    #: minute ago is not authorization, and these exist so a client offers only
    #: what the server would actually accept.
    can_manage: bool = False
    can_validate: bool = False
    can_execute: bool = False
    #: Contributor names, resolved here so no browser is handed a bare UUID to
    #: print and no client asks ``/people`` per card.
    contributor_names: dict[uuid.UUID, str] = dataclass_field(default_factory=dict)
    #: Post-M4. The recurring template this job was generated by, as
    #: ``(id, name)``. ``None`` for everything a template did not produce - so a
    #: detail panel offers *"đi tới công việc định kỳ"* exactly when there is
    #: somewhere to go.
    recurring_template: tuple[uuid.UUID, str] | None = None
    #: Period-container patch. Whether this actor is the stream's subject -
    #: the person who reports into it and the one person who may never count
    #: it. A one-off job has no subject.
    is_subject: bool = False
    #: **The action contract.** Which lifecycle acts this actor may take on
    #: this row right now, each resolved by :func:`resolve_work_actions` from
    #: the same transition table and the same guards the writes apply. A
    #: client draws a button when, and only when, its flag is true; it holds
    #: no status list of its own.
    actions: WorkActions = dataclass_field(default_factory=lambda: WorkActions())


@dataclass(frozen=True, slots=True)
class WorkActions:
    """Which lifecycle acts are available on one row, for one actor, right now.

    One flag per write on :class:`PrWorkService`, and each is derived from
    exactly what that write checks - :data:`~meobot.domain.pr.work.WORK_TRANSITIONS`
    for the edge, then the write's own authorization and ownership guards.
    ``REJECTED`` and ``CANCELLED`` are terminal in the table, so every flag
    is false on them; the table, not this class, is where that is decided.

    A false flag hides a control; the write refuses regardless. A true flag
    is a promise about *now*: the row may move under somebody's hand before
    the button is pressed, and the write re-reads it under a lock.
    """

    can_accept: bool = False
    can_reject: bool = False
    can_start: bool = False
    can_complete: bool = False
    can_approve: bool = False
    can_reopen: bool = False
    can_cancel: bool = False


def resolve_work_actions(
    item: PrWorkItem,
    *,
    actor_id: uuid.UUID | None,
    is_contributor: bool,
    may_manage: bool,
    is_item_manager: bool,
    may_validate: bool,
    may_execute: bool,
) -> WorkActions:
    """The action contract, as a pure function of the row and the actor's standing.

    Every rule here restates one guard in the corresponding write, in the
    same order the write applies it, so the two cannot drift without a test
    noticing (``tests/unit/test_pr_work_actions.py`` pins every status
    against every role):

    * **a period container takes none of these** - ``_refuse_container`` is
      the first check of all seven writes; its results are reported and
      counted one by one through the result service;
    * **accept / reject**: ``PR_WORK_MANAGE`` (the capability, not ownership
      - these are queue decisions taken by somebody other than the owner),
      the edge, and never the proposer (``self_acceptance``);
    * **start / complete**: a contributor holding ``PR_WORK_EXECUTE``, or the
      item's manager, and the edge. ``start`` is advertised from ``ACCEPTED``
      only: the table also admits ``COMPLETED → IN_PROGRESS`` for the
      validator's *Trả lại*, and offering that edge as "Bắt đầu" on finished
      work would be a second reopen with a misleading name;
    * **approve / reopen**: ``PR_WORK_VALIDATE``, the edge, and for approve
      never a contributor (``self_validation``);
    * **cancel**: the item's manager (``PR_WORK_MANAGE`` **and** owner or
      ``PR_WORK_VIEW_ALL``), manual work only (``source_derived_is_read_only``
      - a projected or generated row's lifecycle belongs to its source),
      never approved (``approved_is_final``), and the edge. Which leaves
      ``PROPOSED``, ``ACCEPTED``, ``IN_PROGRESS`` and ``COMPLETED``, and
      nothing else - not ``REJECTED``, not ``CANCELLED``.
    """
    if item.is_period_container:
        return WorkActions()
    status = item.status
    not_proposer = actor_id is not None and item.created_by_user_id != actor_id
    may_execute_here = (is_contributor and may_execute) or is_item_manager
    return WorkActions(
        can_accept=may_manage
        and not_proposer
        and can_transition_work(status, PrWorkStatus.ACCEPTED),
        can_reject=may_manage
        and not_proposer
        and can_transition_work(status, PrWorkStatus.REJECTED),
        can_start=may_execute_here
        and status is PrWorkStatus.ACCEPTED
        and can_transition_work(status, PrWorkStatus.IN_PROGRESS),
        can_complete=may_execute_here and can_transition_work(status, PrWorkStatus.COMPLETED),
        can_approve=may_validate
        and not is_contributor
        and can_transition_work(status, PrWorkStatus.APPROVED),
        can_reopen=may_validate
        and status is PrWorkStatus.COMPLETED
        and can_transition_work(status, PrWorkStatus.IN_PROGRESS),
        can_cancel=is_item_manager
        and item.source_type is PrWorkSourceType.MANUAL
        and status is not PrWorkStatus.APPROVED
        and can_transition_work(status, PrWorkStatus.CANCELLED),
    )


class PrWorkService:
    """Creates work, moves it, and decides when it counts.

    Args:
        session: Unit of work. The caller owns the transaction boundary, which
            is what makes :meth:`approve` atomic without this service knowing
            about transactions.
        audit: Event writer sharing that session.
        capabilities: Resolves the four ``PR_WORK_*`` capabilities.
        codes: Allocates ``WRK-2026-000001`` on the same session, so the number
            and the row it names commit together.
        notifier: In-app notifications. Optional, and a no-op when absent, so a
            test can drive the lifecycle without asserting about an inbox.
        eligibility: M2's quota evaluator. Optional, and a no-op when absent -
            the ledger works exactly as M1 shipped it without one, which is
            what makes the quota engine additive rather than a dependency
            validation acquired. See :meth:`approve`.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        codes: PrCodeService,
        notifier: PrWorkNotifier | None = None,
        eligibility: PrWorkQuotaEligibilityService | None = None,
        periods: PrWorkPeriodService | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._codes = codes
        self._notifier = notifier
        self._eligibility = eligibility
        # The finalised-performance guard on ``approve``. Optional so the
        # unit fixtures that build this service alone keep working; the
        # application wiring always passes it.
        self._periods = periods

    # =====================================================================
    # Work types
    # =====================================================================
    async def create_work_type(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code: str,
        name: str,
        category: PrWorkCategory,
        description: str | None = None,
        default_unit: PrWorkUnit | None = None,
        default_quota_basis: PrWorkQuotaBasis = PrWorkQuotaBasis.ITEM_COUNT,
        requires_evidence: bool = False,
        display_order: int = 0,
    ) -> PrWorkType:
        """Register a kind of work. ``PR_WORK_CONFIGURE``.

        ``code`` is normalised to upper snake and is the **stable machine
        identifier**: nothing authorises or groups on ``name``, so renaming
        "Kịch bản ngắn" is a display change and never a breaking one.

        ``default_quota_basis`` is M2's, and it defaults to ``ITEM_COUNT``
        rather than being inferred from ``default_unit``: whether a kind of work
        is measured by rows or by amount is a decision somebody takes, and
        guessing it from a unit would silently make "seeding comments" worth a
        hundredth of what the department means by it.

        ``default_unit`` is optional and the two bases treat the omission
        differently, which is M2.5's rule. An ``ITEM_COUNT`` type gets ``ITEM``:
        its amounts are counts of contributions and the unit is never read as a
        quantity, so there is nothing to decide. A ``QUANTITY`` type is
        **refused** without one, because that is the type whose unit is what its
        numbers mean - a seeding target silently inheriting "sản phẩm" is the
        ambiguity between "100 comments" and "100 jobs" written into the
        taxonomy. ``ITEM`` remains available to a ``QUANTITY`` type that
        genuinely wants it; it just has to be said.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        if default_unit is None and default_quota_basis is PrWorkQuotaBasis.QUANTITY:
            raise PrValidationError(
                "Loại công việc tính theo số lượng phải có đơn vị.",
                details={"field": "default_unit", "reason": "unit_required_for_quantity"},
            )
        unit = default_unit if default_unit is not None else PrWorkUnit.ITEM
        normalized = _require_code(code)
        if is_auto_work_type_code(normalized):
            # The namespace is what makes "this type was provisioned from
            # content" a fact the code alone can state. A manual type inside it
            # would make that statement false for everyone reading it later.
            raise PrValidationError(
                f"Mã bắt đầu bằng {AUTO_WORK_TYPE_CODE_PREFIX} được dành riêng cho loại "
                "công việc hệ thống tự tạo từ nội dung.",
                details={"field": "code", "reason": "reserved_code", "code": normalized},
            )
        if await self._work_type_by_code(normalized) is not None:
            raise PrConflictError(
                "Đã có loại công việc với mã này.",
                details={"field": "code", "reason": "duplicate_code", "code": normalized},
            )
        row = PrWorkType(
            code=normalized,
            name=_require_text(name, "name", MAX_LABEL),
            category=category,
            description=_optional_text(description, "description", MAX_TEXT),
            default_unit=unit,
            default_quota_basis=default_quota_basis,
            requires_evidence=requires_evidence,
            display_order=max(0, display_order),
        )
        self._session.add(row)
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_TYPE_CREATED,
            entity_type="pr_work_type",
            entity_id=row.id,
            after={"code": row.code, "name": row.name, "category": category.value},
        )
        return row

    async def ensure_source_work_type(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        code: str,
        name: str,
        category: PrWorkCategory,
        default_unit: PrWorkUnit,
        provenance: dict[str, object],
    ) -> PrWorkType:
        """Get, or create, a work type the **system** provisions. Internal.

        **Reachable from no route, and checks no capability.** The content
        projector is the caller: it met a content type nobody has mapped and
        provisions the type its first accepted deliverable is filed under, in
        the same run that counts that deliverable. It acts as the worker, which
        holds no capabilities, and the trust comes from the fact that triggered
        it - a completed content milestone - not from whoever pressed a button.
        Manual creation stays behind ``PR_WORK_CONFIGURE`` on
        :meth:`create_work_type`, and this method refuses any code outside the
        reserved namespace, so it cannot be bent into a way round that gate.

        Idempotent **on ``code``**, which is the identity: a second call finds
        the row the first one wrote, active or not, and returns it unchanged -
        the caller decides what an inactive one means. Two workers provisioning
        the same type at once both insert; one loses on ``pr_work_types.code``,
        and the loser re-reads the winner's row inside the savepoint rather
        than failing the projection.

        ``ITEM_COUNT`` always: one accepted deliverable is one unit, and no
        measurement anybody has not decided is invented here. Nor is a rate -
        the type is created with **no scoring rule**, and it stays unpriced
        until somebody configures one.
        """
        normalized = _require_code(code)
        if not is_auto_work_type_code(normalized):
            raise PrValidationError(
                "System-provisioned work types live in the reserved namespace only",
                details={"field": "code", "reason": "not_reserved_code", "code": normalized},
            )
        found = await self._work_type_by_code(normalized)
        if found is not None:
            return found
        row = PrWorkType(
            code=normalized,
            name=_require_text(name, "name", MAX_LABEL),
            category=category,
            default_unit=default_unit,
            default_quota_basis=PrWorkQuotaBasis.ITEM_COUNT,
            requires_evidence=False,
            display_order=0,
        )
        # Added **inside** the savepoint: ``begin_nested()`` flushes whatever is
        # already pending before it opens the SAVEPOINT, so a row added earlier
        # would be inserted outside it and a unique conflict would poison the
        # whole transaction instead of just this attempt.
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            # Another worker provisioned it a moment ago. Theirs is the type.
            if row in self._session:
                self._session.expunge(row)
            winner = await self._work_type_by_code(normalized)
            if winner is None:  # pragma: no cover - the index refused for another reason
                raise
            return winner
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_TYPE_CREATED,
            entity_type="pr_work_type",
            entity_id=row.id,
            after={
                "code": row.code,
                "name": row.name,
                "category": category.value,
                "default_unit": default_unit.value,
                "default_quota_basis": PrWorkQuotaBasis.ITEM_COUNT.value,
                **provenance,
            },
        )
        return row

    async def update_work_type(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_type_id: uuid.UUID,
        code: str | None = None,
        name: str | None = None,
        category: PrWorkCategory | None = None,
        description: str | None = None,
        default_unit: PrWorkUnit | None = None,
        default_quota_basis: PrWorkQuotaBasis | None = None,
        requires_evidence: bool | None = None,
        display_order: int | None = None,
    ) -> PrWorkType:
        """Edit a work type. ``PR_WORK_CONFIGURE``. **Structurally locked once used.**

        M1 made ``code``, ``category`` and ``default_unit`` permanently
        uneditable by leaving them off the signature, and let
        ``default_quota_basis`` be changed at any time. M2.5 replaces both
        halves of that with one rule that depends on **use** rather than on
        which field it is:

        * before a type has ever been used, everything is editable. A type
          created five minutes ago with the wrong basis is a typo, and forcing a
          second type to fix it would leave the mistake in the picker for ever;
        * once it is used, ``code``, ``default_quota_basis`` and
          ``default_unit`` are refused - see
          :data:`~meobot.domain.pr.work_types.WORK_TYPE_STRUCTURAL_FIELDS`. They
          are what historical rows *mean*, and the answer to a real change of
          measurement model is a new type beside the old one.

        ``category`` moves to the safe side in the same change, and the
        asymmetry is deliberate rather than an oversight: a category is how a
        report groups rows, not what a row measured.

        Refusal is explicit. A structural field that is silently ignored is
        worse than one that is refused, because the screen then shows the old
        value and the person believes they changed it.

        ``is_active`` is **not** here. Retiring a type is its own decision with
        its own audit action - see :meth:`activate_work_type` and
        :meth:`deactivate_work_type`.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        row = await self._require_work_type(work_type_id)

        normalized_code = _require_code(code) if code is not None else None
        if (
            normalized_code is not None
            and normalized_code != row.code
            and is_auto_work_type_code(normalized_code)
        ):
            # The other door into the reserved namespace. A hand-made type
            # renamed into it would claim a provenance it does not have - see
            # :meth:`create_work_type`. (A provisioned type cannot be renamed
            # *out* of it: it is in use from the moment it exists, and the
            # structural lock below refuses the code change.)
            raise PrValidationError(
                f"Mã bắt đầu bằng {AUTO_WORK_TYPE_CODE_PREFIX} được dành riêng cho loại "
                "công việc hệ thống tự tạo từ nội dung.",
                details={"field": "code", "reason": "reserved_code", "code": normalized_code},
            )
        # ``default_unit`` is deliberately **not** in this dictionary. The unit is
        # what a result is called, every stored amount carries its own copy, and
        # renaming it is exactly what an administrator correcting "sản phẩm" to
        # "khách hàng" needs - see ``WORK_TYPE_STRUCTURAL_FIELDS`` and
        # :meth:`_propagate_unit`.
        requested_structural = {
            "code": None
            if normalized_code is None or normalized_code == row.code
            else (normalized_code),
            "default_quota_basis": (
                None
                if default_quota_basis is None or default_quota_basis is row.default_quota_basis
                else default_quota_basis
            ),
        }
        changing = sorted(
            field for field, value in requested_structural.items() if value is not None
        )
        # Asked only when something structural would actually change: an edit
        # that resubmits the whole form unchanged is not an attempt to rewrite
        # history, and refusing it would make the screen unusable for the
        # name-only change it is really doing.
        if changing and await self.is_work_type_in_use(row.id):
            raise PrConflictError(
                "Loại công việc đã phát sinh dữ liệu nên không đổi được mã hoặc cách tính. "
                "Hãy tạo loại công việc mới.",
                details={
                    "field": changing[0],
                    "reason": WORK_TYPE_STRUCTURE_LOCKED,
                    "work_type_code": row.code,
                    "locked_fields": changing,
                },
            )
        # Same rule as :meth:`create_work_type`, at the one other door into a
        # QUANTITY type: switching an unused type onto QUANTITY while leaving
        # the unit on the ``ITEM`` placeholder would produce exactly the
        # "100 sản phẩm" ambiguity create refuses.
        if (
            default_quota_basis is PrWorkQuotaBasis.QUANTITY
            and default_quota_basis is not row.default_quota_basis
            and default_unit is None
        ):
            raise PrValidationError(
                "Loại công việc tính theo số lượng phải có đơn vị.",
                details={"field": "default_unit", "reason": "unit_required_for_quantity"},
            )
        if (
            normalized_code is not None
            and normalized_code != row.code
            and await self._work_type_by_code(normalized_code) is not None
        ):
            raise PrConflictError(
                "Đã có loại công việc với mã này.",
                details={
                    "field": "code",
                    "reason": "duplicate_code",
                    "code": normalized_code,
                },
            )

        # Every editable field, so that the "only what moved" filter below
        # covers all of them. A subset would make a description-only edit write
        # an audit row with an empty diff - a row saying somebody changed
        # something and declining to say what.
        def snapshot() -> dict[str, object]:
            return {
                "code": row.code,
                "name": row.name,
                "category": row.category.value,
                "description": row.description,
                "default_unit": row.default_unit.value,
                "default_quota_basis": row.default_quota_basis.value,
                "requires_evidence": row.requires_evidence,
                "display_order": row.display_order,
            }

        before = snapshot()
        if normalized_code is not None:
            row.code = normalized_code
        if name is not None:
            row.name = _require_text(name, "name", MAX_LABEL)
        if category is not None:
            row.category = category
        if description is not None:
            row.description = _optional_text(description, "description", MAX_TEXT)
        unit_changed = default_unit is not None and default_unit is not row.default_unit
        if default_unit is not None:
            row.default_unit = default_unit
        if default_quota_basis is not None:
            row.default_quota_basis = default_quota_basis
        if requires_evidence is not None:
            row.requires_evidence = requires_evidence
        if display_order is not None:
            row.display_order = max(0, display_order)
        await self._session.flush()
        if unit_changed:
            await self._propagate_unit(row)
        after = snapshot()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_TYPE_UPDATED,
            entity_type="pr_work_type",
            entity_id=row.id,
            # Only what moved. A trail that restates six unchanged fields on
            # every rename is a trail somebody stops reading.
            before={key: value for key, value in before.items() if after[key] != value},
            after={key: value for key, value in after.items() if before[key] != value},
        )
        return row

    async def _propagate_unit(self, work_type: PrWorkType) -> None:
        """Carry a renamed unit onto what still reads it live. Nothing historical.

        Three kinds of row copy a type's unit, and each is treated by what it
        is rather than by when it was written:

        * **period containers in months that are still current** - open
          periods whose last day has not passed. Their quantity is a running
          sum that is still being reported into, and the person reading
          "27 sản phẩm" today is the person who asked for "27 khách hàng";
        * **quotas on draft plans** - a draft is a proposal nobody has approved,
          and leaving it on the old unit would make it fail readiness for a
          reason nobody can act on. Approved and superseded quotas are history
          and are not touched;
        * **one-off jobs and containers in past or closed months** - left
          exactly as filed. That is the whole reason the unit is copied onto
          each row: a report about August says what August said.
        """
        from meobot.db.models.pr_reporting import PrReportingPeriod
        from meobot.db.models.pr_work_quota import PrWorkPlan, PrWorkQuota
        from meobot.domain.pr.reporting import PrPeriodStatus
        from meobot.domain.pr.work_quota import PrWorkPlanStatus

        today = utcnow().date()
        current_periods = select(PrReportingPeriod.id).where(
            PrReportingPeriod.status == PrPeriodStatus.OPEN,
            PrReportingPeriod.date_end >= today,
        )
        await self._session.execute(
            update(PrWorkItem)
            .where(
                PrWorkItem.work_type_id == work_type.id,
                PrWorkItem.reporting_period_id.in_(current_periods),
                PrWorkItem.unit.is_not(None),
            )
            .values(unit=work_type.default_unit)
            .execution_options(synchronize_session="fetch")
        )
        draft_plans = select(PrWorkPlan.id).where(PrWorkPlan.status == PrWorkPlanStatus.DRAFT)
        await self._session.execute(
            update(PrWorkQuota)
            .where(
                PrWorkQuota.work_type_id == work_type.id,
                PrWorkQuota.plan_id.in_(draft_plans),
                PrWorkQuota.unit.is_not(None),
            )
            .values(unit=work_type.default_unit)
            .execution_options(synchronize_session="fetch")
        )
        await self._session.flush()

    async def set_work_type_active(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_type_id: uuid.UUID,
        is_active: bool,
    ) -> PrWorkType:
        """Retire a kind of work, or bring it back. ``PR_WORK_CONFIGURE``.

        **Never a delete.** An inactive type keeps every row filed under it,
        stays readable on historical work and in approved plans, and simply
        stops being offered for anything new - :meth:`_create` and M2's
        ``add_quota`` both refuse it. That is the whole lifecycle: the taxonomy
        only ever grows, and what changes is what may be chosen next.

        Idempotent, and quietly so. Deactivating an already inactive type writes
        no audit row, because nothing was decided.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        row = await self._require_work_type(work_type_id)
        if row.is_active is is_active:
            return row
        if not is_active:
            # A type an active Content → Work rule still files into cannot be
            # retired underneath that rule: the next accepted deliverable would
            # have nowhere to go, and the projector would have to choose between
            # failing it and silently provisioning a second type beside the
            # first - splitting one stream's history in two. Neither is
            # acceptable, so the order is fixed: remap or deactivate the rule,
            # then retire the type.
            mapped = (
                (
                    await self._session.execute(
                        select(PrContentWorkRule)
                        .where(
                            PrContentWorkRule.work_type_id == row.id,
                            PrContentWorkRule.is_active.is_(True),
                        )
                        .order_by(PrContentWorkRule.contribution_kind.asc())
                    )
                )
                .scalars()
                .all()
            )
            if mapped:
                raise PrValidationError(
                    "Loại công việc này đang được ánh xạ từ nội dung. Hãy đổi hoặc tắt ánh xạ "
                    "nội dung trước khi tắt loại công việc.",
                    details={
                        "field": "is_active",
                        "reason": "work_type_mapped_from_content",
                        "code": row.code,
                        "rules": [
                            {
                                "contribution_kind": one.contribution_kind.value,
                                "content_type": (
                                    one.content_type.value if one.content_type else None
                                ),
                            }
                            for one in mapped
                        ],
                    },
                )
        row.is_active = is_active
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=(
                AuditAction.PR_WORK_TYPE_ACTIVATED
                if is_active
                else AuditAction.PR_WORK_TYPE_DEACTIVATED
            ),
            entity_type="pr_work_type",
            entity_id=row.id,
            before={"is_active": not is_active},
            after={"is_active": is_active, "code": row.code},
        )
        return row

    async def bootstrap_work_types(
        self, *, actor: Actor, request_id: uuid.UUID
    ) -> Sequence[PrWorkType]:
        """Create the starting taxonomy. ``PR_WORK_CONFIGURE``. **Idempotent.**

        Why this is not a migration: these rows are business data the owner
        renames, recategorises and retires. A migration that inserted them would
        make the department's edits look like drift the next
        ``compare_metadata`` sweep complains about, and re-running it after a
        rename would either fail or undo the rename.

        Idempotent **on ``code``**, which is the identifier that never changes
        under a rename. A second run therefore matches a type the owner has
        since renamed to "Kịch bản ngắn" and leaves it alone - it does not
        restore the original name, and it does not add a duplicate beside it.

        A type the owner has since **deactivated** is also left alone. Skipping
        it is the point: reactivating what somebody deliberately retired, every
        time this is run, would make the run a way to undo a decision.

        Returns only the rows it created, so a caller can say "nothing to do".
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        existing = {
            row.code for row in (await self._session.execute(select(PrWorkType))).scalars().all()
        }
        created: list[PrWorkType] = []
        for spec in BOOTSTRAP_WORK_TYPES:
            if spec.code in existing:
                continue
            row = PrWorkType(
                code=spec.code,
                name=spec.name,
                category=spec.category,
                default_unit=spec.default_unit,
                default_quota_basis=spec.default_quota_basis,
                display_order=spec.display_order,
            )
            self._session.add(row)
            created.append(row)
        if not created:
            return ()
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_TYPES_BOOTSTRAPPED,
            entity_type="pr_work_type",
            entity_id=None,
            after={"created_codes": [row.code for row in created], "created": len(created)},
        )
        return tuple(created)

    async def list_work_types(
        self, *, actor: Actor, include_inactive: bool = False
    ) -> Sequence[PrWorkType]:
        """The taxonomy, for a picker. Any authenticated PR reader may see it.

        ``include_inactive`` is **not** a filter the caller is trusted with. M1
        let anyone holding ``PR_WORK_EXECUTE`` ask for the retired types too,
        which made a deactivated type one query parameter away from being
        offered again by any client that asked for everything. M2.5 requires
        ``PR_WORK_CONFIGURE`` for it: seeing what the department has retired is
        part of configuring the taxonomy, and nothing an employee's picker
        needs.

        Ordered by category, then the owner's ``display_order``, then name - so
        a picker reads in the order somebody arranged rather than by id.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_EXECUTE)
        if include_inactive:
            await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        statement = select(PrWorkType)
        if not include_inactive:
            statement = statement.where(PrWorkType.is_active.is_(True))
        statement = statement.order_by(
            PrWorkType.category.asc(), PrWorkType.display_order.asc(), PrWorkType.name.asc()
        )
        return (await self._session.execute(statement)).scalars().all()

    async def get_work_type(self, *, actor: Actor, work_type_id: uuid.UUID) -> PrWorkType:
        """One kind of work, active or not. ``PR_WORK_EXECUTE``.

        Readable when inactive, unlike the list: a work item filed last March
        still names its type, and a screen that could not resolve it would show
        history with a hole in it.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_EXECUTE)
        return await self._require_work_type(work_type_id)

    async def is_work_type_in_use(self, work_type_id: uuid.UUID) -> bool:
        """Whether any real business record refers to this type. **M2.5.**

        The one place the question is answered, and the reason it is one place:
        the same answer decides whether the server refuses a structural edit and
        whether the screen draws the field disabled. Two implementations would
        eventually disagree, and the way they would disagree is a locked field
        the API happily accepts.

        Three references, and each is a distinct way history would be rewritten:

        * a **work item** was filed under it - its ``unit`` was copied from this
          type at creation, so the unit is what its ``quantity`` means;
        * a **quota** measures it - written under this type's basis;
        * an **allocation** decided a contribution against it - the row a
          reported month was computed from, which outlives the quota that
          produced it and so is asked about separately rather than assumed to
          follow the quota.

        Deliberately **not** consulted: M3's content mapping. That table is not
        on this branch, and its own ``RESTRICT`` foreign key protects it
        independently - see ``docs/pr/WORK_TYPE_MANAGEMENT_M25.md``.
        """
        statement = select(
            or_(
                exists().where(PrWorkItem.work_type_id == work_type_id),
                exists().where(PrWorkQuota.work_type_id == work_type_id),
                exists().where(PrWorkQuotaAllocation.work_type_id == work_type_id),
            )
        )
        return bool((await self._session.execute(statement)).scalar())

    # =====================================================================
    # Creating work
    # =====================================================================
    async def propose_work(
        self, *, actor: Actor, request_id: uuid.UUID, command: CreateWorkCommand
    ) -> PrWorkItem:
        """An employee suggests work they did or want to do. ``PR_WORK_EXECUTE``.

        Lands at :attr:`~meobot.domain.pr.work.PrWorkStatus.PROPOSED`, which is
        **not work**: it is in nobody's workload, its contribution is
        ``PENDING``, and no period figure counts it. Somebody else has to accept
        it before any of that changes, and :meth:`accept` refuses the proposer.

        The proposer is always a contributor on their own proposal, including
        when they name other people as well. Proposing work you had no part in
        is a management act, and management acts go through
        :meth:`assign_work`.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_EXECUTE)
        await self._validate_operational_command(command)
        proposer = _require_user_id(actor)
        contributors = _unique(command.contributor_user_ids or ()) or (proposer,)
        if proposer not in contributors:
            contributors = (proposer, *contributors)
        return await self._create(
            actor=actor,
            request_id=request_id,
            command=command,
            status=PrWorkStatus.PROPOSED,
            contributors=contributors,
            assigned_by=None,
        )

    async def assign_work(
        self, *, actor: Actor, request_id: uuid.UUID, command: CreateWorkCommand
    ) -> PrWorkItem:
        """A manager creates work and puts it on somebody. ``PR_WORK_MANAGE``.

        Lands at :attr:`~meobot.domain.pr.work.PrWorkStatus.ACCEPTED` directly:
        **the assignment is the authorization**, and asking a manager to accept
        their own assignment afterwards would be a second click that decides
        nothing. That is the asymmetry the anti-gaming rule needs - an employee
        cannot put work into their own workload, and a manager can, because
        that is what a manager is for.

        At least one contributor is required. Work assigned to nobody is a note.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        await self._validate_operational_command(command)
        contributors = _unique(command.contributor_user_ids)
        if not contributors:
            raise PrValidationError(
                "Hãy chọn ít nhất một người thực hiện.",
                details={"field": "contributor_user_ids", "reason": "no_contributor"},
            )
        return await self._create(
            actor=actor,
            request_id=request_id,
            command=command,
            status=PrWorkStatus.ACCEPTED,
            contributors=contributors,
            assigned_by=_require_user_id(actor),
        )

    async def assign_work_batch(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        command: CreateWorkCommand,
        mode: PrWorkAssignmentMode,
    ) -> tuple[PrWorkItem, ...]:
        """A manager assigns work to several people. ``PR_WORK_MANAGE``. **M4A.**

        The two modes are two different business facts, not two spellings of
        one - see :class:`~meobot.domain.pr.work.PrWorkAssignmentMode`:

        * ``SHARED_WORK`` is exactly :meth:`assign_work`, delegated to
          unchanged. One job, one item, one contribution per person;
        * ``SEPARATE_PER_ASSIGNEE`` creates **one whole work item per person**,
          each with that person as its only contributor. Three people told to
          do a hundred comments each are three obligations: each is completed
          separately, validated separately by somebody who did not do it, and
          late separately.

        Every item is created through :meth:`_create` - the same method the
        single-assignee path uses - so a batch is *n* ordinary manual work
        items and not a fourth kind of work. They share the request's
        transaction, so a refusal on the third assignee leaves none of the
        first two behind.

        Returns:
            The items created, in the order the contributors were named. One
            element for ``SHARED_WORK``, one per contributor otherwise.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        await self._validate_operational_command(command)
        contributors = _unique(command.contributor_user_ids)
        if not contributors:
            raise PrValidationError(
                "Hãy chọn ít nhất một người thực hiện.",
                details={"field": "contributor_user_ids", "reason": "no_contributor"},
            )
        if mode is PrWorkAssignmentMode.SHARED_WORK:
            return (await self.assign_work(actor=actor, request_id=request_id, command=command),)
        # Validated once, before anything is written: an assignee list whose
        # last name is inactive must not leave the first two people holding
        # work from a batch that was refused.
        await self._require_active_users(contributors)
        assigned_by = _require_user_id(actor)
        items: list[PrWorkItem] = []
        for user_id in contributors:
            items.append(
                await self._create(
                    actor=actor,
                    request_id=request_id,
                    command=command,
                    status=PrWorkStatus.ACCEPTED,
                    contributors=(user_id,),
                    assigned_by=assigned_by,
                )
            )
        logger.info(
            "pr_work_assigned_separately",
            extra={
                "pr_work_items": len(items),
                "pr_work_assignees": len(contributors),
            },
        )
        return tuple(items)

    async def generate_recurring_work(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        command: CreateWorkCommand,
        source_key: str,
        occurred_at: datetime,
        occurrence_id: uuid.UUID,
    ) -> PrWorkItem:
        """One work item a recurring template's occurrence owes. **M4B.**

        The fourth creation path, and deliberately a *sibling* of
        :meth:`assign_work` rather than a private shortcut past it:

        * it is an **operational command**, so
          :meth:`_validate_operational_command` applies exactly as it does to a
          manager filling in the form. A template that somehow reached ``ACTIVE``
          with no quantity on a quantity-measured type is refused here as well
          as at the template, because a rule enforced in one place is a rule with
          one bug between it and being unenforced;
        * it requires ``PR_WORK_MANAGE`` **of the actor**, and the actor is the
          manager who activated the template - not the worker process. An
          ``ACTIVE`` template is that manager's standing authorization for the
          routine, so the work is filed under their name, and if their authority
          has since been withdrawn the generation fails rather than proceeding
          on an authorization that no longer exists;
        * it lands at ``ACCEPTED``, for the same reason :meth:`assign_work` does:
          *the assignment is the authorization*. It is emphatically **not**
          ``COMPLETED``, ``APPROVED`` or ``COUNTED`` - activating a template is
          not doing the work and not validating it, and M1's independent
          approval is still the only thing that can make any of this count.

        ``occurred_at`` is the occurrence's scheduled instant, not now, so work
        caught up after an outage is dated to the day it was owed.

        Idempotency is the database's, exactly as it is for M3: the caller
        composes ``source_key`` from the occurrence row's id, and
        ``uq_pr_work_items_source`` makes a second attempt at the same
        occurrence collide rather than duplicate.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        await self._validate_operational_command(command)
        assert_source_key(source_key)
        contributors = _unique(command.contributor_user_ids)
        if not contributors:
            raise PrValidationError(
                "Hãy chọn ít nhất một người thực hiện.",
                details={"field": "contributor_user_ids", "reason": "no_contributor"},
            )
        return await self._create(
            actor=actor,
            request_id=request_id,
            command=command,
            status=PrWorkStatus.ACCEPTED,
            contributors=contributors,
            assigned_by=_require_user_id(actor),
            source_type=PrWorkSourceType.RECURRING,
            source_key=source_key,
            at=occurred_at,
            # **The occurrence's own scheduled instant.** A routine's execution
            # date is the firing it came from, not the moment the sweeper got to
            # it - which after an outage can be three days later.
            execution_at=occurred_at,
            # The provenance edge, so a work card can reach the template without
            # anybody decoding ``source_key``.
            recurring_occurrence_id=occurrence_id,
        )

    async def _validate_operational_command(self, command: CreateWorkCommand) -> None:
        """Every rule an **operational work command** must satisfy. **M4B.**

        The boundary this method *is*, stated once because getting it wrong in
        either direction breaks a different milestone:

        * **above it** - :meth:`propose_work`, :meth:`assign_work`,
          :meth:`assign_work_batch` and :meth:`generate_recurring_work`, plus
          the recurring template that plans a future call to the last of them -
          somebody is **commanding work into existence now**. Every rule about
          what a well-formed instruction looks like applies, and applies here
          rather than at the route, so a service-level caller who never touched
          FastAPI cannot bypass it. That was the M4A gap: the quantity rule sat
          in the router, and the recurring generator would have been the first
          caller in MeoBot to create operational work without going through one;
        * **below it** - :meth:`_create`, :meth:`create_source_work` and the
          reconciliation paths - is **representation**, and it stays deliberately
          permissive. M2's ``UNMEASURABLE`` / ``MISSING_QUANTITY`` allocation
          describes something that can still be true of rows already in the
          table: a quantity-measured job filed years ago with no number. Making
          that unconstructible would not make the data cleaner, it would remove
          the system's ability to *say* what is wrong with it, and M2 built that
          state specifically to replace a silent ``NO_QUOTA`` that told an
          employee their manager had set no target when their manager had.

        The distinction is the difference between "you may not ask for this" and
        "this cannot be described", and only the first is a rule about commands.
        """
        require_quantity_for_basis(
            await self._require_work_type(command.work_type_id), command.quantity
        )

    async def _create(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        command: CreateWorkCommand,
        status: PrWorkStatus,
        contributors: tuple[uuid.UUID, ...],
        assigned_by: uuid.UUID | None,
        source_type: PrWorkSourceType = PrWorkSourceType.MANUAL,
        source_key: str | None = None,
        at: datetime | None = None,
        execution_at: datetime | None = None,
        recurring_occurrence_id: uuid.UUID | None = None,
    ) -> PrWorkItem:
        """The half the creation entry points share. **Never called from a route.**

        ``source_type`` defaults to ``MANUAL``, which is the whole of M1's source
        story: no request body reaches a field that could claim derived
        provenance, so a person cannot file work as though the content workflow
        had produced it. M4B may override it, and only M4B does - see
        :meth:`generate_recurring_work`, which is the one caller that passes a
        source, and which is itself an operational entry point subject to
        :meth:`_validate_operational_command`.

        **This method validates representation, not commands.** It refuses a
        negative quantity, an inactive work type and an inactive contributor,
        because none of those can be described coherently at all. It does *not*
        refuse a quantity-measured type filed without a number, because that
        can - see :meth:`_validate_operational_command` for the whole of why.

        ``at`` back-dates the assignment and acceptance timestamps to the moment
        the work is deemed to have arisen. Catch-up generation after an outage
        uses it, so a job owed on Tuesday says Tuesday.

        ``execution_at`` is **when the work is performed**, and is a different
        parameter from ``at`` because they are different facts: ``at`` moves the
        row's own bookkeeping, while ``execution_at`` is what a person reads as
        *"Thực hiện"*. Only a caller a source has told may pass it - see the
        column. It stays null for everything a person files, because nowhere in
        the manual creation form does anybody say which day they will do it.
        """
        now = at or utcnow()
        work_type = await self._require_work_type(command.work_type_id)
        if not work_type.is_active:
            raise PrValidationError(
                "Loại công việc này đã ngừng sử dụng.",
                details={"field": "work_type_id", "reason": "work_type_inactive"},
            )
        if len(contributors) > MAX_CONTRIBUTORS:
            raise PrValidationError(
                f"Một công việc không nhận quá {MAX_CONTRIBUTORS} người thực hiện.",
                details={"field": "contributor_user_ids", "reason": "too_many_contributors"},
            )
        await self._require_active_users(contributors)
        if (
            command.channel_id is not None
            and await self._session.get(PrChannel, command.channel_id) is None
        ):
            raise PrNotFoundError(
                "Không tìm thấy kênh.", details={"channel_id": str(command.channel_id)}
            )

        quantity = _require_quantity(command.quantity)
        item = PrWorkItem(
            code=await self._codes.allocate_work_code(at=now),
            title=_require_text(command.title, "title", MAX_TITLE),
            description=_optional_text(command.description, "description", MAX_TEXT),
            work_type_id=work_type.id,
            # M1's entry points produce manual work and nothing else; only
            # M4B's generator passes anything here, and it passes a key that
            # ``assert_source_key`` has already validated.
            source_type=source_type,
            source_key=source_key,
            status=status,
            priority=command.priority,
            quantity=quantity,
            # Copied from the type rather than joined at read time, so editing
            # a type never rewrites what historical work claimed to be. Absent
            # when there is no quantity - the CHECK constraint requires the pair.
            unit=work_type.default_unit if quantity is not None else None,
            created_by_user_id=_require_user_id(actor),
            assigned_by_user_id=assigned_by,
            assigned_at=now if assigned_by is not None else None,
            accepted_at=now if status is PrWorkStatus.ACCEPTED else None,
            due_at=command.due_at,
            execution_at=execution_at,
            recurring_occurrence_id=recurring_occurrence_id,
            channel_id=command.channel_id,
        )
        self._session.add(item)
        await self._session.flush()

        for user_id in contributors:
            self._session.add(
                PrWorkContribution(
                    work_item_id=item.id,
                    user_id=user_id,
                    contribution_role=PrWorkContributionRole.PRIMARY
                    if user_id == contributors[0]
                    else PrWorkContributionRole.CONTRIBUTOR,
                    credit_weight=DEFAULT_CREDIT_WEIGHT,
                    assigned_at=now,
                )
            )
        await self._session.flush()

        await self._history(
            item,
            event=PrWorkEventType.PROPOSED
            if status is PrWorkStatus.PROPOSED
            else PrWorkEventType.ASSIGNED,
            actor=actor,
            to_status=status,
            metadata={"contributors": [str(one) for one in contributors]},
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_CREATED,
            entity_type="pr_work_item",
            entity_id=item.id,
            after={
                "code": item.code,
                "status": status.value,
                "work_type": work_type.code,
                "contributors": [str(one) for one in contributors],
                "source_type": item.source_type.value,
            },
        )
        if self._notifier is not None and status is PrWorkStatus.ACCEPTED:
            await self._notifier.work_assigned(item, contributors, actor=actor)
        return item

    # =====================================================================
    # The acceptance boundary
    # =====================================================================
    async def accept(
        self, *, actor: Actor, request_id: uuid.UUID, work_item_id: uuid.UUID
    ) -> PrWorkItem:
        """Take on somebody's proposal. ``PR_WORK_MANAGE``.

        **The proposer is refused.** If somebody could accept their own
        proposal, "propose" would be "assign" spelled differently and an
        employee would be writing their own workload - which is the first thing
        the milestone exists to prevent. Holding ``PR_WORK_MANAGE`` does not
        help: a manager may accept anybody's proposal except their own.

        Raises:
            PrPermissionDeniedError: Not a work manager, or the proposer.
            PrValidationError: Not at ``PROPOSED``.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        item = await self._lock(work_item_id)
        _refuse_container(item, action="accept")
        assert_work_transition(item.status, PrWorkStatus.ACCEPTED)
        actor_id = _require_user_id(actor)
        if item.created_by_user_id == actor_id:
            raise PrPermissionDeniedError(
                "Bạn không thể tự duyệt đề xuất công việc của mình. "
                "Cần một người quản lý khác chấp nhận.",
                details={
                    "reason": "self_acceptance",
                    "work_item_id": str(item.id),
                },
            )

        now = utcnow()
        item.status = PrWorkStatus.ACCEPTED
        item.accepted_at = now
        item.assigned_by_user_id = actor_id
        item.assigned_at = item.assigned_at or now
        await self._session.flush()

        await self._history(
            item,
            event=PrWorkEventType.ACCEPTED,
            actor=actor,
            from_status=PrWorkStatus.PROPOSED,
            to_status=PrWorkStatus.ACCEPTED,
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_ACCEPTED,
            entity_type="pr_work_item",
            entity_id=item.id,
            before={"status": PrWorkStatus.PROPOSED.value},
            after={"status": item.status.value, "code": item.code},
        )
        if self._notifier is not None:
            await self._notifier.proposal_decided(item, accepted=True, actor=actor)
        return item

    async def reject(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        reason: str | None = None,
    ) -> PrWorkItem:
        """Decline a proposal. ``PR_WORK_MANAGE``, and not by its proposer.

        Terminal, and the row stays: a rejected proposal is a thing somebody
        suggested and a manager declined, and deleting it would lose both halves
        of that. Its contributions are marked ``EXCLUDED`` rather than left
        ``PENDING`` for ever.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        item = await self._lock(work_item_id)
        _refuse_container(item, action="reject")
        assert_work_transition(item.status, PrWorkStatus.REJECTED)
        actor_id = _require_user_id(actor)
        if item.created_by_user_id == actor_id:
            raise PrPermissionDeniedError(
                "Bạn không thể tự xử lý đề xuất công việc của mình.",
                details={"reason": "self_acceptance", "work_item_id": str(item.id)},
            )
        now = utcnow()
        item.status = PrWorkStatus.REJECTED
        item.cancelled_at = now
        item.cancelled_by_user_id = actor_id
        item.cancel_reason = _optional_text(reason, "reason", MAX_TEXT)
        await self._exclude_contributions(item, actor=actor, reason="Đề xuất không được chấp nhận")
        await self._session.flush()

        await self._history(
            item,
            event=PrWorkEventType.REJECTED,
            actor=actor,
            from_status=PrWorkStatus.PROPOSED,
            to_status=PrWorkStatus.REJECTED,
            note=item.cancel_reason,
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_REJECTED,
            entity_type="pr_work_item",
            entity_id=item.id,
            before={"status": PrWorkStatus.PROPOSED.value},
            after={"status": item.status.value, "reason": item.cancel_reason},
        )
        if self._notifier is not None:
            await self._notifier.proposal_decided(item, accepted=False, actor=actor)
        return item

    # =====================================================================
    # Execution
    # =====================================================================
    async def start(
        self, *, actor: Actor, request_id: uuid.UUID, work_item_id: uuid.UUID
    ) -> PrWorkItem:
        """Mark accepted work as being done. A contributor's own act."""
        item = await self._lock(work_item_id)
        _refuse_container(item, action="start")
        await self._require_contributor_or_manager(actor, item)
        # **Start owns one edge.** The table also admits ``COMPLETED →
        # IN_PROGRESS``, but that edge is the validator's *Trả lại*
        # (:meth:`reopen`): a contributor pressing "Bắt đầu" on finished work
        # must not quietly un-finish it. The action contract advertises
        # ``can_start`` from ``ACCEPTED`` only, and this is the write agreeing.
        _require_status(item, PrWorkStatus.ACCEPTED, target=PrWorkStatus.IN_PROGRESS)
        assert_work_transition(item.status, PrWorkStatus.IN_PROGRESS)
        before = item.status
        item.status = PrWorkStatus.IN_PROGRESS
        item.started_at = item.started_at or utcnow()
        await self._session.flush()
        await self._history(
            item,
            event=PrWorkEventType.STARTED,
            actor=actor,
            from_status=before,
            to_status=item.status,
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_STARTED,
            entity_type="pr_work_item",
            entity_id=item.id,
            before={"status": before.value},
            after={"status": item.status.value},
        )
        return item

    async def complete(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        note: str | None = None,
    ) -> PrWorkItem:
        """A contributor says the work is finished.

        **This does not make it count.** The item moves to ``COMPLETED``, whose
        Vietnamese label is *"Chờ xác nhận"*, and every contribution stays
        ``PENDING`` until somebody who did not do the work approves it. That
        sentence is the milestone.

        Evidence is required here rather than at insert when the work type says
        so - the same place ``required_for_review`` is enforced on content
        resources, and the only place where "is this finished" is being asked.
        """
        item = await self._lock(work_item_id)
        _refuse_container(item, action="complete")
        await self._require_contributor_or_manager(actor, item)
        assert_work_transition(item.status, PrWorkStatus.COMPLETED)
        work_type = await self._require_work_type(item.work_type_id)
        if work_type.requires_evidence and not await self._has_evidence(item.id):
            raise PrValidationError(
                "Loại công việc này cần ít nhất một minh chứng trước khi báo hoàn thành.",
                details={"field": "evidence", "reason": "evidence_required"},
            )

        before = item.status
        item.status = PrWorkStatus.COMPLETED
        item.completed_at = utcnow()
        item.completed_by_user_id = _require_user_id(actor)
        await self._session.flush()
        await self._history(
            item,
            event=PrWorkEventType.COMPLETED,
            actor=actor,
            from_status=before,
            to_status=item.status,
            note=_optional_text(note, "note", MAX_TEXT),
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_COMPLETED,
            entity_type="pr_work_item",
            entity_id=item.id,
            before={"status": before.value},
            after={"status": item.status.value, "completed_at": item.completed_at.isoformat()},
        )
        if self._notifier is not None:
            await self._notifier.awaiting_validation(item, actor=actor)
        return item

    async def reopen(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        reason: str | None = None,
    ) -> PrWorkItem:
        """A validator sends finished work back. ``PR_WORK_VALIDATE``.

        The counterpart of :meth:`approve`, and the reason it exists: without
        it a validator looking at work that is not done has only two options -
        approve it anyway, or cancel somebody's afternoon.

        Nothing is counted and nothing is un-counted here: the item never
        reached ``APPROVED``, so no contribution ever had a ``counted_at`` to
        take back.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_VALIDATE)
        item = await self._lock(work_item_id)
        _refuse_container(item, action="reopen")
        # **Reopen owns one edge.** ``ACCEPTED → IN_PROGRESS`` is in the table
        # too, but that is a contributor starting work (:meth:`start`), and a
        # validator "sending back" work nobody has finished is not a thing.
        # ``can_reopen`` is advertised from ``COMPLETED`` only; this agrees.
        _require_status(item, PrWorkStatus.COMPLETED, target=PrWorkStatus.IN_PROGRESS)
        assert_work_transition(item.status, PrWorkStatus.IN_PROGRESS)
        item.status = PrWorkStatus.IN_PROGRESS
        item.completed_at = None
        item.completed_by_user_id = None
        await self._session.flush()
        await self._history(
            item,
            event=PrWorkEventType.REOPENED,
            actor=actor,
            from_status=PrWorkStatus.COMPLETED,
            to_status=item.status,
            note=_optional_text(reason, "reason", MAX_TEXT),
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_REOPENED,
            entity_type="pr_work_item",
            entity_id=item.id,
            before={"status": PrWorkStatus.COMPLETED.value},
            after={"status": item.status.value, "reason": reason},
        )
        return item

    # =====================================================================
    # The counting boundary
    # =====================================================================
    async def approve(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        note: str | None = None,
    ) -> WorkItemDetail:
        """Independently validate finished work. **The only thing that counts.**

        ``PR_WORK_VALIDATE``, **and the actor must not be a contributor.** The
        second half is not a refinement of the first: it is the rule, and it
        applies to an OWNER exactly as it applies to anybody else. A person who
        did the work cannot be the person who confirms it was done, because the
        confirmation is what puts a number on their own performance record.

        Everything in one transaction, in this order, and the order matters:

        1. lock the item, so a second validator waits rather than racing;
        2. re-read the status under the lock and check the edge is legal;
        3. check the capability;
        4. check the actor contributed to nothing on this item;
        5. stamp ``approved_at`` on the item;
        6. stamp ``count_status = COUNTED`` and ``counted_at`` on every
           contribution that is still ``PENDING``;
        7. append the history and audit rows.

        The second validator to arrive finds ``APPROVED`` at step 2 and is
        refused by the transition matrix, so nothing is counted twice - and the
        database says the same thing independently through
        ``ck_pr_work_contributions_counted_at_matches_status``.

        ``counted_at`` is **one instant for every contribution on the item**,
        which is what makes period attribution unambiguous: a three-person
        shoot cannot land in two months because two of the writes happened
        either side of midnight.

        An ``EXCLUDED`` contribution is left alone. Exclusion is a deliberate
        decision, and approving the item around it must not quietly undo it.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_VALIDATE)
        item = await self._lock(work_item_id)
        _refuse_container(item, action="approve")
        assert_work_transition(item.status, PrWorkStatus.APPROVED)
        actor_id = _require_user_id(actor)

        contributions = await self._contributions(item.id)
        if any(row.user_id == actor_id for row in contributions):
            raise PrPermissionDeniedError(
                "Bạn có tham gia công việc này nên không thể tự xác nhận. "
                "Cần một người khác xác nhận để công việc được ghi nhận.",
                details={
                    "reason": "self_validation",
                    "work_item_id": str(item.id),
                },
            )

        now = utcnow()
        await self._require_not_finalized(
            now,
            user_ids=[
                row.user_id
                for row in contributions
                if row.count_status is PrWorkCountStatus.PENDING
            ],
        )
        item.status = PrWorkStatus.APPROVED
        item.approved_at = now
        item.approved_by_user_id = actor_id

        counted: list[PrWorkContribution] = []
        for row in contributions:
            if row.count_status is not PrWorkCountStatus.PENDING:
                continue
            row.count_status = PrWorkCountStatus.COUNTED
            row.counted_at = now
            counted.append(row)
        await self._session.flush()

        await self._history(
            item,
            event=PrWorkEventType.APPROVED,
            actor=actor,
            from_status=PrWorkStatus.COMPLETED,
            to_status=PrWorkStatus.APPROVED,
            note=_optional_text(note, "note", MAX_TEXT),
            # **A Work decision.** ``count_origin`` reads this back so a
            # source replay never undoes a person's validation.
            metadata={"origin": PrWorkCountOrigin.WORK_VALIDATOR.value},
        )
        # One row per person whose credit moved, so a three-person job leaves a
        # timeline that names all three rather than a single line nobody can
        # attribute.
        for row in counted:
            await self._history(
                item,
                event=PrWorkEventType.COUNTED,
                actor=actor,
                contribution=row,
                metadata={
                    "user_id": str(row.user_id),
                    "counted_at": now.isoformat(),
                    "origin": PrWorkCountOrigin.WORK_VALIDATOR.value,
                },
            )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_APPROVED,
            entity_type="pr_work_item",
            entity_id=item.id,
            before={"status": PrWorkStatus.COMPLETED.value},
            after={
                "status": item.status.value,
                "code": item.code,
                "approved_at": now.isoformat(),
                "counted_contributions": [str(row.id) for row in counted],
                "counted_user_ids": [str(row.user_id) for row in counted],
            },
        )
        logger.info(
            "pr_work_counted",
            extra={
                "pr_work_item_id": str(item.id),
                "pr_work_counted": len(counted),
            },
        )
        if self._notifier is not None:
            await self._notifier.work_approved(item, tuple(counted), actor=actor)
        await self._project_eligibility(counted, now=now)
        return await self.detail(actor=actor, work_item_id=item.id)

    # =====================================================================
    # The source-derived boundary. M3.
    # =====================================================================
    #
    # Two methods, reachable from **no route and no Telegram tool**, and that is
    # the whole of their safety story. Both refuse anything whose ``source_type``
    # is ``MANUAL``, so nothing here widens what a person may do to work a person
    # filed; what they add is the ability for the *source* to say a thing
    # happened, and later to say it did not.
    #
    # Why they exist at all rather than the projector writing the columns: the
    # ladder, ``counted_at``, the history, the audit row and the M2 handoff are
    # one transaction with one order, and a projector reproducing that order in
    # its own SQL would be a second implementation of the rule the whole module
    # is built to have one of.

    @property
    def capabilities(self) -> PrCapabilityService:
        """The capability service this bundle was built with.

        Exposed so the content projector can gate its own explicit reconcile on
        ``PR_WORK_CONFIGURE`` without being handed a second instance - one
        resolver, one answer, and no chance of a projector authorising against a
        different picture of the actor's rights than the writes it then makes.
        """
        return self._capabilities

    async def create_source_work(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        source_key: str,
        work_type_id: uuid.UUID,
        title: str,
        contributor_user_id: uuid.UUID,
        content_id: uuid.UUID,
        occurred_at: datetime,
    ) -> PrWorkItem:
        """Write work a **source** produced. M3. Internal only.

        The third creation path, beside :meth:`propose_work` and
        :meth:`assign_work`, and the only one that may write ``source_type`` at
        all - :meth:`_create` hard-codes ``MANUAL`` precisely so that no request
        body anywhere can claim derived provenance.

        Lands at ``COMPLETED`` rather than ``PROPOSED`` or ``ACCEPTED``, and the
        rung is the point. M1's ``COMPLETED`` means *"a contributor says it is
        finished and nobody has confirmed it"*, which is exactly what a content
        milestone asserts: the deliverable exists, the source recorded it, and
        whether it **counts** is still a separate decision. Source-derived work
        therefore joins the same ladder manual work is on rather than a private
        one, and the counting boundary is the same boundary.

        The acceptance gate is skipped and that is not a hole: M1's rule is that
        *an employee cannot put work in their own workload*, and here the
        content workflow did - a script does not reach head review because its
        writer said so. The gate that still applies is the one that matters, and
        it applies in :meth:`count_source_work`.

        ``occurred_at`` back-dates the assignment and completion timestamps to
        the business event, so a work board shows the job on the day it was done
        rather than the day a worker got round to it.

        Idempotency is the database's: ``uq_pr_work_items_source`` is a partial
        unique index over ``(source_type, source_key)``, so two workers racing on
        one milestone produce one row and one loses.
        """
        assert_source_key(source_key)
        work_type = await self._require_work_type(work_type_id)
        if not work_type.is_active:
            raise PrValidationError(
                "Loại công việc này đã ngừng sử dụng.",
                details={"field": "work_type_id", "reason": "work_type_inactive"},
            )
        await self._require_active_users((contributor_user_id,))

        item = PrWorkItem(
            code=await self._codes.allocate_work_code(at=occurred_at),
            title=_require_text(title, "title", MAX_TITLE),
            work_type_id=work_type.id,
            source_type=PrWorkSourceType.CONTENT,
            source_key=source_key,
            status=PrWorkStatus.COMPLETED,
            priority=PrPriority.NORMAL,
            # One deliverable. Written rather than left null so a ``QUANTITY``
            # work type has something to measure - M2 would otherwise report the
            # row as ``UNMEASURABLE`` for a fact the source knows perfectly well.
            quantity=Decimal("1"),
            unit=work_type.default_unit,
            created_by_user_id=contributor_user_id,
            assigned_by_user_id=None,
            assigned_at=occurred_at,
            accepted_at=occurred_at,
            completed_at=occurred_at,
            completed_by_user_id=contributor_user_id,
            # **The canonical source milestone**, kept as its own fact. It is
            # the same instant ``completed_at`` gets here, and deliberately not
            # the same column: ``reopen`` clears that one, and the day the
            # writer delivered must survive a validator sending the work back.
            execution_at=occurred_at,
            content_id=content_id,
        )
        self._session.add(item)
        await self._session.flush()
        self._session.add(
            PrWorkContribution(
                work_item_id=item.id,
                user_id=contributor_user_id,
                contribution_role=PrWorkContributionRole.PRIMARY,
                credit_weight=DEFAULT_CREDIT_WEIGHT,
                assigned_at=occurred_at,
            )
        )
        await self._session.flush()

        await self._history(
            item,
            event=PrWorkEventType.CREATED,
            actor=actor,
            to_status=PrWorkStatus.COMPLETED,
            metadata={
                "source_key": source_key,
                "content_id": str(content_id),
                "contributor_user_id": str(contributor_user_id),
                "occurred_at": occurred_at.isoformat(),
            },
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_CREATED,
            entity_type="pr_work_item",
            entity_id=item.id,
            after={
                "code": item.code,
                "status": item.status.value,
                "work_type": work_type.code,
                "source_type": item.source_type.value,
                "source_key": source_key,
                "contributors": [str(contributor_user_id)],
            },
        )
        return item

    async def count_source_work(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        validated_by_user_id: uuid.UUID,
        effective_validation_at: datetime,
        note: str | None = None,
    ) -> PrWorkItem:
        """Count work the **source** independently validated. M3. Internal only.

        The trusted half of the content projector: everything
        :meth:`approve` does, with two facts supplied by the source instead of by
        the caller's session.

        ``validated_by_user_id`` is **the human who validated the milestone in
        the content workflow** - the head reviewer who approved the script, the
        internal reviewer who accepted the cut. Not the worker: attributing the
        approval to ``meobot-worker`` would put a machine's name on the row that
        decides somebody's KPI, and the whole point is that a *person* who did
        not do the work confirmed it.

        ``effective_validation_at`` is **when that person decided**, and it is
        why this method exists rather than a flag on :meth:`approve`. A
        contribution belongs to the reporting period containing its
        ``counted_at``; if a worker retry stamped its own clock, a script
        approved on 30 September and projected after a Monday-morning outage
        would land in October. The business instant is the source's, and only a
        caller that can prove it has one may supply it - which is why this
        parameter reaches no request body anywhere in the application.

        **The self-validation rule is unchanged and re-applied here**, against
        the source's validator rather than the actor: if the person who approved
        the content is the person the work credits, nothing is counted. See
        :meth:`approve` on why that holds whatever capability anybody holds.

        Raises:
            PrValidationError: Not source-derived, or not at ``COMPLETED``.
            PrPermissionDeniedError: The source validator is a contributor.
        """
        item = await self._lock(work_item_id)
        _require_source_derived(item, action="count_source_work")
        assert_work_transition(item.status, PrWorkStatus.APPROVED, source_derived=True)

        contributions = await self._contributions(item.id)
        if any(row.user_id == validated_by_user_id for row in contributions):
            raise PrPermissionDeniedError(
                "Người xác nhận ở quy trình nội dung cũng là người thực hiện công việc này, "
                "nên công việc chưa được ghi nhận. Cần một người khác xác nhận.",
                details={
                    "reason": "self_validation",
                    "work_item_id": str(item.id),
                    "validated_by_user_id": str(validated_by_user_id),
                },
            )

        item.status = PrWorkStatus.APPROVED
        item.approved_at = effective_validation_at
        item.approved_by_user_id = validated_by_user_id

        counted: list[PrWorkContribution] = []
        held: list[PrWorkContribution] = []
        for row in contributions:
            if row.count_status is PrWorkCountStatus.COUNTED:
                continue
            # **``EXCLUDED`` is revived here, and only here - and only when the
            # source excluded it.**
            #
            # A redo is the case: the source withdrew the milestone, this
            # contribution was excluded with a reason, and the source has now
            # re-asserted it. Leaving the row excluded would make the *second*
            # acceptance produce no credit at all - the work would sit approved
            # for ever with a contribution saying it does not count.
            #
            # The exclusion reversed is the one ``reverse_source_work`` wrote,
            # which stamps ``origin = SOURCE`` on its timeline row. An
            # exclusion a **person** made - or one with no attributable
            # event - is a decision the source cannot overrule, so the row is
            # left exactly as it is and named in the audit trail.
            if (
                row.count_status is PrWorkCountStatus.EXCLUDED
                and await self._exclusion_origin(row) is not PrWorkCountOrigin.SOURCE
            ):
                held.append(row)
                continue
            row.count_status = PrWorkCountStatus.COUNTED
            # **The source's instant, not the worker's.** See the docstring.
            row.counted_at = effective_validation_at
            row.excluded_at = None
            row.excluded_by_user_id = None
            row.excluded_reason = None
            counted.append(row)
        await self._session.flush()

        await self._history(
            item,
            event=PrWorkEventType.APPROVED,
            actor=actor,
            from_status=PrWorkStatus.COMPLETED,
            to_status=PrWorkStatus.APPROVED,
            note=_optional_text(note, "note", MAX_TEXT),
            metadata={
                "source_validated_by_user_id": str(validated_by_user_id),
                "effective_validation_at": effective_validation_at.isoformat(),
                "origin": PrWorkCountOrigin.SOURCE.value,
            },
        )
        for row in counted:
            await self._history(
                item,
                event=PrWorkEventType.COUNTED,
                actor=actor,
                contribution=row,
                metadata={
                    "user_id": str(row.user_id),
                    "counted_at": effective_validation_at.isoformat(),
                    "origin": PrWorkCountOrigin.SOURCE.value,
                },
            )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_SOURCE_COUNTED,
            entity_type="pr_work_item",
            entity_id=item.id,
            before={"status": PrWorkStatus.COMPLETED.value},
            after={
                "status": item.status.value,
                "code": item.code,
                "source_type": item.source_type.value,
                "source_key": item.source_key,
                "validated_by_user_id": str(validated_by_user_id),
                "effective_validation_at": effective_validation_at.isoformat(),
                "counted_contributions": [str(row.id) for row in counted],
                "counted_user_ids": [str(row.user_id) for row in counted],
                "held_excluded_contributions": [str(row.id) for row in held],
            },
        )
        logger.info(
            "pr_work_source_counted",
            extra={
                "pr_work_item_id": str(item.id),
                "pr_work_source_key": item.source_key,
                "pr_work_counted": len(counted),
            },
        )
        await self._project_eligibility(counted, now=effective_validation_at)
        return item

    async def retype_source_work(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        work_type_id: uuid.UUID,
        reason: str,
    ) -> PrWorkItem:
        """Re-file uncounted source-derived work under a different type. M3.1. Internal only.

        The correction path for the two ways a work type can turn out wrong
        without anybody doing anything wrong: somebody fixes the content's type
        (it was filed as a short script and is really an ultra-short one), or the
        owner changes the mapping before the work has been counted.

        **Only while nothing on the item is ``COUNTED``.** A counted contribution
        is a figure in a reporting period that somebody may already have been
        assessed on, and re-filing it under another heading would rewrite what a
        month claimed - which is the rule the period states exist to protect, and
        which no correction is worth breaking. The refusal is silent by design:
        the projector reports ``UNCHANGED`` and the ledger keeps the type it was
        filed as.

        Not reachable by a person. ``_require_source_derived`` refuses a manual
        item, and no request body anywhere carries a work type for source work.
        """
        item = await self._lock(work_item_id)
        _require_source_derived(item, action="retype_source_work")
        if item.work_type_id == work_type_id:
            return item
        contributions = await self._contributions(item.id)
        if any(row.count_status is PrWorkCountStatus.COUNTED for row in contributions):
            return item

        work_type = await self._require_work_type(work_type_id)
        before = item.work_type_id
        item.work_type_id = work_type.id
        # The unit travels with the type: it is what a quantity *means*, and
        # leaving the old one would make the number on this row say something
        # the new type never claimed.
        if item.quantity is not None:
            item.unit = work_type.default_unit
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_TYPE_UPDATED,
            entity_type="pr_work_item",
            entity_id=item.id,
            before={"work_type_id": str(before)},
            after={
                "work_type_id": str(work_type.id),
                "work_type_code": work_type.code,
                "code": item.code,
                "reason": reason,
            },
        )
        return item

    async def reverse_source_work(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        reason: str,
    ) -> PrWorkItem:
        """Take counted source-derived work back out of the count. M3. Internal only.

        The one place M1's *"``APPROVED`` is terminal"* is relaxed, and it is
        relaxed exactly where the reason M1 gave no longer applies. M1 said the
        answer belonged to *"the milestone that has a quota engine to stay
        consistent with"*; M2 shipped that engine, and M3 is the milestone whose
        source can withdraw the fact underneath the work - a head approval that
        is undone means the department does not accept that deliverable, and
        leaving it counted would keep a KPI figure for work nobody accepted.

        **Manual work is untouched.** ``assert_work_transition`` refuses the edge
        unless the caller declares the item source-derived, and
        :func:`_require_source_derived` is what earns that declaration.

        The contributions go to ``EXCLUDED`` rather than back to ``PENDING``, and
        the item to ``COMPLETED``. Two consequences worth stating:

        * ``EXCLUDED`` is M1's word for *"will never count as it stands"*, and it
          keeps the row rather than deleting it, so the timeline still says the
          work was done and then withdrawn. Nothing is erased;
        * ``COMPLETED`` is where a **redo** lands: if the source becomes valid
          again while the period is open, the same item takes the same edge
          forward again through :meth:`count_source_work`, on the *new*
          qualifying instant. No second work item, no second contribution.

        The caller is responsible for refusing this when the contribution's
        reporting period is ``CLOSED`` or ``LOCKED`` - see
        :class:`~meobot.application.pr_content_work_projector.PrContentWorkProjector`,
        which checks the period before it gets here. This method deliberately
        does **not** know about periods: it would be a second place that decides
        what a closed month permits.
        """
        item = await self._lock(work_item_id)
        _require_source_derived(item, action="reverse_source_work")
        assert_work_transition(item.status, PrWorkStatus.COMPLETED, source_derived=True)

        now = utcnow()
        before = item.status
        item.status = PrWorkStatus.COMPLETED
        item.approved_at = None
        item.approved_by_user_id = None

        withdrawn: list[PrWorkContribution] = []
        for row in await self._contributions(item.id):
            if row.count_status is not PrWorkCountStatus.COUNTED:
                continue
            row.count_status = PrWorkCountStatus.EXCLUDED
            # ``counted_at`` is cleared with the status, because the CHECK
            # constraint ``counted_at_matches_status`` refuses the pair coming
            # apart - which is the database saying the same thing this method
            # does: work that is not counted has no counting instant.
            row.counted_at = None
            row.excluded_at = now
            row.excluded_by_user_id = actor.user_id
            row.excluded_reason = reason
            withdrawn.append(row)
        await self._session.flush()

        await self._history(
            item,
            event=PrWorkEventType.REOPENED,
            actor=actor,
            from_status=before,
            to_status=PrWorkStatus.COMPLETED,
            note=reason,
        )
        for row in withdrawn:
            await self._history(
                item,
                event=PrWorkEventType.EXCLUDED,
                actor=actor,
                contribution=row,
                note=reason,
                # The source's own exclusion, and therefore the only kind a
                # source redo may revive - see ``count_source_work``.
                metadata={"user_id": str(row.user_id), "origin": PrWorkCountOrigin.SOURCE.value},
            )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_SOURCE_REVERSED,
            entity_type="pr_work_item",
            entity_id=item.id,
            before={"status": before.value},
            after={
                "status": item.status.value,
                "code": item.code,
                "source_type": item.source_type.value,
                "source_key": item.source_key,
                "reason": reason,
                "excluded_contributions": [str(row.id) for row in withdrawn],
                "excluded_user_ids": [str(row.user_id) for row in withdrawn],
            },
        )
        logger.info(
            "pr_work_source_reversed",
            extra={
                "pr_work_item_id": str(item.id),
                "pr_work_source_key": item.source_key,
                "pr_work_excluded": len(withdrawn),
            },
        )
        # The contributions are no longer candidates, so M2 has to be told: an
        # allocation for work that is not counted is a KPI figure for work the
        # department withdrew. The same savepoint rule applies - see
        # ``_project_eligibility``.
        await self._reconcile_eligibility(withdrawn, now=now)
        return item

    async def _reconcile_eligibility(
        self, withdrawn: Sequence[PrWorkContribution], *, now: datetime
    ) -> None:
        """Recompute M2 for the periods work has just left. M3.

        The mirror of :meth:`_project_eligibility` and, deliberately, the same
        shape: behind a savepoint, never able to refuse the reversal, logged
        rather than raised. A reversal that committed while its eligibility did
        not is a state the next reconcile fixes; a reversal that rolled back
        because a projection failed would leave the ledger asserting work the
        source has withdrawn, which is the worse of the two.

        The evaluator is a projection over the *remaining* counted
        contributions, so removing one is not a subtraction it has to be told
        about - it recomputes the whole ``(user, period)`` and the released
        capacity flows to whatever comes next in the deterministic order.
        """
        if self._eligibility is None or not withdrawn:
            return
        try:
            async with self._session.begin_nested():
                await self._eligibility.on_contributions_uncounted(withdrawn, now=now)
        except Exception:  # deliberately broad; see the docstring
            logger.exception(
                "pr_work_quota_reconcile_failed",
                extra={"pr_work_contributions": [str(row.id) for row in withdrawn]},
            )

    async def _project_eligibility(
        self, counted: Sequence[PrWorkContribution], *, now: datetime
    ) -> None:
        """Ask M2 to evaluate the work that has just become ``COUNTED``.

        **Chosen consistency: the same transaction, behind a ``SAVEPOINT``.**

        The two requirements pull in opposite directions - the projection must
        be reliable, and it must never be able to corrupt or refuse an approval
        - and MeoChat has no outbox worker for this kind of derived read model
        to lean on. A savepoint gives both:

        * *same transaction*, so an approval and the eligibility it implies
          commit together and a screen refreshed a second later is already
          right. There is no window in which work is counted and the KPI screen
          says nothing about it;
        * *rolled back alone*, so a projection that fails - a period nobody
          created, a lock timeout, a bug - takes only itself with it. The
          approval, the ``counted_at`` values, the history rows and the audit
          row are all still there when this returns, and the database is not
          left in the poisoned state that catching an error inside a plain
          transaction produces;
        * *convergent*, because the evaluator is a projection rather than an
          increment. The next reconcile of that open period produces exactly the
          allocations this attempt would have.

        **Work validation never depends on a quota existing.** A contribution
        whose quota status will be ``NO_QUOTA`` still becomes ``COUNTED``, and
        so does one counted into a month nobody has opened a reporting period
        for - see
        :meth:`~meobot.application.pr_work_quota_service.PrWorkQuotaEligibilityService.on_contributions_counted`,
        which swallows those two cases deliberately and logs them.

        A ``PrWorkService`` built without an evaluator - M1's wiring, and every
        test that only cares about the ledger - does nothing here.
        """
        if self._eligibility is None or not counted:
            return
        try:
            async with self._session.begin_nested():
                await self._eligibility.on_contributions_counted(counted, now=now)
        except Exception:  # deliberately broad; see the docstring
            # Logged rather than raised: the approval is correct and committed
            # work must not be undone because a derived figure could not be
            # computed. ``exc_info`` so the cause is in the log rather than
            # only the fact.
            logger.exception(
                "pr_work_quota_projection_failed",
                extra={"pr_work_contributions": [str(row.id) for row in counted]},
            )

    async def cancel(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        reason: str | None = None,
    ) -> PrWorkItem:
        """Abandon work. ``PR_WORK_MANAGE``.

        **Refused once the work is approved**, and that is an M1 product
        decision rather than a gap. Approved work has written ``counted_at``
        onto its contributions, and taking that back is a correction against a
        reporting period that may since have been closed or locked. Correction
        semantics belong to the milestone that has a quota engine to stay
        consistent with; until then validation is final and a mistake is
        recorded as new work rather than by rewriting history.

        The row is never deleted. Its contributions become ``EXCLUDED`` with a
        reason, so nothing is left ``PENDING`` for ever waiting on a job that
        will not happen.
        """
        item = await self._lock(work_item_id)
        # **A period container is never cancelled.** It is the system's
        # accounting stream for one person, one type and one month: a
        # cancelled one would keep its unique slot, so a later report would
        # find it and file results under an ``EXCLUDED`` contribution - an
        # actual the card shows and the KPI screen does not. An empty stream
        # is removed by an administrator (*cleanup-empty-containers*); a
        # filled one is corrected result by result.
        _refuse_container(item, action="cancel")
        await self._require_item_manager(actor, item)
        self._refuse_source_mutation(item, field="status")
        if item.status is PrWorkStatus.APPROVED:
            raise PrValidationError(
                "Công việc đã được xác nhận nên không hủy được. "
                "Hãy ghi nhận điều chỉnh bằng một công việc mới.",
                details={"reason": "approved_is_final", "work_item_id": str(item.id)},
            )
        assert_work_transition(item.status, PrWorkStatus.CANCELLED)
        before = item.status
        now = utcnow()
        item.status = PrWorkStatus.CANCELLED
        item.cancelled_at = now
        item.cancelled_by_user_id = _require_user_id(actor)
        item.cancel_reason = _optional_text(reason, "reason", MAX_TEXT)
        await self._exclude_contributions(item, actor=actor, reason="Công việc đã hủy")
        await self._session.flush()

        await self._history(
            item,
            event=PrWorkEventType.CANCELLED,
            actor=actor,
            from_status=before,
            to_status=item.status,
            note=item.cancel_reason,
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_CANCELLED,
            entity_type="pr_work_item",
            entity_id=item.id,
            before={"status": before.value},
            after={"status": item.status.value, "reason": item.cancel_reason},
        )
        return item

    # =====================================================================
    # Contributors
    # =====================================================================
    async def add_contributor(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        user_id: uuid.UUID,
        contribution_role: PrWorkContributionRole = PrWorkContributionRole.CONTRIBUTOR,
        credit_weight: Decimal | None = None,
    ) -> PrWorkContribution:
        """Put somebody on a job. ``PR_WORK_MANAGE``.

        ``credit_weight`` is a **manager's** field and never the contributor's:
        an employee cannot raise what their own share is worth, and the column
        constraint refuses anything above a whole unit whoever writes it.

        Refused once the work is approved. Adding a contributor to counted work
        would either create an uncounted contribution on an approved item - an
        inconsistency the count status cannot express - or silently backdate
        somebody's credit into a period that is already reported.
        """
        item = await self._lock(work_item_id)
        _refuse_container(item, action="add_contributor")
        await self._require_item_manager(actor, item)
        self._refuse_source_mutation(item, field="contributor_user_ids")
        if item.status in {PrWorkStatus.APPROVED, PrWorkStatus.REJECTED, PrWorkStatus.CANCELLED}:
            raise PrValidationError(
                "Công việc đã kết thúc nên không thêm người thực hiện được.",
                details={"reason": "work_is_final", "status": item.status.value},
            )
        await self._require_active_users((user_id,))
        if await self._contribution_of(item.id, user_id, contribution_role) is not None:
            raise PrConflictError(
                "Người này đã có trong công việc với vai trò đó.",
                details={"reason": "duplicate_contributor", "user_id": str(user_id)},
            )
        if len(await self._contributions(item.id)) >= MAX_CONTRIBUTORS:
            raise PrValidationError(
                f"Một công việc không nhận quá {MAX_CONTRIBUTORS} người thực hiện.",
                details={"field": "user_id", "reason": "too_many_contributors"},
            )

        row = PrWorkContribution(
            work_item_id=item.id,
            user_id=user_id,
            contribution_role=contribution_role,
            credit_weight=_require_weight(credit_weight),
            assigned_at=utcnow(),
        )
        self._session.add(row)
        await self._session.flush()
        await self._history(
            item,
            event=PrWorkEventType.CONTRIBUTOR_ADDED,
            actor=actor,
            contribution=row,
            metadata={"user_id": str(user_id), "role": contribution_role.value},
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_CONTRIBUTOR_ADDED,
            entity_type="pr_work_item",
            entity_id=item.id,
            after={
                "user_id": str(user_id),
                "role": contribution_role.value,
                "credit_weight": str(row.credit_weight),
            },
        )
        if self._notifier is not None:
            await self._notifier.work_assigned(item, (user_id,), actor=actor)
        return row

    async def remove_contributor(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        contribution_id: uuid.UUID,
    ) -> None:
        """Take somebody off a job. ``PR_WORK_MANAGE``.

        A **hard delete**, and the one in this module - because a contribution
        that was never counted is a mistake in the assignment rather than a
        thing that happened, and leaving an ``EXCLUDED`` row would put somebody
        on a job they never did. What preserves the record is
        ``pr_work_history``, which keeps the ``CONTRIBUTOR_REMOVED`` row naming
        who was taken off and by whom.

        A **counted** contribution is refused outright: that one is a thing that
        happened, and removing it would delete a fact a reporting period may
        already have counted.
        """
        item = await self._lock(work_item_id)
        _refuse_container(item, action="remove_contributor")
        await self._require_item_manager(actor, item)
        self._refuse_source_mutation(item, field="contributor_user_ids")
        row = await self._session.get(PrWorkContribution, contribution_id)
        if row is None or row.work_item_id != item.id:
            raise PrNotFoundError(
                "Không tìm thấy người thực hiện trong công việc này.",
                details={"contribution_id": str(contribution_id)},
            )
        if row.count_status is PrWorkCountStatus.COUNTED:
            raise PrValidationError(
                "Phần việc này đã được ghi nhận nên không gỡ được.",
                details={"reason": "contribution_counted", "contribution_id": str(row.id)},
            )
        remaining = [one for one in await self._contributions(item.id) if one.id != row.id]
        if not remaining:
            raise PrValidationError(
                "Công việc phải còn ít nhất một người thực hiện.",
                details={"reason": "last_contributor"},
            )

        user_id = row.user_id
        role = row.contribution_role
        await self._history(
            item,
            event=PrWorkEventType.CONTRIBUTOR_REMOVED,
            actor=actor,
            metadata={"user_id": str(user_id), "role": role.value},
        )
        await self._session.delete(row)
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_CONTRIBUTOR_REMOVED,
            entity_type="pr_work_item",
            entity_id=item.id,
            before={"user_id": str(user_id), "role": role.value},
        )

    # =====================================================================
    # Metadata a manager may change
    # =====================================================================
    async def change_deadline(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        due_at: datetime | None,
        reason: str | None = None,
    ) -> PrWorkItem:
        """Move a deadline. ``PR_WORK_MANAGE``.

        Recorded with **both** dates in the history, because "why is this not
        overdue any more" is the question a moved deadline creates and the old
        value is the only thing that answers it.
        """
        item = await self._lock(work_item_id)
        await self._require_item_manager(actor, item)
        self._refuse_source_mutation(item, field="due_at")
        self._refuse_final(item, "đổi hạn")
        before = item.due_at
        item.due_at = due_at
        await self._session.flush()
        await self._history(
            item,
            event=PrWorkEventType.DEADLINE_CHANGED,
            actor=actor,
            note=_optional_text(reason, "reason", MAX_TEXT),
            metadata={
                "from": before.isoformat() if before else None,
                "to": due_at.isoformat() if due_at else None,
            },
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_DEADLINE_CHANGED,
            entity_type="pr_work_item",
            entity_id=item.id,
            before={"due_at": before.isoformat() if before else None},
            after={"due_at": due_at.isoformat() if due_at else None},
        )
        return item

    async def change_priority(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        priority: PrPriority,
    ) -> PrWorkItem:
        """Re-prioritise. ``PR_WORK_MANAGE``."""
        item = await self._lock(work_item_id)
        await self._require_item_manager(actor, item)
        self._refuse_source_mutation(item, field="priority")
        self._refuse_final(item, "đổi mức ưu tiên")
        before = item.priority
        item.priority = priority
        await self._session.flush()
        await self._history(
            item,
            event=PrWorkEventType.PRIORITY_CHANGED,
            actor=actor,
            metadata={"from": before.value, "to": priority.value},
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_PRIORITY_CHANGED,
            entity_type="pr_work_item",
            entity_id=item.id,
            before={"priority": before.value},
            after={"priority": priority.value},
        )
        return item

    # =====================================================================
    # Evidence
    # =====================================================================
    async def add_evidence(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        label: str,
        location: str,
        note: str | None = None,
    ) -> PrWorkEvidence:
        """Attach a link proving the work. A contributor's own act.

        ``location`` is a URL or a path - MeoBot stores no files, because the
        department already keeps its output in Drive and on the NAS and a second
        copy would be a migration nobody asked for.

        The **legacy shape**, kept for clients that still send a label and a
        link. New clients send one text - see :meth:`add_evidence_text`.
        """
        return await self._attach_evidence(
            actor=actor,
            request_id=request_id,
            work_item_id=work_item_id,
            label=_require_text(label, "label", MAX_LABEL),
            location=_require_text(location, "location", MAX_LOCATION),
            note=_optional_text(note, "note", MAX_TEXT),
        )

    async def add_evidence_text(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        text: str,
    ) -> PrWorkEvidence:
        """Attach evidence as **one free text**. A contributor's own act.

        A description, a link, a Drive folder, several lines, any mix - the
        member is no longer asked to split it into a label and a link. The text
        is stored whole in ``note``; ``label`` and ``location`` are *derived*
        (first line; first link, else the text-only sentinel) so the table, the
        history rows and every legacy reader keep working unchanged. No URL is
        required, and nothing here validates the text as one.

        Same authorization and same "counts as evidence" rule as the legacy
        shape: a text row is a ``pr_work_evidence`` row, and a work type that
        requires evidence is satisfied by it exactly as by a link.
        """
        body = _require_text(text, "text", MAX_TEXT)
        return await self._attach_evidence(
            actor=actor,
            request_id=request_id,
            work_item_id=work_item_id,
            label=evidence_label_for(body),
            location=evidence_location_for(body),
            note=body,
        )

    async def _attach_evidence(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        label: str,
        location: str,
        note: str | None,
    ) -> PrWorkEvidence:
        """The one insertion path behind both evidence shapes."""
        item = await self._lock(work_item_id)
        await self._require_contributor_or_manager(actor, item)
        row = PrWorkEvidence(
            work_item_id=item.id,
            label=label,
            location=location,
            note=note,
            added_by_user_id=_require_user_id(actor),
        )
        self._session.add(row)
        await self._session.flush()
        await self._history(
            item,
            event=PrWorkEventType.EVIDENCE_ADDED,
            actor=actor,
            metadata={"label": row.label},
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_EVIDENCE_ADDED,
            entity_type="pr_work_item",
            entity_id=item.id,
            after={"evidence_id": str(row.id), "label": row.label},
        )
        return row

    async def remove_evidence(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        evidence_id: uuid.UUID,
    ) -> None:
        """Detach evidence. Refused once the work has been validated.

        Approved work was validated **on the strength of** its evidence, and
        removing it afterwards would leave a counted contribution nobody can
        check.
        """
        item = await self._lock(work_item_id)
        await self._require_contributor_or_manager(actor, item)
        if item.status is PrWorkStatus.APPROVED:
            raise PrValidationError(
                "Công việc đã được xác nhận nên không gỡ minh chứng được.",
                details={"reason": "approved_is_final"},
            )
        row = await self._session.get(PrWorkEvidence, evidence_id)
        if row is None or row.work_item_id != item.id:
            raise PrNotFoundError(
                "Không tìm thấy minh chứng.", details={"evidence_id": str(evidence_id)}
            )
        label = row.label
        await self._history(
            item, event=PrWorkEventType.EVIDENCE_REMOVED, actor=actor, metadata={"label": label}
        )
        await self._session.delete(row)
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_EVIDENCE_REMOVED,
            entity_type="pr_work_item",
            entity_id=item.id,
            before={"evidence_id": str(evidence_id), "label": label},
        )

    # =====================================================================
    # Reading one item
    # =====================================================================
    async def detail(self, *, actor: Actor, work_item_id: uuid.UUID) -> WorkItemDetail:
        """One work item, with the flags a client draws its controls from.

        Visibility is **relationship-based**, and deliberately not team-based:
        MeoBot models no team, department or manager relationship, and inferring
        one from channel assignments would be a hierarchy invented out of
        something that is not one. Four ways in, and no fifth:

        * a **contributor** - it is their work;
        * whoever **created** it, or **assigned** it - their book;
        * somebody who may **decide about it right now** - a validator has to be
          able to open the item they are being asked to validate, and that is
          exactly the item in their queue;
        * ``PR_WORK_VIEW_ALL`` - Head and Admin.

        ``PR_WORK_MANAGE`` on its own is **not** one of them, and that is the
        fix this patch makes: managing work means deciding about what you put
        somebody on or what is waiting for you, not reading every colleague's
        record. The list scopes enforce the same rule, so the detail route
        cannot be used to walk past them one id at a time.
        """
        item = await self._require_item(work_item_id)
        contributions = await self._contributions(item.id)
        actor_id = actor.user_id
        is_contributor = actor_id is not None and any(
            row.user_id == actor_id for row in contributions
        )
        may_view_all = await self._capabilities.allows(actor, PrCapability.PR_WORK_VIEW_ALL)
        if not await self._may_see(
            actor, item, is_contributor=is_contributor, may_view_all=may_view_all
        ):
            raise PrPermissionDeniedError(
                "Bạn không có quyền xem công việc này.",
                details={"reason": "not_involved", "work_item_id": str(item.id)},
            )

        # "May I manage *this item*" - which drives the controls, and is now a
        # narrower question than "do I hold PR_WORK_MANAGE": a manager who has
        # nothing to do with a job does not get its buttons.
        may_manage = await self._capabilities.allows(actor, PrCapability.PR_WORK_MANAGE)
        can_manage = may_manage and (may_view_all or self._is_owner_of(item, actor_id))
        may_validate = await self._capabilities.allows(actor, PrCapability.PR_WORK_VALIDATE)
        may_execute = await self._capabilities.allows(actor, PrCapability.PR_WORK_EXECUTE)
        return WorkItemDetail(
            item=item,
            work_type=await self._require_work_type(item.work_type_id),
            contributions=tuple(contributions),
            evidence=tuple(await self._evidence(item.id)),
            content_code=await self._content_code(item),
            can_manage=can_manage,
            # The self-validation rule, rendered as a flag rather than
            # duplicated in the browser: a contributor never sees a validate
            # button, and if they call the route anyway ``approve`` refuses them
            # with the same rule.
            can_validate=not is_contributor and may_validate,
            is_subject=item.subject_user_id is not None and item.subject_user_id == actor_id,
            can_execute=is_contributor or can_manage,
            contributor_names=await self._names([row.user_id for row in contributions]),
            recurring_template=await self._recurring_template(item),
            # The per-action contract, from the same guards the writes apply.
            # ``can_manage`` / ``can_validate`` / ``can_execute`` above stay as
            # the coarse per-actor facts other panels read; no client decides a
            # lifecycle button from them any more.
            actions=resolve_work_actions(
                item,
                actor_id=actor_id,
                is_contributor=is_contributor,
                may_manage=may_manage,
                is_item_manager=can_manage,
                may_validate=may_validate,
                may_execute=may_execute,
            ),
        )

    async def _may_see(
        self,
        actor: Actor,
        item: PrWorkItem,
        *,
        is_contributor: bool,
        may_view_all: bool,
    ) -> bool:
        """The one visibility rule, used by every read and every write path.

        One method rather than a check per route, because a visibility rule
        expressed in six places is a visibility rule with a hole in it.
        """
        if may_view_all or is_contributor:
            return True
        actor_id = actor.user_id
        if actor_id is None:
            return False
        if self._is_owner_of(item, actor_id):
            return True
        # In their decision queue: a proposal they may accept, or finished work
        # they may validate - minus, in both cases, anything they were part of,
        # which the caller has already ruled out above.
        if item.status is PrWorkStatus.PROPOSED and item.created_by_user_id != actor_id:
            return await self._capabilities.allows(actor, PrCapability.PR_WORK_MANAGE)
        if item.status is PrWorkStatus.COMPLETED:
            return await self._capabilities.allows(actor, PrCapability.PR_WORK_VALIDATE)
        # A period container is in every validator's queue while it holds a
        # result nobody has counted, for the same reason finished work is.
        if item.is_period_container and await self._has_pending_results(item.id):
            return await self._capabilities.allows(actor, PrCapability.PR_WORK_VALIDATE)
        return False

    async def _has_pending_results(self, work_item_id: uuid.UUID) -> bool:
        from meobot.db.models.pr_work_result import PrWorkResult

        return bool(
            (
                await self._session.execute(
                    select(
                        exists().where(
                            PrWorkResult.work_item_id == work_item_id,
                            PrWorkResult.status == PrWorkCountStatus.PENDING,
                        )
                    )
                )
            ).scalar()
        )

    async def _recurring_template(self, item: PrWorkItem) -> tuple[uuid.UUID, str] | None:
        """The template a generated job came from, as ``(id, name)``. Post-M4.

        Reached through ``recurring_occurrence_id`` - a foreign key - rather
        than by decoding ``source_key``, which this module compares for equality
        and never parses.
        """
        if item.recurring_occurrence_id is None:
            return None
        from meobot.db.models.pr_work_recurring import (
            PrWorkRecurringOccurrence,
            PrWorkRecurringTemplate,
        )

        row = (
            await self._session.execute(
                select(PrWorkRecurringTemplate.id, PrWorkRecurringTemplate.name)
                .join(
                    PrWorkRecurringOccurrence,
                    PrWorkRecurringOccurrence.template_id == PrWorkRecurringTemplate.id,
                )
                .where(PrWorkRecurringOccurrence.id == item.recurring_occurrence_id)
            )
        ).one_or_none()
        return (row[0], row[1]) if row is not None else None

    async def _content_code(self, item: PrWorkItem) -> str | None:
        """The code of the content this work came from, if it came from any. M3.

        A read of one column on one row, and only for source-derived work - so a
        board full of manual items pays nothing for it.
        """
        if item.content_id is None:
            return None
        from meobot.db.models.pr import PrContentItem

        code = await self._session.scalar(
            select(PrContentItem.code).where(PrContentItem.id == item.content_id)
        )
        return str(code) if code is not None else None

    @staticmethod
    def _refuse_source_mutation(item: PrWorkItem, *, field: str) -> None:
        """Refuse a hand-edit of a fact the content workflow owns. M3.

        **Source-derived work is not a manual work item with a different
        badge.** Its title, its work type, its quantity, its contributor and its
        lifecycle are all restatements of what the content workflow recorded,
        and the projector rewrites them from the source on every run - so an edit
        here would either be silently reverted, which is confusing, or would have
        to be preserved, which would make the ledger disagree with the workflow
        it is supposed to be a view of.

        The rule is the same for an employee and for a manager, and that is
        deliberate: this is not a permission level, it is *whose fact it is*.
        The way to change source-derived work is to change the content, and the
        projector follows.

        **One manual action stays available**, and it is the important one:
        independent validation of a piece the source could not validate itself.
        That is not a source fact - the source has no opinion about it - and it
        is the whole reason a self-approved script does not count until somebody
        else looks. It goes through :meth:`approve`, which is untouched.
        """
        if item.source_type is PrWorkSourceType.MANUAL:
            return
        raise PrValidationError(
            "Công việc này được ghi nhận tự động từ quy trình nội dung, "
            "nên không sửa trực tiếp được. Hãy điều chỉnh ở nội dung gốc.",
            details={
                "field": field,
                "reason": "source_derived_is_read_only",
                "source_type": item.source_type.value,
                "work_item_id": str(item.id),
                "content_id": str(item.content_id) if item.content_id else None,
            },
        )

    async def _require_item_manager(self, actor: Actor, item: PrWorkItem) -> None:
        """``PR_WORK_MANAGE`` **over this particular job**.

        The capability alone is not enough: the actor must also have put the
        work there - created or assigned it - or hold ``PR_WORK_VIEW_ALL``.
        Otherwise a Trưởng nhóm with no connection to a job could move its
        deadline or cancel it while being unable to read it, which is a write
        path wider than the read path.

        Deliberately **not** applied to ``accept``, ``reject``, ``approve`` and
        ``reopen``: those are queue decisions, and the whole point of the
        acceptance and validation boundaries is that they are taken by somebody
        *other* than the person who owns the work.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        if self._is_owner_of(item, actor.user_id):
            return
        if await self._capabilities.allows(actor, PrCapability.PR_WORK_VIEW_ALL):
            return
        raise PrPermissionDeniedError(
            "Bạn không quản lý công việc này.",
            details={"reason": "not_item_manager", "work_item_id": str(item.id)},
        )

    @staticmethod
    def _is_owner_of(item: PrWorkItem, actor_id: uuid.UUID | None) -> bool:
        """Whether this actor put the work there - created it or assigned it."""
        return actor_id is not None and (
            item.created_by_user_id == actor_id or item.assigned_by_user_id == actor_id
        )

    async def history(
        self, *, actor: Actor, work_item_id: uuid.UUID, limit: int = 100
    ) -> tuple[Sequence[PrWorkHistory], dict[uuid.UUID, str]]:
        """The item's timeline, oldest first, with actor names resolved.

        Same visibility as :meth:`detail`, enforced by calling it - so there is
        one visibility rule rather than two that could drift.
        """
        await self.detail(actor=actor, work_item_id=work_item_id)
        result = await self._session.execute(
            select(PrWorkHistory)
            .where(PrWorkHistory.work_item_id == work_item_id)
            .order_by(PrWorkHistory.created_at.asc())
            .limit(max(1, min(limit, 500)))
        )
        rows = list(result.scalars().all())
        return rows, await self._names([row.actor_user_id for row in rows if row.actor_user_id])

    async def _names(self, user_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        """User ids to display names, in one query. Never a per-row lookup."""
        unique = list(dict.fromkeys(user_ids))
        if not unique:
            return {}
        result = await self._session.execute(
            select(User.id, User.full_name).where(User.id.in_(unique))
        )
        return {row[0]: row[1] for row in result.all()}

    # =====================================================================
    # Internals
    # =====================================================================
    async def _lock(self, work_item_id: uuid.UUID) -> PrWorkItem:
        """Load one item holding it against concurrent writers.

        ``SELECT ... FOR UPDATE`` on PostgreSQL through the shared helper. The
        lock is what makes two simultaneous approvals safe: the second waits,
        then re-reads a status the transition matrix refuses.
        """
        item = await lock_row(self._session, PrWorkItem, work_item_id)
        if item is None:
            raise PrNotFoundError(
                "Không tìm thấy công việc.", details={"work_item_id": str(work_item_id)}
            )
        return item

    async def _require_item(self, work_item_id: uuid.UUID) -> PrWorkItem:
        item = await self._session.get(PrWorkItem, work_item_id)
        if item is None:
            raise PrNotFoundError(
                "Không tìm thấy công việc.", details={"work_item_id": str(work_item_id)}
            )
        return item

    async def _require_work_type(self, work_type_id: uuid.UUID) -> PrWorkType:
        row = await self._session.get(PrWorkType, work_type_id)
        if row is None:
            raise PrNotFoundError(
                "Không tìm thấy loại công việc.", details={"work_type_id": str(work_type_id)}
            )
        return row

    async def _work_type_by_code(self, code: str) -> PrWorkType | None:
        result = await self._session.execute(
            select(PrWorkType).where(PrWorkType.code == code).limit(1)
        )
        return result.scalars().one_or_none()

    async def _contributions(self, work_item_id: uuid.UUID) -> list[PrWorkContribution]:
        result = await self._session.execute(
            select(PrWorkContribution)
            .where(PrWorkContribution.work_item_id == work_item_id)
            .order_by(PrWorkContribution.assigned_at.asc(), PrWorkContribution.id.asc())
        )
        return list(result.scalars().all())

    async def _contribution_of(
        self, work_item_id: uuid.UUID, user_id: uuid.UUID, role: PrWorkContributionRole
    ) -> PrWorkContribution | None:
        result = await self._session.execute(
            select(PrWorkContribution).where(
                PrWorkContribution.work_item_id == work_item_id,
                PrWorkContribution.user_id == user_id,
                PrWorkContribution.contribution_role == role,
            )
        )
        return result.scalars().one_or_none()

    async def _evidence(self, work_item_id: uuid.UUID) -> Sequence[PrWorkEvidence]:
        result = await self._session.execute(
            select(PrWorkEvidence)
            .where(PrWorkEvidence.work_item_id == work_item_id)
            .order_by(PrWorkEvidence.created_at.asc())
        )
        return result.scalars().all()

    async def _has_results(self, work_item_id: uuid.UUID) -> bool:
        from meobot.db.models.pr_work_result import PrWorkResult

        return bool(
            (
                await self._session.execute(
                    select(exists().where(PrWorkResult.work_item_id == work_item_id))
                )
            ).scalar()
        )

    async def _has_evidence(self, work_item_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(PrWorkEvidence.id).where(PrWorkEvidence.work_item_id == work_item_id).limit(1)
        )
        return result.scalars().first() is not None

    async def _require_active_users(self, user_ids: Sequence[uuid.UUID]) -> None:
        """Every named person exists and may still use MeoBot.

        Checked here rather than left to the foreign key, because the FK would
        accept a suspended account and putting work on somebody who cannot log
        in is a silent way to lose it.
        """
        if not user_ids:
            return
        result = await self._session.execute(
            select(User).where(User.id.in_(list(dict.fromkeys(user_ids))))
        )
        found = {row.id: row for row in result.scalars().all()}
        for user_id in user_ids:
            row = found.get(user_id)
            if row is None:
                raise PrNotFoundError(
                    "Không tìm thấy người dùng.", details={"user_id": str(user_id)}
                )
            if not row.may_use_meobot:
                raise PrValidationError(
                    f"{row.full_name} hiện không hoạt động nên không nhận việc được.",
                    details={"field": "user_id", "reason": "user_inactive"},
                )

    async def _require_contributor_or_manager(self, actor: Actor, item: PrWorkItem) -> None:
        """Execution acts belong to whoever is doing the work, or to its manager.

        "Its manager" is narrower than "a manager": the person who assigned or
        created the job, or a ``PR_WORK_VIEW_ALL`` holder. A Trưởng nhóm with no
        connection to a job cannot mark it finished, for the same reason they
        cannot read it - and letting the write path be wider than the read path
        would mean somebody could complete work they were never shown.

        The manager branch exists so that somebody's holiday does not strand a
        job, not because managing implies doing it. It changes nothing about
        counting: :meth:`approve` still refuses anybody with a contribution,
        manager or not.
        """
        actor_id = actor.user_id
        is_contributor = False
        if actor_id is not None:
            existing = await self._session.execute(
                select(PrWorkContribution.id).where(
                    PrWorkContribution.work_item_id == item.id,
                    PrWorkContribution.user_id == actor_id,
                )
            )
            is_contributor = existing.scalars().first() is not None
        if is_contributor:
            await self._capabilities.require(actor, PrCapability.PR_WORK_EXECUTE)
            return
        if await self._capabilities.allows(actor, PrCapability.PR_WORK_MANAGE) and (
            self._is_owner_of(item, actor_id)
            or await self._capabilities.allows(actor, PrCapability.PR_WORK_VIEW_ALL)
        ):
            return
        raise PrPermissionDeniedError(
            "Bạn không tham gia công việc này nên không cập nhật được.",
            details={"reason": "not_a_contributor", "work_item_id": str(item.id)},
        )

    async def _exclude_contributions(self, item: PrWorkItem, *, actor: Actor, reason: str) -> None:
        """Close out pending credit when a job will not happen.

        ``EXCLUDED`` rather than left ``PENDING``: a contribution waiting for a
        validation that will never come is a row every future report has to
        remember to filter out. Counted contributions are untouched - by the
        time this runs the item is not approved, so there are none, and the loop
        says so explicitly rather than relying on that.
        """
        now = utcnow()
        for row in await self._contributions(item.id):
            if row.count_status is not PrWorkCountStatus.PENDING:
                continue
            row.count_status = PrWorkCountStatus.EXCLUDED
            row.excluded_at = now
            row.excluded_by_user_id = actor.user_id
            row.excluded_reason = reason
            await self._history(
                item,
                event=PrWorkEventType.EXCLUDED,
                actor=actor,
                contribution=row,
                note=reason,
            )

    async def count_origin(self, item: PrWorkItem) -> PrWorkCountOrigin | None:
        """Who made the **current** count on a legacy one-off item.

        The item-grain twin of ``PrWorkResultService.count_origin``, read off
        the newest ``APPROVED`` event: :meth:`count_source_work` stamps
        ``origin = SOURCE`` beside ``source_validated_by_user_id``,
        :meth:`approve` stamps ``origin = WORK_VALIDATOR``. ``None`` when the
        item is not approved or no approval is on record.
        """
        if item.status is not PrWorkStatus.APPROVED:
            return None
        event = (
            (
                await self._session.execute(
                    select(PrWorkHistory)
                    .where(
                        PrWorkHistory.work_item_id == item.id,
                        PrWorkHistory.event_type == PrWorkEventType.APPROVED,
                    )
                    .order_by(PrWorkHistory.created_at.desc(), PrWorkHistory.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .one_or_none()
        )
        if event is None:
            return None
        metadata = event.event_metadata or {}
        if metadata.get("origin") == PrWorkCountOrigin.WORK_VALIDATOR.value:
            return PrWorkCountOrigin.WORK_VALIDATOR
        if (
            metadata.get("origin") == PrWorkCountOrigin.SOURCE.value
            or "source_validated_by_user_id" in metadata
        ):
            return PrWorkCountOrigin.SOURCE
        return PrWorkCountOrigin.WORK_VALIDATOR

    async def _exclusion_origin(self, row: PrWorkContribution) -> PrWorkCountOrigin | None:
        """Who excluded a contribution: the source (``reverse_source_work``) or a person."""
        event = (
            (
                await self._session.execute(
                    select(PrWorkHistory)
                    .where(
                        PrWorkHistory.contribution_id == row.id,
                        PrWorkHistory.event_type == PrWorkEventType.EXCLUDED,
                    )
                    .order_by(PrWorkHistory.created_at.desc(), PrWorkHistory.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .one_or_none()
        )
        if event is None:
            return None
        origin = (event.event_metadata or {}).get("origin")
        if isinstance(origin, str) and origin in PrWorkCountOrigin.__members__:
            return PrWorkCountOrigin(origin)
        return None

    async def _require_not_finalized(
        self, moment: datetime, *, user_ids: Sequence[uuid.UUID]
    ) -> None:
        """Refuse to count into a month whose performance somebody has finalised."""
        if self._periods is None or not user_ids:
            return
        period = await self._periods.period_for(moment)
        if period is None:
            return
        for user_id in dict.fromkeys(user_ids):
            if await self._periods.performance_finalized(user_id=user_id, period_id=period.id):
                raise PrConflictError(
                    f"Hiệu suất kỳ {period.code} của một người thực hiện đã được chốt nên "
                    "không xác nhận thêm công việc vào kỳ này.",
                    details={
                        "reason": PERFORMANCE_FINALIZED,
                        "period": period.code,
                        "subject_user_id": str(user_id),
                    },
                )

    async def record_history(
        self,
        item: PrWorkItem,
        *,
        event: PrWorkEventType,
        actor: Actor,
        from_status: PrWorkStatus | None = None,
        to_status: PrWorkStatus | None = None,
        contribution: PrWorkContribution | None = None,
        note: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """The timeline writer, for the result service. See :meth:`_history`.

        Public so a period container's results appear on the same timeline as
        every other event on the item, written by the same method, rather than
        by a second writer that would one day disagree about the shape.
        """
        await self._history(
            item,
            event=event,
            actor=actor,
            from_status=from_status,
            to_status=to_status,
            contribution=contribution,
            note=note,
            metadata=metadata,
        )

    async def _history(
        self,
        item: PrWorkItem,
        *,
        event: PrWorkEventType,
        actor: Actor,
        from_status: PrWorkStatus | None = None,
        to_status: PrWorkStatus | None = None,
        contribution: PrWorkContribution | None = None,
        note: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Append one row to the **user-facing** timeline.

        Written in the caller's transaction alongside the audit row, and
        deliberately carrying different content: the audit row has the request
        id and the structured before/after for somebody investigating, this has
        the status edge and the note for somebody reading their own work's
        story. Two consumers, two shapes, one transaction.
        """
        self._session.add(
            PrWorkHistory(
                work_item_id=item.id,
                contribution_id=contribution.id if contribution is not None else None,
                event_type=event,
                from_status=from_status,
                to_status=to_status,
                actor_user_id=actor.user_id,
                note=note,
                event_metadata=dict(metadata) if metadata else None,
            )
        )
        await self._session.flush()

    @staticmethod
    def _refuse_final(item: PrWorkItem, what: str) -> None:
        if item.status in {
            PrWorkStatus.APPROVED,
            PrWorkStatus.REJECTED,
            PrWorkStatus.CANCELLED,
        }:
            raise PrValidationError(
                f"Công việc đã kết thúc nên không {what} được.",
                details={"reason": "work_is_final", "status": item.status.value},
            )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _require_status(item: PrWorkItem, expected: PrWorkStatus, *, target: PrWorkStatus) -> None:
    """Refuse unless the row is at ``expected``, in the transition table's own words.

    For the two writes whose target is reachable from more than one status
    (``IN_PROGRESS`` from ``ACCEPTED`` by starting and from ``COMPLETED`` by
    sending back). Each write owns one of those edges, and this is how it
    says so - with the same ``illegal_transition`` shape
    :func:`~meobot.domain.pr.work.assert_work_transition` uses, so a client
    reads one refusal vocabulary.
    """
    if item.status is expected:
        return
    raise PrValidationError(
        f"Cannot move work from {item.status.value!r} to {target.value!r}",
        details={
            "field": "status",
            "reason": "illegal_transition",
            "current": item.status.value,
            "target": target.value,
            "allowed": [expected.value] if item.status is expected else [],
            "expected": expected.value,
        },
    )


def _refuse_container(item: PrWorkItem, *, action: str) -> None:
    """A period container is not finished, validated or staffed like a job.

    Its results are reported and counted one by one through
    :class:`~meobot.application.pr_work_result_service.PrWorkResultService`;
    "complete" and "approve" have no meaning for a stream that runs until the
    month ends, and a second contributor would make one person's KPI stream
    two people's. The refusal names the reason so a client can route to the
    result controls instead.
    """
    if not item.is_period_container:
        return
    raise PrValidationError(
        "Đây là công việc định kỳ theo kỳ: hãy báo cáo và xác nhận từng kết quả "
        "thay vì thao tác trên cả công việc.",
        details={
            "reason": PERIOD_CONTAINER_LOCKED,
            "action": action,
            "work_item_id": str(item.id),
        },
    )


def _require_source_derived(item: PrWorkItem, *, action: str) -> None:
    """Raise unless this item was written by a projector rather than a person.

    The gate on both source-derived operations, and the reason M1's manual
    ladder is untouched by either. ``MANUAL`` is the only source a person can
    produce - no request body anywhere carries ``source_type`` - so "the source
    owns this row" and "no person filed this row" are the same test.
    """
    if item.source_type is PrWorkSourceType.MANUAL:
        raise PrValidationError(
            "Công việc này do người dùng tạo nên không thể cập nhật từ nguồn.",
            details={
                "field": "source_type",
                "reason": "not_source_derived",
                "action": action,
                "work_item_id": str(item.id),
            },
        )


def _require_user_id(actor: Actor) -> uuid.UUID:
    if actor.user_id is None:
        raise PrPermissionDeniedError(
            "Chỉ tài khoản đã đăng nhập mới thao tác được với công việc.",
            details={"reason": "actor_has_no_user_row"},
        )
    return actor.user_id


def _require_text(value: str, field: str, max_length: int) -> str:
    text = (value or "").strip()
    if not text:
        raise PrValidationError(
            "Trường này không được để trống.",
            details={"field": field, "reason": "empty"},
        )
    if len(text) > max_length:
        raise PrValidationError(
            f"Trường này tối đa {max_length} ký tự.",
            details={"field": field, "reason": "too_long", "max_length": max_length},
        )
    return text


def _optional_text(value: str | None, field: str, max_length: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return _require_text(text, field, max_length)


def _require_code(value: str) -> str:
    """A work type's stable identifier: upper snake, **ASCII** letters, digits, underscore.

    ASCII is checked explicitly, and that is the whole point of the function
    rather than a detail of it. ``str.isalnum()`` is true for every Unicode
    letter, so "Kịch bản" normalises to ``KỊCH_BẢN`` and would have been accepted
    as a machine identifier - which is exactly the confusion between the code and
    the display name that having two fields exists to prevent. A code travels
    through URLs, logs, spreadsheet exports and conversations between people
    reading different keyboards; the accented name belongs in ``name``.
    """
    code = (value or "").strip().upper().replace(" ", "_").replace("-", "_")
    ok = (
        code
        and len(code) <= 64
        and all((part.isascii() and part.isalnum()) or part == "_" for part in code)
    )
    if not ok:
        raise PrValidationError(
            "Mã loại công việc chỉ gồm chữ không dấu, số và dấu gạch dưới.",
            details={"field": "code", "reason": "malformed_code"},
        )
    return code


def require_quantity_for_basis(work_type: PrWorkType, quantity: Decimal | None) -> None:
    """Refuse a ``QUANTITY``-measured work type filed with no number. **M4A, moved in M4B.**

    M1 left ``quantity`` optional for every type, which was right while nothing
    read it. M2 then made ``default_quota_basis`` decide how counted work is
    *reported*, and a ``QUANTITY`` job filed without a number is not a small
    omission - it reads as zero comments against a plan expressed in comments.

    **Called by every operational entry point, deliberately not by ``_create``.**
    M4A put this at the route, on the reasoning that every way a *person* files
    work is an HTTP call. M4B made that reasoning false: the recurring generator
    creates operational work from a Celery beat sweep and touches no router at
    all, so a rule living in the router would have been a rule the scheduler did
    not have. It moved down exactly one level - to
    :meth:`PrWorkService._validate_operational_command`, which
    :meth:`~PrWorkService.propose_work`, :meth:`~PrWorkService.assign_work`,
    :meth:`~PrWorkService.assign_work_batch` and
    :meth:`~PrWorkService.generate_recurring_work` all call, and which the
    recurring template validates itself against before it may be activated.

    It went **no further down**, and that boundary is the point. M2's
    ``UNMEASURABLE`` / ``MISSING_QUANTITY`` state has to stay constructible: it
    is a materialised allocation naming the quota and saying what is missing,
    built to replace a silent ``NO_QUOTA`` that told an employee their manager
    had set no target when their manager had. Putting this check inside
    ``_create`` or ``create_source_work`` would make it unconstructible - the
    content projector always writes ``quantity = 1`` and M2.5 locks
    ``default_quota_basis`` once a type is in use, so no path would be left to
    reach it, and twenty M2 tests failed saying so when it was tried.

    Enforcing a new rule by quietly deleting an older milestone's repair path is
    not a stricter system; it is a system that has lost the ability to describe
    something that can still be true of rows already in the table.
    """
    if quantity is not None or work_type.default_quota_basis is not PrWorkQuotaBasis.QUANTITY:
        return
    raise PrValidationError(
        "Loại công việc này được tính theo số lượng. "
        f"Hãy nhập số {work_unit_label(work_type.default_unit).lower()}.",
        details={
            "field": "quantity",
            "reason": "quantity_required_for_basis",
            "quota_basis": work_type.default_quota_basis.value,
            "unit": work_type.default_unit.value,
        },
    )


def _require_quantity(value: Decimal | None) -> Decimal | None:
    """A plausible amount, or nothing.

    Bounded because ``quantity`` is the one field that scales what an item is
    worth, and an unbounded number in it is an unbounded claim.
    """
    if value is None:
        return None
    if value <= 0 or value > MAX_WORK_QUANTITY:
        raise PrValidationError(
            f"Số lượng phải lớn hơn 0 và không quá {MAX_WORK_QUANTITY:,.0f}.",
            details={"field": "quantity", "reason": "out_of_range"},
        )
    return value


def _require_weight(value: Decimal | None) -> Decimal:
    """A share of one job, at most a whole unit. See ``DEFAULT_CREDIT_WEIGHT``."""
    if value is None:
        return DEFAULT_CREDIT_WEIGHT
    if value < MIN_CREDIT_WEIGHT or value > MAX_CREDIT_WEIGHT:
        raise PrValidationError(
            "Tỷ lệ đóng góp phải nằm trong khoảng 0 đến 1.",
            details={"field": "credit_weight", "reason": "out_of_range"},
        )
    return value


def _unique(values: Sequence[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    """Preserve order, drop repeats. The first entry becomes ``PRIMARY``."""
    return tuple(dict.fromkeys(values))


__all__: list[str] = [
    "MAX_CONTRIBUTORS",
    "CreateWorkCommand",
    "PrWorkService",
    "WorkActions",
    "WorkItemDetail",
    "resolve_work_actions",
]
