"""Opt-in real-source regression evaluation; never run by ordinary tests."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from pathlib import Path
from zipfile import ZipFile

from deepblue import __version__
from deepblue.agent import Agent
from deepblue.budget import BudgetClient
from deepblue.config import Config
from deepblue.evaluation import python_command, summarize
from deepblue.llm import DeepSeekClient, ModelError
from deepblue.prompts import build_system_prompt
from deepblue.session import Session
from deepblue.tools import ToolContext, create_tools
from deepblue.verification import VerificationConfig
from project_tasks import COMMON, REVISION, TASKS

REPO = Path(__file__).resolve().parents[1]


def snapshot():
    raw = subprocess.check_output(['git', 'archive', '--format=zip', REVISION, 'src/deepblue'], cwd=REPO)
    with ZipFile(io.BytesIO(raw)) as archive:
        return {name: archive.read(name) for name in archive.namelist() if not name.endswith('/')}


def prepare(task, root, sources, broken=True):
    root.mkdir(parents=True, exist_ok=False)
    for name, data in sources.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if broken:
        target = root / task['file']
        text = target.read_text(encoding='utf-8')
        if text.count(task['before']) != 1:
            raise ValueError('Frozen mutation no longer matches source: ' + task['id'])
        target.write_text(text.replace(task['before'], task['after']), encoding='utf-8', newline='\n')
    (root / 'check.py').write_text(COMMON + task['public'], encoding='utf-8', newline='\n')


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def independent(task, root):
    env = {k: v for k, v in os.environ.items() if k not in ('DEEPSEEK_API_KEY', 'LLM_API_KEY')}
    result = subprocess.run([sys.executable, '-B', '-c', COMMON + task['public'] + task['independent']],
                            cwd=root, env=env, capture_output=True, timeout=20, text=True, encoding='utf-8',
                            **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    return {'status': 'passed' if result.returncode == 0 else 'failed', 'exit_code': result.returncode,
            'output': (result.stdout + result.stderr)[-12000:]}



def allocate(budget, planned_runs, seconds):
    return dict(calls=0, tokens=0, max_calls=budget['max_calls']//planned_runs,
                max_tokens=budget['max_tokens']//planned_runs, unknown_usage=False,
                deadline=min(budget['deadline'], time.monotonic()+seconds))


def failure(record, messages):
    if record.get('budget_stop'):
        return record['budget_stop']
    if record.get('integrity_violations'):
        return 'scope_or_check_modified'
    if record.get('harness_error'):
        return 'harness_error'
    if record['independent_status'] == 'passed':
        return None
    text = '\n'.join(messages)
    for pattern, category in [('HTTP 401', 'authentication'), ('HTTP 429', 'rate_limit'),
                              ('请求超时', 'network_timeout'), ('[DONE]', 'stream_disconnect')]:
        if pattern in text:
            return category
    status = record.get('execution_status')
    return {'step_limit': 'step_limit', 'cancelled': 'cancelled', 'error': 'model_or_runtime_error',
            'incomplete': 'output_truncated'}.get(status, 'independent_check_failed')


def evaluation_tools(context, navigation="on"):
    if navigation not in {"on", "off"}:
        raise ValueError("Unknown navigation variant")
    registry = create_tools(context)
    if navigation == "off":
        for name in ("symbols", "project_checks"):
            registry.tools.pop(name)
    return registry


def navigation_summary(records):
    # Missing metrics (old reports or unstarted runs) are not zero observations.
    measured = [r['tool_metrics'] for r in records if r.get('tool_metrics')]
    totals = {key: sum(m.get(key, 0) for m in measured) for key in
              ('tool_calls', 'read_calls', 'repeated_reads', 'failed_calls',
               'repeated_failures', 'tool_seconds')}
    first = [m['calls_before_first_file_change'] for m in measured
             if m.get('calls_before_first_file_change') is not None]
    return dict(measured_runs=len(measured), totals=totals,
                first_change_runs=len(first),
                mean_calls_before_first_file_change=sum(first)/len(first) if first else None)


def schedule(tasks, navigation, repeats):
    variants = ('on', 'off') if navigation == 'paired' else (navigation,)
    for repeat in range(1, repeats + 1):
        for index, task in enumerate(tasks):
            modes = ('baseline', 'verified') if (index + repeat) % 2 else ('verified', 'baseline')
            for mode in modes:
                order = variants if (index + repeat) % 2 else variants[::-1]
                for variant in order:
                    yield task, mode, variant, repeat


def summary(records):
    result = {}
    groups = sorted({(r['mode'], r.get('navigation', 'legacy')) for r in records})
    paired = len({n for _, n in groups}) > 1
    for base_mode, variant in groups:
        mode = base_mode + '/' + variant if paired else base_mode
        selected = [r for r in records if r['mode'] == base_mode and r.get('navigation', 'legacy') == variant]

        result[mode] = summarize(selected)
        result[mode]['planned_runs'] = len(selected)
        result[mode]['navigation_metrics'] = navigation_summary(selected)
        result[mode]['started_runs'] = sum(r.get('execution_status') != 'not_run' for r in selected)
        result[mode]['not_run'] = sum(r.get('execution_status') == 'not_run' for r in selected)
        result[mode]['finished_successes'] = sum(r.get('execution_status') == 'finished' and r.get('independent_status') == 'passed' for r in selected)
        result[mode]['passed_but_interrupted'] = result[mode]['successes'] - result[mode]['finished_successes']
        result[mode]['failure_categories'] = {c: sum(r.get('failure_category') == c for r in selected)
                                              for c in sorted({r['failure_category'] for r in selected if r.get('failure_category')})}
        result[mode]['splits'] = {s: summarize([r for r in selected if r['split'] == s]) for s in ('dev', 'holdout')}
    return result


def markdown_report(report):
    lines = ['# DeepBlue 项目源码回归评测', '',
             f"模型：{report['model']}；固定任务源码：`{report['source_revision']}`。", '',
             '人为注入的回归缺陷，不是历史 Issue 基准；重复次数及导航条件见报告；不据小样本判断总体优劣。', '',
             '| 模式 | 独立通过 / 计划 | 正常结束且通过 | 已通过但执行中断 | 已启动 | 未运行 | Token | 耗时秒 |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for mode, group in report['summary'].items():
        lines.append(f"| {mode} | {group['successes']} / {group['planned_runs']} | {group['finished_successes']} | {group['passed_but_interrupted']} | {group['started_runs']} | {group['not_run']} | {group['total_tokens']} | {group['elapsed_seconds']:.2f} |")
    lines += ['', '| 任务 | 集合 | 模式 | 导航 | 重复 | 执行 | 独立检查 | 失败分类 |', '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for r in report['records']:
        lines.append('| ' + ' | '.join(str(r.get(k) or '—') for k in
                     ('task_id','split','mode','navigation','repeat','execution_status','independent_status','failure_category')) + ' |')
    lines += ['', '导航工具：`' + report.get('navigation', '未记录') + '`；缺失计量不按零计入。',
              '', '| 模式 | 有计量运行 | 工具调用 | 重复读取 | 重复失败 | 首次文件修改前调用均值（样本数） |',
              '| --- | --- | --- | --- | --- | --- |']
    for mode, group in report['summary'].items():
        metrics = group.get('navigation_metrics', {})
        totals = metrics.get('totals', {})
        lines.append(f"| {mode} | {metrics.get('measured_runs', 0)} | {totals.get('tool_calls', '—')} | {totals.get('repeated_reads', '—')} | {totals.get('repeated_failures', '—')} | {metrics.get('mean_calls_before_first_file_change')} ({metrics.get('first_change_runs', 0)}) |")
    lines += ['', '详细用量、逐项证据、预算停止与范围检查见同目录 report.json。',
              '保留集仅指未提供给 Agent/未用模型结果调参；同一作者、同一项目，不具备独立外部基准或隔离保证。',
              'Token 为接口已返回的用量，不包含无法计量的失败请求，不等于账单金额。']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description='固定源码的 5 个回归任务；--live 才调用收费 API')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--validate', action='store_true', help='仅验证原版通过/注入后失败，不调用 API')
    parser.add_argument('--key-file', type=Path, help='显式本地 Key 文件；不写入报告')
    parser.add_argument('--model', default=os.getenv('DEEPSEEK_MODEL') or os.getenv('LLM_MODEL') or 'deepseek-v4-flash')
    parser.add_argument('--budget-estimator', choices=['calibrated', 'conservative'], default='calibrated')
    parser.add_argument('--navigation', choices=['on', 'off', 'paired'], default='on',
                        help='导航工具消融；off 仅移除 symbols/project_checks，两种验收模式均应用')
    parser.add_argument('--suite', choices=['legacy', 'navigation'], default='legacy')
    parser.add_argument('--repeats', type=int, choices=range(1, 11), default=1)
    parser.add_argument('--active-checks', type=int, choices=range(11), default=2)
    parser.add_argument('--max-calls', type=int, default=80)
    parser.add_argument('--token-budget', type=int, default=400000)
    parser.add_argument('--seconds', type=int, default=1200)
    parser.add_argument('--max-steps', type=int, default=8)
    parser.add_argument('--run-seconds', type=int, default=120)
    parser.add_argument('--output', type=Path, default=REPO / '.test-tmp' / 'project-evaluations')
    args = parser.parse_args()
    from navigation_tasks import TASKS as NAVIGATION_TASKS
    tasks = NAVIGATION_TASKS if args.suite == 'navigation' else TASKS
    runs = list(schedule(tasks, args.navigation, args.repeats))
    if args.live == args.validate:
        parser.error('选择 --validate 或 --live；真实运行收费。')
    if not all(v > 0 for v in (args.max_calls, args.token_budget, args.seconds, args.max_steps, args.run_seconds)):
        parser.error('预算必须为正数。')
    if args.max_calls < len(runs) or args.token_budget < len(runs):
        parser.error('总预算不足以为每次任务分配额度。')
    key = ''
    if args.live:
        key = (args.key_file.read_text(encoding='utf-8-sig').strip() if args.key_file else
               os.getenv('DEEPSEEK_API_KEY') or os.getenv('LLM_API_KEY') or '')
        if not key:
            parser.error('请通过环境变量或 --key-file 配置 Key；Web 内存 Key 不会导出。')
    sources = snapshot()
    output = args.output.resolve() / uuid.uuid4().hex[:12]
    output.mkdir(parents=True)
    if args.validate:
        for task in tasks:
            for broken in (False, True):
                root = output / (task['id'] + ('-broken' if broken else '-reference'))
                prepare(task, root, sources, broken)
                checked = independent(task, root)
                assert (checked['status'] == 'passed') != broken, (task['id'], checked)
            print('PASS ' + task['id'], flush=True)
        print(f'{len(tasks)} 个原版通过，{len(tasks)} 个注入缺陷失败；计划 {len(runs)} 次运行；未调用 API。')
        return 0
    budget = dict(calls=0, tokens=0, max_calls=args.max_calls, max_tokens=args.token_budget,
                  deadline=time.monotonic() + args.seconds, unknown_usage=False)
    report = dict(version=__version__, source_revision=REVISION,
                  evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  suite_sha256=hashlib.sha256(Path(__file__).with_name('navigation_tasks.py' if args.suite == 'navigation' else 'project_tasks.py').read_bytes()).hexdigest(),
                  model=args.model, platform=platform.platform(), python=platform.python_version(),
                  decoding='provider defaults; thinking disabled; no auto compaction; max output 2048',
                  planned_runs=len(runs), max_steps=args.max_steps, max_calls=args.max_calls,
                  token_budget=args.token_budget, wall_seconds=args.seconds, run_seconds=args.run_seconds,
                  allocation='equal non-transferable per task/mode', runtime_guidance='remaining-steps-v1',
                  runtime_policy='skills-commands-v1',
                  budget_estimator=args.budget_estimator, active_checks=args.active_checks,
                  navigation=args.navigation, suite=args.suite, repeats=args.repeats,
                  runtime_sha256={name: hashlib.sha256((REPO/'src/deepblue'/name).read_bytes()).hexdigest()
                                  for name in ('budget.py', 'config.py', 'verification.py', 'task_state.py', 'session.py', 'tool_metrics.py', 'recovery.py', 'tools/navigation.py', 'hooks.py', 'capabilities.py', 'permissions.py', 'tools/base.py')},
                  agent_sha256=hashlib.sha256((REPO/'src/deepblue/agent.py').read_bytes()).hexdigest(),
                  prompts_sha256=hashlib.sha256((REPO/'src/deepblue/prompts.py').read_bytes()).hexdigest(), records=[])
    def save():
        report['summary'] = summary(report['records'])
        report['budget'] = {k: v for k, v in budget.items() if k != 'deadline'}
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if key:
            text = text.replace(key, '[REDACTED]')
        temporary = output / 'report.tmp'
        temporary.write_text(text, encoding='utf-8')
        temporary.replace(output / 'report.json')
        markdown = markdown_report(report)
        if key:
            markdown = markdown.replace(key, '[REDACTED]')
        (output / 'report.md').write_text(markdown, encoding='utf-8')
    print('REPORT ' + str(output / 'report.json'), flush=True)
    halted = None
    for task, mode, variant, repeat in runs:
        record = dict(task_id=task['id'], split=task['split'], mode=mode, source_file=task['file'],
                      execution_status='not_run', independent_status='not_run', usage={}, navigation=variant, repeat=repeat)
        if halted:
            record['failure_category'] = halted
            report['records'].append(record); save(); continue
        print('START ' + task['id'] + ' / ' + mode, flush=True)
        run = output / (task['id'] + '-' + mode + '-' + variant + '-' + str(repeat))
        root = run / 'workspace'
        session = None
        client = None
        messages = []
        started = time.monotonic()
        allocation = allocate(budget, report['planned_runs'], args.run_seconds)
        tool_counts = {}
        try:
            prepare(task, root, sources)
            before = hashes(root)
            record['initial_sha256'] = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
            config = Config(root, key, budget_estimator=args.budget_estimator, active_checks=args.active_checks, home=run / 'sessions', model=args.model,
                            base_url=os.getenv('DEEPSEEK_BASE_URL') or os.getenv('LLM_BASE_URL') or 'https://api.deepseek.com',
                            max_steps=args.max_steps, max_tokens=2048, max_context_bytes=64000,
                            request_timeout=30, shell_timeout=20, stream=False, auto_compact=False)
            cancelled = lambda: time.monotonic() >= allocation['deadline']
            session = Session.create(config.home, root, config.model, build_system_prompt(root))
            client = BudgetClient(DeepSeekClient(config, cancelled=cancelled), budget, 2048, allocation, estimator_mode=args.budget_estimator)
            check = VerificationConfig(python_command("exec(open('check.py', encoding='utf-8').read())"), root, 20, 1) if mode == 'verified' else None
            def emit(kind, data):
                if kind == 'tool_start':
                    name = data['name']; tool_counts[name] = tool_counts.get(name, 0)+1
                if kind in ('notice', 'error'):
                    messages.append(str(data.get('text', '')))
            result = Agent(config, client, evaluation_tools(ToolContext(root, session.artifacts, 20), variant), session,
                           emit, check, cancelled=cancelled).run(task['prompt'] +
                '\n仅修改 ' + task['file'] + '，不要修改 check.py 或其他文件。可运行 check.py 查看公开示例。' +
                '\nPython 解释器：' + sys.executable + '。不要调用外部模型或网络服务。')
            record.update(execution_status=result.execution_status, verification_status=result.verification_status,
                          steps=result.steps)
            after = hashes(root)
            changed = [name for name in before.keys() | after.keys() if before.get(name) != after.get(name)]
            record['changed_files'] = sorted(changed)
            record['integrity_violations'] = sorted(name for name in changed if name != task['file'])
            checked = independent(task, root)
            record.update(independent_status=checked['status'], independent_evidence=checked)
            if record['integrity_violations']:
                record['independent_status'] = 'failed'
        except KeyboardInterrupt:
            record['execution_status'] = 'cancelled'; halted = 'cancelled'
        except Exception as exc:
            record['harness_error'] = type(exc).__name__ + ': ' + str(exc)
            record['execution_status'] = 'error'
        finally:
            if session:
                record['usage'] = dict(session.usage)
                record['tool_metrics'] = (session.last_run or {}).get('tool_metrics', {})
                session.close()
            if client and client.reason:
                record['budget_stop'] = client.reason
                record['budget_scope'] = client.scope
                if client.scope == 'global':
                    halted = client.reason
            if budget['unknown_usage']:
                record['budget_stop'] = 'usage_unavailable'; halted = 'usage_unavailable'
            if time.monotonic() >= budget['deadline']:
                record['budget_stop'] = 'time_budget'; halted = 'time_budget'
                record['budget_scope'] = 'global'
            elif time.monotonic() >= allocation['deadline']:
                record['budget_stop'] = 'time_budget'; record['budget_scope'] = 'run'
            record['allocation'] = {k:v for k,v in allocation.items() if k != 'deadline'}
            record['tool_counts'] = tool_counts
            record['failure_category'] = failure(record, messages)
            if record['failure_category'] == 'authentication':
                halted = 'authentication'
            record['diagnostics'] = messages[-6:]
            record['elapsed_seconds'] = round(time.monotonic() - started, 3)
            report['records'].append(record); save()
        print(record['independent_status'].upper() + ' ' + str(record['failure_category']), flush=True)
    report['complete'] = not halted
    report['all_runs_started'] = all(r['execution_status'] != 'not_run' for r in report['records'])
    report['all_runs_finished'] = all(r['execution_status'] == 'finished' for r in report['records'])
    save()
    return 0 if not halted else 2


if __name__ == '__main__':
    raise SystemExit(main())
