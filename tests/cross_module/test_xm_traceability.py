"""P22a harness self-tests: docs/traceability.csv generator (shared/08, prompt P22 §11)."""

from __future__ import annotations

import shutil
import textwrap

from qa_harness import traceability as tr

JUNIT = """<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="3">
<testcase classname="tests.demo.test_demo" name="test_a"/>
<testcase classname="tests.demo.test_demo.TestK" name="test_b[x]"><failure message="boom"/></testcase>
<testcase classname="tests.demo.test_demo" name="test_c"/>
</testsuite></testsuites>"""


def _tree(tmp_path):
    shared = tmp_path / "docs" / "prompt-pack" / "shared"
    shared.mkdir(parents=True)
    shutil.copyfile(tr.SHARED_08, shared / "08-traceability-matrix.md")
    demo = tmp_path / "tests" / "demo"
    demo.mkdir(parents=True)
    (demo / "test_demo.py").write_text(textwrap.dedent('''
        import pytest
        pytestmark = pytest.mark.req("UR-01")

        @pytest.mark.req("UR-07", "UR-14")
        def test_a():
            pass

        @pytest.mark.req("UR-99")
        class TestK:
            @pytest.mark.req("UR-08")
            def test_b(self):
                pass

        def helper():
            pass
    '''), encoding="utf-8")
    (demo / "journey.spec.ts").write_text("test('venta completa @UR-09 @UR-11', async () => {});\n", encoding="utf-8")
    junit = tmp_path / "reports" / "junit"
    junit.mkdir(parents=True)
    (junit / "ci.xml").write_text(JUNIT, encoding="utf-8")
    return tmp_path


def test_p22a_requirements_parsed_from_shared_08():
    reqs = tr.parse_requirements()
    assert [r.ur for r in reqs] == [f"UR-{i:02d}" for i in range(1, 26)]
    assert next(r for r in reqs if r.ur == "UR-14").gates == "G2"


def test_p22a_markers_junit_and_playwright_are_joined(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    monkeypatch.setattr(tr, "ROOT", root)
    rows, reqs, tests = tr.generate(root=root)
    got = {(r[0], r[5]): r[6] for r in rows}
    assert got[("UR-07", "test_a")] == "PASSED"
    assert got[("UR-14", "test_a")] == "PASSED"
    assert got[("UR-01", "test_a")] == "PASSED", "module pytestmark applies"
    assert got[("UR-08", "test_b")] == "FAILED", "worst parametrized outcome wins"
    assert got[("UR-99", "test_b")] == "UNKNOWN_UR"
    assert got[("UR-09", "venta completa @UR-09 @UR-11")] == "NOT_RUN"
    assert got[("UR-02", "")] == "MISSING"
    missing = tr.uncovered(rows)
    assert "UR-07" not in missing and "UR-08" in missing and "UR-02" in missing
    assert tr.uncovered(rows, gate="G0") == ["UR-19"]


def test_p22a_check_mode_fails_while_requirements_are_uncovered(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    monkeypatch.setattr(tr, "ROOT", root)
    monkeypatch.setattr(tr, "generate", lambda report_root=None, _g=tr.generate: _g(root=root, report_root=report_root))
    out = tmp_path / "trace.csv"
    assert tr.main(["--out", str(out), "--check"]) == 1
    assert out.read_text(encoding="utf-8").splitlines()[0] == ",".join(tr.HEADER)
    assert tr.main(["--out", str(out)]) == 0
