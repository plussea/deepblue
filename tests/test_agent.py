import json
import tempfile
import unittest
from pathlib import Path

from deepblue.agent import Agent
from deepblue.config import Config
from deepblue.llm import ModelError
from deepblue.models import Completion
from deepblue.session import Session
from deepblue.tools import ToolContext, create_tools
from test_tools import python_command


def tool_call(name, args, call_id="call"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def response(*calls, reason="tool_calls", content=None):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = list(calls)
    return Completion(message, reason)


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append(json.loads(json.dumps(messages)))
        item = next(self.responses)
        if isinstance(item, BaseException):
            raise item
        return item


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = Config(self.root, "fake", home=self.root / "home")
        self.session = Session.create(self.config.home, self.root, "fake", "system")
        self.addCleanup(self.session.close)
        self.tools = create_tools(ToolContext(self.root, self.session.artifacts, 10))

    def run_agent(self, responses):
        client = FakeClient(responses)
        agent = Agent(self.config, client, self.tools, self.session)
        return agent, client, agent.run("修复测试失败")

    def test_repair_loop_runs_real_failing_and_passing_tests(self):
        (self.root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        command = python_command("from calc import add; assert add(2, 3) == 5; print('PASS')")
        _, client, result = self.run_agent([
            response(tool_call("shell", {"command": command}, "test1")),
            response(tool_call("read", {"path": "calc.py"}, "read1")),
            response(tool_call("edit", {"path": "calc.py", "old_text": "return a - b", "new_text": "return a + b"}, "edit1")),
            response(tool_call("shell", {"command": command}, "test2")),
            response(reason="stop", content="修复完成，测试通过。"),
        ])
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.steps, 5)
        first_result = json.loads(client.requests[1][-1]["content"])
        last_result = json.loads(client.requests[-1][-1]["content"])
        self.assertNotEqual(first_result["exit_code"], 0)
        self.assertEqual(last_result["exit_code"], 0)
        self.assertIn("PASS", last_result["output"])

    def test_truncated_tool_arguments_are_never_executed(self):
        _, client, result = self.run_agent([
            response(tool_call("write", {"path": "bad", "content": "bad"}), reason="length"),
            response(reason="stop", content="请重试"),
        ])
        self.assertFalse((self.root / "bad").exists())
        self.assertFalse(json.loads(client.requests[1][-1]["content"])["ok"])
        self.assertEqual(result.status, "completed")

    def test_unknown_tool_is_returned_to_model(self):
        _, client, _ = self.run_agent([response(tool_call("missing", {})), response(reason="stop", content="done")])
        self.assertIn("未知工具", client.requests[1][-1]["content"])

    def test_multiple_calls_keep_their_ids_and_order(self):
        _, client, _ = self.run_agent([
            response(tool_call("write", {"path": "file", "content": "abc"}, "a"),
                     tool_call("read", {"path": "file"}, "b")),
            response(reason="stop", content="done"),
        ])
        self.assertEqual([m["tool_call_id"] for m in client.requests[1][-2:]], ["a", "b"])

    def test_model_error_can_retry_without_duplicate_user_message(self):
        agent, _, result = self.run_agent([ModelError("offline"), response(reason="stop", content="ok")])
        self.assertEqual(result.status, "error")
        self.assertEqual(agent.run().status, "completed")
        self.assertEqual(sum(m["role"] == "user" for m in self.session.messages), 1)

    def test_step_limit_and_context_limit(self):
        self.config.max_steps = 1
        _, _, result = self.run_agent([response(tool_call("read", {"path": "missing"}))])
        self.assertEqual(result.status, "step_limit")
        self.config.max_context_bytes = 1
        _, client, result = self.run_agent([])
        self.assertEqual(result.status, "context_limit")
        self.assertEqual(client.requests, [])

    def test_cancelled_batch_gets_results_for_every_call(self):
        from unittest.mock import patch
        with patch.object(self.tools, "execute", side_effect=KeyboardInterrupt):
            _, _, result = self.run_agent([response(tool_call("shell", {"command": "x"}, "a"),
                                                   tool_call("shell", {"command": "y"}, "b"))])
        self.assertEqual(result.status, "cancelled")
        self.assertEqual([m["tool_call_id"] for m in self.session.messages[-2:]], ["a", "b"])
