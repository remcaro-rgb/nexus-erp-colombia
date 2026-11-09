"""Invariant-12 leak framework (prompt P22 §4, OWASP API1 BOLA / API5 BFLA / API3 property level).

Pipeline::

    cases   = generate_cases(operations, matrix, dataset)     # pure, from the contract
    results = LeakSuite(transport, dataset).run(cases)        # calls the system under test
    write_matrix_csv(results, path)                            # evidence for G3

A :class:`Transport` adapts any channel (Django test client, live HTTP, AI tool runner, MCP client,
export/report runners, webhook capture) to ``request(...) -> Response``. Operations the transport
does not implement yet are reported ``NOT_IMPLEMENTED`` (never PASSED); authenticated cases without an
authenticator are ``BLOCKED`` with the reason - the matrix is honest about what was really exercised.
"""

from __future__ import annotations

import csv
import enum
import io
import json
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .openapi import Channel, Operation
from .oracle import Dataset, Row, can_see, expected_statuses, forbidden_fields
from .principals import Matrix, Principal


class CaseKind(enum.StrEnum):
    ANONYMOUS = "anonymous"  # no credentials -> 401
    VISIBILITY = "visibility"  # collection read: rows ⊆ policy, masked fields absent
    OWN_OBJECT = "own_object"  # visible object: 200 + masked fields absent
    BOLA_FOREIGN_TENANT = "bola_foreign_tenant"
    BOLA_OUT_OF_SCOPE = "bola_out_of_scope"  # same tenant, other company / owner / warehouse
    BFLA = "bfla"  # permission missing -> 403


class Status(enum.StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    BLOCKED = "BLOCKED"
    NO_DATA = "NO_DATA"


@dataclass(frozen=True, slots=True)
class LeakCase:
    op: Operation
    principal: Principal | None
    kind: CaseKind
    target: Row | None = None

    @property
    def principal_key(self) -> str:
        return self.principal.key if self.principal else "anonymous"

    @property
    def case_id(self) -> str:
        tgt = str(self.target.id) if self.target else "-"
        return f"{self.op.key}|{self.principal_key}|{self.kind.value}|{tgt}"


@dataclass
class Response:
    status: int
    body: Any = None
    headers: Mapping[str, str] = field(default_factory=dict)

    def rows(self) -> list[dict[str, Any]]:
        """Rows of a page ``{items: [...]}``, a JSON list, a single object or CSV text."""
        body = self.body
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8", errors="replace")
        if isinstance(body, str):
            text = body.strip()
            if not text:
                return []
            if text[0] in "[{":
                try:
                    body = json.loads(text)
                except json.JSONDecodeError:
                    return []
            else:
                return [dict(r) for r in csv.DictReader(io.StringIO(text))]
        if isinstance(body, dict):
            if isinstance(body.get("items"), list):
                return [r for r in body["items"] if isinstance(r, dict)]
            if isinstance(body.get("data"), list):
                return [r for r in body["data"] if isinstance(r, dict)]
            return [body]
        if isinstance(body, list):
            return [r for r in body if isinstance(r, dict)]
        return []


class Transport(Protocol):
    name: str

    def implemented(self, op: Operation) -> bool: ...

    def can_authenticate(self, principal: Principal) -> bool: ...

    def request(self, op: Operation, path: str, principal: Principal | None, headers: dict[str, str]) -> Response: ...


@dataclass
class CaseResult:
    case: LeakCase
    status: Status
    expected: frozenset[int] = frozenset()
    actual: int | None = None
    detail: str = ""


def concrete_path(op: Operation, target: Row | None, filler: uuid.UUID | None = None) -> str:
    path = op.path
    for name in op.path_params:
        if name == "id" and target is not None:
            value = str(target.id)
        else:
            value = str(filler or uuid.UUID(int=0))
        path = path.replace("{" + name + "}", value)
    return path


def _pick(rows: Iterable[Row], pred: Callable[[Row], bool]) -> Row | None:
    for r in rows:
        if pred(r):
            return r
    return None


def generate_cases(ops: Iterable[Operation], matrix: Matrix, dataset: Dataset) -> list[LeakCase]:
    """All cases for ``ops`` x principals. Pure; depends only on the contract and the dataset.

    Allowed + visible *writes* are never generated: the leak suite must not change state. Their
    positive behaviour is covered by module tests and the cross-module scenarios.
    """
    cases: list[LeakCase] = []
    for op in ops:
        if op.portal:
            continue  # portal APIs are party-scoped by token (D-033): own suite with external identities
        cases.append(LeakCase(op, None, CaseKind.ANONYMOUS))
        rows = dataset.of(op.resource)
        for p in matrix.principals:
            allowed = p.allows(op.permission)
            if not op.targets_one_object:
                if not allowed:
                    cases.append(LeakCase(op, p, CaseKind.BFLA))
                elif op.is_read:
                    cases.append(LeakCase(op, p, CaseKind.VISIBILITY))
                continue
            own = _pick(rows, lambda r, p=p: can_see(p, r))
            foreign = _pick(rows, lambda r, p=p: r.tenant_id != p.tenant.id)
            scoped = _pick(rows, lambda r, p=p: r.tenant_id == p.tenant.id and not can_see(p, r))
            if not allowed:
                cases.append(LeakCase(op, p, CaseKind.BFLA, own or foreign))
            elif op.is_read:
                cases.append(LeakCase(op, p, CaseKind.OWN_OBJECT, own))
            if allowed or foreign is not None:
                cases.append(LeakCase(op, p, CaseKind.BOLA_FOREIGN_TENANT, foreign))
            if scoped is not None:
                cases.append(LeakCase(op, p, CaseKind.BOLA_OUT_OF_SCOPE, scoped))
    return cases


def _flatten(obj: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            name = f"{prefix}{k}"
            yield name, v
            if isinstance(v, dict):
                yield from _flatten(v, name + ".")
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        yield from _flatten(item, name + "[].")


def check_rows(principal: Principal, op: Operation, rows: list[dict[str, Any]], dataset: Dataset) -> list[str]:
    """Problems in returned rows: invisible/unknown rows and masked fields present."""
    problems: list[str] = []
    banned = forbidden_fields(principal, op)
    for item in rows:
        rid = item.get("id")
        row = dataset.get(rid) if rid is not None else None
        if rid is not None and row is None:
            problems.append(f"unknown row id {rid} (not in the synthetic dataset)")
        elif row is not None and not can_see(principal, row):
            problems.append(f"row {rid} outside policy (tenant/company/scope)")
        tid = item.get("tenant_id")
        if tid is not None and str(tid) != str(principal.tenant.id):
            problems.append(f"row {rid} carries foreign tenant_id {tid}")
        cid = item.get("company_id")
        if cid is not None and str(cid) not in {str(c) for c in principal.company_ids}:
            problems.append(f"row {rid} carries company_id {cid} outside the principal's companies")
        for name, value in _flatten(item):
            if name in banned and value not in (None, "", [], {}):
                problems.append(f"row {rid} exposes masked field {name!r}")
    return problems


class LeakSuite:
    def __init__(self, transport: Transport, dataset: Dataset) -> None:
        self.transport = transport
        self.dataset = dataset

    def headers_for(self, op: Operation, case: LeakCase) -> dict[str, str]:
        h = {"Accept": "application/json", "Accept-Language": "es-CO"}
        if op.method != "get":
            h["Idempotency-Key"] = str(uuid.uuid5(uuid.NAMESPACE_URL, case.case_id))
        if op.method in ("patch", "put", "delete"):
            h["If-Match"] = '"0"'
        p = case.principal
        if p is not None:
            company = case.target.company_id if case.target and case.target.company_id else sorted(p.company_ids)[0]
            h["X-Company-Id"] = str(company)
        return h

    def run_case(self, case: LeakCase) -> CaseResult:
        op = case.op
        if not self.transport.implemented(op):
            return CaseResult(case, Status.NOT_IMPLEMENTED, detail=f"{self.transport.name}: no route")
        if case.principal is not None and not self.transport.can_authenticate(case.principal):
            return CaseResult(case, Status.BLOCKED, detail=f"{self.transport.name}: cannot authenticate principal")
        if op.targets_one_object and case.kind is not CaseKind.ANONYMOUS and case.target is None:
            return CaseResult(case, Status.NO_DATA, detail=f"no synthetic {op.resource} row for this case")
        expected = expected_statuses(case.principal, op, case.target)
        path = concrete_path(op, case.target)
        resp = self.transport.request(op, path, case.principal, self.headers_for(op, case))
        problems: list[str] = []
        if resp.status not in expected:
            problems.append(f"status {resp.status} not in {sorted(expected)}")
        if 200 <= resp.status < 300 and case.principal is not None and op.is_read:
            problems += check_rows(case.principal, op, resp.rows(), self.dataset)
        if resp.status >= 400 and case.principal is not None:
            problems += _check_error_body(resp, self.dataset, case)
        status = Status.FAILED if problems else Status.PASSED
        return CaseResult(case, status, expected, resp.status, "; ".join(problems))

    def run(self, cases: Iterable[LeakCase]) -> list[CaseResult]:
        return [self.run_case(c) for c in cases]


def _check_error_body(resp: Response, dataset: Dataset, case: LeakCase) -> list[str]:
    """Error bodies must not echo data of the protected object (existence/data oracle)."""
    if case.target is None or not case.target.fields:
        return []
    text = resp.body if isinstance(resp.body, str) else json.dumps(resp.body, default=str, ensure_ascii=False)
    leaked = [k for k, v in case.target.fields.items() if isinstance(v, str) and len(v) >= 6 and v in text]
    return [f"error body echoes protected field(s) {sorted(leaked)}"] if leaked else []


MATRIX_HEADER = (
    "operation",
    "channel",
    "permission",
    "principal",
    "case",
    "target",
    "expected",
    "actual",
    "status",
    "detail",
)


def write_matrix_csv(results: Iterable[CaseResult], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(MATRIX_HEADER)
        for r in results:
            c = r.case
            w.writerow(
                (
                    c.op.key,
                    c.op.channel.value,
                    c.op.permission,
                    c.principal_key,
                    c.kind.value,
                    str(c.target.id) if c.target else "",
                    " ".join(str(s) for s in sorted(r.expected)),
                    "" if r.actual is None else r.actual,
                    r.status.value,
                    r.detail,
                )
            )
    return path


def summarize(results: Iterable[CaseResult]) -> dict[str, Any]:
    results = list(results)
    by_status = Counter(r.status.value for r in results)
    by_channel: dict[str, Counter[str]] = {}
    for r in results:
        by_channel.setdefault(r.case.op.channel.value, Counter())[r.status.value] += 1
    return {
        "cases": len(results),
        "by_status": dict(sorted(by_status.items())),
        "by_channel": {k: dict(sorted(v.items())) for k, v in sorted(by_channel.items())},
        "failed": [f"{r.case.case_id}: {r.detail}" for r in results if r.status is Status.FAILED],
    }


def channels_covered(ops: Iterable[Operation]) -> set[Channel]:
    return {op.channel for op in ops}
