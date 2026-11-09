"""Closed-loop HTTP load generator for the P22 performance suite (standard library only).

k6/Locust are not installable from the default cloud allowlist, so the harness ships its own small,
deterministic generator: N virtual users (threads) each issue requests back-to-back against a
**loopback** target and record latencies in integer nanoseconds (no floats in the measurements).
``evaluate()`` compares nearest-rank percentiles with ``budgets.json``.

    python3 tests/perf/loadgen.py --base http://127.0.0.1:8080 --path /api/health/live \\
        --users 20 --requests 50 --budget api_read --out reports/perf/health.json
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUDGETS = HERE / "budgets.json"
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def percentile_ns(samples: Sequence[int], pct: int) -> int:
    """Nearest-rank percentile (pct in 1..100) of integer samples."""
    if not samples:
        raise ValueError("no samples")
    if not 1 <= pct <= 100:
        raise ValueError("pct must be 1..100")
    ordered = sorted(samples)
    rank = -(-pct * len(ordered) // 100)  # ceil(pct/100 * n)
    return ordered[rank - 1]


@dataclass
class RunResult:
    name: str
    users: int
    requests: int
    latencies_ns: list[int] = field(default_factory=list)
    statuses: dict[int, int] = field(default_factory=dict)
    errors: int = 0
    wall_ns: int = 0

    def summary(self) -> dict[str, object]:
        lat = self.latencies_ns or [0]
        return {
            "name": self.name,
            "users": self.users,
            "requests": self.requests,
            "completed": len(self.latencies_ns),
            "errors": self.errors,
            "statuses": {str(k): v for k, v in sorted(self.statuses.items())},
            "p50_ms": percentile_ns(lat, 50) // 1_000_000,
            "p95_ms": percentile_ns(lat, 95) // 1_000_000,
            "p99_ms": percentile_ns(lat, 99) // 1_000_000,
            "max_ms": max(lat) // 1_000_000,
            "throughput_rpm": (len(self.latencies_ns) * 60_000_000_000 // self.wall_ns) if self.wall_ns else 0,
        }


Request = Callable[[], int]  # returns the HTTP status


def http_get(base: str, path: str, headers: dict[str, str] | None = None, timeout: int = 30) -> Request:
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK:
        raise ValueError(f"refusing non-loopback target {base!r} (DEV-only)")
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 80
    local = threading.local()

    def call() -> int:
        conn = getattr(local, "conn", None)
        if conn is None:
            conn = local.conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            conn.request("GET", path, headers=headers or {})
            r = conn.getresponse()
            r.read()
            return r.status
        except (OSError, http.client.HTTPException):
            conn.close()
            local.conn = None
            raise

    return call


def run(name: str, request: Request, users: int, requests_per_user: int) -> RunResult:
    res = RunResult(name=name, users=users, requests=users * requests_per_user)
    lock = threading.Lock()
    start = threading.Barrier(users)

    def worker() -> None:
        lat: list[int] = []
        st: dict[int, int] = {}
        err = 0
        start.wait()
        for _ in range(requests_per_user):
            t0 = time.perf_counter_ns()
            try:
                code = request()
            except (OSError, http.client.HTTPException):
                err += 1
                continue
            lat.append(time.perf_counter_ns() - t0)
            st[code] = st.get(code, 0) + 1
        with lock:
            res.latencies_ns.extend(lat)
            res.errors += err
            for k, v in st.items():
                res.statuses[k] = res.statuses.get(k, 0) + v

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(users)]
    t_start = time.perf_counter_ns()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    res.wall_ns = time.perf_counter_ns() - t_start
    return res


def load_budgets(path: Path = BUDGETS) -> dict[str, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: int(v) for k, v in data["p95_ms"].items()}


def evaluate(result: RunResult, budget_key: str, budgets: dict[str, int] | None = None) -> dict[str, object]:
    budgets = budgets if budgets is not None else load_budgets()
    s = result.summary()
    limit = budgets[budget_key]
    ok_status = all(200 <= int(code) < 400 for code in s["statuses"])  # type: ignore[union-attr]
    passed = s["p95_ms"] < limit and result.errors == 0 and ok_status  # type: ignore[operator]
    return {**s, "budget": budget_key, "p95_budget_ms": limit, "passed": passed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P22 loopback load generator")
    ap.add_argument("--base", required=True)
    ap.add_argument("--path", required=True)
    ap.add_argument("--users", type=int, default=10)
    ap.add_argument("--requests", type=int, default=20)
    ap.add_argument("--budget", default="api_read")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    res = run(args.path, http_get(args.base, args.path), args.users, args.requests)
    report = evaluate(res, args.budget)
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
