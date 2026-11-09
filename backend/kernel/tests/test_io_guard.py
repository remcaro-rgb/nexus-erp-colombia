import asyncio

import httpx
import pytest
import requests

from kernel import io_guard, uow
from kernel.errors import ExternalIOForbidden


@pytest.mark.django_db
def test_T_P01_08_external_io_forbidden_inside_uow(ctx):
    with uow.unit_of_work(ctx):
        assert io_guard.uow_active()
        with pytest.raises(ExternalIOForbidden):
            requests.get("http://127.0.0.1:9/never", timeout=1)
        with pytest.raises(ExternalIOForbidden), httpx.Client() as client:
            client.get("http://127.0.0.1:9/never")

        async def call():
            async with httpx.AsyncClient() as client:
                await client.get("http://127.0.0.1:9/never")

        with pytest.raises(ExternalIOForbidden):
            asyncio.run(call())
    assert not io_guard.uow_active()


def test_T_P01_08_outside_uow_the_call_is_attempted():
    io_guard.install()  # idempotent
    with pytest.raises(requests.ConnectionError):
        requests.get("http://127.0.0.1:9/never", timeout=1)
    with pytest.raises(httpx.ConnectError), httpx.Client() as client:
        client.get("http://127.0.0.1:9/never")
    assert getattr(requests.Session.request, "__erp_guarded__", False)
