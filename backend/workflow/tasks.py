"""Celery tasks of the workflow module (queue ``default``)."""

from __future__ import annotations

import uuid

from celery import shared_task


@shared_task(name="workflow.run_scheduled_job", acks_late=True, max_retries=5, default_retry_delay=30)
def run_scheduled_job(run_id: str) -> str:
    from workflow.scheduler.service import DbSchedulerService

    run = DbSchedulerService().execute(uuid.UUID(run_id))
    return str(run.state)


@shared_task(name="workflow.scheduler_tick")
def scheduler_tick() -> int:
    from workflow.scheduler.service import DbSchedulerService

    return DbSchedulerService().tick() or 0


@shared_task(name="workflow.deliver_webhooks")
def deliver_webhooks() -> int:
    from workflow.webhooks.service import WebhookService

    return WebhookService().deliver_due()
