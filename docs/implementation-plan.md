# Neobund CLI 与 Codex Skill

目标：通过 Neobund 企业版 OpenAPI 查询授权及商品、上传本地视频、提交带货视频任务、查询结果。面向用户已经明确的 Neobund 集成场景；不实现 TikTok OAuth、不创建 AI 视频、不进行真实发布测试。

设计：Python 3.11+ 标准库，JSON 输出，CLI 和可独立安装的 Skill 同目录打包。从 NEOBUND_API_KEY 环境变量或权限 600 的本地密钥文件读取凭证。HTTP 与业务状态均检查，禁止自动重试写请求和携带凭证的重定向。发布默认本地预览，--execute 真正调用；使用本地提交日志阻止同一批次在结果未知时被盲目重试。接收带时区的 ISO 时间并转 UTC。

备选：curl 包装更小，但分片上传及 JSON 大整数不易可靠处理；Node 需要额外解决 int64 精度。选择 Python。

- [x] 先写行为测试：默认预览、批量字段与 UTC 校验、int64、HTTP/业务错误与密钥遮蔽、模糊提交结果不重试、分片边界。
- [x] 实现 skill/neobund-publish/scripts/neobund.py 入口、client.py 传输、publishing.py 校验与日志、uploads.py 上传。
- [x] 编写 README、可运行示例及简短 SKILL.md；提供个人 Skills 安装器，不覆盖已有文件。
- [x] 26 项本地 HTTP/替代传输测试及 CLI 冒烟通过；Skill 格式校验通过。无真实 API 联调。

独立审阅发现并已修复：截断 HTTP 响应统一处理；precheck=0 默认值归一防止切换单条/批量绕过防重；安装失败回滚。日志只保证当前机器同一批次防重，不是跨机器或跨批次幂等。

验收：Codex 能从帮助和 Skill 找到命令，用本地文件生成定时发布参数；明确执行后仅提交一次，返回 taskIds 并区分任务提交与实际发布成功。

依据：https://www.neobund.ai/docs/openapi-v1（本会话已读取，2026-09-23）。Official Account 与自家商品的 Neobund 支持仍须真实账号验证，CLI 不宣称已验证。
