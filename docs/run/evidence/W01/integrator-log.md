
## integrate — 2026-11-09T14:43:58Z

* P01: ACCEPTED
* P02: ACCEPTED
* P03: ACCEPTED

```
$ make ci  [after P01] -> exit 0 in 639s
$ make ci  [after P02] -> exit 0 in 32s
$ make ci  [after P03] -> exit 0 in 31s
$ (make dev-up && make dev-seed) > /tmp/nexus-devup.log 2>&1; make test-ids IDS="$NEXUS_IDS"  [feature tests (1 ids)] -> exit 0 in 62s
```
