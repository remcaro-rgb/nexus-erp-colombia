# Parameter verification record (P06)

Engineering record of the regulatory parameter re-verification required by the P06 prompt
("open each official source in shared/05 marked SEARCH/SECONDARY; upgrade only after opening it").
**This is not legal advice and does not claim legal compliance.** Every value below still needs the
EG-7 legal-review stamp (`legal_reviewed_by/at`) before any production use; the seed carries none, so
production rules (`ParameterNotVerified`) refuse all of them.

* Verification date: **2026-11-09** (America/Bogota), from a cloud build session with HTTPS access.
* Method: the official page was downloaded (HTTP 200), its text extracted, and the cited sentence located.
  `sha256` is the hash of the downloaded document as served that day (evidence for re-checks, not a
  signature). Pages that could not be opened keep their previous status.
* Seed file: `backend/tax/fixtures/parameters_2026.yaml` (version `2026.1`). Checked by
  `tests/regulatory/test_parameter_seed.py`.

## Sources opened on 2026-11-09

| Source | Document | Result | sha256 of the downloaded document | Finding |
|---|---|---|---|---|
| S-08 | DIAN Res. 000238/2025 (normograma) | OPENED (HTTP 200) | `cbf41f002d8a404646f6d1d0476d881f7fbbc1c5d7ba95498d95c9ab361447c7` | Art. 1.2.1: "Fijar en cincuenta y dos mil trescientos setenta y cuatro pesos (**$52.374**) el valor de la Unidad de Valor Tributario -UVT que regirá durante el año 2026". **UVT 2026 = 52.374 re-opened and confirmed** (G1 acceptance). |
| S-09 | Decreto 572/2025 (normograma) | OPENED | `870a3893e0a73e584ec7a85ea5dafd15460c81dca35394d4af7c39042764bfd3` | Art. 2 (DUR 1625 art. 1.2.4.4.1): no withholding on services below **2 UVT**. Art. 3: agricultural products ≤ **70 UVT** exempt, else **1.5 %**. Art. 4: café pergamino/cereza ≤ **70 UVT**, else **0.5 %**. Art. 5: gold purchases by C.I. **2.5 %**. Art. 6: literal i) of 1.2.4.9.1 → below **10 UVT**. Art. 9: applies from the first calendar day of the month after publication (dated 28-May-2025). The litigation facts (suspension 7-May-2026, revocation 2-Jun-2026, application from 1-Jul-2026 per DIAN Concepto 15414/2026) remain **secondary** (S-32) → status stays **DISPUTED**. The pre-572 texts ("Legislación anterior") are loaded by JavaScript and were not readable → the pre-572 set stays **UNKNOWN** (fails closed). General rates for compras/servicios/honorarios/arrendamientos are not in the opened texts → **UNKNOWN**. |
| S-10 | Estatuto Tributario compilado (normograma) | OPENED | `dfa3a40092232da046c5ae80d88575e665ddec91b97c3f5b7a0a688aa8fdccf5` | ET 468: "del diecinueve por ciento (19%)". ET 468-1: "gravados con la tarifa del cinco por ciento (5%)". ET 437-1: "hasta del cincuenta por ciento (50%) … será aplicable la tarifa del quince por ciento (15%)". ET 336 num. 3: 40 % / "mil trescientas cuarenta (1.340) UVT anuales". **ET 383 table** read from the official image `graficas/estatuto_tributario_obj_37.gif` (sha256 `b9c11a994c6c4bfbc5b697748b2b38fbd5a58bb939a4b9e7e56204177cf9e355`): >0–95 UVT 0 %; >95–150 19 %; >150–360 28 % + 10 UVT; >360–640 33 % + 69 UVT; >640–945 35 % + 162 UVT; >945–2300 37 % + 268 UVT; >2300 39 % + 770 UVT → **upgraded VERIFIED-SECONDARY → VERIFIED-PRIMARY** (B-05 table cells). ET 206 num. 10 (25 %) not located in the extraction → not seeded. |
| S-36 | Ley 100/1993 compilada (normograma) | OPENED | `61c93571daa27db4df61ee67c5cb4bff6fbb1a6688158f25bbb9850e705179af` | Additional subsistence contribution (FSP scale): "de 16 a 17 smlmv de un 0.2%, de 17 a 18 smlmv de un 0.4%, de 18 a 19 smlmv de un 0.6%, de 19 a 20 smlmv de un 0.8% y superiores a 20 smlmv de 1%" → **FSP scale upgraded VERIFIED-SECONDARY → VERIFIED-PRIMARY**. |
| S-41 | Ley 51/1983 (SIDN PDF scan) | DOWNLOADED (HTTP 200, image PDF) | `f18917ea776afe0717ae63f3521db479a47ae199ab9472ccece5f05cea734e4e` | Scan only (no OCR in this session); the status VERIFIED-PRIMARY of shared/04 (OCR read on 2026-11-08) is kept. The holiday algorithm (`localization_co/holidays.py`) is tested for 2026 and 2027. |
| S-11 | DUR 1625/2016 (normograma) | FETCHED | `430aa5dc8c4117c9cbab25339d31ffb56e808fcc853655cc8f574f0de024e40c` | Confirms literal i) of 1.2.4.9.1 as substituted by Decreto 572 art. 6 (10 UVT). The yearly tax-calendar decree is not in it → calendars stay a tenant data import (R-TX-07). |

## Sources that could not be opened (status kept)

| Source | Attempt on 2026-11-09 | Status kept |
|---|---|---|
| S-15 (Decreto 1469/2025, SMMLV; Decreto 1470/2025, auxilio de transporte) | Cancillería compilation: HTTP 403 (sha256 of the refusal page `1a4e453d…112e`); DIAN normograma: HTTP 404 | SMMLV value VERIFIED-SEARCH, parameter **DISPUTED** (S-33 secondary litigation); transport allowance **VERIFIED-SECONDARY** |
| B-05 (Decreto 1772/1994, ARL rates) | DIAN normograma: HTTP 404 | ARL table **VERIFIED-SECONDARY** (values from secondary sources; B-05 open) |
| S-18 (Código Sustantivo del Trabajo) | not retried (HTTP 500 on 2026-11-08) | not seeded by P06 (P13 parameters) |
| S-32, S-33 (litigation facts) | secondary by nature | DISPUTED statuses kept |
| S-37, S-38, S-39 | not re-opened (already OPENED on 2026-11-08) | VERIFIED-PRIMARY kept |

## Resulting seed (2026)

| Code | Value | Status | Source | Validity |
|---|---|---|---|---|
| `uvt` | 52.374 | VERIFIED-PRIMARY | S-08 | 2026-01-01 … 2026-12-31 (2027 fails closed) |
| `smmlv` | 1.750.905 | DISPUTED (legal history: suspended 2026-02-12, Decreto 159/2026, suspension revoked 2026-07-09) | S-15 / S-33 | 2026 |
| `aux_transporte` | 249.095 | VERIFIED-SECONDARY | S-15 | 2026 |
| `iva.general` / `iva.reducido` | 19 % / 5 % | VERIFIED-PRIMARY | S-10 | from 2026-01-01, open-ended |
| `reteiva.general` | 15 % | VERIFIED-PRIMARY | S-10 | from 2026-01-01, open-ended |
| `retefuente.servicios.base_uvt` | 2 UVT / UNKNOWN / 2 UVT | DISPUTED / UNKNOWN / DISPUTED | S-09 | 01-01…05-06 / 05-07…06-30 / 07-01…12-31 |
| `retefuente.{otros_ingresos,agricola,cafe}.*` | 10 UVT; 70 UVT & 1.5 %; 70 UVT & 0.5 % | DISPUTED | S-09 | 2026-07-01 … 2026-12-31 |
| `retefuente.{servicios,compras,honorarios,arrendamientos}.tarifa`, `retefuente.compras.base_uvt` | — | UNKNOWN (fail closed) | S-09 | 2026 |
| `ret_lab.art383.tabla` | table above | VERIFIED-PRIMARY (upgraded) | S-10 | from 2026-01-01 |
| `ret_lab.depuracion.tope_pct` / `tope_uvt` | 40 % / 1.340 UVT | VERIFIED-PRIMARY | S-10 | from 2026-01-01 |
| `ss.*` (salud 8.5/4, pensión 12/4, FSP 1 % from 4 SMMLV, FSP scale, IBC 25 SMMLV, 40 % rule) | | VERIFIED-PRIMARY (FSP scale upgraded) | S-36, S-37 | from 2026-01-01 |
| `parafiscal.ccf` / `sena` / `icbf`, `exoneracion.umbral_smmlv` | 4 % / 2 % / 3 %, 10 SMMLV | VERIFIED-PRIMARY | S-38, S-39, S-10 | from 2026-01-01 |
| `arl.tarifas` | I 0.522 · II 1.044 · III 2.436 · IV 4.350 · V 6.960 % | VERIFIED-SECONDARY | B-05 | from 2026-01-01 |

ICA tariffs: none (municipal, B-13); a tenant loads them per municipality/CIIU through `/api/v1/tax/ica-tariffs`
plus a dated parameter. Exógena format packs: only `SYN-EXO-1001` (NOT_DIAN_VALID, D-019, B-14).

## Synthetic development fixture

`backend/tax/fixtures/parameters_synthetic_dev.yaml` (codes prefixed `syn.`, `synthetic: true`, source `SYN-DEV`)
gives demo values for the concepts whose official values are UNKNOWN. It is labelled
**FIXTURE — NOT LEGALLY VERIFIED** in every computation warning and is refused by production rules.

## How to re-verify

1. Open the official source; if confirmed, create a source (`POST /api/v1/tax/sources`) with the access date and
   the excerpt hash, upload the document, and propose the value (`POST /api/v1/tax/parameters:propose`).
2. A second person approves (`…/{id}:approve`); with `parameters.legal_review=true` the approver records the EG-7 stamp.
3. Update this record and `parameters_2026.yaml` (or the next year's seed) in the same change.
