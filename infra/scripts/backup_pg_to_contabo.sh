#!/usr/bin/env bash
# Optional: nightly pg_dump to Contabo Object Storage (S3-compatible).
# Requires: aws-cli v2 (`aws`), .env with S3_* and WAREHOUSE_PG_* loaded (e.g. `set -a; source ../.env; set +a`).
set -euo pipefail

: "${WAREHOUSE_PG_HOST:?}"
: "${WAREHOUSE_PG_DATABASE:?}"
: "${S3_ENDPOINT_URL:?}"
: "${S3_BUCKET:?}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="pgdump_${WAREHOUSE_PG_DATABASE}_${STAMP}.sql.gz"

export PGPASSWORD="${WAREHOUSE_PG_PASSWORD}"
pg_dump -h "${WAREHOUSE_PG_HOST}" -p "${WAREHOUSE_PG_PORT:-5432}" -U "${WAREHOUSE_PG_USER}" \
  "${WAREHOUSE_PG_DATABASE}" | gzip >"/tmp/${FILE}"

aws s3 cp "/tmp/${FILE}" "s3://${S3_BUCKET}/backups/${FILE}" \
  --endpoint-url "${S3_ENDPOINT_URL}"

rm -f "/tmp/${FILE}"
echo "Uploaded s3://${S3_BUCKET}/backups/${FILE}"
