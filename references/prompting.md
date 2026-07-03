# Prompting

Keep prompts small and literal. Do not add a hidden preservation or style template unless the user asks for one.

By default, the CLI sends the prompt unchanged. Use `--clean-background` only when the user explicitly wants the conservative plain-background rewrite.

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

## Reference edits

- Use the first image as the edit target.
- Extra images are references only when the user clearly says so.
- Ask for one edit objective per request when possible.
- For fragile human edits, preserve only the invariants the user actually requested.
