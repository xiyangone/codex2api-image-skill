from __future__ import annotations

import struct
from pathlib import Path

from .errors import CommandError


def png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", data[16:24])
    return None


def gif_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) >= 10 and data[:6] in {b"GIF87a", b"GIF89a"}:
        return struct.unpack("<HH", data[6:10])
    return None


def webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    if chunk == b"VP8 " and len(data) >= 30:
        width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return width, height
    if chunk == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return width, height
    return None


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    offset = 2
    while offset + 9 < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(data):
            return None
        length = struct.unpack(">H", data[offset : offset + 2])[0]
        if length < 2 or offset + length > len(data):
            return None
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            height = struct.unpack(">H", data[offset + 3 : offset + 5])[0]
            width = struct.unpack(">H", data[offset + 5 : offset + 7])[0]
            return width, height
        offset += length
    return None


def detect_image_bytes(data: bytes) -> tuple[str, int, int]:
    checks = (
        ("png", png_dimensions),
        ("jpeg", jpeg_dimensions),
        ("gif", gif_dimensions),
        ("webp", webp_dimensions),
    )
    for kind, fn in checks:
        dims = fn(data)
        if dims is not None:
            return kind, dims[0], dims[1]
    raise CommandError("unsupported or invalid image file")


def image_dimensions(path: Path) -> tuple[str, int, int]:
    return detect_image_bytes(path.read_bytes())


def extension_for_content(content_type: str, fallback: str = "png") -> str:
    lower = content_type.lower().split(";", 1)[0].strip()
    if lower == "image/png":
        return "png"
    if lower in {"image/jpeg", "image/jpg"}:
        return "jpg"
    if lower == "image/webp":
        return "webp"
    if lower == "image/gif":
        return "gif"
    return fallback
