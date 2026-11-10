# STATUS — Nexus ERP (edition 3.0.0-claude-cloud)

Updated: 2026-11-10T00:58:47Z (UTC). Repository: https://github.com/remcaro-rgb/nexus-erp-colombia.

Written only by the integrator (`python3 tools/cloudctl.py status`). A task counts as working only when it is ACCEPTED with
integrator-run test evidence under `docs/run/evidence/`.

## What to paste next

```
NEXT: wave 02 — open 2 NEW Claude Code cloud session(s) on remcaro-rgb/nexus-erp-colombia (at most 5 at the same time; start the next one when one finishes) and paste exactly one file into each:
  prompts/RETRY-P05.md   (attempt 3: attempt 2 rejected: CI parity (tools/parity_gate.py) failed after merging it: PARITY-GATE FAILED (merge of P05) 3/4 policy: last line: forbidden-pattern scan OK)
  prompts/RETRY-P08.md   (attempt 3: attempt 2 rejected: CI parity (tools/parity_gate.py) failed after merging it: PARITY-GATE FAILED (merge of P08) 1/4 policy: last line: Severity: 3 moderate | 1 )
THEN: when every one of those sessions has ended (DONE or BLOCKED), paste prompts/INTEGRATOR-after-W02.md into 1 NEW session.
```

## Tasks

| Wave | Task | Kind | Status | Attempt | Accepted at | Last note |
|---|---|---|---|---|---|---|
| 01 | P01 | worker | ACCEPTED | 1 | 2026-11-09T14:43:58Z |  |
| 01 | P02 | worker | ACCEPTED | 1 | 2026-11-09T14:43:58Z |  |
| 01 | P03 | worker | ACCEPTED | 1 | 2026-11-09T14:43:58Z |  |
| 01 | G0 | gate | ACCEPTED | 1 | 2026-11-09T14:52:56Z |  |
| 02 | P04 | worker | ACCEPTED | 1 | 2026-11-09T21:19:50Z |  |
| 02 | P05 | worker | REWORK | 3 |  | attempt 2 rejected: CI parity (tools/parity_gate.py) failed after merging it: PARITY-GATE  |
| 02 | P07 | worker | ACCEPTED | 1 | 2026-11-09T21:19:50Z |  |
| 02 | P08 | worker | REWORK | 3 |  | attempt 2 rejected: CI parity (tools/parity_gate.py) failed after merging it: PARITY-GATE  |
| 02 | P06 | worker | ACCEPTED | 1 | 2026-11-09T21:19:50Z |  |
| 02 | P22a | worker | ACCEPTED | 1 | 2026-11-09T21:19:50Z |  |
| 02 | G1 | gate | PENDING | 1 |  |  |
| 03 | P15 | worker | PENDING | 1 |  |  |
| 03 | P09a | worker | PENDING | 1 |  |  |
| 03 | P13b | worker | PENDING | 1 |  |  |
| 03 | P11a | worker | PENDING | 1 |  |  |
| 03 | P13a | worker | PENDING | 1 |  |  |
| 03 | P14 | worker | PENDING | 1 |  |  |
| 03 | P22b | worker | PENDING | 1 |  |  |
| 04 | P10a | worker | PENDING | 1 |  |  |
| 04 | P11b | worker | PENDING | 1 |  |  |
| 04 | P09c | worker | PENDING | 1 |  |  |
| 04 | P09b | worker | PENDING | 1 |  |  |
| 05 | P16a | worker | PENDING | 1 |  |  |
| 05 | P12 | worker | PENDING | 1 |  |  |
| 05 | P10b | worker | PENDING | 1 |  |  |
| 05 | P16b | worker | PENDING | 1 |  |  |
| 05 | G2 | gate | PENDING | 1 |  |  |
| 06 | P18 | worker | PENDING | 1 |  |  |
| 06 | P22c | worker | PENDING | 1 |  |  |
| 06 | P17a | worker | PENDING | 1 |  |  |
| 07 | P19 | worker | PENDING | 1 |  |  |
| 07 | P17b | worker | PENDING | 1 |  |  |
| 07 | P17c | worker | PENDING | 1 |  |  |
| 08 | P21 | worker | PENDING | 1 |  |  |
| 08 | P20 | worker | PENDING | 1 |  |  |
| 08 | G3 | gate | PENDING | 1 |  |  |
| 09 | P23 | worker | PENDING | 1 |  |  |
| 09 | P22d | worker | PENDING | 1 |  |  |
| 09 | G4 | gate | PENDING | 1 |  |  |
| 09 | RC | release | PENDING | 1 |  |  |
| 10 | DEV | dev | PENDING | 1 |  |  |
