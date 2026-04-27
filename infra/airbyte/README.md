## Airbyte Setup (abctl) - In This Repo

Airbyte is kept in this repo as an operational setup (`abctl`) instead of raw Docker Compose.
This follows Airbyte's current OSS guidance.

### Why this approach

- Airbyte OSS moved away from the old monolithic Compose deployment path.
- `abctl` is the supported install/upgrade path and is more stable long-term.
- It still gives you Airbyte UI on port `8000` as requested in the stack guide.

### Files

- `install_abctl.sh`: installs Airbyte on the App VPS host
- `abctl.env.example`: sample environment variables for installation

### Install on App VPS

```bash
cd infra/airbyte
cp abctl.env.example abctl.env
# edit abctl.env as needed
source ./abctl.env
bash ./install_abctl.sh
```

### Post-install

Open `http://<APP_HOST>:8000` and create a Postgres destination using:

- host: private DB server IP (`WAREHOUSE_PG_HOST`)
- port: `5432`
- database/user/password from your `.env`

