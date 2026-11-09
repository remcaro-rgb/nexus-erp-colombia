"""Guard against external I/O inside a unit of work (constitution C2-6, ADR-006).

``install()`` wraps ``requests.Session.request``, ``httpx.Client.send`` and
``httpx.AsyncClient.send``. While a unit of work is active in the current
context, those calls raise :class:`kernel.errors.ExternalIOForbidden` before any
socket is opened. Use the outbox to trigger external effects after commit.
"""

from __future__ import annotations

import contextvars
import functools
from collections.abc import Callable
from typing import Any

from kernel.errors import ExternalIOForbidden

_uow_depth: contextvars.ContextVar[int] = contextvars.ContextVar("erp_uow_depth", default=0)
_installed = False


def uow_active() -> bool:
    return _uow_depth.get() > 0


def enter_uow() -> contextvars.Token[int]:
    return _uow_depth.set(_uow_depth.get() + 1)


def exit_uow(token: contextvars.Token[int]) -> None:
    _uow_depth.reset(token)


def check(target: str) -> None:
    if uow_active():
        raise ExternalIOForbidden(f"blocked external call to {target} inside a unit of work")


def _wrap_sync(func: Callable[..., Any], describe: Callable[..., str]) -> Callable[..., Any]:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        check(describe(*args, **kwargs))
        return func(*args, **kwargs)

    wrapper.__erp_guarded__ = True  # type: ignore[attr-defined]
    return wrapper


def _wrap_async(func: Callable[..., Any], describe: Callable[..., str]) -> Callable[..., Any]:
    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        check(describe(*args, **kwargs))
        return await func(*args, **kwargs)

    wrapper.__erp_guarded__ = True  # type: ignore[attr-defined]
    return wrapper


def _describe_requests(_self: Any, method: Any, url: Any, *a: Any, **k: Any) -> str:
    return f"{method} {url}"


def _describe_httpx(_self: Any, request: Any, *a: Any, **k: Any) -> str:
    return f"{getattr(request, 'method', '?')} {getattr(request, 'url', '?')}"


def install() -> None:
    """Idempotently install the guard on the HTTP client libraries present."""
    global _installed
    if _installed:
        return
    try:
        import requests

        if not getattr(requests.Session.request, "__erp_guarded__", False):
            requests.Session.request = _wrap_sync(  # type: ignore[method-assign]
                requests.Session.request, _describe_requests
            )
    except ImportError:  # pragma: no cover - requests is a dev dependency
        pass
    try:
        import httpx

        if not getattr(httpx.Client.send, "__erp_guarded__", False):
            httpx.Client.send = _wrap_sync(httpx.Client.send, _describe_httpx)  # type: ignore[method-assign]
        if not getattr(httpx.AsyncClient.send, "__erp_guarded__", False):
            httpx.AsyncClient.send = _wrap_async(  # type: ignore[method-assign]
                httpx.AsyncClient.send, _describe_httpx
            )
    except ImportError:  # pragma: no cover - httpx is a dev dependency
        pass
    _installed = True
