#!/usr/bin/env python3
"""parity_gate — the integrator's CI-parity gate (edition 3.0.4-claude-cloud). Integrator-only; workers never edit it (they may run it).

  python3 tools/parity_gate.py [--final]

Runs on the current HEAD exactly what GitHub Actions runs: `bash scripts/ci/ci-parity.sh` with all four jobs — policy (forbidden patterns,
ADR-004 static checks, compose lint, shellcheck, secret scans, semgrep, dependency audit, licenses), p03-tests, make-ci (`make ci` +
makemigrations + mypy over the whole backend/ + tsc/vitest/build of frontend/) and compose-startup (dev stack + seed + health + DR drill) —
after installing the locked dependencies (`uv sync --frozen`, `pnpm install --frozen-lockfile`). Without scripts/ci/ci-parity.sh (early
waves) it runs `make ci`.

* `cloudctl integrate` runs it after EACH merge (3.0.3 default `--test-cmd`): a merge after which parity fails is reset and that task becomes
  REWORK. The reason is the gate's LAST line, e.g.
  `PARITY-GATE FAILED (merge of P08) 1/4 make-ci: mypy 12 err backend/ledger(P05); tsc 14 err frontend/src/features/ledger(P05) …`
  — the failing job(s), the top directories of the failing files and the plan task that owns them.
* On an integration branch (nexus-integrate-wNN) the failing jobs' log excerpts (secrets scrubbed) are written to
  docs/run/evidence/WNN/parity-gate/<TASK>-<k>.log; cloudctl commits them with the state, so the RETRY session can read the real error.
* `--final` (before publish): OK at once when HEAD's code (every top-level entry except docs/) equals a tree this gate already passed in this
  VM (/tmp/nexus-parity-gate/ok-trees) or equals origin/main's code (nothing but docs/ would be published); otherwise the full gate runs.
* Prints no secret values (output and excerpts are scrubbed); writes nothing to stderr. Exit 0 = OK, 1 = FAILED, 2 = usage.
"""
import argparse, datetime as dt, hashlib, json, os, re, subprocess, sys

ROOT = os.environ.get("NEXUS_ROOT") or subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip() or os.getcwd()
STATE_DIR = os.environ.get("NEXUS_PARITY_STATE_DIR", "/tmp/nexus-parity-gate")
JOBS = ["policy", "p03-tests", "make-ci", "compose-startup"]
MAX_LAST = 290  # cloudctl keeps the last line (<= 300 chars) as the REWORK reason
SECRET_RX = [
    re.compile(r"(?i)(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@"),
    re.compile(r"(?i)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_KEY|ENCRYPTION_KEY)[A-Z0-9_]*\s*[=:]\s*)\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b|\bsk-[A-Za-z0-9_-]{20,}\b|\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"(?<![A-Za-z0-9_/+=-])(?=[A-Za-z0-9_+/=-]*[A-Z])(?=[A-Za-z0-9_+/=-]*[a-z])(?=[A-Za-z0-9_+/=-]*[0-9])[A-Za-z0-9_+/-]{40,}={0,2}(?![A-Za-z0-9_+/=-])"),
]


def scrub(text):
    for rx in SECRET_RX:
        text = rx.sub(lambda m: (m.group(1) if m.groups() and m.group(1) else "") + "***", text)
    return text


def sh(cmd, log=None, env=None):
    # 3.0.4: stderr is merged into stdout in the order it was written, so log tails and the last line show the real context
    p = subprocess.run(cmd, shell=True, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, executable="/bin/bash", env=env)
    out = p.stdout
    if log is not None:
        log.write(out)
        log.flush()
    return p.returncode, out


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def code_tree(ref="HEAD"):
    """Hash of every top-level tree entry except docs/ (state, evidence, ui-progress, handoffs never change what CI runs)."""
    lines = [l for l in git("ls-tree", ref).splitlines() if not l.endswith("\tdocs")]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:20]


def plan_owners():
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import cloudctl as C  # noqa: E402
        plan = C.load_plan()
        return lambda path: sorted(t for t, x in plan["tasks"].items() if x.get("kind") in ("worker", "dev") and C.owns(x, path))
    except Exception:  # never let the attribution helper break the gate
        return lambda path: []


def area(path):
    parts = path.split("/")
    if parts[0] == "backend" and len(parts) > 2:
        return "/".join(parts[:2])
    if parts[:3] == ["frontend", "src", "features"] and len(parts) > 4:
        return "/".join(parts[:4])
    if parts[0] == "frontend" and len(parts) > 2:
        return "/".join(parts[:3])
    return "/".join(parts[:2]) if len(parts) > 2 else path


def summarize(job, text, owners):
    """Short, owner-attributed description of why one ci-parity job failed (no secrets: only paths, counts and rule names)."""
    out, files = [], {}

    def add(kind, path):
        files.setdefault(kind, []).append(path)

    for m in re.finditer(r"(?m)^(\S+\.py):(\d+): forbidden ([^:]+):", text):
        add("forbidden " + m.group(3), f"{m.group(1)}:{m.group(2)}")
    for m in re.finditer(r"(?m)^(\S+\.py):(\d+): (SEC-VIEW|RAW-SQL|HTTP-LIB|SYNTAX)\b", text):
        add("ADR-004 " + m.group(3), f"{m.group(1)}:{m.group(2)}")
    for m in re.finditer(r"(?m)^(backend/\S+?\.py):\d+: error:", text):
        add("mypy", m.group(1))
    for m in re.finditer(r"(?m)^(?:frontend/)?(src/\S+?\.tsx?)\(\d+,\d+\): error TS\d+", text):
        add("tsc", "frontend/" + m.group(1))
    for m in re.finditer(r"(?m)^\s*(?:FAIL|×)\s+(?:frontend/)?(src/\S+?\.(?:test|spec)\.tsx?)", text):
        add("vitest", "frontend/" + m.group(1))
    for m in re.finditer(r"(?m)^FAILED (\S+?\.py)::", text):
        add("pytest", m.group(1))
    for m in re.finditer(r"(?m)^In (\S+) line \d+:", text):
        add("shellcheck", m.group(1))
    for kind, paths in files.items():
        by_area = {}
        for p in paths:
            by_area.setdefault(area(p), []).append(p)
        bits = []
        for a_, ps in sorted(by_area.items(), key=lambda kv: -len(kv[1])):
            own = owners(ps[0].split(":")[0])
            first = ps[0] if kind.startswith(("forbidden", "ADR-004")) else a_
            bits.append(f"{len(ps)} {first}" + (f"({','.join(own)})" if own else ""))
        out.append(f"{kind}: " + ", ".join(bits[:3]) + (" …" if len(bits) > 3 else ""))
    extra = [
        (r"Found (\d+) errors? in (\d+) files?", lambda m: f"mypy total {m.group(1)} in {m.group(2)} files"),
        (r"make(?:\[\d+\])?: \*\*\* \[Makefile:\d+: ([\w.-]+)\]", lambda m: f"make target {m.group(1)}"),
        (r"(\d+) Code Findings?", lambda m: f"semgrep {m.group(1)} findings"),
        (r"leaks found: (\d+)", lambda m: f"gitleaks {m.group(1)} leaks"),
        (r"Found (\d+) known vulnerabilit", lambda m: f"pip-audit {m.group(1)} vulns"),
        (r"(\d+) failed(?:,| in)", lambda m: f"{m.group(1)} tests failed"),
        (r"\[nexus-dev\] ERROR: ([^\n]{0,90})", lambda m: "dev stack: " + m.group(1)),
    ]
    for rx, fmt in extra:
        ms = list(re.finditer(rx, text))
        if ms:
            s = fmt(ms[0])
            if s not in out:
                out.append(s)
    m = re.search(r'"failed": \[([^\]]*)\][^\n]*"report": "([^"]+)"', text)
    if m:
        bits = []
        try:
            rep = json.load(open(os.path.join(ROOT, m.group(2)), encoding="utf-8"))
            for c in rep.get("checks", []):
                if c.get("status") != "PASSED":
                    tables = [t for t in str(c.get("detail", "")).split(",") if re.fullmatch(r"[a-z0-9_]+", t)]
                    labels = sorted({t.split("_", 1)[0] for t in tables})
                    own = sorted({o for l in labels for o in owners(f"backend/{l}/models.py")})
                    bits.append(c.get("check", "?") + (f" [{', '.join(labels[:4])} tables" + (f" ({','.join(own)})" if own else "") + "]" if labels else ""))
        except (OSError, ValueError):
            bits = [x.strip().strip('"') for x in m.group(1).split(",") if x.strip()]
        out.append("DR drill FAILED: " + "; ".join(bits[:3]))
    if not out:
        tail = [l for l in text.strip().splitlines() if l.strip() and not l.startswith("[ci-parity]")][-1:] or ["no output"]
        out.append("last line: " + tail[0][:100])
    return scrub("; ".join(out))


def split_jobs(text):
    """ci-parity prints `CI-PARITY <job> exit=N` after each job: the output before it (since the previous marker) belongs to that job."""
    res, start = {}, 0
    for m in re.finditer(r"(?m)^CI-PARITY (policy|p03-tests|make-ci|compose-startup) exit=(\d+)\s*$", text):
        res[m.group(1)] = (int(m.group(2)), text[start:m.start()])
        start = m.end()
    return res


def merged_task():
    m = re.match(r"integrate (\S+) attempt \d+ from ", git("log", "-1", "--format=%s"))
    return m.group(1) if m else None


def evidence_path(task):
    m = re.fullmatch(r"nexus-integrate-w(\d\d)", git("rev-parse", "--abbrev-ref", "HEAD"))
    if not m:
        return None
    d = os.path.join(ROOT, "docs", "run", "evidence", f"W{m.group(1)}", "parity-gate")
    os.makedirs(d, exist_ok=True)
    k = 1
    while os.path.exists(os.path.join(d, f"{task or 'HEAD'}-{k}.log")):
        k += 1
    return os.path.join(d, f"{task or 'HEAD'}-{k}.log")


def is_socket_db_url(url):
    """libpq/Django unix-socket URLs: `postgres:///db`, `postgresql:///db`, or any URL with `host=/…` (the Makefile's peer-auth default)."""
    u = (url or "").strip()
    return bool(u) and (re.match(r"(?i)postgres(?:ql)?:///", u) is not None or re.search(r"(?:^|[?&\s])host=/", u) is not None)


def child_env():
    """Environment for the gate's child processes. 3.0.4: a unix-socket DATABASE_URL (e.g. the Makefile default exported by a session)
    is dropped, so ci-parity's make-ci job starts its own TCP PostgreSQL as in GitHub Actions instead of failing with
    `make-ci parity needs a TCP DATABASE_URL`. A TCP DATABASE_URL is kept (ci-parity honours it)."""
    env = dict(os.environ, PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1")
    note = None
    if is_socket_db_url(env.get("DATABASE_URL", "")):
        env.pop("DATABASE_URL")
        note = "[parity-gate] unix-socket DATABASE_URL dropped from the gate's environment (ci-parity starts its own TCP PostgreSQL)"
    return env, note


def ensure_full_history():
    """3.0.4: gitleaks (policy job) scans the git history; in a shallow clone the boundary commit looks all-new and its seed fixtures are
    reported as leaks. Unshallow first; returns an error string when the clone stays shallow."""
    if git("rev-parse", "--is-shallow-repository") != "true":
        return None
    p = subprocess.run(["git", "fetch", "--unshallow", "origin"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if git("rev-parse", "--is-shallow-repository") == "true":
        return "shallow clone and `git fetch --unshallow origin` failed: " + ((p.stdout or "").strip().splitlines() or ["?"])[-1][:120]
    return None


def parity_cmd():
    override = os.environ.get("NEXUS_PARITY_CMD")
    if override:
        if not os.environ.get("NEXUS_REHEARSAL"):
            raise SystemExit("PARITY-GATE FAILED: NEXUS_PARITY_CMD is only allowed in the local rehearsal")
        return override
    if os.path.isfile(os.path.join(ROOT, "scripts", "ci", "ci-parity.sh")):
        return "bash scripts/ci/ci-parity.sh"
    return None


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    os.makedirs(STATE_DIR, exist_ok=True)
    ok_file = os.path.join(STATE_DIR, "ok-trees")
    tree, head = code_tree(), git("rev-parse", "--short", "HEAD")
    known = set(open(ok_file).read().split()) if os.path.isfile(ok_file) else set()
    if a.final and tree in known:
        print(f"PARITY-FINAL OK: code tree {tree} of {head} already passed the gate in this session (only docs/ changed since)")
        return 0
    if a.final and git("rev-parse", "-q", "--verify", "origin/main") and tree == code_tree("origin/main"):
        print(f"PARITY-FINAL OK: no code change against origin/main (only docs/ — state, evidence, screenshots — is published)")
        return 0
    task = merged_task()
    label = f"merge of {task}" if task else f"HEAD {head}"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logp = os.path.join(STATE_DIR, f"{stamp}-{task or 'HEAD'}.log")
    env, env_note = child_env()
    shallow = ensure_full_history()
    if shallow:
        print(scrub(f"PARITY-GATE FAILED ({label}) platform: {shallow}")[:MAX_LAST])
        return 1
    with open(logp, "w", encoding="utf-8") as log:
        if env_note:
            log.write(env_note + "\n")
            print(env_note)
        dep_fail = []
        if os.path.isfile(os.path.join(ROOT, "uv.lock")):
            rc, out = sh("uv sync --frozen", log, env)
            if rc:
                dep_fail.append("uv sync --frozen: " + (out.strip().splitlines() or ["?"])[-1][:120])
        if os.path.isfile(os.path.join(ROOT, "pnpm-lock.yaml")):
            rc, out = sh("pnpm install --frozen-lockfile", log, env)
            if rc:
                dep_fail.append("pnpm install --frozen-lockfile: " + (out.strip().splitlines() or ["?"])[-1][:120])
        if dep_fail:
            print(scrub(f"PARITY-GATE FAILED ({label}) dependencies: " + "; ".join(dep_fail))[:MAX_LAST])
            return 1
        cmd = parity_cmd()
        if cmd is None:
            rc, out = sh("make ci", log, env)
            if rc:
                print(scrub(f"PARITY-GATE FAILED ({label}) make ci (no scripts/ci/ci-parity.sh yet): " + summarize("make-ci", out, plan_owners()))[:MAX_LAST])
                return 1
            open(ok_file, "a").write(tree + "\n")
            print(f"PARITY-GATE OK (make ci; scripts/ci/ci-parity.sh not present yet) {label} tree={tree}")
            return 0
        rc, out = sh(cmd, log, env)
    jobs = split_jobs(out)
    for j in JOBS:
        if j in jobs:
            print(f"  CI-PARITY {j} exit={jobs[j][0]}")
    failed = [j for j in JOBS if j not in jobs or jobs[j][0] != 0]
    if rc == 0 and not failed:
        open(ok_file, "a").write(tree + "\n")
        print(f"PARITY-GATE OK 4/4 ({label}) tree={tree}")
        return 0
    owners = plan_owners()
    parts = []
    ev = evidence_path(task)
    excerpt = [f"# parity gate — {label} — {stamp} — HEAD {head}", "# excerpts of the failing ci-parity jobs (secrets scrubbed)", ""]
    for j in failed:
        code, text = jobs.get(j, (None, out if not jobs else ""))
        parts.append(f"{j}: " + (summarize(j, text, owners) if text else "did not run (ci-parity stopped before it)"))
        lines = scrub(text).splitlines()[-150:]
        excerpt += [f"## {j} (exit {code})", "", "```"] + [l[:400] for l in lines] + ["```", ""]
    rel = ""
    if ev:
        open(ev, "w", encoding="utf-8").write("\n".join(excerpt)[-60000:] + "\n")
        rel = " log=" + os.path.relpath(ev, ROOT)
    msg = f"PARITY-GATE FAILED ({label}) {len(failed)}/4 " + " | ".join(parts)
    msg = scrub(msg)
    if len(msg) + len(rel) > MAX_LAST:
        msg = msg[: MAX_LAST - len(rel) - 1] + "…"
    print(msg + rel)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # a crash of the gate must not look like a pass
        print(f"PARITY-GATE FAILED (gate error): {scrub(str(e))[:200]}")
        sys.exit(1)
