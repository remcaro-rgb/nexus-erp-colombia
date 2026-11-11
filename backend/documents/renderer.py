"""``kernel.interfaces.DocumentRenderer`` implementation (D-005) — pure: no database, no network, no clock.

``render(template_code, data)`` picks the built-in template of that code (``documents.builtin``), the label layout
for ``inventory.label`` or the generic key/value layout, renders the sandboxed markup against the plain data and
lays it out as PDF (or HTML). The same template version + data always produce the same bytes (deterministic hash).
:func:`render_body` renders an explicit template body (company templates, previews) with the same pipeline.

Every document carries a visible watermark; without an explicit one the DEV label :data:`DEV_LABEL` is used
(DEV-only system: every document is synthetic).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Literal

from documents import builtin, labels, layout
from documents.markup import TemplateError, render
from kernel.interfaces.documents import RenderedDocument

DEV_LABEL = "DOCUMENTO SINTÉTICO — ENTORNO DEV, sin validez legal ni fiscal"
MAX_DEPTH = 12
MediaType = Literal["application/pdf", "text/html"]


def plain(value: Any, depth: int = 0) -> Any:
    """JSON-like plain data only (dict/list/str/int/Decimal/bool/None); dates, UUIDs → text; floats → Decimal."""
    if depth > MAX_DEPTH:
        raise TemplateError("data nested too deeply")
    if value is None or isinstance(value, bool | int | str | Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v, depth + 1) for v in value]
    return str(value)


def _label_for(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


def generic_context(template_code: str, data: Mapping[str, Any]) -> dict[str, Any]:
    """Fields/tables view of arbitrary plain data for the generic layout (deterministic key order)."""
    fields: list[dict[str, str]] = []
    tables: list[dict[str, Any]] = []

    def scalar(v: Any) -> str:
        if isinstance(v, Mapping) and "amount" in v:
            return f"{v.get('amount')} {v.get('currency', '')}".strip()
        if isinstance(v, Mapping):
            return ", ".join(f"{k}: {scalar(x)}" for k, x in sorted(v.items()))
        if isinstance(v, list):
            return ", ".join(scalar(x) for x in v)
        return "" if v is None else str(v)

    for key in sorted(data):
        value = data[key]
        if isinstance(value, list) and value and all(isinstance(x, Mapping) for x in value):
            rows = [[_label_for(str(k)), scalar(x[k])] for x in value for k in sorted(x)]
            tables.append({"title": _label_for(key), "rows": rows})
        else:
            fields.append({"label": _label_for(key), "value": scalar(value)})
    return {"title": data.get("title") or template_code, "document_type": template_code, "fields": fields,
            "tables": tables}  # fmt: skip


def render_body(
    body: str,
    template_code: str,
    version: str,
    data: Mapping[str, Any],
    *,
    media_type: MediaType = "application/pdf",
    watermark: str | None = None,
    title: str | None = None,
) -> RenderedDocument:
    context = plain(data)
    mark = watermark or DEV_LABEL
    text = render(body, context)
    blocks = layout.blocks(text)
    heading = title or str(context.get("title") or template_code)
    if media_type == "text/html":
        content = layout.to_html(blocks, title=heading, watermark=mark).encode()
    elif media_type == "application/pdf":
        content = layout.to_pdf(blocks, title=heading, watermark=mark, footer=f"{template_code} v{version}")
    else:
        raise TemplateError(f"unsupported media type {media_type}")
    return RenderedDocument(content, media_type, template_code, version, mark)


class PdfDocumentRenderer:
    """The registered ``DocumentRenderer`` (pure-Python PDF engine; see CCR P17a-1 for WeasyPrint)."""

    def render(
        self,
        template_code: str,
        data: dict[str, Any],
        *,
        locale: str = "es-CO",
        media_type: Literal["application/pdf", "text/html"] = "application/pdf",
        watermark: str | None = None,
    ) -> RenderedDocument:
        if locale != "es-CO":
            raise TemplateError(f"unsupported locale {locale} (es-CO only, D-001)")
        if template_code == builtin.LABEL_TEMPLATE and media_type == "application/pdf":
            mark = watermark or DEV_LABEL
            content = labels.render_labels(plain(data), watermark=mark, title="Etiquetas")
            return RenderedDocument(content, media_type, template_code, builtin.LABEL_VERSION, mark)
        template = builtin.builtin(template_code)
        if template is None:
            generic = builtin.BUILTINS["generic"]
            return render_body(
                generic.body,
                template_code,
                generic.version,
                generic_context(template_code, plain(data)),
                media_type=media_type,
                watermark=watermark,
            )
        return render_body(
            template.body, template.code, template.version, data, media_type=media_type, watermark=watermark,
            title=template.name,
        )  # fmt: skip


RENDERER = PdfDocumentRenderer()
