# Incident response (reference; DEV incidents follow the same steps without external notification)

1. **Detect & triage**: alert or report → assign incident lead → severity (SEV1 data exposure / ledger integrity,
   SEV2 outage, SEV3 degradation). Open an incident log (UTC timestamps).
2. **Contain**: revoke tokens/sessions, rotate affected secrets (secret-rotation.md), block the attack path, preserve
   evidence (logs are already PII-scrubbed; copy audit tables read-only).
3. **Eradicate & recover**: fix forward; corrections to fiscal/accounting data only by reversal documents; restore from
   backup only into a new instance (backup-and-dr.md).
4. **Personal-data breach (R-DP-04, Ley 1581/2012 regime)**: the data-protection officer assesses the breach; notification
   to the authority (SIC) and to affected data subjects follows the legally required procedure and deadlines, which are an
   **external legal input** to be confirmed by counsel — this runbook does not state them. Record what data, how many
   subjects, measures taken.
5. **Post-incident**: blameless review within 5 business days, actions with owners, update runbooks and tests.
