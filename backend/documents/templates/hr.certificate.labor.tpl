# {{ company.name }}
> NIT {{ company.nit }}{% if company.dv %}-{{ company.dv }}{% endif %}{% if branch.name %} · Sucursal {{ branch.name }}{% endif %}
## CERTIFICADO LABORAL
{{ city|default:'Bogotá D.C.' }}, {{ issue_date|date }}
La empresa {{ company.name }} certifica que {{ employee.name }}, identificado(a) con {{ employee.id_type }} {{ employee.id_number }}, labora en la compañía desde el {{ employee.start_date|date }} en el cargo de {{ employee.position }}, con contrato {{ employee.contract_type }}.
Se expide a solicitud del interesado.
@space 24
{{ signer.name }}
> {{ signer.position }}
