from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import RuntimeConfig
from .errors import CommandError, redact_text
from .image_info import MAX_IMAGE_BYTES

MAX_JSON_BYTES = 96 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


@dataclass(frozen=True)
class BinaryResponse:
    data: bytes
    content_type: str


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    if not value or not value.strip():
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = date.timestamp() - (time.time() if now is None else now)
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def is_retryable(error: CommandError, method: str) -> bool:
    kind = error.error_kind.lower()
    if error.outcome_unknown or kind.startswith("policy_") or kind in {
        "image_output_rejected", "content_policy_violation", "invalid_api_key", "authentication_error",
        "permission_denied", "insufficient_quota", "account_pool_usage_limit_reached",
    }:
        return False
    if method == "GET" and kind == "transport_error":
        return True
    return error.status_code in {429, 503} and error.retry_after is not None


class Codex2APIClient:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        # Do not forward a bearer credential or signed asset URL through a redirect.
        self._open = build_opener(NoRedirect()).open

    def redact(self, text: str) -> str:
        return redact_text(text, (self.config.api_key,))

    def api_url(self, path: str) -> str:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc:
            raise CommandError("API paths must be relative to the configured service")
        return urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))

    def service_url(self, path_or_url: str) -> str:
        parsed = urlsplit(path_or_url)
        if parsed.scheme:
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise CommandError("asset URL must use HTTP(S) without embedded credentials")
            return path_or_url
        if parsed.netloc:
            raise CommandError("protocol-relative asset URLs are not supported")
        base = urlsplit(self.config.base_url)
        return urljoin(f"{base.scheme}://{base.netloc}/", path_or_url.lstrip("/"))

    def _http_error(self, exc: HTTPError, route: str) -> CommandError:
        try:
            body = exc.read(MAX_ERROR_BYTES + 1).decode("utf-8", errors="replace")
        except (OSError, HTTPException) as read_error:
            body = "HTTP error body could not be read: " + str(read_error)
        if len(body.encode("utf-8")) > MAX_ERROR_BYTES:
            body = body[:MAX_ERROR_BYTES] + " [truncated]"
        message, kind = str(exc.reason), "http_error"
        try:
            parsed = json.loads(body)
            error = parsed.get("error") if isinstance(parsed, dict) else None
            if isinstance(error, dict):
                message = str(error.get("message") or message)
                kind = str(error.get("code") or error.get("type") or kind)
        except json.JSONDecodeError:
            pass
        return CommandError(message, route=route, status_code=exc.code, error_kind=kind, body=body,
                            retry_after=parse_retry_after((exc.headers or {}).get("Retry-After")), secrets=(self.config.api_key,))

    def _read(self, request: Request, route: str, timeout: float, limit: int) -> tuple[bytes, str, int]:
        if not math.isfinite(timeout) or timeout <= 0:
            raise CommandError("request timeout must be positive and finite")
        try:
            with self._open(request, timeout=timeout) as response:
                data = response.read(limit + 1)
                content_type = response.headers.get("Content-Type", "")
                status = response.status
        except HTTPError as exc:
            raise self._http_error(exc, route) from exc
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            raise CommandError("request transport failed", route=route, error_kind="transport_error",
                               body=str(exc), outcome_unknown=request.method != "GET", secrets=(self.config.api_key,)) from exc
        if len(data) > limit:
            raise CommandError("response exceeds the client size limit", route=route, error_kind="response_too_large",
                               outcome_unknown=request.method != "GET")
        return data, content_type, status

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 300) -> dict[str, Any]:
        method = method.upper()
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(self.api_url(path), data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.config.api_key}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        raw, _, status = self._read(request, path, timeout, MAX_JSON_BYTES)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise CommandError("response is not valid JSON", route=path, body=raw[:MAX_ERROR_BYTES].decode("utf-8", errors="replace"),
                               error_kind="invalid_response", status_code=status, outcome_unknown=method != "GET", secrets=(self.config.api_key,)) from exc
        if not isinstance(parsed, dict):
            raise CommandError("response JSON is not an object", route=path, status_code=status, body=self.redact(raw[:MAX_ERROR_BYTES].decode("utf-8", errors="replace")), error_kind="invalid_response", outcome_unknown=method != "GET")
        return parsed

    def request_with_retry(
        self, method: str, path: str, payload: dict[str, Any] | None = None, *, timeout: float,
        auto_retry: bool = False, max_attempts: int = 3,
        clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if max_attempts < 1 or not math.isfinite(timeout) or timeout <= 0:
            raise CommandError("timeout and max_attempts must be positive")
        deadline = clock() + timeout
        attempts: list[dict[str, Any]] = []
        count = max_attempts if auto_retry else 1
        for index in range(1, count + 1):
            remaining = deadline - clock()
            if remaining <= 0:
                raise CommandError("request retry budget exhausted", route=path, details={"attempts": attempts})
            try:
                response = self.request_json(method, path, payload, timeout=remaining)
                attempts.append({"index": index, "ok": True})
                return response, attempts
            except CommandError as error:
                attempts.append({"index": index, "ok": False, "error": error.as_dict()})
                if index == count or not is_retryable(error, method.upper()):
                    error.details["attempts"] = attempts
                    raise
                delay = max(0.2, error.retry_after if error.retry_after is not None else 2 ** (index - 1))
                if delay >= deadline - clock():
                    error.details.update({"attempts": attempts, "retry_stopped": "Retry-After exceeds remaining time budget"})
                    raise
                sleep(delay)
        raise AssertionError("unreachable retry state")

    def get_binary(self, path_or_url: str, timeout: float = 300) -> BinaryResponse:
        request = Request(self.service_url(path_or_url), method="GET")
        request.add_header("Accept", "image/*")
        data, content_type, _ = self._read(request, path_or_url, timeout, MAX_IMAGE_BYTES)
        return BinaryResponse(data, content_type)
