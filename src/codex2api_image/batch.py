from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import CommandError


def read_jobs(path: Path) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CommandError(f"batch file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CommandError(f"batch file is not valid JSON: {exc}") from exc
    jobs = parsed.get("jobs") if isinstance(parsed, dict) else None
    if not isinstance(jobs, list):
        raise CommandError('batch file must be shaped as {"jobs":[...]}')
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(jobs, start=1):
        if not isinstance(row, dict):
            raise CommandError(f"batch job {idx} is not an object")
        out.append(row)
    return out
