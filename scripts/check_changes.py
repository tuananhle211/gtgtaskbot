#!/usr/bin/env python3
# ruff: noqa: S603, S607 - CLI script (stdout is the interface, git/uv by name)
"""Definition-of-done gate for a change: run it before saying "xong".

    python3 scripts/check_changes.py                 # changed files vs HEAD + this branch
    python3 scripts/check_changes.py --base origin/dev
    python3 scripts/check_changes.py --full          # whole suites (slow: backend ~18 min)
    python3 scripts/check_changes.py --docs-ok "lý do"   # behaviour unchanged, no doc update
    python3 scripts/check_changes.py --docs-only     # CI: only the docs rule
    python3 scripts/check_changes.py --status        # is the last green run still current?

What it does, from the changed files:
  1. maps each file to a module doc (docs/modules/<m>.md) - the same table as CLAUDE.md;
  2. backend touched -> ruff check, ruff format --check, mypy src, then pytest on the
     tests each module doc lists + the changed test files + the always-run guards;
  3. frontend touched -> tsc + the whole vitest suite (~45 s);
  4. a new migration -> one Alembic head, and CLAUDE.md names it as the head;
  5. docs rule: every module whose code changed must have its doc changed too
     (or pass --docs-ok "<why the behaviour did not change>").
On success it stamps a fingerprint of the code in .git/, which the Stop hook
(.claude/hooks/stop_check.py) compares with the tree.

Standard library only: the hooks run it with the system python3.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Code whose fingerprint the Stop hook watches.
CODE_PATHS = ["src", "tests", "alembic", "frontend/src", "frontend/tests", "scripts", ".github"]

# path prefix -> module doc. Keep in step with the table in CLAUDE.md.
MODULE_MAP: list[tuple[str, str]] = [
    ("src/meobot/domain/org/", "org"),
    ("src/meobot/application/org/", "org"),
    ("src/meobot/application/units/", "org"),
    ("src/meobot/api/routers/departments.py", "org"),
    ("src/meobot/api/routers/permissions.py", "org"),
    ("src/meobot/api/routers/units.py", "org"),
    ("src/meobot/application/account/", "account"),
    ("src/meobot/domain/account/", "account"),
    ("src/meobot/api/routers/account.py", "account"),
    ("src/meobot/domain/board/", "board"),
    ("src/meobot/application/board/", "board"),
    ("src/meobot/application/tasks/", "board"),
    ("src/meobot/api/routers/board.py", "board"),
    ("src/meobot/api/routers/tasks.py", "board"),
    ("src/meobot/domain/orders/", "ord"),
    ("src/meobot/application/orders/", "ord"),
    ("src/meobot/api/routers/orders.py", "ord"),
    ("src/meobot/application/pr_", "pr"),
    ("src/meobot/api/routers/pr", "pr"),
    ("src/meobot/domain/pr/", "pr"),
    ("src/meobot/domain/seeding/", "pr"),
    ("src/meobot/application/seeding/", "pr"),
    ("src/meobot/domain/performance/", "performance"),
    ("src/meobot/application/performance/", "performance"),
    ("src/meobot/domain/ads/", "ads"),
    ("src/meobot/application/ads/", "ads"),
    ("src/meobot/integrations/meta/", "ads"),
    ("src/meobot/integrations/crm/", "ads"),
    ("src/meobot/tasks/ads.py", "ads"),
    ("src/meobot/api/routers/ads", "ads"),
    ("src/meobot/domain/finance/", "finance"),
    ("src/meobot/application/finance/", "finance"),
    ("src/meobot/application/crm/", "finance"),
    ("src/meobot/api/routers/finance", "finance"),
    ("src/meobot/domain/hr/", "hr"),
    ("src/meobot/application/hr/", "hr"),
    ("src/meobot/application/hr_", "hr"),
    ("src/meobot/api/routers/hr", "hr"),
    ("src/meobot/domain/resources/", "resources"),
    ("src/meobot/application/resources/", "resources"),
    ("src/meobot/api/routers/resources", "resources"),
    ("src/meobot/domain/cskh/", "cskh"),
    ("src/meobot/application/cskh/", "cskh"),
    ("src/meobot/integrations/pancake/", "cskh"),
    ("src/meobot/integrations/zalo_bridge/", "cskh"),
    ("src/meobot/api/routers/cskh", "cskh"),
    ("deploy/zalo-bridge/", "cskh"),
    ("src/meobot/application/agents/", "agents"),
    ("src/meobot/domain/agents/", "agents"),
    ("src/meobot/api/routers/agent", "agents"),
    ("src/meobot/application/assistant/", "assistant"),
    ("src/meobot/domain/assistant/", "assistant"),
    ("src/meobot/tools/report_tools.py", "assistant"),
    ("src/meobot/application/ai/", "assistant"),
    ("src/meobot/domain/ai/", "assistant"),
    ("src/meobot/application/cron/", "platform"),
    ("src/meobot/application/ops/", "platform"),
    ("frontend/src/components/shell.tsx", "platform"),
    ("frontend/src/app/orders/", "ord"),
    ("frontend/src/app/tasks/", "board"),
    ("frontend/src/app/pr/", "pr"),
    ("frontend/src/app/seeding/", "pr"),
    ("frontend/src/app/performance/", "performance"),
    ("frontend/src/app/ads/", "ads"),
    ("frontend/src/app/finance/", "finance"),
    ("frontend/src/app/hr/", "hr"),
    ("frontend/src/app/resources/", "resources"),
    ("frontend/src/app/cskh/", "cskh"),
    ("frontend/src/app/agents/", "agents"),
    ("frontend/src/app/account/", "account"),
    ("frontend/src/app/people/", "org"),
]

# Run on every backend change: cheap tests that guard cross-module rules.
ALWAYS_BACKEND_TESTS = ["tests/unit/test_event_loop_guard.py"]

TEST_REF = re.compile(r"(?:frontend/)?tests/(?:unit|integration)/test_[a-z0-9_]+\.py")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def changed_files(base: str | None) -> list[str]:
    """Uncommitted + untracked files, plus the branch's commits since `base`."""
    files: set[str] = set()
    files.update(git("diff", "--name-only", "HEAD").split())
    files.update(git("ls-files", "--others", "--exclude-standard").split())
    if base:
        merge_base = git("merge-base", "HEAD", base).strip()
        files.update(git("diff", "--name-only", f"{merge_base}...HEAD").split())
    return sorted(f for f in files if f)


def module_of(path: str) -> str | None:
    for prefix, module in MODULE_MAP:
        if path.startswith(prefix):
            return module
    return None


def fingerprint() -> str:
    """Hash of every uncommitted change under CODE_PATHS (tracked diff + untracked files)."""
    digest = hashlib.sha256()
    digest.update(git("rev-parse", "HEAD").encode())
    digest.update(git("diff", "HEAD", "--binary", "--", *CODE_PATHS).encode())
    for name in sorted(
        git("ls-files", "--others", "--exclude-standard", "--", *CODE_PATHS).split()
    ):
        path = ROOT / name
        digest.update(name.encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def stamp_path() -> Path:
    git_dir = Path(git("rev-parse", "--git-dir").strip())
    if not git_dir.is_absolute():
        git_dir = ROOT / git_dir
    return git_dir / "apm-check.stamp"


def has_code_changes() -> bool:
    return bool(
        git("diff", "HEAD", "--name-only", "--", *CODE_PATHS).strip()
        or git("ls-files", "--others", "--exclude-standard", "--", *CODE_PATHS).strip()
    )


def run(title: str, cmd: list[str], cwd: Path = ROOT, env: dict[str, str] | None = None) -> bool:
    print(f"\n=== {title}\n$ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})})
    ok = result.returncode == 0
    print(f"--> {'OK' if ok else 'FAILED'}: {title}", flush=True)
    return ok


def run_gated(title: str, cmd: list[str], report: Path, cwd: Path = ROOT) -> bool:
    """Run a test command that writes JUnit XML; pass unless a failure is NEW
    (not in .github/known-failures.txt)."""
    report.unlink(missing_ok=True)
    run(title, cmd, cwd=cwd)
    return run(
        f"{title}: new failures only", [sys.executable, "scripts/junit_gate.py", str(report)]
    )


def report_dir() -> Path:
    git_dir = stamp_path().parent
    path = git_dir / "apm-reports"
    path.mkdir(exist_ok=True)
    return path


def tests_listed_in(module: str) -> set[str]:
    doc = ROOT / "docs" / "modules" / f"{module}.md"
    if not doc.exists():
        return set()
    return set(TEST_REF.findall(doc.read_text(encoding="utf-8")))


def check_docs(files: list[str], docs_ok: str | None) -> bool:
    touched = {m for f in files if (m := module_of(f))}
    docs_changed = {
        Path(f).stem for f in files if f.startswith("docs/modules/") and f.endswith(".md")
    }
    missing = sorted(touched - docs_changed)
    print("\n=== Docs rule (CLAUDE.md: a behaviour change updates docs/modules/<module>.md)")
    print(f"modules touched: {', '.join(sorted(touched)) or '-'}")
    if not missing:
        print("--> OK: every touched module has its doc updated")
        return True
    if docs_ok:
        print(f"--> WAIVED for {', '.join(missing)}: {docs_ok}")
        return True
    print(
        f"--> FAILED: code changed but no doc change for: {', '.join(missing)}\n"
        "    Update docs/modules/<module>.md (Quy tắc nghiệp vụ hiện hành + one line in "
        'Mốc thay đổi), or re-run with --docs-ok "<why behaviour did not change>".'
    )
    return False


def check_migrations(files: list[str]) -> bool:
    new = [f for f in files if re.match(r"alembic/versions/\d{4}_.+\.py$", f)]
    if not new:
        return True
    print("\n=== Migrations")
    numbers = sorted(
        p.name[:4] for p in (ROOT / "alembic" / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")
    )
    head = numbers[-1]
    dupes = sorted({n for n in numbers if numbers.count(n) > 1})
    ok = True
    if dupes:
        print(f"--> FAILED: two migrations share a number: {dupes} (rebase on dev, renumber)")
        ok = False
    claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    if f"Migration head: **{head}**" not in claude_md:
        print(f"--> FAILED: CLAUDE.md does not name {head} as 'Migration head' - update that line")
        ok = False
    if ok:
        print(f"--> OK: head {head}, CLAUDE.md up to date")
    print(
        "    Reminder: deploy runs `alembic upgrade head` on prod. The migration must be "
        "reversible-safe on real data (no NOT NULL without default/backfill, no data loss)."
    )
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", help="also include commits since merge-base with this ref")
    parser.add_argument(
        "--full", action="store_true", help="run the whole backend + frontend suites"
    )
    parser.add_argument("--docs-ok", metavar="REASON", help="waive the docs rule, with the reason")
    parser.add_argument(
        "--docs-only", action="store_true", help="only the docs + migration rules (CI)"
    )
    parser.add_argument(
        "--status", action="store_true", help="exit 0 if the last green run matches the tree"
    )
    args = parser.parse_args()

    if args.status:
        stamp = stamp_path()
        current = stamp.exists() and stamp.read_text().strip() == fingerprint()
        print("current" if current else "stale")
        return 0 if current else 1

    files = changed_files(args.base)
    print("Changed files:\n  " + "\n  ".join(files) if files else "No changed files.")

    results: list[bool] = [check_docs(files, args.docs_ok), check_migrations(files)]
    if args.docs_only:
        return 0 if all(results) else 1

    py = [
        f
        for f in files
        if f.endswith(".py") and f.split("/")[0] in {"src", "tests", "alembic", "scripts"}
    ]
    fe = [f for f in files if f.startswith("frontend/")]

    if py or args.full:
        results.append(run("ruff check", ["uv", "run", "ruff", "check", "src", "tests"]))
        results.append(
            run("ruff format --check", ["uv", "run", "ruff", "format", "--check", "src", "tests"])
        )
        results.append(run("mypy", ["uv", "run", "mypy", "src"]))
        junit = report_dir() / "pytest.xml"
        pytest = [
            "uv",
            "run",
            "pytest",
            "-o",
            "addopts=--strict-markers --strict-config",
            "-q",
            f"--junitxml={junit}",
        ]
        if args.full:
            results.append(run_gated("pytest (full suite)", pytest, junit))
        else:
            selected = set(ALWAYS_BACKEND_TESTS)
            for module in {m for f in files if (m := module_of(f))}:
                selected |= {t for t in tests_listed_in(module) if t.startswith("tests/")}
            selected |= {f for f in files if re.match(r"tests/.+/test_.+\.py$", f)}
            selected = {t for t in selected if (ROOT / t).exists()}
            results.append(
                run_gated(f"pytest ({len(selected)} files)", [*pytest, *sorted(selected)], junit)
            )
            print("    (CI runs the whole suite; run --full yourself for cross-module changes.)")

    if fe or args.full:
        frontend = ROOT / "frontend"
        results.append(run("tsc", ["npm", "run", "typecheck"], cwd=frontend))
        junit = report_dir() / "vitest.xml"
        results.append(
            run_gated(
                "vitest (full suite)",
                [
                    "npx",
                    "vitest",
                    "run",
                    "--reporter=default",
                    "--reporter=junit",
                    f"--outputFile.junit={junit}",
                ],
                junit,
                cwd=frontend,
            )
        )

    ok = all(results)
    if ok:
        stamp_path().write_text(fingerprint())
        print("\nALL GREEN - stamp written. Report what ran in the hand-over.")
    else:
        print("\nNOT DONE - fix the FAILED steps above (do not weaken a test to pass it).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
