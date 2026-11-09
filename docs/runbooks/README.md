# Runbooks (owner: P03)

DEV-only run: only `dev-environment.md`, `backup-and-dr.md` (DEV part) and `observability.md` describe things that run.
All other runbooks are **reference procedures for a future production project**; they are not exercised here and do not
claim compliance (external gates EG-1…EG-13 remain open).

| Runbook | Scope |
|---|---|
| [dev-environment.md](dev-environment.md) | start / stop / restart / logs / seed / health / backup / tunnel — DEV |
| [backup-and-dr.md](backup-and-dr.md) | DEV backup/restore + DR drill; prod reference: WAL-G PITR, retention lock |
| [observability.md](observability.md) | OTel pipeline, PII scrubbing, dashboards |
| [deploy.md](deploy.md) | build → scan → deploy (reference) |
| [rollback.md](rollback.md) | image rollback, expand/contract migrations (reference) |
| [upgrade.md](upgrade.md) | Django LTS plan (6.2 by Oct 2027), PostgreSQL minor updates, image digests |
| [certificate-rotation.md](certificate-rotation.md) | DIAN signing certificate (PKCS#12), TLS certificates |
| [secret-rotation.md](secret-rotation.md) | DB roles, Django key, OIDC client secrets, MinIO, Valkey |
| [incident-response.md](incident-response.md) | triage, containment, data-breach notification (R-DP-04) |
| [dian-outage.md](dian-outage.md) | DIAN/PT outage → contingency mode (R-FE-05, P15) |
| [scale-out.md](scale-out.md) | more workers, API replicas, read replica |
