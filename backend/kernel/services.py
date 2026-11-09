"""Service registry: modules register implementations of kernel interfaces.

``register(PostingService, impl)`` at app ready; consumers call
``get(PostingService)``. Tests register the contract test doubles from
``kernel.testing.fakes``. A missing service fails closed.

Interfaces are ``typing.Protocol`` classes, so the parameters are typed as
``Callable[..., T]`` (mypy refuses abstract classes for ``type[T]``).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import Any, cast

from kernel.errors import ServiceUnavailable

_services: dict[Any, Any] = {}


def _name(interface: Any) -> str:
    return str(getattr(interface, "__name__", interface))


def register[T](interface: Callable[..., T], implementation: T, *, replace: bool = False) -> None:
    if interface in _services and not replace:
        raise ValueError(f"{_name(interface)} already registered")
    runtime_protocol = getattr(interface, "_is_runtime_protocol", False)
    if runtime_protocol and not isinstance(implementation, cast(type, interface)):
        raise TypeError(f"{type(implementation).__name__} does not implement {_name(interface)}")
    _services[interface] = implementation


def get[T](interface: Callable[..., T]) -> T:
    try:
        return cast(T, _services[interface])
    except KeyError:
        raise ServiceUnavailable(f"{_name(interface)} is not available") from None


def optional[T](interface: Callable[..., T]) -> T | None:
    return cast("T | None", _services.get(interface))


def unregister(interface: Any) -> None:
    _services.pop(interface, None)


@contextlib.contextmanager
def override[T](interface: Callable[..., T], implementation: T) -> Iterator[T]:
    previous = _services.get(interface)
    register(interface, implementation, replace=True)
    try:
        yield implementation
    finally:
        if previous is None:
            unregister(interface)
        else:
            _services[interface] = previous
