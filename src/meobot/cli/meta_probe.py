"""Operator command: ask a connected Facebook Page what Graph will answer.

Step 1F.2.4d.

```
meobot-meta-probe list                 which channels have a live Meta connection
meobot-meta-probe run APEX-FB          probe that channel's Page, one metric at a time
meobot-meta-probe run APEX-FB --json   the same report, machine-readable
```

Why this is a shell command and not a button
---------------------------------------------

For the reason ``meobot-policy`` gives, and one more. A probe is an
*investigation*: somebody is asking why a card is blank, and the answer is a
table of metric names that means nothing to anybody outside engineering. Putting
it on the channel screen would put nine rows of Graph vocabulary in front of a
marketing manager to answer a question they did not ask.

Access to the container is the control, exactly as it is for ``alembic upgrade``.
There is no PR capability for this and none was invented - the same reasoning
that declined to invent ``PR_PLATFORM_MANAGE``.

The token never leaves the process
-----------------------------------

The credential is decrypted, handed to the probe, and used as a bearer header.
It is **never printed**, never written to the JSON output, never logged and
never included in an error message - there is a test that renders a full report
over a fake Graph and asserts the token string does not appear in the output.

The one visible trace is the *number of characters* in nothing at all: the tool
prints the Page's name and id, which came back from Graph, and never the
credential that reached it.

This command writes nothing
----------------------------

No snapshot, no audit row, no change to a connection's health. A probe that
fails must not look like a sync that failed - that is precisely the confusion it
exists to clear up. The database session is used to read one channel and one
connection, and is rolled back.
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
from meobot.core.time import utcnow
from meobot.db.models.pr import PrChannel, PrPlatform
from meobot.db.models.pr_channel_connection import PrChannelConnection
from meobot.db.session import Database
from meobot.domain.pr.channel_connections import PrChannelConnectionState
from meobot.domain.pr.channel_metrics import PrChannelPlatform
from meobot.integrations.meta.client import MetaGraphClient
from meobot.integrations.meta.constants import MetaEndpoints
from meobot.integrations.meta.errors import MetaApiError
from meobot.integrations.meta.probe import (
    MetaCapabilityProbe,
    MetricProbeResult,
    PageProbeReport,
)

#: The connection states worth probing. A ``DISCONNECTED`` channel has no
#: credential, and a ``PENDING_SELECTION`` one holds a *user* token that is not
#: the Page token production syncs with - probing it would answer a question
#: about a credential MeoBot does not use.
PROBEABLE = (PrChannelConnectionState.CONNECTED, PrChannelConnectionState.ACTION_REQUIRED)


def _meta_client(settings: Settings) -> MetaGraphClient:
    """The same transport the connector builds, from the same settings.

    Built here rather than through ``build_provider`` because the probe needs
    the *client*, not a provider: it asks questions no provider method exposes,
    and going through a provider would mean probing only what the provider
    already knows how to ask for - which is the opposite of the point.
    """
    if not settings.meta_connector_enabled:
        raise SystemExit(
            "Meta is not configured here. Set META_APP_ID, META_APP_SECRET "
            "and PR_SECRET_ENCRYPTION_KEY."
        )
    assert settings.meta_app_id is not None
    assert settings.meta_app_secret is not None
    return MetaGraphClient(
        app_id=settings.meta_app_id,
        app_secret=settings.meta_app_secret.get_secret_value(),
        redirect_uri=settings.meta_redirect_uri,
        endpoints=MetaEndpoints(version=settings.meta_graph_api_version),
    )


async def _list(database: Database) -> int:
    """Every channel a probe could be run against, and what state it is in."""
    async with database.transaction() as session:
        rows = (
            await session.execute(
                select(PrChannel, PrChannelConnection, PrPlatform.code)
                .join(PrChannelConnection, PrChannelConnection.channel_id == PrChannel.id)
                .join(PrPlatform, PrPlatform.id == PrChannel.platform_id)
                .where(PrChannelConnection.status.in_(PROBEABLE))
                .order_by(PrChannel.code)
            )
        ).all()

    printed = 0
    for channel, connection, platform_code in rows:
        if connection.provider is not PrChannelPlatform.FACEBOOK:
            continue
        printed += 1
        print(
            f"{channel.code:<16} {platform_code:<10} {connection.status.value:<18} "
            f"page={connection.provider_account_id} "
            f"{connection.provider_account_name or ''}"
        )
    if not printed:
        print("No connected Facebook channel to probe.")
    return 0


async def _run(database: Database, settings: Settings, reference: str, as_json: bool) -> int:
    """Probe one channel's Page and print what Graph would answer.

    The credential is read inside the transaction and the transaction is closed
    before the network calls begin - holding a database transaction open across
    a dozen Graph requests would pin a connection for the length of an
    investigation for no benefit.
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
        if connection.provider is not PrChannelPlatform.FACEBOOK:
            print(
                f"{channel.code} is connected to {connection.provider.value}, "
                "and this probe only understands Facebook Pages.",
                file=sys.stderr,
            )
            return 1
        # The one decryption, through the service that owns it - rather than by
        # reaching for a ``SecretBox`` here, which would make this the second
        # place in the codebase that knows how a stored credential is unwrapped.
        # The plaintext lives in one local for the length of the probe.
        audit = AuditService(session)
        token = PrChannelConnectionService(
            session,
            audit,
            PrCapabilityService(session, audit),
            settings,
        ).read_credential(connection)
        channel_code = channel.code

    if token is None:
        print(
            f"{channel_code} has no usable stored credential - reconnect it first.",
            file=sys.stderr,
        )
        return 1

    client = _meta_client(settings)
    try:
        report = await MetaCapabilityProbe(client).probe_page(access_token=token, now=utcnow())
    except MetaApiError as exc:
        # MeoBot's classification, never Meta's prose.
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


def _as_payload(channel_code: str, report: PageProbeReport) -> dict[str, Any]:
    """The report as plain JSON. **No credential, by construction.**"""
    return {
        "channel_code": channel_code,
        "page_id": report.page_id,
        "page_name": report.page_name,
        "window": {"start": report.window.start_iso, "end": report.window.end_iso},
        "posts_read": report.posts_read,
        "available": list(report.available()),
        "unavailable": list(report.unavailable()),
        "results": [
            {
                "metric": result.metric,
                "kind": result.kind.value,
                "verdict": result.verdict.value,
                "detail": result.detail,
                "sample": result.sample,
            }
            for result in report.results
        ],
    }


def render(channel_code: str, report: PageProbeReport) -> str:
    """The human report, as one string.

    Returned rather than printed so a test can assert on it - specifically, that
    no access token appears anywhere in it.
    """
    lines = [
        f"Channel : {channel_code}",
        f"Page    : {report.page_name or '(no name)'} ({report.page_id})",
        f"Window  : {report.window.start_iso} .. {report.window.end_iso}",
        f"Posts   : {'not listed' if report.posts_read is None else report.posts_read} read",
        "",
    ]
    for title, results in (
        ("PAGE FIELDS", report.fields),
        ("PAGE INSIGHTS", report.insights),
        ("POST FIELDS", report.posts),
    ):
        if not results:
            continue
        lines.append(title)
        lines.extend(_row(result) for result in results)
        lines.append("")
    lines.append(f"Usable now : {', '.join(report.available()) or '(none)'}")
    lines.append(f"Not usable : {', '.join(report.unavailable()) or '(none)'}")
    return "\n".join(lines)


def _row(result: MetricProbeResult) -> str:
    detail = f"  {result.sample or result.detail or ''}".rstrip()
    return f"  {result.verdict.value:<14} {result.metric:<44}{detail}"


async def _find_channel(session: AsyncSession, reference: str) -> PrChannel | None:
    """A channel by its code, or by its id when the reference parses as one.

    Code first, because that is what an operator has in front of them. The id
    path exists for the case where two channels share a code across a rename.
    """
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
        prog="meobot-meta-probe",
        description=(
            "Ask a connected Facebook Page which Graph metrics it will answer. "
            "Read-only: records nothing and never prints a token."
        ),
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="channels with a live Facebook connection")
    run = sub.add_parser("run", help="probe one channel's Page")
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
