"""Isolated browser fixture. No real model endpoint or credentials are used."""
import json
import os
import sys
import threading
import time
import uuid
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from deepblue.session import Session
from deepblue.web import Workspace, make_server


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        prompt = data['messages'][-1].get('content') or ''
        if prompt in ('fault-401', 'fault-429'):
            raw = b'upstream echoes browser-fixture-secret'
            self.send_response(int(prompt[-3:]))
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if prompt == 'fault-timeout':
            time.sleep(1)
            self.close_connection = True
            return
        tool_fixture = data['messages'][-1].get('role') == 'user' and 'tool-fixture' in prompt
        text = '# 模拟回复\n\n- 已读取\n- 已完成\n\n| 项目 | 状态 |\n| --- | --- |\n| 测试 | 通过 |\n\n```python\nprint("深蓝")\n```\n\n[打开文件](hello.py#L2)\n<script>window.injected=true</script>\n[危险链接](javascript:alert(1))'
        if not data.get('stream'):
            raw = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'OK'}, 'finish_reason': 'stop'}]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        try:
            if prompt == 'fault-disconnect':
                # A complete tool call without DONE must never be executed.
                raw = {'choices': [{'index': 0, 'delta': {'tool_calls': [
                    {'index': 0, 'id': 'never-run', 'type': 'function', 'function': {
                        'name': 'write', 'arguments': json.dumps({'path': 'must-not-exist.txt', 'content': 'unsafe'})}}
                ]}, 'finish_reason': 'tool_calls'}]}
                self.wfile.write(('data: ' + json.dumps(raw) + '\n\n').encode())
                self.wfile.flush()
                self.close_connection = True
                return
            count = 70 if 'slow' in prompt else 1
            for i in range(count):
                delta = (f'第 {i} 行输出。\n\n' + '说明文字。' * 30 + '\n\n') if count > 1 else text
                content = {'content': delta}
                if tool_fixture:
                    content = {'tool_calls': [{'index': 0, 'id': 'fixture-shell', 'type': 'function', 'function': {
                        'name': 'shell', 'arguments': json.dumps({'command': python_command("import time; print('live log', flush=True); time.sleep(5); print('done')")})}}]}
                if prompt == 'fault-tool':
                    content = {'tool_calls': [{'index': 0, 'id': 'missing-file', 'type': 'function', 'function': {
                        'name': 'read', 'arguments': json.dumps({'path': 'nonexistent-fixture.txt'})}}]}
                raw = {'choices': [{'index': 0, 'delta': content, 'finish_reason': None}]}
                self.wfile.write(('data: ' + json.dumps(raw) + '\n\n').encode())
                self.wfile.flush()
                if count > 1:
                    time.sleep(.2)
            self.wfile.write(('data: ' + json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if tool_fixture or prompt == 'fault-tool' else 'stop'}], 'usage': {'total_tokens': 20}}) + '\n\ndata: [DONE]\n\n').encode())
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def python_command(code):
    import shlex
    if os.name == 'nt':
        return "& '" + sys.executable.replace("'", "''") + "' -c '" + code.replace("'", "''") + "'"
    return shlex.quote(sys.executable) + ' -c ' + shlex.quote(code)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    base = Path(__file__).resolve().parents[1] / '.test-tmp' / ('web-e2e-' + uuid.uuid4().hex[:8])
    root = base / 'project'
    root.mkdir(parents=True)
    commands = root / '.deepblue' / 'commands'
    commands.mkdir(parents=True)
    (commands / 'review.md').write_text('---\ndescription: 审查文件\nskills: audit\n---\nReview {{1}}', encoding='utf-8')
    skills = root / '.deepblue' / 'skills' / 'audit'
    skills.mkdir(parents=True)
    (skills / 'SKILL.md').write_text('Inspect then verify.', encoding='utf-8')
    (root / 'hello.py').write_text('# example\nprint("hello")\n', encoding='utf-8')
    (root.parent / 'second-project').mkdir()
    (root / 'notes.py').write_text('# second tab\n', encoding='utf-8')
    def git(*args):
        subprocess.run(['git', *args], cwd=root, check=True, capture_output=True,
                       **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    (root/'.hidden').mkdir()
    (root/'.hidden'/'guide.md').write_text('# Guide\n\n**strong**\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n```python\nprint(1)\n```\n<script>window.injected=true</script>',encoding='utf-8')
    git('init', '-q')
    git('add', 'hello.py', 'notes.py', '.deepblue', '.hidden')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
    (root / 'hello.py').write_text('# example\nprint("staged")\n', encoding='utf-8')
    git('add', 'hello.py')
    (root / 'hello.py').write_text('# example\nprint("unstaged")\n', encoding='utf-8')
    for key in ('DEEPSEEK_API_KEY', 'LLM_API_KEY', 'DEEPSEEK_MODEL', 'LLM_MODEL', 'DEEPSEEK_BASE_URL', 'LLM_BASE_URL', 'LLM_TIMEOUT_SECONDS'):
        os.environ.pop(key, None)
    history = Session.create(base / 'home', root, 'fixture', 'system')
    history.add({'role': 'user', 'content': '历史会话'})
    with history.path.open('ab') as file:
        for i in range(250):
            file.write((json.dumps({'type': 'message', 'message': {'role': 'assistant', 'content': f'历史内容 {i}'}}) + '\n').encode())
    history.close()
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    workspace = Workspace(root, base / 'home', model='fixture', base_url=f'http://127.0.0.1:{provider.server_port}')
    server = make_server(workspace, 0)
    print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}', 'output': str(base), 'history': history.header['id'],
                      'verify': python_command("print('verification evidence')")}), flush=True)
    try:
        server.serve_forever()
    finally:
        workspace.cancel()
        server.server_close()
        provider.shutdown()
        provider.server_close()


if __name__ == '__main__':
    main()
