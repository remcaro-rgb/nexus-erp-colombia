# Backup and disaster recovery

## DEV (runs)
* Backup: `deploy/dev/backup.sh` → `pg_dump -Fc` of `erp` into `$ERP_BACKUP_DIR` (default outside the repo), mode 0600.
* Restore: `deploy/dev/restore.sh FILE` (type `RESTORE erp`, or `--yes` for drills). Stops app services, recreates the
  database owned by `erp_owner`, `pg_restore --exit-on-error --single-transaction`, restarts app services.
* DR drill (`python3 scripts/ci/dr_drill.py`, requested `make dr-drill` target via CCR P03-1): dump → **fresh**
  PostgreSQL 18 container (`--network none`, tmpfs, throwaway passwords, same role init) → restore → invariants
  `deploy/dr/invariants.d/*.sql` → per-table row-count parity → `reports/dr/dr-drill.json` with measured restore time.
  Platform invariants: RLS enabled+forced on every `tenant_id` table, `erp_app` not superuser/BYPASSRLS/owner, constraints
  validated, indexes valid. Module invariants (shared/07 §9: 1 balanced journals, 3 GL inventory = valuation layers,
  4 AR/AP/GRNI = subledgers, 5 FA register, 6 payroll, 7 stock = moves, 8 immutability, 9 numbering) are added by their owners
  as `NN-<module>.sql` returning `(check, status, detail)` rows.

## Production reference (not run in this DEV-only project)
* WAL-G (or pgBackRest) continuous WAL archiving to S3/MinIO (`archive_mode=on`, `archive_timeout=300` in
  `docker-compose.prod.yml`), nightly full backup, PITR. Proposed targets: RPO ≤ 15 min, RTO ≤ 4 h (ADR-014) — targets to
  validate in drills, not guarantees.
* Object storage: bucket versioning on; fiscal documents in a bucket created with object lock (compliance mode) and a
  retention period configured per tenant (default policy 10 years, configurable; the legal period is an external input).
* Quarterly drill: restore into an isolated environment, run the full invariant suite, record measured RPO/RTO in the drill
  report (EG-12).
