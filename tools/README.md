# tools (Python 3.11+, standard library only)

* `cloudctl.py` — plan validation, worker start/check/report, integrator integrate/close-gates/publish, next/status. `python3 tools/cloudctl.py -h`
* `featctl.py` — feature registry: `validate --root docs/spec`, `required`, `gate-check`, `collect` (JUnit XML / Playwright JSON → feature results)
* `uictl.py` — UI overlay validation: `validate --root docs/spec`
* `test_cloudctl.py` — unit tests: `python3 -m unittest discover -s tools -p 'test_*.py'`
