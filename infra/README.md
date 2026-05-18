## Infra stack

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

### After adding dbt models in Workbench

```bash
docker-compose build --no-cache user_code
docker-compose up -d user_code dagster_webserver dagster_daemon
```

Dagster job: `transform_raw_to_silver_gold`

### dbt-Workbench vendor

Built from `vendor/dbt-Workbench` (pinned in repo).
