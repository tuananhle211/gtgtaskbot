"""Wipe a DEV/LOCAL database back to the test state. Never run against production.

1. Deletes every ORD (ADS) order and everything hanging off it (nodes, events, submissions,
   approvals, the unified ``tasks`` rows, ORD KPI results, order notifications, code counters).
2. Hard-deletes every user except OWNER accounts and the test logins of
   ``scripts/seed_test_logins.py`` (ids below), together with their data: nullable
   references from surviving rows are set to NULL, NOT NULL references delete the referencing
   row (recursively).

Everything runs in ONE transaction. Without ``--apply`` it is a dry run: it does all the work,
prints the counts, then rolls back.

    docker compose exec -T api python - < scripts/reset_dev_data.py            # dry run
    docker compose exec -T api python - --apply < scripts/reset_dev_data.py    # for real

Then (re)create the test logins:

    docker compose exec -T api python - < scripts/seed_test_logins.py
"""

# Table/column names come from information_schema, never from input.
# ruff: noqa: S608

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from meobot.core.config import get_settings
from meobot.db.session import Database

# Keep in sync with ACCOUNTS in scripts/seed_test_logins.py.
KEEP_TELEGRAM_IDS = (1000, 1100, 1101, 1200, 1201, 1210, 1211, 1220, 1221, 1230, 1231, 1300, 1400)

CLEAR_ORDERS = (
    "delete from pr_work_results where source_type = 'ORDER'",
    "delete from user_notifications where target_kind = 'order'",
    "delete from order_approvals",
    "delete from order_events",
    "delete from order_submissions",
    "delete from order_nodes",
    "delete from orders",  # the ORDER rows of ``tasks`` cascade
    "delete from order_code_counters",
)

FK_SQL = """
select kcu.table_name, kcu.column_name, c.is_nullable = 'YES', ccu.table_name, ccu.column_name
from information_schema.referential_constraints rc
join information_schema.key_column_usage kcu on kcu.constraint_name = rc.constraint_name
join information_schema.constraint_column_usage ccu on ccu.constraint_name = rc.constraint_name
join information_schema.columns c
  on c.table_name = kcu.table_name and c.column_name = kcu.column_name
where kcu.table_schema = 'public'
"""
PK_SQL = """
select tc.table_name, kcu.column_name from information_schema.table_constraints tc
join information_schema.key_column_usage kcu on kcu.constraint_name = tc.constraint_name
where tc.constraint_type = 'PRIMARY KEY' and tc.table_schema = 'public'
"""


async def main(apply: bool) -> None:
    settings = get_settings()
    db = Database(settings)
    deleted: dict[str, int] = {}
    nulled: dict[str, int] = {}
    async with db.engine.connect() as conn:
        transaction = await conn.begin()
        url = conn.engine.url
        print(f"database: {url.host}/{url.database}  app_env={settings.app_env}")

        for statement in CLEAR_ORDERS:
            result = await conn.execute(text(statement))
            table = statement.split()[2]
            deleted[table] = deleted.get(table, 0) + result.rowcount

        fks = (await conn.execute(text(FK_SQL))).all()
        pks: dict[str, list[str]] = {}
        for table, column in (await conn.execute(text(PK_SQL))).all():
            pks.setdefault(table, []).append(column)
        refs: dict[tuple[str, str], list[tuple[str, str, bool]]] = {}
        for table, column, nullable, ref_table, ref_column in fks:
            refs.setdefault((ref_table, ref_column), []).append((table, column, nullable))

        async def purge(table: str, column: str, values: list, depth: int = 0) -> None:
            if not values or depth > 25:
                return
            for (ref_table, ref_column), children in refs.items():
                if ref_table != table:
                    continue
                parents = (
                    values
                    if ref_column == column
                    else [
                        row[0]
                        for row in (
                            await conn.execute(
                                text(
                                    f'select "{ref_column}" from "{table}" '
                                    f'where "{column}" = any(:v)'
                                ),
                                {"v": values},
                            )
                        ).all()
                    ]
                )
                if not parents:
                    continue
                for child, child_column, nullable in children:
                    if child == table and child_column == column:
                        continue
                    if nullable:
                        savepoint = await conn.begin_nested()
                        try:
                            result = await conn.execute(
                                text(
                                    f'update "{child}" set "{child_column}" = null '
                                    f'where "{child_column}" = any(:v)'
                                ),
                                {"v": parents},
                            )
                            await savepoint.commit()
                            if result.rowcount:
                                key = f"{child}.{child_column}"
                                nulled[key] = nulled.get(key, 0) + result.rowcount
                            continue
                        except Exception:  # a check constraint forbids NULL here
                            await savepoint.rollback()
                    key_columns = pks.get(child, [])
                    if len(key_columns) == 1:
                        ids = [
                            row[0]
                            for row in (
                                await conn.execute(
                                    text(
                                        f'select "{key_columns[0]}" from "{child}" '
                                        f'where "{child_column}" = any(:v)'
                                    ),
                                    {"v": parents},
                                )
                            ).all()
                        ]
                        await purge(child, key_columns[0], ids, depth + 1)
                    result = await conn.execute(
                        text(f'delete from "{child}" where "{child_column}" = any(:v)'),
                        {"v": parents},
                    )
                    if result.rowcount:
                        deleted[child] = deleted.get(child, 0) + result.rowcount

        doomed = [
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "select id from users where role <> 'OWNER' "
                        "and coalesce(telegram_user_id, 0) <> all(:keep)"
                    ),
                    {"keep": list(KEEP_TELEGRAM_IDS)},
                )
            ).all()
        ]
        print("users to delete:", len(doomed))
        await purge("users", "id", doomed)
        result = await conn.execute(text("delete from users where id = any(:v)"), {"v": doomed})
        deleted["users"] = result.rowcount

        kept = (
            await conn.execute(text("select role, count(*) from users group by role order by 1"))
        ).all()

        if apply:
            await transaction.commit()
        else:
            await transaction.rollback()

    for table, count in sorted(deleted.items(), key=lambda item: -item[1])[:30]:
        print(f"deleted {count:>7}  {table}")
    print("set to null:", sum(nulled.values()), "references in", len(nulled), "columns")
    print("users left:", ", ".join(f"{role}={count}" for role, count in kept))
    print("COMMITTED" if apply else "DRY RUN - rolled back, nothing changed (add --apply)")
    await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv[1:]))
