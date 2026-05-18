"""
Dagster + dbt: materialize models in infra/dbt/models (raw -> silver -> gold).

After adding models in dbt Workbench, rebuild: docker-compose build --no-cache user_code
"""

from __future__ import annotations

import os
from pathlib import Path

from dagster import (
    AssetExecutionContext,
    AssetSelection,
    Definitions,
    ScheduleDefinition,
    define_asset_job,
)
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

DBT_PROJECT_DIR = Path(__file__).resolve().parent.parent / "dbt"

dbt_project = DbtProject(
    project_dir=DBT_PROJECT_DIR,
    profiles_dir=DBT_PROJECT_DIR,
    target=os.environ.get("DBT_TARGET", "prod"),
)

dbt_resource = DbtCliResource(project_dir=dbt_project)


@dbt_assets(manifest=dbt_project.manifest_path)
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

defs = Definitions(
    assets=[mobile_analytics_dbt_assets],
    resources={"dbt": dbt_resource},
    jobs=[transform_raw_to_silver_gold],
    schedules=[nightly_etl_schedule],
)
