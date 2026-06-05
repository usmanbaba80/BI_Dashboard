# VPS deploy checklist (one pull)

Use this after `git pull` on the App VPS (`/opt/BI_Dashboard`).

**Day-to-day operations** (add sources, connections, dbt models, Dagster jobs): see **[OPERATIONS.md](./OPERATIONS.md)**.

## 1. Stop stack (required when `infra/dbt/` changed)

```bash
cd /opt/BI_Dashboard/infra
docker-compose down
```

## 2. Pull and prepare dbt project

```bash
cd /opt/BI_Dashboard
git pull

mkdir -p infra/dbt/artifacts
# Workbench file UI expects a git repo in the mounted project (once per clone)
test -d infra/dbt/.git || git -C infra/dbt init -q
```

## 3. Merge `.env` from `.env.example`

Update your real `infra/.env` (do not commit secrets):

| Variable | Must be |
|----------|---------|
| `WAREHOUSE_PG_HOST` | DB private IP (`10.0.4.2`) |
| `WAREHOUSE_PG_PASSWORD` | Real warehouse password |
| `DBTWB_VITE_API_BASE_URL` | `http://<APP_PUBLIC_IP>:8001` (not localhost) |
| `AIRBYTE_ENABLED` | `true` when using Dagster ingestion |
| `AIRBYTE_CONNECTION_ID` | UUID from Airbyte (preferred) |
| `AIRBYTE_CONNECTION_NAME` | Exact name from Airbyte UI (if no ID) |
| `AIRBYTE_API_HOST` | `host.docker.internal` or `172.17.0.1` |

## 4. Align dbt with warehouse

```bash
# On DB VPS or from App with psql
psql -h 10.0.4.2 -U datawarehouseuser -d datawarehouse -c "\dt raw.*"
```

Edit `infra/dbt/models/sources.yml` and `infra/dbt/models/silver/stg_custom_api.sql`:

- `source('raw', '<table>')` = actual raw table name
- `meta.dagster.asset_key` middle segment = **exact** Airbyte connection name (see Dagster Assets after step 6)

## 5. Vendor frontend `package.json` (if build says `COPY failed: no source files`)

The vendored Workbench `.gitignore` used to exclude `frontend/package.json`. After pull, verify:

```bash
ls -la /opt/BI_Dashboard/infra/vendor/dbt-Workbench/frontend/package.json
```

If missing, either `git pull` the latest repo (files are tracked now) or:

```bash
curl -fsSL -o /opt/BI_Dashboard/infra/vendor/dbt-Workbench/frontend/package.json \
  https://raw.githubusercontent.com/rezer-bleede/dbt-Workbench/main/frontend/package.json
curl -fsSL -o /opt/BI_Dashboard/infra/vendor/dbt-Workbench/frontend/package-lock.json \
  https://raw.githubusercontent.com/rezer-bleede/dbt-Workbench/main/frontend/package-lock.json
```

## 6. Build and start (full stack)

Use `docker-compose down` first (step 1). Avoid `docker-compose up --force-recreate` on this host (old compose v1.29 + Docker → `ContainerConfig` errors). Prefer `docker-compose down` then `docker-compose up -d`.

```bash
cd /opt/BI_Dashboard/infra

# Rebuild images that changed in this repo
docker-compose build --no-cache \
  user_code \
  dagster_webserver \
  dagster_daemon \
  dbt_workbench_backend \
  dbt_workbench_frontend

docker-compose up -d
docker-compose ps
```

Until Airbyte API credentials work, keep `AIRBYTE_ENABLED=false` in `.env` so `user_code` stays healthy.

## 7. dbt Workbench

**"Failed to load profiles"** — the UI calls the API URL baked in at **frontend build** time. In `.env` set `DBTWB_VITE_API_BASE_URL=http://<APP_PUBLIC_IP>:8001` (not `localhost`), then rebuild the frontend:

```bash
docker-compose build --no-cache dbt_workbench_frontend
docker-compose up -d --no-deps dbt_workbench_frontend
```

Test API from your laptop: `curl http://<APP_IP>:8001/profiles`

1. Open `http://<APP_IP>:3001`
2. **Environments** → profiles should show `mobile_analytics` / `prod` (not “No profiles configured yet”)
3. Delete or ignore default env (`test_project` / `postgres`)
4. Create **Production**: profile `mobile_analytics`, target `prod`
5. **Runs** → target `prod` → **Run**, then **Docs**
6. Dashboard → artifacts **Present**

## 8. Dagster pipeline

1. Open `http://<APP_IP>:3000`
2. **Assets** → Airbyte assets + dbt models; edge from Airbyte stream → silver model
3. **Jobs** → `ingest_and_transform` → Launch run
4. **Deployments** → **Schedules** → enable nightly schedule

## 9. Airbyte (separate)

- UI: `http://<APP_IP>:8000` (or SSH tunnel)
- Destination: host `10.0.4.2`, schema `raw`, password set
- Manual sync once before first dbt run

## If `user_code` is unhealthy (dagster_webserver / dagster_daemon won't start)

```bash
docker-compose logs --tail=80 user_code
```

Common causes:

1. **Airbyte 401 Unauthorized** — Dagster needs API credentials. On the App VPS run `abctl local credentials` and set `AIRBYTE_USERNAME` / `AIRBYTE_PASSWORD` in `.env`, then `docker-compose up -d --force-recreate user_code`. Or set `AIRBYTE_ENABLED=false` until configured.
2. **Airbyte unreachable** — ensure abctl is on port 8000 and `AIRBYTE_API_HOST` is correct (`host.docker.internal` or `172.17.0.1`).
2. **Python error in `definitions.py`** — fix from log, rebuild `user_code`.
3. **Missing dbt project** — ensure `infra/dbt/dbt_project.yml` exists on the VPS.

```bash
docker-compose up -d user_code
docker-compose ps
# when user_code is healthy:
docker-compose up -d dagster_webserver dagster_daemon
```

## Common misconfigurations (fixed in repo)

| Issue | Fix |
|-------|-----|
| Workbench “No profiles” | `DBT_PROFILES_PATH` → dbt project in compose |
| Workbench API from browser | `DBTWB_VITE_API_BASE_URL` = public IP |
| Dagster job can’t reach Airbyte | `AIRBYTE_*` in `dagster.yaml` run launcher + `extra_hosts` |
| dbt → Airbyte lineage broken | `sources.yml` `meta.dagster.asset_key` matches Dagster UI |
| Missing artifacts | `mkdir dbt/artifacts`, run dbt from Workbench |
| Stale Dagster dbt graph | Rebuild `user_code` after model changes |

## Rebuild only user_code (after editing models)

```bash
cd /opt/BI_Dashboard/infra
docker-compose build --no-cache user_code
docker-compose up -d user_code dagster_webserver dagster_daemon
```
