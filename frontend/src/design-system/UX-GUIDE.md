# Guía de redacción y UX — Nexus ERP Colombia (owner P08)

> Entregable 13 del prompt P08. Ubicación provisional (CCR P08-2): debería publicarse como `docs/ux/guide.md`.
> Entorno DEV: todos los ejemplos usan datos sintéticos.

## 1. Tono
* Español de Colombia (es-CO), trato de **usted**, frases cortas, voz activa. Sin tecnicismos de software ante el usuario
  final; sí con el vocabulario contable correcto.
* Nunca se culpa al usuario ("Usted ingresó mal…"); se describe el hecho y la salida.
* Nada de mensajes de éxito antes de que la API confirme (UI-CONTRACT §12). Prohibidos "Acción ejecutada con éxito" genéricos:
  diga qué pasó ("Comprobante CE-000123 contabilizado").

## 2. Mensajes de error: qué pasó + cómo arreglarlo
| Mal | Bien |
|---|---|
| "Error 422" | "El periodo noviembre 2026 está cerrado. Elija un periodo abierto o pida la reapertura a contabilidad." |
| "Valor inválido" | "Use el formato 1.234.567,89 (punto de miles, coma decimal)." |
| "No autorizado" | "Su rol no permite contabilizar en esta empresa. Solicite el permiso *ledger.post* a un administrador." |
* Siempre que exista, muestre el **código de seguimiento** (`trace_id`) en el toast de error (`Toast.traceId`).
* Errores de campo junto al campo (`Field.error`, `role="alert"`), mapeados desde `problem.errors[].pointer`.
* Errores de cálculo de fórmulas con **línea y columna** (FormulaEditor).

## 3. Estados vacíos y no disponibles
* Sin datos: texto honesto + la acción del registro ("Aún no hay movimientos para su cuenta en este periodo.").
* Proveedor aún no aceptado: **"Módulo aún no disponible"**. Nunca cifras de ejemplo, aleatorias o estáticas.
* Integraciones simuladas: etiqueta visible **"Simulado"**; IA sin proveedor: **"IA en modo de prueba (sin proveedor conectado)"**.

## 4. Números, fechas y moneda
* COP: `$ 1.284.500` (sin decimales en tableros; 2 decimales en documentos). Compacto `$ 1,3 M` solo con el valor completo en
  `aria-label`/tooltip. Montos siempre como cadena decimal, nunca `float`.
* Fechas: `15 nov 2026` (visualización) y `dd/mm/aaaa` (captura), zona America/Bogota.
* Porcentajes: `4,2 %` (coma decimal, espacio antes de %).

## 5. Accesibilidad (WCAG 2.2 AA)
Foco visible y no tapado (2.4.11), objetivos ≥ 24 px (usamos ≥ 32 px), alternativa a arrastrar (2.5.7: FileDrop con botón,
reordenar tablero con botones), ayuda consistente (3.2.6), no pedir dos veces lo mismo (3.3.7: borradores, valores por
defecto del contexto), autenticación accesible (3.3.8: se permite pegar y usar gestores de contraseñas), enlace "Saltar al
contenido principal", landmarks, regiones vivas para resultados asíncronos, `prefers-reduced-motion`.

## 6. Glosario contable colombiano (uso obligatorio en la interfaz)
| Término | Uso |
|---|---|
| **Tercero** | Persona natural o jurídica con la que se registran operaciones (cliente, proveedor, empleado). No "contacto" ni "partner". |
| **NIT / DV** | Número de identificación tributaria y su dígito de verificación (`900.123.456-8`). |
| **Comprobante** | Documento contable que soporta un asiento (comprobante de diario, de egreso, recibo de caja). |
| **Causación** | Reconocimiento contable de un hecho económico cuando ocurre, aunque no se haya pagado o cobrado. |
| **Contabilizar** | Registrar en el libro mayor (acción principal de un documento). Corrección = **reversión**, nunca borrado. |
| **Anular / Reversar** | Anular deja el documento sin efecto mediante un documento de reversión; los números no se reutilizan. |
| **PUC** | Plan Único de Cuentas (clases 1–9). |
| **Libro mayor / Auxiliar** | Saldos por cuenta / detalle por tercero, centro de costo u otra dimensión. |
| **Centro de costo, unidad de negocio, auxiliar abierto, ítem contable** | Dimensiones analíticas (Agrupador / Detalle). |
| **Retención en la fuente, ReteIVA, ReteICA** | Retenciones calculadas por el motor tributario; nunca se escriben tasas a mano. |
| **Recibo de caja (RC) / Comprobante de egreso (CE)** | Documentos de tesorería de ingreso / salida de dinero. |
| **Cartera / Cuentas por pagar** | Saldos abiertos por cobrar / por pagar. |
| **Exógena** | Información exógena para la DIAN (en DEV: formato sintético, "no válido ante la DIAN"). |
| **Periodo cerrado** | No admite contabilización; se muestra con candado y texto "Cerrado". |
