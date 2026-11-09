#!/usr/bin/env python3
"""cloudctl — wave/runbook control for Nexus ERP on Claude Code cloud sessions (edition 3.0.1-claude-cloud).

Python 3.11+ standard library only. Run from the repository root (the git top level is used).

  validate-plan                          plan-cloud.json: acyclic, waves, owned paths disjoint per wave, every 2.2.0 task/feature/test mapped once
  show          --task T                 the task brief (paths, tests, features, docs to read)
  worker-start  --task T [--resume]      precondition + branch + attempt checks for a worker session (BLOCKED-PRECONDITION, exit 3, if not allowed)
  worker-check  --task T                 changed paths vs owned paths, protected paths, secret scan
  worker-report --task T --status DONE|BLOCKED [--reason R] [--no-push]   docs/run/reports/T.json as its own commit, then push the branch
  integrate     --wave N [--test-cmd C] [--ids-cmd C]   (C may use $NEXUS_IDS) scan reports, verify, merge, re-run tests, accept/reject, commit state (local; integrator only)
  close-gates   --wave N [--ids-cmd C]                  gates/RC whose requirements are accepted: re-run every test due, gate-check, commit state
  publish       --wave N [--no-push]                    audit + fast-forward push of main (and the RC branch) + print the next paste list
  audit-repair  [--no-push]              revert commits that reached main without the integrator (acknowledged by trailer) and push
  audit-main                             first-parent history of origin/main: only the seed commit and integrator commits
  next                                   the exact prompt files to paste next
  status                                 re-render docs/run/STATUS.md from STATE.json
  wave-tests    --wave N                 test IDs owned by the tasks of wave N
"""
import argparse, datetime as dt, fnmatch, glob as globmod, hashlib, json, os, re, subprocess, sys, time

PLAN_REL = "docs/run/plan-cloud.json"
STATE_REL = "docs/run/STATE.json"
STATUS_REL = "docs/run/STATUS.md"
SPEC_REL = "docs/spec"
REPORT_SCHEMA = "nexus-cloud/report/v1"
PLAN_SCHEMA = "nexus-cloud/plan/v1"
SEED_TRAILER = "Nexus-Seed:"
INTEG_TRAILER = "Nexus-Integrator:"
PROTECTED = ["CLAUDE.md", "docs/spec/**", "docs/prompt-pack/**", "docs/cloud-prompts/**", "docs/run/STATE.json", "docs/run/STATUS.md",
             "docs/run/plan-cloud.json", "docs/run/evidence/**", "tools/**", ".claude/**", "scripts/cloud/**", ".gitignore", "docs/ui-progress/**"]
# owner maintenance commits (trailer Nexus-Seed:) after the initial seed may touch only these paths (prompt/tool updates, never product or state)
MAINT_PATHS = ["docs/cloud-prompts/**", "docs/ui-progress/README.md", "tools/**", "CLAUDE.md", ".claude/settings.json"]
SECRET_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("password assignment", re.compile(r"(?i)(?:password|passwd|secret|api_?key|token)['\"]?\s*[:=]\s*['\"]?(?!changeme|example|placeholder|dummy|test|fake|\$\{|<|\{\{|\*+|x+\b|None\b|null\b|\"\"|''|os\.environ|env\()[A-Za-z0-9!@#%^&+/=_-]{12,}")),
]
SECRET_FILES = [".env", ".env.*", "*.pem", "*.key", "id_rsa", "id_ed25519", "*.p12", "*.pfx", "deploy/dev/secrets/*"]
SECRET_FILE_OK = [".env.example", "*.env.example", "*.example", "deploy/dev/secrets/.gitkeep", "deploy/dev/secrets/README.md"]


# ------------------------------------------------------------------------------------------------------------------------- helpers
def die(msg, code=2):
    print(msg)
    sys.exit(code)


def toplevel():
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else os.getcwd()


ROOT = os.environ.get("NEXUS_ROOT") or toplevel()


def git(*args, check=True):
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed ({p.returncode}): {(p.stderr or p.stdout).strip()[-800:]}")
    return p.stdout.strip() if check else p


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def save_json(rel, obj):
    p = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def sample(glob):
    return glob.replace("**", "s1/s2.x").replace("*", "s3").replace("?", "q")


def match(path, glob):
    if glob.endswith("/**"):
        return path.startswith(glob[:-3] + "/") or fnmatch.fnmatchcase(path, glob)
    return fnmatch.fnmatchcase(path, glob)


def globs_overlap(a, b):
    return a == b or match(sample(a), b) or match(sample(b), a)


def common_paths(plan, tid):
    return [c.replace("{task}", tid) for c in plan["common_paths_per_task"]]


def owns(task, path):
    return any(match(path, g) for g in task.get("paths", [])) and not any(match(path, g) for g in task.get("except", []))


def allowed(plan, tid, path):
    return owns(plan["tasks"][tid], path) or any(match(path, g) for g in common_paths(plan, tid))


def push_retry(*refspec):
    last = ""
    for wait in (0, 2, 4, 8, 16):
        if wait:
            time.sleep(wait)
        p = git("push", "origin", *refspec, check=False)
        if p.returncode == 0:
            return True, (p.stdout + p.stderr).strip()
        last = (p.stdout + p.stderr).strip()
        if re.search(r"(?i)non-fast-forward|rejected|protected|denied|403|permission", last):
            return False, last
    return False, last


def fetch_retry():
    last = ""
    for wait in (0, 2, 4, 8, 16):
        if wait:
            time.sleep(wait)
        p = git("fetch", "origin", "--prune", check=False)
        if p.returncode == 0:
            return
        last = (p.stdout + p.stderr).strip()
    die(f"BLOCKED-GIT: git fetch failed after retries: {last[-400:]}", 4)


def current_branch():
    return git("rev-parse", "--abbrev-ref", "HEAD")


# ------------------------------------------------------------------------------------------------------------------------- plan
def _mediated(plan, a, b):
    return any(a in m["paths"] and b in m["paths"] for m in plan.get("mediated_overlaps", []))


def _done_wave(T, r):
    rt = T.get(r, {})
    return rt.get("wave", 0) if rt.get("kind") in ("worker", "dev") else rt.get("after_wave", 0)


def validate_plan(plan, reg=None, legacy=None):
    E = []
    need = lambda c, m: c or E.append(m)
    need(plan.get("schema") == PLAN_SCHEMA, f"plan schema must be {PLAN_SCHEMA}")
    T = plan["tasks"]
    for tid, t in T.items():
        need(t.get("kind") in ("worker", "gate", "release", "dev"), f"{tid}: bad kind")
        for r in t.get("requires", []):
            need(r in T, f"{tid}: unknown requirement {r}")
        if t.get("kind") in ("worker", "dev"):
            need(bool(re.match(r"^W\d\d-S\d+-[A-Za-z0-9]+-[a-z0-9-]+\.md$", t.get("prompt_file", ""))), f"{tid}: bad prompt_file")
            need(t.get("retry_file") == f"RETRY-{tid}.md", f"{tid}: retry_file must be RETRY-{tid}.md")
            need(isinstance(t.get("paths"), list) and t["paths"], f"{tid}: owned paths missing")
            need(t.get("weight", 0) > 0, f"{tid}: weight must be > 0")
            for g in t.get("paths", []):
                need(not globs_overlap(g, "docs/ui-progress/**"), f"{tid}: owned path {g} overlaps docs/ui-progress/** (integrator only)")
    state, order = {}, []
    def visit(n, stack):
        if state.get(n) == 1:
            E.append("cycle: " + " -> ".join(stack + [n])); return
        if state.get(n) == 2:
            return
        state[n] = 1
        for r in T[n].get("requires", []):
            if r in T:
                visit(r, stack + [n])
        state[n] = 2; order.append(n)
    for n in T:
        visit(n, [])
    waves = {w["n"]: w for w in plan["waves"]}
    for tid, t in T.items():
        if t.get("kind") in ("worker", "dev"):
            need(t.get("wave") in waves and tid in waves[t["wave"]]["tasks"], f"{tid}: not listed in wave {t.get('wave')}")
            for r in t.get("requires", []):
                need(_done_wave(T, r) < t.get("wave", 0), f"{tid} (wave {t.get('wave')}) requires {r}, done only in/after wave {_done_wave(T, r)}")
        else:
            need(t.get("after_wave") in waves, f"{tid}: after_wave missing")
            for r in t.get("requires", []):
                need(_done_wave(T, r) <= t.get("after_wave", 0), f"{tid} after wave {t.get('after_wave')} requires {r} (wave {_done_wave(T, r)})")
    listed = [x for w in plan["waves"] for x in w["tasks"]]
    need(len(listed) == len(set(listed)), "a task is listed in two waves")
    for w in plan["waves"]:
        need(re.match(r"^INTEGRATOR-after-W\d\d\.md$", w.get("integrator_file", "")) is not None, f"wave {w['n']}: integrator_file missing")
        ts = w["tasks"]
        for i in range(len(ts)):
            for j in range(i + 1, len(ts)):
                a, b = T[ts[i]], T[ts[j]]
                for ga in a["paths"]:
                    for gb in b["paths"]:
                        if globs_overlap(ga, gb) and gb not in a.get("except", []) and ga not in b.get("except", []) and not _mediated(plan, ga, gb):
                            E.append(f"wave {w['n']}: {ts[i]} {ga!r} overlaps {ts[j]} {gb!r}")
    if reg is not None:
        tests = {x["id"] for x in reg["tests"]}
        mapped = {}
        for tid, t in T.items():
            for x in t.get("tests", []):
                need(x in tests, f"{tid}: unknown test {x}")
                need(x not in mapped, f"test {x} mapped twice ({mapped.get(x)} and {tid})")
                mapped[x] = tid
        need(set(mapped) == tests, f"tests not mapped: {sorted(tests - set(mapped))[:10]}")
        fmap = {}
        for tid, t in T.items():
            for f in t.get("features", []):
                need(f not in fmap, f"feature {f} owned twice ({fmap.get(f)} and {tid})")
                fmap[f] = tid
        need(set(fmap) == {f["id"] for f in reg["features"]}, f"features not mapped: {sorted({f['id'] for f in reg['features']} - set(fmap))}")
    if legacy is not None:
        src = {}
        for tid, t in T.items():
            for s in t.get("source", []):
                src.setdefault(s, []).append(tid)
        need(set(src) == set(legacy["tasks"]), f"2.2.0 tasks not mapped: {sorted(set(legacy['tasks']) - set(src))}")
        for s, lst in src.items():
            if len(lst) > 1:
                need(all(T[x].get("split_of") == s for x in lst), f"{s} mapped to {lst} without split_of")
                need(abs(sum(T[x].get("share", 0) for x in lst) - 1.0) < 1e-6, f"{s}: split shares must sum to 1")
    return E, order


# ------------------------------------------------------------------------------------------------------------------------- state
def load_plan():
    return load_json(PLAN_REL)


def load_state():
    return load_json(STATE_REL)


def deps_ok(plan, st, tid):
    return all(st["tasks"][r]["status"] == "ACCEPTED" for r in plan["tasks"][tid].get("requires", []))


def refresh_ready(plan, st):
    for tid, s in st["tasks"].items():
        if s["status"] == "PENDING" and deps_ok(plan, st, tid):
            s["status"] = "READY"


def apply_event(plan, st, ev):
    ev = {**ev, "at": ev.get("at") or now()}
    t = st["tasks"][ev["task"]]
    typ = ev["type"]
    if typ == "accept":
        t.update(status="ACCEPTED", accepted_sha=ev["sha"], branch=ev.get("branch"), accepted_at=ev["at"])
    elif typ == "reject":
        t.setdefault("notes", []).append(f"attempt {t['attempt']} rejected: {ev['reason']}")
        if t["attempt"] >= plan["limits"]["max_attempts"]:
            t["status"] = "FAILED"
        else:
            t.update(status="REWORK", attempt=t["attempt"] + 1)
    elif typ == "blocked":
        t.setdefault("notes", []).append(f"attempt {t['attempt']} BLOCKED: {ev['reason']}")
        t["status"] = "BLOCKED"
    elif typ == "unblock":
        t["status"] = "REWORK" if t["attempt"] > 1 else "READY"
    elif typ == "gate_pass":
        t.update(status="ACCEPTED", accepted_sha=ev["sha"], digest=ev.get("digest"), accepted_at=ev["at"])
    elif typ == "gate_fail":
        t.setdefault("notes", []).append(f"gate check failed: {ev['reason']}")
    elif typ == "rc":
        t.update(status="ACCEPTED", accepted_sha=ev["sha"], branch=ev.get("branch"), accepted_at=ev["at"])
        st["rc"] = {"sha": ev["sha"], "branch": ev.get("branch"), "at": ev["at"]}
    else:
        raise ValueError(f"unknown event {typ}")
    st.setdefault("history", []).append(ev)
    st["updated_at"] = ev["at"]
    refresh_ready(plan, st)
    return st


def next_text(plan, st):
    T, S = plan["tasks"], st["tasks"]
    L = []
    failed = [t for t in S if S[t]["status"] == "FAILED"]
    blocked = [t for t in S if S[t]["status"] == "BLOCKED"]
    for t in failed:
        L.append(f"STOP-OPERATOR: {t} failed {plan['limits']['max_attempts']} attempts ({(S[t].get('notes') or [''])[-1][:200]})")
    if all(S[t]["status"] == "ACCEPTED" for t in S):
        return "\n".join(L + ["COMPLETE: every task is ACCEPTED. On your Mac follow docs/run/LOCAL-RUN.md to run the app at http://localhost:8080 (DEV only)."])
    ready = [t for t in T if T[t]["kind"] in ("worker", "dev") and S[t]["status"] in ("READY", "REWORK")]
    if ready:
        wave = min(T[t]["wave"] for t in ready)
        fresh = sorted([t for t in ready if S[t]["status"] == "READY" and S[t]["attempt"] == 1 and not S[t].get("notes")], key=lambda t: (T[t]["wave"], -T[t]["weight"], t))
        retry = sorted(t for t in ready if t not in fresh)
        maxp = plan["limits"]["max_parallel_sessions"]
        L.append(f"NEXT: wave {wave:02d} — open {len(fresh) + len(retry)} NEW Claude Code cloud session(s) on {plan['repo_slug']} (at most {maxp} at the same time;"
                 " start the next one when one finishes) and paste exactly one file into each:")
        for t in fresh:
            L.append(f"  prompts/{T[t]['prompt_file']}")
        for t in retry:
            L.append(f"  prompts/{T[t]['retry_file']}   (attempt {S[t]['attempt']}: {(S[t].get('notes') or ['session ended before its report'])[-1][:160]})")
        iw = max(T[t]["wave"] for t in ready)
        due_after = [T[g]["after_wave"] for g in T if T[g]["kind"] in ("gate", "release") and S[g]["status"] != "ACCEPTED" and T[g]["after_wave"] > iw
                     and all(S[r]["status"] == "ACCEPTED" or r in ready for r in T[g]["requires"])]
        integ = plan["waves"][(min(due_after) if due_after else iw) - 1]["integrator_file"]
        L.append(f"THEN: when every one of those sessions has ended (DONE or BLOCKED), paste prompts/{integ} into 1 NEW session.")
    elif not blocked and not failed:
        pend = [t for t in T if S[t]["status"] != "ACCEPTED"]
        w = min((T[t].get("wave") or T[t].get("after_wave")) for t in pend)
        L.append(f"NEXT: no worker prompt is due; paste prompts/{plan['waves'][w - 1]['integrator_file']} into 1 NEW session.")
    for t in blocked:
        L.append(f"OPERATOR: {t} is BLOCKED — {(S[t].get('notes') or [''])[-1][:200]}. Remove the named cause, then paste prompts/{T[t]['retry_file']}.")
    return "\n".join(L)


def render_status(plan, st):
    T, S = plan["tasks"], st["tasks"]
    L = [f"# STATUS — Nexus ERP (edition {plan['edition']})", "", f"Updated: {st.get('updated_at')} (UTC). Repository: {plan['repo_url']}.", "",
         "Written only by the integrator (`python3 tools/cloudctl.py status`). A task counts as working only when it is ACCEPTED with",
         "integrator-run test evidence under `docs/run/evidence/`.", "", "## What to paste next", "", "```", next_text(plan, st), "```", "",
         "## Tasks", "", "| Wave | Task | Kind | Status | Attempt | Accepted at | Last note |", "|---|---|---|---|---|---|---|"]
    for w in plan["waves"]:
        for tid in w["tasks"] + [g for g in T if T[g]["kind"] in ("gate", "release") and T[g].get("after_wave") == w["n"]]:
            s = S[tid]
            note = ((s.get("notes") or [""])[-1])[:90].replace("|", "/")
            L.append(f"| {w['n']:02d} | {tid} | {T[tid]['kind']} | {s['status']} | {s['attempt']} | {s.get('accepted_at') or ''} | {note} |")
    if st.get("rc"):
        L += ["", f"Release candidate: branch `{st['rc'].get('branch')}` at `{st['rc']['sha']}`."]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------------------------------------------------------------- checks
def scan_secrets(paths, at_ref=None):
    bad = []
    for p in paths:
        base = os.path.basename(p)
        hit = lambda pats: any(fnmatch.fnmatch(p, g) or fnmatch.fnmatch(base, g) for g in pats)
        if hit(SECRET_FILES) and not hit(SECRET_FILE_OK):
            bad.append(f"{p}: secret-like file"); continue
        if at_ref:
            r = git("show", f"{at_ref}:{p}", check=False)
            if r.returncode != 0:
                continue
            txt = r.stdout
        else:
            try:
                txt = open(os.path.join(ROOT, p), encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
        for name, rx in SECRET_PATTERNS:
            if rx.search(txt):
                bad.append(f"{p}: {name}"); break
    return bad


def changed_paths(base, head):
    return [x for x in git("diff", "--name-only", "--no-renames", f"{base}...{head}").splitlines() if x]


def check_paths(plan, tid, paths):
    errs = []
    for p in paths:
        if any(match(p, g) for g in PROTECTED) and not owns(plan["tasks"][tid], p) and not any(match(p, g) for g in common_paths(plan, tid)):
            errs.append(f"{p}: protected path (integrator/seed only)")
        elif not allowed(plan, tid, p):
            errs.append(f"{p}: not owned by {tid}")
    return errs


def validate_report(rep, tid):
    E = []
    need = lambda c, m: c or E.append(m)
    need(rep.get("schema") == REPORT_SCHEMA, "report schema")
    need(rep.get("task") == tid, "report task mismatch")
    need(rep.get("status") in ("DONE", "BLOCKED"), "report status must be DONE or BLOCKED")
    need(isinstance(rep.get("attempt"), int), "report attempt must be an integer")
    need(re.fullmatch(r"[0-9a-f]{40}", rep.get("tested_sha") or "") is not None, "tested_sha must be a full commit id")
    need(isinstance(rep.get("changed_paths"), list), "changed_paths must be a list")
    if rep.get("status") == "BLOCKED":
        need(bool(rep.get("reason")), "BLOCKED needs a reason")
    txt = json.dumps(rep)
    for name, rx in SECRET_PATTERNS:
        if rx.search(txt):
            E.append(f"report contains a {name}")
    return E


# ------------------------------------------------------------------------------------------------------------------------- worker
def cmd_show(a):
    plan = load_plan()
    t = plan["tasks"].get(a.task)
    if not t:
        die(f"unknown task {a.task}")
    print(json.dumps({k: t.get(k) for k in ("title", "kind", "wave", "requires", "source", "split_of", "scope", "paths", "except", "features", "tests", "docs")},
                     indent=2, ensure_ascii=False))
    print("common paths you may also write:", ", ".join(common_paths(plan, a.task)))


def main_state(plan):
    return json.loads(git("show", f"origin/{plan['default_branch']}:{STATE_REL}"))


def cmd_worker_start(a):
    plan, tid = load_plan(), a.task
    if tid not in plan["tasks"] or plan["tasks"][tid]["kind"] not in ("worker", "dev"):
        die(f"BLOCKED-PRECONDITION {tid}: not a worker task of {PLAN_REL}. Stop.", 3)
    url = git("remote", "get-url", "origin")
    if plan["repo_slug"].lower() not in url.lower() and not os.environ.get("NEXUS_REHEARSAL"):
        die(f"BLOCKED-PRECONDITION {tid}: origin is {url}, expected {plan['repo_slug']}. Stop.", 3)
    fetch_retry()
    st = main_state(plan)
    s = st["tasks"][tid]
    unmet = [r for r in plan["tasks"][tid]["requires"] if st["tasks"][r]["status"] != "ACCEPTED"]
    if s["status"] == "ACCEPTED":
        die(f"BLOCKED-PRECONDITION {tid}: already ACCEPTED at {s.get('accepted_sha')}; nothing to do. Stop.", 3)
    if s["status"] == "FAILED":
        die(f"BLOCKED-PRECONDITION {tid}: FAILED after {s['attempt']} attempts; operator decision needed. Stop.", 3)
    if unmet:
        die(f"BLOCKED-PRECONDITION {tid}: not yet ACCEPTED on main: {', '.join(unmet)}. Paste this prompt only when docs/run/STATUS.md lists it. Stop.", 3)
    main = plan["default_branch"]
    br = current_branch()
    if not br.startswith("claude/"):
        br = f"claude/{tid.lower()}-a{s['attempt']}-{os.urandom(2).hex()}"
        git("checkout", "-q", "-B", br, f"origin/{main}")
    else:
        p = git("merge", "--no-edit", f"origin/{main}", check=False)
        if p.returncode != 0:
            git("merge", "--abort", check=False)
            die(f"BLOCKED-PRECONDITION {tid}: session branch {br} does not merge cleanly with main. Stop.", 3)
    resumed = None
    if a.resume:
        cands = []
        for ref in git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/").splitlines():
            if ref in (f"origin/{main}", "origin/HEAD", f"origin/{br}") or ref.startswith("origin/release/"):
                continue
            p = git("show", f"{ref}:docs/run/progress/{tid}.md", check=False)
            if p.returncode == 0 and re.search(rf"(?m)^attempt:\s*{s['attempt']}\s*$", p.stdout):
                cands.append((git("log", "-1", "--format=%ct", ref), ref))
        if cands:
            resumed = sorted(cands)[-1][1]
            p = git("merge", "--no-edit", resumed, check=False)
            if p.returncode != 0:
                git("merge", "--abort", check=False)
                die(f"BLOCKED-PRECONDITION {tid}: earlier work on {resumed} does not merge with main; run worker-start without --resume and start fresh.", 3)
    print(json.dumps({"task": tid, "attempt": s["attempt"], "status_on_main": s["status"], "branch": br, "resumed_from": resumed,
                      "rework_notes": s.get("notes", [])[-3:], "main_sha": git("rev-parse", f"origin/{main}")}, indent=2))
    print(f"OK-START {tid} attempt {s['attempt']} on branch {br}")


def cmd_worker_check(a):
    plan = load_plan()
    base = git("merge-base", "HEAD", f"origin/{plan['default_branch']}")
    dirty = [x[3:].split(" -> ")[-1] for x in git("status", "--porcelain").splitlines() if x]
    paths = sorted(set(changed_paths(base, "HEAD")) | set(dirty))
    errs = check_paths(plan, a.task, paths) + scan_secrets(paths)
    for e in errs:
        print("PROBLEM", e)
    print(("CHECK-OK " if not errs else "CHECK-FAILED ") + f"{a.task}: {len(paths)} changed paths")
    sys.exit(0 if not errs else 1)


def cmd_worker_report(a):
    plan, tid = load_plan(), a.task
    fetch_retry()
    st = main_state(plan)
    if git("status", "--porcelain"):
        die("REPORT-REFUSED: commit (or discard) your work first; the report must be its own commit", 1)
    base = git("merge-base", "HEAD", f"origin/{plan['default_branch']}")
    tested = git("rev-parse", "HEAD")
    paths = changed_paths(base, tested)
    tests = {}
    res = os.path.join(ROOT, "reports", f"{tid}-feature-results.json")
    if os.path.isfile(res):
        r = json.load(open(res, encoding="utf-8"))
        tests = {k: v.get("status") for k, v in r.get("tests", {}).items() if k in plan["tasks"][tid].get("tests", [])}
    sid = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID", "")
    rep = {"schema": REPORT_SCHEMA, "task": tid, "attempt": st["tasks"][tid]["attempt"], "status": a.status, "reason": a.reason,
           "branch": current_branch(), "base_sha": base, "tested_sha": tested, "changed_paths": paths, "tests": tests,
           "missing_tests": sorted(set(plan["tasks"][tid].get("tests", [])) - {k for k, v in tests.items() if v == "PASSED"}),
           "session_url": ("https://claude.ai/code/" + sid.replace("cse_", "session_", 1)) if sid else "", "created_at": now()}
    errs = validate_report(rep, tid)
    if a.status == "DONE":
        errs += check_paths(plan, tid, paths) + scan_secrets(paths)
        if rep["missing_tests"]:
            errs.append(f"mandatory tests not PASSED in reports/{tid}-feature-results.json: {rep['missing_tests'][:12]}")
    if errs:
        for e in errs:
            print("PROBLEM", e)
        die(f"REPORT-REFUSED {tid}: fix the problems above, or report BLOCKED with the exact reason", 1)
    save_json(f"docs/run/reports/{tid}.json", rep)
    git("add", f"docs/run/reports/{tid}.json")
    git("commit", "-q", "-m", f"report {tid} attempt {rep['attempt']}: {a.status}")
    if a.no_push:
        print(f"REPORT-WRITTEN {tid} (not pushed)"); return
    ok, out = push_retry("-u", "HEAD:refs/heads/" + rep["branch"])
    if not ok:
        die(f"BLOCKED-GIT {tid}: push of {rep['branch']} failed: {out[-300:]}", 4)
    print(f"REPORT-PUSHED {tid} {a.status} branch={rep['branch']} tested_sha={tested}")


# ------------------------------------------------------------------------------------------------------------------------- integrator
ACK_TRAILER = "Nexus-Audit-Ack:"


def audit_main(ref, full=False):
    """First-parent commits after the initial seed must be integrator commits (trailer Nexus-Integrator:), owner maintenance commits
    (trailer Nexus-Seed:) that touch only MAINT_PATHS, or commits acknowledged (reverted) by a later integrator commit."""
    commits = []
    for chunk in git("log", "--first-parent", "--format=%H%x1f%B%x1e", ref).split("\x1e"):
        chunk = chunk.strip()
        if chunk:
            commits.append(chunk.split("\x1f", 1))
    seeds = [i for i, (_, b) in enumerate(commits) if SEED_TRAILER in b]
    if not seeds:
        return ["no seed commit (Nexus-Seed trailer) in the first-parent history"]
    base = seeds[-1]  # the oldest seed commit is the initial seed
    bad, acks = [], set()
    for h, body in commits[:base]:
        if INTEG_TRAILER in body:
            acks |= set(re.findall(rf"(?m)^{ACK_TRAILER}\s*([0-9a-f]{{40}})", body))
            continue
        if h in acks:
            continue
        if SEED_TRAILER in body:
            touched = [p for p in git("diff-tree", "--no-commit-id", "--name-only", "-r", "-m", "--first-parent", h).splitlines() if p]
            outside = [p for p in touched if not any(match(p, g) for g in MAINT_PATHS)]
            if not outside:
                continue
        bad.append(h if full else h[:12])
    return bad


def cmd_audit_repair(a):
    plan = load_plan()
    main = plan["default_branch"]
    fetch_retry()
    bad = audit_main(f"origin/{main}", full=True)
    if not bad:
        print("AUDIT-OK: nothing to repair"); return
    if any(len(b) != 40 for b in bad):
        die(f"BLOCKED-REPAIR: {bad}; the seed commit is missing — re-seed the repository (operator).", 5)
    git("checkout", "-q", "-B", "nexus-audit-repair", f"origin/{main}")
    for h in bad:  # newest first
        parents = git("rev-list", "--parents", "-n", "1", h).split()[1:]
        p = git("revert", "--no-commit", *(["-m", "1"] if len(parents) > 1 else []), h, check=False)
        if p.returncode != 0:
            git("revert", "--abort", check=False); git("reset", "-q", "--hard", f"origin/{main}")
            die(f"BLOCKED-REPAIR: reverting {h[:12]} conflicts; operator must revert it by hand on GitHub and add a commit with the trailers '{INTEG_TRAILER} audit repair' and '{ACK_TRAILER} {h}'.", 5)
    msg = f"audit repair: revert {len(bad)} commit(s) that reached {main} without the integrator\n\n" + "\n".join(f"{ACK_TRAILER} {h}" for h in bad) + f"\n{INTEG_TRAILER} audit repair"
    git("commit", "-q", "--allow-empty", "-m", msg)
    if audit_main("HEAD"):
        die("BLOCKED-REPAIR: audit still fails after the repair commit", 5)
    if a.no_push:
        print("AUDIT-REPAIRED (not pushed)"); return
    ok, out = push_retry(f"HEAD:refs/heads/{main}")
    if not ok:
        die(f"BLOCKED-PUSH: {out[-300:]}", 4)
    print(f"AUDIT-REPAIRED: reverted {[h[:12] for h in bad]}; {main} -> {git('rev-parse', 'HEAD')[:12]}")
    print(next_text(plan, load_state()))


def find_report(plan, st, tid):
    best = None
    for ref in git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/").splitlines():
        if ref in (f"origin/{plan['default_branch']}", "origin/HEAD") or ref.startswith("origin/release/"):
            continue
        p = git("show", f"{ref}:docs/run/reports/{tid}.json", check=False)
        if p.returncode != 0:
            continue
        try:
            rep = json.loads(p.stdout)
        except ValueError:
            continue
        if rep.get("task") != tid or rep.get("attempt") != st["tasks"][tid]["attempt"]:
            continue
        key = (rep.get("status") == "DONE", rep.get("created_at", ""))
        if best is None or key > best[0]:
            best = (key, ref, rep)
    return (best[1], best[2]) if best else (None, None)


def verify_candidate(plan, tid, ref, rep):
    E = validate_report(rep, tid)
    tip = git("rev-parse", ref)
    if git("merge-base", "--is-ancestor", rep["tested_sha"], tip, check=False).returncode != 0:
        E.append("tested_sha is not an ancestor of the branch tip")
    else:
        extra = [p for p in changed_paths(rep["tested_sha"], tip) if p != f"docs/run/reports/{tid}.json"]
        if extra:
            E.append(f"commits after tested_sha touch {extra[:5]} (only the report may follow the tested commit)")
    paths = changed_paths(git("merge-base", f"origin/{plan['default_branch']}", ref), ref)
    E += check_paths(plan, tid, paths)
    E += scan_secrets(paths, at_ref=ref)
    return E


def union_merge_file(path):
    """Resolve a conflicted append-only file by keeping both sides' lines (ours first)."""
    full = os.path.join(ROOT, path)
    out, mode, ours, theirs = [], None, [], []
    for line in open(full, encoding="utf-8").read().splitlines(keepends=True):
        if line.startswith("<<<<<<< "):
            mode, ours, theirs = "o", [], []
        elif line.startswith("=======") and mode == "o":
            mode = "t"
        elif line.startswith(">>>>>>> ") and mode == "t":
            out += ours + [x for x in theirs if x not in ours]; mode = None
        elif mode == "o":
            ours.append(line)
        elif mode == "t":
            theirs.append(line)
        else:
            out.append(line)
    open(full, "w", encoding="utf-8").write("".join(out))


def run_cmd(cmd, label, log):
    t0 = time.time()
    p = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, executable="/bin/bash")
    log.append(f"$ {cmd}  [{label}] -> exit {p.returncode} in {time.time() - t0:.0f}s")
    return p.returncode, (p.stdout + p.stderr)[-1500:]


def collect_results(ids, commit, out_rel, log, ids_cmd):
    for d in ("reports/junit", "reports/playwright"):
        for f in globmod.glob(os.path.join(ROOT, d, "*")):
            os.remove(f)
    os.environ["NEXUS_IDS"] = " ".join(sorted(ids))
    run_cmd(ids_cmd, f"feature tests ({len(ids)} ids)", log)
    reps = sorted(globmod.glob(os.path.join(ROOT, "reports/junit/*.xml")) + globmod.glob(os.path.join(ROOT, "reports/playwright/*.json")))
    os.makedirs(os.path.dirname(os.path.join(ROOT, out_rel)), exist_ok=True)
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools/featctl.py"), "collect", "--root", os.path.join(ROOT, SPEC_REL), "--commit", commit,
                        "--out", os.path.join(ROOT, out_rel)] + reps, cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        log.append(f"featctl collect failed: {(p.stderr or p.stdout)[-300:]}")
        return {}
    data = json.load(open(os.path.join(ROOT, out_rel), encoding="utf-8"))
    for v in data.get("tests", {}).values():
        f, _, frag = v["evidence"].partition("#")
        v["evidence"] = os.path.relpath(f, ROOT) + ("#" + frag if frag else "")
    save_json(out_rel, data)
    return data.get("tests", {})


def commit_state(plan, st, wave, verdict, log, what):
    d = os.path.join(ROOT, f"docs/run/evidence/W{wave:02d}")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "integrator-log.md"), "a", encoding="utf-8") as f:
        f.write(f"\n## {what} — {now()}\n\n" + ("\n".join(f"* {k}: {v}" for k, v in verdict.items()) or "* nothing to do") + "\n\n```\n" + "\n".join(log) + "\n```\n")
    save_json(STATE_REL, st)
    open(os.path.join(ROOT, STATUS_REL), "w", encoding="utf-8").write(render_status(plan, st))
    git("add", "-A", "docs/run")
    if git("diff", "--cached", "--quiet", check=False).returncode != 0:
        git("commit", "-q", "-m", f"state: {what} after wave {wave:02d}\n\n" + "\n".join(f"{k}: {v[:120]}" for k, v in verdict.items()) + f"\n\n{INTEG_TRAILER} wave {wave:02d} {what}")


def cmd_integrate(a):
    plan = load_plan()
    main = plan["default_branch"]
    log, verdict = [], {}
    fetch_retry()
    bad = audit_main(f"origin/{main}")
    if bad:
        die(f"BLOCKED-AUDIT: origin/{main} has first-parent commits that are not integrator commits: {bad}. Only the integrator may advance {main}. Operator: inspect and revert them.", 5)
    work = f"nexus-integrate-w{a.wave:02d}"
    git("checkout", "-q", "-B", work, f"origin/{main}")
    st = load_state()
    refresh_ready(plan, st)
    T, S = plan["tasks"], st["tasks"]
    found = {}
    for tid in [t for t in T if T[t]["kind"] in ("worker", "dev") and S[t]["status"] in ("READY", "REWORK", "BLOCKED")]:
        ref, rep = find_report(plan, st, tid)
        if not ref:
            if S[tid]["status"] != "BLOCKED":
                verdict[tid] = "MISSING (no report for this attempt on any branch; paste its RETRY prompt)"
                note = f"attempt {S[tid]['attempt']}: no report found on any branch (session ended before reporting?)"
                if (S[tid].get("notes") or [""])[-1] != note:
                    S[tid].setdefault("notes", []).append(note)
            continue
        if rep["status"] == "BLOCKED":
            if S[tid]["status"] != "BLOCKED":
                apply_event(plan, st, {"type": "blocked", "task": tid, "reason": f"{rep['reason'][:300]} (branch {ref[7:]})"})
            verdict[tid] = f"BLOCKED by worker: {rep['reason'][:200]}"
            continue
        if S[tid]["status"] == "BLOCKED":
            apply_event(plan, st, {"type": "unblock", "task": tid})
        errs = verify_candidate(plan, tid, ref, rep)
        if errs:
            apply_event(plan, st, {"type": "reject", "task": tid, "reason": "verification: " + "; ".join(errs)[:400]})
            verdict[tid] = "REWORK (verification): " + "; ".join(errs)[:300]
            continue
        found[tid] = (ref, rep)
    has_make = lambda: os.path.isfile(os.path.join(ROOT, "Makefile"))
    order = [t for t in T if t in found]
    merged = []
    for _round in range(4):
        git("checkout", "-q", "-B", work, f"origin/{main}")
        merged, dropped = [], []
        for tid in order:
            ref, rep = found[tid]
            p = git("merge", "--no-ff", "--no-edit", "-m", f"integrate {tid} attempt {rep['attempt']} from {ref[7:]}\n\n{INTEG_TRAILER} merge {tid}", ref, check=False)
            if p.returncode != 0:
                conflicted = git("diff", "--name-only", "--diff-filter=U").splitlines()
                if conflicted and all(any(match(c, g) for g in plan["union_merge_paths"]) for c in conflicted):
                    for c in conflicted:
                        union_merge_file(c); git("add", c)
                    git("commit", "-q", "--no-edit")
                    log.append(f"{tid}: union-merged {conflicted}")
                    print(f"UNION-MERGED {tid}: {conflicted}")
                else:
                    git("merge", "--abort", check=False)
                    dropped.append((tid, f"merge conflict with work integrated before it in {conflicted[:6]}; merge origin/main into your branch and resolve"))
                    continue
            if a.test_cmd and has_make():
                rc, tail = run_cmd(a.test_cmd, f"after {tid}", log)
                if rc != 0:
                    git("reset", "-q", "--hard", "HEAD^")
                    dropped.append((tid, f"`{a.test_cmd}` failed after merging it: {tail[-300:]}"))
                    continue
            merged.append(tid)
        res = {}
        ids = sorted({x for t in merged for x in T[t].get("tests", [])})
        if ids and has_make():
            res = collect_results(ids, git("rev-parse", "HEAD"), f"docs/run/evidence/W{a.wave:02d}/feature-results.json", log, a.ids_cmd)
        bad_t = {t: [x for x in T[t].get("tests", []) if (res.get(x) or {}).get("status") != "PASSED"] for t in merged}
        bad_t = {t: m for t, m in bad_t.items() if m}
        for tid, why in dropped:
            apply_event(plan, st, {"type": "reject", "task": tid, "reason": why}); verdict[tid] = "REWORK: " + why[:200]
            order.remove(tid)
        for tid, miss in bad_t.items():
            apply_event(plan, st, {"type": "reject", "task": tid, "reason": f"integrator-run mandatory tests not PASSED: {miss[:12]}"})
            verdict[tid] = f"REWORK: mandatory tests not PASSED {miss[:8]}"
            order.remove(tid)
        if not bad_t:
            break
    else:
        merged = []
        git("checkout", "-q", "-B", work, f"origin/{main}")
    head = git("rev-parse", "HEAD")
    for tid in merged:
        apply_event(plan, st, {"type": "accept", "task": tid, "sha": head, "branch": found[tid][0][7:]})
        verdict[tid] = "ACCEPTED"
    commit_state(plan, st, a.wave, verdict, log, "integrate")
    print("VERDICTS:")
    for k, v in verdict.items():
        print(f"  {k}: {v}")
    gates = [g for g in T if T[g]["kind"] in ("gate", "release") and S[g]["status"] != "ACCEPTED" and deps_ok(plan, st, g)]
    print("GATES-DUE: " + (" ".join(gates) if gates else "none"))
    print("NEXT-STEP: " + ("write the gate-owned tests named in your prompt under tests/gates/ if missing, commit them, then run close-gates" if gates else "run publish"))


def on_integrator_branch(wave):
    if current_branch() != f"nexus-integrate-w{wave:02d}":
        die(f"BLOCKED-ORDER: run `python3 tools/cloudctl.py integrate --wave {wave}` first (current branch {current_branch()})", 2)


def cmd_close_gates(a):
    plan = load_plan()
    on_integrator_branch(a.wave)
    st = load_state()
    T, S = plan["tasks"], st["tasks"]
    log, verdict = [], {}
    # the integrator's own commits (after the merges) may only add gate tests / gate docs
    own = []
    for h in git("rev-list", "--first-parent", "--no-merges", f"origin/{plan['default_branch']}..HEAD").splitlines():
        own += [p for p in git("diff-tree", "--no-commit-id", "--name-only", "-r", h).splitlines() if not p.startswith("docs/run/")]
    stray = [p for p in own if not (match(p, "tests/gates/**") or match(p, "docs/gates/**") or match(p, "docs/ui-progress/**"))]
    if stray:
        die(f"BLOCKED-ORDER: integrator commits touch paths outside tests/gates/**, docs/gates/** and docs/ui-progress/**: {stray[:8]}", 2)
    has_make = os.path.isfile(os.path.join(ROOT, "Makefile"))
    for g in [t for t in T if T[t]["kind"] in ("gate", "release")]:
        if S[g]["status"] == "ACCEPTED" or not deps_ok(plan, st, g):
            continue
        head = git("rev-parse", "HEAD")
        if T[g]["kind"] == "release":
            rcb = f"release/rc-{a.wave:02d}"
            apply_event(plan, st, {"type": "rc", "task": g, "sha": head, "branch": rcb})
            verdict[g] = f"RC recorded (branch {rcb} at {head[:12]})"
            continue
        gate_ids = T[g].get("gate_tests", [])
        rel = f"docs/run/evidence/{g}/feature-results.json"
        res = collect_results(gate_ids, head, rel, log, a.ids_cmd) if (gate_ids and has_make) else {}
        out = {"failed": ["no results"], "checks": []}
        if res:
            p = subprocess.run([sys.executable, os.path.join(ROOT, "tools/featctl.py"), "gate-check", "--root", os.path.join(ROOT, SPEC_REL), "--gate", g,
                                "--results", os.path.join(ROOT, rel)], cwd=ROOT, capture_output=True, text=True)
            if p.stdout.strip().startswith("{"):
                out = json.loads(p.stdout)
        if not gate_ids or (res and not out.get("failed") and not out.get("errors")):
            apply_event(plan, st, {"type": "gate_pass", "task": g, "sha": head, "digest": out.get("digest")})
            verdict[g] = f"GATE PASSED at {head[:12]}"
            continue
        missing = sorted({x for c in out.get("checks", []) for x in c.get("missing_or_failed", [])}) or [x for x in gate_ids if (res.get(x) or {}).get("status") != "PASSED"]
        owners = sorted({o for o in T if T[o]["kind"] in ("worker", "dev") and set(T[o].get("tests", [])) & set(missing)})
        apply_event(plan, st, {"type": "gate_fail", "task": g, "reason": f"tests not PASSED at {head[:12]}: {missing[:15]}"})
        for o in owners:
            if S[o]["status"] == "ACCEPTED":
                S[o]["status"] = "REWORK"; S[o]["attempt"] += 1
                S[o].setdefault("notes", []).append(f"reopened by gate {g}: tests {[x for x in missing if x in T[o]['tests']][:10]} not PASSED at {head[:12]}")
        own_t = [x for x in missing if x in T[g].get("tests", [])]
        verdict[g] = f"GATE FAILED: {missing[:10]}; reopened {owners}" + (f"; gate-owned tests failing: {own_t}" if own_t else "")
    commit_state(plan, st, a.wave, verdict, log, "close-gates")
    print("VERDICTS:")
    for k, v in verdict.items():
        print(f"  {k}: {v}")
    print("NEXT-STEP: run publish")


def cmd_publish(a):
    plan = load_plan()
    on_integrator_branch(a.wave)
    main = plan["default_branch"]
    fetch_retry()
    if git("merge-base", "--is-ancestor", f"origin/{main}", "HEAD", check=False).returncode != 0:
        die(f"BLOCKED-PUSH: origin/{main} moved while you integrated; paste this integrator prompt again in a new session.", 4)
    bad = audit_main("HEAD")
    if bad:
        die(f"BLOCKED-AUDIT: refusing to publish; non-integrator first-parent commits: {bad}", 5)
    st = load_state()
    if a.no_push:
        print("NOT PUSHED (--no-push)")
    else:
        ok, out = push_retry(f"HEAD:refs/heads/{main}")
        if not ok:
            die(f"BLOCKED-PUSH: could not fast-forward {main}: {out[-300:]}\nIf a GitHub rule refuses it, see START-HERE (main must accept fast-forward pushes; do not require pull requests).", 4)
        if st.get("rc") and st["rc"].get("branch"):
            push_retry(f"{st['rc']['sha']}:refs/heads/{st['rc']['branch']}")
        print(f"PUSHED {main} -> {git('rev-parse', 'HEAD')[:12]}")
    print(next_text(plan, st))


# ------------------------------------------------------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(prog="cloudctl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate-plan")
    s = sub.add_parser("show"); s.add_argument("--task", required=True)
    s = sub.add_parser("worker-start"); s.add_argument("--task", required=True); s.add_argument("--resume", action="store_true")
    s = sub.add_parser("worker-check"); s.add_argument("--task", required=True)
    s = sub.add_parser("worker-report"); s.add_argument("--task", required=True); s.add_argument("--status", required=True, choices=["DONE", "BLOCKED"])
    s.add_argument("--reason", default=""); s.add_argument("--no-push", action="store_true")
    s = sub.add_parser("integrate"); s.add_argument("--wave", type=int, required=True)
    s.add_argument("--test-cmd", default="make ci"); s.add_argument("--ids-cmd", default='make test-ids IDS="$NEXUS_IDS"')
    s = sub.add_parser("close-gates"); s.add_argument("--wave", type=int, required=True); s.add_argument("--ids-cmd", default='make test-ids IDS="$NEXUS_IDS"')
    s = sub.add_parser("publish"); s.add_argument("--wave", type=int, required=True); s.add_argument("--no-push", action="store_true")
    s = sub.add_parser("audit-repair"); s.add_argument("--no-push", action="store_true")
    sub.add_parser("audit-main"); sub.add_parser("next"); sub.add_parser("status")
    s = sub.add_parser("wave-tests"); s.add_argument("--wave", type=int, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "validate-plan":
        plan = load_plan()
        reg = load_json(f"{SPEC_REL}/features/feature-registry.json")
        legacy = load_json(f"{SPEC_REL}/plan.json")
        E, _ = validate_plan(plan, reg, legacy)
        for e in E:
            print("ERROR", e)
        if E:
            sys.exit(1)
        print(f"PLAN-OK tasks={len(plan['tasks'])} waves={len(plan['waves'])} max_width={max(len(w['tasks']) for w in plan['waves'])} "
              f"tests={len(reg['tests'])} features={len(reg['features'])} max_parallel_sessions={plan['limits']['max_parallel_sessions']}")
    elif a.cmd == "audit-main":
        fetch_retry()
        bad = audit_main(f"origin/{load_plan()['default_branch']}")
        print("AUDIT-OK" if not bad else f"AUDIT-FAILED {bad}"); sys.exit(0 if not bad else 5)
    elif a.cmd == "next":
        print(next_text(load_plan(), load_state()))
    elif a.cmd == "status":
        open(os.path.join(ROOT, STATUS_REL), "w", encoding="utf-8").write(render_status(load_plan(), load_state())); print("STATUS written")
    elif a.cmd == "wave-tests":
        plan = load_plan()
        print(" ".join(sorted(x for t in plan["waves"][a.wave - 1]["tasks"] for x in plan["tasks"][t].get("tests", []))))
    else:
        {"show": cmd_show, "worker-start": cmd_worker_start, "worker-check": cmd_worker_check, "worker-report": cmd_worker_report,
         "integrate": cmd_integrate, "audit-repair": cmd_audit_repair, "close-gates": cmd_close_gates, "publish": cmd_publish}[a.cmd](a)


if __name__ == "__main__":
    main()
