"""Model mixin that records an audit event for every save/delete inside the active UoW."""

from __future__ import annotations

from typing import Any, ClassVar

from django.db import models
from django.forms.models import model_to_dict

from kernel.errors import UnitOfWorkRequired
from kernel.uow import current_uow


class AutoAuditMixin(models.Model):
    """``save()``/``delete()`` outside a unit of work raise; inside one they audit (same transaction)."""

    AUDIT_RESOURCE: ClassVar[str] = ""
    AUDIT_SENSITIVE: ClassVar[tuple[str, ...]] = ()
    AUDIT_EXCLUDE: ClassVar[tuple[str, ...]] = ("updated_at", "updated_by", "version")

    class Meta:
        abstract = True

    def _audit_state(self) -> dict[str, Any]:
        data = model_to_dict(self)
        data.update({f.attname: getattr(self, f.attname) for f in self._meta.concrete_fields})
        return {k: v for k, v in data.items() if k not in self.AUDIT_EXCLUDE}

    def _resource(self) -> str:
        return self.AUDIT_RESOURCE or self._meta.label_lower

    def save(self, *args: Any, **kwargs: Any) -> None:
        from workflow.audit.service import record_change

        uow = current_uow()
        if uow is None:
            raise UnitOfWorkRequired(f"{self._meta.label} must be saved inside a unit of work (audited)")
        before = None
        if not self._state.adding:
            stored = type(self)._default_manager.filter(pk=self.pk).first()
            before = stored._audit_state() if stored is not None else None
        action = "create" if before is None else "update"
        super().save(*args, **kwargs)
        record_change(
            f"{self._resource()}.{action}",
            self._resource(),
            self.pk,
            company_id=getattr(self, "company_id", None),
            before=before,
            after=self._audit_state(),
            sensitive=self.AUDIT_SENSITIVE,
            uow=uow,
        )

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        from workflow.audit.service import record_change

        uow = current_uow()
        if uow is None:
            raise UnitOfWorkRequired(f"{self._meta.label} must be deleted inside a unit of work (audited)")
        before, pk = self._audit_state(), self.pk
        result = super().delete(*args, **kwargs)
        record_change(
            f"{self._resource()}.delete", self._resource(), pk, company_id=getattr(self, "company_id", None),
            before=before, after=None, sensitive=self.AUDIT_SENSITIVE, uow=uow,
        )  # fmt: skip
        return result
