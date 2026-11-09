# Source Register

Access date for every entry: **2026-11-08** (America/Bogota). Verification pass v1.1 on the same date.
**Method** describes exactly what was done; an HTTP 200 alone never counts as verification:
* **OPENED**: the document was downloaded and the cited passage was located in its text (quotes checked by text search). "OCR" = a scanned Diario Oficial PDF read through text extraction (spacing noise, figures confirmed).
* **SEARCH**: only a search-engine extract of the page was seen. The page itself was not fetched (blocked or timed out).
* **SECONDARY**: only non-official commentary.
* **NOT OPENED**: cited for orientation; the agents must open it before relying on it.
Agents re-verify at build time (P23) and never upgrade a status without opening an official source.

## Colombian law and regulation

| ID | Source | URL | Method | Used for |
|---|---|---|---|---|
| S-01 | DIAN Res. 000165/2023 (compiled) | https://normograma.dian.gov.co/dian/compilacion/docs/resolucion_dian_0165_2023.htm | OPENED | R-FE-01,05,07 |
| S-02 | DIAN Res. 000227/2025 Resolución Única (Title 5; art. 1.5.1.10.2 annexes v1.9/v1.0; PT requisites incl. ISO 27001; Ch. 4 RADIAN art. 1.5.4.4.1; amendment annotations) | https://normograma.dian.gov.co/dian/compilacion/docs/resolucion_dian_0227_2025.htm | OPENED | R-FE-01,02,06,09,10; R-TX-05 |
| S-03 | DIAN Res. 000202/2025 | https://normograma.dian.gov.co/dian/compilacion/docs/resolucion_dian_0202_2025.htm | OPENED | R-FE-03 |
| S-04 | DIAN Res. 000167/2021 (documento soporte) | https://normograma.dian.gov.co/dian/compilacion/docs/resolucion_dian_0167_2021.htm | OPENED | R-FE-08 |
| S-05 | DIAN Res. 000013/2021 (nómina electrónica) | https://normograma.dian.gov.co/dian/compilacion/docs/resolucion_dian_0013_2021.htm | OPENED | R-FE-09 |
| S-06 | DIAN Res. 000085/2022 (RADIAN; tacit acceptance 3 business days) | https://normograma.dian.gov.co/dian/compilacion/docs/resolucion_dian_0085_2022.htm | OPENED | R-FE-10 |
| S-07 | Decreto 1154/2020 | official PDF download returned an empty file | NOT OPENED (the needed fact is covered by S-06) | R-FE-10 (context) |
| S-08 | DIAN Res. 000238/2025 (UVT 2026) | https://normograma.dian.gov.co/dian/compilacion/docs/resolucion_dian_0238_2025.htm | OPENED | R-TX-01 |
| S-09 | Decreto 572/2025 | https://normograma.dian.gov.co/dian/compilacion/docs/decreto_0572_2025.htm | OPENED (text); litigation facts SECONDARY | R-TX-03 |
| S-10 | Estatuto Tributario (compiled): arts. 114-1, 336, 383, 437-1, 468, 468-1, 632, 714 | https://normograma.dian.gov.co/dian/compilacion/docs/estatuto_tributario.htm | OPENED (these articles located; the art. 383 table cells were not legible in the extraction) | R-TX-04,06; R-AC-04; R-LB-08 |
| S-11 | DUR Tributario 1625/2016 | https://normograma.dian.gov.co/dian/compilacion/docs/decreto_1625_2016.htm | FETCHED, provision NOT located (calendar decree is yearly) → treat as unverified | R-TX-07 |
| S-12 | Ley 962/2005 art. 28 | https://normograma.dian.gov.co/dian/compilacion/docs/ley_0962_2005.htm | OPENED | R-AC-03 |
| S-14 | Decreto 2650/1993 (PUC) | — | NOT OPENED | R-AC-02 |
| S-15 | Decreto 1469/2025 (SMMLV 2026) official compilation; Decreto 1470/2025 | https://www.cancilleria.gov.co/normograma/compilacion/docs/decreto_1469_2025.htm (direct fetch 403; full text seen in search extract); https://dapre.presidencia.gov.co/normativa/normativa/DECRETO%201469%20DEL%2029%20DE%20DICIEMBRE%20DE%202025.pdf (bot defense) | SEARCH (1469); SECONDARY (1470) | R-LB-01,02 |
| S-16 | (superseded by S-34) Senado basedoc Ley 2466 | http://www.secretariasenado.gov.co/senado/basedoc/ley_2466_2025.html | NOT OPENED (HTTP 500; HTTPS no connection) | — |
| S-18 | Código Sustantivo del Trabajo (arts. 132, 168, 186, 306) | http://www.secretariasenado.gov.co/senado/basedoc/codigo_sustantivo_trabajo.html | NOT OPENED (500); amended CST arts. 160, 161, 179 read via S-34 | R-LB-06,07,12,13 |
| S-21 | MinSalud Res. 2388/2016 (PILA) | — | NOT OPENED | R-LB-09 |
| S-22 | Corte Constitucional Sentencia C-264/2026 | https://www.corteconstitucional.gov.co/relatoria/2026/C-264-26.htm | OPENED (synthesis + full RESUELVE) | R-LB-10 |
| S-24 | Ley 1581/2012 | https://normograma.dian.gov.co/dian/compilacion/docs/ley_1581_2012.htm | OPENED | R-DP-01,02 |
| S-25 | DUR 1074/2015 (Decretos 1377/2013, 090/2018; microempresa thresholds) | https://normograma.dian.gov.co/dian/compilacion/docs/decreto_1074_2015.htm | SEARCH | R-DP-03,05; R-AC-01 thresholds |
| S-26 | SIC RNBD guidance | https://www.sic.gov.co/ (unreachable from the box) | SEARCH + SECONDARY | R-DP-03,04 |
| S-31 | DIAN e-invoicing micrositio (annex files, XSDs, toolbox) | https://www.dian.gov.co/ (home reachable; micrositio not fetched) | NOT OPENED → P15 build-time task | B-06, EG-1 |
| S-32 | DIAN Concepto 15414/2026 | — | SECONDARY | R-TX-03 |
| S-33 | Consejo de Estado suspension/revocation re SMMLV | press/law-firm reports | SECONDARY | R-LB-01 |
| S-34 | **Ley 2466/2025**, Diario Oficial 53.160 (Rama Judicial SIDN) | https://sidn.ramajudicial.gov.co/SIDN/NORMATIVA/TEXTOS_COMPLETOS/7_LEYES/LEYES%202025/LEY%202466%20DE%202025.PDF | OPENED (arts. 10, 11, 13, 14, 70 read) | R-LB-04 |
| S-35 | **Ley 2101/2021** (SIDN) | https://sidn.ramajudicial.gov.co/SIDN/NORMATIVA/TEXTOS_COMPLETOS/7_LEYES/LEYES%202021/LEY%202101%20DE%202021.PDF | OPENED (art. 3) | R-LB-05 |
| S-36 | **Ley 100/1993** compiled (arts. 18, 20, 204) | https://normograma.dian.gov.co/dian/compilacion/docs/ley_0100_1993.htm | OPENED | R-LB-08 |
| S-37 | **Ley 1393/2010** art. 30 | https://normograma.dian.gov.co/dian/compilacion/docs/ley_1393_2010.htm | OPENED | R-LB-08 |
| S-38 | **Ley 21/1982** art. 12 (CCF 4%, SENA 2%) (SIDN scan) | https://sidn.ramajudicial.gov.co/SIDN/NORMATIVA/TEXTOS_COMPLETOS/7_LEYES/LEYES%201982/LEY%2021%20DE%201982.PDF | OPENED (OCR) | R-LB-08 |
| S-39 | **Ley 89/1988** art. 1 (ICBF 3%) (SIDN scan) | https://sidn.ramajudicial.gov.co/SIDN/NORMATIVA/TEXTOS_COMPLETOS/7_LEYES/LEYES%201988/LEY%2089%20DE%201988.PDF | OPENED (OCR) | R-LB-08 |
| S-40 | **Ley 50/1990** art. 99 (cesantías before 15 Feb) (SIDN) | https://sidn.ramajudicial.gov.co/SIDN/NORMATIVA/TEXTOS_COMPLETOS/7_LEYES/LEYES%201990/LEY%2050%20DE%201990.PDF | OPENED | R-LB-07 |
| S-41 | **Ley 51/1983** (holidays moved to Monday) (SIDN scan) | https://sidn.ramajudicial.gov.co/SIDN/NORMATIVA/TEXTOS_COMPLETOS/7_LEYES/LEYES%201983/LEY%2051%20DE%201983.PDF | OPENED (OCR) | R-LB-11 |
| S-42 | **DUR 2420/2015** compiled with Decreto 1670/2021 modifications (Comisión de Regulación de Agua Potable official compilation) | https://normas.cra.gov.co/gestor/docs/decreto_2420_2015.htm | OPENED (arts. 1.1.1.1, 1.1.2.1, 1.1.2.4, 1.1.3.1) | R-AC-01 |
| S-43 | DUR 2420/2015 (Colpensiones compilation) | https://normativa.colpensiones.gov.co/colpens/docs/decreto_2420_2015.htm | OPENED; **outdated** (no 1670/2021 notes) → not relied upon | — |
| S-44 | **DIAN Res. 000219/2025** (RUB, not e-invoicing) | https://normograma.dian.gov.co/dian/compilacion/docs/resolucion_dian_0219_2025.htm | OPENED | R-FE-04 |
| S-46 | **Ley 2381/2024** (SIDN), art. 94 original effective date 1-Jul-2025 | https://sidn.ramajudicial.gov.co/SIDN/NORMATIVA/TEXTOS_COMPLETOS/7_LEYES/LEYES%202024/LEY%202381%20DE%202024.PDF | OPENED | R-LB-10 |
| S-47 | Ley 1314/2009 | SIDN URL pattern returned a different law's scan; DIAN normograma 404 | NOT OPENED (covered by S-42 for the operative rules) | R-AC-01 (context) |

## Technology, security, standards

| ID | Source | URL | Method | Used for |
|---|---|---|---|---|
| T-01 | MCP versioning (current revision 2026-07-28) | https://modelcontextprotocol.io/specification/versioning | OPENED | ADR-011 |
| T-02 | MCP 2026-07-28 changelog | https://modelcontextprotocol.io/specification/2026-07-28/changelog | OPENED | P20 |
| T-03 | MCP 2026-07-28 authorization (PRM, RFC 8707, RFC 9207, CIMD, DCR deprecated) | https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration.md (+ sibling pages) | OPENED | P20, P04 |
| T-04 | RFC 9728 | https://datatracker.ietf.org/doc/html/rfc9728 | NOT OPENED (referenced by T-03) | P20 |
| T-05 | RFC 8707 | https://www.rfc-editor.org/rfc/rfc8707 | NOT OPENED (referenced by T-03) | P20 |
| T-06 | RFC 9207 | https://datatracker.ietf.org/doc/html/rfc9207 | NOT OPENED (referenced by T-03) | P20 |
| T-07 | Django supported versions | https://www.djangoproject.com/download/ | OPENED | ADR-002 |
| T-08 | PostgreSQL versioning | https://www.postgresql.org/support/versioning/ | OPENED | ADR-002 |
| T-09 | PostgreSQL row security | https://www.postgresql.org/docs/current/ddl-rowsecurity.html | OPENED | ADR-003 |
| T-10 | OWASP ASVS 5.0.0 | https://owasp.org/www-project-application-security-verification-standard/ | OPENED | C2, P22 |
| T-11 | OWASP API Security Top 10 2023 | https://owasp.org/API-Security/editions/2023/en/0x11-t10/ | OPENED | P04, P22 |
| T-12 | OWASP Top 10 for LLM Applications 2025 | https://genai.owasp.org/llm-top-10/ | OPENED | P19, P20, P22 |
| T-13 | WCAG 2.2 | https://www.w3.org/TR/WCAG22/ | OPENED | P08, P22 |
| T-14 | RFC 9457 Problem Details | https://www.rfc-editor.org/rfc/rfc9457 | NOT OPENED | P02 |
| T-15 | PyPI `mcp` package metadata (latest **2.3.0**, uploaded 2026-10-02) | https://pypi.org/pypi/mcp/json | OPENED | ADR-011, P20 |
| T-16 | MCP Python SDK v2.0.0 release notes (published 2026-07-28: "supports the 2026-07-28 revision … serves every earlier revision from the same server"; v1.x in maintenance) | https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0 | SEARCH | ADR-011, P20 |
| T-17 | MCP SDK list for 2026-07-28 (Python and TypeScript are Tier 1; "SDKs adopt new protocol revisions on their own schedules") | https://modelcontextprotocol.io/docs/2026-07-28/sdk | SEARCH | ADR-011 |
| T-18 | MCP Python SDK "What's new in v2" (no-session 2026 path; legacy clients still use sessions; `request_state` key for multi-replica) | https://py.sdk.modelcontextprotocol.io/whats-new/ | SEARCH | P20 |
| T-19 | npm `@modelcontextprotocol/sdk` latest (**1.32.2**); its revision support NOT verified | https://registry.npmjs.org/@modelcontextprotocol/sdk/latest | OPENED (version only) | ADR-011 |

## ERP benchmarks (first-party documentation)

| ID | Source | URL | Method | Used for |
|---|---|---|---|---|
| BM-01 | ERPNext perpetual inventory (Stock Received But Not Billed) | https://docs.erpnext.com/docs/user/manual/en/perpetual-inventory | OPENED | shared/06, shared/07 |
| BM-02 | Odoo 18 inventory valuation | https://www.odoo.com/documentation/18.0/applications/inventory_and_mrp/inventory/product_management/inventory_valuation/using_inventory_valuation.html | OPENED | shared/06, ADR-008 |
| BM-03 | Dynamics 365 Business Central consolidation | https://learn.microsoft.com/en-us/dynamics365/business-central/finance-consolidated-company-reporting | OPENED | P16 |
| BM-04 | **Oracle NetSuite: Automated Intercompany Management Overview** ("automatically generate elimination journal entries"; IC POs → SOs; IC reconciliation report; elimination from the Period Close Checklist) | https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_N1486393.html | OPENED | P16 |
| BM-04b | **Oracle NetSuite: Elimination Subsidiaries** ("Only journal entries post to elimination subsidiaries") | https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_N268759.html | OPENED | P16 (consolidation book design) |
| BM-05 | SAP Business One perpetual inventory / GRPO | — | NOT OPENED; **claims removed** (the GRNI practice rests on BM-01/BM-02) | — |
| BM-06 | **SAP Learning: the Universal Journal (ACDOCA)** ("single source of truth"; GL, asset accounting, controlling, CO-PA, material ledger in one table; dimensions in each journal entry) | https://learning.sap.com/courses/outlining-cost-management-and-profitability-analysis/discussing-the-concept-of-the-universal-journal_f62cf151-5562-44f5-8ff6-df16fb232e94 | OPENED | ADR-005 |
| BM-06b | **SAP Help: S/4HANA Finance VDM** (ACDOCA = "Universal Journal Entry Line Items"; amounts in transaction and company-code currency) | https://help.sap.com/doc/saphelp_hba_sfin_300/3.5/en-US/c2/9cb752b59b2142e10000000a44176d/content.htm?no_cache=true | OPENED | ADR-005, P05 |

**Counts:** 37 Colombian legal entries, 19 technology/standards entries, 8 benchmark entries = **64 entries**.
* **OPENED (43):** S-01, S-02, S-03, S-04, S-05, S-06, S-08, S-09, S-10, S-12, S-22, S-24, S-34, S-35, S-36, S-37, S-38, S-39, S-40, S-41, S-42, S-43, S-44, S-46, T-01, T-02, T-03, T-07, T-08, T-09, T-10, T-11, T-12, T-13, T-15, T-19, BM-01, BM-02, BM-03, BM-04, BM-04b, BM-06, BM-06b. S-43 was opened but is outdated and not relied upon.
* **SEARCH-only (6):** S-15, S-25, S-26, T-16, T-17, T-18.
* **SECONDARY-only (2):** S-32, S-33.
* **NOT OPENED / provision not located (13):** S-07, S-11, S-14, S-16, S-18, S-21, S-31, S-47, T-04, T-05, T-06, T-14, BM-05.
* IDs retired in v1.1: S-13 (→ S-42), S-17 (→ S-35), S-19 (→ S-40), S-20 (→ S-36/S-37), S-23 (→ S-41), S-30 (→ S-44). Benchmark IDs were renamed from B-xx to BM-xx so they don't collide with blocker IDs.
