---
name: codex2api-image
description: Generate, edit, batch, verify, and download raster images through the user's local codex2api service with API-key authentication. Use for local http://127.0.0.1:8080 image workflows, /v1/images/generations, /v1/images/edits, /v1/images/jobs async tasks, gpt-image-2 / gpt-image-2-2k / gpt-image-2-4k, explicit size/quality/output controls, or when the user says codex2api image instead of Codex built-in image generation. Do not use for Codex native image_gen.
---

# Codex2API Image

Use the exe-backed local codex2api runtime as source of truth. The source tree is only reference material; do not assume the user is running from `src`.

## Hard Boundaries

- Do not use Codex built-in `imagegen` for codex2api image requests.
- Use API Key mode only, with explicit key sources from this skill's runtime configuration.
- Prefer the running service at `http://127.0.0.1:8080` and the skill-local `.env`.
- Surface raw HTTP status and response body before guessing.
- Do not add hidden prompt templates. Send the user's prompt directly unless the user asks for rewriting or explicitly uses `--clean-background`.
- Do not use retry prompt rewrites unless the user explicitly enables `--auto-retry` or a batch row sets `"auto_retry": true`.
- Interpret English or Chinese "no background" phrasing as a plain clean background, not transparency.
- Do not request or send transparent background; describe a clean/plain background in the prompt instead.
- Verify saved files with `info` before reporting dimensions. Never call 2K/4K true unless dimensions prove it.
- Treat `upscale=2k|4k` as server-side post-save resizing unless actual output proves model-native high resolution.
- Do not perform local image post-processing in this skill. Use codex2api API routes only.

## Runtime

```powershell
cd C:\Users\21218\.codex\skills\codex2api-image
uv run codex2api-image models
```

`.env` for this skill:

```dotenv
CODEX2API_BASE_URL=http://127.0.0.1:8080/v1
CODEX2API_API_KEY=
```

If `.env` exists but `CODEX2API_API_KEY` is blank, fail explicitly.

## Route Choice

Default to the smallest direct API-key route that matches the task. Do not prefer the image studio/job route unless the user asks for it or direct API repeatedly fails.

| Need | Command | Endpoint |
|---|---|---|
| Text-to-image, synchronous | `generate` | `POST /v1/images/generations` |
| Reference edit, synchronous | `edit` | `POST /v1/images/edits` |
| Parallel direct-API batch | `batch --concurrency N` with `mode=edit` or `mode=generate` | Concurrent direct API calls |
| Backup long-running task | `job run` | `POST /v1/images/jobs` then `GET /v1/images/jobs/:id` |
| Save a signed asset URL from a job | `asset save` | signed `/p/img/...` URL |
| Batch work | `batch --concurrency 3` | Per job: generate/edit/job |
| Verify file dimensions | `info` | local file inspection |

## Examples

Synchronous generation:

```powershell
uv run codex2api-image generate `
  --prompt "A small orange cat sitting on a cloud" `
  --model gpt-image-2 `
  --size 1024x1024 `
  --quality high `
  --out G:\images\cat.png
```

Reference edit:

```powershell
uv run codex2api-image edit `
  --prompt "Replace the background with a plain clean light gray background" `
  --image G:\images\source.png `
  --model gpt-image-2 `
  --out G:\images\edited.png
```

Explicit conservative background cleanup:

```powershell
uv run codex2api-image edit `
  --prompt "remove the background" `
  --image G:\images\source.png `
  --model gpt-image-2 `
  --clean-background `
  --out G:\images\clean-bg.png
```

Backup async job:

```powershell
uv run codex2api-image job run `
  --prompt "Improve sharpness only; keep the layout and composition unchanged" `
  --image G:\images\source.png `
  --model gpt-image-2-4k `
  --quality high `
  --upscale 4k `
  --out G:\images\job-result.png
```

Use `job run` as a backup path only. For normal parallel tests, use direct API batch rows:

```powershell
uv run codex2api-image batch `
  --input G:\images\batch.json `
  --out-dir G:\images\out `
  --concurrency 6
```

Explicit failure-recovery retries:

```powershell
uv run codex2api-image edit `
  --prompt "Change only the shoes; preserve everything else" `
  --image G:\images\source.png `
  --model gpt-image-2-4k `
  --quality high `
  --output-format png `
  --auto-retry `
  --out G:\images\edited.png
```

Verify output:

```powershell
uv run codex2api-image info G:\images\job-result.png
```

## References

Read only the relevant reference:

- `references/commands.md` for the full CLI contract and batch JSON.
- `references/api-map.md` for endpoint responsibilities and why multiple image APIs exist.
- `references/prompting.md` for minimal prompt handling, background wording, white-tights wording, and 422 prompt-frame recovery.
- `references/troubleshooting.md` for raw-error handling and verification rules.
