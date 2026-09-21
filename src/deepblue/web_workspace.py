"""Session, settings, files and registered evidence services for the local UI."""
from __future__ import annotations

import json
import os
import codecs
from pathlib import Path

from .config import Config
from .llm import DeepSeekClient
from .session import project_sessions
from .verification import current_status
from .web_sessions import SessionIndex, atomic_json, message_page


def read_log_page(path, offset):
    with path.open('rb') as file:
        file.seek(max(0, offset))
        raw = file.read(32000)
        end = file.tell()
        more = bool(file.read(1))
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    content = decoder.decode(raw, final=not more)
    end -= len(decoder.getstate()[0])
    return content, end, more


class WorkspaceFeatures:
    def init_features(self):
        self.index = SessionIndex()
        self.memory_key = ''
        self.secrets = set()
        self.env_fields = {'model': ('DEEPSEEK_MODEL', 'LLM_MODEL'), 'base_url': ('DEEPSEEK_BASE_URL', 'LLM_BASE_URL'),
                           'timeout': ('LLM_TIMEOUT_SECONDS',), 'api_key': ('DEEPSEEK_API_KEY', 'LLM_API_KEY')}

    def api_key(self):
        return os.getenv('DEEPSEEK_API_KEY') or os.getenv('LLM_API_KEY') or self.memory_key

    def redact(self, data):
        keys = sorted(self.secrets | {self.api_key()}, key=len, reverse=True)
        def visit(value):
            if isinstance(value, str):
                for key in keys:
                    if key:
                        value = value.replace(key, '[REDACTED]')
                return value
            if isinstance(value, dict):
                return {k: visit(v) for k, v in value.items()}
            if isinstance(value, list):
                return [visit(v) for v in value]
            return value
        return visit(data)

    def configuration(self):
        with self.lock:
            sources = {k: next((n for n in names if os.getenv(n)), 'memory' if k == 'api_key' and self.memory_key else 'startup')
                       for k, names in self.env_fields.items()}
            return dict(model=self.settings['model'], base_url=self.settings['base_url'], timeout=self.settings['timeout'],
                        configured=bool(self.api_key()), sources=sources)

    def configure(self, data):
        with self.lock:
            settings, key = dict(self.settings), self.memory_key
            for name in ('model', 'base_url', 'timeout', 'api_key'):
                if name not in data:
                    continue
                if any(os.getenv(n) for n in self.env_fields[name]):
                    raise ValueError(f'{name} 由环境变量管理，请在启动环境中修改。')
                value = data[name]
                if name == 'timeout':
                    if isinstance(value, bool) or not isinstance(value, (float, int)) or not 0.1 <= value <= 300:
                        raise ValueError('超时须在 0.1–300 秒之间。')
                elif not isinstance(value, str) or len(value) > 2000 or '\n' in value or '\r' in value:
                    raise ValueError('配置值无效。')
                if name == 'api_key':
                    key = value.strip()
                else:
                    settings[name] = value
            Config(self.cwd, key or self.api_key() or 'validation-only', home=self.home, model=settings['model'],
                   base_url=settings['base_url'], request_timeout=settings['timeout'])
            if self.api_key():
                self.secrets.add(self.api_key())
            self.memory_key = key
            if key:
                self.secrets.add(key)
            self.settings = settings
            return self.configuration()

    def test_connection(self):
        with self.lock:
            config = Config(self.cwd, self.api_key(), home=self.home, model=self.settings['model'],
                            base_url=self.settings['base_url'], request_timeout=self.settings['timeout'], max_tokens=16, stream=False)
        completion = DeepSeekClient(config).complete([{'role': 'user', 'content': '只回答 OK'}], [])
        return {'ok': True, 'model': config.model, 'usage': completion.usage}

    def metadata(self, session_id):
        path = self.session_path(session_id).with_suffix('.web.json')
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        return {}

    def edit_session(self, data):
        session_id = data.get('id')
        path = self.session_path(session_id)
        if not path.is_file():
            raise ValueError('会话不存在。')
        with self.lock:
            meta = self.metadata(session_id)
            if 'title' in data:
                title = data['title']
                if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
                    raise ValueError('标题须为 1–120 字符。')
                meta['title'] = title.strip()
            if 'archived' in data:
                if type(data['archived']) is not bool:
                    raise ValueError('归档状态须为布尔值。')
                if self.job and not self.job['finished'] and self.job['session_id'] == session_id:
                    raise ValueError('运行中会话不能归档。')
                meta['archived'] = data['archived']
            atomic_json(path.with_suffix('.web.json'), meta)
            return meta

    def sessions(self, cursor=0, limit=50, query='', archived=False, paged=False):
        if len(query) > 200:
            raise ValueError('搜索词不能超过 200 字符。')
        paths = sorted(project_sessions(self.home, self.cwd).glob('*.jsonl'), key=lambda p: (p.stat().st_mtime_ns, p.stem), reverse=True)
        result, errors = [], 0
        with self.index.lock:
            for path in paths:
                try:
                    meta = self.metadata(path.stem)
                    if bool(meta.get('archived')) != archived:
                        continue
                    session = self.index.read(path, self.cwd)
                    title = meta.get('title') or next((m.get('content', '')[:60] for m in session.messages if m['role'] == 'user'), '新会话')
                    if query and query.casefold() not in title.casefold() and not any(query.casefold() in (m.get('content') or '').casefold() for m in session.messages):
                        continue
                    result.append(dict(id=path.stem, title=title, model=session.header['model'], modified=path.stat().st_mtime, archived=archived))
                except (ValueError, OSError, KeyError):
                    errors += 1
        start, size = max(0, int(cursor)), max(1, min(100, int(limit)))
        page = result[start:start + size]
        return {'items': page, 'next': start + size if start + size < len(result) else None, 'total': len(result), 'unreadable': errors} if paged else result

    def state(self, session_id, before=None, limit=100):
        with self.index.lock:
            session = self.index.read(self.session_path(session_id), self.cwd)
            page = message_page(session, before, limit)
            # No writer lock is acquired. Only complete newline-terminated records are observed.
            context = session.context_messages()
            with self.lock:
                job = next((j for j in reversed(list(self.jobs.values())) if j['session_id'] == session_id), {})
                job_status = job.get('status')
                job_notices = list(job.get('notices') or []) if job.get('finished') else []
                job_outcome = dict(job.get('outcome') or {})
            return self.redact(dict(id=session_id, **page, model=session.header['model'], usage=session.usage,
                compactions=session.compaction_count, context_bytes=len(json.dumps(context, ensure_ascii=False).encode()),
                context_limit=self.settings['max_context_bytes'], recovery=session.refresh_recovery(),
                verification_status=current_status(session.last_run, self.cwd, (self.home,)),
                last_run=session.last_run, metadata=self.metadata(session_id), evidence=self.evidence_list(session), job_status=job_status,
                job_notices=job_notices, job_outcome=job_outcome,
                logs=[dict(id=o['operation_id'], name=o['name'], phase=o['phase']) for o in session.operations.values() if o.get('log_path')]))

    def export(self, session_id, format):
        with self.index.lock:
            session = self.index.read(self.session_path(session_id), self.cwd)
            data = self.redact({'session': session.header, 'messages': session.messages})
            if format == 'json':
                return {'content': json.dumps(data, ensure_ascii=False, indent=2), 'filename': session_id + '.json'}
            if format != 'md':
                raise ValueError('仅支持 md/json 导出。')
            blocks = ['# DeepBlue 会话 ' + session_id]
            for msg in data['messages']:
                blocks.append('## ' + msg['role'] + '\n\n' + (msg.get('content') or ''))
                if msg.get('tool_calls'):
                    blocks.append('```json\n' + json.dumps(msg['tool_calls'], ensure_ascii=False, indent=2) + '\n```')
            return {'content': '\n\n'.join(blocks), 'filename': session_id + '.md'}

    def message_content(self, session_id, index, offset=0):
        with self.index.lock:
            session = self.index.read(self.session_path(session_id), self.cwd)
            if not 0 <= index < len(session.messages):
                raise ValueError('消息不存在。')
            content = session.messages[index].get('content') or ''
            start = max(0, offset)
            return self.redact({'content': content[start:start + 32000], 'next': start + 32000 if start + 32000 < len(content) else None})

    def evidence_list(self, session):
        return [dict(id=e['verification_id'], **{k: e.get(k) for k in
                ('command', 'cwd', 'exit_code', 'elapsed_seconds', 'status', 'error', 'workspace_before', 'workspace_after')})
                for run in session.runs.values() for e in run.get('evidence', []) if e.get('verification_id')]

    def evidence(self, session_id, evidence_id, offset=0):
        with self.index.lock:
            session = self.index.read(self.session_path(session_id), self.cwd)
            entry = next((e for run in session.runs.values() for e in run.get('evidence', []) if e.get('verification_id') == evidence_id), None)
            if not entry:
                raise ValueError('验收证据不存在。')
            if not entry.get('log_path'):
                return self.redact({'content': entry.get('output') or entry.get('error') or '没有命令日志。', 'next': None})
            root = session.path.with_suffix('').resolve()
            path = Path(entry['log_path']).resolve()
            if not root.is_relative_to(session.path.parent.resolve()) or not path.is_relative_to(root):
                raise ValueError('证据路径超出会话工件目录。')
            content, end, more = read_log_page(path, offset)
            return self.redact({'content': content, 'next': end if more else None})

    def tool_log(self, session_id, operation_id, offset=0):
        with self.index.lock:
            session = self.index.read(self.session_path(session_id), self.cwd)
            operation = session.operations.get(operation_id)
            if not operation or not operation.get('log_path'):
                raise ValueError('工具日志不存在。')
            root = session.path.with_suffix('').resolve()
            path = Path(operation['log_path']).resolve()
            if not root.is_relative_to(session.path.parent.resolve()) or not path.is_relative_to(root):
                raise ValueError('日志超出会话工件目录。')
            content, end, more = read_log_page(path, offset)
            live = operation.get('phase') != 'finished'
            with self.lock:
                live = live and any(not j['finished'] and j['session_id'] == session_id for j in self.jobs.values())
            return self.redact({'content': content, 'next': end if more or live else None, 'live': live})

    def search_files(self, query):
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError('搜索词过长。')
        results, visited = [], 0
        for folder, directories, files in os.walk(self.cwd, followlinks=False):
            directories[:] = [d for d in sorted(directories) if not d.startswith('.') and d not in {'node_modules', '__pycache__', 'build', 'dist'}
                              and not (Path(folder) / d).is_symlink() and not getattr((Path(folder) / d).lstat(), 'st_file_attributes', 0) & 0x400]
            for name in sorted(files):
                visited += 1
                if visited > 20000:
                    return {'items': results, 'truncated': True}
                path = Path(folder) / name
                relative = path.relative_to(self.cwd).as_posix()
                if query.casefold() in relative.casefold() and not path.is_symlink():
                    try:
                        self.safe_path(relative)
                        results.append({'name': name, 'path': relative, 'directory': False})
                    except ValueError:
                        continue
                if len(results) >= 100:
                    return {'items': results, 'truncated': True}
        return {'items': results, 'truncated': False}

    def review(self, session_id=None):
        from .web_review import status
        try:
            result = status(self)
        except ValueError as exc:
            result = {'available': False, 'files': [], 'reason': str(exc)}
        with self.lock:
            job = next((j for j in reversed(list(self.jobs.values())) if j['session_id'] == session_id), None)
            result['baseline'] = job.get('baseline') if job else None
            result['job_id'] = job['id'] if job else None
        result['operations'] = []
        if session_id:
            with self.index.lock:
                session = self.index.read(self.session_path(session_id), self.cwd)
                result['operations'] = [dict(id=o['operation_id'], name=o['name'], phase=o['phase'], path=o.get('path'),
                                             before=o.get('before'), after=o.get('after'), diff=(o.get('result') or {}).get('diff', '')[:16000])
                                        for o in session.operations.values() if o['name'] in {'write', 'edit'}]
        return self.redact(result)
