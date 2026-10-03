<h1 align="center">DeepBlue · 深蓝</h1>

<p align="center">
  一个轻量、可读、可扩展的 Python 终端 Coding Agent。
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#web-工作台">Web 工作台</a> ·
  <a href="#工具">编码与搜索工具</a> ·
  <a href="#会话">会话恢复</a> ·
  <a href="#开发与测试">开发与测试</a>
</p>

<p align="center">
  <strong>Python ≥ 3.10</strong> · <strong>DeepSeek API</strong> · <strong>无第三方运行时依赖</strong>
</p>

---

深蓝是一个在本地项目目录中工作的编码助手。你描述任务，它通过 DeepSeek 决定读取哪些文件、如何修改代码、运行什么命令，再根据真实的工具结果继续处理。

第一版专注于一个闭环：**阅读代码 → 修改代码 → 运行测试 → 根据结果修复 → 汇报结果**。

当前版本 **v0.7.2** 在 v0.7.1 的项目内存储修复基础上，加入 **浅色简约 Web 界面、任务错误与压缩结果的持久展示**，并补齐真实 DeepSeek Web 验收。支持会话管理、Markdown、文件引用、Git 差异与验收日志。CLI 与 Web 共享同一套 Agent 和本地会话，保持 Python 标准库后端和单 Agent。

项目参考 [pi](https://github.com/earendil-works/pi) 的小核心、少量工具、模型驱动工作方式，使用 Python 独立实现。Web 布局参考本地 `pi-web` 的会话、对话与文件面板，独立实现；仍保持单 Agent，不引入插件系统或复杂终端界面。

## 目录

- [快速开始](#快速开始)
- [Web 工作台](#web-工作台)
- [模型与认证](#模型与认证)
- [交互模式](#交互模式)
- [工具](#工具)
- [程序化验收](#程序化验收)
- [会话](#会话)
- [项目说明](#项目说明)
- [命令行参考](#命令行参考)
- [配置](#配置)
- [工作原理](#工作原理)
- [项目结构](#项目结构)
- [开发与测试](#开发与测试)
- [更新记录与 Issue](#更新记录与-issue)
- [运行边界](#运行边界)
- [常见问题](#常见问题)
- [后续方向](#后续方向)

---

## 快速开始

需要 **Python 3.10 以上**，建议使用 Python 3.12。Windows 还需要 `pwsh` 或 Windows PowerShell；Linux/macOS 使用 `/bin/sh`。

### 安装

进入本项目目录，使用满足版本要求的 Python 创建虚拟环境。以下 `python` 应指向 Python 3.10 以上：

```powershell
cd deepblue
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

如果已有 `uv`，也可以让它选择 Python 3.12：

```powershell
cd deepblue
uv venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -e .
```

Linux/macOS 的安装命令：

```bash
cd deepblue
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

包名为 `deepblue-agent`，命令名和 Python 模块名均为 `deepblue`。当前从本地源码安装，不依赖项目已发布到 PyPI。

### 配置 API Key 并启动

Windows PowerShell：

```powershell
$env:DEEPSEEK_API_KEY = "你的 DeepSeek API Key"
.\.venv\Scripts\deepblue.exe
```

Linux/macOS：

```bash
export DEEPSEEK_API_KEY="你的 DeepSeek API Key"
.venv/bin/deepblue
```

在虚拟环境激活后，可以直接使用 `deepblue` 或 `python -m deepblue`。后文示例假定已经激活虚拟环境，或将 `deepblue` 替换为对应的完整可执行文件路径。

进入对话后输入：

```text
你 > 先阅读这个项目，介绍主要模块。
你 > 找出测试失败的原因，修改代码，再运行测试验证。
```

对另一个项目工作：

```powershell
.\.venv\Scripts\deepblue.exe --cwd "E:\projects\my-app"
```

只执行一个任务并退出：

```bash
deepblue -p "阅读项目，说明入口文件和主要模块"
```

## Web 工作台

安装后，在设置好 API 环境变量的终端运行：

```powershell
deepblue-web --cwd "E:\projects\my-app"
# 或：python -m deepblue.web --cwd "E:\projects\my-app"
```

打开 **http://127.0.0.1:30142**。默认只监听本机，可用 `--port 30143` 更换端口；Web 默认存储在项目内 `.deepblue`（避免受限环境无法写入用户目录）；`--home` 或 `DEEPBLUE_HOME` 可显式覆盖。CLI 默认仍为用户目录 `~/.deepblue`；需为两者指定同一存储目录才能共享会话。服务启动时会验证目录可写，并显示存储路径。支持 `--model`、`--base-url`、`--timeout`，沿用 DeepSeek/LLM 环境变量；Web 默认网络操作超时 30 秒。

- 浅色界面：白色对话区、浅灰侧栏；底栏常驻累计 Token 与压缩次数。
- 左侧：会话分页、标题/内容搜索、新建；重命名、归档/恢复、MD/JSON 导出收纳在对话区“会话操作”菜单。运行时可浏览其他会话。
- 中间：常用 Markdown、代码复制/基础高亮、工具卡片、继续请求、压缩与指定任务停止。草稿按项目/会话保存在浏览器，可清除。
- 右侧：目录与文件标签切换，最多保留 12 个文件标签；正文占满面板剩余高度，支持行号/查找/刷新。拖动面板左边缘调整宽度，右上角放大/还原，Esc 退出放大；窄屏覆盖式阅读可关闭返回对话。输入 `@` 或点击“引用文件”将相对路径交给 Agent 读取。
- 输入栏：Enter 发送，Shift+Enter 换行；中文输入法确认候选时不发送。文本框随内容增高，验收命令和清除草稿收在“＋”菜单；保留底部 Token 与压缩计数。文件标签本次页面内保留，刷新后重新打开。
- “状态”：用量、上下文、验收证据与分页日志、实时工具输出、过期证据和恢复核对。
- “修改”：只读 Git 状态、暂存/未暂存/未跟踪差异，区分任务开始前已有修改；非 Git 项目显示已记录的文件操作。
- 展开输入框下的“验收设置”可配置验收命令或清除草稿；失败后最多修复一次。默认单任务最多 30 个模型轮次，Shell/验收超时 120 秒。

**配置 Key**：可使用启动环境变量，也可在左下角“DeepSeek 设置”输入本次服务使用的 Key。环境管理的配置在网页中锁定；网页输入的 Key 仅留在服务端内存，不回显、不写磁盘或浏览器存储，重启后需重新输入。新任务冻结设置，既有会话沿用原模型。“测试连接”会显式调用一次模型 API。

页面通过带 Token 的 SSE 接收事件，连接失败时退避并可回退轮询；连续失败后显示“重新连接”，不会自动重新提交任务。全局只执行一个任务，重复 request_id 返回原任务。服务重启把未结束任务标为中断，协作通知残留 worker 停止并核对会话，不重放未知工具。

停止不会回滚已发生的修改。历史按页加载，事件缓存最多 2000 条，缺口通过有界任务快照同步，原始记录仍保存在 JSONL。网页文件/日志访问有路径边界，Agent 的 Shell 仍按当前用户权限运行；本机来源校验不是公网账号认证。

任务结束后的错误和压缩结果会保留，刷新或重启后仍可查看；工具失败展示在对应工具卡片。短会话若压缩后更大，会保留原文，压缩次数不会增加。

详见 [Web 使用说明](docs/web.md)、[v0.7.2 验收记录](docs/v0.7.2.md)、[v0.7](docs/v0.7.md)。

## 模型与认证

目前只支持 **DeepSeek Chat Completions API**，直接通过 Python 标准库发送 HTTPS 请求。

| 项目 | 默认值 |
| --- | --- |
| API 地址 | `https://api.deepseek.com` |
| 模型 | `deepseek-flash` |
| 认证 | `DEEPSEEK_API_KEY` 环境变量 |
| 思考模式 | 关闭 |
| 输出模式 | 默认流式，文本到达即显示；`--no-stream` 可关闭 |

默认模型名依据 [DeepSeek 官方快速开始](https://api-docs.deepseek.com/)；请求和工具消息格式参考 [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)。模型可用性以你的 API 账户为准，不在代码中硬编码模型白名单。

```bash
deepblue --model deepseek-v4-pro
```

模型名称可配置，当前固定关闭思考模式，不保证兼容仅支持思考模式的旧模型或第三方代理的协议差异。

API Key 不写入配置文件或会话元数据，也不会传入 `shell` 子进程的环境。程序不会自动读取 `.env`。会话会保存实际对话和工具结果，因此不要在提示或命令输出中放入密钥。

## 交互模式

启动时展示版本、模型、工作目录和会话路径。执行期间显示模型调用轮次、工具名称、修改 diff、命令结果和日志路径。

```text
DeepBlue 深蓝 v0.7.2 · deepseek-flash
工作目录：...
会话：...

你 > 修复 add 函数，并执行测试
[深蓝 · 第 1 轮] 等待 DeepSeek…
[read] ...
[edit] ...
[shell] ...
```

上面是交互格式示意，不是预录的模型执行结果。实际工具顺序由模型决定。

| 命令 | 功能 |
| --- | --- |
| `/help` | 查看命令帮助 |
| `/new` | 创建新会话，保留旧记录，重新加载项目说明 |
| `/status` | 查看模型、目录、历史与活动消息数、上下文字节数、累计 token 和压缩次数 |
| `/recovery` | 只读核对已记录操作、未知结果和文件变化 |
| `/compact [N]` | 手动压缩旧历史，默认保留最近 2 个用户轮次；N 为 1–20 |
| `/paste` | 开始多行输入；单独一行 `/send` 提交，`/cancel` 取消 |
| `/retry` | 继续待处理的模型请求；不会直接重放历史工具调用 |
| `/task` | 查看当前任务目标、笔记和程序维护的运行状态 |
| `/exit`、`/quit` | 保存现有记录并退出 |
| `Ctrl+C` | 取消当前任务；在输入提示处清空本次输入 |
| EOF | 退出；Windows 通常为 `Ctrl+Z` 后回车 |

普通输入为逐行提交，需要粘贴多段代码或需求时使用 `/paste`：

```text
你 > /paste
... 请重构下面这段代码：
... def add(a, b):
...     return a + b
... /send
```

默认通过 SSE 逐段展示回复，工具参数收齐且响应完整结束后才执行。中途断流或取消时，已显示的片段不作为完整回复保存，工具不执行；可以用 `/retry` 重试。当前不支持执行中插入新指令或完整 TUI 编辑器。

## 程序化验收

通过命令行显式提供检查，不自动执行仓库文件声明的验收命令：

```powershell
deepblue -p "修复失败的测试" --verify "python -B -m unittest discover -s tests" --verify-timeout 120 --verify-repairs 1
```

| 参数 | 行为 |
| --- | --- |
| `--verify COMMAND` | 模型结束后由程序运行检查；当前支持一个 Shell 命令 |
| `--verify-cwd PATH` | 执行目录，默认 `--cwd`；相对路径基于工作区，须在工作区内 |
| `--verify-timeout N` | 每次检查超时，默认 120 秒 |
| `--verify-repairs N` | 失败后最多修复次数，默认 1，范围 0–10；共享 `--max-steps` 模型调用预算 |
| `--verify-not-applicable REASON` | 显式标记纯解释等任务不适用代码验收，与 `--verify` 互斥 |

执行状态 `finished` 仅说明模型本轮结束。验收状态独立记录：`unverified` 未配置、`not_applicable` 显式不适用、`passed` 指定检查通过、`failed` 命令非零退出、`error` 超时或运行异常、`stale` 工作区变化、`cancelled` 用户取消。任意命令的非零退出无法可靠区分依赖缺失与断言失败，需查看日志判断，不能把所有 `failed` 当作测试断言失败。

一次性运行时，配置的验收失败、过期或异常均返回非零退出码；取消返回 130。未配置验收时仍可正常返回 0，但明确显示 `unverified`。`passed` 只代表配置的检查通过，不证明全部需求正确。恢复会话时需重新显式提供验收参数，不从历史记录自动执行命令。

每次运行保存任务 ID、运行 ID、用量与耗时；每次检查另存验证 ID、命令、目录、退出码、日志路径和前后文件 SHA-256 清单。`/status` 会重新核对最近通过记录与当前文件，变化后显示 `stale`；开始新任务会重置本次验收状态。历史证据保留，不代表后续任务也通过。

指纹包含未提交和未跟踪的普通文件，排除会话目录及 `.git`、`__pycache__`、`.venv`、`node_modules`、`.pytest_cache`。当前最多扫描 20,000 个目录项、128 MiB 文件，约 10 秒本地保护；系统 I/O 不提供硬实时保证。链接、联接点或超限会产生 `error`，不会跳过核对后声称通过。检查创建报告或修改源码也会导致 `stale`，宜把测试产物写到工作区外。指纹不是事务快照、完整依赖环境指纹或安全隔离。

### 最小开发评测

先安装项目，再从仓库目录运行。以下命令**会调用真实 API 并产生费用**，必须显式使用 `--live`；本版本没有默认执行批量收费评测。

```powershell
python scripts/evaluate.py --live --mode baseline --tasks add unique clamp mean invoice --max-steps 10 --max-tokens 2048
python scripts/evaluate.py --live --mode verified --tasks add unique clamp mean invoice --max-steps 10 --max-tokens 2048
```

可用 `--repeat 3` 重复运行。每次创建独立工作副本，结果写入 `.test-tmp/evaluations/<运行批次>/report.json`，保存源代码指纹、模型配置、运行证据、成功数/总数、用量和耗时。`false_finished_rate` 的分母是模型正常结束的运行，属于“结束但独立验收未通过”的操作性指标，不是文本语义上的完成声明识别。没有用量的运行会单独计数；Token 统计仅覆盖服务实际返回的用量。

这 5 个任务是公开的人工开发夹具，不是独立保留集或真实仓库基准。独立检查保存在评测器中而非交给 Agent 的工作副本；当前同一用户的 Shell 没有权限隔离，因此不具备对抗篡改保证。后续基准评测仍需外部保管测试或隔离执行。详见 [v0.3 实现与验收](docs/v0.3.md)。

## 工具

深蓝向模型提供九个工具，按模型给出的顺序执行。

| 工具 | 参数 | 行为 |
| --- | --- | --- |
| `read` | `path`、可选 `offset` / `limit` | 读取 UTF-8 文本并显示行号，支持分页 |
| `write` | `path`、`content` | 创建或完整覆盖文件，自动创建父目录 |
| `edit` | `path`、`old_text`、`new_text` | 唯一精确替换，返回统一格式 diff |
| `shell` | `command`、可选 `timeout` | 执行命令，返回退出码、合并输出和日志路径 |
| `find` | `pattern`、可选 `path` / `limit` / `include_hidden` | 按文件名或相对路径 glob 搜索 |
| `grep` | `pattern`、可选 `path` / `glob` / `limit` / `ignore_case` / `include_hidden` | 搜索字面文本，返回文件、行号和片段 |
| `task_update` | 可选 `progress` / `blockers` / `next_step`，至少一项 | 保存模型笔记，每项最多 2000 字符；不能修改目标、执行状态或验收结果 |
| `symbols` | 可选 `path` / `query` / `limit` | Python AST 定义定位，返回限定名、文件与起止行号 |
| `project_checks` | 可选 `path` | 从项目配置发现候选检查命令，只建议、不执行 |

### 文件工具

- 相对路径基于启动时的工作目录；也支持绝对路径。
- `read` 默认读取 300 行，单次最多 2,000 行，返回文本约限制为 32 KiB。截断时返回 `next_offset`，模型可继续读取。
- `read` 不支持图片、二进制文件和超过输出上限的单行；非 UTF-8 内容返回错误。
- `edit` 必须且只能匹配一处原文。匹配不到、重复匹配、新旧文本相同都会报错，不做猜测性修改。
- `edit` 保留 BOM 和未修改部分的换行；替换文本使用文件检测到的换行形式。
- `write` 写入 UTF-8；不会自动沿用被覆盖文件的 BOM。单次写入和编辑文件大小限制为 4 MiB。
- 写入使用同目录临时文件后替换，减少部分写入；不提供与其他编辑器之间的事务隔离。

### 命令工具

- Windows 优先使用 `pwsh`，否则使用 `powershell`；其他系统使用 `/bin/sh`。
- 每次从项目目录开始，不保留前一次命令的 `cd` 或环境变量修改。
- 标准输入关闭，不能执行需要密码输入、确认提示或交互终端的程序。
- 输出写入会话日志；返回末尾最多 32 KiB，终端展示进一步限制为 4,000 字符。
- 默认超时 120 秒；模型可设置更短超时，不能超过 `--shell-timeout`。
- 输出超过约 8 MiB 时终止进程。此限制由轮询检测，实际文件可能略超过阈值。
- 超时和取消时终止进程树。Windows 使用 Job Object 管理子进程；不支持后台常驻服务。
- 非零退出码、未知工具和参数错误都会作为结果交回模型，供下一轮修复。

### 搜索工具

`find` / `grep` 使用 Python 实现，不依赖 `rg` 或系统 Shell。模型可以先定位文件和匹配行，再调用 `read` 阅读完整上下文。

```text
find(pattern="*.py", path="src")
grep(pattern="def complete", path="src", glob="*.py")
```

- `find` 和 `grep.glob` 支持 `*`、`?`、`[]`。不含 `/` 时匹配文件名，含 `/` 时匹配相对路径；使用 Python `fnmatch` 语义，`*` 可以跨目录，前导 `**/` 也匹配根层文件。
- `grep.pattern` 是字面文本，不是正则；`ignore_case=true` 使用 Unicode 大小写折叠。
- 默认跳过隐藏条目和 `.git`、`.venv`、`node_modules`、`__pycache__`、`build`、`dist` 等常见目录。不解析 `.gitignore`。`include_hidden=true` 可以包含隐藏条目，但常见排除目录仍跳过。
- 目录遍历不跟随遇到的符号链接或 Windows junction。无权限文件会跳过；明确指定的文件路径可直接检索。
- 每次默认返回最多 100 条，`limit` 最大 500，结果列表约限制为 32 KiB。命中行最多展示 500 字符，长行截取匹配附近内容。
- `grep` 跳过二进制、非 UTF-8 和超过 4 MiB 的文件。`skipped` 记录跳过数量。
- 遍历超过约 5 秒或 20,000 个条目时停止，并用 `truncated` 和 `reason` 提示缩小范围；这不是严格的文件系统 I/O 超时。

## 会话

会话保存在当前用户目录下，按工作目录隔离：

```text
~/.deepblue/
└── sessions/
    └── <工作目录哈希>/
        ├── <会话 ID>.jsonl
        ├── <会话 ID>.lock
        └── <会话 ID>/
            └── shell-<ID>.log
```

JSONL 以追加方式保存系统提示、用户消息、模型工具调用、工具结果和 API 返回的用量。API Key 不在会话头中。

恢复**当前工作目录最近的会话**：

```bash
deepblue --continue
deepblue --cwd /path/to/project --continue
deepblue --continue -p "继续检查剩余的测试"
```

恢复时沿用会话保存的模型和系统提示；项目 `AGENTS.md` 如有变化，使用 `/new` 重新加载。`--continue` 不存在可恢复会话时会报错，不会悄悄新建。

同一会话只允许一个进程写入。进程退出后操作系统释放锁，磁盘上的 `.lock` 文件可以保留。

**恢复对话不等于恢复命令执行。** 工具执行前先持久化开始事件，结束后保存结果事件，再写入对话。若结果事件已落盘但对话未写入，恢复时补回已知结果；没有完成记录则补充“结果未知”，不自动重放。文件写入可能已生效，恢复报告会比较当前内容、之前内容与已知写入意图；内容一致不等于证明命令完整执行。

`/recovery` 查看核对报告。已观察文件被外部修改后，`write` / `edit` 会要求先重新 `read`，避免直接按过期观察覆盖。Shell 记录 PID、开始时间、目录和日志路径，但恢复不会根据旧 PID 终止或接管进程。只跟踪已观察文件，不是整个工作区的变更监控，也不提供 Shell 幂等、自动回滚或“恰好执行一次”保证。详见 [v0.5 说明](docs/v0.5.md)。

会话尾部未完成的一行可在恢复时截除；已完整提交的行如果损坏则报错。当前没有会话分支或文件回滚。

### 手动压缩上下文

对话变长时，输入 `/compact`。深蓝将旧轮次交给模型生成摘要，同时保留原始系统提示和最近 2 个用户轮次的完整消息组，工具调用与结果不会被拆开。需要进一步缩小时可使用 `/compact 1`。

```bash
# 压缩已有会话并退出
deepblue --continue --compact

# 保留最近一轮，压缩后继续任务
deepblue --continue --compact --keep-turns 1 -p "继续实现剩余功能"
```

压缩会额外调用 DeepSeek API，并计入会话用量。较大历史分段汇总，每段最多约 32,000 字节，单次最多 20 段；不会执行摘要响应中的工具调用。摘要生成完成后只追加一条压缩记录，原始消息留在 JSONL 中，重启后自动重建摘要上下文。

失败或取消时保留原上下文；已经发生的 API 用量仍会记录。轮次不足或摘要不能减少上下文时不写入压缩记录。压缩减少的是发给模型的上下文，**不会减小历史文件**；模型摘要可能遗漏信息，重要事实仍应以原始文件和测试结果为准。

v0.7 可以读取早期会话；新版本的 Web 元数据与原始 JSONL 分开保存。建议用当前版本继续打开包含结构化摘要、操作事件和验收证据的会话。

### 自动压缩与结构化状态

默认活动上下文达到本地字节上限的 80% 时尝试自动压缩，保留最近 2 个完整用户轮次。每次任务最多尝试两次，间隔至少 4 个模型轮次，每次最多 2 个摘要请求；手动压缩仍最多 20 个请求。摘要调用额外计费，单独受上述上限控制，不占原任务 `--max-steps`，但全部计入用量。无可压缩轮次、摘要无收益或失败不会反复重试；仍超出硬上限时停止。

```bash
deepblue --compact-threshold 0.75 --keep-turns 2
deepblue --no-auto-compact
deepblue --summary-format text  # 保留旧文本摘要，用于比较
```

默认 `structured` 摘要包含目标、约束、修改、未解决问题和下一步，模型输出须符合 JSON 结构。程序另从原始记录复制反引号标记、包含“不要/禁止/必须/记住”等词的约束原句、观察过的路径，以及真实验证证据与来源位置。超长约束会拒绝压缩，不静默截掉。该规则不是完整的自然语言语义校验，模型整理字段仍可能遗漏或出错。

超过 8 KiB 的工具结果另存归档，活动上下文保留错误、退出码、关键路径与首尾节选；原始完整结果仍保留在会话。旧 `read` 的文件发生变化后，活动视图会标记过期并要求重读。归档减少发送给模型的内容，不减少磁盘历史。

可运行 `python scripts/context_compare.py` 比较完整历史、旧文本摘要与结构化摘要的本地行为；该脚本使用固定响应，不调用 API，不代表真实模型质量评测。详见 [v0.4 说明](docs/v0.4.md)。

## 项目说明

深蓝创建会话时读取工作目录根部的 `AGENTS.md`，将其作为项目约定加入系统提示。例如：

```markdown
# 项目约定

- 使用 Python 标准库完成可以简单实现的功能。
- 修改代码后运行 python -m unittest discover -s tests -v。
- 不修改生成文件，不主动提交 Git。
```

当前只自动加载根目录文件，大小限制 64 KiB；不会自动遍历父目录或子目录中的规则文件。

## 命令行参考

```bash
# 交互对话
deepblue

# 带初始任务的交互对话
deepblue "介绍这个项目"

# 一次性任务
deepblue -p "修复失败测试，并验证"

# 恢复会话
deepblue -c

# 指定项目和模型
deepblue --cwd /path/to/project --model deepseek-flash

# 调整执行限制
deepblue --max-steps 50 --shell-timeout 300 --timeout 180

# 使用完整回复模式，或压缩旧会话
deepblue --no-stream
deepblue --continue --compact --keep-turns 1

# 帮助与版本
deepblue --help
deepblue --version
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-p`、`--print` | 关闭 | 一次性任务，必须提供任务文本 |
| `-c`、`--continue` | 关闭 | 恢复当前目录最近会话 |
| `--cwd` | 当前目录 | 项目工作目录 |
| `--model` | `deepseek-flash` | 新会话使用的模型 |
| `--base-url` | `https://api.deepseek.com` | API 根地址（可含 `/v1`）或完整 `/chat/completions` 地址 |
| `--home` | `~/.deepblue` | 本地会话存储根目录 |
| `--max-steps` | `30` | 一次任务最多调用模型次数 |
| `--timeout` | `120` | API 网络操作超时秒数 |
| `--shell-timeout` | `120` | 单条命令最大执行秒数 |
| `--max-tokens` | `8192` | 单次模型最大输出 token 数 |
| `--max-context-bytes` | `400000` | 活动上下文序列化后的 UTF-8 字节上限 |
| `--no-stream` | 关闭 | 关闭默认流式输出 |
| `--no-auto-compact` | 关闭 | 禁用自动压缩，仍可手动 `/compact` |
| `--compact-threshold` | `0.8` | 自动压缩字节阈值比例，范围 0.2–0.95 |
| `--summary-format` | `structured` | 结构化摘要；`text` 使用旧文本摘要 |
| `--compact` | 关闭 | 先压缩已有会话，必须与 `--continue` 一起使用；无任务时压缩后退出 |
| `--keep-turns` | `2` | 压缩时保留最近用户轮次，范围 1–20 |

一次性模式：模型回复写到 stdout，运行状态和工具展示写到 stderr。完成返回 `0`；模型错误、轮数上限或不完整输出返回 `1`；配置/启动错误返回 `2`；取消返回 `130`。交互模式允许错误后继续输入，正常退出返回 `0`。

模型不再请求工具时，程序认为这一轮对话结束；这不代表程序独立证明了任务正确，仍应查看实际测试结果。

## 配置

| 环境变量 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 必填，DeepSeek API Key |
| `DEEPSEEK_MODEL` | 新会话默认模型 |
| `DEEPSEEK_BASE_URL` | API 根地址 |
| `LLM_API_KEY` | `DEEPSEEK_API_KEY` 的备用名称 |
| `LLM_MODEL` | `DEEPSEEK_MODEL` 的备用名称 |
| `LLM_BASE_URL` | `DEEPSEEK_BASE_URL` 的备用名称；支持完整 `/chat/completions` 地址 |
| `LLM_TIMEOUT_SECONDS` | API 网络操作超时，默认 120 秒 |
| `DEEPBLUE_HOME` | 会话和日志保存位置 |

新会话的同名配置优先级为：**命令行参数 → `DEEPSEEK_*` → `LLM_*` → 默认值**。`--timeout` 优先于 `LLM_TIMEOUT_SECONDS`。恢复会话固定使用保存的模型，禁止用 `--model` 切换到不同模型。

也可以使用如下 PowerShell 配置（密钥用你自己的值，不要保存进源码）：

```powershell
$env:LLM_BASE_URL = "https://api.deepseek.com/chat/completions"
$env:LLM_API_KEY = "你的 DeepSeek API Key"
$env:LLM_MODEL = "deepseek-v4-flash"
$env:LLM_TIMEOUT_SECONDS = "30"
deepblue
```

默认自动压缩上下文；无法缩减且超过本地字节上限时停止，可用 `/compact` 或 `/new` 处理。字节数不是 token 数，模型服务也可能更早拒绝超长上下文。

## 工作原理

```text
用户任务
   │
   ▼
系统提示 + 历史消息 + 工具定义
   │
   ▼
DeepSeek API ◄──────────────────────┐
   │                               │
   ├── 工具调用 → 校验 → 执行 → 保存结果
   │
   └── 无工具调用 → 显示回复 → 等待输入
```

Agent 核心不读终端输入，通过事件把文本增量、工具状态和错误交给 CLI 展示。模型客户端、执行循环、工具和会话存储分别独立，方便以后添加新界面。

工具调用参数只有在模型响应完整时才会执行。输出截断或异常结束的调用被转换成错误结果，不执行可能残缺的写入。

## 项目结构

```text
deepblue/
├── pyproject.toml
├── README.md
├── src/deepblue/
│   ├── cli.py          # 命令行、交互输入和事件展示
│   ├── config.py       # 配置与校验
│   ├── models.py       # 消息、模型接口和运行结果
│   ├── llm.py          # DeepSeek HTTP 协议适配
│   ├── agent.py        # 模型—工具循环
│   ├── compaction.py   # 结构化/文本摘要与有界压缩
│   ├── recovery.py     # 工具事件、文件核对与过期修改保护
│   ├── verification.py # 程序化验收和工作区指纹
│   ├── web.py          # 本机 HTTP 服务、会话与文件接口
│   ├── web_worker.py   # Web 任务进程与事件输出
│   ├── web_jobs.py     # 持久任务身份、快照与事件
│   ├── web_sessions.py # 只读增量会话索引
│   ├── web_workspace.py # 设置、会话与证据 API
│   ├── web_review.py   # 只读 Git 审阅
│   ├── web_static/     # 中文 HTML/CSS/JS 前端，无构建步骤
│   ├── prompts.py      # 系统提示和根目录 AGENTS.md
│   ├── session.py      # JSONL、压缩视图、用量、文件锁和恢复
│   └── tools/          # 工具协议、读写编辑、命令及进程管理
└── tests/              # 单元、HTTP 协议和本地集成测试
```

## 开发与测试

在安装后的 Python 环境中运行：

```bash
python -m unittest discover -s tests -v
```

也可以不安装包，直接从源码验证：

```powershell
$env:PYTHONPATH = "$PWD/src"
python -m unittest discover -s tests -v
python -m deepblue --help
```

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m deepblue --help
```

v0.7.2 本地回归：**108 项 Python 测试、15 组浏览器场景通过**；包括 401、429、超时、断流后禁止执行工具，以及错误刷新恢复。真实 DeepSeek Web 四组验收通过，覆盖 hi、修复与验收、压缩记忆、停止与刷新；详见 [验收记录](docs/v0.7.2.md)。源码与验收文档随 v0.7.2 提交交付；远程同步状态见对应 Issue。

v0.7.1 提交前完整回归：**107 项 Python 测试通过**（Windows / Python 3.12，ResourceWarning 作为错误），包含 Web 存储权限与默认目录优先级测试。本次未重复调用真实 DeepSeek。

v0.7.0 验收记录：**105 项 Python 回归、10 组浏览器端到端场景通过**，wheel 构建、独立安装、CLI/Web 入口与 8 个静态资源验证通过。浏览器测试运行方式见 [v0.7 验收说明](docs/v0.7.md)。

常规 Python / `npm run test:web` 测试使用脚本化模型响应和本机 HTTP 服务，**不需要 API Key，不调用收费接口**。覆盖工具调用闭环、真实命令执行、失败测试修复、中文路径、CRLF/BOM、错误参数、截断调用、超时、会话恢复、文件锁、CLI 和 HTTP 消息格式。

这里的“失败测试修复”使用固定模型响应驱动真实文件编辑和真实测试运行，验证运行时闭环，不代表对 DeepSeek 模型能力的评测。

真实 API 验收需要配置 Key 后执行：

```bash
deepblue -p "只回答：深蓝已连接"
deepblue --cwd /path/to/disposable-project -p "创建 hello.py，输出 hello deepblue，然后运行它并汇报结果"
```

配置环境变量后，可显式运行完整真实联调：

```bash
python scripts/live_smoke.py --v2
```

该脚本**会调用收费 API**，不会被普通单元测试自动执行。它在 `.test-tmp/` 创建独立项目，验证流式对话、六个工具、失败测试修复和压缩后的记忆恢复，并用本地测试独立核对修复结果。不加 `--v2` 则只验收原有四个编码工具和普通会话恢复。脱敏报告和日志保存在对应 `live-*` 子目录；密钥只从环境变量读取。

已完成 `deepseek-v4-flash` 真实联调：[第一版联调记录](docs/live-integration.md)、[第二版实现与验收](docs/v0.2.md)。第二版 58 项本地测试通过，六工具编码闭环及压缩后的记忆恢复验证通过。

### 真实 Web 验收（显式收费）

先以当前仓库为 `--cwd` 启动 Web，在网页设置中配置真实 DeepSeek Key。脚本通过浏览器操作已启动服务，不读取或复制 Key；临时文件和脱敏报告保存在 `.test-tmp/web-live-*`。执行前应确保没有其他任务，验收期间不要修改工作区，以免验收证据因文件变化失效。

```powershell
npm install
$env:DEEPBLUE_PYTHON = (Get-Command python).Source # Python >= 3.10
$env:DEEPBLUE_WEB_URL = "http://127.0.0.1:30142"
# 已安装 Chrome 时可指定；否则先 npx playwright install chromium
$env:CHROME_PATH = "C:/Program Files/Google/Chrome/Application/chrome.exe"
npm run test:web:live
```

可直接运行 `node scripts/web_live_smoke.cjs --run`。测试修复独立的小文件、运行指定检查、制造可压缩历史并取消一个等待命令，会产生多次模型请求及摘要费用。失败后报告保留已通过项，可使用 `--run --resume .test-tmp/web-live-xxxxxxxx` 继续同一测试会话；它不会重跑已通过项。受控 401/429/超时/断流走 `npm run test:web`，不对真实账户制造故障。

### 首批项目源码对照评测

v0.7.2 已同步 GitHub（`e05688f`）。下一阶段新增 5 个固定源码回归任务，按 dev / holdout 分组，从独立副本比较 baseline / verified，带请求、Token 和时间预算，输出逐项失败分类与 JSON / Markdown 报告。

```powershell
$env:PYTHONPATH = "src"
python scripts/evaluate_project.py --validate # 不调用 API，检查原版通过/缺陷版失败
# 以下显式收费；需要当前进程配置 Key
python scripts/evaluate_project.py --live --model deepseek-v4-flash
```

这些是实际项目模块上的人为缺陷，不是历史 Issue 基准；不能据此声称通用完成率。执行器还支持 `--key-file`、`--max-calls`、`--token-budget`、`--seconds`；预算和 Key 使用说明见 [首批评测文档](docs/project-evaluation.md)。新工具的真实运行与提交状态见 [Issue #17](https://github.com/plussea/deepblue/issues/17)。

Agent 新增每轮剩余步数、最小局部修复与直接检查提示；评测按任务对分配预算，每模式独享最多 8 次请求、40,000 Token 保守准入门槛，不借用后续任务额度。完整 **115 项 Python 回归通过**，预算边界与报告调整后 **6 项针对回归通过**。

第二轮真实 DeepSeek 已覆盖全部 **10 次任务**，共 **59 次请求、202,026 Token**。baseline 独立检查通过 **3/5**，verified **4/5**；正常结束且通过分别为 **3/5、1/5**，verified 另外 3 次虽修复通过仍因局部预算停止，不能混算为正常完成。见 [第二轮结果与下一步](docs/evaluation-results/2026-09-23-round2.md)；[首轮预算截断结果](docs/evaluation-results/2026-09-22.md)保留供参考。小样本不能证明模式总体优劣；下一步优先改善预算估计、验收收尾与输出截断。新增代码和文档尚未提交、推送。

### Harness 下一阶段：统一预算与验收收尾

实施顺序与验收标准见 [Harness 下一阶段实施计划](docs/harness下一阶段实施计划.md)，跟踪 [Issue #18](https://github.com/plussea/deepblue/issues/18)。P0.1/P0.2 已本地实现，完整 127 项 Python 回归通过，尚未提交、推送；后续阶段状态见计划。

CLI、Web worker 和评测现在复用核心预算组件。自动压缩、验收失败后的修复请求均计入同次运行；每次运行保存请求数、已报告 Token、保守请求估计与停止原因。步骤上限、上下文上限、输出截断或预算停止后，只要配置了验收命令且时间允许，运行时执行一次最终检查并保存证据。用户取消或网络错误不追加检查；验收通过不把中断改写为正常完成。

```powershell
# CLI：每次运行最多 12 次模型请求，含自动压缩；为已配置的检查预留 10 秒
python -m deepblue -p "修复测试失败" --max-requests 12 --token-budget 80000 --run-seconds 180 --finalize-reserve-seconds 10 --verify "python -m unittest discover -s tests"
# Web：启动参数进入作业配置快照；检查命令仍在 Web 中配置
python -m deepblue.web --max-requests 12 --token-budget 80000 --run-seconds 180
```

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--max-requests` | `max-steps + 4` | 单次运行所有模型请求尝试上限，失败请求也计数 |
| `--token-budget` | 不限制 | 已报告用量加下一请求保守估计的准入门槛 |
| `--run-seconds` | 不限制 | 单次运行协作时间限制 |
| `--finalize-reserve-seconds` | 10 | 有检查命令时提前停止新模型请求，给验收留时间；必须小于运行时限 |

Token 估计在样本不足时使用请求 UTF-8 字节数，满足条件后按下节规则校准；两种模式均保留最大输出和余量，不是精确分词或金额硬上限。时间限制在请求/检查边界协作生效，在途 API、工具和指纹扫描仍可能延迟停止；不是硬实时终止。`/retry` 是新的运行额度，保留任务身份与会话累计用量。手动 `/compact` 仍使用既有摘要请求上限，不属于一次任务 run 的预算。预算参数本阶段通过启动命令配置，尚未增加 Web 设置表单。

最终结果可为 `budget_limit + passed`，表示当前文件通过指定检查，但执行因预算中断；CLI 仍返回非零退出码。P0.3 预算校准与主动验收见下节；持久化任务进度仍属于后续阶段。

### Harness P0.3：预算校准与主动验收

跟踪 [Issue #19](https://github.com/plussea/deepblue/issues/19)，已本地实现，验证状态见下表。原始真实模型评测结果保留，本轮不追加付费调用。

- **预算校准**：默认 `calibrated`。在同次运行、相同工具声明下，最近 16 个样本中至少有 3 个有效 `prompt_tokens` 样本、且各自请求大小为当前的 0.5–2 倍，才采用样本最大 Token/字节比例再加 25% 余量；输入估计不低于字节数的 1/4，另预留完整最大输出与 512 Token。预算总额不变。工具声明变化、缺失输入用量或请求规模突变时回退；出现输入低估后，该工具声明在本次运行内禁用校准。缓存命中 Token 不从输入用量扣除。
- **可审计**：请求计量记录输入字节数、估计模式、估计输入、供应商输入用量及偏差。校准不是分词器，内容分布改变仍可能低估，不能保证金额硬上限。
- **主动验收**：已配置验收命令时，在完整 write/edit/shell 工具批次后比较工作区指纹，发现变化即运行检查，并在下一次模型请求前反馈证据。默认每次运行最多 2 次主动检查，可设为 0–10；最终检查仍按现有预算与修复上限执行。指纹无法确认时不猜测变化。
- **防止重复检查**：同次运行、未变化版本的稳定通过/失败证据可复用；再次修改会失效。Shell 调用即使未改文件也会使缓存失效，避免忽略环境变化。修改工作区的检查、错误、超时和取消证据不复用。此缓存只描述指定检查和受指纹覆盖的文件，不能保证外部服务或时间相关检查仍有效。
- **不提前宣布完成**：主动检查通过只进入汇报提示，不自动把任务标成完成；后续修改、取消、步骤/预算停止仍按真实状态记录。同一失败证据最多发出一次自动修复要求，且共享 `--verify-repairs` 额度。

```powershell
# 关闭两项新增行为进行受控对照（不改变其他运行时行为）
python -m deepblue -p "修复测试" --verify "python check.py" --budget-estimator conservative --active-checks 0
# Web 同样支持这两个启动参数；作业启动后冻结配置
python -m deepblue.web --budget-estimator calibrated --active-checks 2
```

评测脚本也支持 `--budget-estimator` 和 `--active-checks`，并记录 `calibrated-active-verification-v1` 策略及实际选项；这与历史第二轮不同，不混称同一实验条件。尚未通过新的真实模型样本证明完成率或成本改善。

### Harness P1.1：任务状态与恢复接续

跟踪 [Issue #20](https://github.com/plussea/deepblue/issues/20)。任务状态以带版本的 `task_state` 事件追加到会话 JSONL，与聊天摘要独立保存，旧会话仍可加载。

- **用户目标与验收条件**：保存本次原始任务文本、来源消息位置和实际配置的验收命令。新任务创建新身份；`/retry` 保留目标、模型笔记及已观察写入路径，同时记录上一运行的历史状态。旧会话没有结构化目标时明确标为未知，不从模型回复推断。
- **模型笔记**：Agent 可通过 `task_update` 更新进度、阻碍和下一步。这些字段明确标注为模型陈述，不能设置运行完成或检查通过。
- **程序事实**：工具批次、验收和运行结束时保存状态、步数、停止原因、已观察 write/edit 路径及证据引用。Shell 的全部文件副作用不在该路径列表中；仍需查看 diff/检查证据。重启发现未结束运行时标记中断，不重放未知操作。
- **接续上下文**：每次模型请求都附带当前任务状态，压缩不会删除它，也不修改原始系统提示。长目标在模型上下文中最多展示 6000 字符并标注截断，原文仍在记录中；任务状态本身也计入上下文和预算。
- **查看**：CLI 使用 `/task`；Web 在“状态 → 任务状态”折叠查看。刷新和增量读取可恢复；检查通过仍按工作区指纹判定是否过期。

本阶段提供任务记录与提示接续，不提供自动排队、跨任务调度或自动续跑。长任务仍需用户继续或 `/retry`。没有追加真实模型付费实验；源码尚未提交、推送。

## 按需定位与操作计量（P1.2，本地实现）

跟踪 [Issue #21](https://github.com/plussea/deepblue/issues/21)。`symbols` 按名称子串匹配 Python 类和函数（含嵌套定义），先定位再分页 `read`；不导入源码，不提供引用分析或持久索引。默认跳过依赖、隐藏目录及链接；每次最多扫描 200 个 Python 文件、每个 256 KiB，5 秒协作时限及约 30 KiB 结果上限。`truncated` / `skipped` / `syntax_errors` 表示结果可能不全，应缩小范围。单个 AST 解析期间不能硬实时中断。

`project_checks` 只读取指定目录的 `pytest.ini`、`pyproject.toml` 与 `package.json`，返回 pytest 配置标记以及 npm 的 test/lint/typecheck/check 脚本对应候选命令。它不完整解析 TOML、不保证依赖或命令可用，不递归推断所有构建系统，也不执行脚本或覆盖已有验收命令；候选为空时应阅读项目说明。

每次运行的 `tool_metrics` 保存在运行记录与任务状态中，可通过 CLI `/task` 或 Web 折叠任务状态查看：

- `tool_calls` / `read_calls`：已完成的工具调用与读取次数，不包含被截断而未执行的调用。
- `repeated_reads`：同次运行内，同一绝对路径、offset、limit 和稳定文件哈希再次读取；不同页或文件变化不计，Shell 后清空读取历史。
- `failed_calls` / `repeated_failures`：失败次数及同工具、同参数、同错误文本和退出码再次失败的次数。输出正文不参与签名；这是重复结果指标，不等于无效操作或浪费。
- `calls_before_first_file_change`：第一次成功 write/edit 且观察到文件状态变化之前的工具调用数；没有此类变化时为 null，不统计 Shell 的文件副作用。
- `tool_seconds`：已完成工具操作的累计耗时，不包含模型请求或独立验收命令。

每个 run 独立计数，恢复依靠完成事件的唯一 ID，不重放工具。旧记录缺少 run ID 时不推断归属。尚未通过新任务固定预算对照验证效率提升；本轮没有追加付费模型调用，代码未提交或推送。

## 工具权限策略（P1.3 第一部分，本地实现）

隔离环境诊断：`python -m deepblue --check-isolation`。无需 Key，不创建会话、不执行项目命令、不安装工具或拉取镜像；报告 Docker 服务、bubblewrap 和 Windows WSL 的可用状态。诊断成功返回 0 仅表示报告已生成，`isolation_verified: false` 明确表示尚未验收隔离，工具可用不等于沙箱生效。当前执行后端仍为 host，受限模式仍禁止 Shell。


启动 CLI 或 Web 时可设置 `--permission-mode trusted|workspace|read-only`，默认 `trusted` 保持兼容。Web 作业冻结启动策略，聊天参数不能提升权限。

| 模式 | 文件工具 | Shell / 命令验收 |
| --- | --- | --- |
| `trusted` | 原有当前用户权限 | 允许 |
| `workspace` | 工作目录内读取和修改 | 拒绝 |
| `read-only` | 工作目录内读取 | 拒绝 |

例如 `python -m deepblue.web --cwd . --permission-mode read-only`。受限模式拒绝越界路径、符号链接/junction、多重硬链接文件、`.git` 等仓库元数据、`.deepblue` 及配置的运行存储目录；递归读取也检查候选文件。`read-only` 仍允许程序保存会话、日志和任务笔记，它限制的是 Agent 项目文件工具。已有会话内容不会因切换模式被删除或自动脱敏，应使用新会话处理不同信任范围的数据。

受限模式不支持 `--verify` 或 Web 的命令验收；此时应移除验收命令，程序明确拒绝执行。运行与任务状态记录实际权限模式。默认 trusted 能执行任意当前用户命令，不能当成隔离环境。

**这属于应用层工具策略，不是操作系统沙箱。** 路径检查与实际打开文件之间仍存在竞态，不能防御同一用户的恶意并发替换；也没有对网络、进程或已加载会话内容提供系统级隔离。P1.3 的完整验收仍待 Shell 沙箱、隔离环境和攻击测试完成，不能据此扩大无人值守范围。跟踪 [Issue #22](https://github.com/plussea/deepblue/issues/22)，代码未提交/推送。

## 扩展接口与任务工作流（P2，本地实现）

P2.1 提供版本 1 的 Python hook：`run`、`tool`、`compact`、`verification` 各有 before/after/error。所有工具和内置权限通过同一调用接口，显式注册扩展工具禁止覆盖已有名称。不会自动加载项目代码或插件。注册示例、错误语义和边界见 [扩展接口](docs/extensions.md)，跟踪 [Issue #24](https://github.com/plussea/deepblue/issues/24)。

P2.2 Web 使用方式，跟踪 [Issue #25](https://github.com/plussea/deepblue/issues/25)：

- **补充指令**：任务执行中输入文字，点击“补充指令”。在下一次模型请求前接收，完整工具批次不会被打断；当前运行预算不重置。如果没有下一次请求，消息暂停留在队列。
- **后续任务**：执行中 Enter/发送将文字加入后续队列，当前任务正常结束后顺序执行。每条后续任务是新的 run，使用独立预算，因此总费用可能超过单次运行额度；异常、取消或预算停止时暂停剩余消息。
- **消息队列**：会话操作 → 消息队列，可刷新、修改、取消未领取消息，或空闲时手动执行后续任务。SQLite 事务确保领取与编辑互斥；刷新入队重试保留请求 ID。服务重启把待执行消息转为 held、已领取但结果不明的转为 unknown，不自动重放。unknown 需要先核对历史；确需再做时作为新消息发送。
- **会话分叉**：会话操作 → 分叉会话，留空复制全部，也可指定历史消息数量（包含系统消息）。只允许完整工具调用/结果边界，原会话不变。分叉不复制文件、用量、任务状态或验收证据；历史文本中的成功陈述不代表当前验收通过。
- **多项目**：左侧项目“切换”，添加已有绝对目录。通过项目 ID 路由到独立工作区，每个项目串行执行、不同项目可独立运行；切换不取消旧任务或修改其目录。会话、队列和草稿按项目隔离。最近项目目录持久化，最多 16 个；文件标签仍只在当前页面保留。

设置和内存 Key 仍只存当前服务进程；新项目从服务初始项目的当时配置复制默认值，之后独立修改，不复制内存 Key。环境变量仍具有全局优先级。任务启动与后续任务入队时冻结配置；同一服务内排队 Key 仅在内存保留。重启后手动执行 held 后续任务沿用已保存配置并使用当前可用 Key。新任务开始前的文件内容可能变化，应重新阅读和验收。

P1.3 系统级隔离按用户要求暂缓。以上能力不提供沙箱，trusted 模式仍是当前用户权限；workspace/read-only 的工具限制继续生效。实现与测试均为本地状态，尚未提交/推送，没有自动追加付费模型实验。

## 更新记录与 Issue

下表记录各阶段实现与验收结果，提交同步状态以仓库历史及对应 Issue 为准；推送源码不等于发布 PyPI 包或 GitHub Release。

每次功能更新或问题修复都关联 GitHub Issue，记录改动范围、验证结果和完成状态，并同步更新本 README 中受影响的使用说明。已有相关 Issue 时继续更新，独立事项新建 Issue。

| Issue | 更新内容 | 验证与状态 |
| --- | --- | --- |
| [#1：DeepBlue v0.1 最小 Coding Agent](https://github.com/plussea/deepblue/issues/1) | DeepSeek 接入、四个工具、Agent 循环、CLI、会话恢复和中文版说明 | 第一版 27 项测试通过，打包安装验证通过；已完成 |
| [#2：LLM 配置兼容与真实 DeepSeek 联调](https://github.com/plussea/deepblue/issues/2) | 完整接口地址、`LLM_*` 配置、联调脚本和相关修正 | 真实接口闭环通过，29 项回归测试通过；实现完成 |
| [#3：v0.2 流式输出、压缩与搜索](https://github.com/plussea/deepblue/issues/3) | SSE 流式输出、`/compact`、`find` / `grep`、`/paste` 和用量显示 | 58 项测试、真实接口和打包验证通过；实现完成 |
| [#4：v0.3 程序化验收与评测骨架](https://github.com/plussea/deepblue/issues/4) | 显式检查、状态分离、有限修复、文件指纹、5 个开发评测任务 | 73 项回归及打包安装通过；实现完成；未做真实任务批量评测 |
| [#5：v0.4 上下文管理](https://github.com/plussea/deepblue/issues/5) | 结构化摘要、来源事实、自动压缩、输出归档、读取过期 | 合并版本 91 项回归及打包通过；实现完成 |
| [#6：v0.5 中断恢复](https://github.com/plussea/deepblue/issues/6) | 工具执行事件、文件哈希核对、恢复摘要、故障注入 | 含真实进程退出测试，91 项回归通过；实现完成 |
| [#7：Web 工作台](https://github.com/plussea/deepblue/issues/7) | 会话、流式事件、文件预览、验收与恢复面板 | 接口、桌面/窄屏浏览器、打包安装通过；实现完成 |
| [#8：Web 下一阶段路线](https://github.com/plussea/deepblue/issues/8) | 对照 pi-web 的差距、v0.6–v0.8 范围与验收标准 | v0.6/v0.7 实现完成；v0.8 继续跟踪 |
| [#9：v0.6 任务可靠性](https://github.com/plussea/deepblue/issues/9) | 请求幂等、持久任务身份、SSE/快照/重连、按 ID 停止及重启核对 | 105 项回归、10 组浏览器场景及打包安装通过；实现完成 |
| [#10：v0.6 会话体验](https://github.com/plussea/deepblue/issues/10) | 增量索引、分页、内容搜索、归档恢复、导出与草稿 | 105 项回归、10 组浏览器场景及打包安装通过；实现完成 |
| [#11：v0.6 DeepSeek 设置](https://github.com/plussea/deepblue/issues/11) | 服务端内存 Key、环境优先、配置冻结、显式连接测试 | 105 项回归、10 组浏览器场景及打包安装通过；实现完成 |
| [#12：v0.7 消息与文件](https://github.com/plussea/deepblue/issues/12) | 安全 Markdown、代码复制/高亮、工具卡片、文件行号和引用 | 105 项回归、10 组浏览器场景及打包安装通过；实现完成 |
| [#13：v0.7 修改与证据](https://github.com/plussea/deepblue/issues/13) | Git diff、已有改动基线、验收/工具日志、恢复详情 | 105 项回归、10 组浏览器场景及打包安装通过；实现完成 |
| [#14：Web 存储权限修复](https://github.com/plussea/deepblue/issues/14) | 项目内默认存储、启动写入预检与可操作错误提示 | 22 项 Web 回归通过，实际服务已改用项目内存储；实现完成 |
| [#15：v0.7.x 真实 Web 验收](https://github.com/plussea/deepblue/issues/15) | 真实浏览器链路、受控故障、错误刷新恢复 | 实现与验收通过；源码交付状态见 Issue |
| [#16：浅色简约 Web](https://github.com/plussea/deepblue/issues/16) | 配色与信息精简、会话菜单、保留 Token/压缩次数 | 桌面/窄屏回归通过；源码交付状态见 Issue |

| [#17：项目源码对照评测](https://github.com/plussea/deepblue/issues/17) | 5 个固定回归任务、开发/保留分组、预算与独立检查、对照报告 | 本地实现；第二轮 10/10 已运行，区分独立通过与正常完成；待同步源码 |
| [#18：Harness 预算与收尾](https://github.com/plussea/deepblue/issues/18) | 阶段计划、核心预算、CLI/Web 配置、停止后验收 | 本地实现；127 项 Python 回归通过；尚未提交/推送 |
| [#19：预算校准与主动验收](https://github.com/plussea/deepblue/issues/19) | 有界样本校准、修改后检查、同版本证据复用、对照开关 | 本地实现；139 项 Python 回归通过；尚未提交/推送 |
| [#24：生命周期扩展接口](https://github.com/plussea/deepblue/issues/24) | 版本化 hook、工具注册、内置权限 | 本地实现；176 项完整 Python 回归、收尾 25 项针对回归、22 组浏览器回归通过；未提交/推送 |
| [#25：队列、分叉与多项目](https://github.com/plussea/deepblue/issues/25) | 边界引导、后续队列、完整历史分叉、项目路由 | 本地实现；176 项完整 Python 回归、收尾 25 项针对回归、22 组浏览器回归通过；未提交/推送 |
| [#23：紧凑输入与文件标签预览](https://github.com/plussea/deepblue/issues/23) | Enter 发送、输入法保护、整高文件标签、拖动与放大/还原 | 本地实现；19 组浏览器回归通过；未提交/推送 |
| [#22：工具权限策略与隔离边界](https://github.com/plussea/deepblue/issues/22) | 三种权限模式、路径保护、Shell/验收拒绝、Web 配置冻结 | 策略本地实现；162 项 Python 回归及补充 Web worker 测试、16 组浏览器回归通过；沙箱待完成；未提交/推送 |
| [#21：按需定位与操作计量](https://github.com/plussea/deepblue/issues/21) | Python 定义定位、候选检查发现、重复读取/失败指标 | 本地实现；155 项 Python 回归及补充持久化测试、16 组浏览器回归通过；真实效率对照待完成；未提交/推送 |
| [#20：任务状态与恢复接续](https://github.com/plussea/deepblue/issues/20) | 任务事件、笔记工具、压缩/重启接续、CLI/Web 查看 | 本地实现；147 项 Python、16 组浏览器回归通过；未提交/推送 |

## 运行边界

深蓝会按当前用户权限操作本机文件和执行命令，当前没有逐操作审批、文件系统沙箱或网络隔离。`--cwd` 是默认工作目录，**不是访问权限边界**。应在信任的项目和适合修改的工作副本中使用。

当前不支持图片输入、订阅账号登录、MCP、Skills、自动 Git 提交、多 Agent，Shell 不支持后台常驻服务。Web 仅供本机使用。Linux/macOS 的实现分支仍需在对应系统上验收；当前开发测试环境为 Windows + Python 3.12。

## 常见问题

**网页发送消息提示 WinError 5 / `.deepblue` 拒绝访问？**

这是创建本地会话时的存储错误，发生在模型请求之前。v0.7.1 默认使用项目内 `.deepblue`，并在启动时检查写权限。可显式运行 `deepblue-web --cwd /path/to/project --home /path/to/writable-storage`；若设置了 `DEEPBLUE_HOME`，也需检查它指向的位置。无需为此以管理员运行服务。旧会话不会自动迁移，仍可通过 `--home` 指向原目录。


**提示 Python 版本过低？**

用 `python --version` 确认实际解释器。系统中的 Python 3.8 不满足要求；可使用 `uv venv --python 3.12` 创建新环境，或用已安装的新版解释器创建环境。

**没有配置 API Key？**

在启动深蓝的同一个终端设置 `DEEPSEEK_API_KEY`。`--help` 和 `--version` 不需要 Key。

**出现 401、402 或 429？**

检查 Key、账户余额或请求限流。修复后在交互模式输入 `/retry`；程序不自动重试，以免掩盖重复请求或持续消耗。

**模型等待时没有逐字输出？**

默认按模型返回的文本片段展示。检查是否设置了 `--no-stream`；工具参数组装与手动摘要期间不会逐字显示。可用 `Ctrl+C` 取消；`--timeout` 控制网络操作超时，并非整个任务的总时限。

**命令运行超过两分钟？**

使用 `--shell-timeout 300` 提高上限。模型工具参数只能缩短该上限。

**想清空上下文但保留旧记录？**

输入 `/new`。旧 JSONL 和命令日志保留，当前不自动清理日志。

## 后续方向

- [Web 下一阶段实现计划](docs/web下一阶段实现计划.md)：v0.6/v0.7 已实现；v0.8 再评估多项目、会话分叉和消息队列。保持 Python 后端，近期仅支持 DeepSeek。
- 真实长任务的上下文质量对照与更完善的终端编辑体验。
- 更完整的项目忽略规则与检索能力。
- 更多模型适配和可选权限策略。
- 稳定核心接口后的 Skills / 扩展能力。

---

<p align="center">
  <strong>DeepBlue · 深蓝</strong><br />
  从一个可靠的编码闭环开始。
</p>


### 导航效率对照准备（2026-09-30，Issue #21）

评测脚本支持 `--navigation on|off`，关闭时移除 symbols/project_checks；baseline/verified 仍用于比较自动验收。报告汇总重复读取、重复失败、工具调用和首次文件修改前调用数，显示计量样本数。8 项针对回归通过，未调用付费模型；新任务真实对照仍待完成。详见 [评测说明](docs/project-evaluation.md#p12-导航消融准备2026-09-30)。本地修改未提交、推送。

导航对照现支持 `--suite navigation --navigation paired --repeats 2`：新增两个尚未运行真实模型的试验任务，同批配对、反转重复顺序、独立预算与四组报告。10 项针对回归通过；真实效率验收仍待完成，跟踪 [#21](https://github.com/plussea/deepblue/issues/21)。


### Skill 与自定义命令（Issue #26）

支持用户/项目 `.deepblue/skills/<name>/SKILL.md` 和 `.deepblue/commands/<name>.md`，项目同名优先。Skill 摘要按需加载，命令支持 `{{1}}` / `{{args}}` 参数及 `skills: review` 引用；Web 输入 `/` 可选择并补全，CLI 用 `/skills`、`/commands` 查看。详见 [目录约定与示例](docs/skills-commands.md)。本地实现；186 项完整 Python 回归及后补 6 项针对测试通过，尚未提交/推送。跟踪 [#26](https://github.com/plussea/deepblue/issues/26)。


Skill / Command 收尾核对（2026-10-02）：186 项完整 Python 回归、后补 16 项针对回归、23 组浏览器回归通过（计数有重叠，不累计）。日志 `.test-tmp/skills-tests.log`、`.test-tmp/skills-browser.log`，浏览器报告 `.test-tmp/web-e2e-bbc6f44d/report.json`。本地 Web 已启动最新源码，目录 API 与 commands.js 健康检查通过。未付费调用模型、未提交/推送，#26 保持开放等待同步。


### 内置命令与 Skill Creator（2026-10-02）

开箱提供 `/review` 审查、`/explain` 解释、`/fix` 修复、`/test` 检查、`/plan` 规划及 `/create-skill` 创建技能。例如 `/review src/`、`/create-skill 为 Python 项目创建测试规范技能`。`/review`、`/explain`、`/plan` 默认不修改文件；`/test` 默认运行相关检查，不自动修复。

`/create-skill` 引用内置 `skill-creator`，生成兼容 DeepBlue 简单元数据格式的 Skill，默认写入项目 `.deepblue/skills`，使用现有工具和权限。默认能力随 Python 包分发，优先级为项目 > 用户 > 内置；CLI 控制命令名仍保留。

17 项针对回归通过，覆盖默认发现、命令展开、技能引用及覆盖规则。通用 Skill validator 缺少 PyYAML 未运行，DeepBlue 实际加载器验证通过；未安装依赖或调用付费模型。代码未提交/推送，跟踪 [Issue #26](https://github.com/plussea/deepblue/issues/26)。服务需重启加载新的默认目录规则。


斜杠菜单更新：输入 `/` 同时列出 Command 与 Skill，以 `/ Command`、`◇ Skill` 标识区分；选择技能补全 `/skill:名称 `，可追加任务后发送并明确加载该技能。8 项能力测试通过；实际 Web 验证六个内置命令、Skill 点击、Tab 补全及 Escape 关闭通过。服务已重启，刷新页面生效。跟踪 #26，未提交/推送。


### 文件树与 Markdown 预览（2026-10-03）

文件面板改为可原地展开/折叠的目录树，层级缩进、简约箭头和蓝色焦点描边；显示隐藏文件与目录。`.git` 显示为受限项，仍不开放元数据读取；链接/junction 仍跳过，每层最多显示 300 项。文件搜索纳入隐藏目录，保留 Git、依赖与构建缓存排除及数量上限。

`.md` 默认渲染标题、列表、引用、表格、分隔线和带复制按钮的代码块，可切换源码；查找/跳转行号进入源码。保留整高标签预览、拖动宽度和放大。修复 Windows CRLF/BOM 标题识别，相对文件链接按文档目录解析。采用安全 DOM 渲染，HTML 不执行；仍是常用 Markdown 子集，未支持完整 CommonMark、Mermaid 或公式。

9 项 Web Python 回归、24 组浏览器回归通过，覆盖隐藏目录与 Markdown 切换；截图已检查。日志 `.test-tmp/tree-python.log`、`.test-tmp/tree-browser.log`，报告 `.test-tmp/web-e2e-38c11e66/report.json`。本地 Web 已更新，刷新生效。跟踪 [Issue #27](https://github.com/plussea/deepblue/issues/27)，未提交/推送。


### 2026-10-03 源码集中同步

本次集中提交此前 Harness、评测、Skill/Command 和 Web 文件树/Markdown 改动；下文及历史章节中的“未提交/推送”是阶段记录。后续优先级见 [下一步计划](docs/下一步计划-2026-10-03.md)。P1.2 真实效率对照与 P1.3 系统隔离仍待完成；此源码同步不代表发布 PyPI 或 GitHub Release。

提交前完整回归：190 项 Python 测试通过（87.277 秒）；最近浏览器回归 24 组通过。凭据特征扫描与暂存差异格式检查通过，运行目录及本地密钥不纳入提交。
