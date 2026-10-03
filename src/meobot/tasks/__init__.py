"""Celery application and background tasks."""

from meobot.tasks.celery_app import celery_app

__all__ = ["celery_app"]
