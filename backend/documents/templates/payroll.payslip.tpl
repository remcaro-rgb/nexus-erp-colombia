# {{ company.name }}
> NIT {{ company.nit }}{% if company.dv %}-{{ company.dv }}{% endif %}{% if branch.name %} · Sucursal {{ branch.name }}{% endif %}
## DESPRENDIBLE DE NÓMINA — {{ period }}
Empleado: {{ employee.name }} · {{ employee.id_number }}
@cols 50,25,25
|!Concepto|Devengos|Deducciones|
{% for l in lines %}|{{ l.concept }}|{{ l.earning|money }}|{{ l.deduction|money }}|
{% endfor %}---
|!NETO A PAGAR|{{ net|money }}| |
> {{ net|words }}
