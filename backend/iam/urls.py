"""IAM routes (internal ``/api/v1/iam/*`` and portal ``/api/portal/v1/*``).

Mounted by ``path("", include("iam.urls"))`` in ``erp.urls`` (CCR P04-1; until then
:func:`iam.install.ensure_installed` appends it at test start). Action routes
(``…:archive``) come before the detail routes because ``<str:pk>`` would match them.
"""

from django.urls import path

from iam.api import views as api_views
from iam.branches import views as branch_views
from iam.external import views as ext_views
from iam.roles import views as role_views

urlpatterns = [
    path("api/v1/iam/me", api_views.MeView.as_view(), name="iam-me"),
    path("api/v1/iam/cost-centers", api_views.CostCentersView.as_view(), name="iam-cost-centers"),
    path("api/v1/iam/cost-centers/<str:pk>", api_views.CostCenterDetailView.as_view(), name="iam-cost-center"),
    path(
        "api/v1/iam/companies/<str:company_id>/branches",
        branch_views.CompanyBranchesView.as_view(),
        name="iam-company-branches",
    ),
    path("api/v1/iam/branches", branch_views.BranchesView.as_view(), name="iam-branches"),
    path("api/v1/iam/branches/<str:pk>:archive", branch_views.BranchArchiveView.as_view(), name="iam-branch-archive"),
    path(
        "api/v1/iam/branches/<str:pk>:unarchive",
        branch_views.BranchUnarchiveView.as_view(),
        name="iam-branch-unarchive",
    ),
    path("api/v1/iam/branches/<str:pk>", branch_views.BranchDetailView.as_view(), name="iam-branch"),
    path("api/v1/iam/roles", role_views.RolesView.as_view(), name="iam-roles"),
    path("api/v1/iam/role-assignments", role_views.RoleAssignmentsView.as_view(), name="iam-role-assignments"),
    path(
        "api/v1/iam/role-assignments/<str:pk>",
        role_views.RoleAssignmentDetailView.as_view(),
        name="iam-role-assignment",
    ),
    path("api/v1/iam/external-accounts", ext_views.ExternalAccountsView.as_view(), name="iam-external-accounts"),
    path("api/v1/iam/external-accounts/<str:pk>:suspend", ext_views.SuspendView.as_view(), name="iam-ext-suspend"),
    path(
        "api/v1/iam/external-accounts/<str:pk>:reactivate",
        ext_views.ReactivateView.as_view(),
        name="iam-ext-reactivate",
    ),
    path("api/v1/iam/external-accounts/<str:pk>:revoke", ext_views.RevokeView.as_view(), name="iam-ext-revoke"),
    path("api/v1/iam/external-accounts/<str:pk>:resend", ext_views.ResendView.as_view(), name="iam-ext-resend"),
    path(
        "api/v1/iam/external-accounts/<str:pk>",
        ext_views.ExternalAccountDetailView.as_view(),
        name="iam-external-account",
    ),
    path("api/portal/v1/me", ext_views.PortalMeView.as_view(), name="portal-me"),
    path("api/portal/v1/context", ext_views.PortalContextView.as_view(), name="portal-context"),
    path(
        "api/portal/v1/invitations:accept",
        ext_views.PortalAcceptInvitationView.as_view(),
        name="portal-invitation-accept",
    ),
]
