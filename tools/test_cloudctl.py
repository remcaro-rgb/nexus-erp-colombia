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


class StateTests(unittest.TestCase):
    def setUp(self):
        self.st = copy.deepcopy(STATE)

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


class AuditTests(unittest.TestCase):
    """audit-main on throw-away git repositories (rehearsal pins stand in for the real seed/3.0.1 SHAs)."""

    def setUp(self):
        if not C.PIN_302_FILES:
            self.skipTest("3.0.2 pins not generated")
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
            self.w(p, open(os.path.join(REPO, p), encoding="utf-8").read())
        return self.c("Nexus prompts update 3.0.2-claude-cloud", C.UPDATE_302_TRAILER)

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
            self.w(p, open(os.path.join(REPO, p), encoding="utf-8").read())
        self.w("backend/evil.py", "x\n")
        h = self.c("Nexus prompts update 3.0.2-claude-cloud", C.UPDATE_302_TRAILER)
        self.assertEqual(C.audit_main("HEAD", full=True), [h])

    def test_302_with_modified_content_rejected(self):
        for p in list(C.PIN_302_FILES) + ["tools/cloudctl.py"]:
            self.w(p, open(os.path.join(REPO, p), encoding="utf-8").read())
        self.w("tools/cloudctl.py", open(os.path.join(REPO, "tools/cloudctl.py"), encoding="utf-8").read() + "# tampered\n")
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

    def test_normalized_self_hash(self):
        txt = open(os.path.join(REPO, "tools/cloudctl.py"), encoding="utf-8").read()
        self.assertEqual(C.normalized_cloudctl_sha256(txt), C.PIN_302_CLOUDCTL_SHA256)


if __name__ == "__main__":
    unittest.main()
