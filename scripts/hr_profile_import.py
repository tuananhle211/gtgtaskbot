"""Load the HR sheet's "Nhân sự" tab into the profiles (``docs/hr/HR_MODULE_PLAN.md``, đợt 2).

Run inside the api container (accounts must exist - đợt 1's ``hr_org_import``)::

    docker compose cp staff-full.json api:/tmp/staff-full.json
    docker compose exec -T api python - --input /tmp/staff-full.json < scripts/hr_profile_import.py
    docker compose exec -T api python - --input /tmp/staff-full.json --apply \
        < scripts/hr_profile_import.py
    docker compose exec -T api rm /tmp/staff-full.json

``staff-full.json`` = the whole "Nhân sự" tab as a list of rows (header row
included). HCNS's workbook loads directly (0097): ``--input /tmp/ds.xlsx``
reads its tab "DS NHÂN SỰ" (``--sheet`` for another). Either file holds ID
numbers, phones and bank accounts: copy it in, run, delete it. The ID number,
bank accounts and insurance salary are sealed with the deployment's key
(``PR_SECRET_ENCRYPTION_KEY``).
Without ``--apply`` the transaction is rolled back. Only counts are printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from meobot.application.audit_service import AuditService
from meobot.application.hr.profiles import HrProfileService
from meobot.application.hr.sheet_import import (
    HrProfileImport,
    ProfileImportReport,
    table_from_xlsx,
)
from meobot.core.config import get_settings
from meobot.db.session import Database
from meobot.domain.audit.models import AuditAction, AuditEntry, AuditResult


class _DryRunError(Exception):
    """Raised inside the transaction to roll a dry run back."""


async def main() -> int:
    parser = argparse.ArgumentParser(description="Load HR profiles from the sheet")
    parser.add_argument("--input", required=True, help="rows as JSON, or an .xlsx workbook")
    parser.add_argument("--sheet", default="DS NHÂN SỰ", help="the workbook's tab (.xlsx only)")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    source = Path(args.input)
    table = (
        table_from_xlsx(source.read_bytes(), args.sheet)
        if source.suffix.lower() == ".xlsx"
        else json.loads(source.read_text(encoding="utf-8"))
    )

    settings = get_settings()
    db = Database(settings)
    report: ProfileImportReport | None = None
    try:
        async with db.session_factory() as session:
            try:
                async with session.begin():
                    audit = AuditService(session)
                    service = HrProfileService(session, settings, audit)
                    report = await HrProfileImport(session, service).run(table)
                    await audit.record(
                        AuditEntry(
                            request_id=uuid.uuid4(),
                            action=AuditAction.HR_PROFILE_BULK_UPDATED.value,
                            result=AuditResult.SUCCESS,
                            entity_type="hr_profile",
                            after_data={"sheet_import": True, **report.summary()},
                        )
                    )
                    if not args.apply:
                        raise _DryRunError
            except _DryRunError:
                pass
    finally:
        await db.dispose()
    assert report is not None
    print(json.dumps(report.summary(), ensure_ascii=False, indent=2))
    print("APPLIED." if args.apply else "DRY RUN - nothing written. Add --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
