<h1 align="center">DeepBlue · 深蓝</h1>

<p align="center">
  一个轻量、可读、可扩展的 Python 终端 Coding Agent。
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#工具">四个工具</a> ·
  <a href="#会话">会话恢复</a> ·
  <a href="#开发与测试">开发与测试</a>
</p>

<p align="center">
  <strong>Python ≥ 3.10</strong> · <strong>DeepSeek API</strong> · <strong>无第三方运行时依赖</strong>
</p>

---

深蓝是一个在本地项目目录中工作的编码助手。你描述任务，它通过 DeepSeek 决定读取哪些文件、如何修改代码、运行什么命令，再根据真实的工具结果继续处理。

第一版专注于一个闭环：**阅读代码 → 修改代码 → 运行测试 → 根据结果修复 → 汇报结果**。

项目参考 [pi](https://github.com/earendil-works/pi) 的小核心、少量工具、模型驱动工作方式，使用 Python 独立实现。采用单进程 CLI，支持交互对话和一次性任务，暂不引入多 Agent、插件系统或复杂终端界面。

## 目录

- [快速开始](#快速开始)
- [模型与认证](#模型与认证)
- [交互模式](#交互模式)
- [工具](#工具)
- [会话](#会话)
- [项目说明](#项目说明)
- [命令行参考](#命令行参考)
- [配置](#配置)
- [工作原理](#工作原理)
- [项目结构](#项目结构)
- [开发与测试](#开发与测试)
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

## 模型与认证

v0.1 只支持 **DeepSeek Chat Completions API**，直接通过 Python 标准库发送 HTTPS 请求。

| 项目 | 默认值 |
| --- | --- |
| API 地址 | `https://api.deepseek.com` |
| 模型 | `deepseek-flash` |
| 认证 | `DEEPSEEK_API_KEY` 环境变量 |
| 思考模式 | 关闭 |
| 输出模式 | 非流式，完整回复返回后显示 |

默认模型名依据 [DeepSeek 官方快速开始](https://api-docs.deepseek.com/)；请求和工具消息格式参考 [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)。模型可用性以你的 API 账户为准，不在代码中硬编码模型白名单。

```bash
deepblue --model deepseek-v4-pro
```

模型名称可配置，但第一版固定关闭思考模式，不保证兼容仅支持思考模式的旧模型或第三方代理的协议差异。

API Key 不写入配置文件或会话元数据，也不会传入 `shell` 子进程的环境。程序不会自动读取 `.env`。会话会保存实际对话和工具结果，因此不要在提示或命令输出中放入密钥。

## 交互模式

启动时展示版本、模型、工作目录和会话路径。执行期间显示模型调用轮次、工具名称、修改 diff、命令结果和日志路径。

```text
DeepBlue 深蓝 v0.1.0 · deepseek-flash
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
| `/status` | 查看模型、目录、会话路径和消息数量 |
| `/retry` | 继续待处理的模型请求；不会直接重放历史工具调用 |
| `/exit`、`/quit` | 保存现有记录并退出 |
| `Ctrl+C` | 取消当前任务；在输入提示处清空本次输入 |
| EOF | 退出；Windows 通常为 `Ctrl+Z` 后回车 |

第一版为逐行输入，不支持执行中插入新指令、多行编辑器或逐 token 输出。取消后可以重新输入任务。

## 工具

深蓝向模型提供四个工具，按模型给出的顺序执行。

| 工具 | 参数 | 行为 |
| --- | --- | --- |
| `read` | `path`、可选 `offset` / `limit` | 读取 UTF-8 文本并显示行号，支持分页 |
| `write` | `path`、`content` | 创建或完整覆盖文件，自动创建父目录 |
| `edit` | `path`、`old_text`、`new_text` | 唯一精确替换，返回统一格式 diff |
| `shell` | `command`、可选 `timeout` | 执行命令，返回退出码、合并输出和日志路径 |

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

**恢复对话不等于恢复命令执行。** 如果崩溃时存在没有结果的工具调用，深蓝补充“结果未知”的错误记录，不自动重新执行。模型继续前应检查文件或命令副作用；这并不是外部操作的“恰好执行一次”保证。

会话尾部未完成的一行可在恢复时截除；已完整提交的行如果损坏则报错。第一版没有会话分支、自动摘要或文件回滚。

## 项目说明

深蓝创建会话时读取工作目录根部的 `AGENTS.md`，将其作为项目约定加入系统提示。例如：

```markdown
# 项目约定

- 使用 Python 标准库完成可以简单实现的功能。
- 修改代码后运行 python -m unittest discover -s tests -v。
- 不修改生成文件，不主动提交 Git。
```

第一版只自动加载根目录文件，大小限制 64 KiB；不会自动遍历父目录或子目录中的规则文件。

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
| `--base-url` | `https://api.deepseek.com` | API 根地址，可包含 `/v1` |
| `--home` | `~/.deepblue` | 本地会话存储根目录 |
| `--max-steps` | `30` | 一次任务最多调用模型次数 |
| `--timeout` | `120` | API 网络操作超时秒数 |
| `--shell-timeout` | `120` | 单条命令最大执行秒数 |
| `--max-tokens` | `8192` | 单次模型最大输出 token 数 |
| `--max-context-bytes` | `400000` | 历史消息序列化后的 UTF-8 字节上限 |

一次性模式：模型回复写到 stdout，运行状态和工具展示写到 stderr。完成返回 `0`；模型错误、轮数上限或不完整输出返回 `1`；配置/启动错误返回 `2`；取消返回 `130`。交互模式允许错误后继续输入，正常退出返回 `0`。

模型不再请求工具时，程序认为这一轮对话结束；这不代表程序独立证明了任务正确，仍应查看实际测试结果。

## 配置

| 环境变量 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 必填，DeepSeek API Key |
| `DEEPSEEK_MODEL` | 新会话默认模型 |
| `DEEPSEEK_BASE_URL` | API 根地址 |
| `DEEPBLUE_HOME` | 会话和日志保存位置 |

新会话的同名配置优先级为：**命令行参数 → 环境变量 → 默认值**。恢复会话固定使用保存的模型，禁止用 `--model` 切换到不同模型。

第一版不自动压缩上下文。超过本地字节上限时停止并提示 `/new`；字节数不是 token 数，模型服务也可能更早拒绝超长上下文。

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

Agent 核心不读终端输入，通过事件把文本、工具状态和错误交给 CLI 展示。模型客户端、执行循环、工具和会话存储分别独立，方便以后添加流式输出或新界面。

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
│   ├── prompts.py      # 系统提示和根目录 AGENTS.md
│   ├── session.py      # JSONL、文件锁和中断恢复
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

测试使用脚本化模型响应和本机 HTTP 服务，**不需要 API Key，不调用收费接口**。覆盖工具调用闭环、真实命令执行、失败测试修复、中文路径、CRLF/BOM、错误参数、截断调用、超时、会话恢复、文件锁、CLI 和 HTTP 消息格式。

这里的“失败测试修复”使用固定模型响应驱动真实文件编辑和真实测试运行，验证运行时闭环，不代表对 DeepSeek 模型能力的评测。

真实 API 验收需要配置 Key 后执行：

```bash
deepblue -p "只回答：深蓝已连接"
deepblue --cwd /path/to/disposable-project -p "创建 hello.py，输出 hello deepblue，然后运行它并汇报结果"
```

## 运行边界

深蓝会按当前用户权限操作本机文件和执行命令，第一版没有逐操作审批、文件系统沙箱或网络隔离。`--cwd` 是默认工作目录，**不是访问权限边界**。应在信任的项目和适合修改的工作副本中使用。

第一版不支持图片输入、订阅账号登录、MCP、Skills、自动 Git 提交、多 Agent、后台服务、流式输出或自动压缩。Linux/macOS 的实现分支仍需在对应系统上验收；当前开发测试环境为 Windows + Python 3.12。

## 常见问题

**提示 Python 版本过低？**

用 `python --version` 确认实际解释器。系统中的 Python 3.8 不满足要求；可使用 `uv venv --python 3.12` 创建新环境，或用已安装的新版解释器创建环境。

**没有配置 API Key？**

在启动深蓝的同一个终端设置 `DEEPSEEK_API_KEY`。`--help` 和 `--version` 不需要 Key。

**出现 401、402 或 429？**

检查 Key、账户余额或请求限流。修复后在交互模式输入 `/retry`；程序不自动重试，以免掩盖重复请求或持续消耗。

**模型等待时没有逐字输出？**

第一版采用非流式请求，完整响应返回后才展示。可用 `Ctrl+C` 取消；`--timeout` 控制网络操作超时，并非整个任务的总时限。

**命令运行超过两分钟？**

使用 `--shell-timeout 300` 提高上限。模型工具参数只能缩短该上限。

**想清空上下文但保留旧记录？**

输入 `/new`。旧 JSONL 和命令日志保留，第一版不自动清理日志。

## 后续方向

- 流式模型输出和更好的多行输入。
- 手动摘要与上下文压缩。
- 独立的文件搜索工具。
- 更多模型适配和可选权限策略。
- 稳定核心接口后的 Skills / 扩展能力。

---

<p align="center">
  <strong>DeepBlue · 深蓝</strong><br />
  从一个可靠的编码闭环开始。
</p>
