"""Wipe all task data (orders + PR content) from a DEV/LOCAL database. Users are kept.

Clears:
  - ADS: orders, nodes, events, submissions, approvals, code counters, work rules, token ledger
  - PR: content items, tasks, versions, comments, transitions, approvals, resources,
        derivatives, destinations, production submissions, AI reviews, script reviews/approvals,
        work results, content work rules/projections, code counters
  - Shared: unified tasks table, task-related notifications

    docker compose exec -T api python - < scripts/wipe_task_data.py            # dry run
    docker compose exec -T api python - --apply < scripts/wipe_task_data.py    # for real
"""

# Table/column names are string literals, never from input.
# ruff: noqa: S608

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from meobot.core.config import get_settings
from meobot.db.session import Database

# Order: children first, parents last. Foreign keys cascade where possible,
# but explicit ordering avoids FK violations on tables without CASCADE.
CLEAR_STATEMENTS = (
    # --- notifications ---
    "delete from user_notifications where target_kind in ('order', 'pr_content')",
    # --- ADS (orders) ---
    "delete from pr_work_results where source_type = 'ORDER'",
    "delete from order_token_ledger",
    "delete from order_work_rules",
    "delete from order_approvals",
    "delete from order_events",
    "delete from order_submissions",
    "delete from order_nodes",
    "delete from orders",
    "delete from order_code_counters",
    # --- PR content children ---
    "delete from script_approvals",
    "delete from script_reviews",
    "delete from pr_ai_reviews",
    "delete from pr_ai_review_runs",
    "delete from pr_production_submissions",
    "delete from pr_content_transition_events",
    "delete from pr_content_comments",
    "delete from pr_content_versions",
    "delete from pr_content_resources",
    "delete from pr_content_destinations",
    "delete from pr_content_derivatives",
    "delete from pr_content_work_projections",
    "delete from pr_content_work_rules",
    "delete from pr_approval_events",
    "delete from pr_task_assignments",
    "delete from pr_tasks",
    "delete from pr_content_targets",
    "delete from pr_work_results where source_type = 'PR'",
    "delete from pr_content_items",
    "delete from pr_code_counters",
    # --- unified tasks ---
    "delete from tasks",
)


async def main(apply: bool) -> None:
    settings = get_settings()
    db = Database(settings)
    deleted: dict[str, int] = {}
    async with db.engine.connect() as conn:
        transaction = await conn.begin()
        url = conn.engine.url
        print(f"database: {url.host}/{url.database}  app_env={settings.app_env}")

        for statement in CLEAR_STATEMENTS:
            table = statement.split("from")[1].strip().split()[0]
            result = await conn.execute(text(statement))
            deleted[table] = deleted.get(table, 0) + result.rowcount

        users = (
            await conn.execute(
                text("select count(*) from users")
            )
        ).scalar()

        if apply:
            await transaction.commit()
        else:
            await transaction.rollback()

    for table, count in sorted(deleted.items(), key=lambda item: -item[1]):
        if count:
            print(f"deleted {count:>7}  {table}")
    print(f"users untouched: {users}")
    print("COMMITTED" if apply else "DRY RUN - rolled back, nothing changed (add --apply)")
    await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv[1:]))
