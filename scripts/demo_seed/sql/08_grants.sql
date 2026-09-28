-- 08_grants.sql
-- Read grants so the readiness app can scan the catalog — whether it runs
-- on-behalf-of a user (OBO) or as the app service principal. Catalog-level grants
-- cascade to every current and future schema/table. Idempotent (GRANT is additive).
-- The seeding user owns the catalog and already has full rights; these grants open
-- read access to the wider account so the app (and reviewers) can assess it.
--
-- NOTE: `account users` is the built-in group of all workspace/account users.
-- Narrow this to a specific group if broad read is not desired in your workspace.

GRANT USE CATALOG ON CATALOG {{CATALOG}} TO `account users`;
-- @@
GRANT USE SCHEMA  ON CATALOG {{CATALOG}} TO `account users`;
-- @@
GRANT SELECT      ON CATALOG {{CATALOG}} TO `account users`;
