"""Immutable per-project workspaces, selected per request rather than globally."""
import hashlib
import json
import os
import threading
from pathlib import Path
from .web_sessions import atomic_json


def project_id(path):
    return hashlib.sha256(os.path.normcase(str(Path(path).resolve())).encode()).hexdigest()[:20]


class Projects:
    def __init__(self, primary):
        self.primary = primary
        self.lock = threading.RLock()
        self.workspaces = {project_id(primary.cwd): primary}
        self.path = primary.home / 'web-projects.json'
        self.paths = {project_id(primary.cwd): str(primary.cwd)}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding='utf-8'))
                for value in saved[:16]:
                    path = Path(value).resolve()
                    if path.is_dir():
                        self.paths[project_id(path)] = str(path)
            except (ValueError, TypeError, OSError):
                pass

    def list(self):
        with self.lock:
            return [{'id': key, 'path': path, 'name': Path(path).name} for key, path in self.paths.items()]

    def close(self):
        with self.lock:
            for workspace in self.workspaces.values():
                with workspace.lock:
                    workspace.closing = True
                    workspace.cancel()

    def add(self, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('需要项目绝对目录。')
        path = Path(value).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError('项目必须是已有的绝对目录。')
        path = path.resolve()
        identity = project_id(path)
        with self.lock:
            if identity not in self.paths and len(self.paths) >= 16:
                raise ValueError('最近项目上限为 16 个。')
            self.paths[identity] = str(path)
            atomic_json(self.path, list(self.paths.values()))
            self.get(identity)
        return {'id': identity}

    def get(self, identity):
        if not identity:
            return self.primary
        with self.lock:
            if identity not in self.paths:
                raise ValueError('未知项目，请先添加。')
            if identity not in self.workspaces:
                from .web import Workspace
                settings = {k:v for k,v in self.primary.settings.items() if k not in {'provider','include_usage','token_parameter','api_profile'}}
                settings['cwd'] = self.paths[identity]
                workspace = Workspace(**settings)
                workspace.token = self.primary.token
                self.workspaces[identity] = workspace
            return self.workspaces[identity]
