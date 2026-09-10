from __future__ import annotations

import re
from typing import Any

_SECRET_FIELD = re.compile(r'(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|secret|signature|sig)["\s]*[:=]["\s]*)([^"\s&,}]+)')
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s\"',}]+")
_DATA_URL = re.compile(r"data:[^\s\"']+", re.IGNORECASE)


def redact_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "<redacted>")
    value = _BEARER.sub("Bearer <redacted>", value)
    value = _SECRET_FIELD.sub(r"\1<redacted>", value)
    return _DATA_URL.sub("<image-data>", value)


class CommandError(RuntimeError):
    """Structured, sanitized failure, shared by HTTP, jobs and filesystem operations."""

    def __init__(
        self,
        message: str,
        *,
        route: str = "",
        status_code: int | None = None,
        error_kind: str = "",
        body: str = "",
        retry_after: float | None = None,
        outcome_unknown: bool = False,
        job_id: int | None = None,
        details: dict[str, Any] | None = None,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self.message = redact_text(message, secrets)
        self.route = redact_text(route, secrets)
        self.status_code = status_code
        self.error_kind = redact_text(error_kind, secrets)
        self.body = redact_text(body, secrets)
        self.retry_after = retry_after
        self.outcome_unknown = outcome_unknown
        self.job_id = job_id
        self.details = details or {}
        super().__init__(self.message)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"message": self.message}
        for key in ("route", "status_code", "error_kind", "body", "retry_after", "job_id"):
            value = getattr(self, key)
            if value is not None and value != "":
                result[key] = value
        if self.outcome_unknown:
            result["outcome_unknown"] = True
        result.update(self.details)
        return result

    def __str__(self) -> str:
        prefix = f"HTTP {self.status_code}: " if self.status_code is not None else ""
        return prefix + self.message
