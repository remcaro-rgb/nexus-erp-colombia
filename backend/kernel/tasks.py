"""Celery tasks and the tenancy-binding task base class."""

from __future__ import annotations

from typing import Any

from celery import Task, shared_task

from kernel import outbox, tenancy

CONTEXT_KWARG = "_erp_context"


class TenantTask(Task):  # type: ignore[misc]
    """Base class: a task called with ``_erp_context=ctx.to_dict()`` runs with
    that context active; ``kernel.uow`` binds it in each transaction."""

    abstract = True

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raw = kwargs.pop(CONTEXT_KWARG, None)
        ctx = tenancy.TenantContext.from_dict(raw) if raw else None
        with tenancy.activate(ctx):
            return super().__call__(*args, **kwargs)

    @classmethod
    def context_kwargs(cls, ctx: tenancy.TenantContext) -> dict[str, Any]:
        return {CONTEXT_KWARG: ctx.to_dict()}


@shared_task(name="kernel.dispatch_event", acks_late=True)  # type: ignore[untyped-decorator]
def dispatch_event(envelope: dict[str, Any]) -> dict[str, str]:
    return outbox.dispatch(envelope)
