"""IAM data model (P04 + FEATURES-ADDENDUM F-ACC-BRANCH / F-PORTAL-CORE).

Every table carries ``tenant_id`` and is protected by PostgreSQL RLS
(``iam.rls``, migration 0001). Company-scoped tables also check
``company_id`` against ``app.company_ids``. ``Company`` exposes
``UNIQUE (tenant_id, id)`` for the kernel composite FKs and ``Branch`` exposes
``UNIQUE (tenant_id, company_id, id)`` for warehouse/POS composite FKs.

The cost-center master is NOT here (superseded by F-ACC-DIM, owned by P05):
role scopes reference DimensionNode ids through ``kernel.interfaces.Dimensions``.
"""

from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from kernel import clock, ids
from kernel.models import AuditedModel, CompanyModel, TenantModel


class Tenant(AuditedModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        SUSPENDED = "SUSPENDED"

    id = models.UUIDField(primary_key=True, default=ids.new_id, editable=False)
    # Equal to ``id``: lets the generic tenant RLS predicate apply to this table too.
    tenant_id = models.UUIDField(editable=False)
    name = models.CharField(max_length=200)
    plan = models.CharField(max_length=40, default="dev")
    region = models.CharField(max_length=40, default="co")
    data_residency = models.CharField(max_length=40, default="co")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "iam_tenant"
        constraints = [models.CheckConstraint(condition=Q(tenant_id=models.F("id")), name="iam_tenant_self_ck")]


class Company(TenantModel, AuditedModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        ARCHIVED = "ARCHIVED"

    code = models.CharField(max_length=40)
    nit = models.CharField(max_length=15)
    dv = models.CharField(max_length=1)
    legal_name = models.CharField(max_length=200)
    niif_group = models.CharField(max_length=8, default="2")
    currency = models.CharField(max_length=3, default="COP")
    municipality_code = models.CharField(max_length=5, blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "iam_company"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "id"], name="iam_company_tenant_id_uq"),
            models.UniqueConstraint(fields=["tenant_id", "code"], name="iam_company_code_uq"),
        ]


class Branch(CompanyModel, AuditedModel):
    """Sucursal (F-ACC-BRANCH). ``company_id`` is immutable."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        ARCHIVED = "ARCHIVED"

    #: a branch-scoped role sees the branches it is scoped to (policy engine)
    IAM_SCOPE_FIELDS: ClassVar[dict[str, str]] = {"branch_ids": "id"}

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=255, blank=True, default="")
    municipality_code = models.CharField(max_length=5, null=True, blank=True)
    phone = models.CharField(max_length=25, null=True, blank=True)
    default_business_unit_id = models.UUIDField(null=True, blank=True)
    default_warehouse_id = models.UUIDField(null=True, blank=True)
    is_head_office = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    valid_from = models.DateField(default=clock.business_date)
    valid_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "iam_branch"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "company_id", "id"], name="iam_branch_company_id_uq"),
            models.UniqueConstraint(Lower("code"), "tenant_id", "company_id", name="iam_branch_code_uq"),
            # At most one ACTIVE head office per company (the "at least one" half is checked by the
            # service and by a deferred constraint trigger, see migration 0001).
            models.UniqueConstraint(
                fields=["tenant_id", "company_id"],
                condition=Q(is_head_office=True, status="ACTIVE"),
                name="iam_branch_one_head_office_uq",
            ),
        ]


class User(TenantModel, AuditedModel):
    """Mirror of the IdP subject. ``kind=EXTERNAL`` rows belong to portal accounts."""

    class Kind(models.TextChoices):
        INTERNAL = "INTERNAL"
        EXTERNAL = "EXTERNAL"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        SUSPENDED = "SUSPENDED"
        REVOKED = "REVOKED"

    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.INTERNAL)
    idp_subject = models.CharField(max_length=255, null=True, blank=True)
    email = models.EmailField(max_length=254)
    display_name = models.CharField(max_length=200, blank=True, default="")
    mfa_enabled = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "iam_user"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "id"], name="iam_user_tenant_id_uq"),
            models.UniqueConstraint(
                fields=["tenant_id", "idp_subject"],
                condition=Q(idp_subject__isnull=False),
                name="iam_user_subject_uq",
            ),
        ]


class Membership(CompanyModel):
    """User ↔ company link (``Membership(user, tenant, companies[])``, one row per company)."""

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="memberships")
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=clock.now, editable=False)

    class Meta:
        db_table = "iam_membership"
        constraints = [models.UniqueConstraint(fields=["user", "company_id"], name="iam_membership_uq")]


class Role(TenantModel, AuditedModel):
    """Role template copy (contracts/src/roles.yaml) or custom role.

    ``grants``/``denies`` are fnmatch patterns over permission codes; deny wins.
    """

    key = models.CharField(max_length=80)
    name = models.CharField(max_length=200)
    grants = models.JSONField(default=list)
    denies = models.JSONField(default=list)
    max_classification = models.CharField(max_length=32, default="internal")
    requires_mfa = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "iam_role"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "key"], name="iam_role_key_uq"),
            models.UniqueConstraint(fields=["tenant_id", "id"], name="iam_role_tenant_id_uq"),
        ]


class RoleAssignment(CompanyModel, AuditedModel):
    """``(user, role, company, scope, valid_from/to)``.

    ``scope`` keys (all optional, AND-combined; absent = whole company):
    ``branch_ids``, ``warehouse_ids``, ``cost_center_ids`` (subtree roots; each id
    grants its whole subtree through ``kernel.interfaces.Dimensions``).
    A DB trigger rejects assignments to EXTERNAL users (``external_role_forbidden``).
    """

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="assignments")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="assignments")
    scope = models.JSONField(default=dict)
    active = models.BooleanField(default=True)
    valid_from = models.DateField(default=clock.business_date)
    valid_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "iam_role_assignment"


class RowPolicy(TenantModel, AuditedModel):
    """ABAC row rule (JSON language, compiled to Django ``Q``). Deny overrides allow.

    ``rule`` = ``{"resource": "sales.invoice", "effect": "allow"|"deny", "actions": [...],
    "where": {"all"|"any": [{"attr", "op", "value"}]}}``; ``role`` null = every role.
    """

    role = models.ForeignKey(Role, null=True, blank=True, on_delete=models.PROTECT, related_name="row_policies")
    resource = models.CharField(max_length=120)
    rule = models.JSONField()
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "iam_row_policy"


class FieldPolicy(TenantModel, AuditedModel):
    """Per role (null = all roles) and field: readable / writable / mask.

    Unreadable fields are omitted from responses (never null); readable fields with
    ``mask != none`` are returned masked (``full``, ``last4``, ``hash``, ``range``).
    """

    class Mask(models.TextChoices):
        NONE = "none"
        FULL = "full"
        LAST4 = "last4"
        HASH = "hash"
        RANGE = "range"

    role = models.ForeignKey(Role, null=True, blank=True, on_delete=models.PROTECT, related_name="field_policies")
    resource = models.CharField(max_length=120)
    field = models.CharField(max_length=120)
    readable = models.BooleanField(default=False)
    writable = models.BooleanField(default=False)
    mask = models.CharField(max_length=8, choices=Mask.choices, default=Mask.NONE)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "iam_field_policy"


class PolicyVersion(models.Model):
    """Per-tenant policy version; bumped by every role/policy/assignment/account change."""

    tenant_id = models.UUIDField(primary_key=True)
    version = models.PositiveBigIntegerField(default=1)

    class Meta:
        db_table = "iam_policy_version"


class ExternalAccount(CompanyModel, AuditedModel):
    """Portal identity (F-PORTAL-CORE): exactly one party or employee in one company."""

    class Kind(models.TextChoices):
        SUPPLIER = "supplier"
        CUSTOMER = "customer"
        EMPLOYEE = "employee"

    class ExternalStatus(models.TextChoices):
        INVITED = "INVITED"
        ACTIVE = "ACTIVE"
        SUSPENDED = "SUSPENDED"
        REVOKED = "REVOKED"
        EXPIRED = "EXPIRED"

    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name="external_account")
    portal_kind = models.CharField(max_length=16, choices=Kind.choices)
    party_id = models.UUIDField(null=True, blank=True)
    party_kind = models.CharField(max_length=16, blank=True, default="")
    employee_id = models.UUIDField(null=True, blank=True)
    email = models.EmailField(max_length=254)
    external_status = models.CharField(
        max_length=16, choices=ExternalStatus.choices, default=ExternalStatus.INVITED
    )
    invited_by = models.UUIDField(null=True)
    invited_at = models.DateTimeField(default=clock.now)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "iam_external_account"
        constraints = [
            # exactly one of party / employee (D-018)
            models.CheckConstraint(
                condition=(Q(party_id__isnull=False, employee_id__isnull=True, portal_kind__in=["supplier", "customer"]))
                | Q(party_id__isnull=True, employee_id__isnull=False, portal_kind="employee"),
                name="iam_external_account_one_subject_ck",
            ),
        ]


class PortalInvitation(TenantModel):
    account = models.ForeignKey(ExternalAccount, on_delete=models.PROTECT, related_name="invitations")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=clock.now)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "iam_portal_invitation"
