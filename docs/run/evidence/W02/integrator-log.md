
## integrate — 2026-11-09T21:19:50Z

* P08: REWORK: `make ci` failed after merging it: TIVE_TLS` environment variable is deprecated and will be removed in a future release. Use `UV_SYSTEM_CERTS` instead.
warning: The `UV_NATIVE_TLS` environment variabl
* P05: REWORK: mandatory tests not PASSED ['T-ACC-DIM-08']
* P22a: ACCEPTED
* P04: ACCEPTED
* P06: ACCEPTED
* P07: ACCEPTED

```
$ make ci  [after P22a] -> exit 0 in 42s
$ make ci  [after P04] -> exit 0 in 54s
$ make ci  [after P05] -> exit 0 in 65s
$ make ci  [after P06] -> exit 0 in 82s
$ make ci  [after P07] -> exit 0 in 86s
$ make ci  [after P08] -> exit 2 in 85s
$ (make dev-up && make dev-seed) > /tmp/nexus-devup.log 2>&1; make test-ids IDS="$NEXUS_IDS"  [feature tests (20 ids)] -> exit 0 in 65s
$ make ci  [after P22a] -> exit 0 in 39s
$ make ci  [after P04] -> exit 0 in 47s
$ make ci  [after P06] -> exit 0 in 63s
$ make ci  [after P07] -> exit 0 in 72s
$ (make dev-up && make dev-seed) > /tmp/nexus-devup.log 2>&1; make test-ids IDS="$NEXUS_IDS"  [feature tests (12 ids)] -> exit 0 in 60s
```
