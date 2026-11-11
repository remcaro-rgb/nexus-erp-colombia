# {{ company.name }}
> NIT {{ company.nit }}{% if company.dv %}-{{ company.dv }}{% endif %}{% if branch.name %} · Sucursal {{ branch.name }}{% endif %}
## ORDEN DE COMPRA N.º {{ number }}
Fecha: {{ document_date|date }} · Proveedor: {{ party.name }} · NIT {{ party.tax_id }}
Entregar en: {{ warehouse.name }}
---
@cols 8,52,12,14,14
|!Lín.|Descripción|Cant.|Costo unit.|Valor|
{% for l in lines %}|{{ l.line_no }}|{{ l.description }}|{{ l.quantity|number }}|{{ l.unit_price|money }}|{{ l.amount|money }}|
{% endfor %}---
@cols 70,30
|!TOTAL|{{ total|money }}|
**Valor en letras: {{ total|words }}**
