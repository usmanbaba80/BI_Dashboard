"""
Dagster + dbt: materialize models in infra/dbt/models (raw -> silver -> gold).

When no dbt models exist yet, only stack_healthcheck is loaded so user_code stays healthy.
After adding models in Workbench: docker-compose build --no-cache user_code
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dagster import (
    AssetExecutionContext,
    AssetSelection,
    Definitions,
    ScheduleDefinition,
    asset,
    define_asset_job,
)
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

DBT_PROJECT_DIR = Path(__file__).resolve().parent.parent / "dbt"


def _manifest_has_models(manifest_path: Path) -> bool:
    if not manifest_path.is_file():
        return False
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    nodes = data.get("nodes") or {}
    return any(k.startswith("model.") for k in nodes)


def _build_definitions() -> Definitions:
    if not (DBT_PROJECT_DIR / "dbt_project.yml").is_file():
        @asset
        def stack_healthcheck():
            return {
                "status": "ok",
                "dbt": f"missing dbt_project.yml at {DBT_PROJECT_DIR}",
            }

        return Definitions(assets=[stack_healthcheck])

    # dagster-dbt 0.25.x: project_dir only (profiles.yml lives in the dbt project root)
    dbt_project = DbtProject(project_dir=DBT_PROJECT_DIR)
    manifest_path = Path(dbt_project.manifest_path)

    if not _manifest_has_models(manifest_path):
        @asset
        def stack_healthcheck():
            return {
                "status": "ok",
                "dbt": "no models yet — add SQL under infra/dbt/models then rebuild user_code",
            }

        return Definitions(assets=[stack_healthcheck])

    dbt_resource = DbtCliResource(project_dir=dbt_project)

    @dbt_assets(manifest=manifest_path)
    def mobile_analytics_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
        yield from dbt.cli(["build"], context=context).stream()

    transform_raw_to_silver_gold = define_asset_job(
        name="transform_raw_to_silver_gold",
        selection=AssetSelection.assets(mobile_analytics_dbt_assets),
    )

    nightly_etl_schedule = ScheduleDefinition(
        job=transform_raw_to_silver_gold,
        cron_schedule=os.environ.get("DAGSTER_ETL_CRON", "0 2 * * *"),
    )

    return Definitions(
        assets=[mobile_analytics_dbt_assets],
        resources={"dbt": dbt_resource},
        jobs=[transform_raw_to_silver_gold],
        schedules=[nightly_etl_schedule],
    )


defs = _build_definitions()
