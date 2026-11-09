#!/usr/bin/env bash
# PostgreSQL role model (ADR-003, P03 deliverable 2). Runs once, on first initialisation of the db volume,
# via the postgres image's /docker-entrypoint-initdb.d hook. Owner: P03.
#   erp_owner    owns database `erp` and schema `public`; runs migrations (one-shot `migrate` job)
#   erp_app      the application: LOGIN, NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE, owns nothing
#   erp_readonly reporting / replica reads: SELECT only, NOBYPASSRLS
#   erp_platform cross-tenant platform jobs: NOBYPASSRLS; gets only the aggregate views modules grant to it
# Passwords come from the environment (generated into deploy/dev/secrets/dev.env); never printed.
set -euo pipefail

: "${ERP_DB_OWNER_PASSWORD:?}" "${ERP_DB_APP_PASSWORD:?}" "${ERP_DB_READONLY_PASSWORD:?}" "${ERP_DB_PLATFORM_PASSWORD:?}"
ERP_DB_NAME="${ERP_DB_NAME:-erp}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_pw="$ERP_DB_OWNER_PASSWORD" -v app_pw="$ERP_DB_APP_PASSWORD" \
  -v ro_pw="$ERP_DB_READONLY_PASSWORD" -v platform_pw="$ERP_DB_PLATFORM_PASSWORD" \
  -v dbname="$ERP_DB_NAME" <<'SQL'
CREATE ROLE erp_owner    LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'owner_pw';
CREATE ROLE erp_app      LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT PASSWORD :'app_pw';
CREATE ROLE erp_readonly LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT PASSWORD :'ro_pw';
CREATE ROLE erp_platform LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT PASSWORD :'platform_pw';
-- Django test runner (pytest-django) creates test_<db>; allowed for the owner only in dev.
ALTER ROLE erp_owner CREATEDB;
CREATE DATABASE :"dbname" OWNER erp_owner ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE :"dbname" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"dbname" TO erp_app, erp_readonly, erp_platform;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$ERP_DB_NAME" <<'SQL'
ALTER SCHEMA public OWNER TO erp_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO erp_app, erp_readonly, erp_platform;
-- Everything erp_owner creates later (migrations) is usable by the app, readable by readonly.
ALTER DEFAULT PRIVILEGES FOR ROLE erp_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO erp_app;
ALTER DEFAULT PRIVILEGES FOR ROLE erp_owner IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO erp_app;
ALTER DEFAULT PRIVILEGES FOR ROLE erp_owner IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO erp_app;
ALTER DEFAULT PRIVILEGES FOR ROLE erp_owner IN SCHEMA public
  GRANT SELECT ON TABLES TO erp_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE erp_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO erp_readonly;
-- erp_platform receives grants only on the aggregate views that modules create for it (ADR-003).
SQL
