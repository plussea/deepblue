# Skill 与 Command 实施计划及使用说明

跟踪 [Issue #26](https://github.com/plussea/deepblue/issues/26)。本阶段按顺序实现目录注册、Agent 加载、命令展开、CLI/Web 入口及回归。当前本地实现，尚未提交或推送。

## 目录与规则

用户级 `~/.deepblue/skills/<name>/SKILL.md`、`~/.deepblue/commands/<name>.md`；项目级 `<项目>/.deepblue/skills/<name>/SKILL.md`、`<项目>/.deepblue/commands/<name>.md`。项目同名覆盖用户级，同作用域重名报诊断；保留 CLI 内置命令名。名称为小写字母开头的字母、数字、下划线、连字符，最长 64 字符。

每目录最多 128 项，每文件最多 64 KiB；拒绝链接/junction，错误文件跳过并在目录 API 中返回诊断。只扫描声明文件，不导入 Python。项目 `.deepblue` 已被 Git 忽略，需要共享时自行选择明确的版本管理方式。

## Skill

示例 `.deepblue/skills/review/SKILL.md`：

```markdown
---
name: review
description: 审查代码并运行相关检查
---
先阅读目标及上下文，再报告具体问题；按需读取 reference.md。
```

元数据为简单的 `key: value` 单行格式，不是完整 YAML；支持 name、description、skills，后者供命令使用。无头部时名称来自文件/目录名。模型每轮看到技能摘要目录，通过 `load_skill(name)` 按需加载正文；用户可以在任务中明确要求使用某技能。`load_skill(name, resource="reference.md")` 读取目录内 UTF-8 资源，拒绝绝对路径、上级路径和链接；有同样的大小限制。

正文在 Agent 创建时形成快照；工具返回正文/资源的 SHA256，普通工具记录保存加载结果。资源在调用时读取并记录哈希。命令直接引用的技能正文进入用户消息，重试沿用已落盘展开文本。压缩可能总结旧正文，需要时再次加载，不能声称永久保留原文。

技能是任务方法，不提高权限；脚本需显式通过现有 shell 工具执行，read-only/workspace 模式仍拒绝 Shell。技能目录可读取声明资源，但不因此开放任意用户目录。当前没有安装市场、启停 UI 或热加载；CLI 新建 Agent/新进程加载，Web 每个 worker 加载。

## Command

示例 `.deepblue/commands/review.md`：

```markdown
---
description: 审查指定路径
skills: review
---
审查 {{1}}，用户原始参数为：{{args}}
```

输入 `/review "src/my file.py"`。`{{1}}`、`{{2}}` 等是位置参数，`{{args}}` 是整段原始参数。单双引号组合参数，反斜杠按原样保留，避免破坏 Windows 路径；不支持反斜杠转义引号。缺失参数、未闭合引号、未知命令/技能报错。替换单次完成，不递归、不执行 Shell 插值。多个技能以逗号分隔，按顺序追加正文；展开结果有 64000 字符限制。

CLI 提供 `/skills`、`/commands` 查看目录，内置命令优先。Web 输入 `/` 查询当前项目命令，方向键选择、Tab/Enter 补全，Escape 关闭；补全后 Enter 发送，Shift+Enter 换行。输入法组合期间不触发补全。菜单内容通过 textContent 展示。

命令在实际 Agent 执行时展开；队列等待期间修改命令会影响后续任务使用的版本，并非入队冻结。当前项目 worker 的 steering 使用该 Agent 的目录快照。对无效命令，worker 报错，不发起模型请求；不要据此认为已有文件操作被撤销。

## 验证与剩余范围

186 项完整 Python 回归通过；后补资源读取、路径逃逸拒绝、命令落盘测试共 6 项针对测试通过。23 组浏览器回归通过，包括自定义命令目录、键盘补全及引用 Skill 后发送。没有真实模型调用、提交或推送。后续可根据使用反馈增加更完整参数声明、目录热刷新和 Skill 管理面板；这些不属于本轮最小闭环。


Skill / Command 收尾核对（2026-10-02）：186 项完整 Python 回归、后补 16 项针对回归、23 组浏览器回归通过（计数有重叠，不累计）。日志 `.test-tmp/skills-tests.log`、`.test-tmp/skills-browser.log`，浏览器报告 `.test-tmp/web-e2e-bbc6f44d/report.json`。本地 Web 已启动最新源码，目录 API 与 commands.js 健康检查通过。未付费调用模型、未提交/推送，#26 保持开放等待同步。


### 内置命令与 Skill Creator（2026-10-02）

开箱提供 `/review` 审查、`/explain` 解释、`/fix` 修复、`/test` 检查、`/plan` 规划及 `/create-skill` 创建技能。例如 `/review src/`、`/create-skill 为 Python 项目创建测试规范技能`。`/review`、`/explain`、`/plan` 默认不修改文件；`/test` 默认运行相关检查，不自动修复。

`/create-skill` 引用内置 `skill-creator`，生成兼容 DeepBlue 简单元数据格式的 Skill，默认写入项目 `.deepblue/skills`，使用现有工具和权限。默认能力随 Python 包分发，优先级为项目 > 用户 > 内置；CLI 控制命令名仍保留。

17 项针对回归通过，覆盖默认发现、命令展开、技能引用及覆盖规则。通用 Skill validator 缺少 PyYAML 未运行，DeepBlue 实际加载器验证通过；未安装依赖或调用付费模型。代码未提交/推送，跟踪 [Issue #26](https://github.com/plussea/deepblue/issues/26)。服务需重启加载新的默认目录规则。


斜杠菜单更新：输入 `/` 同时列出 Command 与 Skill，以 `/ Command`、`◇ Skill` 标识区分；选择技能补全 `/skill:名称 `，可追加任务后发送并明确加载该技能。8 项能力测试通过；实际 Web 验证六个内置命令、Skill 点击、Tab 补全及 Escape 关闭通过。服务已重启，刷新页面生效。跟踪 #26，未提交/推送。
