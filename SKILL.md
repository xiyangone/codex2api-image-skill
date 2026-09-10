---
name: codex2api-image
description: Generate, edit, batch, download and verify raster images through the user's local codex2api API, including GPT Image 2 and GPT Image 2.5. Use for explicit codex2api workflows rather than Codex native image generation.
---

# Codex2API Image

通过本机 codex2api 的 API Key 接口处理图片。CLI 版本 `0.3.0`，接口核对基线为 [codex2api v2.9.5](https://github.com/james-6-23/codex2api/releases/tag/v2.9.5)。运行中的 exe、模型目录和账号权限仍需分别核对，不把源码版本当作服务可用性证明。

## 必守边界

- 不改用 Codex 原生 imagegen；不管理账号、修改后台设置或部署服务。
- 默认 API 基址 `http://127.0.0.1:8080/v1`，默认读取 skill 的 `.env`。只使用 `CODEX2API_*` 配置，不读取通用 `OPENAI_*` 作为后备；不得回显密钥。
- 默认原样发送提示词。`--clean-background` 和 `--auto-retry` 均须用户明确要求；重试不改提示词、模型、质量或格式。
- 不请求透明背景；“无背景”按干净纯色背景处理。Pillow 只用于解码校验，不在本 skill 内修图、扩图、缩放或重新编码。
- 只保存到新文件；不得覆盖已有产物或逃出指定输出目录。批量输入、代码、脚本和依赖变更仍遵守用户的跨文件确认规则。

## 路由选择

| 需求 | 命令 | 关键约束 |
|---|---|---|
| 单任务文生图 / 参考图编辑 | `generate` / `edit` | 默认路径；同步 GPT Image 2 系列尺寸须为 16 的倍数 |
| 精确画布，例如 1920×1080 | `job run --size 1920x1080` | 保留目标尺寸，服务端处理上游尺寸适配；不在客户端向下取整 |
| 一个任务生成 1–4 张 | `job run --n N` | 同步接口不支持 `n>1`，不能静默忽略 |
| 多个独立任务 | `batch` | 先整批预检，再执行；默认并发 2，账号较少时使用 1–2 |
| 已提交任务的继续查询 / 下载 | `job wait ID` | 使用创建时的同一 API Key，不重新提交任务 |
| 签名资源下载 / 本地检查 | `asset save` / `info` | 不向资源 URL 发送 API Key；完整解码校验后报告实际尺寸 |

首次使用或运行端更新后，检查 `/health` 与 `models`。模型出现在目录中只证明目录可读，不证明账号能出图。没有账号时仅做 dry-run 和离线测试，不自动发起生图探测。

## 模型与尺寸

- 默认仍是 `gpt-image-2`；按用户指定使用 `gpt-image-2.5-flare` 或 `gpt-image-2.5-sunburst`，支持日期快照及 `-2k/-4k` 档位别名。
- `xhigh/max` 用于 Image 2.5；旧 Image 2 不会被客户端静默降档。
- `--model` 是图像模型；文本驱动由服务端配置，本 skill 不修改该全局设置。
- 同步尺寸不合法时直接报错。需要非 16 倍数的准确画布时改用 job；最终必须核对保存文件的物理尺寸。

## 最短流程

```powershell
Set-Location 'C:\Users\21218\.codex\skills\codex2api-image'
uv run --locked codex2api-image models
uv run --locked codex2api-image generate --prompt 'A small orange cat' --model gpt-image-2.5-flare --quality high --out 'G:\images\cat-new.png'
uv run --locked codex2api-image info 'G:\images\cat-new.png'
```

先核对参数时加 `--dry-run`：它不读取认证配置、不联网、不写图片。编辑预演只输出参考图占位符。生成 / 编辑 / job / batch 的详细示例见 [命令说明](references/commands.md)。

## 失败与验收

- `--auto-retry` 只对可恢复读取错误，或带有效 `Retry-After` 的 429/503，执行有限退避；默认最多 3 次。401/403、明确拒绝和结果未知的 POST 超时均不自动重发。
- 已取得 job ID 后只轮询原任务。等待或下载失败时报告 job ID，续查原任务；不要通过再次生成来恢复下载。
- 保存后输出 `saved[]`、每张图的格式 / 宽高 / 字节数、请求模型和数量；`info` 使用相同完整解码检查。随后目视验收内容。
- 部分成功、服务端警告或 job 精确尺寸不符均保留已保存文件并返回非零退出码；不能报告“全部完成”。
- 错误报告保留路由、HTTP 状态、错误类型、脱敏响应体、重试记录和已保存路径。不得把账号、限流、文本驱动问题当成提示词问题。

## 按需参考

- [commands.md](references/commands.md)：CLI、批量 JSON、依赖维护和退出码。
- [api-map.md](references/api-map.md)：同步 / 异步参数、模型与资源职责。
- [prompting.md](references/prompting.md)：原意保留和参考图角色。
- [troubleshooting.md](references/troubleshooting.md)：v2.9.5 限流、错误分类、续查与验证边界。
