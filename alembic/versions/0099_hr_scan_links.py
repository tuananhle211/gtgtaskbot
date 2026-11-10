"""Hồ sơ nhân sự: the scan folder becomes a list of Drive links.

Revision ID: 0099
Revises: 0098
Create Date: 2026-11-10

HCNS's workbook keeps 1 to 4 Drive links per person in "Hồ sơ scan" (one per
scanned paper), sometimes beside a few words. 0097 stored the whole cell in
``hr_profiles.scan_link``. Now every link is a row of ``hr_scan_links``
(label, url; added, edited, deleted on the profile's "Tài liệu đính kèm"),
and what is left of the cell once the links are taken out stays as a note:
``scan_link`` is renamed ``scan_note``.

Downgrade
---------

Puts the links back into the text (one per line, "label url", after the
note), renames the column back and drops the table.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0099"
down_revision: str | None = "0098"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = sa.Uuid().with_variant(postgresql.UUID(as_uuid=True), "postgresql")
URL = re.compile(r"https?://[^\s,;]+", re.IGNORECASE)


def split_cell(text: str | None) -> tuple[list[tuple[str | None, str]], str | None]:
    """The links of a "Hồ sơ scan" cell as ``(label, url)`` in order, once
    each, and the words left (``None`` when nothing is).

    Line by line: words just before a link on its line name it ("CV <link>",
    "Kế toán giữ: <link>"); a line that is only the tail of the link above
    (a URL the sheet wrapped: ``abc_28/view?usp=drive_link``) is glued back
    on; any other words are the note."""
    if not text:
        return [], None
    found: list[tuple[str | None, str]] = []
    notes: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if found and not URL.search(line) and _URL_TAIL.fullmatch(line):
            label, url = found[-1]
            found[-1] = (label, url + line)
            continue
        cursor = 0
        for match in URL.finditer(line):
            before = _clean_words(line[cursor : match.start()])
            found.append((before, match.group(0)))
            cursor = match.end()
        after = _clean_words(line[cursor:])
        if after:
            notes.append(after)
    seen: set[str] = set()
    links: list[tuple[str | None, str]] = []
    for label, url in found:
        if url not in seen:
            seen.add(url)
            links.append((label, url))
    return links, " ".join(notes) or None


#: The tail of a wrapped URL: no spaces, a path or query in it.
_URL_TAIL = re.compile(r"[\w\-.~%/?=&#+]*[/?=][\w\-.~%/?=&#+]*")


def _clean_words(text: str) -> str | None:
    words = " ".join(re.sub(r"[\s,;|\-\u2013]+", " ", text).split()).strip(" :")
    return words or None


def upgrade() -> None:
    op.create_table(
        "hr_scan_links",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_hr_scan_links_user_id", "hr_scan_links", ["user_id"])
    with op.batch_alter_table("hr_profiles") as batch:
        batch.alter_column("scan_link", new_column_name="scan_note")

    bind = op.get_bind()
    links = sa.table(
        "hr_scan_links",
        sa.column("id", _UUID),
        sa.column("user_id", _UUID),
        sa.column("label", sa.String(200)),
        sa.column("url", sa.Text()),
        sa.column("sort_order", sa.Integer()),
    )
    rows = bind.execute(
        sa.text("SELECT user_id, scan_note FROM hr_profiles WHERE scan_note IS NOT NULL")
    ).all()
    for user_id, text in rows:
        found, rest = split_cell(text)
        if found:
            bind.execute(
                links.insert(),
                [
                    {
                        "id": uuid.uuid4(),
                        "user_id": user_id,
                        "label": None if label is None else label[:200],
                        "url": url[:2000],
                        "sort_order": index,
                    }
                    for index, (label, url) in enumerate(found)
                ],
            )
        bind.execute(
            sa.text("UPDATE hr_profiles SET scan_note = :rest WHERE user_id = :user"),
            {"rest": rest, "user": user_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    gathered: dict[object, list[str]] = {}
    for user_id, label, url in bind.execute(
        sa.text(
            "SELECT user_id, label, url FROM hr_scan_links ORDER BY user_id, sort_order, created_at"
        )
    ).all():
        gathered.setdefault(user_id, []).append(f"{label} {url}" if label else url)
    for user_id, urls in gathered.items():
        note = bind.execute(
            sa.text("SELECT scan_note FROM hr_profiles WHERE user_id = :user"), {"user": user_id}
        ).scalar()
        text = "\n".join([*([note] if note else []), *urls])
        bind.execute(
            sa.text("UPDATE hr_profiles SET scan_note = :text WHERE user_id = :user"),
            {"text": text, "user": user_id},
        )
    with op.batch_alter_table("hr_profiles") as batch:
        batch.alter_column("scan_note", new_column_name="scan_link")
    op.drop_index("ix_hr_scan_links_user_id", table_name="hr_scan_links")
    op.drop_table("hr_scan_links")
