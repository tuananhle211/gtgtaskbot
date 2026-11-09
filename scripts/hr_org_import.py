"""Load the HR sheet's org chart and staff (``docs/hr/HR_MODULE_PLAN.md``, đợt 1).

Run inside the api container, the input copied in first::

    docker compose cp staff.json api:/tmp/staff.json
    docker compose exec -T api python - --input /tmp/staff.json < scripts/hr_org_import.py
    # look at the summary, then for real:
    docker compose exec -T api python - --input /tmp/staff.json --apply \\
        --passwords /tmp/hr-passwords.xlsx < scripts/hr_org_import.py
    docker compose cp api:/tmp/hr-passwords.xlsx ./hr-passwords.xlsx   # then delete it in the box

``staff.json`` = the "Nhân sự" tab as a list of rows (header row included),
cut to the columns the load reads: Chi nhánh, Mã nhân viên, Họ và tên, MÃ
phòng ban, Chức danh nội bộ, Pháp nhân đóng BHXH, Ngày chính thức. ID numbers,
phones and birthdays are never needed - leave them out.

Without ``--apply`` everything runs in a transaction that is rolled back: the
summary is exactly what ``--apply`` would do. Only counts and department codes
are printed - never a name or a password. With ``--apply`` the new accounts'
temporary passwords go to ``--passwords`` (an .xlsx, mode 600) for HCNS to hand
out; nothing else ever holds them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from openpyxl import Workbook

from meobot.application.ads.sync_service import vn_today
from meobot.application.audit_service import AuditService
from meobot.application.org.hr_import import HrOrgImport, ImportReport, staff_rows
from meobot.core.config import get_settings
from meobot.db.session import Database
from meobot.domain.audit.models import AuditAction, AuditEntry, AuditResult
from meobot.domain.org.models import BRANCHES


class _DryRunError(Exception):
    """Raised inside the transaction to roll a dry run back."""


def _write_passwords(report: ImportReport, path: str) -> None:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Tài khoản mới"
    sheet.append(["Mã nhân viên", "Họ và tên", "Phòng ban", "Chi nhánh", "Mật khẩu tạm"])
    for account in report.new_accounts:
        sheet.append(
            [
                account.member_code,
                account.full_name,
                account.department,
                BRANCHES.get(account.branch or "", ""),
                account.password,
            ]
        )
    sheet.append([])
    sheet.append(["Đăng nhập bằng mã nhân viên + mật khẩu tạm; hệ thống bắt đổi mật khẩu lần đầu."])
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        book.save(handle)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--passwords", default="/tmp/hr-passwords.xlsx")  # noqa: S108
    args = parser.parse_args()

    rows = staff_rows(json.loads(Path(args.input).read_text(encoding="utf-8")))

    db = Database(get_settings())
    report: ImportReport | None = None
    try:
        async with db.session_factory() as session:
            try:
                async with session.begin():
                    report = await HrOrgImport(session, today=vn_today()).run(rows)
                    await AuditService(session).record(
                        AuditEntry(
                            request_id=uuid.uuid4(),
                            action=AuditAction.HR_ORG_IMPORTED.value,
                            result=AuditResult.SUCCESS,
                            entity_type="org",
                            after_data=report.summary(),
                        )
                    )
                    if not args.apply:
                        raise _DryRunError
                    if report.new_accounts:
                        # Written before the commit: a failed commit removes it.
                        _write_passwords(report, args.passwords)
            except _DryRunError:
                pass
            except BaseException:
                if args.apply and os.path.exists(args.passwords):
                    os.remove(args.passwords)
                raise
    finally:
        await db.dispose()

    assert report is not None
    print(json.dumps(report.summary(), ensure_ascii=False, indent=2))
    print(f"rows read: {len(rows)}")
    if args.apply:
        print(f"APPLIED. New accounts: {len(report.new_accounts)} -> {args.passwords}")
    else:
        print("DRY RUN - nothing written. Add --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
