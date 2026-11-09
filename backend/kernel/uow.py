"""Unit of work (C2-6): atomic transaction + tenancy binding + idempotency +
domain events → outbox + serialization/deadlock retry.

Two entry points:

* ``with unit_of_work(ctx, idempotency_key=..., request=...) as uow:`` — one
  attempt. If ``uow.replay`` is not ``None`` the command already ran: return
  ``uow.replay`` and do nothing else. Call ``uow.set_response(body, status)``
  to store the response for future replays. Nested calls join the outer UoW.
* ``run_in_unit_of_work(fn, ctx, ...)`` — retries ``fn(uow)`` on SQLSTATE
  40001/40P01 up to 3 times with jitter and handles replays automatically.
  Application services should prefer this form.

External I/O through ``requests``/``httpx`` raises while a UoW is active.
"""

from __future__ import annotations

import contextlib
import contextvars
import random
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, TypeVar

import structlog
from django.db import DatabaseError, OperationalError, connections, transaction

from kernel import idempotency, io_guard, tenancy
from kernel.errors import RetryExhausted, UnitOfWorkRequired, ValidationFailed
from kernel.events import DomainEvent
from kernel.idempotency import StoredResponse

log = structlog.get_logger("kernel.uow")

RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})
MAX_RETRIES = 3

T = TypeVar("T")

_current: contextvars.ContextVar[UnitOfWork | None] = contextvars.ContextVar("erp_current_uow", default=None)


@dataclass
class UnitOfWork:
    ctx: tenancy.TenantContext
    using: str = "default"
    idempotency_key: str | None = None
    request_hash: str | None = None
    replay: StoredResponse | None = None
    events: list[DomainEvent] = field(default_factory=list)
    response: StoredResponse | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    _after_commit: list[Callable[[], None]] = field(default_factory=list)

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        company_id: uuid.UUID | None = None,
        causation_id: uuid.UUID | None = None,
    ) -> DomainEvent:
        """Collect a domain event; it is written to ``kernel_outbox`` at commit."""
        event = DomainEvent.build(self.ctx, event_type, payload, company_id=company_id, causation_id=causation_id)
        self.events.append(event)
        return event

    def set_response(self, body: Any, status_code: int = 200) -> None:
        self.response = StoredResponse(body=body, status_code=status_code)

    def on_commit(self, fn: Callable[[], None]) -> None:
        """Run ``fn`` after a successful commit (never inside the transaction)."""
        self._after_commit.append(fn)


def current_uow() -> UnitOfWork | None:
    return _current.get()


def require_uow() -> UnitOfWork:
    uow = _current.get()
    if uow is None:
        raise UnitOfWorkRequired()
    return uow


def _flush(uow: UnitOfWork) -> None:
    from kernel import outbox

    if uow.events:
        outbox.write(uow.events, using=uow.using)
        uow.events.clear()
    if uow.idempotency_key is not None:
        resp = uow.response or StoredResponse(body=None, status_code=204)
        idempotency.complete(uow.ctx.tenant_id, uow.idempotency_key, resp.body, resp.status_code, uow.using)


@contextlib.contextmanager
def unit_of_work(
    ctx: tenancy.TenantContext,
    *,
    idempotency_key: str | None = None,
    request: Any = None,
    scope: str = "",
    using: str = "default",
) -> Iterator[UnitOfWork]:
    outer = _current.get()
    if outer is not None:
        if idempotency_key is not None:
            raise ValidationFailed("a nested unit of work cannot take an Idempotency-Key")
        if outer.ctx != ctx:
            raise ValidationFailed("a nested unit of work must use the same tenant context")
        yield outer
        return

    uow = UnitOfWork(ctx=ctx, using=using, idempotency_key=idempotency_key)
    io_token = io_guard.enter_uow()
    uow_token = _current.set(uow)
    try:
        with tenancy.activate(ctx), transaction.atomic(using=using):
            tenancy.bind(ctx, using)
            if idempotency_key is not None:
                uow.request_hash = idempotency.request_hash(request, scope)
                uow.replay = idempotency.claim(ctx.tenant_id, idempotency_key, uow.request_hash, scope, using)
            yield uow
            if uow.replay is None:
                _flush(uow)
            for fn in uow._after_commit:
                transaction.on_commit(fn, using=using)
    finally:
        _current.reset(uow_token)
        io_guard.exit_uow(io_token)


def _sqlstate(exc: BaseException) -> str | None:
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        state = getattr(cur, "sqlstate", None) or getattr(cur, "pgcode", None)
        if state:
            return str(state)
        cur = cur.__cause__ or cur.__context__
    return None


def is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, DatabaseError) and _sqlstate(exc) in RETRYABLE_SQLSTATES


def _backoff(attempt: int) -> None:
    # Full jitter, 10–80 ms ceiling growing per attempt.
    time.sleep(random.uniform(0, 0.01 * (2**attempt)))  # noqa: S311 - jitter, not crypto


def run_in_unit_of_work[T](
    fn: Callable[[UnitOfWork], T],
    ctx: tenancy.TenantContext,
    *,
    idempotency_key: str | None = None,
    request: Any = None,
    scope: str = "",
    using: str = "default",
    max_retries: int = MAX_RETRIES,
) -> T | StoredResponse:
    """Run ``fn(uow)`` atomically; retry on serialization failure / deadlock.

    Returns ``fn``'s result, or the :class:`StoredResponse` of a replay.
    """
    if _current.get() is not None:
        raise ValidationFailed("run_in_unit_of_work cannot be nested; call fn(current_uow())")
    attempt = 0
    while True:
        try:
            with unit_of_work(ctx, idempotency_key=idempotency_key, request=request, scope=scope, using=using) as uow:
                if uow.replay is not None:
                    return uow.replay
                return fn(uow)
        except (OperationalError, DatabaseError) as exc:
            if not is_retryable(exc):
                raise
            if connections[using].in_atomic_block:
                raise  # an outer transaction owns the retry decision
            attempt += 1
            log.warning("uow.retry", attempt=attempt, sqlstate=_sqlstate(exc))
            if attempt > max_retries:
                raise RetryExhausted(f"gave up after {max_retries} retries") from exc
            _backoff(attempt)


def transactional(
    *, scope: str = "", max_retries: int = MAX_RETRIES
) -> Callable[[Callable[..., T]], Callable[..., T | StoredResponse]]:
    """Decorator: ``@transactional()`` on ``def svc(uow, *args, **kwargs)``.

    The wrapped function is called as ``svc(ctx, *args, idempotency_key=None,
    request=None, **kwargs)``.
    """

    def deco(func: Callable[..., T]) -> Callable[..., T | StoredResponse]:
        def wrapper(
            ctx: tenancy.TenantContext,
            *args: Any,
            idempotency_key: str | None = None,
            request: Any = None,
            **kwargs: Any,
        ) -> T | StoredResponse:
            return run_in_unit_of_work(
                lambda uow: func(uow, *args, **kwargs),
                ctx,
                idempotency_key=idempotency_key,
                request=request,
                scope=scope or func.__qualname__,
                max_retries=max_retries,
            )

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return deco
