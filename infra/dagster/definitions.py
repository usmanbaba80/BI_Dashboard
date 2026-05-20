"""
Dagster orchestration: Airbyte ingestion (raw) -> dbt (silver/gold).

Set AIRBYTE_ENABLED=true and Airbyte API env vars after abctl install.
Rebuild user_code after changing dbt models: docker-compose build --no-cache user_code
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from dagster import (
    AssetExecutionContext,
    AssetKey,
    AssetSelection,
    Definitions,
    ScheduleDefinition,
    asset,
    define_asset_job,
)
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, DbtProject, dbt_assets

DBT_PROJECT_DIR = Path(__file__).resolve().parent.parent / "dbt"


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes")


def _manifest_has_models(manifest_path: Path) -> bool:
    if not manifest_path.is_file():
        return False
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    nodes = data.get("nodes") or {}
    return any(k.startswith("model.") for k in nodes)


def _airbyte_connection_filter():
    connection_id = os.environ.get("AIRBYTE_CONNECTION_ID", "").strip()
    connection_name = os.environ.get("AIRBYTE_CONNECTION_NAME", "").strip()
    if not connection_id and not connection_name:
        return None

    def _filter(meta) -> bool:
        if connection_id and getattr(meta, "connection_id", None) == connection_id:
            return True
        if connection_name and getattr(meta, "name", None) == connection_name:
            return True
        return False

    return _filter


def _build_airbyte_definitions() -> tuple[list, dict]:
    if not _env_bool("AIRBYTE_ENABLED"):
        return [], {}

    try:
        from dagster_airbyte import AirbyteResource, load_assets_from_airbyte_instance
    except ImportError:
        return [], {}

    host = os.environ.get("AIRBYTE_API_HOST", "host.docker.internal").strip()
    port = os.environ.get("AIRBYTE_API_PORT", "8000").strip()
    username = os.environ.get("AIRBYTE_USERNAME", "").strip()
    password = os.environ.get("AIRBYTE_PASSWORD", "").strip()

    resource_kwargs: dict[str, Any] = {"host": host, "port": port}
    if username:
        resource_kwargs["username"] = username
    if password:
        resource_kwargs["password"] = password

    airbyte_resource = AirbyteResource(**resource_kwargs)
    prefix = os.environ.get("AIRBYTE_ASSET_KEY_PREFIX", "airbyte").strip()
    key_prefix = [p for p in prefix.split("/") if p] if prefix else ["airbyte"]

    connection_filter = _airbyte_connection_filter()
    load_kwargs: dict[str, Any] = {
        "airbyte": airbyte_resource,
        "key_prefix": key_prefix,
    }
    if connection_filter is not None:
        load_kwargs["connection_filter"] = connection_filter

    airbyte_assets = load_assets_from_airbyte_instance(**load_kwargs)
    return [airbyte_assets], {"airbyte": airbyte_resource}


class RawSourceAirbyteTranslator(DagsterDbtTranslator):
    """Link dbt models on source('raw', ...) to upstream Airbyte Dagster assets."""

    def __init__(self, manifest_path: Path) -> None:
        super().__init__()
        self._source_asset_keys: dict[str, AssetKey] = {}
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                manifest = {}
            for unique_id, node in (manifest.get("sources") or {}).items():
                dagster_meta = (node.get("meta") or {}).get("dagster") or {}
                asset_key = dagster_meta.get("asset_key")
                if asset_key:
                    self._source_asset_keys[unique_id] = AssetKey(asset_key)

    def get_deps_asset_keys(
        self, dbt_resource_props: Mapping[str, Any]
    ) -> Iterable[AssetKey]:
        deps = set(super().get_deps_asset_keys(dbt_resource_props))

        if dbt_resource_props.get("resource_type") != "model":
            return deps

        prefix = os.environ.get("AIRBYTE_ASSET_KEY_PREFIX", "airbyte").strip()
        key_prefix = [p for p in prefix.split("/") if p] if prefix else ["airbyte"]
        connection_name = os.environ.get("AIRBYTE_CONNECTION_NAME", "").strip()

        for node_id in dbt_resource_props.get("depends_on", {}).get("nodes", []):
            if not node_id.startswith("source."):
                continue
            if node_id in self._source_asset_keys:
                deps.add(self._source_asset_keys[node_id])
                continue
            parts = node_id.split(".")
            if len(parts) < 4 or parts[-2] != "raw":
                continue
            table_name = parts[-1]
            if connection_name:
                deps.add(AssetKey([*key_prefix, connection_name, table_name]))
            else:
                deps.add(AssetKey([*key_prefix, table_name]))

        return deps


def _build_definitions() -> Definitions:
    if not (DBT_PROJECT_DIR / "dbt_project.yml").is_file():
        @asset
        def stack_healthcheck():
            return {
                "status": "ok",
                "dbt": f"missing dbt_project.yml at {DBT_PROJECT_DIR}",
            }

        return Definitions(assets=[stack_healthcheck])

    airbyte_asset_defs, airbyte_resources = _build_airbyte_definitions()

    dbt_project = DbtProject(project_dir=DBT_PROJECT_DIR)
    manifest_path = Path(dbt_project.manifest_path)
    has_models = _manifest_has_models(manifest_path)

    if not has_models and not airbyte_asset_defs:
        @asset
        def stack_healthcheck():
            return {
                "status": "ok",
                "dbt": "no models yet — add SQL under infra/dbt/models then rebuild user_code",
                "airbyte": "set AIRBYTE_ENABLED=true to orchestrate ingestion",
            }

        return Definitions(assets=[stack_healthcheck])

    assets: list = list(airbyte_asset_defs)
    resources: dict = dict(airbyte_resources)
    jobs: list = []
    schedules: list = []

    if has_models:
        dbt_resource = DbtCliResource(project_dir=dbt_project)
        resources["dbt"] = dbt_resource

        translator: Optional[DagsterDbtTranslator] = None
        if airbyte_asset_defs:
            translator = RawSourceAirbyteTranslator(manifest_path)

        @dbt_assets(
            manifest=manifest_path,
            dagster_dbt_translator=translator,
        )
        def mobile_analytics_dbt_assets(
            context: AssetExecutionContext, dbt: DbtCliResource
        ):
            yield from dbt.cli(["build"], context=context).stream()

        assets.append(mobile_analytics_dbt_assets)

        if airbyte_asset_defs:
            ingest_and_transform = define_asset_job(
                name="ingest_and_transform",
                selection=AssetSelection.all(),
                description="Run Airbyte sync(s) to raw, then dbt build for silver/gold",
            )
        else:
            ingest_and_transform = define_asset_job(
                name="transform_raw_to_silver_gold",
                selection=AssetSelection.assets(mobile_analytics_dbt_assets),
                description="dbt build only (Airbyte not wired — enable AIRBYTE_ENABLED)",
            )

        jobs.append(ingest_and_transform)
        schedules.append(
            ScheduleDefinition(
                job=ingest_and_transform,
                cron_schedule=os.environ.get("DAGSTER_ETL_CRON", "0 2 * * *"),
            )
        )
    elif airbyte_asset_defs:
        airbyte_only = define_asset_job(
            name="airbyte_ingest",
            selection=AssetSelection.all(),
        )
        jobs.append(airbyte_only)
        schedules.append(
            ScheduleDefinition(
                job=airbyte_only,
                cron_schedule=os.environ.get("DAGSTER_ETL_CRON", "0 2 * * *"),
            )
        )

    return Definitions(
        assets=assets,
        resources=resources,
        jobs=jobs,
        schedules=schedules,
    )


defs = _build_definitions()
