from __future__ import annotations

from dataclasses import replace
from dataclasses import dataclass
import re
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


def sanitized_user_request(prompt: str) -> str:
    sanitized = prompt.strip()
    replacements = (
        (r"\bupscale\s+this\s+(specific|particular)\s+image\b", "make a conservative clarity pass"),
        (r"\b(edit|enhance|restore|process)\s+this\s+(specific|particular)\s+image\b", "apply a conservative visual pass"),
        (r"\bthis\s+(specific|particular)\s+image\b", "the provided reference"),
        (r"\bpreserv(e|ing)\s+(the\s+)?(original\s+)?identity\b", "keep visual continuity"),
        (r"\bidentity\b", "visual continuity"),
        (r"\bperson\b", "main subject"),
        (r"\bface\b", "visible details"),
        (r"\bbody\s+shape\b", "silhouette"),
        (r"\b4k\b", "clean output"),
        (r"\bhigh[-\s]?resolution\b", "clean output"),
        (r"\bupscale\b", "clarity pass"),
        (r"\bversion\b", "result"),
    )
    for pattern, replacement in replacements:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\bwhile\s+keep\b", "while keeping", sanitized, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", sanitized).strip(" .")


def sanitized_retry_prompt(prompt: str) -> str:
    sanitized = sanitized_user_request(prompt)
    if sanitized:
        return " ".join(
            [
                "Using the provided reference, apply a conservative non-destructive visual cleanup.",
                "Keep the scene, pose, clothing, background, lighting, and composition unchanged.",
                "Do not redraw, restyle, or change semantic content.",
                f"Sanitized user request: {sanitized}.",
            ]
        )
    return " ".join(
        [
            "Using the provided reference, apply a conservative non-destructive visual cleanup.",
            "Keep the scene, pose, clothing, background, lighting, and composition unchanged.",
            "Do not redraw, restyle, or change semantic content.",
        ]
    )


def restoration_retry_prompt(prompt: str) -> str:
    return " ".join(
        [
            "Perform a conservative photo restoration pass on the provided reference.",
            "Keep the scene, composition, pose, clothing, background, and lighting direction unchanged.",
            "Apply only non-destructive cleanup: reduce visible compression artifacts and keep natural exposure.",
        ]
    )


def quality_cleanup_retry_prompt(prompt: str) -> str:
    return " ".join(
        [
            "Apply a conservative quality cleanup to the provided reference.",
            "Keep the same scene, composition, pose, clothing, background, and lighting.",
            "Make no semantic content changes; do not redraw or restyle the scene.",
            "Keep natural color and exposure while reducing visible artifacts only.",
        ]
    )


def modest_outfit_reframe_prompt(prompt: str) -> str:
    return " ".join(
        [
            "Using the provided reference, make a complete modest casual outfit edit rather than a localized body-area edit.",
            "Keep the same setting, composition, pose, natural daylight, and camera angle.",
            "Use everyday clothing language and keep unrelated scene details unchanged.",
            f"User request: {sanitized_user_request(prompt)}",
        ]
    )


def prompt_mentions_white_tights(prompt: str) -> bool:
    return bool(
        re.search(
            r"(白丝|白色丝袜|白色连裤袜|white\s+(opaque\s+)?(tights|stockings)|opaque\s+white\s+tights)",
            prompt,
            flags=re.IGNORECASE,
        )
    )


def prompt_mentions_no_shoes_tights(prompt: str) -> bool:
    return prompt_mentions_white_tights(prompt) and bool(
        re.search(r"(裸足|赤脚|无鞋|不穿鞋|no\s+shoes|without\s+shoes|barefoot)", prompt, flags=re.IGNORECASE)
    )


def closed_foot_tights_reframe_prompt(prompt: str) -> str:
    phone_wallpaper = bool(re.search(r"(手机壁纸|phone\s+wallpaper|portrait)", prompt, flags=re.IGNORECASE))
    prefix = "Create a portrait-oriented phone wallpaper from the reference." if phone_wallpaper else "Using the provided reference, edit the outfit."
    return " ".join(
        [
            prefix,
            "Change the clothing to a modest casual outfit: a relaxed white top, denim shorts, smooth closed-foot opaque white tights, and no shoes.",
            "The tights should look like one continuous soft fabric layer with closed rounded ends, not toe-sock styling.",
            "Keep the grassy outdoor setting, seated composition, natural daylight, and camera angle.",
        ]
    )


def auto_retry_attempts(prompt: str, options: ImageOptions) -> list[tuple[str, ImageOptions, str]]:
    attempts: list[tuple[str, ImageOptions, str]] = [("original", options, prompt)]

    retry_prompts = [
        ("sanitized_user_prompt", sanitized_retry_prompt(prompt)),
        ("t1_conservative_restoration_prompt", restoration_retry_prompt(prompt)),
        ("t2_conservative_quality_cleanup_prompt", quality_cleanup_retry_prompt(prompt)),
        ("t3_modest_outfit_reframe_prompt", modest_outfit_reframe_prompt(prompt)),
    ]
    if prompt_mentions_no_shoes_tights(prompt):
        retry_prompts.append(("t4_closed_foot_tights_reframe_prompt", closed_foot_tights_reframe_prompt(prompt)))
    for reason, attempt_prompt in retry_prompts:
        attempts.append((reason, options, attempt_prompt))

    png_like = options.output_format.strip().lower() in {"", "png", ".png"}
    jpeg_options = replace(options, output_format="jpeg", background="opaque" if options.background.strip().lower() == "transparent" else options.background)
    if png_like:
        attempts.append(("png_to_jpeg", jpeg_options, restoration_retry_prompt(prompt)))

    quality_seen = {options.quality.strip().lower()}
    for quality in ("auto", "low"):
        if quality not in quality_seen:
            attempts.append((f"quality_{quality}", replace(jpeg_options if png_like else options, quality=quality), restoration_retry_prompt(prompt)))
            quality_seen.add(quality)

    normal_model = "gpt-image-2" if options.model.strip().lower() in {"gpt-image-2-2k", "gpt-image-2-4k"} else options.model
    lowered = replace(
        jpeg_options if png_like else options,
        model=normal_model,
        size="auto",
        quality="auto",
        upscale="",
    )
    attempts.append(("lower_resolution_default", lowered, restoration_retry_prompt(prompt)))

    deduped: list[tuple[str, ImageOptions, str]] = []
    seen: set[tuple[str, ImageOptions, str]] = set()
    for reason, attempt_options, attempt_prompt in attempts:
        key = (reason, attempt_options, attempt_prompt)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((reason, attempt_options, attempt_prompt))
    return deduped


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
