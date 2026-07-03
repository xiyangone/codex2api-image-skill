from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .batch import read_jobs
from .config import ConfigError, load_config
from .errors import CommandError
from .http_client import Codex2APIClient
from .image_info import image_dimensions
from .images import image_sources_to_urls, save_asset_url, save_response_image
from .jobs import response_job_id, save_job_assets, submit_job, wait_job
from .payloads import ImageOptions, clean_background_prompt, edit_payload, generation_payload, job_payload


def build_options(args: argparse.Namespace, row: dict[str, Any] | None = None) -> ImageOptions:
    row = row or {}
    if "input_fidelity" in row:
        raise CommandError("input_fidelity is not supported by gpt-image-2*; remove it from the batch row.")
    background = str(row.get("background") or args.background)
    if background.strip().lower() == "transparent":
        raise CommandError("background=transparent is not supported; ask for a plain clean background in the prompt instead.")
    return ImageOptions(
        model=str(row.get("model") or args.model),
        size=str(row.get("size") or args.size),
        quality=str(row.get("quality") or args.quality),
        output_format=str(row.get("output_format") or args.output_format),
        response_format=str(row.get("response_format") or args.response_format),
        background=background,
        moderation=str(row.get("moderation") or args.moderation),
        output_compression=row.get("output_compression") if isinstance(row.get("output_compression"), int) else args.output_compression,
        n=int(row.get("n") or args.n),
        style=str(row.get("style") or args.style),
        upscale=str(row.get("upscale") or args.upscale),
    )


def row_bool(row: dict[str, Any], key: str, default: bool) -> bool:
    if key not in row:
        return default
    value = row[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise CommandError(f"batch row {key} must be a boolean")


def prompt_for_request(prompt: str, args: argparse.Namespace, row: dict[str, Any] | None = None) -> str:
    enabled = bool(getattr(args, "clean_background", False))
    if row is not None:
        enabled = row_bool(row, "clean_background", enabled)
    if not enabled:
        return prompt
    return clean_background_prompt(prompt)


def client_from_args(args: argparse.Namespace) -> Codex2APIClient:
    env_file = Path(args.env_file).expanduser() if args.env_file else None
    cfg = load_config(env_file=env_file, base_url=args.base_url, api_key=args.api_key)
    return Codex2APIClient(cfg)


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def cmd_models(args: argparse.Namespace) -> None:
    client = client_from_args(args)
    response = client.request_json("GET", "/models", timeout=args.timeout)
    data = response.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                print(item["id"])
        return
    print_json(response)


def cmd_generate(args: argparse.Namespace) -> None:
    client = client_from_args(args)
    options = build_options(args)
    payload = generation_payload(prompt_for_request(args.prompt, args), options)
    if args.dry_run:
        print_json(payload)
        return
    response = client.request_json("POST", "/images/generations", payload, timeout=args.timeout)
    saved = save_response_image(client, response, Path(args.out), args.timeout)
    kind, width, height = image_dimensions(saved)
    print_json({"saved": str(saved), "kind": kind, "width": width, "height": height, "mode": "sync-generate"})


def cmd_edit(args: argparse.Namespace) -> None:
    client = client_from_args(args)
    options = build_options(args)
    images = image_sources_to_urls(tuple(args.image))
    payload = edit_payload(prompt_for_request(args.prompt, args), images, options)
    if args.dry_run:
        redacted = dict(payload)
        redacted["images"] = [{"image_url": f"<image {idx}>"} for idx, _ in enumerate(images, start=1)]
        print_json(redacted)
        return
    response = client.request_json("POST", "/images/edits", payload, timeout=args.timeout)
    saved = save_response_image(client, response, Path(args.out), args.timeout)
    kind, width, height = image_dimensions(saved)
    print_json({"saved": str(saved), "kind": kind, "width": width, "height": height, "mode": "sync-edit"})


def cmd_job_submit(args: argparse.Namespace) -> None:
    client = client_from_args(args)
    images = image_sources_to_urls(tuple(args.image or ()))
    payload = job_payload(prompt_for_request(args.prompt, args), images, build_options(args))
    if args.dry_run:
        redacted = dict(payload)
        if "input_images" in redacted:
            redacted["input_images"] = [f"<image {idx}>" for idx, _ in enumerate(images, start=1)]
        print_json(redacted)
        return
    response = submit_job(client, payload, args.timeout)
    print_json(response)


def cmd_job_wait(args: argparse.Namespace) -> None:
    client = client_from_args(args)
    job = wait_job(client, args.job_id, args.timeout, args.poll_interval)
    saved = save_job_assets(client, job, Path(args.out) if args.out else None, Path(args.out_dir) if args.out_dir else None, args.timeout)
    print_json({"job_id": args.job_id, "saved": [str(path) for path in saved]})


def cmd_job_run(args: argparse.Namespace) -> None:
    client = client_from_args(args)
    images = image_sources_to_urls(tuple(args.image or ()))
    payload = job_payload(prompt_for_request(args.prompt, args), images, build_options(args))
    if args.dry_run:
        redacted = dict(payload)
        if "input_images" in redacted:
            redacted["input_images"] = [f"<image {idx}>" for idx, _ in enumerate(images, start=1)]
        print_json(redacted)
        return
    response = submit_job(client, payload, args.timeout)
    job_id = response_job_id(response)
    job = wait_job(client, job_id, args.timeout, args.poll_interval)
    saved = save_job_assets(client, job, Path(args.out) if args.out else None, Path(args.out_dir) if args.out_dir else None, args.timeout)
    print_json({"job_id": job_id, "saved": [str(path) for path in saved]})


def cmd_asset_save(args: argparse.Namespace) -> None:
    client = client_from_args(args)
    saved = save_asset_url(client, args.url, Path(args.out), args.timeout)
    kind, width, height = image_dimensions(saved)
    print_json({"saved": str(saved), "kind": kind, "width": width, "height": height})


def row_images(row: dict[str, Any]) -> tuple[str, ...]:
    raw = row.get("images", row.get("input_images", ()))
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise CommandError("batch job images/input_images must be an array")
    return tuple(str(item) for item in raw)


def batch_output_path(row: dict[str, Any], out_dir: Path, index: int) -> Path:
    return out_dir / str(row.get("out") or f"{index:03d}.png")


def ensure_unique_batch_outputs(rows: list[dict[str, Any]], out_dir: Path) -> None:
    seen: dict[str, int] = {}
    for index, row in enumerate(rows, start=1):
        path = batch_output_path(row, out_dir, index)
        key = str(path.resolve()).lower()
        previous = seen.get(key)
        if previous is not None:
            raise CommandError(f"batch output path collision: job {previous} and job {index} both write {path}")
        seen[key] = index


def run_batch_row(client: Codex2APIClient, args: argparse.Namespace, index: int, row: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    prompt = str(row.get("prompt") or "").strip()
    if not prompt:
        raise CommandError("missing prompt")
    options = build_options(args, row)
    prompt = prompt_for_request(prompt, args, row)
    images = image_sources_to_urls(row_images(row))
    mode = str(row.get("mode") or ("edit" if images else "generate")).lower()
    out = batch_output_path(row, out_dir, index)
    if mode in {"job", "async"}:
        response = submit_job(client, job_payload(prompt, images, options), args.timeout)
        job_id = response_job_id(response)
        job = wait_job(client, job_id, args.timeout, args.poll_interval)
        assets = job.get("assets")
        saved = save_job_assets(client, job, out if isinstance(assets, list) and len(assets) == 1 else None, out_dir, args.timeout)
        return {"index": index, "mode": "job", "job_id": job_id, "saved": [str(path) for path in saved]}
    if mode == "edit":
        response = client.request_json("POST", "/images/edits", edit_payload(prompt, images, options), timeout=args.timeout)
    elif mode == "generate":
        response = client.request_json("POST", "/images/generations", generation_payload(prompt, options), timeout=args.timeout)
    else:
        raise CommandError(f"unknown mode: {mode}")
    saved = save_response_image(client, response, out, args.timeout)
    return {"index": index, "mode": mode, "saved": str(saved)}


def run_batch_rows(
    rows: list[dict[str, Any]],
    *,
    concurrency: int,
    runner: Callable[[int, dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    if concurrency < 1:
        raise CommandError("batch concurrency must be at least 1")
    ordered: list[dict[str, Any] | None] = [None] * len(rows)
    first_error: str | None = None
    if concurrency == 1:
        for index, row in enumerate(rows, start=1):
            try:
                ordered[index - 1] = runner(index, row)
            except Exception as exc:
                raise CommandError(f"batch failed: job {index}: {exc}") from exc
        return [item for item in ordered if item is not None]

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_to_index = {pool.submit(runner, index, row): index for index, row in enumerate(rows, start=1)}
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                ordered[index - 1] = future.result()
            except Exception as exc:
                if first_error is None:
                    first_error = f"job {index}: {exc}"
    if first_error is not None:
        raise CommandError(f"batch failed: {first_error}")
    return [item for item in ordered if item is not None]


def cmd_batch(args: argparse.Namespace) -> None:
    client = client_from_args(args)
    rows = read_jobs(Path(args.input))
    out_dir = Path(args.out_dir)
    ensure_unique_batch_outputs(rows, out_dir)
    results = run_batch_rows(
        rows,
        concurrency=args.concurrency,
        runner=lambda index, row: run_batch_row(client, args, index, row, out_dir),
    )
    print_json({"results": results})


def cmd_info(args: argparse.Namespace) -> None:
    items: list[dict[str, object]] = []
    for raw in args.image:
        path = Path(raw)
        kind, width, height = image_dimensions(path)
        items.append({"path": str(path), "kind": kind, "width": width, "height": height, "bytes": path.stat().st_size})
    print_json(items)


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--timeout", type=int, default=900)


def add_image_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--size", default="auto")
    parser.add_argument("--quality", default="auto")
    parser.add_argument("--output-format", default="png")
    parser.add_argument("--response-format", choices=("b64_json", "url"), default="b64_json")
    parser.add_argument("--background", default="auto")
    parser.add_argument("--moderation", choices=("auto", "low"), default="low")
    parser.add_argument("--output-compression", type=int, default=None)
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--style", default="")
    parser.add_argument("--upscale", choices=("", "2k", "4k"), default="")
    parser.add_argument("--clean-background", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="API-key-only CLI for local codex2api image endpoints.")
    sub = parser.add_subparsers(dest="command", required=True)

    models = sub.add_parser("models")
    add_common(models)
    models.set_defaults(func=cmd_models)

    generate = sub.add_parser("generate")
    add_common(generate)
    add_image_options(generate)
    generate.add_argument("--prompt", required=True)
    generate.add_argument("--out", required=True)
    generate.add_argument("--dry-run", action="store_true")
    generate.set_defaults(func=cmd_generate)

    edit = sub.add_parser("edit")
    add_common(edit)
    add_image_options(edit)
    edit.add_argument("--prompt", required=True)
    edit.add_argument("--image", action="append", required=True)
    edit.add_argument("--out", required=True)
    edit.add_argument("--dry-run", action="store_true")
    edit.set_defaults(func=cmd_edit)

    batch = sub.add_parser("batch")
    add_common(batch)
    add_image_options(batch)
    batch.add_argument("--input", required=True)
    batch.add_argument("--out-dir", required=True)
    batch.add_argument("--poll-interval", type=float, default=2.0)
    batch.add_argument("--concurrency", type=int, default=3)
    batch.set_defaults(func=cmd_batch)

    job = sub.add_parser("job")
    job_sub = job.add_subparsers(dest="job_command", required=True)

    submit = job_sub.add_parser("submit")
    add_common(submit)
    add_image_options(submit)
    submit.add_argument("--prompt", required=True)
    submit.add_argument("--image", action="append")
    submit.add_argument("--dry-run", action="store_true")
    submit.set_defaults(func=cmd_job_submit)

    wait = job_sub.add_parser("wait")
    add_common(wait)
    wait.add_argument("job_id", type=int)
    wait.add_argument("--out", default="")
    wait.add_argument("--out-dir", default="")
    wait.add_argument("--poll-interval", type=float, default=2.0)
    wait.set_defaults(func=cmd_job_wait)

    run = job_sub.add_parser("run")
    add_common(run)
    add_image_options(run)
    run.add_argument("--prompt", required=True)
    run.add_argument("--image", action="append")
    run.add_argument("--out", default="")
    run.add_argument("--out-dir", default="")
    run.add_argument("--poll-interval", type=float, default=2.0)
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=cmd_job_run)

    asset = sub.add_parser("asset")
    asset_sub = asset.add_subparsers(dest="asset_command", required=True)
    save = asset_sub.add_parser("save")
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
        args.func(args)
    except (CommandError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
