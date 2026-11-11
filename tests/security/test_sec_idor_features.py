"""T-X-QA-03 (security, gate G3): leak/IDOR suite over all portal endpoints and the new internal endpoints (P22c).

Real data, real stack: ``idor_world`` builds tenant T1 with real documents of P04/P05/P09/P10/P11/P16 and portal
identities, and tenant T2. Every **implemented** contract operation whose path belongs to a FEATURES-ADDENDUM API
(``SCOPE``) or to the portal (``/api/portal/v1``) is exercised through the real Django stack (URLconf, OIDC verifier,
P04 policy engine, row-level security) with these cases:

internal operations
* ``anonymous``    no token                                   -> 401/403
* ``nogrant``      T1 user of ALFA with no module grant        -> 401/403/404 (BFLA), never 2xx
* ``beta@ALFA``    T1 user of BETA only, X-Company-Id = ALFA   -> 401/403/404 (company outside the membership)
* ``beta@BETA``    same user in its own company, T1-ALFA ids  -> 403/404 on object routes; lists show no ALFA id
* ``t2admin``      all-powerful user of tenant T2, T1 ids      -> 403/404 on object routes; lists show no T1 id
* ``portal``       a supplier-portal token on internal routes -> 401/403 (D-033: external users never get internal roles)
Object ids come from the T1 admin's own list responses and every object route is first fetched by that admin
(positive control: the id is real and reachable), so a 404 for another principal proves isolation, not a typo.
Writes (POST/PATCH/PUT/DELETE) are only ever attempted by principals that must be refused; no allowed write runs.

portal operations (supplier A/B, customer, employee)
* anonymous and internal tokens -> 401/403; another portal kind -> 401/403/404;
* supplier A with supplier B's object ids (taken from B's own portal lists) and with internal T1 ids -> 403/404;
* every portal list/detail body of A contains no id of B, no B party id, no internal-only note.

Any 2xx where a refusal is required, any 5xx, or a protected id in a body is a finding. The matrix is written to
``reports/leak/p22c-idor-matrix.csv`` and the summary to ``reports/leak/p22c-idor-summary.json``.
"""

from __future__ import annotations

import csv
import json
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import purchasing.portal  # noqa: F401  (CCR P11b-1: the P11b migrations join the app only once imported)
import purchasing.requisitions  # noqa: F401
import pytest
from idor_world import IdorWorld, idor_world
from qa_harness import openapi, paths, routes
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

#: path prefixes of the FEATURES-ADDENDUM APIs (docs/spec/features/feature-registry.json › features[].api)
SCOPE = (
    "/api/v1/ledger/dimension",
    "/api/v1/iam/companies/",
    "/api/v1/iam/branches",
    "/api/v1/iam/external-accounts",
    "/api/v1/tax/exogena",
    "/api/v1/reporting",
    "/api/v1/treasury",
    "/api/v1/inventory",
    "/api/v1/purchasing",
    "/api/v1/workflow/formulas",
    "/api/v1/sales/price-formulas",
    "/api/v1/sales/pricing",
    "/api/v1/einvoice/providers",
    "/api/v1/payroll",
    "/api/v1/dataio",
)
PORTAL = "/api/portal/v1"
# 405 = the contract lists a method the route does not implement (counted as not implemented, no data either way)
REFUSED = {401, 403, 405}
HIDDEN = {401, 403, 404, 405}  # 404 preferred for foreign objects; uniform 403 tolerated (no existence oracle either way)
NOT_FOUND = {403, 404, 405}
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
FILLER = "0190f000-0000-7000-8000-00000000dead"
PARAM_RE = re.compile(r"\{[^}]+\}")

#: Registered NON-LEAKING deviations (filed defects, CCRs P22c-*): (operation, principal, status) -> defect id.
#: A matching row is excluded from the main assertion only when its body leaked nothing (status deviation only);
#: each defect has its own strict-xfail test below, so the gap stays visible and the marker must go once fixed.
KNOWN_DEVIATIONS: dict[tuple[str, str, int], str] = {
    ("GET /api/v1/workflow/formulas", "nogrant", 200): "DEF-P22-070",
    ("POST /api/v1/purchasing/rfqs/{rfq_id}/quotes/{id}/attachments", "nogrant", 422): "DEF-P22-071",
    ("POST /api/v1/purchasing/rfqs/{rfq_id}/quotes/{id}/attachments", "beta@BETA", 422): "DEF-P22-071",
    ("POST /api/v1/purchasing/rfqs/{rfq_id}/quotes/{id}/attachments", "t2admin", 422): "DEF-P22-071",
    ("POST /api/v1/treasury/statements:import", "nogrant", 422): "DEF-P22-072",
    ("POST /api/v1/payroll/novelties:bulk", "nogrant", 422): "DEF-P22-073",
}


# --------------------------------------------------------------------------------------------- transport


class Client:
    def __init__(self, token: str | None, company: uuid.UUID | None = None) -> None:
        self.c = APIClient()
        self.token = token
        self.company = company

    def call(self, method: str, path: str, company: uuid.UUID | None = None) -> Any:
        h: dict[str, str] = {}
        if self.token:
            h["HTTP_AUTHORIZATION"] = f"Bearer {self.token}"
        cid = company or self.company
        if cid:
            h["HTTP_X_COMPANY_ID"] = str(cid)
        if method != "get":
            h["HTTP_IDEMPOTENCY_KEY"] = str(uuid.uuid4())
            h["HTTP_IF_MATCH"] = '"0"'
            return getattr(self.c, method)(path, {}, format="json", **h)
        return self.c.get(path, **h)


def _ids(resp: Any) -> list[str]:
    try:
        data = resp.json()
    except (ValueError, TypeError):
        return []
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [str(i["id"]) for i in items if isinstance(i, dict) and isinstance(i.get("id"), str)]


def _text(resp: Any) -> str:
    try:
        return resp.content.decode("utf-8", "replace")
    except AttributeError:
        return ""


# --------------------------------------------------------------------------------------------- scope


def _implemented(op: openapi.Operation) -> bool:
    return routes.resolves(PARAM_RE.sub(FILLER, op.path))


def in_scope_ops() -> tuple[list[openapi.Operation], list[openapi.Operation]]:
    internal, portal = [], []
    for op in openapi.operations():
        if op.path.startswith(PORTAL):
            if _implemented(op):
                portal.append(op)
        elif op.path.startswith(SCOPE) and _implemented(op):
            internal.append(op)
    return internal, portal


# --------------------------------------------------------------------------------------------- resolution


class Resolver:
    """Concrete object paths for templates, from the given principal's own list responses (positive control)."""

    def __init__(self, client: Client, gets: set[str]) -> None:
        self.client = client
        self.gets = gets  # GET templates of the contract (to know which prefixes are listable)
        self.lists: dict[str, list[str]] = {}
        self.pool: set[str] = set()

    def list_ids(self, concrete_list: str) -> list[str]:
        if concrete_list not in self.lists:
            r = self.client.call("get", concrete_list)
            self.lists[concrete_list] = _ids(r) if r.status_code == 200 else []
            self.pool.update(self.lists[concrete_list])
            self.pool.update(m for m in UUID_RE.findall(_text(r)) if r.status_code == 200)
        return self.lists[concrete_list]

    def resolve(self, template: str) -> str | None:
        """First concrete path whose parameters are real ids reachable by the principal (detail GET 200 if any)."""
        parts = PARAM_RE.split(template)
        names = PARAM_RE.findall(template)
        detail = template.split(":")[0] if ":" in template.rsplit("/", 1)[-1] else template

        def walk(i: int, prefix: str) -> str | None:
            if i == len(names):
                path = prefix + parts[i]
                check = path.split(":")[0] if ":" in path.rsplit("/", 1)[-1] else path
                if ("get", detail) in self._get_templates() or detail in self.gets:
                    r = self.client.call("get", check)
                    return path if r.status_code == 200 else None
                return path
            seg = prefix + parts[i]
            listing = seg.rstrip("/")
            cands = self.list_ids(listing) if listing else []
            for cand in cands[:3]:
                hit = walk(i + 1, seg + cand)
                if hit:
                    return hit
            return None

        return walk(0, "")

    def _get_templates(self) -> set[tuple[str, str]]:
        return {("get", t) for t in self.gets}


# --------------------------------------------------------------------------------------------- cases


@dataclass(frozen=True)
class Result:
    op: str
    principal: str
    path: str
    status: int
    verdict: str  # PASSED | FAILED | DEVIATION (registered, non-leaking)
    detail: str = ""


class Recorder:
    def __init__(self) -> None:
        self.rows: list[Result] = []

    def check(self, op: openapi.Operation, principal: str, path: str, resp: Any, allowed: set[int], forbidden_text: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        status = resp.status_code
        problems: list[str] = []
        if status not in allowed and not (200 in allowed and 200 <= status < 300):
            problems.append(f"status {status} not in {sorted(allowed)}")
        if status >= 500:
            problems.append("server error on an isolation case")
        body = _text(resp).replace(path, "")
        leaked = sorted(s for s in forbidden_text if s and s in body)
        if leaked:
            problems.append(f"protected values in body: {leaked[:5]}")
        key = f"{op.method.upper()} {op.path}"
        verdict = "FAILED" if problems else "PASSED"
        known = KNOWN_DEVIATIONS.get((key, principal, status))
        if problems and known and not leaked and status < 500:
            verdict, problems = "DEVIATION", [f"{known}: {problems[0]}"]
        self.rows.append(Result(key, principal, path, status, verdict, "; ".join(problems)))

    def failed(self) -> list[Result]:
        return [r for r in self.rows if r.verdict == "FAILED"]

    def write(self, name: str, summary: dict[str, Any]) -> None:
        out = paths.REPORTS / "leak"
        out.mkdir(parents=True, exist_ok=True)
        with (out / f"p22c-idor-{name}-matrix.csv").open("w", newline="", encoding="utf-8") as fh:
            wr = csv.writer(fh)
            wr.writerow(["operation", "principal", "path", "status", "verdict", "detail"])
            for r in self.rows:
                wr.writerow([r.op, r.principal, r.path, r.status, r.verdict, r.detail])
        (out / f"p22c-idor-{name}-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture
def iw(db: Any) -> Iterator[IdorWorld]:
    with idor_world() as world:
        yield world


def _concrete_or_filler(resolver: Resolver, template: str) -> tuple[str, bool]:
    if "{" not in template:
        return template, True
    hit = resolver.resolve(template)
    return (hit, True) if hit else (PARAM_RE.sub(FILLER, template), False)


def _fmt(rows: list[Result]) -> str:
    return "\n".join(f"{r.principal:10} {r.status} {r.op}  [{r.path}] {r.detail}" for r in rows[:60])


def _run_internal(iw: IdorWorld, write: bool = True) -> tuple[Recorder, dict[str, Any]]:
    internal, _portal = in_scope_ops()
    assert len(internal) >= 300, f"in-scope implemented internal operations unexpectedly few: {len(internal)}"
    alfa = iw.w.world.companies["ALFA"]
    beta = iw.w.world.companies["BETA"]
    admin = Client(iw.tokens["admin"], alfa)
    admin_beta = Client(iw.tokens["admin"], beta)
    gets = {op.path for op in internal if op.method == "get"}
    res_alfa = Resolver(admin, gets)
    res_beta = Resolver(admin_beta, gets)
    clients = {
        "anonymous": Client(None, alfa),
        "nogrant": Client(iw.tokens["nogrant"], alfa),
        "beta@ALFA": Client(iw.tokens["beta"], alfa),
        "beta@BETA": Client(iw.tokens["beta"], beta),
        "t2admin": Client(iw.tokens["t2admin"], iw.t2_companies["GAMMA"]),
        "portal": Client(str(iw.portal["supA"].token)),
    }
    rec = Recorder()
    resolved = 0
    object_ops = 0
    list_ok = 0
    lists = [op for op in internal if op.method == "get" and "{" not in op.path]
    # admin positive control over every list first (fills the id pool of ALFA and of BETA)
    for op in lists:
        r = admin.call("get", op.path)
        list_ok += r.status_code == 200
        res_alfa.list_ids(op.path)
        res_beta.list_ids(op.path)
    alfa_only = res_alfa.pool - res_beta.pool - {str(alfa), str(beta)}
    t1_ids = (res_alfa.pool | res_beta.pool) - {FILLER}
    assert len(alfa_only) >= 20, f"seeded ALFA world too small for a meaningful suite: {len(alfa_only)} ids"

    for op in internal:
        path, real = _concrete_or_filler(res_alfa, op.path)
        if "{" in op.path:
            object_ops += 1
            resolved += real
        m = op.method
        rec.check(op, "anonymous", path, clients["anonymous"].call(m, path), REFUSED)
        rec.check(op, "nogrant", path, clients["nogrant"].call(m, path), HIDDEN, alfa_only)
        rec.check(op, "beta@ALFA", path, clients["beta@ALFA"].call(m, path), HIDDEN, alfa_only)
        rec.check(op, "portal", path, clients["portal"].call(m, path), REFUSED, alfa_only)
        if "{" in op.path:
            # object routes (reads and writes) with T1-ALFA ids: never reachable from another company / tenant.
            # With a filler id (no real object resolved) a validation answer is acceptable too: nothing exists.
            extra = set() if real else {400, 409, 422}
            rec.check(op, "beta@BETA", path, clients["beta@BETA"].call(m, path), NOT_FOUND | extra, alfa_only)
            rec.check(op, "t2admin", path, clients["t2admin"].call(m, path), NOT_FOUND | extra, t1_ids)
        elif m == "get":
            # lists / searches / exports / reports in the principal's own scope: allowed, but no foreign row
            own = {200, 400, 403, 404, 422}
            rec.check(op, "beta@BETA", path, clients["beta@BETA"].call(m, path), own, alfa_only)
            rec.check(op, "t2admin", path, clients["t2admin"].call(m, path), own, t1_ids)
            rec.check(op, "t2admin?q", path, clients["t2admin"].call(m, f"{path}?q=SYN"), own, t1_ids)
            rec.check(op, "beta@BETA?q", path, clients["beta@BETA"].call(m, f"{path}?q=SYN"), own, alfa_only)
        # collection-level writes of beta@BETA / t2admin would be allowed writes in their own scope: never executed
    summary = {
        "operations": len(internal),
        "list_operations": len(lists),
        "lists_reachable_by_admin": list_ok,
        "object_operations": object_ops,
        "object_operations_with_real_ids": resolved,
        "t1_ids": len(t1_ids),
        "alfa_only_ids": len(alfa_only),
        "cases": len(rec.rows),
        "failed": len(rec.failed()),
        "registered_deviations": sorted({r.detail.split(":")[0] for r in rec.rows if r.verdict == "DEVIATION"}),
    }
    if write:
        rec.write("internal", summary)
    return rec, summary


def test_T_X_QA_03_internal_endpoints_bola_bfla_cross_tenant_cross_company(iw: IdorWorld) -> None:
    rec, summary = _run_internal(iw)
    assert not rec.failed(), f"{len(rec.failed())} isolation findings:\n{_fmt(rec.failed())}"
    # meaningfulness: a large share of object routes was attacked with ids proven real by the positive control
    assert summary["object_operations_with_real_ids"] >= summary["object_operations"] // 3, summary
    assert summary["lists_reachable_by_admin"] >= summary["list_operations"] - 2, summary


def test_T_X_QA_03_suite_detects_a_disabled_authorization_check(iw: IdorWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation self-test: with the P04 permission decision forced to 'granted' the suite must report BFLA findings."""
    from iam.policy import engine as policy_engine

    real = policy_engine.PolicyEngine.authorize

    def granted(self: Any, ctx: Any, action: str, resource: Any = None) -> Any:
        decision = real(self, ctx, action, resource)
        if decision.reason == "deny_by_default" and self.effective(ctx).principal_kind not in ("external", "none"):
            return policy_engine.Decision(True, "granted")
        return decision

    monkeypatch.setattr(policy_engine.PolicyEngine, "authorize", granted)
    rec, _summary = _run_internal(iw, write=False)
    bfla = [r for r in rec.failed() if r.principal == "nogrant"]
    assert len(bfla) >= 50, f"mutant not detected: only {len(bfla)} BFLA findings"


def test_T_X_QA_03_portal_endpoints_party_isolation(iw: IdorWorld) -> None:
    internal, portal = in_scope_ops()
    assert len(portal) >= 40, len(portal)
    kinds = {"supplier": "supA", "customer": "cus", "employee": "emp"}
    tok = {k: str(v.token) for k, v in iw.portal.items()}
    sup_a = Client(tok["supA"])
    sup_b = Client(tok["supB"])
    gets = {op.path for op in portal if op.method == "get"}
    res_b = Resolver(sup_b, gets)
    res_a = Resolver(sup_a, gets)
    for op in portal:
        if op.method == "get" and "{" not in op.path and "/supplier/" in op.path:
            res_a.list_ids(op.path)
            res_b.list_ids(op.path)
    b_only = res_b.pool - res_a.pool
    assert b_only, "supplier B has no portal documents of its own: the BOLA cases would be vacuous"
    b_secrets = b_only | {iw.party_ids["supB"], "NOTA-INTERNA-"}
    internal_ids = Resolver(Client(iw.tokens["admin"], iw.w.world.companies["ALFA"]), set())
    for op in internal:
        if op.method == "get" and "{" not in op.path and op.path.startswith(("/api/v1/purchasing", "/api/v1/treasury")):
            internal_ids.list_ids(op.path)
    rec = Recorder()
    anon = Client(None)
    internal_tok = Client(iw.tokens["admin"], iw.w.world.companies["ALFA"])
    t2 = Client(iw.tokens["t2admin"], iw.t2_companies["GAMMA"])
    a_own = 0
    for op in portal:
        kind = next((k for k in kinds if f"/{k}/" in op.path), None)
        m = op.method
        b_path = res_b.resolve(op.path) if "{" in op.path else op.path
        target = b_path or PARAM_RE.sub(FILLER, op.path)
        rec.check(op, "anonymous", target, anon.call(m, target), REFUSED)
        rec.check(op, "internal", target, internal_tok.call(m, target), REFUSED)
        rec.check(op, "t2internal", target, t2.call(m, target), REFUSED)
        for other, principal in kinds.items():
            if kind and other != kind and principal in tok:
                rec.check(op, f"{principal}(x-kind)", target, Client(tok[principal]).call(m, target), HIDDEN, b_secrets)
        if kind == "supplier":
            if "{" in op.path:
                if b_path and set(UUID_RE.findall(b_path)) <= b_only:
                    rec.check(op, "supA->B", b_path, sup_a.call(m, b_path), NOT_FOUND, b_secrets)
                for iid in sorted(internal_ids.pool)[:5]:
                    p = PARAM_RE.sub(iid, op.path)
                    rec.check(op, "supA->int", p, sup_a.call(m, p), NOT_FOUND | ({400, 409, 422} if m != "get" else set()), b_secrets)
                own = res_a.resolve(op.path)
                if own and m == "get":
                    r = sup_a.call("get", own)
                    a_own += r.status_code == 200
                    rec.check(op, "supA(own)", own, r, {200}, b_secrets)
            elif m == "get":
                rec.check(op, "supA(list)", op.path, sup_a.call("get", op.path), {200}, b_secrets)
                rec.check(op, "supA(q)", op.path, sup_a.call("get", f"{op.path}?q=PROV-B"), {200, 400}, b_secrets)
                rec.check(op, "supA(party)", op.path, sup_a.call("get", f"{op.path}?party_id={iw.party_ids['supB']}"), {200, 400, 403}, b_secrets)
    for principal in ("cus", "emp"):
        if principal not in tok:
            continue
        c = Client(tok[principal])
        for op in portal:
            if op.method == "get" and "{" not in op.path and op.path.count("/") <= 4:  # /me, /context
                rec.check(op, f"{principal}(self)", op.path, c.call("get", op.path), {200}, b_secrets)
    summary = {
        "portal_operations": len(portal),
        "supplier_b_only_ids": len(b_only),
        "supplier_a_own_objects_reachable": a_own,
        "onboarded": sorted(tok),
        "cases": len(rec.rows),
        "failed": len(rec.failed()),
    }
    rec.write("portal", summary)
    assert not rec.failed(), f"{len(rec.failed())} portal isolation findings:\n{_fmt(rec.failed())}"
    assert a_own >= 2, summary  # positive control: A reaches its own objects through the same routes


def _probe(iw: IdorWorld, op_key: str, principal: str) -> int:
    method, template = op_key.split(" ", 1)
    alfa = iw.w.world.companies["ALFA"]
    admin = Client(iw.tokens["admin"], alfa)
    path = Resolver(admin, {template}).resolve(template) if "{" in template else template
    assert path, f"no real object for {template}"
    client = {
        "nogrant": Client(iw.tokens["nogrant"], alfa),
        "beta@BETA": Client(iw.tokens["beta"], iw.w.world.companies["BETA"]),
        "t2admin": Client(iw.tokens["t2admin"], iw.t2_companies["GAMMA"]),
    }[principal]
    return int(client.call(method.lower(), path).status_code)


def _defect_cases(defect: str) -> list[tuple[str, str]]:
    return sorted({(op, who) for (op, who, _st), d in KNOWN_DEVIATIONS.items() if d == defect})


@pytest.mark.parametrize(
    "defect",
    [
        pytest.param(d, marks=pytest.mark.xfail(strict=True, reason=f"{d} (see contracts/change-requests/P22c-*.md)"))
        for d in sorted(set(KNOWN_DEVIATIONS.values()))
    ],
)
def test_p22c_registered_authorization_deviation_is_fixed(iw: IdorWorld, defect: str) -> None:
    """Strict xfail per registered deviation: the unauthorized caller must get 401/403/404 (BFLA/BOLA order)."""
    for op_key, principal in _defect_cases(defect):
        status = _probe(iw, op_key, principal)
        assert status in {401, 403, 404, 405}, f"{defect}: {op_key} as {principal} -> {status}"
