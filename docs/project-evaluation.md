# 首批项目源码回归评测

跟踪 [Issue #17](https://github.com/plussea/deepblue/issues/17)。上一阶段 v0.7.2 已提交并推送：[e05688f](https://github.com/plussea/deepblue/commit/e05688f4badc5e426c82148f14413b389bb442eb)。本阶段新增评测工具，并改进 Agent 的剩余步数与最小修复提示；不变更版本号。

当前状态：本地实现完成，完整 Python 回归 115 项通过；预算边界与报告调整后 6 项针对回归通过。第二轮真实 DeepSeek 已覆盖全部 10 次任务：59 次请求、202,026 Token；baseline 独立通过 3/5（正常结束 3 次），verified 独立通过 4/5（正常结束 1 次，其余 3 次预算中断）。见 [第二轮结果与下一步](evaluation-results/2026-09-23-round2.md)。首轮预算截断结果另行保留。新增代码和文档尚未提交、推送。

## 任务范围与来源

固定以上提交的 `src/deepblue` 快照，每个任务只注入一个已声明的回归缺陷。模型得到任务说明、带缺陷的源码和公开检查，从独立副本修复。第二轮沿用相同任务与检查哈希，新增运行时提示同时应用于两种模式；预算与提示均变化，因此不能把跨轮差异单独归因于提示。独立检查保留在外部评测器中，不作为提示或修复反馈。

| 任务 | 集合 | 实际模块 | 要求 |
| --- | --- | --- | --- |
| completion-url | dev | config.py | 根 URL 与完整 endpoint 不重复拼接，保留安全校验 |
| recursive-glob | dev | tools/search.py | `**/*.py` 同时覆盖根目录与嵌套文件 |
| jsonl-tail | dev | web_sessions.py | 半行尾部等待追加，完整损坏行仍报错，不重复消息 |
| compaction-range | holdout | config.py | 范围边界、NaN 和无穷值校验 |
| web-storage | holdout | web.py | 项目默认存储与显式配置优先级，无创建/迁移副作用 |

任务定义见 [project_tasks.py](../scripts/project_tasks.py)。所有原版都须通过公开与独立检查，注入版本都须失败。两种模式初始文件哈希须相同。

这是**真实项目源码上的人为回归任务**，不是五个真实历史 Issue，也不是通用编码基准。保留集只表示不把独立检查交给 Agent、运行后不据其响应调参；评测作者可见检查，同源任务仍存在相关性。后续需外部项目、独立作者与真正未见任务。

## 对照与预算

- 5 任务 × baseline / verified，各一次；按任务交替两种模式的先后顺序。
- 同一个 DeepSeek 模型、供应商默认解码、关闭 thinking、最大输出 2048、每次任务最多 8 步；Agent 每轮临时系统上下文提示当前/剩余轮数、最小修复与单独检查，最后两轮提醒收敛，提示不写入原始会话、不改变工具消息配对；两组都关闭自动压缩，避免额外摘要预算混入本次验证机制对照。
- baseline 由 Agent 自行结束；verified 自动运行公开检查，失败最多修复一次，仍共享相同的 8 步上限。
- 全批最多 80 次请求尝试（失败请求也计数），1200 秒协作停止期限。第二轮开始按 10 次任务等分预算：每个任务对 16 次请求，再等分为每模式 8 次；每次任务另有 120 秒协作期限。额度不跨模式或任务转移。
- 第二轮默认 Token 总门槛为 400,000，每个任务对 80,000、每种模式 40,000（第一轮为全批共享 150,000）：每次请求前，用已报告 Token 加上 UTF-8 请求大小、最大输出与余量作保守准入估计。它不是精确分词器或金额硬上限；在途请求也无法由本地脚本撤销计费。
- 局部预算耗尽只停止当前任务，后续任务使用自己的完整额度；全局超时、缺失用量、认证失败或用户取消才停止后续任务。剩余任务记为未运行，不从计划分母删除。网络调用最长等待 30 秒，停止可能需要等待在途调用及进程清理。

## 独立检查与统计

每个模式独立复制快照。运行后检查公开检查文件及范围外文件的哈希；即使独立断言通过，越界修改也记失败。验收不能仅依赖模型说“完成”。执行结束但独立检查失败单独统计。独立检查通过与执行正常结束分开：已修好文件但因预算未能汇报的情况也保留预算原因，不能描述为正常结束。

每轮保存 `report.json` 和 `report.md`，含源码提交、评测脚本/任务定义 SHA256、模型、预算、初始哈希、变更文件、执行/验收状态、失败分类、Token 与耗时，按模式和 dev/holdout 分组。中断前已完成结果保留，不自动重跑收费任务。

同一用户权限的 Shell 不是安全隔离；Agent 理论上仍能访问副本外的文件。文件哈希检查也不能提供对抗篡改保证。原始会话和日志仅保存在本机 `.test-tmp`，Key 不传入模型工具环境或报告。

## 运行

在仓库根目录，Python 3.10 以上：

```powershell
$env:PYTHONPATH = "src"
python scripts/evaluate_project.py --validate
```

上面只验证夹具，不调用模型。真实执行需要显式配置 Key，并会收费：

```powershell
$env:PYTHONPATH = "src"
# DEEPSEEK_API_KEY / LLM_API_KEY 可从当前进程环境读取
python scripts/evaluate_project.py --live --model deepseek-v4-flash
# 也支持显式指定本地 Key 文件；不要将它提交到 Git
python scripts/evaluate_project.py --live --key-file .deepblue/evaluation-key.txt
```

Web 设置中的内存 Key 不会自动导出给独立评测进程。`.deepblue` 与 `.test-tmp` 被 Git 忽略。使用 Key 文件时由使用者在评测完成后自行移除；脚本不会把 Key 文件路径写入报告。

本阶段新增范围的提交、真实运行状态与结果见 Issue #17；未运行真实模型时不得把固定响应回归或 `--validate` 结果当成模型成功率。

## Harness 后续运行时变化

Issue #18 将预算客户端迁入核心，并增加停止后的最终配置检查。之后的新评测报告记录 `runtime_policy=budget-finalization-v1` 及预算/配置/验收模块哈希；这会影响验收行为，不能与第二轮混称同一运行时。原始第二轮报告和工作区快照不变。本阶段只运行无付费模型的回归，不自动重跑已使用保留集。

P0.3 后，新报告策略为 `calibrated-active-verification-v1`，记录 `budget_estimator` 与 `active_checks`；两模式使用同一估计策略，verified 才使用配置验收。可通过 `--budget-estimator conservative --active-checks 0` 关闭新行为进行消融，但仍不是历史运行时（P0.1/P0.2 等仍保留）。后续真实评测应使用新任务、固定这些选项并单独报告，不能重写第二轮结论。

P1.1 后策略为 `persistent-task-state-v1`，增加 task_update 工具与任务状态上下文，并记录 task_state/session 模块哈希；新运行与历史条件不同，仍需独立新实验记录。


## P1.2 导航消融准备（2026-09-30）

评测器增加 `--navigation on|off`，默认 on；off 只从模型工具声明和执行注册表移除 `symbols`、`project_checks`，其他工具、提示、预算和验收模式保持一致。它应用于该批次的 baseline/verified 两组；baseline/verified 仍表示是否自动验收，不表示导航开关。公共提示保持相同，其中导航建议在 off 组没有对应工具，因此本对照测量的是工具可用性，不是整套提示策略。

JSON 报告记录批次和逐次 navigation 设置；JSON/Markdown 按验收模式汇总工具调用、重复读取/失败及首次 write/edit 文件变化前调用均值与样本数。缺失计量不当作零；未观察到 write/edit 变化不纳入首次修改均值，Shell 改动仍不由此指标证明。成功率、Token、耗时和未运行分母继续保留。

仅验证夹具、不调用模型：

```powershell
python scripts/evaluate_project.py --validate --navigation off
```

后续真实实验应为新任务交替执行 on/off，固定模型、验收模式、预算和运行时，重复运行并保留完整分母。两次独立批次的导航开关本身不保证配对或交替调度。现有五个任务已使用，只可作回归；尚未新增未见任务，也未完成真实效率验收。此轮 8 项评测测试通过，包含工具确实不可调用、缺失指标与零值区分、固定源码夹具和原有预算/报告回归；未调用付费接口，未提交或推送。


## 导航配对试验集（2026-09-30）

新增 `--suite navigation --navigation paired --repeats 2`：两个新编写的源码回归任务（读取分页丢行、精确编辑重叠匹配），固定 e05688f 源码，尚未用于真实模型实验。它们是同源人工缺陷的 dev 试验集，不是独立保留集或充分的导航基准；提示/公开检查仍暴露模块线索，不能据此证明复杂仓库检索能力。

同批次执行每任务 × baseline/verified × on/off × 重复次数，下一次重复反转导航顺序，并交替验收模式顺序。每次使用独立副本；总请求与 Token 额度按完整计划次数等分、不转移。两任务重复两次共 16 次，若保持每次 8 请求和 40,000 Token，应显式设置总额 128 请求、640,000 Token；这只是配置示例，本轮没有付费执行。时间门槛仍为协作停止，不是硬时限。

```powershell
python scripts/evaluate_project.py --validate --suite navigation --navigation paired --repeats 2
```

报告按 baseline/on、baseline/off、verified/on、verified/off 分组，保留重复编号和未运行分母。夹具原版通过、注入后失败；10 项针对测试通过。真实实验、更多外部任务与成功率/成本结论仍待完成；代码未提交/推送。
