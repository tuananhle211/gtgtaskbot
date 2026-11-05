#!/usr/bin/env python3
"""skill-sync (user brain, timer 5 min): approved skills in the Second Brain -> each agent.

For every agent of policy.yaml, finds `.../skills/<name>/SKILL.md` in the vault that the
agent may read (the same rules as brain-mcp: zones, `cam`, stricter `pham_vi`), never in
01-inbox-ai/ or 02-de-xuat/ (not approved yet), and mirrors those skill folders into
/srv/brain/skills/<agent>/ - which is that profile's `skills.external_dirs` (read-only for
Hermes). The whole folder is rebuilt and swapped, so a skill removed from the vault leaves
the agent too. Only rewrites when something changed.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import os
import shutil
import sys
from pathlib import Path

BRAIN_FILE = os.environ.get("BRAIN_MCP_FILE", "/opt/apexmed/brain-mcp.py")
spec = importlib.util.spec_from_loader("brain", importlib.machinery.SourceFileLoader("brain", BRAIN_FILE))
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)  # type: ignore[union-attr]

OUT = Path(os.environ.get("BRAIN_SKILLS", "/srv/brain/skills"))
NOT_APPROVED = ("01-inbox-ai/", "02-de-xuat/", "00-inbox/", "99-luu-tru/")


def skills_for(agent: str) -> dict[str, Path]:
    rules = brain.POLICY.rules(agent)
    found: dict[str, Path] = {}
    for skill_md in sorted(brain.VAULT.rglob("SKILL.md")):
        rel = brain.nfc(skill_md.relative_to(brain.VAULT).as_posix())
        parts = rel.split("/")
        if any(p.startswith(".") for p in parts) or rel.startswith(NOT_APPROVED):
            continue
        if len(parts) < 3 or parts[-3] != "skills":
            continue  # only <zone>/skills/<name>/SKILL.md
        if not brain.readable(rules, rel):
            continue
        name = parts[-2]
        if name in found:
            brain.log(f"skill-sync: {agent}: trùng tên '{name}' ({rel}) - giữ bản đầu {found[name]}")
            continue
        found[name] = skill_md.parent
    return found


def digest(skills: dict[str, Path], rules) -> str:
    h = hashlib.sha256()
    for name, folder in sorted(skills.items()):
        for f in sorted(folder.rglob("*")):
            if f.is_file() and rules.path_allowed(brain.nfc(f.relative_to(brain.VAULT).as_posix())):
                h.update(f"{name}/{f.relative_to(folder)}".encode())
                h.update(f.read_bytes())
    return h.hexdigest()


def main() -> None:
    data, _people = brain.POLICY._load()
    OUT.mkdir(parents=True, exist_ok=True)
    for agent in sorted((data.get("agents") or {}).keys()):
        rules = brain.POLICY.rules(agent)
        skills = skills_for(agent)
        stamp = digest(skills, rules)
        target = OUT / agent
        marker = target / ".digest"
        if target.is_dir() and marker.exists() and marker.read_text() == stamp:
            continue
        tmp = OUT / f".tmp-{agent}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(mode=0o755)
        for name, folder in skills.items():
            for f in folder.rglob("*"):
                rel = brain.nfc(f.relative_to(brain.VAULT).as_posix())
                if f.is_file() and not f.name.startswith(".") and rules.path_allowed(rel):
                    dest = tmp / name / f.relative_to(folder)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(f, dest)
        (tmp / ".digest").write_text(stamp)
        old = OUT / f".old-{agent}"
        shutil.rmtree(old, ignore_errors=True)
        if target.exists():
            target.rename(old)
        tmp.rename(target)
        shutil.rmtree(old, ignore_errors=True)
        brain.log(f"skill-sync: {agent}: {len(skills)} skill ({', '.join(sorted(skills)) or 'không có'})")


if __name__ == "__main__":
    sys.exit(main())
