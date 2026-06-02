"""
Dagster orchestration: Airbyte ingestion (raw) -> dbt (silver/gold).

abctl Airbyte: set AIRBYTE_CLIENT_ID/SECRET (abctl local credentials) + AIRBYTE_CONNECTION_ID.
Legacy /api/v1 + dagster-airbyte is only used when client credentials are not set.
Rebuild user_code after changes: docker-compose build --no-cache user_code
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from airbyte_public import api_ready as public_api_ready
from airbyte_public import fetch_access_token, public_api_configured
from airbyte_public import trigger_connection_sync, wait_for_job

logger = logging.getLogger(__name__)

from dagster import (
    AssetKey,
    AssetSelection,
    Definitions,
    Failure,
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


def _airbyte_api_ready() -> bool:
    """Probe Airbyte before registering cacheable assets (avoids crash on 401 at grpc load)."""
    host = os.environ.get("AIRBYTE_API_HOST", "host.docker.internal").strip()
    port = os.environ.get("AIRBYTE_API_PORT", "8000").strip()
    username = os.environ.get("AIRBYTE_USERNAME", "").strip()
    password = os.environ.get("AIRBYTE_PASSWORD", "").strip()
    url = f"http://{host}:{port}/api/v1/workspaces/list"
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if username and password:
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        logger.warning("Airbyte API HTTP %s for %s", exc.code, url)
        return False
    except Exception as exc:
        logger.warning("Airbyte API unreachable at %s: %s", url, exc)
        return False


def _slugify_dagster_name(value: str) -> str:
    """Dagster op/input names only allow [A-Za-z0-9_]."""
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "connection"


def _airbyte_connection_slug() -> str:
    override = os.environ.get("AIRBYTE_CONNECTION_SLUG", "").strip()
    if override:
        return _slugify_dagster_name(override)
    raw = os.environ.get("AIRBYTE_CONNECTION_NAME", "").strip()
    if not raw:
        raw = os.environ.get("AIRBYTE_CONNECTION_ID", "connection")[:8]
    return _slugify_dagster_name(raw)


def _airbyte_sync_asset_key() -> AssetKey:
    prefix = os.environ.get("AIRBYTE_ASSET_KEY_PREFIX", "airbyte").strip()
    key_prefix = [p for p in prefix.split("/") if p] if prefix else ["airbyte"]
    return AssetKey([*key_prefix, _airbyte_connection_slug(), "sync"])


def _build_airbyte_public_sync_asset() -> list:
    """Single Dagster asset: trigger Airbyte connection sync via /api/public/v1 (abctl)."""
    connection_id = os.environ.get("AIRBYTE_CONNECTION_ID", "").strip()
    sync_key = _airbyte_sync_asset_key()
    poll_seconds = int(os.environ.get("AIRBYTE_JOB_POLL_SECONDS", "15"))
    timeout_seconds = int(os.environ.get("AIRBYTE_JOB_TIMEOUT_SECONDS", "3600"))

    @asset(
        key=sync_key,
        name="airbyte_connection_sync",
        description=(
            f"Triggers Airbyte connection sync ({connection_id}) via public API, "
            "waits for job completion, loads raw.*"
        ),
    )
    def airbyte_connection_sync(context):
        token = fetch_access_token()
        context.log.info("Triggering Airbyte sync for connection %s", connection_id)
        job_id = trigger_connection_sync(connection_id, token)
        context.log.info("Airbyte job started: %s", job_id)
        result = wait_for_job(
            job_id,
            token,
            poll_seconds=poll_seconds,
            timeout_seconds=timeout_seconds,
            log=context.log,
        )
        return {"connection_id": connection_id, "job_id": job_id, "result": result}

    return [airbyte_connection_sync]


def _build_airbyte_legacy_definitions() -> tuple[list, dict]:
    if not _airbyte_api_ready():
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

    try:
        airbyte_assets = load_assets_from_airbyte_instance(**load_kwargs)
    except (Exception, Failure) as exc:
        logger.warning("Legacy Airbyte assets could not load (%s)", exc)
        return [], {}

    return [airbyte_assets], {"airbyte": airbyte_resource}


def _build_airbyte_definitions() -> tuple[list, dict]:
    if not _env_bool("AIRBYTE_ENABLED"):
        return [], {}

    if public_api_configured():
        if not public_api_ready():
            logger.warning(
                "AIRBYTE_ENABLED=true but public API is not ready. "
                "Set AIRBYTE_CLIENT_ID/SECRET + AIRBYTE_CONNECTION_ID from "
                "'abctl local credentials', or AIRBYTE_ENABLED=false."
            )
            return [], {}
        return _build_airbyte_public_sync_asset(), {}

    if not _airbyte_api_ready():
        logger.warning(
            "AIRBYTE_ENABLED=true but Airbyte API is not ready. "
            "For abctl set AIRBYTE_CLIENT_ID/SECRET (abctl local credentials), "
            "or legacy AIRBYTE_USERNAME/PASSWORD for /api/v1, or AIRBYTE_ENABLED=false."
        )
        return [], {}

    return _build_airbyte_legacy_definitions()


def _sanitize_asset_key(key: AssetKey) -> AssetKey:
    return AssetKey([_slugify_dagster_name(str(part)) for part in key.path])


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
                    self._source_asset_keys[unique_id] = AssetKey(
                        [_slugify_dagster_name(str(part)) for part in asset_key]
                    )

    def get_deps_asset_keys(
        self, dbt_resource_props: Mapping[str, Any]
    ) -> Iterable[AssetKey]:
        deps = {
            _sanitize_asset_key(key)
            for key in super().get_deps_asset_keys(dbt_resource_props)
        }

        if dbt_resource_props.get("resource_type") != "model":
            return deps

        raw_nodes = [
            node_id
            for node_id in dbt_resource_props.get("depends_on", {}).get("nodes", [])
            if node_id.startswith("source.")
            and len(node_id.split(".")) >= 4
            and node_id.split(".")[-2] == "raw"
        ]
        if not raw_nodes:
            return deps

        prefix = os.environ.get("AIRBYTE_ASSET_KEY_PREFIX", "airbyte").strip()
        key_prefix = [p for p in prefix.split("/") if p] if prefix else ["airbyte"]
        airbyte_root = _slugify_dagster_name(key_prefix[0])

        if public_api_configured():
            # Manifest meta may still reference human-readable Airbyte names (spaces/arrows).
            deps = {
                key
                for key in deps
                if not (
                    len(key.path) >= 1
                    and _slugify_dagster_name(str(key.path[0])) == airbyte_root
                )
            }
            deps.add(_airbyte_sync_asset_key())
            return deps

        for node_id in raw_nodes:
            if node_id in self._source_asset_keys:
                deps.add(self._source_asset_keys[node_id])
                continue
            table_name = node_id.split(".")[-1]
            deps.add(AssetKey([*key_prefix, _airbyte_connection_slug(), table_name]))

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

        dbt_assets_kwargs: dict[str, Any] = {"manifest": manifest_path}
        if translator is not None:
            dbt_assets_kwargs["dagster_dbt_translator"] = translator

        # No type hint on `context` — nested defs break Dagster's context annotation check
        @dbt_assets(**dbt_assets_kwargs)
        def mobile_analytics_dbt_assets(context, dbt: DbtCliResource):
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
