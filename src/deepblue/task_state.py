"""Durable task facts and separately attributed model notes."""
from __future__ import annotations

import copy

from .tools.base import Tool


NOTE_FIELDS = ('progress', 'blockers', 'next_step')


def begin(session, task_id, run_id, prompt, verification):
    previous = session.task_state
    if previous and previous['task_id'] == task_id:
        state = copy.deepcopy(previous)
        state['previous_runtime'] = {**copy.deepcopy(previous['runtime']), 'historical': True}
    else:
        state = dict(schema_version=1, task_id=task_id,
                     goal=prompt if prompt is not None else '旧会话未记录结构化目标；请参考原始对话。',
                     goal_source='user' if prompt is not None else 'legacy_unknown',
                     goal_message_index=len(session.messages) if prompt is not None else None,
                     notes=dict(source='model', progress='', blockers='', next_step=''))
    state['acceptance'] = ({'command': verification.command, 'cwd': str(verification.cwd),
                            'timeout': verification.timeout, 'max_repairs': verification.max_repairs}
                           if verification else None)
    state['runtime'] = dict(run_id=run_id, execution_status='running', verification_status='unverified',
                            touched_files=copy.deepcopy(previous['runtime'].get('touched_files', [])) if previous and previous['task_id'] == task_id else [], evidence=None)
    session.record_task_state(state)


def checkpoint(session, **updates):
    if not session.task_state:
        return
    state = copy.deepcopy(session.task_state)
    state['runtime'].update(updates)
    session.record_task_state(state)


def view(session, *, context=False):
    if not session.task_state:
        return None
    state = copy.deepcopy(session.task_state)
    runtime = state['runtime']
    if runtime.get('verification_status') == 'passed':
        from .verification import current_status
        from pathlib import Path
        evidence = runtime.get('evidence')
        if evidence:
            # Same exclusions as the source verification, reconstructed from its own snapshot.
            excluded = tuple(Path(p) for p in evidence.get('workspace_after', {}).get('excluded_paths', []))
            runtime['verification_status'] = current_status(
                {'verification_status': 'passed', 'evidence': [evidence]}, Path(session.header['cwd']), excluded)
    if getattr(session, 'task_recovered', False) and runtime.get('execution_status') == 'running':
        runtime['execution_status'] = 'interrupted'
    if context:
        state['goal_truncated'] = len(state['goal']) > 6000
        state['goal'] = state['goal'][:6000]
        state['runtime']['touched_files'] = runtime.get('touched_files', [])[-30:]
    return state


def note_tool(session):
    def update(context, args):
        if not session.task_state:
            raise ValueError('没有活动任务。')
        if not args or any(not isinstance(v, str) or len(v) > 2000 for v in args.values()):
            raise ValueError('至少提供一项笔记，每项最多 2000 字符。')
        state = copy.deepcopy(session.task_state)
        state['notes'].update(args)
        session.record_task_state(state)
        return {'ok': True, 'task_id': state['task_id'], 'source': 'model',
                'notice': '笔记已保存；执行与验收状态只能由运行时更新。'}
    return Tool('task_update', '保存任务进度、阻碍和下一步笔记（模型陈述，不是验收证据）。阶段变化时更新，不要每轮重复。',
                {name: {'type': 'string'} for name in NOTE_FIELDS}, [], update)


def evidence_reference(check):
    result = {key: check.get(key) for key in ('verification_id', 'status', 'command', 'cwd', 'exit_code', 'evidence_path')}
    after = check.get('workspace_after')
    if after:
        result['workspace_after'] = {key: after[key] for key in ('sha256', 'excluded_paths')}
    return result
