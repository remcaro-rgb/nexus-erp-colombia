# Blockers and External Release Gates

**Honesty statement:** a single orchestration run can build, test, and package the whole system up to internal gate **G4 (release candidate)**. **No prompt can grant**
DIAN habilitación, a digital-signature certificate, contracts with providers, bank formats, or legal sign-off. Those are **external gates**. Until they are cleared,
the system must run with production fiscal transmission **disabled** (fail-closed; `FISCAL_MODE=sandbox|disabled` by default, and `production` needs all gates recorded).
Agents must **not** treat an external gate as a reason to leave code incomplete. Everything up to the boundary is implemented and tested against sandboxes and fixtures.

## External gates (EG)
| ID | Gate | Who clears it | Evidence required in `docs/compliance/gates/EG-xx.md` | System behavior until cleared |
|---|---|---|---|---|
| EG-1 | DIAN habilitación: test set approved (FE, notes, DEE POS, DS, nómina) for the issuing software or via the PT; numbering resolutions | Tenant + DIAN (or PT) | DIAN test-set acceptance screenshots/IDs, TestSetId, software ID/PIN registered | `FISCAL_MODE` ≠ production for that company |
| EG-2 | Issuer's digital-signature certificate from a digital certification entity (ONAC accreditation regime: B-15), used for direct signing per the DIAN signature policy. This is a different thing from PT habilitación (EG-3) | Tenant | Certificate subject/serial/expiry (never the key itself) | Direct adapter disabled |
| EG-3 | Technology-provider (PT) contract, API credentials, SLA, and evidence of the PT's **DIAN habilitación** plus its **ISO 27001** certificate (or the dated commitment, due within 18 months of habilitación) and contingency plan (R-FE-06) | Operator | Contract reference, sandbox + prod endpoints, certification evidence | Provider adapter in sandbox only |
| EG-4 | PILA operator agreement and file layout version confirmation | Tenant/operator | Operator acceptance of a test file | PILA export marked "draft layout" |
| EG-5 | Bank file formats and statement formats per bank | Tenant | Bank spec documents + test file acceptance | Generic CSV only |
| EG-6 | LLM provider keys, DPAs, retention terms, region (Ley 1581 Art. 26 transfer analysis) | Operator + DPO | Signed DPA/terms, transfer analysis memo | AI disabled for the tenant or local model only |
| EG-7 | Legal sign-off: tax (shared/04 B), labor/payroll (D), data protection (E), consumer | Colombian counsel / revisor fiscal | Signed review of every `LEGAL-REVIEW` row and parameter seed | Parameters that are UNKNOWN or lack a legal-review stamp block calculations in production |
| EG-8 | Contador público review of the CoA template, posting matrix, and NIIF policies per group | Contador | Signed review | Template marked "pending review" in onboarding |
| EG-9 | External penetration test + remediation | Third-party | Report + retest | Production launch blocked |
| EG-10 | Pinned MCP SDK passes the conformance suite for revision 2026-07-28 **and** a 2025-11-25 client. Evidence (2026-11-08): the Python `mcp` SDK v2.x states support for 2026-07-28 plus serving earlier revisions (T-15/T-16). This gate confirms it with our own tests | Engineering | SDK version, lock file, conformance run | Fallback (ADR-011): pin `mcp>=1.28,<2` and serve 2025-11-25 only, with the server design kept stateless-ready; documented as a limitation |
| EG-11 | Keycloak (or chosen IdP) support for the required OAuth features (RFC 8707 resource indicators, CIMD) confirmed | Engineering | Version + test | Fallback: pre-registered clients only; DCR disabled |
| EG-12 | Production infrastructure: backups tested, DR drill, monitoring, on-call, data residency decision | Operator | Drill report | Launch blocked |
| EG-13 | **Only if** the `[A]` flag `pos_offline_fiscal_selling` is wanted: written business and legal acceptance of eventual consistency (GL/stock posted at sync), oversell policy, bounded per-terminal number allocation and caps, plus Colombian counsel/PT validation that the disconnected procedure satisfies DIAN contingency rules (R-FE-05/07, annex) | Tenant + counsel | Signed acceptance + procedure validation | Flag stays off; POS is online-only (ADR-015) |

## Known blockers / unresolved items (B), after verification pass v1.1 (2026-11-08)
Legitimately unknowable today (no research can close these):
| ID | Item | Default handling |
|---|---|---|
| B-01 | UVT, SMMLV, auxilio de transporte for **2027** not yet published | `UNKNOWN` → fail closed for dates ≥ 2027-01-01 until loaded with an official source |
| B-13 | Municipal ICA tariffs, customer-specific tax profiles, item VAT classification | Tenant-loaded; no defaults; fail closed where missing |
| B-16 | Bank file formats (EG-5), PILA operator test acceptance (EG-4) | Bank/operator specific |

Pending legal events (official text verified; outcome pending):
| ID | Item | Default handling |
|---|---|---|
| B-02 | SMMLV 2026 nulidad case (litigation history only from secondary sources) | DISPUTED parameter; admin banner; recompute tool |
| B-03 | Decreto 572/2025 nulidad (litigation history only from secondary sources) | DISPUTED; both parameter sets kept by date |
| B-04 | Ley 2381/2024: exequible provisions effective **1-Apr-2027** (C-264/2026, verified). The returned provisions depend on the Cámara correction and a new Court review; reports of a re-vote or postponement are secondary | Flag off; enable no earlier than 2027-04-01 after legal sign-off; returned articles as separate disabled sub-flags |

Not yet opened from an official text (narrowed scope; agents must open before marking verified):
| ID | Item | Default handling |
|---|---|---|
| B-05 | **Only these payroll/labor values remain unverified:** ARL rate table by risk class (Decreto 1772/1994); FSP graduated scale above 16 SMMLV (Ley 797/2003); interest on cesantías 12% (Ley 52/1975); prima (CST 306), vacations (CST 186), overtime premiums (CST 168), integral salary (CST 132); incapacity/leave pay rules; auxilio de transporte 2026 decree text (Decreto 1470/2025); ET 383 table cell values (article opened, cells not legible) | Seed as `VERIFIED-SECONDARY`; P06/P13 open the official text (Senado/SIDN/normograma) at build time and upgrade; legal review |
| B-06 | Annex **files** (FE v1.9, DEE v1.0, nómina, DS, RADIAN): XSDs, code lists, CUFE/CUDE/CUNE formulas, web-service method names | P15 downloads them from the DIAN micrositio at build time and records hashes |
| B-09 | RADIAN **event codes** (event types and 3-business-day tacit acceptance verified; codes are in the annex) | Verify in the annex before enabling event emission in production |
| B-12 | Monthly hourly divisor at 42 h (e.g., 210): **not stated in Ley 2101/2466**; an interpretive rule | Parameter `monthly_hour_divisor` by date; legal review |
| B-14 | Exógena format specifications and the yearly tax calendar decree | Loaded per year by P06/P17 |
| B-15 | ONAC accreditation regime for digital certification entities (Ley 527/1999, its regulations) | Only affects the direct-signing option; collect evidence at EG-2 |

Resolved in v1.1 (kept for traceability): B-07 Res. 219/2025 (about RUB, not e-invoicing); B-08 PT certification (ISO 27001 or a commitment within 18 months, plus a contingency plan); B-10 Decreto 1154 (tacit acceptance confirmed in Res. 085); B-11 Ley 2466 text (opened via the Rama Judicial SIDN Diario Oficial scan).

## Rules for agents encountering a new blocker
1. Implement the code path fully behind a parameter, adapter, or flag. Write tests with fixtures, plus a test asserting fail-closed behavior in production mode.
2. Add a row to this register via a CCR (`contracts/change-requests/BLK-<agent>-<n>.md`) with the source you could not verify.
3. Never invent rates, codes, layouts, or dates. Never mark a parameter VERIFIED without opening an official source and recording its URL and access date.
