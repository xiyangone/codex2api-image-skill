from __future__ import annotations

from pathlib import Path


def skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_env_file() -> Path:
    return skill_root() / ".env"
