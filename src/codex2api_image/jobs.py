from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import CommandError
from .http_client import Codex2APIClient
from .images import asset_bytes, decode_base64, save_image_set
from .paths import contained_output, ensure_distinct_outputs, output_targets


def response_job_id(response: dict[str, Any]) -> int:
    job = response_job(response)
    job_id = job.get("id")
    if type(job_id) is not int or job_id <= 0:
        raise CommandError("job response does not contain a positive numeric job.id", outcome_unknown=True)
    return job_id


def response_job(response: dict[str, Any]) -> dict[str, Any]:
    job = response.get("job")
    if not isinstance(job, dict):
        raise CommandError("job response does not contain job object", outcome_unknown=True)
    return job


def get_job(client: Codex2APIClient, job_id: int, timeout: float, include_cache: bool = False) -> dict[str, Any]:
    suffix = "?include_cache=1" if include_cache else ""
    return client.request_json("GET", f"/images/jobs/{job_id}{suffix}", timeout=timeout)


def wait_job(
    client: Codex2APIClient, job_id: int, timeout: float, poll_interval: float, *,
    auto_retry: bool = False, max_attempts: int = 3,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if type(job_id) is not int or job_id <= 0:
        raise CommandError("job ID must be a positive integer")
    if not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(poll_interval) or poll_interval <= 0:
        raise CommandError("job timeout and poll interval must be positive and finite")
    deadline = clock() + timeout
    last_status = "unknown"
    while clock() < deadline:
        try:
            response, _ = client.request_with_retry("GET", f"/images/jobs/{job_id}", timeout=min(30.0, deadline - clock()),
                                                    auto_retry=auto_retry, max_attempts=max_attempts, clock=clock, sleep=sleep)
            job = response_job(response)
        except CommandError as error:
            error.job_id = job_id
            raise
        status = str(job.get("status", "")).lower()
        last_status = status
        if status == "succeeded":
            return job
        if status in {"failed", "canceled", "cancelled"}:
            code = job.get("status_code")
            raw = job.get("upstream_body", "")
            body = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            raise CommandError(client.redact(str(job.get("error_message") or job.get("error") or status)),
                               route=f"/images/jobs/{job_id}", status_code=code if type(code) is int and code > 0 else None,
                               error_kind=str(job.get("error_kind") or status), body=client.redact(body), job_id=job_id)
        if status not in {"queued", "pending", "running"}:
            raise CommandError("unrecognized job status", job_id=job_id, details={"status": status})
        remaining = deadline - clock()
        if remaining > 0:
            sleep(min(poll_interval, remaining))
    raise CommandError("job wait budget exhausted; resume with job wait, do not submit again", error_kind="job_timeout",
                       job_id=job_id, details={"last_status": last_status})


def save_job_assets(client: Codex2APIClient, job: dict[str, Any], out: Path | None, out_dir: Path | None, timeout: float) -> list[Path]:
    if (out is None) == (out_dir is None):
        raise CommandError("choose exactly one of out or out_dir")
    assets = job.get("assets")
    if not isinstance(assets, list) or not assets:
        raise CommandError("job completed without assets", job_id=job.get("id"))
    if any(not isinstance(asset, dict) for asset in assets):
        raise CommandError("invalid job asset metadata", job_id=job.get("id"))
    if out is not None:
        targets = output_targets(out, len(assets))
    else:
        assert out_dir is not None
        targets = [contained_output(out_dir, str(asset.get("filename") or f"job-{job.get('id')}-{index:03d}.png"))
                   for index, asset in enumerate(assets, 1)]
        ensure_distinct_outputs(targets)
    items: list[tuple[bytes, Path]] = []
    for asset, target in zip(assets, targets):
        cached = asset.get("cache_b64_json")
        url = asset.get("proxy_url") or asset.get("url")
        if isinstance(cached, str) and cached:
            data = decode_base64(cached)
        elif isinstance(url, str) and url:
            data = asset_bytes(client, url, timeout)
        else:
            raise CommandError("job asset has no downloadable resource", job_id=job.get("id"))
        items.append((data, target))
    return save_image_set(items)
