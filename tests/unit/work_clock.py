"""A frozen clock for the Work / KPI suites.

Those suites pin their reporting period to **September 2026** (``SEPTEMBER``,
``month()``, ``september()``, ``ready_month()``), while the services they drive
stamp approvals, completions and the counted instant with the real
``utcnow()``. Once the calendar left September the stamped instant fell into a
month the test never opened, so the quota engine filed the work under an
auto-created current month and the assertions on September came back empty.
Nothing in the application regressed; the tests were describing one month and
measuring another.

The services import ``utcnow`` by name, so the clock is pinned on every
``meobot.application.pr_*`` module that holds one, not on ``meobot.core.time``.
The moment sits inside the pinned month and after every
``SEPTEMBER + timedelta(minutes=n)`` the suites file, so ordering assertions
keep their meaning. A test that pins its own module clock afterwards (the
recurring-work suites do) still wins: its ``monkeypatch`` runs later.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator
from datetime import UTC, datetime
from types import ModuleType

import pytest

import meobot.application as application_package

#: 15 September 2026, 11:00 in Ho Chi Minh City.
WORK_NOW = datetime(2026, 9, 15, 4, 0, tzinfo=UTC)


def _stamping_modules() -> list[ModuleType]:
    """Every ``meobot.application.pr_*`` module that imported ``utcnow``."""
    modules: list[ModuleType] = []
    for info in pkgutil.iter_modules(application_package.__path__):
        if not info.name.startswith("pr_"):
            continue
        module = importlib.import_module(f"{application_package.__name__}.{info.name}")
        if hasattr(module, "utcnow"):
            modules.append(module)
    return modules


@pytest.fixture
def frozen_work_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[datetime]:
    """Pin every time stamp the PR / Work services write to ``WORK_NOW``."""
    for module in _stamping_modules():
        monkeypatch.setattr(module, "utcnow", lambda: WORK_NOW)
    yield WORK_NOW
