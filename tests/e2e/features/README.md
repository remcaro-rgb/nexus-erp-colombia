# P22c feature e2e specs (FEATURES-ADDENDUM UI tests, gate G3)

One spec per mandatory UI test id: `T-<ID>.<topic>.spec.ts`, test titles start with the id. They run **only** through their
pytest runner (`tests/cross_module/test_xm_e2e_*.py`, `qa_harness/e2e.py`), which

1. builds a synthetic world on the real PostgreSQL test database with the module kits' real services (P04 policy engine
   and local OIDC tokens, P09 inventory, P05 ledger, P11 purchasing, P16 treasury, …), rows committed (`transactional_db`);
2. serves the real Django app on `127.0.0.1:<ephemeral>`;
3. runs Playwright (system Chrome, es-CO, America/Bogota) with `--grep <ID>`; `support/stack.ts` starts the P08 Vite dev
   server with `/api` proxied to that origin and mounts the real app (`support/app-main.tsx`) with a synthetic signed-in
   session (`setAuth`), so every call goes through the real verifier and policy.

```bash
make db-setup
uv run --frozen pytest tests/cross_module/test_xm_e2e_inventory_pres.py -q -rx     # or: make test-ids IDS="T-INV-PRES-05"
```

Rules
* Every step of the acceptance text is done in the UI. When the product offers **no UI** for a step, the spec does it
  through the public API with `app.gap("DEF-P22-0xx", "what is missing", fn)` so the later steps still run; the gap fails
  the test. The runner reports a spec that fails *only* on exactly its registered gaps as strict XFAIL (never PASSED,
  `featctl` counts it as not passed); any other failure, or a registered gap that is no longer hit, fails.
* `augment: true` in the runner adds pages their owners delivered in their own folders but did not register in the shell
  descriptor (CCRs P09c-1, P11b-5, …); the missing registration is a separate strict-xfail defect.
* Keyboard (`tabTo`) and accessible names (`expectLabelledControls`) are checked in every spec; uncaught page errors fail.
* No secrets: tokens are synthetic LocalIssuer tokens of a throw-away tenant, written to a 0600 temp file, never printed.
