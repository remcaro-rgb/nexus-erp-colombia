# {{ company.name }}
> NIT {{ company.nit }}{% if company.dv %}-{{ company.dv }}{% endif %}{% if branch.name %} · Sucursal {{ branch.name }}{% endif %}
## FACTURA ELECTRÓNICA DE VENTA N.º {{ number }}
> {{ resolution_text|default:'Resolución de numeración: (SINTÉTICA — no válida ante la DIAN)' }}
Fecha: {{ issue_date|date }} · Vence: {{ due_date|date }}
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
CUFE: {{ cufe|default:'SIN-CUFE (SINTÉTICO)' }}
