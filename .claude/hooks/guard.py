#!/usr/bin/env python3
# ruff: noqa: T201, S607, E501 - CLI script (stdout is the interface, git/uv by name)
"""PreToolUse guard for every Claude Code session in this repo (see CONTRIBUTING.md).

Everyone:
  - deny reading / printing secrets: .env (not .env.example), secrets/, root handover/,
    `docker compose config` without --quiet, env dumps inside containers;
  - deny `git push` (a push to master/dev deploys).
Everyone except the owner (marker file .claude/owner.local, gitignored):
  - deny `git commit` on master / dev / main: work on a feature branch;
  - ask a human before editing a red-zone file (frozen PR code, migrations,
    permissions, sync hooks, secrets box, deploy / CI / tooling, dependencies);
  - ask before destructive or remote commands (reset --hard, rm -rf, ssh, downgrade...);
  - remind the model when it edits an existing test.
Prints a JSON decision on stdout; prints nothing to let the call through.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OWNER = (ROOT / ".claude" / "owner.local").exists()

RED_ZONES: list[tuple[str, str]] = [
    ("src/meobot/application/pr_", "PR logic is frozen (CLAUDE.md hard rule)"),
    ("src/meobot/api/routers/pr", "PR routes are frozen (CLAUDE.md hard rule)"),
    ("src/meobot/domain/pr/", "PR logic is frozen (CLAUDE.md hard rule)"),
    ("alembic/versions/", "migrations run on prod at deploy - data safety review"),
    ("src/meobot/domain/org/modules.py", "permission registry - every module reads it"),
    ("src/meobot/application/org/access.py", "load_access - every request's permissions"),
    ("src/meobot/application/tasks/sync.py", "before_flush hook mirroring every task"),
    ("src/meobot/application/performance/publish_points.py", "after_flush points hook"),
    ("src/meobot/db/models/__init__.py", "installs the global flush hooks"),
    ("src/meobot/core/secrets.py", "SecretBox - secrets at rest"),
    ("src/meobot/core/config.py", "settings read by every service"),
    ("frontend/next.config.mjs", "API rewrites + security headers"),
    ("frontend/src/middleware.ts", "CSP / auth middleware"),
    (".github/", "CI and the deploy workflow (push = deploy)"),
    ("deploy/", "server deployment files"),
    ("docker-compose", "service topology (prod ports must stay 127.0.0.1)"),
    ("Dockerfile", "image build"),
    ("frontend/Dockerfile", "image build"),
    ("pyproject.toml", "dependencies / tool config"),
    ("uv.lock", "dependencies"),
    ("frontend/package.json", "dependencies"),
    ("frontend/package-lock.json", "dependencies"),
    (".claude/", "the guardrails themselves"),
    ("CLAUDE.md", "the rules every session loads"),
    ("scripts/check_changes.py", "the definition-of-done gate"),
    ("scripts/junit_gate.py", "the definition-of-done gate"),
]

SECRET_PATH = re.compile(
    r"(?:^|[\s'\"=:<>(/])"  # boundary before the name
    r"(?:\.env(?![.\w-]*example)(?:\.[\w-]+)?|secrets/|(?<![\w.-]/)handover/)"
)

ASK_COMMANDS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\bgit\s+(reset\s+--hard|clean\s+-\w*f|checkout\s+--\s|restore\s|stash\s+(drop|clear)|branch\s+-D)"
        ),
        "discards work",
    ),
    (re.compile(r"\brm\s+-\w*r\w*f|\brm\s+-\w*f\w*r"), "recursive delete"),
    (
        re.compile(r"\bdocker\s+(compose\s+.*down\s+.*-v|volume\s+rm|system\s+prune)"),
        "deletes Docker data (the local DB)",
    ),
    (re.compile(r"\balembic\s+downgrade\b"), "rolls the schema back"),
    (re.compile(r"\b(dropdb|DROP\s+(TABLE|DATABASE|SCHEMA)|TRUNCATE)\b", re.I), "destroys data"),
    (re.compile(r"(^|[\s;&|])(ssh|scp|rsync)\s"), "reaches a server (prod/dev VPS)"),
    (re.compile(r"\bgh\s+(pr\s+(merge|close)|repo|api\s+-X)"), "acts on GitHub"),
]


def emit(decision: str, reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


def context(text: str) -> None:
    print(
        json.dumps(
            {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}
        )
    )


def rel(path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            return str(p.resolve().relative_to(ROOT))
        except ValueError:
            return str(p)
    return str(Path(path))


def is_secret_rel(path: str) -> bool:
    name = Path(path).name
    return (
        (name == ".env" or (name.startswith(".env.") and "example" not in name))
        or path.startswith("secrets/")
        or path.startswith("handover/")
    )


def current_branch() -> str:
    try:
        return subprocess.run(
            ["git", "branch", "--show-current"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()
    except OSError:
        return ""


def check_bash(command: str) -> None:
    if SECRET_PATH.search(command) or str(ROOT / "handover") in command:
        emit(
            "deny",
            "Không đọc/in .env, secrets/ hay handover/ ở gốc repo (CLAUDE.md). "
            "Cần biết tên biến thì đọc .env.example.",
        )
        return
    if re.search(r"\bdocker\s+compose\b.*\bconfig\b", command) and not re.search(
        r"(-q|--quiet)\b", command
    ):
        emit(
            "deny",
            "`docker compose config` in ra giá trị bí mật từ .env - dùng `docker compose config --quiet`.",
        )
        return
    if re.search(r"\bdocker\s+(compose\s+)?(exec|run)\b.*\b(printenv|env)\s*($|[;&|])", command):
        emit("deny", "Không in biến môi trường trong container (chứa khoá bí mật).")
        return
    if re.search(r"\bgit\s+push\b", command):
        emit(
            "deny",
            "Không push: push lên master/dev là deploy. Người làm tự push nhánh feature "
            "của mình và mở PR vào dev (CONTRIBUTING.md).",
        )
        return
    if OWNER:
        return
    if re.search(r"\bgit\s+commit\b", command) and current_branch() in {"master", "main", "dev"}:
        emit(
            "deny",
            f"Đang ở nhánh {current_branch()}: tạo nhánh trước "
            "(git switch -c feat/<tên-ngắn> origin/dev), rồi commit ở đó.",
        )
        return
    for pattern, why in ASK_COMMANDS:
        if pattern.search(command):
            emit("ask", f"Lệnh này {why} - cần người xác nhận.")
            return


def check_edit(path: str, tool: str) -> None:
    r = rel(path)
    if is_secret_rel(r):
        emit("deny", "Không ghi vào .env / secrets/ / handover/.")
        return
    if OWNER:
        return
    for prefix, why in RED_ZONES:
        if r.startswith(prefix) or (
            prefix in {"docker-compose", "Dockerfile"} and Path(r).name.startswith(prefix)
        ):
            emit(
                "ask",
                f"Vùng đỏ: {r} - {why}. Sửa ở đây cần người duyệt kế hoạch trước "
                "(skill apm-change, bước 2). Đồng ý chỉ khi kế hoạch đã được duyệt.",
            )
            return
    is_test = re.match(r"(tests/|frontend/tests/)", r)
    if is_test and tool == "Edit" and (ROOT / r).exists():
        context(
            f"Bạn đang sửa một test có sẵn ({r}). Chỉ sửa khi hành vi được YÊU CẦU thay đổi; "
            "không nới assertion để code mới qua. Ghi lại test nào đổi kỳ vọng và vì sao trong báo cáo."
        )


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {}) or {}
    if tool == "Bash":
        check_bash(inp.get("command", ""))
    elif tool in {"Edit", "Write", "NotebookEdit"}:
        check_edit(inp.get("file_path") or inp.get("notebook_path") or "", tool)
    elif tool in {"Read", "Grep", "Glob"}:
        target = inp.get("file_path") or inp.get("path") or ""
        if target and is_secret_rel(rel(target)):
            emit("deny", "Không đọc .env / secrets/ / handover/ ở gốc repo (CLAUDE.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
