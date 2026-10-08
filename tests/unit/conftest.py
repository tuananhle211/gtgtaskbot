"""Fixtures shared across the unit suites.

Only registration lives here. A fixture defined in a test module and imported
by another is both a lint error and a real coupling - the importing suite then
depends on the *file* rather than on the fixture - so a fixture two suites need
is built in its own module and named here.
"""

from __future__ import annotations

from tests.unit.pr_world import world
from tests.unit.work_clock import frozen_work_clock

__all__: list[str] = ["frozen_work_clock", "world"]
