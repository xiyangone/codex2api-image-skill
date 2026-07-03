# Troubleshooting

## Always surface raw failures first

Report:

- HTTP status and response body.
- Timeout or connection error.
- Auth failure such as `missing_api_key`.
- `no_available_account`.
- Safety or prompt-filter response.
- Saved path and actual dimensions if an image was produced.

## Authentication

This skill uses API Key mode only:

1. Process env `CODEX2API_API_KEY`.
2. Process env `OPENAI_API_KEY`.
3. Skill-local `.env` `CODEX2API_API_KEY`.
4. Skill-local `.env` `OPENAI_API_KEY`.

If `.env` exists but the key is blank, stop and say it is blank.

## Service checks

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
uv run codex2api-image models
```

`GET /health` proves the exe is alive. `models` proves API-key auth works.

## Size and upscale

- `gpt-image-2-2k` and `gpt-image-2-4k` are model IDs exposed by this local service when available.
- `upscale=2k|4k` on async jobs is post-save resizing.
- Use `info` on the saved file before reporting any resolution claim.
- If upstream reports unsupported input-fidelity, remove old CLI/manual fields. Current CLI does not expose that option.
- If a user says "no background", prompt for a clean plain background rather than transparent output.
- For repeated `server_error` or missing image output, retry explicitly with `--auto-retry`. The retry log shows which prompt/format/quality/size fallback succeeded or failed.
- For `422 image_output_rejected`, do not expect JPEG, quality, or size fallbacks to fix the request. The CLI tries prompt-frame fallbacks first and stops on hard sensitive refusals.
- Treat `sexualized`, `non-sensitive`, `non-explicit`, `legs`, `visible legs`, `feet`, `footwear`, `lower half`, and `body-related` as soft prompt-frame failures unless the error also mentions hard-stop terms. Reframe localized edits as complete modest outfit edits.
- Treat `nude`, `nudity`, `explicit`, `minor`, `underage`, and `nsfw` as hard-stop terms for automatic retries.
- Treat `503 account_pool_usage_limit_reached` and `402 deactivated_workspace` as account/workspace availability failures, not prompt failures.
- If output looks blurrier than the source, compare actual pixel dimensions first. Do not use this skill for local image post-processing.

## Async jobs

Use `job run` only as a backup for long tasks or when the user explicitly asks for the image studio/job route. Default parallel testing should use direct API `batch --concurrency N`.

`job run` submits `POST /v1/images/jobs`, polls `GET /v1/images/jobs/:id`, then downloads the signed asset URL from the completed job.

If a job completes without assets, report the raw job JSON status/error.
