# Commands

Run from the skill directory:

```powershell
cd C:\Users\21218\.codex\skills\codex2api-image
uv run codex2api-image <command>
```

Global API options are available on API commands:

- `--base-url` overrides `CODEX2API_BASE_URL`.
- `--api-key` overrides `CODEX2API_API_KEY`; do not print it.
- `--env-file` selects a dotenv file.
- `--timeout` defaults to `900`.

Image commands also support `--clean-background` and `--auto-retry`. Both are explicit opt-in only: the default behavior sends the prompt unchanged. Use `--clean-background` for conservative plain-background cleanup, not for transparent output. Use `--auto-retry` only when failure recovery should try conservative prompt rewrites, JPEG fallback, lower quality, and lower resolution. Policy refusals stay on prompt-frame retries and do not fall through to technical format/quality/size downgrades.

Default to direct API routes. For parallel testing, use `batch --concurrency N` with normal `edit` or `generate` rows; this runs direct API calls concurrently and keeps each row's output path and retry log separate. Use `mode=job` only as a backup for long-running tasks or when direct API is unstable.

## `models`

```powershell
uv run codex2api-image models
```

## `generate`

```powershell
uv run codex2api-image generate `
  --prompt "..." `
  --model gpt-image-2 `
  --size auto `
  --quality auto `
  --output-format png `
  --background auto `
  --moderation low `
  --out G:\out\image.png
```

Use `--dry-run` to print the JSON payload without sending.

## `edit`

```powershell
uv run codex2api-image edit `
  --prompt "..." `
  --image G:\in\primary.png `
  --image G:\in\reference.png `
  --model gpt-image-2 `
  --out G:\out\edited.png
```

`--image` accepts local image paths, `file://`, HTTP(S), and `data:image/...;base64,...`.

`gpt-image-2*` uses its own image-input fidelity behavior. Do not pass old input-fidelity fields.
Use prompt text such as "plain clean background" for requests like "no background"; do not request transparent output.
Use `--clean-background` only when a more conservative plain-background rewrite is wanted.
Use `--auto-retry` only when failures should be retried with a recorded fallback sequence.

Explicit clean-background mode:

```powershell
uv run codex2api-image edit `
  --prompt "remove the background" `
  --image G:\in\primary.png `
  --clean-background `
  --out G:\out\clean-bg.png
```

Explicit auto-retry mode:

```powershell
uv run codex2api-image edit `
  --prompt "Change only the shoes; preserve everything else" `
  --image G:\in\primary.png `
  --model gpt-image-2-4k `
  --quality high `
  --output-format png `
  --auto-retry `
  --out G:\out\edit-retry.png
```

## `job`

Submit only:

```powershell
uv run codex2api-image job submit --prompt "..." --model gpt-image-2-4k
```

Wait and save an existing job:

```powershell
uv run codex2api-image job wait 123 --out G:\out\job.png
```

Submit, wait, and save. This is a backup route, not the default path:

```powershell
uv run codex2api-image job run `
  --prompt "..." `
  --image G:\in\primary.png `
  --model gpt-image-2-4k `
  --upscale 4k `
  --out G:\out\job.png
```

## `asset save`

Use for signed asset URLs returned as `proxy_url`:

```powershell
uv run codex2api-image asset save `
  --url "/p/img/123?exp=...&sig=..." `
  --out G:\out\asset.png
```

## `batch`

```powershell
uv run codex2api-image batch --input G:\batch.json --out-dir G:\out --concurrency 3
```

`--concurrency` defaults to `3`. Results are printed in input order. If any row fails, already-started rows finish and successful outputs remain saved; failed rows are returned as JSON entries with `"ok": false`. Direct API parallelism is implemented here; it does not require `/v1/images/jobs`.

Batch JSON:

```json
{
  "jobs": [
    {
      "mode": "generate",
      "prompt": "A small orange cat",
      "model": "gpt-image-2",
      "size": "1024x1024",
      "quality": "high",
      "out": "cat.png"
    },
    {
      "mode": "edit",
      "prompt": "Replace the background",
      "images": ["G:/in/source.png"],
      "clean_background": true,
      "out": "edit.png"
    },
    {
      "mode": "job",
      "prompt": "High resolution pass",
      "input_images": ["G:/in/source.png"],
      "model": "gpt-image-2-4k",
      "upscale": "4k",
      "auto_retry": true,
      "out": "job.png"
    }
  ]
}
```

If `mode` is omitted, rows with images use direct API `edit`; rows without images use direct API `generate`.

Batch rows may set `"clean_background": true` for the explicit conservative prompt wrapper, or `false` to disable a global `--clean-background` flag for that row. Batch rows may also set `"auto_retry": true` to enable fallback retries, or `false` to disable a global `--auto-retry` flag for that row.

Batch rows must not include old input-fidelity fields. Output filenames must be unique after joining with `--out-dir`.

## `info`

```powershell
uv run codex2api-image info G:\out\image.png
```

Reports local kind, width, height, and bytes.
