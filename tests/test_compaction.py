import json
import tempfile
import unittest
from pathlib import Path

from deepblue.compaction import compact_context, split_utf8
from deepblue.config import Config
from deepblue.llm import ModelError
from deepblue.session import Session
from test_agent import FakeClient, response, tool_call


class CompactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = Config(self.root, "test", summary_format="text")
        self.session = Session.create(self.root / "home", self.root, "model", "project policy")
        self.addCleanup(self.session.close)
        for index in range(4):
            self.session.add({"role": "user", "content": f"task {index}"})
            self.session.add(response(tool_call("read", {"path": "x"}, f"call{index}")).message)
            self.session.add({"role": "tool", "tool_call_id": f"call{index}", "content": "long result " * 100})
            self.session.add({"role": "assistant", "content": f"finished {index}"})

    def test_preserves_recent_tool_pairs_and_raw_history_on_reload(self):
        original = self.session.path.read_bytes()
        result = compact_context(self.session, FakeClient([response(reason="stop", content="goal and edits")]), self.config)
        self.assertTrue(result["changed"])
        self.assertLess(result["after_bytes"], result["before_bytes"])
        self.assertEqual(len(self.session.messages), 17)
        context = self.session.context_messages()
        self.assertEqual(context[0]["content"], "project policy")
        self.assertEqual(context[2]["content"], "task 2")
        self.assertEqual(context[3]["tool_calls"][0]["id"], context[4]["tool_call_id"])
        self.assertTrue(self.session.path.read_bytes().startswith(original))
        path = self.session.path
        self.session.close()
        loaded = Session.load(path, self.root)
        self.addCleanup(loaded.close)
        self.assertEqual(loaded.context_messages(), context)
        self.assertEqual(loaded.compaction_count, 1)
        self.assertEqual(len(loaded.messages), 17)

    def test_error_truncation_and_cancellation_leave_context_unchanged(self):
        before = self.session.context_messages()
        for item in (ModelError("offline"), KeyboardInterrupt(), response(reason="length", content="partial"),
                     response(tool_call("shell", {}), content="bad")):
            with self.subTest(item=item), self.assertRaises((ModelError, KeyboardInterrupt, ValueError)):
                compact_context(self.session, FakeClient([item]), self.config)
            self.assertEqual(self.session.context_messages(), before)
            self.assertIsNone(self.session.compaction)

    def test_chunked_summary_combines_previous_results_before_commit(self):
        self.session.messages[1]["content"] = "旧资料" * 6000
        client = FakeClient([response(reason="stop", content="summary1"), response(reason="stop", content="summary2")])
        result = compact_context(self.session, client, self.config)
        self.assertTrue(result["changed"])
        self.assertEqual(len(client.requests), 2)
        self.assertIn("summary1", client.requests[1][1]["content"])

    def test_second_compaction_includes_previous_summary(self):
        compact_context(self.session, FakeClient([response(reason="stop", content="first summary")]), self.config)
        for index in range(2):
            self.session.add({"role": "user", "content": f"new task {index}"})
            self.session.add({"role": "assistant", "content": "done"})
        client = FakeClient([response(reason="stop", content="second summary")])
        compact_context(self.session, client, self.config)
        self.assertIn("first summary", client.requests[0][1]["content"])
        self.assertEqual(self.session.compaction_count, 2)
        self.assertEqual(self.session.context_messages()[2]["content"], "new task 0")

    def test_noop_and_pending_tool_calls(self):
        client = FakeClient([])
        self.assertFalse(compact_context(self.session, client, self.config, keep_turns=4)["changed"])
        self.assertEqual(client.requests, [])
        self.session.add(response(tool_call("shell", {}, "pending")).message)
        with self.assertRaisesRegex(ValueError, "未完成"):
            compact_context(self.session, client, self.config)

    def test_utf8_chunks_keep_all_characters(self):
        text = "中文🙂abc" * 100
        chunks = list(split_utf8(text, 31))
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk.encode("utf-8")) <= 31 for chunk in chunks))

    def test_usage_survives_reload_and_includes_summary(self):
        summary = response(reason="stop", content="summary")
        summary.usage = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
        compact_context(self.session, FakeClient([summary]), self.config)
        self.session.close()
        loaded = Session.load(self.session.path, self.root)
        self.addCleanup(loaded.close)
        self.assertEqual(loaded.usage["total_tokens"], 110)
        self.assertEqual(loaded.usage["api_calls"], 1)

    def test_invalid_persisted_boundary_fails_instead_of_breaking_pairs(self):
        path = self.session.path
        self.session.close()
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps({"type": "compaction", "summary": "bad", "first_kept": 2}) + "\n")
        with self.assertRaisesRegex(ValueError, "压缩记录"):
            Session.load(path, self.root)
