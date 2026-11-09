# Certificate rotation (reference)

* **DIAN signing certificate (PKCS#12)**: an external input (habilitación/production certificates are EG items). Stored
  encrypted (ADR-012), decrypted only in the einvoice worker memory, access audited. Rotation: upload the new PKCS#12 to
  the secret manager as a new version → P15 validates chain/expiry in SANDBOX → switch the active version during a low
  traffic window → keep the old version until no pending documents reference it → revoke. Alert 30 days before expiry.
* TLS certificates (staging/prod proxy): ACME/managed certificates; rotation is automatic; monitor expiry ≥ 14 days.
* In DEV there is no real certificate: the DIAN adapter runs `FIXTURE_NOT_CERTIFIED`.
