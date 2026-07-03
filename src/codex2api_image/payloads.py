from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OMIT_VALUES = {"", "omit", "none", "null", "unspecified"}


@dataclass(frozen=True)
class ImageOptions:
    model: str = "gpt-image-2"
    size: str = "auto"
    quality: str = "auto"
    output_format: str = "png"
    response_format: str = "b64_json"
    background: str = "auto"
    moderation: str = "low"
    output_compression: int | None = None
    n: int = 1
    style: str = ""
    upscale: str = ""


def maybe_set(payload: dict[str, Any], key: str, value: str | int | None, *, omit_auto: bool = False) -> None:
    if value is None:
        return
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.lower() in OMIT_VALUES:
            return
        if omit_auto and normalized.lower() == "auto":
            return
        payload[key] = normalized
        return
    payload[key] = value


def styled_prompt(prompt: str, style: str) -> str:
    prompt = prompt.strip()
    style = style.strip()
    if not style:
        return prompt
    return f"{prompt}\n\nStyle: {style}"


def clean_background_prompt(prompt: str) -> str:
    prompt = prompt.strip()
    return "\n".join(
        [
            "Objective: use a plain clean light background with no clutter.",
            "For image edits, change only the background.",
            "Preserve the primary subject, pose, clothing, crop, lighting, and facial/body details.",
            "Use a plain clean light background; not transparent.",
            "Do not add text, watermark, extra people, props, or scenery.",
            f"User request: {prompt}",
        ]
    )


def generation_payload(prompt: str, options: ImageOptions) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": options.model,
        "prompt": styled_prompt(prompt, options.style),
        "response_format": options.response_format,
    }
    maybe_set(payload, "size", options.size)
    maybe_set(payload, "quality", options.quality)
    maybe_set(payload, "output_format", options.output_format)
    maybe_set(payload, "background", options.background)
    maybe_set(payload, "moderation", options.moderation)
    maybe_set(payload, "output_compression", options.output_compression)
    if options.n > 1:
        payload["n"] = options.n
    return payload


def edit_payload(prompt: str, images: tuple[str, ...], options: ImageOptions) -> dict[str, Any]:
    payload = generation_payload(prompt, options)
    payload["images"] = [{"image_url": image} for image in images if image.strip()]
    return payload


def job_payload(prompt: str, input_images: tuple[str, ...], options: ImageOptions) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": options.model,
        "prompt": prompt.strip(),
    }
    maybe_set(payload, "size", options.size, omit_auto=True)
    maybe_set(payload, "quality", options.quality, omit_auto=True)
    maybe_set(payload, "output_format", options.output_format)
    maybe_set(payload, "background", options.background, omit_auto=True)
    maybe_set(payload, "style", options.style)
    maybe_set(payload, "upscale", options.upscale)
    if input_images:
        payload["input_images"] = list(input_images)
    return payload
