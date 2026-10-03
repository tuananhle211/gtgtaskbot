"""Step 1C.1's shape, held still.

Offline structural promises: the two new tables exist and are shaped the way
the design says, migration 0016's frozen vocabulary still matches the domain
enum, and - the one that matters most - **nothing in this repository allocates
a code by reading the biggest one and adding one.**
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

import meobot.db.models
from meobot.db.base import Base
from meobot.db.models.pr_authorization import PrUserCapability
from meobot.db.models.pr_code_counter import PrCodeCounter
from meobot.domain.pr.codes import PrCodeNamespace
from meobot.domain.pr.policy import GRANT_BACKED, PrCapability, requires_grant

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "meobot"
MIGRATION = ROOT / "alembic" / "versions" / "0016_pr_capabilities_and_codes.py"

#: The shapes a race-prone allocator takes. Each is a real thing somebody
#: writes when they need "the next number" and have not thought about two
#: transactions doing it at once.
UNSAFE_ALLOCATION = re.compile(
    r"max\s*\(\s*[A-Za-z_.]*code|"  # MAX(code)
    r"count\s*\(\s*\*?\s*\)\s*\+\s*1|"  # COUNT(*) + 1
    r"func\.max\s*\(|"  # SQLAlchemy func.max(...)
    r"ORDER\s+BY\s+code\s+DESC",  # read the last one and add one
    re.IGNORECASE,
)


def _executable_lines(path: Path) -> list[tuple[int, str]]:
    """Source lines with comments and string literals removed.

    A text sweep for ``MAX(code)`` would otherwise flag the docstrings that
    explain why ``MAX(code)`` is wrong - which is the one place the phrase
    *should* appear. Tokenising is the honest way to ask "does the code do
    this" rather than "does the file mention this".
    """
    import io
    import tokenize

    blanked: dict[int, list[str]] = {}
    with path.open("rb") as handle:
        source = handle.read().decode("utf-8")
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in {tokenize.COMMENT, tokenize.STRING}:
            continue
        if token.type in {tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT}:
            continue
        blanked.setdefault(token.start[0], []).append(token.string)
    return [(number, " ".join(parts)) for number, parts in sorted(blanked.items())]


@pytest.fixture(scope="module")
def revision_0016() -> Any:
    """Migration 0016, imported so its frozen vocabulary can be read."""
    spec = importlib.util.spec_from_file_location("meobot_migration_0016", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- The tables exist and are registered -----------------------------------


def test_both_new_tables_are_registered(revision_0016: Any) -> None:
    assert "pr_user_capabilities" in Base.metadata.tables
    assert "pr_code_counters" in Base.metadata.tables
    for name in ("PrUserCapability", "PrCodeCounter"):
        assert name in meobot.db.models.__all__

    created = set(
        re.findall(r'op\.create_table\(\s*"([^"]+)"', MIGRATION.read_text(encoding="utf-8"))
    )
    assert created == {"pr_user_capabilities", "pr_code_counters"}
    assert revision_0016.revision == "0016"
    assert revision_0016.down_revision == "0015"


def test_the_migration_capability_list_covers_every_grantable_capability(
    revision_0016: Any,
) -> None:
    """A migration keeps meaning what it meant; this keeps the two in step.

    The frozen list in 0016 is the ``CHECK`` constraint on
    ``pr_user_capabilities.capability``, so what it has to cover is exactly the
    set of values that column can ever *receive* - and that is
    :data:`GRANT_BACKED`, not the whole enum.
    ``PrCapabilityService.grant`` refuses anything else before it writes, with
    ``PrValidationError``, so a capability outside the list can never reach the
    row.

    Until Step 1F.2.3 the two were the same set and this was written as equality.
    That step added three capabilities - delete, assign production, do production
    - which are decided by role and permission alone, and widening a 2026
    migration's constraint to name values nothing can insert would be a schema
    change bought with nothing.

    So the invariant is stated as the two things that actually matter, and the
    second is the one that would catch the dangerous mistake: **anything missing
    from the migration's list must be un-grantable.** A future grant-backed
    capability added without its migration fails here.
    """
    frozen = set(revision_0016.PR_CAPABILITIES)
    assert {member.value for member in GRANT_BACKED} <= frozen
    for member in PrCapability:
        if member.value not in frozen:
            assert not requires_grant(member), member.value


def test_no_earlier_pr_migration_was_touched() -> None:
    """0012-0015 are history and stay that way."""
    versions = ROOT / "alembic" / "versions"
    for number, name in (
        ("0012", "0012_pr_core_foundation.py"),
        ("0013", "0013_pr_reporting_foundation.py"),
        ("0014", "0014_pr_ai_review.py"),
        ("0015", "0015_pr_content_versions.py"),
    ):
        source = (versions / name).read_text(encoding="utf-8")
        assert f'revision: str = "{number}"' in source
        assert "pr_user_capabilities" not in source
        assert "pr_code_counters" not in source


# --- Grants ----------------------------------------------------------------


def test_a_grant_names_a_user_and_never_a_telegram_identity() -> None:
    columns = {column.name for column in PrUserCapability.__table__.columns}
    assert {"user_id", "capability", "effective_from", "effective_to"} <= columns
    assert not [name for name in columns if "telegram" in name or "username" in name]
    # No ``updated_at``: closing a grant is a dated fact, not an edit.
    assert "updated_at" not in columns


def test_a_grant_carries_its_whole_scope_on_the_row_and_in_two_tables() -> None:
    """Step 1F.2.7's shape: a mode per axis, and never a list in a string.

    The four booleans and two mode columns are what makes a scope decidable
    without a join when it is ``ALL``; the two child tables are what makes a
    ``SELECTED`` scope a real set with real foreign keys.
    """
    columns = {column.name for column in PrUserCapability.__table__.columns}
    assert {
        "content_type_scope",
        "include_unclassified_content",
        "channel_scope",
        "include_unassigned_channel",
        "requires_role_baseline",
        "revoked_at",
        "revoked_by_user_id",
    } <= columns
    # No comma-separated anything. A scope is rows, not a string of ids.
    assert not [name for name in columns if name in {"content_types", "channel_ids"}]

    for table, child, parent_column in (
        ("pr_user_capability_content_types", "content_type", "capability_grant_id"),
        ("pr_user_capability_channels", "channel_id", "capability_grant_id"),
    ):
        assert table in Base.metadata.tables
        child_columns = {column.name for column in Base.metadata.tables[table].columns}
        assert {child, parent_column} <= child_columns


def test_a_selected_scope_cannot_hold_the_same_value_twice() -> None:
    """One unique index per child table, per grant."""
    for table, name, columns in (
        (
            "pr_user_capability_content_types",
            "uq_pr_user_capability_content_types_grant_type",
            ["capability_grant_id", "content_type"],
        ),
        (
            "pr_user_capability_channels",
            "uq_pr_user_capability_channels_grant_channel",
            ["capability_grant_id", "channel_id"],
        ),
    ):
        index = next(
            candidate for candidate in Base.metadata.tables[table].indexes if candidate.name == name
        )
        assert index.unique
        assert [column.name for column in index.columns] == columns


def test_a_grant_cascades_to_its_scope_and_restricts_towards_a_channel() -> None:
    """The asymmetry: scope rows are parts of the grant, a channel is not.

    A channel somebody has been given approval rights over must not be deletable
    out from under the grant, so that side is ``RESTRICT``. The rows that spell
    the scope out have no life without the grant, so that side is ``CASCADE``.
    """
    channels = Base.metadata.tables["pr_user_capability_channels"]
    by_column = {
        next(iter(constraint.columns)).name: constraint
        for constraint in channels.foreign_key_constraints
    }
    assert by_column["capability_grant_id"].ondelete == "CASCADE"
    assert by_column["channel_id"].ondelete == "RESTRICT"
    assert by_column["channel_id"].referred_table.name == "pr_channels"

    content_types = Base.metadata.tables["pr_user_capability_content_types"]
    assert {c.ondelete for c in content_types.foreign_key_constraints} == {"CASCADE"}


def test_every_grant_foreign_key_restricts_and_points_at_users() -> None:
    constraints = PrUserCapability.__table__.foreign_key_constraints
    assert {constraint.referred_table.name for constraint in constraints} == {"users"}
    assert {constraint.ondelete for constraint in constraints} == {"RESTRICT"}
    # Three since Step 1F.2.7: the subject, who granted it, and who withdrew it.
    assert len(constraints) == 3


def test_several_scoped_grants_of_one_gate_are_not_a_conflict() -> None:
    """Step 1F.2.7 dropped the partial unique index, and why.

    *Facebook posts on the two Facebook channels* and *short video scripts
    everywhere* are two rights, not a duplicate. What is left is a plain lookup
    index; refusing an **identical** scope is the service's job, where the
    comparison can actually be made.
    """
    names = {index.name for index in PrUserCapability.__table__.indexes}
    assert "uq_pr_user_capabilities_open_grant" not in names
    lookup = next(
        candidate
        for candidate in PrUserCapability.__table__.indexes
        if candidate.name == "ix_pr_user_capabilities_user_capability"
    )
    assert not lookup.unique
    assert [column.name for column in lookup.columns] == ["user_id", "capability"]


def test_revision_0031_preserves_every_existing_grant_exactly() -> None:
    """The one data statement in the migration, asserted by its text.

    Existing grants keep the authority they had: ``ALL``/``ALL`` scope, because
    an unscoped grant applied to everything, and ``requires_role_baseline``,
    because that is what made their holder entitled. Reading them as standalone
    would widen security silently, which is the thing this revision must not do.
    """
    source = (ROOT / "alembic" / "versions" / "0031_pr_scoped_approval_grants.py").read_text(
        encoding="utf-8"
    )
    assert "UPDATE pr_user_capabilities" in source
    for clause in (
        "content_type_scope = 'ALL'",
        "channel_scope = 'ALL'",
        "include_unclassified_content = true",
        "include_unassigned_channel = true",
        "requires_role_baseline = true",
    ):
        assert clause in source, clause
    # And the *defaults* for anything written afterwards fail closed.
    assert 'server_default="SELECTED"' in source


# --- Counters --------------------------------------------------------------


def test_the_counter_table_has_two_partial_unique_indexes() -> None:
    """The asymmetry that makes ``ON CONFLICT`` work with a nullable year.

    Two ``NULL``s are distinct in PostgreSQL, so one ``UNIQUE (namespace,
    year)`` would neither dedupe the channel row nor give ``ON CONFLICT``
    anything to match against.
    """
    indexes = {index.name: index for index in PrCodeCounter.__table__.indexes}
    yearly = indexes["uq_pr_code_counters_namespace_year"]
    global_ = indexes["uq_pr_code_counters_namespace_global"]

    assert yearly.unique and global_.unique
    assert [column.name for column in yearly.columns] == ["namespace", "year"]
    assert [column.name for column in global_.columns] == ["namespace"]
    assert "year IS NOT NULL" in str(yearly.dialect_options["postgresql"]["where"])
    assert "year IS NULL" in str(global_.dialect_options["postgresql"]["where"])


def test_a_counter_never_points_below_one() -> None:
    checks = {
        constraint.name
        for constraint in PrCodeCounter.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert "ck_pr_code_counters_next_value_positive" in checks
    assert "ck_pr_code_counters_namespace_not_empty" in checks


def test_the_counter_table_has_no_foreign_keys() -> None:
    """A counter belongs to a namespace, not to a row."""
    assert PrCodeCounter.__table__.foreign_key_constraints == set()


def test_every_namespace_the_domain_knows_is_a_plain_string_column() -> None:
    """Stored as text so a stray ``psql`` read is legible.

    Not an enum column: the vocabulary is small and closed today, but a new
    namespace should cost a row rather than a column widening, and nothing
    joins on it.
    """
    assert PrCodeCounter.__table__.columns["namespace"].type.length == 40
    for namespace in PrCodeNamespace:
        assert len(namespace.value) <= 40


# --- The rule this whole allocator exists for ------------------------------


def test_no_code_is_allocated_by_reading_the_largest_one() -> None:
    """Requirement 27, swept across the whole package.

    ``MAX(code) + 1``, ``COUNT(*) + 1`` and "order by code descending, take the
    first" are the three shapes of the same race. None of them appears
    anywhere in ``src``.
    """
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        for number, line in _executable_lines(path):
            if UNSAFE_ALLOCATION.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert offenders == [], offenders

    # The sweep has to be able to see one, or it proves nothing. This module's
    # own docstring names all three shapes, and is excluded only because
    # ``_executable_lines`` drops string literals - so point the pattern at the
    # raw text and confirm it still fires.
    counter_source = (SRC / "db" / "models" / "pr_code_counter.py").read_text(encoding="utf-8")
    assert UNSAFE_ALLOCATION.search(counter_source) is not None


def test_the_allocator_uses_an_upsert_returning_the_number() -> None:
    """The safe shape, asserted positively so the sweep above cannot pass by
    the allocator having been deleted."""
    source = (SRC / "application" / "pr_code_service.py").read_text(encoding="utf-8")
    assert "on_conflict_do_update" in source
    assert "returning" in source
    assert "index_where" in source


def test_no_pr_service_lets_a_caller_choose_a_code() -> None:
    """Every creation contract in the PR package, checked by shape."""
    from meobot.application.pr_channel_service import CreateChannelCommand
    from meobot.application.pr_content_service import CreateContentCommand
    from meobot.application.pr_publication_service import RegisterPublicationCommand
    from meobot.application.pr_task_service import CreateTaskCommand

    for command in (
        CreateChannelCommand,
        CreateContentCommand,
        RegisterPublicationCommand,
        CreateTaskCommand,
    ):
        assert "code" not in command.__dataclass_fields__, command.__name__


# --- Still transport-agnostic ----------------------------------------------


def test_the_new_modules_import_no_transport_or_llm_client() -> None:
    forbidden = re.compile(
        r"\b(?:openai|anthropic|litellm|langchain|aiogram|telegram|apscheduler|celery"
        r"|gspread|openpyxl|httpx|requests|fastapi|starlette)\b"
    )
    for path in (
        SRC / "application" / "pr_capability_service.py",
        SRC / "application" / "pr_code_service.py",
        SRC / "domain" / "pr" / "policy.py",
        SRC / "domain" / "pr" / "codes.py",
        SRC / "db" / "models" / "pr_authorization.py",
        SRC / "db" / "models" / "pr_code_counter.py",
        MIGRATION,
    ):
        imports = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith(("import ", "from "))
        )
        assert not forbidden.search(imports), path.name


def test_no_pr_service_authorizes_by_telegram_identity() -> None:
    """Requirement 5, as a sweep rather than one behavioural case.

    A PR service may never read a Telegram handle at all - not for
    authorization, not for anything. The only place ``Actor``'s Telegram fields
    legitimately reach is the audit trail, which
    :class:`~meobot.application.audit_service.AuditService` writes from the
    actor itself without any PR module naming them.
    """
    offenders: list[str] = []
    for path in sorted((SRC / "application").glob("pr_*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith(("#", "*", '"', "'")):
                continue
            if "telegram_username" in line or "telegram_user_id" in line:
                offenders.append(f"{path.name}:{number}")
    assert offenders == [], offenders
