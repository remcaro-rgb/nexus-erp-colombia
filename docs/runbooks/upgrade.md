# Upgrades (reference)

* Django: the stack pins Django 5.2 LTS (C2-1). Plan for Django 6.2 LTS must be ready by **October 2027**: upgrade in a
  branch, run `python -W error::DeprecationWarning` test suite on 5.2 first, then 6.0/6.1 deprecations, then 6.2;
  `makemigrations --check` clean; full CI + scenarios; staged rollout.
* PostgreSQL minor updates (18.x): pull the new digest, `pg_dump` backup, replace the container (same volume), check
  `SELECT version()` and run the DR drill invariants.
* Image digests and lockfiles: Dependabot weekly PRs; each one passes CI (incl. grype) before merge. No `latest` tags (D-028).
* Keycloak major updates: export realm, test import on a scratch instance, then upgrade.
