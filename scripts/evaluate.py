"""Explicit opt-in development evaluation, never invoked by ordinary tests."""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import time
import uuid
from pathlib import Path

from deepblue import __version__
from deepblue.agent import Agent
from deepblue.config import Config
from deepblue.evaluation import TASKS, independent_check, prepare, python_command, summarize
from deepblue.llm import DeepSeekClient
from deepblue.prompts import build_system_prompt
from deepblue.session import Session
from deepblue.tools import ToolContext, create_tools
from deepblue.verification import VerificationConfig, fingerprint


def main():
    parser = argparse.ArgumentParser(description="5 个开发任务的显式收费评测；不是保留测试集")
    parser.add_argument("--live", action="store_true", help="明确启用真实 API 请求")
    parser.add_argument("--mode", choices=["baseline", "verified"], default="baseline")
    parser.add_argument("--tasks", nargs="+", choices=[t["id"] for t in TASKS], default=[t["id"] for t in TASKS])
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--output", type=Path, default=Path(".test-tmp/evaluations"))
    args = parser.parse_args()
    if not args.live:
        parser.error("真实评测会产生费用，须显式传入 --live；先确认任务数、重复数和调用预算。")
    if not 1 <= args.repeat <= 10 or not 1 <= args.max_steps <= 100 or not 1 <= args.max_tokens <= 8192:
        parser.error("repeat 范围 1–10，max-steps 1–100，max-tokens 1–8192。")
    output = args.output.resolve() / uuid.uuid4().hex
    output.mkdir(parents=True)
    records = []
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
    code_root = Path(__file__).resolve().parents[1] / "src"
    report = {"version": __version__, "git_revision": revision,
              "source_fingerprint": fingerprint(code_root), "platform": platform.platform(),
              "python": platform.python_version(), "mode": args.mode,
              "max_steps": args.max_steps, "max_tokens": args.max_tokens,
              "model": os.getenv("DEEPSEEK_MODEL") or os.getenv("LLM_MODEL") or "deepseek-flash",
              "decoding": "provider defaults, thinking disabled", "records": records}
    for task in TASKS:
        if task["id"] not in args.tasks:
            continue
        for repeat in range(args.repeat):
            run_dir = output / (task["id"] + "-" + str(repeat))
            root = run_dir / "workspace"
            prepare(task, root)
            started = time.monotonic()
            record = {"task_id": task["id"], "repeat": repeat, "initial_fingerprint": fingerprint(root)}
            session = None
            try:
                config = Config(root, os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY", ""),
                                home=run_dir / "sessions", model=report["model"],
                                base_url=os.getenv("DEEPSEEK_BASE_URL") or os.getenv("LLM_BASE_URL") or "https://api.deepseek.com",
                                request_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
                                max_steps=args.max_steps, max_tokens=args.max_tokens, shell_timeout=30, stream=False)
                session = Session.create(config.home, root, config.model, build_system_prompt(root))
                tools = create_tools(ToolContext(root, session.artifacts, 30))
                verification = (VerificationConfig(python_command("exec(open('check.py', encoding='utf-8').read())"), root, 15, 1)
                                if args.mode == "verified" else None)
                result = Agent(config, DeepSeekClient(config), tools, session, verification=verification).run(
                    task["prompt"] + "\n可以运行 check.py 检查示例。")
                record.update(execution_status=result.execution_status, verification_status=result.verification_status,
                              run_id=result.run_id, session_path=str(session.path), usage=dict(session.usage))
                checked = independent_check(task, root, run_dir / "independent")
                record.update(independent_status="passed" if checked["ok"] else "error" if checked.get("error") else "failed",
                              independent_evidence=checked)
            except (OSError, ValueError) as exc:
                record.update(execution_status="error", independent_status="error", error=str(exc))
            except KeyboardInterrupt:
                record.update(execution_status="cancelled", independent_status="not_run")
            finally:
                if session:
                    record["usage"] = dict(session.usage)
                    session.close()
                record["elapsed_seconds"] = round(time.monotonic() - started, 3)
                records.append(record)
                report["summary"] = summarize(records)
                serialized = json.dumps(report, ensure_ascii=False, indent=2)
                for key in (os.getenv("DEEPSEEK_API_KEY"), os.getenv("LLM_API_KEY")):
                    if key:
                        serialized = serialized.replace(key, "[REDACTED]")
                (output / "report.json").write_text(serialized, encoding="utf-8")
            if record["execution_status"] == "cancelled":
                print(output / "report.json")
                return 130
    print(output / "report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
