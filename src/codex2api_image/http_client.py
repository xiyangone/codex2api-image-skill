from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from .config import RuntimeConfig
from .errors import CommandError


@dataclass(frozen=True)
class BinaryResponse:
    data: bytes
    content_type: str


class Codex2APIClient:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def api_url(self, path: str) -> str:
        return urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))

    def service_url(self, path_or_url: str) -> str:
        parsed = urlparse(path_or_url)
        if parsed.scheme in {"http", "https", "data"}:
            return path_or_url
        parsed_base = urlparse(self.config.base_url)
        root = f"{parsed_base.scheme}://{parsed_base.netloc}/"
        return urljoin(root, path_or_url.lstrip("/"))

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 300) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(self.api_url(path), data=data, method=method.upper())
        req.add_header("Authorization", f"Bearer {self.config.api_key}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise CommandError(f"HTTP {exc.code} {exc.reason}: {body}") from exc
        except URLError as exc:
            raise CommandError(f"request failed: {exc.reason}") from exc
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            text = raw[:1000].decode("utf-8", errors="replace")
            raise CommandError(f"response is not JSON: {text}") from exc
        if not isinstance(parsed, dict):
            raise CommandError("response JSON is not an object")
        return parsed

    def get_binary(self, path_or_url: str, timeout: int = 300, auth: bool = False) -> BinaryResponse:
        req = Request(self.service_url(path_or_url), method="GET")
        if auth:
            req.add_header("Authorization", f"Bearer {self.config.api_key}")
        try:
            with urlopen(req, timeout=timeout) as resp:
                content_type = resp.headers.get("Content-Type", "")
                return BinaryResponse(resp.read(), content_type)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise CommandError(f"HTTP {exc.code} {exc.reason}: {body}") from exc
        except URLError as exc:
            raise CommandError(f"download failed: {exc.reason}") from exc
