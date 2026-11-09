# Scale-out (reference)

* Workers: `docker compose ... up -d --scale worker=N`; queues per workload (einvoice, reports, imports) so slow jobs do not
  starve posting events; watch `erp_celery_queue_length`.
* API: increase `ERP_WEB_CONCURRENCY` (gunicorn workers) and replicas behind the proxy; stateless sessions (OIDC tokens).
* Database: a streaming read replica for `erp_readonly` reporting; the app keeps writing to the primary; PgBouncer in
  transaction mode only with `SET LOCAL` tenancy binding (ADR-003).
* Outbox relay: one active relay per database (leader election by advisory lock) — scale consumers, not relays.
