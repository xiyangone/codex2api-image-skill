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

Image commands also support `--clean-background`. It is explicit opt-in only: the default behavior sends the prompt unchanged. Use it for conservative plain-background cleanup, not for transparent output.

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

Explicit clean-background mode:

```powershell
uv run codex2api-image edit `
  --prompt "remove the background" `
  --image G:\in\primary.png `
  --clean-background `
  --out G:\out\clean-bg.png
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

Submit, wait, and save:

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

`--concurrency` defaults to `3`. Results are printed in input order. If any row fails, already-started rows finish, then the command reports the failing job index.

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
      "out": "job.png"
    }
  ]
}
```

If `mode` is omitted, rows with images use `edit`; rows without images use `generate`.

Batch rows may set `"clean_background": true` for the explicit conservative prompt wrapper, or `false` to disable a global `--clean-background` flag for that row.

Batch rows must not include old input-fidelity fields. Output filenames must be unique after joining with `--out-dir`.

## `info`

```powershell
uv run codex2api-image info G:\out\image.png
```

Reports local kind, width, height, and bytes.
