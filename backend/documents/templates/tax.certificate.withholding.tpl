# {{ company.name }}
> NIT {{ company.nit }}{% if company.dv %}-{{ company.dv }}{% endif %}{% if branch.name %} · Sucursal {{ branch.name }}{% endif %}
## CERTIFICADO DE RETENCIÓN {{ kind|upper }} — AÑO {{ year }}
Retenido: {{ party.name }} · NIT {{ party.tax_id }}
---
@cols 40,30,30
|!Concepto|Base|Retenido|
{% for l in lines %}|{{ l.concept }}|{{ l.base|money }}|{{ l.amount|money }}|
{% endfor %}---
**Total retenido: {{ total|money }} ({{ total|words }})**
> Certificado SINTÉTICO generado en DEV — no válido ante la DIAN.
