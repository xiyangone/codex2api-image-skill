# Prompting

Keep prompts small and literal. Do not add a hidden preservation or style template unless the user asks for one.

By default, the CLI sends the prompt unchanged. Use `--clean-background` only when the user explicitly wants the conservative plain-background rewrite. Use `--auto-retry` only when the user wants failure recovery to rewrite prompts and downgrade risky parameters.

## Background wording

- English or Chinese "no background" phrasing means a clean plain background, not transparency.
- Prefer wording such as: `Use a plain clean light background with no clutter.`
- Do not request transparent output or set transparent background fields.

## Explicit clean-background mode

`--clean-background` wraps the user prompt with a conservative edit objective:

- use a plain clean light background with no clutter;
- for image edits, change only the background;
- preserve the primary subject, pose, clothing, crop, lighting, and facial/body details;
- avoid transparency, text, watermarks, extra people, props, or scenery;
- keep the original prompt under `User request: ...`.

Batch rows can enable it with `"clean_background": true`. A row value of `false` disables a global `--clean-background` flag for that row.

## Explicit auto-retry mode

`--auto-retry` keeps the first attempt unchanged. Only after failure does it try:

- a sanitized user prompt that removes high-risk wording while preserving safe visual intent;
- T1 conservative restoration:
  `Perform a conservative photo restoration pass on the provided reference. Keep the scene, composition, pose, clothing, background, and lighting direction unchanged. Apply only non-destructive cleanup: reduce visible compression artifacts and keep natural exposure.`
- T2 conservative quality cleanup:
  `Apply a conservative quality cleanup to the provided reference. Keep the same scene, composition, pose, clothing, background, and lighting. Make no semantic content changes; do not redraw or restyle the scene. Keep natural color and exposure while reducing visible artifacts only.`
- T3 modest outfit reframe:
  `Using the provided reference, make a complete modest casual outfit edit rather than a localized body-area edit. Keep the same setting, composition, pose, natural daylight, and camera angle. Use everyday clothing language and keep unrelated scene details unchanged.`
- T4 closed-foot white tights reframe, only when the user requests white tights with no shoes:
  `Change the clothing to a modest casual outfit: a relaxed white top, denim shorts, smooth closed-foot opaque white tights, and no shoes. The tights should look like one continuous soft fabric layer with closed rounded ends, not toe-sock styling.`
- only for non-policy technical failures, technical fallbacks: PNG to JPEG, lower quality (`auto`, then `low`), lower resolution/default model settings.

Every retry attempt is reported in JSON with model, size, quality, output format, background, and error text.

For `422 image_output_rejected`, `response does not contain data[0]`, or refusal text such as `Sorry, I can't...`, change the prompt frame before changing format, quality, or size. Format/size fallbacks help transient or technical failures; they do not fix policy refusals. If the refusal mentions `nude`, `nudity`, `explicit`, `minor`, `underage`, or `nsfw`, stop immediately and do not try further prompt or parameter fallbacks.

Soft wording such as `sexualized`, `non-sensitive`, `non-explicit`, `legs`, `visible legs`, `feet`, `footwear`, `lower half`, or `body-related` often means the prompt framed a safe clothing edit as a localized body edit. For those, do not start by changing PNG/JPEG, quality, or model size. Reframe to a complete modest outfit edit first.

Hard wording such as `nude`, `nudity`, `explicit`, `minor`, `underage`, or `nsfw` is a hard stop for retries.

Avoid retry prompt phrases that repeatedly triggered refusals in practice:

- `upscale this specific image`
- `edit this particular image`
- `preserve identity`
- `preserve the original person`
- `preserve face`
- `preserve body shape`
- `version`, `rendition`, or `final render` in fallback prompts

Do not put `4K`, `upscale`, `high-resolution finished version`, `new version`, `rendition`, or `production-quality render` into retry prompts. Use model/quality/upscale parameters for API resolution changes.

Do not use production/artwork/final-render reframing as an automatic fallback for sensitive reference edits. It can turn a conservative enhancement request into a semantic redraw and may increase rejection risk.

## Reference edits

- Use the first image as the edit target.
- Extra images are references only when the user clearly says so.
- Ask for one edit objective per request when possible.
- For fragile human edits, preserve only the invariants the user actually requested.

## Clothing and white tights edits

Observed safer frame for the user's reference-edit workflow:

- Prefer full outfit wording: `Change the clothing/outfit to a modest casual outfit...`
- Use `opaque white tights` for white tights.
- For no-shoes white tights, use `smooth closed-foot opaque white tights`, `continuous soft fabric layer`, and `closed rounded ends`.
- For phone wallpapers, use `portrait-oriented phone wallpaper` and `size=1024x1792`, then verify actual dimensions with `info`.
- Do not make the edit about a localized body area.

Avoid these prompt terms in retry templates unless the user explicitly requires them:

- `lower-leg`
- `leg area`
- `feet`
- `footwear area`
- `body`
- `tights only`
- `stockings only`
- `change only the legs`
- `barefoot`
- `preserve identity`
- `preserve face`
- `preserve body shape`

Stable white-tights prompt:

```text
Change the clothing to a modest casual outfit: a relaxed white top, denim shorts, opaque white tights, and clean white sneakers. Keep the same grass background, seated pose, natural daylight, and camera angle.
```

Stable no-shoes white-tights phone-wallpaper prompt:

```text
Create a portrait-oriented phone wallpaper from the reference. Change the clothing to a modest casual outfit: a relaxed white top, denim shorts, smooth closed-foot opaque white tights, and no shoes. The tights should look like one continuous soft fabric layer with closed rounded ends, not toe-sock styling. Keep the grassy outdoor setting, seated composition, natural daylight, and camera angle.
```
