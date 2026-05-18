#!/usr/bin/env bash
# dbt-Workbench expects /workspace/repos/default to be a git repo (bind-mounted to infra/dbt).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DBT_DIR="${SCRIPT_DIR}/../dbt"

cd "${DBT_DIR}"

if [[ -d .git ]]; then
  echo "Git repo already exists in ${DBT_DIR}"
  exit 0
fi

git init -b main
git add dbt_project.yml profiles.yml models artifacts workbench-repos 2>/dev/null || git add .
git -c user.email="dbt@localhost" -c user.name="dbt" commit -m "Initial dbt project" || true

echo "Initialized git repo in ${DBT_DIR}"
