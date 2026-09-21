# DeepSeek 真实接口联调记录

联调日期：2026-09-20。运行环境：Windows、Python 3.12。

## 接口配置

| 项目 | 值 |
| --- | --- |
| 接口 | `https://api.deepseek.com/chat/completions` |
| 请求模型 | `deepseek-v4-flash` |
| 网络操作超时 | 30 秒 |
| 思考模式 | 关闭 |
| 密钥 | 仅通过进程环境提供，未保存到源码和报告 |

## 实测结果

本次测试通过真实 CLI、真实 DeepSeek 响应和真实文件/进程工具完成，没有使用脚本化模型响应。

| 阶段 | 结果 | 耗时 |
| --- | --- | --- |
| 基础连接 | 返回“深蓝已连接”，退出码 0 | 0.88 秒 |
| 编码闭环 | 读取代码、运行失败测试、精确修改、重跑测试、写入报告 | 9.74 秒 |
| 会话恢复 | 新进程读取原会话，正确回答上一轮随机联调标记 | 1.14 秒 |

编码夹具包含一个把加法写成减法的 `add` 函数和 3 个测试。模型执行的工具顺序为：

```text
read(calc.py)
read(test_calc.py)
shell(运行测试：3 个失败)
edit(calc.py：a - b → a + b)
shell(再次运行测试：3 个通过)
write(RESULT.md)
```

测试脚本独立复跑了原测试，退出码为 0；同时核对测试文件 SHA-256 未变化，确认通过并非来自删除或修改断言。

本次共 8 次有用量记录的模型请求，累计 `total_tokens` 为 17,374。该数值是各请求用量之和，包含重复上下文，不等于新增文本量或账单金额。

原始报告和执行记录保留在本地（该目录被 Git 忽略）：

- [结构化报告](../.test-tmp/live-69260d6b721f/report.json)
- [真实编码过程](../.test-tmp/live-69260d6b721f/repair.log)
- [独立验证结果](../.test-tmp/live-69260d6b721f/verification.log)
- [模型生成的结果说明](../.test-tmp/live-69260d6b721f/fixture/RESULT.md)

## 联调相关修正

- 完整 `/chat/completions` 地址直接使用，避免重复拼接路径。
- 支持 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_TIMEOUT_SECONDS`；原 `DEEPSEEK_*` 配置继续有效。
- 命令工具同时移除两种 API Key 环境变量，避免模型命令继承认证信息。
- HTTP 错误响应及时关闭，避免连接资源残留。
- 本地快速修复测试禁止生成字节码缓存，避免同一秒内等长源码修改导致的夹具偶发失败。

## 重现

在 Python 3.10 以上环境中设置上述环境变量，然后从项目目录执行：

```bash
python scripts/live_smoke.py
```

此脚本会调用收费接口，每次创建新的临时项目和联调报告。上表为本次观测结果，不保证未来的模型输出顺序、耗时和 token 用量相同。
