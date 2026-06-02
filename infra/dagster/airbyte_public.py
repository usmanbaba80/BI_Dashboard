"""Airbyte Config API (public /api/public/v1) for abctl deployments.

Legacy dagster-airbyte uses /api/v1 + Basic auth; abctl returns 403 there.
Use client_id + client_secret from `abctl local credentials`.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)

TERMINAL_JOB_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "incomplete"}
)


def public_api_configured() -> bool:
    client_id = os.environ.get("AIRBYTE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AIRBYTE_CLIENT_SECRET", "").strip()
    connection_id = os.environ.get("AIRBYTE_CONNECTION_ID", "").strip()
    return bool(client_id and client_secret and connection_id)


def base_url() -> str:
    host = os.environ.get("AIRBYTE_API_HOST", "host.docker.internal").strip()
    port = os.environ.get("AIRBYTE_API_PORT", "8000").strip()
    return f"http://{host}:{port}/api/public/v1"


def _request(
    method: str,
    path: str,
    *,
    token: Optional[str] = None,
    body: Optional[dict[str, Any]] = None,
    timeout: int = 30,
) -> tuple[int, Any]:
    url = f"{base_url()}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            return response.status, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {"message": raw}
        except json.JSONDecodeError:
            payload = {"message": raw}
        return exc.code, payload


def fetch_access_token() -> str:
    client_id = os.environ.get("AIRBYTE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AIRBYTE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "AIRBYTE_CLIENT_ID and AIRBYTE_CLIENT_SECRET are required "
            "(from: abctl local credentials)"
        )

    status, payload = _request(
        "POST",
        "/applications/token",
        body={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant-type": "client_credentials",
        },
    )
    if status != 200:
        raise RuntimeError(f"Airbyte token request failed HTTP {status}: {payload}")

    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not token:
        raise RuntimeError(f"Airbyte token response missing access_token: {payload}")
    return str(token)


def api_ready() -> bool:
    if not public_api_configured():
        return False
    try:
        token = fetch_access_token()
        status, _ = _request("GET", "/workspaces", token=token)
        return status == 200
    except Exception as exc:
        logger.warning("Airbyte public API not ready: %s", exc)
        return False


def trigger_connection_sync(connection_id: str, token: str) -> str:
    status, payload = _request(
        "POST",
        "/jobs",
        token=token,
        body={"connectionId": connection_id, "jobType": "sync"},
    )
    if status != 200:
        raise RuntimeError(f"Airbyte sync trigger failed HTTP {status}: {payload}")

    job_id = None
    if isinstance(payload, dict):
        job_id = payload.get("jobId") or payload.get("job_id")
        if not job_id and isinstance(payload.get("job"), dict):
            job_id = payload["job"].get("id") or payload["job"].get("jobId")

    if not job_id:
        raise RuntimeError(f"Airbyte job response missing jobId: {payload}")
    return str(job_id)


def wait_for_job(
    job_id: str,
    token: str,
    *,
    poll_seconds: int = 15,
    timeout_seconds: int = 3600,
    log=None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status = ""

    while time.monotonic() < deadline:
        status_code, payload = _request("GET", f"/jobs/{job_id}", token=token)
        if status_code != 200:
            raise RuntimeError(f"Airbyte job status failed HTTP {status_code}: {payload}")

        job_status = ""
        if isinstance(payload, dict):
            job_status = str(
                payload.get("status")
                or (payload.get("job") or {}).get("status")
                or ""
            ).lower()

        if job_status and job_status != last_status:
            if log:
                log.info("Airbyte job %s status: %s", job_id, job_status)
            last_status = job_status

        if job_status in TERMINAL_JOB_STATUSES:
            if job_status == "succeeded":
                return payload if isinstance(payload, dict) else {"jobId": job_id}
            raise RuntimeError(
                f"Airbyte job {job_id} ended with status {job_status!r}: {payload}"
            )

        time.sleep(poll_seconds)

    raise RuntimeError(f"Airbyte job {job_id} timed out after {timeout_seconds}s")
