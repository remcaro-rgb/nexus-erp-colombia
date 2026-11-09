-- DR drill invariants, platform level (P03). Every query returns rows (check, status, detail);
-- status is PASSED or FAILED. Module-level invariants (shared/07 §9: balanced journals, subledger = GL,
-- stock = moves, numbering) are added by their owners as further files NN-<module>.sql via CCR.

-- ADR-003: every table with a tenant_id column has RLS enabled AND forced.
SELECT 'rls_forced_on_tenant_tables' AS check,
       CASE WHEN count(*) = 0 THEN 'PASSED' ELSE 'FAILED' END AS status,
       coalesce(string_agg(c.relname, ',' ORDER BY c.relname), '') AS detail
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
WHERE c.relkind IN ('r', 'p')
  AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
  AND NOT (c.relrowsecurity AND c.relforcerowsecurity);

-- ADR-003: the application role can never bypass RLS and owns nothing.
SELECT 'app_role_no_bypass' AS check,
       CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'erp_app' AND NOT rolsuper AND NOT rolbypassrls)
             AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = 'erp_app')
            THEN 'PASSED' ELSE 'FAILED' END AS status,
       '' AS detail;

-- Restored constraints are all validated and no index is left invalid.
SELECT 'constraints_validated' AS check,
       CASE WHEN count(*) = 0 THEN 'PASSED' ELSE 'FAILED' END AS status,
       coalesce(string_agg(conname, ','), '') AS detail
FROM pg_constraint WHERE NOT convalidated;

SELECT 'indexes_valid' AS check,
       CASE WHEN count(*) = 0 THEN 'PASSED' ELSE 'FAILED' END AS status,
       coalesce(string_agg(indexrelid::regclass::text, ','), '') AS detail
FROM pg_index WHERE NOT indisvalid;
