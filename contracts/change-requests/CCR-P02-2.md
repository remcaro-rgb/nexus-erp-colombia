# CCR-P02-2 — kernel interface drafts (to P01, owner of backend/kernel/interfaces/)

Status: OPEN (P02, wave 1) — informational, no contract change.

`contracts/interfaces/kernel_interfaces_draft.py` holds typed `Protocol` drafts for every interface P01 must provide in `backend/kernel/interfaces/`
(drafted at G0, frozen v1 at G1): PostingService, StockService, TaxService, ParameterProvider, PolicyService, AuditService, WorkflowService,
SchedulerService, FiscalGateway, DocumentRenderer, ReportQueryService, NotificationService, FileStore, and the FEATURES-ADDENDUM stubs Dimensions,
Notifications, Inventory, Labels, PayrollPayables, FiscalDocuments, PaymentStatus, ExportFormat (T-X-QA-07 at G0 checks P01's real module).
Request: copy/split them into `backend/kernel/interfaces/*.py` keeping names, signatures, docstring contracts and error codes (contracts/errors.yaml);
publish test doubles. Deviations → reply in this CCR so P02 updates the draft.
