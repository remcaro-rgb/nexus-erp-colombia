# STATUS — Nexus ERP (edition 3.0.0-claude-cloud)

Updated: 2026-11-10T20:44:40Z (UTC). Repository: https://github.com/remcaro-rgb/nexus-erp-colombia.

Written only by the integrator (`python3 tools/cloudctl.py status`). A task counts as working only when it is ACCEPTED with
integrator-run test evidence under `docs/run/evidence/`.

## What to paste next

```
NEXT: wave 04 — open 4 NEW Claude Code cloud session(s) on remcaro-rgb/nexus-erp-colombia (at most 5 at the same time; start the next one when one finishes) and paste exactly one file into each:
  prompts/W04-S1-P10a-sales-ar-crm-core.md
  prompts/W04-S2-P11b-requisitions-supplier-portal.md
  prompts/W04-S3-P09c-inventory-bom-transformations.md
  prompts/W04-S4-P09b-inventory-barcodes-issues.md
THEN: when every one of those sessions has ended (DONE or BLOCKED), paste prompts/INTEGRATOR-after-W04.md into 1 NEW session.
```

## Tasks

| Wave | Task | Kind | Status | Attempt | Accepted at | Last note |
|---|---|---|---|---|---|---|
| 01 | P01 | worker | ACCEPTED | 1 | 2026-11-09T14:43:58Z |  |
| 01 | P02 | worker | ACCEPTED | 1 | 2026-11-09T14:43:58Z |  |
| 01 | P03 | worker | ACCEPTED | 1 | 2026-11-09T14:43:58Z |  |
| 01 | G0 | gate | ACCEPTED | 1 | 2026-11-09T14:52:56Z |  |
| 02 | P04 | worker | ACCEPTED | 1 | 2026-11-09T21:19:50Z |  |
| 02 | P05 | worker | ACCEPTED | 3 | 2026-11-10T03:42:02Z | attempt 2 rejected: CI parity (tools/parity_gate.py) failed after merging it: PARITY-GATE  |
| 02 | P07 | worker | ACCEPTED | 1 | 2026-11-09T21:19:50Z |  |
| 02 | P08 | worker | ACCEPTED | 3 | 2026-11-10T03:42:02Z | attempt 2 rejected: CI parity (tools/parity_gate.py) failed after merging it: PARITY-GATE  |
| 02 | P06 | worker | ACCEPTED | 1 | 2026-11-09T21:19:50Z |  |
| 02 | P22a | worker | ACCEPTED | 1 | 2026-11-09T21:19:50Z |  |
| 02 | G1 | gate | ACCEPTED | 1 | 2026-11-10T03:45:00Z |  |
| 03 | P15 | worker | ACCEPTED | 1 | 2026-11-10T20:44:40Z |  |
| 03 | P09a | worker | ACCEPTED | 1 | 2026-11-10T20:44:40Z |  |
| 03 | P13b | worker | ACCEPTED | 1 | 2026-11-10T20:44:40Z |  |
| 03 | P11a | worker | ACCEPTED | 1 | 2026-11-10T20:44:40Z |  |
| 03 | P13a | worker | ACCEPTED | 1 | 2026-11-10T20:44:40Z |  |
| 03 | P14 | worker | ACCEPTED | 1 | 2026-11-10T20:44:40Z |  |
| 03 | P22b | worker | ACCEPTED | 1 | 2026-11-10T20:44:40Z |  |
| 04 | P10a | worker | READY | 1 |  |  |
| 04 | P11b | worker | READY | 1 |  |  |
| 04 | P09c | worker | READY | 1 |  |  |
| 04 | P09b | worker | READY | 1 |  |  |
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
