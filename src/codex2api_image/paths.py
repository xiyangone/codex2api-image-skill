from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

from .errors import CommandError


def skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_env_file() -> Path:
    return skill_root() / ".env"


def resolve_output(path: Path, *, root: Path | None = None) -> Path:
    path = path.expanduser()
    windows = PureWindowsPath(str(path))
    components = windows.parts[1:] if windows.anchor else windows.parts
    if any(":" in part or part.endswith((" ", ".")) or PureWindowsPath(part).is_reserved() for part in components):
        raise CommandError("output path contains a reserved or ambiguous Windows component")
    # Reject links before resolving them, including dangling links and Windows junctions.
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise CommandError(f"output path must not traverse a link: {part}")
    resolved = path.resolve()
    if root is not None and not resolved.is_relative_to(root.expanduser().resolve()):
        raise CommandError(f"output path escapes the requested directory: {path}")
    if resolved.exists():
        raise CommandError(f"output already exists; choose a new filename: {resolved}")
    parent = resolved.parent
    while not parent.exists():
        parent = parent.parent
    if not parent.is_dir():
        raise CommandError(f"output parent is not a directory: {parent}")
    return resolved


def contained_output(root: Path, name: str) -> Path:
    if not name.strip():
        raise CommandError("output filename is empty")
    windows = PureWindowsPath(name)
    if windows.drive or windows.root or ":" in name or ".." in windows.parts:
        raise CommandError("output filename must be relative and cannot contain parent traversal")
    if any(part.endswith((" ", ".")) or PureWindowsPath(part).is_reserved() for part in windows.parts):
        raise CommandError("output filename contains a reserved or ambiguous Windows component")
    return resolve_output(root / name, root=root)


def ensure_distinct_outputs(paths: list[Path]) -> None:
    seen: set[str] = set()
    for path in paths:
        key = os.path.normcase(str(path.resolve())).casefold()
        if key in seen:
            raise CommandError(f"output path collision: {path}")
        seen.add(key)


def output_targets(out: Path, count: int) -> list[Path]:
    if count < 1:
        raise CommandError("response did not contain any output images")
    if count == 1:
        return [resolve_output(out)]
    targets = [out.with_name(f"{out.stem}-{index:03d}{out.suffix}") for index in range(1, count + 1)]
    resolved = [resolve_output(target, root=out.parent) for target in targets]
    ensure_distinct_outputs(resolved)
    return resolved
