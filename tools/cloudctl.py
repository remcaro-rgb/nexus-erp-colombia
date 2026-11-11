#!/usr/bin/env python3
"""cloudctl — wave/runbook control for Nexus ERP on Claude Code cloud sessions (edition 3.0.4-claude-cloud).

Python 3.11+ standard library only. Run from the repository root (the git top level is used).

  validate-plan                          plan-cloud.json: acyclic, waves, owned paths disjoint per wave, every 2.2.0 task/feature/test mapped once
  show          --task T                 the task brief (paths, tests, features, docs to read)
  worker-start  --task T [--resume]      precondition + branch + attempt checks for a worker session (BLOCKED-PRECONDITION, exit 3, if not allowed)
  worker-check  --task T                 changed paths vs owned paths, protected paths, secret scan
  worker-report --task T --status DONE|BLOCKED [--reason R] [--no-push]   docs/run/reports/T.json as its own commit, then push the branch
  integrate     --wave N [--test-cmd C] [--ids-cmd C]   (C may use $NEXUS_IDS) scan reports, verify, merge, re-run tests, accept/reject, commit state (local; integrator only)
                                         --test-cmd runs after EACH merge (default since 3.0.3: the CI-parity gate `python3 tools/parity_gate.py`);
                                         a merge after which it fails is reset and the task gets REWORK with the command's last output as reason
  close-gates   --wave N [--ids-cmd C]                  gates/RC whose requirements are accepted: re-run every test due, gate-check, commit state
  publish       --wave N [--no-push]                    audit + fast-forward push of main (and the RC branch) + print the next paste list
  audit-repair  [--no-push]              revert commits that reached main without the integrator (acknowledged by trailer) and push
  audit-main                             first-parent history of origin/main: only the seed commit and integrator commits
  next                                   the exact prompt files to paste next
  status                                 re-render docs/run/STATUS.md from STATE.json
  wave-tests    --wave N                 test IDs owned by the tasks of wave N
"""
import argparse, datetime as dt, fnmatch, glob as globmod, hashlib, json, os, re, shutil, subprocess, sys, time

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
# 3.0.1 maintenance paths (historical: the 3.0.1 update touched only these). Since 3.0.2 audit-main accepts only PINNED maintenance commits.
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


def is_shallow():
    return git("rev-parse", "--is-shallow-repository", check=False).stdout.strip() == "true"


def fetch_retry():
    """git fetch with retries. 3.0.4: a shallow clone (cloud VMs may clone with limited depth) is unshallowed first: audit-main walks the
    whole first-parent history back to the pinned seed, and gitleaks treats the shallow boundary commit as all-new (false leaks)."""
    last = ""
    for wait in (0, 2, 4, 8, 16):
        if wait:
            time.sleep(wait)
        args = ["fetch", "origin", "--prune"] + (["--unshallow"] if is_shallow() else [])
        p = git(*args, check=False)
        if p.returncode == 0:
            if is_shallow():
                die("BLOCKED-PLATFORM: the clone is still shallow after `git fetch --unshallow origin`; audit-main needs the full history", 4)
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
        for ref in git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/claude/").splitlines():
            if ref == f"origin/{br}":
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


# Owner maintenance commits (trailer Nexus-Seed:) are accepted ONLY when pinned here. A trailer is not authentication: the audit detects
# unauthorized writes to main (any session using the owner's GitHub account can push); REPAIR-audit-main reverts them.
PINNED_SEED_COMMITS = {
    "seed": "19a77d0840ac2d56d3d216ced5254a804f437f58",       # Nexus ERP seed 3.0.0-claude-cloud (initial seed of remcaro-rgb/nexus-erp-colombia)
    "update301": "da85d6c90d49f60f38bb0c3a99f9ba5005e33da5",  # Nexus prompts update 3.0.1-claude-cloud
}
UPDATE_302_TRAILER = "Nexus-Seed: 3.0.2-claude-cloud"
UPDATE_303_TRAILER = "Nexus-Seed: 3.0.3-claude-cloud"
# The 3.0.2 tightening commit is pinned by CONTENT (its SHA cannot be known before it exists): exactly one first-parent commit after update301
# with the exact trailer line, whose changed paths are all listed below (plus tools/cloudctl.py) and whose tree holds exactly these blobs.
# tools/cloudctl.py is pinned by the sha256 of its own text with the PIN block (between the markers) removed.
# >>> PIN-3.0.2
PIN_302_FILES = {
    'CLAUDE.md': 'f9f68499646f4336435d514fe420623c6bb0717e',
    'docs/cloud-prompts/CLOUD-PLATFORM-NOTES.md': 'ce5fb111f2c14d53e0ed71fc9a3c1933dd046aa7',
    'docs/cloud-prompts/INTEGRATOR-after-W01.md': '94d45ea02cc8609355111e0aa49d2d3862f8911a',
    'docs/cloud-prompts/INTEGRATOR-after-W02.md': 'd2271e4c1a34cec89833b19aac98a9ab8e0bf4ca',
    'docs/cloud-prompts/INTEGRATOR-after-W03.md': '5c5ef4c5a405a90d21e4f14fbf39dcd7a2e3cc5f',
    'docs/cloud-prompts/INTEGRATOR-after-W04.md': '49dc5f5d451a80cc8765750f9a01191cd13c9bd9',
    'docs/cloud-prompts/INTEGRATOR-after-W05.md': '480b6db89c28c006210ba097de2f6d23c77c2e6a',
    'docs/cloud-prompts/INTEGRATOR-after-W06.md': '330d08c2326340febf672688f694aa5990f37ea2',
    'docs/cloud-prompts/INTEGRATOR-after-W07.md': '4984afd0ed86e56030df08ee8ae575f263ab90fa',
    'docs/cloud-prompts/INTEGRATOR-after-W08.md': 'be0bf3e91dbfb4e6307da2422c89cf7c19a1e873',
    'docs/cloud-prompts/INTEGRATOR-after-W09.md': '21e7925a99cce127401ca5d0fe3c501b532bcbcb',
    'docs/cloud-prompts/INTEGRATOR-after-W10.md': 'd872dc1263542b61731c9801cfe54c41e071f8bf',
    'docs/cloud-prompts/REPAIR-audit-main.md': '2f4d288592a410d53a6921768a94102213b34366',
    'docs/cloud-prompts/RETRY-DEV.md': 'fb5e94941b85e20636d54df55e5dfdbb01d7a0e7',
    'docs/cloud-prompts/RETRY-P01.md': 'b1ed2ac1b098306a307505a817e7c07d98f76442',
    'docs/cloud-prompts/RETRY-P02.md': '231d7589f69ca6a68e90fcdec39b96f7724d5654',
    'docs/cloud-prompts/RETRY-P03.md': 'f90f1d9661d787f23d05c09fc578712b2f367a34',
    'docs/cloud-prompts/RETRY-P04.md': '8d8ea9a032a91c57eebd59cb4a2cb6de96574525',
    'docs/cloud-prompts/RETRY-P05.md': '6bba21fa162856f56c65cb6b3c1cd80dd0fe7907',
    'docs/cloud-prompts/RETRY-P06.md': 'b08d790ad35f8d6193b1b9d7bc6f990c400ba8e8',
    'docs/cloud-prompts/RETRY-P07.md': '933c8ad2f7cc4ebef180f4f87bc962e3df709daa',
    'docs/cloud-prompts/RETRY-P08.md': 'd1561caa8863dfabd3e3f963d2d7e2fd6cba7b90',
    'docs/cloud-prompts/RETRY-P09a.md': '4de886ad5213df58d7d7aa15b37ca2825efb2c9f',
    'docs/cloud-prompts/RETRY-P09b.md': '9d5282eb7d7408efaa48364ab23e053746706dd9',
    'docs/cloud-prompts/RETRY-P09c.md': '3f064c2a435714dc0393442570c584dbeeb80517',
    'docs/cloud-prompts/RETRY-P10a.md': '82cd8d18246ff793b7c73f5471667361ebd2d20d',
    'docs/cloud-prompts/RETRY-P10b.md': '58c7c01590ada761b485f8260a1dd351b6c66129',
    'docs/cloud-prompts/RETRY-P11a.md': '7b2c13d5c60615428c8fd72bead1acdcff225439',
    'docs/cloud-prompts/RETRY-P11b.md': '9847423f14bef2885db6b0ff589fc7a89bd7c2dc',
    'docs/cloud-prompts/RETRY-P12.md': 'a96a359471dca59f53468544a805852e1279a0c8',
    'docs/cloud-prompts/RETRY-P13a.md': '794d41cbc8a885aad8bf5b5cc17ea856cf67830e',
    'docs/cloud-prompts/RETRY-P13b.md': '49aa88bbeaa92ebf47891af9fca1afa4884c6cdb',
    'docs/cloud-prompts/RETRY-P14.md': '2b2110827cf454dae618b31e9d8ef86d7acbe47f',
    'docs/cloud-prompts/RETRY-P15.md': 'de5b7b3ebbc77e88d01574368075992c955c241b',
    'docs/cloud-prompts/RETRY-P16a.md': '748ff3d686db28ff63b56947d082ac0ddb522185',
    'docs/cloud-prompts/RETRY-P16b.md': 'd8914f29401e3e636ff83aa35ab6aaef5132be0d',
    'docs/cloud-prompts/RETRY-P17a.md': '1559c0441809548ff2233d53d2ab1ff8329f480e',
    'docs/cloud-prompts/RETRY-P17b.md': 'b4e9a122041481c775435d38815860f0e55d40c3',
    'docs/cloud-prompts/RETRY-P17c.md': '87a8718e584c75e7e1beff83abb9dfdd861c955b',
    'docs/cloud-prompts/RETRY-P18.md': '56bfe100b3486583ac95054ce2a272d104eb0ae6',
    'docs/cloud-prompts/RETRY-P19.md': '56339b3ea7434d44d8754c2b2f5c0b1b7a59260e',
    'docs/cloud-prompts/RETRY-P20.md': 'd7c2f905936c091cff54dc5d3728e3ed278f740a',
    'docs/cloud-prompts/RETRY-P21.md': '1e2d268926759e72b6289e53b129df83bdd39b08',
    'docs/cloud-prompts/RETRY-P22a.md': '8ee8f482187ac0333ceb047c6a92a817d483ddc5',
    'docs/cloud-prompts/RETRY-P22b.md': '4613904ce3dc044aa35bae079df2d62fb18e1e67',
    'docs/cloud-prompts/RETRY-P22c.md': '9765fdc3ad02132249c7932e499af4563170db55',
    'docs/cloud-prompts/RETRY-P22d.md': 'f6c8313e9db813958ec27a453e3ad9c6912b578e',
    'docs/cloud-prompts/RETRY-P23.md': '44cbea81fe227bde576553f737dcbc21b683ff2a',
    'docs/cloud-prompts/RUNBOOK.md': '7212a0dfb34b3eb40f0179de6678f5596c5635c9',
    'docs/cloud-prompts/START-HERE.md': '852bf353ef64de4296485d789f3d612ef6e31c69',
    'docs/cloud-prompts/W01-S1-P01-foundation-kernel.md': '874a8371ac897a8185b85287eec72650d52f9289',
    'docs/cloud-prompts/W01-S2-P02-contract-freeze.md': '787ed3fc148f22a81c56c2a083379e77227a89bf',
    'docs/cloud-prompts/W01-S3-P03-devops-ci.md': '3ec1c92dbfbb32f15de97d84bb43633fe5d0921d',
    'docs/cloud-prompts/W02-S1-P04-iam-rls-field-security.md': '11614462c294dd07abdf82e51625c652c720eefc',
    'docs/cloud-prompts/W02-S2-P05-ledger-posting-engine.md': '07d31105f362e72263edf3b543ef97e316922b50',
    'docs/cloud-prompts/W02-S3-P07-workflow-audit-scheduler.md': '27904ba5c54635e47c0e97a044e7bc96a4f496ff',
    'docs/cloud-prompts/W02-S4-P08-ux-shell-design-system.md': '6261d77fe576919d22b05dc2af33002c18d3cf84',
    'docs/cloud-prompts/W02-S5-P06-tax-localization-co.md': '0249650cc1af6eaa00c49f321ae58f41e428eaa0',
    'docs/cloud-prompts/W02-S6-P22a-qa-harness.md': '5c78f7fe39fe32bebb53425c7e45194c0e219b70',
    'docs/cloud-prompts/W03-S1-P15-dian-einvoice-gateway.md': 'b0939f456e460e85127e9e1f6db0ca6ed01c8267',
    'docs/cloud-prompts/W03-S2-P09a-inventory-core.md': '2c41df02dca724c9d83148d4262631b71cd6e2d0',
    'docs/cloud-prompts/W03-S3-P13b-payroll-formulas.md': 'd9e83f1d82023d33c0eca795ded466cba7dfd784',
    'docs/cloud-prompts/W03-S4-P11a-purchasing-ap-core.md': '9ba32c2cd5dd35a6f4325b6bed03af3eab3e1b85',
    'docs/cloud-prompts/W03-S5-P13a-hr-timekeeping-employee-portal.md': '6dc2d42a59b0419e3c259515f6a449a461811bb3',
    'docs/cloud-prompts/W03-S6-P14-fixed-assets.md': '17c4fafce12a648d9b31cea0ca5c6c6a0dc2803b',
    'docs/cloud-prompts/W03-S7-P22b-qa-cross-module.md': 'dc9c68a7fc5532f7e36f00a077fa609158b3376b',
    'docs/cloud-prompts/W04-S1-P10a-sales-ar-crm-core.md': 'f2943467d8f86ef77062c6eeb6442d9477a9f911',
    'docs/cloud-prompts/W04-S2-P11b-requisitions-supplier-portal.md': '31076e2eeabc481db1b9f91c0329ba7635debebb',
    'docs/cloud-prompts/W04-S3-P09c-inventory-bom-transformations.md': '09e4e29dc59cf9b0351a4c6d4cfd5af01bb0c143',
    'docs/cloud-prompts/W04-S4-P09b-inventory-barcodes-issues.md': '05b44dfae611f446de1a08908948c7ea670233d1',
    'docs/cloud-prompts/W05-S1-P16a-treasury-vouchers.md': '2121301b8be4858dfb2681b748d82004afe87ab3',
    'docs/cloud-prompts/W05-S2-P12-pos.md': '03d0d51a798dc979f1e45487a68fba790f7d24a4',
    'docs/cloud-prompts/W05-S3-P10b-customer-portal.md': 'f55045de6acf6ee26ab353178ba26d22980c1a6c',
    'docs/cloud-prompts/W05-S4-P16b-intercompany-consolidation.md': '209e30eb8d05be593e608a3e99484f9b9f1941c2',
    'docs/cloud-prompts/W06-S1-P18-bulk-import-export.md': '0d3058f51046c3c5368723e58b54985ad72325e2',
    'docs/cloud-prompts/W06-S2-P22c-qa-leak-tests.md': '0b836d29ff81816bb7b232e59692c012200d4f67',
    'docs/cloud-prompts/W06-S3-P17a-reporting-semantic-documents.md': 'dcb209197b4eedbd6b5221aca4676123947b3ebb',
    'docs/cloud-prompts/W07-S1-P19-ai-business-agent.md': '5f7262fc225a02ee6be29b697418b1e0fa5d09aa',
    'docs/cloud-prompts/W07-S2-P17b-financial-statements.md': 'f91987f1f4df7952edd9dd051bf0bdd9628bcee9',
    'docs/cloud-prompts/W07-S3-P17c-exogena.md': '4d58d417cdc369691d2a58fae62642d465b465fc',
    'docs/cloud-prompts/W08-S1-P21-ai-onboarding-agent.md': '7d5b50cf64b0fd0b36c838433b8e60335338ef59',
    'docs/cloud-prompts/W08-S2-P20-mcp-server.md': 'f2f6f6abbc9eb235e6877239c25766900cbabb55',
    'docs/cloud-prompts/W09-S1-P23-compliance-evidence.md': 'b7dba469a2314d234d81428dd102e820afff51a3',
    'docs/cloud-prompts/W09-S2-P22d-qa-e2e-a11y-perf.md': '8be6e1adc1b50c9f375d421980e84224b2e0f3b0',
    'docs/cloud-prompts/W10-S1-DEV-dev-runtime-local-run.md': '21ffe0b1d072ecbaf65914c8a7a62b59feda0669',
    'tools/README.md': '65e0d8d89db1410cf920ee12a3c23c4a85fa43a4',
    'tools/test_cloudctl.py': 'd6d87cd353a17fe291b141d4932d09a309488aad',
}
PIN_302_CLOUDCTL_SHA256 = '0178f7cd1ed983aa56518d8e8a9a09aed096a3164f5d2b41be53d0708705e2cf'
# <<< PIN-3.0.2
# The 3.0.3 maintenance commit (CI-parity gate, dev UI login, prompts) is pinned the same way: exactly one first-parent commit after the
# accepted 3.0.2 commit (or after the 3.0.2 seed of a fresh repository) with the exact trailer line, whose changed paths are all listed
# below (plus tools/cloudctl.py) and whose tree holds exactly these blobs; tools/cloudctl.py by the sha256 of its text without the PIN-3.0.3
# block. The PIN-3.0.2 block above stays byte-identical so the 3.0.2 commit keeps validating against its own tree.
# >>> PIN-3.0.3
PIN_303_FILES = {
    'docs/cloud-prompts/CLOUD-PLATFORM-NOTES.md': 'ce5fb111f2c14d53e0ed71fc9a3c1933dd046aa7',
    'docs/cloud-prompts/INTEGRATOR-after-W01.md': '73d06b9ac38398ec25dfbecd4557eff1949cb06b',
    'docs/cloud-prompts/INTEGRATOR-after-W02.md': 'b62090ce862153f84c84580be86e17a3837bfb5e',
    'docs/cloud-prompts/INTEGRATOR-after-W03.md': '676652cec5546f2e8c4f6b6d0df9878888b931a5',
    'docs/cloud-prompts/INTEGRATOR-after-W04.md': '04494a5dc5b28c6a1882bbb6c368e0eca31c7d3d',
    'docs/cloud-prompts/INTEGRATOR-after-W05.md': '4e26c328ceea3836974187bf9cf1c33a6232f5dc',
    'docs/cloud-prompts/INTEGRATOR-after-W06.md': '31e17e7a11c3851494673106c05bf736949479dd',
    'docs/cloud-prompts/INTEGRATOR-after-W07.md': '9e05ca3988ce6b513cff8db341e2ce7d559499f5',
    'docs/cloud-prompts/INTEGRATOR-after-W08.md': 'c06b54c5ed47eab29f401de9cbaabdaa4347dd85',
    'docs/cloud-prompts/INTEGRATOR-after-W09.md': '59efd879ff6ba776d65645f7bb0f6346260d24e8',
    'docs/cloud-prompts/INTEGRATOR-after-W10.md': '854b9bb646deef57ab9289e245474a75987110cb',
    'docs/cloud-prompts/PARALLELISM.md': '9d98a50d1249b2cfb924bcf8134a0a0f1f388e3a',
    'docs/cloud-prompts/PASTE-ORDER.md': '27139cedfd2934965023904f8bc8f37ede99e62d',
    'docs/cloud-prompts/REPAIR-audit-main.md': 'c60e5074319c056a1d8f9e3f6237ffad0ef1a0df',
    'docs/cloud-prompts/RETRY-DEV.md': '02a590eb4edb90fc06ea46d7691de14c2017c48d',
    'docs/cloud-prompts/RETRY-P01.md': 'ec4ca8786d9f832644d813fd9f4f4a0f64aab181',
    'docs/cloud-prompts/RETRY-P02.md': '81b735afd3500b9b7ef32f25bb1f2b9be1d4edfc',
    'docs/cloud-prompts/RETRY-P03.md': '3b0f42a50ec549f6774efe6fc2be6f85ebaaed0f',
    'docs/cloud-prompts/RETRY-P04.md': 'eb4aa4ade3ae03fbf6a338096d9c491654aeceb7',
    'docs/cloud-prompts/RETRY-P05.md': '9c9c65e75c7868e6378a98d3dcf2f021914e370a',
    'docs/cloud-prompts/RETRY-P06.md': 'bbf1e4b204d5c9088d32ee6cf796c1b814b0213f',
    'docs/cloud-prompts/RETRY-P07.md': 'c16ef9b7ef9fc490b51c1a8032a2bcac00c0ef47',
    'docs/cloud-prompts/RETRY-P08.md': '259061341c78ed4af89f6e19a3c039efc98c58a5',
    'docs/cloud-prompts/RETRY-P09a.md': '73181defddda1b6550a519b42031865f5d181ca4',
    'docs/cloud-prompts/RETRY-P09b.md': '8c3456dfe4e10c73abfe86d27368fd17727f680c',
    'docs/cloud-prompts/RETRY-P09c.md': 'eba9448cd91a33c6fef2948e0d003d2aaa1c585a',
    'docs/cloud-prompts/RETRY-P10a.md': 'b05b92af6a7351716191a35bbf37a7bf4a20566f',
    'docs/cloud-prompts/RETRY-P10b.md': '480e13e525b2209d1797b100a9b0b0a8ba909144',
    'docs/cloud-prompts/RETRY-P11a.md': '2df40c4b97c7478e5400288e582a39881668a612',
    'docs/cloud-prompts/RETRY-P11b.md': 'cbcc96149f1604ac418a2afa831ff91a00d9bcac',
    'docs/cloud-prompts/RETRY-P12.md': '7e7227e0d7167852326158ddad5370946fda5460',
    'docs/cloud-prompts/RETRY-P13a.md': 'aa986aac3ba39c75cc3900aed99f71bf8c5e13ba',
    'docs/cloud-prompts/RETRY-P13b.md': 'ff9a13502f07eb585fd5079d9d8a3a67fa382b27',
    'docs/cloud-prompts/RETRY-P14.md': 'a9c18ba61ba070ea4922b79e62ea0159f6f3965b',
    'docs/cloud-prompts/RETRY-P15.md': '3b8ea7b081b8f38b8c1ed366a51e9b6b047896bb',
    'docs/cloud-prompts/RETRY-P16a.md': '925404919585f94219e95a3a8495ba26718444a2',
    'docs/cloud-prompts/RETRY-P16b.md': '058cd479511854546670efde73b041105dd7d7cd',
    'docs/cloud-prompts/RETRY-P17a.md': 'f40dcd7bbe002336ba7a9f841a21f22505e39aa3',
    'docs/cloud-prompts/RETRY-P17b.md': '1c6dc43a0f689ffee408fe36fe1bcba8bbf968d3',
    'docs/cloud-prompts/RETRY-P17c.md': 'c4dbdf8fa83460f7cc18e14314e18ab523f97aef',
    'docs/cloud-prompts/RETRY-P18.md': '9e3e272629df020d43e8485cc46a98e6e637e891',
    'docs/cloud-prompts/RETRY-P19.md': '4944901481da2054eb87d5b160dc42b5e9397a34',
    'docs/cloud-prompts/RETRY-P20.md': 'e17542730e715bbd5b0d21b248b32380e675500a',
    'docs/cloud-prompts/RETRY-P21.md': 'f7949d9f976f8ab450156b44f0a19ebf62fec921',
    'docs/cloud-prompts/RETRY-P22a.md': '00c430c3bba2fae6f1cf9c98ffc29b6d72288826',
    'docs/cloud-prompts/RETRY-P22b.md': '58b9bc7e5f678ed0dce9e0aa472227cba89b19f8',
    'docs/cloud-prompts/RETRY-P22c.md': '8b635f4402844c49c56dffac7b13ded9e279cc30',
    'docs/cloud-prompts/RETRY-P22d.md': 'cce71d4b5075f9d03ff873f5f0b5045467d9656d',
    'docs/cloud-prompts/RETRY-P23.md': 'b0defd9b978edf2fd23737b6a85d07c1b65c4f80',
    'docs/cloud-prompts/RUNBOOK.csv': 'd69d331534b158ec5f790b99e80d5981bb7fa626',
    'docs/cloud-prompts/RUNBOOK.md': '76cf5b2d5bca2d3c97bad6615904f2887739094c',
    'docs/cloud-prompts/START-HERE.md': '56c772e4092b3ec2e99638fad48c19525401d59e',
    'docs/cloud-prompts/W01-S1-P01-foundation-kernel.md': '49d18cd62bb21899bf1b6f9e4207cc5604906dba',
    'docs/cloud-prompts/W01-S2-P02-contract-freeze.md': '957e84b6a5c502e9dc897f5ea2ba717e6db06f81',
    'docs/cloud-prompts/W01-S3-P03-devops-ci.md': '33ebb9ee43b232721c00c9bceec7fdebc2aa20a7',
    'docs/cloud-prompts/W02-S1-P04-iam-rls-field-security.md': 'b74d6884ecf5b74d5b6c375d536a92562d31a268',
    'docs/cloud-prompts/W02-S2-P05-ledger-posting-engine.md': '95e7436a9b92c8d22205fa41b22e4cf31e356405',
    'docs/cloud-prompts/W02-S3-P07-workflow-audit-scheduler.md': '9140b874ff725cae304f05d9fcd74b29fc8a4a09',
    'docs/cloud-prompts/W02-S4-P08-ux-shell-design-system.md': '24f7aebd5c678cfa102c1833bbf289de3f362f32',
    'docs/cloud-prompts/W02-S5-P06-tax-localization-co.md': '9578c77c2f9a8b6678ca69b2f37171f674199675',
    'docs/cloud-prompts/W02-S6-P22a-qa-harness.md': 'ab206787caff44589df84cf2248c0a7b96438f02',
    'docs/cloud-prompts/W03-S1-P15-dian-einvoice-gateway.md': '8992c82b3da69519f4f7b361e787e0f226c36012',
    'docs/cloud-prompts/W03-S2-P09a-inventory-core.md': 'fbaa3fa4a6ac6fb79d63195582a6602c985cc055',
    'docs/cloud-prompts/W03-S3-P13b-payroll-formulas.md': 'faa0548890389c85ca54292bcc27f297cf0a3bb5',
    'docs/cloud-prompts/W03-S4-P11a-purchasing-ap-core.md': '62f160b51f122eba86b7b0f77df88b10d6858567',
    'docs/cloud-prompts/W03-S5-P13a-hr-timekeeping-employee-portal.md': 'a7d6bc121100ac84d7e97d94be3acade80b7c439',
    'docs/cloud-prompts/W03-S6-P14-fixed-assets.md': '5ddd52c63a8064c9b83bf31f9aa260c49099cae4',
    'docs/cloud-prompts/W03-S7-P22b-qa-cross-module.md': '54dc3eb0da902632764382a5659aa65d279f1c55',
    'docs/cloud-prompts/W04-S1-P10a-sales-ar-crm-core.md': '854280f8e6be5cbac9b48eff1ecc424bc3aed073',
    'docs/cloud-prompts/W04-S2-P11b-requisitions-supplier-portal.md': '338c7a0e3a1bce5cf7f8d7e3a5b2e11bdf4522ed',
    'docs/cloud-prompts/W04-S3-P09c-inventory-bom-transformations.md': '2ad82fd1199bd92f19723e3a201670af72114442',
    'docs/cloud-prompts/W04-S4-P09b-inventory-barcodes-issues.md': 'ccd5dbd7c775799eb80133118919731b1413c4f4',
    'docs/cloud-prompts/W05-S1-P16a-treasury-vouchers.md': '8e8188aa9e2f641f8a9d6f49f57761f409bb949c',
    'docs/cloud-prompts/W05-S2-P12-pos.md': '6c832b1b206d367ee73c83a807eab095e3c8a1c8',
    'docs/cloud-prompts/W05-S3-P10b-customer-portal.md': 'b3cf37c171094bdf7d556f715d2a73ca7d976d03',
    'docs/cloud-prompts/W05-S4-P16b-intercompany-consolidation.md': '899e31a942dc3e755fee4f2896fea65ecc396244',
    'docs/cloud-prompts/W06-S1-P18-bulk-import-export.md': '54615ab56bf00956916646ff63ed43b65f77c316',
    'docs/cloud-prompts/W06-S2-P22c-qa-leak-tests.md': 'a649996abe61d856e3cfc24b173665a1f30cb22c',
    'docs/cloud-prompts/W06-S3-P17a-reporting-semantic-documents.md': '15c22c026dada9c32461feb8d7f4ea4af42676d1',
    'docs/cloud-prompts/W07-S1-P19-ai-business-agent.md': '6842ab75731745edc7380e35bb34b666bf0a93d8',
    'docs/cloud-prompts/W07-S2-P17b-financial-statements.md': '200f8048fe7e9d33a4b5051fdd59e8011fe2dce5',
    'docs/cloud-prompts/W07-S3-P17c-exogena.md': '7c5235573389b6fcdac9f9dcf5b26029f8d7a693',
    'docs/cloud-prompts/W08-S1-P21-ai-onboarding-agent.md': '9660d07bc72be834a29a95ebba75a30fc1f08e7e',
    'docs/cloud-prompts/W08-S2-P20-mcp-server.md': 'b02ceea3249f683a9d7d19b60dbce31d47db1614',
    'docs/cloud-prompts/W09-S1-P23-compliance-evidence.md': 'f937ef2bf28f65619f94f7642c7180ee8d69b358',
    'docs/cloud-prompts/W09-S2-P22d-qa-e2e-a11y-perf.md': 'a9a9acda8e74b345ac1073eaeb271d1aa005cbdf',
    'docs/cloud-prompts/W10-S1-DEV-dev-runtime-local-run.md': 'ad15c1caec523e22c571614ea06d9a9457fd4ad9',
    'tools/README.md': '98543c5129cfdbd772a168f1dd975d4271282a9a',
    'tools/dev_login.py': '2aa4ec55eb8de0bd6a2cfb0ceae325295a9f1dcf',
    'tools/parity_gate.py': '0ce2eabd09eeb9835c85a716fd464ba15a1d88b6',
    'tools/test_cloudctl.py': '7e7b44495237d6d6c9cc6d44d5846bd6fbd05bfe',
    'tools/ui_snapshot.cjs': '11b97ec8f1937c2db84a3562b892d4e54e0f1b33',
    'tools/ui_snapshot.py': '47ef2f19e811c43813cbfd7241d3ba774ef1c1bb',
}
PIN_303_CLOUDCTL_SHA256 = '8ae5e552fcc70a4e8613922d7c9a54d2e4b26c5b294d87d8a31351a69ddd8768'
# <<< PIN-3.0.3
# The 3.0.4 maintenance commit (collect_results on Playwright's artifacts dir, unshallow + shallow refusal, parity-gate env and output order,
# dev_login field parsing, integrator Step 1 unshallow/warm-up/DATABASE_URL) is pinned the same way: exactly one first-parent commit after
# the accepted 3.0.3 commit with the exact trailer line, whose changed paths are all listed below (plus tools/cloudctl.py) and whose tree
# holds exactly these blobs; tools/cloudctl.py by the sha256 of its text without the PIN-3.0.4 block. PIN-3.0.2/3.0.3 stay byte-identical.
UPDATE_304_TRAILER = "Nexus-Seed: 3.0.4-claude-cloud"
# >>> PIN-3.0.4
PIN_304_FILES = {
    'docs/cloud-prompts/INTEGRATOR-after-W03.md': '993c5c0baf927919ca446004e4d9b04494a26aba',
    'docs/cloud-prompts/INTEGRATOR-after-W04.md': 'b5580719aafa4aa6b436307e406d9008da9285fd',
    'docs/cloud-prompts/INTEGRATOR-after-W05.md': 'f1909b2f5e065861193ba6f5a84b8bd3040a4dc3',
    'docs/cloud-prompts/INTEGRATOR-after-W06.md': '8c873c3437f1a2f7d2cf3d1140d3a3e95e3e970d',
    'docs/cloud-prompts/INTEGRATOR-after-W07.md': 'a842d122a51815a1aa951cfedae17dccf7643206',
    'docs/cloud-prompts/INTEGRATOR-after-W08.md': 'd64442431f778d2547f5c94d0c3c1e5558b7fb76',
    'docs/cloud-prompts/INTEGRATOR-after-W09.md': '451776128ca7e6b02a52635fc02dbf03bef0ad4f',
    'docs/cloud-prompts/INTEGRATOR-after-W10.md': 'a8147c43faac0360d0c9e61ca83c214c0ae1107a',
    'tools/README.md': '88de29ad2ab7caa08fbf58dc4746033205d1a933',
    'tools/dev_login.py': '0b0f0e56d36cb0c57f486884178d7579f8534c11',
    'tools/parity_gate.py': '6f125d6da4943ff4c71df21b699376642a093950',
    'tools/test_cloudctl.py': 'ee0e868e345eaae3fb6815cc9aa018f0e8dcee67',
}
PIN_304_CLOUDCTL_SHA256 = '939f5a7a56e652e1d7e902f56fff3bcb4da708a8013adce9057bc6883d308623'
# <<< PIN-3.0.4
TOOLING_PATHS = ["tools/**", "CLAUDE.md", ".claude/**", "docs/spec/**", "docs/prompt-pack/**", "docs/cloud-prompts/**", "scripts/cloud/**", ".gitignore"]


def pinned_shas():
    pins = dict(PINNED_SEED_COMMITS)
    extra = os.environ.get("NEXUS_REHEARSAL_PINS", "")
    if os.environ.get("NEXUS_REHEARSAL") and extra:  # local rehearsals only: stand-ins for the real SHAs (role=sha,role=sha)
        for kv in extra.split(","):
            role, _, sha = kv.partition("=")
            if role in pins and re.fullmatch(r"[0-9a-f]{40}", sha):
                pins[role] = sha
    return pins


def normalized_cloudctl_sha256(text, pin="3.0.2"):
    """sha256 of cloudctl.py text without its PIN-<pin> block (the block holds the pins and cannot pin itself)."""
    out, skip = [], False
    for line in text.splitlines(keepends=True):
        if line.startswith(f"# >>> PIN-{pin}"):
            skip = True
        elif line.startswith(f"# <<< PIN-{pin}"):
            skip = False
        elif not skip:
            out.append(line)
    return hashlib.sha256("".join(out).encode("utf-8")).hexdigest()


def blob_at(commit, path):
    p = git("rev-parse", "--verify", "-q", f"{commit}:{path}", check=False)
    return p.stdout.strip() if p.returncode == 0 else None


def changed_vs_first_parent(h):
    return [p for p in git("diff-tree", "--no-commit-id", "--name-only", "-r", "-m", "--first-parent", "--no-renames", h).splitlines() if p]


def tree_matches_302(h):
    if not PIN_302_FILES or not PIN_302_CLOUDCTL_SHA256:
        return False
    if any(blob_at(h, p) != b for p, b in PIN_302_FILES.items()):
        return False
    c = git("show", f"{h}:tools/cloudctl.py", check=False)
    return c.returncode == 0 and normalized_cloudctl_sha256(c.stdout) == PIN_302_CLOUDCTL_SHA256


def is_302_commit(h, body):
    if not re.search(rf"(?m)^{re.escape(UPDATE_302_TRAILER)}\s*$", body):
        return False, "trailer"
    allowed = set(PIN_302_FILES) | {"tools/cloudctl.py"}
    extra = [p for p in changed_vs_first_parent(h) if p not in allowed]
    if extra:
        return False, f"3.0.2 commit touches unpinned paths {extra[:5]}"
    if not tree_matches_302(h):
        return False, "3.0.2 commit content differs from the pinned 3.0.2 files"
    return True, ""


def tree_matches_303(h):
    if not PIN_303_FILES or not PIN_303_CLOUDCTL_SHA256:
        return False
    if any(blob_at(h, p) != b for p, b in PIN_303_FILES.items()):
        return False
    c = git("show", f"{h}:tools/cloudctl.py", check=False)
    return c.returncode == 0 and normalized_cloudctl_sha256(c.stdout, "3.0.3") == PIN_303_CLOUDCTL_SHA256


def is_303_commit(h, body):
    if not re.search(rf"(?m)^{re.escape(UPDATE_303_TRAILER)}\s*$", body):
        return False, "trailer"
    allowed = set(PIN_303_FILES) | {"tools/cloudctl.py"}
    extra = [p for p in changed_vs_first_parent(h) if p not in allowed]
    if extra:
        return False, f"3.0.3 commit touches unpinned paths {extra[:5]}"
    if not tree_matches_303(h):
        return False, "3.0.3 commit content differs from the pinned 3.0.3 files"
    return True, ""


def tree_matches_304(h):
    if not PIN_304_FILES or not PIN_304_CLOUDCTL_SHA256:
        return False
    if any(blob_at(h, p) != b for p, b in PIN_304_FILES.items()):
        return False
    c = git("show", f"{h}:tools/cloudctl.py", check=False)
    return c.returncode == 0 and normalized_cloudctl_sha256(c.stdout, "3.0.4") == PIN_304_CLOUDCTL_SHA256


def is_304_commit(h, body):
    if not re.search(rf"(?m)^{re.escape(UPDATE_304_TRAILER)}\s*$", body):
        return False, "trailer"
    allowed = set(PIN_304_FILES) | {"tools/cloudctl.py"}
    extra = [p for p in changed_vs_first_parent(h) if p not in allowed]
    if extra:
        return False, f"3.0.4 commit touches unpinned paths {extra[:5]}"
    if not tree_matches_304(h):
        return False, "3.0.4 commit content differs from the pinned 3.0.4 files"
    return True, ""


def is_tooling(p):
    return any(match(p, g) for g in TOOLING_PATHS)


def repair_tooling_problem(commits, pos, i, h, acks):
    """An integrator commit may change tooling paths only as an audit repair that restores them exactly: every tooling path changed by the
    commit or by a commit it acknowledges must, at this commit, have the blob it had just before the oldest acknowledged commit that changed it
    (and nothing else may have changed that path in between). Otherwise the ack would hide a tooling change."""
    touched = {p for p in changed_vs_first_parent(h) if is_tooling(p)}
    acked = sorted({pos[x] for x in acks if x in pos and pos[x] > i})
    by_path = {}
    for j in acked:
        for p in changed_vs_first_parent(commits[j][0]):
            if is_tooling(p):
                by_path.setdefault(p, []).append(j)
    if not touched and not by_path:
        return ""
    if touched - set(by_path):
        return f"integrator commit modifies tooling paths {sorted(touched - set(by_path))[:5]}"
    for p, js in by_path.items():
        oldest = max(js)
        for k in range(i + 1, oldest):
            if k not in acked and p in changed_vs_first_parent(commits[k][0]):
                return f"audit-repair cannot restore {p}: a non-acknowledged commit changed it too (operator must repair by hand)"
        before = commits[oldest + 1][0] if oldest + 1 < len(commits) else None
        if before is None or blob_at(h, p) != blob_at(before, p):
            return f"audit-repair commit does not restore tooling path {p} exactly"
    return ""


def audit_main(ref, full=False, explain=None):
    """First-parent history of `ref` after the pinned seed may contain only:
       * integrator commits (trailer Nexus-Integrator:) that do not modify tooling paths (tools/**, CLAUDE.md, .claude/**, docs/spec/**,
         docs/prompt-pack/**, docs/cloud-prompts/**, scripts/cloud/**, .gitignore) — except an audit-repair commit that restores them exactly;
       * the pinned owner maintenance commits (PINNED_SEED_COMMITS, and at most one content-pinned 3.0.2 commit after update301, then one
         3.0.3 commit after it and one 3.0.4 commit after that);
       * commits acknowledged (reverted) by a later audit-repair commit (trailer Nexus-Audit-Ack:).
    Anything else — including any other Nexus-Seed-trailered commit — is reported."""
    why = explain if explain is not None else {}
    if is_shallow():  # 3.0.4: a shallow history cannot be audited (the seed and pinned commits are cut off); never guess
        return ["the clone is shallow: run `git fetch --unshallow origin` (audit-main needs the full first-parent history)"]
    commits = []
    for chunk in git("log", "--first-parent", "--format=%H%x1f%B%x1e", ref).split("\x1e"):
        chunk = chunk.strip()
        if chunk:
            commits.append(chunk.split("\x1f", 1))
    pins = pinned_shas()
    pos = {h: i for i, (h, _) in enumerate(commits)}
    if pins["seed"] in pos:
        base = pos[pins["seed"]]
        fresh = False
    else:
        # a NEW repository seeded from the 3.0.2 seed: its oldest seed commit carries the 3.0.2 trailer and the pinned 3.0.2 content
        seeds = [i for i, (h, b) in enumerate(commits) if SEED_TRAILER in b]
        if not seeds or UPDATE_302_TRAILER not in commits[seeds[-1]][1] or not tree_matches_302(commits[seeds[-1]][0]):
            return [f"pinned seed commit {pins['seed'][:12]} is not in the first-parent history of {ref}"]
        base, fresh = seeds[-1], True
    upd301 = pos.get(pins["update301"])
    valid302 = [] if (fresh or upd301 is None) else [i for i, (h, b) in enumerate(commits[:upd301]) if INTEG_TRAILER not in b and SEED_TRAILER in b and is_302_commit(h, b)[0]]
    accepted302 = max(valid302) if valid302 else None  # the oldest one; any later copy is a forgery
    anchor303 = base if fresh else accepted302  # 3.0.3 is accepted only after 3.0.2 (applied commit or 3.0.2 seed)
    valid303 = [] if anchor303 is None else [i for i, (h, b) in enumerate(commits[:anchor303]) if INTEG_TRAILER not in b and SEED_TRAILER in b and is_303_commit(h, b)[0]]
    accepted303 = max(valid303) if valid303 else None
    anchor304 = accepted303  # 3.0.4 is accepted only after the accepted 3.0.3 commit
    valid304 = [] if anchor304 is None else [i for i, (h, b) in enumerate(commits[:anchor304]) if INTEG_TRAILER not in b and SEED_TRAILER in b and is_304_commit(h, b)[0]]
    accepted304 = max(valid304) if valid304 else None
    bad, acks = [], {}
    def flag(h, reason):
        bad.append(h if full else h[:12]); why[h] = reason
    for i, (h, body) in enumerate(commits[:base]):  # newest first, stops before the seed
        if h in acks:  # reverted by a later audit-repair commit (whose restore was verified above)
            continue
        if INTEG_TRAILER in body:
            my_acks = re.findall(rf"(?m)^{ACK_TRAILER}\s*([0-9a-f]{{40}})", body)
            for x in my_acks:
                acks[x] = h
            problem = repair_tooling_problem(commits, pos, i, h, my_acks)
            if problem:
                flag(h, problem)
            continue
        if SEED_TRAILER in body:
            if not fresh and h == pins["update301"]:
                continue
            if i in (accepted302, accepted303, accepted304):
                continue
            if i in valid302:
                flag(h, "a second 3.0.2 maintenance commit (only one is ever allowed)"); continue
            if i in valid303:
                flag(h, "a second 3.0.3 maintenance commit (only one is ever allowed)"); continue
            if i in valid304:
                flag(h, "a second 3.0.4 maintenance commit (only one is ever allowed)"); continue
            if re.search(rf"(?m)^{re.escape(UPDATE_304_TRAILER)}\s*$", body):
                flag(h, f"unpinned Nexus-Seed commit ({is_304_commit(h, body)[1] if anchor304 is not None else '3.0.4 needs the 3.0.3 update first'})"); continue
            if re.search(rf"(?m)^{re.escape(UPDATE_303_TRAILER)}\s*$", body):
                flag(h, f"unpinned Nexus-Seed commit ({is_303_commit(h, body)[1] if anchor303 is not None else '3.0.3 needs the 3.0.2 update first'})"); continue
            if not fresh and upd301 is not None and i < upd301:
                flag(h, f"unpinned Nexus-Seed commit ({is_302_commit(h, body)[1]})"); continue
            flag(h, "unpinned Nexus-Seed commit"); continue
        flag(h, "not an integrator commit")
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
    for ref in git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/claude/").splitlines():
        # only worker branches (claude/*); dependabot/*, release/* and any other branch are never candidates
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


def clear_report_dir(d):
    """Empty reports/junit or reports/playwright before a run. Playwright's outputDir (reports/playwright/artifacts: traces, videos) is a
    DIRECTORY, so entries are removed recursively (3.0.4: os.remove on it raised IsADirectoryError in the second integrate round)."""
    for f in globmod.glob(os.path.join(ROOT, d, "*")) + globmod.glob(os.path.join(ROOT, d, ".*")):
        if os.path.isdir(f) and not os.path.islink(f):
            shutil.rmtree(f)
        else:
            os.remove(f)


def collect_results(ids, commit, out_rel, log, ids_cmd):
    for d in ("reports/junit", "reports/playwright"):
        clear_report_dir(d)
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


# 3.0.3: after each merge the integrator runs the CI-parity gate (the four GitHub Actions jobs), not only `make ci`, so a task whose merge
# turns policy / mypy / tsc / compose red is REWORK instead of ACCEPTED. Repositories without the gate tool keep `make ci`.
DEFAULT_TEST_CMD = "python3 tools/parity_gate.py" if os.path.isfile(os.path.join(ROOT, "tools", "parity_gate.py")) else "make ci"


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
                    what = "CI parity (tools/parity_gate.py)" if "parity_gate.py" in a.test_cmd else f"`{a.test_cmd}`"
                    dropped.append((tid, f"{what} failed after merging it: {tail.strip().splitlines()[-1][-300:] if 'parity_gate.py' in a.test_cmd and tail.strip() else tail[-300:]}"))
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
    s.add_argument("--test-cmd", default=DEFAULT_TEST_CMD); s.add_argument("--ids-cmd", default='make test-ids IDS="$NEXUS_IDS"')
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
        why = {}
        bad = audit_main(f"origin/{load_plan()['default_branch']}", explain=why)
        if bad:
            for h, r in why.items():
                print(f"  {h[:12]}: {r}")
        print("AUDIT-OK" if not bad else f"AUDIT-FAILED {bad}  (paste prompts/REPAIR-audit-main.md)"); sys.exit(0 if not bad else 5)
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
