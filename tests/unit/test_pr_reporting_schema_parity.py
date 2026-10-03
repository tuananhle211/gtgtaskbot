"""The PR reporting foundation's shape, held still.

These tests run offline and cannot see a real PostgreSQL - only
``tests/integration/test_pr_reporting_migrations.py`` can, and it is the one
that proves migration 0013 and the models agree on a database built the way
production builds one. What this file does is hold the *structural* promises of
Step 1B in place, so that breaking one costs seconds rather than a report that
quietly prints the wrong number:

* exactly nine tables were added, and Step 1A's eleven were left alone;
* ``views`` and ``reach`` are separate columns on both snapshot tables;
* the two snapshot tables stay append-only;
* every reference to a person still points at the one ``users`` table;
* no URL and no storage path is a key;
* the enum vocabularies in the migration still match the domain enums, and the
  metric column lists the non-negative constraints are built from still match
  the columns those tables actually have;
* every ``NOT NULL`` column the ORM omits from its ``INSERT`` has a server
  default - the exact defect revision 0011 was written to repair;
* Step 1B introduced no Excel dependency, no report generator, no scheduled
  job and no Telegram surface.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import BigInteger, DateTime, Table

import meobot.db.models
from meobot.db.base import Base
from meobot.db.models.pr import USERS_TABLE
from meobot.db.models.pr_reporting import (
    CHANNEL_EXPANSION_METRIC_COLUMNS,
    CHANNEL_METRIC_COLUMNS,
    CHANNEL_WINDOW_METRIC_COLUMNS,
    POST_METRIC_COLUMNS,
    WEEKLY_INPUT_SIGNED_COLUMNS,
    WEEKLY_INPUT_VALUE_COLUMNS,
    PrAction,
    PrChannelMetricSnapshot,
    PrIssue,
    PrPostMetricSnapshot,
    PrPublication,
    PrReportArtifact,
    PrReportingPeriod,
    PrReportRun,
    PrWeeklyManualInput,
)
from meobot.domain.pr.reporting import (
    PrActionStatus,
    PrArtifactFormat,
    PrIssueSeverity,
    PrIssueStatus,
    PrMetricSource,
    PrPeriodStatus,
    PrPeriodType,
    PrPublicationStatus,
    PrReportRunStatus,
    PrReportTriggerType,
    PrReportType,
    PrWeeklyInputStatus,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "meobot"

STEP_1B_MODELS = (
    PrReportingPeriod,
    PrPublication,
    PrPostMetricSnapshot,
    PrChannelMetricSnapshot,
    PrWeeklyManualInput,
    PrIssue,
    PrAction,
    PrReportRun,
    PrReportArtifact,
)

#: The nine tables Step 1B creates. Spelled out rather than derived from the
#: models, so that deleting a model cannot quietly delete its own assertion.
EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "pr_reporting_periods",
        "pr_publications",
        "pr_post_metric_snapshots",
        "pr_channel_metric_snapshots",
        "pr_weekly_manual_inputs",
        "pr_issues",
        "pr_actions",
        "pr_report_runs",
        "pr_report_artifacts",
    }
)

#: Step 1A's eleven, which this revision must not touch.
STEP_1A_TABLES: frozenset[str] = frozenset(
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

#: Tables that record an observation and are never edited afterwards.
APPEND_ONLY_TABLES = (PrPostMetricSnapshot, PrChannelMetricSnapshot)


@pytest.fixture(scope="module")
def revision_0013() -> Any:
    """Migration 0013, imported as a module so its literal lists can be read."""
    path = ROOT / "alembic" / "versions" / "0013_pr_reporting_foundation.py"
    spec = importlib.util.spec_from_file_location("meobot_migration_0013", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def revision_0027() -> Any:
    """Migration 0027, imported so Step 1F.2.4a's column list can be read too."""
    path = ROOT / "alembic" / "versions" / "0027_pr_channel_metrics.py"
    spec = importlib.util.spec_from_file_location("meobot_migration_0027", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def revision_0030() -> Any:
    """Migration 0030, imported so Step 1F.2.4d's column list can be read too."""
    path = ROOT / "alembic" / "versions" / "0030_pr_facebook_metrics_expansion.py"
    spec = importlib.util.spec_from_file_location("meobot_migration_0030", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reporting_tables() -> list[Table]:
    return [Base.metadata.tables[name] for name in sorted(EXPECTED_TABLES)]


# --- Requirement 4: exactly nine tables, and Step 1A untouched -------------


def test_step_1b_adds_exactly_nine_tables(revision_0013: Any) -> None:
    """Requirement 4.

    A subset, not an equality: later steps add more ``pr_`` tables - Step 1A1's
    ``pr_ai_reviews`` arrives in 0014 - and an equality here would fail every
    time the module grows, which says nothing about Step 1B. What must stay
    true is that *this* revision's nine are all present, are disjoint from Step
    1A's eleven, and are exactly what 0013 creates and drops.
    """
    assert len(EXPECTED_TABLES) == 9
    registered = {name for name in Base.metadata.tables if name.startswith("pr_")}
    assert registered >= EXPECTED_TABLES | STEP_1A_TABLES
    assert EXPECTED_TABLES.isdisjoint(STEP_1A_TABLES)

    # And the migration creates those nine and no others.
    source = (ROOT / "alembic" / "versions" / "0013_pr_reporting_foundation.py").read_text(
        encoding="utf-8"
    )
    created = set(re.findall(r'op\.create_table\(\s*"([^"]+)"', source))
    dropped = set(re.findall(r'op\.drop_table\("([^"]+)"\)', source))
    assert created == EXPECTED_TABLES
    assert dropped == EXPECTED_TABLES


def test_migration_0013_follows_0012(revision_0013: Any) -> None:
    assert revision_0013.revision == "0013"
    assert revision_0013.down_revision == "0012"


def test_migration_0012_is_left_alone() -> None:
    """Rewriting a migration that has already run is how two deployments end up
    with different schemas at the same revision number."""
    source = (ROOT / "alembic" / "versions" / "0012_pr_core_foundation.py").read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "0012"' in source
    assert 'down_revision: str | None = "0011"' in source
    for table in EXPECTED_TABLES:
        assert table not in source, f"0012 was edited to mention {table}"


def test_step_1b_alters_no_step_1a_table(revision_0013: Any) -> None:
    """Additive only: no ``ALTER``, no column change, no dropped constraint."""
    source = (ROOT / "alembic" / "versions" / "0013_pr_reporting_foundation.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "op.add_column",
        "op.drop_column",
        "op.alter_column",
        "op.drop_constraint",
        "op.rename_table",
        "op.execute",
    ):
        assert forbidden not in source, f"0013 uses {forbidden}"
    # No extension and no trigger, as specified.
    assert "CREATE EXTENSION" not in source
    assert "CREATE TRIGGER" not in source


def test_every_step_1b_model_is_exported_from_the_models_package() -> None:
    """Alembic autogenerate only sees what the package imports."""
    for model in STEP_1B_MODELS:
        assert model.__name__ in meobot.db.models.__all__
        assert getattr(meobot.db.models, model.__name__) is model


# --- Requirement 10: views and reach are different numbers -----------------


@pytest.mark.parametrize("model", APPEND_ONLY_TABLES)
def test_views_and_reach_are_separate_columns(model: Any) -> None:
    """Requirement 10.

    One counts plays, the other counts people. A schema that stores whichever
    the platform happened to expose produces a report nobody can reconcile
    against the platform's own dashboard, so both columns exist on both
    snapshot tables and neither is derived from the other.
    """
    columns = model.__table__.columns
    assert "views" in columns
    assert "reach" in columns
    assert columns["views"] is not columns["reach"]
    for name in ("views", "reach"):
        column = columns[name]
        assert isinstance(column.type, BigInteger)
        assert column.nullable, f"{model.__tablename__}.{name} must be optional"
        # Neither is computed, defaulted or filled in from the other.
        assert column.default is None
        assert column.server_default is None
        assert column.onupdate is None


@pytest.mark.parametrize(
    ("model", "prefix"),
    [
        (PrPostMetricSnapshot, ("publication_id", "observed_at")),
        (PrChannelMetricSnapshot, ("channel_id", "observed_at")),
    ],
)
def test_no_index_duplicates_the_unique_index_leftmost_prefix(
    model: Any, prefix: tuple[str, str]
) -> None:
    """The redundant trend-line indexes were removed on purpose.

    Each snapshot table's unique index is a B-tree over
    ``(subject, observed_at, source)``. PostgreSQL can use a B-tree for any
    query constraining a **leftmost prefix** of its columns, so a lookup on the
    subject alone, or on the subject plus an ``observed_at`` range, is already
    served by it. A standalone ``(subject, observed_at)`` index would answer
    exactly the same queries while costing a second write on every append to a
    table that only ever appends.

    Asserted rather than merely documented, so that re-adding one has to be a
    deliberate change to this test.
    """
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in model.__table__.indexes
    }
    unique = {name: columns for name, columns in indexes.items() if name.startswith("uq_")}

    # The observation index: ``(subject, observed_at, source)``. Step 1F.2.4b
    # added a second unique index to the channel table for sync idempotency -
    # ``(channel_id, provider_reading_key)`` - which is not a duplicate of this
    # one: it shares only the leftmost column, answers a different question
    # ("has this provider reading already been recorded"), and covers a
    # different set of rows (partial, over the automatic ones). So the
    # observation index is picked out by name rather than by being the only one.
    observation = next(
        columns for name, columns in unique.items() if name.endswith("_observed_source")
    )
    assert observation[:2] == prefix
    assert observation[2] == "source"

    for name, columns in unique.items():
        if name.endswith("_observed_source"):
            continue
        # Any *other* unique index must be doing something this one cannot.
        assert columns != observation, f"{name} duplicates the observation index"
        assert observation[: len(columns)] != columns, f"{name} is a redundant prefix"

    for name, columns in indexes.items():
        if name.startswith("uq_"):
            continue
        assert columns != prefix, f"{name} duplicates the unique index prefix"
        # And no non-unique index is a leftmost prefix of the unique one.
        assert observation[: len(columns)] != columns, f"{name} is redundant"


def test_impressions_is_a_third_number_again() -> None:
    """A platform may report all three, and they may all differ."""
    for model in APPEND_ONLY_TABLES:
        assert {"views", "reach", "impressions"} <= set(model.__table__.columns.keys())


# --- Requirement 13: the snapshot tables stay append-only ------------------


@pytest.mark.parametrize("model", APPEND_ONLY_TABLES)
def test_metric_snapshots_have_no_updated_at(model: Any) -> None:
    """Requirement 13. A reading that can be edited is not a reading."""
    columns = model.__table__.columns
    assert "updated_at" not in columns
    assert "created_at" in columns
    # ``observed_at`` is the instant the reading describes and is distinct from
    # the instant the row was written: a backfilled import is created today and
    # observed in March.
    assert "observed_at" in columns
    assert columns["observed_at"] is not columns["created_at"]


def test_report_artifacts_have_no_updated_at() -> None:
    """Its only mutation is stamping ``delivered_at``, a dated fact of its own."""
    columns = PrReportArtifact.__table__.columns
    assert "updated_at" not in columns
    assert "created_at" in columns
    assert "delivered_at" in columns


@pytest.mark.parametrize("model", APPEND_ONLY_TABLES)
def test_nothing_in_a_snapshot_is_recomputed_on_update(model: Any) -> None:
    """No ``onupdate`` anywhere: an append-only row has no update path."""
    for column in model.__table__.columns:
        assert column.onupdate is None, f"{model.__tablename__}.{column.name}"


# --- Requirements 20 and 21: identity and delete behaviour -----------------


def test_every_user_reference_points_at_the_existing_users_table() -> None:
    """Requirement 20.

    Collected by walking the foreign keys rather than by reading the model
    source, so a new table that invents its own person table fails here even if
    nobody remembers to add an assertion for it.
    """
    assert USERS_TABLE == "users"
    person_columns: dict[str, str] = {}
    for table in reporting_tables():
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
        "pr_actions.owner_user_id",
        "pr_issues.owner_user_id",
        "pr_publications.publisher_user_id",
        "pr_report_runs.requested_by_user_id",
        "pr_weekly_manual_inputs.approved_by_user_id",
        "pr_weekly_manual_inputs.entered_by_user_id",
        # Step 1F.2.4a. The seventh, and the first on a snapshot table: a
        # hand-entered reading records who entered it, in the row itself, so
        # "ai nhập chỉ số này?" is answered by the history rather than by
        # searching an audit log.
        "pr_channel_metric_snapshots.recorded_by_user_id",
    }


def test_no_second_identity_table_was_added() -> None:
    """There is one identity table in MeoBot and it is ``users``."""
    smell = re.compile(r"staff|employee|personnel|people|member_profile|pr_users?\b")
    offenders = [name for name in Base.metadata.tables if smell.search(name)]
    assert offenders == [], offenders
    assert "users" in Base.metadata.tables


def test_every_step_1b_foreign_key_is_restrict() -> None:
    """Requirement 21.

    Nothing cascades and nothing is set to null. Deleting a user who entered a
    week's numbers, a channel that has been measured, a period that has been
    reported, a publication with metrics or a run that produced a file must
    fail rather than erase the history hanging off it.
    """
    checked = 0
    for table in reporting_tables():
        for constraint in table.foreign_key_constraints:
            assert constraint.ondelete == "RESTRICT", f"{table.name}.{constraint.name}"
            checked += 1
    # One self-reference, seven person references and twelve references to a
    # channel, a content item, a period, a publication, an issue or a run.
    # Twenty-one since Step 1F.2.3f, which added ``production_submission_id``
    # and ``derivative_id`` to ``pr_publications``. Both ``RESTRICT``: a
    # published file may not be deleted out from under the record of where it
    # was posted. Twenty-two since Step 1F.2.4a added
    # ``pr_channel_metric_snapshots.recorded_by_user_id`` - also ``RESTRICT``,
    # so removing the person who typed the numbers in cannot quietly remove
    # that they typed them. The count is asserted as well as the rule, so a key
    # added with the wrong ``ondelete`` cannot hide behind "all of them
    # restrict".
    assert checked == 22, checked


def test_the_period_self_reference_is_restrict_and_nullable() -> None:
    """A period points at the one before it, and deleting that one fails."""
    column = PrReportingPeriod.__table__.columns["previous_period_id"]
    assert column.nullable
    target = next(iter(column.foreign_keys))
    assert target.column.table.name == "pr_reporting_periods"
    assert target.ondelete == "RESTRICT"


# --- Neither a URL nor a storage path is a key -----------------------------


def test_no_url_or_storage_path_is_used_as_a_key() -> None:
    """A URL changes when a handle is renamed; a file moves when disks fill.

    Every relationship here is a UUID, so neither can orphan a row.
    """
    for table in reporting_tables():
        for column in table.columns:
            if "url" not in column.name and "storage_path" not in column.name:
                continue
            assert not column.foreign_keys, f"{table.name}.{column.name} is a foreign key"
            assert not column.unique, f"{table.name}.{column.name} is unique"
            assert not column.index, f"{table.name}.{column.name} is indexed"
        for index in table.indexes:
            names = {column.name for column in index.columns}
            assert not any("url" in name or "storage_path" in name for name in names), (
                f"{table.name}.{index.name}"
            )

    # The platform's own identifier is what a collector matches on, and it is a
    # separate column from the URL precisely so the URL never has to be one.
    assert "platform_post_id" in PrPublication.__table__.columns
    assert "url" in PrPublication.__table__.columns


# --- The migration and the models say the same thing -----------------------


#: Values a **later** revision legitimately added to a Step 1B vocabulary.
#:
#: Every enum column here is ``VARCHAR`` with ``native_enum=False`` and no
#: ``CHECK`` - see ``meobot.db.base.value_enum`` and 0013's ``_enum`` - so
#: widening one of these vocabularies needs no DDL at all, and a migration that
#: emitted an ``ALTER`` for it would be doing nothing. 0023 made the same
#: argument when it added ``CRITICAL`` to the priority enum.
#:
#: Listing the additions rather than relaxing the assertion to a subset check is
#: the point: a value **removed** from the domain, or a typo in one, still fails
#: here, and adding a member without an entry below is a deliberate act rather
#: than a silent drift.
LATER_ADDITIONS: dict[str, tuple[str, ...]] = {
    # Step 1F.2.3f.1. A publication recorded in error and taken back. Not
    # ``REMOVED``, which says the post existed and was pulled down - see
    # ``PrPublicationStatus``.
    "PUBLICATION_STATUSES": ("REVERSED",),
}


def test_the_migration_enum_lists_match_the_domain_enums(revision_0013: Any) -> None:
    """The stored vocabulary is one vocabulary.

    The migration spells its values out as literals on purpose - it has to keep
    meaning what it meant on the day it ran. This is the assertion that keeps
    that literal list and the domain enum in step, so adding a report status to
    Python without widening the column is caught here.

    ``LATER_ADDITIONS`` is how a legitimate widening is recorded: the migration's
    list plus what a named later step added must be exactly the enum, in order.
    """
    expected = {
        "PERIOD_TYPES": PrPeriodType,
        "PERIOD_STATUSES": PrPeriodStatus,
        "PUBLICATION_STATUSES": PrPublicationStatus,
        "METRIC_SOURCES": PrMetricSource,
        "WEEKLY_INPUT_STATUSES": PrWeeklyInputStatus,
        "ISSUE_SEVERITIES": PrIssueSeverity,
        "ISSUE_STATUSES": PrIssueStatus,
        "ACTION_STATUSES": PrActionStatus,
        "REPORT_TYPES": PrReportType,
        "REPORT_TRIGGER_TYPES": PrReportTriggerType,
        "REPORT_RUN_STATUSES": PrReportRunStatus,
        "ARTIFACT_FORMATS": PrArtifactFormat,
    }
    for attribute, enum_type in expected.items():
        written = getattr(revision_0013, attribute) + LATER_ADDITIONS.get(attribute, ())
        assert written == tuple(member.value for member in enum_type), attribute


def test_a_widened_vocabulary_needs_no_ddl() -> None:
    """Step 1F.2.3f.1: why ``REVERSED`` shipped without a migration.

    The column is a plain ``VARCHAR(20)``. ``sa.Enum(..., native_enum=False)``
    emits no ``CHECK`` - ``create_constraint`` defaults to ``False`` - so the
    database has never constrained which of these strings it holds, and adding
    one is a Python change and nothing else. Asserted rather than asserted-in-a-
    docstring, because the day it stops being true is the day a release needs a
    migration nobody wrote.
    """
    column = PrPublication.__table__.c.status
    assert isinstance(column.type, sa.Enum)
    assert column.type.native_enum is False
    assert column.type.create_constraint is False
    assert column.type.length == 20
    # And no table-level check names it either.
    conditions = " ".join(
        str(constraint.sqltext)
        for constraint in PrPublication.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    )
    assert "status" not in conditions


def test_the_migration_metric_column_lists_match_the_models(revision_0013: Any) -> None:
    """The non-negative constraints are built from these lists in both places.

    Adding a metric column to a model without widening the migration's list
    would leave the new column silently unconstrained. This is what stops that.
    """
    assert revision_0013.POST_METRIC_COLUMNS == POST_METRIC_COLUMNS
    assert revision_0013.CHANNEL_METRIC_COLUMNS == CHANNEL_METRIC_COLUMNS
    assert revision_0013.WEEKLY_INPUT_VALUE_COLUMNS == WEEKLY_INPUT_VALUE_COLUMNS
    assert revision_0013.WEEKLY_INPUT_SIGNED_COLUMNS == WEEKLY_INPUT_SIGNED_COLUMNS


def _numeric_columns(model: Any, *, ignore: set[str]) -> set[str]:
    return {
        column.name
        for column in model.__table__.columns
        if column.name not in ignore
        and type(column.type).__name__ in {"BigInteger", "Integer", "Numeric"}
    }


def test_every_listed_metric_column_actually_exists() -> None:
    """A constraint naming a column that is not there is a constraint on nothing.

    Step 1F.2.4a split the channel table's list in two and Step 1F.2.4d made it
    three. Each revision's tuple stays frozen at what that revision created - a
    parity test below holds each model list and its revision to the same
    columns - and each has its own check constraint. The numeric sweep is over
    the **union**, so a metric added to any of the three without being
    constrained still fails here rather than shipping.
    """
    pairs = (
        (PrPostMetricSnapshot, POST_METRIC_COLUMNS),
        (
            PrChannelMetricSnapshot,
            CHANNEL_METRIC_COLUMNS
            + CHANNEL_WINDOW_METRIC_COLUMNS
            + CHANNEL_EXPANSION_METRIC_COLUMNS,
        ),
    )
    for model, columns in pairs:
        present = set(model.__table__.columns.keys())
        assert set(columns) <= present, set(columns) - present
        # And every numeric column of the table is covered by the list, so a
        # new metric cannot be added without also being constrained. Neither
        # snapshot table has a signed column.
        numeric = _numeric_columns(model, ignore=set())
        assert numeric == set(columns), numeric.symmetric_difference(set(columns))

    # The three lists name different columns. An overlap would mean one column
    # carried by two constraints, which is not wrong but is a sign somebody
    # widened a frozen list by accident.
    thirds = (
        CHANNEL_METRIC_COLUMNS,
        CHANNEL_WINDOW_METRIC_COLUMNS,
        CHANNEL_EXPANSION_METRIC_COLUMNS,
    )
    for index, left in enumerate(thirds):
        for right in thirds[index + 1 :]:
            assert set(left).isdisjoint(right), set(left) & set(right)


def test_the_1f24a_metric_column_list_matches_its_migration(revision_0027: Any) -> None:
    """Step 1F.2.4a's columns and its constraint are built from one list.

    The same guard :func:`test_the_migration_metric_column_lists_match_the_models`
    applies to 0013, for the same reason: a column added to the model without
    widening the revision's tuple would ship unconstrained on a real database.
    """
    assert revision_0027.CHANNEL_WINDOW_METRIC_COLUMNS == CHANNEL_WINDOW_METRIC_COLUMNS


def test_the_1f24d_metric_column_list_matches_its_migration(revision_0030: Any) -> None:
    """Step 1F.2.4d's six columns and its constraint are built from one list.

    The third time this guard has been needed and the third time it is the same
    guard: a column added to the model without widening the revision's tuple
    would ship unconstrained on a real database, where the model's
    ``CheckConstraint`` is only ever consulted by the offline suite.
    """
    assert revision_0030.CHANNEL_EXPANSION_METRIC_COLUMNS == CHANNEL_EXPANSION_METRIC_COLUMNS


def test_page_likes_and_followers_are_separate_columns() -> None:
    """Step 1F.2.4d. ``fans`` is not a second name for ``followers``.

    Meta split the two and they have diverged permanently - a fan liked the
    Page, a follower receives its posts - so a schema that stored one would have
    to choose which of two numbers every Facebook report quotes to throw away.
    Both are nullable because most platforms have only one of them.
    """
    columns = PrChannelMetricSnapshot.__table__.columns
    assert "fans" in columns and "followers" in columns
    assert columns["fans"].nullable and columns["followers"].nullable
    assert type(columns["fans"].type).__name__ == "BigInteger"


def test_the_windowed_post_count_is_not_the_lifetime_one() -> None:
    """Step 1F.2.4d. ``posts_count`` and ``posts_count_30d`` are both present.

    The windowless column is a lifetime total; the windowed ones count what was
    published inside the window, and only the second kind can be the denominator
    of an average. Collapsing them would have made "trung bình mỗi bài" wrong by
    orders of magnitude and entirely plausible on a card.
    """
    columns = set(PrChannelMetricSnapshot.__table__.columns.keys())
    assert {"posts_count", "posts_count_7d", "posts_count_30d"} <= columns
    # And reactions did not quietly become likes.
    assert {"reactions_30d", "likes_30d"} <= columns


def test_the_recorder_is_attributable_and_optional() -> None:
    """Step 1F.2.4a. Who typed a reading in, where the reading itself can say it.

    Nullable because an ``API`` row has no author and neither does anything
    written before 0027; ``RESTRICT`` because deleting a person who entered
    numbers must not quietly remove that they entered them.
    """
    column = PrChannelMetricSnapshot.__table__.columns["recorded_by_user_id"]
    assert column.nullable
    assert column.default is None and column.server_default is None
    key = next(iter(column.foreign_keys))
    assert key.column.table.name == "users"
    assert key.ondelete == "RESTRICT"


def test_every_numeric_weekly_column_is_constrained_or_declared_signed() -> None:
    """Every number on the weekly table is in exactly one of the two tuples.

    ``members_gain`` is deliberately outside the non-negative constraint, and
    the way that stays a *decision* rather than an oversight is that it has to
    be named in ``WEEKLY_INPUT_SIGNED_COLUMNS``. A new numeric column that is
    in neither tuple fails here rather than shipping unconstrained.
    """
    present = set(PrWeeklyManualInput.__table__.columns.keys())
    assert set(WEEKLY_INPUT_VALUE_COLUMNS) <= present
    assert set(WEEKLY_INPUT_SIGNED_COLUMNS) <= present

    # The two tuples do not overlap: a column is constrained or it is signed.
    assert set(WEEKLY_INPUT_VALUE_COLUMNS).isdisjoint(WEEKLY_INPUT_SIGNED_COLUMNS)

    # ``version_no`` has its own ``>= 1`` constraint and is not a measurement.
    numeric = _numeric_columns(PrWeeklyManualInput, ignore={"version_no"})
    assert numeric == set(WEEKLY_INPUT_VALUE_COLUMNS) | set(WEEKLY_INPUT_SIGNED_COLUMNS)


def test_members_gain_is_signed_and_members_end_is_not() -> None:
    """A count cannot be negative; a delta can.

    ``members_end`` is the membership at the close of the period and is a
    count. ``members_gain`` is the change over the period: a group that lost
    members has a negative gain, and refusing to store that would make a real
    week unrecordable.
    """
    assert "members_gain" in WEEKLY_INPUT_SIGNED_COLUMNS
    assert "members_gain" not in WEEKLY_INPUT_VALUE_COLUMNS
    assert "members_end" in WEEKLY_INPUT_VALUE_COLUMNS
    assert "members_end" not in WEEKLY_INPUT_SIGNED_COLUMNS

    columns = PrWeeklyManualInput.__table__.columns
    gain = columns["members_gain"]
    assert isinstance(gain.type, BigInteger)
    assert gain.nullable
    assert gain.default is None, "members_gain must not be defaulted"
    assert gain.server_default is None, "members_gain must not be defaulted"

    # And the constraint really does leave it alone.
    constraint = next(
        candidate
        for candidate in PrWeeklyManualInput.__table__.constraints
        if candidate.name == "ck_pr_weekly_manual_inputs_values_not_negative"
    )
    sql = str(constraint.sqltext)  # type: ignore[attr-defined]
    assert "members_gain" not in sql
    assert "(members_end IS NULL OR members_end >= 0)" in sql


def test_the_non_negative_constraint_covers_every_listed_column() -> None:
    """The generated SQL mentions each column twice: null-check and range."""
    pairs = (
        (PrPostMetricSnapshot, POST_METRIC_COLUMNS, "metrics_not_negative"),
        (PrChannelMetricSnapshot, CHANNEL_METRIC_COLUMNS, "metrics_not_negative"),
        # Step 1F.2.4a's own constraint, over its own columns. See
        # ``test_every_listed_metric_column_actually_exists``.
        (
            PrChannelMetricSnapshot,
            CHANNEL_WINDOW_METRIC_COLUMNS,
            "window_metrics_not_negative",
        ),
        # Step 1F.2.4d's own constraint, over its own six columns.
        (
            PrChannelMetricSnapshot,
            CHANNEL_EXPANSION_METRIC_COLUMNS,
            "expansion_metrics_not_negative",
        ),
        (PrWeeklyManualInput, WEEKLY_INPUT_VALUE_COLUMNS, "values_not_negative"),
    )
    for model, columns, suffix in pairs:
        # ``NAMING_CONVENTION`` has already expanded the short suffix the model
        # declares into ``ck_<table>_<suffix>`` by the time metadata is built.
        name = f"ck_{model.__tablename__}_{suffix}"
        constraint = next(
            candidate
            for candidate in model.__table__.constraints
            if candidate.name == name and type(candidate).__name__ == "CheckConstraint"
        )
        sql = str(constraint.sqltext)  # type: ignore[attr-defined]
        for column in columns:
            assert f"({column} IS NULL OR {column} >= 0)" in sql, f"{name}: {column}"


def test_every_enum_value_is_a_stable_uppercase_code() -> None:
    """Name and value are the same string, so a rename cannot be silent."""
    for enum_type in (
        PrPeriodType,
        PrPeriodStatus,
        PrPublicationStatus,
        PrMetricSource,
        PrWeeklyInputStatus,
        PrIssueSeverity,
        PrIssueStatus,
        PrActionStatus,
        PrReportType,
        PrReportTriggerType,
        PrReportRunStatus,
        PrArtifactFormat,
    ):
        for member in enum_type:
            assert member.value == member.name
            assert member.value == member.value.upper()
            assert re.fullmatch(r"[A-Z][A-Z_]*", member.value), member.value


def test_every_enum_column_is_wide_enough_for_its_longest_value() -> None:
    """A ``VARCHAR(20)`` holding a 25-character status truncates or errors."""
    for table in reporting_tables():
        for column in table.columns:
            length = getattr(column.type, "length", None)
            enum_values = getattr(column.type, "enums", None)
            if length is None or not enum_values:
                continue
            longest = max(len(value) for value in enum_values)
            assert longest <= length, f"{table.name}.{column.name}: {longest} > {length}"


def test_no_postgresql_enum_type_is_created() -> None:
    """The repository stores enum values as ``VARCHAR``; 0013 does not deviate.

    Asserted rather than assumed, because introducing a native ``ENUM`` for the
    PR reporting columns would make them behave unlike every other enum column
    in the schema and turn a future value addition into an ``ALTER TYPE``.
    """
    for table in reporting_tables():
        for column in table.columns:
            if not getattr(column.type, "enums", None):
                continue
            assert getattr(column.type, "native_enum", None) is False, (
                f"{table.name}.{column.name} would create a PostgreSQL ENUM type"
            )
    source = (ROOT / "alembic" / "versions" / "0013_pr_reporting_foundation.py").read_text(
        encoding="utf-8"
    )
    # Read from the parsed tree rather than the text: the module docstring
    # explains the enum strategy and quotes ``sa.Enum(...)`` while doing so,
    # and prose about a call is not a call.
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Enum"
    ]
    assert len(calls) == 1, "every enum column must go through the one helper"
    native = {
        keyword.value.value
        for keyword in calls[0].keywords
        if keyword.arg == "native_enum" and isinstance(keyword.value, ast.Constant)
    }
    assert native == {False}, native
    assert "postgresql.ENUM" not in source


# --- Defaults: the 0011 lesson, applied to the new tables ------------------


def test_every_not_null_column_the_orm_omits_has_a_server_default() -> None:
    """A ``NOT NULL`` column with neither a Python nor a server default must be
    supplied by the caller on every insert. Anything else sends a ``NULL`` and
    fails at the first write. The list below is the complete set of columns
    Step 1B expects a caller to fill; everything else has a default.
    """
    caller_supplied = {
        "pr_reporting_periods.code",
        "pr_reporting_periods.period_type",
        "pr_reporting_periods.date_start",
        "pr_reporting_periods.date_end",
        "pr_publications.code",
        "pr_publications.content_id",
        "pr_publications.channel_id",
        "pr_publications.published_at",
        "pr_post_metric_snapshots.publication_id",
        "pr_post_metric_snapshots.observed_at",
        "pr_post_metric_snapshots.source",
        "pr_channel_metric_snapshots.channel_id",
        "pr_channel_metric_snapshots.observed_at",
        "pr_channel_metric_snapshots.source",
        "pr_weekly_manual_inputs.channel_id",
        "pr_weekly_manual_inputs.period_id",
        "pr_weekly_manual_inputs.version_no",
        "pr_weekly_manual_inputs.entered_by_user_id",
        "pr_issues.code",
        "pr_issues.title",
        "pr_actions.issue_id",
        "pr_actions.description",
        "pr_actions.owner_user_id",
        "pr_report_runs.report_type",
        "pr_report_runs.period_id",
        "pr_report_runs.idempotency_key",
        "pr_report_runs.template_version",
        "pr_report_runs.trigger_type",
        "pr_report_runs.source_cutoff_at",
        "pr_report_artifacts.report_run_id",
        "pr_report_artifacts.artifact_format",
        "pr_report_artifacts.version_no",
        "pr_report_artifacts.file_name",
        "pr_report_artifacts.storage_path",
        "pr_report_artifacts.mime_type",
    }
    unfilled = {
        f"{table.name}.{column.name}"
        for table in reporting_tables()
        for column in table.columns
        if not column.nullable and column.default is None and column.server_default is None
    }
    assert unfilled == caller_supplied, unfilled.symmetric_difference(caller_supplied)


#: Every defaulted enum column, as ``(model, column, member)``. The ORM default
#: and the server default must be the *same* value in every one of them: a
#: model that defaults to ``OPEN`` while the column defaults to ``PENDING``
#: produces rows whose status depends on whether the ORM or raw SQL wrote them.
DEFAULTED_ENUM_COLUMNS = (
    ("pr_reporting_periods", "status", PrPeriodStatus.OPEN),
    ("pr_publications", "status", PrPublicationStatus.PUBLISHED),
    ("pr_weekly_manual_inputs", "status", PrWeeklyInputStatus.DRAFT),
    ("pr_issues", "severity", PrIssueSeverity.MEDIUM),
    ("pr_issues", "status", PrIssueStatus.OPEN),
    ("pr_actions", "status", PrActionStatus.TODO),
    ("pr_report_runs", "status", PrReportRunStatus.PENDING),
)


@pytest.mark.parametrize(("table_name", "column_name", "member"), DEFAULTED_ENUM_COLUMNS)
def test_the_orm_default_and_the_server_default_are_the_same_value(
    table_name: str, column_name: str, member: Any
) -> None:
    """Requirement: model and migration defaults remain identical."""
    column = Base.metadata.tables[table_name].columns[column_name]
    assert column.default is not None, f"{table_name}.{column_name} has no ORM default"
    assert column.default.arg is member  # type: ignore[union-attr]
    assert column.server_default is not None, f"{table_name}.{column_name}"
    assert column.server_default.arg == member.value  # type: ignore[union-attr]
    # The stored value is the stable uppercase code, not the member's repr.
    assert member.value == member.value.upper()


def test_the_non_enum_defaults_are_the_ones_specified() -> None:
    runs = PrReportRun.__table__.columns
    assert runs["missing_data_count"].default.arg == 0  # type: ignore[union-attr]
    assert runs["missing_data_count"].server_default.arg == "0"  # type: ignore[union-attr]

    issues = PrIssue.__table__.columns
    assert issues["needs_management_decision"].default.arg is False  # type: ignore[union-attr]
    assert issues["needs_management_decision"].server_default is not None


def test_report_type_and_trigger_type_are_deliberately_undefaulted() -> None:
    """Neither has a value that is right more often than it is wrong.

    A run is weekly or monthly, and manual or scheduled or a retry. Defaulting
    either would file a run under a heading nobody chose, and the wrong heading
    on a report run is worse than a caller having to say which it is.
    """
    runs = PrReportRun.__table__.columns
    for name in ("report_type", "trigger_type"):
        assert runs[name].default is None, f"pr_report_runs.{name} gained a default"
        assert runs[name].server_default is None, f"pr_report_runs.{name} gained a default"
        assert not runs[name].nullable


def test_every_timestamp_column_is_timezone_aware() -> None:
    for table in reporting_tables():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone is True, f"{table.name}.{column.name}"


def test_every_created_and_updated_timestamp_carries_a_server_default() -> None:
    """``created_at`` and ``updated_at`` are the database's clock, everywhere."""
    for table in reporting_tables():
        for name in ("created_at", "updated_at"):
            column = table.columns.get(name)
            if column is None:
                continue
            assert column.server_default is not None, f"{table.name}.{name}"


def test_uuids_remain_the_primary_keys() -> None:
    """A code is a label. Every relationship in this module is a UUID."""
    for table in reporting_tables():
        primary = [column.name for column in table.primary_key.columns]
        assert primary == ["id"], f"{table.name} primary key is {primary}"


# --- Nothing derives a completion time -------------------------------------


def test_no_completion_or_lifecycle_time_is_set_by_the_database() -> None:
    """Requirement: ``completed_at`` is never derived from ``status``.

    A trigger doing that would make the two impossible to disagree, which
    sounds desirable until a task is reopened and the original time is gone.
    The same reasoning covers a period's ``closed_at``/``locked_at``, an issue's
    ``resolved_at`` and an artifact's ``delivered_at``.
    """
    derived_candidates = {
        "pr_actions": ("completed_at",),
        "pr_issues": ("resolved_at",),
        "pr_reporting_periods": ("closed_at", "locked_at"),
        "pr_report_artifacts": ("delivered_at",),
        "pr_report_runs": ("started_at", "finished_at"),
        "pr_weekly_manual_inputs": ("submitted_at", "approved_at"),
    }
    for table_name, columns in derived_candidates.items():
        table = Base.metadata.tables[table_name]
        for name in columns:
            column = table.columns[name]
            assert column.nullable, f"{table_name}.{name}"
            assert column.default is None, f"{table_name}.{name}"
            assert column.server_default is None, f"{table_name}.{name}"
            assert column.onupdate is None, f"{table_name}.{name}"


# --- Requirement 22: Step 1B is a database change and nothing else ---------


def test_no_excel_library_is_imported_anywhere() -> None:
    """Requirement 22, the Excel half.

    ``PrArtifactFormat.XLSX`` is a stored code, not a workbook: Step 1B records
    that a file *will be* an XLSX and writes none. Nothing in the source tree
    imports a spreadsheet library, and no dependency provides one.
    """
    pattern = re.compile(r"^\s*(?:import|from)\s+(openpyxl|xlsxwriter|xlwt|pandas)\b", re.MULTILINE)
    offenders = [
        f"{path.relative_to(ROOT)}"
        for path in SRC.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], offenders

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = " ".join(pyproject["project"]["dependencies"]).lower()
    for library in ("openpyxl", "xlsxwriter", "xlwt", "pandas"):
        assert library not in declared, library


def test_no_module_outside_the_models_package_touches_a_reporting_table() -> None:
    """Requirement 22, the service half, as narrowed by Step 1C.

    Step 1B was a schema and nothing read it. **Step 1C made that deliberately
    false**, which this list records - exactly as the original version of this
    test said it would have to be, in the same change.

    Two services, and only two, now touch a reporting table:

    * ``pr_publication_service`` writes ``pr_publications`` - registering the
      fact that something went out is the whole of what it does;
    * ``pr_workflow_service`` reads ``pr_post_metric_snapshots`` for one
      question only, the ``PUBLISHED -> MEASURED`` precondition.

    Everything else - Telegram handlers, commands, Celery tasks, report
    renderers, spreadsheet writers - still must not appear here. That is what
    this test is now for.
    """
    allowed = {
        Path("src/meobot/db/models/pr_reporting.py"),
        Path("src/meobot/db/models/__init__.py"),
        Path("src/meobot/application/pr_publication_service.py"),
        Path("src/meobot/application/pr_workflow_service.py"),
        # Step 1E: the web API serialises a publication into a response model.
        # It reads the row the publication service already wrote, and does so
        # explicitly rather than dumping the ORM object - which is the only
        # reason this file names the class at all.
        Path("src/meobot/api/schemas/pr.py"),
        # Step 1F.2.3a: permanent content deletion. It names ``PrPublication`` to
        # **refuse** - a publication means the piece went out, and deleting one is
        # the thing that operation must never do - and ``PrIssue`` to detach a
        # report line from content that is going away. Neither reads a metric,
        # aggregates a week or renders anything, which is what this guard is for.
        Path("src/meobot/application/pr_lifecycle_service.py"),
        # Step 1F.2.3b: workflow undo. Same shape, same single reason - a
        # publication is what makes an internal-review approval final, so the
        # undo asks whether one exists and refuses. It reads no column of it.
        Path("src/meobot/application/pr_undo_service.py"),
        # Step 1F.2.3f: derivative production outputs. Same shape again, and the
        # same single question - ``EXISTS (SELECT … WHERE derivative_id = …)``,
        # asked to **refuse**: once something has been published from a file,
        # that file may not be deleted or moved, because the publication row is
        # then part of the answer to "what did we actually post". It reads no
        # metric, aggregates no week and renders nothing.
        Path("src/meobot/application/pr_content_asset_service.py"),
        # Step 1F.2.3f.2: the same question again, for the original handed-in
        # file rather than the re-cut. ``EXISTS`` against
        # ``production_submission_id``, asked so a location correction can be
        # refused once something has been published from it.
        Path("src/meobot/application/pr_production_service.py"),
        # Step 1F.2.3f.2: the action list asks the publication service whether a
        # contributor may record a posting. It names the service, never the
        # table - see ``test_41`` in ``test_pr_web_admin``.
        Path("src/meobot/application/pr_action_service.py"),
        # Step 1F.2.4b: the connector writes API snapshots into the same
        # timeline. Same shape as the manual writer below it - one table, its
        # own columns, no period aggregated and nothing rendered.
        Path("src/meobot/application/pr_channel_sync_service.py"),
        # Step 1F.2.4a: the one service that **writes** a metric snapshot.
        #
        # This is the first entry on the list that is not asking a publication
        # question, and it is a deliberate widening rather than a leak. Step 1B
        # built ``pr_channel_metric_snapshots`` and left it unwritten; Step
        # 1F.2.4a gave a person a form that appends to it. It touches that one
        # table, reads and writes only its own columns, aggregates no period and
        # renders nothing - which is exactly what the rest of this guard is still
        # for.
        Path("src/meobot/application/pr_channel_metrics_service.py"),
        # M2: the quota engine, and the first thing ever to read
        # ``pr_reporting_periods``.
        #
        # Step 1B built the table and left it with no reader at all; M2 attaches
        # KPI plans to it, which is exactly what Step 1B said periods were for.
        # Three files name it, each for one reason:
        #
        # * ``pr_work_period_service`` is the **only** place a date becomes a
        #   period. It exists so that no second calendar exists - a plan names a
        #   period row and the row owns the dates, rather than the module
        #   inventing its own notion of "September" out of ``day_bounds``;
        # * ``pr_work_quota_service`` reads a period's ``status`` to decide
        #   whether eligibility may be recomputed at all, and its dates to bound
        #   the candidates. It locks the row so two transactions cannot both
        #   think one quota slot remains;
        # * ``pr_work_plan_service`` refuses to approve a plan for a period that
        #   is not ``OPEN``.
        #
        # None of them aggregates a week, renders a report or writes a metric,
        # which is what this guard is still for. The one write is
        # ``ensure_month_period``, which appends a period row an administrator
        # asked for and is idempotent.
        Path("src/meobot/application/pr_work_period_service.py"),
        Path("src/meobot/application/pr_work_quota_service.py"),
        Path("src/meobot/application/pr_work_plan_service.py"),
        # M2: the wire shapes for the period picker and the plan screen. Reads
        # the row the period service already wrote, explicitly rather than
        # dumping the ORM object - the same reason ``schemas/pr.py`` is here.
        Path("src/meobot/api/schemas/pr_work_quota.py"),
        # M2: the quota engine's tables, which reference ``pr_reporting_periods``
        # by foreign key. A models module, like ``pr_reporting.py`` above it.
        Path("src/meobot/db/models/pr_work_quota.py"),
        # M3: the Content → Work projector, for two reasons and no others.
        #
        # * ``PrPublication`` is one of the three **sources** it reads. Step 1B's
        #   publication row is the durable record that something went out, and
        #   *"who posted this"* is a fact only that row holds - the projector
        #   reads ``publisher_user_id`` off it and writes a work item. It
        #   aggregates nothing and writes no publication;
        # * ``PrReportingPeriod`` is read to **refuse**. A projection whose
        #   effect would land in a ``CLOSED`` or ``LOCKED`` month is blocked, and
        #   asking the period is how it knows. It never writes one - M2's
        #   ``pr_work_period_service`` above is still the only thing that does.
        #
        # This guard exists to stop the reporting module being reimplemented
        # somewhere else. Reading a publication as evidence of work, and a
        # period as a boundary not to cross, is the opposite of that.
        Path("src/meobot/application/pr_content_work_projector.py"),
        # Step 1F.2.3f.4: the content board, scoped to a work month.
        #
        # *Đã đăng* used to be cumulative and grew for ever. It is now the pieces
        # published **in the selected month**, which cannot be decided without
        # knowing when a piece went out - and that fact lives in
        # ``pr_publications`` and nowhere else. Two files name it:
        #
        # * ``pr_content_query`` builds ``MIN(published_at)`` over an item's
        #   active publications as a **correlated scalar subquery**, and compares
        #   it against the month's bounds. One column of one table, in a
        #   ``WHERE``. It joins nothing, so a piece on three channels is still
        #   one card;
        # * ``pr_query_service`` asks the same aggregate once per page, grouped
        #   by content id, so a card can print the day it went out. One round
        #   trip for fifty cards, and skipped entirely when no month is selected.
        #
        # Neither writes a publication, aggregates a week or renders a report,
        # which is what this guard is for. This is a *board column* reading the
        # durable record of what went out - the same use M3's projector below
        # makes of it, one step further along.
        Path("src/meobot/application/pr_content_query.py"),
        Path("src/meobot/application/pr_query_service.py"),
        # M3: the projection queue table, which references ``pr_content_items``
        # and mentions ``pr_publications`` only in prose explaining why a
        # publication milestone is queued like any other. A models module.
        Path("src/meobot/db/models/pr_content_work.py"),
        # M6, and the reason is the same one M3's projector gives one line up:
        # a **reporting period is the unit a performance figure is computed for
        # and the boundary it must not cross.** Every M6 table is keyed on one,
        # the calculation reads one to decide the month's workdays, and the
        # review, recalculation and finalisation services read one to *refuse*
        # when it is CLOSED or LOCKED.
        #
        # None of them writes a period, aggregates a report, or produces a
        # reporting artifact - M2's ``pr_work_period_service`` is still the only
        # thing that creates one. This guard exists to stop the reporting module
        # being reimplemented elsewhere, and reading a period as a boundary not
        # to cross is the opposite of reimplementing it.
        Path("src/meobot/application/pr_performance_service.py"),
        Path("src/meobot/application/pr_performance_review_service.py"),
        Path("src/meobot/application/pr_performance_bonus_service.py"),
        Path("src/meobot/application/pr_performance_target_service.py"),
        # M6's tables, each carrying a ``reporting_period_id``. A models module.
        Path("src/meobot/db/models/pr_performance.py"),
        # M4B, and the reason is M3's projector's, one milestone on: the
        # recurring-work generator reads ``PrReportingPeriod`` to **refuse**.
        # An occurrence whose scheduled instant falls in a ``CLOSED`` or
        # ``LOCKED`` month is settled as skipped rather than generated, because
        # filing retroactive work into a shut month would move a number
        # underneath a report that already quotes it - and retrying tomorrow
        # would fail for the identical reason for ever.
        #
        # It writes no period, aggregates nothing and produces no artifact. This
        # guard exists to stop the reporting module being reimplemented
        # elsewhere, and a scheduler that asks a period where it may not go is
        # the opposite of that.
        Path("src/meobot/application/pr_work_recurring_generator.py"),
        # M4B's occurrence ledger, which references ``pr_reporting_periods`` by
        # foreign key so a skipped firing can name the month that blocked it. A
        # models module, like ``pr_work_quota.py`` above it.
        Path("src/meobot/db/models/pr_work_recurring.py"),
        # KPI workload visibility: the one plan-workload calculator resolves a
        # plan's period to price it on the period's last day. Reads only.
        Path("src/meobot/application/pr_plan_workload.py"),
        # Period containers (0039). A stream *is* a month: ``pr_work_items``
        # carries ``reporting_period_id``, the result service opens the month's
        # container and refuses a shut one, and the work service reads the
        # period to decide which open streams a renamed unit reaches. None of
        # them writes a period, aggregates anything or produces an artifact.
        Path("src/meobot/db/models/pr_work.py"),
        Path("src/meobot/application/pr_work_result_service.py"),
        Path("src/meobot/application/pr_work_service.py"),
        # Work maintenance: sync, rebuild, admin removals and the legacy-item
        # delete each read ``PrReportingPeriod`` to **refuse** a month that is
        # not OPEN, and to tell the performance service which month to
        # recompute. Nothing here writes a period, aggregates a report or
        # produces an artifact.
        Path("src/meobot/application/pr_work_maintenance_service.py"),
    }
    needles = tuple(EXPECTED_TABLES) + tuple(model.__name__ for model in STEP_1B_MODELS)
    # Whole words only. ``PrPublicationStatus`` is a domain enum that merely
    # starts with a model's name, and naming a vocabulary is not touching a
    # table - matching on substrings would flag it and teach nobody anything.
    patterns = {needle: re.compile(rf"\b{re.escape(needle)}\b") for needle in needles}
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if relative in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for needle, pattern in patterns.items():
            if pattern.search(text):
                offenders.append(f"{relative}: {needle}")
    assert offenders == [], offenders


def test_no_scheduled_job_generates_a_pr_report() -> None:
    """Requirement 22, the scheduler half.

    The pre-existing ``q_reports`` queue and the ``reports.generate_demo``
    milestone-3 stub are untouched and unrelated. What must not exist yet is a
    beat entry, a queue route or a task module for the *PR* report.
    """
    celery_app = (SRC / "tasks" / "celery_app.py").read_text(encoding="utf-8")
    schedule = celery_app.split("beat_schedule=", 1)[1]
    assert "pr" not in re.findall(r'"([a-z0-9-]+)":\s*\{', schedule)
    # ``meobot.tasks.pr_reviews`` (Step 1F) is a PR task module and is meant to
    # exist - it runs the AI review gate, which is not a report. What must still
    # not exist is anything generating a PR *report*, so the needles name
    # reports rather than the whole ``pr`` prefix.
    for offender in ("pr_report", "pr-report", "pr.report", "meobot.tasks.pr_report"):
        assert offender not in celery_app, offender
    assert not (SRC / "tasks" / "pr.py").exists()
    assert not (SRC / "tasks" / "pr_reporting.py").exists()


#: What a transport surface must not name. Deliberately *not* the bare
#: ``pr_publication``: Step 1C.1 added a capability called
#: ``PR_PUBLICATION_REGISTER``, and a case-folded substring sweep would flag
#: every module that merely lists the capability vocabulary. The reporting
#: module is named by its table, its service module and its report/weekly
#: artefacts - those are what this catches.
NEEDLES: tuple[str, ...] = (
    "pr_publications",
    "pr_publication_service",
    "pr_report",
    "pr_weekly",
    "pr_reporting",
)


def test_no_telegram_surface_mentions_the_reporting_module() -> None:
    """Requirement 22, the Telegram half. No handler, no command, no keyboard.

    ``application`` and ``tools`` are scanned with exception lists rather than
    dropped from the sweep. Step 1C put two legitimate PR services in the first
    (see
    :func:`test_no_module_outside_the_models_package_touches_a_reporting_table`)
    and Step 1D put a publication-registration *tool* in the second - which is
    the thing this guard existed to wait for. Stopping the scan entirely would
    stop noticing what it is actually for: a *report renderer* or a *weekly
    aggregation* appearing beside them.

    ``bot`` and ``integrations`` remain absolute: no Telegram handler and no
    third-party client mentions any of this. **``api`` no longer is**, and that
    is Step 1E: one schema module serialises a publication into a response, and
    one router exposes the register-and-read endpoints the publication service
    already backed. Neither renders a report nor aggregates a week, which is what
    the sweep is for.
    """
    allowed = {
        # Step 1C: the two services that touch publications and metric
        # snapshots, plus the read side that renders them.
        "application": {
            "pr_publication_service.py",
            "pr_workflow_service.py",
            "pr_query_service.py",
            # Step 1F.2.3f.4: the completed board's work-month classification.
            # It names the table to build ``MIN(published_at)`` in a ``WHERE`` -
            # no week aggregated, no report rendered. See the entry in
            # ``test_no_module_outside_the_models_package_touches_a_reporting_table``.
            "pr_content_query.py",
            # Step 1E: the shared service-bundle builder. It names the
            # publication service because it constructs it - one wiring for both
            # clients - and touches no table itself.
            "pr_services.py",
            # Step 1F.2.3a: permanent deletion refuses when a publication exists
            # and detaches a reporting issue from the content it mentions. It
            # deletes neither - see the file's own docstring on why the one
            # destructive branch that would have touched them was removed.
            "pr_lifecycle_service.py",
            # Step 1F.2.3b: undo asks the same question for the same reason -
            # once something has gone out, the decision behind it stands.
            "pr_undo_service.py",
            # Step 1F.2.3f: the derivative service asks whether a publication
            # points at an output, and refuses to delete or move it if one does.
            "pr_content_asset_service.py",
            # Step 1F.2.3f.2: the production service asks the same question about
            # an original handed-in file before allowing its location to be
            # corrected, and the action list asks the publication service what a
            # contributor may record.
            "pr_production_service.py",
            "pr_action_service.py",
            # Step 1F.2.4a: hand-entered channel readings. It imports
            # ``PrChannelMetricSnapshot`` from ``meobot.db.models.pr_reporting``
            # and nothing else from the reporting module - no period, no weekly
            # input, no report run. A *report renderer* appearing beside it is
            # what this sweep still catches.
            "pr_channel_metrics_service.py",
            # Step 1F.2.4b: the YouTube connector's orchestration. Imports
            # ``PrChannelMetricSnapshot`` and nothing else from the reporting
            # module - no period, no weekly input, no report run.
            "pr_channel_sync_service.py",
            # M2: the quota engine, and the first reader ``pr_reporting_periods``
            # has ever had. Step 1B built the table and said periods were what a
            # quota would attach to; these three are that. Each imports
            # ``PrReportingPeriod`` and nothing else from the reporting module -
            # no weekly input, no report run, no metric. A *report renderer* or a
            # *weekly aggregation* appearing beside them is what this sweep is
            # still for.
            "pr_work_period_service.py",
            "pr_work_quota_service.py",
            "pr_work_plan_service.py",
            # M3: the Content → Work projector. Imports ``PrPublication`` - one
            # of the three source milestones it reads work out of - and
            # ``PrReportingPeriod``, which it reads only to **refuse** a
            # projection landing in a shut month. No weekly input, no report run,
            # no metric, and nothing written to either.
            "pr_content_work_projector.py",
            # M6: a performance figure is computed **for one reporting period**
            # and refused when that period is shut, so each of these imports
            # ``PrReportingPeriod``. None imports a report run, a report
            # artifact or a weekly input, and the absolute half of this guard
            # below still applies to every one of them unchanged.
            "pr_performance_service.py",
            "pr_performance_review_service.py",
            "pr_performance_target_service.py",
            # M4B: the recurring-work generator reads ``PrReportingPeriod`` for
            # exactly one purpose - to **refuse** generating retroactive work into
            # a month that has been closed or locked, which would move a number
            # underneath a report that already quotes it. The same single reason
            # M3's projector reads it for, and the same conclusion: no weekly
            # input, no report run, no metric, and nothing written to any of them.
            "pr_work_recurring_generator.py",
            # KPI workload visibility and period containers (0039): the plan
            # workload calculator, the result service and the work service read
            # ``PrReportingPeriod`` - to price a plan on its last day, to open
            # the month a stream belongs to and refuse a shut one, and to bound
            # a unit rename to current months. Read-only, and held to
            # ``off_limits`` below like every other period reader.
            "pr_plan_workload.py",
            "pr_work_result_service.py",
            "pr_work_service.py",
            # Work maintenance: reads ``PrReportingPeriod`` only to refuse a
            # shut month and to name the month a performance figure is
            # recomputed for. Held to ``off_limits`` below like the rest.
            "pr_work_maintenance_service.py",
        },
        # Step 1D: the Telegram tool that registers "this went out", and the
        # wiring and presenter modules it goes through. A publication tool is
        # exactly what this guard was waiting for - see the docstring.
        "tools": {
            "pr_admin_tools.py",
            "pr_support.py",
        },
        # Step 1E: the HTTP surface for the same two operations Telegram already
        # had. A *report* endpoint appearing beside them is what this still
        # catches.
        # Matched on file name, and both the router and the schema module are
        # called ``pr.py``. One entry, therefore - narrow enough that a
        # ``pr_reports.py`` router would still be caught.
        "api": {
            "pr.py",
            # M2: the KPI-plan wire shapes serialise a reporting period into a
            # picker, the way ``pr.py`` serialises a publication. Named
            # specifically rather than by a ``pr_work*`` glob, so a
            # ``pr_reports.py`` router - or a report field on the ledger's own
            # schema module - would still be caught.
            "pr_work_quota.py",
            # M6: the performance wire shapes carry a ``reporting_period_id``
            # and the period's code, the way M2's carry a period picker. Named
            # specifically for the same reason.
            "pr_performance.py",
        },
    }
    for directory in ("bot", "api", "application", "tools", "integrations"):
        for path in (SRC / directory).rglob("*.py"):
            if path.name in allowed.get(directory, set()):
                continue
            text = path.read_text(encoding="utf-8").lower()
            for needle in NEEDLES:
                assert needle not in text, f"{path.relative_to(ROOT)}: {needle}"

    # And the exempted files still touch nothing they were not exempted for.
    # Named precisely rather than by prefix: they legitimately import from
    # ``meobot.db.models.pr_reporting``, so a ``pr_report`` prefix match would
    # flag the import line and prove nothing.
    #
    # **The report half stays absolute for everybody.** A report run, a report
    # artifact or a weekly manual input appearing in any of these files is the
    # thing this guard exists to catch, and no milestone has a reason to write
    # one from a service, a tool or a router.
    off_limits = (
        "pr_report_runs",
        "pr_report_artifacts",
        "pr_weekly_manual_inputs",
        "PrReportRun",
        "PrReportArtifact",
        "PrWeeklyManualInput",
    )
    # **The period half is no longer absolute**, and M2 is why. Step 1B built
    # ``pr_reporting_periods`` with no reader at all and said a quota was what
    # would attach to it; M2 is that quota engine, so four files legitimately
    # name the period - and naming it is the *point*, because the alternative
    # was M2 inventing a second calendar out of ``day_bounds``.
    #
    # Listed one by one rather than by a ``pr_work*`` glob, so a report renderer
    # that happened to live under the Work module would still be caught. Every
    # one of them is still held to ``off_limits`` above.
    period_readers = frozenset(
        {
            "pr_work_period_service.py",
            "pr_work_quota_service.py",
            "pr_work_plan_service.py",
            "pr_work_quota.py",
            # M3: reads the period to **refuse** a projection into a shut month.
            # Held to ``off_limits`` above like every other name here.
            "pr_content_work_projector.py",
            # M6: the same, three times over. Each reads ``PrReportingPeriod`` -
            # the calculation to find the month's workdays, the other two to
            # refuse a write into a shut month - and each is still held to
            # ``off_limits``, so a report run or a weekly input appearing in any
            # of them is caught exactly as before.
            "pr_performance_service.py",
            "pr_performance_review_service.py",
            "pr_performance_target_service.py",
            # M4B: reads the period to **refuse** generating into a shut month.
            # Held to ``off_limits`` above like every other name here, so a report
            # run appearing in the scheduler would be caught exactly as before -
            # and a scheduler is precisely the place such an import gets added
            # quietly.
            "pr_work_recurring_generator.py",
            # KPI workload visibility and 0039's period containers - see above.
            "pr_plan_workload.py",
            "pr_work_result_service.py",
            "pr_work_service.py",
            # Work maintenance - see above.
            "pr_work_maintenance_service.py",
        }
    )
    period_names = ("pr_reporting_periods", "PrReportingPeriod")
    # Resolved by search rather than by joining ``directory / name``: since
    # Step 1E the exemptions are file *names* that may sit in a subpackage
    # (``api/routers/pr.py``, ``api/schemas/pr.py``), and a join would look for
    # a file that is not there.
    for directory, names in allowed.items():
        for name in sorted(names):
            matches = sorted((SRC / directory).rglob(name))
            assert matches, f"{directory}/{name} is exempted but does not exist."
            for match in matches:
                text = match.read_text(encoding="utf-8")
                needles = off_limits if name in period_readers else off_limits + period_names
                for needle in needles:
                    assert needle not in text, f"{match.relative_to(ROOT)}: {needle}"
