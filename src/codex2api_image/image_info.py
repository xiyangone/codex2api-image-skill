from __future__ import annotations

import io
import warnings
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .errors import CommandError

SUPPORTED_FORMATS = {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp", "GIF": "gif"}
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_FRAMES = 256


def detect_image_bytes(data: bytes) -> tuple[str, int, int]:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise CommandError("image is empty or exceeds the 64 MiB limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                kind = SUPPORTED_FORMATS.get(image.format or "")
                if kind is None:
                    raise CommandError("unsupported image format; expected PNG, JPEG, WebP or GIF")
                width, height = image.size
                image.verify()
            # verify() is not a full decode and closes the decoder; reopen and load every frame.
            with Image.open(io.BytesIO(data)) as image:
                frames = getattr(image, "n_frames", 1)
                if frames > MAX_IMAGE_FRAMES:
                    raise CommandError("image contains too many frames")
                for index in range(frames):
                    image.seek(index)
                    image.load()
    except CommandError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise CommandError("unsupported, truncated or invalid image file") from exc
    if width < 1 or height < 1:
        raise CommandError("image dimensions must be positive")
    return kind, width, height


def image_dimensions(path: Path) -> tuple[str, int, int]:
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
    except OSError as exc:
        raise CommandError(f"cannot read image: {path}") from exc
    return detect_image_bytes(data)


def image_info(path: Path) -> dict[str, Any]:
    kind, width, height = image_dimensions(path)
    return {"path": str(path.resolve()), "kind": kind, "width": width, "height": height, "bytes": path.stat().st_size}


def extension_for_kind(kind: str) -> str:
    return "jpg" if kind == "jpeg" else kind
