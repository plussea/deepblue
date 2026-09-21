import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepblue.tools import ToolContext, create_tools


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = create_tools(ToolContext(self.root, self.root / "logs"))
        for path, text in {"main.py": "# 深蓝\ndef Add():\n    return 1\n",
                           "src/中文.py": "target\nTARGET\n", "src/readme.md": "target\n",
                           ".hidden.py": "target", "node_modules/ignored.py": "target",
                           ".git/ignored.py": "target"}.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

    def call(self, name, **args):
        return self.tools.execute(name, json.dumps(args))

    def test_find_recursive_glob_and_exclusions(self):
        result = self.call("find", pattern="**/*.py")
        self.assertEqual(result["files"], ["main.py", "src/中文.py"])
        self.assertFalse(result["truncated"])
        hidden = self.call("find", pattern="*.py", include_hidden=True)["files"]
        self.assertIn(".hidden.py", hidden)
        self.assertNotIn(".git/ignored.py", hidden)

    def test_grep_unicode_lines_literal_matching_and_case(self):
        result = self.call("grep", pattern="target", glob="*.py", ignore_case=True)
        self.assertEqual([match["line"] for match in result["matches"]], [1, 2])
        self.assertEqual(result["matches"][0]["path"], "src/中文.py")
        self.assertEqual(self.call("grep", pattern="t.*t")["matches"], [])
        self.assertEqual(self.call("grep", pattern="深蓝")["matches"][0]["line"], 1)

    def test_limits_report_truncation(self):
        self.assertTrue(self.call("find", pattern="*", limit=1)["truncated"])
        self.assertTrue(self.call("grep", pattern="target", limit=1)["truncated"])
        with patch("deepblue.tools.search.MAX_ENTRIES", 1):
            self.assertTrue(self.call("find", pattern="*")["truncated"])

    def test_binary_and_large_files_are_skipped(self):
        (self.root / "binary").write_bytes(b"target\0abc")
        (self.root / "large").write_bytes(b"target" * 30)
        with patch("deepblue.tools.search.MAX_FILE_BYTES", 100):
            result = self.call("grep", pattern="target")
        self.assertEqual(result["skipped"], 2)

    def test_explicit_file_and_invalid_parameters(self):
        result = self.call("grep", path="main.py", pattern="Add")
        self.assertEqual(result["matches"][0]["line"], 2)
        self.assertFalse(self.call("find", path="missing", pattern="*")["ok"])
        self.assertFalse(self.call("grep", pattern="x", ignore_case="true")["ok"])

    def test_links_are_not_followed(self):
        with patch("deepblue.tools.search.is_link", side_effect=lambda path: path.name == "src"):
            result = self.call("find", pattern="*.py")
        self.assertEqual(result["files"], ["main.py"])
        self.assertEqual(result["skipped"], 1)

    def test_long_line_returns_bounded_excerpt(self):
        (self.root / "long.txt").write_text("a" * 10000 + "needle" + "b" * 10000, encoding="utf-8")
        result = self.call("grep", path="long.txt", pattern="needle")
        self.assertIn("needle", result["matches"][0]["text"])
        self.assertTrue(result["matches"][0]["line_truncated"])
        self.assertLessEqual(len(result["matches"][0]["text"]), 500)

    def test_casefold_expansion_does_not_shift_excerpt_away_from_match(self):
        (self.root / "unicode.txt").write_text("ß" * 400 + "NEEDLE" + "x" * 1000, encoding="utf-8")
        result = self.call("grep", path="unicode.txt", pattern="needle", ignore_case=True)
        self.assertIn("NEEDLE", result["matches"][0]["text"])
