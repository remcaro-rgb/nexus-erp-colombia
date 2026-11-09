"""URL routes of the workflow module; included at ``api/v1/workflow/`` by ``erp.urls`` (CCR P07-1)."""

from django.urls import path

from workflow import api

app_name = "workflow"

urlpatterns = [
    path("formulas", api.FormulaListView.as_view(), name="formulas"),
    path("formulas:validate", api.FormulaValidateView.as_view(), name="formulas-validate"),
    path("formulas:simulate", api.FormulaSimulateView.as_view(), name="formulas-simulate"),
    path("formulas/<uuid:id>", api.FormulaDetailView.as_view(), name="formula"),
    path("formulas/<uuid:id>:submit", api.FormulaSubmitView.as_view(), name="formula-submit"),
    path("formulas/<uuid:id>:approve", api.FormulaApproveView.as_view(), name="formula-approve"),
    path("formulas/<uuid:id>:retire", api.FormulaRetireView.as_view(), name="formula-retire"),
    path("formulas/<uuid:id>/versions", api.FormulaVersionsView.as_view(), name="formula-versions"),
    path("approvals", api.ApprovalListView.as_view(), name="approvals"),
    path("approvals/<uuid:id>", api.ApprovalDetailView.as_view(), name="approval"),
    path("approvals/<uuid:id>:approve", api.ApprovalApproveView.as_view(), name="approval-approve"),
    path("approvals/<uuid:id>:reject", api.ApprovalRejectView.as_view(), name="approval-reject"),
    path("approvals/<uuid:id>:delegate", api.ApprovalDelegateView.as_view(), name="approval-delegate"),
    path("audit-events", api.AuditEventListView.as_view(), name="audit-events"),
    path("audit-events/<uuid:id>", api.AuditEventDetailView.as_view(), name="audit-event"),
    path("audit:verify", api.AuditVerifyView.as_view(), name="audit-verify"),
    path("scheduled-jobs", api.JobListView.as_view(), name="jobs"),
    path("scheduled-jobs/<uuid:id>", api.JobDetailView.as_view(), name="job"),
    path("scheduled-jobs/<uuid:id>:pause", api.JobPauseView.as_view(), name="job-pause"),
    path("scheduled-jobs/<uuid:id>:resume", api.JobResumeView.as_view(), name="job-resume"),
    path("scheduled-jobs/<uuid:id>:run-now", api.JobRunNowView.as_view(), name="job-run-now"),
    path("notifications", api.NotificationListView.as_view(), name="notifications"),
    path("notifications/<uuid:id>:mark-read", api.NotificationReadView.as_view(), name="notification-read"),
    path("webhooks", api.WebhookListView.as_view(), name="webhooks"),
    path("webhooks/<uuid:id>:test", api.WebhookTestView.as_view(), name="webhook-test"),
    path("webhooks/<uuid:id>/deliveries", api.WebhookDeliveriesView.as_view(), name="webhook-deliveries"),
    path(
        "webhooks/<uuid:id>/deliveries/<uuid:delivery_id>:replay",
        api.WebhookReplayView.as_view(),
        name="webhook-replay",
    ),
]

#: ``erp.urls`` adds: path("api/v1/kpis/aprobaciones", api.KpiView.as_view())
kpi_urlpatterns = [path("aprobaciones", api.KpiView.as_view(), name="kpis-aprobaciones")]
