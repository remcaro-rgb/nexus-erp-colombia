# {{ company.name }}
> NIT {{ company.nit }}{% if company.dv %}-{{ company.dv }}{% endif %}{% if branch.name %} · Sucursal {{ branch.name }}{% endif %}
## {{ title }} N.º {{ number|default:'(sin numerar)' }}
Fecha: {{ document_date|date }} · Estado: {{ status_label }}{% if posting_date %} · Contabilizado: {{ posting_date|date }}{% endif %}
{{ party_role }}: {{ party.name|default:'(sin tercero)' }}{% if party.tax_id %} · NIT/CC {{ party.tax_id }}{% endif %}
Fondo: {{ fund.code }} — {{ fund.name }} ({{ fund.kind }}) · Medio de pago: {{ payment_method_label }}{% if reference %} · Ref. {{ reference }}{% endif %}
---
@cols 7,43,20,15,15
|!Lín.|Concepto / documento|Tipo|Base|Valor|
{% for l in lines %}|{{ l.line_no }}|{{ l.description }}|{{ l.kind_label }}|{{ l.base|money }}|{{ l.amount|money }}|
{% endfor %}---
@cols 70,30
{% if withholding_total.amount %}|Retenciones|{{ withholding_total|money }}|
{% endif %}|!{{ total_label }}|{{ total|money }}|
**Valor en letras: {{ total|words }}**
{% if notes %}Observaciones: {{ notes }}
{% endif %}@space 18
@cols 50,50
|!Elaboró|Aprobó|
|{{ prepared_by.name }}|{{ approved_by_names|default:'(sin aprobación requerida)' }}|
> Asiento contable: {{ journal_entry|default:'—' }} · Moneda {{ currency }}
