# BI Dashboard — Operations Guide

End-to-end documentation for **ingestion (Airbyte)**, **transformation (dbt)**, and **orchestration (Dagster)** on the Contabo VPS setup.

| Doc | Purpose |
|-----|---------|
| **This file** | Day-to-day: sources, connections, models, pipelines |
| [DEPLOY.md](./DEPLOY.md) | VPS deploy / rebuild checklist after `git pull` |
| [README.md](./README.md) | Stack overview and ports |
| [airbyte/README.md](./airbyte/README.md) | Installing Airbyte with `abctl` |

---

## 1. Architecture overview

### 1.1 What runs where

| Server | Role | Main services |
|--------|------|----------------|
| **App VPS** (`79.143.178.106` / private `10.0.4.1`) | Orchestration + UIs | Docker: Dagster, dbt Workbench; Host: Airbyte (`abctl`, port 8000) |
| **DB VPS** (private `10.0.4.2`) | Data warehouse | PostgreSQL `datawarehouse` — schemas `raw`, `silver`, `gold` |

### 1.2 Data layers

```text
External source (API, DB, S3 files, …)
        │
        ▼  Airbyte sync
   Postgres  raw.*          ← bronze (landing zone)
        │
        ▼  dbt build
   Postgres  silver.*       ← cleaned / staged models (stg_*)
        │
        ▼  dbt build
   Postgres  gold.*         ← analytics / marts (optional)
```

| Layer | Created by | Schema | Example table |
|-------|------------|--------|----------------|
| Bronze | Airbyte | `raw` | `raw.test` |
| Silver | dbt | `silver` | `silver.stg_custom_api` |
| Gold | dbt | `gold` | `gold.daily_summary` |

### 1.3 Docker containers (App VPS)

All compose services use network **`infra_analytics`**.

| Container | Port (published) | Purpose |
|-----------|------------------|---------|
| `dagster_webserver` | 3000 | Dagster UI |
| `dagster_daemon` | — | Schedules, run queue, spawns job containers |
| `user_code` | 4000 (internal) | Pipeline definitions (gRPC) + dbt project |
| `dagster_postgres` | — | Dagster metadata (runs, schedules) — **not** business data |
| `dbt_workbench_frontend` | 3001 | dbt Workbench UI |
| `dbt_workbench_backend` | 8001 | Workbench API + dbt runs |
| `dbt_workbench_postgres` | — | Workbench metadata |
| **Ephemeral run containers** | — | Created per Dagster job (same image as `user_code`) |

**Airbyte** runs on the **host** via `abctl` (not in `docker-compose`). Dagster reaches it at `host.docker.internal:8000`.

### 1.4 Pipeline modes (Dagster)

| Mode | `AIRBYTE_ENABLED` | Dagster job | What runs |
|------|-------------------|-------------|-----------|
| **A — Transform only** | `false` | `transform_raw_to_silver_gold` | dbt only; you sync Airbyte manually or on its schedule |
| **B — Full pipeline** | `true` + API creds | `ingest_and_transform` | Airbyte sync → dbt build |

Mode B uses Airbyte **public API** (`/api/public/v1`) with `AIRBYTE_CLIENT_ID` / `AIRBYTE_CLIENT_SECRET` from `abctl local credentials`.

---

## 2. One-time setup

### 2.1 Warehouse schemas (DB VPS)

```bash
psql -h 10.0.4.2 -U postgres -d datawarehouse -f sql/001_database_structure.sql
```

Creates `raw`, `silver`, `gold` and grants for `datawarehouseuser`.

### 2.2 Environment file

```bash
cd /opt/BI_Dashboard/infra
cp .env.example .env
nano .env
```

Minimum warehouse settings:

```env
WAREHOUSE_PG_HOST=10.0.4.2
WAREHOUSE_PG_PORT=5432
WAREHOUSE_PG_USER=datawarehouseuser
WAREHOUSE_PG_PASSWORD=<secret>
WAREHOUSE_PG_DATABASE=datawarehouse
DBT_SCHEMA=public
```

### 2.3 Start Docker stack

See [DEPLOY.md](./DEPLOY.md). On this VPS always prefer:

```bash
docker stop <container> && docker rm -f <container>
docker-compose up -d --no-deps <service>
```

Avoid `docker-compose up --force-recreate` (compose 1.29 `ContainerConfig` bug).

### 2.4 Airbyte install

```bash
cd infra/airbyte
# see abctl.env.example, then:
bash install_abctl.sh
abctl local credentials   # save client-id, client-secret, email, password
```

UI: `http://<APP_PUBLIC_IP>:8000`

### 2.5 dbt Workbench environment

1. Open `http://<APP_PUBLIC_IP>:3001`
2. **Environments** → Profile **`mobile_analytics`**, Target **`prod`**
3. Ensure `DBTWB_VITE_API_BASE_URL` in `.env` is the **public** API URL (`http://<APP_IP>:8001`), then rebuild frontend if needed.

---

## 3. Airbyte — sources, destinations, connections

Airbyte has three concepts:

```text
Source  = where data comes FROM
Destination = where data lands TO  (always Postgres warehouse, schema raw)
Connection = source + destination + selected streams + schedule
```

### 3.1 Create destination (once)

**Destinations** → **+ New destination** → **Postgres**

| Field | Value |
|-------|--------|
| Host | `10.0.4.2` (DB VPS private IP) |
| Port | `5432` |
| Database | `datawarehouse` |
| User | `datawarehouseuser` |
| Password | from `.env` |
| **Default schema** | **`raw`** |

Test connection → Save.

> The default schema **must** be `raw`. dbt expects bronze tables under `raw.*`.

### 3.2 Create source

**Sources** → **+ New source** → pick connector:

| Source type | When to use |
|-------------|-------------|
| HTTP / Custom API | REST APIs (e.g. BlinkAI) |
| **S3** | Contabo Object Storage, AWS, MinIO — set **Endpoint** `https://eu2.contabostorage.com` |
| Postgres / MySQL / etc. | Database replication |

Configure credentials → **Test** → **Save**.

#### Contabo S3 source (not a separate “Contabo” connector)

| Field | Value |
|-------|--------|
| Bucket | `biassets` |
| Access Key / Secret | Contabo S3 credentials (Account → Security & Access) |
| **Endpoint** | `https://eu2.contabostorage.com` |
| Region | `us-east-1` or `eu-central-1` (not `eu2`) |
| Stream format | CSV / JSONL / Parquet |
| Globs | e.g. `landing/**/*.csv` |

### 3.3 Create connection

**Connections** → **+ New connection**

1. Select **source** and **destination** (Postgres above)
2. Choose **streams** (tables/files) to sync
3. Set **sync mode** (full refresh or incremental)
4. Name the connection clearly, e.g. `MyAPI_to_warehouse`  
   - Prefer letters, numbers, underscores (Dagster slugifies names for asset keys)
5. **Schedule** (optional if Dagster Mode B will trigger sync)
6. **Save**

### 3.4 First sync and verify bronze

**Sync now** → wait for success.

```bash
psql -h 10.0.4.2 -U datawarehouseuser -d datawarehouse -c "\dt raw.*"
psql -h 10.0.4.2 -U datawarehouseuser -d datawarehouse -c "SELECT count(*) FROM raw.<table>;"
```

Record:

- **Connection ID** (UUID from connection settings)
- **Connection name** (UI title)
- **Raw table name(s)** (from `\dt raw.*`)

### 3.5 Changing or replacing a connection

1. Delete old **connection** in Airbyte (sources/destinations can stay)
2. Create new connection (section 3.3)
3. Run first sync → update dbt `sources.yml` and `.env` `AIRBYTE_CONNECTION_ID`
4. Rebuild/restart `user_code` and Dagster (section 6)

---

## 4. dbt — transformation models

Project path: `infra/dbt/` (mounted into `user_code` and Workbench).

### 4.1 Project layout

```text
infra/dbt/
  dbt_project.yml      # silver → schema silver, gold → schema gold
  profiles.yml         # warehouse connection via WAREHOUSE_PG_* env vars
  models/
    sources.yml        # declare raw tables (bronze)
    silver/            # staging models stg_*
    gold/              # marts (optional)
  macros/
    generate_schema_name.sql
```

### 4.2 Declare raw tables — `sources.yml`

After Airbyte sync, list tables and add each to `sources.yml`:

```yaml
version: 2

sources:
  - name: raw
    description: Bronze tables created by Airbyte
    schema: raw
    tables:
      - name: test                    # exact name from \dt raw.*
        description: Stream from MyAPI connection
        meta:
          dagster:
            asset_key:
              - airbyte
              - MyAPI_to_warehouse    # slug: A-Za-z0-9_ only
              - sync                  # always "sync" for Mode B public API
```

**Dagster asset key rules (Mode B):**

- Segment 1: `airbyte` (or `AIRBYTE_ASSET_KEY_PREFIX`)
- Segment 2: slug of connection name (`MyAPI_to_warehouse`, not `My API → warehouse`)
- Segment 3: `sync`

Optional `.env` override: `AIRBYTE_CONNECTION_SLUG=MyAPI_to_warehouse`

### 4.3 Silver staging model

Create `models/silver/stg_<name>.sql`:

```sql
{{ config(materialized='table', tags=['silver']) }}

select
    col_a,
    col_b
from {{ source('raw', 'test') }}
```

- Folder `silver/` → Postgres schema **`silver`** (via `dbt_project.yml` + `generate_schema_name` macro)
- Model file `stg_custom_api.sql` → table **`silver.stg_custom_api`**

### 4.4 Gold model (optional)

Create `models/gold/<mart>.sql`:

```sql
{{ config(materialized='table', tags=['gold']) }}

select
    current_date as report_date,
    count(*) as row_count
from {{ ref('stg_custom_api') }}
```

### 4.5 Run dbt

**Option A — Workbench UI**

1. `http://<APP_IP>:3001` → **Runs**
2. Command: `build` (or `run`)
3. Target: `prod`
4. **Run** → then **Docs** for lineage

**Option B — CLI in container**

```bash
docker exec infra_user_code_1 bash -c \
  "cd /opt/dagster/dbt && dbt build --profiles-dir . --target prod"
```

### 4.6 Verify silver / gold

```bash
psql -h 10.0.4.2 -U datawarehouseuser -d datawarehouse -c "\dt silver.*"
psql -h 10.0.4.2 -U datawarehouseuser -d datawarehouse -c "\dt gold.*"
```

### 4.7 After editing models

```bash
cd /opt/BI_Dashboard/infra
docker-compose build --no-cache user_code
docker stop infra_user_code_1 && docker rm -f infra_user_code_1
docker-compose up -d --no-deps user_code
# when healthy:
docker stop infra_dagster_webserver_1 infra_dagster_daemon_1
docker rm -f infra_dagster_webserver_1 infra_dagster_daemon_1
docker-compose up -d dagster_webserver dagster_daemon
```

---

## 5. Dagster — pipelines and schedules

### 5.1 Configure Mode B (full pipeline)

Edit `infra/.env`:

```env
AIRBYTE_ENABLED=true
AIRBYTE_API_HOST=host.docker.internal
AIRBYTE_API_PORT=8000

AIRBYTE_CLIENT_ID=<from abctl local credentials>
AIRBYTE_CLIENT_SECRET=<from abctl local credentials>
AIRBYTE_CONNECTION_ID=<UUID from Airbyte connection>
AIRBYTE_CONNECTION_NAME=MyAPI_to_warehouse
AIRBYTE_ASSET_KEY_PREFIX=airbyte

DAGSTER_ETL_CRON=30 2 * * *
DBT_SCHEMA=public
```

**Critical:** Before `docker-compose up`, run:

```bash
unset AIRBYTE_ENABLED
```

Shell `export AIRBYTE_ENABLED=false` overrides `.env` and breaks Mode B.

Verify:

```bash
docker-compose config | grep 'AIRBYTE_ENABLED:' | head -1
# must show true
```

### 5.2 Restart Dagster stack

```bash
cd /opt/BI_Dashboard/infra
unset AIRBYTE_ENABLED

docker-compose build --no-cache user_code
docker stop infra_user_code_1 && docker rm -f infra_user_code_1
docker-compose up -d --no-deps user_code

# wait until healthy
docker inspect infra_user_code_1 --format='{{.State.Health.Status}}'

docker stop infra_dagster_webserver_1 infra_dagster_daemon_1
docker rm -f infra_dagster_webserver_1 infra_dagster_daemon_1
docker-compose up -d dagster_webserver dagster_daemon
```

### 5.3 What you should see in Dagster UI

Open `http://<APP_IP>:3000`

**Assets**

| Asset | Type |
|-------|------|
| `airbyte / <slug> / sync` | Triggers Airbyte connection sync |
| `silver / stg_*` | dbt models |

Lineage: **sync** → **stg_*** (when `sources.yml` `asset_key` matches).

**Jobs**

| Job | When |
|-----|------|
| `ingest_and_transform` | Mode B — sync then dbt |
| `transform_raw_to_silver_gold` | Mode A — dbt only |

### 5.4 Run pipeline manually

**Jobs** → **`ingest_and_transform`** → **Launch run**

Execution order:

1. Airbyte sync (public API `POST /jobs`, wait for completion)
2. `dbt build` (silver + gold + tests)

### 5.5 Enable schedule

**Automation** / **Deployments** → **Schedules** → enable schedule (cron from `DAGSTER_ETL_CRON`).

Typical pattern:

- Airbyte connection schedule: `02:00` (optional if Dagster triggers sync)
- Dagster: `30 2 * * *`

### 5.6 Mode A (Airbyte + Dagster dbt only)

Use when debugging Airbyte API or running multiple connections on Airbyte schedules:

```env
AIRBYTE_ENABLED=false
```

- Airbyte: per-connection schedules → `raw.*`
- Dagster: `transform_raw_to_silver_gold` on cron

---

## 6. End-to-end checklist — new data source

Use this every time you add a new source or recreate a connection.

```text
□ 1. Airbyte: create source (section 3.2)
□ 2. Airbyte: use existing Postgres destination, schema raw (section 3.1)
□ 3. Airbyte: create connection, enable streams (section 3.3)
□ 4. Airbyte: Sync now → succeeded
□ 5. psql: \dt raw.* — note table name(s)
□ 6. dbt: add table to models/sources.yml + meta.dagster.asset_key
□ 7. dbt: add models/silver/stg_*.sql (and gold if needed)
□ 8. Workbench: dbt build on prod — verify silver.*
□ 9. .env: AIRBYTE_CONNECTION_ID, AIRBYTE_CONNECTION_NAME (slug-friendly)
□ 10. unset AIRBYTE_ENABLED; verify docker-compose config shows true
□ 11. Rebuild/restart user_code; wait healthy
□ 12. Restart dagster_webserver + dagster_daemon
□ 13. Dagster UI: ingest_and_transform visible; launch test run
□ 14. Enable schedule (optional)
```

---

## 7. Multiple connections

Current Dagster setup orchestrates **one** connection per deployment (`AIRBYTE_CONNECTION_ID`).

| Strategy | How |
|----------|-----|
| **Recommended** | Airbyte schedules for connections 2+; Dagster Mode B for primary connection + dbt builds **all** models |
| **Mode A** | All connections on Airbyte schedules; Dagster `transform_raw_to_silver_gold` only |
| **Future** | Multiple sync assets (code change) |

Each raw table still needs its own `sources.yml` entry and `stg_*.sql` regardless of who triggers the sync.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `transform_raw_to_silver_gold` only | `AIRBYTE_ENABLED=false` in container | `unset AIRBYTE_ENABLED`, recreate `user_code` |
| `docker-compose config` shows false | Shell export overrides `.env` | `unset AIRBYTE_ENABLED` |
| `user_code` unhealthy | Python error in definitions | `docker-compose logs user_code` |
| `DagsterInvalidDefinitionError` + `→` | Invalid chars in asset key | Use slug in `sources.yml`; pull latest `definitions.py` |
| `ContainerConfig` on up | compose 1.29 recreate bug | `docker stop` + `docker rm` then `up` |
| `DBT_SCHEMA not set` on job | Daemon missing env | Recreate `dagster_daemon` with updated compose |
| dbt relation `raw.x` does not exist | No sync yet or wrong table name | Airbyte sync; fix `sources.yml` |
| `silver_silver.*` tables | Old `DBT_SCHEMA=silver` | Set `DBT_SCHEMA=public` |
| Airbyte S3 test hits AWS | Missing Endpoint | Endpoint `https://eu2.contabostorage.com` |
| Dagster can’t reach Airbyte | Network | `AIRBYTE_API_HOST=host.docker.internal`, Airbyte on :8000 |
| Workbench no profiles | Wrong API URL | Rebuild frontend with public `DBTWB_VITE_API_BASE_URL` |

### Useful commands

```bash
# Container health
docker-compose ps
docker-compose logs --tail=50 user_code

# Env inside user_code
docker exec infra_user_code_1 printenv | grep AIRBYTE

# Warehouse tables
psql -h 10.0.4.2 -U datawarehouseuser -d datawarehouse -c "\dt raw.*"
psql -h 10.0.4.2 -U datawarehouseuser -d datawarehouse -c "\dt silver.*"

# Airbyte API test (host)
abctl local credentials
curl -s -H "Content-Type: application/json" \
  -d '{"client_id":"...","client_secret":"...","grant-type":"client_credentials"}' \
  "http://127.0.0.1:8000/api/public/v1/applications/token"
```

---

## 9. File reference

| Path | Purpose |
|------|---------|
| `infra/.env` | Secrets and feature flags (not committed) |
| `infra/docker-compose.yml` | Container definitions |
| `infra/dagster/definitions.py` | Dagster assets, jobs, schedules |
| `infra/dagster/airbyte_public.py` | Airbyte public API client |
| `infra/dagster/dagster.yaml` | Run launcher, Postgres storage |
| `infra/dagster/workspace.yaml` | Points webserver at `user_code:4000` |
| `infra/dbt/models/sources.yml` | Raw sources + Dagster lineage keys |
| `infra/dbt/models/silver/` | Staging SQL models |
| `infra/dbt/models/gold/` | Mart SQL models |
| `infra/sql/001_database_structure.sql` | Warehouse schema bootstrap |

---

## 10. URLs quick reference

| URL | Service |
|-----|---------|
| `http://<APP_IP>:8000` | Airbyte |
| `http://<APP_IP>:3000` | Dagster |
| `http://<APP_IP>:3001` | dbt Workbench |
| `http://<APP_IP>:8001` | Workbench API |
| `10.0.4.2:5432` | Warehouse Postgres (private) |

---

*For deploy steps after code changes, see [DEPLOY.md](./DEPLOY.md).*
