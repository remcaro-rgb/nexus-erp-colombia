"""Celery tasks of the workflow module (queue ``default``)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Protocol, cast

from celery import shared_task


class Task[**P, R](Protocol):
    """What the workflow code uses of a Celery task: call it inline or ``delay`` it."""

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R: ...

    def delay(self, *args: P.args, **kwargs: P.kwargs) -> Any: ...


def task[**P, R](name: str, **options: Any) -> Callable[[Callable[P, R]], Task[P, R]]:
    """Typed wrapper of Celery's (untyped) ``shared_task`` decorator: the signature of the task is kept."""
    return cast("Callable[[Callable[P, R]], Task[P, R]]", shared_task(name=name, **options))


@task(name="workflow.run_scheduled_job", acks_late=True, max_retries=5, default_retry_delay=30)
def run_scheduled_job(run_id: str) -> str:
    from workflow.scheduler.service import DbSchedulerService

    run = DbSchedulerService().execute(uuid.UUID(run_id))
    return str(run.state)


@task(name="workflow.scheduler_tick")
def scheduler_tick() -> int:
    from workflow.scheduler.service import DbSchedulerService

    return DbSchedulerService().tick() or 0


@task(name="workflow.deliver_webhooks")
def deliver_webhooks() -> int:
    from workflow.webhooks.service import WebhookService

    return WebhookService().deliver_due()
