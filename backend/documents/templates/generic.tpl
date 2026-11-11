# {{ title|default:document_type }}
> Documento generado por Nexus ERP (DEV) · plantilla genérica
---
@cols 35,65
{% for f in fields %}|{{ f.label }}|{{ f.value }}|
{% endfor %}{% for t in tables %}## {{ t.title }}
{% for r in t.rows %}|{{ r.0 }}|{{ r.1 }}|
{% endfor %}{% endfor %}