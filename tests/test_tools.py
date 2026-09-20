import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from deepblue.tools import ToolContext, create_tools
from deepblue.tools.base import MAX_OUTPUT_BYTES


def python_command(code):
    if os.name == "nt":
        return "& '" + sys.executable.replace("'", "''") + "' -c '" + code.replace("'", "''") + "'"
    import shlex
    return shlex.join([sys.executable, "-c", code])


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = create_tools(ToolContext(self.root, self.root / "logs", 10))

    def call(self, name, **args):
        return self.tools.execute(name, json.dumps(args))

    def test_write_read_pagination_and_unicode(self):
        self.assertTrue(self.call("write", path="中文目录/文件.txt", content="甲\n乙\n丙\n")["ok"])
        result = self.call("read", path="中文目录/文件.txt", offset=2, limit=1)
        self.assertEqual(result["content"], "2: 乙\n")
        self.assertEqual(result["next_offset"], 3)
        self.assertFalse(self.call("read", path="中文目录/文件.txt", offset=3)["truncated"])

    def test_unique_edit_preserves_bom_and_crlf(self):
        path = self.root / "code.txt"
        path.write_bytes(b"\xef\xbb\xbfalpha\r\nbeta\r\n")
        result = self.call("edit", path="code.txt", old_text="alpha\nbeta", new_text="alpha\ngamma")
        self.assertTrue(result["ok"])
        self.assertIn("+gamma", result["diff"])
        self.assertEqual(path.read_bytes(), b"\xef\xbb\xbfalpha\r\ngamma\r\n")

    def test_edit_preserves_untouched_mixed_line_endings(self):
        path = self.root / "mixed.txt"
        path.write_bytes(b"a\r\nb\nc\r\n")
        self.assertTrue(self.call("edit", path="mixed.txt", old_text="c", new_text="d")["ok"])
        self.assertEqual(path.read_bytes(), b"a\r\nb\nd\r\n")

    def test_ambiguous_edit_never_mutates_file(self):
        path = self.root / "code.txt"
        path.write_text("aaa", encoding="utf-8")
        for old in ("aa", "missing", ""):
            result = self.call("edit", path="code.txt", old_text=old, new_text="x")
            self.assertFalse(result["ok"])
            self.assertEqual(path.read_text(), "aaa")

    def test_parameter_and_io_errors_are_tool_results(self):
        self.assertFalse(self.tools.execute("read", "broken json")["ok"])
        self.assertFalse(self.call("unknown")["ok"])
        self.assertFalse(self.call("read", path="missing")["ok"])
        self.assertFalse(self.call("read", path="x", offset=True)["ok"])
        self.assertFalse(self.call("read", path="x", surprise=1)["ok"])
        self.assertFalse(self.call("shell", command="x", timeout=float("nan"))["ok"])

    def test_read_rejects_binary_and_oversized_lines(self):
        path = self.root / "data"
        path.write_bytes(b"\x00\x01")
        self.assertFalse(self.call("read", path="data")["ok"])
        path.write_bytes(b"x" * MAX_OUTPUT_BYTES)
        self.assertFalse(self.call("read", path="data")["ok"])

    def test_shell_cwd_unicode_and_nonzero_exit(self):
        code = "from pathlib import Path; print(Path.cwd()); print('深蓝'); raise SystemExit(7)"
        result = self.call("shell", command=python_command(code))
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 7)
        self.assertIn(str(self.root), result["output"])
        self.assertIn("深蓝", result["output"])
        self.assertTrue(Path(result["log_path"]).is_file())

    def test_shell_timeout(self):
        result = self.call("shell", command=python_command("import time; time.sleep(30)"), timeout=0.5)
        self.assertFalse(result["ok"])
        self.assertIn("超时", result["error"])

    def test_shell_keyboard_interrupt_releases_process_and_log(self):
        import time
        from unittest.mock import patch
        real_sleep = time.sleep
        interrupted = False

        def interrupt_once(seconds):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise KeyboardInterrupt()
            real_sleep(seconds)

        with patch("deepblue.tools.shell.time.sleep", side_effect=interrupt_once):
            with self.assertRaises(KeyboardInterrupt):
                self.call("shell", command=python_command("import time; time.sleep(30)"))
        # On Windows this fails if a surviving child still owns a log handle.
        for path in (self.root / "logs").glob("*.log"):
            path.unlink()

    def test_shell_long_output_is_logged_and_truncated(self):
        result = self.call("shell", command=python_command("print('a' * 40000); print('END')"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["truncated"])
        self.assertIn("END", result["output"])
        self.assertGreater(Path(result["log_path"]).stat().st_size, 40000)

    def test_shell_does_not_inherit_api_key(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "private-key"}):
            result = self.call("shell", command=python_command("import os; print('DEEPSEEK_API_KEY' in os.environ)"))
        self.assertTrue(result["ok"])
        self.assertIn("False", result["output"])
