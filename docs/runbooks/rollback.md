# Rollback (reference)

* Application: redeploy the previous `erp-<service>:<tag>` (images are immutable, tagged by commit). Because migrations
  follow expand/contract, the previous release runs against the expanded schema; never roll back by deleting data.
* Database: never `DROP`/down-migrate posted data. Corrections are reversal documents (C1-5). A failed expand migration is
  fixed forward; a contract migration is only applied after the release that no longer needs the old columns is stable.
* Configuration: secrets and env are versioned in the secret manager; restore the previous version and restart.
* Last resort: PITR restore into a new instance (backup-and-dr.md), then cut over; never overwrite the damaged instance.
