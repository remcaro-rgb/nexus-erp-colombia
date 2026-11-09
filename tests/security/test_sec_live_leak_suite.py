"""P22a: the generated invariant-12 leak suite against the real Django app.

Today only kernel routes exist and no authenticator is on main (P04), so authenticated cases are
reported BLOCKED and unrouted operations NOT_IMPLEMENTED in the matrix; nothing may FAIL. As modules
merge, their operations turn into executed cases automatically. Matrix: reports/leak/."""

from __future__ import annotations

import json

import pytest
from qa_harness import openapi, paths
from qa_harness.leak import LeakSuite, Status, generate_cases, summarize, write_matrix_csv
from qa_harness.oracle import Dataset
from qa_harness.principals import matrix
from qa_harness.transports import DjangoClientTransport


@pytest.mark.django_db
def test_p22a_live_leak_matrix_has_no_failures():
    ops = [op for op in openapi.operations() if not op.portal]
    dataset = Dataset()  # module seeders arrive with their owners (P22c wires them)
    cases = generate_cases(ops, matrix(), dataset)
    results = LeakSuite(DjangoClientTransport(authenticator=None), dataset).run(cases)
    summary = summarize(results)
    out_dir = paths.REPORTS / "leak"
    write_matrix_csv(results, out_dir / "leak-matrix-live.csv")
    (out_dir / "leak-summary-live.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    assert summary["failed"] == []
    assert summary["cases"] == len(cases)
    executed = [r for r in results if r.status in (Status.PASSED, Status.FAILED)]
    implemented = {r.case.op.key for r in executed}
    # every implemented contract operation had at least its anonymous case executed
    for r in results:
        if r.case.op.key in implemented and r.case.principal is None:
            assert r.status is Status.PASSED
