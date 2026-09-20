from __future__ import annotations

import json

from .config import Config
from .llm import ModelError
from .models import EventSink, ModelClient, RunResult
from .session import Session
from .tools.base import ToolRegistry


class Agent:
    def __init__(self, config: Config, client: ModelClient, tools: ToolRegistry,
                 session: Session, emit: EventSink | None = None):
        self.config = config
        self.client = client
        self.tools = tools
        self.session = session
        self.emit = emit or (lambda kind, data: None)

    def run(self, prompt: str | None = None) -> RunResult:
        self.session.recover_pending()
        if prompt is not None:
            if not prompt.strip():
                raise ValueError("提示不能为空。")
            self.session.add({"role": "user", "content": prompt})
        elif self.session.messages[-1]["role"] not in {"user", "tool"}:
            raise ValueError("没有待继续的请求，请输入新的任务。")

        for step in range(1, self.config.max_steps + 1):
            context_bytes = len(json.dumps(self.session.messages, ensure_ascii=False).encode("utf-8"))
            if context_bytes > self.config.max_context_bytes:
                self.emit("notice", {"text": "会话超过本地上下文大小限制，请使用 /new；历史已保留。"})
                return RunResult("context_limit", step - 1)
            self.emit("model_start", {"step": step})
            try:
                completion = self.client.complete(self.session.messages, self.tools.declarations())
                message = completion.message
                self.session.add(message)
                self.session.record_usage(completion.usage)
                if message.get("content"):
                    self.emit("text", {"text": message["content"]})
                calls = message.get("tool_calls", [])
                complete = completion.finish_reason in {"stop", "tool_calls"}
                for call in calls:
                    name = call["function"]["name"]
                    self.emit("tool_start", {"name": name, "arguments": call["function"]["arguments"]})
                    if complete:
                        result = self.tools.execute(name, call["function"]["arguments"])
                    else:
                        result = {"ok": False, "error": "模型输出被截断或中止；未执行工具，请重新发出完整调用。"}
                    self.session.add({"role": "tool", "tool_call_id": call["id"],
                                      "content": json.dumps(result, ensure_ascii=False)})
                    self.emit("tool_end", {"name": name, "result": result})
                if not complete and not calls:
                    self.emit("notice", {"text": "模型输出被截断或中止，本次任务未确认完成。请发送“继续”。"})
                    return RunResult("incomplete", step)
                if not calls:
                    return RunResult("completed", step)
            except KeyboardInterrupt:
                self.session.recover_pending()
                self.emit("notice", {"text": "已取消当前执行；历史保留，未完成的调用不会自动重放。"})
                return RunResult("cancelled", step)
            except ModelError as exc:
                self.emit("notice", {"text": str(exc) + " 可使用 /retry 重试模型请求。"})
                return RunResult("error", step)
        self.emit("notice", {"text": "已达到最大模型调用轮数，任务可能尚未完成。使用 /retry 继续，或输入新指令。"})
        return RunResult("step_limit", self.config.max_steps)
