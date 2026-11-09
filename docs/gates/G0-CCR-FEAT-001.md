# G0 attempt 1 — CCR-FEAT-001 record

* change request: **CCR-FEAT-001** (FEATURES-ADDENDUM 2.2.0-dev, status BINDING)
* recorded by: integrator, wave 01, G0 attempt 1 (2026-11-09)
* adopted documents: `docs/spec/FEATURES-ADDENDUM.md`, `docs/spec/features/feature-registry.json`, `docs/spec/DECISIONS-DEFAULTS.md`
* feature-registry sha256: `a62fa1d937c13533ddc08f22b87a65d9bce24d662e6df7e8996f3aa7ee939ab4`
* integrated head at recording: `6b1aff0` (P01, P02, P03 accepted)
* scope at G0: P02 contracts carry the FEATURES-ADDENDUM APIs/events/error codes; every
  `kernel.interfaces` stub named in FEATURES-ADDENDUM exists in P01's `backend/kernel/interfaces/`
  as a typed `typing.Protocol` with an in-memory test double in `backend/kernel/testing/fakes.py`.
* verified by: `tests/gates/test_g0_features.py::test_T_X_QA_07_*`
