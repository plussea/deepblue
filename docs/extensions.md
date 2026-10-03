# Python 扩展接口 v1

当前仅支持可信宿主代码显式注册，不扫描项目目录，不从消息加载 Python 文件。CLI/Web 默认不启用第三方扩展；嵌入 Agent 时使用以下接口。扩展回调与宿主同进程，不是隔离插件。

```python
from deepblue.tools import ToolContext, create_tools
from deepblue.tools.base import Tool

tools = create_tools(ToolContext(project, artifacts))
tools.register(Tool('project_info', '项目摘要', {}, [],
                    lambda context, args: {'ok': True, 'name': context.cwd.name}))
unsubscribe = tools.hooks.on('tool.after', lambda event: print(event['data']['name']), version=1)
# 将 tools 传给 Agent；同一 hooks 同时接收运行、工具、压缩和验收事件。
# 不再需要时调用 unsubscribe()。
```

事件对象为 `{version: 1, event: 'tool.after', data: ...}`。订阅顺序决定调用顺序，每个回调收到独立深拷贝；修改事件不会修改调用参数、结果或其他回调的数据，返回值不作为替换结果。注册已有工具名称或未知事件/版本会失败。

| 事件族 | 数据 | 触发范围 |
| --- | --- | --- |
| run | session_id；after 附 Python RunResult | 一次 Agent.run，包含统一预算与收尾 |
| tool | name、arguments；after 附 result | 注册表内的工具执行；参数校验失败不进入 hook |
| compact | automatic；after 附压缩结果 | 手动与自动压缩 |
| verification | run_id、command；after 附检查结果 | 实际运行的检查；复用旧证据不会再次触发 |

before 在动作之前，异常会阻止动作；after 发生在动作之后，异常不能撤销已经发生的副作用。error 包含 error_type；异常处理回调失败不会覆盖最初错误。普通扩展异常转换为 HookError，不自动重试动作；内置 PolicyDenied 保留解释。工具返回 `{ok:false}` 和验收返回 failed 属于正常结果，触发 after；Python 异常触发 error。after 失败时先核对落盘操作/文件状态，不以 hook 错误推断工具没有执行。

内置权限检查首先注册为 tool.before；自定义工具在受限模式下默认拒绝，trusted 才可执行。这不限制可信 Python 扩展自身任意访问宿主的能力，不能对恶意扩展做安全承诺。

回调同步执行，没有硬超时或线程隔离，必须短小且不阻塞；不要发起模型调用、记录凭据或在事件里执行无限任务。任务内自动压缩/验收仍使用原预算和取消机制。后续若需要不可信插件，将另行设计进程协议与隔离环境。
