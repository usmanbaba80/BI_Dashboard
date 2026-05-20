# BI_Dashboard

Analytics stack: **Airbyte** (ingest) → **Postgres** (`raw` / `silver` / `gold`) → **dbt Workbench** + **Dagster** (transform) → **Power BI** (reporting).

## VPS quick start

```bash
git clone <repo-url> /opt/BI_Dashboard
cd /opt/BI_Dashboard/infra
cp .env.example .env
# edit .env (WAREHOUSE_PG_*, DBTWB_VITE_API_BASE_URL=http://YOUR_IP:8001)

docker-compose up -d --build
```

Airbyte (separate): see `infra/airbyte/README.md` — use hostname `airbyte.local`, not raw IP.

Warehouse schemas only:

```bash
psql ... -f sql/001_database_structure.sql
```

dbt models: create in Workbench UI under `infra/dbt/models/` after Airbyte syncs; register raw tables in `models/sources.yml`.

Details: `infra/README.md` — **one-shot deploy:** `infra/DEPLOY.md`
