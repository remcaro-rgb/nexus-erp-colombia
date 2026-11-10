# FIX-CI-W02 progress
attempt: 1

Plan:
* CW-1: rebuild the formula test payload without the forbidden token (concatenation), keep the case.
* CW-2: TaxAPIView/WorkflowView inherit iam.api.base.SecuredView; kernel.tenancy advisory-lock helpers; HttpxTransport -> workflow/webhooks/adapters.py.
* UI-1: discover backend/<app>/apps.py into INSTALLED_APPS (kernel, iam, rest sorted) and mount urls (URL_MOUNT attr); RLS migrations for tax/workflow/localization_co tenant tables.
* CW-3: fix the remaining mypy errors with real types.
* CW-4: Makefile typecheck -> $(MYPY) backend.
* UI-2: declare tenant_id in realm user profile (admin-only).
* UI-3: iam management command dev_ui_principal.
* DF-1: frontend.Dockerfile corepack after COPY of manifests.
* PW-1: frontend/playwright.config.ts limited to e2e specs.
* FE-1: trial-merge P08 and check tsc/vitest in features/{iam,tax,workflow}.
