"""The web notification centre's four endpoints.

Step 1F.2.3d. Everything the bell in the PR shell talks to: read the inbox, read
the badge, mark one, mark all.

Mounted at ``/api/notifications`` rather than under ``/api/pr``
---------------------------------------------------------------

A notification is not a PR object. PR is the only module emitting them today,
but the table, the service and these routes know nothing about content items
beyond a ``target_kind`` string - and putting them under the PR prefix would
have made "notifications belong to PR" a fact about the URL that somebody would
later have to migrate away from. ``/api/notifications`` is added to
``_ENVELOPE_PREFIXES`` in :mod:`meobot.api.main` so the errors are shaped like
every other browser-facing route's.

Authorization is ownership, and there is nothing else
------------------------------------------------------

No capability, no role, no permission check. A notification's audience is one
person and the session says who that is, so the only question these routes ask
is *are you the recipient* - and they ask it by putting ``actor.user_id`` in the
``WHERE`` clause rather than by loading a row and comparing. See
:class:`~meobot.application.user_notification_service.UserNotificationService`.

The consequence worth stating: an ``OWNER`` has no more access here than an
``EMPLOYEE``. There is no administrative view of somebody else's bell, because
there is no product reason for one and every way of building it is a way of
reading a colleague's private notifications.

Actors with no user row
------------------------

The bootstrap owner has ``actor.user_id is None``. Rather than 403 - which would
put an error box in the shell on every page load for a legitimately configured
deployment - the reads return an empty inbox and a zero badge, and the writes
report that there was nothing to mark. That is accurate: an actor with no
``users`` row has no notifications, because nothing can address one to them.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query

from meobot.api.deps import CurrentActorDep, SessionDep
from meobot.api.schemas.notifications import (
    MarkAllReadResponse,
    NotificationListResponse,
    NotificationResponse,
    UnreadCountResponse,
)
from meobot.application.user_notification_service import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    NotificationPage,
    UserNotificationService,
)
from meobot.core.errors import NotFoundError

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

_UNAUTHENTICATED: dict[int | str, dict[str, Any]] = {401: {"description": "No usable session."}}


@router.get("", response_model=NotificationListResponse, summary="Your notifications")
async def list_notifications(
    actor: CurrentActorDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationListResponse:
    """Your notifications, newest first, with the unread count.

    Bounded: ``limit`` is capped at
    :data:`~meobot.application.user_notification_service.MAX_LIMIT`, so there is
    no request that returns an entire history. A dropdown wants the recent ones,
    and somebody looking for a decision from last month is looking for the
    content rather than for the notification about it.
    """
    if actor.user_id is None:
        return NotificationListResponse.from_page(
            NotificationPage(items=(), unread_count=0, has_more=False)
        )
    service = UserNotificationService(session)
    page = await service.page(user_id=actor.user_id, limit=limit, offset=offset)
    return NotificationListResponse.from_page(page)


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="How many are unread",
    responses=_UNAUTHENTICATED,
)
async def unread_count(actor: CurrentActorDep, session: SessionDep) -> UnreadCountResponse:
    """The badge, and nothing else.

    A ``COUNT`` behind an index rather than a list the client measures - this is
    fetched on every page load, and the version that returns rows is the one
    that stops working first.
    """
    if actor.user_id is None:
        return UnreadCountResponse(unread_count=0)
    service = UserNotificationService(session)
    return UnreadCountResponse(unread_count=await service.unread_count(user_id=actor.user_id))


@router.post(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark one notification read",
    responses={404: {"description": "No such notification of yours."}, **_UNAUTHENTICATED},
)
async def mark_read(
    notification_id: uuid.UUID,
    actor: CurrentActorDep,
    session: SessionDep,
) -> NotificationResponse:
    """Mark one of **your** notifications read, and return it as it now stands.

    Somebody else's id is a 404, not a 403, and that is the intended answer: a
    403 would confirm the notification exists, which is one bit more than a
    stranger is entitled to learn about it. An id that never existed and an id
    belonging to a colleague are indistinguishable from outside, because from
    the caller's position they are the same thing - not yours.

    Marking an already-read notification is **not** an error. The service leaves
    ``read_at`` alone - first-seen is the useful fact and re-stamping it would
    destroy it - and this returns the row, so a double click is idempotent rather
    than a spurious failure in the panel.
    """
    if actor.user_id is None:
        raise _missing(notification_id)
    service = UserNotificationService(session)

    await service.mark_read(user_id=actor.user_id, notification_id=notification_id)

    # Read back through the owner-scoped lookup rather than trusting the
    # ``UPDATE``'s row count: that count is 0 both for a stranger's id *and* for
    # an already-read notification of your own, and those two need opposite
    # answers. Existence is what decides, and only this query can say.
    row = await service.get(user_id=actor.user_id, notification_id=notification_id)
    if row is None:
        raise _missing(notification_id)
    return NotificationResponse.from_row(row)


@router.post(
    "/read-all",
    response_model=MarkAllReadResponse,
    summary="Mark all your notifications read",
    responses=_UNAUTHENTICATED,
)
async def mark_all_read(actor: CurrentActorDep, session: SessionDep) -> MarkAllReadResponse:
    """Clear **your** bell.

    Scoped to the session's user by the service, with no parameter that could
    widen it and no role that does. The resulting count comes back so the badge
    is set from the server's answer rather than assumed to be zero.
    """
    if actor.user_id is None:
        return MarkAllReadResponse(marked=0, unread_count=0)
    service = UserNotificationService(session)
    marked = await service.mark_all_read(user_id=actor.user_id)
    return MarkAllReadResponse(
        marked=marked, unread_count=await service.unread_count(user_id=actor.user_id)
    )


def _missing(notification_id: uuid.UUID) -> NotFoundError:
    return NotFoundError(
        "Không tìm thấy thông báo này.",
        details={"notification_id": str(notification_id)},
    )
