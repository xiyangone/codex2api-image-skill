# API Map

接口核对基线：[codex2api v2.9.5](https://github.com/james-6-23/codex2api/releases/tag/v2.9.5)，tag commit `d680021707b5062db20c0009bde16ca664ad4848`。本地可能包含独立修改，运行版本、目录、账号权限需实时区分。

任务生命周期另按 2026-09-11 的本地图片队列扩展核对；这是 v2.9.5 上的本地功能，不是新的上游发布版本。

## 路由职责

| 路由 | 认证 | 职责 |
|---|---|---|
| GET /health | 服务健康检查 | 存活和汇总状态，不证明模型权限 |
| GET /v1/models | API Key | 当前 key 可见的模型目录；不是全部内置模型清单，不证明真实出图可用 |
| POST /v1/images/generations | API Key | 同步文生图 |
| POST /v1/images/edits | API Key | 同步编辑；JSON `images[].image_url` |
| POST /v1/images/jobs | API Key | 异步生成 / 编辑；返回 202 与数值 `job.id` |
| GET /v1/images/jobs/:id | 创建时的同一 API Key | 任务状态和资产；其他密钥返回 404 |
| /p/img/... | 签名 URL | 下载资源，不附带 API Key |

Batch 是 CLI 的并发编排，不是独立的服务端 Batch API。

`job status/wait/download` 都只读取 `GET /v1/images/jobs/:id`，download 随后读取现有资源。公开 `/v1` 尚无 job list/cancel 路由；取消仅在管理端和图片门户提供，CLI 不借用这些接口或升级认证权限。

## 本地队列与状态契约

- 接收时校验配额，取得账号和 key 的并发容量后才开始执行；queued 不占用执行并发位。核对时在途任务总上限 128、每个 key 32，排队上限 24 小时，执行超时按每张 12 分钟计算。这些是该本地服务端的实现值，不写入客户端的固定权限或永久默认。
- 状态为 queued → running → succeeded/failed/cancelled。重启只恢复未执行的受管队列项；已运行或来源不明的任务不自动重放。客户端同样不因轮询、下载或服务重启而再次 POST。
- GET 响应包含 `job.id`、`status`、`requested_outputs`、`completed_outputs` 和 `assets`。客户端校验查询 ID 一致性，使用进度字段；缺少 requested_outputs 时从 params_json.n 读取请求数，不拿实际保存数量猜测请求数量。
- succeeded 也可能有 warning 或数量不足；failed/cancelled 也可能保留资产。状态、数量、实际文件分别报告，恢复资产不改变任务终态。
- 客户端采用独立且有限的请求、排队、执行、下载预算，具体参数见 commands.md；计时/等待中断不发送取消请求。

模型目录由当前 key 的账号范围、分组、通道与模型允许/拒绝策略共同筛选。目录缺少图片模型不能证明源码不支持图片；需报告可见范围限制，不能自动放开权限或用真实生成请求探测账号。

## 参数映射

| 参数 | 同步 generate/edit | 异步 job |
|---|---|---|
| prompt/model/quality/output_format/background/style | 支持 | 支持 |
| size | Image 2 系列须为 16 倍数；不自动取整 | 保留准确画布；严格尺寸默认开启，服务端适配上游尺寸 |
| n | CLI 仅接受 1；使用 batch 或 job 生成多张 | 1–4 |
| response_format/moderation/output_compression | 支持；compression 只用于 JPEG/WebP | 不传递，显式使用时报错 |
| upscale/strict_size/upscale_fit | 不支持；尺寸档位使用模型别名 | 支持；fit 为 pad 或 cover |
| 输入图 | images 数组 | input_images 数组 |
| 参考图元数据 | input_images_manifest | input_images_manifest |

GPT Image 2 系列的上游尺寸总像素不超过 8,294,400，长短边比例不超过 3:1。Job 的准确画布以服务端生成结果为准，若超分或尺寸保证未落实，应保留图并报告警告，不能在客户端偷偷修图。

## Image 2.5 与文本驱动

支持 `gpt-image-2.5-flare`、`gpt-image-2.5-sunburst`、日期快照及 `-2k/-4k` 别名。2.5 质量档为 auto / low / medium / high / xhigh / max；默认模型仍是 gpt-image-2。

图像模型进入服务端图片工具；调用工具的文本驱动是服务端职责。v2.9.5 的驱动优先级为系统设置 → CODEX_IMAGES_MAIN_MODEL → 内置默认。CLI 不增加一个无效的图像请求字段来覆盖后台文本驱动，也不自行改全局设置。

## 错误与任务结果

- v2.9.5 的瞬时限流、调度队列满等可能返回 429/503；Retry-After 并非总会出现。HTTP 状态、错误 code/type 与实际响应头共同决定能否有限退避；已受理的 queued 任务只继续读取原 ID。
- 401/403、明确内容拒绝和 POST 的未知结果不能自动重发。服务端已进行调度 / 重试，客户端不再堆叠提示词或降档重试链。
- Job 保存 `error_kind`、`status_code`、`upstream_body` 和成功警告 `warning`。成功状态仍可能只完成部分图片。
- 默认查询不加 `include_cache=1`；避免下载未使用的 Base64 缓存。已返回的 cache_b64_json 可用于保存，否则下载 proxy_url/url。
- 资产中 requested_size 与 actual_size 是诊断元数据；最终报告仍以本地文件完整解码得到的宽高为准。

图片 token 计费和后台用量统计由服务端维护。CLI 可保留同步响应中的 usage，不根据模型名自行计算或承诺费用。
