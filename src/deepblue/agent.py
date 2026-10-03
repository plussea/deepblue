from __future__ import annotations

import json
import time
import uuid
from dataclasses import replace

from .config import Config
from .budget import BudgetClient, BudgetExceeded, run_budget, snapshot
from .compaction import compact_context
from .llm import ModelError
from .models import EventSink, ModelClient, RunResult
from .session import Session
from .tools.base import ToolRegistry
from .verification import VerificationConfig, verify, current_status, fingerprint
from .recovery import execute_recorded
from .tool_metrics import operation_metrics
from .prompts import execution_context
from .task_state import begin, checkpoint, note_tool, evidence_reference


class Agent:
    def __init__(self, config: Config, client: ModelClient, tools: ToolRegistry,
                 session: Session, emit: EventSink | None = None,
                 verification: VerificationConfig | None = None,
                 verification_not_applicable: str | None = None, cancelled=None, steering=None):
        from .capabilities import Catalog
        self.catalog = Catalog(config.cwd)
        tools.tools["load_skill"] = self.catalog.tool()
        self.config = config
        self.client = client
        self.tools = tools
        self.hooks = tools.hooks
        self.steering = steering or (lambda: [])
        tools.context.permission_mode = config.permission_mode
        tools.context.protected_paths = (config.home, session.artifacts)
        if verification and config.permission_mode != "trusted":
            raise ValueError("受限模式禁止命令验收；不允许绕过 Shell 权限。")
        self.session = session
        self.emit = emit or (lambda kind, data: None)
        self.verification = verification
        self.verification_not_applicable = verification_not_applicable
        self.cancelled = cancelled or (lambda: False)
        self.tools.context.cancelled = self.cancelled
        self.tools.tools["task_update"] = note_tool(self.session)
        if verification and verification_not_applicable is not None:
            raise ValueError("验收命令与不适用声明不能同时设置。")

    def run(self, prompt: str | None = None) -> RunResult:
        if prompt is not None:
            prompt = self.catalog.expand(prompt)
        return self.hooks.call("run", {"session_id": self.session.header["id"]}, lambda: self._run_lifecycle(prompt))

    def _run_lifecycle(self, prompt: str | None = None) -> RunResult:
        if prompt is not None and not prompt.strip():
            raise ValueError("提示不能为空。")
        self.session.recover_pending()
        self.session.refresh_recovery()
        if prompt is None and self.session.messages[-1]["role"] not in {"user", "tool"}:
            raise ValueError("没有待继续的请求，请输入新的任务。")
        task_id = ((self.session.last_run or {}).get("task_id") if prompt is None else None) or uuid.uuid4().hex
        run_id = uuid.uuid4().hex
        begin(self.session, task_id, run_id, prompt, self.verification)
        checkpoint(self.session, permission_mode=self.config.permission_mode)
        started = time.monotonic()
        usage_before = dict(self.session.usage)
        self.session.record_run({"task_id": task_id, "run_id": run_id, "permission_mode": self.config.permission_mode,
                                 "execution_status": "running", "verification_status": "unverified"})
        evidence = []
        self.budget = run_budget(self.config, bool(self.verification))
        original_client = self.client
        if isinstance(original_client, BudgetClient):
            for external in (original_client.budget, original_client.allocation):
                if external is not None:
                    self.budget['deadline'] = min(self.budget['deadline'], external['deadline'])
                    self.budget['request_deadline'] = min(self.budget['request_deadline'], external.get('request_deadline', external['deadline']))
        self.client = BudgetClient(original_client, self.budget, self.config.max_tokens, estimator_mode=self.config.budget_estimator)
        self.finalization = None
        self.active_checks = 0
        self.repair_feedback = set()
        self.check_cache_invalid = False
        try:
            result = self._run(prompt, task_id, run_id, evidence)
            if self.verification and result.status in {'step_limit', 'context_limit', 'incomplete', 'budget_limit'} and self.finalization != {'status': 'skipped', 'reason': 'time_budget'}:
                check = self._check(task_id, run_id, evidence)
                if self.cancelled() or (check and check['status'] == 'cancelled' and time.monotonic() < self.budget['deadline']):
                    result.status = 'cancelled'
            if self.client.reason:
                self.budget['stop_reason'] = self.client.reason
        except BaseException:
            checkpoint(self.session, execution_status="error", tool_metrics=operation_metrics(self.session, run_id))
            self.session.record_run({"task_id": task_id, "run_id": run_id, "permission_mode": self.config.permission_mode,
                "execution_status": "error", "verification_status": "unverified", "evidence": evidence,
                "budget": snapshot(self.budget), "finalization": self.finalization,
                "tool_metrics": operation_metrics(self.session, run_id)})
            raise
        finally:
            self.client = original_client
        result.task_id, result.run_id, result.evidence = task_id, run_id, evidence
        if evidence:
            result.verification_status = current_status(
                {"verification_status": evidence[-1]["status"], "evidence": evidence},
                self.config.cwd, (self.config.home,))
        elif self.verification_not_applicable is not None:
            result.verification_status = "not_applicable"
        checkpoint(self.session, execution_status=result.execution_status, verification_status=result.verification_status,
                   steps=result.steps, stop_reason=self.budget.get("stop_reason"),
                   tool_metrics=operation_metrics(self.session, run_id))
        self.session.record_run({"task_id": task_id, "run_id": run_id, "permission_mode": self.config.permission_mode,
            "execution_status": result.execution_status, "verification_status": result.verification_status,
            "verification_scope": "configured_checks" if self.verification else None,
            "verification_not_applicable": self.verification_not_applicable,
            "steps": result.steps, "evidence": evidence,
            "budget": snapshot(self.budget), "finalization": self.finalization,
            "active_checks": self.active_checks,
            "tool_metrics": operation_metrics(self.session, run_id),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "usage": {key: value - usage_before[key] for key, value in self.session.usage.items()}})
        self.emit("notice", {"text": f"执行：{result.execution_status}；验收：{result.verification_status}"
                             + ("（仅针对配置的检查）" if self.verification else "（未配置验收命令）")})
        self.emit("notice", {"text": f"本次预算：请求 {self.budget['calls']}/{self.budget['max_calls']}，"
                             f"已报告 Token {self.budget['tokens']}；停止原因：{self.budget.get('stop_reason', '无')}"})
        return result

    def _check(self, task_id, run_id, evidence):
        if self.cancelled():
            self.finalization = {"status": "skipped", "reason": "cancelled"}
            return None
        remaining = self.budget['deadline'] - time.monotonic()
        if remaining < 0.1:
            self.finalization = {"status": "skipped", "reason": "time_budget"}
            self.emit("notice", {"text": "运行时间已耗尽，跳过最终验收；未确认通过。"})
            return None
        # Only reuse stable evidence in this run; arbitrary shell calls invalidate it.
        if evidence and not self.check_cache_invalid:
            previous = evidence[-1]
            before = previous.get('workspace_before', {}).get('sha256')
            after = previous.get('workspace_after', {}).get('sha256')
            if previous['status'] in {'passed', 'failed'} and before and before == after == self._workspace_hash():
                checkpoint(self.session, verification_status=previous['status'], evidence=evidence_reference(previous))
                self.finalization = {"status": "reused", "verification_id": previous['verification_id']}
                return previous
        self.emit("notice", {"text": "正在执行配置的验收检查…"})
        check = self.hooks.call("verification", {"run_id": run_id, "command": self.verification.command}, lambda: verify(replace(self.verification, timeout=min(self.verification.timeout, remaining)),
                       self.config.cwd, self.session.artifacts, (self.config.home,), task_id, run_id,
                       cancelled=lambda: self.cancelled() or time.monotonic() >= self.budget['deadline']))
        evidence.append(check)
        checkpoint(self.session, verification_status=check["status"], evidence=evidence_reference(check))
        self.check_cache_invalid = False
        self.finalization = {"status": "checked", "verification_id": check['verification_id']}
        self.emit("notice", {"text": f"验收：{check['status']}；证据：{check['evidence_path']}"})
        return check

    def _workspace_hash(self):
        try:
            return fingerprint(self.config.cwd, (self.config.home,))['sha256']
        except (OSError, ValueError):
            return None

    def _feedback(self, check):
        identity = check['verification_id']
        if identity in self.repair_feedback or len(self.repair_feedback) >= self.verification.max_repairs:
            return False
        self.repair_feedback.add(identity)
        self.session.add({"role": "user", "content":
            "程序化验收失败，请根据证据修复；不要修改验收标准。\n" + json.dumps({
                key: check.get(key) for key in ('command', 'cwd', 'exit_code', 'output', 'log_path')
            }, ensure_ascii=False)})
        return True

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
            for incoming in self.steering():
                self.session.add({"role": "user", "content": self.catalog.expand(incoming["prompt"])})
                self.emit("notice", {"text": "已在模型请求边界接收补充指令。"})
            context = execution_context(self.session.context_messages(), step, self.config.max_steps)
            context[0] = {**context[0], "content": context[0]["content"] + self.catalog.context()}
            context_bytes = len(json.dumps(context, ensure_ascii=False).encode("utf-8"))
            if (self.config.auto_compact and auto_attempts < 2 and step - last_auto_step >= 4
                and context_bytes >= self.config.max_context_bytes * self.config.compact_threshold):
                auto_attempts += 1
                last_auto_step = step
                self.emit("notice", {"text": "上下文达到阈值，尝试自动压缩（单次最多 2 次摘要请求）…"})
                try:
                    self.hooks.call("compact", {"automatic": True}, lambda: compact_context(self.session, self.client, self.config, self.config.compact_keep_turns, max_requests=2))
                except BudgetExceeded as exc:
                    self.emit("notice", {"text": str(exc)})
                    return RunResult("budget_limit", step - 1)
                except (ModelError, ValueError) as exc:
                    self.emit("notice", {"text": "自动压缩未生效：" + str(exc)})
                except KeyboardInterrupt:
                    return RunResult("cancelled", step - 1)
                context = execution_context(self.session.context_messages(), step, self.config.max_steps)
                context[0] = {**context[0], "content": context[0]["content"] + self.catalog.context()}
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
                possible_change = bool(self.verification and complete and any(
                    call['function']['name'] in {'write', 'edit', 'shell'} for call in calls))
                before_batch = self._workspace_hash() if possible_change and self.active_checks < self.config.active_checks else None
                for call in calls:
                    if self.cancelled():
                        raise KeyboardInterrupt()
                    name = call["function"]["name"]
                    self.emit("tool_start", {"name": name, "arguments": call["function"]["arguments"]})
                    if complete:
                        if self.verification and name == 'shell':
                            self.check_cache_invalid = True
                        result = execute_recorded(self.session, self.tools, call)
                    else:
                        result = {"ok": False, "error": "模型输出被截断或中止；未执行工具，请重新发出完整调用。"}
                    self.session.add({"role": "tool", "tool_call_id": call["id"],
                                      "content": json.dumps(result, ensure_ascii=False)})
                    self.emit("tool_end", {"name": name, "result": result})
                    touched = list(self.session.task_state['runtime'].get('touched_files', []))
                    if complete and name in {'write', 'edit'} and result.get('ok'):
                        path = json.loads(call['function']['arguments']).get('path')
                        if path and path not in touched:
                            touched.append(path)
                    checkpoint(self.session, touched_files=touched[-100:], last_tool=name, steps=step,
                               tool_metrics=operation_metrics(self.session, run_id),
                               verification_status='stale' if evidence and name in {'write', 'edit', 'shell'} and complete
                               else self.session.task_state['runtime']['verification_status'])
                if possible_change and self.active_checks < self.config.active_checks and not self.cancelled():
                    after_batch = self._workspace_hash()
                    if before_batch is not None and after_batch is not None and before_batch != after_batch:
                        self.active_checks += 1
                        check = self._check(task_id, run_id, evidence)
                        if check is not None:
                            if check['status'] == 'cancelled':
                                return RunResult('budget_limit' if not self.cancelled() and time.monotonic() >= self.budget['deadline'] else 'cancelled', step)
                            if check['status'] == 'failed':
                                self._feedback(check)
                            else:
                                self.session.add({'role': 'user', 'content':
                                    '运行时主动验收结果（仅针对指定检查，不代表整个任务完成）：' + json.dumps({
                                        key: check.get(key) for key in ('status', 'command', 'exit_code', 'error', 'evidence_path')
                                    }, ensure_ascii=False) + '。若任务已完成，请据此简洁汇报；后续修改会使该证据失效。'})
                if not complete and not calls:
                    self.emit("notice", {"text": "模型输出被截断或中止，本次任务未确认完成。请发送“继续”。"})
                    return RunResult("incomplete", step)
                if not calls:
                    if self.verification:
                        check = self._check(task_id, run_id, evidence)
                        if check is None:
                            return RunResult("cancelled" if self.cancelled() else "budget_limit", step)
                        if check["status"] == "cancelled":
                            return RunResult("budget_limit" if not self.cancelled() and time.monotonic() >= self.budget["deadline"] else "cancelled", step)
                        if check["status"] == "failed" and step < self.config.max_steps and self._feedback(check):
                            continue
                    return RunResult("completed", step)
            except KeyboardInterrupt:
                self.session.recover_pending()
                self.emit("notice", {"text": "已取消当前执行；历史保留，未完成的调用不会自动重放。"})
                return RunResult("cancelled", step)
            except BudgetExceeded as exc:
                self.budget['stop_reason'] = getattr(self.client, 'reason', None) or 'external_budget'
                self.emit("notice", {"text": str(exc)})
                return RunResult("budget_limit", step - 1)
            except ModelError as exc:
                self.emit("notice", {"text": str(exc) + " 可使用 /retry 重试模型请求。"})
                return RunResult("error", step)
        self.emit("notice", {"text": "已达到最大模型调用轮数，任务可能尚未完成。使用 /retry 继续，或输入新指令。"})
        return RunResult("step_limit", self.config.max_steps)

    def compact(self, keep_turns: int = 2) -> bool:
        try:
            self.emit("notice", {"text": "正在压缩历史上下文，原始会话记录会保留…"})
            result = self.hooks.call("compact", {"automatic": False}, lambda: compact_context(self.session, self.client, self.config, keep_turns,
                                     lambda index: self.emit("notice", {"text": f"生成第 {index} 段摘要…"})))
            self.emit("notice", {"text": f"上下文：{result['before_bytes']} → {result['after_bytes']} 字节。"
                                 + ("摘要已保存。" if result["changed"] else "无需压缩，保留现有上下文。")})
            return True
        except (ModelError, ValueError) as exc:
            self.emit("notice", {"text": "压缩失败，原上下文保留：" + str(exc)})
            return False
        except KeyboardInterrupt:
            self.emit("notice", {"text": "已取消压缩，原上下文保留。"})
            return False
