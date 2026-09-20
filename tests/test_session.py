import json
import tempfile
import unittest
from pathlib import Path

from deepblue.session import Session


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = Session.create(self.root / "home", self.root, "test-model", "system")
        self.addCleanup(self.session.close)

    def test_resume_and_repair_incomplete_tool_batch_without_replay(self):
        self.session.add({"role": "assistant", "content": None, "tool_calls": [
            {"id": "one", "function": {"name": "write", "arguments": "{}"}},
            {"id": "two", "function": {"name": "shell", "arguments": "{}"}},
        ]})
        self.session.add({"role": "tool", "tool_call_id": "one", "content": "done"})
        self.session.close()
        loaded = Session.latest(self.root / "home", self.root)
        self.addCleanup(loaded.close)
        self.assertEqual(loaded.header["model"], "test-model")
        self.assertEqual(loaded.messages[-1]["tool_call_id"], "two")
        self.assertFalse(json.loads(loaded.messages[-1]["content"])["ok"])
        self.assertEqual(loaded.recover_pending(), 0)

    def test_exclusive_lock(self):
        with self.assertRaisesRegex(ValueError, "另一个"):
            Session.load(self.session.path, self.root)

    def test_partial_tail_is_repaired_before_append(self):
        path = self.session.path
        self.session.close()
        with path.open("ab") as file:
            file.write(b'{"type": "mess')
        loaded = Session.load(path, self.root)
        loaded.add({"role": "user", "content": "hello"})
        loaded.close()
        again = Session.load(path, self.root)
        self.addCleanup(again.close)
        self.assertEqual(again.messages[-1]["content"], "hello")

    def test_corrupted_committed_record_is_not_silently_removed(self):
        path = self.session.path
        self.session.close()
        with path.open("ab") as file:
            file.write(b"invalid\n")
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            Session.load(path, self.root)
        self.assertEqual(before, path.read_bytes())
