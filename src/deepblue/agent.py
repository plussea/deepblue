from __future__ import annotations

import json
import time
import uuid

from .config import Config
from .compaction import compact_context
from .llm import ModelError
from .models import EventSink, ModelClient, RunResult
from .session import Session
from .tools.base import ToolRegistry
from .verification import VerificationConfig, verify
from .recovery import execute_recorded


class Agent:
    def __init__(self, config: Config, client: ModelClient, tools: ToolRegistry,
                 session: Session, emit: EventSink | None = None,
                 verification: VerificationConfig | None = None,
                 verification_not_applicable: str | None = None, cancelled=None):
        self.config = config
        self.client = client
        self.tools = tools
        self.session = session
        self.emit = emit or (lambda kind, data: None)
        self.verification = verification
        self.verification_not_applicable = verification_not_applicable
        self.cancelled = cancelled or (lambda: False)
        self.tools.context.cancelled = self.cancelled
        if verification and verification_not_applicable is not None:
            raise ValueError("验收命令与不适用声明不能同时设置。")

    def run(self, prompt: str | None = None) -> RunResult:
        if prompt is not None and not prompt.strip():
            raise ValueError("提示不能为空。")
        self.session.recover_pending()
        self.session.refresh_recovery()
        if prompt is None and self.session.messages[-1]["role"] not in {"user", "tool"}:
            raise ValueError("没有待继续的请求，请输入新的任务。")
        task_id = ((self.session.last_run or {}).get("task_id") if prompt is None else None) or uuid.uuid4().hex
        run_id = uuid.uuid4().hex
        started = time.monotonic()
        usage_before = dict(self.session.usage)
        self.session.record_run({"task_id": task_id, "run_id": run_id,
                                 "execution_status": "running", "verification_status": "unverified"})
        evidence = []
        try:
            result = self._run(prompt, task_id, run_id, evidence)
        except BaseException:
            self.session.record_run({"task_id": task_id, "run_id": run_id,
                "execution_status": "error", "verification_status": "unverified", "evidence": evidence})
            raise
        result.task_id, result.run_id, result.evidence = task_id, run_id, evidence
        if evidence:
            result.verification_status = evidence[-1]["status"]
            if result.status != "completed" and result.verification_status == "passed":
                result.verification_status = "stale"
        elif self.verification_not_applicable is not None:
            result.verification_status = "not_applicable"
        self.session.record_run({"task_id": task_id, "run_id": run_id,
            "execution_status": result.execution_status, "verification_status": result.verification_status,
            "verification_scope": "configured_checks" if self.verification else None,
            "verification_not_applicable": self.verification_not_applicable,
            "steps": result.steps, "evidence": evidence,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "usage": {key: value - usage_before[key] for key, value in self.session.usage.items()}})
        self.emit("notice", {"text": f"执行：{result.execution_status}；验收：{result.verification_status}"
                             + ("（仅针对配置的检查）" if self.verification else "（未配置验收命令）")})
        return result

    def _run(self, prompt, task_id, run_id, evidence) -> RunResult:
        self.session.recover_pending()
        if prompt is not None:
            if not prompt.strip():
                raise ValueError("提示不能为空。")
            self.session.add({"role": "user", "content": prompt})
        elif self.session.messages[-1]["role"] not in {"user", "tool"}:
            raise ValueError("没有待继续的请求，请输入新的任务。")

        auto_attempts, last_auto_step = 0, -4
        for step in range(1, self.config.max_steps + 1):
            if self.cancelled():
                return RunResult("cancelled", step - 1)
            context = self.session.context_messages()
            context_bytes = len(json.dumps(context, ensure_ascii=False).encode("utf-8"))
            if (self.config.auto_compact and auto_attempts < 2 and step - last_auto_step >= 4
                and context_bytes >= self.config.max_context_bytes * self.config.compact_threshold):
                auto_attempts += 1
                last_auto_step = step
                self.emit("notice", {"text": "上下文达到阈值，尝试自动压缩（单次最多 2 次摘要请求）…"})
                try:
                    compact_context(self.session, self.client, self.config, self.config.compact_keep_turns, max_requests=2)
                except (ModelError, ValueError) as exc:
                    self.emit("notice", {"text": "自动压缩未生效：" + str(exc)})
                except KeyboardInterrupt:
                    return RunResult("cancelled", step - 1)
                context = self.session.context_messages()
                context_bytes = len(json.dumps(context, ensure_ascii=False).encode("utf-8"))
            if context_bytes > self.config.max_context_bytes:
                self.emit("notice", {"text": "会话超过本地上下文大小限制，请使用 /compact 或 /new；历史已保留。"})
                return RunResult("context_limit", step - 1)
            self.emit("model_start", {"step": step})
            streamed = False

            def on_text(text):
                nonlocal streamed
                if self.cancelled():
                    raise KeyboardInterrupt()
                streamed = True
                self.emit("text_delta", {"text": text})

            try:
                try:
                    completion = self.client.complete(context, self.tools.declarations(),
                                                      on_text=on_text if self.config.stream else None)
                finally:
                    if streamed:
                        self.emit("text_end", {})
                message = completion.message
                self.session.record_usage(completion.usage)
                if self.cancelled():
                    raise KeyboardInterrupt()
                self.session.add(message)
                if message.get("content") and not streamed:
                    self.emit("text", {"text": message["content"]})
                calls = message.get("tool_calls", [])
                complete = completion.finish_reason in {"stop", "tool_calls"}
                for call in calls:
                    if self.cancelled():
                        raise KeyboardInterrupt()
                    name = call["function"]["name"]
                    self.emit("tool_start", {"name": name, "arguments": call["function"]["arguments"]})
                    if complete:
                        result = execute_recorded(self.session, self.tools, call)
                    else:
                        result = {"ok": False, "error": "模型输出被截断或中止；未执行工具，请重新发出完整调用。"}
                    self.session.add({"role": "tool", "tool_call_id": call["id"],
                                      "content": json.dumps(result, ensure_ascii=False)})
                    self.emit("tool_end", {"name": name, "result": result})
                if not complete and not calls:
                    self.emit("notice", {"text": "模型输出被截断或中止，本次任务未确认完成。请发送“继续”。"})
                    return RunResult("incomplete", step)
                if not calls:
                    if self.verification:
                        self.emit("notice", {"text": "模型本轮结束，正在执行配置的验收检查…"})
                        check = verify(self.verification, self.config.cwd, self.session.artifacts,
                                       (self.config.home,), task_id, run_id, cancelled=self.cancelled)
                        evidence.append(check)
                        self.emit("notice", {"text": f"验收：{check['status']}；证据：{check['evidence_path']}"})
                        if check["status"] == "cancelled":
                            return RunResult("cancelled", step)
                        if check["status"] == "failed" and len(evidence) <= self.verification.max_repairs and step < self.config.max_steps:
                            self.session.add({"role": "user", "content":
                                "程序化验收失败，请根据证据修复；不要修改验收标准。\n" + json.dumps({
                                    key: check.get(key) for key in ("command", "cwd", "exit_code", "output", "log_path")
                                }, ensure_ascii=False)})
                            continue
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

    def compact(self, keep_turns: int = 2) -> bool:
        try:
            self.emit("notice", {"text": "正在压缩历史上下文，原始会话记录会保留…"})
            result = compact_context(self.session, self.client, self.config, keep_turns,
                                     lambda index: self.emit("notice", {"text": f"生成第 {index} 段摘要…"}))
            self.emit("notice", {"text": f"上下文：{result['before_bytes']} → {result['after_bytes']} 字节。"
                                 + ("摘要已保存。" if result["changed"] else "无需压缩，保留现有上下文。")})
            return True
        except (ModelError, ValueError) as exc:
            self.emit("notice", {"text": "压缩失败，原上下文保留：" + str(exc)})
            return False
        except KeyboardInterrupt:
            self.emit("notice", {"text": "已取消压缩，原上下文保留。"})
            return False
