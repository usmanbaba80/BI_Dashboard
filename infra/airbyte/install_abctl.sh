#!/usr/bin/env bash
set -euo pipefail

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required."
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required on this host."
  exit 1
fi

: "${AIRBYTE_HOST:?Set AIRBYTE_HOST in abctl.env (IP or DNS for Airbyte UI)}"
AIRBYTE_PORT="${AIRBYTE_PORT:-8000}"
AIRBYTE_INSECURE_COOKIES="${AIRBYTE_INSECURE_COOKIES:-true}"

echo "Installing abctl..."
curl -LsfS https://get.airbyte.com | bash -

INSTALL_CMD=(abctl local install --host "${AIRBYTE_HOST}" --port "${AIRBYTE_PORT}")
if [[ "${AIRBYTE_INSECURE_COOKIES}" == "true" ]]; then
  INSTALL_CMD+=(--insecure-cookies)
fi

echo "Running: ${INSTALL_CMD[*]}"
"${INSTALL_CMD[@]}"

echo "Airbyte should now be available at http://${AIRBYTE_HOST}:${AIRBYTE_PORT}"
