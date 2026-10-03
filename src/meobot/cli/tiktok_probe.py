"""Operator command: ask a connected TikTok account what the Display API serves.

Step 1F.2.6.

```
meobot-tiktok-probe list                which channels have a live TikTok connection
meobot-tiktok-probe run CH-0014         probe that channel's account, one field at a time
meobot-tiktok-probe run CH-0014 --json  the same report, machine-readable
```

Why this is a shell command and not a button
---------------------------------------------

For the reason ``meobot-meta-probe`` gives. A probe is an *investigation*, and
the answer is a table of TikTok field names that means nothing to anybody
outside engineering. Putting it on the channel screen would put twenty rows of
API vocabulary in front of a marketing manager to answer a question they did not
ask.

Access to the container is the control, exactly as it is for ``alembic upgrade``.
There is no PR capability for this and none was invented.

Why it exists at all
---------------------

This one is not a diagnostic for a card that is already blank - there are no
TikTok cards yet. It is the **precondition for building them**. Whether MeoBot
can report a TikTok account's 30-day views depends on whether ``view_count``
comes back on ``video/list`` rows for a real, authorized, production account,
and that is not a question documentation can settle: it depends on the app's
approved scopes and on the account itself. The report this prints is the input
to the next milestone's design.

The tokens never leave the process
-----------------------------------

The stored refresh token is decrypted, exchanged for an access token, and used
as a bearer header. **Neither is ever printed**, written to the JSON output,
logged or included in an error message - there is a test that renders a full
report over a fake TikTok and asserts that neither string appears in the output.

This command writes nothing
----------------------------

No snapshot, no audit row, no change to a connection's health. A probe that
fails must not look like a sync that failed - that is precisely the confusion it
exists to clear up.

**One exception, and it is unavoidable rather than an oversight.** TikTok rotates
the refresh token on every refresh and invalidates the old one, so obtaining an
access token *necessarily* consumes the stored credential. The rotated token is
therefore written back - through the same service method the sync path uses - and
the transaction is committed before any probing begins. Not doing so would leave
the connection holding a refresh token TikTok has already retired, and the next
scheduled sync would fail ``AUTH_REQUIRED`` because somebody ran a read-only
report. Nothing else about the connection is touched: not its health, not its
sync status, not its failure count.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_channel_connection_service import PrChannelConnectionService
from meobot.core.config import Settings, get_settings
from meobot.db.models.pr import PrChannel, PrPlatform
from meobot.db.models.pr_channel_connection import PrChannelConnection
from meobot.db.session import Database
from meobot.domain.pr.channel_connections import PrChannelConnectionState
from meobot.domain.pr.channel_metrics import PrChannelPlatform
from meobot.domain.pr.errors import PrValidationError
from meobot.integrations.tiktok.client import TikTokApiClient
from meobot.integrations.tiktok.constants import TikTokEndpoints
from meobot.integrations.tiktok.errors import TikTokApiError
from meobot.integrations.tiktok.probe import (
    AccountProbeReport,
    FieldProbeResult,
    TikTokCapabilityProbe,
)

#: The connection states worth probing. A ``DISCONNECTED`` channel has no
#: credential at all, and ``PENDING_SELECTION`` is a state TikTok never reaches
#: - consent authorizes exactly one account, so there is nothing to choose.
PROBEABLE = (PrChannelConnectionState.CONNECTED, PrChannelConnectionState.ACTION_REQUIRED)


def _tiktok_client(settings: Settings) -> TikTokApiClient:
    """The same transport the connector builds, from the same settings.

    Built here rather than through ``build_provider`` because the probe needs
    the *client*, not a provider: it asks questions no provider method exposes,
    and going through a provider would mean probing only what the provider
    already knows how to ask for - which is the opposite of the point.
    """
    if not settings.tiktok_connector_enabled:
        raise SystemExit(
            "TikTok is not configured here. Set TIKTOK_CLIENT_KEY, "
            "TIKTOK_CLIENT_SECRET and PR_SECRET_ENCRYPTION_KEY."
        )
    assert settings.tiktok_client_key is not None
    assert settings.tiktok_client_secret is not None
    return TikTokApiClient(
        client_key=settings.tiktok_client_key,
        client_secret=settings.tiktok_client_secret.get_secret_value(),
        redirect_uri=settings.tiktok_redirect_uri,
        endpoints=TikTokEndpoints(),
    )


async def _list(database: Database) -> int:
    """Every TikTok channel, and whether it could be probed.

    Both halves are printed, and that is deliberate: the common case for this
    command in its first week is "I registered the channel and nothing happens",
    where the useful answer is *this channel has no connection yet* rather than
    an empty list that reads as a broken query.
    """
    async with database.transaction() as session:
        rows = (
            await session.execute(
                select(PrChannel, PrChannelConnection, PrPlatform.code)
                .join(PrPlatform, PrPlatform.id == PrChannel.platform_id)
                .outerjoin(
                    PrChannelConnection,
                    (PrChannelConnection.channel_id == PrChannel.id)
                    & (PrChannelConnection.status.in_(PROBEABLE)),
                )
                .where(PrPlatform.code == PrChannelPlatform.TIKTOK.value)
                .order_by(PrChannel.code)
            )
        ).all()

    if not rows:
        print("No PR channel is registered on the TIKTOK platform.")
        return 0
    for channel, connection, _code in rows:
        if connection is None:
            print(f"{channel.code:<16} {'(not connected)':<20} {channel.name}")
            continue
        print(
            f"{channel.code:<16} {connection.status.value:<20} "
            f"open_id={connection.provider_account_id} "
            f"{connection.provider_account_name or ''}"
        )
    return 0


async def _run(database: Database, settings: Settings, reference: str, as_json: bool) -> int:
    """Probe one channel's TikTok account and print what the API would answer.

    The credential is read, exchanged and the rotated one written back inside
    one transaction, which is then **committed** before any probing begins - see
    the module docstring on why a read-only tool has to write exactly once. The
    transaction is closed before the network probing starts, so a dozen TikTok
    requests do not pin a database connection for the length of an investigation.
    """
    async with database.transaction() as session:
        channel = await _find_channel(session, reference)
        if channel is None:
            print(f"No PR channel with code or id {reference!r}.", file=sys.stderr)
            return 1
        connection = (
            await session.execute(
                select(PrChannelConnection).where(
                    PrChannelConnection.channel_id == channel.id,
                    PrChannelConnection.status.in_(PROBEABLE),
                )
            )
        ).scalar_one_or_none()
        if connection is None:
            print(f"{channel.code} has no live platform connection.", file=sys.stderr)
            return 1
        if connection.provider is not PrChannelPlatform.TIKTOK:
            print(
                f"{channel.code} is connected to {connection.provider.value}, "
                "and this probe only understands TikTok accounts.",
                file=sys.stderr,
            )
            return 1

        # The one place a credential is unwrapped, through the service that owns
        # it - rather than by reaching for a ``SecretBox`` here, which would make
        # this the second place in the codebase that knows how a stored
        # credential is unwrapped. ``access_token_for`` also persists TikTok's
        # rotated refresh token, which is why this runs inside the transaction.
        audit = AuditService(session)
        connections = PrChannelConnectionService(
            session, audit, PrCapabilityService(session, audit), settings
        )
        try:
            token = await connections.access_token_for(connection)
        except TikTokApiError as exc:
            print(
                f"Could not obtain an access token: {exc.error_code.value}. "
                "The connection may need to be re-established in the web panel.",
                file=sys.stderr,
            )
            return 1
        except PrValidationError:
            # No usable stored credential - a rotated-away encryption key, or a
            # connection disconnected between the two queries above. A sentence
            # rather than a traceback: the remedy is to reconnect in the panel.
            print(
                f"{channel.code} has no usable stored credential - reconnect it first.",
                file=sys.stderr,
            )
            return 1
        channel_code = channel.code
        scopes = tuple((connection.granted_scopes or "").split())

    client = _tiktok_client(settings)
    try:
        report = await TikTokCapabilityProbe(client).probe_account(
            access_token=token, granted_scopes=scopes
        )
    except TikTokApiError as exc:
        # MeoBot's classification, never TikTok's prose.
        print(
            f"Probe could not start: {exc.error_code.value}. "
            "Nothing was recorded and the connection is unchanged.",
            file=sys.stderr,
        )
        return 1
    finally:
        await client.aclose()

    if as_json:
        print(json.dumps(_as_payload(channel_code, report), indent=2, ensure_ascii=False))
    else:
        print(render(channel_code, report))
    return 0


def _as_payload(channel_code: str, report: AccountProbeReport) -> dict[str, Any]:
    """The report as plain JSON. **No credential, by construction.**"""
    return {
        "channel_code": channel_code,
        "open_id": report.open_id,
        "display_name": report.display_name,
        "granted_scopes": list(report.granted_scopes),
        "videos_read": report.videos_read,
        "pages_walked": report.pages_walked,
        "more_pages_available": report.more_pages_available,
        "available": list(report.available()),
        "unavailable": list(report.unavailable()),
        "results": [
            {
                "field": result.field,
                "kind": result.kind.value,
                "verdict": result.verdict.value,
                "detail": result.detail,
                "sample": result.sample,
            }
            for result in report.results
        ],
        "video_refusal": (
            None
            if report.video_refusal is None
            else {
                "verdict": report.video_refusal.verdict.value,
                "detail": report.video_refusal.detail,
            }
        ),
    }


def render(channel_code: str, report: AccountProbeReport) -> str:
    """The human report, as one string.

    Returned rather than printed so a test can assert on it - specifically, that
    no access token and no refresh token appear anywhere in it.
    """
    lines = [
        f"Channel : {channel_code}",
        f"TikTok  : {report.display_name or '(no display name)'}",
        f"Open ID : {report.open_id}",
        f"Scopes  : {', '.join(report.granted_scopes) or '(none recorded)'}",
        "",
    ]
    for title, results in (
        ("USER INFO", report.identity),
        ("USER STATS", report.stats),
        ("VIDEO FIELDS", report.videos),
    ):
        if not results:
            continue
        lines.append(title)
        lines.extend(_row(result) for result in results)
        lines.append("")

    if report.video_refusal is not None:
        lines.append("VIDEO FIELDS")
        lines.append(f"  {report.video_refusal.verdict.value:<14} video/list could not be read")
        if report.video_refusal.detail:
            lines.append(f"                 {report.video_refusal.detail}")
        lines.append("")
    else:
        read = "not read" if report.videos_read is None else f"{report.videos_read} read"
        lines.append(f"VIDEOS   : {read}")
        lines.append(
            f"PAGING   : {report.pages_walked} page(s) walked, "
            f"{'more available' if report.more_pages_available else 'end of list reached'}"
        )
        lines.append("")

    lines.append(f"Usable now : {', '.join(report.available()) or '(none)'}")
    lines.append(f"Not usable : {', '.join(report.unavailable()) or '(none)'}")
    limitations = report.limitations()
    lines.append("")
    lines.append("Permission / API limitations:")
    if not limitations:
        lines.append("  (none - every field asked for was served)")
    else:
        lines.extend(
            f"  {result.verdict.value:<14} {result.field:<24}{result.detail or ''}"
            for result in limitations
        )
    return "\n".join(lines)


def _row(result: FieldProbeResult) -> str:
    detail = f"  {result.sample or result.detail or ''}".rstrip()
    return f"  {result.verdict.value:<14} {result.field:<24}{detail}"


async def _find_channel(session: AsyncSession, reference: str) -> PrChannel | None:
    """A channel by its code, or by its id when the reference parses as one."""
    row = (
        await session.execute(select(PrChannel).where(PrChannel.code == reference.strip().upper()))
    ).scalar_one_or_none()
    if row is not None:
        return row
    try:
        channel_id = uuid.UUID(reference)
    except ValueError:
        return None
    return await session.get(PrChannel, channel_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meobot-tiktok-probe",
        description=(
            "Ask a connected TikTok account which Display API fields it will answer. "
            "Read-only apart from the refresh-token rotation TikTok forces, and it "
            "never prints a token."
        ),
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="TikTok channels and their connection status")
    run = sub.add_parser("run", help="probe one channel's TikTok account")
    run.add_argument("channel", help="PR channel code, or its uuid")
    run.add_argument(
        "--json",
        action="store_true",
        help="emit the report as JSON instead of a table",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    database = Database(settings)

    async def run() -> int:
        try:
            if args.action == "list":
                return await _list(database)
            return await _run(database, settings, args.channel, args.json)
        finally:
            await database.dispose()

    return asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
