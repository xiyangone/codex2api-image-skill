from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .errors import CommandError
from .paths import default_env_file

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"


class ConfigError(CommandError):
    pass


@dataclass(frozen=True)
class RuntimeConfig:
    base_url: str
    api_key: str = field(repr=False)
    env_file: Path | None = None


def parse_dotenv(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"cannot read env file: {path}") from exc
    values: dict[str, str] = {}
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        name, value = raw.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name.strip()] = value
    return values


def load_config(
    *,
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> RuntimeConfig:
    env = os.environ if environ is None else environ
    configured_file = env.get("CODEX2API_IMAGE_ENV_FILE", "").strip()
    path = (env_file or (Path(configured_file) if configured_file else default_env_file())).expanduser()
    if (env_file is not None or configured_file) and not path.is_file():
        raise ConfigError(f"env file not found: {path}")
    file_values = parse_dotenv(path)
    url = (base_url or env.get("CODEX2API_BASE_URL") or file_values.get("CODEX2API_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    key = (api_key or env.get("CODEX2API_API_KEY") or file_values.get("CODEX2API_API_KEY") or "").strip()
    try:
        parts = urlsplit(url)
        valid = parts.scheme in {"http", "https"} and bool(parts.hostname) and parts.port != 0
    except ValueError:
        valid = False
        parts = urlsplit("")
    if not valid or parts.username or parts.password or parts.query or parts.fragment:
        raise ConfigError("base URL must be an HTTP(S) API base without credentials, query or fragment")
    if not key:
        raise ConfigError("CODEX2API_API_KEY is missing or blank; configure the dedicated env file or process variable")
    return RuntimeConfig(base_url=url, api_key=key, env_file=path)
