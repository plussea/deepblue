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
from .task_state import view as task_view
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
        from .provider_store import ProviderStore
        self.profile_store = ProviderStore(project_sessions(self.home,self.cwd)/'api-profiles.json')
        self.profiles = {'default': dict(name='默认 DeepSeek',api_key='', provider='deepseek', include_usage=True,token_parameter='max_tokens',
                                        **{k:self.settings[k] for k in ('model','base_url','timeout')})}
        saved = self.profile_store.read()
        if saved: self.profiles = saved['profiles']
        self.activate_profile(saved['active'] if saved else 'default')
        self.secrets.update(p['api_key'] for p in self.profiles.values() if p['api_key'])

    def api_key(self):
        if self.active_profile == 'default':
            return os.getenv('DEEPSEEK_API_KEY') or os.getenv('LLM_API_KEY') or self.memory_key
        return self.memory_key

    def key_for_settings(self, settings):
        identity=settings.get("api_profile", self.active_profile)
        profile = self.profiles.get(identity,{})
        if profile.get("base_url") != settings.get("base_url") or profile.get("provider") != settings.get("provider"):
            return ""
        if identity == "default":
            return os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY") or self.profiles.get(identity,{}).get("api_key", "")
        return self.profiles.get(identity,{}).get("api_key", "")

    def persist_profiles(self):
        self.profile_store.write(self.active_profile, self.profiles)

    def activate_profile(self, identity):
        if identity not in self.profiles: raise ValueError('未知 API 配置。')
        self.active_profile = identity
        self.settings["api_profile"] = identity
        item = self.profiles[identity]
        self.settings.update({k:item[k] for k in ('model','base_url','timeout','provider','include_usage','token_parameter')})
        self.memory_key = item.get('api_key', '')
        if identity == 'default':
            for name in ('model','base_url','timeout'):
                value = next((os.getenv(n) for n in self.env_fields[name] if os.getenv(n)), None)
                if value is not None: self.settings[name] = float(value) if name == 'timeout' else value

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
            sources = {k: (next((n for n in names if os.getenv(n)), 'saved') if self.active_profile == 'default' else 'saved')
                       for k, names in self.env_fields.items()}
            return dict(**{k:self.settings[k] for k in ('model','base_url','timeout','provider','include_usage','token_parameter')},
                        profile_id=self.active_profile, profiles=[{'id':k,'name':v['name']} for k,v in self.profiles.items()],
                        configured=bool(self.api_key()), sources=sources)

    def configure(self, data):
        import re
        import copy
        with self.lock:
            profiles = copy.deepcopy(self.profiles)
            identity = data.get('profile_id', self.active_profile)
            if not isinstance(identity,str) or not re.fullmatch('[a-zA-Z0-9_-]{1,64}',identity):
                raise ValueError('无效配置 ID。')
            if identity not in profiles:
                if len(profiles) >= 32: raise ValueError('最多保存 32 个 API 配置。')
                profiles[identity] = dict(name=identity, model='',base_url='https://api.example.com/v1',timeout=30,
                                          provider='openai-compatible',include_usage=True,token_parameter='max_tokens',api_key='')
            item = profiles[identity]
            for name in ('name','model','base_url','timeout','api_key','provider','include_usage','token_parameter'):
                if name not in data: continue
                if identity == 'default' and name in self.env_fields and any(os.getenv(n) for n in self.env_fields[name]):
                    raise ValueError(f'{name} 由环境变量管理；请新建独立 API 配置。')
                value = data[name]
                if name == 'include_usage':
                    if type(value) is not bool: raise ValueError('无效用量选项。')
                elif name == 'timeout':
                    if type(value) not in (float,int) or not 0.1 <= value <= 300: raise ValueError('超时须在 0.1–300 秒之间。')
                elif not isinstance(value,str) or len(value)>2000 or '\n' in value or '\r' in value:
                    raise ValueError('配置值无效。')
                item[name] = value.strip() if isinstance(value,str) else value
            if identity == self.active_profile and item['base_url'] != self.settings['base_url'] and 'api_key' not in data:
                item['api_key'] = ''
            Config(self.cwd,item['api_key'] or 'validation-only', model=item['model'],base_url=item['base_url'],
                   provider=item['provider'],include_usage=item['include_usage'],token_parameter=item['token_parameter'],request_timeout=item['timeout'])
            if self.api_key(): self.secrets.add(self.api_key())
            self.profile_store.write(identity,profiles)
            self.profiles = profiles
            self.activate_profile(identity)
            self.secrets.update(p['api_key'] for p in profiles.values() if p['api_key'])
            return self.configuration()

    def test_connection(self):
        with self.lock:
            config = Config(self.cwd, self.api_key(), home=self.home, model=self.settings['model'],
                            base_url=self.settings['base_url'], request_timeout=self.settings['timeout'], max_tokens=16, stream=False, provider=self.settings['provider'], include_usage=self.settings['include_usage'], token_parameter=self.settings['token_parameter'])
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
            task = task_view(session)
            if task and task['runtime'].get('execution_status') == 'running' and job_status in {'interrupted', 'failed', 'cancelled'}:
                task['runtime']['execution_status'] = 'interrupted'
            return self.redact(dict(id=session_id, **page, model=(session.last_run or {}).get('model',session.header['model']), usage=session.usage,
                compactions=session.compaction_count, context_bytes=len(json.dumps(context, ensure_ascii=False).encode()),
                context_limit=self.settings['max_context_bytes'], recovery=session.refresh_recovery(),
                verification_status=current_status(session.last_run, self.cwd, (self.home,)),
                task_state=task, last_run=session.last_run, metadata=self.metadata(session_id), evidence=self.evidence_list(session), job_status=job_status,
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
            directories[:] = [d for d in sorted(directories) if d not in {'.git', 'node_modules', '__pycache__', 'build', 'dist'}
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
