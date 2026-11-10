
## integrate — 2026-11-10T20:44:40Z

* P22b: ACCEPTED
* P09a: ACCEPTED
* P11a: ACCEPTED
* P13a: ACCEPTED
* P13b: ACCEPTED
* P15: ACCEPTED
* P14: ACCEPTED

```
$ python3 tools/parity_gate.py  [after P22b] -> exit 0 in 510s
$ python3 tools/parity_gate.py  [after P09a] -> exit 0 in 520s
$ python3 tools/parity_gate.py  [after P11a] -> exit 0 in 534s
$ python3 tools/parity_gate.py  [after P13a] -> exit 0 in 549s
$ python3 tools/parity_gate.py  [after P13b] -> exit 0 in 566s
$ python3 tools/parity_gate.py  [after P15] -> exit 0 in 584s
$ python3 tools/parity_gate.py  [after P14] -> exit 0 in 611s
$ (make dev-up && make dev-seed) > /tmp/nexus-devup.log 2>&1; make test-ids IDS="$NEXUS_IDS"  [feature tests (24 ids)] -> exit 0 in 45s
```
