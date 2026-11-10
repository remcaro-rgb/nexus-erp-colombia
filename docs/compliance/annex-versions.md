# DIAN annex versions — build-time verification (owner P15)

Recorded by the P15 worker session on **2026-11-10** (America/Bogota). Files were downloaded from the official DIAN sites over
HTTPS inside the build session, hashed with SHA-256 and **not** committed (they stay with the operator's evidence; the repository
keeps the hashes, the extracted code lists in `backend/einvoice/annex.py` and the hash vectors in `specs/fiscal-hash/`).
Nothing here is legal advice; production use needs EG-1/EG-2/EG-3 and a legal review (B-06, B-09).

## 1. Adopted versions (primary source)

| Family | Adopted version | Where it is adopted | Files obtained in this build |
|---|---|---|---|
| Factura electrónica de venta (FE) + notas | **1.9** | Res. 227/2025 art. 1.5.1.10.2 ("Se adopta la Versión 1.9 del 'Anexo Técnico de Factura Electrónica de Venta'") | **No** — not linked from the DIAN documentation page on 2026-11-10; v1.8 files used as prior-version reference |
| Documento equivalente electrónico (DEE, tiquete POS) | **1.0** | Res. 227/2025 art. 1.5.1.10.2 (same article, "y la versión 1.0 del 'Anexo Técnico de Documento Equivalente…'") | No |
| Documento soporte (DS) + nota de ajuste | **1.1** | Res. 227/2025 (art. 17 Res. 167/2021 compiled) | No |
| Nómina electrónica + nota de ajuste | **1.0** | Res. 227/2025 (Res. 013/2021 compiled) | No |
| RADIAN | **1.1** | Res. 227/2025 (paragraph "se adopta el Anexo técnico RADIAN versión 1.1") | No |

Source document:

| Source | URL | Accessed | SHA-256 |
|---|---|---|---|
| Res. DIAN 000227/2025, compiled text (normograma) | https://normograma.dian.gov.co/dian/compilacion/docs/resolucion_dian_0227_2025.htm | 2026-11-10 12:53 -05:00 | `6f6ed2366e53416ed605f3fd7d0044cc6e15569dbed358ddf91dda015a68a795` |
| DIAN "Documentación técnica" page (links found: annex v1.7 2020, v1.8 2021, toolbox) | https://www.dian.gov.co/impuestos/factura-electronica/documentacion/Paginas/documentacion-tecnica.aspx | 2026-11-10 | `7172925c406b6a45ff9ead5cacbdee0235fd6117e6f724efc6ca45337b35a30b` |

Res. 000219/2025 (RUB, beneficial owners) is **not** about e-invoicing and is ignored (S-44).

## 2. Official files downloaded (prior version v1.8)

| File | URL | Accessed | SHA-256 |
|---|---|---|---|
| Anexo Técnico FE v1.8 (Res. 000012/2021), PDF 705 pages | https://www.dian.gov.co/impuestos/factura-electronica/Documents/Anexo-Tecnico-Resolucion-000012-09022021.pdf | 2026-11-10 | `0250d2833d5a38b03f2fde193e9276e2b18e3422742cd29a32581529485e99e1` |
| Caja de herramientas FE "Validación previa" (Version 1.8: XSD, Schemes, code lists, example XMLs) | https://www.dian.gov.co/impuestos/factura-electronica/Documents/Caja_de_herramientas_Factura_Electronica_Validacion_Previa.zip (via the page's short link) | 2026-11-10 | `107fcd5725f51305216b1c55c37b338b21d07f5e2d577f5a5bfa083d0f6575bd` |

Toolbox members used (paths inside `…/Version 1.8/`):

| Member | SHA-256 |
|---|---|
| `XSD/maindoc/UBL-Invoice-2.1.xsd` | `40fae8cb436f3a9506d7acce65ba162caef3b0bed4d5cbc0992b2153ded4edf4` |
| `XSD/maindoc/UBL-CreditNote-2.1.xsd` | `21eee9df7418a4ec2a26d92ad80c9b41d5e884959a698793311d2c5c6754c4b0` |
| `XSD/maindoc/UBL-DebitNote-2.1.xsd` | `ea5303c2191dfb579d6d53a66e41094020f33dca0531086da1bd62f722202da0` |
| `XSD/maindoc/UBL-ApplicationResponse-2.1.xsd` | `9420edfe126e86eb3aa051a872940935a4badef12e56322c1a76d2cbcb07ad18` |
| `XSD/maindoc/UBL-AttachedDocument-2.1.xsd` | `58c44a11e5ec58f287128d9e10e1314a04c3fc643fc6d6f3bd5c32503437ad1f` |
| `XSD/maindoc/DIAN_UBL_Structures.xsd` | `41ead5af3ea44f1014be098061c62f8d1bfd7f7b8726997836b66e8955036a2c` |
| `XSD/common/UBL-XAdESv132-2.1.xsd` | `a4f726bcf8cc3f7d9ffa4dab99e005535a8e8b60dced1e5d94578d2e05afa96e` |
| `Listas de valores/TipoDocumento-2.1.gc` | `fff211ac73d9d72dd29745efa5c02eb5cd19c0595ec8668f581f29ccc397aac6` |
| `Listas de valores/TipoImpuesto-2.1.gc` | `fee2b47fa9255c4a283fb89f82747cc7a66c09a69fe315712c1b0affa493d25a` |
| `Listas de valores/TipoAmbiente-2.1.gc` | `fca7486488690bb7dfea5a4fb245c4447fc1bdc24b41a01fd418649623253cb2` |
| `Listas de valores/EventoDocumento-2.1.gc` | `a76e09905d730d80d9f84dae525045fefcf5bdbb7fe5947e2804f0b1ba081d51` |
| `Listas de valores/TiposEventos.gc` | `f6b1ff6ace9f63b5374d90022ee6ff9917e14429104f0dd0d98f141fabb6d6d0` |
| `Listas de valores/AlgoritmoCUFE-2.1.gc` | `2c03a6c8c088dfb6b7f0c4c54ea393b9322f0a3c783670b3db062b3f117a6bce` |
| `Listas de valores/AlgoritmoCUDE-2.1.gc` | `fdc6527e7d7da27ce171ef0e477c6131330acd14ce44252501fec5c19670e5af` |
| `Ejemplificaciones/XMLs de ejemplo/Eventos RADIAN/030 - Acuse_ReciboFEV.xml` | `d18a5fa5445f1317c10e6c75453d5488558bb2ad2f96cad935bbab35a7b03729` |
| `…/031 - Reclamo de la factura Electronica de Venta.xml` | `cfbe67334a089102714f0f501b3f47c33af536f6293c7f1bec7802e689eaeb54` |
| `…/032 - Recibo_B&S.xml` | `b02f936d972bb55705f334d208d7b41b840879f402248c29fb0eacada7806e83` |
| `…/033 - Aceptacion Expresa.xml` | `1acbf2f5c9699225c78092a453567304175b7358257ca7f92f557afe65a149f7` |
| `…/034 - Aceptacion_Tacita_FEV.xml` | `e1bcee93a9ecc6da66b28d5c528f2643663a62c4bd3492e8c450d28c8bdbfe7f` |

## 3. What was taken from the v1.8 files (status `OFFICIAL_FILE_PRIOR_VERSION`)

* **CUFE / CUDE / ApplicationResponse CUDE formulas and worked examples** — annex §11.1.2, §11.1.4, §11.1.5; the four worked examples
  are `specs/fiscal-hash/vectors/annex-fe-1.8.json` and are reproduced by `einvoice.hashing` (T-P15-02).
  **Erratum found:** §11.1.4.3 (nota débito) prints a composition containing Software-PIN `10201`, but its printed hash is the
  SHA-384 of that string *without* the PIN; the vector keeps both values (`specs/fiscal-hash/README.md`).
* **Code lists** — document types 01/02/03/04/91/92, tax schemes 01 IVA / 03 ICA / 04 INC, environments 1/2, credit-note concepts 1–5,
  debit-note concepts 1–4, claim concepts 01–04, identification types (`backend/einvoice/annex.py`).
* **RADIAN event codes** 030 acuse, 031 reclamo, 032 recibo del bien/servicio, 033 aceptación expresa, 034 aceptación tácita — from
  the toolbox example file names (the RADIAN v1.1 annex itself was not obtained: **B-09 stays open**, production emission refused).
* **Web-service method names** (annex §7): `SendBillSync`, `SendBillAsync`, `SendTestSetAsync`, `GetStatus`, `GetStatusZip`,
  `GetNumberingRange`, `GetXmlByDocumentKey`, `SendEventUpdateStatus` — listed for the gated `DirectDianAdapter` `[A]` only.
* **QR URL** (§11.3.1): habilitación `https://catalogo-vpfe-hab.dian.gov.co/document/searchqr?documentkey=<CUFE/CUDE>`
  (production `https://catalogo-vpfe.dian.gov.co/…` is never used by this edition).
* **Signature policy identifier**: `https://facturaelectronica.dian.gov.co/politicadefirma/v2/politicadefirmav2.pdf`.

## 4. Not obtained → labelled synthetic fixtures (`SYNTHETIC_NOT_LEGALLY_VERIFIED`)

| Item | Where | Effect |
|---|---|---|
| FE annex **v1.9** files (XSD, rules, examples) | — | Builders follow v1.8 structure; re-verify every rule against v1.9 (B-06, BLK file below) |
| DEE v1.0 annex (type code, CUDE inputs) | `annex.FAMILIES["DEE_POS"]` | type code `20` synthetic; CUDE computed with the FE CUDE formula |
| DS v1.1 annex (CUDS formula, type codes 05/95) | `hashing.FORMULAS["CUDS"]`, `annex.FAMILIES["DS*"]` | synthetic field order and codes; production refused |
| Nómina v1.0 annex (CUNE formula, XML layout, type codes 102/103) | `hashing.FORMULAS["CUNE"]`, `builders.ubl.nomina` | synthetic layout and field order; production refused |
| RADIAN v1.1 annex (event rules beyond codes) | `service._event_rules` | conservative rules (031/033/034 need 032; one acceptance or claim); B-09 |
| Required document counts of each DIAN test set | `habilitation.SYNTHETIC_REQUIRED` | synthetic default; the operator passes DIAN's counts per TestSetId |

**XSD validation (T-P15-01):** the official XSDs are identified and hashed above, but no XML-Schema validator is available in the
pinned dependencies (stdlib only; `lxml`/`xmlschema` are not in `uv.lock`). The golden-file tests check well-formedness, root
element and determinism; schema validation is requested in `contracts/change-requests/P15-1.md`.

## 5. Discrepancies with the prompt pack (B-06)

* The pack expects "FE v1.9 as of research date" — **confirmed** by Res. 227/2025 art. 1.5.1.10.2.
* The pack expects DEE v1.0 — **confirmed**. RADIAN: the compiled resolution adopts **v1.1** (it also mentions v1.0 historically).
* DS: compiled text adopts **v1.1**; Nómina: **v1.0**.
* No conflict found that requires a BLK-CCR beyond "v1.9 files not published where the pack expected them" (recorded in
  `contracts/change-requests/P15-1.md`, section B-06).

## 6. External gates (step by step; never satisfied in DEV)

**EG-1 — habilitación (FE, notes, DEE POS, DS, nómina) and numbering resolutions** (tenant + DIAN or PT)
1. In the DIAN "Servicios en línea" portal register as facturador electrónico and choose the software (own / PT / gratuito).
2. Register the software in the DIAN catalogue; DIAN assigns the **software ID** and the tenant sets the **PIN** → store both as
   secret files (`deploy/dev/secrets/einvoice/<name>`, mode 0600) and reference them in `PUT /api/v1/einvoice/settings`
   (`software_pin_ref`), never in the API body as values.
3. Create the **TestSetId** in the habilitación portal → `POST /api/v1/einvoice/habilitation/test-sets` and
   `:run` with `parameters.required` = the counts DIAN shows for that set; keep the run results + DIAN acceptance screenshot.
4. Request the **numbering resolution** (prefix, range, validity, technical key) → load it with `POST /numbering-ranges` (the
   technical key as a secret reference) or `:sync` through the PT.
5. Record the evidence (TestSetId, acceptance id/screenshot reference, resolution number) as an `EG-1` gate record.

**EG-2 — signing certificate** (tenant; only for direct signing)
1. Buy a certificate for electronic invoicing from an ONAC-accredited digital certification entity (B-15).
2. Keep the PKCS#12 in the secret store; record subject, serial, thumbprint and expiry (never the key) as certificate metadata;
   the worker alerts at 30/15/7 days before expiry.
3. Replace the DEV fixture signer (HMAC, `FIXTURE_NOT_CERTIFIED`) by an RSA XAdES-EPES signer (CCR P15-1) and validate a signed
   document in the DIAN habilitación validator.

**EG-3 — technology provider (PT)** (operator)
1. Sign the PT contract; obtain sandbox + production endpoints, API credentials, SLA, the PT's DIAN habilitación evidence, its
   ISO 27001 certificate (or dated commitment) and contingency plan (R-FE-06).
2. Register the PT: `POST /api/v1/einvoice/providers` (credentials as `secret://einvoice/<name>` only); if its API is not
   `generic-json-v1`, add a mapping with `einvoice.adapters.http_provider.register_mapping`.
3. `POST /providers/{id}:test-connection` (contract suite must pass) → `PUT /settings` to activate (a switch needs a second
   administrator). Production stays refused in this DEV-only edition.
