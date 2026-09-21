"""Small development fixtures; not a held-out benchmark or a security sandbox."""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from .tools.base import ToolContext
from .tools.shell import shell


TASKS = [
    {"id": "add", "prompt": "修复 calc.py 的 add，让它返回两个数的和。", "files": {
        "calc.py": "def add(a, b):\n    return a - b\n"},
     "solution": {"calc.py": "def add(a, b):\n    return a + b\n"},
     "public": "from calc import add; assert add(2, 3) == 5",
     "private": "from calc import add; assert add(-4, 2) == -2; assert add(0, 0) == 0; assert add(1.5, 2.5) == 4"},
    {"id": "unique", "prompt": "实现 utils.unique：去重并保留首次出现的顺序，输入为可哈希元素列表。", "files": {
        "utils.py": "def unique(items):\n    return items\n"},
     "solution": {"utils.py": "def unique(items):\n    return list(dict.fromkeys(items))\n"},
     "public": "from utils import unique; assert unique([2, 1, 2]) == [2, 1]",
     "private": "from utils import unique; assert unique([]) == []; assert unique(['b','a','b','c']) == ['b','a','c']"},
    {"id": "clamp", "prompt": "实现 bounds.clamp(value, low, high)，限制到闭区间；low > high 时抛 ValueError。", "files": {
        "bounds.py": "def clamp(value, low, high):\n    return value\n"},
     "solution": {"bounds.py": "def clamp(value, low, high):\n    if low > high:\n        raise ValueError('bounds')\n    return max(low, min(value, high))\n"},
     "public": "from bounds import clamp; assert clamp(9, 1, 3) == 3",
     "private": "from bounds import clamp\nassert clamp(-5,1,3)==1\nassert clamp(2,1,3)==2\ntry:\n clamp(2,3,1)\nexcept ValueError:\n pass\nelse:\n raise AssertionError('invalid bounds accepted')"},
    {"id": "mean", "prompt": "stats.mean 应返回平均值；空列表返回 None。修复该行为并保持输入不变。", "files": {
        "stats.py": "def mean(values):\n    return sum(values)\n"},
     "solution": {"stats.py": "def mean(values):\n    return sum(values) / len(values) if values else None\n"},
     "public": "from stats import mean; assert mean([2,4]) == 3",
     "private": "from stats import mean; x=[-1,0,4]; assert mean(x)==1; assert x==[-1,0,4]; assert mean([]) is None"},
    {"id": "invoice", "prompt": "修复 money.subtotal 的数量乘法，并让 invoice.total 对各行 (单价,数量) 求和，空订单返回 0。", "files": {
        "money.py": "def subtotal(price, count):\n    return price + count\n",
        "invoice.py": "from money import subtotal\ndef total(rows):\n    return len(rows)\n"},
     "solution": {"money.py": "def subtotal(price, count):\n    return price * count\n",
                  "invoice.py": "from money import subtotal\ndef total(rows):\n    return sum(subtotal(p,c) for p,c in rows)\n"},
     "public": "from invoice import total; assert total([(3,2),(4,1)])==10",
     "private": "from invoice import total; from money import subtotal; assert total([])==0; assert total([(2.5,2),(3,0)])==5; assert subtotal(4,3)==12"},
]


def python_command(code: str) -> str:
    # json.dumps provides a Python string literal here, never shell escaping.
    wrapped = "exec(" + repr(code) + ")"
    if os.name == "nt":
        return "& '" + sys.executable.replace("'", "''") + "' -B -c '" + wrapped.replace("'", "''") + "'"
    return shlex.join([sys.executable, "-B", "-c", wrapped])


def prepare(task: dict, root: Path):
    root.mkdir(parents=True, exist_ok=False)
    for name, content in task["files"].items():
        (root / name).write_text(content, encoding="utf-8")
    (root / "check.py").write_text(task["public"] + "\n", encoding="utf-8")


def independent_check(task: dict, root: Path, artifacts: Path) -> dict:
    # Assertions stay in the evaluator, outside the editable fixture. Same-user
    # shell access is NOT isolation; this is suitable only for trusted experiments.
    return shell(ToolContext(root, artifacts, 15), {"command": python_command(task["private"])})


def summarize(records: list[dict]) -> dict:
    successes = sum(r.get("independent_status") == "passed" for r in records)
    finished = [r for r in records if r.get("execution_status") == "finished"]
    false_finishes = sum(r.get("independent_status") != "passed" for r in finished)
    tokens = sum(r.get("usage", {}).get("total_tokens", 0) for r in records)
    return {"runs": len(records), "successes": successes,
            "success_rate": successes / len(records) if records else None,
            "finished_runs": len(finished), "false_finished_runs": false_finishes,
            "false_finished_rate": false_finishes / len(finished) if finished else None,
            "total_tokens": tokens, "tokens_per_success": tokens / successes if successes else None,
            "runs_without_usage": sum(not r.get("usage", {}).get("api_calls") for r in records),
            "elapsed_seconds": sum(r.get("elapsed_seconds", 0) for r in records)}
