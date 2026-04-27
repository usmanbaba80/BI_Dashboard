## Infra Stack (PowerBi project)

This folder contains the deployment setup aligned with `Solution_Stack_Guide.html`.

### What runs from `docker-compose.yml`

- Dagster UI: `http://<host>:3000`
- dbt-Workbench UI: `http://<host>:3001`
- dbt-Workbench API: `http://<host>:8001`
- Dagster metadata Postgres (internal)
- dbt-Workbench metadata Postgres (internal)

### What runs outside Compose

- Airbyte via `abctl` on App VPS (see `airbyte/README.md`)
- Expected endpoint: `http://<host>:8000`

### Warehouse pattern

- External Postgres (DB VPS) is the analytics warehouse.
- Compose services connect over private networking using `WAREHOUSE_PG_*`.
- Contabo object storage (`S3_*`) is available for backups/artifacts.

### Quick start

```bash
cd infra
cp .env.example .env
# edit .env
docker compose up -d --build
```

### Note on dbt-Workbench image build

`docker-compose.yml` is pinned to a local vendored copy:

- path: `infra/vendor/dbt-Workbench`
- commit: `dac2af58d0f983c29f4971a5423ebe637d9b810b`

This makes builds reproducible and avoids relying on Docker remote Git build contexts.

