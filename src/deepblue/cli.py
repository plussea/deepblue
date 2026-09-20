from __future__ import annotations

import argparse
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


def display(text: str, *, error: bool = False):
    # Repository contents and subprocess output must not inject terminal escapes.
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", text)
    print(text, file=sys.stderr if error else sys.stdout, flush=True)


def emit(kind: str, data: dict):
    if kind == "text":
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
    result.add_argument("--model", default=None, help="模型名（新会话默认 deepseek-flash）")
    result.add_argument("--base-url", default=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    result.add_argument("--home", type=Path, default=Path(os.getenv("DEEPBLUE_HOME", str(Path.home() / ".deepblue"))))
    result.add_argument("--max-steps", type=int, default=30, help="一次任务最多调用模型的次数")
    result.add_argument("--timeout", type=float, default=120, help="API 请求超时秒数")
    result.add_argument("--shell-timeout", type=float, default=120, help="命令执行时限秒数")
    result.add_argument("--max-tokens", type=int, default=8192)
    result.add_argument("--max-context-bytes", type=int, default=400_000)
    result.add_argument("--version", action="version", version="DeepBlue " + __version__)
    return result


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    if args.one_shot and not args.prompt:
        argument_parser.error("-p 需要提供任务文本。")
    if not args.one_shot and not sys.stdin.isatty():
        argument_parser.error("非交互环境请使用 -p \"任务\"。")
    session = None
    try:
        config = Config(cwd=args.cwd, api_key=os.getenv("DEEPSEEK_API_KEY", ""),
                        model=args.model or os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
                        base_url=args.base_url, home=args.home, max_steps=args.max_steps,
                        request_timeout=args.timeout, shell_timeout=args.shell_timeout,
                        max_tokens=args.max_tokens, max_context_bytes=args.max_context_bytes)
        if args.resume:
            session = Session.latest(config.home, config.cwd)
            if args.model and args.model != session.header["model"]:
                raise ValueError("恢复会话时不能更换模型；请新建会话后使用 --model。")
            config.model = session.header["model"]
        else:
            session = Session.create(config.home, config.cwd, config.model, build_system_prompt(config.cwd))

        def make_agent():
            tools = create_tools(ToolContext(config.cwd, session.artifacts, config.shell_timeout))
            return Agent(config, DeepSeekClient(config), tools, session, emit)

        agent = make_agent()
        if args.one_shot:
            result = agent.run(args.prompt)
            return 0 if result.status == "completed" else 130 if result.status == "cancelled" else 1
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
                display("/help 帮助\n/new 新会话\n/status 当前配置和会话路径\n/retry 继续待处理请求\n/exit 退出")
            elif prompt == "/status":
                display(f"模型：{config.model}\n目录：{config.cwd}\n会话：{session.path}\n消息数：{len(session.messages)}")
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
            elif prompt.startswith("/"):
                display("未知命令。输入 /help 查看帮助。", error=True)
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
