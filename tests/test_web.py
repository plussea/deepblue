import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from deepblue.web import Workspace, make_server
from deepblue.session import Session
from test_agent import tool_call
from test_tools import python_command


class WebTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.payload = {"content": "本机测试回复", "role": "assistant"}
        test = self
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if "整理器" in body["messages"][0]["content"]:
                    message = {"role": "assistant", "content": json.dumps(dict(goals=["继续"], constraints=[], changes=[], open_questions=[], next_steps=[]))}
                else:
                    message = test.payload
                reason = "tool_calls" if message.get("tool_calls") else "stop"
                if body.get("stream"):
                    delta = dict(message)
                    if delta.get("tool_calls"):
                        delta["tool_calls"] = [{**call, "index": index} for index, call in enumerate(delta["tool_calls"])]
                    raw = ("data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": reason}], "usage": {"total_tokens": 5}}) + "\n\ndata: [DONE]\n\n").encode()
                else:
                    raw = json.dumps({"choices": [{"message": message, "finish_reason": reason}], "usage": {"total_tokens": 5}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        self.provider_thread = threading.Thread(target=self.provider.serve_forever, daemon=True)
        self.provider_thread.start()
        self.workspace = Workspace(self.root, self.base / "home", base_url=f"http://127.0.0.1:{self.provider.server_port}")
        self.server = make_server(self.workspace, 0)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.shutdown)
        self.opener = build_opener(ProxyHandler({}))

    def shutdown(self):
        self.workspace.cancel()
        deadline = time.monotonic() + 10
        while self.workspace.job and not self.workspace.job["finished"] and time.monotonic() < deadline:
            time.sleep(0.02)
        for server, thread in ((self.server, self.server_thread), (self.provider, self.provider_thread)):
            server.shutdown()
            server.server_close()
            thread.join(5)

    def request(self, path, body=None, **headers):
        headers.setdefault("X-Deepblue-Token", self.workspace.token)
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with self.opener.open(request, timeout=10) as response:
                raw = response.read()
                return response.status, json.loads(raw) if "application/json" in response.headers["Content-Type"] else raw
        except HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def wait_job(self):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = self.workspace.events(0)
            if result["finished"]:
                return result
            time.sleep(0.03)
        self.fail("Web worker did not finish")

    def test_static_bootstrap_and_cross_site_protection(self):
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/app.js")[0], 200)
        code, data = self.request("/api/bootstrap")
        self.assertEqual(code, 200)
        self.assertNotIn("api_key", data)
        self.assertEqual(self.request("/api/sessions", **{"X-Deepblue-Token": "bad"})[0], 403)
        self.assertEqual(self.request("/api/bootstrap", Origin="https://evil.invalid")[0], 403)
        self.assertEqual(self.request("/api/run", {"prompt": "bad"}, Host="evil.invalid")[0], 403)
        self.assertEqual(self.request("/api/run", {"prompt": "bad"}, **{"X-Deepblue-Token": ""})[0], 403)

    def test_file_boundary_and_text_preview(self):
        (self.root / "hello.py").write_text("print('hello')")
        self.assertEqual(self.request("/api/files")[1][0]["name"], "hello.py")
        self.assertEqual(self.request("/api/file?path=hello.py")[1]["content"], "print('hello')")
        self.assertEqual(self.request("/api/file?path=../outside")[0], 400)
        self.assertEqual(self.request("/api/session?id=../../foo")[0], 400)

    def test_read_only_session_preview_does_not_repair_tail(self):
        session = Session.create(self.workspace.home, self.root, "fake", "system")
        session_id, path = session.header["id"], session.path
        session.close()
        with path.open("ab") as file:
            file.write(b'{"type":"message"')
        original = path.read_bytes()
        self.assertEqual(self.request("/api/session?id=" + session_id)[0], 200)
        self.assertEqual(path.read_bytes(), original)

    def test_worker_stream_session_resume_and_verification(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "web-test-secret"}):
            code, _ = self.request("/api/run", {"prompt": "测试本地会话", "verify": python_command("pass")})
            self.assertEqual(code, 200)
            events = self.wait_job()
            self.assertTrue(any(e["kind"] == "text_delta" for e in events["events"]))
            session_id = events["session_id"]
            state = self.request("/api/session?id=" + session_id)[1]
            self.assertEqual(state["verification_status"], "passed", events)
            self.assertEqual(self.request("/api/sessions")[1][0]["id"], session_id)
            self.request("/api/run", {"prompt": "继续", "session_id": session_id})
            self.wait_job()
            state = self.request("/api/session?id=" + session_id)[1]
            self.assertEqual(sum(m["role"] == "user" for m in state["messages"]), 2)
            self.assertEqual(state["verification_status"], "unverified")

    def test_cancel_running_shell_and_reject_concurrent_job(self):
        self.payload = {"role": "assistant", "content": None, "tool_calls": [tool_call("shell", {"command": python_command("import time; time.sleep(10)")})]}
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "web-test-secret"}):
            self.request("/api/run", {"prompt": "cancel fixture"})
            self.assertEqual(self.request("/api/run", {"prompt": "duplicate"})[0], 400)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if any(e["kind"] == "tool_start" for e in self.workspace.events(0)["events"]):
                    break
                time.sleep(0.02)
            self.request("/api/cancel", {"job_id": self.workspace.job['id']})
            events = self.wait_job()
            done = [e["data"] for e in events["events"] if e["kind"] == "done"]
            self.assertEqual(done[-1]["execution_status"], "cancelled", events)

    def test_missing_key_reports_error_without_leaking_credentials(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "LLM_API_KEY": ""}):
            self.request("/api/run", {"prompt": "missing key"})
            events = self.wait_job()
        self.assertTrue(any(e["kind"] == "error" for e in events["events"]))
        state = self.workspace.state(events['session_id'])
        self.assertTrue(state['job_notices'])
        restarted = Workspace(self.root, self.base / 'home')
        self.assertEqual(restarted.state(events['session_id'])['job_notices'], state['job_notices'])

    def test_job_diagnostics_are_bounded_redacted_and_latest_only(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "LLM_API_KEY": ""}):
            self.request('/api/run', {'prompt': 'diagnostics'})
            events = self.wait_job()
        self.workspace.secrets.add('diagnostic-test-secret')
        job = self.workspace.job
        for i in range(10):
            self.workspace.append_event(job, 'notice', {'text': f'{i}: diagnostic-test-secret ' + 'x' * 3000})
        self.workspace.persist_job(job)
        saved = Workspace(self.root, self.base / 'home').state(events['session_id'])
        self.assertEqual(len(saved['job_notices']), 6)
        self.assertTrue(all(len(s) <= 2000 for s in saved['job_notices']))
        self.assertNotIn('diagnostic-test-secret', json.dumps(saved['job_notices']))
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "LLM_API_KEY": ""}):
            self.request('/api/run', {'prompt': 'new attempt', 'session_id': events['session_id']})
            self.wait_job()
        self.assertNotIn('xxxx', json.dumps(self.workspace.state(events['session_id'])['job_notices']))
