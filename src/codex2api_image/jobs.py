from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .errors import CommandError
from .http_client import Codex2APIClient
from .images import save_asset_url


def submit_job(client: Codex2APIClient, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    return client.request_json("POST", "/images/jobs", payload, timeout=timeout)


def get_job(client: Codex2APIClient, job_id: int, timeout: int, include_cache: bool = True) -> dict[str, Any]:
    suffix = "?include_cache=1" if include_cache else ""
    return client.request_json("GET", f"/images/jobs/{job_id}{suffix}", timeout=timeout)


def response_job_id(response: dict[str, Any]) -> int:
    job = response.get("job")
    if not isinstance(job, dict):
        raise CommandError("job response does not contain job object")
    raw_id = job.get("id")
    if not isinstance(raw_id, int):
        raise CommandError("job response does not contain numeric job.id")
    return raw_id


def response_job(response: dict[str, Any]) -> dict[str, Any]:
    job = response.get("job")
    if not isinstance(job, dict):
        raise CommandError("job response does not contain job object")
    return job


def wait_job(client: Codex2APIClient, job_id: int, timeout: int, poll_interval: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_job: dict[str, Any] | None = None
    while time.monotonic() <= deadline:
        response = get_job(client, job_id, timeout=min(30, timeout))
        job = response_job(response)
        last_job = job
        status = str(job.get("status", "")).lower()
        if status in {"succeeded", "failed", "canceled", "cancelled"}:
            if status != "succeeded":
                error = job.get("error") or job.get("error_message") or job.get("message") or status
                raise CommandError(f"image job {job_id} failed: {error}")
            return job
        time.sleep(max(0.2, poll_interval))
    status = "" if last_job is None else str(last_job.get("status", ""))
    raise CommandError(f"image job {job_id} timed out; last status={status!r}")


def save_job_assets(client: Codex2APIClient, job: dict[str, Any], out: Path | None, out_dir: Path | None, timeout: int) -> list[Path]:
    assets = job.get("assets")
    if not isinstance(assets, list) or not assets:
        raise CommandError("job completed without assets")
    saved: list[Path] = []
    for index, asset in enumerate(assets, start=1):
        if not isinstance(asset, dict):
            continue
        url = asset.get("proxy_url") or asset.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        if out is not None and len(assets) == 1:
            target = out
        else:
            base = out_dir or Path("output/imagegen/jobs")
            filename = asset.get("filename")
            if not isinstance(filename, str) or not filename.strip():
                filename = f"job-{job.get('id', 'image')}-{index}.png"
            target = base / filename
        saved.append(save_asset_url(client, url, target, timeout))
    if not saved:
        raise CommandError("job assets did not include downloadable proxy_url values")
    return saved
