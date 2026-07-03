from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import urlparse

from .errors import CommandError
from .http_client import Codex2APIClient
from .image_info import detect_image_bytes, extension_for_content

MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def data_url_bytes(value: str) -> bytes | None:
    if not value.lower().startswith("data:"):
        return None
    if "," not in value:
        raise CommandError("invalid data URL")
    header, body = value.split(",", 1)
    if ";base64" not in header.lower():
        raise CommandError("data URL must be base64 encoded")
    try:
        return base64.b64decode(body)
    except ValueError as exc:
        raise CommandError("data URL contains invalid base64") from exc


def image_source_to_url(source: str) -> str:
    value = source.strip()
    if not value:
        raise CommandError("image source is empty")
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        return value
    if parsed.scheme == "data":
        if data_url_bytes(value) is None:
            raise CommandError("invalid image data URL")
        return value
    if parsed.scheme == "file":
        path = Path(parsed.path)
    else:
        path = Path(value)
    if not path.is_file():
        raise CommandError(f"image file not found: {path}")
    suffix = path.suffix.lower()
    mime = MIME_BY_EXT.get(suffix, "application/octet-stream")
    data = path.read_bytes()
    detect_image_bytes(data)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def image_sources_to_urls(sources: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(image_source_to_url(source) for source in sources)


def save_bytes(data: bytes, out: Path) -> Path:
    detect_image_bytes(data)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out


def save_response_image(client: Codex2APIClient, response: dict[str, object], out: Path, timeout: int) -> Path:
    data = response.get("data")
    if not isinstance(data, list) or not data:
        raise CommandError("response does not contain data[0]")
    first = data[0]
    if not isinstance(first, dict):
        raise CommandError("response data[0] is not an object")
    raw_b64 = first.get("b64_json")
    if isinstance(raw_b64, str) and raw_b64.strip():
        return save_bytes(base64.b64decode(raw_b64), out)
    raw_url = first.get("url")
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise CommandError("response data[0] has neither b64_json nor url")
    as_data_url = data_url_bytes(raw_url)
    if as_data_url is not None:
        return save_bytes(as_data_url, out)
    binary = client.get_binary(raw_url, timeout=timeout)
    return save_bytes(binary.data, out)


def save_asset_url(client: Codex2APIClient, url: str, out: Path, timeout: int) -> Path:
    as_data_url = data_url_bytes(url)
    if as_data_url is not None:
        return save_bytes(as_data_url, out)
    binary = client.get_binary(url, timeout=timeout)
    if out.suffix == "":
        out = out.with_suffix("." + extension_for_content(binary.content_type))
    return save_bytes(binary.data, out)
