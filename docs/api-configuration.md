# 模型与 API 配置

跟踪 [Issue #28](https://github.com/plussea/deepblue/issues/28)。支持 DeepSeek 预设与 OpenAI-compatible Chat Completions；Responses 和 Anthropic Messages 不是同一协议，本阶段不支持。

## Web 使用

打开“模型与 API 设置”，可新增多组连接并修改名称、模型 ID、API 地址和超时。每组连接对应一个模型，可为同一供应商保存多组模型连接。手动填写模型 ID；尚无远程模型列表发现。

- DeepSeek 预设增加 thinking disabled；通用兼容接口不发送该参数。
- 地址接受 API 根路径（例如 `https://example.com/v1`）或完整 `/chat/completions` 地址，不重复拼接。
- 高级设置可选 `max_tokens` / `max_completion_tokens`，并决定是否请求流式 Token 用量。缺失用量仍遵循原有预算停止规则，不将未知用量当零。
- Key 默认不回显，留空保存表示保持原值。点击“显示 Key”经当前本机鉴权接口读取，可切回隐藏；关闭对话框清空浏览器输入值。清除按钮删除该连接保存的 Key。
- 修改地址而未重新输入 Key，会清除旧凭据，避免将旧供应商 Key 带到新地址。显式重新输入或显示后的值视为本次提交内容。
- “测试连接”会发一次真实模型请求；不等于已验证工具调用能力。普通自动化回归使用本机模拟服务。

配置及选择状态自动保存到项目会话存储下的 `api-profiles.json`，重启恢复，项目之间隔离。Windows 使用当前用户 DPAPI 加密 Key；换用户/换机器后可能无法解密，不静默降级明文。其他系统使用受限目录及文件权限（700/600），Key 未加密；本轮实测平台为 Windows。

默认连接兼容 DEEPSEEK_* / LLM_* 环境变量，环境值优先且界面显示来源。环境中的 Key 不自动复制落盘；若要完全通过 Web 管理，请“新增 API”保存独立连接。新连接不继承默认环境 Key。旧版本只有内存配置，没有可迁移的配置文件，需保存一次；已运行旧服务的内存 Key 不可跨进程自动获取。

## 运行边界

后续任务采用选定连接的模型（包括已有会话的下一次执行）；历史消息不改写。每次运行记录实际模型、接口类型和地址，不记录 Key。执行中及同服务队列的配置/凭据保持冻结；重启后恢复队列按原连接查找 Key，地址或协议已变化则不复用。不得把会话最初的模型名当作每次运行的实际模型。

配置列表和常规响应不包含 Key，明确的显示接口受同源/本机 Token 检查且禁止缓存。配置文件不通过文件预览接口提供；本机工具的 trusted 模式仍不是系统级沙箱。

## CLI

```powershell
$env:LLM_API_KEY = "供应商密钥"
python -m deepblue --provider openai-compatible --base-url https://example.com/v1 --model model-id
# 参数不兼容时按供应商文档选择：
# --token-parameter max_completion_tokens --no-stream-usage
```

CLI 当前仍使用启动参数/环境变量，不读取 Web 项目连接库。Web 支持最多 32 个连接；暂不提供删除连接、OAuth、自定义认证头或无 Key 模式。

## 设计依据

采用 pi 的供应商/模型配置思想及 Cline 的基础字段界面，保留显式协议选择。参考：[pi 本地模型说明](../../pi/packages/coding-agent/docs/models.md)、[OpenCode providers](https://opencode.ai/docs/providers/)、[Cline OpenAI Compatible](https://docs.cline.bot/provider-config/openai-compatible)、[Continue OpenAI 配置](https://docs.continue.dev/customize/model-providers/top-level/openai)。

本轮纳入 GitHub 源码同步；不代表发布新版本。完整 Python 回归 192 项通过，收尾针对性回归 27 项通过，浏览器回归 25 组通过（包括多连接保存、切换、Key 显隐及刷新恢复）。本地 Web 已更新。日志位于 `.test-tmp/providers-full.log`、`.test-tmp/providers-final.log` 和 `.test-tmp/providers-browser.log`。测试使用模拟接口，未调用付费模型。
