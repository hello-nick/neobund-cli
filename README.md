# Neobund CLI + Codex Skill

通过 Neobund OpenAPI 查询账号和商品、上传本地视频、提交带货视频发布/排期任务、查询结果。Python 3.11+，无第三方依赖。文档依据：[Neobund OpenAPI v1](https://www.neobund.ai/docs/openapi-v1)，核对日期 2026-09-23。

## 安装与配置

```bash
python3 install.py
~/.local/bin/neobund configure
~/.local/bin/neobund doctor
```

安装器会将完整 Skill 复制到 `${CODEX_HOME:-~/.codex}/skills/neobund-publish`，创建 `~/.local/bin/neobund`；不会覆盖已有文件。需要更新时先检查已有安装，或用 `--skill-root` 和 `--bin-dir` 指定新的目录。若 PATH 包含 `~/.local/bin`，后续可直接运行 `neobund`。新任务可发现 `$neobund-publish`；当前任务也可以直接读取安装后的 SKILL.md 使用。

`configure` 在自己的交互终端读取密钥，输入不回显，保存到 `~/.config/neobund/api-key`，文件权限 600。不要把 Key 粘贴到 Codex 对话。也支持 `NEOBUND_API_KEY` 环境变量（优先）或 `NEOBUND_API_KEY_FILE` 指定密钥文件。配置文件方便桌面 Codex 与终端共用；只在某个终端 export 的变量不一定会传递到桌面应用。

要求 Neobund **企业版主账号**。授权账号须已在 Neobund 绑定，本工具不提供 OAuth 注册流程。可用 `NEOBUND_BASE_URL` 配置 Neobund 为你提供的 HTTPS API 域名；默认 `https://open.neobund.ai`。

也可免安装运行：

```bash
python3 skill/neobund-publish/scripts/neobund.py --help
```

## 从视频到发布

以下数字为示例，必须替换。查询只返回当前页，使用 `--page` 和 `--page-size` 翻页。

```bash
# 查账号：读取 authId，核对 username、quotaStatus=1、subAccountId 为空
neobund auth list --username your_tiktok_name

# 同步是异步任务；提交后稍后再查询，不能把 syncStatus=2 当作同步完成
neobund products sync --auth-id 101 --execute
neobund products list --auth-id 101

# 上传先预览文件；加 --execute 后上传，返回 data.fileId
neobund upload --file /absolute/video.mp4
neobund upload --file /absolute/video.mp4 --execute

# 本地预览：无需 API Key，不调用任何 API
neobund publish --auth-id 101 --product-id 749000000001 \
  --product-title 'Blue shirt' --title 'New arrival #fashion' \
  --file-id 2001001 --at '2027-08-01T18:00:00+08:00' --precheck

# 明确执行：返回 data.taskIds。省略 --at 会尽快发布
neobund publish --auth-id 101 --product-id 749000000001 \
  --product-title 'Blue shirt' --title 'New arrival #fashion' \
  --file-id 2001001 --at '2027-08-01T18:00:00+08:00' --precheck --execute

neobund tasks get --task-id 5001
neobund tasks wait --task-id 5001 --timeout 120
```

可加 `upload --cover /absolute/cover.jpg` 上传封面。视频/封面单文件不超过 500 MiB。先将文件传到 S3，再由 Neobund 登记视频资产，发布不接受任意视频 URL。已有 AI 资产可使用 `publish --ai-video-id ID --ai-task-no VIDEO_TASK_NO` 代替 `--file-id`，工具不创建 AI 视频。

## 批量任务与结果

```bash
neobund tasks submit --file examples/tasks.json
neobund tasks submit --file /absolute/reviewed-tasks.json --execute
neobund tasks list --auth-id 101 --product-id 749000000001
```

顶层只能为 `{"tasks":[...]}`，支持 1～100 条带货任务；参数错误整批拒绝。批量 JSON 使用 API 原始字段；`scheduledReleaseTime` 是 UTC 的 `yyyy-MM-dd HH:mm:ss`。单条命令的 `--at` 接受带偏移的 ISO 时间并转换为 UTC。不要将商品/授权 ID 经过 JavaScript `number` 转换；Python 保留 int64 精度。

CLI 正常输出为 JSON；错误 JSON 输出到 stderr。退出码：0 成功，2 参数/网络/API 错误，3 等待超时（任务仍可能继续），4 已查到任务发布失败，130 用户中断。

任务状态：100 已提交、200/250 预检中、300 等待发布、350 发布中、500 发布完成、800 失败/终止。**返回 taskIds 只意味着提交成功，状态 500 才是发布完成。** 定时发布时间不是平台最终展示时间保证。

## 防止重复发布

每次执行发布前，CLI 将请求写入 `~/.local/state/neobund-cli/submissions`，用原子创建防止相同批次同时发送；可通过 `NEOBUND_STATE_DIR` 改变位置。相同批次（即使 JSON 字段顺序不同）再次提交会被阻止。

网络断连、超时、响应异常时，记录为 `unknown`；进程中断时可能保留 `sending`。先检查返回的 receipt 文件，再 `tasks list` 按账号/商品/文案核查，必要时重复读取（列表可能短暂延迟）。不要通过删除日志、调整备注或更换 `--submission-id` 来盲目重发。只有明确需要另一条独立的同参数发布时，使用新的 `--submission-id`。这只是本机同批次防重，不是 Neobund 服务端幂等或跨机器/跨批次去重。

写请求均不自动重试。上传分片失败停止；上传登记请求超时先运行 `neobund assets` 核查已上传资产。分片失败产生的未完成上传由 Neobund/S3 生命周期处理。

## 在 Codex 中使用

配置密钥后可以说：

> 使用 $neobund-publish，列出我的 TikTok 授权账号和商品。

> 使用 $neobund-publish，把 /absolute/video.mp4 发布到账号 @myshop，挂商品 749000000001，商品链接标题 Blue shirt，视频文案 New arrival。安排在 2027 年 8 月 1 日北京时间 18:00，并开启预检。

Skill 会根据请求查询、上传、预览和提交；明确的发布指令不需要重复确认。只要求预览时不会提交；目标账号、商品、文件、文案或时区不明确时会询问。

Neobund 文档没有明确保证 **Official Account＋自家店铺商品** 的完整支持，本工具也尚未用真实账号验证。实际账号能否授权、商品能否同步、发布能否成功，需 Neobund 确认或真实联调；工具不会将其误写成已验证能力。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 skill/neobund-publish/scripts/neobund.py tasks submit --file examples/tasks.json
```

测试使用本地临时文件及替代传输，不上传或发布真实内容。
