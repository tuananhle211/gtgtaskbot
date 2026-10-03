"""Step 1A1's shape, held still.

These tests run offline and cannot see a real PostgreSQL - only
``tests/integration/test_pr_ai_review_migrations.py`` can, and it is the one
that proves migration 0014 and the models agree. What this file holds in place
is the set of structural promises that make an AI review safe to store:

* ``AI_REVIEW`` is a workflow stage, and it sits between ``SCRIPTING`` and
  ``TEAM_LEAD_REVIEW``;
* ``pr_ai_reviews`` is append-only - no ``updated_at``, ever;
* nothing on that table names a person, and no foreign key from it reaches
  ``users``. An AI review is not an approval and must not be able to look like
  one;
* ``pr_approval_events`` is unchanged by this step;
* the enum vocabularies in migration 0014 still match the domain enums;
* no LLM client, prompt, Telegram handler, scheduler or report generator
  arrived with this step.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import DateTime, Numeric

import meobot.db.models
from meobot.db.base import Base
from meobot.db.models.pr import PrApprovalEvent, PrContentItem
from meobot.db.models.pr_ai_review import PrAiReview
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewType,
    PrApprovalStage,
    PrWorkflowStage,
)

ROOT = Path(__file__).resolve().parents[2]

AI_REVIEW_TABLE = "pr_ai_reviews"
MIGRATION = ROOT / "alembic" / "versions" / "0014_pr_ai_review.py"
STEP_1A1_DOC = ROOT / "docs" / "pr" / "STEP_1A1_AI_REVIEW_WORKFLOW.md"

#: The canonical content workflow, in order, with ``CANCELLED`` last because it
#: is a terminal alternative rather than a position in the sequence. Spelled
#: out rather than derived from the enum, so that reordering the enum has to
#: come here and be argued for.
CANONICAL_WORKFLOW: tuple[str, ...] = (
    "IDEA",
    "BRIEFING",
    "SCRIPTING",
    "AI_REVIEW",
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

#: The stretch of the canonical workflow this step changed, exactly as the
#: document has to render it. Asserted as one string rather than by comparing
#: three separate offsets, which would pass on a document that merely mentions
#: the three names somewhere in that order.
DOCUMENTED_INSERTION = "→ SCRIPTING\n→ AI_REVIEW\n→ TEAM_LEAD_REVIEW\n"

#: Imports that would mean this step had done more than it was asked to. Each
#: is something an AI-review *execution* needs and a schema step must not have.
OUT_OF_SCOPE_IMPORTS = re.compile(
    r"\b(?:openai|anthropic|litellm|langchain|telegram|aiogram|apscheduler|celery"
    r"|gspread|openpyxl|httpx|requests)\b"
)


@pytest.fixture(scope="module")
def revision_0014() -> Any:
    """Migration 0014, imported as a module so its enum lists can be read."""
    spec = importlib.util.spec_from_file_location("meobot_migration_0014", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration_source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def migration_statements(migration_source: str) -> list[str]:
    """Only the lines that emit DDL - the docstring is prose, not schema."""
    return [line for line in migration_source.splitlines() if "op." in line]


# --- 1 & 2: the workflow gained one stage, in one place --------------------


def test_ai_review_is_a_workflow_stage() -> None:
    """Requirement 1."""
    assert PrWorkflowStage.AI_REVIEW.value == "AI_REVIEW"
    assert "AI_REVIEW" in {member.value for member in PrWorkflowStage}


def test_the_canonical_workflow_puts_ai_review_between_scripting_and_team_lead() -> None:
    """Requirement 2.

    Member order *is* the canonical order - there is no numeric column
    mirroring it, and this repository has never had one. So the ordering
    promise is asserted where the ordering lives.
    """
    stages = [member.value for member in PrWorkflowStage]
    assert tuple(stages) == CANONICAL_WORKFLOW
    assert stages.index("SCRIPTING") < stages.index("AI_REVIEW") < stages.index("TEAM_LEAD_REVIEW")
    # Immediately between, not merely somewhere between.
    assert stages[stages.index("SCRIPTING") + 1] == "AI_REVIEW"
    assert stages[stages.index("AI_REVIEW") + 1] == "TEAM_LEAD_REVIEW"
    # CANCELLED remains a terminal alternative, outside the sequence.
    assert stages[-1] == "CANCELLED"


def test_no_numeric_workflow_ordering_was_introduced() -> None:
    """The order is the enum's, and is not copied into a column.

    A second copy of the sequence is a second thing to keep in step, and
    nothing in this repository orders a workflow by an integer on the row.
    """
    columns = {column.name for column in PrAiReview.__table__.columns}
    assert not {name for name in columns if "order" in name or "sequence" in name}
    assert "workflow_order" not in PrContentItem.__table__.columns
    assert "stage_order" not in PrContentItem.__table__.columns


def test_the_workflow_stage_column_needed_no_migration(migration_statements: list[str]) -> None:
    """The exact consequence of adding ``AI_REVIEW``, asserted rather than told.

    It costs no DDL. ``pr_content_items.workflow_stage`` is a ``VARCHAR(30)``
    with no PostgreSQL ``ENUM`` type, and - because ``sa.Enum(native_enum=False)``
    defaults to ``create_constraint=False`` - no vocabulary ``CHECK`` either.
    There is nothing to widen and nothing to alter, so 0014 must not touch
    ``pr_content_items`` at all. This is the assertion that stops somebody
    adding an ``ALTER`` there later without understanding why there was none.
    """
    stage = PrContentItem.__table__.columns["workflow_stage"]
    assert getattr(stage.type, "length", None) == 30
    assert len("AI_REVIEW") <= 30
    assert stage.type.native_enum is False  # type: ignore[union-attr]
    assert stage.type.create_constraint is False  # type: ignore[union-attr]

    touched = [line for line in migration_statements if "pr_content_items" in line]
    # The only mention 0014 is allowed is the foreign key target of the new
    # table - never the table itself as an operation target.
    assert all("pr_content_items.id" in line for line in touched), touched
    assert not [line for line in migration_statements if "alter_column" in line]


# --- 3, 4, 16: the table exists and is append-only -------------------------


def test_the_ai_review_table_is_registered(revision_0014: Any, migration_source: str) -> None:
    """Requirement 3."""
    assert AI_REVIEW_TABLE in Base.metadata.tables
    assert PrAiReview.__tablename__ == AI_REVIEW_TABLE
    assert PrAiReview.__name__ in meobot.db.models.__all__
    assert meobot.db.models.PrAiReview is PrAiReview

    created = set(re.findall(r'op\.create_table\(\s*"([^"]+)"', migration_source))
    assert created == {AI_REVIEW_TABLE}
    assert revision_0014.revision == "0014"
    assert revision_0014.down_revision == "0013"


def test_ai_reviews_have_no_updated_at() -> None:
    """Requirement 4. A review that can be edited is not a record of one."""
    assert "updated_at" not in PrAiReview.__table__.columns


def test_an_ai_review_records_when_it_ran_and_when_it_was_written() -> None:
    """Requirement 16. Two timestamps, because they are two facts."""
    columns = PrAiReview.__table__.columns
    reviewed_at, created_at = columns["reviewed_at"], columns["created_at"]
    assert not reviewed_at.nullable
    assert not created_at.nullable
    for column in (reviewed_at, created_at):
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True, column.name
    # The database owns ``created_at``; the caller owns ``reviewed_at``.
    assert created_at.server_default is not None
    assert reviewed_at.server_default is None
    assert reviewed_at.default is None


def test_every_ai_review_timestamp_is_timezone_aware() -> None:
    for column in PrAiReview.__table__.columns:
        if isinstance(column.type, DateTime):
            assert column.type.timezone is True, column.name


# --- 5, 6, 17, 18: what the row points at, and what it must not ------------


def test_content_id_is_required_and_restricts() -> None:
    """Requirement 5."""
    column = PrAiReview.__table__.columns["content_id"]
    assert not column.nullable
    constraint = next(
        candidate
        for candidate in PrAiReview.__table__.foreign_key_constraints
        if {c.name for c in candidate.columns} == {"content_id"}
    )
    assert constraint.referred_table.name == "pr_content_items"
    assert constraint.ondelete == "RESTRICT"


def test_task_id_is_optional_and_restricts() -> None:
    """Requirement 6. Some reviews stand behind no task."""
    column = PrAiReview.__table__.columns["task_id"]
    assert column.nullable
    constraint = next(
        candidate
        for candidate in PrAiReview.__table__.foreign_key_constraints
        if {c.name for c in candidate.columns} == {"task_id"}
    )
    assert constraint.referred_table.name == "pr_tasks"
    assert constraint.ondelete == "RESTRICT"


def test_every_ai_review_foreign_key_restricts() -> None:
    """AI review history is never cascade-deleted."""
    constraints = PrAiReview.__table__.foreign_key_constraints
    assert {constraint.ondelete for constraint in constraints} == {"RESTRICT"}
    assert len(constraints) == 2


def test_an_ai_review_names_no_human_reviewer() -> None:
    """Requirement 17. There is no ``reviewer_user_id`` and there never will be.

    An AI review is advisory quality control. A person column on this row would
    make an automated check indistinguishable from somebody signing something
    off, which is exactly the confusion this table is shaped to prevent.
    """
    columns = {column.name for column in PrAiReview.__table__.columns}
    assert "reviewer_user_id" not in columns
    assert not [name for name in columns if name.endswith("user_id")]
    # What it names instead is a model and a prompt.
    assert {"model_name", "model_version", "prompt_version"} <= columns


def test_no_foreign_key_from_ai_reviews_reaches_the_users_table(migration_source: str) -> None:
    """Requirement 18, walked rather than read off the source."""
    referenced = {
        constraint.referred_table.name
        for constraint in PrAiReview.__table__.foreign_key_constraints
    }
    assert referenced == {"pr_content_items", "pr_tasks"}
    assert "users" not in referenced
    # And the migration references no column of it either.
    assert "users.id" not in migration_source


# --- 19: the human approval record is untouched ----------------------------


def test_the_approval_event_schema_is_unchanged_by_this_step(
    migration_statements: list[str],
) -> None:
    """Requirement 19.

    ``pr_approval_events`` remains human-review history: the reviewer foreign
    key, and still no ``updated_at``. Migration 0014 emits no statement that
    names it.

    The column set is asserted as "Step 1F's ten, plus what later steps added",
    with each addition listed. Step 1F.2.3's ``production_submission_id`` is the
    only one so far and it does not weaken anything this test protects: it is
    nullable, it is set once when the row is *inserted*, and nothing updates it.
    An append-only table may gain a column; what it may not gain is an
    ``updated_at`` and a writer, which the two assertions below still forbid.
    """
    assert {column.name for column in PrApprovalEvent.__table__.columns} == {
        "id",
        "content_id",
        "task_id",
        "approval_stage",
        "reviewer_user_id",
        "decision",
        "version_reviewed",
        "comment",
        "decided_at",
        "created_at",
        # Step 1F.2.3: which production submission an INTERNAL_REVIEW decision
        # judged. Null for the two script gates, which judge ``version_reviewed``.
        "production_submission_id",
    }
    assert "updated_at" not in PrApprovalEvent.__table__.columns
    reviewer = PrApprovalEvent.__table__.columns["reviewer_user_id"]
    assert not reviewer.nullable
    assert {fk.column.table.name for fk in reviewer.foreign_keys} == {"users"}
    assert not [line for line in migration_statements if "pr_approval_events" in line]


def test_ai_review_is_not_an_approval_stage() -> None:
    """The vocabulary itself refuses to file an AI result as an approval."""
    assert {member.value for member in PrApprovalStage} == {
        "TEAM_LEAD_REVIEW",
        "HEAD_REVIEW",
        "INTERNAL_REVIEW",
    }
    assert "AI_REVIEW" not in {member.value for member in PrApprovalStage}


# --- 7 to 12: the constraints, as declared ---------------------------------


def test_the_declared_check_constraints_are_exactly_the_four_specified() -> None:
    """Requirements 7, 10, 11 and 12, as schema facts.

    The database refusing each value is checked against a real PostgreSQL in
    ``tests/integration/test_pr_ai_review_migrations.py``; this is the cheap
    guard that no constraint has been dropped and none has been invented.
    """
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in PrAiReview.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert set(checks) == {
        "ck_pr_ai_reviews_reviewed_version_positive",
        "ck_pr_ai_reviews_score_in_range",
        "ck_pr_ai_reviews_model_name_not_empty",
        "ck_pr_ai_reviews_prompt_version_not_empty",
    }
    assert checks["ck_pr_ai_reviews_reviewed_version_positive"] == "reviewed_version >= 1"
    assert "score IS NULL" in checks["ck_pr_ai_reviews_score_in_range"]
    assert "trim(model_name)" in checks["ck_pr_ai_reviews_model_name_not_empty"]
    assert "trim(prompt_version)" in checks["ck_pr_ai_reviews_prompt_version_not_empty"]


def test_score_is_an_optional_bounded_numeric() -> None:
    """Requirements 8, 9 and 10, as a column fact.

    Nullable, so a review type with no meaningful number is still recordable;
    ``Numeric(5, 2)``, so ``100.00`` fits and ``0`` is exact.
    """
    score = PrAiReview.__table__.columns["score"]
    assert score.nullable
    assert isinstance(score.type, Numeric)
    assert (score.type.precision, score.type.scale) == (5, 2)
    assert score.default is None
    assert score.server_default is None


def test_only_the_specified_columns_are_required() -> None:
    """Nothing optional was quietly made mandatory.

    ``score``, ``summary``, ``issues``, ``suggestions``, ``policy_flags``,
    ``model_version`` and ``task_id`` are all optional by requirement.
    """
    required = {
        column.name
        for column in PrAiReview.__table__.columns
        if not column.nullable and column.default is None and column.server_default is None
    }
    assert required == {
        "content_id",
        "review_type",
        "reviewed_version",
        "result",
        "model_name",
        "prompt_version",
        "reviewed_at",
    }


# --- 13, 14, 15: history accumulates ---------------------------------------


def test_no_uniqueness_is_declared_over_content_and_version() -> None:
    """Requirements 13, 14 and 15, as a schema fact.

    Reviewing one version twice - a retry, or a second review of a different
    type - is normal. A unique index on ``(content_id, reviewed_version)``
    would turn the second attempt into a failure or an overwrite instead of
    another row of history, which is the one thing an append-only audit table
    must not do.
    """
    table = PrAiReview.__table__
    assert not [index.name for index in table.indexes if index.unique]
    assert not [
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    ]
    # The pair is indexed - just not uniquely.
    pair = next(
        index
        for index in table.indexes
        if [column.name for column in index.columns] == ["content_id", "reviewed_version"]
    )
    assert not pair.unique


def test_the_specified_indexes_all_exist() -> None:
    """Every access path the requirement names, and no unique one."""
    declared = {
        tuple(column.name for column in index.columns) for index in PrAiReview.__table__.indexes
    }
    assert declared == {
        ("content_id", "reviewed_at"),
        ("content_id", "reviewed_version"),
        ("task_id",),
        ("result",),
        ("review_type",),
        ("reviewed_at",),
        ("model_name",),
    }


# --- 20 & 21: the two new vocabularies, exactly ----------------------------


def test_the_ai_review_result_values_are_exactly_the_three_specified() -> None:
    """Requirement 20."""
    assert [member.value for member in PrAiReviewResult] == [
        "PASS",
        "PASS_WITH_WARNINGS",
        "REVISION_REQUIRED",
    ]
    # None of them is an approval.
    assert "APPROVED" not in {member.value for member in PrAiReviewResult}


def test_the_ai_review_type_values_are_exactly_the_four_specified() -> None:
    """Requirement 21."""
    assert [member.value for member in PrAiReviewType] == [
        "SCRIPT_QUALITY",
        "POLICY_COMPLIANCE",
        "BRAND_TONE",
        "FULL_REVIEW",
    ]


def test_the_migration_enum_lists_match_the_domain_enums(revision_0014: Any) -> None:
    """The stored vocabulary is one vocabulary.

    0014 spells its values out as literals on purpose - it has to keep meaning
    what it meant on the day it ran. This keeps that literal list and the
    domain enum in step, and does the same for the canonical workflow order.
    """
    for attribute, enum_type in (
        ("AI_REVIEW_RESULTS", PrAiReviewResult),
        ("AI_REVIEW_TYPES", PrAiReviewType),
        ("WORKFLOW_STAGES", PrWorkflowStage),
    ):
        assert getattr(revision_0014, attribute) == tuple(member.value for member in enum_type), (
            attribute
        )
    assert revision_0014.WORKFLOW_STAGES == CANONICAL_WORKFLOW


def test_every_new_enum_value_is_a_stable_uppercase_code() -> None:
    """Name and value are the same string, so a rename cannot be silent."""
    for enum_type in (PrAiReviewResult, PrAiReviewType):
        for member in enum_type:
            assert member.value == member.name
            assert re.fullmatch(r"[A-Z][A-Z_]*", member.value), member.value


def test_every_enum_column_is_wide_enough_for_its_longest_value() -> None:
    """A ``VARCHAR`` too short for ``PASS_WITH_WARNINGS`` truncates or errors."""
    for column in PrAiReview.__table__.columns:
        length = getattr(column.type, "length", None)
        enum_values = getattr(column.type, "enums", None)
        if length is None or not enum_values:
            continue
        assert max(len(value) for value in enum_values) <= length, column.name


# --- 25: this step added a table, not a feature ----------------------------


def test_no_llm_telegram_scheduler_or_report_code_arrived_with_this_step() -> None:
    """Requirement 25.

    Step 1A1 is a place to put an answer, written before anything can produce
    one. The model client, the prompt, the job that runs it and the transition
    service are all later steps, and none of their imports belongs in the files
    this step created.
    """
    for path in (
        ROOT / "src" / "meobot" / "db" / "models" / "pr_ai_review.py",
        ROOT / "src" / "meobot" / "domain" / "pr" / "models.py",
        MIGRATION,
    ):
        imports = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith(("import ", "from "))
        )
        assert not OUT_OF_SCOPE_IMPORTS.search(imports), path.name

    # And no *unnamed* module for calling a model appeared beside them. Each
    # exemption is a decision rather than a hole:
    #
    # * ``pr_ai_review_service.py`` (Step 1C) records a verdict something else
    #   produced and still contains no client, prompt or job;
    # * ``ai_review.py`` (Step 1F) is pure domain policy - the finding shape and
    #   the severity-to-outcome mapping. No provider, no session;
    # * ``pr_ai_review_run_service.py`` (Step 1F) owns execution *status* and
    #   calls nothing;
    # * ``pr_ai_review_run.py`` (Step 1F) is the table that status lives in;
    # * ``pr_ai_review_executor.py`` (Step 1F) is the one module that *does*
    #   call a provider, which is the whole point of that step. It is asserted
    #   separately in ``tests/unit/test_pr_ai_review_execution.py`` to own no
    #   workflow rule of its own.
    #
    # The files Step 1A1 itself created are still swept above, unchanged.
    allowed = {
        "pr_ai_review.py",
        "pr_ai_review_service.py",
        "ai_review.py",
        "pr_ai_review_run.py",
        "pr_ai_review_run_service.py",
        "pr_ai_review_executor.py",
    }
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src" / "meobot").rglob("*ai_review*.py")
        if path.name not in allowed
    ]
    assert offenders == [], offenders

    service = ROOT / "src" / "meobot" / "application" / "pr_ai_review_service.py"
    if service.exists():
        assert not OUT_OF_SCOPE_IMPORTS.search(
            "\n".join(
                line
                for line in service.read_text(encoding="utf-8").splitlines()
                if line.startswith(("import ", "from "))
            )
        )


# --- The documented business rules stay documented -------------------------


def test_the_step_1a1_document_states_the_rules_this_step_defers() -> None:
    """The transition rules are deferred, so they have to be written down.

    Nothing enforces them yet. A document is the only place they exist, which
    makes its contents part of the deliverable rather than commentary on it.
    """
    assert STEP_1A1_DOC.exists()
    document = STEP_1A1_DOC.read_text(encoding="utf-8")
    assert DOCUMENTED_INSERTION in document
    for phrase in (
        "CANCELLED",
        "PASS_WITH_WARNINGS",
        "REVISION_REQUIRED",
        "pr_approval_events",
        "append-only",
        "prompt version",
    ):
        assert phrase in document, phrase
