"""Documents API (``contracts/openapi/modules/documents.yaml``): templates CRUD + ``:preview``, ``/render`` (sync PDF),
``/render:render-batch`` (202 Operation). Every handler authorizes through ``PolicyService``."""

from __future__ import annotations

import uuid
from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response

from documents import service
from documents.http import SecuredView, action_body, body, check_if_match, command, file_response, page
from documents.models import GeneratedDocument
from kernel.errors import NotFound
from kernel.idempotency import StoredResponse
from kernel.uow import UnitOfWork, run_in_unit_of_work

SCOPE = "documents"


class TemplateList(SecuredView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = service.templates_qs(ctx, company)
        q = request.query_params.get("q")
        if q:
            qs = qs.filter(name__icontains=q) | qs.filter(code__icontains=q)
        filters = {"status": "status", "code": "code", "document_type": "document_type"}
        return page(request, qs, service.template_json, filters)

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            return service.template_json(service.create_template(uow, company, body(request))), 201

        def headers(data: Any) -> dict[str, str]:
            return {"Location": f"/api/v1/documents/templates/{data['id']}"}

        return command(request, ctx, f"{SCOPE}.template.create", fn, etag=_etag, headers=headers)


def _etag(data: Any) -> str | None:
    from iam.api.base import etag_for

    return etag_for("documents.DocumentTemplate", data["id"], data["version"]) if isinstance(data, dict) else None


class TemplateDetail(SecuredView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        t = service.get_template(ctx, company, pk)
        response = Response(service.template_json(t))
        response["ETag"] = t.etag
        return response

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            t = service.get_template(ctx, company, pk)
            check_if_match(request, t, required=True)
            t = service.update_template(uow, t, body(request))
            return service.template_json(t), 200

        response = command(request, ctx, f"{SCOPE}.template.update", fn)
        if response.status_code == 200 and isinstance(response.data, dict):
            t = service.get_template(ctx, company, pk)
            response["ETag"] = t.etag
        return response

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            t = service.get_template(ctx, company, pk)
            check_if_match(request, t, required=False)
            service.archive_template(uow, t)
            return None, 204

        return command(request, ctx, f"{SCOPE}.template.delete", fn)


class TemplatePreview(SecuredView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        _, params = action_body(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            t = service.get_template(ctx, company, pk)
            return service.preview(uow, t, params.get("sample")), 200

        return command(request, ctx, f"{SCOPE}.template.preview", fn)


class Render(SecuredView):
    """Synchronous render of one small document → ``application/pdf`` (stored with hash + source link)."""

    def post(self, request: Request) -> Any:
        ctx, company = self.ctx_company(request)
        _, params = action_body(request)
        from documents.http import idempotency_key

        key = idempotency_key(request)

        def fn(uow: UnitOfWork) -> GeneratedDocument:
            rendered = service.render_request(uow, company, params)
            if rendered.stored is None:
                raise NotFound("document not stored")
            uow.set_response({"document_id": str(rendered.stored.id)}, 200)
            return rendered.stored

        result = run_in_unit_of_work(
            fn, ctx, idempotency_key=key, request={"path": request.path, "body": body(request)}, scope=f"{SCOPE}.render"
        )
        replayed = isinstance(result, StoredResponse)
        if isinstance(result, StoredResponse):
            doc_id = str((result.body or {}).get("document_id") or "")
            row = GeneratedDocument.objects.filter(company_id=company, id=doc_id).first() if doc_id else None
            if row is None:
                raise NotFound("document not found")
        else:
            row = result
        extension = "pdf" if row.media_type == "application/pdf" else "html"
        name = f"{(row.source_number or row.template_code).replace('/', '-')}.{extension}"
        return file_response(
            bytes(row.content),
            row.media_type,
            name,
            {"X-Document-Sha256": row.sha256, "X-Document-Id": str(row.id),
             "Idempotent-Replayed": "true" if replayed else "false"},
        )  # fmt: skip


class RenderBatchView(SecuredView):
    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        _, params = action_body(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            batch = service.create_batch(uow, company, params)
            service.run_batch(uow, batch)
            return service.operation_json(batch), 202

        def headers(data: Any) -> dict[str, str]:
            return {"Location": f"/api/v1/operations/{data['id']}"}

        return command(request, ctx, f"{SCOPE}.render_batch", fn, headers=headers)
