# {{ company.name }}
> NIT {{ company.nit }}{% if company.dv %}-{{ company.dv }}{% endif %}{% if branch.name %} · Sucursal {{ branch.name }}{% endif %}
## CARTA DE COBRO
{{ issue_date|date }}
Señores {{ party.name }}:
A la fecha registran un saldo vencido de {{ total|money }} ({{ total|words }}) correspondiente a:
@cols 30,25,20,25
|!Documento|Vencimiento|Días|Saldo|
{% for l in lines %}|{{ l.number }}|{{ l.due_date|date }}|{{ l.days }}|{{ l.balance|money }}|
{% endfor %}Agradecemos su pago oportuno.
