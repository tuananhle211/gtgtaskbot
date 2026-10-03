"""FastAPI application.

The API is an operations and development surface, not the product: Telegram is
where the team works. Routes here are limited to health probes, read models,
development helpers and (later) OAuth callbacks. It binds to 127.0.0.1 on the
NAS and is not exposed to the internet.
"""

from meobot.api.main import create_app

__all__ = ["create_app"]
