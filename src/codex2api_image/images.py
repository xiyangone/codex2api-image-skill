from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .errors import CommandError
from .http_client import Codex2APIClient
from .image_info import MAX_IMAGE_BYTES, detect_image_bytes, extension_for_kind
from .paths import ensure_distinct_outputs, output_targets, resolve_output

MAX_INPUT_BYTES = 20 * 1024 * 1024


def decode_base64(value: str) -> bytes:
    if len(value) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise CommandError("encoded image exceeds the client size limit")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise CommandError("image contains invalid base64") from exc


def data_url_bytes(value: str) -> bytes | None:
    if not value.lower().startswith("data:"):
        return None
    if "," not in value:
        raise CommandError("invalid data URL")
    header, body = value.split(",", 1)
    if not header.lower().startswith("data:image/") or ";base64" not in header.lower():
        raise CommandError("image data URL must use base64")
    return decode_base64(body)


def image_source_to_url(source: str) -> str:
    value = source.strip()
    if not value:
        raise CommandError("image source is empty")
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"}:
        if not parsed.hostname or parsed.username or parsed.password:
            raise CommandError("image URL must not contain credentials")
        return value
    if parsed.scheme == "data":
        data = data_url_bytes(value)
        if data is None:
            raise CommandError("invalid image data URL")
    else:
        if parsed.scheme == "file":
            if parsed.netloc:
                raise CommandError("file URL must refer to a local file")
            path = Path(url2pathname(parsed.path))
        else:
            path = Path(value).expanduser()
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_INPUT_BYTES + 1)
        except OSError as exc:
            raise CommandError(f"cannot read input image: {path}") from exc
    if len(data) > MAX_INPUT_BYTES:
        raise CommandError("input image exceeds 20 MiB")
    kind, _, _ = detect_image_bytes(data)
    mime = "image/jpeg" if kind == "jpeg" else f"image/{kind}"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def image_sources_to_urls(sources: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if len(sources) > 16:
        raise CommandError("at most 16 input images are supported")
    return tuple(image_source_to_url(source) for source in sources)


def final_output_path(out: Path, kind: str) -> Path:
    extension = extension_for_kind(kind)
    suffix = out.suffix.lower().lstrip(".")
    if suffix != extension and not (kind == "jpeg" and suffix in {"jpg", "jpeg"}):
        out = out.with_suffix("." + extension)
    return resolve_output(out)


def save_bytes(data: bytes, out: Path) -> Path:
    kind, _, _ = detect_image_bytes(data)
    target = final_output_path(out, kind)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        resolve_output(target)
        with target.open("xb") as stream:
            stream.write(data)
    except FileExistsError as exc:
        raise CommandError(f"output already exists; choose a new filename: {target}") from exc
    except OSError as exc:
        raise CommandError(f"cannot save output: {target}", details={"partial_path": str(target)}) from exc
    return target


def save_image_set(items: list[tuple[bytes, Path]]) -> list[Path]:
    prepared = [(data, final_output_path(out, detect_image_bytes(data)[0])) for data, out in items]
    ensure_distinct_outputs([out for _, out in prepared])
    saved: list[Path] = []
    try:
        for data, out in prepared:
            saved.append(save_bytes(data, out))
    except CommandError as error:
        error.details["saved"] = [str(path) for path in saved]
        raise
    return saved


def asset_bytes(client: Codex2APIClient, url: str, timeout: float) -> bytes:
    inline = data_url_bytes(url)
    return inline if inline is not None else client.get_binary(url, timeout=timeout).data


def save_response_images(client: Codex2APIClient, response: dict[str, Any], out: Path, timeout: float) -> list[Path]:
    rows = response.get("data")
    if not isinstance(rows, list) or not rows:
        raw = client.redact(json.dumps(response, ensure_ascii=False))
        raise CommandError("response contains no image data", error_kind="invalid_response", body=raw[:65536], outcome_unknown=True)
    targets = output_targets(out, len(rows))
    items: list[tuple[bytes, Path]] = []
    for row, target in zip(rows, targets):
        if not isinstance(row, dict):
            raise CommandError("response image item is not an object", outcome_unknown=True)
        if isinstance(row.get("b64_json"), str) and row["b64_json"]:
            data = decode_base64(row["b64_json"])
        elif isinstance(row.get("url"), str) and row["url"]:
            data = asset_bytes(client, row["url"], timeout)
        else:
            raise CommandError("response image has neither b64_json nor url", outcome_unknown=True)
        items.append((data, target))
    return save_image_set(items)


def save_asset_url(client: Codex2APIClient, url: str, out: Path, timeout: float) -> Path:
    resolve_output(out)
    return save_bytes(asset_bytes(client, url, timeout), out)
