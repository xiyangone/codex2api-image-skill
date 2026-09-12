# CLI 命令与维护

在 skill 目录运行，使用锁定依赖：

```powershell
Set-Location 'C:\Users\21218\.codex\skills\codex2api-image'
uv run --locked codex2api-image --version
uv run --locked codex2api-image generate --help
```

## 配置与公共选项

- `--base-url` / `CODEX2API_BASE_URL`：明确选择服务，默认 `http://127.0.0.1:8080/v1`。
- `--env-file` / `CODEX2API_IMAGE_ENV_FILE`：选择配置文件，默认 skill 的 `.env`；明确指定但不存在会报错。
- `CODEX2API_API_KEY`：进程值优先于文件。`--api-key` 也可显式覆盖，但不要把密钥放进命令历史或文档。
- 不读取通用 `OPENAI_API_KEY/OPENAI_BASE_URL`，避免与其他服务混配。
- `--timeout`：默认 900 秒，限制一次 HTTP 请求及其重试预算。同步 generate/edit 仍共享请求与下载预算；异步 job 不再将它当作排队、执行、下载的总时长。
- `--auto-retry`：明确启用有限失败恢复；`--max-attempts` 默认 3，包含首次请求。不开启时只有一次请求。
- `--dry-run`：适用于 generate、edit、job submit/run 和 batch；完全不需要密钥，不联网、不创建输出文件。

图像参数：`--model`、`--size`、`--quality`、`--output-format png|jpeg|webp`、`--background auto|opaque`、`--style`、`--clean-background`。
同步专用：`--response-format b64_json|url`、`--moderation auto|low`、JPEG/WebP 的 `--output-compression 0..100`。不默认指定 moderation。
Job 专用：`--n 1..4`、`--upscale 2k|4k`、`--strict-size/--no-strict-size`、`--upscale-fit pad|cover`。不支持的跨路由参数会报错，不再静默忽略。

## 模型目录与同步请求

```powershell
uv run --locked codex2api-image models
uv run --locked codex2api-image generate --prompt 'A small orange cat by a sunny window' --model gpt-image-2.5-flare --quality xhigh --size 1024x1024 --out 'G:\out\cat-new.png'
uv run --locked codex2api-image edit --prompt 'Replace only the background with clean light gray' --image 'G:\in\source.png' --model gpt-image-2.5-sunburst --quality high --out 'G:\out\edited-new.png'
```

重复 `--image` 添加参考图，最多 16 张，每张本地 / data URL 图片不超过 20 MiB。第一张是编辑目标。`--manifest 'G:\in\references.json'` 可传入角色元数据：

```json
[
  {"index": 0, "filename": "source.png", "role": "source", "label": "编辑目标"},
  {"index": 1, "filename": "face.png", "role": "identity", "label": "脸部参考"}
]
```

索引从 0 开始，必须唯一且对应输入图。元数据不改提示词；每张参考图的实际用途仍需在用户提示词中写清楚。

## 精确尺寸、多图与续查

```powershell
uv run --locked codex2api-image job run --prompt 'A quiet landscape wallpaper' --model gpt-image-2.5-flare --size 1920x1080 --n 2 --strict-size --upscale-fit pad --out 'G:\out\wallpaper-new.png'
uv run --locked codex2api-image job submit --prompt 'A quiet landscape' --model gpt-image-2.5-sunburst --n 2
uv run --locked codex2api-image job status 123
uv run --locked codex2api-image job wait 123 --out-dir 'G:\out\job-123-new'
uv run --locked codex2api-image job download 123 --out-dir 'G:\out\job-123-recovered'
```

`job run/wait/download` 必须明确指定 `--out` 或 `--out-dir`，两者不能并用。单张使用给定文件名；多张与 `--out` 配合时保存为 `stem-001.ext`、`stem-002.ext` 等。`--out-dir` 使用经过目录边界校验的服务端文件名。所有续查和下载使用创建任务时的同一 API Key。

- `job status` 只查询一次：输出 job_id、status、terminal、requested_outputs、completed_outputs、asset_count 及警告/错误，不下载图片或输出提示词、参考图、密钥元数据和原始 params_json。
- `job wait` 只轮询现有任务，成功后下载。失败或取消时停止，报告已有资产数量及恢复入口，不自动下载失败结果。
- `job download` 只读取已结束任务并下载现有资产，不轮询、不重新提交。对 succeeded 正常验收；对 failed/cancelled，即使图片完整保存，仍返回 1，报告 `ok=false`、原状态和错误。活跃任务不下载，避免拿到仍变化的结果集。
- 等待超时后可先 `job status`，再 `job wait`；下载失败或需要取回失败/取消任务的图片时用 `job download`。这些操作均不发起新的生成。部分文件已存在时指定新的输出名或目录，不覆盖已有产物。

### 独立等待预算与进度

| 选项 | 默认值 | 范围 |
|---|---|---|
| `--timeout` | 900 秒 | 提交、查询及其重试的请求预算；单次轮询请求还上限 30 秒；资源请求同时受剩余下载预算限制 |
| `--queue-timeout` | 900 秒 | 本次调用等待任务开始的预算，不消耗执行预算 |
| `--execution-timeout` | 900 秒 × requested_outputs | 首次观察到 running 后的等待预算；显式值是整个任务的预算，不再乘张数 |
| `--download-timeout` | 900 秒 | 整组资产共用的独立下载预算，不按文件重置 |
| `--poll-interval` | 2 秒 | 两次状态查询间隔 |
| `--progress` / `--no-progress` | 开启 | 仅在状态或数量变化时向 stderr 输出一行 JSON |

排队/执行选项用于 job run/wait；下载选项也用于 job download。所有时间参数必须为正数且有限；batch 使用相同选项，仅作用于其中的 job 行，每个任务分别计时。

```powershell
uv run --locked codex2api-image job wait 123 --queue-timeout 1800 --execution-timeout 3600 --download-timeout 600 --no-progress --out-dir 'G:\out\job-123-resumed'
```

进度行包含 `event: job_progress`、job_id、status 和两项输出数量，不污染 stdout 的最终 JSON。并发 batch 进度按 job_id 区分，最终结果仍按输入顺序。启用进度时 stderr 是 JSON Lines 流，不应整体当作单个 JSON 解析。

客户端预算不改变服务端任务：超时或 Ctrl+C 中断等待后保留 job ID，无取消请求、无重提交；再次 wait 会建立新的本地等待预算。不要将服务端允许的最长排队时间理解为客户端必须一直等。

## 批量任务

```json
{
  "jobs": [
    {"mode": "generate", "prompt": "A small orange cat", "model": "gpt-image-2.5-flare", "quality": "high", "out": "cat.png"},
    {"mode": "edit", "prompt": "Replace only the background", "images": ["G:/in/source.png"], "out": "edited.png"},
    {"mode": "job", "prompt": "A quiet wallpaper", "size": "1920x1080", "n": 2, "strict_size": true, "upscale_fit": "pad", "out": "wallpaper.png"}
  ]
}
```

```powershell
uv run --locked codex2api-image batch --input 'G:\batch.json' --out-dir 'G:\out\batch-new' --dry-run
uv run --locked codex2api-image batch --input 'G:\batch.json' --out-dir 'G:\out\batch-new' --concurrency 2
```

省略 mode 时，有图片为 edit，无图片为 generate。合法 mode 只有 generate、edit、job。输出必须是目录内的相对名称，不能使用绝对路径、`..` 或重复目标。

整批先校验提示词、图片、参数与输出路径，再发送请求；空批次或任一输入不合法时不提交任何任务。结果按输入顺序排列。每行可设置 JSON 布尔值 `auto_retry`、`clean_background` 以及 `input_images_manifest`；未知字段会报错。

## 下载与校验

```powershell
uv run --locked codex2api-image asset save --url '/p/img/123?exp=...&sig=...' --out 'G:\out\asset-new.png'
uv run --locked codex2api-image info 'G:\out\asset-new.png'
```

保存全部返回图片。若响应格式与指定扩展名不符，使用实际格式对应的扩展名并报告真实路径；不会重新编码图片。文件和 data URL 均须通过完整图片解码校验。

## 结果与退出码

保存报告包含 `saved[]`、`images[]`（路径、格式、宽高、字节数）、`model`（请求模型）、`requested_n/completed_n`、`warning`；提交/生成还包含尝试记录。Job 另有 job_id、status、terminal、requested_outputs、completed_outputs、asset_count。`completed_n` 是实际保存数，不能与服务端完成数混为一谈。

- `0`：保存命令成功且输出完整；成功的 dry-run、提交受理也为 0。status 的 0 表示查询正常且未报告失败/警告，queued/running 仍未完成。
- `1`：请求 / 配置 / 文件失败、部分成功、job 尺寸不符、服务端警告或失败/取消任务恢复。已保存文件保留。
- `2`：CLI 参数解析错误。

命令异常为 stderr 中的 JSON 错误行；状态查询结果、批量结果及已有图片恢复报告在 stdout JSON 中。失败任务下载成功也不是生成成功，不要只看 HTTP 200、文件存在或单一 ok 字段，需同时核对状态和数量。

## 项目依赖维护

运行依赖只有 Pillow（解码验证）；开发依赖统一放在 `dependency-groups.dev`，不再维护 dev extra。Python 要求 `>=3.11`；更新项目依赖不自动升级全局 uv 或 Python。

v0.4.0 只更新项目版本和任务处理，不增加依赖；锁定版本保持 Pillow 12.3.0、Pyright 1.1.413。下方升级命令是单独维护流程，不是运行 skill 的前置步骤。

以下维护命令会修改锁文件或虚拟环境，执行前遵守用户的写入范围确认：

```powershell
uv lock --upgrade
uv sync --locked --group dev
uv run --locked python -B -m unittest discover -s tests -v
uv run --locked pyright
```

需要只读查看升级候选时使用 `uv lock --upgrade --dry-run`。普通调用使用 `uv run --locked`，不应临时安装未声明依赖。
