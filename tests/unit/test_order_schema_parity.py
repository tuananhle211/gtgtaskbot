"""The ``0042`` tables obey the schema rules the PR tables are held to.

``tests/unit/test_pr_core_schema_parity.py`` scopes its per-table checks to
Step 1A's eleven tables, so a new table is not checked by accident. These are
the same rules, applied to the nine tables units and orders add, plus the one
check that only a new revision can need: the enum values the migration writes
into its ``CHECK``-less ``VARCHAR`` columns are exactly the domain enums.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

import meobot.db.models  # noqa: F401 - registers every table
from meobot.db.base import Base
from meobot.domain.orders.models import (
    OrderApprovalDecision,
    OrderApprovalGate,
    OrderEventKind,
    OrderNodeStatus,
    OrderNodeType,
    OrderScriptSource,
    OrderStage,
    OrderVideoType,
)
from meobot.domain.units.models import UnitCode, UnitMemberRole

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "0042_org_units_and_orders.py"
#: ``0044`` widened the process codes (``orders.video_type``) from three to seven.
PROCESS_MIGRATION = ROOT / "alembic" / "versions" / "0044_ads_process_and_video_kinds.py"

NEW_TABLES = (
    "org_units",
    "org_unit_members",
    "orders",
    "order_nodes",
    "order_submissions",
    "order_events",
    "order_approvals",
    "order_code_counters",
    "order_work_rules",
)
APPEND_ONLY = ("order_submissions", "order_events", "order_approvals")


def _tables() -> dict[str, sa.Table]:
    return {name: Base.metadata.tables[name] for name in NEW_TABLES}


def _migration(path: Path = MIGRATION) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem[:4]}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_new_table_is_registered() -> None:
    missing = [name for name in NEW_TABLES if name not in Base.metadata.tables]
    assert missing == []


def test_every_foreign_key_restricts_deletes() -> None:
    offenders = [
        f"{table.name}.{fk.parent.name}"
        for table in _tables().values()
        for fk in table.foreign_keys
        if fk.ondelete != "RESTRICT"
    ]
    assert offenders == []


def test_every_timestamp_is_timezone_aware() -> None:
    offenders = [
        f"{table.name}.{column.name}"
        for table in _tables().values()
        for column in table.columns
        if isinstance(column.type, sa.DateTime) and not column.type.timezone
    ]
    assert offenders == []


def test_append_only_tables_have_no_updated_at() -> None:
    tables = _tables()
    for name in APPEND_ONLY:
        assert "updated_at" not in tables[name].columns, name
        assert "created_at" in tables[name].columns, name


def test_every_primary_key_is_a_uuid() -> None:
    for table in _tables().values():
        (pk,) = table.primary_key.columns
        assert isinstance(pk.type, sa.Uuid), table.name


def test_every_enum_column_is_a_varchar_wide_enough_for_its_values() -> None:
    for table in _tables().values():
        for column in table.columns:
            if not isinstance(column.type, sa.Enum):
                continue
            assert column.type.native_enum is False, f"{table.name}.{column.name}"
            longest = max(len(value) for value in column.type.enums)
            assert column.type.length is not None and column.type.length >= longest, (
                f"{table.name}.{column.name}: length {column.type.length} < {longest}"
            )


def test_every_not_null_column_the_orm_omits_has_a_default() -> None:
    """A caller fills these; everything else must default on insert."""
    caller_supplied = {
        "org_units.code",
        "org_units.name",
        "org_unit_members.unit_id",
        "org_unit_members.user_id",
        "org_unit_members.role",
        "orders.unit_id",
        "orders.code",
        "orders.title",
        "orders.video_type",
        "orders.order_content",
        "orders.owner_user_id",
        "orders.submitted_at",
        "order_nodes.order_id",
        "order_nodes.node_type",
        "order_submissions.node_id",
        "order_submissions.submission_no",
        "order_submissions.submitted_by_user_id",
        "order_events.order_id",
        "order_events.kind",
        "order_events.actor_user_id",
        "order_approvals.order_id",
        "order_approvals.gate",
        "order_approvals.round_no",
        "order_approvals.decision",
        "order_approvals.actor_user_id",
        "order_code_counters.unit_id",
        "order_code_counters.member_code",
        "order_code_counters.day",
        "order_work_rules.unit_id",
        "order_work_rules.node_type",
        "order_work_rules.work_type_id",
    }
    offenders = [
        f"{table.name}.{column.name}"
        for table in _tables().values()
        for column in table.columns
        if not column.nullable
        and not column.primary_key
        and column.default is None
        and column.server_default is None
        and f"{table.name}.{column.name}" not in caller_supplied
    ]
    assert offenders == []


def test_the_migration_writes_the_same_enum_values_the_domain_declares() -> None:
    migration = _migration()
    pairs = {
        "UNIT_CODE_VALUES": UnitCode,
        "MEMBER_ROLE_VALUES": UnitMemberRole,
        "SCRIPT_SOURCE_VALUES": OrderScriptSource,
        "STAGE_VALUES": OrderStage,
        "NODE_TYPE_VALUES": OrderNodeType,
        "NODE_STATUS_VALUES": OrderNodeStatus,
        "EVENT_KIND_VALUES": OrderEventKind,
        "GATE_VALUES": OrderApprovalGate,
        "DECISION_VALUES": OrderApprovalDecision,
    }
    for constant, enum in pairs.items():
        assert tuple(getattr(migration, constant)) == tuple(member.value for member in enum), (
            constant
        )
    # The process codes: 0042 wrote the first three, 0044 declares all of them.
    codes = tuple(member.value for member in OrderVideoType)
    assert tuple(_migration(PROCESS_MIGRATION).VIDEO_TYPE_VALUES) == codes
    assert tuple(migration.VIDEO_TYPE_VALUES) == codes[: len(migration.VIDEO_TYPE_VALUES)]


def test_the_migration_declares_the_same_tables_the_models_do() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for name in NEW_TABLES:
        assert f'"{name}"' in source, name
    assert 'revision: str = "0042"' in source
    assert 'down_revision: str | None = "0041"' in source


def test_the_active_node_index_lists_exactly_the_active_statuses() -> None:
    from meobot.db.models.order import ACTIVE_NODE
    from meobot.domain.orders.models import ACTIVE_NODE_STATUSES

    listed = {
        token.strip("'")
        for token in str(ACTIVE_NODE).split("(")[1].rstrip(")").replace(" ", "").split(",")
    }
    assert listed == {status.value for status in ACTIVE_NODE_STATUSES}


def test_no_code_allocation_uses_a_banned_sql_shape() -> None:
    """The sweep in ``test_pr_authorization_schema_parity`` covers ``src``; this
    pins the migration too, which that sweep does not read."""
    source = MIGRATION.read_text(encoding="utf-8")
    for banned in ("MAX(code", "COUNT(*)+1", "func.max(", "ORDER BY code DESC"):
        assert banned not in source, banned
