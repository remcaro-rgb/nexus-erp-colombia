"""Nexus ERP Django project (settings, urls, asgi/wsgi, celery)."""

from erp.celery import app as celery_app

__all__ = ["celery_app"]
