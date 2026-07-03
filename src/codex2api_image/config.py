from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .paths import default_env_file

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeConfig:
    base_url: str
    api_key: str
    env_file: Path | None = None


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        raw = line.strip()
        if raw == "" or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def first_non_empty(values: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = values.get(name, "").strip()
        if value:
            return value
    return ""


def load_config(
    *,
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> RuntimeConfig:
    env = dict(os.environ if environ is None else environ)
    path = env_file or Path(env.get("CODEX2API_IMAGE_ENV_FILE", "")).expanduser()
    if str(path) == ".":
        path = default_env_file()
    file_exists = path.is_file()
    file_values = parse_dotenv(path) if file_exists else {}

    resolved_base_url = (
        (base_url or "").strip()
        or first_non_empty(env, "CODEX2API_BASE_URL", "OPENAI_BASE_URL")
        or first_non_empty(file_values, "CODEX2API_BASE_URL", "OPENAI_BASE_URL")
        or DEFAULT_BASE_URL
    )
    resolved_api_key = (
        (api_key or "").strip()
        or first_non_empty(env, "CODEX2API_API_KEY", "OPENAI_API_KEY")
        or first_non_empty(file_values, "CODEX2API_API_KEY", "OPENAI_API_KEY")
    )

    if not resolved_api_key:
        if file_exists:
            raise ConfigError(
                f"API key is blank in {path}; set CODEX2API_API_KEY in that file or in the process environment."
            )
        raise ConfigError("No API key found; set CODEX2API_API_KEY or OPENAI_API_KEY.")
    if not resolved_base_url:
        raise ConfigError("No base URL found; set CODEX2API_BASE_URL.")

    return RuntimeConfig(base_url=resolved_base_url.rstrip("/"), api_key=resolved_api_key, env_file=path)
