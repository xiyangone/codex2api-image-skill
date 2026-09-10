from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import CommandError

MAX_IMAGE_INPUTS = 16
MAX_IMAGE_PIXELS = 8_294_400
SIZE_RE = re.compile(r"^(\d+)x(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class ImageOptions:
    model: str = "gpt-image-2"
    size: str = "auto"
    quality: str = "auto"
    output_format: str = "png"
    response_format: str = "b64_json"
    background: str = "auto"
    moderation: str = ""
    output_compression: int | None = None
    n: int = 1
    style: str = ""
    upscale: str = ""
    strict_size: bool | None = None
    upscale_fit: str = ""


def parse_size(size: str) -> tuple[int, int] | None:
    if size == "auto":
        return None
    match = SIZE_RE.fullmatch(size)
    if match is None or min(int(match[1]), int(match[2])) < 1:
        raise CommandError("size must be auto or positive WIDTHxHEIGHT")
    return int(match[1]), int(match[2])


def validate_options(options: ImageOptions, mode: str) -> None:
    if not options.model.strip() or any(char.isspace() for char in options.model):
        raise CommandError("model must be a non-empty model ID without whitespace")
    allowed_quality = {"auto", "low", "medium", "high", "xhigh", "max"}
    if options.quality not in allowed_quality:
        raise CommandError("unsupported quality value")
    if options.model.lower().startswith("gpt-image-2") and not options.model.lower().startswith("gpt-image-2.5") and options.quality in {"xhigh", "max"}:
        raise CommandError("xhigh/max require GPT Image 2.5; quality will not be silently downgraded")
    if options.output_format not in {"png", "jpeg", "webp"}:
        raise CommandError("output_format must be png, jpeg or webp")
    if options.response_format not in {"b64_json", "url"}:
        raise CommandError("response_format must be b64_json or url")
    if options.background not in {"auto", "opaque"}:
        raise CommandError("background must be auto or opaque; transparent output is not requested by this skill")
    if options.moderation not in {"", "auto", "low"}:
        raise CommandError("moderation must be auto or low when supplied")
    if type(options.n) is not int or not 1 <= options.n <= 4:
        raise CommandError("n must be an integer between 1 and 4")
    if options.output_compression is not None and (type(options.output_compression) is not int or not 0 <= options.output_compression <= 100):
        raise CommandError("output_compression must be an integer between 0 and 100")
    if options.output_compression is not None and options.output_format == "png":
        raise CommandError("output_compression applies only to jpeg/webp")
    if options.strict_size is not None and type(options.strict_size) is not bool:
        raise CommandError("strict_size must be a boolean")
    if options.upscale not in {"", "2k", "4k"} or options.upscale_fit not in {"", "pad", "cover"}:
        raise CommandError("invalid upscale or upscale_fit value")
    size = parse_size(options.size)
    if mode == "job":
        if options.response_format != "b64_json" or options.moderation or options.output_compression is not None:
            raise CommandError("job does not accept response_format=url, moderation or output_compression")
        if size and options.strict_size is not False:
            size = tuple(((value + 15) // 16) * 16 for value in size)
    else:
        if options.n != 1:
            raise CommandError("synchronous n>1 is not supported by codex2api; use batch or job")
        if options.upscale or options.strict_size is not None or options.upscale_fit:
            raise CommandError("upscale/strict_size/upscale_fit are job-only; use a model tier for synchronous upscale")
    if size and options.model.lower().startswith("gpt-image-2"):
        width, height = size
        if width % 16 or height % 16:
            raise CommandError("synchronous image size must be a multiple of 16; use job for an exact display canvas")
        if width * height > MAX_IMAGE_PIXELS or max(width, height) > 3 * min(width, height):
            raise CommandError("upstream size exceeds 8294400 pixels or a 3:1 aspect ratio")


def clean_background_prompt(prompt: str) -> str:
    return "\n".join([
        "Objective: use a plain clean light background with no clutter.",
        "For image edits, change only the background.",
        "Preserve the primary subject, pose, clothing, crop, lighting, and facial/body details.",
        "Use a plain clean light background; not transparent.",
        "Do not add text, watermark, extra people, props, or scenery.",
        f"User request: {prompt}",
    ])


def validate_prompt(prompt: str, mode: str) -> None:
    if not prompt.strip():
        raise CommandError("prompt is required")
    if mode == "job" and len(prompt) > 8000:
        raise CommandError("job prompt must be at most 8000 characters")


def validate_manifest(manifest: object, image_count: int) -> list[dict[str, Any]]:
    if manifest is None:
        return []
    if not isinstance(manifest, list):
        raise CommandError("input_images_manifest must be an array")
    seen: set[int] = set()
    result: list[dict[str, Any]] = []
    for item in manifest:
        if not isinstance(item, dict) or set(item) - {"index", "filename", "role", "label"}:
            raise CommandError("invalid reference image manifest item")
        index = item.get("index")
        if type(index) is not int or not 0 <= index < image_count or index in seen:
            raise CommandError("manifest indexes must be unique valid zero-based image indexes")
        if any(not isinstance(value, str) for key, value in item.items() if key != "index"):
            raise CommandError("manifest filename, role and label must be strings")
        seen.add(index)
        result.append(dict(item))
    return result


def generation_payload(prompt: str, options: ImageOptions) -> dict[str, Any]:
    validate_prompt(prompt, "generate")
    validate_options(options, "generate")
    payload: dict[str, Any] = {"model": options.model, "prompt": prompt, "response_format": options.response_format}
    for key in ("size", "quality", "output_format", "background", "moderation", "style"):
        value = getattr(options, key)
        if value:
            payload[key] = value
    if options.output_compression is not None:
        payload["output_compression"] = options.output_compression
    return payload


def edit_payload(prompt: str, images: tuple[str, ...], options: ImageOptions, manifest: object = None) -> dict[str, Any]:
    if not 1 <= len(images) <= MAX_IMAGE_INPUTS:
        raise CommandError("edit requires 1 to 16 images")
    payload = generation_payload(prompt, options)
    payload["images"] = [{"image_url": image} for image in images]
    if manifest is not None:
        payload["input_images_manifest"] = validate_manifest(manifest, len(images))
    return payload


def job_payload(prompt: str, input_images: tuple[str, ...], options: ImageOptions, manifest: object = None) -> dict[str, Any]:
    validate_prompt(prompt, "job")
    validate_options(options, "job")
    if len(input_images) > MAX_IMAGE_INPUTS:
        raise CommandError("job accepts at most 16 input images")
    payload: dict[str, Any] = {"model": options.model, "prompt": prompt, "n": options.n}
    for key in ("size", "quality", "output_format", "background", "style", "upscale", "upscale_fit"):
        value = getattr(options, key)
        if value and value != "auto":
            payload[key] = value
    if options.strict_size is not None:
        payload["strict_size"] = options.strict_size
    if input_images:
        payload["input_images"] = list(input_images)
    if manifest is not None:
        payload["input_images_manifest"] = validate_manifest(manifest, len(input_images))
    return payload
