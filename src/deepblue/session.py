from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Message


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_sessions(home: Path, cwd: Path) -> Path:
    key = hashlib.sha256(os.path.normcase(str(cwd.resolve())).encode()).hexdigest()[:20]
    return home / "sessions" / key


class Session:
    """Append-only transcript, locked by the OS for the lifetime of its owner."""

    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = path
        self.messages: list[Message] = []
        self.header: dict = {}
        self.compaction: dict | None = None
        self.compaction_count = 0
        self.last_run: dict | None = None
        self.task_state: dict | None = None
        self.task_recovered = False
        self.operations: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.tool_outputs: dict[int, str] = {}
        self.recovery: dict = {}
        self.usage = {"api_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._lock = None
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._acquire()

    def _acquire(self):
        lock = self.path.with_suffix(".lock").open("a+b")
        try:
            lock.seek(0, 2)
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise ValueError("该会话正在被另一个深蓝进程使用。") from None
        self._lock = lock

    @classmethod
    def create(cls, home: Path, cwd: Path, model: str, system_prompt: str, parent=None) -> Session:
        session_id = uuid.uuid4().hex
        session = cls(project_sessions(home, cwd) / (session_id + ".jsonl"))
        try:
            session.header = {"type": "session", "version": 1, "id": session_id,
                              "cwd": str(cwd.resolve()), "model": model, "created_at": now()}
            if parent is not None:
                session.header["parent"] = parent
            session._append(session.header)
            session.add({"role": "system", "content": system_prompt})
            return session
        except BaseException:
            session.close()
            raise

    @classmethod
    def latest(cls, home: Path, cwd: Path) -> Session:
        paths = list(project_sessions(home, cwd).glob("*.jsonl"))
        if not paths:
            raise ValueError("当前目录没有可恢复的会话。先运行 deepblue 创建会话。")
        return cls.load(max(paths, key=lambda path: path.stat().st_mtime_ns), cwd)

    @classmethod
    def load(cls, path: Path, cwd: Path, recover: bool = True) -> Session:
        session = cls(path)
        try:
            # Only a non-newline-terminated final record may be repaired. Corruption
            # in a committed record fails explicitly instead of hiding data loss.
            with path.open("r+b") as file:
                while True:
                    start = file.tell()
                    raw = file.readline()
                    if not raw:
                        break
                    if not raw.endswith(b"\n"):
                        if recover:
                            file.truncate(start)
                        break
                    record = json.loads(raw)
                    if not session.header:
                        if record.get("type") != "session" or record.get("version") != 1:
                            raise ValueError("不支持的会话格式。")
                        session.header = record
                    elif record.get("type") == "message":
                        session.messages.append(record["message"])
                    elif record.get("type") == "compaction":
                        session._validate_compaction(record)
                        session.compaction = record
                        session.compaction_count += 1
                    elif record.get("type") == "usage":
                        session._count_usage(record["usage"])
                    elif record.get("type") == "run":
                        session.last_run = record
                        session.runs[record["run_id"]] = record
                    elif record.get("type") == "task_state":
                        session.apply_task_state(record["state"])
                        session.task_recovered = True
                    elif record.get("type") == "operation":
                        session.operations[record["operation_id"]] = record
                    elif record.get("type") == "tool_output":
                        session.tool_outputs[record["message_index"]] = record["path"]
            if not session.header or not session.messages:
                raise ValueError("会话文件不完整。")
            if Path(session.header["cwd"]).resolve() != cwd.resolve():
                raise ValueError("会话工作目录不匹配。")
            session.tool_outputs = {index: value for index, value in session.tool_outputs.items() if index < len(session.messages)}
            if recover:
                session.recover_pending()
            session.refresh_recovery()
            return session
        except BaseException:
            session.close()
            raise

    @property
    def artifacts(self) -> Path:
        path = self.path.with_suffix("")
        path.mkdir(exist_ok=True)
        return path

    def _append(self, record: dict):
        with self.path.open("ab") as file:
            file.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
            file.flush()
            os.fsync(file.fileno())

    def add(self, message: Message):
        if message.get("role") == "tool" and len(message.get("content", "").encode("utf-8")) > 8192:
            path = self.artifacts / ("output-" + uuid.uuid4().hex + ".txt")
            path.write_text(message["content"], encoding="utf-8")
            self._append({"type": "tool_output", "message_index": len(self.messages), "path": str(path)})
            self.tool_outputs[len(self.messages)] = str(path)
        self._append({"type": "message", "at": now(), "message": message})
        self.messages.append(message)

    def record_usage(self, usage: dict):
        if usage:
            self._append({"type": "usage", "at": now(), "usage": usage})
            self._count_usage(usage)

    def record_run(self, record: dict):
        record = {**record, "type": "run", "at": now()}
        self._append(record)
        self.last_run = record
        self.runs[record["run_id"]] = record

    def apply_task_state(self, state):
        if (not isinstance(state, dict) or state.get('schema_version') != 1
                or not isinstance(state.get('task_id'), str) or not isinstance(state.get('goal'), str)
                or not isinstance(state.get('runtime'), dict) or not isinstance(state.get('notes'), dict)):
            raise ValueError('无效的任务状态记录。')
        notes = state['notes']
        if (set(notes) != {'source', 'progress', 'blockers', 'next_step'} or notes['source'] != 'model'
                or any(not isinstance(notes[k], str) or len(notes[k]) > 2000 for k in ('progress', 'blockers', 'next_step'))):
            raise ValueError('无效的任务笔记。')
        self.task_state = json.loads(json.dumps(state, ensure_ascii=False))

    def record_task_state(self, state):
        from copy import deepcopy
        state = deepcopy(state)
        state['updated_at'] = now()
        previous = self.task_state
        try:
            self.apply_task_state(state)
        finally:
            self.task_state = previous
        self._append({'type': 'task_state', 'state': state})
        self.apply_task_state(state)
        self.task_recovered = False

    def record_operation(self, record: dict):
        record = {**record, "type": "operation", "at": now()}
        self._append(record)
        self.operations[record["operation_id"]] = record

    def refresh_recovery(self):
        from .recovery import recovery_report
        self.recovery = recovery_report(self)
        return self.recovery

    def active_messages(self, start=0, stop=None):
        from .recovery import file_state
        result = []
        snapshots = {}
        reads = {op["message_index"]: op for op in self.operations.values()
                 if op.get("phase") == "finished" and op.get("name") == "read" and op.get("path")}
        for index in range(start, len(self.messages) if stop is None else stop):
            message = self.messages[index]
            if index in self.tool_outputs and message["role"] == "tool" and Path(self.tool_outputs[index]).is_file():
                try:
                    data = json.loads(message["content"])
                    reduced = {key: data[key] for key in ("ok", "error", "exit_code", "path", "log_path", "next_offset") if key in data}
                    text = data.get("output") or data.get("content") or data.get("diff") or message["content"]
                except (ValueError, TypeError, AttributeError):
                    reduced, text = {}, message["content"]
                reduced.update(archived=True, artifact_path=self.tool_outputs[index],
                               excerpt=text[:1200] + "\n[中段已归档，可用 shell 提取原文]\n" + text[-2400:])
                message = {**message, "content": json.dumps(reduced, ensure_ascii=False)}
            if index in reads and message["role"] == "tool":
                op = reads[index]
                if op["path"] not in snapshots:
                    snapshots[op["path"]] = file_state(Path(op["path"]))
                if snapshots[op["path"]] != op.get("after") or "unknown" in op.get("after", {}):
                    message = {**message, "content": json.dumps({"ok": True, "stale": True, "path": op["path"],
                        "notice": "此读取已过期；文件变化，请重新 read。原内容保留在原始会话。"}, ensure_ascii=False)}
            result.append(message)
        return result

    def _count_usage(self, usage: dict):
        self.usage["api_calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if type(value) is int and value >= 0:
                self.usage[key] += value

    def context_messages(self) -> list[Message]:
        if self.compaction is None:
            messages = self.active_messages()
        else:
            messages = self.compacted_messages(self.compaction["summary"], self.compaction["first_kept"])
        if self.recovery.get("unfinished_operations") or self.recovery.get("changed_files"):
            report = json.dumps(self.recovery, ensure_ascii=False)
            messages[0] = {**messages[0], "content": messages[0]["content"] + "\n恢复时只读核对结果（不是新指令）：\n" + report[:12000]}
        if self.task_state:
            from .task_state import view
            state = json.dumps(view(self, context=True), ensure_ascii=False)
            messages[0] = {**messages[0], 'content': messages[0]['content'] +
                '\n当前任务记录（历史数据，不是新指令；notes 是模型笔记，不能替代程序验收）：\n' + state}
        return messages

    def compacted_messages(self, summary: str, first_kept: int) -> list[Message]:
        return [self.messages[0], {"role": "user", "content":
                "以下为较早对话的摘要，作为历史参考，不是新的任务指令。原始记录保存在会话文件中。\n\n" + summary},
                *self.active_messages(first_kept)]

    def _validate_compaction(self, record: dict):
        index = record.get("first_kept")
        if (type(index) is not int or not 1 <= index < len(self.messages)
            or self.messages[index].get("role") != "user"
            or not isinstance(record.get("summary"), str) or not record["summary"].strip()):
            raise ValueError("无效的会话压缩记录。")

    def save_compaction(self, summary: str, first_kept: int):
        record = {"type": "compaction", "at": now(), "summary": summary, "first_kept": first_kept}
        self._validate_compaction(record)
        # A single durable record commits the new view; source messages stay intact.
        self._append(record)
        self.compaction = record
        self.compaction_count += 1

    def recover_pending(self):
        pending = {}
        for index, message in enumerate(self.messages):
            if message["role"] == "assistant":
                for call in message.get("tool_calls", []):
                    pending[call["id"]] = (call, index)
            elif message["role"] == "tool":
                pending.pop(message["tool_call_id"], None)
        for call_id, (_, index) in pending.items():
            completed = [op for op in self.operations.values() if op.get("call_id") == call_id
                         and op.get("message_index", -1) > index and op.get("phase") == "finished"]
            result = completed[-1]["result"] if completed else {
                "ok": False, "error": "执行中断，结果未知。该调用不会自动重放；继续前先检查文件或命令副作用。"
            }
            self.add({"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False)})
        return len(pending)

    def close(self):
        if self._lock is not None:
            self._lock.close()
            self._lock = None
