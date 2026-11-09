# DIAN / technology-provider outage → contingency mode (reference; DEV uses the simulator)

* A DIAN/PT outage while our server is reachable is **not** an offline exception (C1-12): sales still commit in-line
  (GL + stock), only the fiscal transmission follows the contingency flow (R-FE-05, P15).
* Detect: `erp_dian_gateway_mode` / `erp_dian_contingency_active` dashboard, gateway error rate, P15 health.
* Activate contingency in P15 (authorised role, reason audited): documents are issued with the contingency numbering and
  stored pending; the outbox retries transmission at-least-once with idempotency keys.
* Recovery: P15 transmits the pending documents in order, reconciles statuses, alerts on rejections; nothing is rolled back
  (C1-10). Official contingency rules (types, deadlines) are external DIAN inputs — never invented here.
* In DEV the gateway is `FIXTURE_NOT_CERTIFIED`; outages can be simulated with the simulator's failure switch (P15).
