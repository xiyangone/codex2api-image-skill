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

## Async jobs

Use `job run` for long tasks. It submits `POST /v1/images/jobs`, polls `GET /v1/images/jobs/:id`, then downloads the signed asset URL from the completed job.

If a job completes without assets, report the raw job JSON status/error.
