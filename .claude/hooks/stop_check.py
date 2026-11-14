#!/usr/bin/env python3
# ruff: noqa: T201, E501 - CLI script (stdout is the interface, git/uv by name)
"""Stop hook: the model may not end a turn with unchecked code changes.

If code under scripts/check_changes.py CODE_PATHS differs from the last green run
of that script, the stop is blocked once with an instruction to run it (or to say
plainly why not). Skipped for the owner (.claude/owner.local) and on the second
stop of a turn (stop_hook_active), so it can never loop.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def main() -> int:
    if (ROOT / ".claude" / "owner.local").exists():
        return 0
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        data = {}
    if data.get("stop_hook_active"):
        return 0
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import check_changes as cc

        if not cc.has_code_changes():
            return 0
        stamp = cc.stamp_path()
        if stamp.exists() and stamp.read_text().strip() == cc.fingerprint():
            return 0
    except (subprocess.CalledProcessError, OSError):
        return 0
    print(
        json.dumps(
            {
                "decision": "block",
                "reason": (
                    "Có thay đổi code chưa qua cổng kiểm tra. Chạy `python3 scripts/check_changes.py` "
                    "(thêm `--base origin/dev` nếu nhánh đã có commit), sửa hết bước FAILED, rồi mới báo xong "
                    "theo mẫu báo cáo của skill apm-change. Nếu lượt này bạn không sửa code (thay đổi có sẵn "
                    "từ trước) hoặc đang dừng giữa chừng để hỏi người dùng, nói rõ điều đó rồi dừng."
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
