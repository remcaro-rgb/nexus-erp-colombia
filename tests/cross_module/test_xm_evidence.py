"""P22a harness self-tests: gate evidence collector."""

from __future__ import annotations

import hashlib
import json

import pytest
from qa_harness import evidence


def _reports(tmp_path):
    rep = tmp_path / "reports"
    (rep / "junit").mkdir(parents=True)
    (rep / "junit" / "ci.xml").write_text(
        '<testsuite tests="3" failures="1" errors="0" skipped="1"><testcase name="a"/></testsuite>', encoding="utf-8"
    )
    (rep / "leak").mkdir()
    (rep / "leak" / "matrix.csv").write_text("operation,status\nGET /x,PASSED\n", encoding="utf-8")
    (rep / "leak" / "ignored.bin").write_bytes(b"\x00")
    return rep


def test_p22a_collect_copies_reports_with_index(tmp_path):
    rep = _reports(tmp_path)
    dest = evidence.collect("G2", "selftest", source=rep, dest_root=tmp_path / "gates", root=tmp_path)
    index = json.loads((dest / "index.json").read_text(encoding="utf-8"))
    assert index["gate"] == "G2" and index["environment"] == "dev" and index["synthetic_data"] is True
    assert {f["path"] for f in index["files"]} == {"junit/ci.xml", "leak/matrix.csv"}
    assert index["junit"] == {"tests": 3, "failures": 1, "errors": 0, "skipped": 1}
    f = next(f for f in index["files"] if f["path"] == "leak/matrix.csv")
    assert f["sha256"] == hashlib.sha256((dest / "leak" / "matrix.csv").read_bytes()).hexdigest()


def test_p22a_collect_refuses_secret_like_content(tmp_path):
    rep = _reports(tmp_path)
    (rep / "security").mkdir()
    (rep / "security" / "log.md").write_text("client_secret = " + "x" * 24 + "\n", encoding="utf-8")
    with pytest.raises(evidence.SecretFound):
        evidence.collect("G3", "selftest", source=rep, dest_root=tmp_path / "gates", root=tmp_path)
    assert not (tmp_path / "gates" / "G3-evidence").exists()


@pytest.mark.parametrize("gate,label", [("G5", "x"), ("G1", "../escape"), ("G1", "")])
def test_p22a_collect_validates_gate_and_label(tmp_path, gate, label):
    with pytest.raises(ValueError):
        evidence.collect(gate, label, source=tmp_path, dest_root=tmp_path / "gates", root=tmp_path)
