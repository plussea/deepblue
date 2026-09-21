"""Opt-in paid API integration test; reads credentials only from environment.

Run explicitly: python scripts/live_smoke.py
Creates a disposable fixture and a credential-free report under .test-tmp/.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="显式调用真实收费 API 的联调测试")
    parser.add_argument("--v2", action="store_true", help="额外验证 find/grep、流式调用和压缩后的会话恢复")
    args = parser.parse_args()
    if not (os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")):
        print("请先在进程环境中设置 API Key；该脚本会调用真实收费接口。")
        return 2
    root = Path(__file__).resolve().parents[1]
    work = root / ".test-tmp" / ("live-" + uuid.uuid4().hex[:12])
    fixture = work / "fixture"
    fixture.mkdir(parents=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    secrets = [env[name] for name in ("LLM_API_KEY", "DEEPSEEK_API_KEY") if env.get(name)]

    def safe(text):
        for secret in secrets:
            text = text.replace(secret, "[REDACTED]")
        return text

    report = {"model": env.get("DEEPSEEK_MODEL") or env.get("LLM_MODEL") or "deepseek-flash",
              "timeout_seconds": env.get("LLM_TIMEOUT_SECONDS", "120"), "v2": args.v2,
              "streaming": True, "stages": []}
    report_path = work / "report.json"
    print(f"联调输出：{work}", flush=True)

    def save():
        report_path.write_text(safe(json.dumps(report, ensure_ascii=False, indent=2)), encoding="utf-8")

    def invoke(name, prompt, resume=False, cwd=fixture, extra=()):
        command = [sys.executable, "-m", "deepblue", "--cwd", str(cwd),
                   "--home", str(work / "sessions"), "--max-steps", "10",
                   "--max-tokens", "2048", "--shell-timeout", "30", "-p", prompt]
        if resume:
            command.append("--continue")
        command.extend(extra)
        print(f"开始：{name}", flush=True)
        start = time.monotonic()
        # Capture CLI output for the report; API credentials never enter argv.
        run = subprocess.run(command, env=env, capture_output=True, text=True,
                             encoding="utf-8", timeout=360)
        text = safe(run.stdout + "\n" + run.stderr)
        (work / f"{name}.log").write_text(text, encoding="utf-8")
        print(text[-10000:], flush=True)
        report["stages"].append({"name": name, "exit_code": run.returncode,
                                 "elapsed_seconds": round(time.monotonic() - start, 2)})
        save()
        if run.returncode:
            raise RuntimeError(f"{name} 失败，CLI 退出码 {run.returncode}")
        return run.stdout

    try:
        connection = work / "connection"
        connection.mkdir()
        answer = invoke("connection", "不要调用工具，只回答：深蓝已连接", cwd=connection)
        if "深蓝已连接" not in answer:
            raise RuntimeError("基础对话没有返回预期内容。")

        (fixture / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        test = fixture / "test_calc.py"
        test.write_text("import unittest\nfrom calc import add\n\nclass AddTests(unittest.TestCase):\n"
                        "    def test_positive(self): self.assertEqual(add(2, 3), 5)\n"
                        "    def test_negative(self): self.assertEqual(add(-2, -3), -5)\n"
                        "    def test_zero(self): self.assertEqual(add(0, 8), 8)\n", encoding="utf-8")
        original_hash = hashlib.sha256(test.read_bytes()).hexdigest()
        baseline = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-v"], cwd=fixture,
                                  capture_output=True, text=True, encoding="utf-8", timeout=20)
        if baseline.returncode == 0:
            raise RuntimeError("联调夹具应在修复前失败。")
        report["baseline_exit_code"] = baseline.returncode
        (work / "baseline.log").write_text(baseline.stdout + baseline.stderr, encoding="utf-8")
        token = "BLUE-" + uuid.uuid4().hex[:8]
        prompt = ("这是一个一次性联调项目，仅操作当前目录。请先使用 read 阅读 calc.py 和 test_calc.py，"
                  "通过 shell 执行 unittest 确认失败，然后用 edit 修复 calc.py（不能修改测试文件），"
                  "再次运行测试确认通过，最后使用 write 创建 RESULT.md，写明修复内容和测试结果。"
                  f"运行 Python 时使用此解释器：{sys.executable}。"
                  f"请记住本次联调标记 {token}，它不需要写入文件。")
        if args.v2:
            prompt = "开始时先调用 find 搜索 *.py，再调用 grep 搜索字面文本 return，接着完成以下任务：" + prompt
        invoke("repair", prompt)
        if hashlib.sha256(test.read_bytes()).hexdigest() != original_hash:
            raise RuntimeError("模型修改了测试文件，验收失败。")
        verified = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-v"], cwd=fixture,
                                  capture_output=True, text=True, encoding="utf-8", timeout=20)
        (work / "verification.log").write_text(verified.stdout + verified.stderr, encoding="utf-8")
        report["independent_test_exit_code"] = verified.returncode
        if verified.returncode or not (fixture / "RESULT.md").is_file():
            raise RuntimeError("独立测试或 RESULT.md 验收未通过。")
        if args.v2:
            invoke("prepare_compaction", "不要调用工具，也不要复述任何标记，只回答：准备压缩。", resume=True)
            answer = invoke("compact_resume", "不要调用工具，只回答最初编码任务中让我记住的联调标记。",
                            resume=True, extra=("--compact", "--keep-turns", "1"))
        else:
            answer = invoke("resume", "不要调用工具，只回答上一轮让我记住的联调标记。", resume=True)
        if token not in answer:
            raise RuntimeError("恢复会话后没有正确记住上一轮标记。")
        tools = []
        usage = []
        compactions = []
        for path in (work / "sessions").rglob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                if item["type"] == "usage":
                    usage.append(item["usage"])
                if item["type"] == "compaction":
                    compactions.append(item)
                for call in item.get("message", {}).get("tool_calls", []):
                    tools.append(call["function"]["name"])
        report["tool_calls"] = tools
        report["total_tokens"] = sum(item.get("total_tokens", 0) for item in usage)
        report["api_calls_with_usage"] = len(usage)
        required = {"read", "write", "edit", "shell"}
        if args.v2:
            required.update({"find", "grep"})
            if not compactions or token not in compactions[-1]["summary"]:
                raise RuntimeError("没有成功压缩，或摘要遗漏联调标记。")
            report["compaction_count"] = len(compactions)
            report["summary_preserved_marker"] = True
        if not required.issubset(tools):
            raise RuntimeError("未覆盖要求的工具。")
        report["status"] = "passed"
        save()
        print(safe(json.dumps(report, ensure_ascii=False, indent=2)), flush=True)
        return 0
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        report["status"] = "failed"
        report["error"] = safe(str(exc))
        save()
        print(report["error"], flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
