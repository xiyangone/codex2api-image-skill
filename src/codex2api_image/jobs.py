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

DEFAULT_QUEUE_TIMEOUT = 900.0
DEFAULT_EXECUTION_TIMEOUT_PER_OUTPUT = 900.0
DEFAULT_DOWNLOAD_TIMEOUT = 900.0
ACTIVE_JOB_STATUSES = frozenset({"queued", "pending", "running"})
FAILED_JOB_STATUSES = frozenset({"failed", "canceled", "cancelled"})
TERMINAL_JOB_STATUSES = FAILED_JOB_STATUSES | {"succeeded"}


def validate_job_id(job_id: Any) -> int:
    if type(job_id) is not int or job_id <= 0:
        raise CommandError("job ID must be a positive integer")
    return job_id


def validate_timeout(value: float, name: str) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise CommandError(f"{name} must be positive and finite")


def response_job_id(response: dict[str, Any]) -> int:
    try:
        return validate_job_id(response_job(response).get("id"))
    except CommandError as error:
        error.outcome_unknown = True
        raise


def response_job(response: dict[str, Any]) -> dict[str, Any]:
    job = response.get("job")
    if not isinstance(job, dict):
        raise CommandError("job response does not contain job object", error_kind="invalid_response")
    return job


def job_params(job: dict[str, Any]) -> dict[str, Any]:
    raw = job.get("params_json")
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            params = json.loads(raw)
        except json.JSONDecodeError:
            params = None
        if isinstance(params, dict):
            return params
    raise CommandError("job parameters are not a JSON object", error_kind="invalid_response", job_id=job.get("id"))


def job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    """Whitelist progress fields, excluding prompts, key metadata and image data."""
    job_id = validate_job_id(job.get("id"))
    status = str(job.get("status", "")).strip().lower()
    if status not in ACTIVE_JOB_STATUSES | TERMINAL_JOB_STATUSES:
        raise CommandError("unrecognized job status", error_kind="invalid_response", job_id=job_id)
    assets = job.get("assets")
    if assets is None:
        assets = []
    if not isinstance(assets, list) or any(not isinstance(asset, dict) for asset in assets):
        raise CommandError("invalid job asset metadata", error_kind="invalid_response", job_id=job_id)
    requested = job["requested_outputs"] if "requested_outputs" in job else job_params(job).get("n", 1)
    completed = job.get("completed_outputs", len(assets))
    if type(requested) is not int or not 1 <= requested <= 4 or type(completed) is not int or completed < 0:
        raise CommandError("invalid job progress counters", error_kind="invalid_response", job_id=job_id)
    return {"job_id": job_id, "status": status, "requested_outputs": requested, "completed_outputs": completed,
            "asset_count": len(assets), "terminal": status in TERMINAL_JOB_STATUSES}


def job_failure(client: Codex2APIClient, job: dict[str, Any]) -> CommandError:
    snapshot = job_snapshot(job)
    code = job.get("status_code")
    raw = job.get("upstream_body", "")
    body = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    details = dict(snapshot)
    if snapshot["asset_count"]:
        details["next_command"] = f"job download {snapshot['job_id']}"
    return CommandError(client.redact(str(job.get("error_message") or job.get("error") or snapshot["status"])),
                        route=f"/images/jobs/{snapshot['job_id']}",
                        status_code=code if type(code) is int and code > 0 else None,
                        error_kind=client.redact(str(job.get("error_kind") or snapshot["status"])),
                        body=client.redact(body), job_id=snapshot["job_id"], details=details)


def get_job(
    client: Codex2APIClient, job_id: int, timeout: float, include_cache: bool = False, *,
    auto_retry: bool = False, max_attempts: int = 3,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    validate_job_id(job_id)
    suffix = "?include_cache=1" if include_cache else ""
    route = f"/images/jobs/{job_id}{suffix}"
    try:
        response, _ = client.request_with_retry("GET", route, timeout=timeout, auto_retry=auto_retry,
                                                max_attempts=max_attempts, clock=clock, sleep=sleep)
        job = response_job(response)
        if type(job.get("id")) is not int or job["id"] != job_id:
            raise CommandError("response job ID does not match requested ID", error_kind="invalid_response")
        job_snapshot(job)
        return job
    except CommandError as error:
        error.job_id = job_id
        error.route = error.route or route
        raise


def wait_job(
    client: Codex2APIClient, job_id: int, timeout: float, poll_interval: float, *,
    queue_timeout: float = DEFAULT_QUEUE_TIMEOUT, execution_timeout: float | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    auto_retry: bool = False, max_attempts: int = 3,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    validate_job_id(job_id)
    for value, name in ((timeout, "request timeout"), (queue_timeout, "queue timeout"), (poll_interval, "poll interval")):
        validate_timeout(value, name)
    if execution_timeout is not None:
        validate_timeout(execution_timeout, "execution timeout")
    queue_deadline = clock() + queue_timeout
    execution_deadline: float | None = None
    last: dict[str, Any] = {"job_id": job_id, "status": "unknown"}
    last_progress: tuple[str, int, int] | None = None
    while True:
        phase = "execution" if execution_deadline is not None else "queue"
        deadline = execution_deadline if execution_deadline is not None else queue_deadline
        remaining = deadline - clock()
        if remaining <= 0:
            raise CommandError(f"job {phase} wait budget exhausted; resume with job wait, do not submit again",
                               error_kind="job_timeout", job_id=job_id,
                               details={**last, "phase": phase, "last_status": last["status"],
                                        "next_command": f"job wait {job_id}", "server_task_cancelled": False})
        try:
            job = get_job(client, job_id, min(30.0, timeout, remaining), auto_retry=auto_retry,
                          max_attempts=max_attempts, clock=clock, sleep=sleep)
        except CommandError as error:
            error.details.setdefault("phase", phase)
            error.details.setdefault("last_status", last["status"])
            error.details.setdefault("next_command", f"job wait {job_id}")
            raise
        last = job_snapshot(job)
        status = last["status"]
        # Start only once: progress updates and stale queued snapshots never
        # extend the execution budget. Resuming wait creates a new local budget.
        if status == "running" and execution_deadline is None:
            execution_deadline = clock() + (execution_timeout if execution_timeout is not None
                                            else DEFAULT_EXECUTION_TIMEOUT_PER_OUTPUT * last["requested_outputs"])
        progress = (status, last["requested_outputs"], last["completed_outputs"])
        if on_progress is not None and progress != last_progress:
            on_progress(dict(last))
            last_progress = progress
        if status == "succeeded":
            return job
        if status in FAILED_JOB_STATUSES:
            raise job_failure(client, job)
        deadline = execution_deadline if execution_deadline is not None else queue_deadline
        remaining = deadline - clock()
        if remaining > 0:
            sleep(min(poll_interval, remaining))


def save_job_assets(
    client: Codex2APIClient, job: dict[str, Any], out: Path | None, out_dir: Path | None, timeout: float, *,
    request_timeout: float | None = None, clock: Callable[[], float] = time.monotonic,
) -> list[Path]:
    validate_timeout(timeout, "download timeout")
    if request_timeout is not None:
        validate_timeout(request_timeout, "request timeout")
    deadline = clock() + timeout
    if (out is None) == (out_dir is None):
        raise CommandError("choose exactly one of out or out_dir")
    assets = job.get("assets")
    if not isinstance(assets, list) or not assets:
        raise CommandError("job has no downloadable assets", error_kind="missing_assets", job_id=job.get("id"))
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

    def remaining_time() -> float:
        remaining = deadline - clock()
        if remaining <= 0:
            raise CommandError("job download budget exhausted; resume with job download, do not submit again",
                               error_kind="job_timeout", job_id=job.get("id"),
                               details={"phase": "download", "next_command": f"job download {job.get('id')}"})
        return min(remaining, request_timeout) if request_timeout is not None else remaining

    try:
        for asset, target in zip(assets, targets):
            remaining = remaining_time()
            cached = asset.get("cache_b64_json")
            url = asset.get("proxy_url") or asset.get("url")
            if isinstance(cached, str) and cached:
                data = decode_base64(cached)
            elif isinstance(url, str) and url:
                data = asset_bytes(client, url, remaining)
            else:
                raise CommandError("job asset has no downloadable resource")
            items.append((data, target))
        remaining_time()
        return save_image_set(items)
    except CommandError as error:
        error.job_id = job.get("id")
        error.details.setdefault("phase", "download")
        error.details.setdefault("next_command", f"job download {job.get('id')}")
        raise
