# DeepBlue Web 本地工作台

当前版本 **v0.7.1**，包含 v0.6/v0.7 的实现。布局参考同级 `pi-web`，后端保持 Python 标准库，前端采用原生 ES modules，无 Node 构建步骤或 CDN。

## 启动与设置

```powershell
python -m pip install -e .
deepblue-web --cwd "E:\projects\my-app" --port 30142
```

打开 [本机工作台](http://127.0.0.1:30142)。也可运行 `python -m deepblue.web`。Web 的存储优先级为 `--home` > `DEEPBLUE_HOME` > 项目内 `.deepblue`。CLI 默认仍为用户目录 `~/.deepblue`；同一 cwd/home 下可以浏览和继续 CLI 会话。启动会执行临时文件写入预检，失败时说明存储目录和 `--home` 修复方法，而不是等发送消息才出现 WinError 5。旧数据不会自动迁移。

在左下角“DeepSeek 设置”输入 Key，可设置模型、接口地址和网络超时。Key 仅在本次服务进程内保留，不回显，不写入 localStorage、配置文件或任务元数据；重启后重新输入。也可在启动前设置 `DEEPSEEK_API_KEY` / `LLM_API_KEY` 等环境变量，环境管理的字段在网页中不可修改。

`--model`、`--base-url`、`--timeout` 控制启动设置，默认网络操作超时 30 秒。网页保存超时范围 0.1–300 秒；新设置作用于后续任务，既有会话使用原来的模型。“测试连接（调用 API）”会发起一次真实模型请求，不会自动周期测试。

缺 Key 时仍可浏览项目和历史；执行按钮禁用。当前仅适配 DeepSeek Chat Completions。

## 日常使用

1. 新建会话，描述任务；Ctrl/Command + Enter 发送。可在验收设置中输入任务结束后执行的检查命令，失败后最多修复一次。
2. 输入 `@` 选择项目文件，或预览文件后点击“引用文件”。服务端验证路径，由 Agent 工具读取；引用不会自动塞入完整文件内容。
3. 任务运行时可浏览其他会话，点击“查看正在运行的任务”返回。页面全局仍只执行一个任务。
4. “状态”中查看上下文、用量、验收记录、实时工具日志和恢复核对。验收通过只代表指定检查通过，文件随后变化会显示证据过期。
5. “修改”中查看 Git 暂存/未暂存/未跟踪差异，以及任务开始前已有的修改；非 Git 项目可查看已记录的文件操作。

浅色界面保留底栏 Token 和压缩次数；重命名、归档/恢复和 MD/JSON 导出位于“会话操作”菜单。会话支持分页与内容搜索。归档不删除数据；执行中的会话不能归档。历史每页默认 100 条，可点击“加载历史”。草稿及验收命令按项目/会话保存在当前浏览器，刷新会恢复，可在“验收设置”中点击“清除草稿”删除当前草稿。

消息支持常用 Markdown、代码复制与基础高亮，原始 HTML 不执行。文件支持路径搜索、UTF-8 预览、行号、内容查找和手动刷新，不提供上传或直接编辑。窄屏用顶部“会话”“文件面板”打开抽屉。

## 连接、停止与恢复

Web 通过带请求 Token 的 fetch SSE 接收事件，带任务 ID、序号、快照和心跳；连接失败退避并可回退轮询。连续失败达到上限后停止，点击“重新连接”核对状态。任务提交响应丢失时按 request_id 查询原任务，不在后台自动重新执行用户请求。

浏览器刷新可以接回正在运行的任务。服务重启将未结束任务标为中断/未知，并通过原取消标记协作通知可能存活的 worker；不重放工具、不按旧 PID 杀进程。旧 worker 的会话锁未释放时拒绝启动新任务。

“停止执行”绑定具体 job_id，不会用旧标签页误停另一个新任务。Shell 会轮询取消标记并清理进程树，模型网络读取可能要等到返回或超时；停止不回滚已发生的修改。

## 数据与限制

- JSONL 保存原始会话；旁边的 `.web.json` 保存标题/归档状态，`web-jobs/` 保存最小任务元数据。删除元数据会失去对应幂等记录，不能据此推断旧任务未执行。
- 只读索引增量读取完整行，不与 worker 争用写锁，不修复半行尾部；冷启动和内容搜索仍需扫描会话，最多缓存 128 个会话。
- 每个任务最多缓存 2000 个事件；过期游标返回最多 200 个展示项的任务快照。完整历史可分页读取，快照不是持久化的全部输出。
- 文件预览最多 256000 字节；搜索最多 100 项、扫描 20000 个文件。消息与日志支持分段原文访问；Git diff 最多返回 256000 字节并标明截断。
- 工具/验收默认超时 120 秒，任务默认最多 30 个 Agent 轮次，结构化自动压缩默认开启。
- Markdown 是常用子集，不包含完整 CommonMark、Mermaid、公式、多媒体或交互 HTML。

服务仅监听 127.0.0.1，校验 Host/Origin/跨站来源与 Token，不开放 CORS。文件与日志接口限制访问范围；Agent 自身 Shell 仍按当前系统用户权限运行，这不是文件系统沙箱、多用户认证或恶意本机进程隔离。不要直接暴露到公网。

## 开发与验收

功能与验证细节见 [v0.6](v0.6.md)、[v0.7](v0.7.md)。自动浏览器测试使用隔离项目和本机模拟模型，不产生真实 DeepSeek 费用。Node/Playwright 仅为开发测试依赖，安装 Python wheel 后无需 Node 即可运行 Web。

Issue：原始工作台 [#7](https://github.com/plussea/deepblue/issues/7)，总路线 [#8](https://github.com/plussea/deepblue/issues/8)，v0.6 [#9](https://github.com/plussea/deepblue/issues/9)/[#10](https://github.com/plussea/deepblue/issues/10)/[#11](https://github.com/plussea/deepblue/issues/11)，v0.7 [#12](https://github.com/plussea/deepblue/issues/12)/[#13](https://github.com/plussea/deepblue/issues/13)。源码同步状态见仓库提交历史及对应 Issue。

存储权限修复记录：[Issue #14](https://github.com/plussea/deepblue/issues/14)。更换存储目录或重启后，原本只在内存中的网页 Key 需要重新输入。

## v0.7.2

最后一次任务的有界错误提示、压缩结果保存在任务元数据中（最多 6 条、每条 2000 字符，先脱敏再写入），刷新或服务重启后可见。新任务不沿用上次错误。短会话没有压缩收益时保留原文，次数不增加。

真实 Web 验收、故障验证和界面变更见 [v0.7.2 验收记录](v0.7.2.md)，跟踪 [#15](https://github.com/plussea/deepblue/issues/15)、[#16](https://github.com/plussea/deepblue/issues/16)。
