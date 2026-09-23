---
name: neobund-publish
description: 当用户要通过 Neobund 查询 TikTok 授权账号或商品、上传视频、提交或排期带货视频任务、查询发布结果时使用。
---

# Neobund 带货视频

执行本 Skill 目录下的 `scripts/neobund.py`（Python 3.11+）。若 `neobund` 已安装到 PATH，也可直接使用。先运行 `--help` 和相关子命令 `--help` 获取字段；所有正常输出为 JSON。

## 接入

- `doctor` 只检查本地配置。凭证优先使用 `NEOBUND_API_KEY`，其次 `NEOBUND_API_KEY_FILE` 指向的文件，默认 `~/.config/neobund/api-key`（权限 600）。缺少凭证时请用户在自己的终端运行 `neobund configure`，不要让用户把密钥贴进对话。
- Neobund 要求企业版主账号。此工具消费已有授权，不创建 OAuth 授权；账号尚未绑定时，需要先在 Neobund 完成授权。
- 默认 API 域名 `https://open.neobund.ai`；仅当 Neobund 为用户提供其他域名时配置 `NEOBUND_BASE_URL`。

## 工作流

1. `auth list --username NAME`：确定发布账号的 `authId`。核对 username、`quotaStatus=1`、`subAccountId` 为空。不要把多条结果中的第一条当作用户选择；分页结果不能当成完整列表。
2. `products list --auth-id ID`：查询该账号的商品，必要时用 `--title` 或 `--product-id`。若需要更新，执行 `products sync --auth-id ID --execute`，同步是异步的，稍后重新查询。商品必须属于选定授权。
3. 本地视频先 `upload --file /absolute/video.mp4 [--cover /absolute/cover.jpg]` 预览；用户已授权上传/发布该文件时加 `--execute` 上传，保存返回的 `data.fileId`。上传不等于发布。
4. 用 `publish` 生成预览，核对账号、商品、视频、文案和时区。用户只要求准备/预览时止于预览；已明确要求按这些参数发布时直接加 `--execute`，无需重复确认。缺少或歧义的账号、商品、文件、文案、时间应先问清，不能猜。
5. 返回 `taskIds` 只代表任务提交成功；用 `tasks get --task-id ID` 或 `tasks wait --task-id ID --timeout 120` 查询。状态 500 才报告发布完成，并给出 `tiktokVideoId`；800 表示失败/终止，读取 `errorMessage`。

示例（ID 和文案须替换成用户实际选择）：

```bash
neobund publish --auth-id 101 --product-id 749000000001 \
  --product-title 'Blue shirt' --title 'New arrival #fashion' \
  --file-id 2001001 --at '2027-08-01T18:00:00+08:00' --precheck
```

`--at` 必须带 UTC 偏移，CLI 自动转 UTC；按商家指定时区解释，不能把美国时间当作北京时间。省略 `--at` 会尽快发布。批量用 `tasks submit --file /absolute/tasks.json`，顶层 `{"tasks":[...]}`，1～100 条；字段参见 [官方文档第 9 节](https://www.neobund.ai/docs/openapi-v1#section-9)。批量时间字段 `scheduledReleaseTime` 已经是 UTC，格式 `yyyy-MM-dd HH:mm:ss`。批量同样默认预览，`--execute` 才提交。

## 故障及边界

- 发布提交会在 `~/.local/state/neobund-cli/submissions`（或 `NEOBUND_STATE_DIR`）留下记录。超时、断连、响应异常时结果可能未知：查记录、用 `tasks list` 按账号/商品/文案核查，考虑列表复制延迟。不要删除记录或更换 `--submission-id` 来盲目重发；新 ID 仅用于用户明确要求的另一条独立发布。多机器不共享本地防重记录，它不是服务端幂等保证。
- 分片上传失败不自动重试；上传登记请求超时先用 `assets` 检查。S3 签名 URL 不得携带 NB-API-Key，也不要输出完整签名 URL。
- 使用 Python 保留 int64 ID，勿经过 JavaScript `number` 转换。
- 文档统称“达人授权”，未明确保证 Official Account 自卖商品场景。不能仅凭授权/商品查询成功承诺能发布；实际发布结果为准。不能把 authType=3 的店铺授权用于该视频接口；带货视频使用 authType=1。
- 不添加 AI 生成、自动选品、批量扩大发布范围或永久监控任务。只完成用户指定的发布工作。
