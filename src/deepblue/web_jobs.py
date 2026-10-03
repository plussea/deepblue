"""Single-executor jobs with durable identities, bounded events and presentation snapshots."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque

from .session import Session, project_sessions
from .prompts import build_system_prompt
from .web_sessions import atomic_json


class JobManager:
    def init_jobs(self):
        self.closing = False
        from .message_queue import MessageQueue
        self.inbox = MessageQueue(project_sessions(self.home, self.cwd) / "queue.sqlite3")
        self.inbox.recover()
        self.queue_keys = {}
        self.jobs = {}
        self.job = None
        self.job_dir = project_sessions(self.home, self.cwd) / 'web-jobs'
        for path in sorted(self.job_dir.glob('*.json'), key=lambda p: p.stat().st_mtime_ns):
            try:
                saved = json.loads(path.read_text(encoding='utf-8'))
                if not re.fullmatch('[a-f0-9]{32}', saved['id']):
                    continue
                saved.update(events=deque(maxlen=2000), sequence=0, timeline=[], cancel_path=path.with_suffix('.cancel'))
                if not saved.get('finished'):
                    saved.update(finished=True, status='interrupted', outcome={'execution_status': 'unknown', 'verification_status': 'unverified'})
                    saved['cancel_path'].touch()  # cooperative notification to an orphan, never kill by stale PID
                self.jobs[saved['id']] = saved
                self.job = saved
                self.persist_job(saved)
            except (ValueError, KeyError, OSError):
                continue

    def persist_job(self, job):
        public = {k: job.get(k) for k in ('id', 'session_id', 'request_id', 'request_hash', 'finished', 'status', 'created', 'outcome', 'baseline', 'notices')}
        atomic_json(self.job_dir / (job['id'] + '.json'), public)

    def append_event(self, job, kind, data):
        data = self.redact(data)
        with self.lock:
            job['sequence'] += 1
            item = {'id': job['sequence'], 'kind': kind, 'data': data}
            job['events'].append(item)
            if kind == 'text_delta':
                if job['timeline'] and job['timeline'][-1]['kind'] == 'text_delta':
                    job['timeline'][-1]['data']['text'] = (job['timeline'][-1]['data']['text'] + data['text'])[-128000:]
                else:
                    job['timeline'].append(json.loads(json.dumps(item)))
            elif kind != 'model_start':
                job['timeline'].append(item)
            job['timeline'] = job['timeline'][-200:]
            if kind == 'session':
                job['session_id'] = data['id']
            if kind == 'done':
                job['outcome'] = data
            if kind == 'error':
                job['has_error'] = True
            if kind == 'error' or (kind == 'notice' and not str(data.get('text', '')).startswith(('执行：', '正在压缩历史', '生成第'))):
                # Keep bounded, already-redacted diagnostics after transcript refresh/restart.
                job['notices'] = ((job.get('notices') or []) + [str(data.get('text', ''))[:2000]])[-6:]

    def start(self, data, *, frozen_settings=None, frozen_key=None):
        request_id = data.get('request_id') or uuid.uuid4().hex
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}', request_id):
            raise ValueError('无效请求 ID。')
        request_hash = hashlib.sha256(json.dumps({k: data.get(k) for k in ('session_id', 'action', 'prompt', 'verify')}, sort_keys=True).encode()).hexdigest()
        with self.lock:
            settings = dict(self.settings if frozen_settings is None else frozen_settings)
            if self.closing:
                raise ValueError('服务正在关闭，任务未启动。')
            for existing in self.jobs.values():
                if existing.get('request_id') == request_id:
                    if existing['request_hash'] != request_hash:
                        raise ValueError('请求 ID 已用于不同内容。')
                    return {'job_id': existing['id'], 'session_id': existing['session_id'], 'reused': True}
            if self.job and not self.job['finished']:
                raise ValueError('当前任务仍在执行，请等待或停止。')
            # Old workers may still be finishing cooperative cancellation after server restart.
            for old in self.jobs.values():
                if old['status'] == 'interrupted' and old.get('session_id'):
                    probe = Session(self.session_path(old['session_id']))
                    probe.close()
            action = data.get('action', 'run')
            if action not in {'run', 'compact', 'retry'}:
                raise ValueError('无效操作。')
            prompt, command, session_id = data.get('prompt', ''), data.get('verify', ''), data.get('session_id')
            if command and settings.get("permission_mode", "trusted") != "trusted":
                raise ValueError("受限模式禁止命令验收。")
            if not isinstance(prompt, str) or len(prompt) > 64000 or (action == 'run' and not prompt.strip()):
                raise ValueError('任务不能为空且须少于 64000 字符。')
            if not isinstance(command, str) or len(command) > 4000:
                raise ValueError('验收命令过长。')
            references = []
            for match in re.finditer(r'@("(?:\\.|[^"\\])*")', prompt):
                path = self.safe_path(json.loads(match.group(1)))
                if not path.is_file():
                    raise ValueError('引用文件不存在。')
                references.append(path.relative_to(self.cwd).as_posix())
            if session_id:
                if not self.session_path(session_id).is_file():
                    raise ValueError('会话不存在。')
                if self.metadata(session_id).get('archived'):
                    raise ValueError('请先恢复归档会话。')
            elif action != 'run':
                raise ValueError('请先选择会话。')
            else:
                session = Session.create(self.home, self.cwd, settings['model'], build_system_prompt(self.cwd))
                session_id = session.header['id']
                session.close()
            job_id = uuid.uuid4().hex
            try:
                from .web_review import status
                baseline = status(self)
            except ValueError as exc:
                baseline = {'available': False, 'files': [], 'reason': str(exc)}
            job = dict(id=job_id, session_id=session_id, request_id=request_id, request_hash=request_hash,
                       created=time.time(), status='running', finished=False, sequence=0, events=deque(maxlen=2000),
                       timeline=[], cancel_path=self.job_dir / (job_id + '.cancel'), baseline=baseline, outcome=None)
            self.persist_job(job)  # must succeed before dispatch
            self.jobs[job_id] = self.job = job
            if action == 'run':
                self.append_event(job, 'user', {'text': prompt})
            resolved_prompt = prompt + ('\n\n引用的项目文件（请通过 read 工具读取）：\n' + '\n'.join(references) if references else '')
            options = {**settings, 'session_id': session_id, 'action': action, 'prompt': prompt if action == 'run' else None,
                       'queue_path': self.inbox.path, 'job_id': job_id, 'verify': command, 'cancel_path': str(job['cancel_path'])}
            if action == 'run':
                options['prompt'] = resolved_prompt
            key = self.api_key() if frozen_key is None else frozen_key
            if key:
                self.secrets.add(key)
            threading.Thread(target=self.run_worker, args=(job, options, key), daemon=True).start()
            return {'job_id': job_id, 'session_id': session_id}

    def run_worker(self, job, options, key):
        try:
            env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'DEEPSEEK_API_KEY': key, 'LLM_API_KEY': ''}
            flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            with subprocess.Popen([sys.executable, '-m', 'deepblue.web_worker'], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', env=env, **flags) as process:
                process.stdin.write(json.dumps(options, ensure_ascii=False))
                process.stdin.close()
                for line in process.stdout:
                    try:
                        event = json.loads(line)
                        self.append_event(job, event['kind'], event['data'])
                    except (ValueError, KeyError, TypeError):
                        self.append_event(job, 'error', {'text': line[:2000]})
                process.wait()
                if process.returncode:
                    self.append_event(job, 'error', {'text': f'工作进程退出：{process.returncode}；请核对恢复报告。'})
        except Exception as exc:
            self.append_event(job, 'error', {'text': str(exc)})
        finally:
            with self.lock:
                outcome = (job.get('outcome') or {}).get('execution_status')
                job['status'] = ('failed' if job.get('has_error') or not outcome else
                                 'cancelled' if outcome == 'cancelled' else
                                 'completed' if outcome == 'finished' else 'failed')
                job['finished'] = True
                self.persist_job(job)
            try:
                if job['status'] == 'completed' and not self.closing:
                    self.dispatch_followup(job['id'])
                else:
                    self.inbox.release_job(job['id'])
            except Exception as exc:
                self.append_event(job, 'error', {'text': '后续队列暂停：' + str(exc)})
                self.persist_job(job)

    def events(self, after=0, job_id=None, snapshot=False):
        with self.lock:
            job = self.jobs.get(job_id) if job_id else self.job
            if job_id and not job:
                raise ValueError('任务不存在，请重新选择会话。')
            if not job:
                return {'events': [], 'finished': True, 'sequence': 0}
            truncated = bool(after > job['sequence'] or (job['events'] and after < job['events'][0]['id'] - 1))
            result = dict(job_id=job['id'], session_id=job['session_id'], status=job['status'], outcome=job.get('outcome'),
                          finished=job['finished'], sequence=job['sequence'], truncated=truncated,
                          events=[e for e in job['events'] if e['id'] > after])
            if truncated or snapshot:
                result.update(snapshot=json.loads(json.dumps(job['timeline'])), events=[])
            return result

    def cancel(self, job_id=None):
        with self.lock:
            job = self.jobs.get(job_id) if job_id else self.job
            if job_id and not job:
                raise ValueError('任务不存在。')
            if job and not job['finished']:
                job['cancel_path'].touch()
                job['status'] = 'cancelling'
                self.persist_job(job)
            return {'ok': True, 'job_id': job['id'] if job else None}
