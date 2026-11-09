# Module registry (rendered from ui/module-registry.json — the JSON is authoritative)

Version 2.2.0-dev. Groups in sidebar order: Inicio · Finanzas · Comercial · Operaciones · Personas · Análisis · Sistema.

## Inicio (`inicio`) — group Inicio — owner P08

Icon `lucide:layout-dashboard` · route `/m/inicio` · permission `shell.access` · descriptor `frontend/src/shell/modules/inicio.module.ts`

Nav visibility: hidden when the user lacks the module permission

Submenus (module bar): Resumen `/m/inicio` · Analítica `/m/inicio/analitica` (P17) · Reportes `/m/inicio/reportes` (P17) · Actividad `/m/inicio/actividad` (P07)

Quick action: **Nuevo reporte** (`reporting.write`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Ventas netas | Σ créditos − débitos de cuentas de ingresos operacionales (clase 41, PUC) contabilizados en el periodo, empresa seleccionada | COP | flow | previous_period | not_applicable | `sales.kpi.read` | P10 | `/m/ventas/facturas?period={period}` |
| Caja y bancos | Saldo al cierre del periodo (o hoy si el periodo está abierto) de cuentas 1105 + 1110 + 1120 | COP | snapshot | previous_period | not_applicable | `treasury.kpi.read` | P05 | `/m/contabilidad/mayor?accounts=1105,1110,1120` |
| Cartera vencida | Σ saldo abierto de documentos CxC con fecha de vencimiento < fecha de corte | COP | snapshot | previous_period | not_applicable | `receivables.kpi.read` | P10 | `/m/ventas/cartera?vencida=1` |
| Inventario al costo | Σ cantidad disponible × costo unitario de la capa de valoración a la fecha de corte | COP | snapshot | previous_period | not_applicable | `inventory.cost.read` | P09 | `/m/inventario/valoracion` |

Panels: Ingresos vs. gastos operacionales (line, P05); Actividad reciente (feed, P07)

Empty state: Sin movimientos en el periodo. Acciones: 'Importar datos' (/m/datos) o 'Iniciar puesta en marcha' (/m/puesta-en-marcha).

## Contabilidad (`contabilidad`) — group Finanzas — owner P05

Icon `lucide:landmark` · route `/m/contabilidad` · permission `ledger.read` · descriptor `frontend/src/features/ledger/module.ts`

Nav visibility: hidden when the user lacks the module permission

Submenus (module bar): Resumen `/m/contabilidad` · Libro mayor `/m/contabilidad/mayor` · Comprobantes `/m/contabilidad/comprobantes` · Cierres de periodo `/m/contabilidad/cierres` · Dimensiones contables `/m/contabilidad/dimensiones` (P05) [tabs: Centros de costo `/m/contabilidad/dimensiones/centros-costo` (F-ACC-DIM), Unidades de negocio `/m/contabilidad/dimensiones/unidades-negocio` (F-ACC-DIM), Auxiliares abiertos `/m/contabilidad/dimensiones/auxiliares-abiertos` (F-ACC-DIM), Ítems contables `/m/contabilidad/dimensiones/items-contables` (F-ACC-DIM)] · Impuestos `/m/contabilidad/impuestos` (P06) [tabs: Exógena `/m/contabilidad/impuestos/exogena` (F-ACC-EXO)]

Quick action: **Nuevo comprobante** (`ledger.post`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Comprobantes contabilizados | Número de comprobantes en estado contabilizado con fecha contable en el periodo | count | flow | previous_period | not_applicable | `ledger.read` | P05 | `/m/contabilidad/comprobantes?period={period}` |
| Ingresos | Σ movimiento neto crédito de cuentas clase 4 en el periodo | COP | flow | previous_period | not_applicable | `ledger.kpi.read` | P05 | `/m/contabilidad/mayor?class=4` |
| Gastos y costos | Σ movimiento neto débito de cuentas clases 5, 6 y 7 en el periodo | COP | flow | previous_period | not_applicable | `ledger.kpi.read` | P05 | `/m/contabilidad/mayor?class=5,6,7` |
| Resultado del periodo | Ingresos − (gastos + costos) del mismo periodo | COP | flow | previous_period | not_applicable | `ledger.kpi.read` | P05 | `/m/contabilidad/resultados` |
| Descuadres con auxiliares | Número de cuentas de control cuyo saldo difiere del total de su auxiliar (CxC, CxP, inventario, activos) a la fecha de corte; objetivo 0 | count | snapshot | none | not_applicable | `ledger.read` | P05 | `/m/contabilidad/conciliacion-auxiliares` |

Panels: Ingresos vs. gastos (line, P05)

Empty state: Aún no hay comprobantes en este periodo. Acciones: 'Nuevo comprobante', 'Importar saldos iniciales' (/m/datos/importar?tipo=saldos).

## Tesorería (`tesoreria`) — group Finanzas — owner P16

Icon `lucide:wallet` · route `/m/tesoreria` · permission `treasury.read` · descriptor `frontend/src/features/treasury/module.ts`

Nav visibility: hidden when the user lacks the module permission

Submenus (module bar): Resumen `/m/tesoreria` · Recibos de caja `/m/tesoreria/recibos` [tabs: Recibos `/m/tesoreria/recibos/lista` (F-TRE-RC), Conceptos de tesorería `/m/tesoreria/recibos/conceptos` (F-TRE-RC)] · Comprobantes de egreso `/m/tesoreria/egresos` [tabs: Comprobantes `/m/tesoreria/egresos/lista` (F-TRE-CE), Lotes de pago `/m/tesoreria/egresos/lotes` (F-TRE-CE)] · Bancos y cajas `/m/tesoreria/bancos` · Conciliación bancaria `/m/tesoreria/conciliacion` · Intercompañía y consolidación `/m/tesoreria/intercompania`

Quick action: **Nuevo pago** (`treasury.pay`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Saldo disponible | Σ saldo contable de cuentas bancarias y cajas de la empresa a la fecha de corte | COP | snapshot | previous_period | not_applicable | `treasury.kpi.read` | P16 | `/m/tesoreria/bancos` |
| Recaudos | Σ recibos de caja contabilizados en el periodo | COP | flow | previous_period | not_applicable | `treasury.kpi.read` | P16 | `/m/tesoreria/recibos/lista` |
| Pagos | Σ egresos contabilizados en el periodo | COP | flow | previous_period | not_applicable | `treasury.kpi.read` | P16 | `/m/tesoreria/egresos/lista` |
| Partidas sin conciliar | Número de líneas de extracto importado sin cruce contable a la fecha de corte | count | snapshot | previous_period | not_applicable | `treasury.reconcile` | P16 | `/m/tesoreria/conciliacion` |
| Diferencias intercompañía | Σ |saldo por cobrar a vinculada − saldo por pagar espejo| entre empresas del grupo a la fecha de corte | COP | snapshot | none | not_applicable | `consolidation.read` | P16 | `/m/tesoreria/intercompania` |

Panels: Recaudos vs. pagos (line, P16)

Empty state: No hay cuentas bancarias configuradas. Acción: 'Crear cuenta bancaria' o 'Importar extracto (CSV)'. En dev no existe conexión con bancos reales.

## Activos fijos (`activos`) — group Finanzas — owner P14

Icon `lucide:building-2` · route `/m/activos` · permission `assets.read` · descriptor `frontend/src/features/assets/module.ts`

Nav visibility: hidden when the user lacks the module permission

Submenus (module bar): Resumen `/m/activos` · Registro de activos `/m/activos/registro` · Depreciación `/m/activos/depreciacion` · Bajas y traslados `/m/activos/bajas`

Quick action: **Nuevo activo** (`assets.write`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Costo histórico | Σ costo de adquisición de activos activos a la fecha de corte | COP | snapshot | previous_period | not_applicable | `assets.read` | P14 | `/m/activos/registro` |
| Depreciación del periodo | Σ depreciación contabilizada en el periodo (todas las bases: NIIF y fiscal se muestran por separado) | COP | flow | previous_period | not_applicable | `assets.read` | P14 | `/m/activos/depreciacion?period={period}` |
| Valor neto en libros | Costo histórico − depreciación acumulada − deterioro acumulado a la fecha de corte | COP | snapshot | previous_period | not_applicable | `assets.read` | P14 | `/m/activos/registro?orden=valor_neto` |
| Activos pendientes de depreciación | Número de activos en uso sin corrida de depreciación para el periodo | count | snapshot | none | not_applicable | `assets.depreciate` | P14 | `/m/activos/depreciacion?pendientes=1` |

Panels: Depreciación mensual (bar, P14)

Empty state: No hay activos registrados. Acciones: 'Nuevo activo' o 'Importar activos' (/m/datos/importar?tipo=activos).

## Ventas y CRM (`ventas`) — group Comercial — owner P10

Icon `lucide:users` · route `/m/ventas` · permission `sales.read` · descriptor `frontend/src/features/sales/module.ts`

Nav visibility: hidden when the user lacks the module permission

Submenus (module bar): Resumen `/m/ventas` · Oportunidades `/m/ventas/oportunidades` · Clientes y precios `/m/ventas/clientes` [tabs: Clientes `/m/ventas/clientes/lista` (F-PORTAL-CUS), Fórmulas de precio `/m/ventas/clientes/formulas-precio` (F-SAL-PRICING)] · Pedidos `/m/ventas/pedidos` · Facturas de venta `/m/ventas/facturas` · Cartera `/m/ventas/cartera`

Quick action: **Nueva oportunidad** (`crm.write`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Ventas netas | Σ subtotal de facturas de venta contabilizadas − notas crédito del periodo (sin impuestos) | COP | flow | previous_period | not_applicable | `sales.kpi.read` | P10 | `/m/ventas/facturas?period={period}` |
| Pedidos abiertos | Número de pedidos confirmados no facturados totalmente a la fecha de corte | count | snapshot | previous_period | not_applicable | `sales.read` | P10 | `/m/ventas/pedidos?estado=abierto` |
| Pipeline ponderado | Σ valor × probabilidad registrada de oportunidades abiertas a la fecha de corte | COP | snapshot | previous_period | not_applicable | `crm.read` | P10 | `/m/ventas/oportunidades` |
| Cartera vencida | Σ saldo de CxC vencido a la fecha de corte | COP | snapshot | previous_period | not_applicable | `receivables.kpi.read` | P10 | `/m/ventas/cartera?vencida=1` |
| Días de cartera (DSO) | Saldo promedio de CxC del periodo ÷ (ventas a crédito del periodo ÷ días del periodo) | days | flow | previous_period | show '—' with tooltip 'Sin base para calcular' (never 0, ∞ or NaN) | `receivables.kpi.read` | P10 | `/m/ventas/cartera` |

Panels: Ventas netas por mes (line, P10); Oportunidades por etapa (bar, P10)

Empty state: Sin ventas en el periodo. Acciones: 'Nueva oportunidad', 'Crear cliente', 'Importar clientes'.

## Facturación electrónica (`facturacion`) — group Comercial — owner P15

Icon `lucide:file-text` · route `/m/facturacion` · permission `einvoice.read` · descriptor `frontend/src/features/einvoice/module.ts`

Nav visibility: hidden when the user lacks the module permission

Submenus (module bar): Resumen `/m/facturacion` · Documentos `/m/facturacion/documentos` · Rechazos y contingencias `/m/facturacion/rechazos` · Numeración y proveedores `/m/facturacion/numeracion` [tabs: Proveedores tecnológicos `/m/facturacion/numeracion/proveedores` (F-PT-OPEN)]

Quick action: **Ver documentos** (`einvoice.read`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Documentos emitidos | Número de documentos electrónicos (FE, NC, ND, DS, POS-E) generados en el periodo | count | flow | previous_period | not_applicable | `einvoice.read` | P15 | `/m/facturacion/documentos?period={period}` |
| Validados | Documentos con estado validado ÷ documentos enviados en el periodo | percent | flow | previous_period | show '—' with tooltip 'Sin base para calcular' (never 0, ∞ or NaN) | `einvoice.read` | P15 | `/m/facturacion/documentos?estado=validado` |
| Rechazados o pendientes | Número de documentos rechazados o sin respuesta a la fecha de corte | count | snapshot | previous_period | not_applicable | `einvoice.read` | P15 | `/m/facturacion/rechazos` |
| Numeración disponible | Mínimo de (rango autorizado − último consecutivo usado) entre resoluciones vigentes | count | snapshot | none | not_applicable | `einvoice.admin` | P15 | `/m/facturacion/numeracion` |

Panels: Documentos por estado (bar, P15)

Empty state: No hay documentos electrónicos en el periodo. Se generan al contabilizar facturas en Ventas o POS. Acción: 'Ir a Facturas de venta'.

## Punto de venta (`pos`) — group Comercial — owner P12

Icon `lucide:store` · route `/m/pos` · permission `pos.read` · descriptor `frontend/src/features/pos/module.ts`

Nav visibility: hidden when the user lacks the module permission

Submenus (module bar): Resumen `/m/pos` · Vender `/m/pos/vender` · Turnos y arqueos `/m/pos/turnos` · Tiquetes `/m/pos/tiquetes`

Quick action: **Abrir caja** (`pos.sell`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Ventas POS | Σ total sin impuestos de tiquetes POS finalizados en el periodo | COP | flow | previous_period | not_applicable | `pos.kpi.read` | P12 | `/m/pos/tiquetes?period={period}` |
| Tiquetes | Número de tiquetes finalizados en el periodo | count | flow | previous_period | not_applicable | `pos.read` | P12 | `/m/pos/tiquetes` |
| Ticket promedio | Ventas POS ÷ tiquetes del periodo | COP | flow | previous_period | show '—' with tooltip 'Sin base para calcular' (never 0, ∞ or NaN) | `pos.kpi.read` | P12 | `/m/pos/tiquetes` |
| Turnos abiertos | Número de turnos de caja abiertos a la fecha de corte | count | snapshot | none | not_applicable | `pos.read` | P12 | `/m/pos/turnos?estado=abierto` |
| Diferencias de arqueo | Σ |efectivo contado − efectivo esperado| de turnos cerrados en el periodo | COP | flow | previous_period | not_applicable | `pos.supervise` | P12 | `/m/pos/turnos?con_diferencia=1` |

Panels: Ventas por hora (bar, P12)

Empty state: No hay cajas configuradas. Acción: 'Configurar caja' (requiere resolución de numeración POS en Facturación).

## Inventario (`inventario`) — group Operaciones — owner P09

Icon `lucide:package` · route `/m/inventario` · permission `inventory.read` · descriptor `frontend/src/features/inventory/module.ts`

Nav visibility: hidden when the user lacks the module permission

Submenus (module bar): Resumen `/m/inventario` · Existencias `/m/inventario/existencias` [tabs: Por ubicación `/m/inventario/existencias/por-ubicacion` (F-INV-LOC), Por presentación `/m/inventario/existencias/por-presentacion` (F-INV-PRES)] · Maestros `/m/inventario/maestros` [tabs: Productos y presentaciones `/m/inventario/maestros/presentaciones` (F-INV-PRES), Bodegas y ubicaciones `/m/inventario/maestros/bodegas-ubicaciones` (F-INV-LOC), Códigos y etiquetas `/m/inventario/maestros/codigos-etiquetas` (F-INV-BARCODE), Conceptos de salida `/m/inventario/maestros/conceptos-salida` (F-INV-ISSUE)] · Movimientos `/m/inventario/movimientos` [tabs: Traslados `/m/inventario/movimientos/traslados` (F-INV-REQ), Salidas `/m/inventario/movimientos/salidas` (F-INV-ISSUE), Entre ubicaciones `/m/inventario/movimientos/entre-ubicaciones` (F-INV-LOC)] · Producción y transformaciones `/m/inventario/produccion` [tabs: Recetas / BOM `/m/inventario/produccion/recetas` (F-INV-BOM), Transformaciones `/m/inventario/produccion/transformaciones` (F-INV-XFORM), Plantillas de porcionado `/m/inventario/produccion/plantillas-porcionado` (F-INV-XFORM)] · Valoración y costos `/m/inventario/valoracion`

Quick action: **Nuevo producto** (`inventory.write`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Valor del inventario | Σ cantidad disponible × costo unitario de valoración a la fecha de corte | COP | snapshot | previous_period | not_applicable | `inventory.cost.read` | P09 | `/m/inventario/valoracion` |
| Referencias bajo mínimo | Número de referencias-bodega con disponible < punto de reorden configurado | count | snapshot | none | not_applicable | `inventory.read` | P09 | `/m/inventario/existencias?bajo_minimo=1` |
| Rotación de inventario | Costo de ventas del periodo ÷ inventario promedio al costo del periodo (anualizado ×365/días) | ratio | flow | previous_period | show '—' with tooltip 'Sin base para calcular' (never 0, ∞ or NaN) | `inventory.cost.read` | P09 | `/m/inventario/valoracion` |
| Días de inventario | Inventario promedio al costo ÷ (costo de ventas del periodo ÷ días del periodo) | days | flow | previous_period | show '—' with tooltip 'Sin base para calcular' (never 0, ∞ or NaN) | `inventory.cost.read` | P09 | `/m/inventario/valoracion` |
| Traslados en tránsito | Número de traslados despachados sin recibir a la fecha de corte | count | snapshot | none | not_applicable | `inventory.read` | P09 | `/m/inventario/movimientos/traslados?estado=transito` |

Panels: Valor por bodega (bar, P09)

Empty state: No hay productos. Acciones: 'Nuevo producto' o 'Importar productos' (/m/datos/importar?tipo=productos).

## Compras (`compras`) — group Operaciones — owner P11

Icon `lucide:shopping-cart` · route `/m/compras` · permission `purchasing.read` · descriptor `frontend/src/features/purchasing/module.ts`

Nav visibility: hidden when the user lacks the module permission

Submenus (module bar): Resumen `/m/compras` · Solicitudes internas `/m/compras/solicitudes` [tabs: Mis solicitudes `/m/compras/solicitudes/mias` (F-INV-REQ), Por aprobar `/m/compras/solicitudes/por-aprobar` (F-INV-REQ), Consolidación `/m/compras/solicitudes/consolidacion` (F-INV-REQ), Abastecimiento `/m/compras/solicitudes/abastecimiento` (F-INV-REQ)] · Cotizaciones `/m/compras/cotizaciones` [tabs: Solicitudes de cotización `/m/compras/cotizaciones/rfq` (F-PUR-CYCLE), Cotizaciones recibidas `/m/compras/cotizaciones/recibidas` (F-PUR-CYCLE), Comparación `/m/compras/cotizaciones/comparacion` (F-PUR-CYCLE)] · Órdenes y recepciones `/m/compras/ordenes` [tabs: Órdenes de compra `/m/compras/ordenes/lista` (F-PUR-CYCLE), Recepciones `/m/compras/ordenes/recepciones` (F-PUR-CYCLE)] · Facturas de proveedor `/m/compras/facturas` · Proveedores `/m/compras/proveedores`

Quick action: **Nueva orden de compra** (`purchasing.write`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Compras del periodo | Σ subtotal de facturas de proveedor contabilizadas en el periodo | COP | flow | previous_period | not_applicable | `purchasing.kpi.read` | P11 | `/m/compras/facturas?period={period}` |
| Órdenes por aprobar | Número de órdenes de compra en estado pendiente de aprobación | count | snapshot | none | not_applicable | `purchasing.read` | P11 | `/m/compras/ordenes?estado=por_aprobar` |
| Cuentas por pagar | Σ saldo abierto de CxP a la fecha de corte | COP | snapshot | previous_period | not_applicable | `payables.kpi.read` | P11 | `/m/compras/facturas?abiertas=1` |
| CxP vencidas | Σ saldo de CxP con vencimiento < fecha de corte | COP | snapshot | previous_period | not_applicable | `payables.kpi.read` | P11 | `/m/compras/facturas?vencidas=1` |
| Días de pago (DPO) | Saldo promedio de CxP ÷ (compras a crédito del periodo ÷ días del periodo) | days | flow | previous_period | show '—' with tooltip 'Sin base para calcular' (never 0, ∞ or NaN) | `payables.kpi.read` | P11 | `/m/compras/facturas` |

Panels: Compras por mes (line, P11)

Empty state: Sin compras en el periodo. Acciones: 'Nueva orden de compra', 'Crear proveedor'.

## Talento humano y nómina (`talento`) — group Personas — owner P13

Icon `lucide:briefcase` · route `/m/talento` · permission `hr.access` · descriptor `frontend/src/features/hr/module.ts`

Nav visibility: hr.access required to see the module in the sidebar, search results, notifications and the AI panel suggestions; payroll amounts additionally need payroll.amounts.read (field policy); unauthorized users get no hint of salaries, counts of payroll errors or employee names

Submenus (module bar): Resumen `/m/talento` · Empleados `/m/talento/empleados` · Nómina `/m/talento/nomina` [tabs: Liquidación `/m/talento/nomina/liquidacion` (F-PAY-FORMULA), Conceptos y fórmulas `/m/talento/nomina/conceptos` (F-PAY-FORMULA)] · Novedades y ausencias `/m/talento/novedades` · Control de tiempos `/m/talento/tiempos`

Quick action: **Nuevo empleado** (`hr.write`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Empleados activos | Número de contratos vigentes a la fecha de corte | count | snapshot | previous_period | not_applicable | `hr.read` | P13 | `/m/talento/empleados?estado=activo` |
| Costo de nómina | Σ devengados + aportes y provisiones del empleador liquidados en el periodo | COP | flow | previous_period | not_applicable | `payroll.amounts.read` | P13 | `/m/talento/nomina?period={period}` |
| Novedades pendientes | Número de novedades registradas sin aprobar para la nómina abierta | count | snapshot | none | not_applicable | `hr.read` | P13 | `/m/talento/novedades?estado=pendiente` |
| Horas extra | Σ horas extra y recargos aprobados en el periodo | hours | flow | previous_period | not_applicable | `timekeeping.read` | P13 | `/m/talento/tiempos?tipo=extra` |
| Ausentismo | Horas de ausencia (incapacidades, licencias no remuneradas, faltas) ÷ horas programadas del periodo | percent | flow | previous_period | show '—' with tooltip 'Sin base para calcular' (never 0, ∞ or NaN) | `hr.read` | P13 | `/m/talento/novedades?tipo=ausencia` |

Panels: Costo de nómina por mes (bar, P13)

Empty state: No hay empleados. Acciones: 'Nuevo empleado', 'Importar empleados'. Parámetros de nómina desconocidos bloquean la liquidación (fail closed) y se listan aquí.

## Reportes (`reportes`) — group Análisis — owner P17

Icon `lucide:bar-chart-3` · route `/m/reportes` · permission `reporting.read` · descriptor `frontend/src/features/reporting/module.ts`

Nav visibility: hidden when the user lacks the module permission

Submenus (module bar): Resumen `/m/reportes` · Estados financieros `/m/reportes/estados` [tabs: Balance general `/m/reportes/estados/balance-general` (F-ACC-REPORTS), Estado de resultados `/m/reportes/estados/estado-resultados` (F-ACC-REPORTS), Flujo de efectivo `/m/reportes/estados/flujo-efectivo` (F-ACC-REPORTS), Estados financieros `/m/reportes/estados/paquete` (F-ACC-REPORTS)] · Auxiliares `/m/reportes/auxiliares` [tabs: Por cuenta `/m/reportes/auxiliares/por-cuenta` (F-ACC-REPORTS), Por centro de costo `/m/reportes/auxiliares/por-centro-costo` (F-ACC-REPORTS), Por tercero `/m/reportes/auxiliares/por-tercero` (F-ACC-REPORTS)] · Explorador `/m/reportes/explorador` · Programados `/m/reportes/programados` · Documentos `/m/reportes/documentos`

Quick action: **Nuevo reporte** (`reporting.write`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Reportes disponibles | Número de definiciones de reporte que el usuario puede ejecutar | count | snapshot | none | not_applicable | `reporting.read` | P17 | `/m/reportes/explorador` |
| Ejecuciones del periodo | Número de ejecuciones de reportes en el periodo (propias o compartidas visibles) | count | flow | previous_period | not_applicable | `reporting.read` | P17 | `/m/reportes/programados` |
| Ejecuciones fallidas | Número de ejecuciones con error en el periodo | count | flow | previous_period | not_applicable | `reporting.read` | P17 | `/m/reportes/programados?estado=error` |
| Programados activos | Número de reportes programados activos | count | snapshot | none | not_applicable | `reporting.write` | P17 | `/m/reportes/programados` |

Panels: Ejecuciones por día (bar, P17)

Empty state: Aún no hay ejecuciones. Acción: 'Generar balance de prueba' (/m/reportes/estados).

## Aprobaciones y auditoría (`aprobaciones`) — group Sistema — owner P07

Icon `lucide:check-circle-2` · route `/m/aprobaciones` · permission `workflow.read` · descriptor `frontend/src/features/workflow/module.ts`

Nav visibility: hidden when the user lacks the module permission

Submenus (module bar): Resumen `/m/aprobaciones` · Bandeja `/m/aprobaciones/bandeja` · Auditoría `/m/aprobaciones/auditoria` · Tareas programadas `/m/aprobaciones/tareas`

Quick action: **Ver bandeja** (`workflow.read`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Pendientes de mi aprobación | Número de solicitudes asignadas al usuario o a sus roles en estado pendiente | count | snapshot | none | not_applicable | `workflow.read` | P07 | `/m/aprobaciones/bandeja` |
| Vencidas (SLA) | Número de solicitudes pendientes cuyo plazo definido en el flujo ya pasó | count | snapshot | none | not_applicable | `workflow.read` | P07 | `/m/aprobaciones/bandeja?vencidas=1` |
| Tiempo medio de aprobación | Promedio (aprobada_en − creada_en) de solicitudes resueltas en el periodo | hours | flow | previous_period | show '—' with tooltip 'Sin base para calcular' (never 0, ∞ or NaN) | `workflow.read` | P07 | `/m/aprobaciones/bandeja?resueltas=1` |
| Eventos de auditoría | Número de eventos de auditoría visibles para el usuario en el periodo | count | flow | previous_period | not_applicable | `audit.read` | P07 | `/m/aprobaciones/auditoria` |

Panels: Actividad reciente (feed, P07)

Empty state: No tienes solicitudes pendientes.

## Importar y exportar (`datos`) — group Sistema — owner P18

Icon `lucide:arrow-left-right` · route `/m/datos` · permission `dataio.read` · descriptor `frontend/src/features/dataio/module.ts`

Nav visibility: hidden when the user lacks the module permission

Submenus (module bar): Resumen `/m/datos` · Importar `/m/datos/importar` · Exportar `/m/datos/exportar` · Plantillas `/m/datos/plantillas`

Quick action: **Nueva importación** (`dataio.import`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Lotes importados | Número de lotes de importación confirmados en el periodo | count | flow | previous_period | not_applicable | `dataio.read` | P18 | `/m/datos/importar?estado=confirmado` |
| Filas importadas | Σ filas confirmadas en lotes del periodo | count | flow | previous_period | not_applicable | `dataio.read` | P18 | `/m/datos/importar` |
| Lotes rechazados | Número de lotes rechazados por validación (atómicos: no se aplicó ninguna fila) | count | flow | previous_period | not_applicable | `dataio.read` | P18 | `/m/datos/importar?estado=rechazado` |
| Exportaciones | Número de exportaciones generadas en el periodo (respetan políticas de fila y campo) | count | flow | previous_period | not_applicable | `dataio.export` | P18 | `/m/datos/exportar` |

Panels: Lotes por tipo de dato (bar, P18)

Empty state: No hay importaciones. Acción: 'Descargar plantilla' y 'Nueva importación'.

## Puesta en marcha (`puesta-en-marcha`) — group Sistema — owner P21

Icon `lucide:rocket` · route `/m/puesta-en-marcha` · permission `onboarding.read` · descriptor `frontend/src/features/onboarding/module.ts`

Nav visibility: hidden when the user lacks the module permission

Submenus (module bar): Resumen `/m/puesta-en-marcha` · Pasos `/m/puesta-en-marcha/pasos` · Asistente de configuración `/m/puesta-en-marcha/asistente`

Quick action: **Continuar configuración** (`onboarding.write`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Avance | Pasos obligatorios completados ÷ pasos obligatorios de la plantilla de puesta en marcha | percent | snapshot | none | show '—' with tooltip 'Sin base para calcular' (never 0, ∞ or NaN) | `onboarding.read` | P21 | `/m/puesta-en-marcha/pasos` |
| Bloqueos abiertos | Número de pasos bloqueados (p. ej. parámetro legal desconocido, dato maestro faltante) | count | snapshot | none | not_applicable | `onboarding.read` | P21 | `/m/puesta-en-marcha/pasos?estado=bloqueado` |
| Datos maestros cargados | Número de entidades maestras (terceros, productos, cuentas, empleados) con al menos un registro | count | snapshot | none | not_applicable | `onboarding.read` | P21 | `/m/datos` |

Panels: Lista de pasos (checklist, P21)

Empty state: La puesta en marcha no ha iniciado. Acción: 'Iniciar asistente'.

## Administración (`administracion`) — group Sistema — owner P04

Icon `lucide:settings` · route `/m/administracion` · permission `admin.access` · descriptor `frontend/src/features/iam/module.ts`

Nav visibility: hidden when the user lacks the module permission

Submenus (module bar): Resumen `/m/administracion` · Empresas y sucursales `/m/administracion/empresas` [tabs: Empresas `/m/administracion/empresas/lista` (F-ACC-BRANCH), Sucursales `/m/administracion/empresas/sucursales` (F-ACC-BRANCH)] · Usuarios y roles `/m/administracion/usuarios` [tabs: Usuarios internos `/m/administracion/usuarios/internos` (F-PORTAL-CORE), Usuarios de portales `/m/administracion/usuarios/externos` (F-PORTAL-CORE)] · Políticas de seguridad `/m/administracion/seguridad` · Integraciones `/m/administracion/integraciones` · Conectores MCP `/m/administracion/mcp` (P20)

Quick action: **Nuevo usuario** (`iam.admin`)

| KPI | Fórmula | Unidad | Semántica | Comparación | Denominador 0 | Permiso | Proveedor | Drill |
|---|---|---|---|---|---|---|---|---|
| Usuarios activos | Número de usuarios habilitados en el tenant con acceso a la empresa seleccionada | count | snapshot | none | not_applicable | `iam.admin` | P04 | `/m/administracion/usuarios` |
| Usuarios sin MFA | Número de usuarios habilitados sin segundo factor registrado | count | snapshot | none | not_applicable | `iam.admin` | P04 | `/m/administracion/usuarios?mfa=0` |
| Accesos denegados | Número de solicitudes API/UI/AI/MCP denegadas por política en el periodo | count | flow | previous_period | not_applicable | `iam.audit` | P04 | `/m/aprobaciones/auditoria?tipo=denegado` |
| Roles personalizados | Número de roles definidos por el tenant | count | snapshot | none | not_applicable | `iam.admin` | P04 | `/m/administracion/usuarios?vista=roles` |

Panels: Estado de integraciones (table, P04)

Empty state: Solo existe el usuario administrador inicial. Acción: 'Invitar usuario'.

# Portals (external self-service; FEATURES-ADDENDUM)

## Portal de proveedores (`/portal/proveedores`) — owner P11 — F-PORTAL-SUP

Menus: Inicio `/portal/proveedores` · Cotizaciones `/portal/proveedores/cotizaciones` · Órdenes `/portal/proveedores/ordenes` · Envíos `/portal/proveedores/envios` · Facturas `/portal/proveedores/facturas` · Pagos `/portal/proveedores/pagos` · Perfil y documentos `/portal/proveedores/perfil`

| KPI | Fórmula | Unidad | Semántica | Permiso | Drill |
|---|---|---|---|---|---|
| Solicitudes de cotización abiertas | count(RFQ invitations of my party with status SENT and deadline ≥ today) | count | snapshot | `portal.supplier.read` | `/portal/proveedores/cotizaciones` |
| Órdenes por confirmar | count(POs of my party with status SENT not acknowledged) | count | snapshot | `portal.supplier.read` | `/portal/proveedores/ordenes` |
| Facturas por pagar | Σ open AP items of my party (COP) | COP | snapshot | `portal.supplier.read` | `/portal/proveedores/facturas` |
| Pagos recibidos en el periodo | Σ posted CE amounts to my party in the selected period | COP | flow | `portal.supplier.read` | `/portal/proveedores/pagos` |

## Portal de clientes (`/portal/clientes`) — owner P10 — F-PORTAL-CUS

Menus: Inicio `/portal/clientes` · Cotizaciones `/portal/clientes/cotizaciones` · Pedidos `/portal/clientes/pedidos` · Facturas y notas `/portal/clientes/facturas` · Pagos y recibos `/portal/clientes/pagos` · Estado de cuenta `/portal/clientes/estado-cuenta` · Solicitudes `/portal/clientes/solicitudes` · Perfil `/portal/clientes/perfil`

| KPI | Fórmula | Unidad | Semántica | Permiso | Drill |
|---|---|---|---|---|---|
| Saldo pendiente | Σ open AR items of my party (COP) | COP | snapshot | `portal.customer.read` | `/portal/clientes/estado-cuenta` |
| Facturas vencidas | count(open AR items of my party with due_date < today) | count | snapshot | `portal.customer.read` | `/portal/clientes/facturas` |
| Pedidos en curso | count(sales orders of my party not fully delivered) | count | snapshot | `portal.customer.read` | `/portal/clientes/pedidos` |
| Pagos del periodo | Σ posted RC applications to my party in the selected period | COP | flow | `portal.customer.read` | `/portal/clientes/pagos` |

## Portal de empleados (`/portal/empleados`) — owner P13 — F-PORTAL-EMP

Menus: Inicio `/portal/empleados` · Desprendibles `/portal/empleados/desprendibles` · Certificados `/portal/empleados/certificados` · Vacaciones `/portal/empleados/vacaciones` · Actualización de datos `/portal/empleados/datos` · Perfil `/portal/empleados/perfil`

| KPI | Fórmula | Unidad | Semántica | Permiso | Drill |
|---|---|---|---|---|---|
| Días de vacaciones disponibles | accrued vacation days − taken − approved pending (own) | days | snapshot | `portal.employee.read` | `/portal/empleados/vacaciones` |
| Último pago neto | net pay of my last posted payslip | COP | snapshot | `portal.employee.read` | `/portal/empleados/desprendibles` |
| Solicitudes pendientes | count(my vacation + data update requests in SUBMITTED) | count | snapshot | `portal.employee.read` | `/portal/empleados/vacaciones` |

