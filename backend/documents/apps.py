"""Django app of the documents module (P17a): ``kernel.interfaces.DocumentRenderer`` (D-005), company templates,
generated documents with hash + source link. Routes at ``api/v1/documents/`` (``erp.discovery``)."""

from django.apps import AppConfig


class DocumentsConfig(AppConfig):
    name = "documents"
    label = "documents"
    verbose_name = "Plantillas y documentos"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from documents import sources  # noqa: F401  (registers the RC / CE source providers)
        from documents.renderer import RENDERER
        from kernel import services
        from kernel.interfaces.documents import DocumentRenderer

        if services.optional(DocumentRenderer) is None:
            renderer: DocumentRenderer = RENDERER
            services.register(DocumentRenderer, renderer)
