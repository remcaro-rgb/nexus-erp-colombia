#!/usr/bin/env python3
"""uictl (2.2.0-dev): validate-only checks for the binding UI overlay. Does not touch run state or the protocol.

  uictl.py validate --root docs/spec
Checks: reference asset hashes; tokens.json mandatory values; module-registry.json required modules, KPI fields (3-5 per
module), unique ids/routes, owners vs plan.json paths, provider dependency rule (acyclic, P17 never blocks W2);
task-matrix.json coverage of every plan task; UI-CONTRACT.md references; contrast of text tokens (WCAG AA)."""
import argparse, fnmatch, hashlib, json, os, re, sys

VERSION = "2.2.0-dev"
REQUIRED_MODULES = ["inicio", "contabilidad", "tesoreria", "activos", "ventas", "facturacion", "pos", "inventario", "compras",
                    "talento", "reportes", "aprobaciones", "datos", "puesta-en-marcha", "administracion"]
KPI_FIELDS = ["id", "label", "formula", "unit", "semantics", "comparison", "zero_denominator", "permission", "provider_task", "source", "drill"]
UNITS = {"COP", "count", "percent", "ratio", "days", "hours"}
SEMANTICS = {"flow", "snapshot"}
COMPARISONS = {"previous_period", "none"}
LAYOUT = {"header_h": 64, "module_bar_h": 48, "sidebar_w": 256, "sidebar_collapsed_w": 80, "ai_panel_w": 320}
REF_HASH = {"reference.html": "17cdfbf8318451e81145e19d21080c360375486678147b40d1cb02dd4c3de549",
            "reference.png": "e1133e091559e946e406b072e884a8b2d6fa73f4a8c31714f69c1119d3caca36"}
COMPOSITE = {"inicio"}


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def ancestors(plan, tid):
    seen, st = set(), list(plan["tasks"][tid]["requires"])
    while st:
        x = st.pop()
        if x not in seen:
            seen.add(x); st.extend(plan["tasks"][x]["requires"])
    return seen


def _lum(hexc):
    c = [int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def png_size(p):
    d = open(p, "rb").read(24)
    assert d[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(d[16:20], "big"), int.from_bytes(d[20:24], "big")


def validate(root):
    E = []
    def need(c, m):
        if not c: E.append(m)
    ref = os.path.join(root, "ui-reference")
    for f, hx in REF_HASH.items():
        p = os.path.join(ref, f)
        need(os.path.isfile(p) and sha(p) == hx, f"ui-reference/{f} missing or hash differs")
    if os.path.isfile(os.path.join(ref, "reference.png")):
        need(png_size(os.path.join(ref, "reference.png")) == (1892, 856), "reference.png is not 1892x856")
    plan = json.load(open(os.path.join(root, "plan.json")))
    T = plan["tasks"]
    tok = json.load(open(os.path.join(root, "ui", "tokens.json")))
    reg = json.load(open(os.path.join(root, "ui", "module-registry.json")))
    mat = json.load(open(os.path.join(root, "ui", "task-matrix.json")))
    for o, n in ((tok, "tokens"), (reg, "module-registry"), (mat, "task-matrix")):
        need(o.get("version") == VERSION, f"{n}.json version != {VERSION}")
    # tokens
    for k, v in LAYOUT.items():
        need(tok["layout"].get(k) == v, f"tokens.layout.{k} must be {v}")
    need(tok["font"]["family"] == "Inter" and "googleapis" not in json.dumps(tok["font"]["delivery"]).replace("never fonts.googleapis.com", ""), "font must be Inter, local")
    need(tok["color"]["light"]["brand.primary"].lower() == "#4f46e5", "light brand.primary must be indigo-600 #4f46e5")
    need(tok["layout"]["card"]["radius"] == 16 and tok["layout"]["control_radius"] == 12, "radii must be 16/12")
    need(tok["layout"]["gap"] == {"kpi_grid": 16, "section": 24}, "gaps must be 16/24")
    need(tok["chart"]["revenue"]["stroke"].lower() == "#4f46e5" and tok["chart"]["expenses"]["dash"] == [4, 4], "chart series styles")
    need(tok["font"]["min_text_px"] >= 12, "min text 12px")
    need(tok["source"]["png"]["sha256"] == REF_HASH["reference.png"] and tok["source"]["html"]["sha256"] == REF_HASH["reference.html"], "tokens source hashes")
    for theme in ("light", "dark"):
        c = tok["color"][theme]
        for fg in ("text.primary", "text.body", "text.secondary", "text.muted", "brand.primary", "positive.text", "warning.text", "danger.text"):
            if c[fg].startswith("#") and c["surface"].startswith("#"):
                need(contrast(c[fg], c["surface"]) >= 4.5, f"{theme} {fg} on surface < 4.5:1")
            if c[fg].startswith("#") and c["app.bg"].startswith("#"):
                need(contrast(c[fg], c["app.bg"]) >= 4.5, f"{theme} {fg} on app.bg < 4.5:1")
    # registry
    ids = [m["id"] for m in reg["modules"]]
    need(ids == reg["required_modules"], "required_modules must list modules in order")
    need(set(REQUIRED_MODULES) <= set(ids), f"missing modules: {sorted(set(REQUIRED_MODULES) - set(ids))}")
    need(len(set(ids)) == len(ids), "duplicate module ids")
    routes, kpi_ids = set(), set()
    for m in reg["modules"]:
        mid, own = m["id"], m["owner_task"]
        need(own in T and T[own]["owner"] in ("M1", "M2", "M3", "M4"), f"{mid}: owner {own} must be a worker task")
        need(m["group"] in reg["groups"], f"{mid}: unknown group")
        need(any(fnmatch.fnmatch(m["descriptor_file"], g) for g in T.get(own, {}).get("paths", [])), f"{mid}: descriptor {m['descriptor_file']} outside {own} paths")
        need(m["route"] == f"/m/{mid}" and m["dashboard"]["route"] == m["route"], f"{mid}: route")
        need(3 <= len(m["submenus"]) <= 6 and m["submenus"][0]["route"] == m["route"], f"{mid}: 3-6 submenus, first = dashboard")
        for s in m["submenus"]:
            need(s["route"] not in routes, f"duplicate route {s['route']}"); routes.add(s["route"])
            need(s["route"].startswith(m["route"]), f"{mid}: submenu route {s['route']} outside module")
            if s.get("owner_task"):
                need(s["owner_task"] in T, f"{mid}: submenu owner {s['owner_task']} unknown")
            tabs = s.get("tabs", [])
            need(len(tabs) <= 6, f"{s['route']}: at most 6 tabs")
            for tb in tabs:
                need(tb["route"] not in routes, f"duplicate route {tb['route']}"); routes.add(tb["route"])
                need(tb["route"].startswith(s["route"] + "/"), f"{s['route']}: tab route {tb['route']} outside submenu")
                need(bool(tb.get("permission")) and str(tb.get("feature", "")).startswith("F-"), f"{tb['route']}: tab needs permission and feature")
                if tb.get("owner_task"):
                    need(tb["owner_task"] in T, f"{tb['route']}: tab owner unknown")
        ks = m["dashboard"]["kpis"]
        need(3 <= len(ks) <= 5, f"{mid}: needs 3-5 KPIs (has {len(ks)})")
        need(len(m["dashboard"]["panels"]) >= 1, f"{mid}: needs >=1 panel")
        need(len(m["dashboard"]["empty_state"]) > 20 and "placeholder" not in m["dashboard"]["empty_state"].lower(), f"{mid}: honest empty state required")
        need(bool(m.get("quick_action", {}).get("permission")), f"{mid}: quick action with permission")
        allowed = {own} | ancestors(plan, own)
        for k in ks:
            for f in KPI_FIELDS:
                need(bool(k.get(f)), f"{mid}.{k.get('id')}: missing {f}")
            need(k["id"] not in kpi_ids, f"duplicate kpi id {k['id']}"); kpi_ids.add(k["id"])
            need(k["unit"] in UNITS and k["semantics"] in SEMANTICS and k["comparison"] in COMPARISONS, f"{mid}.{k['id']}: unit/semantics/comparison")
            if k["unit"] in ("ratio", "percent") or "÷" in k["formula"]:
                need(k["zero_denominator"] != "not_applicable", f"{mid}.{k['id']}: ratio needs zero_denominator rule")
            need(k["drill"].startswith("/m/"), f"{mid}.{k['id']}: drill must be an app route")
            need(not re.search(r"\b(NPS|satisfacci[oó]n|satisfaction)\b", k["label"] + " " + k["formula"], re.I), f"{mid}.{k['id']}: no invented survey metrics")
            need(k["provider_task"] in T, f"{mid}.{k['id']}: unknown provider")
            if mid not in COMPOSITE:
                need(k["provider_task"] in allowed, f"{mid}.{k['id']}: provider {k['provider_task']} is not {own} or its ancestor (dependency cycle risk)")
            if mid != "reportes":
                need(k["provider_task"] != "P17", f"{mid}.{k['id']}: P17 must not be a required provider (cannot block W2)")
        for p in m["dashboard"]["panels"]:
            need(p.get("provider_task") in T and p.get("permission"), f"{mid}.{p.get('id')}: panel provider/permission")
            if mid not in COMPOSITE:
                need(p["provider_task"] in allowed, f"{mid}.{p['id']}: panel provider not ancestor")
    talento = next((m for m in reg["modules"] if m["id"] == "talento"), None)
    need(talento is not None and talento["permission"] == "hr.access" and "required" in talento["nav_visibility"], "talento must be nav-gated")
    for m in reg["modules"]:
        for k in m["dashboard"]["kpis"]:
            if "payroll" in k["id"] or "nomina" in k["id"]:
                need(k.get("sensitive_fields"), f"{k['id']}: payroll KPI must declare sensitive fields")
    # portals (FEATURES-ADDENDUM: supplier, customer, employee — all mandatory)
    ports = reg.get("portals", [])
    need(sorted(p.get("kind") for p in ports) == ["customer", "employee", "supplier"], "portals must be exactly supplier, customer, employee")
    for p in ports:
        need(p["route"] == f"/portal/{p['id']}" and p["route"] not in routes, f"portal {p['id']}: route"); routes.add(p["route"])
        need(p["owner_task"] in T and T[p["owner_task"]]["owner"] in ("M1", "M2", "M3", "M4"), f"portal {p['id']}: owner must be a worker task")
        need(len(p.get("menus", [])) >= 3 and p["menus"][0]["route"] == p["route"], f"portal {p['id']}: >=3 menus, first = dashboard")
        pk = p["dashboard"]["kpis"]
        need(3 <= len(pk) <= 5, f"portal {p['id']}: needs 3-5 KPIs (has {len(pk)})")
        for k in pk:
            for f in KPI_FIELDS:
                need(bool(k.get(f)), f"portal {p['id']}.{k.get('id')}: missing {f}")
            need(k["id"] not in kpi_ids, f"duplicate kpi id {k['id']}"); kpi_ids.add(k["id"])
            need(k["unit"] in UNITS and k["semantics"] in SEMANTICS and k["comparison"] in COMPARISONS, f"portal {p['id']}.{k['id']}: unit/semantics/comparison")
            need(k["drill"].startswith(p["route"]), f"portal {p['id']}.{k['id']}: drill must stay inside the portal")
            need(k["provider_task"] in T, f"portal {p['id']}.{k['id']}: unknown provider")
    for key in ("context", "modules", "kpis", "search", "notifications", "ai", "export"):
        need(key in reg["api"], f"api.{key} missing")
    # task matrix
    for tid in T:
        r = mat["tasks"].get(tid)
        need(r is not None and r.get("deliverables") and r.get("acceptance"), f"task-matrix missing row for {tid}")
    need(set(mat["tasks"]) <= set(T), f"task-matrix has unknown tasks {sorted(set(mat['tasks']) - set(T))}")
    for m in reg["modules"]:
        need(m["owner_task"] in mat["tasks"] and m["id"] in mat["tasks"][m["owner_task"]]["ui_role"] or m["owner_task"] == "P08", f"{m['id']}: matrix row of {m['owner_task']} must name the module")
    # accounting dimension masters (DIMENSIONS-ADDENDUM.md, CCR-DIM-001)
    E.extend(validate_dimensions(root, plan, reg, mat))
    # contract text
    uc = open(os.path.join(root, "UI-CONTRACT.md"), encoding="utf-8").read()
    for s in ("BINDING", REF_HASH["reference.html"], REF_HASH["reference.png"], "1892×856", "CCR-UI-001", "ui/tokens.json", "ui/module-registry.json", "ui/task-matrix.json",
              "never overrides security"):
        need(s in uc, f"UI-CONTRACT.md must mention {s!r}")
    return E


DIM_KEYS = {"cost_center": "cost_center_id", "business_unit": "business_unit_id", "aux_abierto": "aux_abierto_id", "item_contable": "item_dimension_id"}
DIM_FORBIDDEN_SLOTS = {"item_id", "item", "account_id", "cost_center", "business_unit"}
DIM_TASK_MACHINES = {"P05": "M1", "P08": "M4", "P04": "M2", "P17": "M2"}


def validate_dimensions(root, plan, reg, mat):
    E = []
    def need(c, m):
        if not c: E.append(m)
    T = plan["tasks"]
    p = os.path.join(root, "ui", "dimensions.json")
    if not os.path.isfile(p):
        return ["ui/dimensions.json missing"]
    d = json.load(open(p, encoding="utf-8"))
    need(d.get("version") == VERSION, f"dimensions.json version != {VERSION}")
    need(d.get("ccr") == "CCR-DIM-001", "dimensions.json ccr must be CCR-DIM-001")
    keys = [x.get("key") for x in d.get("dimensions", [])]
    need(sorted(keys) == sorted(DIM_KEYS), f"dimensions must be exactly {sorted(DIM_KEYS)} (got {keys})")
    slots = [x.get("line_slot") for x in d.get("dimensions", [])]
    need(len(set(slots)) == len(slots), "dimension line slots must be distinct")
    for x in d.get("dimensions", []):
        k = x.get("key")
        need(x.get("line_slot") == DIM_KEYS.get(k), f"{k}: line_slot must be {DIM_KEYS.get(k)}")
        need(x.get("line_slot") not in DIM_FORBIDDEN_SLOTS, f"{k}: slot collides with product/account slots")
        need(str(x.get("route", "")).startswith("/m/contabilidad/dimensiones/"), f"{k}: route must be under /m/contabilidad/dimensiones/")
        need(x.get("permission_read", "").startswith("ledger.dimension.") and x.get("permission_write", "").startswith("ledger.dimension."), f"{k}: ledger.dimension.* permissions")
        need(bool(x.get("label_es")) and bool(x.get("meaning_note")), f"{k}: label_es and meaning_note required")
    f = {c["name"]: c for c in d.get("common_fields", [])}
    for n in ("company_id", "code", "description", "node_type", "parent_id", "valid_from", "status", "version"):
        need(n in f, f"dimensions common field {n} missing")
    for n in ("company_id", "code", "description", "node_type"):
        need(f.get(n, {}).get("required") is True, f"dimensions field {n} must be required")
    need(f.get("node_type", {}).get("values") == ["AGRUPADOR", "DETALLE"], "node_type must be [AGRUPADOR, DETALLE]")
    need("per (company_id" in f.get("code", {}).get("unique", ""), "code uniqueness must be per company")
    rules = " ".join(r.get("rule", "") for r in d.get("rules", []))
    ids = [r.get("id") for r in d.get("rules", [])]
    need(ids == [f"DIM-R{i:02d}" for i in range(1, 13)], "rules must be DIM-R01..DIM-R12")
    for s in ("Only DETALLE", "no cycles", "depth", "composite foreign keys", "cannot be deleted", "closure", "no double counting", "same transaction", "never change amounts", "round-trip"):
        need(s in rules, f"dimension rules must state {s!r}")
    for t, m in DIM_TASK_MACHINES.items():
        need(t in d.get("tasks", {}) and T.get(t, {}).get("owner") == m == d["tasks"][t].get("machine"), f"dimension task {t} must exist and be owned by {m}")
    for t, x in d.get("tasks", {}).items():
        need(t in T, f"dimension task {t} not in plan")
        if t in T:
            need(x.get("machine") == T[t]["owner"], f"dimension task {t} machine {x.get('machine')} != plan owner {T[t]['owner']}")
        need(x.get("deliver") and x.get("accept"), f"dimension task {t} needs deliverables and acceptance")
        need(t in mat["tasks"] and "dimensions" in mat["tasks"][t], f"task-matrix row {t} must carry dimensions")
    for g in ("G0", "G1", "G2", "G3", "G4"):
        need(g in d.get("gates", {}) and g in T, f"dimension gate {g} missing")
    p05 = T.get("P05", {}).get("paths", [])
    need(any(fnmatch.fnmatch("frontend/src/features/ledger/dimensions/index.tsx", g) for g in p05) and any(fnmatch.fnmatch("backend/ledger/dimensions/models.py", g) for g in p05),
         "dimension code paths must be inside P05 paths")
    con = next((m for m in reg["modules"] if m["id"] == "contabilidad"), None)
    sm = next((s for s in (con or {}).get("submenus", []) if s.get("route") == "/m/contabilidad/dimensiones"), None)
    need(sm is not None and sm.get("owner_task") == "P05" and sm.get("label") == "Dimensiones contables", "contabilidad submenu 'Dimensiones contables' owned by P05 required")
    need(d.get("scope", {}).get("added") == ["cost_center", "business_unit", "aux_abierto", "item_contable"], "scope.added must list the four dimensions (CCR-FEAT-001)")
    tabs = {t_["route"] for t_ in (sm or {}).get("tabs", [])}
    need(all(x.get("route") in tabs for x in d.get("dimensions", [])), "every dimension route must be a tab of 'Dimensiones contables'")
    ad = os.path.join(root, "DIMENSIONS-ADDENDUM.md")
    need(os.path.isfile(ad), "DIMENSIONS-ADDENDUM.md missing")
    if os.path.isfile(ad):
        a = open(ad, encoding="utf-8").read()
        for s in ("BINDING", "CCR-DIM-001", "Auxiliar abierto", "Ítem contable", "aux_abierto_id", "item_dimension_id", "AGRUPADOR", "DETALLE", "table view", "tree view",
                  "Centro de costo", "Unidad de negocio", "CCR-FEAT-001", "ui/dimensions.json", "T-DIM-14"):
            need(s in a, f"DIMENSIONS-ADDENDUM.md must mention {s!r}")
    return E


def main(argv=None):
    ap = argparse.ArgumentParser(prog="uictl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("validate"); s.add_argument("--root", required=True)
    a = ap.parse_args(argv)
    errs = validate(a.root)
    if errs:
        for e in errs: print("UI-INVALID:", e, file=sys.stderr)
        return 1
    print("UI-OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
