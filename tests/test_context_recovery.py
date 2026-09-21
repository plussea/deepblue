import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepblue.agent import Agent
from deepblue.compaction import compact_context, message_bytes
from deepblue.config import Config
from deepblue.llm import DeepSeekClient
from deepblue.recovery import execute_recorded, file_state
from deepblue.session import Session
from deepblue.tools import ToolContext, create_tools
from test_agent import FakeClient, response, tool_call
from test_tools import python_command


def structured(**changes):
    data = dict(goals=["完成任务"], constraints=[], changes=[], open_questions=[], next_steps=["继续验证"])
    data.update(changes)
    return response(reason="stop", content=json.dumps(data, ensure_ascii=False))


class ContextRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = Config(self.root, "test", home=self.root / "home")
        self.session = Session.create(self.config.home, self.root, "fake", "system")
        self.addCleanup(self.session.close)
        self.tools = create_tools(ToolContext(self.root, self.session.artifacts, 10))

    def history(self):
        for i in range(5):
            self.session.add({"role": "user", "content": f"任务{i}，不要删除测试，记住标记 `BLUE-123`"})
            self.session.add({"role": "assistant", "content": "旧资料" * 1800})

    def call(self, name, args, call_id="call"):
        call = tool_call(name, args, call_id)
        self.session.add(response(call).message)
        result = execute_recorded(self.session, self.tools, call)
        self.session.add({"role": "tool", "tool_call_id": call_id, "content": json.dumps(result)})
        return result

    def test_structured_summary_keeps_exact_constraints_and_real_failed_evidence(self):
        self.history()
        self.session.record_run({"run_id": "r", "verification_status": "failed", "evidence": [
            {"verification_id": "v", "status": "failed", "exit_code": 1, "log_path": "check.log"}]})
        result = compact_context(self.session, FakeClient([structured(), structured()]), self.config)
        self.assertTrue(result["changed"])
        summary = json.loads(self.session.compaction["summary"])
        self.assertEqual(summary["verified_facts"]["literal_anchors"], ["BLUE-123"])
        self.assertIn("不要删除测试", summary["verified_facts"]["verbatim_constraints"][0])
        self.assertEqual(summary["verified_facts"]["verification_evidence"][0]["status"], "failed")
        self.assertEqual(len(self.session.messages), 11)

    def test_malformed_structured_summary_is_atomic(self):
        self.history()
        before = self.session.context_messages()
        with self.assertRaises(ValueError):
            compact_context(self.session, FakeClient([response(reason="stop", content="not JSON")]), self.config)
        self.assertIsNone(self.session.compaction)
        self.assertEqual(self.session.context_messages(), before)

    def test_auto_compaction_then_model_and_no_loop_for_single_huge_turn(self):
        self.history()
        self.config.max_context_bytes = message_bytes(self.session.context_messages()) + 100
        self.config.compact_keep_turns = 3
        client = FakeClient([structured(), structured(), response(reason="stop", content="finished")])
        result = Agent(self.config, client, self.tools, self.session).run("继续")
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.session.compaction_count, 1)
        self.config.max_context_bytes = 100
        client = FakeClient([])
        self.assertEqual(Agent(self.config, client, self.tools, self.session).run("x" * 1000).status, "context_limit")
        self.assertEqual(client.requests, [])

    def test_large_output_archived_but_raw_history_and_error_preserved(self):
        raw = json.dumps({"ok": False, "exit_code": 1, "error": "critical error", "output": "x" * 30000 + "END-FAILURE"})
        self.session.add({"role": "tool", "tool_call_id": "large", "content": raw})
        active = json.loads(self.session.context_messages()[-1]["content"])
        self.assertEqual(active["error"], "critical error")
        self.assertIn("END-FAILURE", active["excerpt"])
        self.assertEqual(Path(active["artifact_path"]).read_text(encoding="utf-8"), raw)
        self.assertEqual(self.session.messages[-1]["content"], raw)
        self.assertLess(len(self.session.context_messages()[-1]["content"]), 6000)

    def test_old_read_marked_stale_and_edit_requires_reread(self):
        path = self.root / "a.txt"
        path.write_text("old")
        self.call("read", {"path": "a.txt"}, "read1")
        path.write_text("user edit")
        active = json.loads(self.session.context_messages()[-1]["content"])
        self.assertTrue(active["stale"])
        self.assertFalse(self.call("write", {"path": "a.txt", "content": "overwrite"}, "write1")["ok"])
        self.assertEqual(path.read_text(), "user edit")
        self.call("read", {"path": "a.txt"}, "read2")
        self.assertTrue(self.call("edit", {"path": "a.txt", "old_text": "user edit", "new_text": "confirmed"}, "edit1")["ok"])

    def test_finished_event_before_message_recovers_result_without_reexecution(self):
        call = tool_call("write", {"path": "a.txt", "content": "once"})
        self.session.add(response(call).message)
        execute_recorded(self.session, self.tools, call)
        self.session.close()
        loaded = Session.load(self.session.path, self.root)
        self.addCleanup(loaded.close)
        self.assertTrue(json.loads(loaded.messages[-1]["content"])["ok"])
        self.assertEqual(loaded.recovery["unfinished_operations"], [])

    def test_failure_after_write_before_finish_is_reconciled_without_replay(self):
        call = tool_call("write", {"path": "a.txt", "content": "once"})
        self.session.add(response(call).message)
        original = self.session.record_operation
        def inject(record):
            if record["phase"] == "finished":
                raise RuntimeError("injected crash after effect")
            original(record)
        with patch.object(self.session, "record_operation", side_effect=inject), self.assertRaises(RuntimeError):
            execute_recorded(self.session, self.tools, call)
        self.session.close()
        loaded = Session.load(self.session.path, self.root)
        self.addCleanup(loaded.close)
        self.assertEqual(loaded.recovery["unfinished_operations"][0]["status"], "matches_intended_content")
        self.assertFalse(json.loads(loaded.messages[-1]["content"])["ok"])
        self.assertEqual((self.root / "a.txt").read_text(), "once")

    def test_before_execution_failure_never_calls_tool(self):
        with patch.object(self.session, "record_operation", side_effect=OSError("disk failed")), patch.object(self.tools, "execute") as execute:
            with self.assertRaises(OSError):
                execute_recorded(self.session, self.tools, tool_call("write", {"path": "bad", "content": "x"}))
        execute.assert_not_called()
        self.assertFalse((self.root / "bad").exists())

    def test_shell_cancellation_keeps_pid_log_and_unknown_outcome(self):
        self.tools.context.cancelled = lambda: True
        call = tool_call("shell", {"command": python_command("import time; time.sleep(10)")})
        self.session.add(response(call).message)
        with self.assertRaises(KeyboardInterrupt):
            execute_recorded(self.session, self.tools, call)
        record = next(iter(self.session.operations.values()))
        self.assertIn("pid", record)
        self.assertTrue(Path(record["log_path"]).exists())
        self.assertEqual(self.session.refresh_recovery()["unfinished_operations"][0]["status"], "unknown")

    def test_external_change_is_detected_on_restore(self):
        self.call("write", {"path": "a", "content": "agent"})
        self.session.close()
        (self.root / "a").write_text("user")
        loaded = Session.load(self.session.path, self.root)
        self.addCleanup(loaded.close)
        self.assertEqual(len(loaded.recovery["changed_files"]), 1)
        self.assertIn("恢复时只读核对", loaded.context_messages()[0]["content"])

    def test_real_process_exit_after_write_before_result(self):
        home = self.root / "crash-home"
        code = '''import os, sys
from pathlib import Path
from deepblue.session import Session
from deepblue.tools import ToolContext, create_tools
from deepblue.recovery import execute_recorded
root, home = map(Path, sys.argv[1:])
s = Session.create(home, root, "fake", "system")
call = {"id":"crash", "type":"function", "function":{"name":"write", "arguments":'{"path":"crash.txt","content":"once"}'}}
s.add({"role":"assistant", "content":None, "tool_calls":[call]})
original = s.record_operation
def record(data):
 if data["phase"] == "finished": os._exit(83)
 original(data)
s.record_operation = record
execute_recorded(s, create_tools(ToolContext(root,s.artifacts)), call)
'''
        result = subprocess.run([sys.executable, "-c", code, str(self.root), str(home)], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 83, result.stderr)
        loaded = Session.latest(home, self.root)
        self.addCleanup(loaded.close)
        self.assertEqual(loaded.recovery["unfinished_operations"][0]["status"], "matches_intended_content")
        self.assertEqual((self.root / "crash.txt").read_text(), "once")

    def test_stream_cancellation_on_heartbeat_without_text(self):
        cancelled = False
        class Heartbeat:
            def readline(self, limit):
                nonlocal cancelled
                cancelled = True
                return b": keepalive\n"
        with self.assertRaises(KeyboardInterrupt):
            DeepSeekClient._read_stream(Heartbeat(), lambda text: self.fail("No text expected"), lambda: cancelled)
