import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from deepblue.config import Config
from deepblue.llm import DeepSeekClient, ModelError


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.requests = []
        self.status = 200
        self.payload = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "你好，深蓝"}}]}
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                test.requests.append((self.path, self.headers.get("Authorization"), body))
                raw = json.dumps(test.payload, ensure_ascii=False).encode("utf-8")
                self.send_response(test.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.config = Config(self.root, "test-secret", base_url=self.url)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_http_protocol(self):
        result = DeepSeekClient(self.config).complete([{"role": "user", "content": "hi"}], [])
        self.assertEqual(result.message["content"], "你好，深蓝")
        path, auth, body = self.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(auth, "Bearer test-secret")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertFalse(body["stream"])

    def test_http_errors_do_not_expose_response_or_secret(self):
        self.status = 401
        self.payload = {"error": "test-secret"}
        with self.assertRaises(ModelError) as caught:
            DeepSeekClient(self.config).complete([], [])
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("test-secret", str(caught.exception))

    def test_reject_duplicate_tool_ids(self):
        call = {"id": "same", "type": "function", "function": {"name": "read", "arguments": "{}"}}
        self.payload["choices"][0]["message"]["tool_calls"] = [call, call]
        with self.assertRaises(ModelError):
            DeepSeekClient(self.config).complete([], [])

    def test_cli_one_shot_and_resume(self):
        env = os.environ.copy()
        env["DEEPSEEK_API_KEY"] = "test-secret"
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        argv = [sys.executable, "-m", "deepblue", "--cwd", str(self.root),
                "--home", str(self.root / "home"), "--base-url", self.url, "-p", "你好"]
        first = subprocess.run(argv, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("你好，深蓝", first.stdout)
        again = subprocess.run(argv + ["--continue"], env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(self.requests[1][2]["messages"]), 4)
        self.assertEqual(len(list((self.root / "home" / "sessions").rglob("*.jsonl"))), 1)

    def test_missing_key_and_help(self):
        env = os.environ.copy()
        env.pop("DEEPSEEK_API_KEY", None)
        for args, code in [(["--help"], 0), (["-p", "hi"], 2)]:
            run = subprocess.run([sys.executable, "-m", "deepblue"] + args, env=env,
                                 capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(run.returncode, code, run.stderr)
            if code:
                self.assertIn("DEEPSEEK_API_KEY", run.stderr)
