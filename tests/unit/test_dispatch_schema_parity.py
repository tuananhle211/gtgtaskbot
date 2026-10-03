"""The models and the migrations have to agree about who fills a column.

The 0.6.0a3 production failure was not a logic error anywhere. It was two
correct-looking definitions of the same table that disagreed: the model said
"the database fills ``created_at``", the migration said "this column is
``NOT NULL`` and has no default", and every offline test built its schema from
the first one.

These tests run offline and cannot see a real PostgreSQL, so they cannot catch
that disagreement on their own - only
``tests/integration/test_dispatch_migrations.py`` can. What they *can* do is
hold both halves still: if somebody removes a server default from a dispatch
model, or adds a timestamp column that migration ``0011`` does not cover, that
is caught here in seconds rather than in somebody's Telegram client.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import DateTime

from meobot.db.models.dispatch import (
    MessageDispatch,
    MessageDispatchDraft,
    MessageDispatchDraftRecipient,
    MessageDispatchPart,
    MessageDispatchRecipient,
    MessageDispatchRecipientPart,
)

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_MODELS = (
    MessageDispatchDraft,
    MessageDispatchDraftRecipient,
    MessageDispatch,
    MessageDispatchPart,
    MessageDispatchRecipient,
    MessageDispatchRecipientPart,
)

#: The eleven columns named in the production incident, spelled out rather than
#: derived. A list that computes itself from the models cannot fail when the
#: models change, which is the one thing it needs to be able to do.
EXPECTED_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {
        ("message_dispatch_drafts", "created_at"),
        ("message_dispatch_drafts", "updated_at"),
        ("message_dispatch_draft_recipients", "created_at"),
        ("message_dispatch_draft_recipients", "updated_at"),
        ("message_dispatches", "created_at"),
        ("message_dispatches", "updated_at"),
        ("message_dispatch_recipients", "created_at"),
        ("message_dispatch_recipients", "updated_at"),
        ("message_dispatch_parts", "created_at"),
        ("message_dispatch_recipient_parts", "created_at"),
        ("message_dispatch_recipient_parts", "updated_at"),
    }
)


@pytest.fixture(scope="module")
def revision_0011() -> Any:
    """Migration 0011, imported as a module so its list can be read directly."""
    path = ROOT / "alembic" / "versions" / "0011_dispatch_timestamp_defaults.py"
    spec = importlib.util.spec_from_file_location("meobot_migration_0011", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0011_covers_exactly_the_reported_columns(revision_0011: Any) -> None:
    assert frozenset(revision_0011.TIMESTAMP_COLUMNS) == EXPECTED_COLUMNS
    # A tuple, not a set: the migration applies them in a fixed order, and a
    # duplicate would silently issue the same ALTER twice.
    assert len(revision_0011.TIMESTAMP_COLUMNS) == len(EXPECTED_COLUMNS)


def test_migration_0011_follows_0010(revision_0011: Any) -> None:
    assert revision_0011.revision == "0011"
    assert revision_0011.down_revision == "0010"


def test_migration_0010_still_creates_the_tables_it_always_did() -> None:
    """0010 is not edited by this hotfix, and must not be.

    Rewriting a migration that has already run somewhere is how two deployments
    end up with different schemas at the same revision number. The fix is a new
    revision, and this pins that ``0010`` was left alone.
    """
    source = (ROOT / "alembic" / "versions" / "0010_multi_group_dispatch.py").read_text(
        encoding="utf-8"
    )
    for table, _ in sorted(EXPECTED_COLUMNS):
        assert f'op.create_table(\n        "{table}"' in source, table
    # And it still contains no default for these columns - which is the defect
    # 0011 corrects rather than conceals.
    assert 'sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)' in source


def test_every_dispatch_timestamp_column_expects_the_database_to_fill_it() -> None:
    """The model half of the contract.

    A ``NOT NULL`` timestamp with no Python default *must* carry a server
    default, or the ORM will send a ``NULL`` for it. That sentence is the whole
    incident, and this is it as an assertion.
    """
    unfilled: list[str] = []
    for model in DISPATCH_MODELS:
        for column in model.__table__.columns:
            if not isinstance(column.type, DateTime) or column.nullable:
                continue
            if column.default is None and column.server_default is None:
                unfilled.append(f"{model.__tablename__}.{column.name}")
    # ``expires_at`` is passed explicitly by the caller and is not a timestamp
    # the database owns, so it is expected here.
    assert unfilled == ["message_dispatch_drafts.expires_at"], unfilled


def test_the_reported_columns_all_carry_a_server_default() -> None:
    missing: list[str] = []
    for model in DISPATCH_MODELS:
        for column in model.__table__.columns:
            key = (model.__tablename__, column.name)
            if key in EXPECTED_COLUMNS and column.server_default is None:
                missing.append(f"{key[0]}.{key[1]}")
    assert missing == [], missing


def test_the_models_declare_every_column_the_migration_alters(revision_0011: Any) -> None:
    """No column is altered that does not exist, and none is missed."""
    declared = {
        (model.__tablename__, column.name)
        for model in DISPATCH_MODELS
        for column in model.__table__.columns
    }
    for table, column in revision_0011.TIMESTAMP_COLUMNS:
        assert (table, column) in declared, f"{table}.{column} is not a model column"
