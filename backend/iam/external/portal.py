"""Portal API base classes (``/api/portal/v1/*``) — used by P10/P11/P13 portal endpoints.

* Authentication: :class:`iam.authn.PortalAuthentication` (``erp-portal`` tokens of
  ACTIVE external accounts only); internal tokens are rejected.
* Scope: the party/employee and company come from the account bound to the token. List
  and detail querysets are filtered in the repository layer by
  :meth:`iam.policy.engine.PolicyEngine.scope_queryset` (company + party/employee);
  a foreign id is a 404 (``portal_document_not_owned`` semantics, no existence oracle).
* Scope parameters in the query string or body (``party_id``, ``company_id``,
  ``employee_id``, ``tenant_id``, ``external_account_id``, ``party``) are refused with
  ``external_account_scope_violation`` unless they equal the token's own values.
* ``portal_kind`` restricts an endpoint to one portal (``portal_kind_mismatch``).

Every portal view registers itself in :data:`REGISTRY` so the IDOR suite
(T-PORTAL-CORE-02) covers every portal endpoint automatically.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, ClassVar

from rest_framework.request import Request
from rest_framework.response import Response

from iam.api.base import SecuredView, parse_uuid
from iam.authn import PortalAuthentication
from iam.errors import ExternalAccountScopeViolation, PortalKindMismatch
from iam.policy.engine import Effective, get_engine
from kernel import tenancy
from kernel.errors import NotFound

SCOPE_PARAMS = ("party_id", "company_id", "employee_id", "tenant_id", "external_account_id", "party")
REGISTRY: list[type[PortalView]] = []


class PortalView(SecuredView):
    authentication_classes: ClassVar[list[Any]] = [PortalAuthentication]
    #: None = any portal kind
    portal_kind: ClassVar[str | None] = None
    #: already checked against the token by _check_scope_params
    ignored_params: ClassVar[tuple[str, ...]] = SCOPE_PARAMS

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        REGISTRY.append(cls)

    def check_access(self, request: Request) -> None:
        portal = self.effective.portal
        if portal is None:
            raise ExternalAccountScopeViolation("not a portal principal")
        if self.portal_kind is not None and portal.kind != self.portal_kind:
            raise PortalKindMismatch("this endpoint belongs to another portal")
        self._check_scope_params(request)
        super().check_access(request)

    #: fields an external user may receive (allowlist; never costs, margins, formulas…)
    portal_fields: ClassVar[tuple[str, ...]] = ("id",)

    def field_view(self) -> tuple[set[str], set[str], dict[str, str]]:
        return set(self.portal_fields), set(), {}

    def paginate(self, request: Request, queryset: Any, **kwargs: Any) -> Response:
        kwargs.pop("filter_fields", None)
        kwargs.pop("sort_fields", None)
        return super().paginate(request, queryset, **kwargs)

    @property
    def effective(self) -> Effective:
        return get_engine().effective(self.ctx)

    def _own_values(self) -> dict[str, set[str]]:
        portal = self.effective.portal
        assert portal is not None  # noqa: S101 - checked in initial()
        own = {
            "company_id": {str(portal.company_id)},
            "tenant_id": {str(self.ctx.tenant_id)},
            "external_account_id": {str(portal.account_id)},
            "party_id": {str(portal.party_id)} if portal.party_id else set(),
            "employee_id": {str(portal.employee_id)} if portal.employee_id else set(),
        }
        own["party"] = own["party_id"]
        return own

    def _check_scope_params(self, request: Request) -> None:
        own = self._own_values()
        body = request.data if isinstance(request.data, Mapping) else {}
        for source in (request.query_params, body):
            for name in SCOPE_PARAMS:
                if name not in source:
                    continue
                value = source.get(name)
                if isinstance(value, Mapping):
                    value = value.get("id")
                if str(value) not in own[name]:
                    raise ExternalAccountScopeViolation("the request names another party or company")


class PortalCollectionView(PortalView):
    """Generic own-documents list/detail for a model with ``company_id`` + ``party_id``/``employee_id``."""

    model: ClassVar[Any] = None
    search_fields: ClassVar[tuple[str, ...]] = ()

    def get(self, request: Request, pk: str | None = None) -> Response:
        action = self.permissions["GET"]
        qs = get_engine().scope_queryset(self.ctx, action, self.model.objects.all())
        if pk is None:
            return self.paginate(request, qs, search_fields=self.search_fields)
        with tenancy.scope(self.ctx):
            obj = qs.filter(pk=parse_uuid(pk)).first()
            if obj is None:
                raise NotFound("not found", code="portal_document_not_owned")
            return Response(self.serialize(obj))


def own_account_id(view: PortalView) -> uuid.UUID:
    portal = view.effective.portal
    assert portal is not None  # noqa: S101 - PortalView.initial guarantees it
    return portal.account_id
