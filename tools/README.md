# tools (Python 3.11+, standard library only; integrator/seed only — workers never edit tools/)

* `cloudctl.py` — plan validation, worker start/check/report, integrator integrate/close-gates/publish, next/status. `python3 tools/cloudctl.py -h`
* `featctl.py` — feature registry: `validate --root docs/spec`, `required`, `gate-check`, `collect` (JUnit XML / Playwright JSON → feature results)
* `uictl.py` — UI overlay validation: `validate --root docs/spec`
* `ui_snapshot.py` + `ui_snapshot.cjs` — integrator-only UI progress screenshots after each wave (system Chrome via a pinned playwright-core,
  no browser download) into `docs/ui-progress/WNN/` + `INDEX.md`; never blocks integration. `python3 tools/ui_snapshot.py --wave N --commit`
* `test_cloudctl.py` — unit tests: `python3 -m unittest discover -s tools -p 'test_*.py'`
