"""Phòng Tài nguyên's catalogue ("bảng danh mục", 2026-10-16) onto a live database.

Idempotent: run it as often as you like, on local, dev or production.

    docker compose exec -T api python - < scripts/seed_resource_catalog.py

Pick-lists (``resource_options``): every entry of the catalogue sheet is
inserted when missing, renamed to the sheet's spelling when present (matched
by its stable ``code``), put in the sheet's order and switched on. Entries
the sheet does not list (e.g. an entry somebody added by hand) are left as
they are, after the sheet's.

Departments ("Ban"): ``D`` is renamed "Media Dựng"; "Media" (``MEDIA``) and
"Khác" (``KHAC``) are created (function NONE, default resource grants) when
missing. The employment statuses ("Trạng thái nhân sự") are code, not data:
Đang làm / Thử việc / Tạm nghỉ / Chuyển ban / Đã nghỉ việc.

Self-contained on purpose: it carries the catalogue itself, so it runs on a
server whose code predates it.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from meobot.application.org.departments import seed_module_defaults
from meobot.core.config import get_settings
from meobot.db.models.department import Department
from meobot.db.models.resource import ResourceOption
from meobot.db.session import Database
from meobot.domain.org.models import DepartmentFunction
from meobot.domain.resources.models import OptionKind, StatusGroup

#: (kind, code, name, status group), in the sheet's order.
CATALOGUE: tuple[tuple[OptionKind, str, str, StatusGroup | None], ...] = (
    (OptionKind.TYPE, "VIA", "Via", None),
    (OptionKind.TYPE, "PAGE", "Page", None),
    (OptionKind.TYPE, "GROUP", "Nhóm", None),
    (OptionKind.TYPE, "AI", "AI", None),
    (OptionKind.TYPE, "TKQC", "TKQC", None),
    (OptionKind.TYPE, "BM", "BM", None),
    (OptionKind.TYPE, "CAPCUT", "CapCut", None),
    (OptionKind.TYPE, "ADOBE", "Adobe", None),
    (OptionKind.TYPE, "VPS", "VPS", None),
    (OptionKind.TYPE, "TIKTOK_CHANNEL", "Kênh Tiktok", None),
    (OptionKind.TYPE, "TIKTOK_BC", "BC Tiktok", None),
    (OptionKind.TYPE, "TIKTOK_TKQC", "TKQC Tiktok", None),
    (OptionKind.TYPE, "DOMAIN", "Tên Miền", None),
    (OptionKind.TYPE, "MAGNIFIC", "Magnific", None),
    (OptionKind.TYPE, "GMAIL", "Gmail", None),
    (OptionKind.TYPE, "WEBCAKE", "Webcake", None),
    (OptionKind.TYPE, "FIGMA", "Figma", None),
    (OptionKind.TYPE, "SKETCHUP", "Sketchup", None),
    (OptionKind.TYPE, "YOUTUBE", "Youtube", None),
    (OptionKind.TYPE, "OTHER", "Khác", None),
    (OptionKind.ROLE, "OWNER_ADMIN", "Chủ sở hữu / Admin", None),
    (OptionKind.ROLE, "EDITOR", "Biên tập viên", None),
    (OptionKind.ROLE, "MODERATOR", "Kiểm duyệt viên", None),
    (OptionKind.ROLE, "ADVERTISER", "Nhà quảng cáo", None),
    (OptionKind.ROLE, "ANALYST", "Phân tích", None),
    (OptionKind.ROLE, "MEMBER", "Thành viên", None),
    (OptionKind.ROLE, "ACCOUNT_OWNER", "Chủ tài khoản (Via/AI)", None),
    (OptionKind.ROLE, "SHARED", "Được chia sẻ dùng chung", None),
    (OptionKind.ROLE, "OTHER", "Khác", None),
    (OptionKind.TWO_FACTOR, "YES", "Có", None),
    (OptionKind.TWO_FACTOR, "NO", "Không", None),
    (OptionKind.TWO_FACTOR, "UNKNOWN", "Không rõ", None),
    (OptionKind.ORIGIN, "COMPANY", "Công ty cấp", None),
    (OptionKind.ORIGIN, "SELF", "Tự tạo", None),
    (OptionKind.ORIGIN, "BOUGHT", "Mua ngoài", None),
    (OptionKind.ORIGIN, "RENTED", "Thuê", None),
    (OptionKind.ORIGIN, "INHERITED", "Nhận lại từ người khác", None),
    (OptionKind.ORIGIN, "UNKNOWN", "Không rõ", None),
    (OptionKind.HANDOVER, "NOT_YET", "Chưa bàn giao", None),
    (OptionKind.HANDOVER, "IN_PROGRESS", "Đang bàn giao", None),
    (OptionKind.HANDOVER, "DONE", "Đã bàn giao", None),
    (OptionKind.STATUS, "ACTIVE", "Hoạt động", StatusGroup.NORMAL),
    (OptionKind.STATUS, "RESTRICTED", "Hạn chế", StatusGroup.ATTENTION),
    (OptionKind.STATUS, "CHECKPOINT", "Checkpoint", StatusGroup.ATTENTION),
    (OptionKind.STATUS, "DIE", "Die / Mất", StatusGroup.LOST),
    (OptionKind.STATUS, "RETURNED", "Đã thu hồi", StatusGroup.CLOSED),
    (OptionKind.STATUS, "STOPPED", "Ngừng dùng", StatusGroup.CLOSED),
)

#: code → new name, for departments that exist.
RENAMED_DEPARTMENTS: dict[str, str] = {"D": "Media Dựng"}
#: (code, name) of departments the sheet lists and a database may lack.
NEW_DEPARTMENTS: tuple[tuple[str, str], ...] = (("MEDIA", "Media"), ("KHAC", "Khác"))


async def main() -> None:
    db = Database(get_settings())
    async with db.session_factory() as session:
        rows = (await session.scalars(select(ResourceOption))).all()
        by_key = {(row.kind, row.code): row for row in rows}
        added = renamed = 0
        for kind, code, name, group in CATALOGUE:
            row = by_key.get((kind, code))
            if row is None:
                row = ResourceOption(kind=kind, code=code, name=name, status_group=group)
                session.add(row)
                by_key[(kind, code)] = row
                added += 1
            elif row.name != name:
                print(f"  {kind.value} {code}: '{row.name}' -> '{name}'")
                row.name = name
                renamed += 1
            row.active = True
            if group is not None:
                row.status_group = group
        # The sheet's order first, anything else after it in its old order.
        listed = {(kind, code) for kind, code, _name, _group in CATALOGUE}
        for kind in OptionKind:
            position = 0
            for wanted_kind, code, _name, _group in CATALOGUE:
                if wanted_kind is kind:
                    by_key[(kind, code)].sort_order = position
                    position += 1
            extras = sorted(
                (row for key, row in by_key.items() if key[0] is kind and key not in listed),
                key=lambda row: (row.sort_order, row.name),
            )
            for row in extras:
                row.sort_order = position
                position += 1
        await session.flush()
        print(f"Pick-lists: {added} added, {renamed} renamed, {len(CATALOGUE)} in the catalogue.")

        departments = {row.code: row for row in (await session.scalars(select(Department))).all()}
        for code, name in RENAMED_DEPARTMENTS.items():
            row = departments.get(code)
            if row is not None and row.name != name:
                print(f"  Ban {code}: '{row.name}' -> '{name}'")
                row.name = name
        next_order = max((row.sort_order for row in departments.values()), default=-1) + 1
        for code, name in NEW_DEPARTMENTS:
            if code in departments:
                continue
            row = Department(
                code=code, name=name, function=DepartmentFunction.NONE, sort_order=next_order
            )
            next_order += 1
            session.add(row)
            await session.flush()
            seed_module_defaults(session, row)
            departments[code] = row
            print(f"  Ban {code} '{name}' created.")
        await session.commit()
        print("Departments:", ", ".join(f"{c}={r.name}" for c, r in sorted(departments.items())))


asyncio.run(main())
