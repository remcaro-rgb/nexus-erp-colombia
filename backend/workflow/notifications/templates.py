"""Notification templates (es-CO). ``sensitive`` keys never reach e-mail bodies
unless the recipient's field policy allows them."""

from __future__ import annotations

from dataclasses import dataclass
from string import Template


@dataclass(frozen=True)
class NotificationTemplate:
    code: str
    title: str
    body: str
    link: str = ""
    permission: str = ""  # needed to see the notification at all
    sensitive: tuple[str, ...] = ()


TEMPLATES: dict[str, NotificationTemplate] = {
    t.code: t
    for t in (
        NotificationTemplate(
            "aprobacion_pendiente",
            "Aprobación pendiente",
            "Tienes una aprobación pendiente: $documento.",
            "/m/aprobaciones/bandeja",
            "workflow.approval.read",
        ),
        NotificationTemplate(
            "aprobacion_decidida",
            "Aprobación $estado",
            "Tu solicitud $documento quedó en estado $estado.",
            "/m/aprobaciones/bandeja",
            "workflow.approval.read",
        ),
        NotificationTemplate(
            "aprobacion_escalada",
            "Aprobación escalada",
            "La aprobación de $documento superó su plazo y fue escalada.",
            "/m/aprobaciones/bandeja?vencidas=1",
            "workflow.approval.read",
        ),
        NotificationTemplate(
            "tarea_fallida",
            "Tarea programada fallida",
            "La tarea $tarea falló: $motivo.",
            "/m/aprobaciones/tareas",
            "workflow.scheduled_job.read",
        ),
        NotificationTemplate(
            "webhook_fallido",
            "Webhook sin entregar",
            "El webhook $webhook no pudo entregar el evento $evento.",
            "/m/aprobaciones",
            "workflow.webhook.read",
        ),
        NotificationTemplate(
            "dsr_vencimiento",
            "Solicitud de titular por vencer",
            "La solicitud $radicado vence el $fecha.",
            "/m/aprobaciones",
            "workflow.dsr_request.read",
            sensitive=("titular",),
        ),
        NotificationTemplate("generica", "$titulo", "$mensaje"),
    )
}


def render(template: NotificationTemplate, data: dict[str, object], *, hide: set[str]) -> tuple[str, str]:
    values = {k: ("•••" if k in hide else str(v)) for k, v in data.items()}
    return Template(template.title).safe_substitute(values)[:200], Template(template.body).safe_substitute(values)
