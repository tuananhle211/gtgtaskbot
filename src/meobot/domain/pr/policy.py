"""What a PR action needs: a permission, and for the review gates a grant.

Step 1C mapped every PR action onto an existing
:class:`~meobot.domain.permissions.matrix.Permission` and reported the one
thing that mapping could not express: **the four MeoBot roles cannot tell a
Team Lead from a Head.** ``TEAM_LEAD`` holds both ``script.review`` and
``script.approve``, so whichever pair of permissions the two review gates are
mapped to, one person satisfies both.

Step 1C.1 fixed that by requiring, for the three review capabilities only, a
row in ``pr_user_capabilities`` alongside the role permission. Step 1F.2.7
keeps the table and changes two things about what a row in it means.

The rule, in full
-----------------

For the ten capabilities that are **not** grant-backed, nothing has changed and
nothing here is new: the actor's role must carry the capability's baseline
permission, and that is the whole check.

For the three that are - the review gates - authorization is a **disjunction**
over the person's active grants, evaluated against the item in front of them:

.. code-block:: text

    may_decide(actor, gate, item) :=
        any active grant G of APPROVAL_CAPABILITIES[gate] held by actor where
            (not G.requires_role_baseline or meets_baseline(actor, capability))
            and G.scope covers item's classification
            and G.scope covers every channel item is going to

:func:`grant_admits` is that predicate, for one grant, and it is the only place
it is written down. :class:`~meobot.application.pr_capability_service.PrCapabilityService`
loops it; every approval path calls that service and nothing re-implements it.

Additive, not narrowing
-----------------------

``requires_role_baseline`` is the difference between the two readings, and it is
per grant rather than global because both readings are true of real rows:

* ``true`` - the Step 1C.1 rule. The grant **narrows**: the holder must also
  hold the permission by role, so an ``EMPLOYEE`` granted ``PR_HEAD_REVIEW``
  still cannot use it. Every grant written before Step 1F.2.7 is migrated to
  this, because it is the rule those grants were given under and reinterpreting
  them as standalone would hand somebody an authority nobody decided to give;
* ``false`` - the Step 1F.2.7 rule, and what every new grant gets. The grant is
  **additive**: it authorises on its own, within its scope, whatever the
  holder's role. That is the business requirement this step exists for - a
  member who reviews Facebook posts on two channels, without being promoted to
  Team Lead and without gaining a single other capability.

Neither reading touches :data:`~meobot.domain.permissions.matrix.ROLE_PERMISSIONS`.
No role was changed, no permission was added, and nobody's role is promoted by
being granted anything. A grant is an exception recorded beside the matrix, not
an edit to it.

Scope
-----

Where a grant applies is :class:`~meobot.domain.pr.grants.GrantScope`: a set of
content classifications and a set of channels, each either ``ALL`` or exactly
the values selected. The matching rules - including what happens to an item
nobody classified and an item with no channel - are in
:mod:`meobot.domain.pr.grants`, which is where to argue with them.

No override
-----------

There is no super-admin bypass, and none was added. The only bypass anywhere in
this repository's authorization is ``_OWNERSHIP_BYPASS_RANK`` in
:class:`~meobot.domain.policy.engine.PolicyEngine`, which lets team leads and
above act on resources they do not personally own - it is a *per-resource
ownership* rule inside the tool layer, it does not skip the permission check,
and it has never applied to these services. ``OWNER`` holding every
``Permission`` is not an override either: the review gates additionally require
a grant that ``OWNER`` does not get automatically. See
``docs/pr/STEP_1C1_AUTHORIZATION_AND_CODES.md`` and
``docs/pr/STEP_1F27_SCOPED_APPROVAL_GRANTS.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor
from meobot.domain.permissions.matrix import Permission, has_permission
from meobot.domain.pr.errors import PrPermissionDeniedError
from meobot.domain.pr.grants import ContentScopeKey, GrantScope
from meobot.domain.pr.models import PrApprovalStage


class PrCapability(StrEnum):
    """A thing a PR service does, named from the business rather than the role.

    Thirteen, and write actions only. Reads are gated on
    :attr:`~meobot.domain.permissions.matrix.Permission.SCRIPT_READ` directly
    through the existing matrix - a capability per read would double the
    vocabulary and separate nothing that is not already separated.

    Ten of them are Step 1C.1's. Step 1F.2.3 added the last three for the
    production half of the workflow, and every one of them is a **new pairing of
    existing permissions**, not a new permission: see ``_BASELINE_PERMISSIONS``.
    """

    PR_CONTENT_CREATE = "PR_CONTENT_CREATE"
    PR_CONTENT_EDIT = "PR_CONTENT_EDIT"
    PR_TEAM_LEAD_REVIEW = "PR_TEAM_LEAD_REVIEW"
    PR_HEAD_REVIEW = "PR_HEAD_REVIEW"
    PR_INTERNAL_REVIEW = "PR_INTERNAL_REVIEW"
    PR_TASK_MANAGE = "PR_TASK_MANAGE"
    PR_CHANNEL_MANAGE = "PR_CHANNEL_MANAGE"
    PR_PUBLICATION_REGISTER = "PR_PUBLICATION_REGISTER"
    #: Step 1F.2.3f.2. **Recording that a post went out**, and nothing else.
    #:
    #: Separate from ``PR_PUBLICATION_REGISTER`` because the two are different
    #: jobs that had been sharing one name. Registering a publication is daily
    #: contributor work - somebody posts a video and writes down where it went -
    #: while *administering* publication history is management: correcting
    #: anybody's link, taking a posting back, deciding what the record says.
    #:
    #: Before this step both needed ``publish.social``, which only ``ADMIN`` and
    #: ``OWNER`` hold, so a member who had just posted something could not say
    #: so. Widening ``publish.social`` to ``EMPLOYEE`` would have handed them
    #: every other thing that permission guards, so the narrow capability is the
    #: honest split: this one is ``script.submit``, the permission that already
    #: means "a contributor's own work".
    #:
    #: Step 1F.2.3f.3 narrowed what this capability is *for*. Creating a
    #: publication no longer consults it at all: recording that a piece went out
    #: is the module's **view** rule now - see
    #: ``PrPublicationService.may_record_publication`` - because the person who
    #: posted the video is routinely neither the channel's assignee nor the
    #: content's owner, and the channel condition this capability used to carry
    #: refused exactly them.
    #:
    #: What it still marks is the **contributor half of correcting one**: with
    #: ``publisher_user_id``, it is what lets somebody fix the link they
    #: themselves recorded. Editing somebody else's publication, reversing one,
    #: and every workflow transition stay exactly where they were.
    PR_PUBLICATION_CREATE = "PR_PUBLICATION_CREATE"
    PR_CONTENT_CANCEL = "PR_CONTENT_CANCEL"
    PR_CONTENT_TRANSITION = "PR_CONTENT_TRANSITION"
    #: Step 1F.2.3. Asking for a content item to be removed from the workspace.
    #: Held by everybody who may write content, because a member deleting their
    #: own untouched draft is ordinary work - what makes management deletion
    #: different is the *second* capability below, not this one. See
    #: :func:`~meobot.domain.pr.lifecycle.delete_permitted`.
    PR_CONTENT_DELETE = "PR_CONTENT_DELETE"
    #: Step 1F.2.3. Saying **who** produces a piece, and taking it off them.
    PR_PRODUCTION_ASSIGN = "PR_PRODUCTION_ASSIGN"
    #: Step 1F.2.3. Doing the production work: claiming an unclaimed piece and
    #: submitting the file for internal review.
    PR_PRODUCTION_EXECUTE = "PR_PRODUCTION_EXECUTE"

    # --- M1: the Work Ledger ---------------------------------------------
    #: Doing your own work: proposing it, moving it along, attaching evidence.
    #: What every employee already has, because filing what you did is
    #: ordinary contributor work rather than a management act.
    PR_WORK_EXECUTE = "PR_WORK_EXECUTE"
    #: Deciding **whose** work it is: assigning it, accepting or rejecting a
    #: proposal, adding and removing contributors, moving a deadline,
    #: cancelling. The management half of the ledger.
    PR_WORK_MANAGE = "PR_WORK_MANAGE"
    #: **The anti-gaming capability.** Confirming that finished work really was
    #: done, which is the only act that makes a contribution ``COUNTED``.
    #:
    #: Separate from ``PR_WORK_MANAGE`` even though both currently reach the
    #: same roles, because they are different decisions and the day validation
    #: earns points is the day somebody will want to hold one without the
    #: other. Holding it is never sufficient on its own: a validator who
    #: contributed to the work is refused whatever they hold - see
    #: :meth:`~meobot.application.pr_work_service.PrWorkService.approve`.
    PR_WORK_VALIDATE = "PR_WORK_VALIDATE"
    #: Editing the work taxonomy. Configuration, like channel master data.
    PR_WORK_CONFIGURE = "PR_WORK_CONFIGURE"
    #: **Seeing the whole department's work**, whoever it belongs to.
    #:
    #: Separate from ``PR_WORK_MANAGE`` on purpose, and the separation is the
    #: point: managing work means deciding about the work *you* put somebody on
    #: or that is waiting for *your* decision, and it must not quietly become a
    #: window onto every colleague's workload. A Trưởng nhóm who assigns three
    #: jobs may follow those three; reading everybody's record is a different
    #: act with different consequences for the people in it.
    #:
    #: MeoBot models no team, department or manager relationship, so there is no
    #: honest middle ground between "what I am part of" and "everything". This
    #: capability is the explicit "everything", held by Head and Admin, rather
    #: than something inferred from channel assignments.
    PR_WORK_VIEW_ALL = "PR_WORK_VIEW_ALL"
    #: **Judging somebody's month.** M6's monthly performance review.
    #:
    #: Its own capability rather than a corner of ``PR_WORK_MANAGE``, and the
    #: distinction is the same one ``PR_WORK_VIEW_ALL`` makes one step further
    #: on: assigning work to a person is not the same act as deciding what their
    #: quality, timeliness and contribution were worth this month, because the
    #: second is a judgement about a person that follows them.
    #:
    #: Paired with ``user.manage`` - the permission that already means "may make
    #: decisions about people" - so it reaches Admin and Owner and **not** a
    #: Trưởng nhóm. MeoBot still models no team, so there is no honest narrower
    #: scope; inventing one from channel assignments would make "who may rate
    #: you" depend on who happens to publish your work.
    #:
    #: Holding it never permits reviewing **yourself**: that is refused by the
    #: service and by a database CHECK, whatever capability the actor has.
    PR_PERFORMANCE_REVIEW = "PR_PERFORMANCE_REVIEW"


#: Capability -> the existing permission that must be held for it.
#:
#: Every value predates Step 1C.1. The reasoning for each, so a later reader
#: can argue with a specific pairing rather than the whole idea:
#:
#: * writing a draft or moving it along is ``script.submit`` - a contributor's
#:   own work;
#: * cancelling ends work, so it takes the approval right rather than the
#:   submit right;
#: * team-lead review is ``script.review``, head review is ``script.approve`` -
#:   two genuinely different permissions, which is why the pairing is worth
#:   keeping even though the grant is what actually separates the two gates;
#: * internal review judges a produced cut, which is what ``video.approve``
#:   already means;
#: * channel master data is configuration, so ``settings.write``;
#: * registering a publication is ``publish.social`` - the permission that has
#:   always guarded "this went public". Step 1F.2.3f.2 kept that pairing for
#:   ``PR_PUBLICATION_REGISTER``, which became the **administration** half, and
#:   gave the creation half its own capability on ``script.submit``.
#:
#: Step 1F.2.3's three, on the same principle - an existing permission that
#: already means the thing, never a new one:
#:
#: * asking to delete content is ``script.submit``. It is the *writer's* right,
#:   because the ordinary deletion is somebody removing a draft they started by
#:   mistake. Management-wide deletion is not this capability: it is this one
#:   **and** ``PR_CONTENT_CANCEL``, which is already ``script.approve`` and
#:   already means "may end this piece of work". No third permission, no role
#:   string, and no super-admin bypass;
#: * producing a cut is ``video.submit`` - the permission that has always meant
#:   "may hand in video work", which is exactly what a producer does;
#: * saying who produces it is ``video.approve``, the same permission behind
#:   ``PR_INTERNAL_REVIEW``: whoever answers for the finished cut is who may say
#:   whose cut it will be. That pairing puts assignment with ``TEAM_LEAD`` and
#:   above and keeps it away from ``EMPLOYEE``, which is the intent - and it is
#:   the permission matrix expressing it, not a role check.
_BASELINE_PERMISSIONS: Mapping[PrCapability, Permission] = MappingProxyType(
    {
        PrCapability.PR_CONTENT_CREATE: Permission.SCRIPT_SUBMIT,
        PrCapability.PR_CONTENT_EDIT: Permission.SCRIPT_SUBMIT,
        PrCapability.PR_CONTENT_TRANSITION: Permission.SCRIPT_SUBMIT,
        PrCapability.PR_CONTENT_CANCEL: Permission.SCRIPT_APPROVE,
        PrCapability.PR_TEAM_LEAD_REVIEW: Permission.SCRIPT_REVIEW,
        PrCapability.PR_HEAD_REVIEW: Permission.SCRIPT_APPROVE,
        PrCapability.PR_INTERNAL_REVIEW: Permission.VIDEO_APPROVE,
        PrCapability.PR_TASK_MANAGE: Permission.SCRIPT_SUBMIT,
        PrCapability.PR_CHANNEL_MANAGE: Permission.SETTINGS_WRITE,
        PrCapability.PR_PUBLICATION_REGISTER: Permission.PUBLISH_SOCIAL,
        PrCapability.PR_CONTENT_DELETE: Permission.SCRIPT_SUBMIT,
        # Step 1F.2.3f.2. ``script.submit`` - the permission that has always
        # meant "a contributor's own work". Recording that you posted something
        # is exactly that, and it is what every ``EMPLOYEE`` already holds.
        PrCapability.PR_PUBLICATION_CREATE: Permission.SCRIPT_SUBMIT,
        PrCapability.PR_PRODUCTION_EXECUTE: Permission.VIDEO_SUBMIT,
        PrCapability.PR_PRODUCTION_ASSIGN: Permission.VIDEO_APPROVE,
        # M1's four, on the same principle - an existing permission that
        # already means the thing, never a new one:
        #
        # * doing your own work is ``script.submit``, the permission that has
        #   always meant "a contributor's own work" and that every ``EMPLOYEE``
        #   holds;
        # * assigning it is ``video.approve``, the same pairing behind
        #   ``PR_PRODUCTION_ASSIGN`` - whoever may say whose cut it will be may
        #   say whose job it will be. That puts it at ``TEAM_LEAD`` and above
        #   and keeps it away from ``EMPLOYEE``, which is the intent, expressed
        #   by the permission matrix rather than by a role check;
        # * validating completed work is ``script.approve`` - the permission
        #   that already means "this is accepted as done";
        # * the taxonomy is ``settings.write``, like every other piece of PR
        #   master data, which puts it at ``ADMIN`` and above.
        PrCapability.PR_WORK_EXECUTE: Permission.SCRIPT_SUBMIT,
        PrCapability.PR_WORK_MANAGE: Permission.VIDEO_APPROVE,
        PrCapability.PR_WORK_VALIDATE: Permission.SCRIPT_APPROVE,
        PrCapability.PR_WORK_CONFIGURE: Permission.SETTINGS_WRITE,
        # M6. ``user.manage`` - already "may make decisions about people" -
        # which reaches Admin and Owner and stops short of Trưởng nhóm.
        PrCapability.PR_PERFORMANCE_REVIEW: Permission.USER_MANAGE,
        # ``user.read`` - the permission that already means "may see who works
        # here". Seeing everybody's *work* is the same statement one step on,
        # and it reaches exactly ``ADMIN`` and ``OWNER``, which is the Head and
        # Admin the Work module needs. Deliberately not ``metrics.read`` or
        # ``report.generate``, which reach ``TEAM_LEAD`` and would put the
        # department-wide view back where this capability exists to remove it
        # from.
        PrCapability.PR_WORK_VIEW_ALL: Permission.USER_READ,
    }
)

#: The capabilities that additionally require a row in
#: ``pr_user_capabilities``. Exactly the three review gates, because they are
#: exactly the decisions the role matrix cannot tell apart. Everything else is
#: adequately separated by permissions alone, and demanding a grant for it
#: would be paperwork with no invariant behind it.
GRANT_BACKED: frozenset[PrCapability] = frozenset(
    {
        PrCapability.PR_TEAM_LEAD_REVIEW,
        PrCapability.PR_HEAD_REVIEW,
        PrCapability.PR_INTERNAL_REVIEW,
    }
)

#: Which capability each human approval gate needs. The gate is a workflow
#: fact; what it takes to stand at it is this table.
APPROVAL_CAPABILITIES: Mapping[PrApprovalStage, PrCapability] = MappingProxyType(
    {
        PrApprovalStage.TEAM_LEAD_REVIEW: PrCapability.PR_TEAM_LEAD_REVIEW,
        PrApprovalStage.HEAD_REVIEW: PrCapability.PR_HEAD_REVIEW,
        PrApprovalStage.INTERNAL_REVIEW: PrCapability.PR_INTERNAL_REVIEW,
    }
)

#: How many content items one bulk approval request may name. Step 1F.2.8.
#:
#: 200, and the number is a consequence of the all-or-nothing promise rather
#: than a guess. One batch is one transaction holding one row lock per item for
#: its whole duration, and each item costs a version read, a scope check, an
#: approval insert, a transition insert, an audit row and possibly a queued
#: notification. At 200 that is a transaction of a few hundred milliseconds
#: holding 200 locks - long enough to be worth knowing about, short enough that
#: a second reviewer working the same gate waits rather than times out. At 2 000
#: it would be a minutes-long transaction blocking every single-item approval of
#: anything in it, and a failure at item 1 999 would throw away all of it.
#:
#: It bounds the *explicit id* list, and there is deliberately no second, larger
#: limit for "approve everything matching": a select-all is resolved to explicit
#: ids by
#: :meth:`~meobot.application.pr_query_service.PrQueryService.approvable_selection`,
#: which returns at most this many and says how many it left. A queue of 340
#: is two honest batches, not one batch that reports 340 and did 200.
BULK_APPROVAL_MAX_ITEMS: int = 200

#: The most content one *"Lưu trữ nội dung kỳ trước"* may put away. Step
#: 1F.2.3f.6, and the same figure as the approval batch for the same reasons:
#: every item is one ``PUBLISHED -> ARCHIVED`` transition through
#: :meth:`~meobot.application.pr_workflow_service.PrContentWorkflowService.request_transition`
#: - its lock, its audit row, its transition event and its work-projection
#: request - inside one transaction that either commits all of them or none.
#: A month with more published pieces than this is two honest batches.
BULK_ARCHIVE_MAX_ITEMS: int = 200

#: The permission that gates *reading* PR data. One permission rather than
#: three read capabilities: nothing in this module separates who may read
#: content from who may read tasks, and pretending otherwise would be a
#: vocabulary that lies about its own precision.
PR_READ_PERMISSION: Permission = Permission.SCRIPT_READ

#: The permission that gates recording an automated review. Not a capability:
#: an AI review is advisory quality control that names a model rather than a
#: person, so there is no individual to grant anything to.
PR_AI_REVIEW_PERMISSION: Permission = Permission.SCRIPT_REVIEW

#: The permission that gates granting and revoking PR capabilities. Deciding
#: who may approve is a decision about who may do what, which is what this
#: permission has always guarded - and it is ``OWNER``-only, so handing out
#: review rights stays where handing out authority already lives.
PR_CAPABILITY_ADMIN_PERMISSION: Permission = Permission.USER_ROLE_MANAGE


def baseline_permission(capability: PrCapability) -> Permission:
    """The existing permission a PR capability is built on."""
    return _BASELINE_PERMISSIONS[capability]


def requires_grant(capability: PrCapability) -> bool:
    """True when an active ``pr_user_capabilities`` row is also needed."""
    return capability in GRANT_BACKED


def meets_baseline(actor: Actor, capability: PrCapability) -> bool:
    """True when the actor's role carries the permission behind ``capability``."""
    return has_permission(actor.role, baseline_permission(capability))


def require_permission(actor: Actor, permission: Permission) -> None:
    """Raise :class:`PrPermissionDeniedError` unless the actor holds ``permission``.

    Used for the PR actions that are not capabilities - reads, recording an AI
    verdict, and administering grants.
    """
    if has_permission(actor.role, permission):
        return
    raise PrPermissionDeniedError(
        f"Role {actor.role.value} lacks {permission.value}",
        details={
            "role": actor.role.value,
            "role_label": role_label(actor.role),
            "permission": permission.value,
        },
    )


def may_read(actor: Actor) -> bool:
    """True when this actor may **see** PR content. Step 1F.2.3g.

    The boolean form of ``require_permission(actor, PR_READ_PERMISSION)``, and
    the same decision: there is exactly one rule for who may look at a piece of
    content in this module, it is a role permission, and it has never been
    per-row. Every list, every detail read and every child collection is gated
    on it and on nothing narrower - see
    :class:`~meobot.application.pr_query_service.PrQueryService`.

    Written down here so the two things Step 1F.2.3g opens to *any* viewer -
    contributing a derivative, and commenting - ask the same question the read
    already asked, rather than approximating it with a role string or an
    ownership check. A caller that wants the refusal calls
    :func:`require_permission` instead; this exists for the read models that
    build a list of offers and must not raise.
    """
    return has_permission(actor.role, PR_READ_PERMISSION)


def grant_admits(
    actor: Actor,
    capability: PrCapability,
    *,
    requires_role_baseline: bool,
    scope: GrantScope,
    key: ContentScopeKey | None,
) -> bool:
    """Whether **one** active grant authorises this actor for this item.

    The predicate from the module docstring, for a single grant. Pure: the
    caller has already established that the grant is active - not revoked, and
    inside its dates - because that is a question about a row and a clock, and
    this is a question about authority.

    Args:
        actor: The authenticated principal. Only :attr:`Actor.role` is read;
            the grant was already matched to :attr:`Actor.user_id`.
        capability: The grant-backed capability being exercised.
        requires_role_baseline: The grant's own reading of itself - see the
            module docstring. ``True`` for rows written before Step 1F.2.7.
        scope: Where the grant applies.
        key: The item being decided. ``None`` asks the weaker question *could
            this grant ever authorise them* - which is what a screen drawing
            buttons for a list, or a queue picking lanes, needs. **Never** what
            a write asks: every approval path passes the item.
    """
    if requires_role_baseline and not meets_baseline(actor, capability):
        return False
    if key is None:
        return True
    return scope.covers(key)


def require_baseline(actor: Actor, capability: PrCapability) -> None:
    """Raise unless the actor's role carries the capability's permission.

    The first half of the check. The second half - the grant - needs a session
    and lives in
    :meth:`~meobot.application.pr_capability_service.PrCapabilityService.require`,
    which calls this first so a role that was never entitled fails for the
    honest reason rather than for a missing grant.

    Messages are structural English and the codes are in ``details``: PR
    services are shared by the Telegram bot, a future web admin UI and CLI
    scripts, so the sentence a human reads belongs to the client.
    """
    if meets_baseline(actor, capability):
        return
    permission = baseline_permission(capability)
    raise PrPermissionDeniedError(
        f"Role {actor.role.value} lacks {permission.value} required for {capability.value}",
        details={
            "role": actor.role.value,
            "role_label": role_label(actor.role),
            "permission": permission.value,
            "capability": capability.value,
            "reason": "missing_permission",
        },
    )


__all__: list[str] = [
    "APPROVAL_CAPABILITIES",
    "BULK_APPROVAL_MAX_ITEMS",
    "BULK_ARCHIVE_MAX_ITEMS",
    "GRANT_BACKED",
    "PR_AI_REVIEW_PERMISSION",
    "PR_CAPABILITY_ADMIN_PERMISSION",
    "PR_READ_PERMISSION",
    "PrCapability",
    "baseline_permission",
    "grant_admits",
    "may_read",
    "meets_baseline",
    "require_baseline",
    "require_permission",
    "requires_grant",
]
