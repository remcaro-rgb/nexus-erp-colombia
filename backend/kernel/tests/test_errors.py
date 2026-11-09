from rest_framework import exceptions as drf_exc
from rest_framework.test import APIRequestFactory

from kernel import errors


def _ctx(path="/api/x"):
    request = APIRequestFactory().get(path)
    request.trace_id = "trace-1"
    return {"request": request}


def test_problem_json_for_domain_errors():
    exc = errors.Conflict("period is closed", code="period_closed", period="2026-01")
    r = errors.drf_exception_handler(exc, _ctx())
    assert r.status_code == 409 and r.content_type == errors.PROBLEM_CONTENT_TYPE
    assert r.data == {
        "type": errors.PROBLEM_TYPE_BASE + "period_closed",
        "title": "Conflict",
        "status": 409,
        "detail": "period is closed",
        "code": "period_closed",
        "instance": "/api/x",
        "trace_id": "trace-1",
        "errors": {"period": "2026-01"},
    }
    assert errors.Conflict.code == "conflict"  # class code untouched
    assert errors.NotFound().to_problem()["detail"] == "Not found"


def test_problem_json_for_drf_and_unknown_errors():
    r = errors.drf_exception_handler(drf_exc.ValidationError({"f": ["bad"]}), _ctx())
    assert r.status_code == 400 and r.data["code"] == "validation_failed" and r.data["errors"] == {"f": ["bad"]}
    r = errors.drf_exception_handler(drf_exc.NotAuthenticated(), {"request": None})
    assert r.status_code == 401 and "instance" not in r.data
    from django.http import Http404

    assert errors.drf_exception_handler(Http404(), _ctx()).data["code"] == "not_found"

    class Odd(drf_exc.APIException):
        status_code = 418

    assert errors.drf_exception_handler(Odd(), _ctx()).data["code"] == "api_error"
    assert errors.drf_exception_handler(ValueError("x"), _ctx()) is None


def test_error_registry_is_unique_and_complete():
    codes = errors.kernel_error_codes()
    assert codes["idempotency_key_reused"] == 422
    assert codes["external_io_in_transaction"] == 500
    assert codes["parameter_unknown"] == 422 and codes["sequence_exhausted"] == 409
    assert all(400 <= s < 600 for s in codes.values())
    assert len(codes) == len({c for c in codes})
