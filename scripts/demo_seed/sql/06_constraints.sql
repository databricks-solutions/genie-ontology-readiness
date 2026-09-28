-- 06_constraints.sql
-- PRIMARY KEY + FOREIGN KEY (RELY) constraints. Databricks does not enforce these
-- (they are informational/RELY) but they are what Genie and the readiness probe
-- read to infer joins. Idempotent: drop-if-exists then add; the runner also
-- tolerates "already exists" so a partial prior run re-converges.
-- Order matters: drop FKs, drop PKs, add PKs, then add FKs (FK targets need a PK).
--
-- Pillar coverage:
--   * Relationships & Modeling — declared PK and FK counts (+ gold-layer naming).

-- ============ drop FKs ============
ALTER TABLE {{CATALOG}}.sales.dim_customer        DROP CONSTRAINT IF EXISTS fk_customer__geo;
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders         DROP CONSTRAINT IF EXISTS fk_orders__customer;
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders         DROP CONSTRAINT IF EXISTS fk_orders__product;
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders         DROP CONSTRAINT IF EXISTS fk_orders__date;
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_payments     DROP CONSTRAINT IF EXISTS fk_payments__customer;
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_payments     DROP CONSTRAINT IF EXISTS fk_payments__account;
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_payments     DROP CONSTRAINT IF EXISTS fk_payments__date;
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_gl_entries   DROP CONSTRAINT IF EXISTS fk_gl__account;
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_gl_entries   DROP CONSTRAINT IF EXISTS fk_gl__date;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_shipments DROP CONSTRAINT IF EXISTS fk_shipments__order;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_shipments DROP CONSTRAINT IF EXISTS fk_shipments__warehouse;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_shipments DROP CONSTRAINT IF EXISTS fk_shipments__date;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_inventory DROP CONSTRAINT IF EXISTS fk_inventory__product;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_inventory DROP CONSTRAINT IF EXISTS fk_inventory__warehouse;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_inventory DROP CONSTRAINT IF EXISTS fk_inventory__date;
-- @@
-- ============ drop PKs ============
ALTER TABLE {{CATALOG}}.reference.dim_date        DROP CONSTRAINT IF EXISTS pk_dim_date;
-- @@
ALTER TABLE {{CATALOG}}.reference.dim_geography   DROP CONSTRAINT IF EXISTS pk_dim_geography;
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_customer        DROP CONSTRAINT IF EXISTS pk_dim_customer;
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_product         DROP CONSTRAINT IF EXISTS pk_dim_product;
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders         DROP CONSTRAINT IF EXISTS pk_fact_orders;
-- @@
ALTER TABLE {{CATALOG}}.finance.dim_account       DROP CONSTRAINT IF EXISTS pk_dim_account;
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_payments     DROP CONSTRAINT IF EXISTS pk_fact_payments;
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_gl_entries   DROP CONSTRAINT IF EXISTS pk_fact_gl_entries;
-- @@
ALTER TABLE {{CATALOG}}.operations.dim_warehouse  DROP CONSTRAINT IF EXISTS pk_dim_warehouse;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_shipments DROP CONSTRAINT IF EXISTS pk_fact_shipments;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_inventory DROP CONSTRAINT IF EXISTS pk_fact_inventory;
-- @@
-- ============ add PKs (RELY) ============
ALTER TABLE {{CATALOG}}.reference.dim_date        ADD CONSTRAINT pk_dim_date        PRIMARY KEY (date_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.reference.dim_geography   ADD CONSTRAINT pk_dim_geography   PRIMARY KEY (geo_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_customer        ADD CONSTRAINT pk_dim_customer    PRIMARY KEY (customer_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.sales.dim_product         ADD CONSTRAINT pk_dim_product     PRIMARY KEY (product_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders         ADD CONSTRAINT pk_fact_orders     PRIMARY KEY (order_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.finance.dim_account       ADD CONSTRAINT pk_dim_account     PRIMARY KEY (account_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_payments     ADD CONSTRAINT pk_fact_payments   PRIMARY KEY (payment_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_gl_entries   ADD CONSTRAINT pk_fact_gl_entries PRIMARY KEY (entry_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.operations.dim_warehouse  ADD CONSTRAINT pk_dim_warehouse   PRIMARY KEY (warehouse_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_shipments ADD CONSTRAINT pk_fact_shipments  PRIMARY KEY (shipment_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_inventory ADD CONSTRAINT pk_fact_inventory  PRIMARY KEY (inventory_key) RELY;
-- @@
-- ============ add FKs (RELY) ============
ALTER TABLE {{CATALOG}}.sales.dim_customer        ADD CONSTRAINT fk_customer__geo        FOREIGN KEY (geo_key)      REFERENCES {{CATALOG}}.reference.dim_geography(geo_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders         ADD CONSTRAINT fk_orders__customer      FOREIGN KEY (customer_key) REFERENCES {{CATALOG}}.sales.dim_customer(customer_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders         ADD CONSTRAINT fk_orders__product       FOREIGN KEY (product_key)  REFERENCES {{CATALOG}}.sales.dim_product(product_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.sales.fact_orders         ADD CONSTRAINT fk_orders__date          FOREIGN KEY (date_key)     REFERENCES {{CATALOG}}.reference.dim_date(date_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_payments     ADD CONSTRAINT fk_payments__customer    FOREIGN KEY (customer_key) REFERENCES {{CATALOG}}.sales.dim_customer(customer_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_payments     ADD CONSTRAINT fk_payments__account     FOREIGN KEY (account_key)  REFERENCES {{CATALOG}}.finance.dim_account(account_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_payments     ADD CONSTRAINT fk_payments__date        FOREIGN KEY (date_key)     REFERENCES {{CATALOG}}.reference.dim_date(date_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_gl_entries   ADD CONSTRAINT fk_gl__account           FOREIGN KEY (account_key)  REFERENCES {{CATALOG}}.finance.dim_account(account_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.finance.fact_gl_entries   ADD CONSTRAINT fk_gl__date              FOREIGN KEY (date_key)     REFERENCES {{CATALOG}}.reference.dim_date(date_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_shipments ADD CONSTRAINT fk_shipments__order      FOREIGN KEY (order_key)    REFERENCES {{CATALOG}}.sales.fact_orders(order_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_shipments ADD CONSTRAINT fk_shipments__warehouse  FOREIGN KEY (warehouse_key) REFERENCES {{CATALOG}}.operations.dim_warehouse(warehouse_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_shipments ADD CONSTRAINT fk_shipments__date       FOREIGN KEY (date_key)     REFERENCES {{CATALOG}}.reference.dim_date(date_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_inventory ADD CONSTRAINT fk_inventory__product    FOREIGN KEY (product_key)  REFERENCES {{CATALOG}}.sales.dim_product(product_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_inventory ADD CONSTRAINT fk_inventory__warehouse  FOREIGN KEY (warehouse_key) REFERENCES {{CATALOG}}.operations.dim_warehouse(warehouse_key) RELY;
-- @@
ALTER TABLE {{CATALOG}}.operations.fact_inventory ADD CONSTRAINT fk_inventory__date       FOREIGN KEY (date_key)     REFERENCES {{CATALOG}}.reference.dim_date(date_key) RELY;
