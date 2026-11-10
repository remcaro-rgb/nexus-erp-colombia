attempt: 1

# FIX-UI-LOGIN-W03 progress

Plan:
- UL-1: dockerignore re-includes catalog.yaml + module-registry.json; runtime stage COPYs them to /contracts/... and /docs/spec/ui/...
- UL-2: UiContextView activates request.erp_context (tenancy.activate) for the handler; kernel.interfaces.ui.UiShellProvider; IAM provider registered at ready; extras companies/roles/permissions/user/session_idle_minutes/feature_flags.
- UL-3: /api/v1/ui/modules kernel view delegating to provider (503 without provider / registry).
- UL-4: iam/policy/nav.py resolves registry nav codes against effective catalog codes; NAV_ALIASES table; /iam/me + /ui/context permissions = catalog ∪ nav.
- UL-5: SPA OIDC auth code + PKCE in frontend/src/lib/auth, entry screen, /oidc/callback, session restore, logout.
- UL-6: API client bearer header, 401 → refresh once → retry → sign-in redirect.
