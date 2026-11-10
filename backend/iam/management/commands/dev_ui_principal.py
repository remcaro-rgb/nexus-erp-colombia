"""``manage.py dev_ui_principal --subject <keycloak user id> --username <name> --tenant-id <uuid>`` (DEV only).

FIX-CI-W02 UI-3. Binds a throw-away Keycloak user (created at run time by the integrator's
``tools/dev_login.py``) to an ERP principal of a synthetic tenant so the UI preview can sign in
before P21's demo tenant exists. Idempotent; a second run with the same arguments writes nothing.

Creates or updates, inside one unit of work bound to that tenant (RLS applies, no bypass):

* the tenant ``<tenant-id>`` named ``Vista previa UI (sintético)``;
* one ACTIVE company with a synthetic NIT (``900000099`` + valid check digit, DEV fixture range
  ``9000000xx`` of ``iam.testing.world``), labelled synthetic;
* an ACTIVE INTERNAL ``iam.User`` with ``idp_subject=<subject>`` and its default ``Membership``;
* the role ``ui-preview-readonly``: grants only the ``*.*.read`` codes of
  ``contracts/permissions/catalog.yaml`` and denies every other action (create, update, delete,
  approve, post, void, export, admin) — the normal permission checks decide, nothing is bypassed;
* an active assignment of that role in the company.

Refuses (exit 2) unless ``settings.ERP_ENVIRONMENT == "dev"``. Prints exactly one line
``DEV-UI-PRINCIPAL OK tenant=<uuid> company=<id> user=<id> permissions=<n>`` (``n`` = catalog codes
the role grants; 0 when the catalog is not shipped with the image). Prints nothing secret.
"""

from __future__ import annotations

import fnmatch
import uuid
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from kernel.tenancy import Channel, PrincipalType, TenantContext
from kernel.uow import unit_of_work

TENANT_NAME = "Vista previa UI (sintético)"
COMPANY_CODE = "VISTA-UI"
COMPANY_NIT = "900000099"  # synthetic DEV fixture range 9000000xx (never a real NIT)
COMPANY_NAME = "Vista Previa UI S.A.S. (sintética)"
ROLE_KEY = "ui-preview-readonly"
ROLE_NAME = "Vista previa UI — solo lectura (sintético)"
READ_ACTIONS = ("read",)
ROLE_GRANTS = [f"*.*.{a}" for a in READ_ACTIONS]
ROLE_DENIES = [f"*.*.{a}" for a in ("create", "update", "delete", "approve", "post", "void", "export", "admin")]
#: Fixed service principal stamped on the rows this command writes.
SEED_PRINCIPAL = uuid.UUID("00000000-0000-7000-8000-00000000d0e1")


def granted_codes(codes: tuple[str, ...], grants: list[str], denies: list[str]) -> list[str]:
    """Codes a role with ``grants``/``denies`` (fnmatch patterns, deny wins) allows — as the policy engine does."""
    return [
        c
        for c in codes
        if any(fnmatch.fnmatchcase(c, g) for g in grants) and not any(fnmatch.fnmatchcase(c, d) for d in denies)
    ]


def company_id_for(tenant_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(tenant_id, "dev-ui-preview/company")


class Command(BaseCommand):
    help = "DEV only: bind a Keycloak subject to a read-only internal user of a synthetic UI-preview tenant."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--subject", required=True, help="Keycloak user id (token `sub`)")
        parser.add_argument("--username", required=True)
        parser.add_argument("--tenant-id", required=True, type=uuid.UUID)

    def handle(self, *args: Any, **options: Any) -> None:
        if getattr(settings, "ERP_ENVIRONMENT", None) != "dev":
            raise CommandError("dev_ui_principal is DEV-only (ERP_ENVIRONMENT must be 'dev')", returncode=2)
        subject = str(options["subject"]).strip()
        username = str(options["username"]).strip()
        tenant_id: uuid.UUID = options["tenant_id"]
        if not subject or len(subject) > 255:
            raise CommandError("--subject must have 1..255 characters", returncode=2)
        if not username or len(username) > 150 or not username.replace("-", "").replace("_", "").isalnum():
            raise CommandError("--username must be 1..150 letters, digits, '-' or '_'", returncode=2)
        company_id, user_id, n = ensure_principal(tenant_id, subject, username)
        self.stdout.write(f"DEV-UI-PRINCIPAL OK tenant={tenant_id} company={company_id} user={user_id} permissions={n}")


def ensure_principal(tenant_id: uuid.UUID, subject: str, username: str) -> tuple[uuid.UUID, uuid.UUID, int]:
    """Create/update the synthetic principal (idempotent). Returns (company id, user id, granted code count)."""
    from iam.api.views import catalog_codes
    from iam.models import Company, Membership, Role, RoleAssignment, Tenant, User
    from iam.policy.engine import PolicyEngine
    from localization_co.nit import check_digit

    company_id = company_id_for(tenant_id)
    ctx = TenantContext(tenant_id, frozenset({company_id}), SEED_PRINCIPAL, PrincipalType.SERVICE, channel=Channel.JOB)
    stamp = {"created_by": SEED_PRINCIPAL, "updated_by": SEED_PRINCIPAL}
    changed = False
    with unit_of_work(ctx):
        tenant = Tenant.objects.filter(id=tenant_id).first()
        if tenant is None:
            Tenant.objects.create(id=tenant_id, tenant_id=tenant_id, name=TENANT_NAME, **stamp)
            changed = True
        elif tenant.name != TENANT_NAME or tenant.status != Tenant.Status.ACTIVE:
            Tenant.objects.filter(id=tenant_id).update(name=TENANT_NAME, status=Tenant.Status.ACTIVE)
            changed = True

        company_fields = {
            "code": COMPANY_CODE,
            "nit": COMPANY_NIT,
            "dv": str(check_digit(COMPANY_NIT)),
            "legal_name": COMPANY_NAME,
            "status": Company.Status.ACTIVE,
        }
        company = Company.objects.filter(tenant_id=tenant_id, id=company_id).first()
        if company is None:
            Company.objects.create(id=company_id, tenant_id=tenant_id, **company_fields, **stamp)
            changed = True
        elif any(getattr(company, k) != v for k, v in company_fields.items()):
            Company.objects.filter(tenant_id=tenant_id, id=company_id).update(**company_fields)
            changed = True

        user_fields = {
            "kind": User.Kind.INTERNAL,
            "status": User.Status.ACTIVE,
            "email": f"{username}@example.invalid",
            "display_name": f"{username} (sintético)",
        }
        user = User.objects.filter(tenant_id=tenant_id, idp_subject=subject).first()
        if user is None:
            user = User.objects.create(tenant_id=tenant_id, idp_subject=subject, **user_fields, **stamp)
            changed = True
        elif any(getattr(user, k) != v for k, v in user_fields.items()):
            User.objects.filter(tenant_id=tenant_id, id=user.id).update(**user_fields)
            changed = True

        membership = Membership.objects.filter(tenant_id=tenant_id, company_id=company_id, user_id=user.id).first()
        if membership is None:
            Membership.objects.create(tenant_id=tenant_id, company_id=company_id, user=user, is_default=True)
            changed = True
        elif not membership.is_default:
            Membership.objects.filter(pk=membership.pk).update(is_default=True)
            changed = True

        role_fields: dict[str, Any] = {
            "name": ROLE_NAME,
            "grants": ROLE_GRANTS,
            "denies": ROLE_DENIES,
            "max_classification": "internal",
            "requires_mfa": False,
            "active": True,
        }
        role = Role.objects.filter(tenant_id=tenant_id, key=ROLE_KEY).first()
        if role is None:
            role = Role.objects.create(tenant_id=tenant_id, key=ROLE_KEY, **role_fields, **stamp)
            changed = True
        elif any(getattr(role, k) != v for k, v in role_fields.items()):
            Role.objects.filter(tenant_id=tenant_id, id=role.id).update(**role_fields)
            changed = True

        assignment = RoleAssignment.objects.filter(
            tenant_id=tenant_id, company_id=company_id, user_id=user.id, role_id=role.id
        ).first()
        if assignment is None:
            RoleAssignment.objects.create(
                tenant_id=tenant_id, company_id=company_id, user=user, role=role, scope={}, **stamp
            )
            changed = True
        elif not assignment.active or assignment.scope or assignment.valid_to is not None:
            RoleAssignment.objects.filter(pk=assignment.pk).update(active=True, scope={}, valid_to=None)
            changed = True

        if changed:
            PolicyEngine.bump_version(tenant_id)
    return company_id, user.id, len(granted_codes(catalog_codes(), ROLE_GRANTS, ROLE_DENIES))
