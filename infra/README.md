## Infra stack

**Full VPS checklist (pull + pipeline):** [DEPLOY.md](./DEPLOY.md)

| Service | Port | Notes |
|---------|------|--------|
| Dagster | 3000 | Orchestration |
| dbt Workbench | 3001 | UI; models saved to `dbt/models/` |
| dbt API | 8001 | |
| Airbyte | 8000 | Install with `airbyte/install_abctl.sh` |

Warehouse: external Postgres (`WAREHOUSE_PG_*` in `.env`). Schemas: `raw`, `silver`, `gold` — see `sql/001_database_structure.sql`.

### Start

```bash
cp .env.example .env
docker-compose up -d --build
```

On older hosts use `docker-compose` (hyphen), not `docker compose`. Compose file is v2.4 (compatible with legacy `docker-compose` 1.x).

Dagster job containers use Docker network `infra_analytics` (from project folder name `infra`).

### dbt-Workbench Environments

Workbench must use the project `profiles.yml` (not an empty internal folder). After `git pull`, recreate the backend if Environments shows **No profiles configured yet**:

```bash
docker-compose up -d dbt_workbench_backend
```

Create environment: Profile **`mobile_analytics`**, Target **`prod`**. Ignore the **New Profile** localhost template.

### After adding dbt models in Workbench

```bash
docker-compose build --no-cache user_code
docker-compose up -d user_code dagster_webserver dagster_daemon
```

Dagster job: `ingest_and_transform` (Airbyte sync → dbt build) when `AIRBYTE_ENABLED=true`, else `transform_raw_to_silver_gold`.

### Raw → silver pipeline

1. Run one Airbyte sync; list tables: `psql ... -c "\dt raw.*"`
2. Set `infra/dbt/models/sources.yml` and `silver/stg_custom_api.sql` to the real raw table name
3. In `.env`: `AIRBYTE_ENABLED=true`, `AIRBYTE_CONNECTION_ID` or `AIRBYTE_CONNECTION_NAME` (exact name from Airbyte UI)
4. Rebuild `user_code`, enable schedule in Dagster UI (Deployments → Schedules)
5. Materialize job `ingest_and_transform` or wait for `DAGSTER_ETL_CRON` (default 02:00)

### dbt-Workbench vendor

Built from `vendor/dbt-Workbench` (pinned in repo).
