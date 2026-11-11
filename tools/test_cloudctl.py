#!/usr/bin/env python3
"""Unit tests for tools/cloudctl.py (stdlib unittest). Run: python3 -m unittest tools/test_cloudctl.py -v (from the repo root)."""
import copy, json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
os.environ.setdefault("NEXUS_ROOT", REPO)
sys.path.insert(0, HERE)
import cloudctl as C  # noqa: E402

load = lambda rel: json.load(open(os.path.join(REPO, rel), encoding="utf-8"))
PLAN, STATE = load(C.PLAN_REL), load(C.STATE_REL)
REG, LEGACY = load("docs/spec/features/feature-registry.json"), load("docs/spec/plan.json")


def _git_repo(*a):
    return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)


class PlanTests(unittest.TestCase):
    def test_plan_valid(self):
        E, order = C.validate_plan(PLAN, REG, LEGACY)
        self.assertEqual(E, [])
        self.assertEqual(len(order), len(PLAN["tasks"]))

    def test_every_test_and_feature_once(self):
        tests = [x for t in PLAN["tasks"].values() for x in t.get("tests", [])]
        self.assertEqual(sorted(tests), sorted(x["id"] for x in REG["tests"]))
        self.assertEqual(len(tests), 154)
        feats = [f for t in PLAN["tasks"].values() for f in t.get("features", [])]
        self.assertEqual(sorted(feats), sorted(f["id"] for f in REG["features"]))
        self.assertEqual(len(feats), 28)

    def test_every_legacy_task_mapped(self):
        src = {s for t in PLAN["tasks"].values() for s in t.get("source", [])}
        self.assertEqual(src, set(LEGACY["tasks"]))

    def test_cycle_detected(self):
        p = copy.deepcopy(PLAN)
        p["tasks"]["P01"]["requires"] = ["P23"]
        E, _ = C.validate_plan(p, REG, LEGACY)
        self.assertTrue(any(e.startswith("cycle") for e in E), E[:3])

    def test_overlap_detected(self):
        p = copy.deepcopy(PLAN)
        p["tasks"]["P02"]["paths"] = p["tasks"]["P02"]["paths"] + ["backend/kernel/x.py"]
        E, _ = C.validate_plan(p, REG, LEGACY)
        self.assertTrue(any("overlaps" in e for e in E), E[:3])

    def test_unmapped_test_detected(self):
        p = copy.deepcopy(PLAN)
        p["tasks"]["P09b"]["tests"] = p["tasks"]["P09b"]["tests"][1:]
        E, _ = C.validate_plan(p, REG, LEGACY)
        self.assertTrue(any("not mapped" in e for e in E), E[:3])

    def test_double_mapped_test_detected(self):
        p = copy.deepcopy(PLAN)
        p["tasks"]["P09c"]["tests"] = p["tasks"]["P09c"]["tests"] + [p["tasks"]["P09b"]["tests"][0]]
        E, _ = C.validate_plan(p, REG, LEGACY)
        self.assertTrue(any("mapped twice" in e for e in E), E[:3])

    def test_wave_order_violation_detected(self):
        p = copy.deepcopy(PLAN)
        p["tasks"]["P01"]["requires"] = ["P02"]
        E, _ = C.validate_plan(p, REG, LEGACY)
        self.assertTrue(any("requires P02" in e for e in E), E[:3])

    def test_bad_prompt_name_detected(self):
        p = copy.deepcopy(PLAN)
        p["tasks"]["P01"]["prompt_file"] = "p01.md"
        E, _ = C.validate_plan(p, REG, LEGACY)
        self.assertTrue(any("bad prompt_file" in e for e in E))

    def test_ui_progress_overlap_detected(self):
        p = copy.deepcopy(PLAN)
        p["tasks"]["P08"]["paths"] = p["tasks"]["P08"]["paths"] + ["docs/ui-progress/**"]
        E, _ = C.validate_plan(p, REG, LEGACY)
        self.assertTrue(any("docs/ui-progress" in e for e in E), E[:3])

    def test_parallel_cap(self):
        self.assertEqual(PLAN["limits"]["max_parallel_sessions"], 5)
        self.assertEqual(PLAN["limits"]["max_attempts"], 3)


def initial_state():
    """The plan's initial STATE.json as seeded (3.0.4: the working copy's STATE.json advances every wave, so tests that start from
    wave 01 take the seed's copy; falls back to the working copy when the seed is not in this clone's history)."""
    p = _git_repo("show", f"{C.PINNED_SEED_COMMITS['seed']}:{C.STATE_REL}")
    return json.loads(p.stdout) if p.returncode == 0 and p.stdout.strip() else copy.deepcopy(STATE)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.st = initial_state()

    def test_initial_ready(self):
        ready = sorted(t for t, s in self.st["tasks"].items() if s["status"] == "READY")
        self.assertEqual(ready, ["P01", "P02", "P03"])
        self.assertIn("W01-S1-P01", C.next_text(PLAN, self.st))

    def test_accept_unlocks_gate_then_wave2(self):
        for t in ("P01", "P02", "P03"):
            C.apply_event(PLAN, self.st, {"type": "accept", "task": t, "sha": "a" * 40})
        self.assertEqual(self.st["tasks"]["G0"]["status"], "READY")
        self.assertEqual(self.st["tasks"]["P04"]["status"], "PENDING")
        C.apply_event(PLAN, self.st, {"type": "gate_pass", "task": "G0", "sha": "a" * 40})
        self.assertEqual(self.st["tasks"]["P04"]["status"], "READY")
        self.assertIn("wave 02", C.next_text(PLAN, self.st))

    def test_reject_then_fail(self):
        for i in range(3):
            C.apply_event(PLAN, self.st, {"type": "reject", "task": "P01", "reason": f"r{i}"})
        self.assertEqual(self.st["tasks"]["P01"]["status"], "FAILED")
        self.assertEqual(self.st["tasks"]["P01"]["attempt"], 3)
        self.assertIn("STOP-OPERATOR", C.next_text(PLAN, self.st))

    def test_rework_lists_retry_file(self):
        C.apply_event(PLAN, self.st, {"type": "reject", "task": "P02", "reason": "conflict"})
        self.assertEqual(self.st["tasks"]["P02"]["status"], "REWORK")
        self.assertIn("RETRY-P02.md", C.next_text(PLAN, self.st))

    def test_blocked_and_unblock(self):
        C.apply_event(PLAN, self.st, {"type": "blocked", "task": "P03", "reason": "network"})
        self.assertIn("OPERATOR: P03 is BLOCKED", C.next_text(PLAN, self.st))
        C.apply_event(PLAN, self.st, {"type": "unblock", "task": "P03"})
        self.assertEqual(self.st["tasks"]["P03"]["status"], "READY")

    def test_gate_reopen_points_to_gate_integrator(self):
        for t, s in self.st["tasks"].items():
            s["status"] = "ACCEPTED"
        for t in ("G4", "RC", "DEV"):
            self.st["tasks"][t]["status"] = "PENDING"
        self.st["tasks"]["P22c"].update(status="REWORK", attempt=2, notes=["reopened by gate G4"])
        txt = C.next_text(PLAN, self.st)
        self.assertIn("RETRY-P22c.md", txt)
        self.assertIn("INTEGRATOR-after-W09.md", txt)

    def test_normal_wave_points_to_own_integrator(self):
        st = self.st
        for t in ("P01", "P02", "P03"):
            C.apply_event(PLAN, st, {"type": "accept", "task": t, "sha": "a" * 40})
        C.apply_event(PLAN, st, {"type": "gate_pass", "task": "G0", "sha": "a" * 40})
        self.assertIn("INTEGRATOR-after-W02.md", C.next_text(PLAN, st))

    def test_complete(self):
        for s in self.st["tasks"].values():
            s["status"] = "ACCEPTED"
        self.assertTrue(C.next_text(PLAN, self.st).startswith("COMPLETE"))

    def test_status_render(self):
        txt = C.render_status(PLAN, self.st)
        self.assertIn("| 01 | P01 |", txt)
        self.assertIn("What to paste next", txt)


class PathAndSecretTests(unittest.TestCase):
    def test_owned_ok(self):
        self.assertEqual(C.check_paths(PLAN, "P01", ["backend/kernel/models.py", "docs/handoffs/P01.md", "docs/run/reports/P01.json"]), [])

    def test_foreign_rejected(self):
        self.assertTrue(C.check_paths(PLAN, "P01", ["backend/inventory/x.py"]))

    def test_protected_rejected(self):
        for p in ("docs/ui-progress/W03/INDEX.md", "docs/ui-progress/README.md", "tools/ui_snapshot.py", "docs/run/STATE.json", "tools/cloudctl.py", "CLAUDE.md", "docs/spec/UI-CONTRACT.md", ".claude/settings.json"):
            self.assertTrue(C.check_paths(PLAN, "P01", [p]), p)

    def test_other_task_report_rejected(self):
        self.assertTrue(C.check_paths(PLAN, "P01", ["docs/run/reports/P02.json"]))

    def test_secret_patterns(self):
        hits = lambda s: [n for n, rx in C.SECRET_PATTERNS if rx.search(s)]
        self.assertTrue(hits("-----BEGIN RSA " + "PRIVATE KEY-----"))
        self.assertTrue(hits("token = ghp_" + "a" * 36))
        self.assertTrue(hits("AKIA" + "B" * 16))
        self.assertTrue(hits("pass" + 'word = "Sup3rS3cretValue99"'))
        self.assertTrue(hits("DB_PASS" + "WORD = 'Qz7rehearsalFakeValue991'"))
        self.assertTrue(hits('{"api_' + 'key": "abcdefghijklmnop1234"}'))
        self.assertFalse(hits('password = os.environ["DB_PASSWORD"]'))
        self.assertFalse(hits("password: changeme"))

    def test_secret_files(self):
        self.assertTrue(C.scan_secrets([".env"]))
        self.assertTrue(C.scan_secrets(["deploy/dev/secrets/admin.txt"]))
        self.assertFalse(C.scan_secrets(["deploy/dev/secrets/README.md"]))

    def test_glob_overlap(self):
        self.assertTrue(C.globs_overlap("backend/**", "backend/kernel/**"))
        self.assertFalse(C.globs_overlap("backend/inventory/**", "backend/sales/**"))


class ReportTests(unittest.TestCase):
    def rep(self, **kw):
        r = {"schema": C.REPORT_SCHEMA, "task": "P01", "attempt": 1, "status": "DONE", "reason": "", "tested_sha": "a" * 40, "changed_paths": []}
        r.update(kw)
        return r

    def test_ok(self):
        self.assertEqual(C.validate_report(self.rep(), "P01"), [])

    def test_bad(self):
        self.assertTrue(C.validate_report(self.rep(task="P02"), "P01"))
        self.assertTrue(C.validate_report(self.rep(status="MAYBE"), "P01"))
        self.assertTrue(C.validate_report(self.rep(status="BLOCKED"), "P01"))
        self.assertTrue(C.validate_report(self.rep(tested_sha="abc"), "P01"))
        self.assertTrue(C.validate_report(self.rep(reason="ghp_" + "x" * 36), "P01"))


_HIST302 = []


def hist_302():
    """The commit of this repository holding the pinned 3.0.2 contents (the applied 3.0.2 update, or the 3.0.2 seed of a fresh repository).
    Since 3.0.3 the working copy no longer holds the 3.0.2 files, so the 3.0.2 audit tests take them from history."""
    if not _HIST302:
        keep = C.ROOT
        C.ROOT = REPO
        try:
            hs = _git_repo("log", "--all", "--format=%H", "--fixed-strings", "--grep", C.UPDATE_302_TRAILER).stdout.split()
            _HIST302.append(next((h for h in hs if C.tree_matches_302(h)), None))
        finally:
            C.ROOT = keep
    return _HIST302[0]


def content_302(path):
    return _git_repo("show", f"{hist_302()}:{path}").stdout


_HIST303 = []


def hist_303():
    """The commit of this repository holding the pinned 3.0.3 contents. Since 3.0.4 the working copy no longer holds them."""
    if not _HIST303:
        keep = C.ROOT
        C.ROOT = REPO
        try:
            hs = _git_repo("log", "--all", "--format=%H", "--fixed-strings", "--grep", C.UPDATE_303_TRAILER).stdout.split()
            _HIST303.append(next((h for h in hs if C.tree_matches_303(h)), None))
        finally:
            C.ROOT = keep
    return _HIST303[0]


def content_303(path):
    return _git_repo("show", f"{hist_303()}:{path}").stdout


class AuditTests(unittest.TestCase):
    """audit-main on throw-away git repositories (rehearsal pins stand in for the real seed/3.0.1 SHAs)."""

    def setUp(self):
        if not C.PIN_302_FILES:
            self.skipTest("3.0.2 pins not generated")
        if not hist_302():
            self.skipTest("the pinned 3.0.2 contents are not in this clone's history (shallow clone?)")
        self.tmp = tempfile.mkdtemp(prefix="nexus-audit-")
        self.env_keep = {k: os.environ.get(k) for k in ("NEXUS_REHEARSAL", "NEXUS_REHEARSAL_PINS")}
        self.root_keep = C.ROOT
        C.ROOT = self.tmp
        self.g("init", "-q", "-b", "main")
        self.w("README.md", "x\n")
        self.c("initial")
        for p in list(C.PIN_302_FILES) + ["tools/cloudctl.py"]:
            self.w(p, "old 3.0.0 content of " + p + "\n")
        self.w("docs/run/STATE.json", "{}\n")
        self.seed = self.c("seed", "Nexus-Seed: 3.0.0-claude-cloud")
        self.w("docs/run/STATE.json", "{\"w\": 1}\n")
        self.c("state: W01", "Nexus-Integrator: wave 01 integrate")
        self.w("docs/cloud-prompts/INTEGRATOR-after-W02.md", "3.0.1 version\n")
        self.u301 = self.c("Nexus prompts update 3.0.1-claude-cloud", "Nexus-Seed: 3.0.1-claude-cloud")
        os.environ["NEXUS_REHEARSAL"] = "1"
        os.environ["NEXUS_REHEARSAL_PINS"] = f"seed={self.seed},update301={self.u301}"

    def tearDown(self):
        C.ROOT = self.root_keep
        for k, v in self.env_keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def g(self, *a):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e.invalid", *a], cwd=self.tmp, capture_output=True, text=True, check=True).stdout.strip()

    def w(self, path, text):
        os.makedirs(os.path.dirname(os.path.join(self.tmp, path)) or self.tmp, exist_ok=True)
        with open(os.path.join(self.tmp, path), "w", encoding="utf-8") as f:
            f.write(text)

    def c(self, msg, trailer=None, allow_empty=False):
        self.g("add", "-A")
        self.g("commit", "-q", *(["--allow-empty"] if allow_empty else []), "-m", msg, *(["-m", trailer] if trailer else []))
        return self.g("rev-parse", "HEAD")

    def apply_302(self):
        for p in list(C.PIN_302_FILES) + ["tools/cloudctl.py"]:
            self.w(p, content_302(p))
        return self.c("Nexus prompts update 3.0.2-claude-cloud", C.UPDATE_302_TRAILER)

    def files_303(self):
        for p in list(C.PIN_303_FILES) + ["tools/cloudctl.py"]:
            self.w(p, content_303(p))

    def apply_303(self):
        self.files_303()
        return self.c("Nexus prompts update 3.0.3-claude-cloud", C.UPDATE_303_TRAILER)

    def need_303(self):
        if not C.PIN_303_FILES:
            self.skipTest("3.0.3 pins not generated")
        if not hist_303():
            self.skipTest("the pinned 3.0.3 contents are not in this clone's history (shallow clone?)")

    def files_304(self):
        for p in list(C.PIN_304_FILES) + ["tools/cloudctl.py"]:
            self.w(p, open(os.path.join(REPO, p), encoding="utf-8").read())

    def apply_304(self):
        self.files_304()
        return self.c("Nexus prompts update 3.0.4-claude-cloud", C.UPDATE_304_TRAILER)

    def need_304(self):
        self.need_303()
        if not C.PIN_304_FILES:
            self.skipTest("3.0.4 pins not generated")

    def test_T_OPS_304_legit_304_after_303_accepted(self):
        self.need_304()
        self.apply_302()
        self.apply_303()
        self.w("docs/run/STATE.json", "{\"w\": 6}\n")
        self.c("state: W05", "Nexus-Integrator: wave 05 close-gates")   # main moved between 3.0.3 and 3.0.4: still fine
        self.apply_304()
        self.assertEqual(C.audit_main("HEAD"), [])

    def test_T_OPS_304_without_303_rejected(self):
        self.need_304()
        self.apply_302()
        h = self.apply_304()
        why = {}
        self.assertEqual(C.audit_main("HEAD", full=True, explain=why), [h])
        self.assertIn("3.0.3 update first", why[h])

    def test_T_OPS_304_second_commit_rejected(self):
        self.need_304()
        self.apply_302()
        self.apply_303()
        self.apply_304()
        h = self.c("Nexus prompts update 3.0.4-claude-cloud", C.UPDATE_304_TRAILER, allow_empty=True)
        why = {}
        self.assertEqual(C.audit_main("HEAD", full=True, explain=why), [h])
        self.assertIn("second 3.0.4", why[h])

    def test_T_OPS_304_touching_other_path_rejected(self):
        self.need_304()
        self.apply_302()
        self.apply_303()
        self.files_304()
        self.w("backend/evil.py", "x\n")
        h = self.c("Nexus prompts update 3.0.4-claude-cloud", C.UPDATE_304_TRAILER)
        why = {}
        self.assertEqual(C.audit_main("HEAD", full=True, explain=why), [h])
        self.assertIn("unpinned paths", why[h])

    def test_T_OPS_304_modified_content_rejected(self):
        self.need_304()
        self.apply_302()
        self.apply_303()
        self.files_304()
        self.w("tools/parity_gate.py", open(os.path.join(REPO, "tools/parity_gate.py"), encoding="utf-8").read() + "# tampered\n")
        h = self.c("Nexus prompts update 3.0.4-claude-cloud", C.UPDATE_304_TRAILER)
        why = {}
        self.assertEqual(C.audit_main("HEAD", full=True, explain=why), [h])
        self.assertIn("differs from the pinned 3.0.4", why[h])

    def test_T_OPS_304_normalized_self_hash(self):
        self.need_304()
        txt = open(os.path.join(REPO, "tools/cloudctl.py"), encoding="utf-8").read()
        self.assertEqual(C.normalized_cloudctl_sha256(txt, "3.0.4"), C.PIN_304_CLOUDCTL_SHA256)

    def test_T_OPS_304_pin_303_block_unchanged(self):
        self.need_303()
        blk = lambda t: t[t.index("# >>> PIN-3.0.3"):t.index("# <<< PIN-3.0.3")]
        self.assertEqual(blk(open(os.path.join(REPO, "tools/cloudctl.py"), encoding="utf-8").read()), blk(content_303("tools/cloudctl.py")))

    def test_T_OPS_304_shallow_clone_refused(self):
        shallow = tempfile.mkdtemp(prefix="nexus-audit-shallow-")
        try:
            subprocess.run(["git", "clone", "-q", "--depth", "1", "file://" + self.tmp, shallow], check=True, capture_output=True)
            C.ROOT = shallow
            bad = C.audit_main("HEAD")
            self.assertEqual(len(bad), 1)
            self.assertIn("shallow", bad[0])
        finally:
            C.ROOT = self.tmp
            shutil.rmtree(shallow, ignore_errors=True)

    def test_T_OPS_304_fetch_unshallows(self):
        shallow = tempfile.mkdtemp(prefix="nexus-fetch-shallow-")
        try:
            subprocess.run(["git", "clone", "-q", "--depth", "1", "file://" + self.tmp, shallow], check=True, capture_output=True)
            C.ROOT = shallow
            self.assertTrue(C.is_shallow())
            C.fetch_retry()
            self.assertFalse(C.is_shallow())
            self.assertEqual(C.audit_main("HEAD"), [])
        finally:
            C.ROOT = self.tmp
            shutil.rmtree(shallow, ignore_errors=True)

    def test_legit_303_after_302_accepted(self):
        self.need_303()
        self.apply_302()
        self.w("docs/run/STATE.json", "{\"w\": 3}\n")
        self.c("state: W02", "Nexus-Integrator: wave 02 integrate")   # main moved between 3.0.2 and 3.0.3: still fine
        self.apply_303()
        self.assertEqual(C.audit_main("HEAD"), [])

    def test_303_without_302_rejected(self):
        self.need_303()
        h = self.apply_303()
        why = {}
        self.assertEqual(C.audit_main("HEAD", full=True, explain=why), [h])
        self.assertIn("3.0.2 update first", why[h])

    def test_second_303_commit_rejected(self):
        self.need_303()
        self.apply_302()
        self.apply_303()
        h = self.c("Nexus prompts update 3.0.3-claude-cloud", C.UPDATE_303_TRAILER, allow_empty=True)
        why = {}
        self.assertEqual(C.audit_main("HEAD", full=True, explain=why), [h])
        self.assertIn("second 3.0.3", why[h])

    def test_303_touching_other_path_rejected(self):
        self.need_303()
        self.apply_302()
        self.files_303()
        self.w("backend/evil.py", "x\n")
        h = self.c("Nexus prompts update 3.0.3-claude-cloud", C.UPDATE_303_TRAILER)
        why = {}
        self.assertEqual(C.audit_main("HEAD", full=True, explain=why), [h])
        self.assertIn("unpinned paths", why[h])

    def test_303_with_modified_content_rejected(self):
        self.need_303()
        self.apply_302()
        self.files_303()
        self.w("tools/parity_gate.py", content_303("tools/parity_gate.py") + "# tampered\n")
        h = self.c("Nexus prompts update 3.0.3-claude-cloud", C.UPDATE_303_TRAILER)
        why = {}
        self.assertEqual(C.audit_main("HEAD", full=True, explain=why), [h])
        self.assertIn("differs from the pinned 3.0.3", why[h])

    def test_303_on_fresh_302_seed_accepted(self):
        self.need_303()
        tmp2, orig = tempfile.mkdtemp(prefix="nexus-audit-fresh-"), self.tmp
        try:
            C.ROOT, self.tmp = tmp2, tmp2
            self.g("init", "-q", "-b", "main")
            for p in list(C.PIN_302_FILES) + ["tools/cloudctl.py"]:
                self.w(p, content_302(p))
            self.c("seed 3.0.2", C.UPDATE_302_TRAILER)
            os.environ["NEXUS_REHEARSAL_PINS"] = "seed=" + "1" * 40
            self.apply_303()
            self.assertEqual(C.audit_main("HEAD"), [])
            h = self.c("again", C.UPDATE_303_TRAILER, allow_empty=True)
            self.assertEqual(C.audit_main("HEAD", full=True), [h])
        finally:
            self.tmp = orig
            shutil.rmtree(tmp2, ignore_errors=True)

    def test_pinned_history_and_legit_302_accepted(self):
        self.assertEqual(C.audit_main("HEAD"), [])
        self.w("docs/run/STATE.json", "{\"w\": 2}\n")
        self.c("state: W02", "Nexus-Integrator: wave 02 integrate")   # main moved before the update: still fine
        self.apply_302()
        self.assertEqual(C.audit_main("HEAD"), [])

    def test_unpinned_seed_commit_rejected(self):
        self.w("docs/cloud-prompts/INTEGRATOR-after-W02.md", "forged\n")
        h = self.c("Nexus prompts update 3.0.1-claude-cloud", "Nexus-Seed: 3.0.1-claude-cloud")
        why = {}
        self.assertEqual(C.audit_main("HEAD", full=True, explain=why), [h])
        self.assertIn("unpinned Nexus-Seed", why[h])

    def test_second_302_commit_rejected(self):
        self.apply_302()
        h = self.c("Nexus prompts update 3.0.2-claude-cloud", C.UPDATE_302_TRAILER, allow_empty=True)
        why = {}
        self.assertEqual(C.audit_main("HEAD", full=True, explain=why), [h])
        self.assertIn("second 3.0.2", why[h])

    def test_302_touching_other_path_rejected(self):
        for p in list(C.PIN_302_FILES) + ["tools/cloudctl.py"]:
            self.w(p, content_302(p))
        self.w("backend/evil.py", "x\n")
        h = self.c("Nexus prompts update 3.0.2-claude-cloud", C.UPDATE_302_TRAILER)
        self.assertEqual(C.audit_main("HEAD", full=True), [h])

    def test_302_with_modified_content_rejected(self):
        for p in list(C.PIN_302_FILES) + ["tools/cloudctl.py"]:
            self.w(p, content_302(p))
        self.w("tools/cloudctl.py", content_302("tools/cloudctl.py") + "# tampered\n")
        h = self.c("Nexus prompts update 3.0.2-claude-cloud", C.UPDATE_302_TRAILER)
        self.assertEqual(C.audit_main("HEAD", full=True), [h])

    def test_integrator_commit_touching_tools_rejected(self):
        self.w("tools/cloudctl.py", "rewritten\n")
        h = self.c("state", "Nexus-Integrator: wave 02 integrate")
        why = {}
        self.assertEqual(C.audit_main("HEAD", full=True, explain=why), [h])
        self.assertIn("tooling", why[h])

    def test_repair_restoring_tools_accepted(self):
        self.w("tools/cloudctl.py", "forged\n")
        h = self.c("sneaky", "Nexus-Seed: 3.0.1-claude-cloud")
        self.g("revert", "--no-edit", h)
        self.g("commit", "-q", "--amend", "-m", "audit repair", "-m", f"Nexus-Audit-Ack: {h}\nNexus-Integrator: audit repair")
        self.assertEqual(C.audit_main("HEAD"), [])

    def test_repair_of_integrator_tools_change_accepted(self):
        self.w("tools/cloudctl.py", "forged\n")
        h = self.c("state", "Nexus-Integrator: wave 02 integrate")
        self.g("revert", "--no-edit", h)
        self.g("commit", "-q", "--amend", "-m", "audit repair", "-m", f"Nexus-Audit-Ack: {h}\nNexus-Integrator: audit repair")
        self.assertEqual(C.audit_main("HEAD"), [])

    def test_fake_ack_cannot_hide_tools_change(self):
        self.w("tools/cloudctl.py", "forged\n")
        h = self.c("sneaky", "Nexus-Seed: 3.0.1-claude-cloud")
        r = self.c("audit repair", f"Nexus-Audit-Ack: {h}\nNexus-Integrator: audit repair", allow_empty=True)  # acks without reverting
        self.assertEqual(C.audit_main("HEAD", full=True), [r])

    def test_missing_pinned_seed_rejected(self):
        os.environ["NEXUS_REHEARSAL_PINS"] = "seed=" + "0" * 40
        self.assertTrue(C.audit_main("HEAD")[0].startswith("pinned seed commit"))

    def test_normalized_302_hash_from_history(self):
        self.assertEqual(C.normalized_cloudctl_sha256(content_302("tools/cloudctl.py")), C.PIN_302_CLOUDCTL_SHA256)

    def test_normalized_303_hash_from_history(self):
        self.need_303()
        self.assertEqual(C.normalized_cloudctl_sha256(content_303("tools/cloudctl.py"), "3.0.3"), C.PIN_303_CLOUDCTL_SHA256)

    def test_pin_302_block_unchanged(self):
        blk = lambda t: t[t.index("# >>> PIN-3.0.2"):t.index("# <<< PIN-3.0.2")]
        self.assertEqual(blk(open(os.path.join(REPO, "tools/cloudctl.py"), encoding="utf-8").read()), blk(content_302("tools/cloudctl.py")))


class CollectResultsTests(unittest.TestCase):
    """3.0.4: collect_results empties reports/playwright although Playwright's outputDir (artifacts/) is a directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nexus-collect-")
        self.root_keep, self.ids_keep = C.ROOT, os.environ.get("NEXUS_IDS")
        C.ROOT = self.tmp

    def tearDown(self):
        C.ROOT = self.root_keep
        if self.ids_keep is None:
            os.environ.pop("NEXUS_IDS", None)
        else:
            os.environ["NEXUS_IDS"] = self.ids_keep
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_T_OPS_304_collect_results_removes_playwright_artifacts_dir(self):
        art = os.path.join(self.tmp, "reports", "playwright", "artifacts", "x")
        os.makedirs(art)
        open(os.path.join(art, "trace.zip"), "wb").write(b"PK")
        open(os.path.join(self.tmp, "reports", "playwright", "old.json"), "w").write("{}")
        os.makedirs(os.path.join(self.tmp, "reports", "junit"))
        open(os.path.join(self.tmp, "reports", "junit", "old.xml"), "w").write("<x/>")
        log = []
        for _round in range(2):  # integrate calls it once per retry round; round 2 used to crash with IsADirectoryError
            C.collect_results(["T-X-01"], "0" * 40, "docs/run/evidence/W99/feature-results.json", log, "mkdir -p reports/playwright/artifacts/y")
            self.assertEqual(os.listdir(os.path.join(self.tmp, "reports", "junit")), [])
            self.assertEqual(os.listdir(os.path.join(self.tmp, "reports", "playwright")), ["artifacts"])  # only what this run created
            self.assertEqual(os.listdir(os.path.join(self.tmp, "reports", "playwright", "artifacts")), ["y"])

    def test_T_OPS_304_clear_report_dir_keeps_symlink_target(self):
        target = os.path.join(self.tmp, "elsewhere")
        os.makedirs(target)
        open(os.path.join(target, "keep.txt"), "w").write("k")
        d = os.path.join(self.tmp, "reports", "playwright")
        os.makedirs(d)
        os.symlink(target, os.path.join(d, "link"))
        C.clear_report_dir("reports/playwright")
        self.assertEqual(os.listdir(d), [])
        self.assertTrue(os.path.isfile(os.path.join(target, "keep.txt")))


class ParityGateTests(unittest.TestCase):
    """tools/parity_gate.py: per-job split, owner attribution and scrubbing of the REWORK reason (3.0.3)."""

    @classmethod
    def setUpClass(cls):
        import parity_gate as G
        cls.G = G

    def test_default_test_cmd_is_parity_gate(self):
        self.assertEqual(C.DEFAULT_TEST_CMD, "python3 tools/parity_gate.py")

    def test_split_and_attribute(self):
        G = self.G
        out = ("[ci-parity] step forbidden-patterns\n"
               "backend/workflow/tests/test_x.py:33: forbidden " + "pri" + "nt(: payload\n"
               "CI-PARITY policy exit=1\nCI-PARITY p03-tests exit=0\n"
               "backend/ledger/models.py:10: error: Name \"x\" is not defined  [name-defined]\n"
               "backend/ledger/api.py:3: error: Missing return  [return]\n"
               "src/features/ledger/Page.tsx(4,7): error TS2322: Type mismatch.\n"
               "Found 2 errors in 2 files (checked 300 source files)\n"
               "CI-PARITY make-ci exit=1\n")
        jobs = G.split_jobs(out)
        self.assertEqual({k: v[0] for k, v in jobs.items()}, {"policy": 1, "p03-tests": 0, "make-ci": 1})
        owners = lambda p: ["P05"] if "ledger" in p else ["P07"] if "workflow" in p else []
        s = G.summarize("make-ci", jobs["make-ci"][1], owners)
        self.assertIn("mypy: 2 backend/ledger(P05)", s)
        self.assertIn("tsc: 1 frontend/src/features/ledger(P05)", s)
        self.assertIn("mypy total 2 in 2 files", s)
        self.assertIn("backend/workflow/tests/test_x.py:33(P07)", G.summarize("policy", jobs["policy"][1], owners))

    def test_scrub(self):
        G = self.G
        val = "Zq" + "8vT3mW9xK2pL7rN4bY6cH1dF5gJ0sA" + "e3Qw9Ur7Ty"
        self.assertNotIn(val, G.scrub("ERP_DB_" + "PASSWORD=" + val))
        self.assertNotIn(val, G.scrub("token " + val))
        self.assertNotIn("s3cr3tpw", G.scrub("postgres://erp:" + "s3cr3tpw" + "@db:5432/erp"))
        self.assertIn("a" * 40, G.scrub("HEAD " + "a" * 40))

    def test_T_OPS_304_sh_interleaves_stderr_in_order(self):
        rc, out = self.G.sh("echo one; echo two >&2; echo three; echo four >&2; exit 3")
        self.assertEqual(rc, 3)
        self.assertEqual(out.split(), ["one", "two", "three", "four"])

    def test_T_OPS_304_socket_database_url_dropped(self):
        G = self.G
        for sock in ("postgres:///nexus_erp", "postgresql:///nexus_erp?host=/var/run/postgresql", "postgres://u@/db?host=/tmp",
                     "dbname=nexus_erp host=/var/run/postgresql"):
            self.assertTrue(G.is_socket_db_url(sock), sock)
        for tcp in ("", "postgres://nexus_ci@127.0.0.1:5432/nexus_erp", "postgresql://u@localhost/db?sslmode=disable"):
            self.assertFalse(G.is_socket_db_url(tcp), tcp)
        keep = os.environ.get("DATABASE_URL")
        try:
            os.environ["DATABASE_URL"] = "postgres:///nexus_erp"
            env, note = G.child_env()
            self.assertNotIn("DATABASE_URL", env)
            self.assertIn("DATABASE_URL dropped", note)
            os.environ["DATABASE_URL"] = "postgres://nexus_ci@127.0.0.1:5432/nexus_erp"
            env, note = G.child_env()
            self.assertEqual(env["DATABASE_URL"], "postgres://nexus_ci@127.0.0.1:5432/nexus_erp")
            self.assertIsNone(note)
        finally:
            if keep is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = keep


class DevLoginTests(unittest.TestCase):
    """tools/dev_login.py: loopback-only origin and the 0600 throw-away credentials file (3.0.3)."""

    @classmethod
    def setUpClass(cls):
        import dev_login as D
        cls.D = D

    def setUp(self):
        self.keep = os.environ.get("ERP_PUBLIC_ORIGIN")

    def tearDown(self):
        if self.keep is None:
            os.environ.pop("ERP_PUBLIC_ORIGIN", None)
        else:
            os.environ["ERP_PUBLIC_ORIGIN"] = self.keep

    def test_loopback_only(self):
        D = self.D
        for ok in ("http://localhost:8080", "http://127.0.0.1:8083"):
            os.environ["ERP_PUBLIC_ORIGIN"] = ok
            self.assertEqual(D.public_origin(), ok)
        for bad in ("http://0.0.0.0:8080", "http://10.0.0.5:8080", "https://erp.example.com", "http://localhost.evil.test"):
            os.environ["ERP_PUBLIC_ORIGIN"] = bad
            with self.assertRaises(D.LoginError):
                D.public_origin()

    def test_T_OPS_304_principal_line_parsed_in_full(self):
        D = self.D
        t, c, u = "6f1c2b0e-3a4d-5e6f-8a9b-0c1d2e3f4a5b", "1b2c3d4e-5f60-4718-9a0b-1c2d3e4f5a6b", "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
        line = (f"DEV-UI-PRINCIPAL OK tenant={t} company={c} user={u} permissions=137 "
                "display_name=\"Vista previa UI (sintético)\" initials=VU")
        self.assertGreater(len(line), 160)
        f = D.parse_principal(line)
        self.assertEqual(f, {"tenant": t, "company": c, "user": u, "permissions": "137", "display_name": "Vista previa UI (sintético)", "initials": "VU"})
        s = D.principal_summary(f)
        self.assertLessEqual(len(s), 160)
        self.assertTrue(s.endswith("…"))
        self.assertEqual(D.principal_summary({"tenant": t}), f"tenant={t}")

    def test_T_OPS_304_erp_principal_returns_all_fields(self):
        D = self.D
        keep = {k: os.environ.get(k) for k in ("NEXUS_REHEARSAL", "NEXUS_DEV_LOGIN_ERP_CMD")}
        try:
            os.environ["NEXUS_REHEARSAL"] = "1"
            os.environ["NEXUS_DEV_LOGIN_ERP_CMD"] = ('echo "noise"; echo "DEV-UI-PRINCIPAL OK tenant=$3 company=' + "c" * 60 +
                                                     ' user=$1 permissions=12 initials=UP"')
            f = D.erp_principal("subject-" + "s" * 60)
            self.assertEqual(f["initials"], "UP")
            self.assertEqual(f["user"], "subject-" + "s" * 60)
            self.assertEqual(f["tenant"], D.TENANT_ID)
        finally:
            for k, v in keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_secret_file_mode(self):
        d = tempfile.mkdtemp(prefix="nexus-devlogin-")
        try:
            tmp = self.D.write_secret_file(os.path.join(d, "secrets", "ui-preview.env"), "x" * 32)
            self.assertEqual(os.stat(tmp).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(os.path.dirname(tmp)).st_mode & 0o077, 0)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
