-- Schemas only (no tables). Tables are created by:
--   raw.*    -> Airbyte on first sync (per connection/stream)
--   silver.* -> dbt run (models/materialized table)
--   gold.*   -> dbt run (models/materialized table)
--
-- Run on DB VPS as postgres superuser, or as a user with CREATE privilege:
--   psql -h HOST -U postgres -d datawarehouse -f 001_database_structure.sql

BEGIN;

CREATE SCHEMA IF NOT EXISTS raw;
COMMENT ON SCHEMA raw IS 'Bronze: Airbyte creates tables here automatically';

CREATE SCHEMA IF NOT EXISTS silver;
COMMENT ON SCHEMA silver IS 'Silver: dbt creates tables on dbt run';

CREATE SCHEMA IF NOT EXISTS gold;
COMMENT ON SCHEMA gold IS 'Gold: dbt creates tables on dbt run; Power BI reads here';

COMMIT;

-- Grants for application user (change name if different)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'datawarehouseuser') THEN
    GRANT USAGE ON SCHEMA raw, silver, gold TO datawarehouseuser;
    GRANT CREATE ON SCHEMA raw, silver, gold TO datawarehouseuser;
    GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA raw, silver, gold TO datawarehouseuser;
    GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA raw, silver, gold TO datawarehouseuser;
    ALTER DEFAULT PRIVILEGES IN SCHEMA raw, silver, gold
      GRANT ALL ON TABLES TO datawarehouseuser;
    ALTER DEFAULT PRIVILEGES IN SCHEMA raw, silver, gold
      GRANT ALL ON SEQUENCES TO datawarehouseuser;
  END IF;
END $$;
