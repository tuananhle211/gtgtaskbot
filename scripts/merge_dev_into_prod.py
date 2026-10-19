"""One-off: merge the DEV database's ORD data into the PRODUCTION database.

Production holds the PR history; dev holds everything built for ORD (people's
tags, the unit's settings and catalogues, the orders). Both must be at the
same alembic head. The two databases live on the same Postgres server: the
dev dump is restored next to the target first (``pg_restore -d <source>``).

What moves, by rule:

* **people** are matched by Telegram id. Somebody on both sides keeps the
  production account (id, name, history) and takes from dev their password,
  their role (an OWNER is never demoted) and their picture; somebody only on
  dev is created. The dev test logins (Telegram ids 1000-1400) are left behind
  with everything of theirs. A created account has to /start the production
  bot once before it gets Telegram notifications (the web login works at once);
* **ORD's setup** comes from dev as it is: the unit's settings (permission
  matrix, effort, reviews), video kinds, platforms, durations;
* **tags**: for everybody who is on dev, dev says which streams they are in
  (ORD role, Leader, own Leader, member code, daily tokens - and whether they
  are still in PR). People only on production keep their tags;
* **orders** placed on dev, with their steps, hand-ins, history, approvals,
  tokens, counters and unified ``tasks`` rows - every person in them renamed
  to the production account. An order that needs a left-behind test account
  is skipped and listed;
* **open invite codes**, and the bell's notifications about the moved orders.

The same script serves the other way round ("PR data arrives later"): the
source is then the running ORD database and the target a freshly upgraded copy
of the PR database - see ``docs/ord/PROD_CUTOVER_RUNBOOK.md``.

* since 0061-0068 (2026-10-19): the **departments** - every source department
  (matched by code, created when new) with its grants and its open seats (the
  source says where everybody on the source sits, except in **PR Thương Hiệu**,
  whose seats and tags come from the PR side only: dev / production carried
  PR tags that 0048 handed out, not the PR team's roster); **personal grants**
  (``user_permissions``), the **AI connections and routes**, the **resource
  catalogue** (``resource_options``, only while the target holds no
  resource), and for people on both sides their member code and employment
  status. Resources themselves are counted, never carried.

PR content, PR tasks, audit and everything else of production is not touched.

Everything runs in ONE transaction on the target. Without ``--apply`` it is a
dry run: it does all the work, prints the report, then rolls back.

    docker compose run --rm -T -e MERGE_SOURCE_DB=dev_import api \\
        python - [--apply] [--orders=all|live|none] [--same-bot] \\
        < scripts/merge_dev_into_prod.py

``--orders``: ``all`` (default), ``live`` (not the cancelled ones) or ``none``
(people, tags and ORD's setup only). ``--same-bot``: source and target are
served by the same Telegram bot, so who the bot can reach is kept (without it
a created account has to /start the target's bot first). Running it twice is
harmless: orders already there are skipped by their code.

``MERGE_TARGET_DB`` defaults to the database in ``DATABASE_URL``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg

TEST_LOGINS = (1000, 1400)
#: System roles, lowest first: somebody on both sides keeps the higher one.
ROLE_RANK = {"EMPLOYEE": 0, "TEAM_LEAD": 1, "ADMIN": 2, "OWNER": 3}
#: ``--orders=``: every order, all but the cancelled ones, or people and setup only.
ORDER_CHOICES = ("all", "live", "none")
#: Order tables below ``orders``, parents first, with the column tying a row
#: to its order (``None``: through ``order_nodes``).
ORDER_CHILDREN: tuple[tuple[str, str | None], ...] = (
    ("order_nodes", "order_id"),
    ("order_submissions", None),
    ("order_events", "order_id"),
    ("order_approvals", "order_id"),
    ("order_token_ledger", "order_id"),
)


def dsn_for(database: str | None) -> str:
    parts = urlsplit(os.environ["DATABASE_URL"])
    path = parts.path if database is None else f"/{database}"
    return urlunsplit(("postgresql", parts.netloc, path, "", ""))


async def columns(conn: asyncpg.Connection, table: str) -> list[str]:
    rows = await conn.fetch(
        "select column_name from information_schema.columns "
        "where table_schema = 'public' and table_name = $1 order by ordinal_position",
        table,
    )
    return [row["column_name"] for row in rows]


async def user_columns(conn: asyncpg.Connection, table: str) -> dict[str, bool]:
    """``column -> nullable`` for the columns of ``table`` that point at users."""
    rows = await conn.fetch(
        """
        select kcu.column_name, c.is_nullable = 'YES' as nullable
        from information_schema.referential_constraints rc
        join information_schema.key_column_usage kcu
          on kcu.constraint_name = rc.constraint_name
        join information_schema.constraint_column_usage ccu
          on ccu.constraint_name = rc.constraint_name
        join information_schema.columns c
          on c.table_name = kcu.table_name and c.column_name = kcu.column_name
        where kcu.table_schema = 'public' and kcu.table_name = $1
          and ccu.table_name = 'users' and ccu.column_name = 'id'
        """,
        table,
    )
    return {row["column_name"]: row["nullable"] for row in rows}


class Merge:
    def __init__(
        self,
        source: asyncpg.Connection,
        target: asyncpg.Connection,
        *,
        orders: str = "all",
        same_bot: bool = False,
    ) -> None:
        self.source = source
        self.target = target
        self.orders = orders
        #: Both sides talk through the same Telegram bot: who it can reach carries over.
        self.same_bot = same_bot
        self.people: dict[Any, Any] = {}  # dev user id -> production user id
        self.report: Counter[str] = Counter()
        self.notes: list[str] = []

    # --- helpers -------------------------------------------------------------------

    async def shared_columns(self, table: str) -> list[str]:
        theirs = set(await columns(self.target, table))
        return [name for name in await columns(self.source, table) if name in theirs]

    def rename(self, row: dict[str, Any], people_columns: dict[str, bool]) -> dict[str, Any] | None:
        """``row`` with every person renamed to their production account; None
        when a person it cannot do without was left behind."""
        renamed = dict(row)
        for column, nullable in people_columns.items():
            value = row.get(column)
            if value is None:
                continue
            mapped = self.people.get(value)
            if mapped is None and not nullable:
                return None
            renamed[column] = mapped
        return renamed

    async def insert(self, table: str, row: dict[str, Any], cols: list[str]) -> None:
        names = ", ".join(f'"{name}"' for name in cols)
        slots = ", ".join(f"${index}" for index in range(1, len(cols) + 1))
        await self.target.execute(
            f'insert into "{table}" ({names}) values ({slots})',  # noqa: S608 - names from the catalogue
            *[row[name] for name in cols],
        )

    # --- people ----------------------------------------------------------------------

    async def merge_people(self) -> None:
        cols = await self.shared_columns("users")
        people_columns = await user_columns(self.target, "users")
        rows = await self.source.fetch(
            "select * from users where telegram_user_id is null "
            "or telegram_user_id not between $1 and $2 order by created_at",
            *TEST_LOGINS,
        )
        known = {
            row["telegram_user_id"]: row
            for row in await self.target.fetch(
                "select * from users where telegram_user_id is not null"
            )
        }
        new: list[dict[str, Any]] = []
        for row in rows:
            match = known.get(row["telegram_user_id"]) if row["telegram_user_id"] else None
            if match is None:
                new.append(dict(row))
                self.people[row["id"]] = row["id"]
                continue
            self.people[row["id"]] = match["id"]
            self.report["people on both sides"] += 1
            # The higher of the two system roles: an ORD-side EMPLOYEE record
            # never demotes the PR side's TEAM_LEAD (nor anybody an OWNER).
            role = max(match["role"], row["role"], key=ROLE_RANK.get)
            if role != match["role"]:
                self.notes.append(f"role {match['role']} -> {role}: {match['full_name']}")
                self.report["roles raised to the source's"] += 1
            if row["password_hash"] is not None:
                self.report["passwords taken from dev"] += 1
            await self.target.execute(
                """
                update users set role = $2,
                    password_hash = coalesce($3, password_hash),
                    password_changed_at = coalesce($4, password_changed_at),
                    password_temporary = case when $3 is null
                        then password_temporary else $5 end,
                    failed_login_count = 0, locked_until = null
                where id = $1
                """,
                match["id"],
                role,
                row["password_hash"],
                row["password_changed_at"],
                row["password_temporary"],
            )
            await self._take_person_fields(match["id"], dict(row))
            if self.same_bot and row["private_chat_available"]:
                await self.target.execute(
                    "update users set telegram_private_chat_id = $2, "
                    "private_chat_available = true where id = $1",
                    match["id"],
                    row["telegram_private_chat_id"],
                )
        # New accounts: first without the links to other people, then with.
        for row in new:
            bare = {**row, **dict.fromkeys(people_columns)}
            # They talked to the DEV bot: production's has to hear from them first.
            if not self.same_bot:
                bare["private_chat_available"] = False
            # 0068: one member code per living person.
            if bare.get("member_code") and await self._code_taken(bare["member_code"], None):
                self.notes.append(
                    f"member code {bare['member_code']} already on the target: "
                    f"{bare['full_name']} created without one"
                )
                bare["member_code"] = None
            await self.insert("users", bare, cols)
            self.report["people created from dev"] += 1
        for row in new:
            linked = self.rename(row, people_columns) or row
            for column in people_columns:
                if linked.get(column) is not None:
                    await self.target.execute(
                        f'update users set "{column}" = $2 where id = $1',  # noqa: S608
                        row["id"],
                        linked[column],
                    )

    async def _code_taken(self, code: str, except_user: Any) -> bool:
        return bool(
            await self.target.fetchval(
                "select 1 from users where member_code = $1 and deleted_at is null "
                "and ($2::uuid is null or id <> $2)",
                code,
                except_user,
            )
        )

    async def _take_person_fields(self, person: Any, row: dict[str, Any]) -> None:
        """0063 / 0065: the member code and the employment status are the
        person's; the source (where ORD runs) has the current ones."""
        theirs = set(await columns(self.target, "users"))
        if "employment_status" in theirs and row.get("employment_status"):
            await self.target.execute(
                "update users set employment_status = $2 where id = $1",
                person,
                row["employment_status"],
            )
        if "member_code" in theirs and row.get("member_code"):
            if await self._code_taken(row["member_code"], person):
                self.notes.append(
                    f"member code {row['member_code']} already on the target: "
                    f"{row['full_name']} keeps the target's"
                )
            else:
                await self.target.execute(
                    "update users set member_code = $2 where id = $1", person, row["member_code"]
                )

    async def merge_avatars(self) -> None:
        cols = await self.shared_columns("user_avatars")
        for row in await self.source.fetch("select * from user_avatars"):
            person = self.people.get(row["user_id"])
            if person is None:
                continue
            if await self.target.fetchval("select 1 from user_avatars where user_id = $1", person):
                continue
            await self.insert("user_avatars", {**dict(row), "user_id": person}, cols)
            self.report["pictures taken from dev"] += 1

    # --- ORD's setup -----------------------------------------------------------------

    async def merge_unit(self) -> None:
        theirs = await self.source.fetchrow("select * from org_units where code = 'ADS'")
        ours = await self.target.fetchrow("select * from org_units where code = 'ADS'")
        if theirs is None or ours is None or theirs["id"] != ours["id"]:
            raise SystemExit("The ORD unit is not the same row on both sides: stop.")
        await self.target.execute(
            "update org_units set settings = $2 where id = $1", ours["id"], theirs["settings"]
        )
        self.report["ORD settings taken from dev"] += 1
        for table in (
            "unit_video_kinds",
            "unit_platforms",
            "unit_durations",
            "unit_design_codes",
        ):
            cols = await self.shared_columns(table)
            rest = [name for name in cols if name != "id"]
            sets = ", ".join(f'"{name}" = ${index}' for index, name in enumerate(rest, start=2))
            for row in await self.source.fetch(f'select * from "{table}"'):  # noqa: S608
                # Updated in place when it is there: an order may point at it.
                done = await self.target.execute(
                    f'update "{table}" set {sets} where id = $1',  # noqa: S608
                    row["id"],
                    *[row[name] for name in rest],
                )
                if done == "UPDATE 0":
                    await self.insert(table, dict(row), cols)
                self.report[f"{table} rows"] += 1

    async def merge_tags(self) -> None:
        """For everybody on dev, dev says which streams they are in - except
        PR, whose tags (like its seats) are the PR side's own."""
        cols = await self.shared_columns("org_unit_members")
        people_columns = await user_columns(self.target, "org_unit_members")
        now = datetime.now(UTC)
        pr_unit = await self.target.fetchval("select id from org_units where code = 'PR'")
        rows = await self.source.fetch(
            "select * from org_unit_members where unit_id <> $1 order by joined_at", pr_unit
        )
        wanted: dict[tuple[Any, Any], dict[str, Any]] = {}
        for row in rows:
            renamed = self.rename(dict(row), people_columns)
            if renamed is None:
                continue  # a test login's tag
            wanted[(renamed["unit_id"], renamed["user_id"])] = renamed
        on_dev = set(self.people.values())
        existing = await self.target.fetch("select * from org_unit_members")
        for row in existing:
            key = (row["unit_id"], row["user_id"])
            if row["user_id"] not in on_dev or row["unit_id"] == pr_unit:
                continue  # only on production, or a PR tag: untouched
            gone = key not in wanted or wanted[key]["left_at"] is not None
            if gone and row["left_at"] is None:
                await self.target.execute(
                    "update org_unit_members set left_at = $2, manager_user_id = null "
                    "where id = $1",
                    row["id"],
                    now,
                )
                self.report["tags closed (not in that stream on dev)"] += 1
        have = {(row["unit_id"], row["user_id"]): row for row in existing}
        # Without the "own Leader" link first: it may point at a tag not there yet.
        for key, row in wanted.items():
            bare = {**row, "manager_user_id": None}
            if key in have:
                sets = ", ".join(
                    f'"{name}" = ${index}'
                    for index, name in enumerate(
                        [c for c in cols if c not in ("id", "unit_id", "user_id")], start=2
                    )
                )
                await self.target.execute(
                    f"update org_unit_members set {sets} where id = $1",  # noqa: S608
                    have[key]["id"],
                    *[bare[c] for c in cols if c not in ("id", "unit_id", "user_id")],
                )
            else:
                await self.insert("org_unit_members", bare, cols)
            if row["left_at"] is None:
                self.report["open tags from dev"] += 1
        for (unit_id, user_id), row in wanted.items():
            if row["manager_user_id"] is not None:
                await self.target.execute(
                    "update org_unit_members set manager_user_id = $3 "
                    "where unit_id = $1 and user_id = $2",
                    unit_id,
                    user_id,
                    row["manager_user_id"],
                )
                self.report["own-Leader links"] += 1

    # --- the organisation (0061-0068) -----------------------------------------------

    async def merge_departments(self) -> None:
        """Every source department, by code, with its grants; then the open
        seats: for everybody on the source, the source says where they sit -
        but PR Thương Hiệu's seats are the PR side's own."""
        if not await self.target.fetchval(
            "select 1 from information_schema.tables where table_name = 'departments'"
        ):
            return
        cols = await self.shared_columns("departments")
        rest = [name for name in cols if name not in ("id", "code")]
        ours = {row["code"]: row for row in await self.target.fetch("select * from departments")}
        dept_map: dict[Any, Any] = {}
        pr_codes: set[Any] = set()
        for row in await self.source.fetch("select * from departments order by sort_order, code"):
            match = ours.get(row["code"])
            if match is None:
                new_id = row["id"]
                if await self.target.fetchval("select 1 from departments where id = $1", new_id):
                    new_id = uuid.uuid4()
                await self.insert("departments", {**dict(row), "id": new_id}, cols)
                self.report["departments created from source"] += 1
                dept_map[row["id"]] = new_id
            else:
                sets = ", ".join(f'"{name}" = ${index}' for index, name in enumerate(rest, start=2))
                await self.target.execute(
                    f"update departments set {sets} where id = $1",  # noqa: S608
                    match["id"],
                    *[row[name] for name in rest],
                )
                dept_map[row["id"]] = match["id"]
                self.report["departments updated from source"] += 1
            if row["function"] == "PR":
                pr_codes.add(dept_map[row["id"]])
        # Grants: the source's cells replace the target's, department by department.
        pcols = await self.shared_columns("department_permissions")
        for source_id, target_id in dept_map.items():
            await self.target.execute(
                "delete from department_permissions where department_id = $1", target_id
            )
            for row in await self.source.fetch(
                "select * from department_permissions where department_id = $1", source_id
            ):
                await self.insert(
                    "department_permissions",
                    {**dict(row), "id": uuid.uuid4(), "department_id": target_id},
                    pcols,
                )
                self.report["department grant cells"] += 1
        # Seats.
        mcols = await self.shared_columns("department_members")
        people_columns = await user_columns(self.target, "department_members")
        now = datetime.now(UTC)
        wanted: dict[tuple[Any, Any], dict[str, Any]] = {}
        for row in await self.source.fetch(
            "select * from department_members where left_at is null order by joined_at"
        ):
            target_id = dept_map.get(row["department_id"])
            renamed = self.rename(dict(row), people_columns)
            if renamed is None or target_id is None or target_id in pr_codes:
                continue  # a test login's seat, or a PR seat (the PR side decides)
            wanted[(target_id, renamed["user_id"])] = {**renamed, "department_id": target_id}
        on_source = set(self.people.values())
        existing = await self.target.fetch("select * from department_members where left_at is null")
        for row in existing:
            key = (row["department_id"], row["user_id"])
            if row["user_id"] not in on_source or row["department_id"] in pr_codes:
                continue
            if key not in wanted:
                await self.target.execute(
                    "update department_members set left_at = $2, manager_user_id = null "
                    "where id = $1",
                    row["id"],
                    now,
                )
                self.report["seats closed (not on the source)"] += 1
        have = {(row["department_id"], row["user_id"]): row for row in existing}
        for key, row in wanted.items():
            if key in have:
                await self.target.execute(
                    "update department_members set position = $2, manager_user_id = null "
                    "where id = $1",
                    have[key]["id"],
                    row["position"],
                )
            else:
                await self.insert(
                    "department_members",
                    {**row, "id": uuid.uuid4(), "manager_user_id": None},
                    mcols,
                )
            self.report["seats from source"] += 1
        for (department_id, user_id), row in wanted.items():
            boss = row.get("manager_user_id")
            if boss is None:
                continue
            is_head = await self.target.fetchval(
                "select 1 from department_members where department_id = $1 and user_id = $2 "
                "and position = 'HEAD' and left_at is null",
                department_id,
                boss,
            )
            if is_head:
                await self.target.execute(
                    "update department_members set manager_user_id = $3 "
                    "where department_id = $1 and user_id = $2 and left_at is null",
                    department_id,
                    user_id,
                    boss,
                )
                self.report["seat manager links"] += 1

    async def merge_personal_grants(self) -> None:
        if not await self.target.fetchval(
            "select 1 from information_schema.tables where table_name = 'user_permissions'"
        ):
            return
        cols = await self.shared_columns("user_permissions")
        people_columns = await user_columns(self.target, "user_permissions")
        for row in await self.source.fetch("select * from user_permissions order by created_at"):
            renamed = self.rename(dict(row), people_columns)
            if renamed is None:
                continue
            if await self.target.fetchval(
                "select 1 from user_permissions where id = $1", row["id"]
            ):
                continue
            await self.insert("user_permissions", renamed, cols)
            self.report["personal grants"] += 1

    async def merge_ai(self) -> None:
        if not await self.target.fetchval(
            "select 1 from information_schema.tables where table_name = 'ai_connections'"
        ):
            return
        cols = await self.shared_columns("ai_connections")
        people_columns = await user_columns(self.target, "ai_connections")
        for row in await self.source.fetch("select * from ai_connections order by created_at"):
            if await self.target.fetchval("select 1 from ai_connections where id = $1", row["id"]):
                continue
            renamed = self.rename(dict(row), people_columns) or {
                **dict(row),
                **dict.fromkeys(people_columns),
            }
            await self.insert("ai_connections", renamed, cols)
            self.report["AI connections"] += 1
        rcols = await self.shared_columns("ai_routes")
        for row in await self.source.fetch("select * from ai_routes"):
            await self.target.execute("delete from ai_routes where purpose = $1", row["purpose"])
            await self.insert("ai_routes", dict(row), rcols)
            self.report["AI routes"] += 1

    async def merge_resource_catalogue(self) -> None:
        if not await self.target.fetchval(
            "select 1 from information_schema.tables where table_name = 'resources'"
        ):
            return
        held = await self.source.fetchval("select count(*) from resources")
        if held:
            self.report["resources NOT carried (see notes)"] += held
            self.notes.append(f"{held} resource(s) on the source stay there: decide by hand")
        if await self.target.fetchval("select count(*) from resources"):
            return  # the target's catalogue is in use: left alone
        cols = await self.shared_columns("resource_options")
        await self.target.execute("delete from resource_options")
        for row in await self.source.fetch("select * from resource_options order by sort_order"):
            await self.insert("resource_options", dict(row), cols)
            self.report["resource catalogue rows"] += 1

    # --- orders ----------------------------------------------------------------------

    async def merge_orders(self) -> None:
        order_cols = await self.shared_columns("orders")
        order_people = await user_columns(self.target, "orders")
        taken = set(await self._codes())
        moved: list[Any] = []
        for row in await self.source.fetch("select * from orders order by submitted_at"):
            if self.orders == "none" or (self.orders == "live" and row["stage"] == "CANCELLED"):
                self.report["orders left on dev (--orders)"] += 1
                continue
            renamed = self.rename(dict(row), order_people)
            if renamed is None:
                self.notes.append(f"order skipped (test account): {row['code']}")
                self.report["orders skipped"] += 1
                continue
            if row["code"] in taken:
                self.notes.append(f"order skipped (code already on production): {row['code']}")
                self.report["orders skipped"] += 1
                continue
            await self.insert("orders", renamed, order_cols)
            moved.append(row["id"])
            self.report["orders"] += 1
        for table, link in ORDER_CHILDREN:
            cols = await self.shared_columns(table)
            people_columns = await user_columns(self.target, table)
            if link is None:
                rows = await self.source.fetch(
                    f'select t.* from "{table}" t join order_nodes n on n.id = t.node_id '  # noqa: S608
                    "where n.order_id = any($1::uuid[]) order by t.created_at",
                    moved,
                )
            else:
                rows = await self.source.fetch(
                    f'select * from "{table}" where "{link}" = any($1::uuid[])',  # noqa: S608
                    moved,
                )
            for row in rows:
                renamed = self.rename(dict(row), people_columns)
                if renamed is None:
                    self.notes.append(f"{table} row dropped (test account): {row['id']}")
                    self.report[f"{table} rows dropped"] += 1
                    continue
                await self.insert(table, renamed, cols)
                self.report[f"{table} rows"] += 1
        # The unified task rows of those orders.
        cols = await self.shared_columns("tasks")
        people_columns = await user_columns(self.target, "tasks")
        for row in await self.source.fetch(
            "select * from tasks where order_id = any($1::uuid[])", moved
        ):
            renamed = self.rename(dict(row), people_columns)
            if renamed is None:
                continue
            await self.target.execute("delete from tasks where order_id = $1", row["order_id"])
            await self.insert("tasks", renamed, cols)
            self.report["tasks rows"] += 1
        # What the bell says about those orders.
        cols = await self.shared_columns("user_notifications")
        for row in await self.source.fetch(
            "select * from user_notifications where target_kind = 'order' "
            "and target_id = any($1::uuid[])",
            moved,
        ):
            person = self.people.get(row["recipient_user_id"])
            if person is None or await self.target.fetchval(
                "select 1 from user_notifications where idempotency_key = $1",
                row["idempotency_key"],
            ):
                continue
            await self.insert(
                "user_notifications", {**dict(row), "recipient_user_id": person}, cols
            )
            self.report["order notifications"] += 1
        # KPI rows hang off the source's own work plans: counted, never carried.
        left = await self.source.fetchval(
            "select count(*) from pr_work_results where source_type = 'ORDER'"
        )
        if left:
            self.report["order KPI rows NOT carried (see notes)"] += left
            self.notes.append(
                f"{left} KPI result(s) recorded for orders stay on the source: decide by hand"
            )
        # The day's order numbers, so the next code does not collide.
        cols = await self.shared_columns("order_code_counters")
        for row in await self.source.fetch("select * from order_code_counters"):
            exists = await self.target.fetchval(
                "select 1 from order_code_counters where unit_id = $1 and member_code = $2 "
                "and day = $3",
                row["unit_id"],
                row["member_code"],
                row["day"],
            )
            if not exists:
                await self.insert("order_code_counters", dict(row), cols)
                self.report["order_code_counters rows"] += 1

    async def _codes(self) -> list[str]:
        return [row["code"] for row in await self.target.fetch("select code from orders")]

    async def merge_invites(self) -> None:
        cols = await self.shared_columns("invite_codes")
        people_columns = await user_columns(self.target, "invite_codes")
        rows = await self.source.fetch(
            "select * from invite_codes where active and use_count < max_uses "
            "and (expires_at is null or expires_at > now())"
        )
        for row in rows:
            renamed = self.rename(dict(row), people_columns)
            if renamed is None:
                continue
            if renamed.get("created_by") is None:
                continue  # made by a test login
            if await self.target.fetchval(
                "select 1 from invite_codes where code_hash = $1", row["code_hash"]
            ):
                continue
            await self.insert("invite_codes", renamed, cols)
            self.report["open invite codes"] += 1


async def main(apply: bool, orders: str, same_bot: bool) -> None:
    source_db = os.environ.get("MERGE_SOURCE_DB")
    if not source_db:
        raise SystemExit("Set MERGE_SOURCE_DB to the database holding the restored dev dump.")
    target_db = os.environ.get("MERGE_TARGET_DB")
    source = await asyncpg.connect(dsn_for(source_db))
    target = await asyncpg.connect(dsn_for(target_db))
    heads = [
        await conn.fetchval("select version_num from alembic_version") for conn in (source, target)
    ]
    target_name = target_db or "(DATABASE_URL)"
    print(f"source {source_db} @ {heads[0]}  ->  target {target_name} @ {heads[1]}")
    if heads[0] != heads[1]:
        raise SystemExit("The two databases are not at the same migration: upgrade first.")
    merge = Merge(source, target, orders=orders, same_bot=same_bot)
    transaction = target.transaction()
    await transaction.start()
    try:
        await merge.merge_people()
        await merge.merge_avatars()
        await merge.merge_unit()
        await merge.merge_tags()
        await merge.merge_departments()
        await merge.merge_personal_grants()
        await merge.merge_ai()
        await merge.merge_resource_catalogue()
        await merge.merge_orders()
        await merge.merge_invites()
        counts = {
            name: await target.fetchval(f"select count(*) from {name}")  # noqa: S608
            for name in (
                "users",
                "pr_content_items",
                "orders",
                "order_nodes",
                "tasks",
                "department_members",
            )
        }
    except BaseException:
        await transaction.rollback()
        raise
    if apply:
        await transaction.commit()
    else:
        await transaction.rollback()
    for name, count in sorted(merge.report.items()):
        print(f"{count:>6}  {name}")
    for note in merge.notes:
        print("  note:", note)
    print("target after:", ", ".join(f"{name}={count}" for name, count in counts.items()))
    print("COMMITTED" if apply else "DRY RUN - rolled back, nothing changed (add --apply)")
    await source.close()
    await target.close()


if __name__ == "__main__":
    chosen = next(
        (arg.split("=", 1)[1] for arg in sys.argv[1:] if arg.startswith("--orders=")), "all"
    )
    if chosen not in ORDER_CHOICES:
        raise SystemExit(f"--orders takes one of: {', '.join(ORDER_CHOICES)}")
    asyncio.run(
        main(
            apply="--apply" in sys.argv[1:],
            orders=chosen,
            same_bot="--same-bot" in sys.argv[1:],
        )
    )
