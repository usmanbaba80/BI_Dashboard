#!/usr/bin/env bash
# Apply 001_database_structure.sql using credentials from infra/.env
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
  set +a
fi

: "${WAREHOUSE_PG_HOST:?Set WAREHOUSE_PG_HOST in infra/.env}"
: "${WAREHOUSE_PG_USER:?Set WAREHOUSE_PG_USER}"
: "${WAREHOUSE_PG_PASSWORD:?Set WAREHOUSE_PG_PASSWORD}"
: "${WAREHOUSE_PG_DATABASE:?Set WAREHOUSE_PG_DATABASE}"

export PGPASSWORD="${WAREHOUSE_PG_PASSWORD}"

echo "Applying database structure to ${WAREHOUSE_PG_HOST}/${WAREHOUSE_PG_DATABASE} ..."
psql -h "${WAREHOUSE_PG_HOST}" -p "${WAREHOUSE_PG_PORT:-5432}" -U "${WAREHOUSE_PG_USER}" -d "${WAREHOUSE_PG_DATABASE}" \
  -f "${SCRIPT_DIR}/001_database_structure.sql"

echo "Done. Verify with:"
echo "  psql ... -c \"\\dn\" && psql ... -c \"\\dt raw.*\" && psql ... -c \"\\dt silver.*\" && psql ... -c \"\\dt gold.*\""
