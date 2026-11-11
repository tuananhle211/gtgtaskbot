"""The API runs one process with one event loop: CPU-heavy work called straight
from an ``async def`` holds up every other request (measured 11/11/2026: a
timesheet export pushed a plain ``/api/auth/session`` from 0.01 s to 0.4 s).

Workbook building / reading must go through ``asyncio.to_thread`` (plan
``docs/ops/SCALING_PLAN.md`` A). This test reads the source: no ``async def``
in ``src/meobot`` may *call* one of the heavy helpers directly - passing it to
``asyncio.to_thread(helper, ...)`` is a reference, not a call, and is fine.
Code inside a nested plain ``def`` (the thing handed to the thread) is not
checked either.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "meobot"

#: Functions whose work is openpyxl / file parsing - seconds of CPU on a big file.
HEAVY = {
    "Workbook",
    "load_workbook",
    "read_workbook",
    "parse_workbook",
    "export_rows",
    "template_workbook",
    "report_workbook",
    "table_from_xlsx",
    "read_upload",
    "_profiles_workbook",
    "_read_sheets",
}


def _called_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _direct_calls(function: ast.AsyncFunctionDef) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue  # a nested function runs wherever it is sent
        if isinstance(node, ast.Call):
            name = _called_name(node)
            if name in HEAVY:
                found.append((name, node.lineno))
        stack.extend(ast.iter_child_nodes(node))
    return found


def test_01_no_async_function_builds_or_reads_a_workbook_on_the_event_loop() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                for name, line in _direct_calls(node):
                    offenders.append(f"{path.relative_to(SRC)}:{line} {node.name}() calls {name}()")
    assert offenders == [], "Run these through `await asyncio.to_thread(...)`:\n" + "\n".join(
        offenders
    )


def test_02_the_guard_sees_a_direct_call_and_ignores_a_thread_hand_off() -> None:
    source = """
async def bad():
    return export_rows(rows)

async def good():
    def build():
        return Workbook()
    return await asyncio.to_thread(export_rows, rows), await asyncio.to_thread(build)
"""
    tree = ast.parse(source)
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    assert _direct_calls(functions["bad"]) == [("export_rows", 3)]
    assert _direct_calls(functions["good"]) == []
