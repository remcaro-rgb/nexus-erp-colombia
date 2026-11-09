#!/usr/bin/env python3
"""Contract diff: frozen OpenAPI vs docs/spec/features/feature-registry.json and FEATURES-ADDENDUM.md (T-X-QA-06).

Checks, per registry feature:
  * the addendum's "API" and "Errors (stable codes)" bullets are the same lists as the registry (the twin documents agree);
  * every endpoint token of every API line resolves to operations of the bundled contract that are tagged `x-features: [<feature>]`:
      - absolute tokens `/api/...` must match a path exactly (`{a|b}` alternations expanded; `{x}`, `<x>` and `*` are wildcards for one segment);
      - relative tokens (`…/x`, `/x`, `:verb`) must match the end of a feature-tagged path;
      - `(+`:a`, `/b`)` groups attach to the token before the group (`P:a`, `P/{id}:a`, `P/b`, `P/{id}/b`);
      - methods written before a token (`GET/POST`, `CRUD` = GET+POST on the collection and GET+PATCH on the item) must exist on one matched path;
      - query names (`?a=&b=`, `?format=…`) must be parameters of a matched GET operation;
  * lines without endpoint tokens need an entry in contracts/src/registry-coverage.yaml (checked as declared there);
  * every stable error code is registered in contracts/errors.yaml, is in the OpenAPI `ErrorCode` enum and appears in `x-error-codes` of an operation
    tagged with the feature (`formula_*` = at least one registered `formula_` code);
  * every event type named in the registry or the addendum exists as a webhook and as contracts/events/<module>/<type>.json; every feature with
    state transitions (registry `states`) publishes at least one event tagged with it.

CLI: python3 contracts/tools/registry_diff.py [--root .]  → prints problems, exit 1 if any.
"""
from __future__ import annotations

import json
import os
import re
import sys

import yaml

METHODS = ("get", "post", "put", "patch", "delete")
EVENT_RE = re.compile(r"\b([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.v[0-9]+)\b")
CODE_RE = re.compile(r"^([a-z][a-z0-9_]*\*?)")
WILD = r"[^/:]+"
PARAM = r"\{[^}/]+\}"


def repo_root(start=None):
    p = os.path.abspath(start or os.path.join(os.path.dirname(__file__), "..", ".."))
    return p


_CACHE = {}


def load_bundle(path):
    """The bundled contract (JSON twin of openapi.yaml, written by build_contracts.py). Returns a private deep copy."""
    key = (path, os.path.getmtime(path))
    if key not in _CACHE:
        with open(path, encoding="utf-8") as fh:
            _CACHE[key] = fh.read()
    return json.loads(_CACHE[key])


class Contract:
    def __init__(self, root):
        self.root = root
        c = os.path.join(root, "contracts")
        self.doc = load_bundle(os.path.join(c, "openapi", "openapi.json"))
        with open(os.path.join(c, "errors.yaml"), encoding="utf-8") as fh:
            self.errors = {e["code"]: e for e in yaml.safe_load(fh)["codes"]}
        self.params = self.doc["components"]["parameters"]
        self.ops = []  # (method, path, op)
        for path, item in self.doc["paths"].items():
            for m in METHODS:
                if m in item:
                    self.ops.append((m, path, item[m]))
        self.webhooks = self.doc.get("webhooks", {})

    def op_params(self, op):
        out = set()
        for p in op.get("parameters", []):
            if "$ref" in p:
                p = self.params[p["$ref"].rsplit("/", 1)[1]]
            out.add(p["name"])
        return out

    def feature_ops(self, fid):
        return [(m, p, o) for (m, p, o) in self.ops if fid in (o.get("x-features") or [])]


# ------------------------------------------------------------------------------------------------ token parsing
def expand_alternations(tok):
    m = re.search(r"\{([^{}]*\|[^{}]*)\}", tok)
    if not m:
        return [tok]
    out = []
    for alt in m.group(1).split("|"):
        out += expand_alternations(tok[: m.start()] + alt + tok[m.end():])
    return out


def token_regex(tok):
    """Regex body for a path token (no anchors): `{x}` → a path parameter segment; `<x>` and `*` → any one segment."""
    out, i = "", 0
    while i < len(tok):
        ch = tok[i]
        if ch in "{<":
            j = tok.index("}" if ch == "{" else ">", i)
            out += PARAM if ch == "{" else WILD
            i = j + 1
        elif ch == "*":
            out += WILD
            i += 1
        else:
            out += re.escape(ch)
            i += 1
    return out


def split_query(tok):
    if "?" in tok:
        path, q = tok.split("?", 1)
        names = [kv.split("=", 1)[0] for kv in q.split("&") if kv.split("=", 1)[0]]
        return path, names
    return tok, []


def is_path_token(t):
    return t.startswith(("/", "…", ":", "?"))


def parse_line(line):
    """Yield dicts {token, methods, group_anchor_index, index} for every endpoint token of an API line."""
    groups = [(m.start(), m.end()) for m in re.finditer(r"\(\+[^)]*\)", line)]
    toks = []
    for m in re.finditer(r"`([^`]+)`", line):
        t = m.group(1).strip()
        if not is_path_token(t):
            continue
        before = line[: m.start()]
        mm = re.search(r"((?:GET|POST|PUT|PATCH|DELETE|CRUD)(?:/(?:GET|POST|PUT|PATCH|DELETE|CRUD))*)\s*$", before)
        methods = mm.group(1).split("/") if mm else []
        in_group = any(a <= m.start() < b for a, b in groups)
        toks.append({"token": t, "methods": methods, "in_group": in_group, "pos": m.start()})
    anchor = None
    for i, t in enumerate(toks):
        if t["in_group"]:
            t["anchor"] = anchor
        else:
            t["anchor"] = None
            if not t["token"].startswith("?"):
                anchor = i
    return toks


def strip_action(p):
    return re.sub(r":[a-z0-9-]+$", "", p)


def ancestors(p):
    """(prefix, next_segment) from p itself up to the module base (/api/v1/<m> or /api/portal/v1/<kind>)."""
    segs = p.split("/")
    floor = 5 if p.startswith("/api/portal/") else 4
    for k in range(len(segs), floor - 1, -1):
        yield "/".join(segs[:k]), (segs[k] if k < len(segs) else None)


def subst_first_wild(x, s):
    m = re.search(r"\{[^}]*\}|<[^>]*>|\*", x)
    return x if not m else x[: m.start()] + s + x[m.end():]


def literal_then(prefix, rest):
    return "^" + re.escape(prefix) + token_regex(rest) + "$"


def match_paths(rx, paths):
    r = re.compile(rx)
    return sorted(p for p in paths if r.match(p))


def resolve_token(t, raw, anchor_paths, paths):
    """Return (matched paths, problem or None). raw = token without query and without leading ellipsis."""
    ellipsis = t["token"].startswith("…")
    if t.get("in_group") and anchor_paths:
        out, miss = set(), []
        for b in anchor_paths:
            b0 = strip_action(b)
            got = match_paths(literal_then(b0, raw), paths) + match_paths(literal_then(b0, "/" + "{x}" + raw), paths)
            if not got:
                miss.append(b0)
            out |= set(got)
        return sorted(out), (f"no match under {miss[:3]}" if miss else None)
    if raw.startswith("/api/"):
        out, miss = set(), []
        for alt in expand_alternations(raw):
            got = match_paths("^" + token_regex(alt) + "$", paths)
            if not got:
                miss.append(alt)
            out |= set(got)
        return sorted(out), (f"no path {miss[:3]}" if miss else None)
    if (ellipsis or raw.startswith(":")) and anchor_paths:
        out, miss = set(), []
        for b in anchor_paths:
            b0 = strip_action(b)
            got = []
            for a, s in ancestors(b0):
                cands = []
                if a == b0:
                    cands.append(literal_then(a, raw))
                    cands.append(literal_then(a, "/{x}" + raw))
                elif s is not None:
                    cands.append(literal_then(a, subst_first_wild(raw, s)))
                for c in cands:
                    got += match_paths(c, paths)
                if got:
                    break
            if not got:
                miss.append(b0)
            out |= set(got)
        return sorted(out), (f"no match relative to {miss[:3]}" if miss else None)
    # relative resource name (`/putaway-rules`, `/rfqs/{id}/quotes`) or no anchor: suffix within the feature
    rx = "^.*" + token_regex(raw if raw.startswith((":", "/")) else "/" + raw) + "$"
    got = match_paths(rx, paths)
    return got, (None if got else "no feature path ends with it")


def check_api_line(contract, fid, line, problems, state):
    toks = parse_line(line)
    fops = contract.feature_ops(fid)
    paths = sorted({p for (_m, p, _o) in fops})
    by_path = {}
    for (m, p, _o) in fops:
        by_path.setdefault(p, set()).add(m)
    last_non_group = state.get("anchor", [])
    last_paths = []
    for t in toks:
        tok = t["token"]
        if tok.startswith("?"):
            names = [kv.split("=", 1)[0] for kv in tok[1:].split("&") if kv]
            gets = [o for (m, p, o) in fops if m == "get" and p in last_paths]
            for n in names:
                if not any(n in contract.op_params(o) for o in gets):
                    problems.append(f"{fid}: query parameter `{n}` of `{tok}` not on GET {last_paths[:3]}")
            continue
        raw, qnames = split_query(tok)
        raw = raw.lstrip("…")
        anchor = last_non_group
        matched, why = resolve_token(t, raw, anchor, paths)
        if why:
            problems.append(f"{fid}: `{tok}` — {why} (operations tagged {fid}) — line: {line[:110]}")
        if not matched:
            continue
        methods = [m.lower() for m in t["methods"]]
        want = {m for m in methods if m != "crud"}
        if not want and "crud" not in methods:
            verb = re.search(r":([a-z0-9-]+)$", raw)
            if verb and verb.group(1) != "export":
                want.add("post")
        if want and not any(want <= by_path.get(p, set()) for p in matched):
            problems.append(f"{fid}: `{tok}` needs {sorted(want)} on one path; found " + ", ".join(f"{p}:{sorted(by_path[p])}" for p in matched[:4]))
        if "crud" in methods:
            ok = False
            for p in matched:
                if {"get", "post"} <= by_path.get(p, set()):
                    irx = re.compile("^" + re.escape(p) + r"/\{[^}]+\}$")
                    if any(irx.match(q) and {"get", "patch"} <= ms for q, ms in by_path.items()):
                        ok = True
            if not ok:
                problems.append(f"{fid}: CRUD `{tok}` needs GET+POST on the collection and GET+PATCH on its item")
        if qnames:
            gets = [o for (m, p, o) in fops if m == "get" and p in matched]
            for n in qnames:
                if not any(n in contract.op_params(o) for o in gets):
                    problems.append(f"{fid}: query parameter `{n}` of `{tok}` missing on GET {matched[:3]}")
        if not t["in_group"]:
            last_non_group = matched
        last_paths = matched
    state["anchor"] = last_non_group
    return len(toks)


# ------------------------------------------------------------------------------------------------ addendum
def addendum_sections(md):
    """{feature_id: {"api": [...], "errors": [...]}} from FEATURES-ADDENDUM.md bullets."""
    out, cur, block = {}, None, None
    for line in md.splitlines():
        h = re.match(r"^## \d+\. (F-[A-Z0-9-]+) — ", line)
        if h:
            cur, block = h.group(1), None
            out[cur] = {"api": [], "errors": []}
            continue
        if line.startswith("## "):
            cur, block = None, None
            continue
        if cur is None:
            continue
        if re.match(r"^\* \*\*API\b", line):
            block = "api"
            continue
        if re.match(r"^\* \*\*Errors\b", line):
            block = "errors"
            continue
        if line.startswith("* "):
            block = None
            continue
        m = re.match(r"^  \* (.*)$", line)
        if m and block:
            out[cur][block].append(m.group(1).strip())
    return out


def error_code_of(text):
    m = CODE_RE.match(text.strip())
    return m.group(1) if m else None


# ------------------------------------------------------------------------------------------------ main check
def run(root=None):
    root = repo_root(root)
    problems = []
    stats = {"features": 0, "api_lines": 0, "tokens": 0, "error_codes": 0, "events": 0, "manual_lines": 0}
    with open(os.path.join(root, "docs", "spec", "features", "feature-registry.json"), encoding="utf-8") as fh:
        reg = json.load(fh)
    with open(os.path.join(root, "docs", "spec", "FEATURES-ADDENDUM.md"), encoding="utf-8") as fh:
        md = fh.read()
    with open(os.path.join(root, "contracts", "src", "registry-coverage.yaml"), encoding="utf-8") as fh:
        manual = yaml.safe_load(fh)["lines"]
    contract = Contract(root)
    enum = set(contract.doc["components"]["schemas"]["ErrorCode"]["enum"])
    add = addendum_sections(md)
    manual_by = {(m["feature"], m["text"]): m for m in manual}
    used_manual = set()
    for f in reg["features"]:
        fid = f["id"]
        stats["features"] += 1
        # twin documents agree
        a = add.get(fid)
        if a is None:
            problems.append(f"{fid}: section missing in FEATURES-ADDENDUM.md")
        else:
            if a["api"] != [x.strip() for x in f["api"]]:
                problems.append(f"{fid}: API bullets differ between FEATURES-ADDENDUM.md and the registry")
            if a["errors"] != [x.strip() for x in f["errors"]]:
                problems.append(f"{fid}: error bullets differ between FEATURES-ADDENDUM.md and the registry")
        # endpoints
        state = {}
        for line in f["api"]:
            stats["api_lines"] += 1
            n = check_api_line(contract, fid, line, problems, state)
            stats["tokens"] += n
            if n == 0:
                m = manual_by.get((fid, line))
                if not m:
                    problems.append(f"{fid}: API line without endpoint tokens needs contracts/src/registry-coverage.yaml: {line[:100]}")
                    continue
                used_manual.add((fid, line))
                stats["manual_lines"] += 1
                check_manual(contract, fid, m, problems)
        # error codes
        tagged_codes = set()
        for (_m, _p, o) in contract.feature_ops(fid):
            tagged_codes |= set(o.get("x-error-codes") or [])
        for e in f["errors"]:
            code = error_code_of(e)
            stats["error_codes"] += 1
            if code is None:
                problems.append(f"{fid}: cannot read error code from {e!r}")
                continue
            if code.endswith("*"):
                pre = code[:-1]
                if not any(c.startswith(pre) for c in contract.errors):
                    problems.append(f"{fid}: no registered code matches {code}")
                if not any(c.startswith(pre) for c in tagged_codes):
                    problems.append(f"{fid}: no operation tagged {fid} declares a {code} error")
                continue
            if code not in contract.errors:
                problems.append(f"{fid}: error code {code} not in contracts/errors.yaml")
            if code not in enum:
                problems.append(f"{fid}: error code {code} not in OpenAPI components.schemas.ErrorCode")
            if code not in tagged_codes:
                problems.append(f"{fid}: error code {code} not declared (x-error-codes) by any operation tagged {fid}")
        # events
        blob = json.dumps(f, ensure_ascii=False)
        names = set(EVENT_RE.findall(blob))
        if a is not None:
            sec = re.search(r"^## \d+\. " + re.escape(fid) + r" — .*?(?=^## )", md, re.S | re.M)
            if sec:
                names |= set(EVENT_RE.findall(sec.group(0)))
        for ev in sorted(names):
            stats["events"] += 1
            check_event(root, contract, fid, ev, problems)
        if f.get("states"):
            evs = [k for k, v in contract.webhooks.items() if fid in (v["post"].get("x-features") or [])]
            if not evs:
                problems.append(f"{fid}: has state transitions but no event (webhook) tagged with it")
            for ev in evs:
                stats["events"] += 1
                check_event(root, contract, fid, ev, problems)
        if f["api"] and not contract.feature_ops(fid):
            problems.append(f"{fid}: no operation tagged with the feature")
    for key in manual_by:
        if key not in used_manual:
            problems.append(f"registry-coverage.yaml: stale entry {key[0]}: {key[1][:80]}")
    return problems, stats


def check_event(root, contract, fid, ev, problems):
    if ev not in contract.webhooks:
        problems.append(f"{fid}: event {ev} missing in OpenAPI webhooks")
    mod = ev.split(".")[0]
    p = os.path.join(root, "contracts", "events", mod, ev + ".json")
    if not os.path.isfile(p):
        problems.append(f"{fid}: event schema contracts/events/{mod}/{ev}.json missing")


def check_manual(contract, fid, m, problems):
    have = {(mm.upper(), p) for (mm, p, _o) in contract.ops}
    for spec in m.get("operations", []):
        meth, path = spec.split(" ", 1)
        if (meth.upper(), path) not in have:
            problems.append(f"{fid}: manual coverage operation {spec} not in the contract")
    kind = m["check"]
    if kind == "idempotency":
        bad = []
        for (mm, p, o) in contract.feature_ops(fid):
            if mm in ("post", "put", "patch", "delete"):
                names = contract.op_params(o)
                req = any(("$ref" in x and x["$ref"].endswith("/IdempotencyKey")) or (x.get("name") == "Idempotency-Key" and x.get("required")) for x in o.get("parameters", []))
                if "Idempotency-Key" not in names or not req:
                    bad.append(f"{mm.upper()} {p}")
        if bad:
            problems.append(f"{fid}: mutating operations without required Idempotency-Key: {bad[:5]}")
        if not contract.feature_ops(fid):
            problems.append(f"{fid}: idempotency check has no operations")
    elif kind == "read_scopes":
        codes = {o["x-permission"] for (_m, _p, o) in contract.ops}
        for mod in m.get("modules", []):
            if not any(c.startswith(mod + ".") and c.endswith(".read") for c in codes):
                problems.append(f"{fid}: module {mod} has no read permission code for MCP scopes")
    elif kind != "operations":
        problems.append(f"{fid}: unknown manual check {kind}")


def main(argv):
    root = None
    if "--root" in argv:
        root = argv[argv.index("--root") + 1]
    problems, stats = run(root)
    for p in problems:
        print("DIFF", p)
    print(json.dumps(stats))
    print("REGISTRY-DIFF-OK" if not problems else f"REGISTRY-DIFF-FAILED {len(problems)} problems")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
