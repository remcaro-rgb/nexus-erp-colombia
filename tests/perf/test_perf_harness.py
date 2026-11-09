"""P22a perf harness self-tests: percentile maths, budgets, and a real loopback run against the
Django live server (small load; the 200-user targets run in P22d against the dev stack)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loadgen
from qa_harness import paths


def test_p22a_nearest_rank_percentiles():
    data = list(range(1, 101))
    assert loadgen.percentile_ns(data, 50) == 50
    assert loadgen.percentile_ns(data, 95) == 95
    assert loadgen.percentile_ns(data, 100) == 100
    assert loadgen.percentile_ns([7], 95) == 7
    with pytest.raises(ValueError):
        loadgen.percentile_ns([], 95)
    with pytest.raises(ValueError):
        loadgen.percentile_ns([1], 0)


def test_p22a_budgets_match_prompt_targets():
    b = loadgen.load_budgets()
    assert b == {
        "api_read": 400,
        "api_posting_le_50_lines": 800,
        "pos_checkout": 300,
        "report_query_5m_lines": 3000,
        "outbox_lag": 5000,
    }


def test_p22a_evaluate_fails_on_slow_or_erroring_runs():
    fast = loadgen.RunResult("x", 1, 3, latencies_ns=[1_000_000] * 3, statuses={200: 3}, wall_ns=3_000_000)
    assert loadgen.evaluate(fast, "api_read")["passed"] is True
    slow = loadgen.RunResult("x", 1, 3, latencies_ns=[500_000_000] * 3, statuses={200: 3}, wall_ns=1)
    assert loadgen.evaluate(slow, "api_read")["passed"] is False
    errs = loadgen.RunResult("x", 1, 3, latencies_ns=[1] * 2, statuses={500: 2}, errors=1, wall_ns=1)
    assert loadgen.evaluate(errs, "api_read")["passed"] is False


def test_p22a_refuses_non_loopback_targets():
    with pytest.raises(ValueError):
        loadgen.http_get("http://example.com", "/")
    with pytest.raises(ValueError):
        loadgen.http_get("https://127.0.0.1", "/")


@pytest.fixture
def wsgi_server():
    """The real Django WSGI app on 127.0.0.1:<ephemeral> (threaded wsgiref; loopback only).

    pytest-django's ``live_server`` cannot be used yet: settings define no STATIC_URL and its static
    handler crashes (noted in docs/handoffs/P22a.md)."""
    import socketserver
    import threading
    from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

    from django.core.wsgi import get_wsgi_application

    class Quiet(WSGIRequestHandler):
        def log_message(self, *args):
            return None

    class Threaded(socketserver.ThreadingMixIn, WSGIServer):
        daemon_threads = True

    srv = make_server("127.0.0.1", 0, get_wsgi_application(), server_class=Threaded, handler_class=Quiet)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_p22a_loopback_run_against_real_wsgi_app(wsgi_server):
    res = loadgen.run("health-live", loadgen.http_get(wsgi_server, "/api/health/live"), users=8, requests_per_user=25)
    report = loadgen.evaluate(res, "api_read")
    out = paths.REPORTS / "perf" / "p22a-health-live.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    assert report["completed"] == 200 and report["errors"] == 0
    assert report["statuses"] == {"200": 200}
    assert report["passed"], report
