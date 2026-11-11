# {{ company.name }}
> NIT {{ company.nit }} · {{ branch.name }}
## DOCUMENTO POS N.º {{ number }}
Fecha: {{ document_date|date }} · Caja: {{ terminal }}
@cols 50,15,35
{% for l in lines %}|{{ l.description }}|{{ l.quantity|number:0 }}|{{ l.amount|money }}|
{% endfor %}@cols 65,35
|!TOTAL|{{ total|money }}|
> {{ total|words }}
