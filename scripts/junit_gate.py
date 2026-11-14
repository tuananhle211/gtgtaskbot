#!/usr/bin/env python3
# ruff: noqa: S314 - CLI script (stdout is the interface, git/uv by name)
"""Fail only on NEW test failures: compare a JUnit XML report with the known list.

    python3 scripts/junit_gate.py report.xml [--known .github/known-failures.txt]
    python3 scripts/junit_gate.py report.xml --print-ids   # list failing ids (to seed the file)

A test id is "<classname> :: <name>" as the JUnit report writes it (pytest:
"tests.unit.test_x :: test_y"; vitest: "tests/x.test.tsx :: describe > it").
The known list holds failures that were already red on master when the gate was
added. Fixing one makes it pass -> the gate says so, and the line must be removed.
Never add a line to make your own change pass: fix the code or the test instead.

Standard library only.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KNOWN = ROOT / ".github" / "known-failures.txt"


def case_id(case: ET.Element) -> str:
    return f"{case.get('classname', '')} :: {case.get('name', '')}"


def read_report(path: Path) -> tuple[set[str], set[str]]:
    """(failing ids, passing ids)."""
    failing: set[str] = set()
    passing: set[str] = set()
    for case in ET.parse(path).getroot().iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            failing.add(case_id(case))
        elif case.find("skipped") is None:
            passing.add(case_id(case))
    return failing, passing


def read_known(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("--known", type=Path, default=DEFAULT_KNOWN)
    parser.add_argument("--print-ids", action="store_true")
    args = parser.parse_args()

    if not args.report.exists():
        print(f"junit_gate: no report at {args.report} - the test run crashed before writing it")
        return 1
    failing, passing = read_report(args.report)
    if args.print_ids:
        print("\n".join(sorted(failing)))
        return 0

    known = read_known(args.known)
    new = sorted(failing - known)
    fixed = sorted(known & passing)
    still = sorted(failing & known)

    if still:
        print(f"junit_gate: {len(still)} known failure(s) still red (listed in {args.known.name}).")
    if fixed:
        print("junit_gate: these known failures PASS now - remove them from the list:")
        print("  " + "\n  ".join(fixed))
    if new:
        print(f"junit_gate: {len(new)} NEW failure(s) - this change broke them:")
        print("  " + "\n  ".join(new))
        return 1
    print(f"junit_gate: OK - no new failures ({len(passing)} passed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
