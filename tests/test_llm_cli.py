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
        self.stream_chunks = None
        self.stream_gate = None
        self.gate_observed = False
        self.payload = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "你好，深蓝"}}]}
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                test.requests.append((self.path, self.headers.get("Authorization"), body))
                if body.get("stream") and test.status == 200:
                    if test.stream_chunks is not None:
                        chunks = test.stream_chunks
                    else:
                        choice = test.payload["choices"][0]
                        chunks = [{"choices": [{"index": 0, "delta": {"content": choice["message"]["content"]},
                                                "finish_reason": choice["finish_reason"]}]}]
                    raw = b"".join(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode("utf-8") for chunk in chunks)
                    raw += b"data: [DONE]\n\n"
                    content_type = "text/event-stream"
                else:
                    raw = json.dumps(test.payload, ensure_ascii=False).encode("utf-8")
                    content_type = "application/json"
                self.send_response(test.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                if test.stream_gate is not None and content_type == "text/event-stream":
                    first, rest = raw.split(b"\n\n", 1)
                    self.wfile.write(first + b"\n\n")
                    self.wfile.flush()
                    test.gate_observed = test.stream_gate.wait(timeout=3)
                    self.wfile.write(rest)
                else:
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

    def test_generic_protocol_omits_vendor_parameters(self):
        self.config.provider='openai-compatible'
        self.config.include_usage=False
        self.config.token_parameter='max_completion_tokens'
        DeepSeekClient(self.config).complete([{'role':'user','content':'hi'}],[],lambda text:None)
        body=self.requests[-1][2]
        self.assertNotIn('thinking',body)
        self.assertNotIn('stream_options',body)
        self.assertNotIn('max_tokens',body)
        self.assertEqual(body['max_completion_tokens'],self.config.max_tokens)

    def test_http_protocol(self):
        result = DeepSeekClient(self.config).complete([{"role": "user", "content": "hi"}], [])
        self.assertEqual(result.message["content"], "你好，深蓝")
        path, auth, body = self.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(auth, "Bearer test-secret")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertFalse(body["stream"])

    def test_cli_verification_controls_exit_code(self):
        from test_tools import python_command
        env = {**os.environ, "DEEPSEEK_API_KEY": "test-secret"}
        argv = [sys.executable, "-m", "deepblue", "--cwd", str(self.root),
                "--home", str(self.root / "home"), "--base-url", self.url,
                "--verify-repairs", "0", "-p", "fix", "--verify"]
        for command, expected, status in [("pass", 0, "passed"), ("raise AssertionError()", 1, "failed")]:
            with self.subTest(status=status):
                result = subprocess.run(argv + [python_command(command)], env=env, capture_output=True,
                                        text=True, encoding="utf-8", timeout=20)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertIn("验收：" + status, result.stderr)

    def test_evaluation_script_records_failed_task_without_real_api(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "evaluate.py"
        env = {**os.environ, "DEEPSEEK_API_KEY": "test-secret", "DEEPSEEK_BASE_URL": self.url}
        output = self.root / "evaluation"
        result = subprocess.run([sys.executable, str(script), "--live", "--mode", "verified", "--tasks", "add",
                                 "--max-steps", "1", "--output", str(output)],
                                env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(next(output.glob("*/report.json")).read_text(encoding="utf-8"))
        self.assertEqual(report["summary"]["runs"], 1)
        self.assertEqual(report["summary"]["successes"], 0)
        self.assertEqual(report["records"][0]["verification_status"], "failed")
        self.assertEqual(report["records"][0]["independent_status"], "failed")

    def test_http_errors_do_not_expose_response_or_secret(self):
        self.status = 401
        self.payload = {"error": "test-secret"}
        with self.assertRaises(ModelError) as caught:
            DeepSeekClient(self.config).complete([], [])
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("test-secret", str(caught.exception))

    def test_streaming_request_and_no_stream_override(self):
        self.stream_chunks = [
            {"choices": [{"index": 0, "delta": {"content": "a"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"content": "b"}, "finish_reason": "stop"}],
             "usage": {"total_tokens": 9}},
        ]
        text = []
        result = DeepSeekClient(self.config).complete([], [], on_text=text.append)
        self.assertEqual(text, ["a", "b"])
        self.assertEqual(result.message["content"], "ab")
        self.assertTrue(self.requests[-1][2]["stream"])
        self.assertEqual(self.requests[-1][2]["stream_options"], {"include_usage": True})
        self.assertNotIn("tools", self.requests[-1][2])
        self.config.stream = False
        DeepSeekClient(self.config).complete([], [], on_text=text.append)
        self.assertFalse(self.requests[-1][2]["stream"])
        self.assertEqual(text, ["a", "b"])

    def test_compact_cli_rebuilds_context_and_continues(self):
        from deepblue.session import Session
        home = self.root / "home"
        session = Session.create(home, self.root, "model", "system")
        for index in range(4):
            session.add({"role": "user", "content": f"task{index} " + "old context " * 100})
            session.add({"role": "assistant", "content": "done"})
        session.close()
        env = os.environ.copy()
        env["DEEPSEEK_API_KEY"] = "test-secret"
        run = subprocess.run([sys.executable, "-m", "deepblue", "--cwd", str(self.root),
                              "--home", str(home), "--base-url", self.url, "--continue", "--compact", "--summary-format", "text", "-p", "next"],
                             env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(len(self.requests), 2)
        self.assertFalse(self.requests[0][2]["stream"])
        self.assertNotIn("tools", self.requests[0][2])
        self.assertEqual(self.requests[1][2]["messages"][2]["content"].split()[0], "task2")
        loaded = Session.latest(home, self.root)
        self.addCleanup(loaded.close)
        self.assertEqual(loaded.compaction_count, 1)

    def test_stream_callback_runs_before_response_finishes(self):
        self.stream_gate = threading.Event()
        self.stream_chunks = [
            {"choices": [{"index": 0, "delta": {"content": "early"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        DeepSeekClient(self.config).complete([], [], on_text=lambda text: self.stream_gate.set())
        self.assertTrue(self.gate_observed, "Text must arrive before the server sends the final frame")

    def test_multiline_input_and_status(self):
        from deepblue.cli import main
        from unittest.mock import patch
        import contextlib
        import io
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}), \
             patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=["/paste", "第一行", "第二行", "/send", "/status", "/exit"]), \
             contextlib.redirect_stdout(io.StringIO()) as output, \
             contextlib.redirect_stderr(io.StringIO()):
            code = main(["--cwd", str(self.root), "--home", str(self.root / "home"), "--base-url", self.url])
        self.assertEqual(code, 0)
        self.assertEqual(self.requests[0][2]["messages"][-1]["content"], "第一行\n第二行")
        self.assertIn("活动上下文", output.getvalue())

    def test_full_completion_url_is_not_duplicated(self):
        self.config.base_url = self.url + "/chat/completions"
        DeepSeekClient(self.config).complete([], [])
        self.assertEqual(self.requests[0][0], "/v1/chat/completions")

    def test_llm_environment_aliases(self):
        from deepblue.cli import parser
        from unittest.mock import patch
        env = os.environ.copy()
        for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
            env.pop(name, None)
        env.update(LLM_API_KEY="alias-key", LLM_BASE_URL=self.url + "/chat/completions/",
                   LLM_MODEL="alias-model", LLM_TIMEOUT_SECONDS="30")
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(parser().parse_args([]).timeout, 30)
        argv = [sys.executable, "-m", "deepblue", "--cwd", str(self.root),
                "--home", str(self.root / "home"), "-p", "hi"]
        run = subprocess.run(argv, env=env, capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        path, auth, body = self.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(auth, "Bearer alias-key")
        self.assertEqual(body["model"], "alias-model")

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
        env.pop("LLM_API_KEY", None)
        for args, code in [(["--help"], 0), (["-p", "hi"], 2)]:
            run = subprocess.run([sys.executable, "-m", "deepblue"] + args, env=env,
                                 capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(run.returncode, code, run.stderr)
            if code:
                self.assertIn("DEEPSEEK_API_KEY", run.stderr)
