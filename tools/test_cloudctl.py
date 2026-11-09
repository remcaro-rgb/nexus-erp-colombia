#!/usr/bin/env python3
"""Unit tests for tools/cloudctl.py (stdlib unittest). Run: python3 -m unittest tools/test_cloudctl.py -v (from the repo root)."""
import copy, json, os, sys, unittest

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
        for p in ("docs/run/STATE.json", "tools/cloudctl.py", "CLAUDE.md", "docs/spec/UI-CONTRACT.md", ".claude/settings.json"):
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


if __name__ == "__main__":
    unittest.main()
