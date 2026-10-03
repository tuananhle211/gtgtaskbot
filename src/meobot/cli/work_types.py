"""Operator command: put the starting work taxonomy into an empty deployment.

M2.5.

```
meobot-work-types list                  what the department currently has
meobot-work-types bootstrap             create the V1 taxonomy, idempotently
meobot-work-types bootstrap --dry-run   what it would create, writing nothing
```

Why a command and not a migration
----------------------------------

Work types are **business data**. The owner renames them, moves them between
categories and retires them, all without a deploy. A migration that inserted
them would put mutable rows under schema control: the next ``compare_metadata``
sweep would read the department's rename as drift, and re-running the migration
after that rename would either fail on the unique code or quietly restore a name
somebody deliberately changed.

Why a command *as well as* a button
------------------------------------

``POST /api/pr/work/types/bootstrap`` exists and is the ordinary route - an
owner sets the department up from MeoChat without anybody's help, which is the
whole point of M2.5. This command is for the case that route cannot cover: a
deployment where nobody can reach the screen yet. It is the same service method
behind both, so neither can drift from the other.

Idempotent, and only additive
------------------------------

Matching is on ``code``, the one field a rename does not touch. A second run
creates nothing. A type the owner has renamed is left with its new name; a type
the owner has **deactivated is left deactivated**, because a command that
resurrected retired types every time it ran would be a way to undo a decision
rather than a way to seed a database.

Who it acts as
---------------

The configured owner, exactly as scheduled tasks do - see
:meth:`~meobot.tasks.runtime.TaskContext.system_actor`. The audit row therefore
names a responsible account rather than "nobody", and the capability check is
the real one rather than a bypass: access to the container is the control, as it
already is for ``alembic upgrade``.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections.abc import Sequence

from meobot.application.pr_services import build_pr_services
from meobot.core.config import get_settings
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.work_types import BOOTSTRAP_WORK_TYPES


def _operator(telegram_owner_id: int | None) -> Actor:
    """The owner the run is attributed to. See the module docstring."""
    return Actor(
        user_id=None,
        telegram_user_id=telegram_owner_id,
        telegram_username=None,
        full_name="meobot-work-types",
        role=Role.OWNER,
        active=True,
        is_bootstrap_owner=True,
    )


async def _list(database: Database) -> int:
    settings = get_settings()
    async with database.transaction() as session:
        services = build_pr_services(session, settings)
        rows = await services.work.list_work_types(
            actor=_operator(settings.meobot_owner_telegram_id), include_inactive=True
        )
        for row in rows:
            flag = "on " if row.is_active else "off"
            print(
                f"{flag} {row.code:<20} {row.category.value:<13} "
                f"{row.default_quota_basis.value:<11} {row.default_unit.value:<8} {row.name}"
            )
        if not rows:
            print("No work types. Run: meobot-work-types bootstrap")
    return 0


async def _bootstrap(database: Database, *, dry_run: bool) -> int:
    settings = get_settings()
    if dry_run:
        # Read-only, and deliberately not a transaction that is rolled back: a
        # dry run that opened a write path would be one refactor away from not
        # being a dry run.
        async with database.transaction() as session:
            services = build_pr_services(session, settings)
            existing = {
                row.code
                for row in await services.work.list_work_types(
                    actor=_operator(settings.meobot_owner_telegram_id), include_inactive=True
                )
            }
        missing = [spec for spec in BOOTSTRAP_WORK_TYPES if spec.code not in existing]
        for spec in missing:
            print(f"would create {spec.code:<20} {spec.name}")
        present = len(BOOTSTRAP_WORK_TYPES) - len(missing)
        print(f"\n{len(missing)} to create, {present} already present.")
        return 0

    async with database.transaction() as session:
        services = build_pr_services(session, settings)
        created = await services.work.bootstrap_work_types(
            actor=_operator(settings.meobot_owner_telegram_id), request_id=uuid.uuid4()
        )
        codes = [row.code for row in created]
    for code in codes:
        print(f"created {code}")
    print(
        f"\n{len(codes)} created." if codes else "\nNothing to do - the taxonomy is already set up."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meobot-work-types", description="Manage the work-type taxonomy."
    )
    sub = parser.add_subparsers(dest="group", required=True)
    sub.add_parser("list", help="Show every work type, active and retired.")
    boot = sub.add_parser("bootstrap", help="Create the V1 taxonomy. Safe to run twice.")
    boot.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created and write nothing.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = Database(get_settings())

    async def run() -> int:
        try:
            if args.group == "list":
                return await _list(database)
            if args.group == "bootstrap":
                return await _bootstrap(database, dry_run=args.dry_run)
            return 1
        finally:
            await database.dispose()

    return asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
