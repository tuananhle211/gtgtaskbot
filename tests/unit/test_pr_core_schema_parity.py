"""The PR foundation's shape, held still.

These tests run offline and cannot see a real PostgreSQL - only
``tests/integration/test_pr_core_migrations.py`` can, and it is the one that
proves migration 0012 and the models agree. What this file does is hold the
*structural* promises of Step 1A in place, so that breaking one costs seconds
rather than a production incident:

* every PR reference to a person points at the one ``users`` table, and no
  second identity table has appeared beside it;
* no URL is a key;
* the append-only approval table stays append-only;
* the enum vocabularies in the migration still match the domain enums;
* every ``NOT NULL`` PR column the ORM omits from its ``INSERT`` has a server
  default - the exact defect revision 0011 was written to repair.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import DateTime, Table

import meobot.db.models
from meobot.db.base import Base
from meobot.db.models.pr import (
    USERS_TABLE,
    PrApprovalEvent,
    PrBrand,
    PrChannel,
    PrChannelAssignment,
    PrContentFormat,
    PrContentItem,
    PrContentPillar,
    PrContentTarget,
    PrPlatform,
    PrTask,
    PrTaskAssignment,
)
from meobot.domain.pr.models import (
    PrApprovalDecision,
    PrApprovalStage,
    PrChannelAssignmentRole,
    PrChannelCategory,
    PrChannelStatus,
    PrContentTargetStatus,
    PrEntityStatus,
    PrPriority,
    PrTaskAssignmentRole,
    PrTaskStatus,
    PrWorkflowStage,
)

ROOT = Path(__file__).resolve().parents[2]

PR_MODELS = (
    PrBrand,
    PrPlatform,
    PrContentFormat,
    PrContentPillar,
    PrChannel,
    PrChannelAssignment,
    PrContentItem,
    PrContentTarget,
    PrTask,
    PrTaskAssignment,
    PrApprovalEvent,
)

#: The eleven tables Step 1A creates. Spelled out rather than derived from the
#: models, so that deleting a model cannot quietly delete its own assertion.
EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "pr_brands",
        "pr_platforms",
        "pr_content_formats",
        "pr_content_pillars",
        "pr_channels",
        "pr_channel_assignments",
        "pr_content_items",
        "pr_content_targets",
        "pr_tasks",
        "pr_task_assignments",
        "pr_approval_events",
    }
)

#: Names a second identity table would plausibly be given. Step 1A adds none:
#: a PR channel owner is a MeoBot ``users`` row, not a copy of one.
IDENTITY_TABLE_SMELL = re.compile(r"staff|employee|personnel|people|member_profile|pr_users?\b")


@pytest.fixture(scope="module")
def revision_0012() -> Any:
    """Migration 0012, imported as a module so its enum lists can be read."""
    path = ROOT / "alembic" / "versions" / "0012_pr_core_foundation.py"
    spec = importlib.util.spec_from_file_location("meobot_migration_0012", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def revision_0023() -> Any:
    """Migration 0023, imported for its priority vocabulary and backfill values."""
    path = ROOT / "alembic" / "versions" / "0023_pr_content_priority.py"
    spec = importlib.util.spec_from_file_location("meobot_migration_0023", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pr_tables() -> list[Table]:
    return [Base.metadata.tables[name] for name in sorted(EXPECTED_TABLES)]


# --- The tables exist, and the migration creates exactly those --------------


def test_step_1a_registers_its_eleven_pr_tables() -> None:
    """Step 1A's eleven, still all present and still exactly what 0012 creates.

    Scoped to revision 0012 rather than to every ``pr_`` table in the metadata:
    later steps add more of them - Step 1B's nine live in
    ``tests/unit/test_pr_reporting_schema_parity.py`` - and a test that forbade
    that would fail for the wrong reason every time the module grew. What must
    stay true is that *this* revision's table set has not changed.
    """
    assert len(EXPECTED_TABLES) == 11
    registered = {name for name in Base.metadata.tables if name.startswith("pr_")}
    assert registered >= EXPECTED_TABLES

    source = (ROOT / "alembic" / "versions" / "0012_pr_core_foundation.py").read_text(
        encoding="utf-8"
    )
    created = set(re.findall(r'op\.create_table\(\s*"([^"]+)"', source))
    assert created == EXPECTED_TABLES


def test_every_pr_model_is_exported_from_the_models_package() -> None:
    """Alembic autogenerate only sees what the package imports."""
    for model in PR_MODELS:
        assert model.__name__ in meobot.db.models.__all__
        assert getattr(meobot.db.models, model.__name__) is model


# --- Identity: one users table, referenced, never duplicated ---------------


def test_every_pr_user_reference_points_at_the_existing_users_table() -> None:
    """Requirement 14.

    Collected by walking the foreign keys rather than by reading the model
    source, so a new PR table that invents its own person table fails here even
    if nobody remembers to add an assertion for it.
    """
    assert USERS_TABLE == "users"
    person_columns: dict[str, str] = {}
    for table in pr_tables():
        for column in table.columns:
            if not column.name.endswith("user_id"):
                continue
            targets = {fk.column.table.name for fk in column.foreign_keys}
            assert targets, f"{table.name}.{column.name} names a person but has no foreign key"
            person_columns[f"{table.name}.{column.name}"] = targets.pop()

    assert set(person_columns.values()) == {"users"}, person_columns
    # And every one of them is actually present - a silent rename of a column
    # would otherwise empty this test without failing it.
    assert set(person_columns) == {
        "pr_approval_events.reviewer_user_id",
        "pr_channel_assignments.user_id",
        "pr_content_items.created_by_user_id",
        "pr_content_items.owner_user_id",
        # Step 1F.2.3's producer, pointing at ``users`` like every other person
        # reference here - which is the property this test exists for. A producer
        # is a MeoBot user, not a row in some production-staff table.
        #
        # Step 1F.2.3a's ``deleted_by_user_id`` was here for one step and is gone
        # with the soft delete: a permanent deletion leaves no row to attribute,
        # so who deleted it lives in the audit trail instead.
        "pr_content_items.producer_user_id",
        "pr_task_assignments.user_id",
        "pr_tasks.created_by_user_id",
    }


def test_no_second_staff_or_employee_identity_table_exists() -> None:
    """Requirement 15. There is one identity table in MeoBot and it is ``users``."""
    offenders = [name for name in Base.metadata.tables if IDENTITY_TABLE_SMELL.search(name)]
    assert offenders == [], offenders
    assert "users" in Base.metadata.tables


def test_pr_person_references_never_cascade_a_user_deletion() -> None:
    """Deleting a person who owns PR work must fail, not erase the work."""
    for table in pr_tables():
        for constraint in table.foreign_key_constraints:
            if constraint.referred_table.name != "users":
                continue
            assert constraint.ondelete == "RESTRICT", f"{table.name}.{constraint.name}"


def test_every_pr_foreign_key_is_restrict() -> None:
    """No brand, platform, channel, content item or task is cascade-deleted."""
    for table in pr_tables():
        for constraint in table.foreign_key_constraints:
            assert constraint.ondelete == "RESTRICT", f"{table.name}.{constraint.name}"


# --- URLs are data, not keys ------------------------------------------------


def test_no_url_column_is_used_as_a_foreign_key() -> None:
    """Requirement 16.

    A channel's URL changes when a handle is renamed or a platform migrates a
    domain. Every relationship here is a UUID; ``url`` is stored for a person
    to click and is joined on by nothing.
    """
    for table in pr_tables():
        for column in table.columns:
            if "url" not in column.name:
                continue
            assert not column.foreign_keys, f"{table.name}.{column.name} is a foreign key"
            assert not column.unique, f"{table.name}.{column.name} is unique"
            assert not column.index, f"{table.name}.{column.name} is indexed"
        for constraint in table.foreign_key_constraints:
            names = {column.name for column in constraint.columns}
            assert not any("url" in name for name in names), f"{table.name}.{constraint.name}"

    # The platform's own identifier is what a collector will match on, and it
    # is a separate column from the URL precisely so the URL never has to be.
    assert "external_id" in PrChannel.__table__.columns
    assert "url" in PrChannel.__table__.columns


# --- The append-only table stays append-only -------------------------------


def test_approval_events_have_no_updated_at() -> None:
    """Requirement 17. A decision that can be edited is not a record of one."""
    assert "updated_at" not in PrApprovalEvent.__table__.columns
    assert "created_at" in PrApprovalEvent.__table__.columns
    assert "decided_at" in PrApprovalEvent.__table__.columns


def test_task_assignments_have_no_updated_at() -> None:
    """Its only mutations are ``accepted_at`` and ``completed_at``.

    Both are dated facts in their own right, so a generic "last touched"
    column would add a third, less precise answer to a question already
    answered twice.
    """
    assert "updated_at" not in PrTaskAssignment.__table__.columns


# --- Codes are unique, and a brand's is immutable --------------------------


def test_every_human_readable_code_is_uniquely_indexed() -> None:
    """Requirement 4, as a schema fact rather than an insert.

    The database refusing a duplicate is checked against a real PostgreSQL in
    ``tests/integration/test_pr_core_migrations.py``; this is the cheap guard
    that the index has not been dropped.
    """
    coded = {
        "pr_brands",
        "pr_platforms",
        "pr_content_formats",
        "pr_content_pillars",
        "pr_channels",
        "pr_content_items",
        "pr_tasks",
    }
    for table in pr_tables():
        if table.name not in coded:
            continue
        unique_code_indexes = [
            index
            for index in table.indexes
            if index.unique and [column.name for column in index.columns] == ["code"]
        ]
        assert unique_code_indexes, f"{table.name}.code has no unique index"


def test_a_brand_code_cannot_be_changed_once_set() -> None:
    """Codes are printed, typed and pasted into things that outlive a release."""
    brand = PrBrand(code="BRAND-A", name="Brand A")
    assert brand.code == "BRAND-A"

    with pytest.raises(ValueError, match="immutable"):
        brand.code = "BRAND-B"
    assert brand.code == "BRAND-A"

    # Assigning the same value is not a change and is not an error - an ORM
    # refresh or a no-op update must not blow up.
    brand.code = "BRAND-A"
    assert brand.code == "BRAND-A"


def test_uuids_remain_the_primary_keys() -> None:
    """A code is a label. Every relationship in this module is a UUID."""
    for table in pr_tables():
        primary = [column.name for column in table.primary_key.columns]
        assert primary == ["id"], f"{table.name} primary key is {primary}"


# --- The migration and the models say the same thing -----------------------


def test_migration_0012_follows_0011(revision_0012: Any) -> None:
    assert revision_0012.revision == "0012"
    assert revision_0012.down_revision == "0011"


def test_migration_0011_is_left_alone() -> None:
    """Rewriting a migration that has already run is how two deployments end
    up with different schemas at the same revision number."""
    source = (ROOT / "alembic" / "versions" / "0011_dispatch_timestamp_defaults.py").read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "0011"' in source
    assert "pr_" not in source


def test_the_migration_enum_lists_match_the_domain_enums(revision_0012: Any) -> None:
    """The stored vocabulary is one vocabulary.

    The migration spells its values out as literals on purpose - it has to keep
    meaning what it meant on the day it ran. This is the assertion that keeps
    that literal list and the domain enum in step, so adding a workflow stage
    to Python without widening the column is caught here.
    """
    expected = {
        "ENTITY_STATUSES": PrEntityStatus,
        "CHANNEL_CATEGORIES": PrChannelCategory,
        "CHANNEL_STATUSES": PrChannelStatus,
        "CHANNEL_ASSIGNMENT_ROLES": PrChannelAssignmentRole,
        # ``PRIORITIES`` is deliberately not here - Step 1F.2.3d changed that
        # vocabulary, and 0012's literal stays what it was. See
        # ``test_the_0012_priority_list_stays_the_one_0012_created``.
        "CONTENT_TARGET_STATUSES": PrContentTargetStatus,
        "TASK_STATUSES": PrTaskStatus,
        "TASK_ASSIGNMENT_ROLES": PrTaskAssignmentRole,
        "APPROVAL_STAGES": PrApprovalStage,
        "APPROVAL_DECISIONS": PrApprovalDecision,
    }
    for attribute, enum_type in expected.items():
        assert getattr(revision_0012, attribute) == tuple(member.value for member in enum_type), (
            attribute
        )


def test_the_0012_workflow_stage_list_stays_the_one_0012_created(revision_0012: Any) -> None:
    """``WORKFLOW_STAGES`` in 0012 is history, and history does not gain a stage.

    Step 1A1 adds ``AI_REVIEW`` to :class:`PrWorkflowStage`. Migration 0012's
    literal deliberately does **not** follow, because a migration has to keep
    meaning what it meant on the day it ran - and on that day the workflow had
    thirteen stages. Nothing breaks by leaving it: the column is a
    ``VARCHAR(30)`` with no PostgreSQL ``ENUM`` type and no vocabulary
    ``CHECK``, so this list generated no constraint that could now be too
    narrow. See ``alembic/versions/0014_pr_ai_review.py``.

    The *current* vocabulary is checked against 0014's list in
    ``tests/unit/test_pr_ai_review_schema_parity.py``.
    """
    assert revision_0012.WORKFLOW_STAGES == (
        "IDEA",
        "BRIEFING",
        "SCRIPTING",
        "TEAM_LEAD_REVIEW",
        "HEAD_REVIEW",
        "APPROVED",
        "PRODUCTION",
        "INTERNAL_REVIEW",
        "READY_TO_PUBLISH",
        "PUBLISHED",
        "MEASURED",
        "ARCHIVED",
        "CANCELLED",
    )
    # Every value 0012 knew about is still in the enum: a stage may be added,
    # but removing or renaming one would strand rows already written under it.
    assert set(revision_0012.WORKFLOW_STAGES) <= {member.value for member in PrWorkflowStage}


def test_the_0012_priority_list_stays_the_one_0012_created(revision_0012: Any) -> None:
    """``PRIORITIES`` in 0012 is history too, and history keeps ``LOW``.

    Step 1F.2.3d adds ``CRITICAL`` and removes ``LOW``. 0012's literal follows
    neither, for the reason
    :func:`test_the_0012_workflow_stage_list_stays_the_one_0012_created` gives:
    on the day it ran, those four were the vocabulary, and the column it created
    is a ``VARCHAR(20)`` with no ``CHECK``, so the list constrained nothing that
    could now be wrong.

    The *current* vocabulary is checked against 0023's list in
    :func:`test_the_0023_priority_list_matches_the_domain_enum`.
    """
    assert revision_0012.PRIORITIES == ("LOW", "NORMAL", "HIGH", "URGENT")


def test_the_0023_priority_list_matches_the_domain_enum(revision_0023: Any) -> None:
    """0023 names the vocabulary as it now stands, in the enum's own order."""
    assert tuple(member.value for member in PrPriority) == revision_0023.PRIORITIES


def test_removing_low_is_a_migration_rather_than_only_an_enum_edit(
    revision_0012: Any, revision_0023: Any
) -> None:
    """The one value 0012 knew that the enum no longer has must be backfilled.

    Every other retired-vocabulary test in this file asserts the *subset* rule -
    a value may be added, but removing one strands rows written under it.
    ``LOW`` is the single exception in the module's history, and it is only
    allowed because 0023 moves those rows rather than leaving them: a row still
    reading ``'LOW'`` would raise ``LookupError`` the moment SQLAlchemy tried to
    coerce it into :class:`PrPriority`.

    So the exception is pinned to the thing that makes it safe. If the backfill
    is ever dropped from 0023, this fails.
    """
    current = {member.value for member in PrPriority}
    retired = set(revision_0012.PRIORITIES) - current
    assert retired == {"LOW"}, "retiring another priority needs its own backfill"

    assert revision_0023.RETIRED_PRIORITY == "LOW"
    assert revision_0023.REPLACEMENT_PRIORITY in current

    # Both columns, because one enum backs both. Backfilling only the content
    # one would leave ``pr_tasks`` holding a value the enum cannot coerce.
    assert revision_0023.CONTENT_ITEMS == "pr_content_items"
    assert revision_0023.TASKS == "pr_tasks"


def test_every_enum_value_is_a_stable_uppercase_code() -> None:
    """Name and value are the same string, so a rename cannot be silent."""
    for enum_type in (
        PrEntityStatus,
        PrChannelCategory,
        PrChannelStatus,
        PrChannelAssignmentRole,
        PrPriority,
        PrWorkflowStage,
        PrContentTargetStatus,
        PrTaskStatus,
        PrTaskAssignmentRole,
        PrApprovalStage,
        PrApprovalDecision,
    ):
        for member in enum_type:
            assert member.value == member.name
            assert member.value == member.value.upper()
            assert re.fullmatch(r"[A-Z][A-Z_]*", member.value), member.value


def test_every_enum_column_is_wide_enough_for_its_longest_value() -> None:
    """A ``VARCHAR(20)`` holding a 25-character stage truncates or errors."""
    for table in pr_tables():
        for column in table.columns:
            length = getattr(column.type, "length", None)
            enum_values = getattr(column.type, "enums", None)
            if length is None or not enum_values:
                continue
            longest = max(len(value) for value in enum_values)
            assert longest <= length, f"{table.name}.{column.name}: {longest} > {length}"


def test_every_not_null_pr_column_the_orm_omits_has_a_server_default() -> None:
    """The 0011 defect, asserted for the new tables.

    A ``NOT NULL`` column with neither a Python default nor a server default
    must be supplied by the caller on every insert. Anything else sends a
    ``NULL`` and fails at the first write. The list below is the complete set
    of columns Step 1A expects a caller to fill; everything else has a default.
    """
    caller_supplied = {
        "pr_brands.code",
        "pr_brands.name",
        "pr_platforms.code",
        "pr_platforms.name",
        "pr_content_formats.code",
        "pr_content_formats.name",
        "pr_content_pillars.code",
        "pr_content_pillars.name",
        "pr_channels.code",
        "pr_channels.name",
        "pr_channels.platform_id",
        "pr_channels.category",
        "pr_channel_assignments.channel_id",
        "pr_channel_assignments.user_id",
        "pr_channel_assignments.assignment_role",
        "pr_channel_assignments.effective_from",
        "pr_content_items.code",
        "pr_content_items.title",
        "pr_content_items.brand_id",
        "pr_content_items.owner_user_id",
        "pr_content_items.created_by_user_id",
        "pr_content_targets.content_id",
        "pr_content_targets.channel_id",
        "pr_tasks.code",
        "pr_tasks.task_type",
        "pr_tasks.title",
        "pr_tasks.created_by_user_id",
        "pr_task_assignments.task_id",
        "pr_task_assignments.user_id",
        "pr_task_assignments.assignment_role",
        "pr_task_assignments.assigned_at",
        "pr_approval_events.content_id",
        "pr_approval_events.approval_stage",
        "pr_approval_events.reviewer_user_id",
        "pr_approval_events.decision",
        "pr_approval_events.version_reviewed",
        "pr_approval_events.decided_at",
    }
    unfilled = {
        f"{table.name}.{column.name}"
        for table in pr_tables()
        for column in table.columns
        if not column.nullable and column.default is None and column.server_default is None
    }
    assert unfilled == caller_supplied, unfilled.symmetric_difference(caller_supplied)


def test_every_pr_timestamp_the_database_owns_carries_a_server_default() -> None:
    """``created_at`` and ``updated_at`` are the database's clock, everywhere."""
    for table in pr_tables():
        for name in ("created_at", "updated_at"):
            column = table.columns.get(name)
            if column is None:
                continue
            assert isinstance(column.type, DateTime)
            assert column.type.timezone is True, f"{table.name}.{name} is naive"
            assert column.server_default is not None, f"{table.name}.{name}"


def test_every_pr_timestamp_column_is_timezone_aware() -> None:
    for table in pr_tables():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone is True, f"{table.name}.{column.name}"


# --- Defaults the specification names --------------------------------------


def test_priority_and_stage_defaults_are_the_ones_specified() -> None:
    content = PrContentItem.__table__.columns
    assert content["priority"].default.arg is PrPriority.NORMAL  # type: ignore[union-attr]
    assert content["workflow_stage"].default.arg is PrWorkflowStage.IDEA  # type: ignore[union-attr]
    assert content["priority"].server_default.arg == "NORMAL"  # type: ignore[union-attr]
    assert content["workflow_stage"].server_default.arg == "IDEA"  # type: ignore[union-attr]

    task = PrTask.__table__.columns
    assert task["priority"].default.arg is PrPriority.NORMAL  # type: ignore[union-attr]
    assert task["status"].default.arg is PrTaskStatus.TODO  # type: ignore[union-attr]
    assert task["status"].server_default.arg == "TODO"  # type: ignore[union-attr]


def test_a_task_completion_time_is_never_set_by_the_database() -> None:
    """Nothing derives ``completed_at`` from ``status``.

    A trigger doing that would make the two columns impossible to disagree,
    which sounds desirable until a task is reopened and the original completion
    time is gone.
    """
    completed = PrTask.__table__.columns["completed_at"]
    assert completed.nullable
    assert completed.default is None
    assert completed.server_default is None
    assert completed.onupdate is None


def test_the_channel_assignment_unique_index_covers_open_rows_only() -> None:
    """Open means ``effective_to IS NULL`` - not "in force today".

    The index refuses a second *open* row for a (channel, person, role) and
    nothing more. It is not an overlap constraint, and
    :func:`test_no_overlap_constraint_is_declared_on_channel_assignments`
    is the assertion that stops anybody describing it as one.
    """
    index = next(
        candidate
        for candidate in PrChannelAssignment.__table__.indexes
        if candidate.name == "uq_pr_channel_assignments_open_role"
    )
    assert index.unique
    assert [column.name for column in index.columns] == [
        "channel_id",
        "user_id",
        "assignment_role",
    ]
    assert "effective_to IS NULL" in str(index.dialect_options["postgresql"]["where"])


def test_no_overlap_constraint_is_declared_on_channel_assignments() -> None:
    """Step 1A does not prevent overlapping assignment periods.

    Asserted rather than merely documented, so that a future change which does
    add an exclusion constraint or a date-range unique index has to update the
    deferred invariant in ``docs/pr/STEP_1A_PR_CORE_FOUNDATION.md`` at the same
    time. The only unique index on this table is the open-row one; the only
    ``CHECK`` constraints are the range rules, neither of which looks at
    another row.
    """
    table = PrChannelAssignment.__table__
    unique_indexes = {index.name for index in table.indexes if index.unique}
    assert unique_indexes == {"uq_pr_channel_assignments_open_role"}

    check_names = {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert check_names == {
        "ck_pr_channel_assignments_allocation_percent_in_range",
        "ck_pr_channel_assignments_effective_period_ordered",
    }
    # No PostgreSQL exclusion constraint, and no extension is required by this
    # module - both were considered and deferred to Step 1B.
    assert not any(
        constraint.__class__.__name__ == "ExcludeConstraint" for constraint in table.constraints
    )


def test_a_content_item_may_target_many_channels_but_each_only_once() -> None:
    """The schema half of requirements 9 and 10."""
    target = PrContentTarget.__table__
    pair = next(
        index
        for index in target.indexes
        if [column.name for column in index.columns] == ["content_id", "channel_id"]
    )
    assert pair.unique
    # ``content_id`` alone is not unique - that is what lets one item fan out.
    assert not any(
        index.unique
        for index in target.indexes
        if [column.name for column in index.columns] == ["content_id"]
    )


def test_a_task_may_have_many_assignees_but_not_the_same_person_twice_per_role() -> None:
    """The schema half of requirements 11 and 12."""
    assignments = PrTaskAssignment.__table__
    triple = next(
        index
        for index in assignments.indexes
        if [column.name for column in index.columns] == ["task_id", "user_id", "assignment_role"]
    )
    assert triple.unique
    assert not any(
        index.unique
        for index in assignments.indexes
        if [column.name for column in index.columns] == ["task_id"]
    )
