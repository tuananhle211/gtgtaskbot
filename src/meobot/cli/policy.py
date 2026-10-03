"""Operator commands for official platform policy.

Step 1F.1. Activation is the moment production policy changes, and it is
deliberately something a person does on purpose - not a consequence of a
scheduled fetch, and not a button in a web console nobody audits. So it lives
here, behind a shell on the host, where the operator already has to be to run a
migration.

```
meobot-policy sources seed        register the official pages from the manifest
meobot-policy sources list        what is registered, and how it is ingested
meobot-policy refresh             re-read them; snapshot only what changed
meobot-policy snapshots list      what has been captured
meobot-policy packs build         compose a DRAFT from the newest snapshots
meobot-policy packs list          every version, and which is ACTIVE
meobot-policy packs activate      make a DRAFT the one new reviews use
meobot-policy import              operator-supplied text, for a source that
                                  cannot be fetched
```

Authorization is the shell
--------------------------

There is no PR capability for this and none was invented. "Who may change the
policy every review is judged against" is an operations question, not a content
one, and the existing capabilities describe content work - stretching one to
cover this would have put an unrelated grant in charge of production policy.
Access to the container is the control, exactly as it is for ``alembic upgrade``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from meobot.application.pr_policy_pack_service import PrPolicyPackError, PrPolicyPackService
from meobot.application.pr_policy_source_service import PrPolicySourceService
from meobot.core.config import get_settings
from meobot.db.session import Database
from meobot.domain.pr.models import PrDistributionMode, PrPolicyPackStatus
from meobot.integrations.platform_policy.fetcher import PolicySourceFetcher


def _mode(raw: str) -> PrDistributionMode:
    try:
        return PrDistributionMode(raw.upper())
    except ValueError:
        raise SystemExit(f"Unknown distribution mode {raw!r}. Use ORGANIC or PAID_AD.") from None


async def _sources_seed(database: Database) -> int:
    async with database.transaction() as session:
        added = await PrPolicySourceService(session).seed()
    print(f"Registered {added} new policy source(s).")
    return 0


async def _sources_list(database: Database) -> int:
    async with database.transaction() as session:
        sources = await PrPolicySourceService(session).list_sources()
        for source in sources:
            flag = "on " if source.enabled else "off"
            print(
                f"{flag} {source.platform_code:<9} {source.policy_scope.value:<22} "
                f"{source.source_role.value:<16} {source.source_family:<34} "
                f"{source.ingestion_method.value:<16} {source.canonical_url}"
            )
        if not sources:
            print("No policy sources registered. Run: meobot-policy sources seed")
    return 0


async def _refresh(database: Database, family: str | None) -> int:
    """Re-read official pages. Never changes an ACTIVE pack."""
    async with database.transaction() as session:
        service = PrPolicySourceService(session, PolicySourceFetcher())
        if family:
            source = await service.source_by_family(family)
            if source is None:
                print(f"No source with family {family!r}.", file=sys.stderr)
                return 1
            outcomes = [await service.refresh_source(source)]
        else:
            outcomes = list(await service.refresh_all())

    for outcome in outcomes:
        if outcome.error_code:
            state = f"FAILED ({outcome.error_code})"
        elif outcome.changed:
            state = "CHANGED - new snapshot"
        elif outcome.discovered_urls:
            state = f"index - discovered {len(outcome.discovered_urls)} sub-page(s)"
        else:
            state = outcome.skipped_reason or "unchanged"
        print(f"{outcome.source_family:<34} {state}")
        for url in outcome.discovered_urls:
            print(f"    {url}")
    print("\nACTIVE packs are unchanged. Build and activate to use new snapshots.")
    return 0


async def _snapshots_list(database: Database, limit: int) -> int:
    async with database.transaction() as session:
        service = PrPolicySourceService(session)
        families = {source.id: source.source_family for source in await service.list_sources()}
        for snapshot in await service.list_snapshots(limit=limit):
            print(
                f"{snapshot.fetched_at:%Y-%m-%d %H:%M}  "
                f"{families.get(snapshot.source_id, '?'):<34} "
                f"{snapshot.content_sha256[:12]}  "
                f"{len(snapshot.normalized_content):>8} chars  "
                f"{snapshot.ingestion_method.value}"
            )
    return 0


async def _packs_build(database: Database, platform: str, mode: PrDistributionMode) -> int:
    async with database.transaction() as session:
        try:
            pack = await PrPolicyPackService(session).build_draft(
                platform_code=platform.upper(), distribution_mode=mode
            )
        except PrPolicyPackError as exc:
            print(f"Cannot build: {exc.message}", file=sys.stderr)
            print(f"  details: {exc.details}", file=sys.stderr)
            return 1
        rules = await PrPolicyPackService(session).rules_for(pack.id)
    print(f"Built DRAFT {pack.label} with {len(rules)} rule(s).")
    print(f"Activate with: meobot-policy packs activate {platform.upper()} {mode.value}")
    return 0


async def _packs_list(database: Database) -> int:
    async with database.transaction() as session:
        service = PrPolicyPackService(session)
        packs = await service.list_packs()
        for pack in packs:
            rules = len(await service.rules_for(pack.id))
            marker = "*" if pack.status is PrPolicyPackStatus.ACTIVE else " "
            print(
                f"{marker} {pack.label:<38} v{pack.version:<3} {pack.status.value:<9} "
                f"{rules:>4} rules  {pack.manifest_hash[:12]}"
            )
        if not packs:
            print("No policy packs. Run: meobot-policy packs build <PLATFORM> <MODE>")
        else:
            print("\n* = ACTIVE (used by new AI review runs)")
    return 0


async def _packs_activate(database: Database, platform: str, mode: PrDistributionMode) -> int:
    """Promote the newest DRAFT. Retires the previous ACTIVE in one transaction."""
    async with database.transaction() as session:
        service = PrPolicyPackService(session)
        drafts = [
            pack
            for pack in await service.list_packs(platform_code=platform.upper())
            if pack.distribution_mode is mode and pack.status is PrPolicyPackStatus.DRAFT
        ]
        if not drafts:
            print(
                f"No DRAFT pack for {platform.upper()} {mode.value}. Build one first.",
                file=sys.stderr,
            )
            return 1
        newest = max(drafts, key=lambda pack: pack.version)
        try:
            activated = await service.activate(newest)
        except PrPolicyPackError as exc:
            print(f"Cannot activate: {exc.message}", file=sys.stderr)
            return 1
    print(f"ACTIVE: {activated.label}")
    print("Existing queued/running reviews keep the pack they pinned.")
    return 0


async def _import(database: Database, family: str, path: str) -> int:
    """Store operator-supplied text for a source that cannot be fetched.

    The file is read before the transaction opens: blocking IO inside a
    coroutine would stall the event loop, and there is no reason to hold a
    database transaction open across a disk read.
    """
    text = Path(path).read_text(encoding="utf-8")
    async with database.transaction() as session:
        service = PrPolicySourceService(session)
        source = await service.source_by_family(family)
        if source is None:
            print(f"No source with family {family!r}.", file=sys.stderr)
            return 1
        outcome = await service.import_snapshot(source, text=text)
    if outcome.error_code:
        print(f"Rejected: {outcome.error_code}", file=sys.stderr)
        return 1
    print("Unchanged - no snapshot created." if not outcome.changed else "New snapshot stored.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meobot-policy",
        description="Manage official platform policy sources, snapshots and packs.",
    )
    sub = parser.add_subparsers(dest="group", required=True)

    sources = sub.add_parser("sources", help="the official page registry").add_subparsers(
        dest="action", required=True
    )
    sources.add_parser("seed", help="register the manifest's official pages")
    sources.add_parser("list", help="show registered sources")

    refresh = sub.add_parser("refresh", help="re-read official pages (changes no ACTIVE pack)")
    refresh.add_argument("--family", help="one source family; default all enabled")

    snapshots = sub.add_parser("snapshots", help="captured policy text").add_subparsers(
        dest="action", required=True
    )
    listing = snapshots.add_parser("list", help="show recent snapshots")
    listing.add_argument("--limit", type=int, default=30)

    packs = sub.add_parser("packs", help="versioned policy packs").add_subparsers(
        dest="action", required=True
    )
    build = packs.add_parser("build", help="compose a DRAFT from the newest snapshots")
    build.add_argument("platform", help="FACEBOOK or TIKTOK")
    build.add_argument("mode", help="ORGANIC or PAID_AD")
    packs.add_parser("list", help="show every pack version")
    activate = packs.add_parser("activate", help="make the newest DRAFT active")
    activate.add_argument("platform")
    activate.add_argument("mode")

    importer = sub.add_parser("import", help="store operator-supplied policy text")
    importer.add_argument("family", help="source family to import into")
    importer.add_argument("file", help="UTF-8 text file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = Database(get_settings())

    async def run() -> int:
        try:
            if args.group == "sources":
                return await (
                    _sources_seed(database) if args.action == "seed" else _sources_list(database)
                )
            if args.group == "refresh":
                return await _refresh(database, args.family)
            if args.group == "snapshots":
                return await _snapshots_list(database, args.limit)
            if args.group == "packs":
                if args.action == "build":
                    return await _packs_build(database, args.platform, _mode(args.mode))
                if args.action == "activate":
                    return await _packs_activate(database, args.platform, _mode(args.mode))
                return await _packs_list(database)
            if args.group == "import":
                return await _import(database, args.family, args.file)
            return 1
        finally:
            await database.dispose()

    return asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
