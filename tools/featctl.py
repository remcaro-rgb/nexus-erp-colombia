#!/usr/bin/env python3
"""featctl (2.2.0-dev): validate the mandatory feature registry and compute gate feature checks.

  featctl.py validate   --root docs/spec
  featctl.py required   --root docs/spec --gate G2
  featctl.py gate-check --root docs/spec --gate G2 --results docs/gates/G2-evidence/feature-results.json
  featctl.py collect    --root docs/spec --commit SHA --out FILE  REPORT...   (JUnit XML and/or Playwright JSON)

Test naming rule (binding): every automated test case that implements a registry test carries its ID in its name or title,
e.g. pytest `test_T_ACC_DIM_01_closure_rebuild` or Playwright `[T-PORTAL-SUP-06] supplier portal login`. `collect` maps the
reports to registry IDs: an ID is PASSED only if it appears at least once and every case carrying it passed (none failed,
errored or skipped). Level `doc` tests are automated too (they assert files/records exist); nothing is marked PASSED by hand.

Results file (written by the tester of each task, consolidated by M5; never hand-edited to PASSED):
  {"schema": "erp-features/results/v1", "registry_sha256": "<sha of features/feature-registry.json>", "commit": "<sha>",
   "tests": {"T-ACC-DIM-01": {"status": "PASSED", "evidence": "docs/gates/G1-evidence/junit/ledger.xml#test_x"}, ...}}
A check is PASSED only if every one of its tests is PASSED with a non-empty evidence path. Read-only; never changes run state.
"""
import argparse, fnmatch, hashlib, json, os, re, sys

VERSION = "2.2.0-dev"
GATES = ["G0", "G1", "G2", "G3", "G4", "DEV"]
GATE_ORDER = {g: i for i, g in enumerate(GATES)}
REQUIRED_FEATURES = [
    "F-ACC-DIM", "F-ACC-BRANCH", "F-ACC-EXO", "F-ACC-REPORTS", "F-TRE-RC", "F-TRE-CE", "F-INV-ISSUE", "F-INV-PRES", "F-INV-LOC", "F-INV-BARCODE",
    "F-INV-BOM", "F-INV-XFORM", "F-INV-REQ", "F-PUR-CYCLE", "F-FORMULA-ENGINE", "F-SAL-PRICING", "F-PT-OPEN", "F-PAY-FORMULA", "F-PORTAL-CORE",
    "F-PORTAL-SUP", "F-PORTAL-CUS", "F-PORTAL-EMP", "F-X-AI-MCP", "F-X-DATAIO", "F-X-SEED", "F-X-QA", "F-X-COMPLIANCE", "F-X-RUNTIME"]
ASPECTS = ["model", "states", "api", "errors", "posting", "security", "ui", "ai", "dataio", "reports"]
LEVELS = {"cross", "unit", "integration", "property", "concurrency", "security", "e2e", "visual", "a11y", "runtime", "doc"}
EXTERNAL_ROWS = {"A1", "A1.2", "N1.3"}
TASK_RE = re.compile(r"^(P\d\d[a-d]?|G[0-4]|DEV)$")
RESULTS_SCHEMA = "erp-features/results/v1"


def sha_file(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def load(root):
    reg_p = os.path.join(root, "features", "feature-registry.json")
    return json.load(open(reg_p, encoding="utf-8")), json.load(open(os.path.join(root, "plan.json"), encoding="utf-8")), reg_p


def ancestors(T, t, seen=None):
    seen = set() if seen is None else seen
    for d in T.get(t, {}).get("requires", []):
        if d not in seen:
            seen.add(d); ancestors(T, d, seen)
    return seen


def gate_of(T, task):
    if task == "DEV":
        return "DEV"
    for g in GATES[:-1]:
        if task == g or task in ancestors(T, g):
            return g
    return None


def joint_gate(T, a, b):
    for g in GATES[:-1]:
        anc = ancestors(T, g) | {g}
        if a in anc and b in anc:
            return g
    return "DEV"


def sample(glob):
    return glob.replace("**", "x/y").replace("*", "x")


def validate(root):
    E = []
    def need(c, m):
        if not c: E.append(m)
    reg, plan, reg_p = load(root)
    T = plan["tasks"]
    need(reg.get("schema") == "erp-features/registry/v1" and reg.get("version") == VERSION, f"registry schema/version must be erp-features/registry/v1 / {VERSION}")
    F = {f["id"]: f for f in reg.get("features", [])}
    need(sorted(F) == sorted(REQUIRED_FEATURES), f"features must be exactly the mandatory set (missing {sorted(set(REQUIRED_FEATURES) - set(F))}, extra {sorted(set(F) - set(REQUIRED_FEATURES))})")
    dec = {d["id"] for d in reg.get("decisions", [])}
    need(dec == {f"D-{i:03d}" for i in range(1, len(dec) + 1)} and len(dec) >= 30, "decisions must be D-001..D-nnn contiguous (>=30)")
    tests = {}
    for t in reg.get("tests", []):
        need(t["id"] not in tests, f"duplicate test {t['id']}"); tests[t["id"]] = t
    for fid, f in F.items():
        own = f["owner"]
        need(own in T or own == "DEV", f"{fid}: owner {own} not in plan")
        need(f.get("mandatory", "P") == "P" and "[A]" not in f["title_es"] + f["title_en"], f"{fid}: every registry feature is mandatory [P]")
        need(f["gate"] == gate_of(T, own), f"{fid}: gate {f['gate']} != computed {gate_of(T, own)}")
        if not fid.startswith("F-X-"):
            for a in ASPECTS:
                need(bool(f.get(a)), f"{fid}: aspect '{a}' must be specified (state 'N/A: reason' explicitly if not applicable)")
            need(bool(f.get("audit")) or f.get("enabler"), f"{fid}: user feature must map to audit rows (only enablers may not)")
        need(len(f["tests"]) >= 2, f"{fid}: needs >=2 tests")
        for d in f.get("defaults", []):
            need(d in dec, f"{fid}: unknown default {d}")
        anc_own = ancestors(T, own) if own in T else set(T)
        stubs = {s["task"]: s for s in f.get("stubs", [])}
        for s in f.get("stubs", []):
            need(s["task"] in T and s.get("interface"), f"{fid}: stub {s} invalid")
            if s["task"] in T and own in T:
                need(s["task"] not in anc_own, f"{fid}: stub {s['task']} is an ancestor of {own} (real dependency; stub not allowed)")
                need(s["gate"] == joint_gate(T, own, s["task"]), f"{fid}: stub {s['task']} gate {s['gate']} != earliest joint gate {joint_gate(T, own, s['task'])}")
        involved = {c.split("/")[0] for c in f.get("contributors", {})} | {t["task"] for t in f["tests"]} | set(f.get("paths", {}))
        for c in sorted(involved):
            need(bool(TASK_RE.match(c)) and (c in T or c == "DEV"), f"{fid}: unknown task {c}")
            if c in T and own in T and c != own:
                ok = c in anc_own or own in ancestors(T, c) or c in stubs
                need(ok, f"{fid}: {c} is a sibling of owner {own} — declare a contract stub (frozen at G0) with its joint gate {joint_gate(T, own, c)}")
        for task, globs in f.get("paths", {}).items():
            for g in globs:
                need(task in T and any(fnmatch.fnmatch(sample(g), pg) for pg in T[task].get("paths", [])), f"{fid}: path {g} outside plan paths of {task}")
        for t in f["tests"]:
            need(t["id"] in tests and t["feature"] == fid, f"{fid}: test {t['id']} not registered")
            need(re.match(rf"^T-{re.escape(fid[2:])}-\d\d$", t["id"]) is not None, f"{t['id']}: id format")
            need(t["level"] in LEVELS and len(t["text"]) >= 15, f"{t['id']}: level/text")
            need(t["gate"] == gate_of(T, t["task"]), f"{t['id']}: gate {t['gate']} != computed")
        if f.get("kpis"):
            need(3 <= len(f["kpis"]) <= 5, f"{fid}: portal dashboards need 3-5 KPIs")
    checks = reg.get("checks", [])
    ids = [c["id"] for c in checks]
    need(len(ids) == len(set(ids)), "duplicate check ids")
    audit = [c for c in checks if c["kind"].startswith("audit-")]
    need(sum(c["kind"] == "audit-parent" for c in audit) == 25 and sum(c["kind"] == "audit-subcheck" for c in audit) == 62, "audit mapping must have exactly 25 parents and 62 subchecks")
    parents = {c["id"] for c in audit if c["kind"] == "audit-parent"}
    for c in checks:
        need(bool(c["tests"]) and all(t in tests for t in c["tests"]), f"check {c['id']}: tests missing/unknown")
        if c["tests"] and all(t in tests for t in c["tests"]):
            need(c["gate"] == max((tests[t]["gate"] for t in c["tests"]), key=GATE_ORDER.get), f"check {c['id']}: gate must be the max test gate")
        need(c["status_v2_2_0"].startswith("FULL"), f"check {c['id']}: status must be FULL in {VERSION}")
        need(bool(c["external_input"]) == (c["id"] in EXTERNAL_ROWS) or c["kind"] == "feature", f"check {c['id']}: external_input flag")
        if c["kind"] == "audit-subcheck":
            need(c["parent"] in parents, f"check {c['id']}: parent {c['parent']} unknown")
    for fid in F:
        need(fid in ids, f"feature check {fid} missing")
    for tid in tests:
        need(any(tid in c["tests"] for c in checks), f"test {tid} not part of any check")
    # cross-artefact consistency
    ui = os.path.join(root, "ui")
    try:
        mreg = json.load(open(os.path.join(ui, "module-registry.json"), encoding="utf-8"))
        mat = json.load(open(os.path.join(ui, "task-matrix.json"), encoding="utf-8"))
        need(mreg.get("version") == VERSION and mat.get("version") == VERSION, "ui registry/matrix version mismatch")
        tab_feats = {tb["feature"] for m in mreg["modules"] for s in m["submenus"] for tb in s.get("tabs", [])}
        for tb in (tb for m in mreg["modules"] for s in m["submenus"] for tb in s.get("tabs", [])):
            need(tb["feature"] in F, f"tab {tb['route']}: unknown feature {tb['feature']}")
        for p in mreg.get("portals", []):
            need(p["feature"] in F and [k["id"] for k in p["dashboard"]["kpis"]] == [k["id"] for k in F[p["feature"]]["kpis"]], f"portal {p['id']}: KPIs must equal feature registry")
            tab_feats.add(p["feature"])
        for fid in ("F-ACC-DIM", "F-ACC-BRANCH", "F-ACC-EXO", "F-ACC-REPORTS", "F-TRE-RC", "F-TRE-CE", "F-INV-ISSUE", "F-INV-PRES", "F-INV-LOC", "F-INV-BARCODE",
                    "F-INV-BOM", "F-INV-XFORM", "F-INV-REQ", "F-PUR-CYCLE", "F-SAL-PRICING", "F-PT-OPEN", "F-PAY-FORMULA", "F-PORTAL-CORE", "F-PORTAL-SUP", "F-PORTAL-CUS", "F-PORTAL-EMP"):
            need(fid in tab_feats, f"{fid}: needs a route (tab or portal) in ui/module-registry.json")
        for t in tests.values():
            if t["task"] in mat["tasks"]:
                need(any(a.startswith(f"[FEAT] {t['id']} ") for a in mat["tasks"][t["task"]]["acceptance"]), f"task-matrix row {t['task']} must carry {t['id']}")
        for f in F.values():
            if f["owner"] in mat["tasks"]:
                need(any(d.startswith(f"[FEAT] Owner of {f['id']} ") for d in mat["tasks"][f["owner"]]["deliverables"]), f"task-matrix row {f['owner']} must own {f['id']}")
    except (OSError, KeyError, ValueError) as e:
        E.append(f"ui artefacts unreadable: {e}")
    for name, must in (("FEATURES-ADDENDUM.md", ["BINDING", "CCR-FEAT-001", "mandatory [P]", "features/feature-registry.json"] + REQUIRED_FEATURES),
                       ("DECISIONS-DEFAULTS.md", ["CCR-FEAT-001", "D-001", "never ask", "Never ask"][:3]),
                       ("AUTONOMOUS-EXECUTION.md", ["BINDING", "STATE.json", "operator", "never ask"]), ("CLOUD-ADDENDUM.md", ["BINDING", "cloudctl", "featctl gate-check"])):
        p = os.path.join(root, name)
        need(os.path.isfile(p), f"{name} missing")
        if os.path.isfile(p):
            txt = open(p, encoding="utf-8").read()
            for s in must:
                need(s.lower() in txt.lower(), f"{name} must mention {s!r}")
    return E


def required_tests(reg, gate):
    return sorted(t["id"] for t in reg["tests"] if GATE_ORDER[t["gate"]] <= GATE_ORDER[gate])


def required(reg, gate):
    """Checks touched by the gate, each restricted to its tests due by that gate (complete checks: gate <= this gate)."""
    due = set(required_tests(reg, gate))
    out = []
    for c in reg["checks"]:
        ts = [t for t in c["tests"] if t in due]
        if ts:
            out.append({**c, "tests": ts, "complete": GATE_ORDER[c["gate"]] <= GATE_ORDER[gate]})
    return out


def gate_check(root, gate, results_path):
    reg, _plan, reg_p = load(root)
    res = json.load(open(results_path, encoding="utf-8"))
    errs = []
    if res.get("schema") != RESULTS_SCHEMA:
        errs.append(f"results schema must be {RESULTS_SCHEMA}")
    if res.get("registry_sha256") != sha_file(reg_p):
        errs.append("results registry_sha256 does not match features/feature-registry.json")
    tr = res.get("tests", {})
    out = []
    for c in required(reg, gate):
        bad = [t for t in c["tests"] if not (isinstance(tr.get(t), dict) and tr[t].get("status") == "PASSED" and tr[t].get("evidence"))]
        out.append({"id": c["id"], "status": "PASSED" if not bad else "FAILED", "complete": c["complete"], "missing_or_failed": bad})
    failed = [c["id"] for c in out if c["status"] != "PASSED"]
    digest = hashlib.sha256(json.dumps({"gate": gate, "registry_sha256": sha_file(reg_p), "checks": out}, sort_keys=True).encode()).hexdigest()
    return {"gate": gate, "registry_sha256": sha_file(reg_p), "required": len(out), "failed": failed, "checks": out, "digest": digest, "errors": errs}


ID_IN_NAME = re.compile(r"T[-_]([A-Z0-9]+(?:[-_][A-Z0-9]+)*?)[-_](\d\d)(?![0-9])")


def _norm_ids(name, known):
    out, up = set(), name.upper()
    for i in range(len(up) - 1):
        if up[i] == "T" and up[i + 1] in "-_" and (i == 0 or not up[i - 1].isalnum() or up[i - 1] == "_" or up[i - 1] == "["):
            m = ID_IN_NAME.match(up, i)
            if m:
                cand = "T-" + m.group(1).replace("_", "-") + "-" + m.group(2)
                if cand in known:
                    out.add(cand)
    return out


def collect(root, commit, reports):
    import xml.etree.ElementTree as ET
    reg, _plan, reg_p = load(root)
    known = {t["id"] for t in reg["tests"]}
    seen = {}
    def add(ids, ok, ev):
        for i in ids:
            cur = seen.get(i)
            if cur is None:
                seen[i] = {"status": "PASSED" if ok else "FAILED", "evidence": ev}
            elif not ok:
                seen[i] = {"status": "FAILED", "evidence": ev}
    for rp in reports:
        if rp.endswith(".xml"):
            for tc in ET.parse(rp).getroot().iter("testcase"):
                name = (tc.get("classname") or "") + "." + (tc.get("name") or "")
                bad = any(tc.find(x) is not None for x in ("failure", "error", "skipped"))
                add(_norm_ids(name, known), not bad, f"{rp}#{tc.get('name')}")
        else:
            data = json.load(open(rp, encoding="utf-8"))
            def walk(suite, prefix=""):
                for sp in suite.get("specs", []):
                    title = prefix + " " + sp.get("title", "")
                    res = [r_.get("status") for t in sp.get("tests", []) for r_ in t.get("results", [])]
                    ok = bool(res) and all(x == "passed" for x in res[-1:]) and sp.get("ok", True)
                    add(_norm_ids(title, known), ok, f"{rp}#{sp.get('title')}")
                for ch in suite.get("suites", []):
                    walk(ch, prefix + " " + ch.get("title", ""))
            for su in data.get("suites", []):
                walk(su, su.get("title", ""))
    return {"schema": RESULTS_SCHEMA, "registry_sha256": sha_file(reg_p), "commit": commit, "tests": dict(sorted(seen.items()))}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="featctl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("validate"); s.add_argument("--root", required=True)
    s = sub.add_parser("required"); s.add_argument("--root", required=True); s.add_argument("--gate", required=True, choices=GATES)
    s = sub.add_parser("gate-check"); s.add_argument("--root", required=True); s.add_argument("--gate", required=True, choices=GATES); s.add_argument("--results", required=True)
    s = sub.add_parser("collect"); s.add_argument("--root", required=True); s.add_argument("--commit", required=True); s.add_argument("--out", required=True); s.add_argument("reports", nargs="*")
    a = ap.parse_args(argv)
    if a.cmd == "collect":
        need_sha = re.match(r"^[0-9a-f]{40}$", a.commit)
        if not need_sha:
            print("FEAT-INVALID: --commit must be a 40-hex SHA", file=sys.stderr); return 1
        res = collect(a.root, a.commit, a.reports)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=1); fh.write("\n")
        reg, _, _ = load(a.root)
        missing = sorted({t["id"] for t in reg["tests"]} - set(res["tests"]))
        print(json.dumps({"out": a.out, "found": len(res["tests"]), "failed": sorted(k for k, v in res["tests"].items() if v["status"] != "PASSED"),
                          "not_found": len(missing)}))
        return 0
    if a.cmd == "validate":
        errs = validate(a.root)
        for e in errs: print("FEAT-INVALID:", e, file=sys.stderr)
        if errs: return 1
        reg, _, p = load(a.root)
        print(f"FEAT-OK features={len(reg['features'])} tests={len(reg['tests'])} checks={len(reg['checks'])} registry_sha256={sha_file(p)}")
        return 0
    reg, _, _ = load(a.root)
    if a.cmd == "required":
        for c in required(reg, a.gate): print(c["id"], c["gate"], "complete" if c["complete"] else "partial", " ".join(c["tests"]))
        return 0
    r = gate_check(a.root, a.gate, a.results)
    print(json.dumps({k: r[k] for k in ("gate", "registry_sha256", "required", "failed", "digest", "errors")}, indent=1))
    return 0 if not r["failed"] and not r["errors"] else 2


if __name__ == "__main__":
    sys.exit(main())
