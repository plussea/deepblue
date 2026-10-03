"""One isolated UI job; credentials arrive only through the server environment."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .agent import Agent
from .config import Config
from .llm import DeepSeekClient
from .prompts import build_system_prompt
from .session import Session, project_sessions
from .tools import ToolContext, create_tools
from .verification import VerificationConfig


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    options = json.load(sys.stdin)
    session = None
    tool_started = None
    def emit(kind, data):
        nonlocal tool_started
        if kind == 'tool_start':
            tool_started = time.monotonic()
        elif kind == 'tool_end' and tool_started is not None:
            data = {**data, 'elapsed_seconds': round(time.monotonic() - tool_started, 3)}
            tool_started = None
        print(json.dumps({"kind": kind, "data": data}, ensure_ascii=False), flush=True)
    cancelled = lambda: Path(options["cancel_path"]).exists()
    try:
        config = Config(Path(options["cwd"]), os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY", ""),
                        provider=options.get("provider","deepseek"),include_usage=options.get("include_usage",True),token_parameter=options.get("token_parameter","max_tokens"),home=Path(options["home"]), model=options["model"], base_url=options["base_url"],
                        request_timeout=options["timeout"], max_steps=options["max_steps"],
                        permission_mode=options.get('permission_mode', 'trusted'), budget_estimator=options.get('budget_estimator', 'calibrated'), active_checks=options.get('active_checks', 2), max_requests=options.get('max_requests'), token_budget=options.get('token_budget'),
                        run_seconds=options.get('run_seconds'), finalize_reserve_seconds=options.get('finalize_reserve_seconds', 10),
                        max_context_bytes=options["max_context_bytes"], shell_timeout=options["shell_timeout"])
        session = (Session.load(project_sessions(config.home, config.cwd) / (options["session_id"] + ".jsonl"), config.cwd)
                   if options.get("session_id") else Session.create(config.home, config.cwd, config.model, build_system_prompt(config.cwd)))
        # Selected profile model is frozen by the job; old transcript remains readable.
        emit("session", {"id": session.header["id"], "recovery": session.refresh_recovery()})
        tools = create_tools(ToolContext(config.cwd, session.artifacts, config.shell_timeout))
        verification = VerificationConfig(options["verify"], config.cwd, options["shell_timeout"], 1) if options.get("verify") else None
        client = DeepSeekClient(config, cancelled=cancelled)
        from .message_queue import MessageQueue
        inbox = MessageQueue(Path(options['queue_path'])) if options.get('queue_path') else None
        def steering():
            if not inbox or cancelled():
                return
            for _ in range(32):
                item = inbox.claim('steering', options['job_id'])
                if not item:
                    break
                yield item
                inbox.finish(item['id'], 'applied')
        agent = Agent(config, client, tools, session, emit, verification, cancelled=cancelled, steering=steering)
        if options["action"] == "compact":
            ok = agent.compact()
            emit("done", {"execution_status": "finished" if ok else "error", "verification_status": "unverified"})
        else:
            result = agent.run(options.get("prompt"))
            emit("done", {"execution_status": result.execution_status, "verification_status": result.verification_status})
    except KeyboardInterrupt:
        emit("done", {"execution_status": "cancelled", "verification_status": "unverified"})
    except Exception as exc:
        emit("error", {"text": str(exc)})
    finally:
        if session:
            session.close()


if __name__ == "__main__":
    main()
