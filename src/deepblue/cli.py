from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import __version__
from .agent import Agent
from .config import Config
from .llm import DeepSeekClient
from .prompts import build_system_prompt
from .session import Session
from .tools import ToolContext, create_tools
from .verification import VerificationConfig, current_status


def display(text: str, *, error: bool = False, end: str = "\n"):
    # Repository contents and subprocess output must not inject terminal escapes.
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", text)
    print(text, file=sys.stderr if error else sys.stdout, flush=True, end=end)


def emit(kind: str, data: dict):
    if kind == "text_delta":
        display(data["text"], end="")
    elif kind == "text_end":
        display("")
    elif kind == "text":
        display(data["text"])
    elif kind == "model_start":
        display(f"[深蓝 · 第 {data['step']} 轮] 等待 DeepSeek…", error=True)
    elif kind == "tool_start":
        display(f"[{data['name']}] {data['arguments'][:300]}", error=True)
    elif kind == "tool_end":
        result = data["result"]
        text = result.get("diff") or result.get("error") or result.get("output")
        display(f"[{data['name']}] {'完成' if result['ok'] else '失败'}", error=True)
        if text:
            display(text[:4000] + ("\n[终端展示已截断]" if len(text) > 4000 else ""), error=True)
        if result.get("log_path"):
            display("日志：" + result["log_path"], error=True)
    elif kind == "notice":
        display(data["text"], error=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="deepblue", description="DeepBlue 深蓝 · 基于 DeepSeek 的本地 coding agent")
    result.add_argument("prompt", nargs="?", help="启动后的第一条任务")
    result.add_argument("-p", "--print", dest="one_shot", action="store_true", help="执行一次任务后退出")
    result.add_argument("-c", "--continue", dest="resume", action="store_true", help="恢复当前目录最近的会话")
    result.add_argument("--cwd", type=Path, default=Path.cwd(), help="工作目录")
    result.add_argument("--token-parameter", choices=["max_tokens","max_completion_tokens"], default="max_tokens")
    result.add_argument("--no-stream-usage", action="store_true")
    result.add_argument("--provider", choices=["deepseek","openai-compatible"], default="deepseek")
    result.add_argument("--model", default=None, help="模型名（新会话默认 deepseek-flash）")
    result.add_argument("--base-url", default=os.getenv("DEEPSEEK_BASE_URL") or os.getenv("LLM_BASE_URL") or "https://api.deepseek.com")
    result.add_argument("--home", type=Path, default=Path(os.getenv("DEEPBLUE_HOME", str(Path.home() / ".deepblue"))))
    result.add_argument("--max-steps", type=int, default=30, help="一次任务最多调用模型的次数")
    result.add_argument("--budget-estimator", choices=["calibrated", "conservative"], default="calibrated", help="预算估计模式")
    result.add_argument("--permission-mode", choices=("trusted", "workspace", "read-only"), default="trusted")
    result.add_argument("--active-checks", type=int, default=2, help="修改后主动验收次数上限，0 关闭，最多 10")
    result.add_argument("--max-requests", type=int, default=None, help="每次运行请求上限，含自动压缩；默认 max-steps + 4")
    result.add_argument("--token-budget", type=int, default=None, help="每次运行 Token 保守准入门槛；默认不限制")
    result.add_argument("--run-seconds", type=float, default=None, help="运行协作时限；默认不限制")
    result.add_argument("--finalize-reserve-seconds", type=float, default=10, help="有验收命令时预留的收尾秒数")
    result.add_argument("--timeout", type=float, default=os.getenv("LLM_TIMEOUT_SECONDS", "120"), help="API 请求超时秒数")
    result.add_argument("--shell-timeout", type=float, default=120, help="命令执行时限秒数")
    result.add_argument("--max-tokens", type=int, default=8192)
    result.add_argument("--max-context-bytes", type=int, default=400_000)
    result.add_argument("--no-stream", action="store_true", help="关闭流式输出，等待完整回复")
    result.add_argument("--no-auto-compact", action="store_true", help="关闭自动压缩")
    result.add_argument("--compact-threshold", type=float, default=0.8, help="自动压缩字节阈值比例，0.2–0.95")
    result.add_argument("--summary-format", choices=["structured", "text"], default="structured")
    result.add_argument("--compact", action="store_true", help="恢复会话后先手动压缩上下文；须与 -c 一起使用")
    result.add_argument("--keep-turns", type=int, default=2, help="压缩保留最近的用户轮次数（1–20）")
    result.add_argument("--check-isolation", action="store_true", help="只读诊断隔离工具与服务，无需 API Key")
    result.add_argument("--version", action="version", version="DeepBlue " + __version__)
    verification_group = result.add_mutually_exclusive_group()
    verification_group.add_argument("--verify", metavar="COMMAND", help="模型结束后执行的显式验收命令")
    verification_group.add_argument("--verify-not-applicable", metavar="REASON", help="显式声明无需代码验收的原因，如纯解释任务")
    result.add_argument("--verify-cwd", type=Path, help="验收目录，默认工作目录；相对路径基于 --cwd")
    result.add_argument("--verify-timeout", type=float, default=120, help="每次验收命令超时秒数")
    result.add_argument("--verify-repairs", type=int, default=1, help="验收失败后最多修复次数（0–10），共享 max-steps")
    return result


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    if args.check_isolation:
        from .isolation import diagnostics
        display(json.dumps(diagnostics(), ensure_ascii=False, indent=2))
        return 0
    if args.one_shot and not args.prompt and not args.compact:
        argument_parser.error("-p 需要提供任务文本。")
    if args.compact and not args.resume:
        argument_parser.error("--compact 必须与 --continue 一起使用。")
    if not 1 <= args.keep_turns <= 20:
        argument_parser.error("--keep-turns 必须在 1 到 20 之间。")
    compact_only = args.compact and not args.prompt
    if not args.one_shot and not compact_only and not sys.stdin.isatty():
        argument_parser.error("非交互环境请使用 -p \"任务\"。")
    session = None
    try:
        config = Config(provider=args.provider,token_parameter=args.token_parameter,include_usage=not args.no_stream_usage,cwd=args.cwd, api_key=os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY", ""),
                        model=args.model or os.getenv("DEEPSEEK_MODEL") or os.getenv("LLM_MODEL") or "deepseek-flash",
                        base_url=args.base_url, home=args.home, max_steps=args.max_steps,
                        request_timeout=args.timeout, shell_timeout=args.shell_timeout,
                        permission_mode=args.permission_mode, max_requests=args.max_requests, token_budget=args.token_budget, active_checks=args.active_checks, budget_estimator=args.budget_estimator,
                        run_seconds=args.run_seconds, finalize_reserve_seconds=args.finalize_reserve_seconds,
                        max_tokens=args.max_tokens, max_context_bytes=args.max_context_bytes,
                        stream=not args.no_stream, auto_compact=not args.no_auto_compact,
                        compact_threshold=args.compact_threshold, compact_keep_turns=args.keep_turns,
                        summary_format=args.summary_format)
        verification = None
        if args.verify is not None:
            verify_cwd = args.verify_cwd or config.cwd
            if not verify_cwd.is_absolute():
                verify_cwd = config.cwd / verify_cwd
            verification = VerificationConfig(args.verify, verify_cwd, args.verify_timeout, args.verify_repairs)
        elif args.verify_cwd is not None:
            raise ValueError("--verify-cwd 必须与 --verify 一起使用。")
        if args.resume:
            session = Session.latest(config.home, config.cwd)
            if args.model and args.model != session.header["model"]:
                raise ValueError("恢复会话时不能更换模型；请新建会话后使用 --model。")
            config.model = session.header["model"]
        else:
            session = Session.create(config.home, config.cwd, config.model, build_system_prompt(config.cwd))

        def make_agent():
            tools = create_tools(ToolContext(config.cwd, session.artifacts, config.shell_timeout))
            return Agent(config, DeepSeekClient(config), tools, session, emit, verification, args.verify_not_applicable)

        agent = make_agent()
        if args.resume and (session.recovery.get("unfinished_operations") or session.recovery.get("changed_files")):
            display("恢复核对：\n" + json.dumps(session.recovery, ensure_ascii=False, indent=2), error=True)
        if args.compact:
            success = agent.compact(args.keep_turns)
            if compact_only or not success:
                return 0 if success else 1
        if args.one_shot:
            result = agent.run(args.prompt)
            return 0 if result.successful else 130 if result.status == "cancelled" else 1
        display(f"DeepBlue 深蓝 v{__version__} · {config.model}\n工作目录：{config.cwd}\n会话：{session.path}")
        display("输入任务开始。/help 查看命令；Ctrl+C 取消当前任务。")
        if args.resume:
            for message in reversed(session.messages):
                if message["role"] == "assistant" and message.get("content"):
                    display("上次回复：\n" + message["content"][:2000])
                    break
        if args.prompt:
            agent.run(args.prompt)
        while True:
            try:
                prompt = input("\n你 > ").strip()
            except EOFError:
                return 0
            except KeyboardInterrupt:
                display("\n输入 /exit 退出，或继续输入任务。")
                continue
            if not prompt:
                continue
            if prompt in {"/exit", "/quit"}:
                return 0
            if prompt == "/help":
                display("/help 帮助\n/new 新会话\n/status 配置、上下文和用量\n/task 当前任务状态\n/recovery 只读核对文件与未知操作\n/compact [N] 压缩历史，默认保留最近 2 轮\n/paste 多行输入，单独一行 /send 提交\n/retry 继续待处理请求\n/exit 退出")
            elif prompt == "/task":
                from .task_state import view
                display(json.dumps(view(session), ensure_ascii=False, indent=2) if session.task_state else '当前会话尚无结构化任务记录。')
            elif prompt == "/recovery":
                display(json.dumps(session.refresh_recovery(), ensure_ascii=False, indent=2))
            elif prompt == "/status":
                context = session.context_messages()
                size = len(json.dumps(context, ensure_ascii=False).encode("utf-8"))
                display(f"模型：{config.model}\n目录：{config.cwd}\n会话：{session.path}\n"
                        f"历史消息：{len(session.messages)}，活动上下文消息：{len(context)}\n"
                        f"上下文：{size}/{config.max_context_bytes} 字节，压缩次数：{session.compaction_count}\n"
                        f"有用量记录的请求：{session.usage['api_calls']}，累计 token：{session.usage['total_tokens']}\n"
                        f"流式输出：{'开启' if config.stream else '关闭'}")
                display("最近运行验收：" + current_status(session.last_run, config.cwd, (config.home,))
                        + "（仅针对该次任务及配置的检查）")
            elif prompt == "/compact" or prompt.startswith("/compact "):
                parts = prompt.split()
                try:
                    if len(parts) > 2:
                        raise ValueError()
                    keep = int(parts[1]) if len(parts) == 2 else args.keep_turns
                except ValueError:
                    display("用法：/compact [保留轮数，1–20]", error=True)
                    continue
                agent.compact(keep)
            elif prompt == "/paste":
                display("进入多行输入；单独一行 /send 提交，/cancel 或 Ctrl+C 取消。")
                lines = []
                try:
                    while True:
                        line = input("... ")
                        if line == "/cancel":
                            lines = []
                            break
                        if line == "/send":
                            break
                        lines.append(line)
                except (EOFError, KeyboardInterrupt):
                    lines = []
                    display("\n已取消多行输入。")
                combined = "\n".join(lines)
                if combined.strip():
                    agent.run(combined)
            elif prompt == "/new":
                replacement = Session.create(config.home, config.cwd, config.model, build_system_prompt(config.cwd))
                session.close()
                session = replacement
                agent = make_agent()
                display("已创建新会话：" + str(session.path))
            elif prompt == "/retry":
                try:
                    agent.run()
                except ValueError as exc:
                    display(str(exc), error=True)
            elif prompt in {"/skills", "/commands"}:
                display(str(agent.catalog.public()[prompt[1:]]))
            elif prompt.startswith("/"):
                try:
                    agent.run(prompt)
                except ValueError as exc:
                    display(str(exc), error=True)
            else:
                agent.run(prompt)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        display("错误：" + str(exc), error=True)
        return 2
    except KeyboardInterrupt:
        display("已取消。", error=True)
        return 130
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
