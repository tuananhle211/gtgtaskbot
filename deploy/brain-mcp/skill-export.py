#!/usr/bin/env python3
"""skill-export (user hermes, timer 5 min): skills the agents learned -> Second Brain proposals.

With skills.write_approval on, Hermes stages every skill write an agent makes in
~/.hermes/profiles/<agent>/pending/skills/<id>.json instead of applying it. This job
turns each one into a full SKILL.md and sends it through brain-mcp's `de_xuat_skill`
WITH THAT AGENT'S OWN TOKEN (so it lands in 02-de-xuat/<agent>/skills/<name>/), then
moves the record to pending/skills-da-day/. Emma approves in Obsidian; skill-sync
brings approved skills back. Nothing is applied locally. Standard library only.
"""

from __future__ import annotations

import json
import shutil
import sys
import urllib.request
from pathlib import Path

PROFILES = Path.home() / ".hermes" / "profiles"
SYNCED = Path("/srv/brain/skills")
BRAIN = "http://127.0.0.1:8660/mcp"
WHOLE = {"create", "edit"}


def log(*parts: object) -> None:
    print(*parts, file=sys.stderr, flush=True)


def token_of(profile: Path) -> str | None:
    try:
        for line in (profile / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("BRAIN_MCP_TOKEN="):
                return line.split("=", 1)[1].strip() or None
    except OSError:
        return None
    return None


def current_skill(profile: Path, agent: str, name: str) -> str | None:
    for base in (profile / "skills", SYNCED / agent):
        for path in base.rglob("SKILL.md"):
            if path.parent.name == name:
                return path.read_text(encoding="utf-8", errors="replace")
    return None


def apply_op(profile: Path, agent: str, op: dict, draft: dict[str, str]) -> tuple[str | None, str]:
    """One skill write applied on top of `draft` (skills already changed in this record)."""
    action, name = op.get("action", ""), op.get("name", "")
    if action in WHOLE and op.get("content"):
        return op["content"], ("tạo skill mới" if action == "create" else "viết lại skill")
    if action == "patch":
        if op.get("content"):
            return op["content"], "viết lại skill"
        base = draft.get(name) or current_skill(profile, agent, name)
        old, new = op.get("old_string"), op.get("new_string")
        if base and old and old in base and new is not None:
            count = -1 if op.get("replace_all") else 1
            return base.replace(old, new, count), "sửa một đoạn skill"
        return None, "sửa một đoạn nhưng không tìm thấy skill / đoạn gốc"
    return None, f"thao tác '{action}' (chưa hỗ trợ đề xuất tự động)"


def proposals(profile: Path, agent: str, payload: dict) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """{skill name: (full SKILL.md, what happened)} for a record (single write or batch),
    and the operations that could not be turned into a proposal."""
    ops = payload.get("operations") if payload.get("action") == "batch" else [payload]
    draft: dict[str, str] = {}
    what: dict[str, str] = {}
    skipped: list[str] = []
    for op in ops or []:
        content, label = apply_op(profile, agent, op, draft)
        name = op.get("name") or "skill"
        if content is None:
            skipped.append(f"{name}: {label}")
            continue
        draft[name] = content
        what[name] = label if name not in what else f"{what[name]} + {label}"
    return {name: (draft[name], what[name]) for name in draft}, skipped


def propose(token: str, name: str, content: str, reason: str) -> str:
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "de_xuat_skill",
                   "arguments": {"ten": name, "noi_dung_skill_md": content, "ly_do": reason}},
    }
    req = urllib.request.Request(BRAIN, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["result"]["content"][0]["text"]


def main() -> None:
    for profile in sorted(p for p in PROFILES.iterdir() if (p / "config.yaml").exists()):
        pending = profile / "pending" / "skills"
        records = sorted(pending.glob("*.json")) if pending.is_dir() else []
        if not records:
            continue
        agent, token = profile.name, token_of(profile)
        if not token:
            log(f"{agent}: không có BRAIN_MCP_TOKEN, bỏ qua {len(records)} bản chờ")
            continue
        done = profile / "pending" / "skills-da-day"
        done.mkdir(parents=True, exist_ok=True)
        for path in records:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            skills, skipped = proposals(profile, agent, record.get("payload") or {})
            for note in skipped:
                log(f"{agent}/{note} - để lại cho người xem trên server")
            if not skills:
                continue
            origin = "tự xem lại hội thoại" if record.get("origin") == "background_review" else "trong lúc làm việc"
            sent = 0
            for name, (content, what) in skills.items():
                reason = f"Agent {agent} {what} ({origin}). {record.get('summary', '')}".strip()
                try:
                    answer = propose(token, name, content, reason[:500])
                except Exception as exc:  # brain down: try again next round
                    log(f"{agent}/{name}: chưa gửi được ({type(exc).__name__})")
                    continue
                if answer.startswith("Đã gửi"):
                    sent += 1
                    log(f"{agent}/{name}: đã đẩy lên 02-de-xuat")
                else:
                    log(f"{agent}/{name}: brain từ chối - {answer}")
            if sent == len(skills) and not skipped:
                shutil.move(str(path), done / path.name)


if __name__ == "__main__":
    main()
