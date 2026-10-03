"""A channel's link to a platform account, and the health of syncing it.

Step 1F.2.4b. Step 1F.2.4a shipped the provider *port* and no provider, and
said the connector step would add an implementation and change no table. This
module is the vocabulary that step needs: what a connection is, what state it is
in, how a sync went, and how those two combine into the one badge a channel card
shows.

Three vocabularies, deliberately not one
-----------------------------------------

The mistake this module exists to avoid is a single ``status`` string meaning
both *"OAuth still works"* and *"the last sync succeeded"*. Those come apart
constantly - a perfectly valid connection whose last run hit a quota limit is
connected **and** failing - and a screen driven by one string has to choose
which of the two truths to tell.

So:

* :class:`PrChannelConnectionState` - can we still talk to the platform at all?
  ``CONNECTED``, ``ACTION_REQUIRED`` (the token is gone and a person must
  reauthorize), ``DISCONNECTED``;
* :class:`PrChannelSyncStatus` - how did the most recent attempt go?
  ``NEVER_SYNCED``, ``SYNCING``, ``SUCCESS``, ``FAILED``;
* :class:`PrChannelSyncErrorCode` - if it failed, what class of failure was it,
  in terms a person and a retry policy can both act on.

And :func:`metrics_status_for` combines the connection state with "are there any
snapshots" into the Step 1F.2.4a data badge - which is why *that* enum grew an
``ACTION_REQUIRED`` member rather than being made to lie.

Nothing here knows what YouTube is
-----------------------------------

No Google endpoint, no scope string, no HTTP. This is the vocabulary; the
transport is :mod:`meobot.integrations.youtube` and the orchestration is
:mod:`meobot.application.pr_channel_sync_service`. A second provider adds a
registry entry and nothing in this file changes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from meobot.core.errors import MeoBotError
from meobot.domain.pr.channel_metrics import (
    MANUAL_METRIC_FIELDS,
    PrChannelMetricsStatus,
    PrChannelPlatform,
)


class PrChannelConnectionState(StrEnum):
    """Whether MeoBot can still reach this channel's platform account.

    About the **credential**, never about the last run. A connection that has
    failed its last five syncs on a provider outage is still ``CONNECTED``:
    nothing about the authorization is wrong, and telling somebody to
    reauthorize would waste their time and rotate a working token.
    """

    #: A usable refresh token is held. Sync may be attempted.
    CONNECTED = "CONNECTED"
    #: The stored credential no longer works - revoked in the Google account,
    #: expired through disuse, or scopes withdrawn. Only a person can fix it.
    ACTION_REQUIRED = "ACTION_REQUIRED"
    #: Deliberately taken down, or never established. No credential is held.
    DISCONNECTED = "DISCONNECTED"
    #: Step 1F.2.4c. Consent is done and **nothing is bound yet** - the interval
    #: in which a person is choosing which Facebook Page or Instagram account
    #: this channel means.
    #:
    #: Its own member because neither of the others is true here. ``CONNECTED``
    #: would claim a channel that cannot sync; ``DISCONNECTED`` would claim no
    #: credential is held, while a long-lived user token is sitting in the row
    #: precisely so the chooser can list accounts. YouTube never reaches this
    #: state - Google binds the single channel that authorized - which is why
    #: Step 1F.2.4b had no use for it.
    #:
    #: It needs no migration: ``status`` is a plain ``VARCHAR(20)`` with no check
    #: constraint, so a new member is a new string in a column that accepts it.
    PENDING_SELECTION = "PENDING_SELECTION"


#: States in which a scheduled sweep may pick a connection up. ``ACTION_REQUIRED``
#: is excluded on purpose: retrying a revoked token every cycle earns nothing but
#: rate limits and log noise, and the fix is a person clicking reconnect.
SYNCABLE_STATES: frozenset[PrChannelConnectionState] = frozenset(
    {PrChannelConnectionState.CONNECTED}
)


class PrChannelSyncStatus(StrEnum):
    """How the most recent sync attempt went."""

    #: Connected, but nothing has been fetched yet.
    NEVER_SYNCED = "NEVER_SYNCED"
    #: Claimed by a worker and in flight. Also the lock - see
    #: ``PrChannelSyncService``, where claiming is a conditional UPDATE.
    SYNCING = "SYNCING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class PrChannelSyncErrorCode(StrEnum):
    """Why a sync failed, in a vocabulary that is safe to store and show.

    Closed, small, and **never a provider message**. A provider's own error text
    is somebody else's prose about somebody else's system: it changes without
    notice, it has been known to contain identifiers, and it is not something a
    Vietnamese-speaking channel manager can act on. What they can act on is
    "reconnect" versus "wait" versus "tell an engineer", which is what these
    seven values distinguish.
    """

    #: The credential is gone. Only a reconnect fixes it, and it is the one code
    #: that moves the connection to ``ACTION_REQUIRED``.
    AUTH_REQUIRED = "AUTH_REQUIRED"
    #: Quota or rate limit. Transient by definition; back off and try later.
    RATE_LIMITED = "RATE_LIMITED"
    #: The platform is down, slow, or returned a 5xx. Transient.
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    #: The authorized account is not the one this connection is bound to.
    INVALID_ACCOUNT = "INVALID_ACCOUNT"
    #: The token works but was granted fewer scopes than the sync needs.
    INSUFFICIENT_SCOPE = "INSUFFICIENT_SCOPE"
    #: A 200 whose body was not what the API documents. Not retried blindly.
    BAD_RESPONSE = "BAD_RESPONSE"
    UNKNOWN = "UNKNOWN"


#: Failures worth trying again on the next cadence without a person involved.
#: ``AUTH_REQUIRED``, ``INVALID_ACCOUNT`` and ``INSUFFICIENT_SCOPE`` all need a
#: human decision, and retrying them is how a quiet integration becomes a loud
#: one for no benefit.
TRANSIENT_SYNC_ERRORS: frozenset[PrChannelSyncErrorCode] = frozenset(
    {
        PrChannelSyncErrorCode.RATE_LIMITED,
        PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE,
        PrChannelSyncErrorCode.BAD_RESPONSE,
        PrChannelSyncErrorCode.UNKNOWN,
    }
)


class ChannelSyncProviderError(MeoBotError):
    """A provider could not answer, already classified.

    The one exception type the sync orchestration catches, and the reason it
    exists is the port's whole purpose: ``PrChannelSyncService`` must not import
    a YouTube error class, because a second provider would then have to raise
    one. The transport classifies its own failures - which it is the only layer
    equipped to do, since only it knows what a 403 from this particular API
    means - and hands up a value from
    :class:`PrChannelSyncErrorCode`.

    It deliberately carries **no provider payload**. A provider's own error prose
    is somebody else's text about somebody else's system; what travels is the
    class, which is what a retry policy and a Vietnamese screen can both act on.
    """

    code = "pr.channel_sync_provider"

    #: The class of failure. A **class attribute with no ``__init__`` of its
    #: own**, deliberately: the concrete errors are integration errors too -
    #: ``YouTubeApiError`` inherits both - and a constructor here would sit in
    #: the middle of that cooperative chain and have to accept every keyword
    #: ``IntegrationError`` passes through it. Contributing nothing to the chain
    #: makes this base transparent, and each subclass sets the value itself.
    error_code: PrChannelSyncErrorCode = PrChannelSyncErrorCode.UNKNOWN


class PrChannelSyncTrigger(StrEnum):
    """Who asked for this sync. Audit only - it changes nothing about the run."""

    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"


#: The platforms a connector exists for. Everything else is *not yet supported*,
#: which the UI says in words rather than by offering a button that fails.
#:
#: Step 1F.2.4b implemented YouTube; Step 1F.2.4c added Facebook and Instagram;
#: Step 1F.2.6 added TikTok. Website and Other are absent on purpose and there
#: is no class for them anywhere in the tree - an empty provider that raises
#: would read, to somebody scanning the codebase, like an integration that
#: exists.
#:
#: Membership here means *a connection can be established and kept healthy*. It
#: does **not** mean every card on the metrics panel fills: TikTok's connector
#: reads the lifetime counts the Display API serves and nothing windowed, because
#: that API has no reporting window at all. See
#: :mod:`meobot.integrations.tiktok.provider`.
CONNECTABLE_PLATFORMS: frozenset[PrChannelPlatform] = frozenset(
    {
        PrChannelPlatform.YOUTUBE,
        PrChannelPlatform.FACEBOOK,
        PrChannelPlatform.INSTAGRAM,
        PrChannelPlatform.TIKTOK,
    }
)


def is_connectable(platform: PrChannelPlatform | None) -> bool:
    """Whether a connector exists for this platform at all."""
    return platform is not None and platform in CONNECTABLE_PLATFORMS


def metrics_status_for(
    *,
    connection_state: PrChannelConnectionState | None,
    has_snapshot: bool,
) -> PrChannelMetricsStatus:
    """The Step 1F.2.4a data badge, now that connections exist.

    Still derived, still stored nowhere. What changed is that
    ``has_api_connection`` stopped being a constant ``False``.

    The ``ACTION_REQUIRED`` branch is the one worth reading twice: a channel
    whose token has been revoked keeps every API reading it ever took, and must
    **not** show a healthy "Đã kết nối API" badge over data that stopped
    updating three weeks ago. It gets its own state, and the history stays.

    Args:
        connection_state: The channel's connection, or ``None`` when it has
            never had one.
        has_snapshot: Whether any metric snapshot exists, of any source.
    """
    if connection_state is PrChannelConnectionState.CONNECTED:
        return PrChannelMetricsStatus.CONNECTED_API
    if connection_state is PrChannelConnectionState.ACTION_REQUIRED:
        return PrChannelMetricsStatus.ACTION_REQUIRED
    # A half-finished connect flow is not a data source. Falling through means a
    # channel mid-selection shows MANUAL or DISCONNECTED - whichever is true of
    # its snapshots - rather than claiming an API feed it does not yet have.
    if has_snapshot:
        return PrChannelMetricsStatus.MANUAL
    return PrChannelMetricsStatus.DISCONNECTED


def reading_fingerprint(
    *,
    provider_account_id: str,
    period_start: str | None,
    period_end: str | None,
    metrics: Mapping[str, Any],
) -> str:
    """A stable key for *this reading of this account over this period*.

    The idempotency device. A scheduler that retries a job, two sweeps that
    overlap, or an operator pressing "Đồng bộ ngay" twice must not leave three
    identical rows in a time series - so the key is stored on the snapshot and
    a unique index refuses the second write.

    What goes in is deliberate:

    * **the account**, so two channels cannot collide;
    * **the effective reporting period**, which is what makes this correct
      rather than merely convenient. Two syncs a week apart that both report
      124,812 followers are *different readings that happen to agree*, and they
      both belong in the timeline - they have different period bounds, so they
      get different keys. Only a re-fetch of the same period with the same
      numbers is a duplicate;
    * **the metric values**, so a same-period re-fetch that actually returns
      new figures - Analytics data settling a day late - is a new reading
      rather than being swallowed.

    ``observed_at`` is deliberately **not** in it. That is when MeoBot looked,
    and including it would make every fingerprint unique and the whole mechanism
    a no-op.
    """
    material = {
        "account": provider_account_id,
        "period_start": period_start,
        "period_end": period_end,
        # Sorted and rendered canonically so two dicts that differ only in key
        # order cannot produce two fingerprints.
        "metrics": {name: metrics.get(name) for name in MANUAL_METRIC_FIELDS},
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def is_due(
    *,
    last_succeeded_at: datetime | None,
    now: datetime,
    min_interval_seconds: int,
) -> bool:
    """Whether a connection has waited long enough to be synced again.

    Measured from the last **success**, not the last attempt. A connection
    failing every cycle would otherwise never become due again, and the point of
    a cadence is that a channel gets fetched about once a day whatever happened
    in between.
    """
    if last_succeeded_at is None:
        return True
    return (now - last_succeeded_at).total_seconds() >= min_interval_seconds


def backoff_seconds(consecutive_failures: int, *, base_seconds: int, cap_seconds: int) -> int:
    """How long to rest a connection after ``consecutive_failures`` failures.

    Exponential and capped. Nothing here is a tight loop: the smallest possible
    wait is one sweep interval, because this only ever *delays* a connection the
    sweeper would otherwise have picked up.
    """
    if consecutive_failures <= 0:
        return 0
    shift = min(consecutive_failures - 1, 16)
    return int(min(cap_seconds, base_seconds * (2**shift)))


__all__: list[str] = [
    "CONNECTABLE_PLATFORMS",
    "SYNCABLE_STATES",
    "TRANSIENT_SYNC_ERRORS",
    "ChannelSyncProviderError",
    "PrChannelConnectionState",
    "PrChannelSyncErrorCode",
    "PrChannelSyncStatus",
    "PrChannelSyncTrigger",
    "backoff_seconds",
    "is_connectable",
    "is_due",
    "metrics_status_for",
    "reading_fingerprint",
]
