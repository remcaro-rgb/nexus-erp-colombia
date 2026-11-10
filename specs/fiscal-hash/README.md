# Fiscal hash specification — CUFE / CUDE / CUNE / CUDS (owner P15)

Language-neutral specification of the DIAN unique codes computed **server side only** (`backend/einvoice/hashing.py`,
the `[P]` implementation). No TypeScript port exists: the `[A]` offline fiscal POS flag (ADR-015) is not built.

* `formulas.json` — the field order per code, the fixed tax codes, which inputs are secrets (technical key `ClTec`,
  `SoftwarePIN`: never logged, never returned) and the provenance `status` of each formula. It is generated from
  `einvoice.hashing.FORMULAS`; the test `test_T_P15_03_*` fails if the two drift apart.
* Algorithm: `SHA-384( concatenation of the fields )`, lower-case hex, UTF-8.
* Amounts: dot decimal separator, **two decimals truncated** (not rounded), no thousands separator, no currency symbol.
  Missing taxes are `0.00`.

## Vectors
| File | Origin | Status |
|---|---|---|
| `vectors/annex-fe-1.8.json` | Worked examples of DIAN Anexo Técnico FE **v1.8** §11.1 (FE CUFE, NC CUDE, ND CUDE, ApplicationResponse CUDE) | `OFFICIAL_FILE_PRIOR_VERSION` — the adopted annex is v1.9 (Res. 227/2025); re-verify (B-06) |
| `vectors/synthetic.json` | Computed by the reference implementation from synthetic inputs (CUDS, CUNE, truncation case) | `SYNTHETIC_NOT_LEGALLY_VERIFIED` — the DS v1.1 and Nómina v1.0 annex files were not obtained |

**Annex erratum (v1.8 §11.1.4.3, nota débito):** the printed composition contains Software‑PIN `10201`, but the printed hash
is SHA‑384 of that string *without* the PIN. The vector keeps both (`expected` = formula over the printed composition,
`annex_published_hash` = the printed value, reproduced by the test with the PIN removed).

Every implementation (any language) must reproduce every `composition` and `expected` value of both files.
