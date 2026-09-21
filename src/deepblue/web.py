"""Loopback-only browser workspace using the existing agent and session format."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import threading
import time
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .session import project_sessions
from .web_jobs import JobManager
from .web_workspace import WorkspaceFeatures
from .llm import ModelError

STATIC = Path(__file__).with_name("web_static")


def storage_home(cwd, explicit=None):
    """Web is project-local by default; explicit storage is never silently changed."""
    return Path(explicit or os.getenv('DEEPBLUE_HOME') or Path(cwd) / '.deepblue').expanduser().resolve()


def check_storage(home, cwd):
    try:
        for directory in (home, project_sessions(home, cwd), project_sessions(home, cwd) / 'web-jobs'):
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=directory) as probe:
                probe.write(b'deepblue storage check')
                probe.flush()
                os.fsync(probe.fileno())
    except OSError as exc:
        raise ValueError(f'会话存储目录不可写：{home}。请通过 --home 指定有写入权限的目录，'
                         '或检查 DEEPBLUE_HOME。这是本地存储错误，尚未调用模型 API。') from exc


class Workspace(WorkspaceFeatures, JobManager):
    def __init__(self, cwd, home, model="deepseek-flash", base_url="https://api.deepseek.com", timeout=30,
                 max_steps=30, max_context_bytes=400000, shell_timeout=120):
        self.cwd, self.home = Path(cwd).resolve(), Path(home).resolve()
        if not self.cwd.is_dir():
            raise ValueError("工作目录不存在。")
        check_storage(self.home, self.cwd)
        self.settings = dict(cwd=str(self.cwd), home=str(self.home), model=model, base_url=base_url,
                             timeout=timeout, max_steps=max_steps, max_context_bytes=max_context_bytes,
                             shell_timeout=shell_timeout)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.init_features()
        self.init_jobs()

    def session_path(self, session_id):
        if not isinstance(session_id, str) or not re.fullmatch(r"[a-f0-9]{32}", session_id):
            raise ValueError("无效的会话 ID。")
        return project_sessions(self.home, self.cwd) / (session_id + ".jsonl")

    def safe_path(self, value):
        if not isinstance(value, str):
            raise ValueError("无效的文件路径。")
        path = (self.cwd / value).resolve()
        if not path.is_relative_to(self.cwd) or ".git" in path.relative_to(self.cwd).parts:
            raise ValueError("文件路径超出工作区。")
        return path

    def files(self, value=""):
        path = self.safe_path(value)
        if not path.is_dir():
            raise ValueError("目录不存在。")
        entries = []
        for item in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
            if item.name.startswith(".") or item.name in {"node_modules", "__pycache__"}:
                continue
            if item.is_symlink() or getattr(item.lstat(), "st_file_attributes", 0) & 0x400:
                continue
            entries.append({"name": item.name, "path": item.relative_to(self.cwd).as_posix(), "directory": item.is_dir()})
            if len(entries) >= 300:
                break
        return entries


def make_server(workspace, port=30142):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, mime="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = workspace.redact(body)
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if isinstance(body, (dict, list)) else body
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def allowed(self, token=False):
            port = self.server.server_port
            origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
            if "http://" + self.headers.get("Host", "") not in origins:
                return False
            if self.headers.get("Origin") and self.headers["Origin"] not in origins:
                return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                return False
            return not token or secrets.compare_digest(self.headers.get("X-Deepblue-Token", ""), workspace.token)

        def do_GET(self):
            parsed = urlsplit(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            if not self.allowed(path.startswith("/api/") and path != "/api/bootstrap"):
                return self.send(403, {"error": "拒绝跨站或未授权请求。"})
            try:
                if path == "/api/bootstrap":
                    return self.send(200, {"token": workspace.token, "version": __version__, "cwd": str(workspace.cwd), "home": str(workspace.home),
                        "model": workspace.settings["model"], "configured": bool(workspace.api_key())})
                if path == "/api/sessions":
                    return self.send(200, workspace.sessions(query.get('cursor', ['0'])[0], query.get('limit', ['50'])[0],
                        query.get('q', [''])[0], query.get('archived', ['0'])[0] == '1', bool(query)))
                if path == "/api/session":
                    return self.send(200, workspace.state(query.get("id", [""])[0], query.get('before', [None])[0], query.get('limit', ['100'])[0]))
                if path == '/api/export':
                    return self.send(200, workspace.export(query.get('id', [''])[0], query.get('format', ['md'])[0]))
                if path == '/api/message':
                    return self.send(200, workspace.message_content(query.get('id', [''])[0], int(query.get('index', ['-1'])[0]), int(query.get('offset', ['0'])[0])))
                if path == '/api/evidence':
                    return self.send(200, workspace.evidence(query.get('id', [''])[0], query.get('evidence_id', [''])[0], int(query.get('offset', ['0'])[0])))
                if path == '/api/tool-log':
                    return self.send(200, workspace.tool_log(query.get('id', [''])[0], query.get('operation_id', [''])[0], int(query.get('offset', ['0'])[0])))
                if path == '/api/config':
                    return self.send(200, workspace.configuration())
                if path == '/api/request':
                    with workspace.lock:
                        job = next((j for j in workspace.jobs.values() if j.get('request_id') == query.get('id', [''])[0]), None)
                        return self.send(200, {'job_id': job['id'], 'session_id': job['session_id']} if job else {'job_id': None})
                if path == '/api/review':
                    return self.send(200, workspace.review(query.get('id', [None])[0]))
                if path == '/api/diff':
                    from .web_review import diff
                    return self.send(200, diff(workspace, query.get('path', [''])[0], query.get('staged', ['0'])[0] == '1'))
                if path == '/api/file-search':
                    return self.send(200, workspace.search_files(query.get('q', [''])[0]))
                if path == "/api/events":
                    return self.send(200, workspace.events(max(0, int(query.get("after", ["0"])[0])), query.get('job_id', [None])[0], query.get('snapshot', ['0'])[0] == '1'))
                if path == '/api/stream':
                    job_id = query.get('job_id', [''])[0]
                    if not job_id:
                        raise ValueError('需要任务 ID。')
                    after = max(0, int(query.get('after', ['0'])[0]))
                    first = workspace.events(after, job_id, query.get('snapshot', ['0'])[0] == '1')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    deadline, heartbeat = time.monotonic() + 20, 0
                    try:
                        while time.monotonic() < deadline:
                            result = first if first is not None else workspace.events(after, job_id)
                            if first is not None or result['events'] or result['finished'] or time.monotonic() >= heartbeat:
                                self.wfile.write(('data: ' + json.dumps(workspace.redact(result), ensure_ascii=False) + '\n\n').encode())
                                self.wfile.flush()
                                heartbeat = time.monotonic() + 5
                            first = None
                            after = result['sequence']
                            if result['finished']:
                                break
                            time.sleep(0.2)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        pass
                    self.close_connection = True
                    return
                if path == "/api/files":
                    return self.send(200, workspace.files(query.get("path", [""])[0]))
                if path == "/api/file":
                    file = workspace.safe_path(query.get("path", [""])[0])
                    with file.open("rb") as source:
                        raw = source.read(256000 + 1)
                    if b"\0" in raw:
                        raise ValueError("仅支持 UTF-8 文本预览。")
                    return self.send(200, {"content": raw[:256000].decode("utf-8", errors="replace"), "truncated": len(raw) > 256000})
                resources = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                             "/style.css": ("style.css", "text/css; charset=utf-8")}
                for name in ('api.js', 'render.js', 'connection.js', 'sessions.js', 'files.js'):
                    resources['/' + name] = (name, 'text/javascript; charset=utf-8')
                if path in resources:
                    name, mime = resources[path]
                    return self.send(200, (STATIC / name).read_bytes(), mime)
                self.send(404, {"error": "未找到。"})
            except (ValueError, OSError, KeyError) as exc:
                self.send(400, {"error": str(exc)})

        def do_POST(self):
            if not self.allowed(True):
                return self.send(403, {"error": "拒绝跨站或未授权请求。"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 300000 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("需要有界 JSON 请求。")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("需要 JSON 对象。")
                if self.path == "/api/run":
                    return self.send(200, workspace.start(data))
                if self.path == "/api/cancel":
                    if not data.get('job_id'):
                        raise ValueError('停止操作必须指定任务 ID。')
                    return self.send(200, workspace.cancel(data['job_id']))
                if self.path == '/api/session/edit':
                    return self.send(200, workspace.edit_session(data))
                if self.path == '/api/config':
                    return self.send(200, workspace.configure(data))
                if self.path == '/api/config/test':
                    return self.send(200, workspace.test_connection())
                self.send(404, {"error": "未找到。"})
            except (ValueError, OSError, ModelError) as exc:
                self.send(400, {"error": str(exc)})
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="deepblue-web", description="深蓝本地 Web 工作台")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--home", type=Path, help="会话存储目录；默认 DEEPBLUE_HOME 或项目内 .deepblue")
    parser.add_argument("--port", type=int, default=30142)
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL") or os.getenv("LLM_MODEL") or "deepseek-flash")
    parser.add_argument("--base-url", default=os.getenv("DEEPSEEK_BASE_URL") or os.getenv("LLM_BASE_URL") or "https://api.deepseek.com")
    parser.add_argument("--timeout", type=float, default=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")))
    args = parser.parse_args(argv)
    try:
        workspace = Workspace(args.cwd, storage_home(args.cwd, args.home), args.model, args.base_url, args.timeout)
    except ValueError as exc:
        parser.error(str(exc))
    server = make_server(workspace, args.port)
    print(f"DeepBlue Web v{__version__} — http://127.0.0.1:{server.server_port}\n工作目录：{workspace.cwd}\n会话存储：{workspace.home}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        workspace.cancel()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
