-- Drop all tables in raw, silver, gold (keeps the three schemas).
-- Use this once if you already ran the old 001_database_structure.sql with pre-built tables.
--
--   psql -h HOST -U postgres -d datawarehouse -f 002_drop_tables_keep_schemas.sql

DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT format('%I.%I', schemaname, tablename) AS fqname
    FROM pg_tables
    WHERE schemaname IN ('raw', 'silver', 'gold')
  LOOP
    EXECUTE 'DROP TABLE IF EXISTS ' || r.fqname || ' CASCADE';
    RAISE NOTICE 'Dropped %', r.fqname;
  END LOOP;
END $$;

-- Re-apply schema comments (schemas remain)
COMMENT ON SCHEMA raw IS 'Bronze: Airbyte creates tables here automatically';
COMMENT ON SCHEMA silver IS 'Silver: dbt creates tables on dbt run';
COMMENT ON SCHEMA gold IS 'Gold: dbt creates tables on dbt run; Power BI reads here';
