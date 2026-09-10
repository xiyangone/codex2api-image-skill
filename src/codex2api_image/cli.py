from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from . import __version__
from .batch import read_jobs
from .config import load_config
from .errors import CommandError
from .http_client import Codex2APIClient
from .image_info import image_info
from .images import final_output_path, image_sources_to_urls, save_asset_url, save_response_images
from .jobs import response_job, response_job_id, save_job_assets, wait_job
from .paths import contained_output, ensure_distinct_outputs, output_targets, resolve_output
from .payloads import ImageOptions, clean_background_prompt, edit_payload, generation_payload, job_payload, parse_size

ROUTES = {"generate": "/images/generations", "edit": "/images/edits", "job": "/images/jobs"}


@dataclass(frozen=True)
class PreparedRequest:
    mode: str
    payload: dict[str, Any]
    options: ImageOptions
    out: Path | None
    out_dir: Path | None
    auto_retry: bool


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def client_from_args(args: argparse.Namespace) -> Codex2APIClient:
    return Codex2APIClient(load_config(env_file=Path(args.env_file) if args.env_file else None,
                                      base_url=args.base_url, api_key=args.api_key))


def build_options(args: argparse.Namespace, row: dict[str, Any] | None = None) -> ImageOptions:
    row = row or {}
    defaults = ImageOptions()
    values: dict[str, Any] = {}
    for field in fields(ImageOptions):
        value = row[field.name] if field.name in row else getattr(args, field.name, getattr(defaults, field.name))
        if isinstance(getattr(defaults, field.name), str):
            if not isinstance(value, str):
                raise CommandError(f"{field.name} must be a string")
            value = value.strip()
            if field.name not in {"model", "style"}:
                value = value.lower()
            if field.name == "output_format" and value == "jpg":
                value = "jpeg"
        values[field.name] = value
    return ImageOptions(**values)


def row_bool(row: dict[str, Any], key: str, default: bool) -> bool:
    value = row.get(key, default)
    if type(value) is not bool:
        raise CommandError(f"{key} must be a JSON boolean")
    return value


def row_images(row: dict[str, Any]) -> tuple[str, ...]:
    if "images" in row and "input_images" in row:
        raise CommandError("use images or input_images, not both")
    raw = row.get("images", row.get("input_images", []))
    if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
        raise CommandError("images/input_images must be an array of strings")
    return tuple(raw)


def load_manifest(path: str | None) -> object:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CommandError("cannot read reference image manifest JSON") from exc


def prepare_request(
    args: argparse.Namespace, mode: str, *, row: dict[str, Any] | None = None,
    out: Path | None = None, out_dir: Path | None = None,
) -> PreparedRequest:
    options = build_options(args, row)
    values = row or {}
    prompt = values.get("prompt", getattr(args, "prompt", ""))
    if not isinstance(prompt, str):
        raise CommandError("prompt must be a string")
    if row_bool(values, "clean_background", getattr(args, "clean_background", False)):
        prompt = clean_background_prompt(prompt)
    sources = row_images(values) if row is not None else tuple(getattr(args, "image", None) or ())
    images = image_sources_to_urls(sources)
    manifest = values.get("input_images_manifest") if row is not None else load_manifest(getattr(args, "manifest", None))
    if mode == "generate":
        if images or manifest:
            raise CommandError("generate does not accept reference images; use edit or job")
        payload = generation_payload(prompt, options)
    elif mode == "edit":
        payload = edit_payload(prompt, images, options, manifest)
    elif mode == "job":
        payload = job_payload(prompt, images, options, manifest)
    else:
        raise CommandError(f"unknown mode: {mode}")
    if out is not None:
        resolve_output(out)
        out = final_output_path(out, options.output_format)
        output_targets(out, options.n)
    if out_dir is not None:
        out_dir = out_dir.expanduser()
        if out_dir.exists() and not out_dir.is_dir():
            raise CommandError("out_dir must be a directory")
        # The eventual filenames are resolved and checked again before any download/write.
        for path in (out_dir, *out_dir.parents):
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise CommandError("output directory must not traverse a link")
        out_dir = out_dir.resolve()
    return PreparedRequest(mode, payload, options, out, out_dir,
                           row_bool(values, "auto_retry", getattr(args, "auto_retry", False)))


def dry_run_report(prepared: PreparedRequest) -> dict[str, Any]:
    payload = dict(prepared.payload)
    if "images" in payload:
        payload["images"] = [{"image_url": f"<image {index}>"} for index, _ in enumerate(payload["images"], 1)]
    if "input_images" in payload:
        payload["input_images"] = [f"<image {index}>" for index, _ in enumerate(payload["input_images"], 1)]
    return {"dry_run": True, "route": ROUTES[prepared.mode], "payload": payload,
            "planned_outputs": [str(path) for path in output_targets(prepared.out, prepared.options.n)] if prepared.out else [],
            "out_dir": str(prepared.out_dir) if prepared.out_dir else None}


def saved_report(saved: list[Path], *, model: str, requested_n: int, size: str = "auto", strict_size: bool = False, warning: str = "") -> dict[str, Any]:
    images = [image_info(path) for path in saved]
    warnings = [warning] if warning else []
    if requested_n != len(saved):
        warnings.append(f"requested {requested_n} images, saved {len(saved)}")
    expected = parse_size(size) if strict_size else None
    if expected and any((image["width"], image["height"]) != expected for image in images):
        warnings.append(f"actual output does not match requested canvas {size}; files retained, no local resizing")
    return {"ok": not warnings, "model": model, "saved": [str(path) for path in saved], "images": images,
            "requested_n": requested_n, "completed_n": len(saved), "warning": "; ".join(warnings)}


def run_prepared(client: Codex2APIClient, args: argparse.Namespace, prepared: PreparedRequest) -> dict[str, Any]:
    deadline = time.monotonic() + args.timeout
    response, attempts = client.request_with_retry("POST", ROUTES[prepared.mode], prepared.payload, timeout=args.timeout,
                                                  auto_retry=prepared.auto_retry, max_attempts=args.max_attempts)
    job_id: int | None = None
    try:
        if prepared.mode == "job":
            job_id = response_job_id(response)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CommandError("job submitted but wait budget exhausted", job_id=job_id)
            job = wait_job(client, job_id, remaining, args.poll_interval, auto_retry=prepared.auto_retry, max_attempts=args.max_attempts)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CommandError("job completed but download budget exhausted; resume job wait", job_id=job_id)
            saved = save_job_assets(client, job, prepared.out, prepared.out_dir, remaining)
            report = saved_report(saved, model=prepared.options.model, requested_n=prepared.options.n, size=prepared.options.size,
                                  strict_size=prepared.options.strict_size is not False, warning=client.redact(str(job.get("warning") or "")))
            report["job_id"] = job_id
        else:
            assert prepared.out is not None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CommandError("generation completed but download budget exhausted", outcome_unknown=True)
            saved = save_response_images(client, response, prepared.out, remaining)
            report = saved_report(saved, model=prepared.options.model, requested_n=prepared.options.n,
                                  warning=client.redact(str(response.get("warning") or "")))
            if isinstance(response.get("usage"), dict):
                report["usage"] = response["usage"]
        report.update({"mode": prepared.mode, "attempts": attempts})
        return report
    except CommandError as error:
        if job_id is not None:
            error.job_id = job_id
        error.details.setdefault("attempts", attempts)
        raise


def cmd_models(args: argparse.Namespace) -> int:
    client = client_from_args(args)
    response, _ = client.request_with_retry("GET", "/models", timeout=args.timeout, auto_retry=args.auto_retry, max_attempts=args.max_attempts)
    data = response.get("data")
    if not isinstance(data, list):
        raise CommandError("models response does not contain a data array")
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            print(item["id"])
    return 0


def cmd_image(args: argparse.Namespace) -> int:
    mode = args.command if args.command != "job" else "job"
    prepared = prepare_request(args, mode, out=Path(args.out) if args.out else None,
                               out_dir=Path(args.out_dir) if getattr(args, "out_dir", None) else None)
    if args.dry_run:
        print_json(dry_run_report(prepared))
        return 0
    report = run_prepared(client_from_args(args), args, prepared)
    print_json(report)
    return 0 if report["ok"] else 1


def cmd_job_submit(args: argparse.Namespace) -> int:
    prepared = prepare_request(args, "job")
    if args.dry_run:
        print_json(dry_run_report(prepared))
        return 0
    client = client_from_args(args)
    response, attempts = client.request_with_retry("POST", "/images/jobs", prepared.payload, timeout=args.timeout,
                                                  auto_retry=prepared.auto_retry, max_attempts=args.max_attempts)
    print_json({"job_id": response_job_id(response), "status": response_job(response).get("status"), "attempts": attempts})
    return 0


def cmd_job_wait(args: argparse.Namespace) -> int:
    out = resolve_output(Path(args.out)) if args.out else None
    out_dir = Path(args.out_dir) if args.out_dir else None
    client = client_from_args(args)
    deadline = time.monotonic() + args.timeout
    job = wait_job(client, args.job_id, args.timeout, args.poll_interval, auto_retry=args.auto_retry, max_attempts=args.max_attempts)
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CommandError("job completed but download budget exhausted; resume job wait")
        saved = save_job_assets(client, job, out, out_dir, remaining)
        params: dict[str, Any] = {}
        raw_params = job.get("params_json")
        if isinstance(raw_params, str):
            try:
                parsed = json.loads(raw_params)
                if isinstance(parsed, dict):
                    params = parsed
            except json.JSONDecodeError:
                pass
        requested = params.get("n", len(saved))
        if type(requested) is not int or requested < 1:
            requested = len(saved)
        size = params.get("size") or "auto"
        report = saved_report(saved, model=str(params.get("model") or "unknown"), requested_n=requested,
                              size=size, strict_size=params.get("strict_size") is not False,
                              warning=client.redact(str(job.get("warning") or "")))
        report.update({"job_id": args.job_id, "mode": "job"})
        print_json(report)
        return 0 if report["ok"] else 1
    except CommandError as error:
        error.job_id = args.job_id
        raise


def cmd_asset_save(args: argparse.Namespace) -> int:
    target = resolve_output(Path(args.out))
    saved = save_asset_url(client_from_args(args), args.url, target, args.timeout)
    print_json({"ok": True, "saved": [str(saved)], "images": [image_info(saved)]})
    return 0


def prepare_batch(args: argparse.Namespace) -> list[PreparedRequest]:
    rows = read_jobs(Path(args.input))
    if not rows:
        raise CommandError("batch must contain at least one job")
    allowed = {field.name for field in fields(ImageOptions)} | {
        "mode", "prompt", "out", "images", "input_images", "input_images_manifest", "auto_retry", "clean_background",
    }
    prepared: list[PreparedRequest] = []
    planned_paths: list[Path] = []
    for index, row in enumerate(rows, 1):
        try:
            if set(row) - allowed:
                raise CommandError("unknown batch field(s): " + ", ".join(sorted(set(row) - allowed)))
            sources = row_images(row)
            mode = row.get("mode", "edit" if sources else "generate")
            if not isinstance(mode, str):
                raise CommandError("batch mode must be a string")
            name = row.get("out", f"{index:03d}.png")
            if not isinstance(name, str):
                raise CommandError("batch out must be a relative filename")
            out = contained_output(Path(args.out_dir), name)
            item = prepare_request(args, mode.strip().lower(), row=row, out=out)
            prepared.append(item)
            assert item.out is not None
            planned_paths.extend(output_targets(item.out, item.options.n))
        except CommandError as error:
            error.details["index"] = index
            raise
    ensure_distinct_outputs(planned_paths)
    return prepared


def cmd_batch(args: argparse.Namespace) -> int:
    if args.concurrency < 1:
        raise CommandError("concurrency must be at least 1")
    prepared = prepare_batch(args)
    if args.dry_run:
        print_json({"dry_run": True, "jobs": [dry_run_report(item) for item in prepared]})
        return 0
    client = client_from_args(args)
    results: list[dict[str, Any]] = [{} for _ in prepared]
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(run_prepared, client, args, item): index for index, item in enumerate(prepared)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                result = future.result()
            except CommandError as error:
                result = {"ok": False, "error": error.as_dict()}
            except Exception as error:
                result = {"ok": False, "error": {"message": client.redact(str(error)), "error_kind": "unexpected_error"}}
            result["index"] = index + 1
            results[index] = result
    failed = sum(not item["ok"] for item in results)
    print_json({"results": results, "failed": failed})
    return 1 if failed else 0


def cmd_info(args: argparse.Namespace) -> int:
    print_json([image_info(Path(path)) for path in args.image])
    return 0


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url")
    parser.add_argument("--api-key", help="Prefer CODEX2API_API_KEY in the env file; never place keys in shell history")
    parser.add_argument("--env-file")
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--auto-retry", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=3)


def add_image_options(parser: argparse.ArgumentParser) -> None:
    defaults = ImageOptions()
    for name in ("model", "size", "quality", "output_format", "response_format", "background", "moderation", "style", "upscale", "upscale_fit"):
        parser.add_argument("--" + name.replace("_", "-"), default=getattr(defaults, name))
    parser.add_argument("--output-compression", type=int)
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--strict-size", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--clean-background", action="store_true")


def add_output(parser: argparse.ArgumentParser) -> None:
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--out")
    output.add_argument("--out-dir")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="API-key-only codex2api image CLI (v2.9.5 contract)")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    models = sub.add_parser("models")
    add_common(models)
    models.set_defaults(func=cmd_models)
    for name in ("generate", "edit"):
        command = sub.add_parser(name)
        add_common(command)
        add_image_options(command)
        command.add_argument("--prompt", required=True)
        command.add_argument("--out", required=True)
        command.add_argument("--dry-run", action="store_true")
        if name == "edit":
            command.add_argument("--image", action="append", required=True)
            command.add_argument("--manifest")
        command.set_defaults(func=cmd_image)
    batch = sub.add_parser("batch")
    add_common(batch)
    add_image_options(batch)
    batch.add_argument("--input", required=True)
    batch.add_argument("--out-dir", required=True)
    batch.add_argument("--concurrency", type=int, default=2)
    batch.add_argument("--poll-interval", type=float, default=2)
    batch.add_argument("--dry-run", action="store_true")
    batch.set_defaults(func=cmd_batch)
    jobs = sub.add_parser("job").add_subparsers(dest="job_command", required=True)
    for name in ("submit", "run"):
        command = jobs.add_parser(name)
        add_common(command)
        add_image_options(command)
        command.add_argument("--prompt", required=True)
        command.add_argument("--image", action="append")
        command.add_argument("--manifest")
        command.add_argument("--dry-run", action="store_true")
        if name == "run":
            add_output(command)
            command.add_argument("--poll-interval", type=float, default=2)
        command.set_defaults(func=cmd_job_submit if name == "submit" else cmd_image)
    wait = jobs.add_parser("wait")
    add_common(wait)
    wait.add_argument("job_id", type=int)
    add_output(wait)
    wait.add_argument("--poll-interval", type=float, default=2)
    wait.set_defaults(func=cmd_job_wait)
    save = sub.add_parser("asset").add_subparsers(dest="asset_command", required=True).add_parser("save")
    add_common(save)
    save.add_argument("--url", required=True)
    save.add_argument("--out", required=True)
    save.set_defaults(func=cmd_asset_save)
    info = sub.add_parser("info")
    info.add_argument("image", nargs="+")
    info.set_defaults(func=cmd_info)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        timeout = getattr(args, "timeout", 1)
        interval = getattr(args, "poll_interval", 1)
        if not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(interval) or interval <= 0:
            raise CommandError("timeout and poll interval must be positive and finite")
        if getattr(args, "max_attempts", 1) < 1:
            raise CommandError("max_attempts must be at least 1")
        return args.func(args)
    except CommandError as error:
        print(json.dumps({"ok": False, "error": error.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": {"message": str(error), "error_kind": "local_error"}}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
