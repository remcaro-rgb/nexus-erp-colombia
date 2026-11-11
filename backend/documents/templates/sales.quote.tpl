# {{ company.name }}
> NIT {{ company.nit }}{% if company.dv %}-{{ company.dv }}{% endif %}{% if branch.name %} · Sucursal {{ branch.name }}{% endif %}
## COTIZACIÓN N.º {{ number }}
Fecha: {{ document_date|date }} · Válida hasta: {{ valid_until|date }}
Cliente: {{ party.name }} · NIT/CC {{ party.tax_id }}
---
@cols 8,52,12,14,14
|!Lín.|Descripción|Cant.|Precio|Valor|
{% for l in lines %}|{{ l.line_no }}|{{ l.description }}|{{ l.quantity|number }}|{{ l.unit_price|money }}|{{ l.amount|money }}|
{% endfor %}---
@cols 70,30
|Subtotal|{{ subtotal|money }}|
|Impuestos|{{ tax_total|money }}|
|!TOTAL|{{ total|money }}|
**Valor en letras: {{ total|words }}**
