# UI progress — after wave 05

Integrated commit: `5d40fc9d05ca` · captured 2026-11-11T03:23:51Z (UTC) · viewport 1440×900 · device scale 1 · 1.68 MB · browser: system Chrome via Playwright 1.65.0

**Status: OK**

Synthetic seeded data only (DEV). Password, token and secret fields are masked. Pages not yet built are listed, never faked. Contract notes are automatic measurements against docs/spec/UI-CONTRACT.md §3 (header 64 px, sidebar 256/80 px, AI panel 320 px).

Signed in as: throwaway DEV user `ui-preview` of a synthetic tenant, bootstrapped by tools/dev_login.py (password only in deploy/dev/secrets/ui-preview.env, 0600, rotated each run)

| Page | Route | Theme / variant | Status | Contract notes |
|---|---|---|---|---|
| Login / entry | `/` | light | captured | header not found; sidebar not found; AI panel not found |
| Login / entry | `/` | dark | captured | header not found; sidebar not found; AI panel not found; dark class ✓ |
| Inicio | `/m/inicio` | light | captured | header 64 ✓; sidebar 256 ✓; AI panel not found |
| Inicio | `/m/inicio` | dark | captured | header 64 ✓; sidebar 256 ✓; AI panel not found; dark class ✓ |
| Contabilidad | `/m/contabilidad` | light | captured | header 64 ✓; sidebar 256 ✓; AI panel not found |
| Contabilidad | `/m/contabilidad` | dark | captured | header 64 ✓; sidebar 256 ✓; AI panel not found; dark class ✓ |
| Activos fijos | `/m/activos` | light | captured | header 64 ✓; sidebar 256 ✓; AI panel not found |
| Activos fijos | `/m/activos` | dark | captured | header 64 ✓; sidebar 256 ✓; AI panel not found; dark class ✓ |
| Ventas y CRM | `/m/ventas` | light | captured | header 64 ✓; sidebar 256 ✓; AI panel not found |
| Ventas y CRM | `/m/ventas` | dark | captured | header 64 ✓; sidebar 256 ✓; AI panel not found; dark class ✓ |
| Facturación electrónica | `/m/facturacion` | light | captured | header 64 ✓; sidebar 256 ✓; AI panel not found |
| Facturación electrónica | `/m/facturacion` | dark | captured | header 64 ✓; sidebar 256 ✓; AI panel not found; dark class ✓ |
| Inventario | `/m/inventario` | light | captured | header 64 ✓; sidebar 256 ✓; AI panel not found |
| Inventario | `/m/inventario` | dark | captured | header 64 ✓; sidebar 256 ✓; AI panel not found; dark class ✓ |
| Compras | `/m/compras` | light | captured | header 64 ✓; sidebar 256 ✓; AI panel not found |
| Compras | `/m/compras` | dark | captured | header 64 ✓; sidebar 256 ✓; AI panel not found; dark class ✓ |
| Talento humano y nómina | `/m/talento` | light | captured | header 64 ✓; sidebar 256 ✓; AI panel not found |
| Talento humano y nómina | `/m/talento` | dark | captured | header 64 ✓; sidebar 256 ✓; AI panel not found; dark class ✓ |
| Aprobaciones y auditoría | `/m/aprobaciones` | light | captured | header 64 ✓; sidebar 256 ✓; AI panel not found |
| Aprobaciones y auditoría | `/m/aprobaciones` | dark | captured | header 64 ✓; sidebar 256 ✓; AI panel not found; dark class ✓ |
| Inicio (sidebar + AI panel collapsed) | `/m/inicio` | collapsed | captured | header 64 ✓; sidebar 80 ✓; AI panel closed ✓; collapse control(s) not found (aria-label/aria-controls), captured as rendered |

## Not captured

| Page | Route | Why |
|---|---|---|
| Tesorería | `/m/tesoreria` | not yet built (owner P16 not accepted) |
| Punto de venta | `/m/pos` | not yet built (owner P12 not accepted) |
| Reportes | `/m/reportes` | not yet built (owner P17 not accepted) |
| Importar y exportar | `/m/datos` | not yet built (owner P18 not accepted) |
| Puesta en marcha | `/m/puesta-en-marcha` | not yet built (owner P21 not accepted) |
| Portal de proveedores | `/portal/proveedores` | not captured: portal pages need an external portal account |
| Portal de clientes | `/portal/clientes` | not captured: portal pages need an external portal account |
| Portal de empleados | `/portal/empleados` | not captured: portal pages need an external portal account |
| Administración | `/m/administracion` | not yet built (HTTP 200, heading: Página no encontrada) |

## Gallery

### Login / entry — light

![Login / entry light](login-light.png)

### Login / entry — dark

![Login / entry dark](login-dark.png)

### Inicio — light

![Inicio light](inicio-light.png)

### Inicio — dark

![Inicio dark](inicio-dark.png)

### Contabilidad — light

![Contabilidad light](contabilidad-light.png)

### Contabilidad — dark

![Contabilidad dark](contabilidad-dark.png)

### Activos fijos — light

![Activos fijos light](activos-light.png)

### Activos fijos — dark

![Activos fijos dark](activos-dark.png)

### Ventas y CRM — light

![Ventas y CRM light](ventas-light.png)

### Ventas y CRM — dark

![Ventas y CRM dark](ventas-dark.png)

### Facturación electrónica — light

![Facturación electrónica light](facturacion-light.png)

### Facturación electrónica — dark

![Facturación electrónica dark](facturacion-dark.png)

### Inventario — light

![Inventario light](inventario-light.png)

### Inventario — dark

![Inventario dark](inventario-dark.png)

### Compras — light

![Compras light](compras-light.png)

### Compras — dark

![Compras dark](compras-dark.png)

### Talento humano y nómina — light

![Talento humano y nómina light](talento-light.png)

### Talento humano y nómina — dark

![Talento humano y nómina dark](talento-dark.png)

### Aprobaciones y auditoría — light

![Aprobaciones y auditoría light](aprobaciones-light.png)

### Aprobaciones y auditoría — dark

![Aprobaciones y auditoría dark](aprobaciones-dark.png)

### Inicio (sidebar + AI panel collapsed) — collapsed

![Inicio (sidebar + AI panel collapsed) collapsed](inicio-collapsed.png)
